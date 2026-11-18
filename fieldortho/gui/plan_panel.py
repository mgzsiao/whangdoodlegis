"""Plan earthworks: a guided assistant for levelling a lot / pad, or measuring a pile, hole or deposit.

Lives in the right-hand panel of the Measure & earthworks screen; the map (drawing the outline, the
cut/fill colours) belongs to the page.
"""
from __future__ import annotations

import json
import math
import os

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QSlider,
                               QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton, QRadioButton,
                               QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget, QAbstractItemView, QHeaderView)

from ..core import activity, alignment as alm, crs as crsmod, earthworks as ew, measure as ms, road as rdm, system
from ..core import vertical as vt
from ..core.settings import config_dir
from . import road_parts as rp, theme
from .export_widgets import CrsPicker, FolderPicker, FormatChecks
from .pages_setup import ChoiceCard, clear_layout
from .road_widgets import SectionView, TemplatePreview
from .widgets import IssueBox, StatCard, label
from .workers import Task

PREFS = "earthworks_prefs.json"


def _prefs() -> dict:
    try:
        with open(os.path.join(config_dir(), PREFS), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_prefs(d: dict):
    try:
        with open(os.path.join(config_dir(), PREFS), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
    except OSError:
        pass


def spin(lo, hi, val, suffix, dec=1, step=0.1, width=110):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setSingleStep(step)
    s.setValue(val)
    s.setSuffix(suffix)
    s.setFixedWidth(width)
    return s


def radio_block(group: QButtonGroup, key: str, title: str, text: str, lay: QVBoxLayout) -> QRadioButton:
    rb = QRadioButton(title)
    rb.setProperty("key", key)
    rb.setStyleSheet("font-weight: 600;")
    group.addButton(rb)
    lay.addWidget(rb)
    h = label(text, "Hint", wrap=True)
    h.setContentsMargins(24, 0, 0, 6)
    lay.addWidget(h)
    return rb


def explain(text: str) -> QLabel:
    lab = label(text, "", wrap=True)
    lab.setStyleSheet(f"background: {theme.INFO_SOFT}; border-radius: 8px; padding: 8px 10px; font-size: 12px;")
    return lab


class TypePointsDialog(QDialog):
    """Paste or type the corners of the outline."""

    def __init__(self, default_crs: str, lonlat, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Type the corners")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.addWidget(label("One corner per line, in order around the area. Copy them from a survey plan, a title, "
                            "a spreadsheet or a GPS.", "", wrap=True))
        lay.addWidget(label("Coordinate system of the numbers", "H2"))
        self.crs = CrsPicker(default=default_crs, lonlat=lonlat)
        lay.addWidget(self.crs)
        row = QHBoxLayout()
        row.addWidget(QLabel("Each line is"))
        self.order = QComboBox()
        self.order.addItem("Easting, Northing  (or Longitude, Latitude)", False)
        self.order.addItem("Northing, Easting  (or Latitude, Longitude)", True)
        row.addWidget(self.order, 1)
        lay.addLayout(row)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("699512.40, 980233.10\n699540.00, 980231.75\n699541.20, 980205.00\n"
                                     "699513.00, 980206.30\n\nor degrees:  124°47'10\"E, 8°52'31\"N")
        self.text.setStyleSheet(f"background: {theme.INPUT_BG}; color: {theme.INK};")
        self.text.setMinimumHeight(170)
        lay.addWidget(self.text)
        self.msg = label("", "Error", wrap=True)
        lay.addWidget(self.msg)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.points = None
        self.dst_wkt = ""
        self.lonlat = lonlat

    def _ok(self):
        code = self.crs.code()
        if not code:
            self.msg.setText("Choose the coordinate system of the numbers.")
            return
        try:
            self.points = ew.parse_points(self.text.toPlainText(), code, self.dst_wkt, self.order.currentData(),
                                          self.lonlat)
        except Exception as e:
            self.msg.setText(str(e))
            return
        if len(self.points) < 2:
            self.msg.setText("Type at least two points (three corners for an area).")
            return
        self.accept()


class ExportDialog(QDialog):
    def __init__(self, plan_name: str, default_crs: str, lonlat, folder: str, parent=None, road: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Report and files for engineers")
        self.setMinimumWidth(640)
        p = _prefs()
        lay = QVBoxLayout(self)
        lay.addWidget(label("A PDF report, an Excel workbook, spot levels and GIS / CAD files, all in one folder.",
                            "", wrap=True))
        f = QFormLayout()
        self.project = QLineEdit(plan_name)
        self.location = QLineEdit(p.get("location", ""))
        self.location.setPlaceholderText("e.g. Barangay Poblacion, Talakag, Bukidnon")
        self.by = QLineEdit(p.get("prepared_by", ""))
        self.by.setPlaceholderText("Name and office")
        f.addRow("Project", self.project)
        f.addRow("Location", self.location)
        f.addRow("Prepared by", self.by)
        self.spacing = spin(1, 50, float(p.get("spacing", 5.0)), " m", 1, 1.0)
        self.spacing.setToolTip("Spot levels (ground, finished, cut/fill depth) are listed on a grid this far apart.")
        f.addRow("Spot levels every", self.spacing)
        lay.addLayout(f)
        lay.addWidget(label("Coordinate system for the files", "H2"))
        self.crs = CrsPicker(default=default_crs, lonlat=lonlat)
        lay.addWidget(self.crs)
        lay.addWidget(label("GIS / CAD files", "H2"))
        self.fmts = FormatChecks(["gpkg", "shp", "geojson", "kml", "dxf"], True,
                                 p.get("formats", ["gpkg", "kml", "dxf"]))
        lay.addWidget(self.fmts)
        self.rasters = QCheckBox("Cut/fill depth and finished ground as GeoTIFF, colour map as PNG")
        self.rasters.setChecked(True)
        lay.addWidget(self.rasters)
        if road:
            self.rasters.setVisible(False)
            self.spacing.setVisible(False)
            f.labelForField(self.spacing).setVisible(False)
        lay.addWidget(label("Save to folder", "H2"))
        self.folder = FolderPicker(folder)
        lay.addWidget(self.folder)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Make report and files")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _ok(self):
        if not self.crs.code():
            QMessageBox.warning(self, "Coordinate system", "Choose a valid coordinate system.")
            return
        _save_prefs({"location": self.location.text(), "prepared_by": self.by.text(),
                     "spacing": self.spacing.value(), "formats": self.fmts.selected()})
        self.accept()


class PlanPanel(QWidget):
    STEPS = {
        "pad": ["type", "draw", "height", "edges", "soil", "result"],
        "pile": ["type", "draw", "ref", "soil", "result"],
        # 1.2: the road shape is asked part by part (guided steps), before the curves that depend on it
        "road": ["type", "draw", "std", *rp.PART_STEPS, "curves", "stations", "rise", "vcurves", "soil", "result"],
    }
    TITLES = {"type": "What are you planning?", "draw": "Draw the area", "height": "How high should the ground be?",
              "edges": "What happens at the edges?", "ref": "Measure from what?", "soil": "Soil and trucks",
              "result": "Results", "stations": "How often should we check the ground?",
              "rise": "How does the road sit on the ground?", "curves": "Curves: smooth the bends",
              "vcurves": "Smooth the grade changes?", **rp.TITLES}

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.kind = "pad"
        self.idx = 0
        self.takeoff: ew.Takeoff | None = None
        self.plan_path = ""
        self.name = ""
        self.task = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.compute)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        w = QWidget()
        w.setObjectName("Page")
        w.setStyleSheet(f"QWidget#Page {{ background: {theme.CARD}; }}")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        self.step_label = label("", "Section")
        lay.addWidget(self.step_label)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        lay.addWidget(self.bar)
        self.done_box = label("", "Muted", wrap=True)
        self.done_box.setStyleSheet(f"background: {theme.SUBTLE}; border-radius: 8px; padding: 8px 10px; "
                                    f"color: {theme.MUTED}; font-size: 12px;")
        lay.addWidget(self.done_box)
        self.title = label("", "H1")
        self.title.setStyleSheet("font-size: 18px; font-weight: 600;")
        lay.addWidget(self.title)
        self.stack = QStackedWidget()
        self.pages = {}
        self._make_shape_widgets()
        for key, fn in (("type", self._p_type), ("draw", self._p_draw), ("height", self._p_height),
                        ("edges", self._p_edges), ("ref", self._p_ref), ("std", self._p_std),
                        *((k, lambda l_, k_=k: self._p_part(k_, l_)) for k in rp.PART_STEPS),
                        ("curves", self._p_curves), ("stations", self._p_stations),
                        ("rise", self._p_rise), ("vcurves", self._p_vcurves), ("soil", self._p_soil),
                        ("result", self._p_result)):
            pw = QWidget()
            pl = QVBoxLayout(pw)
            pl.setContentsMargins(0, 0, 0, 0)
            pl.setSpacing(8)
            fn(pl)
            pl.addStretch(1)
            self.pages[key] = pw
            self.stack.addWidget(pw)
        lay.addWidget(self.stack)
        self.estimate = label("", "", wrap=True)
        self.estimate.setStyleSheet(f"border: 1px solid {theme.LINE}; border-radius: 8px; padding: 8px 10px;")
        lay.addWidget(self.estimate)
        lay.addStretch(1)
        sc.setWidget(w)
        outer.addWidget(sc, 1)
        foot = QHBoxLayout()
        foot.setContentsMargins(16, 8, 16, 10)
        self.back_btn = QPushButton("‹  Back")
        self.back_btn.clicked.connect(self.back)
        self.change_btn = QPushButton("Change dimensions")
        self.change_btn.setToolTip("Type your own values for this part of the road.")
        self.change_btn.clicked.connect(self._toggle_change)
        self.next_btn = QPushButton("Next  ›")
        self.next_btn.setObjectName("Primary")
        self.next_btn.setMinimumWidth(170)
        self.next_btn.clicked.connect(self.next)
        foot.addWidget(self.back_btn)
        foot.addStretch(1)
        foot.addWidget(self.change_btn)
        foot.addWidget(self.next_btn)
        outer.addLayout(foot)
        self._choose_type("pad")
        self.go(0)

    # ================================================================ step pages
    def _p_type(self, lay):
        lay.addWidget(label("Choose one. You can go back and change it later.", "Hint", wrap=True))
        self.cards = {}
        for key, title, text in (
                ("pad", "Level a lot or building pad",
                 "Make the ground flat (or gently sloped for rain) at a chosen height. Shows how much to cut and "
                 "fill, the sides, and the trucks needed."),
                ("pile", "Measure a pile, hole or landslide deposit",
                 "How much soil, gravel or debris is there, or how much is needed to fill a hole.")):
            c = ChoiceCard(key, title, text)
            c.clicked.connect(self._choose_type)
            lay.addWidget(c)
            self.cards[key] = c
        c = ChoiceCard("road", "Road, path or canal",
                       "Draw the line it follows. The app checks the ground every 5\u201320 m, sets the road height, "
                       "builds the cross-sections from your road shape and adds up the cut and fill.")
        c.clicked.connect(self._choose_type)
        lay.addWidget(c)
        self.cards["road"] = c

    def _p_draw(self, lay):
        self.draw_text = label("", "", wrap=True)
        lay.addWidget(self.draw_text)
        row = QHBoxLayout()
        for text, fn in (("Undo last corner", self.page._undo), ("Start again", self.page._clear)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(label("Or bring in the outline:", "Hint"))
        row = QHBoxLayout()
        b = QPushButton("From a file…")
        b.setToolTip("KML / KMZ (Google Earth), GPX (GPS), Shapefile, GeoPackage, GeoJSON or DXF (CAD).")
        b.clicked.connect(self._import_file)
        row.addWidget(b)
        b = QPushButton("Type the corners…")
        b.clicked.connect(self._type_points)
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(label("Heights from", "H2"))
        self.src = QComboBox()
        self.src.addItem("Bare ground (DTM): for lots, yards and building pads", "dtm")
        self.src.addItem("Ground + trees, buildings, piles (DSM): for stockpiles", "dsm")
        self.src.currentIndexChanged.connect(self._src_changed)
        lay.addWidget(self.src)
        self.src_hint = label("", "Hint", wrap=True)
        lay.addWidget(self.src_hint)
        self.shape_info = label("", "Good", wrap=True)
        lay.addWidget(self.shape_info)

    def _p_height(self, lay):
        self.hgroup = QButtonGroup(self)
        self.rb_bal = radio_block(self.hgroup, "balanced", "Balanced: cut = fill",
                                  "The app finds the height where the soil you cut is just enough for the fill. "
                                  "Least soil to haul in or out.", lay)
        self.rb_fix = radio_block(self.hgroup, "fixed", "At a height I choose",
                                  "For example the road level, a floor level on the plans, or above the flood line.",
                                  lay)
        row = QHBoxLayout()
        row.setContentsMargins(24, 0, 0, 0)
        self.level = spin(-500, 9000, 0.0, " m", 2, 0.1, 130)
        row.addWidget(QLabel("Finished height"))
        row.addWidget(self.level)
        row.addStretch(1)
        lay.addLayout(row)
        self.ground_hint = label("", "Hint", wrap=True)
        self.ground_hint.setContentsMargins(24, 0, 0, 0)
        lay.addWidget(self.ground_hint)
        self.rb_bal.setChecked(True)
        lay.addSpacing(6)
        self.slope_cb = QCheckBox("Slope it gently so rain drains away")
        self.slope_cb.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.slope_cb)
        g = QGridLayout()
        g.setContentsMargins(24, 0, 0, 0)
        self.slope = spin(0.1, 10, 1.0, " %", 1, 0.5, 100)
        self.flow = QComboBox()
        self.flow.addItem("Downhill (follow the land)", None)
        for name, az in ew.COMPASS:
            self.flow.addItem(name.capitalize(), az)
        g.addWidget(QLabel("Slope"), 0, 0)
        g.addWidget(self.slope, 0, 1)
        g.addWidget(QLabel("Rain runs toward"), 1, 0)
        g.addWidget(self.flow, 1, 1)
        lay.addLayout(g)
        lay.addWidget(explain("1% means the ground drops 1 cm for every metre (10 cm over 10 m). Lots and yards are "
                              "usually given 1–2% so water does not stand. The height above is the height in the "
                              "middle."))
        for wdg in (self.rb_bal, self.rb_fix):
            wdg.toggled.connect(self._changed)
        self.level.valueChanged.connect(self._changed)
        self.slope_cb.toggled.connect(self._changed)
        self.slope.valueChanged.connect(self._changed)
        self.flow.currentIndexChanged.connect(self._changed)

    def _p_edges(self, lay):
        self.egroup = QButtonGroup(self)
        self.rb_slopes = radio_block(self.egroup, "slopes", "Sloped sides (soil)",
                                     "Where the lot is higher or lower than the land around it, the soil is shaped "
                                     "into a slope that meets the ground. These sides are counted too.", lay)
        g = QGridLayout()
        g.setContentsMargins(24, 0, 0, 0)
        self.soil = QComboBox()
        for k, (name, _c, _f, _d) in ew.SOILS.items():
            self.soil.addItem(name, k)
        self.cut_r = spin(0.2, 10, 1.0, "  : 1", 1, 0.25, 110)
        self.fill_r = spin(0.2, 10, 1.5, "  : 1", 1, 0.25, 110)
        g.addWidget(QLabel("Soil"), 0, 0)
        g.addWidget(self.soil, 0, 1, 1, 2)
        g.addWidget(QLabel("Cut sides"), 1, 0)
        g.addWidget(self.cut_r, 1, 1)
        g.addWidget(label("out : up", "Hint"), 1, 2)
        g.addWidget(QLabel("Filled sides"), 2, 0)
        g.addWidget(self.fill_r, 2, 1)
        g.addWidget(label("out : up", "Hint"), 2, 2)
        lay.addLayout(g)
        lay.addWidget(explain("1.5 : 1 means the side goes 1.5 m out for every 1 m of height. Gentler sides are "
                              "safer against slides but take more room. Ask an engineer for high or steep sides."))
        self.rb_walls = radio_block(self.egroup, "walls", "Straight walls (retaining wall or riprap)",
                                    "The cut and fill stop at the outline. The wall itself is not counted.",
                                    lay)
        self.rb_slopes.setChecked(True)
        self.soil.currentIndexChanged.connect(self._soil_changed)
        self.cut_r.valueChanged.connect(self._ratio_edited)
        self.fill_r.valueChanged.connect(self._ratio_edited)
        self.rb_slopes.toggled.connect(self._changed)

    def _p_ref(self, lay):
        self.rgroup = QButtonGroup(self)
        for key, title, text in (
                ("edge", "The ground around the edge", "Best for piles, mounds and holes: a surface is stretched "
                 "across the outline and everything above (or below) it is counted."),
                ("edge_mean", "A flat level at the average edge height", "For piles standing on flat ground."),
                ("level", "A height I choose", "Everything above or below this height is counted."),
                ("lowest", "The lowest ground inside", "Everything above the lowest point.")):
            rb = radio_block(self.rgroup, key, title, text, lay)
            rb.toggled.connect(self._changed)
            if key == "edge":
                rb.setChecked(True)
            if key == "level":
                row = QHBoxLayout()
                row.setContentsMargins(24, 0, 0, 6)
                self.ref_level = spin(-500, 9000, 0.0, " m", 2, 0.1, 130)
                self.ref_level.valueChanged.connect(self._changed)
                row.addWidget(QLabel("Height"))
                row.addWidget(self.ref_level)
                row.addStretch(1)
                lay.addLayout(row)
        self.dsm_box = QVBoxLayout()
        lay.addLayout(self.dsm_box)

    # ---- road pages
    TIGHT = (("gentle", "Gentle curves", 60.0, "for faster vehicles; needs more room"),
             ("normal", "Curves at the smallest radius", -1.0, "as tight as the smallest radius below allows"),
             ("none", "No curves", 0.0, "keep the sharp bends as drawn"))

    def _p_curves(self, lay):
        lay.addWidget(label("At each bend the road can turn along a smooth curve (an arc of a circle) instead of a "
                            "sharp corner. A bigger radius is a gentler curve.", "", wrap=True))
        row = QHBoxLayout()
        row.addWidget(QLabel("Smallest curve radius"))
        row.addWidget(self.r_min_radius)
        row.addStretch(1)
        lay.addLayout(row)
        self.min_r_note = label("", "Hint", wrap=True)
        lay.addWidget(self.min_r_note)
        lay.addWidget(explain(rp.WHY_RADIUS))
        self.tgroup = QButtonGroup(self)
        for key, title, r, text in self.TIGHT:
            rb = radio_block(self.tgroup, key, f"{title}" + (f" (radius {r:g} m)" if r > 0 else ""), text, lay)
            rb.setProperty("r", r)
            if key == "normal":
                rb.setChecked(True)
            rb.toggled.connect(self._tight_changed)
        self.r_min_radius.valueChanged.connect(self._min_radius_changed)
        lay.addWidget(label("Each bend", "H2"))
        self.curve_box = QVBoxLayout()
        self.curve_box.setSpacing(4)
        lay.addLayout(self.curve_box)
        lay.addWidget(explain("The curve begins before the bend and ends after it. If two bends are close "
                              "together, their curves are made smaller so they fit. Too tight a curve is hard for "
                              "trucks; the app warns you."))
        self.curve_rows = []
        self._radii = {}

    def _tight_r(self) -> float:
        b = self.tgroup.checkedButton()
        r = float(b.property("r")) if b else -1.0
        return max(self.r_min_radius.value(), 5.0) if r < 0 else r

    def _min_radius_changed(self, _v):
        self._min_radius_note()
        b = self.tgroup.checkedButton() if hasattr(self, "tgroup") else None
        if b is not None and float(b.property("r")) < 0:
            self._tight_changed(True)
        elif hasattr(self, "curve_rows"):
            self._curves_changed()

    def _min_radius_note(self):
        std = rdm.STANDARDS.get(self.r_std.currentData() or "")
        v = self.r_min_radius.value()
        if std and v < std["min_radius"] - 1e-9:
            self.min_r_note.setText(f"\u26A0 Smaller than the DPWH {std['min_radius']:g} m for this road: bends "
                                    f"under {std['min_radius']:g} m are marked in the report.")
            self.min_r_note.setObjectName("Warn")
        else:
            self.min_r_note.setText(f"DPWH: {std['min_radius']:g} m for this road (D.O. 112 s. 2019)." if std else
                                    "Your own value (DPWH: 30 m, 50 m for Tourism roads).")
            self.min_r_note.setObjectName("Hint")
        theme.repolish(self.min_r_note)

    def _tight_changed(self, on):
        if not on:
            return
        r = self._tight_r()
        for vtx, cb, sp, _st in self.curve_rows:
            cb.setChecked(r > 0)
            if r > 0:
                sp.setValue(r)
        self._curves_changed()

    def _build_curve_rows(self):
        clear_layout(self.curve_box)
        self.curve_rows = []
        pts = self.page.pts
        if len(pts) < 3:
            self.curve_box.addWidget(label("The road line has no bends.", "Hint"))
            self._curves_changed()
            return
        k = 0
        for i, (d, left) in enumerate(alm.deflections(pts), start=1):
            if d < 1.0:
                continue
            k += 1
            w = QWidget()
            g = QGridLayout(w)
            g.setContentsMargins(0, 2, 0, 2)
            g.addWidget(QLabel(f"Bend {k} (point {i + 1}): turns {'left' if left else 'right'} {d:.0f}\u00b0"),
                        0, 0, 1, 3)
            cb = QCheckBox("Smooth curve, radius")
            sp = spin(3, 2000, self._radii.get(i, self._tight_r() or 30.0), " m", 0, 5, 100)
            cb.setChecked(self._radii.get(i, self._tight_r()) > 0)
            st = label("", "Hint", wrap=True)
            g.addWidget(cb, 1, 0)
            g.addWidget(sp, 1, 1)
            g.addWidget(st, 2, 0, 1, 3)
            g.setColumnStretch(2, 1)
            cb.toggled.connect(lambda _v: self._curves_changed())
            sp.valueChanged.connect(lambda _v: self._curves_changed())
            self.curve_box.addWidget(w)
            self.curve_rows.append((i, cb, sp, st))
        self._curves_changed()

    def curve_radii(self) -> list:
        n = len(self.page.pts)
        r = [0.0] * max(0, n - 2)
        for vtx, cb, sp, _st in self.curve_rows:
            if 1 <= vtx <= n - 2:
                r[vtx - 1] = sp.value() if cb.isChecked() else 0.0
        return r

    def _curves_changed(self):
        for vtx, cb, sp, _st in self.curve_rows:
            sp.setEnabled(cb.isChecked())
            self._radii[vtx] = sp.value() if cb.isChecked() else 0.0
        if self.kind != "road" or len(self.page.pts) < 2:
            return
        try:
            al = alm.build(self.page.pts, self.curve_radii())
        except ValueError:
            return
        rmin = self.r_min_radius.value()
        by_v = {c.vertex: c for c in al.curves}
        for vtx, cb, sp, st in self.curve_rows:
            c = by_v.get(vtx)
            if not cb.isChecked() or c is None:
                st.setText("Sharp bend.")
                st.setObjectName("Hint")
            elif c.reduced and rmin and c.radius < rmin - 1e-6:
                st.setText(f"\u26A0 Only a {c.radius:.0f} m curve fits here (the straights next to it are short), "
                           f"less than your smallest radius of {rmin:g} m. Move the bend point on the map so a "
                           f"{rmin:g} m curve fits, or accept it: the report marks this bend.")
                st.setObjectName("Warn")
            elif c.reduced:
                st.setText(f"\u26A0 Made smaller to fit: radius {c.radius:.0f} m (the straights next to it are short).")
                st.setObjectName("Warn")
            elif rmin and c.radius < rmin - 1e-6:
                st.setText(f"\u26A0 Tighter than your smallest radius of {rmin:g} m: the report marks this bend.")
                st.setObjectName("Warn")
            elif c.radius < rdm.VERY_TIGHT:
                st.setText("\u26A0 Very tight: long trucks may not get around.")
                st.setObjectName("Warn")
            else:
                st.setText(f"\u2714 Curve {c.length:.1f} m long, starts {c.tangent:.1f} m before the bend.")
                st.setObjectName("Good")
            theme.repolish(st)
        self.page.show_alignment(al)
        self._changed()

    def _p_stations(self, lay):
        lay.addWidget(label("The app measures the ground across the road at each station. Closer stations follow the "
                            "ground more closely; farther ones are quicker.", "", wrap=True))
        self.sgroup = QButtonGroup(self)
        row = QHBoxLayout()
        for v in (5, 10, 15, 20):
            b = QRadioButton(f"every {v} m")
            b.setProperty("v", float(v))
            self.sgroup.addButton(b)
            row.addWidget(b)
            if v == 10:
                b.setChecked(True)
            b.toggled.connect(self._changed)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(explain("10 m is common for road plans. Use 5 m on hilly or curvy ground. Stations are also "
                              "placed at every bend and at the end."))
        self.station_info = label("", "Good", wrap=True)
        lay.addWidget(self.station_info)

    def interval(self) -> float:
        b = self.sgroup.checkedButton()
        return float(b.property("v")) if b else 10.0

    def _p_rise(self, lay):
        lay.addWidget(label("Where the road starts (0+000)", "H2"))
        self.start_group = QButtonGroup(self)
        self.rb_start_g = QRadioButton("Use the ground there")
        self.rb_start_t = QRadioButton("I know the starting height")
        for b in (self.rb_start_g, self.rb_start_t):
            self.start_group.addButton(b)
            lay.addWidget(b)
            b.toggled.connect(self._changed)
        self.rb_start_g.setChecked(True)
        row = QHBoxLayout()
        row.setContentsMargins(24, 0, 0, 0)
        self.start_z = spin(-500, 9000, 0.0, " m", 2, 0.1, 130)
        self.start_z.valueChanged.connect(self._changed)
        row.addWidget(self.start_z)
        self.start_hint = label("", "Hint")
        row.addWidget(self.start_hint, 1)
        lay.addLayout(row)
        lay.addSpacing(6)
        lay.addWidget(label("Road height", "H2"))
        self.pgroup = QButtonGroup(self)
        rb = radio_block(self.pgroup, "gentle", "Follow the ground",
                         "A smooth grade that follows the land within your requirements below.", lay)
        rb.setChecked(True)
        radio_block(self.pgroup, "raised", "Raised above the ground",
                    "Follows the ground this much higher, e.g. in flood-prone areas.", lay)
        radio_block(self.pgroup, "into", "Set into the ground",
                    "Follows the ground this much lower: the road sits in cut.", lay)
        row = QHBoxLayout()
        row.setContentsMargins(24, 0, 0, 4)
        row.addWidget(QLabel("by"))
        self.offset_m = spin(0.05, 10, 0.5, " m", 2, 0.05, 100)
        self.offset_m.valueChanged.connect(self._changed)
        row.addWidget(self.offset_m)
        row.addWidget(label("above or into the (smoothed) ground at the centre line", "Hint", wrap=True), 1)
        lay.addLayout(row)
        radio_block(self.pgroup, "follow", "Follow the ground closely", "Least cut and fill: the road goes up and "
                    "down with the land, only smoothed (grades up to 25%).", lay)
        radio_block(self.pgroup, "level", "Keep it level", "One height (the starting height) for the whole road.", lay)
        radio_block(self.pgroup, "straight", "Start and end levels", "One straight grade from the start to the end "
                    "height.", lay)
        row = QHBoxLayout()
        row.setContentsMargins(24, 0, 0, 4)
        self.end_ground = QCheckBox("End at the ground there, or at")
        self.end_ground.setChecked(True)
        self.end_z = spin(-500, 9000, 0.0, " m", 2, 0.1, 130)
        self.end_ground.toggled.connect(lambda on: (self.end_z.setEnabled(not on), self._changed()))
        self.end_z.setEnabled(False)
        self.end_z.valueChanged.connect(self._changed)
        row.addWidget(self.end_ground)
        row.addWidget(self.end_z)
        row.addStretch(1)
        lay.addLayout(row)
        radio_block(self.pgroup, "targets", "I have target heights",
                    "Type a station and its height on each line, e.g. “50, 125.40” (or 0+050, 125.40). "
                    "Straight slopes are drawn between them.", lay)
        self.targets = QPlainTextEdit()
        self.targets.setPlaceholderText("50, 125.40\n120, 127.10\n200, 126.00")
        self.targets.setFixedHeight(80)
        self.targets.setStyleSheet(f"background: {theme.INPUT_BG}; color: {theme.INK};")
        self.targets.textChanged.connect(self._changed)
        lay.addWidget(self.targets)
        for b in self.pgroup.buttons():
            b.toggled.connect(self._changed)
        # the designer's requirements (kept with the plan)
        lay.addWidget(label("Your requirements", "H2"))
        lay.addWidget(label("Starting values are shown; type your own. These are designer preferences, kept with "
                            "the plan. Where the land does not allow them, the app warns you and the report lists it.",
                            "Hint", wrap=True))
        self.max_grade = spin(0.5, 30, 12.0, " %", 1, 0.5, 110)
        self.min_grade = spin(0, 5, 0.5, " %", 2, 0.1, 110)
        self.min_grade.setSpecialValueText("no check")
        self.max_fill = spin(0, 30, 1.5, " m", 2, 0.1, 110)
        self.max_fill.setSpecialValueText("no limit")
        self.max_cut = spin(0, 30, 1.5, " m", 2, 0.1, 110)
        self.max_cut.setSpecialValueText("no limit")
        self.req_form = rp.PartForm([
            ("Steepest grade", self.max_grade, "max_grade", 1, "%", "8% means 8 m up for every 100 m; DPWH: 12%"),
            ("Flattest grade in cut", self.min_grade, "min_grade", 1, "%", "so the ditches drain; DPWH: 0.5%"),
            ("Highest fill at the centre", self.max_fill, "max_fill", 1, " m", "road surface above the ground"),
            ("Deepest cut at the centre", self.max_cut, "max_cut", 1, " m", "road surface below the ground")])
        self.req_form.set_editing(True)
        lay.addWidget(self.req_form)
        self.req_more = label("", "Hint", wrap=True)
        lay.addWidget(self.req_more)
        for w_ in (self.max_grade, self.min_grade, self.max_fill, self.max_cut):
            w_.valueChanged.connect(self._req_changed)
        for b in self.pgroup.buttons():
            b.toggled.connect(lambda on: self._req_changed() if on else None)
        lay.addWidget(explain("The heights are the road surface at the centre line. Gentler grades are easier for "
                              "loaded trucks but usually need more cut and fill."))

    def _p_vcurves(self, lay):
        lay.addWidget(label("Where the road changes from one slope to another (a grade change point, PVI), it can "
                            "change gradually along a smooth vertical curve instead of a sudden kink. Drivers then "
                            "see far enough over a hump (crest) and their headlights reach far enough into a dip "
                            "(sag).", "", wrap=True))
        self.vc_group = QButtonGroup(self)
        self.vc_yes = radio_block(self.vc_group, "yes", "Yes \u2013 straight grades with smooth vertical curves",
                                  "The app proposes the grade change points from the road height you chose; you can "
                                  "change them below.", lay)
        self.vc_no = radio_block(self.vc_group, "no", "No \u2013 keep the road height as chosen",
                                 "Quick estimates; the road height is smoothed station by station.", lay)
        self.vc_yes.setChecked(True)
        for b in (self.vc_yes, self.vc_no):
            b.toggled.connect(self._vc_changed)
        row = QHBoxLayout()
        row.addWidget(QLabel("Design speed"))
        self.vc_speed = QComboBox()
        for v in vt.SPEEDS:
            self.vc_speed.addItem(f"{v} km/h", v)
        self.vc_speed.setCurrentIndex(self.vc_speed.findData(30))
        self.vc_speed.currentIndexChanged.connect(self._vc_speed_changed)
        row.addWidget(self.vc_speed)
        row.addStretch(1)
        lay.addLayout(row)
        self.vc_speed_note = label("", "Hint", wrap=True)
        lay.addWidget(self.vc_speed_note)
        row = QHBoxLayout()
        row.addWidget(QLabel("Shortest vertical curve (PVC to PVT)"))
        self.vc_min = spin(0, 500, 60, " m", 0, 5, 110)
        self.vc_min.setSpecialValueText("from the speed only")
        self.vc_min.valueChanged.connect(lambda _v: self._changed())
        row.addWidget(self.vc_min)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(explain(rp.WHY_VC))
        lay.addWidget(label("Reference: how long a curve must be (AASHTO 2018)", "H2"))
        self.k_table = QTableWidget(len(vt.K_TABLE), 5)
        self.k_table.setHorizontalHeaderLabels(["Speed", "Sight dist.", "K crest", "K sag", "Min. L"])
        self.k_table.verticalHeader().setVisible(False)
        self.k_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.k_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.k_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.k_table.verticalHeader().setDefaultSectionSize(24)
        self.k_table.setFixedHeight(40 + 24 * len(vt.K_TABLE))
        self.k_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.k_table)
        lay.addWidget(explain("Curve length L = K \u00d7 A, where A is the change of grade in % (e.g. from +6% to "
                              "\u22122% is A = 8). K comes from the design speed: crest curves for stopping sight "
                              "distance over a hump, sag curves for headlight reach in a dip. Never shorter than "
                              "0.6 \u00d7 speed. Source: " + vt.AASHTO_SOURCE + "."))
        lay.addWidget(label("Grade change points (PVI)", "H2"))
        self.pvi_table = QTableWidget(0, 4)
        self.pvi_table.setHorizontalHeaderLabels(["Station", "Elevation (m)", "Curve length (m)", "Result"])
        hh = self.pvi_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        self.pvi_table.verticalHeader().setVisible(False)
        self.pvi_table.setMinimumHeight(170)
        self.pvi_table.itemChanged.connect(self._pvi_edited)
        lay.addWidget(self.pvi_table)
        row = QHBoxLayout()
        b = QPushButton("Propose again")
        b.setToolTip("Forget my changes and let the app propose the points from the road height.")
        b.clicked.connect(self._pvi_reset)
        row.addWidget(b)
        b = QPushButton("Add a point")
        b.clicked.connect(self._pvi_add)
        row.addWidget(b)
        b = QPushButton("Delete selected")
        b.clicked.connect(self._pvi_delete)
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.vc_status = label("", "Hint", wrap=True)
        lay.addWidget(self.vc_status)
        lay.addWidget(explain("Type a station as metres (e.g. 120) or 0+120. Leave the curve length empty to use the "
                              "design speed. The first and last points are the start and end of the road. The side "
                              "view under the map shows the result."))
        self._pvis_auto = True
        self._pvis: list = []

    def _vc_speed_default(self):
        std = rdm.STANDARDS.get(self.r_std.currentData() or "") if hasattr(self, "r_std") else None
        if std and not getattr(self, "_vc_speed_set", False):
            import re
            m = re.search(r"(\d+)\s*km/h", str(std["speed"]))
            i = self.vc_speed.findData(int(m.group(1))) if m else -1
            if i >= 0:
                self.vc_speed.blockSignals(True)
                self.vc_speed.setCurrentIndex(i)
                self.vc_speed.blockSignals(False)
        self.vc_speed_note.setText(
            (f"Chosen road standard: {std['speed']}." if std else "No road standard chosen: 30 km/h suits most "
             "barangay roads and roads with under 400 vehicles a day.") + " Choose the speed vehicles will really drive.")

    def _fill_k_table(self):
        sp = self.vc_speed.currentData()
        for i, (v, (ssd, kc, ks)) in enumerate(vt.K_TABLE.items()):
            for c, t in enumerate((f"{v} km/h", f"{ssd} m", f"{kc}", f"{ks}", f"{vt.min_length(v):.0f} m")):
                it = QTableWidgetItem(t)
                if v == sp:
                    f_ = it.font()
                    f_.setBold(True)
                    it.setFont(f_)
                    from PySide6.QtGui import QColor
                    it.setBackground(QColor(theme.WARN_SOFT))
                self.k_table.setItem(i, c, it)

    def _vc_speed_changed(self, _i):
        self._vc_speed_set = True
        self._fill_k_table()
        self._changed()

    def _vc_changed(self, *_):
        on = self.vc_yes.isChecked()
        for w_ in (self.vc_speed, self.pvi_table, self.k_table, self.vc_min):
            w_.setEnabled(on)
        self._changed()

    def _fill_pvis(self, pvis, curves=()):
        by = {round(c.pvi_s, 3): c for c in curves}
        self.pvi_table.blockSignals(True)
        self.pvi_table.setRowCount(len(pvis))
        for i, r_ in enumerate(pvis):
            s_, z_ = float(r_[0]), float(r_[1])
            L = float(r_[2]) if len(r_) > 2 and r_[2] else 0.0
            c = by.get(round(s_, 3))
            if i == 0:
                res = "start of the road"
            elif i == len(pvis) - 1:
                res = "end of the road"
            elif c is None:
                res = ""
            elif c.length <= 0:
                res = "no curve needed (grade change below 0.2%)"
            else:
                res = (f"{c.kind}, L {c.length:.1f} m, K {c.k:.1f} ({c.g1:+.2f}% \u2192 {c.g2:+.2f}%)"
                       + (" \u26a0 shortened to fit" if c.reduced else "")
                       + (f" \u26a0 needs K {c.k_min:g}" if c.short else " \u2714"))
            vals = (rdm.station_label(s_), f"{z_:.3f}", f"{L:g}" if L > 0 else "", res)
            for c_, t in enumerate(vals):
                it = QTableWidgetItem(t)
                if c_ == 3 or (c_ == 2 and i in (0, len(pvis) - 1)):
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if c_ == 3 and "\u26a0" in t:
                    it.setForeground(Qt.darkRed)
                self.pvi_table.setItem(i, c_, it)
        self.pvi_table.blockSignals(False)
        self._pvis = [list(r_) + [0.0] * (3 - len(r_)) for r_ in pvis]

    @staticmethod
    def _station_m(t: str) -> float:
        t = t.strip()
        if "+" in t:
            k, m = t.split("+", 1)
            return float(k or 0) * 1000 + float(m)
        return float(t)

    def _pvi_edited(self, item):
        r_, c_ = item.row(), item.column()
        if not (0 <= r_ < len(self._pvis)) or c_ > 2:
            return
        try:
            t = item.text().strip()
            if c_ == 0:
                self._pvis[r_][0] = self._station_m(t)
            elif c_ == 1:
                self._pvis[r_][1] = float(t)
            else:
                self._pvis[r_][2] = float(t) if t else 0.0
        except ValueError:
            self.vc_status.setText("\u26a0 Could not read that value; type a number.")
            return
        self._pvis_auto = False
        self._pvis.sort(key=lambda q: q[0])
        self._changed()

    def _pvi_reset(self):
        self._pvis_auto = True
        self._changed()

    def _pvi_add(self):
        if len(self._pvis) < 2:
            return
        gaps = [(b[0] - a[0], i) for i, (a, b) in enumerate(zip(self._pvis, self._pvis[1:]))]
        _g, i = max(gaps)
        a, b = self._pvis[i], self._pvis[i + 1]
        self._pvis.insert(i + 1, [(a[0] + b[0]) / 2, round((a[1] + b[1]) / 2, 3), 0.0])
        self._pvis_auto = False
        self._fill_pvis(self._pvis)
        self._changed()

    def _pvi_delete(self):
        rows = sorted({i.row() for i in self.pvi_table.selectedIndexes()}, reverse=True)
        rows = [r_ for r_ in rows if 0 < r_ < len(self._pvis) - 1]
        if not rows:
            self.vc_status.setText("Select a point in the middle (the start and end stay).")
            return
        for r_ in rows:
            del self._pvis[r_]
        self._pvis_auto = False
        self._fill_pvis(self._pvis)
        self._changed()

    def _show_vcurves(self, r):
        if not self.vc_yes.isChecked():
            return
        self._fill_pvis(r.pvis, r.vcurves)
        n = sum(1 for c in r.vcurves if c.length > 0)
        bad = [c for c in r.vcurves if c.short or c.reduced]
        self.vc_status.setText(f"{len(r.pvis)} points, {n} vertical curve(s)"
                               + (" (proposed by the app)" if self._pvis_auto else " (your points)")
                               + (f". \u26a0 {len(bad)} curve(s) need attention (see Result)." if bad else "."))

    def _parse_targets(self):
        out = []
        for line in self.targets.toPlainText().splitlines():
            line = line.strip()
            if not line:
                continue
            a, _, b = line.replace(";", ",").partition(",")
            if not b:
                parts = line.split()
                if len(parts) < 2:
                    raise ValueError(f"Could not read “{line}”: type station, height.")
                a, b = parts[0], parts[1]
            a = a.strip()
            if "+" in a:
                k, m = a.split("+", 1)
                st = float(k) * 1000 + float(m)
            else:
                st = float(a)
            out.append([st, float(b)])
        return out

    def _make_shape_widgets(self):
        """The road shape inputs, shared by the guided part steps (std, carriage, shoulders, layers, ditch, slopes)."""
        self.r_std = QComboBox()
        for k, v in rdm.STANDARDS.items():
            self.r_std.addItem(v["label"], k)
        self.r_std.addItem("My own values", "")
        self.r_std.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.r_std.setMinimumContentsLength(16)
        self.std_note = label("", "Hint", wrap=True)
        self.r_width = spin(1, 40, 5.0, " m", 2, 0.1, 110)
        self.r_crown = spin(0, 10, 1.5, " %", 1, 0.5, 110)
        self.r_sh = QCheckBox("Shoulders")
        self.r_sh.setChecked(True)
        self.r_sh_w = spin(0.1, 10, 1.5, " m", 2, 0.1, 110)
        self.r_sh_p = spin(0, 15, 3.0, " %", 1, 0.5, 110)
        self.r_pccp = spin(0, 600, 200, " mm", 0, 10, 110)
        self.r_sub = spin(0, 600, 200, " mm", 0, 10, 110)
        self.r_ditch = QCheckBox("Road side ditches where the road is in cut")
        self.r_ditch.setChecked(True)
        self.r_ditch_d = spin(0.1, 3, 0.5, " m", 2, 0.1, 110)
        self.r_ditch_w = spin(0, 5, 0.0, " m", 2, 0.1, 110)
        self.r_ditch_in = spin(0.5, 10, 2.0, "  : 1", 1, 0.25, 110)
        self.r_ditch_toe = QCheckBox("Also a ditch at the toe of the fill")
        self.r_ditch_toe.setChecked(True)
        self.r_ditch_out = spin(0.5, 10, 1.5, "  : 1", 1, 0.25, 110)
        self.r_soil = QComboBox()
        for k, (name, _c, _f, _d) in ew.SOILS.items():
            self.r_soil.addItem(name, k)
        self.r_soil.setCurrentIndex(self.r_soil.findData("custom"))
        self.r_cut = spin(0.2, 10, 1.5, "  : 1", 1, 0.25, 110)
        self.r_fill = spin(0.2, 10, 2.0, "  : 1", 1, 0.25, 110)
        # the smallest curve radius (curves step) follows the standard: 30 m, 50 m for Tourism roads
        self.r_min_radius = spin(0, 500, 30, " m", 0, 5, 110)
        self.r_min_radius.setSpecialValueText("no check")
        self.part_rows = {
            "carriage": [("Width, both lanes", self.r_width, "width", 1, " m", "the part vehicles drive on, edge to edge"),
                         ("Cross slope from the centre", self.r_crown, "crown_pct", 1, "%",
                          "falls this much toward each edge so rain runs off")],
            "shoulders": [("", self.r_sh, "shoulders", 1, "", ""),
                          ("Width, each side", self.r_sh_w, "shoulder_w", 1, " m", ""),
                          ("Cross slope", self.r_sh_p, "shoulder_pct", 1, "%", "away from the road")],
            "layers": [("Concrete (PCCP)", self.r_pccp, "pccp_t", 1000, " mm", "the slab vehicles drive on"),
                       ("Aggregate subbase", self.r_sub, "subbase_t", 1000, " mm",
                        "under the concrete and the shoulders; cut and fill stop at its bottom")],
            "ditch": [("", self.r_ditch, "ditch", 1, "", ""),
                      ("Depth", self.r_ditch_d, "ditch_d", 1, " m", "below the shoulder edge"),
                      ("Bottom width", self.r_ditch_w, "ditch_w", 1, " m", "0 = V-shaped (DPWH)"),
                      ("Road side", self.r_ditch_in, "ditch_in", 1, " : 1", "out : down, from the shoulder edge"),
                      ("", self.r_ditch_toe, "ditch_toe", 1, "", ""),
                      ("Outer side of the toe ditch", self.r_ditch_out, "ditch_out", 1, " : 1", "out : up")],
            "slopes": [("Soil for the sides", self.r_soil, "soil", 1, "", "sets both slopes; or type your own"),
                       ("Cut sides", self.r_cut, "cut_ratio", 1, " : 1", "out : up"),
                       ("Fill sides", self.r_fill, "fill_ratio", 1, " : 1", "out : down")],
        }
        self.part_forms, self.part_prev, self.part_src, self.part_warn = {}, {}, {}, {}
        self.r_std.currentIndexChanged.connect(self._std_changed)
        for w_ in (self.r_width, self.r_crown, self.r_sh_w, self.r_sh_p, self.r_ditch_w, self.r_ditch_d,
                   self.r_ditch_in, self.r_ditch_out, self.r_pccp, self.r_sub):
            w_.valueChanged.connect(self._shape_changed)
        for c_ in (self.r_sh, self.r_ditch, self.r_ditch_toe):
            c_.toggled.connect(self._shape_changed)
        self.r_soil.currentIndexChanged.connect(self._r_soil_changed)
        self.r_cut.valueChanged.connect(self._r_ratio_edited)
        self.r_fill.valueChanged.connect(self._r_ratio_edited)

    def _p_std(self, lay):
        lay.addWidget(label(rp.EXPLAIN["std"], "", wrap=True))
        row = QHBoxLayout()
        row.addWidget(QLabel("Road standard"))
        row.addWidget(self.r_std, 1)
        lay.addLayout(row)
        lay.addWidget(self.std_note)
        self.preview = TemplatePreview()
        lay.addWidget(self.preview)
        lay.addWidget(explain("More than 400 vehicles a day? D.O. 112 does not cover that: choose “My own values” "
                              "and use your office's standard for bigger roads."))

    def _p_part(self, key, lay):
        prev = TemplatePreview()
        prev.highlight = key
        form = rp.PartForm(self.part_rows[key])
        page, src, warn = rp.part_page(key, prev, form)
        lay.addWidget(page)
        self.part_forms[key], self.part_prev[key], self.part_src[key], self.part_warn[key] = form, prev, src, warn

    def _toggle_change(self):
        f = self.part_forms.get(self.step())
        if f is None:
            return
        f.set_editing(not f.editing)
        self.change_btn.setText("Done changing" if f.editing else "Change dimensions")
        self._shape_changed()

    def _parts_refresh(self):
        if not self.part_forms:
            return
        rd = self.road_design()
        std_key = self.r_std.currentData() or ""
        ref, name = rp.reference(std_key)
        for k, f in self.part_forms.items():
            f.refresh(ref, name)
            self.part_prev[k].set_design(rd)
            self.part_src[k].setText(rp.source_text(k, std_key))
            w = rp.warnings(k, rd, std_key)
            self.part_warn[k].setText("\n".join("⚠ " + t for t in w))
            self.part_warn[k].setVisible(bool(w))

    def _std_changed(self, _i):
        k = self.r_std.currentData()
        st = rdm.STANDARDS.get(k)
        if st:
            for w_, key, k_ in ((self.r_width, "width", 1), (self.r_crown, "crown_pct", 1),
                                (self.r_sh_w, "shoulder_w", 1), (self.r_sh_p, "shoulder_pct", 1),
                                (self.r_pccp, "pccp_t", 1000), (self.r_sub, "subbase_t", 1000),
                                (self.r_ditch_d, "ditch_d", 1), (self.r_ditch_w, "ditch_w", 1),
                                (self.r_ditch_in, "ditch_in", 1), (self.r_ditch_out, "ditch_out", 1)):
                w_.blockSignals(True)
                w_.setValue(st[key] * k_)
                w_.blockSignals(False)
            for c_, key in ((self.r_sh, "shoulders"), (self.r_ditch, "ditch"), (self.r_ditch_toe, "ditch_toe")):
                c_.blockSignals(True)
                c_.setChecked(st[key])
                c_.blockSignals(False)
            self.r_soil.blockSignals(True)
            self.r_soil.setCurrentIndex(self.r_soil.findData("custom"))
            self.r_soil.blockSignals(False)
            self.r_cut.setValue(st["cut_ratio"])
            self.r_fill.setValue(st["fill_ratio"])
            self.r_min_radius.setValue(st["min_radius"])
            self.max_grade.setValue(st["max_grade"])
        self._std_note()
        self._shape_changed()

    def _std_note(self):
        """The description of the chosen standard (does not change any value)."""
        st = rdm.STANDARDS.get(self.r_std.currentData())
        if st:
            self.std_note.setText(
                f"From {rdm.DPWH_SOURCE}: width {st['width']:g} m, concrete (PCCP) at least {st['pccp_t'] * 1000:.0f} mm, "
                f"shoulders {st['shoulder_w']:g} m gravel, cross slope 1.5% (shoulders 3%), curves at least "
                f"{st['min_radius']:g} m radius, grades up to {st['max_grade']:g}%, design speed {st['speed']}. "
                f"From the {rdm.SECTION_SOURCE}: aggregate subbase {st['subbase_t'] * 1000:.0f} mm, fill sides 2 : 1, "
                "V-shaped road side ditch (2 : 1 road side, 1.5 : 1 outer side; 0.50 m deep is our suggestion). "
                "You can still change any value; the app then warns you where the plan is outside the standard.")
        else:
            self.std_note.setText("Your own values: the next steps keep the values you have now (the DPWH values "
                                  "for under 200 vehicles a day to start with); change any of them there.")

    def _shape_changed(self, *_):
        self.r_sh_w.setEnabled(self.r_sh.isChecked())
        self.r_sh_p.setEnabled(self.r_sh.isChecked())
        on = self.r_ditch.isChecked()
        for w_ in (self.r_ditch_w, self.r_ditch_d, self.r_ditch_in, self.r_ditch_toe):
            w_.setEnabled(on)
        self.r_ditch_out.setEnabled(on and self.r_ditch_toe.isChecked())
        if not hasattr(self, "pgroup"):            # still building the pages
            return
        self.preview.set_design(self.road_design())
        self._parts_refresh()
        self._changed()

    def _r_soil_changed(self, _i):
        _n, c, f, _d = ew.SOILS[self.r_soil.currentData()]
        if c is not None:
            for s_, v in ((self.r_cut, c), (self.r_fill, f)):
                s_.blockSignals(True)
                s_.setValue(v)
                s_.blockSignals(False)
        self._shape_changed()

    def _r_ratio_edited(self, _v):
        _n, c, f, _d = ew.SOILS[self.r_soil.currentData()]
        if c is not None and (abs(self.r_cut.value() - c) > 1e-9 or abs(self.r_fill.value() - f) > 1e-9):
            self.r_soil.blockSignals(True)
            self.r_soil.setCurrentIndex(self.r_soil.findData("custom"))
            self.r_soil.blockSignals(False)
        self._shape_changed()

    def road_design(self) -> rdm.RoadDesign:
        b = self.pgroup.checkedButton()
        try:
            targets = self._parse_targets()
        except ValueError:
            targets = []
        return rdm.RoadDesign(
            interval=self.interval(), start_mode="ground" if self.rb_start_g.isChecked() else "typed",
            start_z=self.start_z.value(), profile=b.property("key") if b else "gentle",
            max_grade=self.max_grade.value(), offset_m=self.offset_m.value(), min_grade=self.min_grade.value(),
            max_fill=self.max_fill.value(), max_cut=self.max_cut.value(),
            end_mode="ground" if self.end_ground.isChecked() else "typed",
            end_z=self.end_z.value(), targets=targets, width=self.r_width.value(), crown_pct=self.r_crown.value(),
            shoulders=self.r_sh.isChecked(), shoulder_w=self.r_sh_w.value(), shoulder_pct=self.r_sh_p.value(),
            ditch=self.r_ditch.isChecked(), ditch_w=self.r_ditch_w.value(), ditch_d=self.r_ditch_d.value(),
            ditch_in=self.r_ditch_in.value(), ditch_out=self.r_ditch_out.value(),
            ditch_toe=self.r_ditch_toe.isChecked(), pccp_t=self.r_pccp.value() / 1000,
            subbase_t=self.r_sub.value() / 1000, soil=self.r_soil.currentData(), cut_ratio=self.r_cut.value(),
            fill_ratio=self.r_fill.value(), swell_pct=self.swell.value(), shrink_pct=self.shrink.value(),
            truck_m3=self.truck.value(), radii=self.curve_radii(), standard=self.r_std.currentData() or "",
            min_radius=self.r_min_radius.value(), vc_min=self.vc_min.value(),
            vcurves=self.vc_yes.isChecked(), speed=int(self.vc_speed.currentData() or 30),
            pvis=[list(q) for q in self._pvis] if not self._pvis_auto else [], pvis_auto=self._pvis_auto)

    def _p_soil(self, lay):
        g = QGridLayout()
        g.setVerticalSpacing(4)
        self.swell = spin(0, 80, ms.DEFAULT_SWELL, " %", 0, 5, 90)
        self.shrink = spin(0, 50, ew.DEFAULT_SHRINK, " %", 0, 5, 90)
        self.truck = spin(0, 60, 0, " m³", 1, 1, 100)
        self.truck.setSpecialValueText("none")
        self.swell_lab = QLabel("Excavated soil loosens by")
        g.addWidget(self.swell_lab, 0, 0)
        g.addWidget(self.swell, 0, 1)
        self.swell_hint = label("", "Hint", wrap=True)
        g.addWidget(self.swell_hint, 1, 0, 1, 3)
        self.shrink_lab = QLabel("Packed fill needs extra")
        g.addWidget(self.shrink_lab, 2, 0)
        g.addWidget(self.shrink, 2, 1)
        self.shrink_hint = label("When fill is compacted it gets denser: to fill 100 m³ you need about 110 m³ of soil "
                                 "from the ground (10%).", "Hint", wrap=True)
        g.addWidget(self.shrink_hint, 3, 0, 1, 3)
        g.addWidget(QLabel("Other truck size"), 4, 0)
        g.addWidget(self.truck, 4, 1)
        g.addWidget(label("Trips are always shown for 6 m³ and 10 m³ trucks. Add the size you have on site.", "Hint",
                          wrap=True), 5, 0, 1, 3)
        g.setColumnStretch(2, 1)
        lay.addLayout(g)
        for s in (self.swell, self.shrink, self.truck):
            s.valueChanged.connect(self._changed)

    def _p_result(self, lay):
        self.res_box = QVBoxLayout()
        self.res_box.setSpacing(8)
        lay.addLayout(self.res_box)
        self.road_box = QWidget()
        rb = QVBoxLayout(self.road_box)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.addWidget(label("Cross-sections", "H2"))
        row = QHBoxLayout()
        self.st_slider = QSlider(Qt.Horizontal)
        self.st_slider.valueChanged.connect(self.select_station)
        self.st_label = QLabel("")
        self.st_label.setMinimumWidth(90)
        row.addWidget(self.st_slider, 1)
        row.addWidget(self.st_label)
        rb.addLayout(row)
        self.st_info = label("", "Hint", wrap=True)
        rb.addWidget(self.st_info)
        self.sec_view = SectionView()
        rb.addWidget(self.sec_view)
        rb.addWidget(label("Looking toward higher stations. Brown = ground now, navy = road and sides; heights "
                           "exaggerated. Click the side view under the map to jump to a station.", "Hint", wrap=True))
        self.road_box.setVisible(False)
        lay.addWidget(self.road_box)
        row = QHBoxLayout()
        self.save_btn = QPushButton("Save plan")
        self.save_btn.clicked.connect(self.save)
        self.export_btn = QPushButton("Make report and files…")
        self.export_btn.setObjectName("Primary")
        self.export_btn.clicked.connect(self.export)
        row.addWidget(self.save_btn)
        row.addWidget(self.export_btn)
        row.addStretch(1)
        lay.addLayout(row)
        nb = QPushButton("Start a new plan")
        nb.setObjectName("Link")
        nb.clicked.connect(self.new_plan)
        lay.addWidget(nb)

    # ================================================================ navigation
    def steps(self):
        return self.STEPS[self.kind]

    def step(self) -> str:
        return self.steps()[self.idx]

    def drawing_allowed(self) -> bool:
        return self.step() == "draw"

    def go(self, i):
        self.idx = max(0, min(i, len(self.steps()) - 1))
        key = self.step()
        from PySide6.QtWidgets import QSizePolicy
        for k_, pw in self.pages.items():       # only the shown step decides the panel's width
            pw.setSizePolicy(QSizePolicy.Preferred if k_ == key else QSizePolicy.Ignored,
                             QSizePolicy.Preferred if k_ == key else QSizePolicy.Ignored)
        self.stack.setCurrentWidget(self.pages[key])
        self.stack.adjustSize()
        n = len(self.steps())
        self.step_label.setText(f"{ew.TYPES[self.kind].upper()} · STEP {self.idx + 1} OF {n}")
        self.bar.setRange(0, n)
        self.bar.setValue(self.idx + 1)
        self.title.setText("Draw the road line" if key == "draw" and self.kind == "road" else self.TITLES[key])
        self.back_btn.setEnabled(self.idx > 0)
        last = self.idx == n - 1
        self.next_btn.setVisible(not last)
        nxt = self.steps()[self.idx + 1] if not last else ""
        short = {"draw": "Draw the road line" if self.kind == "road" else "Draw the area", "height": "Height",
                 "edges": "Edges", "ref": "Measure from", "soil": "Soil and trucks", "stations": "Stations",
                 "rise": "On the ground", "curves": "Curves", "vcurves": "Grade changes", **rp.SHORT}
        guided = key in rp.PART_STEPS or key == "std"
        self.next_btn.setText(("See the results" if nxt == "result" else "OK, next" if guided else
                               f"Next: {short.get(nxt, '')}") + "  ›" if nxt else "")
        self.next_btn.setToolTip(f"Next: {short.get(nxt, '')}" if nxt and nxt != "result" else "")
        form = self.part_forms.get(key)
        if form is not None:
            form.set_editing(False)                 # suggested values first: OK, next  or  Change dimensions
        self.change_btn.setVisible(form is not None)
        self.change_btn.setText("Change dimensions")
        if key == "draw":
            self._sync_src()
            self._shape_text()
            self.draw_text.setText(
                "Click along the middle of where the road will go, from its start to its end. Double-click (or "
                "press Enter) on the last point to finish." if self.kind == "road" else
                "Click around the area on the map, corner by corner. Double-click (or press Enter) on the last "
                "corner to close the outline.")
        if key == "curves":
            self._build_curve_rows()
            self._min_radius_note()
        if key == "std" and not self.std_note.text():
            self._std_changed(0)
        if guided:
            self._parts_refresh()
        if key == "stations":
            self._station_text()
        if key == "rise":
            self._start_text()
            self._req_changed()
        if key == "vcurves":
            self._vc_speed_default()
            self._fill_k_table()
        if key == "std":
            self.preview.set_design(self.road_design())
        if key == "height":
            self._ground_text()
        if key == "soil":
            self._soil_texts()
        if key == "ref":
            self._dsm_hint()
        self._done_text()
        self._update_next()
        self.estimate.setVisible(key not in ("type", "draw", "result") and self.page.finished)
        self.page.canvas.set_pick_cursor(key == "draw")
        if key == "result" or (key not in ("type", "draw") and self.takeoff is None):
            self.compute()
        elif key not in ("type", "draw"):
            self._show()

    def next(self):
        if self.step() == "draw" and not self.page.finished:
            return
        self.go(self.idx + 1)

    def back(self):
        self.go(self.idx - 1)

    def _update_next(self):
        ok = True
        if self.step() == "draw":
            ok = self.page.finished and len(self.page.pts) >= (2 if self.kind == "road" else 3)
        self.next_btn.setEnabled(ok)

    def _done_text(self):
        lines = []
        steps = self.steps()[: self.idx]
        if "type" in steps:
            lines.append(f"✔ Plan: {ew.TYPES[self.kind]}")
        if "draw" in steps and self.page.finished and self.kind == "road":
            from shapely.geometry import LineString
            lines.append(f"\u2714 Road line: {LineString(self.page.pts).length:,.0f} m")
        elif "draw" in steps and self.page.finished:
            from shapely.geometry import Polygon
            lines.append(f"✔ Area: {Polygon(self.page.pts).area:,.0f} m², heights from "
                         f"{'bare ground' if self.src.currentData() == 'dtm' else 'ground + objects (DSM)'}")
        if "height" in steps:
            lines.append("✔ Height: " + ("balanced (cut = fill)" if self.rb_bal.isChecked() else
                                              f"{self.level.value():.2f} m") +
                         (f", {self.slope.value():g}% slope" if self.slope_cb.isChecked() else ", level"))
        if "edges" in steps:
            lines.append("✔ Edges: " + (f"sloped sides {self.cut_r.value():g} : 1 / {self.fill_r.value():g} : 1"
                                             if self.rb_slopes.isChecked() else "walls"))
        if "ref" in steps:
            b = self.rgroup.checkedButton()
            lines.append("✔ Measured from: " + (b.text().lower() if b else ""))
        if "curves" in steps:
            n = sum(1 for r_ in self.curve_radii() if r_ > 0)
            lines.append(f"\u2714 Curves: {n} smooth" if n else "\u2714 Curves: sharp bends")
        if "stations" in steps:
            lines.append(f"\u2714 Stations every {self.interval():g} m")
        if "rise" in steps:
            b = self.pgroup.checkedButton()
            k_ = b.property("key") if b else ""
            lines.append("\u2714 Road height: " + ("start at the ground" if self.rb_start_g.isChecked() else
                                                    f"start at {self.start_z.value():.2f} m") +
                         ", " + (b.text().lower() if b else "") +
                         (f" by {self.offset_m.value():g} m" if k_ in ("raised", "into") else "") +
                         (f" (up to {self.max_grade.value():g}%)" if k_ in ("gentle", "raised", "into") else ""))
        if "vcurves" in steps:
            lines.append(f"\u2714 Vertical curves for {self.vc_speed.currentData()} km/h"
                         if self.vc_yes.isChecked() else "\u2714 No vertical curves")
        if "std" in steps:
            lines.append(f"\u2714 Standard: {self.r_std.currentText()}")
        if "carriage" in steps:
            lines.append(f"\u2714 Carriageway {self.r_width.value():.2f} m, {self.r_crown.value():g}% each way")
        if "shoulders" in steps:
            lines.append(f"\u2714 Shoulders {self.r_sh_w.value():.2f} m at {self.r_sh_p.value():g}%"
                         if self.r_sh.isChecked() else "\u2714 No shoulders")
        if "layers" in steps:
            lines.append(f"\u2714 PCCP {self.r_pccp.value():.0f} mm on subbase {self.r_sub.value():.0f} mm")
        if "ditch" in steps:
            lines.append((f"\u2714 Ditches {'V-shaped' if self.r_ditch_w.value() <= 0 else 'flat-bottomed'}, "
                          f"{self.r_ditch_d.value():.2f} m deep" + (", also at the toe of the fill"
                                                                    if self.r_ditch_toe.isChecked() else ""))
                         if self.r_ditch.isChecked() else "\u2714 No road side ditches")
        if "slopes" in steps:
            lines.append(f"\u2714 Sides: cut {self.r_cut.value():g} : 1, fill {self.r_fill.value():g} : 1")
        if "soil" in steps:
            lines.append(f"✔ Soil: loosens {self.swell.value():.0f}%"
                         + (f", packing {self.shrink.value():.0f}%" if self.kind != "pile" else ""))
        self.done_box.setText("\n".join(lines))
        self.done_box.setVisible(bool(lines))

    # ================================================================ answers
    def _choose_type(self, key):
        if key not in self.STEPS:
            return
        changed = key != self.kind
        was_road = self.kind == "road"
        self.kind = key
        for k, c in self.cards.items():
            c.set_selected(k == key)
        if key == "pile" and changed and self.page.files.get("dsm"):
            self.src.setCurrentIndex(self.src.findData("dsm"))
        if key in ("pad", "road") and changed and self.page.files.get("dtm"):
            self.src.setCurrentIndex(self.src.findData("dtm"))
        self.swell.setValue(ew.PILE_SWELL if key == "pile" else ms.DEFAULT_SWELL)
        self.takeoff = None
        if changed:
            self.page.clear_overlay()
            if self.page.pts and (key == "road") != was_road:
                self.page._clear()                  # an outline is not a road line (and the other way round)
        self.go(self.idx)

    def _sync_src(self):
        for i in range(self.src.count()):
            self.src.model().item(i).setEnabled(self.src.itemData(i) in self.page.files)
        if self.page.src_kind.currentData() != self.src.currentData():
            self.src.blockSignals(True)
            self.src.setCurrentIndex(self.src.findData(self.page.src_kind.currentData()))
            self.src.blockSignals(False)
        self._src_hint()

    def _src_hint(self):
        if self.kind == "pile" and self.src.currentData() == "dtm":
            self.src_hint.setText("For a stockpile choose the second option: the bare-ground model may have removed "
                                  "the pile.")
        elif self.kind in ("pad", "road") and self.src.currentData() == "dsm":
            self.src_hint.setText("Use bare ground, otherwise trees and houses count as soil to cut.")
        else:
            self.src_hint.setText("")

    def _src_changed(self, _i):
        k = self.src.currentData()
        if k in self.page.files and self.page.src_kind.currentData() != k:
            self.page.src_kind.setCurrentIndex(self.page.src_kind.findData(k))   # reloads the map
        self._src_hint()
        self.takeoff = None

    def _station_text(self):
        if self.page.finished and len(self.page.pts) >= 2:
            try:
                n = len(rdm.make_stations(self.page.pts, self.interval()))
            except ValueError:
                return
            from shapely.geometry import LineString
            self.station_info.setText(f"{n} stations along the {LineString(self.page.pts).length:,.0f} m line.")

    def _start_text(self):
        if not self.page.finished:
            return
        try:
            z = ms.point_elevation(self.page._dem_path(), *self.page.pts[0])
        except Exception:
            return
        self.start_hint.setText(f"ground at the start: {z:.2f} m")
        if self.start_z.value() == 0:
            self.start_z.blockSignals(True)
            self.start_z.setValue(round(z, 2))
            self.start_z.blockSignals(False)

    REQ_START = {"max_grade": 12.0, "min_grade": 0.5, "max_fill": 1.5, "max_cut": 1.5}

    def _req_changed(self, *_):
        ref = dict(self.REQ_START)
        std = rdm.STANDARDS.get(self.r_std.currentData() or "")
        if std:
            ref["max_grade"] = std["max_grade"]
        self.req_form.refresh(ref, "starting value")
        self.offset_m.setEnabled(self._profile() in ("raised", "into"))
        self.req_more.setText(f"Also set on their own steps: smallest curve radius {self.r_min_radius.value():g} m "
                              f"(Curves), shortest vertical curve {self.vc_min.value():g} m (Grade changes).")
        self._changed()

    def _profile(self) -> str:
        b = self.pgroup.checkedButton()
        return b.property("key") if b else "gentle"

    def _shape_text(self):
        if self.kind == "road" and self.page.finished and len(self.page.pts) >= 2:
            from shapely.geometry import LineString
            ln = LineString(self.page.pts)
            self.shape_info.setText(f"\u2714 Road line: {len(self.page.pts)} points, {ln.length:,.1f} m long.")
            return
        if self.page.finished and len(self.page.pts) >= 3:
            from shapely.geometry import Polygon
            p = Polygon(self.page.pts)
            self.shape_info.setText(f"✔ Outline closed: {len(self.page.pts)} corners, {p.area:,.0f} m² "
                                    f"({p.area / 1e4:.3f} ha), perimeter {p.length:,.1f} m.")
        elif self.page.pts:
            self.shape_info.setText("")
        else:
            self.shape_info.setText("")

    def shape_changed(self):
        """Called by the page when the outline is drawn, finished, undone or cleared."""
        self.takeoff = None
        self.page.clear_overlay()
        if self.step() == "draw":
            self._shape_text()
            self._update_next()

    def _ground_text(self):
        if not self.page.finished:
            return
        try:
            a = ms.measure_area(self.page._dem_path(), self.page.pts)
        except Exception:
            return
        self.ground_hint.setText(f"The ground inside is {a.z_min:.2f} – {a.z_max:.2f} m today, "
                                 f"{a.z_mean:.2f} m on average.")
        if self.level.value() == 0:
            self.level.blockSignals(True)
            self.level.setValue(round(a.z_mean, 2))
            self.level.blockSignals(False)
        if self.ref_level.value() == 0:
            self.ref_level.blockSignals(True)
            self.ref_level.setValue(round(a.z_min, 2))
            self.ref_level.blockSignals(False)

    def _dsm_hint(self):
        clear_layout(self.dsm_box)
        self._ground_text()
        if self.src.currentData() == "dtm" and "dsm" in self.page.files:
            box = IssueBox("info", "Measuring a stockpile?", "The bare-ground model may have removed it. Use heights "
                                                              "from the surface model (DSM) instead.")
            self.dsm_box.addWidget(box)
            b = QPushButton("Use the surface model (DSM)")
            b.clicked.connect(lambda: (self.src.setCurrentIndex(self.src.findData("dsm")), self._dsm_hint(),
                                       self._changed()))
            self.dsm_box.addWidget(b)

    def _soil_changed(self, _i):
        k = self.soil.currentData()
        _n, c, f, _d = ew.SOILS[k]
        if c is not None:
            for s, v in ((self.cut_r, c), (self.fill_r, f)):
                s.blockSignals(True)
                s.setValue(v)
                s.blockSignals(False)
        self._changed()

    def _ratio_edited(self, _v):
        k = self.soil.currentData()
        _n, c, f, _d = ew.SOILS[k]
        if c is not None and (abs(self.cut_r.value() - c) > 1e-9 or abs(self.fill_r.value() - f) > 1e-9):
            self.soil.blockSignals(True)
            self.soil.setCurrentIndex(self.soil.findData("custom"))
            self.soil.blockSignals(False)
        self._changed()

    def _soil_texts(self):
        pad = self.kind == "pad"
        self.shrink.setVisible(pad)
        self.shrink_lab.setVisible(pad)
        self.shrink_hint.setVisible(pad)
        if pad:
            self.swell_lab.setText("Excavated soil loosens by")
            self.swell_hint.setText("Soil cut from the ground takes more space in a truck: about 25% more for "
                                    "common earth. Used for the truck trips.")
        else:
            self.swell_lab.setText("Extra space in the truck")
            self.swell_hint.setText("A stockpile is already loose: 0%. If you measure packed ground or a landslide "
                                    "deposit that will be excavated, use about 25%.")

    def _changed(self, *_):
        self.takeoff = None
        if self.page.finished and self.step() not in ("type", "draw"):
            self.timer.start()
        self._done_text()

    # ================================================================ compute
    def plan(self) -> dict:
        pts = [list(p) for p in self.page.pts]
        name = self.name or {"pad": "Lot plan", "pile": "Pile measurement", "road": "Road plan"}[self.kind]
        if self.kind == "road":
            return ew.plan_dict(type="road", name=name, source=self.src.currentData(), points=pts,
                                road=self.road_design())
        if self.kind == "pad":
            d = ew.PadDesign(level_mode="balanced" if self.rb_bal.isChecked() else "fixed",
                             level=self.level.value(), slope_pct=self.slope.value() if self.slope_cb.isChecked()
                             else 0.0, flow_deg=self.flow.currentData(),
                             sides="slopes" if self.rb_slopes.isChecked() else "walls", soil=self.soil.currentData(),
                             cut_ratio=self.cut_r.value(), fill_ratio=self.fill_r.value(),
                             swell_pct=self.swell.value(), shrink_pct=self.shrink.value(),
                             truck_m3=self.truck.value())
            return ew.plan_dict(type="pad", name=name, source=self.src.currentData(), points=pts, design=d)
        b = self.rgroup.checkedButton()
        return ew.plan_dict(type="pile", name=name, source=self.src.currentData(), points=pts,
                            pile={"reference": b.property("key") if b else "edge", "level": self.ref_level.value(),
                                  "swell_pct": self.swell.value(), "truck_m3": self.truck.value()})

    def compute(self):
        if not self.page.finished or self.page.view is None or self.step() in ("type", "draw"):
            return
        if self.task is not None and self.task.isRunning():
            self.timer.start()
            return
        if self.kind == "road":
            try:
                self._parse_targets()
            except ValueError as e:
                self._failed(str(e))
                return
        plan, path = self.plan(), self.page._dem_path()
        self.estimate.setText("Calculating…")
        if self.step() == "result":
            clear_layout(self.res_box)
            self.res_box.addWidget(label("Calculating…", "Muted"))
        self.task = Task(lambda _t: ew.run_plan(plan, path), self)
        self.task.done.connect(self._computed)
        self.task.failed.connect(self._failed)
        self.task.start()

    def _computed(self, t):
        self.takeoff = t
        if self.kind == "road":
            self.page.show_road(t)
            self._show_vcurves(t)
        else:
            self.page.show_overlay(t)
        self._show()

    def _failed(self, msg):
        self.takeoff = None
        self.estimate.setText(f"⚠ {msg}")
        if self.step() == "result":
            clear_layout(self.res_box)
            self.res_box.addWidget(IssueBox("warning", "Could not calculate", msg))

    def _show(self):
        t = self.takeoff
        if t is None:
            return
        h = t.haul
        self.estimate.setText(f"<b>With these choices:</b> cut {t.cut:,.0f} m³ · fill {t.fill:,.0f} m³<br>"
                              f"{h.sentence()}")
        self.road_box.setVisible(False)
        if self.step() != "result":
            return
        clear_layout(self.res_box)
        head = label(t.headline(), "", wrap=True)
        head.setStyleSheet("font-size: 14px;")
        self.res_box.addWidget(head)
        if self.kind == "road":
            self._show_road(t)
            return
        pile = t.kind == "pile"
        cards = [StatCard("Above (pile)" if pile else "Cut", f"{t.cut:,.0f} m³", f"highest {t.max_cut:.2f} m"
                          if pile else f"deepest {t.max_cut:.2f} m"),
                 StatCard("Below (hole)" if pile else "Fill", f"{t.fill:,.0f} m³", f"deepest {t.max_fill:.2f} m"),
                 StatCard("Net", f"{t.net:,.0f} m³", "cut − fill"),
                 StatCard("Total moved", f"{t.total:,.0f} m³", "cut + fill"),
                 StatCard("Flat area", f"{t.area.area_2d:,.0f} m²", f"{t.area.area_2d / 1e4:.3f} ha"),
                 StatCard("Finished height" if t.kind == "pad" else "Surface area",
                          (f"{t.level:.2f} m" if t.kind == "pad" else f"{t.area.area_3d:,.0f} m²"),
                          (f"{t.level_high:.2f} → {t.level_low:.2f} m" if t.kind == "pad" and t.slope_pct > 0
                           else "in the middle" if t.kind == "pad" else "following the slopes"))]
        cards[0].t.setStyleSheet(f"color: {theme.CUT_TEXT};")
        cards[1].t.setStyleSheet(f"color: {theme.FILL_TEXT};")
        g = QGridLayout()
        g.setSpacing(8)
        for i, c in enumerate(cards):
            g.addWidget(c, i // 2, i % 2)
        self.res_box.addLayout(g)
        if t.kind == "pad" and t.side_cut == t.side_cut and (t.side_cut > 0 or t.side_fill > 0):
            self.res_box.addWidget(label(f"Of these, the sloped sides: cut {t.side_cut:,.0f} m³, fill "
                                         f"{t.side_fill:,.0f} m³. Ground worked including the sides: "
                                         f"{t.works_area:,.0f} m².", "Hint", wrap=True))
        box = QWidget()
        box.setObjectName("Trucks")
        box.setStyleSheet(f"QWidget#Trucks {{ background: {theme.INFO_SOFT}; border-radius: 8px; }}"
                          "QLabel { background: transparent; }")
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 10, 12, 10)
        v.addWidget(label("Hauling", "H2"))
        v.addWidget(label(h.sentence(), "", wrap=True))
        self.res_box.addWidget(box)
        if t.reaches_edge:
            self.res_box.addWidget(IssueBox("warning", "The sides reach the edge",
                                            "The sloped sides run past the area that could be read, or off the drone "
                                            "map. Side volumes may be larger than shown."))
        if t.no_data_pct > 2:
            self.res_box.addWidget(IssueBox("warning", f"{t.no_data_pct:.0f}% has no height data",
                                            "Part of the outline is off the drone map or in a gap."))
        self.res_box.addWidget(label("Red on the map = cut, blue = fill.", "Hint"))

    def _show_road(self, r):
        cards = [StatCard("Cut", f"{r.cut:,.0f} m³", f"deepest {r.deepest_cut:.2f} m at the centre"),
                 StatCard("Fill", f"{r.fill:,.0f} m³", f"highest {r.highest_fill:.2f} m at the centre"),
                 StatCard("Net", f"{r.net:,.0f} m³", "cut \u2212 fill"),
                 StatCard("Total moved", f"{r.total:,.0f} m³", "cut + fill"),
                 StatCard("Length", f"{r.length:,.0f} m", f"{len(r.stations)} stations"),
                 StatCard("Steepest part", f"{r.steepest:.1f} %", f"near {rdm.station_label(r.steepest_at)}")]
        cards[0].t.setStyleSheet(f"color: {theme.CUT_TEXT};")
        cards[1].t.setStyleSheet(f"color: {theme.FILL_TEXT};")
        g = QGridLayout()
        g.setSpacing(8)
        for i, c in enumerate(cards):
            g.addWidget(c, i // 2, i % 2)
        self.res_box.addLayout(g)
        box = QWidget()
        box.setObjectName("Trucks")
        box.setStyleSheet(f"QWidget#Trucks {{ background: {theme.INFO_SOFT}; border-radius: 8px; }}"
                          "QLabel { background: transparent; }")
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 10, 12, 10)
        v.addWidget(label("Hauling", "H2"))
        v.addWidget(label(r.haul.sentence(), "", wrap=True))
        self.res_box.addWidget(box)
        for title, text in r.warnings:
            self.res_box.addWidget(IssueBox("warning", title, text))
        if r.alignment is not None and r.alignment.curves:
            self.res_box.addWidget(label("Curves: " + ", ".join(
                f"{k}: R {c.radius:.0f} m, {c.length:.1f} m ({rdm.station_label(c.pc_s)}\u2013"
                f"{rdm.station_label(c.pt_s)})" for k, c in enumerate(r.alignment.curves, 1)), "Hint", wrap=True))
        mh = QCheckBox("Earth balance (mass haul) under the map")
        mh.setChecked(self.page.road_profile.mode == "mass")
        mh.toggled.connect(lambda on: self.page.road_profile.set_mode("mass" if on else "profile"))
        self.res_box.addWidget(mh)
        if r.steepest > 12 and not r.standard:
            self.res_box.addWidget(IssueBox("warning", f"Steep: {r.steepest:.1f}%",
                                            "Parts of the road are steeper than most loaded trucks can climb "
                                            "comfortably. Try \u201cKeep slopes gentle\u201d with a lower limit."))
        if r.side_limit:
            self.res_box.addWidget(IssueBox("warning", "Very deep or high sides",
                                            f"At {r.side_limit} station(s) a side slope did not meet the ground within "
                                            f"{rdm.MAX_SIDE:g} m. The real volume there is larger."))
        if r.no_data:
            self.res_box.addWidget(IssueBox("warning", "Off the map",
                                            f"{r.no_data} station(s) are outside the drone map or in a gap."))
        self.res_box.addWidget(label("On the map the strip shows the ground worked: red = mostly cut, blue = mostly "
                                     "fill.", "Hint", wrap=True))
        self.road_box.setVisible(True)
        self.st_slider.blockSignals(True)
        self.st_slider.setRange(0, len(r.stations) - 1)
        self.st_slider.blockSignals(False)
        self.select_station(min(self.st_slider.value(), len(r.stations) - 1))

    def select_station(self, i):
        r = self.takeoff
        if self.kind != "road" or r is None or not (0 <= i < len(r.stations)):
            return
        if self.st_slider.value() != i:
            self.st_slider.blockSignals(True)
            self.st_slider.setValue(i)
            self.st_slider.blockSignals(False)
        st = r.stations[i]
        self.st_label.setText(st.label)
        self.st_info.setText(f"Ground {st.ground:.2f} m, road {st.design:.2f} m ("
                             + (f"cut {st.depth:.2f} m" if st.depth >= 0 else f"fill {-st.depth:.2f} m")
                             + f" at the centre). Cut {st.cut_area:.2f} m², fill {st.fill_area:.2f} m².")
        self.sec_view.set_station(st)
        self.page.select_station(i)

    # ================================================================ shapes in
    def _import_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "Outline from a file", os.path.expanduser("~"),
                                           "GIS, GPS or CAD files (*.kml *.kmz *.gpx *.shp *.gpkg *.geojson *.json "
                                           "*.dxf);;All files (*)")
        if not p or self.page.view is None:
            return
        try:
            shapes = ew.read_shapes(p, self.page.view.crs_wkt, self._lonlat())
        except Exception as e:
            QMessageBox.warning(self, "Cannot read the file", str(e))
            return
        need = 2 if self.kind == "road" else 3
        shapes = [s for s in shapes if len(s["points"]) >= need]
        if not shapes:
            QMessageBox.information(self, "Nothing found", f"The file has no line or area with {need} or more points.")
            return
        s = shapes[0]
        if len(shapes) > 1:
            names = [f"{x['name']}  ({len(x['points'])} corners{'' if x['closed'] else ', open line'})"
                     for x in shapes]
            pick, ok = QInputDialog.getItem(self, "Choose the outline", "This file has several shapes:", names, 0,
                                            False)
            if not ok:
                return
            s = shapes[names.index(pick)]
        if not s["georeferenced"]:
            QMessageBox.information(self, "No coordinate system in the file",
                                    "The file does not say its coordinate system, so its numbers were taken to be in "
                                    "the drone map's own system. Check that the outline lands in the right place.")
        self._set_points(s["points"])

    def _type_points(self):
        if self.page.view is None:
            return
        epsg = rasterio_epsg(self.page._dem_path())
        d = TypePointsDialog(f"EPSG:{epsg}" if epsg else "EPSG:4326", self._lonlat(), self)
        d.dst_wkt = self.page.view.crs_wkt
        if d.exec():
            self._set_points(d.points)
            if self.kind != "road" and len(d.points) < 3:
                self.page._clear()

    def _lonlat(self):
        from ..core.terrain import lonlat
        try:
            return lonlat(self.page.view)
        except Exception:
            return None

    def _set_points(self, pts):
        v = self.page.view
        h, w = v.shape
        t = v.transform
        x0, x1, y0, y1 = t.c, t.c + w * t.a, t.f + h * t.e, t.f
        cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            QMessageBox.warning(self, "Not on this drone map",
                                "The outline is outside this drone map. Check the coordinate system and the order of "
                                "the numbers (easting/northing or northing/easting).")
            return
        self.page.set_shape(pts)
        px = [self.page._px(*p) for p in pts]
        xs, ys = [p[0] for p in px], [p[1] for p in px]
        pad = 0.3 * max(max(xs) - min(xs), max(ys) - min(ys), 10)
        self.page.canvas.fitInView(QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad,
                                          max(ys) - min(ys) + 2 * pad), Qt.KeepAspectRatio)
        self.page.canvas._fitted = False

    # ================================================================ save / export / load
    def _ask_name(self) -> bool:
        default = self.name or {"pad": "Lot plan", "pile": "Pile measurement", "road": "Road plan"}[self.kind]
        name, ok = QInputDialog.getText(self, "Name of the plan", "Name:", text=default)
        if not ok or not name.strip():
            return False
        self.name = name.strip()
        return True

    def save(self) -> str | None:
        if self.takeoff is None or self.page.job is None:
            return None
        if not self.name and not self._ask_name():
            return None
        try:
            self.plan_path = ew.save_plan(self.page.job.job_dir, self.plan())
        except OSError as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return None
        self._record("Saved")
        self.page.readout.setText(f"Plan saved: {self.plan_path}")
        return self.plan_path

    def _record(self, status, folder=""):
        t = self.takeoff
        activity.record(activity.Activity(kind="earthworks", name=self.name, path=self.plan_path,
                                          folder=folder or os.path.dirname(self.plan_path),
                                          size=(f"{t.length:,.0f} m road" if self.kind == "road"
                                                else f"{t.area.area_2d:,.0f} m²"), crs="",
                                          status=f"{status} · cut {t.cut:,.0f} / fill {t.fill:,.0f} m³"))

    def export(self):
        if self.takeoff is None or self.page.job is None:
            return
        if not self.name and not self._ask_name():
            return
        if not self.save():
            return
        job = self.page.job
        epsg = rasterio_epsg(self.page._dem_path())
        folder = os.path.join(ew.plans_dir(job.job_dir), ew.safe(self.name))
        d = ExportDialog(self.name, f"EPSG:{epsg}" if epsg else "EPSG:4326", self._lonlat(), folder, self,
                         road=self.kind == "road")
        if not d.exec():
            return
        t, plan = self.takeoff, self.plan()
        dem, ortho = self.page._dem_path(), self.page.files.get("ortho")
        out, crs = d.folder.path(), d.crs.code()
        info = {"project": d.project.text(), "location": d.location.text(), "prepared_by": d.by.text()}
        fmts, rasters, spacing = d.fmts.selected(), d.rasters.isChecked(), d.spacing.value()
        from ..core import earthworks_report as er, road_report as rr
        dlg = QProgressDialog("Making the report and files…", None, 0, 100, self)
        dlg.setWindowTitle("Report and files")
        dlg.setMinimumDuration(0)
        dlg.setWindowModality(Qt.WindowModal)
        result = {}
        road = self.kind == "road"

        def work(tk):           # stores its own result: queued signals may not be delivered before wait()
            try:
                if road:
                    result["files"] = rr.export_road(t, plan, dem, ortho, out, crs, info, job, fmts,
                                                     progress=tk.report)
                else:
                    result["files"] = er.export_package(t, plan, dem, ortho, out, crs, info, job, fmts, rasters,
                                                        spacing, progress=tk.report)
            except Exception as e:  # shown below in plain words
                import traceback
                traceback.print_exc()
                result["error"] = str(e) or e.__class__.__name__
        task = Task(work, self)
        task.progress.connect(lambda p, s: (dlg.setValue(int(p)), dlg.setLabelText(s)))
        task.finished.connect(dlg.close)
        self._export_task = task
        task.start()
        dlg.exec()
        task.wait()
        if "error" in result:
            QMessageBox.warning(self, "Export failed", result["error"])
            return
        files = result.get("files", [])
        self._record("✔ Report made", out)
        pdf = next((f for f in files if f.endswith(".pdf")), None)
        mb = QMessageBox(self)
        mb.setWindowTitle("Report and files ready")
        mb.setText(f"{len(files)} files written to:\n{out}")
        ob = mb.addButton("Open the report", QMessageBox.AcceptRole)
        fb = mb.addButton("Open the folder", QMessageBox.ActionRole)
        mb.addButton("Close", QMessageBox.RejectRole)
        mb.exec()
        if mb.clickedButton() is ob and pdf:
            system.open_path(pdf)
        elif mb.clickedButton() is fb:
            system.open_path(out)

    def _load_road(self, rd):
        self.r_std.blockSignals(True)
        self.r_std.setCurrentIndex(max(0, self.r_std.findData(rd.standard or "")))
        self.r_std.blockSignals(False)
        self._radii = {i + 1: float(r) for i, r in enumerate(rd.radii)}
        self.curve_rows = []
        _set_radio(self.sgroup, float(rd.interval), "v")
        (self.rb_start_g if rd.start_mode == "ground" else self.rb_start_t).setChecked(True)
        if rd.start_z == rd.start_z:
            self.start_z.setValue(rd.start_z)
        _set_radio(self.pgroup, rd.profile)
        self.max_grade.setValue(rd.max_grade)
        self.offset_m.setValue(rd.offset_m)
        self.min_grade.setValue(rd.min_grade)
        self.max_fill.setValue(rd.max_fill)
        self.max_cut.setValue(rd.max_cut)
        self.end_ground.setChecked(rd.end_mode == "ground")
        if rd.end_z == rd.end_z:
            self.end_z.setValue(rd.end_z)
        self.targets.setPlainText("\n".join(f"{a:g}, {b:.2f}" for a, b in rd.targets))
        self.r_width.setValue(rd.width)
        self.r_crown.setValue(rd.crown_pct)
        self.r_sh.setChecked(rd.shoulders)
        self.r_sh_w.setValue(rd.shoulder_w)
        self.r_sh_p.setValue(rd.shoulder_pct)
        self.r_ditch.setChecked(rd.ditch)
        self.r_ditch_w.setValue(rd.ditch_w)
        self.r_ditch_d.setValue(rd.ditch_d)
        self.r_ditch_in.setValue(rd.ditch_in)
        self.r_ditch_out.setValue(rd.ditch_out)
        self.r_ditch_toe.setChecked(rd.ditch_toe)
        self.r_pccp.setValue(rd.pccp_t * 1000)
        self.r_sub.setValue(rd.subbase_t * 1000)
        self.r_soil.setCurrentIndex(max(0, self.r_soil.findData(rd.soil)))
        self.r_cut.setValue(rd.cut_ratio)
        self.r_fill.setValue(rd.fill_ratio)
        self.r_min_radius.blockSignals(True)
        self.r_min_radius.setValue(rd.min_radius)
        self.r_min_radius.blockSignals(False)
        self.vc_min.setValue(rd.vc_min)
        self._std_note()
        self.swell.setValue(rd.swell_pct)
        self.shrink.setValue(rd.shrink_pct)
        self.truck.setValue(rd.truck_m3)
        (self.vc_yes if rd.vcurves else self.vc_no).setChecked(True)
        i = self.vc_speed.findData(int(rd.speed))
        self.vc_speed.blockSignals(True)
        self.vc_speed.setCurrentIndex(max(0, i))
        self.vc_speed.blockSignals(False)
        self._vc_speed_set = True
        self._pvis_auto = rd.pvis_auto or len(rd.pvis) < 2
        self._pvis = [list(q) + [0.0] * (3 - len(q)) for q in rd.pvis]
        if self._pvis:
            self._fill_pvis(self._pvis)

    def new_plan(self):
        self.name, self.plan_path, self.takeoff = "", "", None
        self._pvis_auto, self._pvis, self._vc_speed_set = True, [], False
        self.vc_yes.setChecked(True)
        self.page._clear()
        self.go(0)

    def load(self, plan: dict, path: str):
        """Show a saved plan (from the Jobs list) at its results."""
        self.name, self.plan_path = plan.get("name", ""), path
        self._choose_type(plan["type"])
        if plan.get("source") in self.page.files:
            self.src.setCurrentIndex(self.src.findData(plan["source"]))
        if plan["type"] == "road":
            self._load_road(rdm.design_from(plan))
        elif plan["type"] == "pad":
            d = ew.design_from(plan)
            (self.rb_bal if d.level_mode == "balanced" else self.rb_fix).setChecked(True)
            if d.level == d.level:
                self.level.setValue(d.level)
            self.slope_cb.setChecked(d.slope_pct > 0)
            if d.slope_pct > 0:
                self.slope.setValue(d.slope_pct)
            self.flow.setCurrentIndex(max(0, self.flow.findData(d.flow_deg)) if d.flow_deg is not None else 0)
            self.soil.setCurrentIndex(max(0, self.soil.findData(d.soil)))
            self.cut_r.setValue(d.cut_ratio)
            self.fill_r.setValue(d.fill_ratio)
            (self.rb_slopes if d.sides == "slopes" else self.rb_walls).setChecked(True)
            self.swell.setValue(d.swell_pct)
            self.shrink.setValue(d.shrink_pct)
            self.truck.setValue(d.truck_m3)
        else:
            p = plan.get("pile") or {}
            for b in self.rgroup.buttons():
                if b.property("key") == p.get("reference", "edge"):
                    b.setChecked(True)
            if p.get("level") is not None:
                self.ref_level.setValue(float(p["level"]))
            self.swell.setValue(float(p.get("swell_pct", ew.PILE_SWELL)))
            self.truck.setValue(float(p.get("truck_m3", 0)))
        self.timer.stop()
        self.page.set_shape([tuple(q) for q in plan["points"]])
        if plan["type"] == "road":
            self._build_curve_rows()
            self.timer.stop()
        self.takeoff = None
        self.go(len(self.steps()) - 1)


def _set_radio(group, key, prop="key"):
    for b in group.buttons():
        if b.property(prop) == key:
            b.setChecked(True)


def rasterio_epsg(path: str):
    import rasterio
    try:
        with rasterio.open(path) as ds:
            return ds.crs.to_epsg()
    except Exception:
        return None
