# Changelog

All notable changes to SnitchQL are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
loose semantic-ish versioning (the `0.x` line means "use at your own risk,
test on a copy first").

## [Unreleased]

### Performance — P0 hangs fixed (the big one)

The GUI froze ("not responding") on three operations because every heavy step
ran synchronously on the GUI thread and the grid materialized *every* cell as a
`QTableWidgetItem`. For a 236k-row / 64-column table that was ~12s of blocked
decode plus ~30s of blocked cell-building — repeated on open, on Edit Mode
toggle, and again on post-save reload.

- Replaced the `QTableWidget` with a virtual `QTableView` backed by
  `RowTableModel` (a `QAbstractTableModel`). Qt only ever asks for the cells
  currently visible, so a 465k-row table renders in milliseconds instead of
  ~30s. (#P0 open / #P0 edit mode)
- File reads now run on a background `QThread` (`ReaderThread`); the GUI thread
  stays free, so the window never shows "not responding". (#P0 open)
- The post-save refresh re-reads in the same background thread, eliminating the
  third hang. (#P0 after save)
- Quick filter moved into `RowProxyModel` (a `QSortFilterProxyModel`) and fed a
  precomputed lower-cased search haystack built during the background read, so
  filtering 236k rows is near-instant. (#P1 quick filter)
- Auto-load no longer decodes every `.dat` just to pick demo panes: it uses the
  new `read_table_meta` header-only scan (sub-millisecond per file).

Verified headless against the real GUI (12/12 checks): virtual paint 11 ms vs
30,500 ms before; Quick filter 6 ms over 236k rows; Edit Mode toggle 10 ms; save
persists to disk + non-blocking reload (~0.6 s); stale-load supersede guard
holds; `.bak` backup created.

### Added (round 2)

- **Scalar column editing + date picker.** All scalar types are now editable,
  not just String: Date, Time, Timestamp, Boolean, ShortInt, Integer, Double,
  Currency, AutoInc. A typed cell editor (`TypedCellDelegate`) picks the right
  widget per column: a calendar **date picker** (QDateEdit) for Date, a datetime
  picker for Timestamp, a time editor for Time, a True/False combo for Boolean,
  and a validated text editor for numbers. Invalid input (e.g. "abc" in an
  Integer cell) is rejected with a warning instead of corrupting the row.
  (#P1 date picker / #P1 "cannot edit dates/ints/bools")

### Changed
- Each pane shows the **full directory path** beneath the `.dat` name (previously
  only the file name). (#P2 full path)
- Save Changes button count correctly resets to `(0)` and disables after a
  successful write. (#P2 save count reset)
- Build specs (`snitchql.spec`, `snitchql_onedir.spec`) now list
  `snitchql.tablemodel` in `hiddenimports` for deterministic bundling.

### Added

- `snitchql/tablemodel.py` — `RowTableModel`, `RowProxyModel`, `ReaderThread`.
- `snitchql/reader.py::read_table_meta` — cheap header-only scan for the
  auto-load picker.

### Notes / not yet done

These backlog items are still open: custom SQL (Single View), date picker +
non-String editing, "unexpected terminal window" build fix, dark-mode header
bar, column visibility selection, button-row alignment, blob header display,
and the P3 "all dats" / default-dir cleanup.

## [0.1.0] - initial

- Reader, GUI, filter builder, compare, schema, blobs, dark mode.
- Edit mode: String-only cell editing, staged, confirm dialog, `.bak` backup,
  on-disk value guard.
- Time/Date/Timestamp decode; index `.idx` verify (v4 integer consistent).
