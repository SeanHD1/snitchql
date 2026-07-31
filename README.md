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
the venv and `pip install pyinstaller`. Output: `dist\SnitchQL\SnitchQL.exe`.
Copy the whole `dist\SnitchQL` folder to any Windows PC; no Python required.

### Cross-compile note
A true Linux -> Windows build is possible via MinGW-w64 + a portable Windows
Python, but it's brittle for PyQt6. Building on Windows is strongly recommended.

## Data location
On launch SnitchQL auto-loads the `All Dats` folder if it sits next to the exe.
Otherwise use **Set Data Dir…** to point at your live DBISAM files.

## Status
- Reader, GUI, filter builder, compare, schema, blobs, dark mode: working.
- Index (`.idx`) verify: CONSISTENT on v4 integer indexes; other index
  versions report UNKNOWN honestly (no false corruption verdicts). Rebuild
  not yet implemented.

Pure Python + PyQt6. No external DBISAM libraries.
