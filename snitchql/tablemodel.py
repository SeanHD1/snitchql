"""Virtual table model + proxy + background reader for the SnitchQL GUI.

Why this exists
---------------
The original Pane used a ``QTableWidget`` and built *every* row as a
``QTableWidgetItem`` in ``populate()``. For a 236k-row / 64-column table that
took ~30s of pure GUI-thread work, and the file read itself (~12s) also ran on
the GUI thread. The result: SnitchQL "is not responding" on open, on Edit Mode
toggle (which re-populates), and after Save (which re-reads the file).

This module fixes all three by:

  * ``RowTableModel`` — a ``QAbstractTableModel`` backed by the already-decoded
    rows list. Qt only ever asks for the cells currently visible, so a 465k-row
    table shows instantly. No ``QTableWidgetItem`` is ever materialised.
  * ``RowProxyModel`` — a ``QSortFilterProxyModel`` that owns the Quick filter
    (substring across all columns) and the structured filter-builder rules
    (AND/OR). Sorting is handled natively by the proxy.
  * ``ReaderThread`` — a ``QThread`` that runs ``read_table`` (and pre-builds a
    lower-cased search haystack) off the GUI thread, emitting the finished
    ``Table`` so the UI is never blocked.

Editing is supported by the model: editable cells report ``ItemIsEditable`` in
edit mode, and ``setData`` mutates the in-memory row + stages the change. The
model owns the staging dict and emits ``editStaged`` so the Save button can
update without repopulating anything.
"""
from PyQt6.QtCore import (
    QAbstractTableModel, QSortFilterProxyModel, QThread, QModelIndex,
    Qt, pyqtSignal,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QStyledItemDelegate, QMessageBox

from snitchql.reader import (
    EDITABLE_TYPES, editor_kind, display_for_edit, coerce_edit_value,
)
from snitchql import query as query_mod


# Compare-state sentinels (per source row). Stored in the model so the virtual
# view can paint them via BackgroundRole without ever allocating per-cell items.
_CMP_BLOCK = "block"     # row exists in the other pane -> de-emphasised
_CMP_PRESENT = "present"  # row unique to this pane -> soft tint


class RowTableModel(QAbstractTableModel):
    """Virtual model over a decoded DBISAM ``Table``."""

    editStaged = pyqtSignal(int)  # staged-change count changed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.table = None
        self._rows = []
        self._hays = []          # precomputed lower-cased search haystack per row
        self._edit_mode = False
        self._staged = {}        # (row_index, col_name) -> original value
        self._compare_state = []  # per-row: None / _CMP_BLOCK / _CMP_PRESENT
        self._readonly = False    # True for SQL-query results (no disk backing)
        self._cmp_block = QColor(225, 225, 230)
        self._cmp_present = QColor(255, 224, 178)

    # -- data loading --------------------------------------------------------
    def set_table(self, table, hays=None):
        """Swap in a freshly loaded Table. Clears staging + compare state."""
        self.beginResetModel()
        self.table = table
        self._rows = table.rows
        self._hays = hays if hays is not None else []
        self._staged.clear()
        self._compare_state = [None] * len(self._rows)
        self._readonly = False
        self.endResetModel()
        self.editStaged.emit(0)

    def set_readonly(self, on: bool):
        """Mark the current result as non-editable (e.g. a SQL query result
        that has no backing .dat to write to). Clear any staging too."""
        self._readonly = on
        if on:
            self._staged.clear()
            self.editStaged.emit(0)
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(self.rowCount() - 1, self.columnCount() - 1),
            [Qt.ItemDataRole.DisplayRole],
        )

    def set_haystack(self, hays):
        self._hays = hays

    # -- required model API --------------------------------------------------
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else (len(self.table.columns) if self.table else 0)

    def headerData(self, section, orientation, role, index=QModelIndex()):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if self.table and 0 <= section < len(self.table.columns):
                return self.table.columns[section].name
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not self.table:
            return None
        row = self._rows[index.row()]
        col = self.table.columns[index.column()]
        val = row.get(col.name)
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return "" if val is None else str(val)
        if role == Qt.ItemDataRole.ForegroundRole:
            if self._edit_mode and col.type_id not in EDITABLE_TYPES:
                return QColor(150, 150, 150)
            return None
        if role == Qt.ItemDataRole.BackgroundRole:
            st = self._compare_state[index.row()]
            if st == _CMP_BLOCK:
                return self._cmp_block
            if st == _CMP_PRESENT:
                return self._cmp_present
            return None
        return None

    # -- editing -------------------------------------------------------------
    def set_edit_mode(self, on: bool):
        self._edit_mode = on
        if not on:
            self._staged.clear()
            self.editStaged.emit(0)
        # Repaint flags (editable cells change) + foreground (greyed read-only).
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(self.rowCount() - 1, self.columnCount() - 1),
            [Qt.ItemDataRole.DisplayRole],
        )

    def flags(self, index):
        f = super().flags(index)
        if self._edit_mode and not self._readonly and self.table \
                and 0 <= index.column() < len(self.table.columns):
            col = self.table.columns[index.column()]
            if col.type_id in EDITABLE_TYPES:
                f |= Qt.ItemFlag.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not self._edit_mode \
                or self._readonly or not self.table:
            return False
        col = self.table.columns[index.column()]
        if col.type_id not in EDITABLE_TYPES:
            return False
        row_index = index.row()
        row = self._rows[row_index]
        new_val = "" if value is None else str(value)
        original = row.get(col.name)
        if new_val == ("" if original is None else str(original)):
            self._staged.pop((row_index, col.name), None)
        else:
            self._staged[(row_index, col.name)] = original
            row[col.name] = new_val
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
        self.editStaged.emit(len(self._staged))
        return True

    # -- compare highlighting ------------------------------------------------
    def set_compare(self, state_map, block_color=None, present_color=None):
        """``state_map`` is a list aligned to source rows: None/_CMP_BLOCK/_CMP_PRESENT."""
        if block_color is not None:
            self._cmp_block = block_color
        if present_color is not None:
            self._cmp_present = present_color
        self._compare_state = state_map
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(self.rowCount() - 1, self.columnCount() - 1),
            [Qt.ItemDataRole.BackgroundRole],
        )

    def clear_compare(self):
        self._compare_state = [None] * len(self._rows)
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(self.rowCount() - 1, self.columnCount() - 1),
            [Qt.ItemDataRole.BackgroundRole],
        )

    # -- staging access (for the Save flow) ----------------------------------
    @property
    def staged(self):
        return self._staged

    def staged_count(self):
        return len(self._staged)


class RowProxyModel(QSortFilterProxyModel):
    """Owns the Quick substring filter + structured filter-builder rules.

    Sits between ``RowTableModel`` and the ``QTableView``. Sorting is handled
    natively; filtering recomputes only when ``invalidateFilter`` is called
    (i.e. when the user types or hits Apply).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._quick = ""
        self._rules = []
        self._combine = "AND"

    def set_quick(self, text):
        self._quick = (text or "").strip().lower()
        self.invalidateFilter()

    def set_rules(self, rules, combine="AND"):
        self._rules = rules or []
        self._combine = combine
        self.invalidateFilter()

    def clear_filters(self):
        self._quick = ""
        self._rules = []
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        from typing import cast
        model = cast(RowTableModel, self.sourceModel())
        if model is None or source_row >= len(model._rows):
            return False
        row = model._rows[source_row]
        # structured rules (field/op/value, AND/OR)
        if self._rules:
            results = [query_mod.make_predicate(f, o, v)(row) for (f, o, v) in self._rules]
            ok = all(results) if self._combine == "AND" else any(results)
            if not ok:
                return False
        # quick substring — uses the precomputed haystack when available
        if self._quick:
            if model._hays and source_row < len(model._hays):
                if self._quick not in model._hays[source_row]:
                    return False
            else:
                hay = " ".join("" if v is None else str(v) for v in row.values()).lower()
                if self._quick not in hay:
                    return False
        return True

    def lessThan(self, left, right):
        # Numeric-aware sort so Integers/Doubles order correctly, not lexically.
        ls = self.sourceModel().data(left, Qt.ItemDataRole.DisplayRole)
        rs = self.sourceModel().data(right, Qt.ItemDataRole.DisplayRole)
        try:
            lf = float(ls)
            rf = float(rs)
            return lf < rf
        except (ValueError, TypeError):
            return (ls or "") < (rs or "")


class TypedCellDelegate(QStyledItemDelegate):
    """Per-column editor: date picker for Date, datetime for Timestamp,
    time for Time, True/False combo for Boolean, plain text otherwise.

    Coercion + validation are delegated to ``snitchql.reader.coerce_edit_value``
    so a bad entry (e.g. "abc" in an Integer cell) is rejected with a friendly
    message instead of corrupting the row.
    """

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self._model = model

    def createEditor(self, parent, option, index):
        from PyQt6.QtWidgets import (
            QDateEdit, QTimeEdit, QDateTimeEdit, QComboBox, QLineEdit,
        )
        from PyQt6.QtCore import QDate, QTime, QDateTime
        col = self._model.table.columns[index.column()]
        kind = editor_kind(col)
        val = self._model.data(index, Qt.ItemDataRole.EditRole)
        if kind == "date":
            ed = QDateEdit(parent)
            ed.setCalendarPopup(True)
            ed.setDisplayFormat("yyyy-MM-dd")
            if isinstance(val, str) and val[:10]:
                try:
                    y, m, d = (int(x) for x in val[:10].split("-"))
                    ed.setDate(QDate(y, m, d))
                except Exception:
                    ed.setDate(QDate.currentDate())
            return ed
        if kind == "time":
            ed = QTimeEdit(parent)
            ed.setDisplayFormat("HH:mm:ss")
            if isinstance(val, str):
                try:
                    ed.setTime(QTime.fromString(val, "HH:mm:ss"))
                except Exception:
                    pass
            return ed
        if kind == "ts":
            ed = QDateTimeEdit(parent)
            ed.setCalendarPopup(True)
            ed.setDisplayFormat("yyyy-MM-ddTHH:mm:ss")
            if isinstance(val, str):
                try:
                    ed.setDateTime(QDateTime.fromString(val, "yyyy-MM-ddTHH:mm:ss"))
                except Exception:
                    ed.setDateTime(QDateTime.currentDateTime())
            return ed
        if kind == "bool":
            ed = QComboBox(parent)
            ed.addItems(["False", "True"])
            ed.setCurrentText("True" if val else "False")
            return ed
        ed = QLineEdit(parent)
        ed.setText(display_for_edit(val, col))
        return ed

    def setModelData(self, editor, model, index):
        from PyQt6.QtWidgets import QDateEdit, QTimeEdit, QDateTimeEdit, QComboBox, QLineEdit
        col = self._model.table.columns[index.column()]
        if isinstance(editor, QDateEdit):
            text = editor.date().toString("yyyy-MM-dd")
        elif isinstance(editor, QTimeEdit):
            text = editor.time().toString("HH:mm:ss")
        elif isinstance(editor, QDateTimeEdit):
            text = editor.dateTime().toString("yyyy-MM-ddTHH:mm:ss")
        elif isinstance(editor, QComboBox):
            text = editor.currentText()
        else:
            text = editor.text()
        try:
            coerced = coerce_edit_value(text, col)
        except ValueError as e:
            QMessageBox.warning(editor, "Invalid value",
                                f"{col.name}: {e}")
            return
        model.setData(index, "" if coerced is False and col.type_id == 4 else coerced,
                      Qt.ItemDataRole.EditRole)


class ReaderThread(QThread):
    """Reads a DBISAM table off the GUI thread.

    Emits ``loaded`` with the decoded ``Table`` and a pre-built lower-cased
    search haystack (so the Quick filter stays instant even on 465k rows). On
    failure emits ``failed`` with the error string.
    """

    loaded = pyqtSignal(object, object)  # (Table, haystack)
    failed = pyqtSignal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            from snitchql.reader import read_table
            t = read_table(self.path)
            hays = [
                " ".join("" if v is None else str(v) for v in r.values()).lower()
                for r in t.rows
            ]
            self.loaded.emit(t, hays)
        except Exception as e:  # pragma: no cover - surfaced to the UI
            self.failed.emit(str(e))
