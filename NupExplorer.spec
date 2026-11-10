# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build: python -m PyInstaller --noconfirm --clean NupExplorer.spec
from PyInstaller.utils.hooks import collect_data_files

datas = [('web', 'web')]
datas += collect_data_files('_soundfile_data')  # bundled libsndfile (Opus support)

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['texture2ddecoder', 'compression.zstd', '_zstd', 'soundfile', 'werkzeug.serving'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'UnityPy', 'matplotlib', 'IPython', 'scipy', 'pandas',
              'pytest', 'zstandard'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NupExplorer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='icon.ico',
)
