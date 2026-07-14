#!/usr/bin/env python3
"""
Process an existing recording (video OR audio) into a transcript + summary.

Use this when you already have a recording — a screen grab, a Teams recording,
a phone recording, anything ffmpeg can read — and just want the transcript,
summary, and action items out of it.

Usage:
    python process_recording.py meeting.mp4
    python process_recording.py meeting.mp4 --model small
    python process_recording.py recording.mp3 --output-dir C:\\Notes

Supported inputs: mp4, mkv, mov, avi, webm, mp3, wav, m4a, and more
(anything ffmpeg can decode). Video files have their audio extracted
automatically — the video itself is not needed for the summary.
"""

import os
import sys
import shutil
import argparse
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

from faster_whisper import WhisperModel
import anthropic
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


# ── Audio extraction ────────────────────────────────────────────────────────────

def extract_audio(input_path: Path) -> Path:
    """Extract/convert audio to a 16kHz mono WAV that Whisper can read."""
    if not shutil.which("ffmpeg"):
        console.print(
            "[bold red]ffmpeg not found.[/bold red] It's needed to read the recording.\n"
            "  Windows:  winget install ffmpeg\n"
            "  macOS:    brew install ffmpeg\n"
            "  Linux:    sudo apt install ffmpeg"
        )
        sys.exit(1)

    tmp_wav = Path(tempfile.gettempdir()) / f"_note_audio_{os.getpid()}.wav"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vn",                    # drop video
        "-ac", "1",               # mono
        "-ar", "16000",           # 16 kHz
        "-f", "wav",
        str(tmp_wav),
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        console.print("[bold red]ffmpeg failed to read the file:[/bold red]")
        console.print(result.stderr.decode(errors="ignore")[-1000:])
        sys.exit(1)
    return tmp_wav


# ── Transcription ───────────────────────────────────────────────────────────────

def transcribe(wav_path: Path, model_size: str) -> str:
    console.print(f"[dim]Loading Whisper [{model_size}] — first run downloads the model...[/dim]")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    console.print("[green]Whisper ready. Transcribing...[/green]")

    segments, info = model.transcribe(str(wav_path), beam_size=5, language="en")

    lines = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Transcribing", total=None)
        for seg in segments:
            mins, secs = divmod(int(seg.start), 60)
            text = seg.text.strip()
            if text:
                lines.append(f"[{mins}:{secs:02d}] {text}")
                progress.update(task, description=f"Transcribing... [{mins}:{secs:02d}]")

    return "\n".join(lines)


# ── Summary via Claude ──────────────────────────────────────────────────────────

def generate_summary(transcript: str, api_key: str | None = None) -> str:
    if not transcript.strip():
        return "*(No speech detected in the recording.)*"

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return (
            "*(Set ANTHROPIC_API_KEY to generate a summary. "
            "The transcript has been saved separately.)*"
        )

    client = anthropic.Anthropic(api_key=key)
    console.print("[dim]Sending transcript to Claude for analysis...[/dim]")

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


# ── CLI ──────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Turn an existing recording into a transcript + summary + actions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Path to the recording (mp4, mp3, wav, mkv, ...)")
    parser.add_argument("--model", default="base",
                        choices=["tiny", "base", "small", "medium", "large-v3"],
                        help="Whisper model size (default: base; medium/large = more accurate)")
    parser.add_argument("--output-dir", default=None,
                        help="Where to save transcript + summary (default: ./meetings)")
    parser.add_argument("--api-key", default=None,
                        help="Anthropic API key (or set ANTHROPIC_API_KEY env var)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        console.print(f"[bold red]File not found:[/bold red] {input_path}")
        sys.exit(1)

    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / "meetings"
    output_dir.mkdir(exist_ok=True)

    console.print(Panel(
        f"[bold]Process Recording[/bold]\n"
        f"Input:  [cyan]{input_path.name}[/cyan]  |  "
        f"Model: [cyan]{args.model}[/cyan]  |  "
        f"Output: [cyan]{output_dir}[/cyan]",
        expand=False,
    ))

    # 1. Extract audio
    console.print(Rule("[bold]1/3 Extracting audio[/bold]"))
    wav_path = extract_audio(input_path)

    # 2. Transcribe
    console.print(Rule("[bold]2/3 Transcribing[/bold]"))
    try:
        transcript = transcribe(wav_path, args.model)
    finally:
        try:
            wav_path.unlink()
        except OSError:
            pass

    # 3. Summarise
    console.print(Rule("[bold]3/3 Summarising[/bold]"))
    summary = generate_summary(transcript, api_key=args.api_key)

    # Save — name outputs after the input file
    stem = input_path.stem
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    t_path = output_dir / f"{ts}_{stem}_transcript.txt"
    s_path = output_dir / f"{ts}_{stem}_summary.md"
    t_path.write_text(transcript, encoding="utf-8")
    s_path.write_text(summary, encoding="utf-8")

    console.print(Rule("[bold]Summary[/bold]"))
    console.print(Markdown(summary))
    console.print()
    console.print(f"[green]Transcript:[/green] {t_path}")
    console.print(f"[green]Summary:[/green]    {s_path}")


if __name__ == "__main__":
    main()
