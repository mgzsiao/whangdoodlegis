"""'Use my own files' window and the grouped source list (drone jobs vs. own files)."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QStandardItem
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

from ..core import filesets as fsm, jobs
from . import theme
from .widgets import label

TIF_FILTER = "GeoTIFF (*.tif *.tiff);;All files (*)"

# why each file is needed, per screen: (required?, text)
REASONS = {
    "flood": {
        "dtm": "Floods and landslides follow the bare ground. This is the model the scenarios are run on. "
               "A DEM from NAMRIA IFSAR / LiDAR or from other drone software goes here.",
        "dsm": "Ground plus trees and roofs. Only used when there is no DTM: water and slides are then "
               "blocked by buildings and trees, so results are rougher.",
        "ortho": "Optional. The photo shown under the flood and landslide areas so you can see houses, roads "
                 "and rivers. Not used in the calculation.",
    },
    "measure": {
        "dtm": "Bare-ground levels: roads, lots and pads, cut and fill amounts, slopes and side views.",
        "dsm": "Ground plus piles, buildings and trees: needed to measure the volume of a stockpile or "
               "a building's height.",
        "ortho": "Optional. The photo shown under your drawing so you can see where to click. Also used to "
                 "colour the 3-D model.",
    },
}
NEED_TEXT = ("At least one elevation model (DTM or DSM) is required. Without it the app cannot process "
             "this feature.")


class FileRow(QFrame):
    def __init__(self, kind: str, reason: str, tag: str):
        super().__init__()
        self.kind = kind
        self.setObjectName("Card")
        g = QGridLayout(self)
        g.setContentsMargins(12, 8, 12, 10)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(4)
        t = QLabel(fsm.LABELS[kind])
        t.setStyleSheet("font-weight: 600;")
        g.addWidget(t, 0, 0)
        tg = QLabel(tag)
        col = theme.ACCENT if tag != "Optional" else "#7A8699"
        tg.setStyleSheet(f"color: {col}; font-size: 11px; font-weight: 600;")
        g.addWidget(tg, 0, 1, Qt.AlignRight)
        g.addWidget(label(reason, "Muted", wrap=True), 1, 0, 1, 2)
        h = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("No file chosen")
        h.addWidget(self.edit, 1)
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse)
        h.addWidget(b)
        c = QPushButton("Clear")
        c.clicked.connect(self.edit.clear)
        h.addWidget(c)
        g.addLayout(h, 2, 0, 1, 2)

    def _browse(self):
        start = os.path.dirname(self.edit.text()) if self.edit.text() else os.path.expanduser("~")
        p, _ = QFileDialog.getOpenFileName(self, fsm.LABELS[self.kind], start, TIF_FILTER)
        if p:
            self.edit.setText(os.path.normpath(p))
            w = self.window()
            if hasattr(w, "_suggest_name"):
                w._suggest_name(p)

    def path(self) -> str:
        return self.edit.text().strip().strip('"')


class OwnFilesDialog(QDialog):
    """Pick DTM / DSM / ortho made outside the app. Result: self.fileset (core.filesets.FileSet)."""

    def __init__(self, settings, purpose: str = "measure", parent=None):
        super().__init__(parent)
        self.settings = settings
        self.fileset = None
        self.setWindowTitle("Use my own files")
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        h = QLabel("Use my own elevation and photo files")
        h.setStyleSheet("font-size: 16px; font-weight: 600;")
        lay.addWidget(h)
        lay.addWidget(label("For maps made outside this app (other drone software, NAMRIA IFSAR / LiDAR, ...). "
                            "Use GeoTIFF files that carry their coordinate system. Everything is checked and "
                            "converted on this computer; nothing is downloaded.", "Muted", wrap=True))
        n = QHBoxLayout()
        n.addWidget(QLabel("Name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Barangay Lumbia LiDAR")
        n.addWidget(self.name, 1)
        lay.addLayout(n)
        reasons = REASONS.get(purpose, REASONS["measure"])
        self.rows = {}
        for k in fsm.KINDS:
            tag = "Optional" if k == "ortho" else "Required: DTM or DSM"
            if purpose == "flood" and k == "dtm":
                tag = "Required (recommended)"
            self.rows[k] = FileRow(k, reasons[k], tag)
            lay.addWidget(self.rows[k])
        self.feet = QCheckBox("Heights in these files are in feet (convert to metres)")
        self.feet.setToolTip("Most files are in metres. Tick only if you know the heights are in feet.")
        lay.addWidget(self.feet)
        lay.addWidget(label(NEED_TEXT, "Muted", wrap=True))
        row = QHBoxLayout()
        row.addStretch(1)
        c = QPushButton("Cancel")
        c.clicked.connect(self.reject)
        row.addWidget(c)
        ok = QPushButton("Check and use these files")
        ok.setObjectName("Primary")
        ok.clicked.connect(self._ok)
        row.addWidget(ok)
        lay.addLayout(row)

    def _suggest_name(self, path):
        if not self.name.text().strip():
            self.name.setText(os.path.splitext(os.path.basename(path))[0])

    def picked(self) -> dict:
        return {k: r.path() for k, r in self.rows.items()}

    def _ok(self):
        from PySide6.QtGui import QGuiApplication
        picked = self.picked()
        if not (picked["dtm"] or picked["dsm"]):
            QMessageBox.warning(self, "Elevation model required",
                                "We cannot process without an elevation model.\n\n" + NEED_TEXT +
                                "\n\nDTM (bare ground): " + REASONS["measure"]["dtm"] +
                                "\nDSM (surface): " + REASONS["measure"]["dsm"])
            return
        name = self.name.text().strip() or os.path.splitext(os.path.basename(picked["dtm"] or picked["dsm"]))[0]
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            fs = fsm.create(name, picked, self.settings.jobs_root, heights_feet=self.feet.isChecked())
        except fsm.FileProblem as e:
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Cannot use these files", f"We cannot process with these files.\n\n{e}")
            return
        except Exception as e:
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Cannot use these files", f"{e.__class__.__name__}: {e}")
            return
        QGuiApplication.restoreOverrideCursor()
        if fs.notes:
            QMessageBox.information(self, "Files ready", "The files are ready.\n\n" + "\n".join(fs.notes))
        self.fileset = fs
        self.accept()


# ---------------------------------------------------------------- grouped source list
HEAD_JOBS = "DRONE JOBS PROCESSED IN THIS APP"
HEAD_OWN = "MY OWN FILES"


def _header(combo: QComboBox, text: str):
    combo.addItem(text, None)
    it: QStandardItem = combo.model().item(combo.count() - 1)
    it.setFlags(Qt.NoItemFlags)
    f = QFont(it.font())
    f.setBold(True)
    f.setPointSizeF(max(7.0, f.pointSizeF() * 0.85))
    it.setFont(f)
    it.setForeground(Qt.gray)


def fill_sources(combo: QComboBox, settings, entries_for, placeholder: str):
    """Fill a source combo grouped as drone jobs / own files.
    entries_for(src) -> list of (text, data) for one Job or FileSet (empty = not usable)."""
    combo.addItem(placeholder, None)
    jl, seen = [], set()
    for d in settings.recent_jobs:
        key = os.path.normcase(d)
        if key in seen or not jobs.Job.is_job_dir(d):
            continue
        seen.add(key)
        try:
            j = jobs.Job.load(d)
        except Exception:
            continue
        jl.extend(entries_for(j))
    ol = []
    for fs in fsm.all_filesets():
        if fs.missing():
            continue
        ol.extend(entries_for(fs))
    _header(combo, HEAD_JOBS)
    if jl:
        for t, data in jl:
            combo.addItem("✈  " + t, data)
    else:
        _header(combo, "   (none yet)")
    _header(combo, HEAD_OWN)
    for t, data in ol:
        combo.addItem("\U0001F4C1  " + t, data)
    if not ol:
        _header(combo, "   (none yet: click “Use my own files…”)")


def first_usable(combo: QComboBox) -> int:
    for i in range(combo.count()):
        if combo.itemData(i) is not None and combo.model().item(i).isEnabled():
            return i
    return -1


def add_entry(combo: QComboBox, text: str, data, own: bool) -> int:
    """Insert one entry under its group (used after opening a job or creating a file set)."""
    i = combo.findData(data)
    if i >= 0:
        return i
    head = combo.findText(HEAD_OWN if own else HEAD_JOBS)
    prefix = "\U0001F4C1  " if own else "✈  "
    if head < 0:
        combo.addItem(prefix + text, data)
        return combo.count() - 1
    pos = head + 1
    while pos < combo.count() and combo.itemData(pos) is None and combo.itemText(pos).startswith("   ("):
        combo.removeItem(pos)
    while pos < combo.count() and combo.itemData(pos) is not None:
        pos += 1
    combo.insertItem(pos, prefix + text, data)
    return pos
