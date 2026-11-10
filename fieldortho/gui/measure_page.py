"""Measure & earthworks: measure points, paths, heights, areas and volumes on a processed drone map."""
from __future__ import annotations

import math
import os

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QGridLayout,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSplitter, QVBoxLayout, QWidget)

from ..core import earthworks as ew, exporters as ex, jobs, measure as ms, system
from ..core.model_export import background_for, slope_rgb
from ..core.terrain import load_dem, lonlat as dem_lonlat
from . import theme
from .canvas import MapCanvas, map_card
from .export_dialog import SimpleExportDialog
from .pages_setup import Page, clear_layout
from .widgets import IssueBox, StatCard, label
from .workers import Task

TOOLS = [  # key, button text, title, instruction
    ("point", "Point", "Point height",
     "Click anywhere on the map to read the ground height and coordinates there."),
    ("path", "Path", "Path and side view",
     "Click along a road, creek bank or trail. Double-click (or press Enter) on the last point to finish."),
    ("height", "Height", "Height difference",
     "Click the first point (A), then the second point (B). You get the height difference, distance and slope."),
    ("area", "Area", "Area",
     "Click around the area. Double-click (or press Enter) to close the outline."),
    ("volume", "Volume", "Volume (cut and fill)",
     "Click around a pile, a hole or a lot to level. Double-click (or press Enter) to close the outline."),
    ("compare", "Compare", "Change between two surveys",
     "Choose the other survey below, then click around the area that changed (a quarry, a stockpile, a landslide, "
     "a construction site). Double-click (or press Enter) to close the outline."),
]
TOOL = {t[0]: t for t in TOOLS}
NEEDS = {"point": 1, "height": 2, "path": 2, "area": 3, "volume": 3, "plan": 3, "compare": 3}
SHAPE = "#F5B841"          # amber, the logo colour: stands out on photos and relief
SAVED = "#1F3F77"


def fmt_m(v, d=2):
    return f"{v:,.{d}f} m" if v == v else "-"


def fmt_area(a):
    return f"{a:,.0f} m²" if a >= 100 else f"{a:,.1f} m²"


class ProfileChart(QWidget):
    """Side view of a path: distance along (x) and height (y)."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.d = self.z = None

    def set_data(self, dist, z):
        self.d, self.z = np.asarray(dist), np.asarray(z)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        if self.d is None or not np.isfinite(self.z).any():
            return
        ok = np.isfinite(self.z)
        d, z = self.d, self.z
        z0, z1 = float(np.nanmin(z)), float(np.nanmax(z))
        pad = max(0.5, (z1 - z0) * 0.12)
        z0, z1 = z0 - pad, z1 + pad
        L, R, T, B = 48, 10, 18, 24
        w, h = self.width() - L - R, self.height() - T - B
        dmax = max(float(d[-1]), 1e-6)

        def px(di, zi):
            return QPointF(L + di / dmax * w, T + (z1 - zi) / (z1 - z0) * h)
        f = QFont(self.font().family(), 8)
        p.setFont(f)
        p.setPen(QPen(QColor(theme.LINE), 1))
        for k in range(5):
            zz = z0 + (z1 - z0) * k / 4
            y = T + h - h * k / 4
            p.drawLine(QPointF(L, y), QPointF(L + w, y))
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(0, y - 8, L - 6, 16), Qt.AlignRight | Qt.AlignVCenter, f"{zz:.1f}")
            p.setPen(QPen(QColor(theme.LINE), 1))
        step = nice_step(dmax / 5)
        p.setPen(QColor(theme.MUTED))
        x = 0.0
        while x <= dmax + 1e-9:
            q = px(x, z0)
            p.drawText(QRectF(q.x() - 30, T + h + 4, 60, 16), Qt.AlignCenter, f"{x:g} m")
            x += step
        # ground
        path = QPainterPath()
        fill = QPainterPath()
        started = False
        for di, zi, o in zip(d, z, ok):
            if not o:
                continue
            if not started:
                path.moveTo(px(di, zi))
                fill.moveTo(px(di, z0))
                started = True
            else:
                path.lineTo(px(di, zi))
            fill.lineTo(px(di, zi))
        fill.lineTo(px(d[ok][-1], z0))
        fill.closeSubpath()
        p.fillPath(fill, QColor(138, 106, 58, 45))
        p.setPen(QPen(QColor("#8A6A3A"), 2))
        p.drawPath(path)
        p.setPen(QPen(QColor(theme.INK), 1))
        p.drawText(QRectF(L, 0, w, 12), Qt.AlignLeft, "height (m)")


def nice_step(target):
    if target <= 0:
        return 1.0
    m = 10 ** math.floor(math.log10(target))
    for k in (1, 2, 5, 10):
        if m * k >= target:
            return m * k
    return m * 10


class MeasurePage(Page):
    CRUMB = "Tools"
    TITLE = "Measure & earthworks"

    def __init__(self, session):
        super().__init__(session)
        self.source = QComboBox()
        self.source.setMinimumWidth(320)
        self.source.setToolTip("The processed drone job to measure on.")
        ob = QPushButton("Open job folder…")
        ob.clicked.connect(self._browse_job)
        self.mode_group = QButtonGroup(self)
        modes = []
        for i, text in enumerate(("Measure", "Plan earthworks")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setToolTip("Quick measurements" if i == 0 else "Guided: level a lot or pad, measure a pile, with a "
                                                              "report for engineers")
            self.mode_group.addButton(b, i)
            modes.append(b)
        self.mode_group.idClicked.connect(lambda i: self.set_mode("plan" if i else "measure"))
        self.set_actions(modes + [QLabel("   Drone map"), self.source, ob])
        self.mode = "measure"

        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        self.tool_group = QButtonGroup(self)
        extra = []
        for i, (key, text, _t, _h) in enumerate(TOOLS):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(TOOL[key][2])
            b.setStyleSheet("padding: 6px 10px;")
            b.setMinimumWidth(b.fontMetrics().horizontalAdvance(text) + 30)
            self.tool_group.addButton(b, i)
            extra.append(b)
        self.tool_group.button(0).setChecked(True)
        self.tool_group.idClicked.connect(self._tool_changed)
        self.bg = QComboBox()
        self.bg.addItems(["Drone photo", "Shaded relief", "Slope steepness"])
        self.bg.currentIndexChanged.connect(lambda _i: self._show_background())
        self.cont_cb = QCheckBox("Contours")
        self.cont_cb.toggled.connect(lambda _v: self._draw())
        self.canvas = MapCanvas()
        self.canvas.clicked.connect(self._click)
        self.canvas.double_clicked.connect(self._dclick)
        self.canvas.moved.connect(self._move)
        ll.addWidget(map_card(self.canvas, extra), 1)
        show = QHBoxLayout()
        show.setContentsMargins(4, 0, 4, 0)
        show.addWidget(QLabel("Background"))
        show.addWidget(self.bg)
        show.addWidget(self.cont_cb)
        show.addStretch(1)
        ll.addLayout(show)
        self.readout = label("Choose a processed drone job above.", "Muted")
        from .road_widgets import RoadProfileView
        self.road_profile = RoadProfileView()
        self.road_profile.setVisible(False)
        self.road_profile.picked.connect(lambda i: self.plan.select_station(i))
        ll.addWidget(self.road_profile)
        ll.addWidget(self.readout)
        split.addWidget(left)
        from PySide6.QtWidgets import QStackedWidget
        self.right = QStackedWidget()
        self.right.setMinimumWidth(420)
        self.right.addWidget(self._panel())
        split.addWidget(self.right)
        split.setStretchFactor(0, 1)
        split.setSizes([900, 420])
        self.root.addWidget(split, 1)

        for keys, fn in ((QKeySequence.Undo, self._undo), (Qt.Key_Backspace, self._undo),
                         (Qt.Key_Escape, self._clear), (Qt.Key_Return, self._finish), (Qt.Key_Enter, self._finish)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

        self.job = None
        self.files: dict = {}
        self.view = None            # display Dem (thinned) - map pixels <-> map coordinates
        self.bg_cache: dict = {}
        self.contours = None
        self.pts: list[tuple[float, float]] = []      # map coordinates
        self.finished = False
        self.result = None
        self.result_kind = None
        self.overlay = None
        self.road_res = None
        self.road_sel = 0
        self.preview_al = None
        self.saved: list[ms.Measurement] = []
        self.task = None
        self.rerun = QTimer(self)
        self.rerun.setSingleShot(True)
        self.rerun.setInterval(350)
        self.rerun.timeout.connect(self._compute)
        self.source.currentIndexChanged.connect(self._source_changed)
        from .plan_panel import PlanPanel
        self.plan = PlanPanel(self)
        self.right.addWidget(self.plan)
        self._tool_changed(0)

    # ================================================================ panel
    def _panel(self):
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setMinimumWidth(420)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        w = QWidget()
        w.setObjectName("Page")
        w.setStyleSheet("QWidget#Page { background: white; }")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        self.tool_title = label("", "H2")
        lay.addWidget(self.tool_title)
        self.tool_hint = label("", "Hint", wrap=True)
        lay.addWidget(self.tool_hint)
        row = QHBoxLayout()
        row.addWidget(QLabel("Heights from"))
        self.src_kind = QComboBox()
        self.src_kind.addItem("Bare ground (DTM)", "dtm")
        self.src_kind.addItem("Ground + trees, buildings, piles (DSM)", "dsm")
        self.src_kind.setToolTip("Bare ground removes trees and buildings. Use the second choice to measure piles, "
                                 "buildings or anything standing on the ground.")
        self.src_kind.currentIndexChanged.connect(self._src_kind_changed)
        row.addWidget(self.src_kind, 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.undo_btn = QPushButton("Undo last point")
        self.undo_btn.clicked.connect(self._undo)
        self.clear_btn = QPushButton("Start again")
        self.clear_btn.clicked.connect(self._clear)
        self.finish_btn = QPushButton("Finish shape")
        self.finish_btn.clicked.connect(self._finish)
        for b in (self.undo_btn, self.clear_btn, self.finish_btn):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        # volume options
        self.vol_box = QWidget()
        vb = QVBoxLayout(self.vol_box)
        vb.setContentsMargins(0, 4, 0, 0)
        vb.setSpacing(2)
        vb.addWidget(label("Measure the volume above or below…", "H2"))
        self.ref_group = QButtonGroup(self)
        for i, (key, text, sub) in enumerate((
                ("edge", "The ground around the edge", "Best for piles, mounds and holes: a surface is stretched "
                 "across the outline and everything above it is counted."),
                ("edge_mean", "A flat level at the average edge height", "For piles on flat ground."),
                ("level", "A fixed elevation", "To level a lot or pad to a planned height: shows how much to dig "
                 "and how much to fill."),
                ("lowest", "The lowest ground inside", "Everything above the lowest point."))):
            rb = QRadioButton(text)
            rb.setProperty("ref", key)
            rb.setChecked(i == 0)
            self.ref_group.addButton(rb, i)
            vb.addWidget(rb)
            hl = label(sub, "Hint", wrap=True)
            hl.setContentsMargins(24, 0, 0, 4)
            vb.addWidget(hl)
            if key == "level":
                lr = QHBoxLayout()
                lr.setContentsMargins(24, 0, 0, 4)
                self.level = QDoubleSpinBox()
                self.level.setRange(-500, 9000)
                self.level.setDecimals(2)
                self.level.setSingleStep(0.1)
                self.level.setSuffix(" m")
                self.level.setFixedWidth(140)
                self.level.valueChanged.connect(lambda _v: self._ref_changed())
                lr.addWidget(QLabel("Elevation"))
                lr.addWidget(self.level)
                lr.addStretch(1)
                vb.addLayout(lr)
        self.ref_group.idClicked.connect(lambda _i: self._ref_changed())
        hr = QHBoxLayout()
        hr.addWidget(QLabel("Soil loosens when dug by"))
        self.swell = QDoubleSpinBox()
        self.swell.setRange(0, 80)
        self.swell.setDecimals(0)
        self.swell.setValue(ms.DEFAULT_SWELL)
        self.swell.setSuffix(" %")
        self.swell.setFixedWidth(90)
        self.swell.setToolTip("Dug soil takes more space in a truck than in the ground. About 25% for common "
                              "earth; 0% if the pile is already loose.")
        self.swell.valueChanged.connect(lambda _v: self._show_result())
        hr.addWidget(self.swell)
        hr.addStretch(1)
        vb.addLayout(hr)
        tr = QHBoxLayout()
        tr.addWidget(QLabel("Other truck size"))
        self.truck = QDoubleSpinBox()
        self.truck.setRange(0, 60)
        self.truck.setDecimals(1)
        self.truck.setValue(0)
        self.truck.setSpecialValueText("none")
        self.truck.setSuffix(" m³")
        self.truck.setFixedWidth(100)
        self.truck.setToolTip("Trips are always shown for 6 m³ and 10 m³ trucks. Add the size you have on site.")
        self.truck.valueChanged.connect(lambda _v: self._show_result())
        tr.addWidget(self.truck)
        tr.addStretch(1)
        vb.addLayout(tr)
        lay.addWidget(self.vol_box)

        self.cmp_box = QWidget()
        cb = QVBoxLayout(self.cmp_box)
        cb.setContentsMargins(0, 4, 0, 0)
        cb.setSpacing(4)
        cb.addWidget(label("Compare this map with", "H2"))
        self.cmp_job = QComboBox()
        self.cmp_job.currentIndexChanged.connect(lambda _i: self._ref_changed())
        cb.addWidget(self.cmp_job)
        self.cmp_order = QComboBox()
        self.cmp_order.addItem("This map is the later survey (after)", "later")
        self.cmp_order.addItem("This map is the earlier survey (before)", "earlier")
        self.cmp_order.currentIndexChanged.connect(lambda _i: self._ref_changed())
        cb.addWidget(self.cmp_order)
        self.cmp_lineup = QCheckBox("Line up the heights on the ground around the area")
        self.cmp_lineup.setChecked(True)
        self.cmp_lineup.toggled.connect(lambda _v: self._ref_changed())
        cb.addWidget(self.cmp_lineup)
        cb.addWidget(label("Flights without ground control points can sit a metre or more apart in height. The app "
                           "shifts the later survey so the untouched ground 3\u201315 m around your outline matches. "
                           "Keep the outline a few metres away from other changes.", "Hint", wrap=True))
        self.cmp_box.setVisible(False)
        lay.addWidget(self.cmp_box)

        self.results = QVBoxLayout()
        self.results.setSpacing(8)
        lay.addLayout(self.results)
        self.save_btn = QPushButton("Save measurement")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self._save)
        lay.addWidget(self.save_btn)

        lay.addSpacing(6)
        lay.addWidget(label("SAVED ON THIS MAP", "Section"))
        self.saved_list = QListWidget()
        self.saved_list.setMinimumHeight(120)
        self.saved_list.itemDoubleClicked.connect(lambda _it: self._show_saved())
        lay.addWidget(self.saved_list)
        row = QHBoxLayout()
        sb = QPushButton("Show")
        sb.clicked.connect(self._show_saved)
        db = QPushButton("Delete")
        db.setObjectName("Danger")
        db.clicked.connect(self._delete_saved)
        eb = QPushButton("Export saved\u2026")
        eb.clicked.connect(self._export)
        self.m3d_btn = QPushButton("3D model for SketchUp\u2026")
        self.m3d_btn.setToolTip("Collada (.dae) with the drone photo, or the bare ground; STL for 3-D printing.")
        self.m3d_btn.clicked.connect(self._export_3d)
        for b in (sb, db):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(eb)
        lay.addLayout(row)
        row3 = QHBoxLayout()
        row3.addStretch(1)
        row3.addWidget(self.m3d_btn)
        lay.addLayout(row3)
        lay.addStretch(1)
        lay.addWidget(label("Measurements from a drone map are for planning and estimates. Without ground control "
                            "points, heights can be off by a few metres overall, but differences in height (and so "
                            "volumes) are much more reliable.", "Muted", wrap=True))
        sc.setWidget(w)
        return sc

    # ================================================================ jobs
    def enter(self):
        cur = self.source.currentData()
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItem("– choose a processed drone job –", None)
        seen = set()
        for d in self.s.settings.recent_jobs:
            if d in seen or not jobs.Job.is_job_dir(d):
                continue
            seen.add(d)
            try:
                j = jobs.Job.load(d)
            except Exception:
                continue
            files = j.summary.get("files", {})
            if any(k in files and os.path.exists(files[k]) for k in ("dtm", "dsm")):
                self.source.addItem(j.name, d)
        if cur:
            i = self.source.findData(cur)
            if i < 0:
                self.source.addItem(os.path.basename(cur), cur)
                i = self.source.count() - 1
            self.source.setCurrentIndex(i)
        self.source.blockSignals(False)
        if self.job is None and cur is None and self.source.count() > 1:
            self.source.setCurrentIndex(1)

    def use_job(self, job):
        i = self.source.findData(job.job_dir)
        if i < 0:
            self.source.addItem(job.name, job.job_dir)
            i = self.source.count() - 1
        if self.source.currentIndex() == i:
            self._source_changed(i)
        else:
            self.source.setCurrentIndex(i)

    def _browse_job(self):
        d = QFileDialog.getExistingDirectory(self, "Open a drone mapping job folder", self.s.settings.jobs_root)
        if not d:
            return
        if not jobs.Job.is_job_dir(d):
            QMessageBox.information(self, "Not a job folder", "That folder is not a drone mapping job (no job.json).")
            return
        self.use_job(jobs.Job.load(d))

    def _source_changed(self, _i):
        d = self.source.currentData()
        if not d:
            return
        try:
            job = jobs.Job.load(d)
        except Exception as e:
            QMessageBox.warning(self, "Cannot open job", str(e))
            return
        files = {k: v for k, v in job.summary.get("files", {}).items() if v and os.path.exists(v)}
        if not ("dtm" in files or "dsm" in files):
            QMessageBox.information(self, "No elevation model",
                                    "This job has no terrain or surface model. Process it with DTM/DSM turned on.")
            return
        self.job, self.files = job, files
        self.src_kind.blockSignals(True)
        for i in range(self.src_kind.count()):
            self.src_kind.model().item(i).setEnabled(self.src_kind.itemData(i) in files)
        if self.src_kind.currentData() not in files:
            self.src_kind.setCurrentIndex(self.src_kind.findData("dtm" if "dtm" in files else "dsm"))
        self.src_kind.blockSignals(False)
        self.saved = ms.load_all(job.job_dir)
        self._refresh_saved()
        self._load_view()
        self._clear()

    def _dem_path(self) -> str:
        return self.files.get(self.src_kind.currentData()) or self.files.get("dtm") or self.files.get("dsm")

    def _load_view(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.view = load_dem(self._dem_path(), max_size=2000)
        except Exception as e:
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Cannot read elevation", str(e))
            return
        self.bg_cache, self.contours = {}, None
        try:
            self._show_background()
        finally:
            QGuiApplication.restoreOverrideCursor()
        z = self.view.z
        if np.isfinite(z).any():
            self.readout.setText(f"{self.job.name}: heights {np.nanmin(z):.1f} – {np.nanmax(z):.1f} m. "
                                 f"Elevation model cells {self._native_cell():.2f} m.")

    def _native_cell(self) -> float:
        import rasterio
        try:
            with rasterio.open(self._dem_path()) as ds:
                return abs(ds.transform.a)
        except Exception:
            return float("nan")

    def _src_kind_changed(self, _i):
        if self.job is None:
            return
        self._load_view()
        if self.mode == "plan":
            self.plan.takeoff = None
            self.clear_overlay()
            if self.finished:
                self.plan.compute()
        elif self.finished:
            self._compute()

    # ================================================================ drawing
    def _show_background(self):
        if self.view is None:
            return
        mode = self.bg.currentIndex()
        if mode not in self.bg_cache:
            if mode == 0:
                self.bg_cache[0] = background_for(self.view, self.files.get("ortho"))
            elif mode == 1:
                self.bg_cache[1] = background_for(self.view, None)
            else:
                self.bg_cache[2] = slope_rgb(self.view)
        self.canvas.set_base(self.bg_cache[mode])
        if self.overlay is not None:
            self.canvas.set_overlay(self.overlay)
        self._draw()

    def _px(self, x, y):
        t = self.view.transform
        return (x - t.c) / t.a, (y - t.f) / t.e

    def _xy(self, sx, sy):
        t = self.view.transform
        return t.c + sx * t.a, t.f + sy * t.e

    def _draw(self):
        if self.view is None:
            return
        self.canvas.clear_vectors()
        if self.cont_cb.isChecked():
            if self.contours is None:
                z = self.view.z
                ok = np.isfinite(z)
                relief = float(np.nanmax(z) - np.nanmin(z)) if ok.any() else 1.0
                from ..core.gisexport import auto_interval
                self.cont_int = auto_interval(relief)
                self.contours = ex.contour_features(np.where(ok, z, 0), ok, self.view.transform, self.cont_int)
            for f in self.contours:
                xy = np.asarray(f.geometry.coords)
                c, r = self._px(xy[:, 0], xy[:, 1])
                major = f.properties["MAJOR"]
                self.canvas.add_polyline(list(zip(c, r)), "#5D4037" if major else "#8D6E63", 1.2 if major else 0.6)
        for m in (self.saved if self.mode == "measure" else []):
            px = [self._px(*p) for p in m.points]
            if m.kind == "point":
                self.canvas.add_marker(*px[0], m.name, SAVED, 5)
            else:
                self.canvas.add_polyline(px, SAVED, 1.6, closed=m.kind in ("area", "volume", "compare"),
                                         style=Qt.DashLine)
                self.canvas.add_marker(*px[0], m.name, SAVED, 3)
        if not self.pts:
            return
        kind = self.kind()
        px = [self._px(*p) for p in self.pts]
        closed = kind in ("area", "volume", "plan", "compare") and self.finished and not self.road_line_kind()
        if self.road_res is not None and self.mode == "plan":
            self._draw_road()
        fill = (245, 184, 65, 45) if closed and self.overlay is None else None
        curved = None
        if self.road_line_kind() and self.mode == "plan":
            if self.road_res is not None and self.road_res.centre is not None:
                curved = self.road_res.centre
            elif self.preview_al is not None and self.preview_al.curves:
                curved, _ = self.preview_al.densify(1.0)
        if curved is not None:
            self.canvas.add_polyline(px, "#9AA5BB", 1.0, style=Qt.DashLine)        # the clicked straight lines
            self.canvas.add_polyline([self._px(*q) for q in curved], SHAPE, 2.6)
        elif len(px) >= 2:
            self.canvas.add_polyline(px, SHAPE, 2.6, closed=closed, fill=fill)
        names = ["A", "B"] if kind == "height" else [""] * len(px)
        for (x, y), n in zip(px, names):
            self.canvas.add_marker(x, y, n, SHAPE, 4 if kind not in ("point", "height") else 6)

    # ================================================================ tools / mouse
    def kind(self) -> str:
        if self.mode == "plan":
            return "plan"
        return TOOLS[max(0, self.tool_group.checkedId())][0]

    # ---------------------------------------------------------------- plan mode
    def set_mode(self, m: str):
        self.mode_group.button(1 if m == "plan" else 0).setChecked(True)
        if m == self.mode:
            return
        self.mode = m
        self.right.setCurrentIndex(1 if m == "plan" else 0)
        for b in self.tool_group.buttons():
            b.setEnabled(m == "measure")
        self._reset_shape()
        self._draw()
        if m == "plan":
            self.plan.shape_changed()
            self.plan.go(self.plan.idx)
        else:
            self.canvas.set_pick_cursor(True)
            self._show_result()

    def set_shape(self, pts):
        self._reset_shape()
        self.pts = [tuple(map(float, p)) for p in pts]
        self.finished = True
        self._draw()
        if self.mode == "plan":
            self.plan.shape_changed()

    def show_overlay(self, res):
        self.overlay = self._overlay_for(res)
        self.canvas.set_overlay(self.overlay)
        self._draw()

    def clear_overlay(self):
        changed = self.overlay is not None or self.road_res is not None
        self.overlay = None
        self.road_res = None
        self.road_profile.setVisible(False)
        if changed:
            self.canvas.set_overlay(None)
            self._draw()

    # ---------------------------------------------------------------- road
    def road_line_kind(self) -> bool:
        return self.mode == "plan" and getattr(self, "plan", None) is not None and self.plan.kind == "road"

    def _needs(self, kind):
        return 2 if self.road_line_kind() else NEEDS[kind]

    def show_alignment(self, al):
        self.preview_al = al
        self._draw()

    def show_road(self, r):
        self.road_res = r
        self.road_profile.set_result(r)
        self.road_profile.setVisible(True)
        self._draw()

    def select_station(self, i):
        self.road_sel = i
        self.road_profile.set_selected(i)
        self._draw()

    def _draw_road(self):
        r = self.road_res
        st = r.stations
        for a, b in zip(st[:-1], st[1:]):
            cut = (a.cut_area + b.cut_area) >= (a.fill_area + b.fill_area)
            col = (196, 40, 32, 95) if cut else (30, 90, 170, 95)
            poly = [self._px(*a.catch_xy(-1)), self._px(*b.catch_xy(-1)), self._px(*b.catch_xy(1)),
                    self._px(*a.catch_xy(1))]
            self.canvas.add_polyline(poly, col[:3], 0.5, closed=True, fill=col)
        regular = [i for i, s_ in enumerate(st) if s_.kind == ""]
        every = max(1, len(regular) // 10)
        show = set(regular[::every]) | {len(st) - 1}
        for i, s_ in enumerate(st):
            if i in show:
                self.canvas.add_marker(*self._px(s_.x, s_.y), s_.label, "#0E1B33", 3)
        k = getattr(self, "road_sel", 0)
        if 0 <= k < len(st):
            s_ = st[k]
            self.canvas.add_polyline([self._px(*s_.catch_xy(-1)), self._px(*s_.catch_xy(1))], "#FFFFFF", 3.0)
            self.canvas.add_marker(*self._px(s_.x, s_.y), s_.label, "#FFFFFF", 6)

    def load_plan(self, path: str):
        try:
            plan = ew.load_plan(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Cannot open plan", f"{os.path.basename(path)}: {e}")
            return
        job_dir = plan.get("job_dir", "")
        if not jobs.Job.is_job_dir(job_dir):          # job folder moved: the plan sits in <job>/Earthworks
            job_dir = os.path.dirname(os.path.dirname(os.path.abspath(path)))
        if not jobs.Job.is_job_dir(job_dir):
            QMessageBox.warning(self, "Drone map missing", "The drone mapping job of this plan was not found.")
            return
        self.use_job(jobs.Job.load(job_dir))
        if self.job is None:
            return
        self.set_mode("plan")
        self.plan.load(plan, path)

    def _tool_changed(self, i):
        key, _b, title, hint = TOOLS[i]
        self.tool_title.setText(title)
        self.tool_hint.setText(hint)
        self.vol_box.setVisible(key == "volume")
        self.cmp_box.setVisible(key == "compare")
        if key == "compare":
            self._fill_compare_jobs()
        self.finish_btn.setVisible(key in ("path", "area", "volume"))
        self.canvas.set_pick_cursor(True)
        self._clear()

    def _move(self, sx, sy):
        if self.view is None:
            return
        r, c = int(sy), int(sx)
        h, w = self.view.shape
        if not (0 <= r < h and 0 <= c < w):
            return
        x, y = self._xy(sx, sy)
        z = self.view.z[r, c]
        txt = f"E {x:,.1f}   N {y:,.1f}" + (f"   height ≈ {z:.2f} m" if np.isfinite(z) else "   no height data")
        if self.pts and not self.finished and self.kind() != "point":
            pts = self.pts + [(x, y)]
            seg = sum(math.dist(a, b) for a, b in zip(pts[:-1], pts[1:]))
            txt += f"   ·   length {seg:,.1f} m"
            if self.kind() in ("area", "volume") and len(pts) >= 3:
                from shapely.geometry import Polygon
                txt += f"   area ≈ {fmt_area(Polygon(pts).area)}"
        self.readout.setText(txt)

    def _click(self, sx, sy):
        if self.view is None or (self.task is not None and self.task.isRunning()):
            return
        if self.mode == "plan" and not self.plan.drawing_allowed():
            self.readout.setText("To change the outline, go back to the step “Draw the area”.")
            return
        p = self._xy(sx, sy)
        kind = self.kind()
        if self.finished or kind == "point":
            self._reset_shape()
        self.pts.append(p)
        if len(self.pts) >= self._needs(kind) and kind in ("point", "height"):
            self.finished = True
            self._compute()
        self._draw()
        if self.mode == "plan":
            self.plan.shape_changed()

    def _dclick(self, _sx, _sy):
        self._finish()

    def _finish(self):
        kind = self.kind()
        if self.finished or kind in ("point", "height"):
            return
        # a double-click adds the same point twice: drop near-duplicates
        cleaned = []
        for p in self.pts:
            if not cleaned or math.dist(p, cleaned[-1]) > 1e-3:
                cleaned.append(p)
        self.pts = cleaned
        if len(self.pts) < self._needs(kind):
            self.readout.setText(f"Click at least {self._needs(kind)} points first.")
            return
        self.finished = True
        self._draw()
        if self.mode == "plan":
            self.plan.shape_changed()
            return
        self._compute()

    def _undo(self):
        if self.mode == "plan" and not self.plan.drawing_allowed():
            return
        if self.pts and not self.finished:
            self.pts.pop()
            self._draw()
        elif self.pts and self.mode == "plan":       # reopen a closed outline to edit its last corner
            self.finished = False
            self.clear_overlay()
            self._draw()
        if self.mode == "plan":
            self.plan.shape_changed()

    def _reset_shape(self):
        self.pts, self.finished, self.result, self.result_kind = [], False, None, None
        if self.overlay is not None:
            self.overlay = None
            self.canvas.set_overlay(None)
        self.road_res = None
        self.road_sel = 0
        self.preview_al = None
        if hasattr(self, "road_profile"):
            self.road_profile.setVisible(False)

    def _clear(self):
        self._reset_shape()
        self._draw()
        self._show_result()
        if getattr(self, "plan", None) is not None and self.mode == "plan":
            self.plan.shape_changed()

    def _fill_compare_jobs(self):
        cur = self.cmp_job.currentData()
        self.cmp_job.blockSignals(True)
        self.cmp_job.clear()
        for i in range(self.source.count()):
            d = self.source.itemData(i)
            if d and (self.job is None or os.path.normcase(d) != os.path.normcase(self.job.job_dir)):
                self.cmp_job.addItem(self.source.itemText(i), d)
        if self.cmp_job.count() == 0:
            self.cmp_job.addItem("\u2013 no other processed drone map \u2013", None)
        i = self.cmp_job.findData(cur)
        self.cmp_job.setCurrentIndex(max(0, i))
        self.cmp_job.blockSignals(False)

    def _other_dem(self) -> str | None:
        d = self.cmp_job.currentData()
        if not d:
            return None
        try:
            files = jobs.Job.load(d).summary.get("files", {})
        except Exception:
            return None
        k = self.src_kind.currentData()
        return files.get(k) or files.get("dtm") or files.get("dsm")

    def _ref_changed(self):
        if self.ref_group.checkedButton().property("ref") == "level" and self.result is not None \
                and self.level.value() == 0 and isinstance(self.result, ms.VolumeResult):
            self.level.blockSignals(True)
            self.level.setValue(round(self.result.z_mean, 2))
            self.level.blockSignals(False)
        if self.finished and self.kind() in ("volume", "compare"):
            self.rerun.start()

    # ================================================================ compute
    def _compute(self):
        if not self.finished or self.view is None:
            return
        if self.task is not None and self.task.isRunning():
            self.rerun.start()
            return
        kind, pts, path = self.kind(), list(self.pts), self._dem_path()
        ref = self.ref_group.checkedButton().property("ref")
        level = self.level.value()
        if kind == "volume" and ref == "level" and level == 0:
            # first use: start from the average height inside, the user then types the planned level
            a = ms.measure_area(path, pts)
            self.level.blockSignals(True)
            self.level.setValue(round(a.z_mean, 2))
            self.level.blockSignals(False)
            level = self.level.value()

        other = self._other_dem() if kind == "compare" else None
        if kind == "compare" and not other:
            self._failed("Choose another processed drone map of the same place to compare with.")
            return
        later_here = self.cmp_order.currentData() == "later"
        line_up = self.cmp_lineup.isChecked()

        def work(_t):
            if kind == "compare":
                return ms.compare_surveys(other if later_here else path, path if later_here else other, pts,
                                          line_up)
            if kind == "point":
                return ms.point_elevation(path, *pts[0])
            if kind == "height":
                return ms.height_between(path, pts[0], pts[1])
            if kind == "path":
                return ms.measure_path(path, pts)
            if kind == "area":
                return ms.measure_area(path, pts)
            return ms.measure_volume(path, pts, ref, level)
        self._busy_text("Measuring…")
        self.task = Task(work, self)
        self.task.done.connect(lambda res, k=kind: self._computed(k, res))
        self.task.failed.connect(self._failed)
        self.task.start()

    def _busy_text(self, text):
        clear_layout(self.results)
        self.results.addWidget(label(text, "Muted"))

    def _failed(self, msg):
        self.result = None
        clear_layout(self.results)
        self.results.addWidget(IssueBox("warning", "Could not measure here", msg))
        self.save_btn.setEnabled(False)

    def _computed(self, kind, res):
        if kind != self.kind() or not self.finished:
            return
        self.result, self.result_kind = res, kind
        if isinstance(res, ms.VolumeResult) and res.dz is not None:
            self.overlay = self._overlay_for(res)
            self.canvas.set_overlay(self.overlay)
        if isinstance(res, ms.CompareResult):
            from types import SimpleNamespace
            self.overlay = self._overlay_for(SimpleNamespace(dz=-res.dz, transform=res.transform))   # red = lowered
            self.canvas.set_overlay(self.overlay)
        self._draw()
        self._show_result()

    def _overlay_for(self, res):
        """Cut/fill colours resampled onto the map display grid."""
        from rasterio.warp import Resampling, reproject
        import rasterio
        out = np.full(self.view.shape, np.nan)
        crs = rasterio.crs.CRS.from_wkt(self.view.crs_wkt)
        reproject(res.dz, out, src_transform=res.transform, src_crs=crs, dst_transform=self.view.transform,
                  dst_crs=crs, src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.average)
        return ms.overlay_rgba(out)

    # ================================================================ results
    def _grid(self, cards):
        g = QGridLayout()
        g.setSpacing(8)
        for i, c in enumerate(cards):
            g.addWidget(c, i // 2, i % 2)
        self.results.addLayout(g)

    def _show_result(self):
        clear_layout(self.results)
        res, kind = self.result, self.result_kind
        self.save_btn.setEnabled(res is not None)
        if res is None:
            if self.view is not None:
                self.results.addWidget(label("Results appear here after you finish the shape.", "Muted", wrap=True))
            return
        if kind == "point":
            x, y = self.pts[0]
            self._grid([StatCard("Height", fmt_m(res), f"E {x:,.2f}\nN {y:,.2f}")])
        elif kind == "height":
            r = res
            self._grid([StatCard("Height at A", fmt_m(r.z_a)), StatCard("Height at B", fmt_m(r.z_b)),
                        StatCard("B is higher by" if r.rise >= 0 else "B is lower by", fmt_m(abs(r.rise))),
                        StatCard("Horizontal distance", fmt_m(r.horizontal), f"straight line {fmt_m(r.straight)}"),
                        StatCard("Slope", f"{abs(r.slope_pct):.1f} %", f"{abs(r.slope_deg):.1f}°"
                                 + (f" · 1 in {abs(1 / (r.slope_pct / 100)):.0f}" if r.slope_pct else ""))])
        elif kind == "compare":
            r = res
            cards = [StatCard("Lowered (removed)", f"{r.lowered_m3:,.0f} m³",
                              f"over {fmt_area(r.lowered_area)}, deepest {r.max_lower:.2f} m"),
                     StatCard("Raised (added)", f"{r.raised_m3:,.0f} m³",
                              f"over {fmt_area(r.raised_area)}, highest {r.max_raise:.2f} m"),
                     StatCard("Net change", f"{r.net_m3:+,.0f} m³", "more material now" if r.net_m3 >= 0
                              else "less material now"),
                     StatCard("Area", fmt_area(r.area.area_2d), f"{r.area.area_2d / 1e4:.3f} ha")]
            cards[0].t.setStyleSheet("color: #9C2A22;")
            cards[1].t.setStyleSheet("color: #1E4E86;")
            self._grid(cards)
            if self.cmp_lineup.isChecked():
                self.results.addWidget(label(f"The later survey was shifted {-r.offset:+.2f} m to line up with the "
                                             f"earlier one on {r.offset_points:,} cells of ground around the area.",
                                             "Hint", wrap=True))
                if abs(r.offset) > 3:
                    self.results.addWidget(IssueBox("warning", "Large height difference between the surveys",
                                                    f"The two maps differ by {r.offset:.1f} m overall. Check that "
                                                    "they show the same place and the same kind of model."))
            self.results.addWidget(label("Red on the map = ground lower now (dug, eroded, hauled away); blue = "
                                         "higher now (filled, piled, deposited).", "Hint", wrap=True))
            self._gaps(r.no_data_pct)
        elif kind == "path":
            r = res
            self._grid([StatCard("Length", fmt_m(r.length_2d, 1), "flat, as on a map"),
                        StatCard("Along the ground", fmt_m(r.length_3d, 1), "following ups and downs"),
                        StatCard("Start → end", f"{r.z_start:.1f} → {r.z_end:.1f} m",
                                 f"change {r.z_end - r.z_start:+.2f} m"),
                        StatCard("Lowest / highest", f"{r.z_min:.1f} / {r.z_max:.1f} m"),
                        StatCard("Climb / descent", f"{r.climb:.1f} / {r.descent:.1f} m",
                                 "every rise and fall added up"),
                        StatCard("Steepest part", f"{r.steepest_pct:.0f} %", f"over {ms.STEEP_BASE:g} m")])
            self.results.addWidget(label("Side view along the path", "H2"))
            ch = ProfileChart()
            ch.set_data(r.dist, r.z)
            self.results.addWidget(ch)
            self._gaps(r.no_data_pct)
        else:
            r = res
            cards = [StatCard("Perimeter", fmt_m(r.perimeter_2d, 1), f"along the ground {fmt_m(r.perimeter_3d, 1)}"),
                     StatCard("Flat area", fmt_area(r.area_2d), f"{r.area_2d / 1e4:.3f} ha"),
                     StatCard("Surface area", fmt_area(r.area_3d), "following the slopes"),
                     StatCard("Heights inside", f"{r.z_min:.1f} – {r.z_max:.1f} m", f"average {r.z_mean:.2f} m")]
            if kind == "volume":
                cards += [StatCard("Dig (cut)", f"{r.cut_m3:,.0f} m³", f"over {fmt_area(r.cut_area)}, "
                                   f"deepest {r.max_cut:.2f} m"),
                          StatCard("Fill", f"{r.fill_m3:,.0f} m³", f"over {fmt_area(r.fill_area)}, "
                                   f"deepest {r.max_fill:.2f} m"),
                          StatCard("Net", f"{r.net_m3:,.0f} m³", "extra soil" if r.net_m3 >= 0 else "soil needed"),
                          StatCard("Total moved", f"{r.total_m3:,.0f} m³", "cut + fill")]
                for c, col in ((cards[4], "#9C2A22"), (cards[5], "#1E4E86")):
                    c.t.setStyleSheet(f"color: {col};")
            self._grid(cards)
            if kind == "volume":
                self.results.addWidget(label(f"Measured from {r.reference_text}. Red on the map = dig, "
                                             f"blue = fill.", "Hint", wrap=True))
                self._trucks(r)
                if self.src_kind.currentData() == "dtm":
                    self.results.addWidget(IssueBox(
                        "info", "Measuring a pile or a building?",
                        "Switch “Heights from” to “Ground + trees, buildings, piles (DSM)”. The bare-ground "
                        "model may have removed it."))
            self._gaps(r.no_data_pct)
            if r.cell > self._native_cell() * 1.01:
                self.results.addWidget(label(f"Large area: measured on {r.cell:.2f} m cells to stay fast.",
                                             "Hint", wrap=True))

    def _trucks(self, r):
        sizes = list(ms.TRUCKS) + ([self.truck.value()] if self.truck.value() > 0 else [])
        net = r.net_m3
        loose, trips = ms.truck_trips(abs(net), self.swell.value(), sizes)
        box = QWidget()
        box.setObjectName("Trucks")
        box.setStyleSheet(f"QWidget#Trucks {{ background: {theme.INFO_SOFT}; border-radius: 8px; }}"
                          "QLabel { background: transparent; }")
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)
        what = "Hauling the extra soil away" if net >= 0 else "Bringing in soil to fill"
        v.addWidget(label(what, "H2"))
        v.addWidget(label(f"{abs(net):,.0f} m³ in the ground ≈ {loose:,.0f} m³ loose "
                          f"(+{self.swell.value():.0f}%).", "", wrap=True))
        v.addWidget(label("   ·   ".join(f"~{n:,} trips with {s:g} m³ trucks" for s, n in trips), "", wrap=True))
        self.results.addWidget(box)

    def _gaps(self, pct):
        if pct > 2:
            self.results.addWidget(IssueBox("warning", f"{pct:.0f}% has no height data",
                                            "Part of the shape is outside the drone map or in a gap. Results only "
                                            "count the part with data."))

    # ================================================================ saving
    def _save(self):
        if self.result is None or self.job is None:
            return
        kind = self.result_kind
        n = sum(1 for m in self.saved if m.kind == kind) + 1
        name, ok = QInputDialog.getText(self, "Save measurement", "Name:", text=f"{ms.KINDS[kind]} {n}")
        if not ok or not name.strip():
            return
        settings = {}
        if kind == "compare":
            settings = {"compared_with": self.cmp_job.currentText(), "this_map_is": self.cmp_order.currentData(),
                        "line_up": self.cmp_lineup.isChecked()}
        if kind == "volume":
            settings = {"reference": self.result.reference, "level": self.level.value(),
                        "swell_pct": self.swell.value()}
        res_d = ms.results_dict(kind, self.result, self.swell.value())
        if kind == "compare":
            res_d["compared_with"] = self.cmp_job.currentText()
        m = ms.Measurement(kind, name.strip(), [list(p) for p in self.pts], self.src_kind.currentData(), settings,
                           res_d)
        self.saved.append(m)
        self._persist()
        self._draw()

    def _persist(self):
        try:
            ms.save_all(self.job.job_dir, self.saved)
        except OSError as e:
            QMessageBox.warning(self, "Could not save", f"The measurements could not be saved in the job folder:\n{e}")
        self._refresh_saved()

    def _refresh_saved(self):
        self.saved_list.clear()
        for m in self.saved:
            it = QListWidgetItem(f"{m.name}   –   {ms.KINDS.get(m.kind, m.kind)} · {m.summary()}")
            it.setData(Qt.UserRole, m.id)
            self.saved_list.addItem(it)
        if not self.saved:
            it = QListWidgetItem("Nothing saved yet.")
            it.setFlags(Qt.NoItemFlags)
            self.saved_list.addItem(it)

    def _selected_saved(self):
        it = self.saved_list.currentItem()
        mid = it.data(Qt.UserRole) if it else None
        return next((m for m in self.saved if m.id == mid), None)

    def _show_saved(self):
        m = self._selected_saved()
        if m is None:
            return
        i = next(k for k, t in enumerate(TOOLS) if t[0] == m.kind)
        self.tool_group.button(i).setChecked(True)
        self._tool_changed(i)
        if m.source in self.files and m.source != self.src_kind.currentData():
            self.src_kind.setCurrentIndex(self.src_kind.findData(m.source))
        st = m.settings or {}
        if m.kind == "volume":
            for b in self.ref_group.buttons():
                if b.property("ref") == st.get("reference", "edge"):
                    b.setChecked(True)
            self.level.blockSignals(True)
            self.level.setValue(float(st.get("level", 0)))
            self.level.blockSignals(False)
            self.swell.setValue(float(st.get("swell_pct", ms.DEFAULT_SWELL)))
        self.pts = [tuple(p) for p in m.points]
        self.finished = True
        self._draw()
        self._compute()
        c = np.mean([self._px(*p) for p in self.pts], axis=0)
        self.canvas.centerOn(QPointF(*c))

    def _delete_saved(self):
        m = self._selected_saved()
        if m is None:
            return
        if QMessageBox.question(self, "Delete measurement", f"Delete “{m.name}”?") != QMessageBox.Yes:
            return
        self.saved = [x for x in self.saved if x.id != m.id]
        self._persist()
        self._draw()

    def _export_3d(self):
        if self.job is None:
            return
        opts = _model3d_dialog(self)
        if not opts:
            return
        from shapely.geometry import Polygon
        from PySide6.QtWidgets import QProgressDialog
        from ..core import model3d
        poly = Polygon(self.pts) if opts["shape"] else None
        bounds = poly.bounds if poly is not None else None
        result = {}
        files, name = dict(self.files), self.job.name

        def work(tk):
            try:
                result["files"] = model3d.export_model(files, opts["kind"], opts["folder"], name, bounds, poly,
                                                       opts["detail"], opts["formats"], progress=tk.report)
            except Exception as e:  # shown below
                result["error"] = str(e) or e.__class__.__name__
        dlg = QProgressDialog("Making the 3D model\u2026", None, 0, 100, self)
        dlg.setWindowTitle("3D model")
        dlg.setMinimumDuration(0)
        task = Task(work, self)
        task.progress.connect(lambda pc, tx: (dlg.setValue(int(pc)), dlg.setLabelText(tx)))
        task.finished.connect(dlg.close)
        self._m3d_task = task
        task.start()
        dlg.exec()
        task.wait()
        if "error" in result:
            QMessageBox.warning(self, "3D model failed", result["error"])
            return
        if QMessageBox.question(self, "3D model ready", f"{len(result.get('files', []))} files written to:\n"
                                f"{opts['folder']}\n\nOpen the folder?") == QMessageBox.Yes:
            system.open_path(opts["folder"])

    def _export(self):
        if not self.saved:
            QMessageBox.information(self, "Nothing to export", "Save at least one measurement first.")
            return
        import rasterio
        with rasterio.open(self._dem_path()) as ds:
            epsg = ds.crs.to_epsg()
        d = SimpleExportDialog("Export measurements", [],
                               [("Measurements (points, lines, areas)", ["gpkg", "shp", "geojson", "kml", "dxf"],
                                 ["gpkg", "kml"])],
                               os.path.join(self.job.job_dir, "Measurements"), dem_lonlat(self.view),
                               default_crs=f"EPSG:{epsg}" if epsg else "EPSG:4326", parent=self)
        if not d.exec():
            return
        fmts = next(iter(d.selection().values()), [])
        try:
            files = ms.export_measurements(self.saved, d.folder.path(), f"{self.job.name}_measurements",
                                           self.view.crs_wkt, d.crs.code(), fmts, dem_lonlat(self.view),
                                           f"Measurements - {self.job.name}")
        except Exception as e:
            QMessageBox.warning(self, "Export failed", str(e))
            return
        if QMessageBox.question(self, "Export finished", f"{len(files)} files written (including a CSV table)."
                                "\n\nOpen the folder?") == QMessageBox.Yes:
            system.open_path(d.folder.path())


def _model3d_dialog(page):
    """Ask what 3-D model to make. Returns a dict or None."""
    from PySide6.QtWidgets import QDialog, QDialogButtonBox
    from .export_widgets import FolderPicker
    d = QDialog(page)
    d.setWindowTitle("3D model for SketchUp")
    d.setMinimumWidth(560)
    v = QVBoxLayout(d)
    v.addWidget(label("Which model?", "H2"))
    photo = QRadioButton("Photo model: looks like the site (surface with trees and buildings, drone photo on top)")
    ground = QRadioButton("Clean ground model: bare ground, smooth and light \u2013 good to design on")
    photo.setChecked("ortho" in page.files and "dsm" in page.files)
    ground.setChecked(not photo.isChecked())
    photo.setEnabled("dsm" in page.files or "dtm" in page.files)
    ground.setEnabled("dtm" in page.files or "dsm" in page.files)
    for w_ in (photo, ground):
        v.addWidget(w_)
    v.addWidget(label("Which area?", "H2"))
    whole = QRadioButton("The whole drone map")
    shape = QRadioButton("Only inside the outline drawn on the map")
    has_shape = page.finished and len(page.pts) >= 3 and not page.road_line_kind() and page.kind() not in (
        "point", "height", "path")
    shape.setEnabled(has_shape)
    (shape if has_shape else whole).setChecked(True)
    v.addWidget(whole)
    v.addWidget(shape)
    row = QHBoxLayout()
    row.addWidget(QLabel("Detail"))
    det = QComboBox()
    det.addItem("Light (about 100,000 triangles): fast in SketchUp", "light")
    det.addItem("Normal (about 300,000 triangles)", "normal")
    det.addItem("Detailed (about 800,000 triangles): needs a strong computer", "detailed")
    det.setCurrentIndex(1)
    row.addWidget(det, 1)
    v.addLayout(row)
    dae = QCheckBox("Collada .dae for SketchUp (File \u2192 Import), with the photo")
    dae.setChecked(True)
    stl = QCheckBox("STL (no photo; for 3-D printing and other programs)")
    v.addWidget(dae)
    v.addWidget(stl)
    v.addWidget(label("Save to folder", "H2"))
    folder = FolderPicker(os.path.join(page.job.job_dir, "3D"))
    v.addWidget(folder)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.button(QDialogButtonBox.Ok).setText("Make the 3D model")
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    v.addWidget(bb)
    if not d.exec():
        return None
    fm = [f for f, cbx in (("dae", dae), ("stl", stl)) if cbx.isChecked()]
    if not fm:
        return None
    return {"kind": "photo" if photo.isChecked() else "ground", "shape": shape.isChecked(),
            "detail": det.currentData(), "formats": fm, "folder": folder.path()}
