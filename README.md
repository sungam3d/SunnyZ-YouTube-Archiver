# SunnyZ YouTube Archiver

A friendly desktop app for archiving YouTube **playlists, channels, and individual videos** with [yt-dlp](https://github.com/yt-dlp/yt-dlp). It wraps yt-dlp in a Tkinter GUI that adds a proper progress view, resumable per-folder logs, smart error handling, live-adjustable parallel downloads — and a completely unnecessary secret 90s keygen mode. 🏴‍☠️

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Features](#features)
  - [Input: playlists, videos & channels](#input-playlists-videos--channels)
  - [Quality & format](#quality--format)
  - [Output folders (Channel - Project)](#output-folders-channel--project)
  - [Per-folder JSON progress log & resume](#per-folder-json-progress-log--resume)
  - [Browser cookies](#browser-cookies)
  - [ffmpeg location](#ffmpeg-location)
  - [Embed options](#embed-options)
  - [Pacing: delay between videos](#pacing-delay-between-videos)
  - [Live parallel downloads](#live-parallel-downloads)
  - [Progress display](#progress-display)
  - [Stopping a run](#stopping-a-run)
  - [Completion summary](#completion-summary)
  - [Smart error handling & the Failed popup](#smart-error-handling--the-failed-popup)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Secret keygen mode](#secret-keygen-mode-)
- [Window behaviour](#window-behaviour)
- [Where files end up](#where-files-end-up)
- [Troubleshooting](#troubleshooting)
- [Notes & disclaimers](#notes--disclaimers)

---

## Requirements

- **Python 3.8+** with **Tkinter** (bundled with the standard Windows/macOS Python installers).
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** — the download engine.
- **[ffmpeg](https://ffmpeg.org/)** — required to merge video+audio, make MP3s, and embed thumbnails/subtitles/metadata.
- Designed and tested with Windows in mind (it uses Windows-safe filenames and the optional chiptune uses the Windows-only `winsound`), but the core works on macOS/Linux too.

## Installation

```bash
pip install -U "yt-dlp[default]"
```

Then install ffmpeg and either put it on your `PATH` or point the app at it (see [ffmpeg location](#ffmpeg-location)). Launch the app with:

```bash
python SunnyZ-YouTube-Archiver.py
```

If yt-dlp isn't installed, the app shows a friendly message telling you how to install it.

## Quick start

1. Paste one or more links (one per line) into the top box.
2. Pick a **Quality / stream**.
3. Choose a **Save to folder**.
4. (Optional) set **Browser cookies**, **ffmpeg folder**, delay, embed options, and how many downloads to run at once.
5. Click **Start download**.

---

## Features

### Input: playlists, videos & channels

The top box accepts **one link per line** and mixes types freely:

- Playlists — `https://www.youtube.com/playlist?list=...`
- Single videos — `https://www.youtube.com/watch?v=...`
- Channels — `https://www.youtube.com/@Name/videos`

Before downloading, the app does a fast **flat scan** of every link to count the videos, so it can show an accurate overall total and know what still needs doing.

### Quality & format

A dropdown of presets:

| Preset | What you get |
| --- | --- |
| **Best available (video + audio)** | Highest-quality video+audio, merged |
| **1080p or lower** | Caps resolution at 1080p |
| **720p or lower** | Caps resolution at 720p |
| **480p or lower** | Caps resolution at 480p |
| **360p or lower** | Caps resolution at 360p |
| **Audio only (MP3)** | Extracts audio and converts to MP3 |

### Output folders (Channel - Project)

Downloads are organised into subfolders named **`Channel Name - Project Name`** inside your chosen Save-to folder, e.g. `SunnyZ - VLOGS`. This keeps different channels' playlists from colliding when they share a title.

- Standalone videos go into **`Channel - Videos`**.
- If a channel name can't be determined, it falls back to just the project name (or `Videos`).
- Files are named `NNN - Title [videoid].ext`, where `NNN` is the playlist position, so they sort in order.

The folder name is sanitised the same way yt-dlp would, so naming stays consistent.

### Per-folder JSON progress log & resume

Each generated folder gets its own **`download_log.json`** that acts as a progress tracker *and* the record used to skip already-downloaded videos. For every video it records:

- `id`, `title`, `index`
- `status` (`downloading` → `processing` → `completed`)
- `download_started`, `download_finished`, `processing_finished` timestamps
- the final output `file` name

Because the log lives **inside the download folder**, you can re-run a job at any time and it will **only fetch what's missing** — ideal for resuming big channels or playlists that grow over time. An older single `downloaded.txt` (if present from earlier versions) is still honoured as a fallback so nothing gets re-downloaded.

> Failed videos are **not** marked completed, so they'll be retried on the next run rather than silently skipped.

### Browser cookies

A dropdown to pull cookies straight from a browser you're signed into (`chrome`, `firefox`, `edge`, `brave`, `chromium`, `opera`, `vivaldi`, `safari`, or `None`). Use this for age-restricted, members-only, or "sign in to confirm you're not a bot" situations.

> Close the chosen browser before downloading — browsers lock their cookie database while running.

### ffmpeg location

If ffmpeg isn't on your `PATH`, point the app at the folder containing `ffmpeg`/`ffprobe` with the **Browse** button.

### Embed options

Three toggles, all on by default:

- **Embed subtitles**
- **Embed thumbnail**
- **Embed metadata + chapters**

### Pacing: delay between videos

YouTube throttles or blocks clients that hammer it. A dropdown sets a randomised pause before each video, with a colour-coded warning that updates as you choose:

| Preset | Pause | Note |
| --- | --- | --- |
| **Recommended** | random 5–20 s | Safest; best for channels & large playlists |
| **Balanced** | random 3–10 s | Usually fine for medium playlists |
| **Fast** | random 1–4 s | ⚠️ Higher risk on big jobs |
| **No delay** | none | ⚠️ Highest risk of throttling/blocks |

### Live parallel downloads

A **Simultaneous downloads** selector (1–4) sits to the right of the Start/Stop buttons and is **live** — you can change it *while a run is in progress*:

- **Increase it** → new download "helpers" spin up (each with its own progress wheel) and immediately start pulling remaining videos.
- **Decrease it** → the highest-numbered helper turns **red**, finishes its current video (nothing is lost), then disappears.

Work is shared from a single queue, so no helper sits idle while others still have videos to fetch, and a resume only ever does the work that's actually left.

> More helpers = faster, but it multiplies your request rate to YouTube. **1–2 is safest**, especially paired with a sensible delay.

### Progress display

- **Overall bar** — fills across every video in the whole batch, with a count like `12 of 47 videos`. It accounts for videos already archived, so resuming starts partway instead of at zero.
- **Batch title** — a label showing the actual generated folder name (e.g. `Batch: SunnyZ - VLOGS`), or `(+N more)` when a run spans several folders.
- **Per-download wheels** — one circular progress wheel per active helper:
  - A green arc fills with the download percentage.
  - During post-processing (merging, embedding, MP3 conversion) it switches to a spinning orange arc so it's clearly *working*, never frozen at 100%.
  - The video title and live stats (`47.6%   12.3 MiB / 25.8 MiB   3.1 MiB/s   ETA 0:42`) sit beside it.
- **Activity log** — the raw yt-dlp output, cleaned of terminal colour codes.

### Stopping a run

Click **Stop** and a red, blinking **"Stopping (finishing current process) …"** notice appears next to the button — because yt-dlp has to finish the current file first. Helpers wind down cleanly and the in-flight video still completes and gets logged.

### Completion summary

When everything finishes naturally, a popup shows:

- Batch folder
- Downloaded this run
- Already archived
- Total videos
- Time taken
- (Failed/skipped, if any)

…with an **Open archive folder** button and a **Close** button. (It does **not** appear if you stopped the run yourself.)

### Smart error handling & the Failed popup

The app watches yt-dlp's errors and, after **3 consecutive failures**, assumes the run is doomed (e.g. a bot check on every video), stops automatically, and shows a **Failed** popup. A success in between resets the streak, so a couple of scattered dead videos won't abort a healthy run.

The popup names the **likely cause** and tells you exactly **which settings to change**. Recognised categories include:

- **Bot check / sign-in required** → set Browser cookies, drop to 1 simultaneous download, use the Recommended delay
- **Age-restricted** → set Browser cookies to a signed-in account
- **Rate limited (HTTP 429)** → fewer simultaneous downloads, longer delay, wait a while
- **Private / members-only** → cookies for an account with access (or it's skipped)
- **Video unavailable / removed** → can't be fixed; skipped
- **Region blocked** → cookies / VPN
- **ffmpeg problem** → install ffmpeg and set the ffmpeg folder
- **Network/connection** → check connection, fewer simultaneous downloads
- **Generic fallback** → general advice + "update yt-dlp"

---

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| **Alt + F4** | Toggle the [secret keygen mode](#secret-keygen-mode-) (does **not** close the app) |
| **Ctrl + Alt + K** | Backup toggle for keygen mode |
| Window **✕** button | Close the app normally |

---

## Secret keygen mode 😈

Press **Alt + F4** and, instead of closing, the app transforms into a gloriously cheesy 90s "cracktro" skin — same functionality, maximum nostalgia. Press it again to return to normal.

```
███████╗██╗   ██╗███╗   ██╗███╗   ██╗██╗   ██╗███████╗
██╔════╝██║   ██║████╗  ██║████╗  ██║╚██╗ ██╔╝╚══███╔╝
███████╗██║   ██║██╔██╗ ██║██╔██╗ ██║ ╚████╔╝   ███╔╝
╚════██║██║   ██║██║╚██╗██║██║╚██╗██║  ╚██╔╝   ███╔╝
███████║╚██████╔╝██║ ╚████║██║ ╚████║   ██║   ███████╗
╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═══╝   ╚═╝   ╚══════╝
   ▀▄▀▄▀▄ -=≡  Y O U T U B E   A R C H I V E R  ≡=- ▄▀▄▀▄▀
```

What you get:

- **ASCII block logo** and a neon phosphor-green-on-purple theme applied across the whole UI.
- **Scrolling "greetz" marquee** in full scene style (with a tongue-in-cheek "turn your volume down" warning).
- **Controllable chiptune** (Windows only, via `winsound`): ▶ Play, ■ Stop, » Next Trax (three original loops — no copyrighted melodies), and a tempo slider.
- A dark-purple **"SETTINGS LOCKED WHILE DOWNLOADING"** veil over the options during a run (keygen skin only).

> The whole mode is a joke; the program behaves exactly the same. Chiptune audio is Windows-only — on other systems the controls are shown but silent.

---

## Window behaviour

- Fixed width (**745 px**), resizable in height (from **850 px** up to **1500 px**).
- The window grows to fit extra download wheels and the keygen banner, then shrinks back.

## Where files end up

```
<Save-to folder>/
└── <Channel> - <Project>/
    ├── download_log.json          ← progress tracker + resume/dedup record
    ├── 001 - First Video [id].mkv
    ├── 002 - Second Video [id].mkv
    └── ...
```

## Troubleshooting

- **"Sign in to confirm you're not a bot"** — set **Browser cookies** to a signed-in browser, set **Simultaneous downloads** to 1, and use the **Recommended** delay. (The Failed popup will tell you this too.)
- **Merging/MP3 fails, or "ffmpeg not found"** — install ffmpeg and set the **ffmpeg folder**.
- **Cookies don't work** — make sure the chosen browser is fully closed first.
- **Getting throttled / 429** — fewer simultaneous downloads, longer delay, wait a bit, and/or use cookies.
- **It re-downloads things I already have** — check that you're pointing at the same **Save-to folder**; the `download_log.json` inside each generated folder is what tracks completed videos.

## Notes & disclaimers

- This is a convenience wrapper around yt-dlp; all downloading is performed by yt-dlp and ffmpeg.
- Only download content you have the right to download, and respect YouTube's Terms of Service and the rights of content creators.
- Higher concurrency and shorter delays increase the chance of being rate-limited or temporarily blocked — pace yourself on large jobs.
- Keep yt-dlp up to date (`pip install -U yt-dlp`); YouTube changes often and yt-dlp updates frequently to keep up.
