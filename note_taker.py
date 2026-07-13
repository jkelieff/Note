#!/usr/bin/env python3
"""
Meeting Note Taker
Sits silently in the background, captures audio (and optionally screen video)
from your meeting, transcribes it live with Whisper, then generates a summary
+ action items via Claude when you press Ctrl+C.

Usage:
    python note_taker.py                          # mic only
    python note_taker.py --loopback               # system audio (others' voices)
    python note_taker.py --both                   # mic + system audio
    python note_taker.py --record-screen          # mic + screen video
    python note_taker.py --both --record-screen   # everything
    python note_taker.py --list-devices           # show audio device indices
    python note_taker.py --model small            # better accuracy
"""

import os
import sys
import time
import queue
import shutil
import platform
import subprocess
import threading
import tempfile
import argparse
from pathlib import Path
from datetime import datetime

import numpy as np
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel
import anthropic
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.markdown import Markdown

console = Console()

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION = 30        # seconds of audio per Whisper transcription pass
SILENCE_THRESHOLD = 0.005  # RMS below this = silence, skip transcription
DTYPE = np.float32


# ── Audio helpers ──────────────────────────────────────────────────────────────

def find_loopback_device():
    """Return the best loopback device index for the current platform."""
    devices = sd.query_devices()

    for i, dev in enumerate(devices):
        name = dev["name"].lower()
        if "loopback" in name or "stereo mix" in name or "what u hear" in name:
            return i

    for i, dev in enumerate(devices):
        name = dev["name"].lower()
        if any(x in name for x in ("blackhole", "soundflower", "loopback")):
            return i

    return None


def rms(data: np.ndarray) -> float:
    return float(np.sqrt(np.mean(data ** 2)))


# ── Screen recorder ────────────────────────────────────────────────────────────

class ScreenRecorder:
    """Wraps ffmpeg to record the screen to an MP4 in the background."""

    def __init__(self, output_path: Path, fps: int = 10):
        self.output_path = output_path
        self.fps = fps
        self._process: subprocess.Popen | None = None

    def _build_command(self) -> list[str]:
        system = platform.system()
        if system == "Windows":
            return [
                "ffmpeg", "-y",
                "-f", "gdigrab",
                "-framerate", str(self.fps),
                "-i", "desktop",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "28",
                "-pix_fmt", "yuv420p",
                str(self.output_path),
            ]
        elif system == "Darwin":
            return [
                "ffmpeg", "-y",
                "-f", "avfoundation",
                "-framerate", str(self.fps),
                "-i", "1:none",          # screen index 1, no audio (audio handled separately)
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "28",
                "-pix_fmt", "yuv420p",
                str(self.output_path),
            ]
        else:  # Linux
            display = os.environ.get("DISPLAY", ":0")
            return [
                "ffmpeg", "-y",
                "-f", "x11grab",
                "-framerate", str(self.fps),
                "-i", display,
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "28",
                "-pix_fmt", "yuv420p",
                str(self.output_path),
            ]

    def start(self):
        if not shutil.which("ffmpeg"):
            console.print(
                "[bold red]ffmpeg not found.[/bold red] "
                "Install it to use --record-screen:\n"
                "  Windows:  winget install ffmpeg   (or https://ffmpeg.org/download.html)\n"
                "  macOS:    brew install ffmpeg\n"
                "  Linux:    sudo apt install ffmpeg"
            )
            sys.exit(1)

        cmd = self._build_command()
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        console.print(f"[dim]Screen recording started → {self.output_path.name}[/dim]")

    def stop(self):
        if self._process and self._process.poll() is None:
            try:
                self._process.stdin.write(b"q")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None


# ── Core recorder ──────────────────────────────────────────────────────────────

class MeetingRecorder:
    def __init__(self, device=None, loopback_device=None, mix_both=False,
                 model_size="base", output_dir=None,
                 record_screen=False, screen_fps=10):
        self.device = device
        self.loopback_device = loopback_device
        self.mix_both = mix_both
        self.model_size = model_size
        self.output_dir = Path(output_dir) if output_dir else Path.cwd() / "meetings"
        self.output_dir.mkdir(exist_ok=True)
        self.record_screen = record_screen
        self.screen_fps = screen_fps

        self.mic_queue: queue.Queue = queue.Queue()
        self.loopback_queue: queue.Queue = queue.Queue()
        self.mixed_queue: queue.Queue = queue.Queue()

        self.transcript_segments: list[dict] = []
        self.is_recording = False
        self.start_time: float = 0.0

        console.print(f"[dim]Loading Whisper [{model_size}] — first run downloads the model...[/dim]")
        self.whisper = WhisperModel(model_size, device="cpu", compute_type="int8")
        console.print("[green]Whisper ready.[/green]")

    # ── Audio callbacks ────────────────────────────────────────────────────────

    def _mic_callback(self, indata, frames, time_info, status):
        if status:
            console.print(f"[yellow]mic: {status}[/yellow]", err=True)
        self.mic_queue.put(indata.copy())

    def _loopback_callback(self, indata, frames, time_info, status):
        if status:
            console.print(f"[yellow]loopback: {status}[/yellow]", err=True)
        self.loopback_queue.put(indata.copy())

    # ── Mixing thread ──────────────────────────────────────────────────────────

    def _mixer_thread(self):
        while self.is_recording:
            try:
                mic = self.mic_queue.get(timeout=0.5)
            except queue.Empty:
                mic = None
            try:
                lb = self.loopback_queue.get_nowait()
            except queue.Empty:
                lb = None

            if mic is None and lb is None:
                continue

            if mic is not None and lb is not None:
                n = max(len(mic), len(lb))
                m = np.zeros((n, 1), dtype=DTYPE)
                l = np.zeros((n, 1), dtype=DTYPE)
                m[: len(mic)] = mic
                l[: len(lb)] = lb[:, :1]
                mixed = np.clip((m + l) * 0.5, -1, 1)
            elif mic is not None:
                mixed = mic
            else:
                mixed = lb[:, :1]

            self.mixed_queue.put(mixed)

        while not self.mic_queue.empty() or not self.loopback_queue.empty():
            try:
                mic = self.mic_queue.get_nowait()
            except queue.Empty:
                mic = None
            try:
                lb = self.loopback_queue.get_nowait()
            except queue.Empty:
                lb = None
            if mic is None and lb is None:
                break
            chunk = mic if lb is None else (lb[:, :1] if mic is None else np.clip((mic + lb[:, :1]) * 0.5, -1, 1))
            self.mixed_queue.put(chunk)

    # ── Transcription worker ───────────────────────────────────────────────────

    def _transcription_worker(self, source_queue: queue.Queue):
        buffer: list[np.ndarray] = []
        buffer_samples = 0
        chunk_samples = CHUNK_DURATION * SAMPLE_RATE

        while self.is_recording or not source_queue.empty():
            try:
                chunk = source_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            mono = chunk[:, 0] if chunk.ndim > 1 else chunk.flatten()
            buffer.append(mono)
            buffer_samples += len(mono)

            if buffer_samples >= chunk_samples:
                self._flush_buffer(buffer)
                buffer = []
                buffer_samples = 0

        if buffer:
            self._flush_buffer(buffer)

    def _flush_buffer(self, chunks: list[np.ndarray]):
        audio = np.concatenate(chunks)
        if rms(audio) < SILENCE_THRESHOLD:
            return

        text = self._transcribe(audio)
        if not text:
            return

        elapsed = time.time() - self.start_time
        mins, secs = divmod(int(elapsed), 60)
        timestamp_str = f"{mins}:{secs:02d}"

        self.transcript_segments.append({"timestamp": elapsed, "text": text})
        console.print(f"  [dim cyan][{timestamp_str}][/dim cyan] {text}")

    def _transcribe(self, audio: np.ndarray) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, audio, SAMPLE_RATE)
            path = f.name
        try:
            segments, _ = self.whisper.transcribe(path, beam_size=5, language="en")
            return " ".join(s.text.strip() for s in segments).strip()
        finally:
            os.unlink(path)

    # ── Main recording loop ────────────────────────────────────────────────────

    def record(self) -> Path | None:
        """Start recording. Returns the video path if screen recording was enabled."""
        self.is_recording = True
        self.start_time = time.time()

        # Start screen recorder if requested
        screen_recorder = None
        video_path = None
        if self.record_screen:
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
            video_path = self.output_dir / f"{ts}_recording.mp4"
            screen_recorder = ScreenRecorder(video_path, fps=self.screen_fps)
            screen_recorder.start()

        streams = []
        threads = []

        if self.mix_both:
            source_queue = self.mixed_queue
            mixer = threading.Thread(target=self._mixer_thread, daemon=True)
            threads.append(mixer)
            mixer.start()

            streams.append(sd.InputStream(
                device=self.device,
                channels=CHANNELS, samplerate=SAMPLE_RATE, dtype=DTYPE,
                callback=self._mic_callback, blocksize=int(SAMPLE_RATE * 0.5),
            ))
            if self.loopback_device is not None:
                streams.append(sd.InputStream(
                    device=self.loopback_device,
                    channels=2, samplerate=SAMPLE_RATE, dtype=DTYPE,
                    callback=self._loopback_callback, blocksize=int(SAMPLE_RATE * 0.5),
                ))

        elif self.loopback_device is not None:
            source_queue = self.mic_queue
            streams.append(sd.InputStream(
                device=self.loopback_device,
                channels=2, samplerate=SAMPLE_RATE, dtype=DTYPE,
                callback=self._mic_callback, blocksize=int(SAMPLE_RATE * 0.5),
            ))
        else:
            source_queue = self.mic_queue
            streams.append(sd.InputStream(
                device=self.device,
                channels=CHANNELS, samplerate=SAMPLE_RATE, dtype=DTYPE,
                callback=self._mic_callback, blocksize=int(SAMPLE_RATE * 0.5),
            ))

        t_worker = threading.Thread(
            target=self._transcription_worker, args=(source_queue,), daemon=True
        )
        threads.append(t_worker)
        t_worker.start()

        console.print(Rule("[bold green]Recording — press Ctrl+C to stop[/bold green]"))

        try:
            for s in streams:
                s.start()
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            console.print(Rule("[yellow]Stopping...[/yellow]"))
        finally:
            self.is_recording = False
            if screen_recorder:
                screen_recorder.stop()
                console.print(f"[dim]Screen recording saved.[/dim]")
            for s in streams:
                try:
                    s.stop()
                    s.close()
                except Exception:
                    pass
            for t in threads:
                t.join(timeout=90)

        return video_path

    # ── Transcript ─────────────────────────────────────────────────────────────

    def full_transcript(self) -> str:
        lines = []
        for seg in self.transcript_segments:
            mins, secs = divmod(int(seg["timestamp"]), 60)
            lines.append(f"[{mins}:{secs:02d}] {seg['text']}")
        return "\n".join(lines)

    # ── Summary via Claude ─────────────────────────────────────────────────────

    def generate_summary(self, api_key: str | None = None) -> str:
        transcript = self.full_transcript()
        if not transcript:
            return "*(No speech detected — transcript is empty.)*"

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return (
                "*(Set ANTHROPIC_API_KEY to generate a summary. "
                "Transcript saved separately.)*"
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

    # ── Save outputs ───────────────────────────────────────────────────────────

    def save(self, summary: str) -> tuple[Path, Path]:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        t_path = self.output_dir / f"{ts}_transcript.txt"
        s_path = self.output_dir / f"{ts}_summary.md"
        t_path.write_text(self.full_transcript(), encoding="utf-8")
        s_path.write_text(summary, encoding="utf-8")
        return t_path, s_path


# ── CLI ────────────────────────────────────────────────────────────────────────

def list_devices():
    console.print(Panel("[bold]Available audio devices[/bold]", expand=False))
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        direction = []
        if d["max_input_channels"] > 0:
            direction.append("IN")
        if d["max_output_channels"] > 0:
            direction.append("OUT")
        loopback = " [dim](loopback?)[/dim]" if "loopback" in d["name"].lower() else ""
        console.print(
            f"  [{i:>2}] [cyan]{d['name']}[/cyan] "
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
    parser.add_argument("--screen-fps", type=int, default=10,
                        help="Screen recording frame rate (default: 10 — lower = smaller file)")
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
            loopback_device = find_loopback_device()
        if loopback_device is None:
            console.print(
                "[bold red]No loopback device found.[/bold red]\n"
                "On Windows: enable 'Stereo Mix' in Sound settings, or install VB-Cable.\n"
                "On macOS: install BlackHole (brew install blackhole-2ch).\n"
                "Then re-run with [cyan]--loopback-device INDEX[/cyan] from [cyan]--list-devices[/cyan]."
            )
            sys.exit(1)
        console.print(f"[dim]Loopback: [{loopback_device}] {sd.query_devices(loopback_device)['name']}[/dim]")

    recorder = MeetingRecorder(
        device=args.device,
        loopback_device=loopback_device,
        mix_both=args.both,
        model_size=args.model,
        output_dir=args.output_dir,
        record_screen=args.record_screen,
        screen_fps=args.screen_fps,
    )

    audio_mode = "mic + system audio" if args.both else ("system audio" if args.loopback else "microphone")
    extras = " + screen video" if args.record_screen else ""
    console.print(Panel(
        f"[bold]Meeting Note Taker[/bold]\n"
        f"Capturing: [cyan]{audio_mode}{extras}[/cyan]  |  "
        f"Model: [cyan]{args.model}[/cyan]  |  "
        f"Output: [cyan]{recorder.output_dir}[/cyan]\n\n"
        f"[dim]Live transcript appears below. Press Ctrl+C when the meeting ends.[/dim]",
        expand=False,
    ))

    video_path = recorder.record()

    console.print()
    summary = recorder.generate_summary(api_key=args.api_key)
    t_path, s_path = recorder.save(summary)

    console.print(Rule("[bold]Summary[/bold]"))
    console.print(Markdown(summary))
    console.print()
    console.print(f"[green]Transcript:[/green] {t_path}")
    console.print(f"[green]Summary:[/green]    {s_path}")
    if video_path:
        console.print(f"[green]Video:[/green]      {video_path}")


if __name__ == "__main__":
    main()
