#!/usr/bin/env python3
"""
Meeting Note Taker — local web dashboard.

Runs a small web server on your own machine and opens a dashboard in your
browser. Two ways to make notes:

  1. Upload a recording you already have (any video/audio file).
  2. Record a live meeting straight from the browser — it captures your
     screen's system audio (tick "Share system audio"), so it works with the
     Teams desktop app without any extra audio setup.

Everything runs locally. Transcription is free (Whisper). Summaries use a free
local model (Ollama) by default.

Run with:   python server.py
Then open:  http://localhost:5000  (opens automatically)
"""

import os
import sys
import json
import uuid
import threading
import webbrowser
from pathlib import Path
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory

import notecore

# When frozen by PyInstaller, bundled data (the static/ folder) lives under
# sys._MEIPASS; as a plain script it sits next to this file.
if getattr(sys, "frozen", False):
    FROZEN = True
    APP_DIR = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    PORTABLE_DIR = Path(sys.executable).parent  # the app's own folder (e.g. the USB)
else:
    FROZEN = False
    APP_DIR = Path(__file__).resolve().parent
    PORTABLE_DIR = APP_DIR

# Portable mode (the packaged app): keep EVERYTHING next to the app on the USB —
# settings, saved notes, and transient upload files — so nothing is written to
# the host laptop. As a plain script, use the user's home folder as before.
if FROZEN:
    CONFIG_PATH = PORTABLE_DIR / "settings.json"
    DEFAULT_OUTPUT = PORTABLE_DIR / "MeetingNotes"
    UPLOAD_DIR = PORTABLE_DIR / "_temp_uploads"
else:
    CONFIG_PATH = Path.home() / ".meeting_note_taker.json"
    DEFAULT_OUTPUT = Path.home() / "Documents" / "MeetingNotes"
    UPLOAD_DIR = Path(notecore.tempfile.gettempdir()) / "note_uploads"

try:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # If the app's folder is read-only, fall back to the system temp dir.
    UPLOAD_DIR = Path(notecore.tempfile.gettempdir()) / "note_uploads"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In portable mode, also route the transient audio-extraction file here (onto
# the USB) instead of the laptop's system temp folder.
if FROZEN:
    notecore.tempfile.tempdir = str(UPLOAD_DIR)

app = Flask(__name__, static_folder=str(APP_DIR / "static"))

# In-memory job registry: job_id -> {status, steps[], result, error}
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


# ── Config persistence ───────────────────────────────────────────────────────

def load_config() -> dict:
    # When a model is bundled next to the app, prefer the fully-offline backend.
    default_backend = "local" if notecore.bundled_model_available() else "ollama"
    defaults = {
        "model": "base",
        "backend": default_backend,
        "summary_model": "llama3.1",
        "api_key": "",
        "output_dir": str(DEFAULT_OUTPUT),
    }
    try:
        defaults.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return defaults


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


# ── Background processing ────────────────────────────────────────────────────

def run_job(job_id: str, input_path: Path, cfg: dict):
    def progress(msg: str):
        with JOBS_LOCK:
            JOBS[job_id]["steps"].append(msg)
            JOBS[job_id]["status"] = "running"

    try:
        summary, t_path, s_path = notecore.process_recording(
            input_path=input_path,
            output_dir=Path(cfg["output_dir"]),
            model_size=cfg["model"],
            backend=cfg["backend"],
            api_key=cfg.get("api_key") or None,
            summary_model=cfg.get("summary_model"),
            progress=progress,
        )
        with JOBS_LOCK:
            JOBS[job_id].update({
                "status": "done",
                "result": {
                    "summary": summary,
                    "transcript": t_path.read_text(encoding="utf-8"),
                    "summary_path": str(s_path),
                    "transcript_path": str(t_path),
                },
            })
    except Exception as e:
        with JOBS_LOCK:
            JOBS[job_id].update({"status": "error", "error": str(e)})
    finally:
        try:
            input_path.unlink()
        except OSError:
            pass


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(APP_DIR / "static"), "index.html")


@app.route("/app.js")
def appjs():
    return send_from_directory(str(APP_DIR / "static"), "app.js")


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        cfg = load_config()
        cfg.update(request.get_json(force=True))
        save_config(cfg)
        return jsonify({"ok": True})
    # Don't leak the key back to the page beyond whether one is set
    cfg = load_config()
    safe = dict(cfg)
    safe["has_api_key"] = bool(cfg.get("api_key"))
    safe["api_key"] = ""
    return jsonify(safe)


@app.route("/api/health")
def api_health():
    has_bundled = notecore.bundled_model_available()
    # Only probe for Ollama when there's no bundled model — the probe blocks
    # briefly when nothing is listening, and it's irrelevant if AI is bundled.
    ollama = False if has_bundled else notecore.ollama_available()
    return jsonify({
        "ffmpeg": notecore.find_ffmpeg() is not None,
        "ollama": ollama,
        "bundled_ai": has_bundled,
    })


@app.route("/api/process", methods=["POST"])
def api_process():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename."}), 400

    # Save upload to a temp file, preserving extension
    suffix = Path(f.filename).suffix or ".webm"
    saved = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    f.save(str(saved))

    cfg = load_config()
    # Allow per-request overrides from the form
    for key in ("model", "backend", "summary_model", "output_dir"):
        if key in request.form:
            cfg[key] = request.form[key]
    if "api_key" in request.form and request.form["api_key"]:
        cfg["api_key"] = request.form["api_key"]
    save_config(cfg)

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "queued", "steps": [], "result": None, "error": None}
    threading.Thread(target=run_job, args=(job_id, saved, cfg), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/job/<job_id>")
def api_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Unknown job."}), 404
        return jsonify(job)


def open_browser():
    webbrowser.open("http://localhost:5000")


if __name__ == "__main__":
    print("\n  Meeting Note Taker dashboard")
    print("  Open your browser at: http://localhost:5000")
    print("  (Press Ctrl+C here to stop.)\n")
    if os.environ.get("NOTE_NO_BROWSER") != "1":
        threading.Timer(1.2, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
