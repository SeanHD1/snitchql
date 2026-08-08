# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for SnitchQL (Windows .exe builder).

DEFAULT BUILD = single-file exe (like Elevate's dbsys.exe): one SnitchQL.exe
you double-click, no companion DLLs. PyInstaller unpacks to a temp dir at
launch (1-2s first-run delay), which is normal for PyQt6 apps.

Build on Windows:  pyinstaller snitchql.spec
  -> dist\SnitchQL.exe   (single file)

For a one-folder build instead (handy for debugging), use:
  pyinstaller snitchql_onedir.spec
  -> dist\SnitchQL\SnitchQL.exe + DLLs

Console is left ON so a double-click that errors shows output instead of
silently dying. Set console=False for a silent release build.
Validated against PyInstaller 6.x.
"""
import os

# Resolve paths relative to this spec file (repo root).
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
        # Not used at runtime — keep the bundle lean.
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
    a.binaries,
    a.datas,
    [],
    name="SnitchQL",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # windowed build: no terminal/console window on launch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "snitchql_exe.ico"),  # custom SnitchQL exe icon
)
