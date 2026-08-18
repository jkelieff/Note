#!/usr/bin/env python3
"""
Shared engine for the Meeting Note Taker.

Both the command-line tools and the desktop GUI use these functions so the
transcription/summary logic lives in one place. Designed to work when frozen
into a standalone executable (it looks for a bundled ffmpeg next to itself).
"""

import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from faster_whisper import WhisperModel
import anthropic

# A progress callback takes a single status string. Defaults to no-op.
Progress = Callable[[str], None]


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


def summarise(
    transcript: str,
    api_key: Optional[str] = None,
    progress: Progress = lambda s: None,
) -> str:
    """Turn a transcript into a summary + action items via Claude."""
    if not transcript.strip():
        return "*(No speech detected in the recording.)*"

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return (
            "*(No API key provided, so no summary was generated. "
            "The transcript has still been saved.)*"
        )

    progress("Sending transcript to Claude for summary and actions...")
    client = anthropic.Anthropic(api_key=key)

    prompt = f"""You are an expert meeting assistant. Analyse the transcript below and produce a structured report.

## Output format (strict markdown):

### Summary
2–4 sentences covering the main topics and outcomes.

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

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def process_recording(
    input_path: Path,
    output_dir: Path,
    model_size: str = "base",
    api_key: Optional[str] = None,
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

    summary = summarise(transcript, api_key, progress)

    stem = input_path.stem
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    t_path = output_dir / f"{ts}_{stem}_transcript.txt"
    s_path = output_dir / f"{ts}_{stem}_summary.md"
    t_path.write_text(transcript, encoding="utf-8")
    s_path.write_text(summary, encoding="utf-8")

    progress("Done.")
    return summary, t_path, s_path
