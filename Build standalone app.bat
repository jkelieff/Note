@echo off
REM ============================================================
REM  Meeting Note Taker - build a standalone .exe
REM  Double-click to produce a single clickable app that does NOT
REM  need Python installed. Output: dist\MeetingNoteTaker.exe
REM ============================================================
title Meeting Note Taker - Build
cd /d "%~dp0"

echo.
echo   Building the standalone Meeting Note Taker app...
echo   (first build downloads PyInstaller and can take several minutes)
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo   [X] Python is not installed or not on PATH.
    echo       Install Python 3.9+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo   [X] Failed to install build dependencies.
    pause
    exit /b 1
)

python -m PyInstaller --noconfirm desktop_app.spec
if errorlevel 1 (
    echo.
    echo   [X] Build failed. See the messages above.
    pause
    exit /b 1
)

echo.
echo   ============================================================
echo   Done! Your app is here:
echo       %~dp0dist\MeetingNoteTaker.exe
echo.
echo   Double-click that .exe to run it, or right-click it to pin
echo   it to your taskbar / Start menu.
echo   ============================================================
echo.
pause
