@echo off
REM ============================================================
REM  Meeting Note Taker - first-time setup
REM  Double-click this ONCE to install everything the app needs.
REM ============================================================
title Meeting Note Taker - Setup
cd /d "%~dp0"

echo.
echo   Installing Meeting Note Taker dependencies...
echo   (this can take a few minutes the first time)
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo   [X] Python is not installed or not on PATH.
    echo       Install Python 3.9+ from https://www.python.org/downloads/
    echo       and tick "Add Python to PATH" during setup, then run this again.
    echo.
    pause
    exit /b 1
)

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   [X] Something went wrong installing dependencies.
    echo       Check the messages above.
    pause
    exit /b 1
)

echo.
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo   [!] ffmpeg was not found. Screen video recording needs it.
    echo       Install it with:  winget install ffmpeg
    echo       (Audio + transcript + summary work without it.)
) else (
    echo   [OK] ffmpeg found.
)

echo.
echo   Setup complete. You can now double-click "Meeting Note Taker.bat" to start.
echo.
echo   Optional: set your Anthropic API key so summaries generate, e.g.
echo       setx ANTHROPIC_API_KEY "sk-ant-..."
echo   (open a new window after setx for it to take effect)
echo.
pause
