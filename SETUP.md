# Meeting Note Taker — Setup Guide

Sits silently in the background. Captures audio from your Microsoft Teams meeting, transcribes it live, and produces a summary + action items the moment you press Ctrl+C.

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

## Privacy

- Audio never leaves your machine (Whisper runs locally)
- Only the **text transcript** is sent to Anthropic's API for the summary
- No audio files are stored permanently (temp files deleted immediately after transcription)
