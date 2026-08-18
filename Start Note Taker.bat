@echo off
REM Double-click launcher for Windows.
REM First run installs the Python components automatically; after that it just starts.

cd /d "%~dp0"

echo ===============================================
echo    Meeting Note Taker
echo ===============================================
echo.

REM 1. Check Python
python --version >nul 2>&1
if errorlevel 1 (
  echo Python is not installed or not on PATH.
  echo Install it from https://python.org  ^(tick "Add python.exe to PATH"^)
  echo.
  pause
  exit /b 1
)

REM 2. First-run install
python -c "import flask, faster_whisper" >nul 2>&1
if errorlevel 1 (
  echo First-time setup: installing components ^(a few minutes^)...
  python -m pip install -r requirements.txt
  echo.
)

REM 3. Reminders about external pieces
where ffmpeg >nul 2>&1 || echo NOTE: ffmpeg not found - install with: winget install ffmpeg
curl -s http://localhost:11434/api/tags >nul 2>&1 || echo NOTE: Ollama not running - install from https://ollama.com and run: ollama pull llama3.1

echo.
echo Starting the dashboard... your browser will open shortly.
echo Keep this window open while you use it. Close it to stop.
echo.
python server.py
