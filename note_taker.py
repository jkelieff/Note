#!/usr/bin/env python3
"""
Meeting Note Taker — CLI
Sits silently in the background, captures audio (and optionally screen video)
from your meeting, transcribes it live with Whisper, then generates a summary
+ action items via Claude when you press Ctrl+C.

This is a thin command-line front end over `note_engine.py`; the desktop app
(`desktop_app.py`) uses the same engine.

Usage:
    python note_taker.py                          # mic only
    python note_taker.py --loopback               # system audio (others' voices)
    python note_taker.py --both                   # mic + system audio
    python note_taker.py --record-screen          # mic + screen video
    python note_taker.py --both --record-screen   # everything
    python note_taker.py --list-devices           # show audio device indices
    python note_taker.py --model small            # better accuracy
"""

import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.markdown import Markdown

import note_engine as engine

console = Console()


def list_devices():
    console.print(Panel("[bold]Available audio devices[/bold]", expand=False))
    devices = engine.list_audio_devices()
    if not devices:
        console.print("[yellow]No devices found (is sounddevice installed?).[/yellow]")
        return
    for d in devices:
        direction = []
        if d.max_input > 0:
            direction.append("IN")
        if d.max_output > 0:
            direction.append("OUT")
        loopback = " [dim](loopback?)[/dim]" if d.looks_like_loopback else ""
        console.print(
            f"  [{d.index:>2}] [cyan]{d.name}[/cyan] "
            f"({'|'.join(direction)}){loopback}"
        )
    console.print("\n[dim]Pass the index with --device or --loopback-device.[/dim]")


def main():
    parser = argparse.ArgumentParser(
        description="Silent background meeting recorder → transcript + summary",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list-devices", action="store_true",
                        help="List available audio devices and exit")
    parser.add_argument("--device", type=int, default=None,
                        help="Microphone device index (default: system default)")
    parser.add_argument("--loopback", action="store_true",
                        help="Capture system audio output (what plays through speakers)")
    parser.add_argument("--loopback-device", type=int, default=None,
                        help="Explicit loopback device index (auto-detected if omitted)")
    parser.add_argument("--both", action="store_true",
                        help="Mix microphone + system audio (most complete transcript)")
    parser.add_argument("--record-screen", action="store_true",
                        help="Also record your screen to an MP4 (requires ffmpeg)")
    parser.add_argument("--screen-fps", type=int, default=12,
                        help="Screen recording frame rate (default: 12 — lower = smaller file)")
    parser.add_argument("--model", default="base",
                        choices=["tiny", "base", "small", "medium", "large-v3"],
                        help="Whisper model size (default: base; medium/large = more accurate)")
    parser.add_argument("--output-dir", default=None,
                        help="Where to save files (default: ./meetings)")
    parser.add_argument("--api-key", default=None,
                        help="Anthropic API key (or set ANTHROPIC_API_KEY env var)")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    # Resolve loopback device
    loopback_device = None
    if args.loopback or args.both:
        loopback_device = args.loopback_device
        if loopback_device is None:
            loopback_device = engine.find_loopback_device()
        if loopback_device is None:
            console.print(
                "[bold red]No loopback device found.[/bold red]\n"
                "On Windows: enable 'Stereo Mix' in Sound settings, or install VB-Cable.\n"
                "On macOS: install BlackHole (brew install blackhole-2ch).\n"
                "Then re-run with [cyan]--loopback-device INDEX[/cyan] from [cyan]--list-devices[/cyan]."
            )
            sys.exit(1)
        name = next((d.name for d in engine.list_audio_devices()
                     if d.index == loopback_device), "?")
        console.print(f"[dim]Loopback: [{loopback_device}] {name}[/dim]")

    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / "meetings"
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_mode = ("mic + system audio" if args.both
                  else ("system audio" if args.loopback else "microphone"))
    extras = " + screen video" if args.record_screen else ""
    console.print(Panel(
        f"[bold]Meeting Note Taker[/bold]\n"
        f"Capturing: [cyan]{audio_mode}{extras}[/cyan]  |  "
        f"Model: [cyan]{args.model}[/cyan]  |  "
        f"Output: [cyan]{output_dir}[/cyan]\n\n"
        f"[dim]Live transcript appears below. Press Ctrl+C when the meeting ends.[/dim]",
        expand=False,
    ))

    # Optional screen recording (full desktop for the CLI).
    screen_recorder = None
    if args.record_screen:
        if not engine.ffmpeg_available():
            console.print(f"[bold red]{engine.ffmpeg_install_hint()}[/bold red]")
            sys.exit(1)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        video_path = output_dir / f"{ts}_recording.mp4"
        screen_recorder = engine.ScreenRecorder(video_path, monitor=None,
                                                fps=args.screen_fps)
        screen_recorder.start()
        console.print(f"[dim]Screen recording started → {video_path.name}[/dim]")

    def on_segment(elapsed, text):
        mins, secs = divmod(int(elapsed), 60)
        console.print(f"  [dim cyan][{mins}:{secs:02d}][/dim cyan] {text}")

    transcriber = engine.Transcriber(args.model)
    recorder = engine.LiveRecorder(
        transcriber,
        device=args.device,
        loopback_device=loopback_device,
        mix_both=args.both,
        on_segment=on_segment,
        on_log=lambda m: console.print(f"[dim]{m}[/dim]"),
    )

    console.print(Rule("[bold green]Recording — press Ctrl+C to stop[/bold green]"))
    recorder.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        console.print(Rule("[yellow]Stopping...[/yellow]"))
    finally:
        recorder.stop()
        if screen_recorder:
            screen_recorder.stop()
            console.print("[dim]Screen recording saved.[/dim]")

    console.print()
    transcript_text = recorder.transcript.as_text()
    summary = engine.generate_summary(
        transcript_text, api_key=args.api_key,
        on_log=lambda m: console.print(f"[dim]{m}[/dim]"))
    t_path, s_path = engine.save_outputs(output_dir, transcript_text, summary)

    console.print(Rule("[bold]Summary[/bold]"))
    console.print(Markdown(summary))
    console.print()
    console.print(f"[green]Transcript:[/green] {t_path}")
    console.print(f"[green]Summary:[/green]    {s_path}")
    if screen_recorder:
        console.print(f"[green]Video:[/green]      {screen_recorder.output_path}")


if __name__ == "__main__":
    main()
