# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path

hidden = collect_submodules("badupdateprep")
wiki = Path("badupdateprep") / "wiki"

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[(str(wiki), "badupdateprep/wiki")],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WlfRyt-BadUpdate-Prep",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
