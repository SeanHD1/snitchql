@echo off
REM ============================================================================
REM  SnitchQL - Windows .exe builder  (run this ON YOUR WINDOWS MACHINE)
REM ============================================================================
REM  Prereqs (one time):
REM    1. Install Python 3.11+ for Windows from python.org  (tick "Add to PATH")
REM    2. Open a terminal in this folder and run:
REM         python -m venv venv
REM         venv\Scripts\activate
REM         pip install -r requirements.txt
REM         pip install pyinstaller
REM    3. Then double-click THIS file (build_exe.bat)
REM
REM  DEFAULT = single-file exe (like Elevate's dbsys.exe):
REM         dist\SnitchQL.exe   -- one file, double-click and go.
REM
REM  FOLDER build (debuggable, with DLLs next to it):
REM         build_exe.bat --onedir
REM         -> dist\SnitchQL\SnitchQL.exe + DLLs
REM
REM  After building, the script copies the result next to an "All Dats" (or
REM  "Data") folder if one is found in the current directory, so the auto-load
REM  behaviour matches the source app. Otherwise it's left in dist\.
REM ============================================================================

setlocal
if not exist "venv\Scripts\activate.bat" (
    echo [!] No venv found. Run these first:
    echo       python -m venv venv
    echo       venv\Scripts\activate
    echo       pip install -r requirements.txt
    echo       pip install pyinstaller
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

set SPEC=snitchql.spec
if /I "%~1"=="--onedir" set SPEC=snitchql_onedir.spec

echo [*] Building SnitchQL (spec: %SPEC%) ...
REM --runtime-tmpdir . => the one-file exe extracts next to itself (a local
REM path) instead of a temp dir. This avoids failures when the exe is launched
REM from a network/UNC share (\\server\share) on a client machine.
pyinstaller %SPEC% --noconfirm --clean --runtime-tmpdir .
if errorlevel 1 (
    echo [!] Build failed - see output above.
    pause
    exit /b 1
)

REM --- place the exe next to a data folder so auto-load works (dbsys-like) ---
REM PyInstaller may emit either dist\SnitchQL.exe (onefile) or
REM dist\SnitchQL\SnitchQL.exe (onedir). Locate it robustly.
set "OUT_EXE="
for /r "dist" %%F in (SnitchQL.exe) do (
    if exist "%%F" set "OUT_EXE=%%F"
)
REM Fallback: if PyInstaller reported success but we didn't find it, list dist.
if not defined OUT_EXE (
    if exist "dist" (
        echo [!] Build reported success but exe not found at expected path.
        echo     Contents of dist\:
        dir /b "dist" 2>nul
    ) else (
        echo [!] Build produced no exe - see output above.
    )
    pause
    exit /b 1
)

REM --- bundle the VC++ redistributable if you dropped it next to the build ---
REM PyQt6 (Qt) requires the Microsoft Visual C++ 2019+ Runtime. PyInstaller
REM usually bundles it when present on the BUILD machine, but a bare Windows
REM Server often lacks it. Ship vc_redist.x64.exe alongside the exe so the
REM client just double-clicks it once (no scripts, no admin scripts needed).
if exist "vc_redist.x64.exe" (
    copy /Y "vc_redist.x64.exe" "%OUT_EXE%.vc_redist.x64.exe" >nul
    echo [OK] Bundled vc_redist.x64.exe next to the exe (client runs it once if needed).
)

if exist "All Dats" (
    copy /Y "%OUT_EXE%" "All Dats\SnitchQL.exe" >nul
    echo [OK] Copied exe -> All Dats\SnitchQL.exe  (auto-loads this folder)
) else if exist "Data" (
    copy /Y "%OUT_EXE%" "Data\SnitchQL.exe" >nul
    echo [OK] Copied exe -> Data\SnitchQL.exe  (auto-loads this folder)
) else (
    echo [OK] Built: %OUT_EXE%
    echo      (No "All Dats"/"Data" folder found here, so it won't auto-load.
    echo       Use "Set Data Dir..." in the app, or drop the exe next to your data.)
)
pause
