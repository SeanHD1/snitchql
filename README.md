# SnitchQL

A pure-Python, Windows desktop viewer for **DBISAM** databases (`.dat` / `.idx`
/ `.blb`). Built as a lightweight alternative to Elevate's `dbsys` — no ODBC
driver, no $529 license, just open the files.

## Features
- **Dual-pane viewer** — open up to two `.dat` tables side by side (toggle
  Layout: Dual/Single). Max 2 panes by design.
- **Compare** — toggles a row-diff highlight between the two panes. Shared rows
  are dimmed (soft grey, not eye-searing white); rows unique to one table get a
  warm (left) / cool (right) tint. Off resets to the default grid styling.
- **Schema viewer** — per-pane "Schema" button lists each field's index, name,
  type, **Len/Width** (effective byte width; fixed-width types show the native
  size, e.g. Integer=4), and row offset.
- **Filter builder** — per-pane field/operator/value rules with AND/OR, plus a
  quick substring search across all columns.
- **Blob viewer** — per-pane "Blob" button parses the sibling `<stem>.blb` and
  lists its memo/blob records (text shown inline, binary as a hex preview).
- **Dark mode** — toggle in the toolbar.
- **Export** — current pane to CSV or JSON.

## Running from source (Windows or Linux)
```
pip install -r requirements.txt
python -m snitchql.gui        # or: python run_gui.py
```

## Building the Windows .exe
The build must run **on Windows** (PyInstaller doesn't reliably cross-compile
Linux -> Windows). See `build_exe.bat` — it's a one-click build after you create
the venv and `pip install pyinstaller`. Output: `dist\SnitchQL.exe` (single file).

### Deploying to a client machine (zero scripts)
The single-file exe is self-contained (Python + PyQt6 bundled). The one
external dependency is the **Microsoft Visual C++ 2019+ Redistributable**, which
bare Windows Servers often lack. PyQt6/Qt needs `VCRUNTIME140.dll` etc.

To ship dependency-free:
1. On your **build** machine, install the VC++ 2019 redistributable once so
   PyInstaller bundles the runtime DLLs into the exe.
2. (Optional but recommended) drop `vc_redist.x64.exe` next to `build_exe.bat`;
   the build copies it beside the exe. The client double-clicks it once
   (no scripts, no shell commands) if their server doesn't have the runtime.
3. Copy `SnitchQL.exe` (and `vc_redist.x64.exe` if bundled) to the client.
   **Copy it to a local drive, not a `\\server\share` UNC path** — one-file
   exes extract to a temp dir and can fail over UNC. (The build already passes
   `--runtime-tmpdir .` so it extracts beside itself when run locally.)

If the exe won't start on the client, the console window (kept on by design)
will show the missing-DLL error so you know it's the VC++ runtime.

### Cross-compile note
A true Linux -> Windows build is possible via MinGW-w64 + a portable Windows
Python, but it's brittle for PyQt6. Building on Windows is strongly recommended.

## Data location
On first launch SnitchQL opens with **no preset data directory** — use
**Set Data Dir…** to pick your DBISAM folder. The choice is remembered in
`snitchql.ini` next to the exe, so it persists across restarts. If an
`All Dats` folder sits next to the exe, it auto-loads that as a convenience.

## Status
- Reader, GUI, filter builder, compare, schema, blobs, dark mode: working.
- Time (type 10) and Date/Timestamp columns decoded to HH:MM:SS / dates.
- Edit mode: tick "✎ Edit Mode" to edit String cells; edits stage and require a
  confirm dialog before writing. Each write is guarded (aborts if the on-disk
  value doesn't match) and a `.dat.bak` backup is made first. v1 edits String
  columns only — numeric/BLOB/AutoInc are read-only until the row-offset model
  for those types is cross-verified against pydbisam. Test on a COPY first.
- Index (`.idx`) verify: CONSISTENT on v4 integer indexes; other index
  versions report UNKNOWN honestly (no false corruption verdicts). Rebuild
  not yet implemented.
- Compare feature: functional but pending UX refinement.

Pure Python + PyQt6. No external DBISAM libraries.
