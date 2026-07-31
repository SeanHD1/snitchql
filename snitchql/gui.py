#!/usr/bin/env python3
"""SnitchQL GUI - PyQt6 dual-pane DBISAM table viewer with compare.

Features (M2):
  * Open up to two .dat files side-by-side (max 2, per spec).
  * Each pane: schema-aware table view, row count, filter box.
  * "Compare" toggles a row-diff highlight between the two panes
    (key-based: highlights rows present in one but not the other on the
    first column treated as key; visual only).
  * Export current pane to CSV/JSON.
Desktop-only target (Windows). Pure PyQt6 + stdlib.

Run:  python -m snitchql.gui   (auto-loads the All Dats dir if present)
"""
import os
import sys
from pathlib import Path
from PyQt6.QtCore import QDir
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QTableWidget, QTableWidgetItem, QLineEdit,
    QLabel, QComboBox, QMessageBox, QHeaderView, QSplitter, QMenu,
    QInputDialog, QFrame, QDialog, QListWidget, QTextEdit,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QAction

from snitchql import query as query_mod
from snitchql.reader import EDITABLE_TYPES


def _app_dir() -> Path:
    """Directory of the running executable / script.

    For a PyInstaller one-file build the app unpacks to a temp dir at runtime,
    so ``sys.executable`` points at the temp copy, not the real on-disk exe.
    On Windows we ask the OS for the true module path via GetModuleFileNameW;
    elsewhere we use the script/executable path. This is what "All Dats next
    to the exe" should resolve against.
    """
    if getattr(sys, "frozen", False):
        if sys.platform.startswith("win"):
            try:
                import ctypes
                buf = ctypes.create_unicode_buffer(1024)
                ctypes.windll.kernel32.GetModuleFileNameW(0, buf, 1024)
                return Path(buf.value).parent
            except Exception:
                return Path(sys.executable).parent
        return Path(sys.executable).parent
    # Running from source: use this file's real location.
    return Path(__file__).resolve().parent


# --- persisted config (remembers the user's chosen data dir) ---
def _config_path() -> Path:
    """snitchql.ini next to the real exe (works frozen + from source)."""
    if getattr(sys, "frozen", False):
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            ctypes.windll.kernel32.GetModuleFileNameW(0, buf, 1024)
            return Path(buf.value).parent / "snitchql.ini"
        except Exception:
            return Path(sys.executable).parent / "snitchql.ini"
    return Path(__file__).resolve().parent / "snitchql.ini"


def _load_data_dir() -> str:
    p = _config_path()
    if p.is_file():
        d = p.read_text().strip()
        if d and Path(d).is_dir():
            return d
    return ""


def _save_data_dir(d: str):
    try:
        _config_path().write_text(d)
    except Exception:
        pass


# Auto-load the "All Dats" folder if it sits next to the executable, otherwise
# fall back to the executable's own directory. A previously chosen data dir
# (via "Set Data Dir...") is remembered in snitchql.ini and takes priority.
_APP_DIR = _app_dir()
_remembered = _load_data_dir()
if _remembered:
    DEFAULT_DIR = _remembered
elif (_APP_DIR / "All Dats").is_dir():
    DEFAULT_DIR = str(_APP_DIR / "All Dats")
else:
    DEFAULT_DIR = str(_APP_DIR)

# Light-mode QSS. Soft light-green alternating rows (no eye-searing blue), and a
# gentle tint for the grid. Dark mode (DARK_QSS) overrides this when toggled on.
LIGHT_QSS = """
QTableWidget { alternate-background-color: #e8f3ec; gridline-color: #d0d0d0; }
QHeaderView::section { background-color: #eef2f0; color: #222; }
"""

# Dark-mode QSS. Applied via app.setStyleSheet; empty string restores default
# (light) styling. Kept self-contained so there is no external theme dependency.
DARK_QSS = """
QWidget { background-color: #2b2b2b; color: #e0e0e0; }
QMainWindow, QDialog { background-color: #2b2b2b; }
QPushButton { background-color: #3a3a3a; color: #e0e0e0; border: 1px solid #555; padding: 4px 8px; }
QPushButton:checked { background-color: #4a6fa5; color: #ffffff; }
QLineEdit, QComboBox, QTableWidget { background-color: #1f1f1f; color: #e0e0e0; gridline-color: #444; }
QHeaderView::section { background-color: #3a3a3a; color: #e0e0e0; }
QTableWidget { alternate-background-color: #333333; }
QMenu { background-color: #2b2b2b; color: #e0e0e0; }
QLabel { color: #e0e0e0; }
QSplitter::handle { background-color: #444; }
"""


class FilterRuleRow(QFrame):
    """One (Field, Operator, Value) rule row inside a FilterBuilder."""

    def __init__(self, fields, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)

        self.field = QComboBox()
        self.field.addItems(fields)
        self.field.setMinimumWidth(120)

        self.op = QComboBox()
        for op in query_mod.OPERATORS:
            self.op.addItem(query_mod.OPERATOR_LABELS[op], op)

        self.value = QLineEdit()
        self.value.setPlaceholderText("value")

        self.remove = QPushButton("✕")
        self.remove.setFixedWidth(28)

        h.addWidget(self.field)
        h.addWidget(self.op)
        h.addWidget(self.value, 1)
        h.addWidget(self.remove)

    def rule(self):
        return (self.field.currentText(), self.op.currentData(), self.value.text())


class FilterBuilder(QWidget):
    """Structured filter UI: list of (Field, Operator, Value) rules + AND/OR."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fields = []
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        top = QHBoxLayout()
        top.addWidget(QLabel("Filter builder"))
        top.addStretch(1)
        top.addWidget(QLabel("Combine:"))
        self.combine = QComboBox()
        self.combine.addItem("AND", "AND")
        self.combine.addItem("OR", "OR")
        self.combine.setFixedWidth(70)
        top.addWidget(self.combine)
        self.add_btn = QPushButton("＋ Add condition")
        top.addWidget(self.add_btn)
        self.apply_btn = QPushButton("Apply")
        self.clear_btn = QPushButton("Clear")
        top.addWidget(self.apply_btn)
        top.addWidget(self.clear_btn)
        v.addLayout(top)

        self.rules_layout = QVBoxLayout()
        self.rules_layout.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self.rules_layout)
        self.rule_rows = []

        self.add_btn.clicked.connect(self.add_rule)
        self.clear_btn.clicked.connect(self.clear_rules)

    def set_fields(self, fields):
        self.fields = list(fields)
        # rebuild any existing rows with the new field list
        for row in self.rule_rows:
            cur = row.field.currentText()
            row.field.clear()
            row.field.addItems(self.fields)
            if cur in self.fields:
                row.field.setCurrentText(cur)
        if not self.rule_rows:
            self.add_rule()  # start with one empty rule

    def add_rule(self):
        row = FilterRuleRow(self.fields)
        row.remove.clicked.connect(lambda: self.remove_rule(row))
        self.rules_layout.addWidget(row)
        self.rule_rows.append(row)

    def remove_rule(self, row):
        if row in self.rule_rows:
            self.rule_rows.remove(row)
        row.deleteLater()
        if not self.rule_rows:
            self.add_rule()

    def clear_rules(self):
        for row in list(self.rule_rows):
            self.remove_rule(row)

    def collect(self):
        """Return (rules, combine) where rules is normalized valid tuples."""
        raw = [r.rule() for r in self.rule_rows]
        return query_mod.normalize_rules(raw), self.combine.currentData()


class Pane(QWidget):
    """One table viewer pane."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.table = None          # snitchql Table
        self.path = None
        self.layout = QVBoxLayout(self)

        # toolbar
        bar = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight: bold;")
        self.open_btn = QPushButton("Open…")
        self.export_csv = QPushButton("CSV")
        self.export_json = QPushButton("JSON")
        self.schema_btn = QPushButton("Schema")
        self.blob_btn = QPushButton("Blob")
        self.schema_lbl = QLabel("")
        bar.addWidget(self.title)
        bar.addStretch(1)
        bar.addWidget(self.schema_lbl)
        bar.addWidget(self.open_btn)
        bar.addWidget(self.schema_btn)
        bar.addWidget(self.blob_btn)
        bar.addWidget(self.export_csv)
        bar.addWidget(self.export_json)
        self.layout.addLayout(bar)

        # filter
        fbar = QHBoxLayout()
        fbar.addWidget(QLabel("Quick:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("substring across all columns")
        fbar.addWidget(self.filter_edit)
        self.rows_lbl = QLabel("")
        fbar.addWidget(self.rows_lbl)
        self.layout.addLayout(fbar)

        # structured filter builder
        self.builder = FilterBuilder()
        self.builder.apply_btn.clicked.connect(self.apply_filters)
        self.layout.addWidget(self.builder)

        # table
        self.grid = QTableWidget()
        self.grid.setAlternatingRowColors(True)
        self.grid.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self.grid.setSortingEnabled(True)
        self.layout.addWidget(self.grid)

        self.open_btn.clicked.connect(self.on_open)
        self.export_csv.clicked.connect(lambda: self.on_export("csv"))
        self.export_json.clicked.connect(lambda: self.on_export("json"))
        self.schema_btn.clicked.connect(self.show_schema)
        self.blob_btn.clicked.connect(self.show_blobs)
        self.filter_edit.textChanged.connect(self.apply_filters)
        self.grid.cellChanged.connect(self._on_cell_changed)

        self._all_rows = []   # full decoded rows
        self._display_rows = []
        self._edit_mode = False
        # staged edits: dict[(row_index, col_name)] = original_value
        self._staged = {}

    # ---- schema viewer ----
    def show_schema(self):
        """Open a modal listing the table's field definitions."""
        if self.table is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Schema — {Path(self.path or 'table').name}")
        dlg.resize(520, 420)
        v = QVBoxLayout(dlg)
        cols = self.table.columns
        tbl = QTableWidget()
        tbl.setColumnCount(5)
        tbl.setHorizontalHeaderLabels(["#", "Field", "Type", "Len/Width", "Offset"])
        tbl.setRowCount(len(cols))
        tbl.verticalHeader().setVisible(False)
        tbl.setAlternatingRowColors(True)
        for ri, c in enumerate(cols):
            type_name = getattr(c, "type_name", None) or f"id{c.type_id}"
            # width = effective byte width (native size for fixed types,
            # declared length for strings). Show raw length too if it differs,
            # since DBISAM stores 0 there for fixed-width fields.
            w = c.width
            len_disp = str(w) if w == c.length else f"{w} (raw {c.length})"
            vals = [str(c.index), c.name, str(type_name), len_disp,
                    str(getattr(c, "row_offset", ""))]
            for ci, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                tbl.setItem(ri, ci, item)
        tbl.resizeColumnsToContents()
        tbl.horizontalHeader().setStretchLastSection(True)
        v.addWidget(tbl)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        v.addWidget(close)
        dlg.exec()

    # ---- blob viewer ----
    def show_blobs(self):
        """Open a dialog listing blob records from the sibling .blb file.

        DBISAM stores memo/blob fields in a companion <stem>.blb. We look for a
        .blb next to the currently-open .dat; if found, parse and show its
        records (text inline, binary as hex preview + size).
        """
        if not self.path:
            QMessageBox.information(self, "Blobs", "Open a .dat first.")
            return
        blb = Path(self.path).with_suffix(".blb")
        if not blb.exists():
            QMessageBox.information(self, "Blobs", f"No sibling blob file:\n{blb.name}")
            return
        from snitchql import blob as blob_mod
        try:
            info = blob_mod.read_blobs(str(blb))
        except Exception as e:
            QMessageBox.critical(self, "Blobs", f"Failed to read {blb.name}:\n{e}")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Blobs — {blb.name}")
        dlg.resize(720, 520)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(
            f"version {info['version']} · block size {info['block_size']} · "
            f"{len(info['records'])} records (scanned {info['scanned']:,} bytes)"))
        list_w = QListWidget()
        for i, rec in enumerate(info["records"]):
            if rec["text"] is not None:
                preview = rec["text"].replace("\r", " ").replace("\n", " ")[:120]
                label = f"#{i} [flag {rec['flag']}] text: {preview}"
            else:
                label = f"#{i} [flag {rec['flag']}] binary: {len(rec['data'])} bytes " \
                        f"{rec['data'][:8].hex()}"
            list_w.addItem(label)
        v.addWidget(list_w)
        # detail view for selected record
        detail = QTextEdit()
        detail.setReadOnly(True)
        v.addWidget(detail)

        def on_sel(item):
            idx = list_w.row(item)
            rec = info["records"][idx]
            if rec["text"] is not None:
                detail.setPlainText(rec["text"])
            else:
                detail.setPlainText(
                    f"Binary blob, {len(rec['data'])} bytes\n\nHex preview:\n"
                    + rec["data"][:256].hex(" "))

        list_w.currentItemChanged.connect(
            lambda cur, _prev: on_sel(cur) if cur else None)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        v.addWidget(close)
        dlg.exec()

    # ---- loading ----
    def on_open(self):
        from snitchql.reader import read_table
        path, _ = QFileDialog.getOpenFileName(
            self, "Open DBISAM .dat", DEFAULT_DIR, "DBISAM (*.dat)")
        if not path:
            return
        try:
            t = read_table(path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read {path}:\n{e}")
            return
        self.path = path
        self.table = t
        self.title.setText(f"{Path(path).name}")
        self.schema_lbl.setText(f"{len(t.columns)} cols · {len(t.rows)} rows")
        self._all_rows = t.rows
        self.builder.set_fields([c.name for c in t.columns])
        self.populate(self._all_rows)

    def populate(self, rows):
        t = self.table
        if t is None:
            return
        cols = t.columns
        self.grid.blockSignals(True)  # don't fire cellChanged while we build rows
        self.grid.setColumnCount(len(cols))
        self.grid.setHorizontalHeaderLabels([c.name for c in cols])
        self.grid.setRowCount(len(rows))
        for ri, row in enumerate(rows):
            for ci, col in enumerate(cols):
                val = row.get(col.name)
                item = QTableWidgetItem("" if val is None else str(val))
                if self._edit_mode and col.type_id in EDITABLE_TYPES:
                    # String columns only in v1 (byte-verified against pydbisam).
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                # mark non-editable columns subtly when in edit mode
                if self._edit_mode and col.type_id not in EDITABLE_TYPES:
                    item.setForeground(QColor(150, 150, 150))
                self.grid.setItem(ri, ci, item)
        self.grid.resizeColumnsToContents()
        self.grid.blockSignals(False)
        self.rows_lbl.setText(f"{len(rows)} shown")
        self._display_rows = rows

    def set_edit_mode(self, on: bool):
        """Toggle edit mode: enable/disable String-cell editing and reset staging."""
        self._edit_mode = on
        if not on:
            self._staged.clear()
        # Re-apply editability to currently displayed rows without losing data.
        self.populate(self._display_rows)

    def _on_cell_changed(self, ri, ci):
        if not self._edit_mode:
            return
        if self.table is None:
            return
        col = self.table.columns[ci]
        if col.type_id not in EDITABLE_TYPES:
            return
        item = self.grid.item(ri, ci)
        if item is None:
            return
        new_val = item.text()
        # Map displayed row -> source row index in self.table.rows. The displayed
        # rows are the SAME dict objects as in self.table.rows (live view), so an
        # identity lookup gives the correct logical index (survives filtering).
        try:
            row_index = self.table.rows.index(self._display_rows[ri])
        except (ValueError, IndexError):
            return
        original = self.table.rows[row_index].get(col.name)
        if new_val == ("" if original is None else str(original)):
            # reverted to original -> unstage (and restore stored value)
            self._staged.pop((row_index, col.name), None)
        else:
            # stage: remember original, and update the in-memory row so Save
            # writes the edited value.
            self._staged[(row_index, col.name)] = original
            self.table.rows[row_index][col.name] = new_val
        self._update_save_button()

    def _update_save_button(self):
        n = len(self._staged)
        mw = self.window()
        if hasattr(mw, "save_btn"):
            mw.save_btn.setText(f"Save Changes ({n})")
            mw.save_btn.setEnabled(n > 0)

    def _silent_reopen(self):
        """Re-read the current file from disk and repopulate (post-write refresh)."""
        if not self.path:
            return
        from snitchql.reader import read_table
        try:
            t = read_table(self.path)
        except Exception:
            return
        self.table = t
        self._all_rows = t.rows
        self.builder.set_fields([c.name for c in t.columns])
        self.populate(t.rows)

    def apply_filters(self):
        if self.table is None:
            return
        rows = self._all_rows
        # structured filter builder (AND/OR)
        rules, combine = self.builder.collect()
        if rules:
            rows = query_mod.apply_filters(rows, rules, combine=combine)
        # quick substring on top (AND semantics)
        q = self.filter_edit.text().strip().lower()
        if q:
            out = []
            for r in rows:
                hay = " ".join("" if v is None else str(v) for v in r.values()).lower()
                if q in hay:
                    out.append(r)
            rows = out
        self.populate(rows)

    def on_export(self, kind):
        if self.table is None:
            return
        from snitchql import export as exp
        ext = "csv" if kind == "csv" else "json"
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {kind.upper()}", f"{Path(self.path or 'table').stem}.{ext}",
            f"{kind.upper()} (*.{ext})")
        if not path:
            return
        rows = self._display_rows or self._all_rows
        with open(path, "w", newline="", encoding="utf-8") as f:
            if kind == "csv":
                exp.export_csv(self.table, f, rows)
            else:
                exp.export_json(self.table, f, rows)
        QMessageBox.information(self, "Exported", f"Wrote {len(rows)} rows to {path}")

    # ---- compare support ----
    def key_set(self):
        """Set of first-column values for quick row-diff."""
        if not self._display_rows or self.table is None:
            return set()
        key = self.table.columns[0].name
        return {r.get(key) for r in self._display_rows}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SnitchQL — DBISAM Explorer")
        self.resize(1400, 800)
        # Start in light mode with the soft-green alternating rows.
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(LIGHT_QSS)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # top toolbar
        top = QHBoxLayout()
        self.compare_btn = QPushButton("Compare ▶")
        self.compare_btn.setCheckable(True)
        self.compare_btn.toggled.connect(self.on_compare)
        self.layout_btn = QPushButton("Layout: Dual ▦")
        self.layout_btn.setCheckable(True)
        self.layout_btn.setChecked(True)
        self.layout_btn.toggled.connect(self.on_layout)
        self.dark_btn = QPushButton("🌙 Dark")
        self.dark_btn.setCheckable(True)
        self.dark_btn.toggled.connect(self.on_dark)
        self.dir_btn = QPushButton("Set Data Dir…")
        self.dir_btn.clicked.connect(self.on_set_dir)
        self.edit_btn = QPushButton("✎ Edit Mode")
        self.edit_btn.setCheckable(True)
        self.edit_btn.toggled.connect(self.on_edit_mode)
        self.save_btn = QPushButton("Save Changes (0)")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.on_save_changes)
        top.addWidget(QLabel("SnitchQL"))
        top.addStretch(1)
        top.addWidget(self.dir_btn)
        top.addWidget(self.edit_btn)
        top.addWidget(self.save_btn)
        top.addWidget(self.layout_btn)
        top.addWidget(self.dark_btn)
        top.addWidget(self.compare_btn)
        root.addLayout(top)

        # splitter with two panes
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.pane_a = Pane("Table A")
        self.pane_b = Pane("Table B")
        self.splitter.addWidget(self.pane_a)
        self.splitter.addWidget(self.pane_b)
        root.addWidget(self.splitter, 1)

        # try to auto-open two interesting tables
        self._maybe_autoload()

    def _maybe_autoload(self):
        # Prefer data-bearing tables so a first run shows real content, not
        # empty schema-only files. Reading every .dat can be slow (some are
        # 100k+ rows), so we bound the scan: sample the first N candidates and
        # pick the two best-populated tables that are small enough to render
        # snappily. The user can always Open… any other file.
        from snitchql.reader import read_table
        candidates = sorted(Path(DEFAULT_DIR).glob("*.dat"))[:200]
        scored = []  # (rowcount, path)
        for c in candidates:
            try:
                t = read_table(str(c))
            except Exception:
                continue
            if len(t.rows) <= 0 or len(t.rows) > 50000:
                continue  # skip empty and very large tables for the demo panes
            scored.append((len(t.rows), str(c)))
        scored.sort(reverse=True)
        picked = []
        for n, p in scored:
            if p not in picked:
                picked.append(p)
            if len(picked) == 2:
                break
        if len(picked) >= 1:
            self._silent_open(self.pane_a, picked[0])
        if len(picked) >= 2:
            self._silent_open(self.pane_b, picked[1])

    def _silent_open(self, pane, path):
        from snitchql.reader import read_table
        try:
            t = read_table(path)
        except Exception:
            return
        pane.path = path
        pane.table = t
        pane.title.setText(Path(path).name)
        pane.schema_lbl.setText(f"{len(t.columns)} cols · {len(t.rows)} rows")
        pane._all_rows = t.rows
        pane.builder.set_fields([c.name for c in t.columns])
        pane.populate(t.rows)

    def on_set_dir(self):
        # No preset default: start at "This PC" so the user must consciously
        # choose. The chosen directory is remembered in snitchql.ini.
        d = QFileDialog.getExistingDirectory(
            self, "Select data directory", QDir.rootPath())
        if d:
            global DEFAULT_DIR
            DEFAULT_DIR = d
            _save_data_dir(d)

    def on_edit_mode(self, on):
        """Enable/disable string-cell editing in both panes. Off by default."""
        self.edit_btn.setText("✎ Edit Mode ●" if on else "✎ Edit Mode")
        for pane in (self.pane_a, self.pane_b):
            pane.set_edit_mode(on)

    def on_save_changes(self):
        """Collect staged edits from both panes, confirm, then write with backup.

        Safety: each file is backed up to <name>.dat.bak before any write; every
        cell write is guarded by its current on-disk value (write_cell aborts if
        the row mapping is off). Writes only String columns (v1, byte-verified).
        """
        from snitchql.reader import write_cell
        staged = []  # (pane, row_index, col, original)
        total = 0
        for pane in (self.pane_a, self.pane_b):
            for (ri, cname), orig in pane._staged.items():
                col = next(c for c in pane.table.columns if c.name == cname)
                new_val = pane.table.rows[ri].get(cname)
                staged.append((pane, ri, col, orig))
                total += 1
        if not staged:
            return
        # Build a readable summary
        lines = []
        for pane, ri, col, orig in staged:
            new_val = pane.table.rows[ri].get(col.name)
            lines.append(f"  • {Path(pane.path).name}  row {ri+1}, {col.name}:\n"
                         f"      {orig!r}  →  {new_val!r}")
        msg = (f"About to write {total} change(s) to live .dat file(s).\n\n"
               + "\n".join(lines)
               + "\n\nA backup (.bak) will be made first. This cannot be undone "
                 "from inside SnitchQL. Continue?")
        ans = QMessageBox.question(self, "Confirm write to disk", msg,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        written = 0
        errors = []
        for pane, ri, col, orig in staged:
            try:
                # backup once per file
                bak = Path(pane.path).with_suffix(".dat.bak")
                if not bak.exists():
                    bak.write_bytes(Path(pane.path).read_bytes())
                new_val = pane.table.rows[ri].get(col.name)
                write_cell(pane.path, pane.table, ri, col, new_val,
                           expected_current=orig)
                written += 1
            except Exception as e:
                errors.append(f"{col.name} row {ri+1}: {e}")
        if errors:
            QMessageBox.critical(self, "Write errors",
                                 "Some edits were NOT written:\n\n" + "\n".join(errors))
        else:
            QMessageBox.information(self, "Saved",
                                    f"Wrote {written} change(s). Backup saved as .bak.")
        # refresh both panes from disk
        for pane in (self.pane_a, self.pane_b):
            if pane.path:
                pane._silent_reopen()
        self.edit_btn.setChecked(False)
        self.on_edit_mode(False)

    def on_dark(self, on):
        """Toggle the dark-mode QSS. Off => light styling (soft green alternation)."""
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(DARK_QSS if on else LIGHT_QSS)
        self.dark_btn.setText("☀ Light" if on else "🌙 Dark")

    def on_layout(self, dual):
        """Toggle between dual-pane (max 2) and single-pane views."""
        if dual:
            self.pane_b.setVisible(True)
            self.splitter.addWidget(self.pane_a)
            self.splitter.addWidget(self.pane_b)
            self.layout_btn.setText("Layout: Dual ▦")
        else:
            self.pane_b.setVisible(False)
            self.layout_btn.setText("Layout: Single ▤")
        # Compare only makes sense with both panes visible
        if not dual and self.compare_btn.isChecked():
            self.compare_btn.setChecked(False)

    def on_compare(self, checked):
        # Compare needs both panes present; force dual layout on
        if checked and not self.pane_b.isVisible():
            self.layout_btn.setChecked(True)
            self.on_layout(True)
        if checked:
            ka = self.pane_a.key_set()
            kb = self.pane_b.key_set()
            # rows whose key is in the OTHER table get "blocked out" (dimmed);
            # rows unique to this table get a soft tint so they stand out
            # without the eye-searing white-on-white of the old approach.
            self._apply_compare(self.pane_a, kb, present_color=QColor(255, 224, 178),
                                block_color=QColor(225, 225, 230))
            self._apply_compare(self.pane_b, ka, present_color=QColor(178, 223, 255),
                                block_color=QColor(225, 225, 230))
            self.compare_btn.setText("Compare ■ (on)")
        else:
            self._clear_compare(self.pane_a)
            self._clear_compare(self.pane_b)
            self.compare_btn.setText("Compare ▶")

    def _apply_compare(self, pane, other_keys, present_color, block_color):
        """Dim shared rows (block_color) and tint rows unique to this pane.

        'Blocked out' = de-emphasised, NOT white: shared rows are faded so the
        eye skips them, while unique rows keep a soft tint for contrast.
        """
        grid = pane.grid
        for ri in range(grid.rowCount()):
            key_item = grid.item(ri, 0)
            if key_item is None:
                continue
            present = key_item.text() in other_keys
            color = block_color if present else present_color
            for ci in range(grid.columnCount()):
                it = grid.item(ri, ci)
                if it is not None:
                    it.setBackground(color)

    def _clear_compare(self, pane):
        """Restore the grid's default row-shading instead of forcing solid white.

        Using a null/default brush (not QColor(255,255,255)) lets Qt's
        alternating-row styling come back — and avoids leaving a "white stain"
        that survives subsequent repopulate() calls.
        """
        from PyQt6.QtGui import QBrush
        grid = pane.grid
        for ri in range(grid.rowCount()):
            for ci in range(grid.columnCount()):
                it = grid.item(ri, ci)
                if it is not None:
                    it.setBackground(QBrush())  # reset to default (alternating)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # clean cross-platform look
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
