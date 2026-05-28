# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


ROOT = Path.cwd()
ASSETS = ROOT / "assets"
DOCS = ROOT / "docs"
ICONS = ROOT / "build" / "icons"

icon = None
if sys.platform == "win32" and (ICONS / "app.ico").exists():
    icon = str(ICONS / "app.ico")
elif sys.platform == "darwin" and (ICONS / "app.icns").exists():
    icon = str(ICONS / "app.icns")


a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ASSETS), "assets"), (str(DOCS), "docs")],
    hiddenimports=[],
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
    [],
    exclude_binaries=True,
    name="aichs",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="aichs",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="aichs.app",
        icon=icon,
        bundle_identifier="studio.aichs.desktop",
    )
