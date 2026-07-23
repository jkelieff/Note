#!/bin/bash
# Meeting Note Taker - build a standalone macOS .app.
# Double-click to produce dist/MeetingNoteTaker.app (no Python needed to run it).
cd "$(dirname "$0")" || exit 1

echo "Building the standalone Meeting Note Taker app..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt pyinstaller || { echo "Install failed"; exit 1; }
python3 -m PyInstaller --noconfirm desktop_app.spec || { echo "Build failed"; exit 1; }

echo ""
echo "Done! Your app is at: $(pwd)/dist/MeetingNoteTaker.app"
echo "Drag it to your Applications folder or Dock to keep it."
