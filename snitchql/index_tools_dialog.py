"""SnitchQL — Index Tools dialog (Verify / Repair / Rebuild).

Launched from the main toolbar's "Index Tools" button so the explorer UI stays
uncrowded. Verify runs natively (decode leaves + cross-check .dat). Repair and
Rebuild delegate to the official DBISAM engine (dbsys) — the only safe way to
rewrite a .idx byte-for-byte.
"""
from __future__ import annotations
import sys
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTextEdit, QFileDialog, QMessageBox, QProgressBar,
)

from snitchql.index_tools import verify_table, repair_table, rebuild_indexes, verify_table_engine


class VerifyWorker(QThread):
    finished = pyqtSignal(object)
    progress = pyqtSignal(str)

    def __init__(self, dat_path: Path):
        super().__init__()
        self.dat_path = dat_path

    def run(self):
        try:
            self.progress.emit(f"Verifying {self.dat_path.name} …")
            res = verify_table(self.dat_path)
            self.finished.emit(res)
        except Exception as e:
            self.finished.emit(e)


class IndexToolsDialog(QDialog):
    def __init__(self, parent=None, dat_path: Path | None = None):
        super().__init__(parent)
        self.setWindowTitle("SnitchQL — Index Tools")
        self.resize(640, 480)
        self.dat_path: Path | None = dat_path
        self.worker: VerifyWorker | None = None

        root = QVBoxLayout(self)

        # table selector
        sel = QHBoxLayout()
        self.path_lbl = QLabel(str(dat_path) if dat_path else "No table selected")
        self.sel_btn = QPushButton("Choose .dat…")
        self.sel_btn.clicked.connect(self.on_choose)
        sel.addWidget(QLabel("Table:"))
        sel.addWidget(self.path_lbl, 1)
        sel.addWidget(self.sel_btn)
        root.addLayout(sel)

        # action buttons
        acts = QHBoxLayout()
        self.verify_btn = QPushButton("Verify (native, partial)")
        self.verify_engine_btn = QPushButton("Verify (engine, full)")
        self.repair_btn = QPushButton("Repair (dbsys)")
        self.rebuild_btn = QPushButton("Rebuild (dbsys)")
        self.verify_btn.clicked.connect(self.on_verify)
        self.verify_engine_btn.clicked.connect(self.on_verify_engine)
        self.repair_btn.clicked.connect(self.on_repair)
        self.rebuild_btn.clicked.connect(self.on_rebuild)
        acts.addWidget(self.verify_btn)
        acts.addWidget(self.verify_engine_btn)
        acts.addWidget(self.repair_btn)
        acts.addWidget(self.rebuild_btn)
        acts.addStretch(1)
        root.addLayout(acts)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.out = QTextEdit()
        self.out.setReadOnly(True)
        self.out.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self.out, 1)

        self.append("Index Tools\n"
                     "• Verify — decode index leaves and cross-check against the data file.\n"
                     "• Repair / Rebuild — delegate to the official DBISAM engine (dbsys),\n"
                     "  which is the only safe way to rewrite a .idx byte-for-byte.")

    # ---- helpers ----
    def append(self, text: str):
        self.out.append(text)

    def on_choose(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select DBISAM .dat", "", "DBISAM data (*.dat)")
        if p:
            self.dat_path = Path(p)
            self.path_lbl.setText(p)

    def _idx_path(self) -> Path | None:
        if not self.dat_path:
            QMessageBox.warning(self, "No table", "Choose a .dat file first.")
            return None
        idx = self.dat_path.with_suffix('.idx')
        if not idx.exists():
            QMessageBox.warning(self, "No index", f"No .idx found at {idx}")
            return None
        return idx

    # ---- actions ----
    def on_verify(self):
        if not self.dat_path:
            QMessageBox.warning(self, "No table", "Choose a .dat file first.")
            return
        self.verify_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.append("\n— Starting Verify —")
        self.worker = VerifyWorker(self.dat_path)
        self.worker.progress.connect(self.append)
        self.worker.finished.connect(self.on_verify_done)
        self.worker.start()

    def on_verify_done(self, res):
        self.progress.setVisible(False)
        self.verify_btn.setEnabled(True)
        if isinstance(res, Exception):
            self.append(f"Verify error: {res}")
            return
        self.append(f"Overall: {res.overall}  (rows={res.nrows})")
        for name, info in sorted(res.indexes.items()):
            cov = info.get('coverage', 0)
            note = info.get('note', '')
            self.append(f"  • {name}: recovered {info['matches']} entries "
                        f"(coverage {cov:.2%}) {note}")
        for n in res.notes:
            self.append(f"  ! {n}")
        self.append("— Verify complete —")

    def on_verify_engine(self):
        """Authoritative full-coverage verify via the DBISAM engine (dbsys)."""
        if not self.dat_path:
            QMessageBox.warning(self, "No table", "Choose a .dat file first.")
            return
        ok, msg = verify_table_engine(self.dat_path)
        self.append(f"\n[Engine Verify] {msg}")
        if not ok:
            QMessageBox.information(self, "Engine Verify", msg)

    def on_repair(self):
        idx = self._idx_path()
        if not idx or not self.dat_path:
            return
        ok, msg = repair_table(self.dat_path)
        self.append(f"\n[Repair] {msg}")
        if not ok:
            QMessageBox.information(self, "Repair", msg)

    def on_rebuild(self):
        idx = self._idx_path()
        if not idx or not self.dat_path:
            return
        ok, msg = rebuild_indexes(self.dat_path)
        self.append(f"\n[Rebuild] {msg}")
        if not ok:
            QMessageBox.information(self, "Rebuild", msg)
