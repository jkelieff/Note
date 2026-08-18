# Meeting Note Taker — Web Dashboard

A dashboard that runs on your own machine and opens in your browser. Two ways
to make notes, and **£0 running cost** — transcription and summary both run
locally.

- **Upload a recording** — drop in any video/audio file you already have.
- **Record a live meeting** — the browser captures your screen's system audio,
  so it records the Teams desktop app **without Stereo Mix or any audio setup**.

---

## One-time setup

You need three things installed. All free.

### 1. Python
From https://python.org — tick **"Add python.exe to PATH"** during install.

### 2. ffmpeg
- **Windows:** `winget install ffmpeg` (or download from ffmpeg.org and put
  `ffmpeg.exe` in this folder)
- **macOS:** `brew install ffmpeg`

### 3. Ollama (the free local AI for summaries)
1. Download and install from **https://ollama.com**
2. Open a terminal and pull a model (one-time ~4GB download):
   ```
   ollama pull llama3.1
   ```
   *(On a lower-spec machine use a smaller model: `ollama pull llama3.2` and set
   that name in the dashboard's Settings.)*
3. Ollama then runs quietly in the background. That's it — no account, no key,
   no charges, ever.

### Then install the Python packages
In this folder:
```
pip install -r requirements.txt
```

---

## Running it

```
python server.py
```

Your browser opens at **http://localhost:5000** automatically. Leave the
terminal window open while you use it (it's the engine). Press **Ctrl+C** there
to stop.

At the top you'll see green ticks for **ffmpeg** and **local AI** when
everything's ready. Red means that piece isn't set up yet.

---

## Using it

### Upload a recording
1. Go to the **Upload a recording** tab
2. Click the box (or drag a file in) — mp4, mkv, mov, mp3, wav, m4a, etc.
3. Click **Generate Notes**
4. Progress shows live; the summary + action items appear below, and the files
   save to your notes folder.

### Record a live meeting
1. Go to the **Record a live meeting** tab (use **Chrome or Edge**)
2. Click **Start recording**
3. In the browser's picker, choose **Entire Screen** and tick
   **Share system audio** — this is what captures everyone's voices from Teams
4. Have your meeting. Click **Stop & make notes** when done.
5. It transcribes and summarises the same way.

> To include **your own microphone** too, also tick the microphone/tab-audio
> option in the share dialog.

---

## Settings

- **Transcription accuracy** — bigger = more accurate but slower (`base` is a
  good default; `small` for important meetings).
- **Summary engine** — `Local model (Ollama)` is the free, private default.
  You can switch to Gemini's free tier or paid Claude if you prefer.
- **Summary model name** — the Ollama model you pulled (e.g. `llama3.1`).
- **Save notes to** — where transcripts and summaries are written.

Settings are saved on your machine automatically.

---

## Cost summary

| Step | Runs where | Cost |
|------|-----------|------|
| Transcription (Whisper) | Your machine | Free |
| Summary (Ollama local model) | Your machine | Free |
| **Total** | | **£0** |

Nothing is sent to any external service when using the local model — your
meeting audio and transcript never leave your computer.

---

## A note on recording at work

Please check your employer's policy on recording meetings before using this on
work calls. Many organisations require notifying participants, even for
personal notes.
