#!/usr/bin/env python3
"""
SunnyZ-YouTube-Archiver.py v1.2.04
------------------
A simple desktop GUI around yt-dlp for archiving YouTube playlists.

Features
  * Paste one or more playlist (or video) URLs, one per line
  * Pick a default quality/stream from a dropdown
  * Live log window showing exactly what yt-dlp is doing
  * Progress bar for the current download
  * Choose output folder, browser cookies, and ffmpeg location
  * Toggle subtitle / thumbnail / metadata embedding
  * Keeps a download-archive file so re-running skips what you already have
  * Start / Stop buttons (Stop interrupts the current download)

Requirements
  pip install -U "yt-dlp[default]"     (the [default] part matters in 2026)
  A JavaScript runtime on PATH (Deno recommended) -- needed for YouTube
  ffmpeg + ffprobe (on PATH, or set the ffmpeg folder in the GUI)

Run
  python SunnyZ-YouTube-Archiver.py
"""

import os
import queue
import re
import json
import sys
import time
import subprocess
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

# --- import yt-dlp, with a friendly message if it's missing ----------------
try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadCancelled
except ImportError:
    import sys
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "yt-dlp not installed",
        'yt-dlp is not installed.\n\nOpen a terminal and run:\n\n'
        '    pip install -U "yt-dlp[default]"\n\nthen start this program again.',
    )
    sys.exit(1)

# yt-dlp's own filename sanitizer, so parallel-mode folder names match the
# names yt-dlp would create itself. Falls back to a simple regex if absent.
try:
    from yt_dlp.utils import sanitize_filename as _ytdlp_sanitize
except Exception:  # noqa: BLE001
    _ytdlp_sanitize = None

# Optional, Windows-only beeper for the secret chiptune cracker mode.
try:
    import winsound
except Exception:  # noqa: BLE001
    winsound = None


# ---------------------------------------------------------------------------
# Quality presets: label shown in the dropdown -> yt-dlp format string.
# The fallbacks (the parts after each "/") make these robust when a given
# resolution isn't offered for a particular video.
# ---------------------------------------------------------------------------
QUALITY_PRESETS = {
    "Best available (video + audio)": "bv*+ba/b",
    "1080p or lower": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
    "720p or lower":  "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
    "480p or lower":  "bv*[height<=480]+ba/b[height<=480]/bv*+ba/b",
    "360p or lower":  "bv*[height<=360]+ba/b[height<=360]/bv*+ba/b",
    "Audio only (MP3)": "ba/b",   # handled specially (extract to mp3)
}

COOKIE_BROWSERS = ["None", "chrome", "firefox", "edge", "brave",
                   "chromium", "opera", "vivaldi", "safari"]

# Delay between downloads: label -> (min_seconds, max_seconds).
# yt-dlp waits a random time in [min, max] before each video. Some pause makes
# big playlist jobs look less robotic and avoids YouTube throttling / blocks.
# (0, 0) means no wait at all.
DELAY_PRESETS = {
    "Recommended - random 5-20s (safest)": (5, 20),
    "Balanced - random 3-10s": (3, 10),
    "Fast - random 1-4s (higher risk)": (1, 4),
    "No delay - fastest (high risk of blocks)": (0, 0),
}

# How many videos to download/convert at the same time.
CONCURRENCY_CHOICES = ["1 (one at a time)", "2", "3", "4"]

# Strip terminal colour codes that yt-dlp embeds in some messages.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text):
    return _ANSI_RE.sub("", text or "")


# After this many consecutive failures we assume the run is doomed (e.g. a bot
# check on every video) and stop, rather than grinding through the rest.
ERROR_LIMIT = 3

# category -> (popup title, advice). Advice names the exact app settings to
# change. Keep it plain ASCII so it renders cleanly in a Tk label.
ERROR_INFO = {
    "bot": (
        "YouTube bot check / sign-in required",
        "YouTube blocked the downloads with \"Sign in to confirm you're not a "
        "bot\". This usually happens with too many requests, or without being "
        "signed in.\n\n"
        "What to change in the app, then try again:\n"
        "  - Set 'Browser cookies' to the browser you're signed in to YouTube "
        "with (close that browser first).\n"
        "  - Set 'Simultaneous downloads' to 1.\n"
        "  - Set 'Delay between videos' to 'Recommended (5-20s)'.",
    ),
    "age": (
        "Age-restricted video",
        "YouTube needs a signed-in account to confirm your age for one or more "
        "videos.\n\n"
        "What to change in the app:\n"
        "  - Set 'Browser cookies' to a browser signed in to a YouTube account "
        "that can view the video (close that browser first), then try again.",
    ),
    "rate": (
        "Rate limited (HTTP 429)",
        "YouTube is temporarily refusing requests because too many arrived too "
        "quickly.\n\n"
        "What to change in the app:\n"
        "  - Set 'Simultaneous downloads' to 1.\n"
        "  - Set 'Delay between videos' to 'Recommended (5-20s)'.\n"
        "  - Wait 15-30 minutes before trying again. Using 'Browser cookies' "
        "also helps.",
    ),
    "members": (
        "Private / members-only video",
        "One or more videos are private, members-only, or need a subscription.\n\n"
        "What to change in the app:\n"
        "  - If it's members-only, set 'Browser cookies' to a browser signed in "
        "to an account that has access.\n"
        "  - Truly private videos can't be downloaded and will be skipped.",
    ),
    "unavailable": (
        "Video unavailable",
        "One or more videos were removed, made private, or are otherwise no "
        "longer available on YouTube. These can't be downloaded and will be "
        "skipped - there's no app setting that fixes a deleted video.",
    ),
    "geo": (
        "Region blocked",
        "One or more videos are not available in your country.\n\n"
        "Cookies from a signed-in account may help; otherwise a VPN in an "
        "allowed region would be required (outside this app).",
    ),
    "ffmpeg": (
        "ffmpeg problem",
        "ffmpeg/ffprobe wasn't found, or a merge/convert step failed. ffmpeg is "
        "needed to merge video+audio and to make MP3s.\n\n"
        "What to change in the app:\n"
        "  - Install ffmpeg, then set the 'ffmpeg folder' to the folder that "
        "contains ffmpeg.exe and ffprobe.exe.",
    ),
    "network": (
        "Network / connection problem",
        "The downloads couldn't reach YouTube reliably.\n\n"
        "What to try:\n"
        "  - Check your internet connection.\n"
        "  - Set 'Simultaneous downloads' to 1, then try again.",
    ),
    "other": (
        "Download error",
        "The run hit repeated errors and was stopped.\n\n"
        "General things to try in the app:\n"
        "  - Set 'Browser cookies' to a signed-in browser.\n"
        "  - Set 'Simultaneous downloads' to 1 and 'Delay between videos' to "
        "'Recommended'.\n"
        "  - Make sure yt-dlp is up to date (pip install -U yt-dlp).",
    ),
}


# ===========================================================================
#  SECRET 90s KEYGEN CRACKER DOOMSDAY MODE  (toggle with Alt+F4)  - cheese ->
# ===========================================================================
KEYGEN_LOGO = r"""
███████╗██╗   ██╗███╗   ██╗███╗   ██╗██╗   ██╗███████╗
██╔════╝██║   ██║████╗  ██║████╗  ██║╚██╗ ██╔╝╚══███╔╝
███████╗██║   ██║██╔██╗ ██║██╔██╗ ██║ ╚████╔╝   ███╔╝
╚════██║██║   ██║██║╚██╗██║██║╚██╗██║  ╚██╔╝   ███╔╝
    ███████║╚██████╔╝██║ ╚████║██║ ╚████║   ██║   ███████╗
    ╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═══╝   ╚═╝   ╚══════╝
   ▀▄▀▄▀▄ -=≡  Y O U T U B E   A R C H I V E R  ≡=- ▄▀▄▀▄▀
"""

KEYGEN_GREETZ = (
    "  ***  WELCOME TO THE ONE AND ONLY SunnyZ YOUTUBE ARCHIVER  ***  "
    "RELEASED BY THE [ COUCH POTATO CONSERVATION SCENE ]  ...  "
    "GREETZ FLYING OUT TO: every archivist, every librarian, every gremlin "
    "keeping the old vlogs alive  ...  100% WORKING  ...  0% MALWARE  ...  "
    "NO SURVEY  ...  NO CD KEY REQUIRED  ...  *** PLEASE TURN YOUR VOLUME DOWN "
    "BEFORE PRESSING PLAY, THIS CHIPTUNE IS A CRIME *** ...  "
    "remember kids: archive responsibly and be excellent to each other  ...  "
    "(this whole mode is a joke, the program works exactly the same)  ...  "
    "shout outs to ffmpeg, the real MVP  ...  press ALT+F4 again to flee  ...  "
)

# neon keygen palette
KG_BG = "#0a0018"      # deep purple-black
KG_BG2 = "#15002b"     # panel
KG_GREEN = "#39ff14"   # phosphor green
KG_CYAN = "#00ffd5"
KG_MAGENTA = "#ff2bd6"
KG_YELLOW = "#fff200"


# Musical note frequencies (Hz) for the beeper. Original cheesy loops only -
# no real songs, so nothing copyrighted gets reproduced.
_NOTE = {
    "C4": 262, "D4": 294, "E4": 330, "F4": 349, "G4": 392, "A4": 440, "B4": 494,
    "C5": 523, "D5": 587, "E5": 659, "F5": 698, "G5": 784, "A5": 880, "B5": 988,
    "C6": 1047, "R": 0,
}


def _seq(names, beats=0.25):
    return [(_NOTE[n], beats) for n in names]


CHIP_TUNES = [
    # 0: bouncy major arpeggio romp
    _seq("C5 G4 E5 G4 C6 G5 E5 G5 C5 G4 E5 G4 A5 F5 D5 F5".split()),
    # 1: spooky-ish minor noodle
    _seq("A4 C5 E5 A5 E5 C5 A4 C5 G4 B4 D5 G5 D5 B4 G4 B4".split()),
    # 2: hyperspeed chip run
    _seq("C5 D5 E5 F5 G5 A5 B5 C6 B5 A5 G5 F5 E5 D5 C5 R".split(), 0.18),
]


class ChiptunePlayer:
    """A blocking square-wave beeper looped on a daemon thread (winsound).

    It IS controllable: play / stop / next-track / tempo. Stop only takes
    effect after the current note (notes are short, so it's near-instant)."""

    def __init__(self):
        self.available = winsound is not None
        self.track = 0
        self.bpm = 260
        self._stop = threading.Event()
        self._thread = None

    def play(self):
        if not self.available:
            return
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        t, self._thread = self._thread, None
        if t and t.is_alive():
            t.join(timeout=1.0)

    def next_track(self):
        self.track = (self.track + 1) % len(CHIP_TUNES)

    def _run(self):
        seq = CHIP_TUNES[self.track % len(CHIP_TUNES)]
        while not self._stop.is_set():
            for freq, beats in seq:
                if self._stop.is_set():
                    return
                dur = max(1, int(beats * (60000.0 / max(60, self.bpm))))
                if freq <= 0:
                    self._stop.wait(dur / 1000.0)
                    continue
                try:
                    winsound.Beep(max(37, min(int(freq), 32767)), dur)
                except Exception:  # noqa: BLE001
                    return


# ---------------------------------------------------------------------------
# Logger that funnels yt-dlp's messages into a thread-safe queue so the GUI
# (which must only be touched from the main thread) can display them safely.
# ---------------------------------------------------------------------------
class QueueLogger:
    def __init__(self, q, cancel_event=None):
        self.q = q
        self.cancel_event = cancel_event

    def _check_cancel(self):
        # yt-dlp calls the logger constantly, so this is a responsive place to
        # abort a run (user Stop, or auto-stop after repeated errors) even while
        # it's only extracting metadata and no progress hook is firing.
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise DownloadCancelled()

    def debug(self, msg):
        self._check_cancel()
        # yt-dlp routes most normal output through debug(); the truly noisy
        # internal lines are prefixed "[debug] ", so we drop just those.
        if msg.startswith("[debug] "):
            return
        self.q.put(("log", strip_ansi(msg)))

    def info(self, msg):
        self._check_cancel()
        self.q.put(("log", strip_ansi(msg)))

    def warning(self, msg):
        self.q.put(("log", "WARNING: " + strip_ansi(msg)))

    def error(self, msg):
        clean = strip_ansi(msg)
        self.q.put(("log", "ERROR: " + clean))
        # Separate event so the app can count failures and react.
        self.q.put(("error", clean))
        self._check_cancel()


class SlotWheel(ttk.Frame):
    """One row in the parallel-download view: a circular progress wheel on the
    left, with the video title and a short status line on the right.

    The wheel shows download progress as a filling arc. During postprocessing
    (where there's no percentage) it switches to a spinning arc so it clearly
    looks busy rather than frozen. All drawing is done on a Canvas, so it looks
    the same on every OS and never flickers like ttk's indeterminate bar."""

    SIZE = 46
    THICK = 6
    ARC_BG = "#d9d9d9"
    ARC_DL = "#2e7d32"   # green while downloading
    ARC_PP = "#e65100"   # orange while processing
    ARC_RETIRE = "#c62828"  # red while finishing up before removal

    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, width=self.SIZE, height=self.SIZE,
                                highlightthickness=0, bd=0)
        try:
            bg = ttk.Style().lookup("TFrame", "background")
            if bg:
                self.canvas.configure(bg=bg)
        except tk.TclError:
            pass
        self.canvas.grid(row=0, column=0, rowspan=2, padx=(0, 10), pady=2)
        self.title_var = tk.StringVar(value="Idle")
        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.title_var, font=("", 9, "bold"),
                  wraplength=620, justify="left").grid(row=0, column=1, sticky="sw")
        ttk.Label(self, textvariable=self.status_var, foreground="#444",
                  wraplength=620, justify="left").grid(row=1, column=1, sticky="nw")
        self.columnconfigure(1, weight=1)

        self._spin_angle = 0
        self._spinning = False
        self._after_id = None
        self._retiring = False
        self._draw_ring()

    # -- geometry helpers ---------------------------------------------------
    def _bbox(self):
        h = self.THICK
        return (h, h, self.SIZE - h, self.SIZE - h)

    def _draw_ring(self, extent=0.0, colour=ARC_DL):
        self.canvas.delete("all")
        x0, y0, x1, y1 = self._bbox()
        self.canvas.create_oval(x0, y0, x1, y1, outline=self.ARC_BG,
                                width=self.THICK)
        if extent > 0:
            # start at 12 o'clock, sweep clockwise
            self.canvas.create_arc(x0, y0, x1, y1, start=90,
                                   extent=-359.999 * extent, style="arc",
                                   outline=colour, width=self.THICK)

    # -- public API (called on the main thread) -----------------------------
    def set_progress(self, frac, title=None, status=None):
        if self._retiring:
            return
        self._stop_spin()
        self._draw_ring(max(0.0, min(frac, 1.0)), self.ARC_DL)
        if title is not None:
            self.title_var.set(title)
        if status is not None:
            self.status_var.set(status)

    def set_busy(self, title=None, status=None):
        if self._retiring:
            return
        if title is not None:
            self.title_var.set(title)
        if status is not None:
            self.status_var.set(status)
        if not self._spinning:
            self._spinning = True
            self._spin()

    def set_idle(self, title="Idle", status=""):
        self._stop_spin()
        self._retiring = False
        self._draw_ring(0.0)
        self.title_var.set(title)
        self.status_var.set(status)

    def set_retiring(self):
        """Mark this helper as being removed: red ring + note. It keeps
        whatever it's doing until its current video finishes, then it's wiped."""
        self._retiring = True
        self._stop_spin()
        self._draw_ring(1.0, self.ARC_RETIRE)
        self.title_var.set("Finishing then closing this helper...")
        self.status_var.set("")

    # -- spinner animation ---------------------------------------------------
    def _spin(self):
        x0, y0, x1, y1 = self._bbox()
        self.canvas.delete("all")
        self.canvas.create_oval(x0, y0, x1, y1, outline=self.ARC_BG,
                                width=self.THICK)
        self.canvas.create_arc(x0, y0, x1, y1, start=self._spin_angle,
                               extent=-90, style="arc",
                               outline=self.ARC_PP, width=self.THICK)
        self._spin_angle = (self._spin_angle - 12) % 360
        self._after_id = self.after(40, self._spin)

    def _stop_spin(self):
        self._spinning = False
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None

    def destroy(self):
        self._stop_spin()
        super().destroy()


class App:
    LOG_NAME = "download_log.json"   # per-folder progress tracker / dedup record

    def __init__(self, root):
        self.root = root
        self.root.title("SunnyZ YouTube Archiver")
        self.root.geometry("745x850")
        self.root.minsize(745, 850)
        self.root.maxsize(745, 1500)
        self.msg_queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker = None
        self._busy = {}  # progressbar widget -> scheduled after-id for its sweep
        self._lock = threading.Lock()  # guards parallel progress accounting
        self._log_lock = threading.Lock()  # serialises per-folder JSON writes
        self._blink_after = None
        self.failed_triggered = False
        self.slots = {}
        self.target_workers = 1

        # --- secret keygen mode plumbing (toggled with Alt+F4) ---
        self.keygen_on = False
        self._running = False
        self.style = ttk.Style()
        self._orig_theme = self.style.theme_use()
        self._make_keygen_theme()
        self.player = ChiptunePlayer()
        self._marquee_after = None
        self._tk_defaults = {}     # widget -> dict of restored options

        self._build_ui()
        self._build_keygen_banner()

        # Alt+F4 does NOT close the app - it flips the doomsday skin. The X
        # button still closes normally. (Ctrl+Alt+K is a quiet backup.)
        self._altf4_guard = 0.0
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind_all("<Alt-F4>", self._toggle_keygen)
        self.root.bind_all("<Alt-Key-F4>", self._toggle_keygen)
        self.root.bind_all("<Control-Alt-k>", self._toggle_keygen)

        # Start polling the message queue ~10x/sec to update the log + progress.
        self.root.after(100, self._drain_queue)

    # ----- UI construction -------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self.root, padding=10)
        self.main_frame = main
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)

        # URLs
        ttk.Label(main, text="Playlist, video URL or Channel link (one per line):").grid(
            row=0, column=0, sticky="w", **pad)
        self.url_text = scrolledtext.ScrolledText(main, height=5, wrap="none")
        self.url_text.grid(row=1, column=0, sticky="ew", **pad)
        self.url_text.insert("1.0",
            "https://www.youtube.com/playlist?list=XXXXXXX\n"
            "https://www.youtube.com/watch?v=XXXXXXX\n"
            "https://www.youtube.com/@SunnyZ/videos\n")

        # Options frame
        opts = ttk.LabelFrame(main, text="Options", padding=8)
        opts.grid(row=2, column=0, sticky="ew", **pad)
        opts.columnconfigure(1, weight=1, minsize=260)
        self.opts_frame = opts

        # Dark-purple "locked" veil shown over the options while downloading.
        self.opts_veil = tk.Canvas(main, highlightthickness=0, bd=0, bg="#1b0033")
        self.opts_veil.bind("<Configure>", self._draw_opts_veil)
        # swallow any clicks that land on the veil
        self.opts_veil.bind("<Button-1>", lambda e: "break")

        # Track every input widget so we can grey them out while running.
        self._inputs = []

        # Quality
        ttk.Label(opts, text="Quality / stream:").grid(row=0, column=0, sticky="w", **pad)
        self.quality_var = tk.StringVar(value=list(QUALITY_PRESETS)[0])
        quality_box = ttk.Combobox(opts, textvariable=self.quality_var,
                                   values=list(QUALITY_PRESETS), state="readonly", width=30)
        quality_box.grid(row=0, column=1, sticky="w", **pad)

        # Output folder
        ttk.Label(opts, text="Save to folder:").grid(row=1, column=0, sticky="w", **pad)
        self.outdir_var = tk.StringVar(value=os.path.abspath("archive"))
        outdir_entry = ttk.Entry(opts, textvariable=self.outdir_var)
        outdir_entry.grid(row=1, column=1, sticky="ew", **pad)
        outdir_btn = ttk.Button(opts, text="Browse...", command=self._pick_outdir)
        outdir_btn.grid(row=1, column=2, sticky="w", **pad)

        # Browser cookies
        ttk.Label(opts, text="Browser cookies:").grid(row=2, column=0, sticky="w", **pad)
        self.cookies_var = tk.StringVar(value="None")
        cookies_box = ttk.Combobox(opts, textvariable=self.cookies_var,
                                   values=COOKIE_BROWSERS, state="readonly", width=14)
        cookies_box.grid(row=2, column=1, sticky="w", **pad)

        # ffmpeg location
        ttk.Label(opts, text="ffmpeg folder:").grid(row=3, column=0, sticky="w", **pad)
        self.ffmpeg_var = tk.StringVar(value="")
        ffmpeg_entry = ttk.Entry(opts, textvariable=self.ffmpeg_var)
        ffmpeg_entry.grid(row=3, column=1, sticky="ew", **pad)
        ffmpeg_btn = ttk.Button(opts, text="Browse...", command=self._pick_ffmpeg)
        ffmpeg_btn.grid(row=3, column=2, sticky="w", **pad)

        # Delay between downloads (rate-limit / anti-block pacing)
        ttk.Label(opts, text="Delay between videos:").grid(row=4, column=0, sticky="w", **pad)
        self.delay_var = tk.StringVar(value=list(DELAY_PRESETS)[0])
        delay_box = ttk.Combobox(opts, textvariable=self.delay_var,
                                 values=list(DELAY_PRESETS), state="readonly", width=40)
        delay_box.grid(row=4, column=1, columnspan=2, sticky="w", **pad)
        delay_box.bind("<<ComboboxSelected>>", self._on_delay_change)
        self.delay_warning = tk.StringVar()
        self.delay_warning_lbl = ttk.Label(opts, textvariable=self.delay_warning,
                                            wraplength=560, justify="left")
        self.delay_warning_lbl.grid(row=5, column=0, columnspan=3, sticky="w",
                                    padx=8, pady=(0, 2))
        self._on_delay_change()  # set the initial warning text/colour

        # Embed toggles
        toggles = ttk.Frame(opts)
        toggles.grid(row=6, column=0, columnspan=3, sticky="w", **pad)
        self.embed_subs = tk.BooleanVar(value=True)
        self.embed_thumb = tk.BooleanVar(value=True)
        self.embed_meta = tk.BooleanVar(value=True)
        cb_subs = ttk.Checkbutton(toggles, text="Embed subtitles", variable=self.embed_subs)
        cb_thumb = ttk.Checkbutton(toggles, text="Embed thumbnail", variable=self.embed_thumb)
        cb_meta = ttk.Checkbutton(toggles, text="Embed metadata + chapters", variable=self.embed_meta)
        cb_subs.pack(side="left", padx=6)
        cb_thumb.pack(side="left", padx=6)
        cb_meta.pack(side="left", padx=6)

        # (widget, state-when-enabled) pairs for _set_inputs_state(). NOTE the
        # 'Simultaneous downloads' control is deliberately NOT here - it stays
        # live during a run so helpers can be added/removed on the fly.
        self._inputs = [
            (self.url_text, "normal"),
            (quality_box, "readonly"), (outdir_entry, "normal"), (outdir_btn, "normal"),
            (cookies_box, "readonly"), (ffmpeg_entry, "normal"), (ffmpeg_btn, "normal"),
            (delay_box, "readonly"),
            (cb_subs, "normal"), (cb_thumb, "normal"), (cb_meta, "normal"),
        ]

        # Buttons + live "simultaneous downloads" control on the right
        btns = ttk.Frame(main)
        btns.grid(row=3, column=0, sticky="ew", **pad)
        self.start_btn = ttk.Button(btns, text="Start download", command=self._start)
        self.start_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(btns, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        self.stop_note = tk.StringVar(value="")
        self.stop_note_lbl = tk.Label(btns, textvariable=self.stop_note,
                                      fg="#c62828", font=("", 9, "bold"))
        self.stop_note_lbl.pack(side="left", padx=10)
        self._blink_after = None

        self.concurrency_var = tk.StringVar(value=CONCURRENCY_CHOICES[0])
        conc_box = ttk.Combobox(btns, textvariable=self.concurrency_var,
                                values=CONCURRENCY_CHOICES, state="readonly", width=16)
        conc_box.pack(side="right", padx=4)
        conc_box.bind("<<ComboboxSelected>>", self._on_concurrency_change)
        ttk.Label(btns, text="Simultaneous downloads:").pack(side="right", padx=(0, 2))

        # Progress (two bars: overall across all videos, and current video)
        prog = ttk.LabelFrame(main, text="Progress", padding=8)
        prog.grid(row=4, column=0, sticky="ew", **pad)
        prog.columnconfigure(0, weight=1)

        ttk.Label(prog, text="Overall (all videos):").grid(row=0, column=0, sticky="w")
        self.batch_title = tk.StringVar(value="")
        ttk.Label(prog, textvariable=self.batch_title, font=("", 9, "bold"),
                  foreground="#1565c0", wraplength=760, justify="left").grid(
            row=0, column=0, sticky="e")
        self.overall_progress = ttk.Progressbar(prog, mode="determinate", maximum=1.0)
        self.overall_progress.grid(row=1, column=0, sticky="ew", pady=(0, 2))
        self.overall_status = tk.StringVar(value="Idle.")
        ttk.Label(prog, textvariable=self.overall_status, foreground="#444").grid(
            row=2, column=0, sticky="w", pady=(0, 8))

        # Per-download display. Two interchangeable views live in the same
        # grid cell: a detailed single-video view, and a multi-slot view with
        # one progress wheel per simultaneous download. _start picks which.
        self.single_frame = ttk.Frame(prog)
        self.single_frame.grid(row=3, column=0, sticky="ew")
        self.single_frame.columnconfigure(0, weight=1)
        self.video_title = tk.StringVar(value="Current video:")
        ttk.Label(self.single_frame, textvariable=self.video_title,
                  font=("", 9, "bold"), wraplength=760, justify="left").grid(
            row=0, column=0, sticky="w")
        self.video_progress = ttk.Progressbar(self.single_frame, mode="determinate",
                                               maximum=1.0)
        self.video_progress.grid(row=1, column=0, sticky="ew", pady=(0, 2))
        self.video_status = tk.StringVar(value="")
        ttk.Label(self.single_frame, textvariable=self.video_status, foreground="#444",
                  wraplength=760, justify="left").grid(row=2, column=0, sticky="w")

        self.multi_frame = ttk.Frame(prog)
        self.multi_frame.grid(row=3, column=0, sticky="ew")
        self.multi_frame.columnconfigure(0, weight=1)
        self.slots = {}  # slot_id -> SlotWheel, added/removed live during a run
        self.single_frame.grid_remove()  # always use wheels now

        # Log
        ttk.Label(main, text="Activity log:").grid(row=5, column=0, sticky="w", **pad)
        self.log = scrolledtext.ScrolledText(main, height=14, wrap="word",
                                             state="disabled", font=("Consolas", 9))
        self.log.grid(row=6, column=0, sticky="nsew", **pad)
        main.rowconfigure(6, weight=1)

    # ----- small helpers ---------------------------------------------------
    def _on_concurrency_change(self, event=None):
        # Works whether or not a run is active: the pool manager polls this.
        self.target_workers = int(self.concurrency_var.get().split()[0])

    def _on_delay_change(self, event=None):
        lo, hi = DELAY_PRESETS[self.delay_var.get()]
        if hi >= 5:
            text = ("Pauses a random 5-20s before each video. Recommended for "
                    "channels and large playlists - looks human and avoids blocks.")
            colour = "#2e7d32"  # green
        elif hi >= 3:
            text = ("Shorter random pause. Usually fine for medium playlists; "
                    "a little more likely to be throttled than Recommended.")
            colour = "#2e7d32"  # green
        elif hi >= 1:
            text = ("WARNING: very short delay. On big jobs YouTube may return "
                    "429 errors or the 'sign in to confirm you're not a bot' wall. "
                    "Best for just a few videos.")
            colour = "#e65100"  # orange
        else:
            text = ("WARNING: no delay at all. Highest risk - YouTube may rate-"
                    "limit or temporarily block your IP/account on large jobs. "
                    "Use only for one or two videos.")
            colour = "#c62828"  # red
        self.delay_warning.set(text)
        self.delay_warning_lbl.configure(foreground=colour)

    def _pick_outdir(self):
        d = filedialog.askdirectory(initialdir=self.outdir_var.get() or ".")
        if d:
            self.outdir_var.set(d)

    def _pick_ffmpeg(self):
        d = filedialog.askdirectory(title="Folder containing ffmpeg.exe / ffprobe.exe")
        if d:
            self.ffmpeg_var.set(d)

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ----- start / stop ----------------------------------------------------
    def _start(self):
        urls = [u.strip() for u in self.url_text.get("1.0", "end").splitlines() if u.strip()]
        if not urls:
            messagebox.showwarning("No URLs", "Please paste at least one URL.")
            return

        self.cancel_event.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._set_inputs_state(running=True)
        self._stop_blink()
        self._stop_busy(self.overall_progress)
        self._stop_busy(self.video_progress)
        self.overall_progress.configure(mode="determinate", value=0)
        self.video_progress.configure(value=0)
        self.overall_status.set("Starting...")
        self.video_status.set("")
        self.video_title.set("Current video:")

        outdir = self.outdir_var.get() or "archive"
        self.batch_title.set("Batch: (scanning...)")

        # Per-run progress accounting (see _progress_hook / _run).
        self.total_videos = 0          # total across every URL (from the scan)
        self.pre_done = 0              # videos already in the download archive
        self.done_ids = set()          # ids fully completed during THIS run
        self.current_id = None         # id of the video currently downloading
        self.current_id_frac = 0.0     # monotonic fraction for the current video
        self.current_title = ""        # title of the video currently worked on
        self.slot_id = {}              # slot index -> id of video in that slot
        self.slot_frac = {}            # slot index -> monotonic fraction
        self.slot_meta = {}            # slot index -> (folder_dir, index, title)
        self.video_meta = {}           # id -> per-video log record being built
        self.done_cache = {}           # folder_dir -> set(completed ids), for skipping
        self.legacy_done = set()       # ids from an old downloaded.txt (back-compat)
        self.completed_count = 0       # videos actually downloaded THIS run
        self.run_started = time.monotonic()
        self.run_error = False
        self.out_dir = outdir          # where this batch is saved

        # Error tracking -> auto-stop + "failed" popup after repeated failures.
        self.error_count = 0           # total failures this run
        self.consec_errors = 0         # consecutive failures (reset on success)
        self.error_cats = {}           # category -> count
        self.last_error_msg = ""       # a sample message for the popup
        self.failed_triggered = False  # auto-stop already fired?
        self.counting_enabled = False  # only count errors during downloads

        # Initial number of helpers; can be changed live via the dropdown.
        self.target_workers = int(self.concurrency_var.get().split()[0])
        self._build_slots()

        opts = self._build_opts()
        self.worker = threading.Thread(target=self._run_pool, args=(urls, opts),
                                       daemon=True)
        self.worker.start()

    def _build_slots(self):
        """Clear any wheels from a previous run; the pool adds them live."""
        for wheel in self.slots.values():
            wheel.destroy()
        self.slots = {}
        self.single_frame.grid_remove()
        self.multi_frame.grid()
        self._relayout_slots()

    def _add_slot(self, sid):
        if sid in self.slots:
            return
        wheel = SlotWheel(self.multi_frame)
        wheel.set_idle("Starting...", "")
        self.slots[sid] = wheel
        self._relayout_slots()

    def _remove_slot(self, sid):
        wheel = self.slots.pop(sid, None)
        if wheel is not None:
            wheel.destroy()
            self._relayout_slots()

    def _relayout_slots(self):
        """Re-grid wheels in slot order and grow/shrink the window to fit."""
        for row, sid in enumerate(sorted(self.slots)):
            self.slots[sid].grid(row=row, column=0, sticky="ew", pady=3)
        n = max(1, len(self.slots))
        height = min(700 + n * 62, 1040)
        base_extra = 220 if getattr(self, "keygen_on", False) else 0
        self.root.geometry(f"745x{min(height + base_extra, 1500)}")

    def _stop(self):
        self.cancel_event.set()
        self.stop_btn.configure(state="disabled")
        self.overall_status.set("Stopping...")
        self._start_blink()

    # ----- input enable/disable + stop-blink -------------------------------
    def _set_inputs_state(self, running):
        self._running = running
        for widget, enabled_state in self._inputs:
            try:
                widget.configure(state="disabled" if running else enabled_state)
            except tk.TclError:
                pass
        self._sync_opts_veil()

    def _sync_opts_veil(self):
        # The purple "locked" veil belongs to the keygen skin only.
        if getattr(self, "_running", False) and self.keygen_on:
            self._show_opts_veil()
        else:
            self._hide_opts_veil()

    def _draw_opts_veil(self, event=None):
        c = self.opts_veil
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        for x in range(-h, w, 24):        # diagonal hazard stripes
            c.create_line(x, h, x + h, 0, fill="#2c0052", width=9)
        c.create_text(w // 2, h // 2,
                      text="\u23F3  SETTINGS LOCKED WHILE DOWNLOADING  \u23F3",
                      fill="#cbb0ff", font=("Consolas", 11, "bold"))

    def _show_opts_veil(self):
        # Created after opts_frame, so it already stacks above it.
        self.opts_veil.place(in_=self.opts_frame, x=0, y=0, relwidth=1, relheight=1)
        self.opts_veil.after(10, self._draw_opts_veil)

    def _hide_opts_veil(self):
        self.opts_veil.place_forget()

    def _start_blink(self):
        self._blink_on = True
        self._blink_step()

    def _blink_step(self):
        # Red warning with an animated trailing dot, so it's clearly "working".
        dots = "." * (1 + (int(time.monotonic() * 2) % 3))
        text = f"Stopping (finishing current process) {dots}"
        self.stop_note.set(text if self._blink_on else "")
        self._blink_on = not self._blink_on
        self._blink_after = self.root.after(500, self._blink_step)

    def _stop_blink(self):
        if self._blink_after is not None:
            self.root.after_cancel(self._blink_after)
            self._blink_after = None
        self.stop_note.set("")

    def _open_folder(self, path):
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: S606 - intended
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Could not open folder", str(e))

    @staticmethod
    def _classify_error(msg):
        m = (msg or "").lower()
        if "confirm your age" in m or "age-restricted" in m or "inappropriate" in m:
            return "age"
        if "not a bot" in m or ("sign in to confirm" in m and "age" not in m):
            return "bot"
        if "429" in m or "too many requests" in m:
            return "rate"
        if ("private video" in m or "members-only" in m or "members only" in m
                or "join this channel" in m or "subscriber" in m):
            return "members"
        if ("video unavailable" in m or "has been removed" in m or "no longer available" in m
                or "this video is not available" in m or "account associated" in m
                or "terminated" in m or "removed by the uploader" in m):
            return "unavailable"
        if ("in your country" in m or "geo-restrict" in m or "geo restrict" in m
                or "not available in your" in m or "blocked it in your country" in m):
            return "geo"
        if "ffmpeg" in m or "ffprobe" in m or "postprocessing" in m or "postprocessor" in m:
            return "ffmpeg"
        if ("unable to download" in m or "connection" in m or "timed out" in m
                or "getaddrinfo" in m or "urlopen" in m or "network" in m
                or "temporary failure" in m or "10054" in m):
            return "network"
        return "other"

    def _build_opts(self):
        outdir = self.outdir_var.get() or "archive"
        quality_label = self.quality_var.get()
        fmt = QUALITY_PRESETS[quality_label]
        audio_only = quality_label.startswith("Audio only")

        outtmpl = os.path.join(
            outdir, "%(playlist_title|Videos)s",
            "%(playlist_index|0)03d - %(title)s [%(id)s].%(ext)s")

        opts = {
            "format": fmt,
            "outtmpl": outtmpl,
            "windowsfilenames": True,
            "ignoreerrors": True,
            "retries": 10,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": 3,
            "continuedl": True,
            "logger": QueueLogger(self.msg_queue, self.cancel_event),
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._pp_hook],
        }

        # Delay between downloads, from the GUI dropdown. (0, 0) -> no wait.
        delay_lo, delay_hi = DELAY_PRESETS[self.delay_var.get()]
        if delay_hi > 0:
            opts["sleep_interval"] = delay_lo
            opts["max_sleep_interval"] = delay_hi

        postprocessors = []
        if audio_only:
            opts["format"] = "ba/b"
            postprocessors.append({"key": "FFmpegExtractAudio",
                                   "preferredcodec": "mp3", "preferredquality": "0"})
            if self.embed_meta.get():
                postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})
            if self.embed_thumb.get():
                opts["writethumbnail"] = True
                postprocessors.append({"key": "EmbedThumbnail"})
        else:
            opts["merge_output_format"] = "mkv"
            if self.embed_subs.get():
                opts["writesubtitles"] = True
                opts["writeautomaticsub"] = True
                opts["subtitleslangs"] = ["en.*"]
                postprocessors.append({"key": "FFmpegEmbedSubtitle"})
            if self.embed_meta.get():
                postprocessors.append({"key": "FFmpegMetadata",
                                       "add_metadata": True, "add_chapters": True})
            if self.embed_thumb.get():
                opts["writethumbnail"] = True
                postprocessors.append({"key": "EmbedThumbnail"})

        opts["postprocessors"] = postprocessors

        cookies = self.cookies_var.get()
        if cookies and cookies != "None":
            opts["cookiesfrombrowser"] = (cookies,)

        ffloc = self.ffmpeg_var.get().strip()
        if ffloc:
            opts["ffmpeg_location"] = ffloc

        return opts

    # ----- finish ----------------------------------------------------------
    def _finish(self, success):
        """Push the terminal message. On natural completion include a summary
        dict so the UI can show the completion popup."""
        summary = None
        if success and not self.cancel_event.is_set():
            elapsed = max(0, int(time.monotonic() - self.run_started))
            summary = {
                "folder": self.out_dir,
                "elapsed": elapsed,
                "total": self.total_videos,
                "downloaded": self.completed_count,
                "already": self.pre_done,
            }
        self.msg_queue.put(("finished", summary))

    def _scan_and_count(self, urls, opts):
        """Pre-scan all URLs, set total_videos / pre_done, prime the overall
        bar, and return the flat list of video entries. None if cancelled."""
        self.msg_queue.put(("status", "Scanning playlists for video count..."))
        try:
            entries = self._scan_entries(urls, opts)
        except DownloadCancelled:
            self.msg_queue.put(("status", "Stopped."))
            self.msg_queue.put(("finished", None))
            return None
        except Exception as e:  # noqa: BLE001
            self.msg_queue.put(("log", f"WARNING: could not pre-scan ({e})"))
            entries = []

        # Build the "already done" picture from the per-folder JSON logs, plus
        # any legacy downloaded.txt (so existing archives aren't re-fetched).
        self.legacy_done = self._read_archive_ids(
            os.path.join(self.out_dir, "downloaded.txt"))
        self.done_cache = self._build_done_cache(entries, self.out_dir)
        self.total_videos = len(entries)
        self.pre_done = sum(
            1 for e in entries
            if self._is_done(self._entry_folder(e, self.out_dir), e["id"]))

        # Title the batch with the actual generated folder name(s).
        folders = []
        for e in entries:
            name = os.path.basename(self._entry_folder(e, self.out_dir))
            if name not in folders:
                folders.append(name)
        if len(folders) == 1:
            self.msg_queue.put(("batch", folders[0]))
        elif len(folders) > 1:
            self.msg_queue.put(("batch", f"{folders[0]} (+{len(folders) - 1} more)"))

        if self.total_videos:
            self.msg_queue.put((
                "overall",
                self.pre_done / self.total_videos,
                f"{self.pre_done} of {self.total_videos} already archived",
            ))
            self.msg_queue.put((
                "log",
                f"Found {self.total_videos} video(s); "
                f"{self.pre_done} already downloaded.",
            ))
        else:
            self.msg_queue.put(("overall_indeterminate", None))
        return entries

    # ----- per-folder JSON log (progress tracker + dedup record) -----------
    def _folder_for(self, channel, playlist_title, outdir):
        """Subfolder is 'Channel - Project' (or just the project / 'Videos' when
        the channel is unknown), to keep different channels' folders distinct."""
        project = playlist_title or "Videos"
        name = f"{channel} - {project}" if channel else project
        return os.path.join(outdir, self._safe_folder(name))

    def _entry_folder(self, entry, outdir):
        return self._folder_for(entry.get("channel"), entry.get("playlist_title"),
                                outdir)

    def _log_path(self, folder_dir):
        return os.path.join(folder_dir, self.LOG_NAME)

    def _load_log(self, folder_dir):
        try:
            with open(self._log_path(folder_dir), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {"videos": {}}

    def _build_done_cache(self, entries, outdir):
        """folder_dir -> set(ids marked completed), loaded once before downloads."""
        cache = {}
        for folder in {self._entry_folder(e, outdir) for e in entries}:
            data = self._load_log(folder)
            cache[folder] = {vid for vid, rec in data.get("videos", {}).items()
                             if rec.get("status") == "completed"}
        return cache

    def _is_done(self, folder_dir, vid):
        if vid and vid in self.legacy_done:
            return True
        return vid in self.done_cache.get(folder_dir, ())

    def _log_write(self, meta):
        """Persist one video's record into its folder's JSON log (thread-safe)."""
        folder = meta["folder"]
        rec = meta["rec"]
        with self._log_lock:
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                pass
            data = self._load_log(folder)
            if meta.get("playlist_title") and not data.get("playlist_title"):
                data["playlist_title"] = meta["playlist_title"]
            if meta.get("source") and not data.get("source_url"):
                data["source_url"] = meta["source"]
            data.setdefault("videos", {})[rec["id"]] = rec
            try:
                with open(self._log_path(folder), "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2, ensure_ascii=False)
            except OSError as e:  # noqa: BLE001
                self.msg_queue.put(("log", f"WARNING: could not write log ({e})"))

    @staticmethod
    def _now():
        return datetime.now().isoformat(timespec="seconds")

    def _note_start(self, folder, vid, title, index, source, playlist_title):
        if not vid:
            return
        with self._lock:
            if vid in self.video_meta:
                return
            meta = self.video_meta[vid] = {
                "folder": folder, "playlist_title": playlist_title, "source": source,
                "rec": {"id": vid, "title": title, "index": index,
                        "status": "downloading", "download_started": self._now(),
                        "download_finished": None, "processing_finished": None,
                        "file": None},
            }
        self._log_write(meta)

    def _note_download_finished(self, vid):
        with self._lock:
            meta = self.video_meta.get(vid)
            if not meta or meta["rec"]["download_finished"]:
                return
            meta["rec"]["download_finished"] = self._now()
            meta["rec"]["status"] = "processing"
        self._log_write(meta)

    def _note_file(self, vid, filepath):
        if not (vid and filepath):
            return
        with self._lock:
            meta = self.video_meta.get(vid)
            if not meta:
                return
            meta["rec"]["file"] = os.path.basename(filepath)

    def _finalize(self, vid):
        if not vid:
            return
        with self._lock:
            meta = self.video_meta.get(vid)
            if not meta or meta["rec"]["status"] == "completed":
                return
            meta["rec"]["processing_finished"] = self._now()
            meta["rec"]["status"] = "completed"
            self.done_ids.add(vid)
            self.completed_count += 1
        self._log_write(meta)
        self.msg_queue.put(("ok", None))  # a success resets the failure streak

    # ----- the dynamic worker pool -----------------------------------------
    def _run_pool(self, urls, opts):
        self.msg_queue.put((
            "log", f"Archiving {len(urls)} URL(s) with {self.target_workers} "
            f"download helper(s)..."))

        entries = self._scan_and_count(urls, opts)
        if entries is None:  # cancelled during scan
            return
        self.counting_enabled = True  # scan done; failures now count

        # One job per video, so any free helper grabs the next remaining one.
        # Already-done videos (per the JSON logs) are skipped up front.
        outdir = self.out_dir
        jobs = queue.Queue()
        remaining = 0
        for e in entries:
            folder = self._entry_folder(e, outdir)
            if self._is_done(folder, e["id"]):
                continue
            jobs.put(self._make_job(e, outdir, folder))
            remaining += 1
        if not entries:
            for u in urls:    # scan failed -> hand each raw URL to a helper
                jobs.put({"url": u, "outtmpl": None, "id": None})
            remaining = len(urls)

        self.msg_queue.put(("log", f"{remaining} video(s) left to download."))
        if remaining == 0:
            self.msg_queue.put(("overall", 1.0, "Everything already archived."))
            self.msg_queue.put(("status", "Done."))
            self._finish(success=True)
            return

        self._manage_pool(jobs, opts)

        if self.cancel_event.is_set():
            self.msg_queue.put(("status", "Stopped."))
            self._finish(success=False)
        else:
            self.msg_queue.put(("overall", 1.0, "All videos processed."))
            self.msg_queue.put(("status", "Done."))
            self._finish(success=True)

    def _manage_pool(self, jobs, opts):
        """Keep the number of live helpers equal to self.target_workers, which
        the user can change at any time. Grows by spawning helpers (each with
        its own wheel); shrinks by asking the highest-numbered helper to retire
        (its wheel goes red, it finishes its current video, then is removed)."""
        workers = {}          # slot_id -> {"thread":..., "retire": Event}
        next_slot = 0
        while not self.cancel_event.is_set():
            # Reap finished helpers and clear their wheels.
            for sid in [s for s, w in workers.items() if not w["thread"].is_alive()]:
                workers.pop(sid)
                self.msg_queue.put(("remove_slot", sid))

            jobs_left = not jobs.empty()
            if not jobs_left and not workers:
                break  # nothing left to do and everyone has gone home

            target = max(1, self.target_workers)
            live = [s for s, w in workers.items() if not w["retire"].is_set()]

            # Scale DOWN: ask highest-numbered live helpers to retire.
            while len(live) > target:
                sid = max(live)
                workers[sid]["retire"].set()
                self.msg_queue.put(("slot_retiring", sid))
                live.remove(sid)

            # Scale UP: only while there is still work to pick up.
            while len(live) < target and jobs_left:
                sid = next_slot
                next_slot += 1
                ev = threading.Event()
                t = threading.Thread(target=self._worker,
                                     args=(sid, jobs, opts, ev), daemon=True)
                workers[sid] = {"thread": t, "retire": ev}
                self.msg_queue.put(("add_slot", sid))
                t.start()
                live.append(sid)
                jobs_left = not jobs.empty()

            time.sleep(0.2)

        # Cancelled (or finished): make sure every helper stops and is cleared.
        for w in workers.values():
            w["retire"].set()
        for sid, w in workers.items():
            w["thread"].join()
            self.msg_queue.put(("remove_slot", sid))

    def _make_job(self, entry, outdir, folder):
        """Per-video job dict. outtmpl reproduces the folder layout:
        <outdir>/<Channel - Project>/<index> - <title> [<id>].<ext>."""
        idx = entry["playlist_index"] or 0
        safe_dir = folder.replace("%", "%%")   # folder is a literal path
        outtmpl = os.path.join(safe_dir, f"{idx:03d} - %(title)s [%(id)s].%(ext)s")
        return {
            "url": entry["url"], "outtmpl": outtmpl, "id": entry["id"],
            "title": entry.get("title"), "index": idx, "folder": folder,
            "playlist_title": entry["playlist_title"], "source": entry["url"],
        }

    def _worker(self, slot, jobs, base_opts, retire):
        """One helper, bound to one wheel. Pulls videos until the queue is
        empty, it's asked to retire, or the run is cancelled. A retire request
        only takes effect between videos, so the current download finishes."""
        while not self.cancel_event.is_set() and not retire.is_set():
            try:
                job = jobs.get_nowait()
            except queue.Empty:
                break
            self.slot_meta[slot] = job
            job_opts = dict(base_opts)
            if job.get("outtmpl"):
                job_opts["outtmpl"] = job["outtmpl"]
            job_opts["progress_hooks"] = [lambda d, s=slot: self._progress_hook(d, s)]
            job_opts["postprocessor_hooks"] = [lambda d, s=slot: self._pp_hook(d, s)]
            ok = True
            try:
                with YoutubeDL(job_opts) as ydl:
                    retcode = ydl.download([job["url"]])
                # With ignoreerrors a failed video doesn't raise; the return
                # code tells us. None (older yt-dlp) is treated as success.
                ok = retcode in (0, None)
            except DownloadCancelled:
                ok = False
            except Exception as e:  # noqa: BLE001
                ok = False
                self.msg_queue.put(("log", f"ERROR (helper {slot + 1}): {e}"))
            if ok and not self.cancel_event.is_set() and job.get("id"):
                self._finalize(job["id"])   # stamps processing_finished + completed
            with self._lock:
                self.slot_id[slot] = None
                self.slot_frac[slot] = 0.0
            if not ok:
                break
            self._emit_overall_parallel()
        with self._lock:
            self.slot_id.pop(slot, None)
            self.slot_frac.pop(slot, None)
        self._emit_overall_parallel()

    def _scan_entries(self, urls, opts):
        """Flat-extract every URL into a flat list of video entries:
            {id, url, playlist_title, playlist_index}
        This drives both the total count and the parallel work queue. Raises
        DownloadCancelled if the user hits Stop mid-scan."""
        scan_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "ignoreerrors": True,
            "logger": QueueLogger(self.msg_queue, self.cancel_event),
        }
        if "cookiesfrombrowser" in opts:
            scan_opts["cookiesfrombrowser"] = opts["cookiesfrombrowser"]

        entries = []
        with YoutubeDL(scan_opts) as ydl:
            for url in urls:
                if self.cancel_event.is_set():
                    raise DownloadCancelled()
                info = ydl.extract_info(url, download=False)
                if not info:
                    continue
                children = info.get("entries")
                if children is None:
                    # A standalone video URL (not a playlist).
                    if info.get("id"):
                        entries.append({
                            "id": info["id"],
                            "url": self._watch_url(info),
                            "title": info.get("title"),
                            "channel": info.get("channel") or info.get("uploader"),
                            "playlist_title": None,
                            "playlist_index": None,
                        })
                else:
                    title = info.get("title")
                    pl_channel = (info.get("channel") or info.get("uploader")
                                  or info.get("uploader_id"))
                    pos = 0
                    for entry in children:
                        if not entry or not entry.get("id"):
                            continue
                        pos += 1
                        entries.append({
                            "id": entry["id"],
                            "url": self._watch_url(entry),
                            "title": entry.get("title"),
                            # the video's own channel, falling back to the
                            # playlist owner so the whole playlist stays together
                            "channel": (entry.get("channel") or entry.get("uploader")
                                        or pl_channel),
                            "playlist_title": title,
                            "playlist_index": entry.get("playlist_index") or pos,
                        })
        return entries

    @staticmethod
    def _watch_url(entry):
        """A direct watch URL for a video entry (avoids re-paging the playlist
        for every item, and lets any free worker grab any remaining video)."""
        url = entry.get("url") or entry.get("webpage_url")
        if url and url.startswith("http"):
            return url
        return f"https://www.youtube.com/watch?v={entry['id']}"

    def _safe_folder(self, title):
        """Filesystem-safe playlist folder name, matching yt-dlp where possible."""
        if not title:
            return "Videos"
        name = None
        if _ytdlp_sanitize:
            try:
                name = _ytdlp_sanitize(title, restricted=False)
            except Exception:  # noqa: BLE001
                name = None
        if not name:
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip().rstrip(". ")
        return name or "Videos"

    @staticmethod
    def _read_archive_ids(archive_path):
        """Return the set of video ids already recorded in download_archive.

        Archive lines look like 'youtube dQw4w9WgXcQ'; we key on the last
        whitespace-separated token so any extractor prefix works."""
        ids = set()
        if archive_path and os.path.exists(archive_path):
            try:
                with open(archive_path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            ids.add(line.split()[-1])
            except OSError:
                pass
        return ids

    def _progress_hook(self, d, slot=None):
        # Runs inside a worker thread -- only push messages, never touch Tk.
        if self.cancel_event.is_set():
            raise DownloadCancelled()
        status = d.get("status")
        info = d.get("info_dict") or {}
        vid = info.get("id") or info.get("display_id")
        title = info.get("title")

        if slot is not None:
            self._progress_hook_slot(d, slot, status, vid, title)
            return

        # Detect when we've moved on to a new video and finalise the previous.
        if vid and vid != self.current_id:
            if self.current_id is not None:
                self._finalize(self.current_id)
            self.current_id = vid
            self.current_id_frac = 0.0

        # Keep the title line in sync with whatever we're working on now.
        if title and title != self.current_title:
            self.current_title = title
            self.msg_queue.put(("video_title", title))

        if status == "downloading":
            folder = self._folder_for(info.get("channel") or info.get("uploader"),
                                      info.get("playlist_title"), self.out_dir)
            self._note_start(folder, vid, title, info.get("playlist_index") or 0,
                             info.get("webpage_url") or info.get("original_url"),
                             info.get("playlist_title"))
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes", 0)
            frac = (done / total) if total else 0.0
            # A merged download has separate video/audio streams; keep the
            # per-video fraction monotonic so the overall bar never jumps back.
            self.current_id_frac = max(self.current_id_frac, frac)
            self.msg_queue.put(("video", frac, self._stats_line(d, frac)))
            self._emit_overall()

        elif status == "finished":
            # The download is done; postprocessing (merge/embed) comes next and
            # is reported separately via _pp_hook. Sweep the bar here too so the
            # gap before ffmpeg starts doesn't park it at a frozen 100%.
            self._note_download_finished(vid)
            self._note_file(vid, (info.get("filepath") or d.get("filename")))
            self.current_id_frac = 1.0
            self.msg_queue.put(("video_busy", "Download complete - processing..."))
            self._emit_overall()

    def _progress_hook_slot(self, d, slot, status, vid, title):
        """Parallel-mode progress: report to one wheel and the overall bar."""
        job = self.slot_meta.get(slot) or {}
        with self._lock:
            self.slot_id[slot] = vid
        label = title or job.get("title") or "(loading...)"
        if status == "downloading":
            info = d.get("info_dict") or {}
            folder = job.get("folder") or self._folder_for(
                info.get("channel") or info.get("uploader"),
                info.get("playlist_title"), self.out_dir)
            self._note_start(folder, vid, label, job.get("index", 0),
                             job.get("source"), job.get("playlist_title"))
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes", 0)
            frac = (done / total) if total else 0.0
            with self._lock:
                self.slot_frac[slot] = max(self.slot_frac.get(slot, 0.0), frac)
            self.msg_queue.put(("slot_progress", slot, frac, label,
                                self._stats_line(d, frac)))
            self._emit_overall_parallel()
        elif status == "finished":
            self._note_download_finished(vid)
            self._note_file(vid, ((d.get("info_dict") or {}).get("filepath")
                                   or d.get("filename")))
            with self._lock:
                self.slot_frac[slot] = 1.0
            self.msg_queue.put(("slot_busy", slot, label,
                                "Download complete - processing..."))
            self._emit_overall_parallel()

    def _stats_line(self, d, frac):
        # Build the stats line from the raw NUMERIC fields. yt-dlp's
        # pre-formatted *_str values embed terminal colour codes that show
        # up as little square boxes in a Tk label, so we never use them.
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        done = d.get("downloaded_bytes", 0)
        speed = d.get("speed")        # bytes/sec, or None between samples
        eta = d.get("eta")            # whole seconds, or None
        parts = [f"{frac * 100:.1f}%"]
        if total:
            parts.append(f"{self._human_bytes(done)} / {self._human_bytes(total)}")
        elif done:
            parts.append(self._human_bytes(done))
        if speed:
            parts.append(f"{self._human_bytes(speed)}/s")
        if eta is not None:
            parts.append(f"ETA {self._fmt_eta(eta)}")
        return "    ".join(parts)

    def _pp_hook(self, d, slot=None):
        # Postprocessing (ffmpeg merge, embed subs/thumb/metadata, mp3 extract).
        # No byte fraction is available, so we spin the wheel / sweep the bar and
        # say what's happening instead of leaving it stuck at a solid 100%.
        if self.cancel_event.is_set():
            raise DownloadCancelled()
        info = d.get("info_dict") or {}
        title = info.get("title")
        vid = info.get("id") or info.get("display_id")
        # Track the final output file path as postprocessors rewrite it.
        self._note_file(vid, info.get("filepath"))

        if slot is not None:
            if d.get("status") != "started":
                return
            label = self._pp_label(d.get("postprocessor", "") or "")
            self.msg_queue.put(("slot_busy", slot, title or "(processing)", label))
            return

        if title and title != self.current_title:
            self.current_title = title
            self.msg_queue.put(("video_title", title))
        if d.get("status") != "started":
            return
        label = self._pp_label(d.get("postprocessor", "") or "")
        self.msg_queue.put(("video_busy", label))

    def _emit_overall_parallel(self):
        """Overall progress while several downloads run at once."""
        if not self.total_videos:
            return
        with self._lock:
            completed = self.pre_done + len(self.done_ids)
            partial = sum(
                f for s, f in self.slot_frac.items()
                if self.slot_id.get(s) and self.slot_id[s] not in self.done_ids)
            active = sum(1 for s in self.slot_id if self.slot_id.get(s))
        frac = max(0.0, min((completed + partial) / self.total_videos, 1.0))
        shown = min(completed + active, self.total_videos)
        self.msg_queue.put(("overall", frac,
                            f"{shown} of {self.total_videos} videos"))

    @staticmethod
    def _human_bytes(n):
        try:
            n = float(n)
        except (TypeError, ValueError):
            return "?"
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if n < 1024 or unit == "TiB":
                return f"{n:.1f} {unit}"
            n /= 1024

    @staticmethod
    def _fmt_eta(secs):
        try:
            secs = int(secs)
        except (TypeError, ValueError):
            return "?"
        h, rem = divmod(max(secs, 0), 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h:d}:{m:02d}:{s:02d}"
        return f"{m:d}:{s:02d}"

    @staticmethod
    def _pp_label(name):
        n = name.lower()
        if "merger" in n:
            return "Merging video + audio... (large files can take a while)"
        if "extractaudio" in n:
            return "Extracting audio to MP3..."
        if "videoconvert" in n or "videoremux" in n:
            return "Converting / remuxing video..."
        if "subtitle" in n:
            return "Embedding subtitles..."
        if "thumbnail" in n:
            return "Embedding thumbnail..."
        if "metadata" in n:
            return "Writing metadata + chapters..."
        if "movefiles" in n:
            return "Finalizing file..."
        if "fixup" in n:
            return "Fixing up file..."
        return f"Processing ({name})..."

    def _emit_overall(self):
        """Push an overall-progress update based on whole-video accounting."""
        if not self.total_videos:
            return  # unknown total -> indeterminate bar, nothing to compute
        completed = self.pre_done + len(self.done_ids)
        # current_id_frac contributes only if the current video is a NEW one
        # (already-archived videos were counted in pre_done and fire no hooks).
        partial = self.current_id_frac if self.current_id not in self.done_ids else 0.0
        frac = (completed + partial) / self.total_videos
        frac = max(0.0, min(frac, 1.0))
        shown = min(completed + 1, self.total_videos)
        self.msg_queue.put(("overall", frac, f"Video {shown} of {self.total_videos}"))

    # ----- queue polling on the main thread --------------------------------
    # ----- smooth "busy" sweep (no theme-dependent indeterminate flicker) ---
    def _start_busy(self, bar):
        """Continuously sweep a determinate bar left->right to show activity.

        ttk's built-in indeterminate mode renders as ugly blinking blocks on
        the native Windows theme, so we animate a plain determinate fill
        ourselves -- smooth and identical on every platform."""
        if self._busy.get(bar):
            return  # already sweeping
        bar.configure(value=0)
        self._busy_tick(bar)

    def _busy_tick(self, bar):
        v = float(bar["value"]) + 0.002
        if v >= 1.0:
            v = 0.0
        bar.configure(value=v)
        self._busy[bar] = self.root.after(20, lambda: self._busy_tick(bar))

    def _stop_busy(self, bar):
        after_id = self._busy.pop(bar, None)
        if after_id is not None:
            self.root.after_cancel(after_id)

    # ----- queue polling on the main thread --------------------------------
    def _drain_queue(self):
        try:
            while True:
                kind, *rest = self.msg_queue.get_nowait()
                if kind == "log":
                    self._log(rest[0])
                elif kind == "overall":
                    frac, label = rest
                    self._stop_busy(self.overall_progress)
                    self.overall_progress.configure(value=frac)
                    self.overall_status.set(label)
                elif kind == "overall_indeterminate":
                    self._start_busy(self.overall_progress)
                    self.overall_status.set("Downloading (total count unknown)...")
                elif kind == "video":
                    frac, label = rest
                    self._stop_busy(self.video_progress)
                    self.video_progress.configure(value=frac)
                    self.video_status.set(label)
                elif kind == "video_busy":
                    # Postprocessing: sweep the bar so it doesn't look frozen.
                    self._start_busy(self.video_progress)
                    self.video_status.set(rest[0])
                elif kind == "video_title":
                    self.video_title.set("Current video:  " + rest[0])
                elif kind == "slot_progress":
                    slot, frac, title, status = rest
                    if slot in self.slots:
                        self.slots[slot].set_progress(frac, title, status)
                elif kind == "slot_busy":
                    slot, title, status = rest
                    if slot in self.slots:
                        self.slots[slot].set_busy(title, status)
                elif kind == "slot_idle":
                    slot = rest[0]
                    if slot in self.slots:
                        self.slots[slot].set_idle("Done", "")
                elif kind == "add_slot":
                    self._add_slot(rest[0])
                elif kind == "remove_slot":
                    self._remove_slot(rest[0])
                elif kind == "slot_retiring":
                    slot = rest[0]
                    if slot in self.slots:
                        self.slots[slot].set_retiring()
                elif kind == "status":
                    self.overall_status.set(rest[0])
                elif kind == "batch":
                    self.batch_title.set("Batch: " + rest[0])
                elif kind == "ok":
                    self.consec_errors = 0   # a success breaks the failure streak
                elif kind == "error":
                    self._handle_error_event(rest[0])
                elif kind == "finished":
                    summary = rest[0] if rest else None
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self._set_inputs_state(running=False)
                    self._stop_blink()
                    self._stop_busy(self.overall_progress)
                    self._stop_busy(self.video_progress)
                    self.video_progress.configure(value=0)
                    self.video_title.set("Current video:")
                    self.video_status.set("")
                    for wheel in list(self.slots.values()):
                        wheel.destroy()
                    self.slots = {}
                    self._relayout_slots()
                    if self.failed_triggered:
                        self._show_failed(self._failure_summary())
                    elif summary:
                        self._show_complete(summary)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    # ----- error handling --------------------------------------------------
    def _handle_error_event(self, msg):
        if not self.counting_enabled or self.failed_triggered:
            return
        self.error_count += 1
        self.consec_errors += 1
        self.last_error_msg = msg
        cat = self._classify_error(msg)
        self.error_cats[cat] = self.error_cats.get(cat, 0) + 1
        if self.consec_errors >= ERROR_LIMIT:
            self.failed_triggered = True
            self.cancel_event.set()        # stop the worker(s) cleanly
            self.overall_status.set("Stopping after repeated errors...")
            self._start_blink()

    def _dominant_category(self):
        if not self.error_cats:
            return "other"
        return max(self.error_cats, key=self.error_cats.get)

    def _failure_summary(self):
        return {
            "folder": self.out_dir,
            "elapsed": max(0, int(time.monotonic() - self.run_started)),
            "total": self.total_videos,
            "downloaded": self.completed_count,
            "failed": self.error_count,
            "category": self._dominant_category(),
            "message": self.last_error_msg,
        }

    @staticmethod
    def _fmt_elapsed(seconds):
        mins, secs = divmod(int(seconds), 60)
        hrs, mins = divmod(mins, 60)
        if hrs:
            return f"{hrs}h {mins}m {secs}s"
        if mins:
            return f"{mins}m {secs}s"
        return f"{secs}s"

    # ----- completion popup ------------------------------------------------
    def _show_complete(self, summary):
        win, frm, row = self._popup_skeleton("Archive complete",
                                             "Archive complete", "#2e7d32")
        rows = [
            ("Batch folder:", os.path.basename(os.path.normpath(summary["folder"]))),
            ("Downloaded this run:", str(summary["downloaded"])),
            ("Already archived:", str(summary["already"])),
            ("Total videos:", str(summary["total"])),
            ("Time taken:", self._fmt_elapsed(summary["elapsed"])),
        ]
        if summary.get("failed"):
            rows.append(("Failed / skipped:", str(summary["failed"])))
        row = self._popup_rows(frm, rows, row)
        self._popup_buttons(win, frm, row, summary["folder"])
        self._popup_place(win)

    # ----- failure popup ---------------------------------------------------
    def _show_failed(self, summary):
        title, advice = ERROR_INFO.get(summary["category"], ERROR_INFO["other"])
        win, frm, row = self._popup_skeleton("Archive failed",
                                             "Archive stopped - " + title, "#c62828")
        rows = [
            ("Batch folder:", os.path.basename(os.path.normpath(summary["folder"]))),
            ("Downloaded before stopping:", str(summary["downloaded"])),
            ("Failed:", str(summary["failed"])),
            ("Total videos:", str(summary["total"])),
            ("Time taken:", self._fmt_elapsed(summary["elapsed"])),
        ]
        row = self._popup_rows(frm, rows, row)

        ttk.Separator(frm, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=8)
        row += 1
        ttk.Label(frm, text="What to do:", font=("", 9, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w")
        row += 1
        ttk.Label(frm, text=advice, wraplength=520, justify="left").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(2, 4))
        row += 1
        if summary.get("message"):
            sample = summary["message"]
            if len(sample) > 240:
                sample = sample[:240] + "..."
            ttk.Label(frm, text="Error detail:", font=("", 8, "bold"),
                      foreground="#666").grid(row=row, column=0, columnspan=2,
                                              sticky="w", pady=(6, 0))
            row += 1
            ttk.Label(frm, text=sample, wraplength=520, justify="left",
                      foreground="#666", font=("", 8)).grid(
                row=row, column=0, columnspan=2, sticky="w")
            row += 1
        self._popup_buttons(win, frm, row, summary["folder"])
        self._popup_place(win)

    # ----- shared popup building -------------------------------------------
    def _popup_skeleton(self, win_title, heading, colour):
        win = tk.Toplevel(self.root)
        win.title(win_title)
        win.transient(self.root)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=heading, font=("", 13, "bold"), foreground=colour,
                  wraplength=520, justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        return win, frm, 1

    @staticmethod
    def _popup_rows(frm, rows, start_row):
        for i, (k, v) in enumerate(rows):
            r = start_row + i
            ttk.Label(frm, text=k).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=2)
            ttk.Label(frm, text=v, font=("", 9, "bold")).grid(
                row=r, column=1, sticky="w", pady=2)
        return start_row + len(rows)

    def _popup_buttons(self, win, frm, row, folder):
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Open archive folder",
                   command=lambda: self._open_folder(folder)).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="left", padx=4)

    def _popup_place(self, win):
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.lift()
        win.focus_force()

    # =====================================================================
    #  SECRET 90s KEYGEN CRACKER DOOMSDAY MODE
    # =====================================================================
    def _on_close(self):
        """Real close (X button). If an Alt+F4 just fired the easter egg, this
        close request IS that Alt+F4 - so swallow it instead of quitting."""
        if time.monotonic() - self._altf4_guard < 0.6:
            return
        try:
            self.player.stop()
        except Exception:  # noqa: BLE001
            pass
        self.root.destroy()

    def _toggle_keygen(self, event=None):
        self._altf4_guard = time.monotonic()   # guards the WM close that follows
        self.keygen_on = not self.keygen_on
        self._apply_keygen(self.keygen_on)
        return "break"   # swallow Alt+F4 so it does NOT close the window

    def _make_keygen_theme(self):
        """A neon ttk theme so the whole UI goes full crack-intro when toggled."""
        if "keygen" in self.style.theme_names():
            return
        f = ("Consolas", 9, "bold")
        self.style.theme_create("keygen", parent="clam", settings={
            ".": {"configure": {"background": KG_BG, "foreground": KG_GREEN,
                                "fieldbackground": KG_BG2, "bordercolor": KG_CYAN,
                                "lightcolor": KG_MAGENTA, "darkcolor": KG_BG,
                                "font": f}},
            "TFrame": {"configure": {"background": KG_BG}},
            "TLabel": {"configure": {"background": KG_BG, "foreground": KG_GREEN,
                                     "font": f}},
            "TButton": {"configure": {"background": KG_BG2, "foreground": KG_CYAN,
                                      "relief": "raised", "borderwidth": 3,
                                      "font": f},
                        "map": {"background": [("active", KG_MAGENTA),
                                               ("disabled", "#1a1a1a")],
                                "foreground": [("active", KG_BG),
                                               ("disabled", "#555555")]}},
            "TLabelframe": {"configure": {"background": KG_BG, "relief": "ridge",
                                          "borderwidth": 3, "bordercolor": KG_MAGENTA}},
            "TLabelframe.Label": {"configure": {"background": KG_BG,
                                                "foreground": KG_MAGENTA, "font": f}},
            "TCheckbutton": {"configure": {"background": KG_BG, "foreground": KG_GREEN,
                                           "font": f},
                             "map": {"background": [("active", KG_BG)]}},
            "TCombobox": {"configure": {"fieldbackground": KG_BG2,
                                        "background": KG_BG2, "foreground": KG_CYAN,
                                        "arrowcolor": KG_MAGENTA}},
            "TEntry": {"configure": {"fieldbackground": KG_BG2,
                                     "foreground": KG_GREEN, "insertcolor": KG_GREEN}},
            "Horizontal.TProgressbar": {"configure": {"background": KG_GREEN,
                                                       "troughcolor": KG_BG2,
                                                       "bordercolor": KG_CYAN}},
        })

    def _build_keygen_banner(self):
        """Top chrome: ASCII logo, scrolling greetz, and chiptune controls.
        Built once, hidden until Alt+F4 summons it."""
        self.kg_banner = tk.Frame(self.root, bg=KG_BG, bd=3, relief="ridge",
                                  highlightbackground=KG_MAGENTA, highlightthickness=2)
        tk.Label(self.kg_banner, text=KEYGEN_LOGO, font=("Consolas", 8, "bold"),
                 fg=KG_GREEN, bg=KG_BG, justify="center").pack(fill="x")

        self.kg_marquee = tk.Label(self.kg_banner, text="", font=("Consolas", 10, "bold"),
                                   fg=KG_YELLOW, bg=KG_BG2, anchor="w")
        self.kg_marquee.pack(fill="x", padx=4, pady=(2, 4))

        bar = tk.Frame(self.kg_banner, bg=KG_BG)
        bar.pack(fill="x", padx=4, pady=(0, 4))
        note = "CHIPTUNE:" if self.player.available else "CHIPTUNE (no audio on this OS):"
        tk.Label(bar, text=note, font=("Consolas", 9, "bold"),
                 fg=KG_CYAN, bg=KG_BG).pack(side="left", padx=(2, 6))

        def cheese_btn(txt, cmd):
            return tk.Button(bar, text=txt, command=cmd, font=("Consolas", 9, "bold"),
                             fg=KG_BG, bg=KG_GREEN, activebackground=KG_MAGENTA,
                             activeforeground=KG_BG, relief="raised", bd=3, padx=6)
        cheese_btn("\u25B6 PLAY", self._kg_play).pack(side="left", padx=2)
        cheese_btn("\u25A0 STOP", self._kg_stop).pack(side="left", padx=2)
        cheese_btn("\u00BB NEXT TRAX", self._kg_next).pack(side="left", padx=2)
        self.kg_trax = tk.Label(bar, text="TRAX 1/3", font=("Consolas", 9, "bold"),
                                fg=KG_MAGENTA, bg=KG_BG)
        self.kg_trax.pack(side="left", padx=8)

        tk.Label(bar, text="TEMPO", font=("Consolas", 8, "bold"),
                 fg=KG_CYAN, bg=KG_BG).pack(side="left", padx=(8, 0))
        self.kg_tempo = tk.Scale(bar, from_=120, to=480, orient="horizontal",
                                 length=110, showvalue=False, command=self._kg_tempo,
                                 bg=KG_BG, fg=KG_GREEN, troughcolor=KG_BG2,
                                 highlightthickness=0, sliderrelief="raised")
        self.kg_tempo.set(self.player.bpm)
        self.kg_tempo.pack(side="left")

    # -- music control handlers --
    def _kg_play(self):
        self.player.play()

    def _kg_stop(self):
        self.player.stop()

    def _kg_next(self):
        self.player.next_track()
        self.kg_trax.configure(text=f"TRAX {self.player.track + 1}/{len(CHIP_TUNES)}")
        if self.player._thread and self.player._thread.is_alive():
            self.player.play()  # restart on the new track

    def _kg_tempo(self, val):
        self.player.bpm = int(float(val))

    # -- scrolling greetz marquee --
    def _start_marquee(self):
        self._marquee_i = 0
        self._marquee_src = KEYGEN_GREETZ + "   "
        self._marquee_tick()

    def _marquee_tick(self):
        width = 72
        s = self._marquee_src
        i = self._marquee_i % len(s)
        shown = (s + s)[i:i + width]
        self.kg_marquee.configure(text=shown)
        self._marquee_i = (self._marquee_i + 1) % len(s)
        self._marquee_after = self.root.after(110, self._marquee_tick)

    def _stop_marquee(self):
        if self._marquee_after is not None:
            self.root.after_cancel(self._marquee_after)
            self._marquee_after = None

    # -- theme + tk widget recolouring --
    def _apply_keygen(self, on):
        if on:
            self._pre_keygen_geom = self.root.geometry()
            self.style.theme_use("keygen")
            self._recolor_tk(True)
            self.kg_banner.pack(side="top", fill="x", before=self.main_frame)
            self._start_marquee()
            self.player.play()
            self.root.title("SunnyZ YouTube Archiver  ::  [ CRACKED BY THE SCENE ]")
            h = self.root.winfo_height()
            self.root.geometry(f"745x{min(max(h, 1040), 1500)}")
            self._sync_opts_veil()
        else:
            self._stop_marquee()
            self.player.stop()
            self.kg_banner.pack_forget()
            self.style.theme_use(self._orig_theme)
            self._recolor_tk(False)
            self.root.title("SunnyZ YouTube Archiver")
            if getattr(self, "_pre_keygen_geom", None):
                self.root.geometry(self._pre_keygen_geom)
            self._sync_opts_veil()

    def _recolor_tk(self, on):
        """Recolour the non-ttk widgets (the Text boxes + the red stop note),
        which the ttk theme can't reach. Restores originals when toggled off."""
        targets = [
            (self.root, {"bg": KG_BG}),
            (self.url_text, {"bg": KG_BG2, "fg": KG_GREEN, "insertbackground": KG_GREEN}),
            (self.log, {"bg": "#01030a", "fg": KG_GREEN, "insertbackground": KG_GREEN}),
            (self.stop_note_lbl, {"bg": KG_BG}),
        ]
        for widget, opts in targets:
            if on:
                if widget not in self._tk_defaults:
                    self._tk_defaults[widget] = {k: widget.cget(k) for k in opts}
                try:
                    widget.configure(**opts)
                except tk.TclError:
                    pass
            else:
                saved = self._tk_defaults.pop(widget, None)
                if saved:
                    try:
                        widget.configure(**saved)
                    except tk.TclError:
                        pass


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()