#!/usr/bin/env python3
"""
Shared engine for the Meeting Note Taker.

The desktop app, the CLI tools, and the web dashboard all use these functions
so the transcription/summary logic lives in one place.

Transcription is always local (Whisper) and free. The summary step is
pluggable via `backend`:
  - "ollama"  -> a local model (free, private, no key)      [default]
  - "gemini"  -> Google Gemini free tier (needs a free key)
  - "claude"  -> Anthropic Claude (paid, needs a key)
"""

import os
import sys
import json
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable, Optional

from faster_whisper import WhisperModel

# A progress callback takes a single status string. Defaults to no-op.
Progress = Callable[[str], None]

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

SUMMARY_PROMPT = """You are an expert meeting assistant. Analyse the transcript below and produce a structured report.

## Output format (strict markdown):

### Summary
2-4 sentences covering the main topics and outcomes.

### Key Decisions
- Bullet list of decisions made (skip if none).

### Action Items
| # | Action | Owner | Due |
|---|--------|-------|-----|
List every concrete task, who owns it, and the deadline if mentioned.
Default the Owner to "Me" unless the transcript clearly names someone else.

### Open Questions
- Any unresolved issues or questions raised that need a follow-up.

---
**Transcript:**
{transcript}"""


def _base_dir() -> Path:
    """Folder to look in for a bundled ffmpeg — works frozen or as a script."""
    if getattr(sys, "frozen", False):          # PyInstaller sets this
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def find_ffmpeg() -> Optional[str]:
    """Locate ffmpeg: bundled alongside the app first, then on PATH."""
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    local = _base_dir() / exe
    if local.exists():
        return str(local)
    return shutil.which("ffmpeg")


def extract_audio(input_path: Path, progress: Progress = lambda s: None) -> Path:
    """Extract/convert a recording's audio to 16 kHz mono WAV for Whisper."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not found. Place ffmpeg.exe next to this app, or install it "
            "(Windows: winget install ffmpeg / macOS: brew install ffmpeg)."
        )

    progress("Extracting audio from recording...")
    tmp_wav = Path(tempfile.gettempdir()) / f"_note_audio_{os.getpid()}.wav"
    cmd = [
        ffmpeg, "-y",
        "-i", str(input_path),
        "-vn", "-ac", "1", "-ar", "16000", "-f", "wav",
        str(tmp_wav),
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        tail = result.stderr.decode(errors="ignore")[-600:]
        raise RuntimeError(f"ffmpeg could not read the file:\n{tail}")
    return tmp_wav


def transcribe_file(
    wav_path: Path, model_size: str = "base", progress: Progress = lambda s: None
) -> str:
    """Transcribe a WAV file to a timestamped transcript string."""
    progress(f"Loading transcription model ({model_size})...")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    progress("Transcribing — this can take a while on long recordings...")
    segments, _ = model.transcribe(str(wav_path), beam_size=5, language="en")

    lines = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            mins, secs = divmod(int(seg.start), 60)
            lines.append(f"[{mins}:{secs:02d}] {text}")
            progress(f"Transcribing... reached {mins}:{secs:02d}")
    return "\n".join(lines)


# ── Summary backends ─────────────────────────────────────────────────────────

def ollama_available(host: str = OLLAMA_HOST) -> bool:
    """True if a local Ollama server is reachable."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _summarise_ollama(transcript: str, model: str, host: str,
                      progress: Progress) -> str:
    progress(f"Summarising locally with Ollama ({model})...")
    payload = json.dumps({
        "model": model,
        "prompt": SUMMARY_PROMPT.format(transcript=transcript),
        "stream": False,
        "options": {"temperature": 0.2},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/generate", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("response", "").strip() or "*(Empty response from local model.)*"
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach Ollama at {host}. Is it running? "
            f"Install from https://ollama.com and run 'ollama pull {model}'.\n"
            f"Details: {e}"
        )


def _summarise_gemini(transcript: str, api_key: str, model: str,
                      progress: Progress) -> str:
    progress("Summarising with Google Gemini (free tier)...")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={api_key}")
    payload = json.dumps({
        "contents": [{"parts": [{"text": SUMMARY_PROMPT.format(transcript=transcript)}]}],
        "generationConfig": {"temperature": 0.2},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (urllib.error.URLError, KeyError, IndexError) as e:
        raise RuntimeError(f"Gemini request failed: {e}")


def _summarise_claude(transcript: str, api_key: str, progress: Progress) -> str:
    import anthropic
    progress("Summarising with Claude...")
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=2048,
        messages=[{"role": "user",
                   "content": SUMMARY_PROMPT.format(transcript=transcript)}],
    )
    return response.content[0].text


def summarise(
    transcript: str,
    backend: str = "ollama",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    host: str = OLLAMA_HOST,
    progress: Progress = lambda s: None,
) -> str:
    """Turn a transcript into a summary + action items using the chosen backend."""
    if not transcript.strip():
        return "*(No speech detected in the recording.)*"

    if backend == "ollama":
        return _summarise_ollama(transcript, model or "llama3.1", host, progress)
    if backend == "gemini":
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            return "*(No Gemini API key provided. Transcript saved.)*"
        return _summarise_gemini(transcript, key, model or "gemini-1.5-flash", progress)
    if backend == "claude":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return "*(No Claude API key provided. Transcript saved.)*"
        return _summarise_claude(transcript, key, progress)
    raise ValueError(f"Unknown summary backend: {backend}")


def process_recording(
    input_path: Path,
    output_dir: Path,
    model_size: str = "base",
    backend: str = "ollama",
    api_key: Optional[str] = None,
    summary_model: Optional[str] = None,
    ollama_host: str = OLLAMA_HOST,
    progress: Progress = lambda s: None,
) -> tuple[str, Path, Path]:
    """
    Full pipeline: recording file -> transcript + summary, saved to disk.
    Returns (summary_text, transcript_path, summary_path).
    """
    from datetime import datetime

    output_dir.mkdir(parents=True, exist_ok=True)

    wav = extract_audio(input_path, progress)
    try:
        transcript = transcribe_file(wav, model_size, progress)
    finally:
        try:
            wav.unlink()
        except OSError:
            pass

    summary = summarise(
        transcript, backend=backend, api_key=api_key,
        model=summary_model, host=ollama_host, progress=progress,
    )

    stem = input_path.stem
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    t_path = output_dir / f"{ts}_{stem}_transcript.txt"
    s_path = output_dir / f"{ts}_{stem}_summary.md"
    t_path.write_text(transcript, encoding="utf-8")
    s_path.write_text(summary, encoding="utf-8")

    progress("Done.")
    return summary, t_path, s_path
