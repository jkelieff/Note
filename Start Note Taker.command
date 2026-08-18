#!/bin/bash
# Double-click launcher for macOS.
# First run installs the Python components automatically; after that it just starts.
# (If double-clicking says "permission denied", run once in Terminal:
#   chmod +x "Start Note Taker.command"  )

cd "$(dirname "$0")"

echo "==============================================="
echo "   Meeting Note Taker"
echo "==============================================="
echo

# 1. Check Python is available
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed."
  echo "Install it from https://python.org (or run: brew install python)"
  echo
  read -n 1 -s -r -p "Press any key to close..."
  exit 1
fi

# 2. On first run, install the components
if ! python3 -c "import flask, faster_whisper" >/dev/null 2>&1; then
  echo "First-time setup: installing components (a few minutes)..."
  python3 -m pip install -r requirements.txt
  echo
fi

# 3. Friendly reminders about the two external pieces
command -v ffmpeg >/dev/null 2>&1 || \
  echo "NOTE: ffmpeg not found — install with: brew install ffmpeg"
curl -s http://localhost:11434/api/tags >/dev/null 2>&1 || \
  echo "NOTE: Ollama not running — install from https://ollama.com and run: ollama pull llama3.1"

echo
echo "Starting the dashboard... your browser will open shortly."
echo "Keep this window open while you use it. Close it (or press Ctrl+C) to stop."
echo
python3 server.py
