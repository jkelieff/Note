#!/usr/bin/env python3
"""
Meeting Note Taker — Desktop App
================================

A small always-on-top control panel that sits on your desktop. When you record:

  * you pick which screen the meeting is on,
  * the panel floats on top of everything, and
  * it hides *itself* from the meeting's screen share (and from the recording),
  * it captures that screen to video + captures the audio,
  * and when you stop it transcribes the meeting and writes a summary with
    action items via Claude.

The "invisible to the meeting" trick uses OS window-capture exclusion:
  * Windows 10 2004+ : SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
  * macOS            : NSWindow.sharingType = NSWindowSharingNone
Anyone sharing/recording their screen (Teams, Zoom, Meet, OBS, PrintScreen)
will not see this window, even though you can.

Run:  python desktop_app.py
"""

from __future__ import annotations

import os
import sys
import platform
from pathlib import Path

try:
    from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
    from PySide6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
        QComboBox, QCheckBox, QPlainTextEdit, QFileDialog, QFrame, QMessageBox,
    )
except ImportError:
    sys.stderr.write(
        "PySide6 is required for the desktop app.\n"
        "Install it with:  pip install PySide6\n"
    )
    raise

import note_engine as engine


# ── Window-capture exclusion ─────────────────────────────────────────────────

def exclude_from_capture(widget: QWidget) -> bool:
    """
    Make this window invisible to screen capture / sharing.
    Returns True if the OS accepted the request.
    """
    system = platform.system()
    try:
        if system == "Windows":
            import ctypes
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            hwnd = int(widget.winId())
            user32 = ctypes.windll.user32
            user32.SetWindowDisplayAffinity.restype = ctypes.c_bool
            return bool(user32.SetWindowDisplayAffinity(hwnd,
                                                        WDA_EXCLUDEFROMCAPTURE))
        if system == "Darwin":
            import ctypes
            import objc  # pyobjc
            NSWindowSharingNone = 0
            view = objc.objc_object(c_void_p=int(widget.winId()))
            window = view.window()
            window.setSharingType_(NSWindowSharingNone)
            return True
    except Exception:
        return False
    # Linux/X11 has no reliable per-window capture exclusion.
    return False


# ── Background stop/summarise worker ─────────────────────────────────────────

class StartWorker(QThread):
    """
    Loads the Whisper model and starts live capture off the UI thread so the
    panel never freezes — the first run may download the model.
    """
    started_ok = Signal()
    failed = Signal(str)

    def __init__(self, live: "engine.LiveRecorder"):
        super().__init__()
        self.live = live

    def run(self):
        try:
            self.live.start()
            self.started_ok.emit()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class StopWorker(QThread):
    """
    Finalises a recording off the UI thread: stops audio+screen capture, flushes
    the last transcription pass, calls Claude for the summary, and saves files.
    """
    log = Signal(str)
    finished_ok = Signal(str, str, str)   # summary_md, transcript_path, summary_path
    failed = Signal(str)

    def __init__(self, live: engine.LiveRecorder,
                 screen: engine.ScreenRecorder | None,
                 output_dir: Path, api_key: str | None):
        super().__init__()
        self.live = live
        self.screen = screen
        self.output_dir = output_dir
        self.api_key = api_key

    def run(self):
        try:
            self.log.emit("Stopping capture and finishing transcription...")
            self.live.stop()
            if self.screen:
                self.screen.stop()
                self.log.emit("Screen recording saved.")

            transcript_text = self.live.transcript.as_text()
            summary = engine.generate_summary(
                transcript_text, api_key=self.api_key,
                on_log=lambda m: self.log.emit(m))
            t_path, s_path = engine.save_outputs(
                self.output_dir, transcript_text, summary)
            self.finished_ok.emit(summary, str(t_path), str(s_path))
        except Exception as exc:  # surface, don't crash the app
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ── Live segment bridge (thread -> UI) ───────────────────────────────────────

class SegmentBridge(QObject):
    segment = Signal(float, str)
    log = Signal(str)


# ── Main window ──────────────────────────────────────────────────────────────

class NoteTakerWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Meeting Note Taker")
        self.setWindowFlags(
            Qt.Window | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setMinimumWidth(360)

        self.live: engine.LiveRecorder | None = None
        self.screen_recorder: engine.ScreenRecorder | None = None
        self.start_worker: StartWorker | None = None
        self.stop_worker: StopWorker | None = None
        self.bridge = SegmentBridge()
        self.bridge.segment.connect(self._on_segment)
        self.bridge.log.connect(self._log)

        self.monitors = engine.list_monitors()
        self.audio_devices = engine.list_audio_devices()

        self._build_ui()
        self._apply_style()

    # -- UI construction --
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        # Header
        title = QLabel("● Meeting Note Taker")
        title.setObjectName("title")
        root.addWidget(title)

        self.hidden_label = QLabel("Hidden from screen share: checking…")
        self.hidden_label.setObjectName("hint")
        root.addWidget(self.hidden_label)

        root.addWidget(self._sep())

        # Screen picker
        root.addWidget(self._field_label("Screen the meeting is on"))
        self.screen_combo = QComboBox()
        for m in self.monitors:
            self.screen_combo.addItem(m.label, m.index)
        # default to primary
        for i, m in enumerate(self.monitors):
            if m.primary:
                self.screen_combo.setCurrentIndex(i)
                break
        root.addWidget(self.screen_combo)

        # Audio source
        root.addWidget(self._field_label("Audio to capture"))
        self.audio_combo = QComboBox()
        self.audio_combo.addItem("Microphone only", "mic")
        self.audio_combo.addItem("System audio only (others' voices)", "loopback")
        self.audio_combo.addItem("Both — mic + system (everyone)", "both")
        self.audio_combo.setCurrentIndex(2)
        self.audio_combo.currentIndexChanged.connect(self._refresh_device_row)
        root.addWidget(self.audio_combo)

        # Loopback device row
        self.device_label = self._field_label("System-audio device (loopback)")
        root.addWidget(self.device_label)
        self.device_combo = QComboBox()
        self._populate_loopback_devices()
        root.addWidget(self.device_combo)

        # Options row: model + screen recording toggle
        opts = QHBoxLayout()
        opts.addWidget(self._field_label("Accuracy"))
        self.model_combo = QComboBox()
        for m in ["tiny", "base", "small", "medium", "large-v3"]:
            self.model_combo.addItem(m, m)
        self.model_combo.setCurrentText("base")
        opts.addWidget(self.model_combo)
        self.record_screen_check = QCheckBox("Record video")
        self.record_screen_check.setChecked(True)
        opts.addWidget(self.record_screen_check)
        opts.addStretch(1)
        root.addLayout(opts)

        # Output dir
        out_row = QHBoxLayout()
        self.output_dir = Path.cwd() / "meetings"
        self.output_label = QLabel(self._short_path(self.output_dir))
        self.output_label.setObjectName("hint")
        out_row.addWidget(self.output_label, 1)
        browse = QPushButton("Change…")
        browse.clicked.connect(self._choose_output)
        out_row.addWidget(browse)
        root.addLayout(out_row)

        root.addWidget(self._sep())

        # Record / stop
        self.record_btn = QPushButton("● Start recording")
        self.record_btn.setObjectName("record")
        self.record_btn.clicked.connect(self._toggle_record)
        root.addWidget(self.record_btn)

        self.status_label = QLabel("Ready.")
        self.status_label.setObjectName("status")
        root.addWidget(self.status_label)

        # Live transcript (hidden until recording)
        self.transcript_view = QPlainTextEdit()
        self.transcript_view.setReadOnly(True)
        self.transcript_view.setObjectName("transcript")
        self.transcript_view.setPlaceholderText(
            "Live transcript will appear here while recording…")
        self.transcript_view.setFixedHeight(150)
        root.addWidget(self.transcript_view)

        self._refresh_device_row()

    def _apply_style(self):
        self.setStyleSheet("""
            QWidget { background: #1c1f26; color: #e6e8ee;
                      font-family: 'Segoe UI', system-ui, sans-serif;
                      font-size: 13px; }
            QLabel#title { font-size: 15px; font-weight: 600; color: #ff6b6b; }
            QLabel#hint  { color: #8a92a6; font-size: 11px; }
            QLabel#status{ color: #8a92a6; font-size: 12px; }
            QLabel.field { color: #b6bccb; font-size: 11px; margin-top: 2px; }
            QComboBox, QPushButton, QPlainTextEdit {
                background: #262a33; border: 1px solid #343a46;
                border-radius: 7px; padding: 6px 8px; }
            QComboBox:hover, QPushButton:hover { border-color: #4a5568; }
            QPushButton { background: #2f3542; }
            QPushButton:hover { background: #384050; }
            QPushButton#record { background: #e03e3e; color: white;
                font-weight: 600; padding: 10px; border: none; font-size: 14px; }
            QPushButton#record:hover { background: #f04545; }
            QPushButton#record[recording="true"] { background: #444b5a; }
            QPlainTextEdit#transcript { font-family: 'Consolas', monospace;
                font-size: 12px; color: #cdd3e0; }
            QCheckBox { color: #b6bccb; }
        """)

    # -- small helpers --
    def _sep(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #2b303a; background: #2b303a; max-height:1px;")
        return line

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setProperty("class", "field")
        lbl.setStyleSheet("color:#b6bccb; font-size:11px;")
        return lbl

    def _short_path(self, p: Path) -> str:
        s = str(p)
        return "Saving to: " + (("…" + s[-40:]) if len(s) > 42 else s)

    def _populate_loopback_devices(self):
        self.device_combo.clear()
        self.device_combo.addItem("Auto-detect", None)
        for d in self.audio_devices:
            if d.is_input:
                tag = "  ← loopback?" if d.looks_like_loopback else ""
                self.device_combo.addItem(f"[{d.index}] {d.name}{tag}", d.index)
        if not self.audio_devices:
            self.device_combo.addItem(
                "(no audio devices found — install sounddevice)", None)

    def _refresh_device_row(self):
        mode = self.audio_combo.currentData()
        show = mode in ("loopback", "both")
        self.device_label.setVisible(show)
        self.device_combo.setVisible(show)

    def _choose_output(self):
        d = QFileDialog.getExistingDirectory(self, "Choose output folder",
                                             str(self.output_dir))
        if d:
            self.output_dir = Path(d)
            self.output_label.setText(self._short_path(self.output_dir))

    # -- capture exclusion applied once native handle exists --
    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_capture_exclusion)

    def _apply_capture_exclusion(self):
        ok = exclude_from_capture(self)
        if ok:
            self.hidden_label.setText("✓ Hidden from screen share & recordings")
            self.hidden_label.setStyleSheet("color:#4ecb71; font-size:11px;")
        elif platform.system() == "Linux":
            self.hidden_label.setText(
                "⚠ Capture-hiding not supported on Linux (Windows/macOS only)")
            self.hidden_label.setStyleSheet("color:#e0a13e; font-size:11px;")
        else:
            self.hidden_label.setText(
                "⚠ Could not hide window — update Windows or run again")
            self.hidden_label.setStyleSheet("color:#e0a13e; font-size:11px;")

    # -- recording lifecycle --
    def _toggle_record(self):
        busy = self.start_worker is not None or self.stop_worker is not None
        if busy:
            return
        if self.live is None:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        mode = self.audio_combo.currentData()
        mix_both = mode == "both"
        use_loopback = mode in ("loopback", "both")

        loopback_device = None
        if use_loopback:
            loopback_device = self.device_combo.currentData()
            if loopback_device is None:
                loopback_device = engine.find_loopback_device()
            if loopback_device is None:
                QMessageBox.warning(
                    self, "No loopback device",
                    "Couldn't find a system-audio (loopback) device.\n\n"
                    "Windows: enable 'Stereo Mix' in Sound settings or install "
                    "VB-Cable.\nmacOS: install BlackHole.\n\n"
                    "Then pick it in the device list, or choose "
                    "'Microphone only'.")
                return

        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Screen recorder for the chosen monitor.
        self.screen_recorder = None
        if self.record_screen_check.isChecked():
            if not engine.ffmpeg_available():
                QMessageBox.warning(self, "ffmpeg not found",
                                    engine.ffmpeg_install_hint())
                return
            idx = self.screen_combo.currentData()
            monitor = next((m for m in self.monitors if m.index == idx),
                           self.monitors[0])
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
            video_path = self.output_dir / f"{ts}_recording.mp4"
            self.screen_recorder = engine.ScreenRecorder(video_path, monitor)
            try:
                self.screen_recorder.start()
            except Exception as exc:
                QMessageBox.warning(self, "Screen recording failed", str(exc))
                self.screen_recorder = None
                return

        # Live audio + transcription.
        transcriber = engine.Transcriber(self.model_combo.currentData())
        self.live = engine.LiveRecorder(
            transcriber,
            device=None,
            loopback_device=loopback_device,
            mix_both=mix_both,
            on_segment=lambda t, x: self.bridge.segment.emit(t, x),
            on_log=lambda m: self.bridge.log.emit(m),
        )

        self.transcript_view.clear()
        self._set_controls_enabled(False)
        self.record_btn.setEnabled(False)
        self.record_btn.setText("Starting…")
        self._log("Loading transcription model (first run downloads it)…")

        # Model load + stream start happen off the UI thread so we never freeze.
        self.start_worker = StartWorker(self.live)
        self.start_worker.started_ok.connect(self._on_started)
        self.start_worker.failed.connect(self._on_start_failed)
        self.start_worker.start()

    def _on_started(self):
        self.start_worker = None
        self.record_btn.setEnabled(True)
        self.record_btn.setText("■ Stop & summarise")
        self.record_btn.setProperty("recording", "true")
        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)
        self._log("Recording… this window is hidden from the meeting.")

    def _on_start_failed(self, message: str):
        self.start_worker = None
        if self.screen_recorder:
            try:
                self.screen_recorder.stop()
            except Exception:
                pass
            self.screen_recorder = None
        self.live = None
        self._set_controls_enabled(True)
        self._reset_record_button()
        self._log("Ready.")
        QMessageBox.critical(self, "Could not start recording", message)

    def _stop_recording(self):
        if self.live is None:
            return
        self.record_btn.setEnabled(False)
        self.record_btn.setText("Finishing…")
        self._log("Finalising — transcribing the last bit and summarising…")

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.stop_worker = StopWorker(self.live, self.screen_recorder,
                                      self.output_dir, api_key)
        self.stop_worker.log.connect(self._log)
        self.stop_worker.finished_ok.connect(self._on_finished)
        self.stop_worker.failed.connect(self._on_failed)
        self.stop_worker.start()
        # Ownership handed to worker; clear our live handle so toggle is inert.
        self.live = None
        self.screen_recorder = None

    def _on_finished(self, summary_md: str, t_path: str, s_path: str):
        self.stop_worker = None
        self._reset_record_button()
        self._set_controls_enabled(True)
        self._log(f"Done. Saved summary → {Path(s_path).name}")
        self.transcript_view.appendPlainText(
            "\n" + "─" * 40 + "\nSUMMARY\n" + "─" * 40 + "\n" + summary_md)
        self.transcript_view.appendPlainText(
            f"\nTranscript: {t_path}\nSummary:    {s_path}")
        QMessageBox.information(
            self, "Meeting processed",
            f"Summary and action items saved.\n\n"
            f"Transcript:\n{t_path}\n\nSummary:\n{s_path}")

    def _on_failed(self, message: str):
        self.stop_worker = None
        self._reset_record_button()
        self._set_controls_enabled(True)
        self._log("Failed.")
        QMessageBox.critical(self, "Something went wrong", message)

    def _reset_record_button(self):
        self.record_btn.setEnabled(True)
        self.record_btn.setText("● Start recording")
        self.record_btn.setProperty("recording", "false")
        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)

    def _set_controls_enabled(self, enabled: bool):
        for w in (self.screen_combo, self.audio_combo, self.device_combo,
                  self.model_combo, self.record_screen_check):
            w.setEnabled(enabled)

    # -- bridge slots --
    def _on_segment(self, timestamp: float, text: str):
        mins, secs = divmod(int(timestamp), 60)
        self.transcript_view.appendPlainText(f"[{mins}:{secs:02d}] {text}")

    def _log(self, message: str):
        self.status_label.setText(message)

    def closeEvent(self, event):
        if self.live is not None or self.stop_worker is not None:
            resp = QMessageBox.question(
                self, "Recording in progress",
                "A recording is still running. Stop and discard it?",
                QMessageBox.Yes | QMessageBox.No)
            if resp != QMessageBox.Yes:
                event.ignore()
                return
            if self.live is not None:
                try:
                    self.live.stop()
                except Exception:
                    pass
            if self.screen_recorder is not None:
                try:
                    self.screen_recorder.stop()
                except Exception:
                    pass
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Meeting Note Taker")
    win = NoteTakerWindow()
    win.show()
    # Nudge on-top + re-apply exclusion after the compositor settles.
    win.raise_()
    win.activateWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
