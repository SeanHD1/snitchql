#!/usr/bin/env python3
"""SnitchQL GUI - PyQt6 dual-pane DBISAM table viewer with compare.

Features (M2):
  * Open up to two .dat files side-by-side (max 2, per spec).
  * Each pane: schema-aware virtual table view (only visible cells are
    materialised, so 400k-row tables render instantly), filter box, filter
    builder, compare, schema, blob, export.
  * "Compare" toggles a row-diff highlight between the two panes
    (key-based: highlights rows present in one but not the other on the
    first column treated as key; visual only).
  * Export current pane to CSV/JSON.
Desktop-only target (Windows). Pure PyQt6 + stdlib.

Performance note (P0 fix):
  File reads happen on a background QThread (ReaderThread); the table itself
  is a virtual QAbstractTableModel (RowTableModel) fronted by a
  QSortFilterProxyModel (RowProxyModel). The GUI thread is therefore never
  blocked by either decoding a 500 MB file or building cell widgets, which
  eliminates the "not responding" hangs on open, Edit Mode, and post-save
  refresh.
"""
import os
import sys
from pathlib import Path
from PyQt6.QtCore import QDir, Qt, QSize, QModelIndex, QStandardPaths, QTimer
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QLineEdit, QLabel, QComboBox, QMessageBox,
    QHeaderView, QSplitter, QMenu, QInputDialog, QFrame, QDialog,
    QListWidget, QTextEdit, QTableView, QTableWidget, QTableWidgetItem,
    QListWidgetItem, QCheckBox, QCompleter,
)
from PyQt6.QtGui import QColor, QIcon, QTextCursor, QPalette

from snitchql import query as query_mod
from snitchql.reader import EDITABLE_TYPES
from snitchql.tablemodel import (
    RowTableModel, RowProxyModel, ReaderThread, TypedCellDelegate,
    _CMP_BLOCK, _CMP_PRESENT,
)
from snitchql.sql import run_query, SqlError


def _app_dir() -> Path:
    """Directory of the running executable / script.

    For a PyInstaller one-file build the app unpacks to a temp dir at runtime,
    so ``sys.executable`` points at the temp copy, not the real on-disk exe.
    On Windows we ask the OS for the true module path via GetModuleFileNameW;
    elsewhere we use the script/executable path. This is what the auto-load
    folder should resolve against.
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
    return Path(__file__).resolve().parent


# --- persisted config (remembers the user's chosen data dir + UI prefs) ---
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


def _load_bool(name: str, default: bool) -> bool:
    """Read a `name=1|0` line from the ini (used for dark / layout prefs)."""
    p = _config_path()
    if p.is_file():
        for line in p.read_text().splitlines():
            if line.strip().lower().startswith(f"{name}="):
                return line.split("=", 1)[1].strip() not in ("", "0", "false", "no")
    return default


def _save_bool(name: str, value: bool):
    p = _config_path()
    try:
        lines = p.read_text().splitlines() if p.is_file() else []
        kept = [l for l in lines if not l.strip().lower().startswith(f"{name}=")]
        kept.append(f"{name}={1 if value else 0}")
        p.write_text("\n".join(kept) + "\n")
    except Exception:
        pass


# Auto-load folder resolution. A previously chosen data dir (via "Set Data
# Dir...") is remembered in snitchql.ini and takes priority. Otherwise we
# default to the user's Desktop (closest to the old "All Dats" convention),
# falling back to the executable's own directory if the Desktop can't be found.
# (The old "All Dats" sub-folder magic has been removed — see P3 cleanup.)
_APP_DIR = _app_dir()
_remembered = _load_data_dir()
if _remembered:
    DEFAULT_DIR = _remembered
else:
    _desktop = ""
    try:
        _dp = QStandardPaths.standardLocations(QStandardPaths.StandardLocation.DesktopLocation)
        if _dp:
            _desktop = _dp[0]
    except Exception:
        _desktop = ""
    DEFAULT_DIR = _desktop if (_desktop and Path(_desktop).is_dir()) else str(_APP_DIR)

# ---------------------------------------------------------------------------
# Import helper: CSV / JSON / JSONL -> (column_names, list_of_row_dicts)
# ---------------------------------------------------------------------------
def _import_file(path: str):
    """Parse a CSV or JSON file into (columns, rows).

    CSV: first row = headers; every row becomes a dict keyed by header.
    JSON: either a list of objects (keys = columns) or a list of lists with an
    optional header row. JSONL (.jsonl / .ndjson): one JSON object per line.
    Returns (list[str], list[dict]). Raises ValueError on anything unrecognisable.
    """
    from pathlib import Path as _P
    suffix = _P(path).suffix.lower()
    if suffix == ".csv":
        import csv
        with open(path, "r", newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f)
            rows_raw = list(reader)
        if not rows_raw:
            return [], []
        header = [h.strip() or f"col{i+1}" for i, h in enumerate(rows_raw[0])]
        rows = []
        for r in rows_raw[1:]:
            d = {header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
            rows.append(d)
        return header, rows
    if suffix in (".json", ".jsonl", ".ndjson"):
        import json
        text = open(path, "r", encoding="utf-8-sig", errors="replace").read().strip()
        if not text:
            return [], []
        if suffix == ".jsonl":
            objs = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            data = json.loads(text)
            if isinstance(data, dict):
                # common export wrapper: {"columns": [...], "rows": [...]}
                if "rows" in data:
                    objs = data["rows"]
                    if "columns" in data and isinstance(data["columns"], list):
                        return [str(c) for c in data["columns"]], [dict(r) for r in objs]
                else:
                    objs = [data]
            elif isinstance(data, list):
                objs = data
            else:
                raise ValueError("unsupported JSON shape")
        if not objs:
            return [], []
        cols = []
        for o in objs:
            if isinstance(o, dict):
                for k in o.keys():
                    if k not in cols:
                        cols.append(k)
        rows = [dict(o) if isinstance(o, dict) else {f"col{i+1}": v for i, v in enumerate(o) if isinstance(o, (list, tuple))}
                for o in objs]
        return cols, rows
    raise ValueError(f"unsupported import type: {suffix}")


class _LazyLog:
    """File-like stdout/stderr sink that opens its log only on first write.

    Used in frozen windowed builds so a stray print()/traceback is captured
    without popping a console window — but the file is created lazily, so a
    clean run leaves no stray log next to the exe.
    """

    def __init__(self, path):
        self._path = path
        self._fh = None

    def write(self, s):
        # Only materialise the file when there is actual (non-whitespace) output.
        # Blank / whitespace-only writes (e.g. a stray newline from fmt) stay
        # silent so a clean run leaves no log behind.
        if self._fh is None and s and s.strip():
            try:
                self._fh = open(self._path, "a", encoding="utf-8", buffering=1)
            except Exception:
                return
        if self._fh is not None:
            self._fh.write(s)

    def flush(self):
        if self._fh is not None:
            self._fh.flush()

    def __getattr__(self, name):
        # delegate any other attribute (e.g. encoding) to the open file
        fh = self.__dict__.get("_fh")
        if fh is not None:
            return getattr(fh, name)
        raise AttributeError(name)


class SQLCompleterTextEdit(QTextEdit):
    """QTextEdit with IDE-style word autocompletion, for the SQL query box.

    QTextEdit has no native QCompleter support, so we drive a QCompleter
    manually:

      * As you TYPE (any alnum/``_`` run) the popup auto-filters to the word
        under the caret — like an editor, you never need a magic key.
      * Ctrl+Space forces the list open even on an empty word.
      * When the popup is open: Tab / Enter / Return accepts the highlighted
        completion, Up/Down moves the highlight, Esc dismisses it.
      * Tab with the popup CLOSED moves focus (setTabChangesFocus), so the
        query box behaves like a normal field instead of inserting a tab char.

    The vocabulary is curated to exactly what ``sql.py`` (the mini engine)
    actually parses, so a suggestion never produces a SqlError at run time.
    """

    def __init__(self, words, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)  # Tab = move focus when no popup
        self._comp = QCompleter(sorted(set(words)), self)
        self._comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._comp.setWidget(self)
        self._comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._comp.activated.connect(self._insert_completion)
        # The popup is a QListView that inherits the app-wide DARK_QSS, which has
        # no QAbstractItemView rule -> items render with no contrast and the list
        # can be effectively invisible (you could still Tab/Enter a hidden pick).
        # Two layers of defence so the option TEXT is always visible:
        #   1) A QSS that colors the items themselves (not just the view bg), and
        #   2) A hard QPalette fallback, because the dark app stylesheet can win
        #      over CSS `color` on the delegate in some Qt builds.
        popup = self._comp.popup()
        popup.setStyleSheet(
            "QListView {"
            "  background-color: #2b2b2b;"
            "  border: 1px solid #555;"
            "  selection-background-color: #4a6fa5;"
            "  selection-color: #ffffff;"
            "  show-decoration-selected: 1;"
            "}"
            "QListView::item { color: #e6e6e6; padding: 3px 6px; }"
            "QListView::item:selected {"
            "  background-color: #4a6fa5; color: #ffffff;"
            "}")
        # Palette fallback: set the item text/selection colors directly so they
        # survive any CSS precedence fight with the app-wide dark stylesheet.
        pal = popup.palette()
        LIGHT = QColor("#e6e6e6")   # normal item text (light grey on dark)
        SELBG = QColor("#4a6fa5")   # selected row bg (soft blue)
        SELFG = QColor("#ffffff")   # selected row text (white)
        pal.setColor(QPalette.ColorRole.Text, LIGHT)
        pal.setColor(QPalette.ColorRole.WindowText, LIGHT)
        pal.setColor(QPalette.ColorRole.Base, QColor("#2b2b2b"))
        pal.setColor(QPalette.ColorRole.Window, QColor("#2b2b2b"))
        pal.setColor(QPalette.ColorRole.Highlight, SELBG)
        pal.setColor(QPalette.ColorRole.HighlightedText, SELFG)
        popup.setPalette(pal)
        popup.setMinimumHeight(60)

    # -- word geometry -----------------------------------------------------
    def _current_word(self):
        """Return (word_before_cursor, start_offset) for the token under the caret."""
        cur = self.textCursor()
        pos = cur.position()
        text = self.toPlainText()
        start = pos
        while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
            start -= 1
        return text[start:pos], start

    def _insert_completion(self, completion):
        cur = self.textCursor()
        word, start = self._current_word()
        cur.setPosition(start)
        cur.setPosition(start + len(word), QTextCursor.MoveMode.KeepAnchor)
        cur.insertText(completion)
        self.setTextCursor(cur)

    # -- popup control -----------------------------------------------------
    def _update_completions(self):
        word, _ = self._current_word()
        if len(word) < 1:
            self._comp.popup().hide()
            return
        self._comp.setCompletionPrefix(word)
        if self._comp.completionCount() == 0:
            self._comp.popup().hide()
            return
        self._comp.complete(self.cursorRect())

    def _accept_current(self):
        popup = self._comp.popup()
        idx = popup.currentIndex()
        model = self._comp.completionModel()
        if not idx.isValid() and model.rowCount() > 0:
            idx = model.index(0, 0)
        if idx.isValid():
            self._insert_completion(model.data(idx))
        popup.hide()

    def _move_highlight(self, delta):
        popup = self._comp.popup()
        model = self._comp.completionModel()
        cur = popup.currentIndex()
        row = cur.row() + delta
        if 0 <= row < model.rowCount():
            popup.setCurrentIndex(model.index(row, 0))

    # -- key handling ------------------------------------------------------
    def keyPressEvent(self, e):
        popup = self._comp.popup()
        # 1) If the popup is open, intercept the navigation/accept keys so the
        #    QTextEdit never sees them (no stray newlines/tabs/characters).
        if popup.isVisible():
            if e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Enter, Qt.Key.Key_Return):
                self._accept_current()
                e.accept()
                return
            if e.key() == Qt.Key.Key_Escape:
                popup.hide()
                e.accept()
                return
            if e.key() == Qt.Key.Key_Down:
                self._move_highlight(1)
                e.accept()
                return
            if e.key() == Qt.Key.Key_Up:
                self._move_highlight(-1)
                e.accept()
                return

        # 2) Normal handling (typing, newline, backspace, focus traversal...).
        super().keyPressEvent(e)

        # 3) Re-evaluate the popup after the document changed.
        ctrl = e.modifiers() & Qt.KeyboardModifier.ControlModifier
        if ctrl and e.key() == Qt.Key.Key_Space:
            self._update_completions()
        elif (e.key() == Qt.Key.Key_Backspace) or (e.text() and not ctrl):
            self._update_completions()


# Light-mode QSS. Soft light-green alternating rows (no eye-searing blue), and a
# gentle tint for the grid. Dark mode (DARK_QSS) overrides this when toggled on.
LIGHT_QSS = """
QTableView { alternate-background-color: #e8f3ec; gridline-color: #d0d0d0; }
QHeaderView::section { background-color: #eef2f0; color: #222; }
"""

# Dark-mode QSS. Applied via app.setStyleSheet; empty string restores default
# (light) styling. Kept self-contained so there is no external theme dependency.
DARK_QSS = """
QWidget { background-color: #2b2b2b; color: #e0e0e0; }
QMainWindow, QDialog { background-color: #2b2b2b; }
QPushButton { background-color: #3a3a3a; color: #e0e0e0; border: 1px solid #555; padding: 4px 8px; }
QPushButton:checked { background-color: #4a6fa5; color: #ffffff; }
QPushButton:hover { background-color: #454545; }
QLineEdit, QComboBox, QTableView, QHeaderView { background-color: #1f1f1f; color: #e0e0e0; gridline-color: #444; alternate-background-color: #333333; }
QHeaderView::section {
    background-color: #3a3a3a;
    color: #e0e0e0;
    border: 1px solid #555;
    padding: 4px;
    qproperty-foreground: #e0e0e0;
}
QHeaderView::section:checked { background-color: #4a6fa5; }
QMenu { background-color: #2b2b2b; color: #e0e0e0; }
QLabel { color: #e0e0e0; }
QSplitter::handle { background-color: #444; }
QListWidget, QTextEdit { background-color: #1f1f1f; color: #e0e0e0; }
QCheckBox, QRadioButton { color: #e0e0e0; }
QStatusBar { background-color: #2b2b2b; color: #e0e0e0; }
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
    """One virtual-table viewer pane."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.table = None
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
        self.sql_btn = QPushButton("SQL")
        self.cols_btn = QPushButton("Columns")
        self.import_btn = QPushButton("Import")
        self.schema_lbl = QLabel("")
        self.path_lbl = QLabel("")           # full path under the db name
        self.path_lbl.setStyleSheet("color: #888; font-size: 10px;")
        bar.addWidget(self.title)
        bar.addStretch(1)
        bar.addWidget(self.schema_lbl)
        bar.addWidget(self.open_btn)
        bar.addWidget(self.schema_btn)
        bar.addWidget(self.blob_btn)
        bar.addWidget(self.sql_btn)
        bar.addWidget(self.cols_btn)
        bar.addWidget(self.import_btn)
        bar.addWidget(self.export_csv)
        bar.addWidget(self.export_json)
        self.layout.addLayout(bar)
        self.layout.addWidget(self.path_lbl)

        # filter
        fbar = QHBoxLayout()
        fbar.addWidget(QLabel("Quick Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("substring across indexed fields")
        fbar.addWidget(self.filter_edit)
        self.unindexed_chk = QCheckBox("Search unindexed")
        self.unindexed_chk.setToolTip(
            "Off: quick filter scans only indexed columns (fast). "
            "On: scans every column (slower).")
        self.unindexed_chk.stateChanged.connect(self._on_unindexed_toggled)
        fbar.addWidget(self.unindexed_chk)
        self.rows_lbl = QLabel("")
        fbar.addWidget(self.rows_lbl)
        self.layout.addLayout(fbar)

        # structured filter builder
        self.builder = FilterBuilder()
        self.builder.apply_btn.clicked.connect(self.apply_filters)
        # Clear must reset AND immediately re-apply the (now empty) filter state.
        self.builder.clear_btn.clicked.connect(self.apply_filters)
        self.layout.addWidget(self.builder)

        # virtual table
        self.grid = QTableView()
        self.grid.setAlternatingRowColors(True)
        self.grid.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self.grid.setSortingEnabled(True)
        self.grid.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows)
        self.layout.addWidget(self.grid)

        # model stack: RowTableModel -> RowProxyModel -> QTableView
        self.model = RowTableModel(self)
        self.proxy = RowProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.grid.setModel(self.proxy)
        # Typed per-column editors (date picker, bool combo, etc.). The delegate
        # reads the column type from the model at edit time, so it works for any
        # table loaded after this point.
        self._delegate = TypedCellDelegate(self.model)
        self.grid.setItemDelegate(self._delegate)
        self._visible_cols = None  # source column names currently shown (None = all)

        self.open_btn.clicked.connect(self.on_open)
        self.export_csv.clicked.connect(lambda: self.on_export("csv"))
        self.export_json.clicked.connect(lambda: self.on_export("json"))
        self.schema_btn.clicked.connect(self.show_schema)
        self.blob_btn.clicked.connect(self.show_blobs)
        self.sql_btn.clicked.connect(self.show_sql)
        self.cols_btn.clicked.connect(self.show_columns)
        self.import_btn.clicked.connect(self.on_import)
        self.filter_edit.textChanged.connect(self._on_quick_typed)

        self._all_rows = []   # full decoded rows (== self.table.rows)
        self._hays = []       # precomputed lowercase search haystack
        self._edit_mode = False
        self._loader = None   # active background ReaderThread

    # ---- async open ----
    def _open_async(self, path):
        """Kick off a background read. The GUI stays responsive the whole time."""
        # If a load is already in flight for this pane, stop it cleanly so we
        # don't leak a running QThread (and so it can't deliver stale rows).
        old = self._loader
        if old is not None:
            try:
                old.loaded.disconnect()
                old.failed.disconnect()
            except Exception:
                pass
            if old.isRunning():
                old.quit()
                old.wait(2000)
            old.deleteLater()
        self.title.setText(f"Loading… {Path(path).name}")
        self.schema_lbl.setText("")
        self.path_lbl.setText("")
        self.rows_lbl.setText("")
        self._loader = ReaderThread(path)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.failed.connect(self._on_load_failed)
        self._loader.start()

    def _on_loaded(self, table, hays):
        if self.sender() is not self._loader:
            return  # a newer load superseded this one
        self.path = self._loader.path
        self.table = table
        self._all_rows = table.rows
        self._hays = hays
        self.model.set_table(table, hays)
        self.proxy.setSourceModel(self.model)
        self.builder.set_fields([c.name for c in table.columns])
        self.title.setText(Path(self.path).name)
        self.path_lbl.setText(self.path)
        self.schema_lbl.setText(f"{len(table.columns)} cols · {table.total_rows} rows")
        self._indexed_cols = self._detect_indexed_columns()
        self.proxy.set_indexed_columns(self._indexed_cols)
        self._visible_cols = [c.name for c in table.columns]
        self.builder.set_fields(self._visible_cols)
        self._resize_columns_to_header()
        # re-apply any pending quick/structured filters
        self.apply_filters(silent=True)
        self.on_edit_staged(0)

    def _on_load_failed(self, msg):
        if self.sender() is not self._loader:
            return
        QMessageBox.critical(self, "Error",
                             f"Failed to read {self._loader.path}:\n{msg}")
        self.title.setText("Table")

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
        """Open a dialog listing blob records from the sibling .blb file."""
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
            QMessageBox.critical(self, "Blob read error",
                                f"Could not read {blb.name}:\n{e}")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Blobs — {blb.name}")
        dlg.resize(720, 520)
        v = QVBoxLayout(dlg)
        header = QLabel(
            f"Blob file: {blb.name}   ·   version {info['version']}   ·   "
            f"block size {info['block_size']}   ·   {len(info['records'])} records "
            f"(scanned {info['scanned']:,} bytes)")
        header.setStyleSheet("color: #888; font-size: 11px;")
        header.setWordWrap(True)
        v.addWidget(header)
        if not info["records"]:
            v.addWidget(QLabel("No blob records found in this file."))
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

    # ---- custom SQL query (Single View only) ----
    def show_sql(self):
        """Open a SQL query dialog. Runs against the loaded pane (read-only).

        Per spec this is a Single View feature: if the app is in Dual layout the
        user is told to switch to Single first, since results occupy the whole
        pane.  The query runs over the already-decoded rows via the safe,
        non-executing parser in ``snitchql.sql`` — no eval, no filesystem.
        """
        mw = self.window()
        if mw is not None and hasattr(mw, "layout_btn") and mw.layout_btn.isChecked():
            QMessageBox.information(
                self, "Single View only",
                "Custom SQL runs in Single View. Turn off 'Layout: Dual' first "
                "(top toolbar) so the result can fill this pane.")
            return
        if self.table is None:
            QMessageBox.information(self, "SQL", "Open a .dat first.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"SQL Query — {Path(self.path or 'table').name}")
        dlg.resize(620, 360)
        v = QVBoxLayout(dlg)
        hint = QLabel("SELECT * FROM table  [WHERE …]  [ORDER BY col ASC|DESC]  [LIMIT n]\n"
                      "Ops: = != <> > < >= <=  LIKE   Logic: AND OR ( )")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(hint)
        # Autocomplete vocabulary: ONLY what the mini engine (sql.py) actually
        # parses, plus this table's live column names. Suggesting unsupported
        # keywords would let the user pick a word that then 500s at Run time.
        SQL_KEYWORDS = [
            "SELECT", "FROM", "WHERE", "AND", "OR",
            "ORDER", "BY", "ASC", "DESC", "LIMIT",
        ]
        ALIAS = ["table"]  # FROM <table> is the accepted alias; engine ignores it
        col_names = [c.name for c in self.table.columns] if self.table else []
        editor = SQLCompleterTextEdit(SQL_KEYWORDS + col_names + ALIAS, parent=dlg)
        editor.setPlainText("")
        editor.setAcceptRichText(False)
        v.addWidget(editor, 1)
        err_lbl = QLabel("")
        err_lbl.setStyleSheet("color: #c0392b;")
        v.addWidget(err_lbl)
        btn_row = QHBoxLayout()
        run_btn = QPushButton("Run")
        close_btn = QPushButton("Close")
        run_btn.setAutoDefault(False)      # don't hijack Enter/typing in the dialog
        close_btn.setAutoDefault(False)
        btn_row.addStretch(1)
        btn_row.addWidget(run_btn)
        btn_row.addWidget(close_btn)
        v.addLayout(btn_row)

        def on_run():
            sql = editor.toPlainText().strip()
            if not sql:
                return
            try:
                result = run_query(self.table.columns, self._all_rows, sql)
            except SqlError as e:
                err_lbl.setText(f"SQL error: {e}")
                return
            err_lbl.setText("")
            # load the result as a read-only table in this pane
            self.title.setText(f"SQL ▸ {Path(self.path or 'table').name}")
            self.schema_lbl.setText(
                f"{len(result.columns)} cols · {result.total_rows} rows")
            self.path_lbl.setText(self.path or "")
            self.model.set_table(result)
            self.model.set_readonly(True)   # query results have no .dat to write
            self.proxy.setSourceModel(self.model)
            self.builder.set_fields([c.name for c in result.columns])
            self.proxy.set_quick("")
            self.rows_lbl.setText(f"{self.proxy.rowCount()} shown")
            dlg.accept()

        run_btn.clicked.connect(on_run)
        close_btn.clicked.connect(dlg.reject)
        # Put keyboard focus on the editor as soon as the modal dialog is up, so
        # the first keystroke goes to the SQL box (not a button). Without this,
        # on a live session the QTextEdit can fail to grab focus and typing does
        # nothing — which made autocomplete appear dead.
        QTimer.singleShot(0, editor.setFocus)
        dlg.exec()

    # ---- column / field visibility ----
    def show_columns(self):
        """Open a checklist to show/hide columns in this pane's grid."""
        if self.table is None:
            QMessageBox.information(self, "Columns", "Open a .dat first.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Visible columns — {Path(self.path or 'table').name}")
        dlg.resize(360, 480)
        v = QVBoxLayout(dlg)
        hint = QLabel("Tick the columns to show. Hidden columns are not deleted, "
                      "just filtered from view.")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(hint)
        list_w = QListWidget()
        ncol = self.model.columnCount()
        # map source column index -> visible state (inverse of grid hidden)
        src_visible = []
        for c in range(ncol):
            item = QListWidgetItem(self.table.columns[c].name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # a source column is visible if the grid shows the matching proxy col
            visible = not self.grid.isColumnHidden(
                self.proxy.mapFromSource(self.model.index(0, c)).column())
            item.setCheckState(Qt.CheckState.Checked if visible
                               else Qt.CheckState.Unchecked)
            list_w.addItem(item)
            src_visible.append(visible)
        v.addWidget(list_w, 1)
        btn_row = QHBoxLayout()
        all_btn = QPushButton("All")
        none_btn = QPushButton("None")
        close_btn = QPushButton("Apply")
        btn_row.addStretch(1)
        btn_row.addWidget(all_btn)
        btn_row.addWidget(none_btn)
        btn_row.addWidget(close_btn)
        v.addLayout(btn_row)

        def apply():
            visible = []
            for c in range(list_w.count()):
                item = list_w.item(c)
                show = item.checkState() == Qt.CheckState.Checked
                # proxy column for this source column
                pcol = self.proxy.mapFromSource(self.model.index(0, c)).column()
                self.grid.setColumnHidden(pcol, not show)
                if show:
                    visible.append(self.table.columns[c].name)
            self._visible_cols = visible
            # refresh the filter builder so hidden fields leave the dropdown/export
            self.builder.set_fields(visible)
            dlg.accept()

        all_btn.clicked.connect(lambda: [list_w.item(i).setCheckState(
            Qt.CheckState.Checked) for i in range(list_w.count())])
        none_btn.clicked.connect(lambda: [list_w.item(i).setCheckState(
            Qt.CheckState.Unchecked) for i in range(list_w.count())])
        close_btn.clicked.connect(apply)
        dlg.exec()

    # ---- loading via dialog ----
    def on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open DBISAM table (.dat)", DEFAULT_DIR, "DBISAM tables (*.dat)")
        if not path:
            return
        self._open_async(path)

    def on_import(self):
        """Load a CSV or JSON file and display it as a read-only virtual table.

        Imports are functional in the sense Damion asked for: the data is parsed
        and shown in the pane's virtual grid (with Quick Filter, compare, column
        selection and export all working against it). The result has no backing
        .dat, so it is shown read-only — editing/ saving is disabled, exactly like
        a SQL query result. This avoids writing a DBISAM .dat from scratch (a
        separate, riskier milestone) while giving a real, usable import path.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Import table (CSV or JSON)", DEFAULT_DIR,
            "CSV/JSON (*.csv *.json *.jsonl)")
        if not path:
            return
        try:
            cols, rows = _import_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Import failed",
                                f"Could not read {Path(path).name}:\n{e}")
            return
        if not cols:
            QMessageBox.information(self, "Import", "No columns found in file.")
            return
        # Reuse the existing table-shaped structures so the virtual model works.
        from snitchql.reader import Column, Table
        table = Table(
            path=path,
            columns=[Column(index=i + 1, name=n, type_id=1, length=0, row_offset=0)
                     for i, n in enumerate(cols)],
            rows=rows,
            total_rows=len(rows),
        )
        hays = [" ".join("" if v is None else str(v) for v in r.values()).lower()
                for r in rows]
        self.path = path
        self.table = table
        self._all_rows = rows
        self._hays = hays
        self.model.set_table(table, hays)
        self.model.set_readonly(True)
        self.proxy.setSourceModel(self.model)
        self._visible_cols = list(cols)
        self.builder.set_fields(self._visible_cols)
        self.title.setText(f"Import ▸ {Path(path).name}")
        self.path_lbl.setText(path)
        self.schema_lbl.setText(f"{len(cols)} cols · {len(rows)} rows (imported)")
        self.unindexed_chk.setChecked(False)  # no indexes on imported data
        self._indexed_cols = []
        self.proxy.set_indexed_columns([])
        self._resize_columns_to_header()
        self.proxy.set_quick("")
        self.rows_lbl.setText(f"{self.proxy.rowCount()} shown")


    def apply_filters(self, silent=False):
        """Push quick + structured filters into the proxy.

        The proxy recomputes accepted rows lazily (only visible cells are
        touched), so this is instant even on 400k-row tables.

        The Quick filter defaults to *indexed-only* scanning for speed; the
        "Search unindexed" tick box flips it to a full-column scan.
        """
        if self.table is None:
            return
        rules, combine = self.builder.collect()
        self.proxy.set_rules(rules, combine=combine)
        # indexed-only == NOT searching unindexed
        indexed_only = not self.unindexed_chk.isChecked()
        self.proxy.set_quick(self.filter_edit.text(), indexed_only=indexed_only)
        self.rows_lbl.setText(f"{self.proxy.rowCount()} shown")
        if not silent:
            self._restore_scroll_top()

    def _on_quick_typed(self, _text):
        self.apply_filters(silent=True)

    def _restore_scroll_top(self):
        self.grid.scrollToTop()

    def _resize_columns_to_header(self):
        """Size each column so its header label (and typical content) isn't cut off.

        We avoid resizeColumnsToContents() (it would scan every visible cell and
        stall on 400k-row tables). Instead we set the header's minimum section
        size from the widest header label, then give each column a sensible width
        derived from its header text length. Users can still drag to adjust.
        """
        hdr = self.grid.horizontalHeader()
        hdr.setMinimumSectionSize(40)
        for c in range(self.model.columnCount()):
            name = self.model.headerData(c, Qt.Orientation.Horizontal,
                                         Qt.ItemDataRole.DisplayRole) or ""
            # ~7px per char + padding covers typical DBISAM column names.
            width = max(60, min(280, len(str(name)) * 8 + 16))
            self.grid.setColumnWidth(c, width)

    def _detect_indexed_columns(self):
        """Return the set of column names that have a DBISAM index.

        Scans sibling .idx files (same stem as the .dat) and reads each index
        definition's key fields, mapping field numbers back to column names. A
        single-field index points at exactly one column; composite indexes
        contribute every field they cover. Falls back to an empty set (which the
        proxy treats as "search everything") if no .idx is present or parsing
        fails — so a table with no indexes just behaves like the old full scan.
        """
        if not self.path:
            return []
        from snitchql import idx_writer
        cols_by_num = {c.index: c.name for c in self.table.columns}
        indexed = set()
        base = Path(self.path).with_suffix("")
        for idx_path in sorted(base.parent.glob(base.name + "*.idx")):
            try:
                defs = idx_writer.read_index_defs(idx_path)
            except Exception:
                continue
            for d in defs:
                for fn in d.field_nums[:d.field_count]:
                    name = cols_by_num.get(fn)
                    if name:
                        indexed.add(name)
        return sorted(indexed)

    def _on_unindexed_toggled(self, state):
        """User flipped the 'Search unindexed' box.

        Off (default): quick filter scans only indexed columns (fast). On: full
        scan, with a one-time warning that it's slower. Re-applies immediately.
        """
        on = bool(state)
        if on:
            # show the slowdown warning once per pane session
            if not getattr(self, "_warned_unindexed", False):
                QMessageBox.information(
                    self, "Slower search",
                    "Searching unindexed fields scans every column and is much "
                    "slower on large tables. Turn this off for everyday use.")
                self._warned_unindexed = True
        self.apply_filters(silent=True)


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
        # Export the *currently displayed* (filtered) rows, projected to only the
        # columns the user has made visible (hidden columns are excluded per spec).
        visible = self._visible_cols if self._visible_cols is not None else \
            [c.name for c in self.table.columns]
        rows = []
        for pr in range(self.proxy.rowCount()):
            src = self.proxy.mapToSource(self.proxy.index(pr, 0)).row()
            full = self._all_rows[src]
            rows.append({k: full.get(k) for k in visible})
        # Build a lightweight table-shaped view so export_csv/json emit only the
        # visible columns (and their headers).
        from snitchql.reader import Column
        vcols = [Column(index=i, name=n, type_id=next((c.type_id for c in self.table.columns if c.name == n), 1),
                       length=0, row_offset=0) for i, n in enumerate(visible)]
        vtable = type("T", (), {"columns": vcols, "rows": rows})()
        with open(path, "w", newline="", encoding="utf-8") as f:
            if kind == "csv":
                exp.export_csv(vtable, f, rows)
            else:
                exp.export_json(vtable, f, rows)
        QMessageBox.information(self, "Exported", f"Wrote {len(rows)} rows to {path}")

    # ---- edit mode ----
    def set_edit_mode(self, on: bool):
        self._edit_mode = on
        self.model.set_edit_mode(on)

    def on_edit_staged(self, n):
        """Called by the model's editStaged signal; update MainWindow button."""
        mw = self.window()
        if hasattr(mw, "_refresh_save_button"):
            mw._refresh_save_button()

    def displayed_key_set(self):
        """Set of first-column values over the currently displayed rows."""
        if not self.table or not self._all_rows:
            return set()
        key = self.table.columns[0].name
        keys = set()
        for pr in range(self.proxy.rowCount()):
            src = self.proxy.mapToSource(self.proxy.index(pr, 0)).row()
            keys.add(self._all_rows[src].get(key))
        return keys

    def compare_state_map(self, other_keys):
        """Per source-row compare state: block if key in other, else present."""
        if not self.table:
            return []
        key = self.table.columns[0].name
        return [
            _CMP_BLOCK if r.get(key) in other_keys else _CMP_PRESENT
            for r in self._all_rows
        ]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SnitchQL — DBISAM Explorer")
        self.resize(1400, 800)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(LIGHT_QSS)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # top toolbar
        top = QHBoxLayout()
        self.dir_btn = QPushButton("Set Data Dir…")
        self.dir_btn.clicked.connect(self.on_set_dir)
        self.edit_btn = QPushButton("✎ Edit Mode")
        self.edit_btn.setCheckable(True)
        self.edit_btn.toggled.connect(self.on_edit_mode)
        self.save_btn = QPushButton("Save Changes (0)")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.on_save_changes)
        self.layout_btn = QPushButton("Layout: Dual ▦")
        self.layout_btn.setCheckable(True)
        # Default view is Single (per backlog). Persisted via snitchql.ini.
        self._default_dual = _load_bool("layout_dual", False)
        self.layout_btn.setChecked(self._default_dual)
        self.layout_btn.toggled.connect(self.on_layout)
        self.dark_btn = QPushButton("🌙 Dark")
        self.dark_btn.setCheckable(True)
        # Default theme is Dark (per backlog). Persisted via snitchql.ini.
        self._default_dark = _load_bool("dark", True)
        self.dark_btn.setChecked(self._default_dark)
        self.dark_btn.toggled.connect(self.on_dark)
        self.compare_btn = QPushButton("Compare ▶")
        self.compare_btn.setCheckable(True)
        self.compare_btn.toggled.connect(self.on_compare)
        top.addWidget(QLabel("SnitchQL"))
        top.addSpacing(12)
        top.addWidget(self.dir_btn)
        top.addWidget(self.edit_btn)
        top.addWidget(self.save_btn)
        top.addWidget(self.layout_btn)
        top.addWidget(self.dark_btn)
        top.addWidget(self.compare_btn)
        top.addStretch(1)
        root.addLayout(top)

        # splitter with two panes
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.pane_a = Pane("Table A")
        self.pane_b = Pane("Table B")
        self.splitter.addWidget(self.pane_a)
        self.splitter.addWidget(self.pane_b)
        root.addWidget(self.splitter, 1)

        # keep Save button in sync with either pane's staging
        self.pane_a.model.editStaged.connect(
            lambda _: self._refresh_save_button())
        self.pane_b.model.editStaged.connect(
            lambda _: self._refresh_save_button())

        self._maybe_autoload()

    def _refresh_save_button(self):
        n = self.pane_a.model.staged_count() + self.pane_b.model.staged_count()
        self.save_btn.setText(f"Save Changes ({n})")
        self.save_btn.setEnabled(n > 0)

    def _maybe_autoload(self):
        # Cheap header-only scan (read_table_meta) so we never decode every
        # .dat just to pick the two best demo panes. We score by declared
        # total_rows and prefer mid-sized tables that render snappily.
        from snitchql.reader import read_table_meta
        try:
            candidates = sorted(Path(DEFAULT_DIR).glob("*.dat"))[:200]
        except Exception:
            return
        scored = []
        for c in candidates:
            try:
                meta = read_table_meta(str(c))
            except Exception:
                continue
            if meta["total_rows"] <= 0 or meta["total_rows"] > 50000:
                continue
            scored.append((meta["total_rows"], str(c)))
        scored.sort(reverse=True)
        picked = []
        for _, p in scored:
            if p not in picked:
                picked.append(p)
            if len(picked) == 2:
                break
        if len(picked) >= 1:
            self.pane_a._open_async(picked[0])
        if len(picked) >= 2:
            self.pane_b._open_async(picked[1])

        # Apply persisted UI defaults (Single view + Dark theme on first run).
        self.on_dark(self._default_dark)
        self.on_layout(self._default_dual)

    def on_set_dir(self):
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
        """Collect staged edits from both panes, confirm, then write with backup."""
        from snitchql.reader import write_cell
        staged = []
        for pane in (self.pane_a, self.pane_b):
            for (ri, cname), orig in pane.model.staged.items():
                col = next(c for c in pane.table.columns if c.name == cname)
                staged.append((pane, ri, col, orig))
        if not staged:
            return
        lines = []
        for pane, ri, col, orig in staged:
            new_val = pane.table.rows[ri].get(col.name)
            lines.append(f"  • {Path(pane.path).name}  row {ri+1}, {col.name}:\n"
                         f"      {orig!r}  →  {new_val!r}")
        msg = (f"About to write {len(staged)} change(s) to live .dat file(s).\n\n"
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
        # refresh both panes from disk (background read -> no hang)
        for pane in (self.pane_a, self.pane_b):
            if pane.path:
                pane._open_async(pane.path)
        self.edit_btn.setChecked(False)
        self.on_edit_mode(False)

    def on_dark(self, on):
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(DARK_QSS if on else LIGHT_QSS)
        self.dark_btn.setText("☀ Light" if on else "🌙 Dark")
        _save_bool("dark", on)
        # Keep the compare font-colour (which must equal the row background to
        # make "block" rows vanish) in sync with the active theme's grid colours.
        self._push_grid_backgrounds(on)

    def _push_grid_backgrounds(self, dark):
        """Push the current theme's *actual* grid backgrounds into every pane's model.

        Compare paints "block" text with a colour identical to the row background
        so those rows vanish. We read the real colours from the grid's resolved
        palette (Base + AlternateBase) rather than hardcoding QSS hexes — the QSS
        can be overridden/resolved differently, and an inexact match would leave a
        faint dim colour instead of true invisibility.
        """
        from PyQt6.QtGui import QPalette
        for pane in (self.pane_a, self.pane_b):
            if pane.model is None or pane.grid is None:
                continue
            pal = pane.grid.palette()
            base = pal.color(QPalette.ColorRole.Base)
            alt = pal.color(QPalette.ColorRole.AlternateBase)
            pane.model.set_grid_backgrounds(base, alt)

    def on_layout(self, dual):
        if dual:
            self.pane_b.setVisible(True)
            self.splitter.addWidget(self.pane_a)
            self.splitter.addWidget(self.pane_b)
            self.layout_btn.setText("Layout: Dual ▦")
        else:
            self.pane_b.setVisible(False)
            self.layout_btn.setText("Layout: Single ▤")
        _save_bool("layout_dual", dual)
        if not dual and self.compare_btn.isChecked():
            self.compare_btn.setChecked(False)

    def on_compare(self, checked):
        if checked and not self.pane_b.isVisible():
            self.layout_btn.setChecked(True)
            self.on_layout(True)
        if checked:
            ka = self.pane_a.displayed_key_set()
            kb = self.pane_b.displayed_key_set()
            self.pane_a.model.set_compare(
                self.pane_a.compare_state_map(kb),
                block_color=QColor(225, 225, 230),
                present_color=QColor(255, 224, 178))
            self.pane_b.model.set_compare(
                self.pane_b.compare_state_map(ka),
                block_color=QColor(225, 225, 230),
                present_color=QColor(178, 223, 255))
            self.compare_btn.setText("Compare ■ (on)")
        else:
            self.pane_a.model.clear_compare()
            self.pane_b.model.clear_compare()
            self.compare_btn.setText("Compare ▶")


def _app_icon() -> "QIcon | None":
    """Load the bundled SnitchQL window icon.

    When frozen (PyInstaller) the .ico is collected into the ``assets``
    subfolder next to the real exe; from source it lives in the repo
    ``assets`` dir. Returns None if not found so callers can no-op safely.
    """
    candidates = []
    # Bundled-in-build location (handles one-file temp unpack via _app_dir()).
    candidates.append(_app_dir() / "assets" / "snitchql_app.ico")
    # Running from source: <repo>/assets next to the snitchql package.
    candidates.append(Path(__file__).resolve().parent.parent / "assets" / "snitchql_app.ico")
    for c in candidates:
        if c.is_file():
            return QIcon(str(c))
    return None


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # clean cross-platform look
    # Apply the custom SnitchQL window icon (title bar / taskbar).
    _ic = _app_icon()
    if _ic is not None:
        app.setWindowIcon(_ic)
    # In a frozen windowed build (console=False) any stray print()/traceback
    # would otherwise be lost or, on some setups, trigger a console window.
    # Redirect stdout/stderr to a log file next to the exe so diagnostics are
    # captured without a visible terminal. Only active when frozen.
    #
    # The file is created *lazily* — the first time anything is actually written
    # (e.g. a traceback). On a clean run nothing is ever written, so no stray
    # SnitchQL.log is left next to the exe.
    if getattr(sys, "frozen", False):
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            ctypes.windll.kernel32.GetModuleFileNameW(0, buf, 1024)
            log_path = Path(buf.value).parent / "SnitchQL.log"
        except Exception:
            log_path = Path(sys.executable).parent / "SnitchQL.log"

        _log = _LazyLog(log_path)
        sys.stdout = _log
        sys.stderr = _log
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
