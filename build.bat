@echo off
REM ============================================================
REM  Build "Meeting Note Taker" into a standalone Windows .exe
REM  Run this on a Windows machine you control (needs internet).
REM  Produces: dist\MeetingNoteTaker.exe
REM ============================================================

echo.
echo === Meeting Note Taker - build script ===
echo.

REM 1. Make sure Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not on PATH.
    echo Install it from https://python.org  (tick "Add python.exe to PATH")
    pause
    exit /b 1
)

REM 2. Install the app's own dependencies + the packager
echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

REM 3. Build the single-file executable
echo.
echo Building the executable (this takes a few minutes)...
python -m PyInstaller app.spec --noconfirm

echo.
if exist "dist\MeetingNoteTaker.exe" (
    echo === DONE ===
    echo Your app is here:  dist\MeetingNoteTaker.exe
    echo.
    echo IMPORTANT: put an ffmpeg.exe next to the .exe, or in the same folder,
    echo for it to read video files. See BUILD.md.
) else (
    echo [ERROR] Build did not produce the executable. Scroll up for errors.
)
echo.
pause
