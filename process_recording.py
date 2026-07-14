#!/usr/bin/env python3
"""
Process an existing recording (video OR audio) into a transcript + summary.

Use this when you already have a recording — a screen grab, a Teams recording,
a phone recording, anything ffmpeg can read — and just want the transcript,
summary, and action items out of it.

Thin command-line front end over `note_engine.py`.

Usage:
    python process_recording.py meeting.mp4
    python process_recording.py meeting.mp4 --model small
    python process_recording.py recording.mp3 --output-dir C:\\Notes

Supported inputs: mp4, mkv, mov, avi, webm, mp3, wav, m4a, and more
(anything ffmpeg can decode). Video files have their audio extracted
automatically — the video itself is not needed for the summary.
"""

import sys
import argparse
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn

import note_engine as engine

console = Console()


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
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(Panel(
        f"[bold]Process Recording[/bold]\n"
        f"Input:  [cyan]{input_path.name}[/cyan]  |  "
        f"Model: [cyan]{args.model}[/cyan]  |  "
        f"Output: [cyan]{output_dir}[/cyan]",
        expand=False,
    ))

    # 1. Extract audio
    console.print(Rule("[bold]1/3 Extracting audio[/bold]"))
    try:
        wav_path = engine.extract_audio_to_wav(input_path)
    except RuntimeError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        sys.exit(1)

    # 2. Transcribe
    console.print(Rule("[bold]2/3 Transcribing[/bold]"))
    transcriber = engine.Transcriber(args.model)
    console.print(f"[dim]Loading Whisper [{args.model}] — first run downloads the model...[/dim]")
    transcriber.load()
    console.print("[green]Whisper ready. Transcribing...[/green]")
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Transcribing", total=None)

            def on_segment(start, text):
                mins, secs = divmod(int(start), 60)
                progress.update(task, description=f"Transcribing... [{mins}:{secs:02d}]")

            transcript = transcriber.transcribe_file(wav_path, on_segment=on_segment)
    finally:
        try:
            wav_path.unlink()
        except OSError:
            pass

    transcript_text = transcript.as_text()

    # 3. Summarise
    console.print(Rule("[bold]3/3 Summarising[/bold]"))
    summary = engine.generate_summary(
        transcript_text, api_key=args.api_key,
        on_log=lambda m: console.print(f"[dim]{m}[/dim]"))

    # Save — outputs are timestamped and named after the input file.
    t_path, s_path = engine.save_outputs(
        output_dir, transcript_text, summary, stem=input_path.stem)

    console.print(Rule("[bold]Summary[/bold]"))
    console.print(Markdown(summary))
    console.print()
    console.print(f"[green]Transcript:[/green] {t_path}")
    console.print(f"[green]Summary:[/green]    {s_path}")


if __name__ == "__main__":
    main()
