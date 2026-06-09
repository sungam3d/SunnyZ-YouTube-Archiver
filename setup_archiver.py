#!/usr/bin/env python3
"""
setup_archiver.py
-----------------
One-shot installer for everything the YouTube Playlist Archiver GUI needs:

  1. yt-dlp (with the [default] extras + curl_cffi for impersonation)
  2. A JavaScript runtime  -> Deno (required by YouTube in 2026)
  3. ffmpeg + ffprobe      -> for merging video/audio and embedding extras

It only installs what's missing, prints clear progress, and tells you exactly
what to do if a step needs your attention.

Run it once:

    python setup_archiver.py

Then (importantly) CLOSE and REOPEN your terminal so newly-installed tools are
picked up on your PATH, and launch the GUI:

    python yt_archiver_gui.py
"""

import os
import platform
import shutil
import subprocess
import sys

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"


def step(n, total, text):
    print(f"\n[{n}/{total}] {text}")
    print("-" * 60)


def ok(text):
    print(f"  OK   {text}")


def warn(text):
    print(f"  !!   {text}")


def run(cmd, **kw):
    """Run a command, streaming its output. Returns True on success."""
    print(f"  ->   {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    try:
        subprocess.run(cmd, check=True, **kw)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        warn(f"command failed: {e}")
        return False


# ---------------------------------------------------------------------------
# 1. Python packages
# ---------------------------------------------------------------------------
def install_python_packages():
    step(1, 3, "Installing / upgrading yt-dlp and friends (pip)")
    # "yt-dlp[default]" pulls in the EJS challenge-solver scripts YouTube needs.
    # curl_cffi adds browser impersonation (clears the warning you saw).
    success = run([sys.executable, "-m", "pip", "install", "-U",
                   "yt-dlp[default]", "curl_cffi"])
    if success:
        ok("yt-dlp[default] and curl_cffi are installed/up to date.")
    else:
        warn("pip install failed. Make sure pip works: "
             "'python -m pip --version', then re-run this script.")
    return success


# ---------------------------------------------------------------------------
# 2. JavaScript runtime (Deno)
# ---------------------------------------------------------------------------
def have_js_runtime():
    for exe in ("deno", "node", "bun", "qjs"):
        if shutil.which(exe):
            return exe
    return None


def install_deno():
    step(2, 3, "Setting up a JavaScript runtime (Deno)")
    existing = have_js_runtime()
    if existing:
        ok(f"A JS runtime is already available ('{existing}'). Nothing to do.")
        return True

    if IS_WINDOWS:
        if shutil.which("winget"):
            if run(["winget", "install", "--id", "DenoLand.Deno",
                    "-e", "--accept-source-agreements",
                    "--accept-package-agreements"]):
                ok("Deno installed via winget.")
                return True
            warn("winget install failed; trying the official installer instead.")
        # Fallback: official PowerShell installer
        if run(["powershell", "-NoProfile", "-Command",
                "irm https://deno.land/install.ps1 | iex"]):
            ok("Deno installed via the official script.")
            return True
    elif IS_MAC:
        if shutil.which("brew") and run(["brew", "install", "deno"]):
            ok("Deno installed via Homebrew.")
            return True
        if run("curl -fsSL https://deno.land/install.sh | sh", shell=True):
            ok("Deno installed via the official script.")
            return True
    else:  # Linux / other
        if run("curl -fsSL https://deno.land/install.sh | sh", shell=True):
            ok("Deno installed via the official script.")
            return True

    warn("Could not install Deno automatically. Install it manually from "
         "https://deno.com  (any JS runtime on PATH works).")
    return False


# ---------------------------------------------------------------------------
# 3. ffmpeg + ffprobe
# ---------------------------------------------------------------------------
def have_ffmpeg():
    return shutil.which("ffmpeg") and shutil.which("ffprobe")


def install_ffmpeg():
    step(3, 3, "Setting up ffmpeg + ffprobe")
    if have_ffmpeg():
        ok("ffmpeg and ffprobe are already on your PATH. Nothing to do.")
        return True

    if IS_WINDOWS:
        if shutil.which("winget"):
            if run(["winget", "install", "--id", "Gyan.FFmpeg",
                    "-e", "--accept-source-agreements",
                    "--accept-package-agreements"]):
                ok("ffmpeg installed via winget (on PATH after you reopen the terminal).")
                return True
            warn("winget install failed; trying a direct download instead.")
        return download_ffmpeg_windows()
    elif IS_MAC:
        if shutil.which("brew") and run(["brew", "install", "ffmpeg"]):
            ok("ffmpeg installed via Homebrew.")
            return True
        warn("Install Homebrew (https://brew.sh) then run: brew install ffmpeg")
        return False
    else:  # Linux
        warn("Install ffmpeg with your package manager, e.g.:\n"
             "       sudo apt install ffmpeg   (Debian/Ubuntu)\n"
             "       sudo dnf install ffmpeg   (Fedora)\n"
             "       sudo pacman -S ffmpeg     (Arch)")
        return False


def download_ffmpeg_windows():
    """Last-resort: grab a static Windows build and drop it in ./bin."""
    import json
    import urllib.request
    import zipfile

    print("  ->   Downloading a static ffmpeg build from GitHub (BtbN)...")
    api = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
    try:
        req = urllib.request.Request(api, headers={"User-Agent": "ffmpeg-setup"})
        with urllib.request.urlopen(req) as r:
            data = json.load(r)
        asset = next(
            (a for a in data["assets"]
             if a["name"].endswith("win64-gpl.zip") and "shared" not in a["name"]),
            None)
        if not asset:
            warn("Couldn't find a suitable ffmpeg build to download.")
            return False

        url = asset["browser_download_url"]
        zip_path = os.path.join(os.getcwd(), "_ffmpeg_download.zip")
        urllib.request.urlretrieve(url, zip_path)

        bin_dir = os.path.join(os.getcwd(), "bin")
        os.makedirs(bin_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            for member in z.namelist():
                base = os.path.basename(member).lower()
                if base in ("ffmpeg.exe", "ffprobe.exe"):
                    with z.open(member) as src, \
                         open(os.path.join(bin_dir, os.path.basename(member)), "wb") as dst:
                        shutil.copyfileobj(src, dst)
        os.remove(zip_path)

        ok(f"ffmpeg + ffprobe extracted to:\n       {bin_dir}")
        warn("This folder is NOT on your PATH. In the GUI, set the "
             "'ffmpeg folder' box to the path above.")
        return True
    except Exception as e:  # noqa: BLE001
        warn(f"Direct download failed: {e}")
        warn("Download manually from https://www.gyan.dev/ffmpeg/builds/ and "
             "point the GUI's 'ffmpeg folder' box at the bin directory.")
        return False


# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print(" SunnyZ YouTube Archiver - Environment Setup")
    print(f" Python {sys.version.split()[0]} on {platform.system()}")
    print("=" * 60)

    if sys.version_info < (3, 9):
        warn("Python 3.9+ is required for current yt-dlp. Please upgrade Python.")
        sys.exit(1)

    results = {
        "yt-dlp": install_python_packages(),
        "JS runtime": install_deno(),
        "ffmpeg": install_ffmpeg(),
    }

    print("\n" + "=" * 60)
    print(" Summary")
    print("=" * 60)
    for name, good in results.items():
        print(f"  {'OK ' if good else 'CHECK'}  {name}")

    print("\nNext steps:")
    print("  1. CLOSE this terminal and open a NEW one (so PATH updates apply).")
    print("  2. Run:  SunnyZ-YouTube-Archiver.py")
    if not all(results.values()):
        print("\nSome items need attention -- see the '!!' notes above.")
    print()


if __name__ == "__main__":
    main()
