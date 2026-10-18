# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build configuration for Study Tracker Desktop (Part M).
#
# One-folder, windowed (no console) build — per the plan's v1 guidance.
# Assets are bundled into an "assets" folder next to the executable;
# app/utils/paths.get_resource_dir() resolves them in frozen mode.
#
# Build on Windows:  pyinstaller --noconfirm study_tracker.spec
#              or:   .\run_build.ps1

import os
import tempfile

block_cipher = None

# The user's artwork is PNG (any size). Windows .exe icons must be .ico,
# so derive one from assets/app_icon.png at build time via Pillow.
_app_icon_png = os.path.join('assets', 'app_icon.png')
_icon_path = None
if os.path.isfile(_app_icon_png):
    try:
        from PIL import Image

        _ico_tmp = os.path.join(tempfile.gettempdir(), 'studytracker_app.ico')
        with Image.open(_app_icon_png) as _img:
            _img.load()
            if _img.mode != 'RGBA':
                _img = _img.convert('RGBA')
            _img.save(_ico_tmp, format='ICO',
                      sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        _icon_path = _ico_tmp
    except Exception:
        _icon_path = None  # best-effort: build still succeeds without an exe icon

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=[
        'customtkinter',
        'platformdirs',
        # Pillow <-> Tkinter bridge. PyInstaller's analysis misses these lazy
        # imports, which crashes iconphoto() in the frozen build.
        'PIL.ImageTk',
        'PIL._imagingtk',
        'PIL._tkinter_finder',
        'PIL.ImageDraw',
        'PIL.ImageFont',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'setuptools'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='StudyTracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # GUI app: no console window (Part M rule)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon_path,           # derived from user PNG artwork (None = default icon)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='StudyTracker',
)
