"""One list of everything the user made: drone mapping jobs, georeferenced maps, flood and
landslide scenarios. Used (shortened) on Home and in full on the Jobs page."""
from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QComboBox)

from .. import APP_NAME
from ..core import activity, crs as crsmod, jobs, system
from . import theme
from .widgets import action_bar, card, label

DRONE_STATUS = {jobs.STATUS_DONE: "✔ Ready", jobs.STATUS_FAILED: "⚠ Stopped with error",
                jobs.STATUS_CANCELLED: "Cancelled", jobs.STATUS_RUNNING: "Interrupted",
                jobs.STATUS_NEW: "Not processed"}


@dataclass
class Row:
    kind: str          # drone | georef | flood | landslide
    name: str
    created: str
    size: str
    crs: str
    status: str
    ref: str           # job folder or session / scenario file
    folder: str
    activity_id: str = ""

    @property
    def kind_label(self) -> str:
        return "Drone mapping" if self.kind == "drone" else activity.KINDS.get(self.kind, self.kind)


def _crs_text(code: str) -> str:
    try:
        return crsmod.label(code).replace(" (", "  (", 1) if code else ""
    except Exception:
        return code or ""


def collect(settings) -> list[Row]:
    rows: list[Row] = []
    for d in settings.recent_jobs:
        if not jobs.Job.is_job_dir(d):
            continue
        try:
            j = jobs.Job.load(d)
        except Exception:
            continue
        rows.append(Row("drone", j.name, j.created, f"{len(j.photos)} photos", _crs_text(j.options.target_crs),
                        DRONE_STATUS.get(j.status, j.status), d, d))
    for a in activity.load():
        if not a.exists():
            continue
        rows.append(Row(a.kind, a.name, a.created, a.size, a.crs, a.status, a.path,
                        a.folder or os.path.dirname(a.path), a.id))
    rows.sort(key=lambda r: r.created, reverse=True)
    return rows


class JobsTable(QWidget):
    """Searchable table with Open / Show in folder / Remove buttons."""
    open_row = Signal(object)          # Row

    COLS = ["JOB", "TYPE", "CREATED", "PHOTOS / SIZE", "COORDINATE SYSTEM", "STATUS"]

    def __init__(self, session, limit: int | None = None, title: str = "Recent jobs"):
        super().__init__()
        self.s = session
        self.limit = limit
        self.rows: list[Row] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        c = card()
        outer.addWidget(c)
        lay = QVBoxLayout(c)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        top = QHBoxLayout()
        top.setContentsMargins(16, 12, 12, 10)
        top.addWidget(label(title, "H2"))
        top.addStretch(1)
        self.kind = QComboBox()
        for t, k in (("All types", ""), ("Drone mapping", "drone"), ("Georeference", "georef"),
                     ("Flood scenario", "flood"), ("Landslide scenario", "landslide"),
                     ("Earthworks plan", "earthworks")):
            self.kind.addItem(t, k)
        self.kind.currentIndexChanged.connect(self._fill)
        top.addWidget(self.kind)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search jobs")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._fill)
        top.addWidget(self.search)
        lay.addLayout(top)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setObjectName("Flat")
        self.table.setHorizontalHeaderLabels(self.COLS)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i, w in enumerate((0, 150, 140, 130, 230, 150)):
            if w:
                self.table.setColumnWidth(i, w)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._open(r))
        self.table.itemSelectionChanged.connect(self._sel_changed)
        lay.addWidget(self.table, 1)
        bot = QHBoxLayout()
        bot.setContentsMargins(16, 8, 12, 10)
        self.empty = label("Nothing yet. Start a drone mapping job, georeference a map or run a scenario.", "Muted")
        bot.addWidget(self.empty)
        bot.addStretch(1)
        self.rm_btn = QPushButton("Remove from list")
        self.rm_btn.setToolTip("Only removes the entry from this list. No files are deleted.")
        self.rm_btn.clicked.connect(self._remove)
        self.folder_btn = QPushButton("Show in folder")
        self.folder_btn.clicked.connect(self._show_folder)
        self.open_btn = QPushButton("Open")
        self.open_btn.setObjectName("Primary")
        self.open_btn.clicked.connect(lambda: self._open(self.table.currentRow()))
        for b in (self.rm_btn, self.folder_btn, self.open_btn):
            bot.addWidget(b)
        lay.addLayout(bot)
        self._shown: list[Row] = []

    def refresh(self):
        self.rows = collect(self.s.settings)
        self._fill()

    def _fill(self, *_):
        q = self.search.text().strip().lower()
        k = self.kind.currentData()
        shown = [r for r in self.rows if (not k or r.kind == k) and
                 (not q or q in f"{r.name} {r.kind_label} {r.crs} {r.status}".lower())]
        if self.limit:
            shown = shown[:self.limit]
        self._shown = shown
        self.table.setRowCount(0)
        for r in shown:
            i = self.table.rowCount()
            self.table.insertRow(i)
            vals = [r.name, r.kind_label, r.created.replace("T", "  ")[:17], r.size, r.crs, r.status]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 5:
                    col = theme.OK if v.startswith("✔") else theme.WARN if v.startswith("⚠") else theme.MUTED
                    it.setForeground(QColor(col))
                if c == 0:
                    it.setToolTip(r.ref)
                self.table.setItem(i, c, it)
        has = bool(shown)
        self.empty.setVisible(not self.rows)
        if has:
            self.table.selectRow(0)
        self._sel_changed()

    def _sel_changed(self):
        ok = 0 <= self.table.currentRow() < len(self._shown) and bool(self.table.selectedItems())
        for b in (self.rm_btn, self.folder_btn, self.open_btn):
            b.setEnabled(ok)

    def _cur(self) -> Row | None:
        r = self.table.currentRow()
        return self._shown[r] if 0 <= r < len(self._shown) else None

    def _open(self, r):
        if 0 <= r < len(self._shown):
            self.open_row.emit(self._shown[r])

    def _show_folder(self):
        row = self._cur()
        if row:
            system.open_path(row.folder if os.path.isdir(row.folder) else os.path.dirname(row.ref))

    def _remove(self):
        row = self._cur()
        if not row:
            return
        if QMessageBox.question(self, "Remove from list",
                                f"Remove “{row.name}” from the {APP_NAME} list?\n\nNo files are deleted; "
                                "you can open it again from its folder.") != QMessageBox.Yes:
            return
        if row.kind == "drone":
            st = self.s.settings
            st.recent_jobs = [d for d in st.recent_jobs if os.path.abspath(d) != os.path.abspath(row.ref)]
            st.save()
        else:
            activity.remove(row.activity_id)
        self.refresh()


class JobsPage(QWidget):
    """All jobs, with type filter and search."""
    heading_changed = Signal()

    def __init__(self, session):
        super().__init__()
        self.setObjectName("Page")
        self.s = session
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        self.table = JobsTable(session, None, "All jobs")
        lay.addWidget(self.table, 1)
        self.open_row = self.table.open_row
        self._actions = None

    def heading(self):
        return "Projects", "Jobs"

    def header_actions(self):
        return self._actions

    def set_actions(self, widgets):
        self._actions = action_bar(widgets)

    def enter(self):
        self.table.refresh()

