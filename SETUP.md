# Meeting Note Taker — Setup Guide

Three ways to use this:

1. **`desktop_app.py`** — a small always-on-top control panel that sits on your desktop. Pick the screen your meeting is on, hit record, and it **hides itself from the meeting's screen share** while it captures that screen to video and transcribes the audio. On stop it writes a summary + action items. This is the app most people want.
2. **`note_taker.py`** — the same engine as a headless CLI: sits silently in the background during a live meeting, transcribes as it goes, and produces a summary + actions when you press Ctrl+C.
3. **`process_recording.py`** — takes a recording you already have (a screen grab, a Teams recording, a phone recording — any video or audio file) and produces the same transcript + summary + actions from it.

If IT restrictions or setup hassle get in the way of the live versions, the **process-recording** route is the easy path: record the meeting however you can, then run one command on the file afterward.

---

## The desktop app (recommended)

```bash
pip install -r requirements.txt
python desktop_app.py
```

A compact panel appears and floats on top of everything. In it you:

1. **Choose the screen the meeting is on** — the panel captures exactly that monitor.
2. **Choose the audio** — microphone, system audio (everyone else's voices), or both.
3. Optionally leave **Record video** on to save an MP4 of the meeting screen.
4. Press **Start recording**. A live transcript scrolls in the panel.
5. Press **Stop & summarise** when the meeting ends. Transcript, summary, and action items are written to your output folder.

### Make it a clickable app (no terminal)

You have two levels, depending on whether you want Python involved at all.

**Level 1 — click to launch (Python installed):**

1. Double-click **`Install (first time).bat`** once — it installs the dependencies.
2. From then on, double-click **`Meeting Note Taker.bat`** to start. It uses `pythonw`, so no console window hangs around.
   (macOS: double-click `launch_macos.command` instead.)

**Level 2 — a true standalone `.exe` (no Python needed):**

Double-click **`Build standalone app.bat`**. It bundles everything into a single file:

```
dist\MeetingNoteTaker.exe
```

Double-click that `.exe` to run, or pin it to your taskbar / Start menu. You can copy it to any Windows PC — Python doesn't have to be installed there.
(macOS: `build_macos.command` produces `dist/MeetingNoteTaker.app`, which you can drag to Applications.)

> The standalone build must be produced **on the OS you'll run it on** — build the `.exe` on Windows, the `.app` on macOS. ffmpeg is still needed for video: either install it (`winget install ffmpeg`) or drop `ffmpeg.exe` next to `desktop_app.spec` before building to bundle it in.

### How "not visible in the meeting" works

The panel asks the OS to exclude its own window from screen capture:

- **Windows 10 (2004+) / 11** — `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)`. Teams, Zoom, Meet, OBS, and even PrintScreen won't see the panel, even though you can. The panel shows **"✓ Hidden from screen share & recordings"** when this succeeds.
- **macOS** — `NSWindow.sharingType = NSWindowSharingNone` (needs `pip install pyobjc`).
- **Linux/X11** — no reliable per-window capture exclusion exists, so the panel warns you and you should move it onto a monitor you're *not* sharing.

Because the window is excluded from *all* capture, it also stays out of the MP4 the app records — so your recording shows only the meeting, never the control panel.

---

## Prerequisites

- **Python 3.9+**
- An **Anthropic API key** (for the summary — transcription is local/free)
- Windows 10/11 (primary target), macOS also supported

---

## Installation

```bash
pip install -r requirements.txt
```

On **Windows** you also need:

```bash
pip install sounddevice   # already in requirements.txt
# PortAudio is bundled with the sounddevice wheel on Windows — nothing else needed
```

---

## Getting your Anthropic API key

1. Go to https://console.anthropic.com
2. Create an API key
3. Set it as an environment variable (recommended) or pass it via `--api-key`:

```powershell
# Windows PowerShell — add to your profile to make it permanent
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

```bash
# macOS/Linux
export ANTHROPIC_API_KEY="sk-ant-..."
```

---

## Capturing Teams audio — the invisible approach

Because you want it to **not appear in the meeting**, you capture the audio that plays through your speakers rather than joining as a second participant.

### Windows (recommended): Enable Stereo Mix

1. Right-click the speaker icon in your taskbar → **Sounds**
2. **Recording** tab → right-click empty area → tick **Show Disabled Devices**
3. Right-click **Stereo Mix** → **Enable**
4. Run `python note_taker.py --list-devices` and note the index of "Stereo Mix"
5. Record with:

```powershell
python note_taker.py --loopback-device INDEX
```

> **If Stereo Mix isn't available** (many modern laptops): install [VB-Cable](https://vb-audio.com/Cable/) (free). Set Teams audio output to "CABLE Input", your speakers to "CABLE Output" monitors, then use the VB-Cable device.

### Windows: Capture both your mic AND what you hear (most complete)

```powershell
python note_taker.py --both --loopback-device INDEX
```

This mixes your microphone (what you say) with the system audio (what others say) for a full transcript of everyone.

### macOS

Install [BlackHole](https://github.com/ExistentialAudio/BlackHole):

```bash
brew install blackhole-2ch
```

Then in **Audio MIDI Setup**, create a Multi-Output Device with BlackHole + your speakers. Set Teams to output there, then:

```bash
python note_taker.py --loopback
```

---

## Whisper model sizes

| Model | VRAM / RAM | Accuracy | Speed |
|-------|-----------|----------|-------|
| `tiny` | ~1 GB | ★★☆☆☆ | Very fast |
| `base` | ~1 GB | ★★★☆☆ | Fast (default) |
| `small` | ~2 GB | ★★★★☆ | Medium |
| `medium` | ~5 GB | ★★★★★ | Slower |
| `large-v3` | ~10 GB | ★★★★★ | Slowest |

For most meetings `base` or `small` is the right balance. Switch with `--model small`.

---

## Usage

```
# List audio devices to find the right index
python note_taker.py --list-devices

# Record from microphone only
python note_taker.py

# Record from system audio (Teams speakers) — invisible to participants
python note_taker.py --loopback --loopback-device 3

# Record mic + system audio (captures everyone)
python note_taker.py --both --loopback-device 3

# Better accuracy for technical meetings
python note_taker.py --both --loopback-device 3 --model small

# Custom output folder
python note_taker.py --loopback-device 3 --output-dir C:\Users\You\MeetingNotes
```

Press **Ctrl+C** when the meeting ends. The transcript and summary are saved to `./meetings/` (or your `--output-dir`).

---

## Output

Two files are created per meeting:

| File | Contents |
|------|----------|
| `YYYY-MM-DD_HH-MM_transcript.txt` | Full timestamped transcript |
| `YYYY-MM-DD_HH-MM_summary.md` | Summary, decisions, action items table, open questions |

### Example summary

```markdown
### Summary
The team discussed Q3 roadmap priorities and agreed to delay the mobile launch...

### Key Decisions
- Mobile launch pushed to Q4
- Sarah will lead the API redesign

### Action Items
| # | Action | Owner | Due |
|---|--------|-------|-----|
| 1 | Draft revised mobile spec | Sarah | Friday |
| 2 | Update stakeholders on delay | James | EOD Monday |

### Open Questions
- Budget approval for contractor still pending from Finance
```

---

## Processing a recording you already have

If you recorded the meeting some other way (screen grab, Teams recording, phone), you don't need the live tool at all. Just point this script at the file:

```powershell
python process_recording.py "C:\path\to\your_recording.mp4"
```

It works with video **or** audio files — mp4, mkv, mov, avi, webm, mp3, wav, m4a, and more. For video files it automatically pulls out the audio (the video itself isn't needed for the summary).

```powershell
# Better accuracy
python process_recording.py meeting.mp4 --model small

# Choose where output lands
python process_recording.py meeting.mp4 --output-dir "C:\Users\You\MeetingNotes"
```

You still need `ffmpeg` installed (for reading the file) and `ANTHROPIC_API_KEY` set (for the summary). Output is the same two files: a timestamped transcript and a markdown summary with the action items table.

---

## Privacy

- Audio never leaves your machine (Whisper runs locally)
- Only the **text transcript** is sent to Anthropic's API for the summary
- No audio files are stored permanently (temp files deleted immediately after transcription)
