#!/bin/bash
# Meeting Note Taker - macOS launcher.
# Double-click to start. First time: run  pip install -r requirements.txt
cd "$(dirname "$0")" || exit 1
exec python3 desktop_app.py
