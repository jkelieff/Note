# Building the desktop app (.exe)

This turns the note taker into a single **`MeetingNoteTaker.exe`** you double-click — no Python, no command line for the end user.

> **You build this once on a Windows machine you control** (your personal one),
> then copy the finished `.exe` wherever you need it. You cannot build a Windows
> `.exe` on a Mac or Linux box — the executable is Windows-specific.

---

## What you get

A windowed app:

1. **Browse** to a meeting recording (any video or audio file)
2. Pick accuracy, paste your Anthropic API key (remembered next time)
3. Click **Generate Notes**
4. The summary + action items appear on screen and save to your notes folder

---

## Build steps (on your personal Windows machine)

1. Put these files in one folder:
   - `app.py`
   - `notecore.py`
   - `requirements.txt`
   - `app.spec`
   - `build.bat`

2. Double-click **`build.bat`**.
   It installs everything and produces `dist\MeetingNoteTaker.exe` (takes a few minutes).

That's it. The `.exe` is in the `dist` folder.

---

## The two things the .exe still needs

Packaging bundles the Python code, but **not** these:

### 1. ffmpeg (to read recordings)
Download a static `ffmpeg.exe` from https://www.gyan.dev/ffmpeg/builds/ (the
"essentials" build), and put `ffmpeg.exe` **in the same folder as
`MeetingNoteTaker.exe`**. The app looks right next to itself for it.

### 2. Internet on first use
- The first time it runs, it downloads the transcription model (~150 MB) from
  huggingface.co. After that it works offline for transcription.
- The summary always needs to reach api.anthropic.com.

---

## Distributing it

To hand the app to yourself on another machine, copy the whole `dist` folder
(the `.exe` **plus** `ffmpeg.exe` sitting next to it). A USB stick is fine.

---

## Honest caveats for locked-down / work machines

- **Unsigned executables** (like this one) are often blocked or quarantined by
  corporate antivirus / AppLocker. Proper code signing needs a paid certificate.
  If your work laptop blocks the `.exe`, that's why — and there's no free way
  around it.
- The **first-run model download** and the **API call** both go over the
  network, so a corporate firewall can still stop them.
- **Check your employer's policy on recording meetings** before using this at
  work — many organisations have rules even for personal notes.

If the work machine blocks the `.exe`, the fallback is the plain-Python route in
`SETUP.md` (Python + the two scripts), which some locked-down machines allow
even when they block unknown executables.
