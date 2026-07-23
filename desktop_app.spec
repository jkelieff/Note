# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Meeting Note Taker desktop app.

Produces a single double-clickable executable:
  * Windows -> dist/MeetingNoteTaker.exe
  * macOS   -> dist/MeetingNoteTaker.app
  * Linux   -> dist/MeetingNoteTaker

Build it with:
    pip install pyinstaller
    pyinstaller desktop_app.spec

(On Windows just double-click `Build standalone app.bat`, which does both.)
"""

import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Pull in data files, binaries, and hidden imports for the heavier packages
# that PyInstaller can't fully trace on its own (whisper stack, audio, SDK).
datas, binaries, hiddenimports = [], [], []
for pkg in (
    "faster_whisper",
    "ctranslate2",
    "av",
    "tokenizers",
    "onnxruntime",
    "huggingface_hub",
    "sounddevice",
    "soundfile",
    "anthropic",
    "screeninfo",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        # Package not installed / not collectable — skip; the app still builds.
        pass

# Bundle ffmpeg if a copy sits next to this spec (optional convenience so the
# built app doesn't need ffmpeg separately installed). Drop ffmpeg.exe /
# ffmpeg here before building to include it.
for candidate in ("ffmpeg.exe", "ffmpeg"):
    if os.path.exists(candidate):
        binaries.append((candidate, "."))
        break

# Optional app icon — provide app.ico (Windows) or app.icns (macOS) to use it.
icon_file = None
for candidate in ("app.ico", "app.icns"):
    if os.path.exists(candidate):
        icon_file = candidate
        break

a = Analysis(
    ["desktop_app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MeetingNoteTaker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # windowed app — no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

# macOS: wrap the executable in a proper .app bundle.
app = BUNDLE(
    exe,
    name="MeetingNoteTaker.app",
    icon=icon_file,
    bundle_identifier="com.notetaker.meeting",
    info_plist={
        "NSMicrophoneUsageDescription":
            "Meeting Note Taker records meeting audio to transcribe it.",
        "NSScreenCaptureUsageDescription":
            "Meeting Note Taker records the meeting screen.",
        "LSUIElement": False,
    },
)
