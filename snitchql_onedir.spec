# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for SnitchQL - ONE-FOLDER variant.

Use this for a debuggable folder build:
  pyinstaller snitchql_onedir.spec
  -> dist\\SnitchQL\\SnitchQL.exe + the DLLs/Qt files next to it.

The default single-file build lives in snitchql.spec (the dbsys-like exe).
Validated against PyInstaller 6.x.
"""
import os

ROOT = os.path.dirname(os.path.abspath(SPEC))
entry = os.path.join(ROOT, "run_gui.py")

a = Analysis(
    [entry],
    pathex=[ROOT],
    binaries=[],
    # Bundle the in-app window icon (assets/snitchql_app.ico) into the build
    # so gui.py can load it at runtime via QIcon. Exe icon is set on EXE() below.
    datas=[("assets/snitchql_app.ico", "assets")],
    hiddenimports=[
        "snitchql",
        "snitchql.reader",
        "snitchql.export",
        "snitchql.query",
        "snitchql.gui",
        "snitchql.blob",
        "snitchql.index",
        "snitchql.tablemodel",
        "snitchql.sql",
        "PyQt6.QtWidgets",
        "PyQt6.QtGui",
        "PyQt6.QtCore",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "construct", "rich", "pandas", "openpyxl", "pyodbc",
        "numpy", "PIL", "matplotlib", "PyQt6.QtNetwork",
        "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name="SnitchQL",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,         # windowed build: no terminal/console window on launch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "snitchql_exe.ico"),  # custom SnitchQL exe icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SnitchQL",
)
