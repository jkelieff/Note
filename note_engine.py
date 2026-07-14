#!/usr/bin/env python3
"""
Shared backend engine for the Meeting Note Taker.

This module holds everything that is *not* UI: monitor-aware screen recording,
live audio capture + mixing, Whisper transcription, and the Claude summary.
Both the CLI (`note_taker.py`) and the desktop app (`desktop_app.py`) build on
top of it so the recording/transcription behaviour stays identical everywhere.

Heavy third-party imports (sounddevice, faster_whisper, anthropic) are pulled in
lazily so that simply importing this module — e.g. to enumerate monitors from the
GUI — never fails on a machine that hasn't installed the audio/ML stack yet.
"""

from __future__ import annotations

import os
import sys
import time
import queue
import shutil
import platform
import subprocess
import threading
import tempfile
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

# ── Constants ────────────────────────────────────────────────────────────────

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION = 30        # seconds of audio per Whisper transcription pass
SILENCE_THRESHOLD = 0.005  # RMS below this = silence, skip transcription
DTYPE = np.float32

SUMMARY_MODEL = "claude-opus-4-8"

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


# ── Small helpers ────────────────────────────────────────────────────────────

def rms(data: np.ndarray) -> float:
    return float(np.sqrt(np.mean(data ** 2)))


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffmpeg_install_hint() -> str:
    return (
        "ffmpeg not found. Install it to record the screen:\n"
        "  Windows:  winget install ffmpeg   (or https://ffmpeg.org/download.html)\n"
        "  macOS:    brew install ffmpeg\n"
        "  Linux:    sudo apt install ffmpeg"
    )


@dataclass
class AudioDevice:
    index: int
    name: str
    max_input: int
    max_output: int

    @property
    def is_input(self) -> bool:
        return self.max_input > 0

    @property
    def looks_like_loopback(self) -> bool:
        n = self.name.lower()
        return any(k in n for k in (
            "loopback", "stereo mix", "what u hear",
            "blackhole", "soundflower", "cable output", "vb-audio",
        ))


def list_audio_devices() -> list[AudioDevice]:
    """Enumerate audio devices. Returns [] if sounddevice/PortAudio is missing."""
    try:
        import sounddevice as sd
    except Exception:
        return []
    out: list[AudioDevice] = []
    for i, d in enumerate(sd.query_devices()):
        out.append(AudioDevice(
            index=i,
            name=d["name"],
            max_input=d["max_input_channels"],
            max_output=d["max_output_channels"],
        ))
    return out


def find_loopback_device() -> Optional[int]:
    """Best-guess loopback device index for the current platform, or None."""
    for d in list_audio_devices():
        if d.looks_like_loopback and d.is_input:
            return d.index
    return None


# ── Monitor enumeration ──────────────────────────────────────────────────────

@dataclass
class Monitor:
    index: int
    name: str
    x: int
    y: int
    width: int
    height: int
    primary: bool = False

    @property
    def label(self) -> str:
        tag = " (primary)" if self.primary else ""
        return f"Screen {self.index + 1}: {self.width}x{self.height}{tag}"


def list_monitors() -> list[Monitor]:
    """
    Enumerate monitors with their virtual-desktop geometry.

    Tries Qt first (accurate, matches what the GUI sees), then the `screeninfo`
    package, then falls back to a single primary screen so the CLI still works.
    """
    # 1) Qt — preferred, since the desktop app is Qt-based anyway.
    try:
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        created = False
        if app is None:
            app = QGuiApplication(sys.argv[:1])
            created = True
        primary = app.primaryScreen()
        mons: list[Monitor] = []
        for i, screen in enumerate(app.screens()):
            g = screen.geometry()
            mons.append(Monitor(
                index=i,
                name=screen.name() or f"Screen {i + 1}",
                x=g.x(), y=g.y(), width=g.width(), height=g.height(),
                primary=(screen == primary),
            ))
        if created:
            # Don't tear down a real app; only the throwaway one.
            del app
        if mons:
            return mons
    except Exception:
        pass

    # 2) screeninfo fallback.
    try:
        from screeninfo import get_monitors as _gm
        mons = []
        for i, m in enumerate(_gm()):
            mons.append(Monitor(
                index=i,
                name=getattr(m, "name", None) or f"Screen {i + 1}",
                x=m.x, y=m.y, width=m.width, height=m.height,
                primary=bool(getattr(m, "is_primary", i == 0)),
            ))
        if mons:
            return mons
    except Exception:
        pass

    # 3) Single-screen fallback so nothing hard-crashes.
    return [Monitor(index=0, name="Screen 1", x=0, y=0,
                    width=1920, height=1080, primary=True)]


# ── Screen recorder ──────────────────────────────────────────────────────────

class ScreenRecorder:
    """
    Wraps ffmpeg to record a monitor to an MP4 in the background.

    If a `monitor` is given, only that monitor's region is captured (so the user
    can point the recorder at exactly the screen the meeting is on).
    """

    def __init__(self, output_path: Path, monitor: Optional[Monitor] = None,
                 fps: int = 12):
        self.output_path = Path(output_path)
        self.monitor = monitor
        self.fps = fps
        self._process: Optional[subprocess.Popen] = None

    def _build_command(self) -> list[str]:
        system = platform.system()
        m = self.monitor
        common_out = [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-pix_fmt", "yuv420p",
            str(self.output_path),
        ]

        if system == "Windows":
            cmd = ["ffmpeg", "-y", "-f", "gdigrab", "-framerate", str(self.fps)]
            if m is not None:
                cmd += [
                    "-offset_x", str(m.x),
                    "-offset_y", str(m.y),
                    "-video_size", f"{m.width}x{m.height}",
                ]
            cmd += ["-i", "desktop"]
            return cmd + common_out

        if system == "Darwin":
            # avfoundation indexes capture screens after cameras; the monitor
            # index maps to a screen device. "index:none" = video only.
            screen_idx = (m.index if m is not None else 0)
            return [
                "ffmpeg", "-y",
                "-f", "avfoundation",
                "-framerate", str(self.fps),
                "-i", f"{screen_idx}:none",
            ] + common_out

        # Linux / X11
        display = os.environ.get("DISPLAY", ":0")
        cmd = ["ffmpeg", "-y", "-f", "x11grab", "-framerate", str(self.fps)]
        if m is not None:
            cmd += ["-video_size", f"{m.width}x{m.height}",
                    "-i", f"{display}+{m.x},{m.y}"]
        else:
            cmd += ["-i", display]
        return cmd + common_out

    def start(self) -> None:
        if not ffmpeg_available():
            raise RuntimeError(ffmpeg_install_hint())
        self._process = subprocess.Popen(
            self._build_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self) -> None:
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


# ── Transcript container ─────────────────────────────────────────────────────

@dataclass
class Transcript:
    segments: list[dict] = field(default_factory=list)

    def add(self, timestamp: float, text: str) -> None:
        self.segments.append({"timestamp": timestamp, "text": text})

    def is_empty(self) -> bool:
        return len(self.segments) == 0

    def as_text(self) -> str:
        lines = []
        for seg in self.segments:
            mins, secs = divmod(int(seg["timestamp"]), 60)
            lines.append(f"[{mins}:{secs:02d}] {seg['text']}")
        return "\n".join(lines)


# ── Whisper wrapper ──────────────────────────────────────────────────────────

class Transcriber:
    """Lazy-loaded faster-whisper model with a simple transcribe() call."""

    def __init__(self, model_size: str = "base"):
        self.model_size = model_size
        self._model = None

    def load(self, log: Callable[[str], None] = lambda _m: None) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel
        log(f"Loading Whisper [{self.model_size}] "
            "(first run downloads the model)...")
        self._model = WhisperModel(self.model_size, device="cpu",
                                   compute_type="int8")
        log("Whisper ready.")

    def transcribe_array(self, audio: np.ndarray) -> str:
        import soundfile as sf
        self.load()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, audio, SAMPLE_RATE)
            path = f.name
        try:
            segments, _ = self._model.transcribe(path, beam_size=5, language="en")
            return " ".join(s.text.strip() for s in segments).strip()
        finally:
            os.unlink(path)

    def transcribe_file(self, wav_path: Path,
                        on_segment: Callable[[float, str], None] | None = None
                        ) -> Transcript:
        self.load()
        segments, _ = self._model.transcribe(str(wav_path), beam_size=5,
                                             language="en")
        transcript = Transcript()
        for seg in segments:
            text = seg.text.strip()
            if text:
                transcript.add(seg.start, text)
                if on_segment:
                    on_segment(seg.start, text)
        return transcript


# ── Live audio capture + transcription ───────────────────────────────────────

class LiveRecorder:
    """
    Captures audio live (mic, system loopback, or both mixed), transcribes it in
    ~30s chunks, and reports each finished segment through `on_segment`.

    Runs entirely in background threads. Call start(), later stop().
    """

    def __init__(self, transcriber: Transcriber,
                 device: Optional[int] = None,
                 loopback_device: Optional[int] = None,
                 mix_both: bool = False,
                 on_segment: Callable[[float, str], None] | None = None,
                 on_log: Callable[[str], None] | None = None):
        self.transcriber = transcriber
        self.device = device
        self.loopback_device = loopback_device
        self.mix_both = mix_both
        self.on_segment = on_segment or (lambda _t, _x: None)
        self.on_log = on_log or (lambda _m: None)

        self.mic_queue: queue.Queue = queue.Queue()
        self.loopback_queue: queue.Queue = queue.Queue()
        self.mixed_queue: queue.Queue = queue.Queue()

        self.transcript = Transcript()
        self.is_recording = False
        self.start_time = 0.0
        self._streams: list = []
        self._threads: list[threading.Thread] = []

    # -- audio callbacks --
    def _mic_callback(self, indata, frames, time_info, status):
        self.mic_queue.put(indata.copy())

    def _loopback_callback(self, indata, frames, time_info, status):
        self.loopback_queue.put(indata.copy())

    # -- mixer --
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
            self.mixed_queue.put(self._mix(mic, lb))

        # drain
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
            self.mixed_queue.put(self._mix(mic, lb))

    @staticmethod
    def _mix(mic, lb):
        if mic is not None and lb is not None:
            n = max(len(mic), len(lb))
            m = np.zeros((n, 1), dtype=DTYPE)
            l = np.zeros((n, 1), dtype=DTYPE)
            m[: len(mic)] = mic
            l[: len(lb)] = lb[:, :1]
            return np.clip((m + l) * 0.5, -1, 1)
        if mic is not None:
            return mic
        return lb[:, :1]

    # -- transcription worker --
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
                self._flush(buffer)
                buffer, buffer_samples = [], 0

        if buffer:
            self._flush(buffer)

    def _flush(self, chunks: list[np.ndarray]):
        audio = np.concatenate(chunks)
        if rms(audio) < SILENCE_THRESHOLD:
            return
        text = self.transcriber.transcribe_array(audio)
        if not text:
            return
        elapsed = time.time() - self.start_time
        self.transcript.add(elapsed, text)
        self.on_segment(elapsed, text)

    # -- lifecycle --
    def start(self):
        import sounddevice as sd
        self.transcriber.load(self.on_log)
        self.is_recording = True
        self.start_time = time.time()
        block = int(SAMPLE_RATE * 0.5)

        if self.mix_both:
            source_queue = self.mixed_queue
            mixer = threading.Thread(target=self._mixer_thread, daemon=True)
            self._threads.append(mixer)
            mixer.start()
            self._streams.append(sd.InputStream(
                device=self.device, channels=CHANNELS, samplerate=SAMPLE_RATE,
                dtype=DTYPE, callback=self._mic_callback, blocksize=block))
            if self.loopback_device is not None:
                self._streams.append(sd.InputStream(
                    device=self.loopback_device, channels=2,
                    samplerate=SAMPLE_RATE, dtype=DTYPE,
                    callback=self._loopback_callback, blocksize=block))
        elif self.loopback_device is not None:
            source_queue = self.mic_queue
            self._streams.append(sd.InputStream(
                device=self.loopback_device, channels=2, samplerate=SAMPLE_RATE,
                dtype=DTYPE, callback=self._mic_callback, blocksize=block))
        else:
            source_queue = self.mic_queue
            self._streams.append(sd.InputStream(
                device=self.device, channels=CHANNELS, samplerate=SAMPLE_RATE,
                dtype=DTYPE, callback=self._mic_callback, blocksize=block))

        worker = threading.Thread(target=self._transcription_worker,
                                  args=(source_queue,), daemon=True)
        self._threads.append(worker)
        worker.start()

        for s in self._streams:
            s.start()

    def stop(self):
        self.is_recording = False
        for s in self._streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self._streams = []
        for t in self._threads:
            t.join(timeout=120)
        self._threads = []


# ── Summary via Claude ───────────────────────────────────────────────────────

def generate_summary(transcript_text: str,
                     api_key: Optional[str] = None,
                     on_log: Callable[[str], None] = lambda _m: None) -> str:
    if not transcript_text.strip():
        return "*(No speech detected — transcript is empty.)*"

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return ("*(Set ANTHROPIC_API_KEY to generate a summary. "
                "The transcript has been saved separately.)*")

    import anthropic
    client = anthropic.Anthropic(api_key=key)
    on_log("Sending transcript to Claude for analysis...")
    response = client.messages.create(
        model=SUMMARY_MODEL,
        max_tokens=2048,
        messages=[{"role": "user",
                   "content": SUMMARY_PROMPT.format(transcript=transcript_text)}],
    )
    return response.content[0].text


# ── Output helpers ───────────────────────────────────────────────────────────

def save_outputs(output_dir: Path, transcript_text: str, summary: str,
                 stem: str | None = None) -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    base = f"{ts}_{stem}" if stem else ts
    t_path = output_dir / f"{base}_transcript.txt"
    s_path = output_dir / f"{base}_summary.md"
    t_path.write_text(transcript_text, encoding="utf-8")
    s_path.write_text(summary, encoding="utf-8")
    return t_path, s_path


def extract_audio_to_wav(input_path: Path) -> Path:
    """Extract/convert any media file to a 16kHz mono WAV Whisper can read."""
    if not ffmpeg_available():
        raise RuntimeError(ffmpeg_install_hint())
    tmp_wav = Path(tempfile.gettempdir()) / f"_note_audio_{os.getpid()}.wav"
    cmd = ["ffmpeg", "-y", "-i", str(input_path), "-vn", "-ac", "1",
           "-ar", str(SAMPLE_RATE), "-f", "wav", str(tmp_wav)]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError("ffmpeg failed to read the file:\n"
                           + result.stderr.decode(errors="ignore")[-1000:])
    return tmp_wav
