"""Home screen and the first three wizard steps: photos, check, settings."""
from __future__ import annotations

from .. import APP_NAME, DISCLAIMER_SHORT

import math
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QProgressBar,
                               QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget, QMessageBox)

from ..core import crs as crsmod, jobs, photos as ph, presets, system
from ..core.pipeline import geoid_grid_path
from . import theme
from .session import Session
from .widgets import IssueBox, PhotoMap, StatCard, card, label
from .workers import Task


class Page(QWidget):
    go_next = Signal()
    go_back = Signal()

    def __init__(self, session: Session):
        super().__init__()
        self.setObjectName("Page")
        self.s = session
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(32, 24, 32, 20)
        self.root.setSpacing(14)

    def enter(self):
        pass

    def footer(self, back: str | None = "Back", next_text: str | None = "Next") -> tuple:
        row = QHBoxLayout()
        b = n = None
        if back:
            b = QPushButton(back)
            b.clicked.connect(self.go_back.emit)
            row.addWidget(b)
        row.addStretch(1)
        if next_text:
            n = QPushButton(next_text)
            n.setObjectName("Primary")
            n.setMinimumWidth(160)
            n.clicked.connect(self.go_next.emit)
            row.addWidget(n)
        self.root.addLayout(row)
        return b, n


def clear_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif it.layout():
            clear_layout(it.layout())


def fmt_minutes(m: float) -> str:
    if m < 60:
        return f"about {max(1, round(m))} min"
    h = m / 60
    return f"about {h:.1f} hours" if h < 10 else f"about {round(h)} hours"


# ============================================================================
# Home
# ============================================================================

class ToolTile(QFrame):
    clicked = Signal()

    def __init__(self, icon: str, title: str, text: str):
        super().__init__()
        self.setObjectName("ChoiceCard")
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        i = QLabel(icon)
        i.setStyleSheet("font-size: 30px; background: transparent;")
        lay.addWidget(i)
        t = QLabel(title)
        t.setStyleSheet(f"font-size: 17px; font-weight: 600; color: {theme.ACCENT}; background: transparent;")
        lay.addWidget(t)
        d = QLabel(text)
        d.setWordWrap(True)
        d.setStyleSheet(f"font-size: 12px; color: {theme.MUTED}; background: transparent;")
        lay.addWidget(d)
        lay.addStretch(1)

    def enterEvent(self, e):
        self.setProperty("selected", "true")
        self.style().unpolish(self)
        self.style().polish(self)

    def leaveEvent(self, e):
        self.setProperty("selected", "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, _e):
        self.clicked.emit()


class HomePage(Page):
    new_job = Signal()
    open_job = Signal(str)
    open_settings = Signal()
    open_georef = Signal()
    open_models = Signal()
    about = Signal()

    def __init__(self, session):
        super().__init__(session)
        top = QHBoxLayout()
        col = QVBoxLayout()
        col.addWidget(label(APP_NAME, "H1"))
        col.addWidget(label("Turn drone photos into orthomosaics, elevation models and contours "
                            "for GIS. Works without internet.", "Muted", wrap=True))
        top.addLayout(col, 1)
        sb = QPushButton("⚙  Settings")
        sb.clicked.connect(self.open_settings.emit)
        top.addWidget(sb, 0, Qt.AlignTop)
        self.root.addLayout(top)

        self.engine_box = QVBoxLayout()
        self.root.addLayout(self.engine_box)

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        t1 = ToolTile("\U0001F681", "Process drone photos",
                      "Orthomosaic, surface & terrain models, contours \u2013 exported for GIS in any coordinate system.")
        t1.clicked.connect(self.new_job.emit)
        t2 = ToolTile("\U0001F5FA", "Georeference a map",
                      "Scanned or exported maps with a coordinate grid (e.g. MGB hazard maps) \u2192 GeoTIFF for QGIS, "
                      "plus hazard zones as polygons.")
        t2.clicked.connect(self.open_georef.emit)
        t3 = ToolTile("\U0001F30A", "Flood & landslide modelling",
                      "Raise the water level on a terrain model, or test a steep slope and see how far a landslide runs.")
        t3.clicked.connect(self.open_models.emit)
        for t in (t1, t2, t3):
            t.setMinimumHeight(150)
            tiles.addWidget(t, 1)
        self.root.addLayout(tiles)
        hero = QHBoxLayout()
        self.new_btn = QPushButton("\uFF0B   New mapping job")
        self.new_btn.setObjectName("Primary")
        self.new_btn.clicked.connect(self.new_job.emit)
        hero.addWidget(self.new_btn)
        ob = QPushButton("Open a job folder…")
        ob.clicked.connect(self._browse_job)
        hero.addWidget(ob)
        hero.addStretch(1)
        self.root.addLayout(hero)

        self.root.addSpacing(6)
        self.root.addWidget(label("Recent jobs", "H2"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Job", "Created", "Photos", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for i in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._open_row(r))
        self.root.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.empty = label("No jobs yet. Start with “New mapping job”.", "Muted")
        row.addWidget(self.empty)
        row.addStretch(1)
        self.open_btn = QPushButton("Open")
        self.open_btn.clicked.connect(lambda: self._open_row(self.table.currentRow()))
        row.addWidget(self.open_btn)
        self.folder_btn = QPushButton("Show in folder")
        self.folder_btn.clicked.connect(self._show_folder)
        row.addWidget(self.folder_btn)
        self.root.addLayout(row)
        foot = QHBoxLayout()
        credit = label(DISCLAIMER_SHORT, "Muted")
        credit.setStyleSheet("font-size: 11px;")
        foot.addWidget(credit)
        ab = QPushButton("About")
        ab.setObjectName("Link")
        ab.clicked.connect(self.about.emit)
        foot.addWidget(ab)
        foot.addStretch(1)
        self.root.addLayout(foot)
        self._dirs: list[str] = []

    def enter(self):
        clear_layout(self.engine_box)
        e = self.s.engine
        if e.ok:
            self.engine_box.addWidget(IssueBox("ok", e.message, system.describe_machine()))
        else:
            self.engine_box.addWidget(IssueBox("error", "Processing engine not found",
                                               e.message + " New jobs can be prepared, but not processed. "
                                               "Open Settings \u2192 \u201cDownload and install engine\u201d. "
                                               "Georeferencing and modelling work without it."))
        self.table.setRowCount(0)
        self._dirs = []
        status_text = {jobs.STATUS_DONE: "✔ Ready", jobs.STATUS_FAILED: "⚠ Stopped with error",
                       jobs.STATUS_CANCELLED: "Cancelled", jobs.STATUS_RUNNING: "Interrupted",
                       jobs.STATUS_NEW: "Not processed"}
        for d in self.s.settings.recent_jobs:
            if not jobs.Job.is_job_dir(d):
                continue
            try:
                j = jobs.Job.load(d)
            except Exception:
                continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, v in enumerate([j.name, j.created.replace("T", "  ")[:17], str(len(j.photos)),
                                   status_text.get(j.status, j.status)]):
                self.table.setItem(r, c, QTableWidgetItem(v))
            self._dirs.append(d)
        has = bool(self._dirs)
        self.empty.setVisible(not has)
        self.open_btn.setEnabled(has)
        self.folder_btn.setEnabled(has)
        if has:
            self.table.selectRow(0)

    def _open_row(self, r):
        if 0 <= r < len(self._dirs):
            self.open_job.emit(self._dirs[r])

    def _show_folder(self):
        r = self.table.currentRow()
        if 0 <= r < len(self._dirs):
            system.open_path(self._dirs[r])

    def _browse_job(self):
        d = QFileDialog.getExistingDirectory(self, f"Open a {APP_NAME} job folder", self.s.settings.jobs_root)
        if d:
            if jobs.Job.is_job_dir(d):
                self.open_job.emit(d)
            else:
                QMessageBox.information(self, "Not a job folder",
                                        f"That folder is not a {APP_NAME} job (no job.json inside).")


# ============================================================================
# Step 1 - photos
# ============================================================================

class DropZone(QFrame):
    dropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(170)
        self.setStyleSheet(f"QFrame {{ background: white; border: 2px dashed {theme.LINE}; border-radius: 12px; }}"
                           "QLabel { border: none; background: transparent; }")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        t = QLabel("\U0001F4C1")
        t.setStyleSheet("font-size: 38px;")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        m = QLabel("Drag the folder with the drone photos here")
        m.setStyleSheet("font-size: 16px; font-weight: 600;")
        m.setAlignment(Qt.AlignCenter)
        lay.addWidget(m)
        self.btn = QPushButton("Choose folder…")
        self.btn.setObjectName("Primary")
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.btn)
        row.addStretch(1)
        lay.addLayout(row)
        hint = QLabel("Copy the photos from the drone's SD card to the laptop first. "
                      "One folder = one site.")
        hint.setObjectName("Muted")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if os.path.isfile(p):
                p = os.path.dirname(p)
            if os.path.isdir(p):
                self.dropped.emit(p)
                return


class PhotosPage(Page):
    def __init__(self, session):
        super().__init__(session)
        self.root.addWidget(label("Step 1 · Choose the photos", "H1"))
        self.zone = DropZone()
        self.zone.btn.clicked.connect(self._browse)
        self.zone.dropped.connect(self.load_folder)
        self.root.addWidget(self.zone)
        c = card()
        g = QGridLayout(c)
        g.setContentsMargins(16, 12, 16, 12)
        g.addWidget(label("Photo folder"), 0, 0)
        self.folder = label("-", "Muted")
        g.addWidget(self.folder, 0, 1)
        g.addWidget(label("Job name"), 1, 0)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Barangay San Isidro farm lot 2026-11-04")
        g.addWidget(self.name, 1, 1)
        g.setColumnStretch(1, 1)
        self.root.addWidget(c)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        self.status = label("", "Muted")
        self.root.addWidget(self.bar)
        self.root.addWidget(self.status)
        self.root.addStretch(1)
        _b, self.next_btn = self.footer("Cancel", "Next: check photos")
        self.next_btn.setEnabled(False)
        self.name.textChanged.connect(self._update)
        self.task: Task | None = None
        self.photos_dir = ""

    def enter(self):
        self._update()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Folder with drone photos", os.path.expanduser("~"))
        if d:
            self.load_folder(d)

    def load_folder(self, folder: str):
        if self.task and self.task.isRunning():
            return
        self.photos_dir = folder
        self.folder.setText(folder)
        if not self.name.text().strip() or self.name.property("auto"):
            self.name.setText(jobs.suggest_name(folder))
            self.name.setProperty("auto", True)
        self.s.photos, self.s.survey = [], None
        self.bar.setVisible(True)
        self.bar.setValue(0)
        self.status.setText("Reading photo locations…")
        self.next_btn.setEnabled(False)

        def work(t: Task):
            return ph.scan_folder(folder, lambda i, n: t.report(100 * i / n, f"Reading photo {i} of {n}"),
                                  lambda: t.cancelled)

        self.task = Task(work, self)
        self.task.progress.connect(lambda p, txt: (self.bar.setValue(int(p)), self.status.setText(txt)))
        self.task.done.connect(self._scanned)
        self.task.failed.connect(lambda e: self.status.setText(f"Could not read photos: {e}"))
        self.task.start()

    def _scanned(self, photos):
        self.s.photos = photos
        self.bar.setVisible(False)
        n_gps = sum(1 for p in photos if p.has_gps)
        self.status.setText(f"Found {len(photos)} photos, {n_gps} with GPS location." if photos
                            else "No photos (JPG/TIF) found in this folder.")
        self._update()

    def _update(self):
        self.next_btn.setEnabled(bool(self.s.photos) and bool(self.name.text().strip()))

    def job_name(self) -> str:
        return self.name.text().strip()


# ============================================================================
# Step 2 - check
# ============================================================================

class CheckPage(Page):
    def __init__(self, session):
        super().__init__(session)
        self.root.addWidget(label("Step 2 · Check the photos", "H1"))
        body = QHBoxLayout()
        body.setSpacing(16)
        mapcard = card()
        ml = QVBoxLayout(mapcard)
        ml.setContentsMargins(6, 6, 6, 6)
        self.map = PhotoMap()
        ml.addWidget(self.map)
        body.addWidget(mapcard, 3)

        right = QVBoxLayout()
        grid = QGridLayout()
        grid.setSpacing(10)
        self.c_photos = StatCard("Usable photos")
        self.c_area = StatCard("Area covered")
        self.c_gsd = StatCard("Ground resolution")
        self.c_overlap = StatCard("Overlap")
        for i, c in enumerate((self.c_photos, self.c_area, self.c_gsd, self.c_overlap)):
            grid.addWidget(c, i // 2, i % 2)
        right.addLayout(grid)

        hrow = QHBoxLayout()
        hrow.addWidget(label("Flight height above ground"))
        self.height = QDoubleSpinBox()
        self.height.setRange(0, 1500)
        self.height.setDecimals(0)
        self.height.setSuffix(" m")
        self.height.setSpecialValueText("unknown")
        self.height.setMinimumWidth(120)
        self.height.setToolTip("Only needed if the drone does not record it. Used for estimates only.")
        self.height.editingFinished.connect(self._reanalyze)
        hrow.addWidget(self.height)
        hrow.addStretch(1)
        right.addLayout(hrow)
        self.height_note = label("", "Muted", wrap=True)
        right.addWidget(self.height_note)

        self.blur_cb = QCheckBox("Leave out blurry photos (recommended)")
        self.blur_cb.setChecked(True)
        self.blur_cb.toggled.connect(self._blur_toggled)
        right.addWidget(self.blur_cb)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        holder.setObjectName("Page")
        self.issues = QVBoxLayout(holder)
        self.issues.setContentsMargins(0, 0, 4, 0)
        self.issues.setSpacing(8)
        scroll.setWidget(holder)
        right.addWidget(scroll, 1)
        body.addLayout(right, 2)
        self.root.addLayout(body, 1)
        _b, self.next_btn = self.footer("Back", "Next: choose outputs")

    def enter(self):
        if self.s.flight_height:
            self.height.setValue(self.s.flight_height)
        self._reanalyze()

    def _reanalyze(self):
        h = self.height.value() or None
        if self.height.value() == 0:
            # only use a typed value; keep drone-recorded height otherwise
            h = None
        self.s.flight_height = h
        s = ph.analyze(self.s.photos, h)
        self.s.survey = s
        self._show(s)

    def _blur_toggled(self, on):
        if self.s.job:
            self.s.job.options.exclude_blurry = on
        if self.s.survey:
            self._show(self.s.survey)

    def _show(self, s: ph.Survey):
        excl = self.blur_cb.isChecked()
        pts = []
        for p in sorted([p for p in self.s.photos if p.x is not None], key=lambda p: (p.taken or "", p.name)):
            st = "excluded" if (p.blurry and excl) else ("blurry" if p.blurry else "ok")
            pts.append((p.x, p.y, st))
        r = math.sqrt(s.footprint_m[0] * s.footprint_m[1] / math.pi) if s.footprint_m else 0
        self.map.set_points(pts, r)
        usable = sum(1 for p in self.s.photos if p.has_gps and not (excl and p.blurry))
        self.c_photos.set(f"{usable}", f"of {s.total} in folder")
        self.c_area.set(f"{s.area_ha:.1f} ha" if s.area_ha else "-",
                        f"{s.area_ha * 2.471:.1f} acres" if s.area_ha else "")
        self.c_gsd.set(f"{s.gsd_cm:.1f} cm/pixel" if s.gsd_cm else "unknown",
                       "estimated" if s.gsd_cm else "enter flight height below")
        if s.coverage is not None:
            quality = "excellent" if s.coverage >= 9 else "good" if s.coverage >= 5 else "low"
            self.c_overlap.set(quality.capitalize(), f"each spot in ~{s.coverage:.0f} photos")
        else:
            self.c_overlap.set("unknown", "needs flight height")
        self.blur_cb.setText(f"Leave out blurry photos ({len(s.blurry)} found)")
        self.blur_cb.setEnabled(bool(s.blurry))
        if s.height_source and s.height_source != "entered by user":
            self.height_note.setText(f"Flight height {s.flight_height_m:.0f} m ({s.height_source}).")
        elif not s.flight_height_m:
            self.height_note.setText("This camera does not record height above ground. Type it in "
                                     "to see resolution and overlap estimates (optional).")
        else:
            self.height_note.setText("")
        clear_layout(self.issues)
        if not s.issues:
            self.issues.addWidget(IssueBox("ok", "Photos look good", "Ready to process."))
        for i in sorted(s.issues, key=lambda i: {"error": 0, "warning": 1, "info": 2}[i.level]):
            self.issues.addWidget(IssueBox(i.level, i.title, i.detail))
        self.issues.addStretch(1)
        self.next_btn.setEnabled(s.can_process)


# ============================================================================
# Step 3 - outputs & settings
# ============================================================================

class ChoiceCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, title: str, text: str):
        super().__init__()
        self.key = key
        self.setObjectName("ChoiceCard")
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)
        t = QLabel(title)
        t.setStyleSheet("font-size: 15px; font-weight: 600; background: transparent;")
        lay.addWidget(t)
        d = QLabel(text)
        d.setWordWrap(True)
        d.setStyleSheet(f"font-size: 12px; color: {theme.MUTED}; background: transparent;")
        lay.addWidget(d)
        self.est = QLabel("")
        self.est.setWordWrap(True)
        self.est.setStyleSheet("font-size: 12px; font-weight: 600; background: transparent;")
        lay.addWidget(self.est)
        lay.addStretch(1)

    def set_selected(self, on: bool):
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, _e):
        self.clicked.emit(self.key)


class OptionsPage(Page):
    edit_gcps = Signal()

    def __init__(self, session):
        super().__init__(session)
        self.root.addWidget(label("Step 3 · Quality and outputs", "H1"))
        row = QHBoxLayout()
        self.cards: dict[str, ChoiceCard] = {}
        for key, p in presets.PRESETS.items():
            c = ChoiceCard(key, p.label, p.description)
            c.clicked.connect(self._pick)
            self.cards[key] = c
            row.addWidget(c, 1)
        self.root.addLayout(row)
        self.ram_note = QVBoxLayout()
        self.root.addLayout(self.ram_note)

        cols = QHBoxLayout()
        cols.setSpacing(16)
        # outputs
        oc = card()
        ol = QVBoxLayout(oc)
        ol.setContentsMargins(16, 12, 16, 12)
        ol.addWidget(label("Outputs", "H2"))
        self.cb_ortho = QCheckBox("Orthomosaic (always created)")
        self.cb_ortho.setChecked(True)
        self.cb_ortho.setEnabled(False)
        self.cb_dsm = QCheckBox("Surface model - DSM (tops of trees and buildings)")
        self.cb_dtm = QCheckBox("Terrain model - DTM (bare ground)")
        crow = QHBoxLayout()
        self.cb_cont = QCheckBox("Contour lines, every")
        self.contour = QComboBox()
        for txt, v in (("automatic", 0.0), ("0.5 m", 0.5), ("1 m", 1.0), ("2 m", 2.0), ("5 m", 5.0)):
            self.contour.addItem(txt, v)
        crow.addWidget(self.cb_cont)
        crow.addWidget(self.contour)
        crow.addStretch(1)
        self.cb_pc = QCheckBox("3D point cloud (LAZ)")
        for w in (self.cb_ortho, self.cb_dsm, self.cb_dtm):
            ol.addWidget(w)
        ol.addLayout(crow)
        ol.addWidget(self.cb_pc)
        self.elev_note = label("", "Muted", wrap=True)
        ol.addWidget(self.elev_note)
        ol.addStretch(1)
        cols.addWidget(oc, 1)

        # coordinates
        cc = card()
        cl = QVBoxLayout(cc)
        cl.setContentsMargins(16, 12, 16, 12)
        cl.addWidget(label("Map coordinates", "H2"))
        cl.addWidget(label("Coordinate system", "Muted"))
        self.crs = QComboBox()
        for code, text in crsmod.CHOICES:
            self.crs.addItem(text, code)
        cl.addWidget(self.crs)
        cl.addWidget(label("Heights", "Muted"))
        self.heights = QComboBox()
        self.heights.addItem("Keep as recorded by the drone / GCPs", "as_recorded")
        self.heights.addItem("Convert ellipsoid heights to sea level (EGM96)", "egm96")
        self.heights.addItem("Shift all heights by a fixed amount", "offset")
        if not geoid_grid_path():
            self.heights.model().item(1).setEnabled(False)
            self.heights.setItemData(1, f"Geoid file not installed with this copy of {APP_NAME}",
                                     Qt.ToolTipRole)
        cl.addWidget(self.heights)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-1000, 1000)
        self.offset.setDecimals(3)
        self.offset.setSuffix(" m")
        self.offset.setVisible(False)
        cl.addWidget(self.offset)
        self.heights.currentIndexChanged.connect(
            lambda: self.offset.setVisible(self.heights.currentData() == "offset"))
        cl.addSpacing(6)
        cl.addWidget(label("Accuracy (optional)", "H2"))
        grow = QHBoxLayout()
        self.gcp_label = label("No ground control points", "Muted", wrap=True)
        grow.addWidget(self.gcp_label, 1)
        gb = QPushButton("Ground control points…")
        gb.clicked.connect(self.edit_gcps.emit)
        grow.addWidget(gb)
        cl.addLayout(grow)
        cl.addStretch(1)
        cols.addWidget(cc, 1)
        self.root.addLayout(cols)
        self.root.addStretch(1)
        self.disk_note = QVBoxLayout()
        self.root.addLayout(self.disk_note)
        _b, self.next_btn = self.footer("Back", "▶  Start processing")
        self.preset = "standard"

    def enter(self):
        o = self.s.job.options
        self.preset = o.preset
        self.cb_dsm.setChecked(o.dsm)
        self.cb_dtm.setChecked(o.dtm)
        self.cb_cont.setChecked(o.contours)
        self.cb_pc.setChecked(o.pointcloud)
        self.contour.setCurrentIndex(max(0, self.contour.findData(o.contour_interval)))
        self.crs.setCurrentIndex(max(0, self.crs.findData(o.target_crs)))
        self.heights.setCurrentIndex(max(0, self.heights.findData(o.heights)))
        self.offset.setValue(o.height_offset_m)
        self.refresh_gcp_label()
        self._pick(self.preset)

    def refresh_gcp_label(self):
        o = self.s.job.options
        txt = "No ground control points - accuracy depends on the drone GPS (often 1-5 m)."
        if o.gcp_file and os.path.isfile(self.s.job.path("gcp_list.txt")):
            from ..core import gcp as gcpmod
            try:
                gs = gcpmod.GcpSet.load_json(self.s.job.path("gcp_tags.json"))
                txt = gcpmod.summary(gs)
            except Exception:
                txt = "Ground control points added."
        self.gcp_label.setText(txt)

    def _pick(self, key):
        self.preset = key
        for k, c in self.cards.items():
            c.set_selected(k == key)
        n = len(self.s.selected_photos())
        for k, c in self.cards.items():
            c.est.setText(f"⏱ {fmt_minutes(ph.estimate_minutes(n, k))} on this computer")
        elev = presets.PRESETS[key].elevation
        for w in (self.cb_dsm, self.cb_dtm, self.cb_cont, self.contour, self.cb_pc):
            w.setEnabled(elev)
        self.elev_note.setText("" if elev else "Quick preview makes the orthomosaic only. "
                                               "Choose Standard for elevation models and contours.")
        clear_layout(self.ram_note)
        need = ph.estimate_ram_gb(n, key)
        have = system.total_ram_gb()
        if have and need > have * 0.8:
            self.ram_note.addWidget(IssueBox("warning", f"This may need about {need:.0f} GB of memory",
                                             f"This computer has {have:.0f} GB. Processing could fail or be "
                                             f"very slow. Consider a lower quality or fewer photos."))
        clear_layout(self.disk_note)
        need_disk = ph.estimate_disk_gb(self.s.selected_photos(), key)
        free = system.free_disk_gb(self.s.settings.jobs_root)
        if free and need_disk > free:
            self.disk_note.addWidget(IssueBox("error", "Not enough free disk space",
                                              f"Needs about {need_disk:.0f} GB, only {free:.0f} GB free on the "
                                              f"jobs drive. Free up space or change the jobs folder in Settings."))
            self.next_btn.setEnabled(False)
        else:
            self.next_btn.setEnabled(self.s.engine.ok)
            if not self.s.engine.ok:
                self.disk_note.addWidget(IssueBox("error", "Processing engine not found",
                                                  "Install OpenDroneMap or set its folder in Settings."))

    def apply(self):
        o = self.s.job.options
        o.preset = self.preset
        elev = presets.PRESETS[self.preset].elevation
        o.dsm = self.cb_dsm.isChecked() and elev
        o.dtm = self.cb_dtm.isChecked() and elev
        o.contours = self.cb_cont.isChecked() and elev
        o.pointcloud = self.cb_pc.isChecked() and elev
        o.contour_interval = float(self.contour.currentData())
        o.target_crs = self.crs.currentData()
        o.heights = self.heights.currentData()
        o.height_offset_m = self.offset.value()
