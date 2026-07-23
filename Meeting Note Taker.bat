@echo off
REM ============================================================
REM  Meeting Note Taker - launcher
REM  Double-click to start the app (no terminal window stays open).
REM  Run "Install (first time).bat" once before using this.
REM ============================================================
cd /d "%~dp0"

REM pythonw runs without a console window.
where pythonw >nul 2>nul
if errorlevel 1 (
    REM Fall back to python if pythonw isn't available.
    start "" python "%~dp0desktop_app.py"
) else (
    start "" pythonw "%~dp0desktop_app.py"
)
