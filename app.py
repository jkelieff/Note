#!/usr/bin/env python3
"""
Meeting Note Taker — desktop app.

A simple windowed app: pick a meeting recording, click one button, and get a
transcript + summary + action items. No command line needed.

Run with:   python app.py
Or build into a standalone .exe with PyInstaller (see BUILD.md).
"""

import os
import sys
import json
import queue
import threading
import webbrowser
import subprocess
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

import notecore

APP_NAME = "Meeting Note Taker"
CONFIG_PATH = Path.home() / ".meeting_note_taker.json"
DEFAULT_OUTPUT = Path.home() / "Documents" / "MeetingNotes"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


class NoteTakerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self.msg_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.last_output_dir: Path | None = None

        root.title(APP_NAME)
        root.geometry("760x620")
        root.minsize(680, 560)

        self._build_ui()
        self._poll_queue()

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        header = ttk.Label(self.root, text=APP_NAME, font=("Segoe UI", 16, "bold"))
        header.pack(anchor="w", padx=12, pady=(12, 0))
        ttk.Label(
            self.root,
            text="Turn a meeting recording into a transcript, summary and action items.",
            foreground="#555",
        ).pack(anchor="w", padx=12, pady=(0, 8))

        # Recording file row
        frm_file = ttk.Frame(self.root)
        frm_file.pack(fill="x", **pad)
        ttk.Label(frm_file, text="Recording:").pack(side="left")
        self.file_var = tk.StringVar()
        ttk.Entry(frm_file, textvariable=self.file_var).pack(
            side="left", fill="x", expand=True, padx=6
        )
        ttk.Button(frm_file, text="Browse...", command=self._pick_file).pack(side="left")

        # Options row
        frm_opts = ttk.Frame(self.root)
        frm_opts.pack(fill="x", **pad)
        ttk.Label(frm_opts, text="Accuracy:").pack(side="left")
        self.model_var = tk.StringVar(value=self.cfg.get("model", "base"))
        ttk.Combobox(
            frm_opts, textvariable=self.model_var, width=12, state="readonly",
            values=["tiny", "base", "small", "medium", "large-v3"],
        ).pack(side="left", padx=6)
        ttk.Label(frm_opts, text="(larger = more accurate, slower)",
                  foreground="#888").pack(side="left")

        # API key row
        frm_key = ttk.Frame(self.root)
        frm_key.pack(fill="x", **pad)
        ttk.Label(frm_key, text="Anthropic API key:").pack(side="left")
        self.key_var = tk.StringVar(value=self.cfg.get("api_key", ""))
        self.key_entry = ttk.Entry(frm_key, textvariable=self.key_var, show="•")
        self.key_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm_key, text="Show", variable=self.show_key,
                        command=self._toggle_key).pack(side="left")

        # Output folder row
        frm_out = ttk.Frame(self.root)
        frm_out.pack(fill="x", **pad)
        ttk.Label(frm_out, text="Save notes to:").pack(side="left")
        self.out_var = tk.StringVar(value=self.cfg.get("output_dir", str(DEFAULT_OUTPUT)))
        ttk.Entry(frm_out, textvariable=self.out_var).pack(
            side="left", fill="x", expand=True, padx=6
        )
        ttk.Button(frm_out, text="Change...", command=self._pick_output).pack(side="left")

        # Action buttons
        frm_act = ttk.Frame(self.root)
        frm_act.pack(fill="x", **pad)
        self.go_btn = ttk.Button(frm_act, text="Generate Notes", command=self._start)
        self.go_btn.pack(side="left")
        self.open_btn = ttk.Button(frm_act, text="Open notes folder",
                                   command=self._open_output, state="disabled")
        self.open_btn.pack(side="left", padx=6)

        # Progress
        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", padx=12, pady=(4, 0))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self.root, textvariable=self.status_var, foreground="#555").pack(
            anchor="w", padx=12, pady=(2, 6)
        )

        # Output display
        ttk.Label(self.root, text="Summary & actions:").pack(anchor="w", padx=12)
        self.output = scrolledtext.ScrolledText(self.root, height=14, wrap="word",
                                                font=("Consolas", 10))
        self.output.pack(fill="both", expand=True, padx=12, pady=(2, 12))

    def _toggle_key(self):
        self.key_entry.config(show="" if self.show_key.get() else "•")

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="Choose a meeting recording",
            filetypes=[
                ("Recordings", "*.mp4 *.mkv *.mov *.avi *.webm *.mp3 *.wav *.m4a"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.file_var.set(path)

    def _pick_output(self):
        path = filedialog.askdirectory(title="Choose where to save notes")
        if path:
            self.out_var.set(path)

    # ── Run ─────────────────────────────────────────────────────────────────

    def _start(self):
        if self.worker and self.worker.is_alive():
            return
        recording = self.file_var.get().strip()
        if not recording:
            messagebox.showwarning(APP_NAME, "Please choose a recording file first.")
            return
        if not Path(recording).exists():
            messagebox.showerror(APP_NAME, f"File not found:\n{recording}")
            return

        # Persist settings for next time (key stored locally in your home folder)
        self.cfg.update({
            "model": self.model_var.get(),
            "api_key": self.key_var.get().strip(),
            "output_dir": self.out_var.get().strip(),
        })
        save_config(self.cfg)

        self.output.delete("1.0", "end")
        self.go_btn.config(state="disabled")
        self.open_btn.config(state="disabled")
        self.progress.start(12)

        self.worker = threading.Thread(target=self._run_pipeline, daemon=True)
        self.worker.start()

    def _run_pipeline(self):
        try:
            summary, t_path, s_path = notecore.process_recording(
                input_path=Path(self.file_var.get().strip()),
                output_dir=Path(self.out_var.get().strip()),
                model_size=self.model_var.get(),
                api_key=self.key_var.get().strip() or None,
                progress=lambda s: self.msg_queue.put(("status", s)),
            )
            self.last_output_dir = s_path.parent
            self.msg_queue.put(("done", (summary, t_path, s_path)))
        except Exception as e:  # surface any failure to the user
            self.msg_queue.put(("error", str(e)))

    # ── Queue pump (thread -> UI) ───────────────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "status":
                    self.status_var.set(payload)
                elif kind == "done":
                    summary, t_path, s_path = payload
                    self.progress.stop()
                    self.status_var.set(f"Done — saved to {s_path.parent}")
                    self.output.insert("end", summary)
                    self.go_btn.config(state="normal")
                    self.open_btn.config(state="normal")
                elif kind == "error":
                    self.progress.stop()
                    self.status_var.set("Failed.")
                    self.go_btn.config(state="normal")
                    messagebox.showerror(APP_NAME, payload)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _open_output(self):
        folder = self.last_output_dir or Path(self.out_var.get().strip())
        if not folder.exists():
            return
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(folder)])
        else:
            subprocess.run(["xdg-open", str(folder)])


def main():
    root = tk.Tk()
    # Use a native-ish theme where available
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    NoteTakerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
