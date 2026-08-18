# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Meeting Note Taker.
Build with:  python -m PyInstaller app.spec --noconfirm
Produces a single-file windowed executable: dist/MeetingNoteTaker.exe
"""

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    # faster-whisper / ctranslate2 pull in pieces PyInstaller can miss:
    hiddenimports=[
        'faster_whisper',
        'ctranslate2',
        'tokenizers',
        'onnxruntime',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='MeetingNoteTaker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # windowed app, no console
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='icon.ico',      # add your own icon here if you like
)
