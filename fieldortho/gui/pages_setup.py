"""Home screen and the first three wizard steps: photos, check, settings."""
from __future__ import annotations

from .. import APP_NAME

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
from .inputs import LengthEdit
from .widgets import IssueBox, PhotoMap, StatCard, action_bar, card, label
from .workers import Task


class Page(QWidget):
    go_next = Signal()
    go_back = Signal()
    heading_changed = Signal()
    CRUMB = ""
    TITLE = ""

    def __init__(self, session: Session):
        super().__init__()
        self.setObjectName("Page")
        self.s = session
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(20, 16, 20, 14)
        self.root.setSpacing(12)
        self._actions = None

    def enter(self):
        pass

    # ---- shown by the main window's title bar
    def heading(self) -> tuple[str, str]:
        return self.CRUMB, self.TITLE

    def header_actions(self):
        return self._actions

    def set_actions(self, widgets):
        self._actions = action_bar(widgets)

    def footer(self, back: str | None = "Back", next_text: str | None = "Next") -> tuple:
        row = QHBoxLayout()
        b = n = None
        if back:
            b = QPushButton(("←  " + back) if back == "Back" else back)
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

class ToolCard(QFrame):
    """A tool on the Home screen: icon, title, description and one button."""

    def __init__(self, icon: str, title: str, text: str, button: str, primary: bool = False):
        super().__init__()
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(8)
        ic = QLabel(theme.ICONS[icon])
        ic.setFixedSize(40, 40)
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(f"font-family: {theme.ICON_FONT}; font-size: 19px; color: {theme.ACCENT}; "
                         f"background: {theme.ACCENT_SOFT}; border-radius: 8px;")
        lay.addWidget(ic)
        lay.addWidget(label(title, "H2"))
        lay.addWidget(label(text, "Muted", wrap=True))
        lay.addStretch(1)
        self.button = QPushButton(button + "  →")
        if primary:
            self.button.setObjectName("Primary")
        row = QHBoxLayout()
        row.addWidget(self.button)
        row.addStretch(1)
        lay.addLayout(row)
        self.setMinimumHeight(190)


class HomePage(Page):
    new_job = Signal()
    open_row = Signal(object)
    open_georef = Signal()
    open_models = Signal()
    open_measure = Signal()
    CRUMB = "Home"
    TITLE = "Welcome"

    def __init__(self, session):
        super().__init__(session)
        self.engine_box = QVBoxLayout()
        self.root.addLayout(self.engine_box)
        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        t1 = ToolCard("drone", "Drone mapping",
                      "Turn drone photos into an orthomosaic, surface and terrain models and contours, "
                      "exported for GIS in any coordinate system.", "New mapping job", True)
        t1.button.clicked.connect(self.new_job.emit)
        t2 = ToolCard("georef", "Georeference a map",
                      "Place a scanned map with a coordinate grid (e.g. MGB hazard maps) on the ground and "
                      "turn its hazard zones into polygons.", "Open map image")
        t2.button.clicked.connect(self.open_georef.emit)
        t3 = ToolCard("flood", "Flood & landslide",
                      "Raise a river or the sea on a terrain model, or test a steep slope and see how far a "
                      "landslide could run.", "Open terrain model")
        t3.button.clicked.connect(self.open_models.emit)
        t4 = ToolCard("measure", "Measure & earthworks",
                      "Measure heights, paths, areas and volumes on a drone map: stockpiles, lots to level, "
                      "how much soil to dig or fill.", "Open drone map")
        t4.button.clicked.connect(self.open_measure.emit)
        for t in (t1, t2, t3, t4):
            tiles.addWidget(t, 1)
        self.root.addLayout(tiles)
        from .jobs_page import JobsTable
        self.jobs = JobsTable(session, 12, "Recent jobs")
        self.jobs.open_row.connect(self.open_row.emit)
        self.root.addWidget(self.jobs, 1)
        self.pill = QLabel("")
        self.set_actions([self.pill])

    def enter(self):
        clear_layout(self.engine_box)
        e = self.s.engine
        if e.ok:
            self.pill.setText(f"●  Engine ready · {e.message.replace(' ready', '')}")
            self.pill.setStyleSheet(f"background: {theme.OK_SOFT}; color: {theme.OK}; border-radius: 13px; "
                                    "padding: 5px 12px; font-weight: 600;")
        else:
            self.pill.setText("●  Engine not installed")
            self.pill.setStyleSheet(f"background: {theme.ERROR_SOFT}; color: {theme.ERROR}; border-radius: 13px; "
                                    "padding: 5px 12px; font-weight: 600;")
            self.engine_box.addWidget(IssueBox("error", "Processing engine not found",
                                               e.message + " New jobs can be prepared, but not processed. "
                                               "Open Settings → “Download and install engine”. "
                                               "Georeferencing and modelling work without it."))
        self.jobs.refresh()


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
    TITLE = "Choose the photos"

    def __init__(self, session):
        super().__init__(session)
        self.zone = DropZone()
        self.zone.btn.clicked.connect(self._browse)
        self.zone.dropped.connect(self.load_folder)
        self.root.addWidget(self.zone)
        c = card()
        g = QGridLayout(c)
        g.setContentsMargins(16, 12, 16, 12)
        g.setVerticalSpacing(6)
        g.addWidget(label("Photo folder"), 0, 0)
        self.folder = label("-", "Muted")
        g.addWidget(self.folder, 0, 1)
        g.addWidget(label("Job name"), 1, 0)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Barangay San Isidro farm lot 2026-11-04")
        self.name.setMaxLength(80)
        g.addWidget(self.name, 1, 1)
        self.name_hint = label("Used as the name of the job folder. Letters, numbers, spaces and - are fine.",
                               "Hint", wrap=True)
        g.addWidget(self.name_hint, 2, 1)
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
        name = self.name.text().strip()
        bad = sorted(set(c for c in name if c in r'<>:"/\|?*'))
        if bad:
            self.name_hint.setObjectName("Warn")
            self.name_hint.setText("These characters cannot be used in a folder name and will be replaced by _: "
                                   + " ".join(bad))
        elif not name and self.s.photos:
            self.name_hint.setObjectName("Error")
            self.name_hint.setText("Type a name for this job.")
        else:
            self.name_hint.setObjectName("Hint")
            self.name_hint.setText("Used as the name of the job folder. Letters, numbers, spaces and - are fine.")
        theme.repolish(self.name_hint)
        self.next_btn.setEnabled(bool(self.s.photos) and bool(name))

    def job_name(self) -> str:
        return self.name.text().strip()


# ============================================================================
# Step 2 - check
# ============================================================================

class CheckPage(Page):
    TITLE = "Check the photos"

    def __init__(self, session):
        super().__init__(session)
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

        fc = card()
        fl = QVBoxLayout(fc)
        fl.setContentsMargins(16, 12, 16, 12)
        fl.setSpacing(6)
        fl.addWidget(label("FLIGHT HEIGHT", "Section"))
        fl.addWidget(label("Height above the take-off point", "H2"))
        self.height_edit = LengthEdit(max_m=1500, typical=(20, 500))
        self.height = self.height_edit.spin            # value in the unit shown (metres by default)
        self.height.setToolTip("The flight height set in the drone app. Used only for the estimates above.")
        self.height.editingFinished.connect(self._reanalyze)
        self.height_edit.unit.currentIndexChanged.connect(lambda _i: self._height_note())
        self.height_edit.changed.connect(self._height_note)
        fl.addWidget(self.height_edit)
        fl.addWidget(label("The height set in the drone's flight app (e.g. \u201cAltitude\u201d in DJI apps), "
                           "measured from where the drone took off. Typical mapping flights: 60\u2013120 m "
                           "(200\u2013400 ft). Used only for the estimates above \u2013 processing does not "
                           "need it.", "Hint", wrap=True))
        self.height_note = label("", "Good", wrap=True)
        fl.addWidget(self.height_note)
        right.addWidget(fc)

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
        self.height_edit.set_metres(self.s.flight_height)
        self._reanalyze()

    def _reanalyze(self):
        # only a typed value is used; otherwise the drone-recorded height (if any) is kept
        h = self.height_edit.metres()
        self.s.flight_height = h
        s = ph.analyze(self.s.photos, h)
        self.s.survey = s
        self._show(s)

    def _height_note(self, *_):
        s = self.s.survey
        warn = self.height_edit.warning()
        if warn:
            txt, name = "\u26A0 " + warn, "Warn"
        elif s and s.height_source and s.height_source != "entered by user" and s.flight_height_m:
            txt, name = (f"\u2714 The photos record about {s.flight_height_m:.0f} m "
                         f"({s.flight_height_m * 3.2808:.0f} ft) ({s.height_source}) \u2013 you can leave this "
                         "blank."), "Good"
        elif s and not s.flight_height_m:
            txt, name = ("This camera does not record its height. Type it to see the resolution and overlap "
                         "estimates (optional)."), "Hint"
        else:
            txt, name = "", "Hint"
        self.height_note.setObjectName(name)
        theme.repolish(self.height_note)
        self.height_note.setText(txt)
        self.height_note.setVisible(bool(txt))

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
        self._height_note()
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
    TITLE = "Quality and outputs"

    def __init__(self, session):
        super().__init__(session)
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
        self.offset.setSingleStep(0.1)
        self.offset.setSuffix(" m")
        self.offset.setFixedWidth(140)
        self.offset_box = QWidget()
        ob = QVBoxLayout(self.offset_box)
        ob.setContentsMargins(0, 0, 0, 0)
        ob.setSpacing(3)
        orow = QHBoxLayout()
        orow.addWidget(QLabel("Add to every height"))
        orow.addWidget(self.offset)
        orow.addStretch(1)
        ob.addLayout(orow)
        ob.addWidget(label("In metres. Positive raises, negative lowers all heights, e.g. \u22120.53 m to match "
                           "a benchmark that reads 0.53 m lower than the drone.", "Hint", wrap=True))
        self.offset_box.setVisible(False)
        cl.addWidget(self.offset_box)
        self.heights_hint = label("", "Hint", wrap=True)
        cl.addWidget(self.heights_hint)
        self.heights.currentIndexChanged.connect(self._heights_changed)
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
        self._heights_changed()
        self.refresh_gcp_label()
        self._pick(self.preset)

    def _heights_changed(self, *_):
        k = self.heights.currentData()
        self.offset_box.setVisible(k == "offset")
        self.heights_hint.setText({
            "as_recorded": "Heights stay as the drone GPS (or your control points) measured them. Most drones "
                           "record heights above the ellipsoid, which in the Philippines are roughly 40\u201370 m "
                           "higher than sea level.",
            "egm96": "Converts drone (ellipsoid) heights to approximate mean sea level using the EGM96 geoid "
                     "(about \u00b11 m). Choose this if your control points or maps use sea-level heights.",
            "offset": ""}.get(k, ""))
        self.heights_hint.setVisible(k != "offset")

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
