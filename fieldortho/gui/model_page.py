"""Flood and landslide scenario modelling on a terrain model."""
from __future__ import annotations

import json
import math
import os

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton, QRadioButton, QScrollArea, QSlider,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget, QButtonGroup)

from ..core import activity, crs as crsmod, exporters as ex, flood, jobs, landslide as ls, mapclasses, system
from ..core.model_export import background_for, export_flood, export_landslide, slope_rgb
from ..core.terrain import load_dem, lonlat as dem_lonlat
from . import theme
from .canvas import MapCanvas, map_card
from .inputs import set_state
from .export_dialog import SimpleExportDialog
from .pages_setup import Page, clear_layout
from .widgets import IssueBox, StatCard, label
from .workers import Task

RASTER_ALL = ["gtiff", "asc", "xyz"]
IMG_ALL = ["gtiff", "png", "jpeg", "kmz"]
VEC_ALL = ["gpkg", "shp", "geojson", "kml", "dxf"]


def legend_row(color, text):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    sw = QLabel()
    sw.setFixedSize(18, 14)
    sw.setStyleSheet(f"background: rgb{tuple(color)}; border: 1px solid #888;")
    h.addWidget(sw)
    t = QLabel(text)
    t.setStyleSheet("font-size: 12px;")
    h.addWidget(t, 1)
    return w


class ModelPage(Page):
    home = Signal()
    CRUMB = "Tools"
    TITLE = "Flood & landslide"

    def __init__(self, session):
        super().__init__(session)
        self.source = QComboBox()
        self.source.setMinimumWidth(320)
        self.source.setToolTip("The terrain model (DTM, bare ground) to run the scenarios on.")
        ob = QPushButton("Open elevation file\u2026")
        ob.setObjectName("Primary")
        ob.clicked.connect(self._open_file)
        self.set_actions([QLabel("Terrain"), self.source, ob])

        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        extra = [QLabel("Background:")]
        self.bg_group = QButtonGroup(self)
        for i, t in enumerate(("Photo / relief", "Shaded relief", "Slope steepness")):
            rb = QRadioButton(t)
            rb.setChecked(i == 0)
            self.bg_group.addButton(rb, i)
            extra.append(rb)
        self.bg_group.idClicked.connect(lambda _i: self._show_background())
        self.cont_cb = QCheckBox("Contours")
        self.cont_cb.setChecked(True)
        self.cont_cb.toggled.connect(lambda _v: self._draw_vectors())
        extra += [12, self.cont_cb]
        self.canvas = MapCanvas()
        self.canvas.clicked.connect(self._click)
        self.canvas.double_clicked.connect(self._dclick)
        self.canvas.moved.connect(self._move)
        ll.addWidget(map_card(self.canvas, extra), 1)
        self.readout = label("Choose a terrain model (DTM) above.", "Muted")
        ll.addWidget(self.readout)
        split.addWidget(left)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._flood_tab(), "Flood")
        self.tabs.addTab(self._slide_tab(), "Landslide")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.setMinimumWidth(450)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 1)
        split.setSizes([880, 470])
        self.root.addWidget(split, 1)

        self.dem = None
        self.ortho = None
        self.bg_cache = {}
        self.contours = []
        self.seed = None
        self.ref_z = None
        self.flood_res = None
        self.poly: list[tuple[float, float]] = []
        self.drawing = False
        self.slide_res = None
        self.drain = None
        self.river = None
        self.hand = None
        self.hand_ref = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._run_flood)
        self.source.currentIndexChanged.connect(self._source_changed)
        self._fmode_changed(0)
        self._name = "model"
        self._rise_ft_text(0.0)

    # ================================================================ tabs
    def _flood_tab(self):
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        w = QWidget()
        w.setObjectName("Page")
        w.setStyleSheet("QWidget#Page { background: white; }")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("Type"))
        self.fmode = QComboBox()
        self.fmode.addItem("River or creek rises (water follows the river slope)", "river")
        self.fmode.addItem("Still water: sea, lake or ponding (one flat level)", "still")
        self.fmode.currentIndexChanged.connect(self._fmode_changed)
        mrow.addWidget(self.fmode, 1)
        lay.addLayout(mrow)
        lay.addWidget(label("1. Click on the river / creek (or the sea) where the water comes from.", "", wrap=True))
        self.src_label = label("No source point yet.", "Muted", wrap=True)
        lay.addWidget(self.src_label)
        row = QHBoxLayout()
        self.connect_label = QLabel("Flood spreads")
        row.addWidget(self.connect_label)
        self.connect = QComboBox()
        self.connect.addItem("from the clicked point only", "seeds")
        self.connect.addItem("from the map edge (sea / river leaving the map)", "edges")
        self.connect.addItem("everywhere below the level (incl. closed hollows)", "none")
        self.connect.currentIndexChanged.connect(lambda _i: self._schedule())
        row.addWidget(self.connect, 1)
        lay.addLayout(row)
        lay.addWidget(label("2. Raise the water.", ""))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 200)
        self.slider.valueChanged.connect(self._slider_moved)
        lay.addWidget(self.slider)
        srow = QHBoxLayout()
        self.rise_label = QLabel("Water rise above the riverbed")
        srow.addWidget(self.rise_label)
        self.rise = QDoubleSpinBox()
        self.rise.setRange(0, 200)
        self.rise.setDecimals(2)
        self.rise.setSingleStep(0.1)
        self.rise.setSuffix(" m")
        self.rise.setFixedWidth(120)
        self.rise.setToolTip("In metres (1 m = 3.28 ft). Use the slider or type a value.")
        self.rise.valueChanged.connect(self._rise_changed)
        self.rise.valueChanged.connect(self._rise_ft_text)
        srow.addWidget(self.rise)
        self.rise_ft = label("", "Hint")
        srow.addWidget(self.rise_ft)
        srow.addStretch(1)
        lay.addLayout(srow)
        self.rise_hint = label("", "Hint", wrap=True)
        lay.addWidget(self.rise_hint)
        self.level_label = label("", "Muted", wrap=True)
        lay.addWidget(self.level_label)
        g = QGridLayout()
        self.f_area = StatCard("Flooded area")
        self.f_vol = StatCard("Water volume")
        self.f_max = StatCard("Deepest point")
        self.f_mean = StatCard("Average depth")
        for i, c in enumerate((self.f_area, self.f_vol, self.f_max, self.f_mean)):
            g.addWidget(c, i // 2, i % 2)
        lay.addLayout(g)
        lay.addWidget(label("Depth classes (MGB flood legend)", "H2"))
        self.class_labels = []
        for lo, hi, name, col in flood.CLASSES:
            lr = legend_row(col, name)
            self.class_labels.append(lr.findChildren(QLabel)[-1])
            lay.addWidget(lr)
        self.f_note = QVBoxLayout()
        lay.addLayout(self.f_note)
        lay.addStretch(1)
        lay.addWidget(label("Static inundation model: river mode raises the river by the same height along its "
                            "length (HAND method); still-water mode uses one flat level (sea, lake). Neither "
                            "simulates flow speed, rainfall or timing. Use the terrain model (DTM).", "Muted",
                            wrap=True))
        eb = QPushButton("Export flood scenario\u2026")
        eb.setObjectName("Primary")
        eb.clicked.connect(self._export_flood)
        lay.addWidget(eb)
        sc.setWidget(w)
        return sc

    def _slide_tab(self):
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        w = QWidget()
        w.setObjectName("Page")
        w.setStyleSheet("QWidget#Page { background: white; }")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.addWidget(label("1. Draw the slope to test: tick “Slope steepness” or contours to find steep "
                            "ground (contours close together), then click around the area; double-click to finish.",
                            "", wrap=True))
        r = QHBoxLayout()
        self.draw_btn = QPushButton("✎  Draw area")
        self.draw_btn.setCheckable(True)
        self.draw_btn.toggled.connect(self._toggle_draw)
        r.addWidget(self.draw_btn)
        cb = QPushButton("Clear")
        cb.clicked.connect(self._clear_poly)
        r.addWidget(cb)
        r.addStretch(1)
        lay.addLayout(r)
        self.sel_label = label("No area drawn.", "Muted", wrap=True)
        lay.addWidget(self.sel_label)
        lay.addWidget(label("2. Ground and rain conditions", "H2"))
        f = QFormLayout()
        f.setVerticalSpacing(10)
        f.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.soil = QComboBox()
        for k in ls.SOILS:
            self.soil.addItem(k)
        self.soil.setCurrentIndex(1)
        self.soil.setToolTip("Fills in typical values below; you can change them.")
        self.soil.currentIndexChanged.connect(self._soil_changed)
        f.addRow("Soil type", self.soil)
        self.depth = self._spin(0.3, 20, 2.0, " m", 1)
        self.coh = self._spin(0, 100, 8, " kPa", 1)
        self.phi = self._spin(5, 50, 28, " \u00b0", 1)
        self.gam = self._spin(12, 25, 18, " kN/m\u00b3", 1)
        self._param_hints = {}
        for spin, name, unit_text, typical in (
                (self.depth, "Soil depth", "Depth to the slip surface, in metres. Usually 1\u20135 m.", (0.5, 6)),
                (self.coh, "Cohesion c\u2032", "Soil 'stickiness' in kilopascals (kPa). Usually 0\u201325 kPa; "
                 "sand ~0\u20135, clay ~10\u201325.", (0, 30)),
                (self.phi, "Friction angle \u03c6\u2032", "Internal friction in degrees. Usually 20\u201340\u00b0.",
                 (18, 42)),
                (self.gam, "Unit weight \u03b3", "Weight of the moist soil in kilonewtons per cubic metre. "
                 "Usually 16\u201321 kN/m\u00b3.", (15, 22))):
            cell = QWidget()
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(2)
            spin.setFixedWidth(150)
            cv.addWidget(spin)
            h = label(unit_text, "Hint", wrap=True)
            cv.addWidget(h)
            f.addRow(name, cell)
            self._param_hints[spin] = (h, unit_text, typical)
            spin.valueChanged.connect(lambda _v, sp=spin: self._check_param(sp))
        lay.addLayout(f)
        self.wet = QSlider(Qt.Horizontal)
        self.wet.setRange(0, 100)
        self.wet.setValue(50)
        self.wet_label = QLabel()
        self.wet.valueChanged.connect(self._wet_text)
        lay.addWidget(self.wet_label)
        lay.addWidget(self.wet)
        self.trav = QSlider(Qt.Horizontal)
        self.trav.setRange(8, 40)
        self.trav.setValue(22)
        self.trav_label = QLabel()
        self.trav.valueChanged.connect(self._trav_text)
        lay.addWidget(self.trav_label)
        lay.addWidget(self.trav)
        self.force = QCheckBox("Assume the whole drawn area fails (worst case)")
        lay.addWidget(self.force)
        self.run_btn = QPushButton("▶  Run landslide simulation")
        self.run_btn.setObjectName("Primary")
        self.run_btn.clicked.connect(self._run_slide)
        lay.addWidget(self.run_btn)
        g = QGridLayout()
        self.s_fs = StatCard("Lowest safety factor")
        self.s_rel = StatCard("Sliding area / volume")
        self.s_run = StatCard("Runout distance")
        self.s_aff = StatCard("Area affected")
        for i, c in enumerate((self.s_fs, self.s_rel, self.s_run, self.s_aff)):
            g.addWidget(c, i // 2, i % 2)
        lay.addLayout(g)
        self.s_note = QVBoxLayout()
        lay.addLayout(self.s_note)
        lay.addWidget(legend_row(ls.RELEASE_COLOR, "Source zone (fails: safety factor < 1)"))
        for lo, name, col in ls.VEL_CLASSES:
            lay.addWidget(legend_row(col, "Runout – " + name))
        lay.addWidget(label("Infinite-slope stability with an energy-line runout. A scenario tool for planning "
                            "and awareness, not a geotechnical design.", "Muted", wrap=True))
        eb = QPushButton("Export landslide scenario…")
        eb.setObjectName("Primary")
        eb.clicked.connect(self._export_slide)
        lay.addWidget(eb)
        lay.addStretch(1)
        sc.setWidget(w)
        self._wet_text(50)
        self._trav_text(22)
        return sc

    def _rise_ft_text(self, v):
        self.rise_ft.setText(f"= {v * 3.2808:.1f} ft")

    def _spin(self, lo, hi, v, suffix, dec):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(dec)
        s.setValue(v)
        s.setSuffix(suffix)
        s.setMinimumWidth(130)
        return s

    def _check_param(self, spin):
        h, text, (lo, hi) = self._param_hints[spin]
        v = spin.value()
        unusual = v < lo or v > hi
        set_state(spin, "warn" if unusual else "")
        h.setObjectName("Warn" if unusual else "Hint")
        theme.repolish(h)
        h.setText(("\u26A0 Unusual value. " if unusual else "") + text)

    def _soil_changed(self, _i):
        c, phi, g = ls.SOILS[self.soil.currentText()]
        self.coh.setValue(c)
        self.phi.setValue(phi)
        self.gam.setValue(g)

    def _wet_text(self, v):
        desc = "dry season" if v < 15 else "after normal rain" if v < 45 else "after heavy rain" if v < 80 \
            else "prolonged typhoon rain (saturated)"
        self.wet_label.setText(f"Ground wetness: {v}%  – {desc}")

    def _trav_text(self, v):
        desc = "long-running debris flow" if v < 16 else "soil slide / debris slide" if v < 28 else "short slide / rock fall"
        self.trav_label.setText(f"Travel angle: {v}°  – {desc} (lower = travels further)")

    # ================================================================ sources
    def enter(self):
        cur = self.source.currentData()
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItem("– choose terrain –", None)
        for d in self.s.settings.recent_jobs:
            if not jobs.Job.is_job_dir(d):
                continue
            try:
                j = jobs.Job.load(d)
            except Exception:
                continue
            files = j.summary.get("files", {})
            for k, t in (("dtm", "terrain DTM"), ("dsm", "surface DSM")):
                if k in files and os.path.exists(files[k]):
                    self.source.addItem(f"{j.name} – {t}", (files[k], files.get("ortho"), j.name))
        if cur:
            i = self.source.findData(cur)
            if i < 0:
                self.source.addItem(os.path.basename(cur[0]), cur)
                i = self.source.count() - 1
            self.source.setCurrentIndex(i)
        self.source.blockSignals(False)
        if self.dem is None and self.source.count() > 1 and cur is None:
            self.source.setCurrentIndex(1)

    def use_job(self, job):
        files = job.summary.get("files", {})
        key = "dtm" if "dtm" in files else "dsm"
        if key in files:
            data = (files[key], files.get("ortho"), job.name)
            i = self.source.findData(data)
            if i < 0:
                self.source.addItem(f"{job.name} – {key.upper()}", data)
                i = self.source.count() - 1
            self.source.setCurrentIndex(i)

    def _open_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "Elevation model (GeoTIFF)", os.path.expanduser("~"),
                                           "GeoTIFF (*.tif *.tiff);;All files (*)")
        if p:
            data = (p, None, os.path.splitext(os.path.basename(p))[0])
            self.source.addItem(os.path.basename(p), data)
            self.source.setCurrentIndex(self.source.count() - 1)

    def _source_changed(self, _i):
        data = self.source.currentData()
        if not data:
            return
        path, ortho, name = data
        try:
            self.dem = load_dem(path)
        except Exception as e:
            QMessageBox.warning(self, "Cannot read elevation", str(e))
            return
        self._name = name
        self.ortho = ortho
        self.bg_cache = {}
        self.seed, self.ref_z, self.flood_res, self.slide_res, self.poly = None, None, None, None, []
        self.drain = self.river = self.hand = self.hand_ref = None
        z = self.dem.z
        relief = float(np.nanmax(z) - np.nanmin(z))
        self.slider.setRange(0, max(10, int(math.ceil(relief * 10))))
        self.rise.setRange(0, max(1.0, relief))
        valid = self.dem.valid
        interval = ex_auto_interval(relief)
        self.contours = ex.contour_features(np.where(valid, z, 0), valid, self.dem.transform, interval)
        self.cont_interval = interval
        self._show_background()
        self.readout.setText(f"Terrain {self.dem.shape[1]} × {self.dem.shape[0]} cells of "
                             f"{self.dem.cell:.2f} m, heights {np.nanmin(z):.1f} – {np.nanmax(z):.1f} m. "
                             f"Contours every {interval:g} m.")
        self.src_label.setText("No source point yet.")
        self._update_flood_stats()

    # ================================================================ drawing
    def _show_background(self):
        if self.dem is None:
            return
        mode = self.bg_group.checkedId()
        if mode not in self.bg_cache:
            if mode == 0:
                self.bg_cache[0] = background_for(self.dem, self.ortho)
            elif mode == 1:
                self.bg_cache[1] = background_for(self.dem, None)
            else:
                self.bg_cache[2] = slope_rgb(self.dem)
        self.canvas.set_base(self.bg_cache[mode])
        self._refresh_overlay()
        self._draw_vectors()

    def _refresh_overlay(self):
        if self.tabs.currentIndex() == 0 and (self.flood_res is not None or self.river is not None):
            rgba = flood.overlay_rgba(self.flood_res.depth) if self.flood_res is not None else \
                np.zeros(self.dem.shape + (4,), np.uint8)
            if self.river is not None and self.fmode.currentData() == "river":
                rgba[self.river] = (13, 40, 140, 255)
            self.canvas.set_overlay(rgba)
        elif self.tabs.currentIndex() == 1 and self.slide_res is not None:
            self.canvas.set_overlay(ls.overlay_rgba(self.slide_res))
        else:
            self.canvas.set_overlay(None)

    def _to_px(self, x, y):
        t = self.dem.transform
        return (x - t.c) / t.a, (y - t.f) / t.e

    def _draw_vectors(self):
        if self.dem is None:
            return
        self.canvas.clear_vectors()
        if self.cont_cb.isChecked():
            for f in self.contours:
                xy = np.asarray(f.geometry.coords)
                cols, rows = self._to_px(xy[:, 0], xy[:, 1])
                major = f.properties["MAJOR"]
                self.canvas.add_polyline(list(zip(cols, rows)), "#5D4037" if major else "#8D6E63",
                                         1.3 if major else 0.6)
        if self.tabs.currentIndex() == 0 and self.seed is not None:
            r, c = self.seed
            self.canvas.add_marker(c + 0.5, r + 0.5, "source", "#0D47A1")
        if self.tabs.currentIndex() == 1 and self.poly:
            self.canvas.add_polyline(self.poly, "#D32F2F", 2.2, closed=not self.drawing,
                                     fill=None if (self.drawing or self.slide_res is not None) else (211, 47, 47, 40),
                                     style=Qt.DashLine if self.slide_res is not None else Qt.SolidLine)
            for x, y in self.poly[-1:] if self.drawing else []:
                self.canvas.add_marker(x, y, "", "#D32F2F", 4)

    def _tab_changed(self, i):
        self._refresh_overlay()
        self._draw_vectors()
        self.canvas.set_pick_cursor(i == 0 or self.drawing)

    # ================================================================ mouse
    def _move(self, x, y):
        if self.dem is None:
            return
        r, c = int(y), int(x)
        h, w = self.dem.shape
        if 0 <= r < h and 0 <= c < w:
            X, Y = self.dem.rc_to_xy(r, c)
            z = self.dem.z[r, c]
            txt = f"E {X:,.1f}  N {Y:,.1f}" + (f"   ground {z:.2f} m" if np.isfinite(z) else "")
            if self.flood_res is not None and self.tabs.currentIndex() == 0:
                d = self.flood_res.depth[r, c]
                if np.isfinite(d):
                    txt += f"   water depth {d:.2f} m"
            if self.slide_res is not None and self.tabs.currentIndex() == 1:
                fs = self.slide_res.fs[r, c]
                if np.isfinite(fs):
                    txt += f"   safety factor {fs:.2f}"
                if self.slide_res.affected[r, c]:
                    txt += f"   speed {self.slide_res.velocity[r, c]:.1f} m/s"
            self.readout.setText(txt)

    def _click(self, x, y):
        if self.dem is None:
            return
        if self.tabs.currentIndex() == 0:
            r, c = int(y), int(x)
            h, w = self.dem.shape
            if not (0 <= r < h and 0 <= c < w) or not np.isfinite(self.dem.z[r, c]):
                return
            self.seed = (r, c)
            self.ref_z = flood.reference_elevation(self.dem, r, c)
            if self.fmode.currentData() == "river":
                self._prepare_river()
            else:
                self.src_label.setText(f"Source ground level: {self.ref_z:.2f} m (lowest point near the click).")
                self._draw_vectors()
                self._schedule()
        elif self.drawing:
            self.poly.append((x, y))
            self._draw_vectors()

    def _dclick(self, x, y):
        if self.tabs.currentIndex() == 1 and self.drawing:
            self.draw_btn.setChecked(False)

    def _toggle_draw(self, on):
        self.drawing = on
        if on:
            self.poly = []
            self.slide_res = None
            self._refresh_overlay()
        self.canvas.set_pick_cursor(on)
        self.draw_btn.setText("Finish (or double-click)" if on else "✎  Draw area")
        self._draw_vectors()
        if not on:
            self._selection_info()

    def _clear_poly(self):
        self.poly = []
        self.slide_res = None
        self.draw_btn.setChecked(False)
        self._refresh_overlay()
        self._draw_vectors()
        self.sel_label.setText("No area drawn.")

    def _selection(self):
        if len(self.poly) < 3:
            return None
        return mapclasses.region_mask(self.dem.shape, self.poly)

    def _selection_info(self):
        sel = self._selection()
        if sel is None:
            self.sel_label.setText("Draw at least 3 points.")
            return
        from ..core.terrain import slope_aspect
        s, _ = slope_aspect(self.dem)
        a = sel.sum() * abs(self.dem.transform.a * self.dem.transform.e) / 1e4
        self.sel_label.setText(f"Area {a:.2f} ha, slope average {np.nanmean(s[sel]):.0f}°, "
                               f"steepest {np.nanmax(s[sel]):.0f}°.")

    # ================================================================ flood
    def _slider_moved(self, v):
        self.rise.blockSignals(True)
        self.rise.setValue(v / 10.0)
        self.rise.blockSignals(False)
        self._schedule()

    def _rise_changed(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(v * 10)))
        self.slider.blockSignals(False)
        self._schedule()

    def _schedule(self):
        self.timer.start(80)

    def current_level(self):
        if self.dem is None:
            return None
        mode = self.connect.currentData()
        if mode == "seeds" and self.ref_z is None:
            return None
        base = self.ref_z if self.ref_z is not None else float(np.nanmin(self.dem.z))
        return base + self.rise.value()

    def _fmode_changed(self, _i):
        river = self.fmode.currentData() == "river"
        self.rise_label.setText("Water rise above the riverbed" if river else "Water level above the source")
        self.rise_hint.setText(
            "In metres: how high the river stands above its normal bed, along its whole length. "
            "Typical flood heights are 0.5\u20135 m." if river else
            "In metres, above the ground level where you clicked (or above the lowest ground if spreading "
            "from the map edge). For storm surge use the surge height.")
        self.connect.setVisible(not river)
        self.connect_label.setVisible(not river)
        if river and self.seed is not None and self.river is None:
            self._prepare_river()
        else:
            self._schedule()

    def _prepare_river(self):
        """Drainage network (once per terrain), then the river through the clicked point."""
        from ..core import hydro
        dem, seed = self.dem, self.seed
        drain = self.drain
        self.src_label.setText("Finding the river network\u2026 (first time for this terrain takes a few seconds)")

        def work(_t):
            d = drain or hydro.drainage(dem)
            riv, snap, a = hydro.river_network(d, seed)
            h, ref = hydro.hand(dem, d, riv)
            return d, riv, snap, a, h, ref
        t = Task(work, self)
        t.done.connect(self._river_ready)
        t.failed.connect(lambda e: QMessageBox.warning(self, "River network", e))
        self._rtask = t
        t.start()

    def _river_ready(self, out):
        self.drain, self.river, snap, area, self.hand, self.hand_ref = out
        self.seed = (int(snap[0]), int(snap[1]))
        self.ref_z = float(self.dem.z[self.seed])
        cell_ha = abs(self.dem.transform.a * self.dem.transform.e) / 1e4
        self.src_label.setText(f"River at the click: bed {self.ref_z:.2f} m, drains {area * cell_ha:,.1f} ha "
                               f"upstream (within this map). The river and its similar-sized branches are shown "
                               f"in dark blue.")
        self._draw_vectors()
        self._schedule()

    def _run_flood(self):
        if self.fmode.currentData() == "river":
            clear_layout(self.f_note)
            if self.hand is None:
                if self.dem is not None:
                    self.f_note.addWidget(IssueBox("info", "Click on a river or creek to start."))
                return
            self.flood_res = flood.simulate_river(self.dem, self.rise.value(), self.hand, self.hand_ref, self.river)
            self.level_label.setText(f"River {self.rise.value():.2f} m above its bed along its whole length "
                                     f"(at the click: water surface {self.ref_z + self.rise.value():.2f} m).")
            self._refresh_overlay()
            self._update_flood_stats()
            return
        lvl = self.current_level()
        clear_layout(self.f_note)
        if lvl is None:
            if self.dem is not None:
                self.f_note.addWidget(IssueBox("info", "Click the map to set where the water comes from."))
            return
        mode = self.connect.currentData()
        self.flood_res = flood.simulate(self.dem, lvl, [self.seed] if self.seed else None, mode)
        base = "the source" if self.ref_z is not None else "the lowest ground"
        self.level_label.setText(f"Water surface at {lvl:.2f} m elevation ({self.rise.value():.2f} m above {base}).")
        self._refresh_overlay()
        self._update_flood_stats()

    def _update_flood_stats(self):
        r = self.flood_res
        if r is None:
            for c in (self.f_area, self.f_vol, self.f_max, self.f_mean):
                c.set("-")
            return
        s = r.stats
        self.f_area.set(f"{s['area_ha']:.2f} ha")
        self.f_vol.set(f"{s['volume_m3']:,.0f} m³")
        self.f_max.set(f"{s['max_depth_m']:.2f} m")
        self.f_mean.set(f"{s['mean_depth_m']:.2f} m")
        for lab, (lo, hi, name, col) in zip(self.class_labels, flood.CLASSES):
            lab.setText(f"{name}:  {s['classes'][name]:.2f} ha")

    def _export_flood(self):
        if self.flood_res is None:
            QMessageBox.information(self, "Nothing to export", "Set a source point and raise the water first.")
            return
        d = SimpleExportDialog("Export flood scenario",
                               [("Water depth (m)", RASTER_ALL, ["gtiff"]), ("Flood map image", IMG_ALL, ["png"])],
                               [("Flood zones (MGB depth classes)", VEC_ALL, ["gpkg", "shp"])],
                               self._default_folder("flood"), dem_lonlat(self.dem), parent=self)
        if d.exec():
            name = f"{self._name}_{'river' if self.fmode.currentData() == 'river' else 'flood'}_{self.rise.value():.1f}m"
            try:
                files = export_flood(self.dem, self.flood_res, d.folder.path(), name, d.crs.code(), d.selection(),
                                     f"Source ground level {self.ref_z:.2f} m." if self.ref_z is not None else "")
            except Exception as e:
                QMessageBox.warning(self, "Export failed", str(e))
                return
            a = self.flood_res.stats.get("area_ha", 0)
            self._save_scenario("flood", d.folder.path(), name, d.crs.code(),
                                f"\u2714 Exported \u00b7 {self.rise.value():.1f} m, {a:,.1f} ha")
            self._done(files, d.folder.path())

    # ================================================================ landslide
    def params(self) -> ls.Params:
        return ls.Params(soil_depth=self.depth.value(), cohesion=self.coh.value(), friction=self.phi.value(),
                         unit_weight=self.gam.value(), wetness=self.wet.value() / 100.0,
                         travel_angle=float(self.trav.value()), force_release=self.force.isChecked())

    def _run_slide(self):
        if self.dem is None:
            return
        sel = self._selection()
        if sel is None:
            QMessageBox.information(self, "Draw an area", "Draw the slope to test first (Draw area).")
            return
        p = self.params()
        dem = self.dem
        self.run_btn.setEnabled(False)
        self.run_btn.setText("Simulating…")
        t = Task(lambda _t: ls.simulate(dem, sel, p), self)
        t.done.connect(self._slide_done)
        t.failed.connect(lambda e: (QMessageBox.warning(self, "Simulation", e), self._slide_reset()))
        self._task = t
        t.start()

    def _slide_reset(self):
        self.run_btn.setEnabled(True)
        self.run_btn.setText("▶  Run landslide simulation")

    def _slide_done(self, res):
        self._slide_reset()
        self.slide_res = res
        s = res.stats
        clear_layout(self.s_note)
        fs = s["min_fs"]
        self.s_fs.set(f"{fs:.2f}" if np.isfinite(fs) else "-", "< 1 means it fails")
        self.s_rel.set(f"{s['release_ha']:.2f} ha", f"≈ {s['volume_m3']:,.0f} m³ of soil")
        if s.get("runout_length_m"):
            self.s_run.set(f"{s['runout_length_m']:.0f} m", f"drop {s['drop_m']:.0f} m, up to {s['max_velocity_ms']:.0f} m/s")
        else:
            self.s_run.set("-")
        self.s_aff.set(f"{s['affected_ha']:.2f} ha")
        if s["release_ha"] == 0:
            p = self.params()
            crit = ls.critical_wetness(s["max_slope_deg"], p)
            hint = (f" The steepest part would fail at about {crit:.0%} wetness." if crit is not None else
                    " Try a weaker soil, a deeper soil or the worst-case option.")
            self.s_note.addWidget(IssueBox("ok", "Stable under these conditions",
                                           f"Lowest safety factor {fs:.2f} (needs < 1 to fail)." + hint))
        self._refresh_overlay()
        self._draw_vectors()

    def _export_slide(self):
        if self.slide_res is None:
            QMessageBox.information(self, "Nothing to export", "Run a landslide simulation first.")
            return
        d = SimpleExportDialog("Export landslide scenario",
                               [("Landslide zones image", IMG_ALL, ["png"]), ("Factor of safety", RASTER_ALL, ["gtiff"]),
                                ("Velocity (m/s)", RASTER_ALL, [])],
                               [("Release and runout zones", VEC_ALL, ["gpkg", "shp"])],
                               self._default_folder("landslide"), dem_lonlat(self.dem), parent=self)
        if d.exec():
            try:
                files = export_landslide(self.dem, self.slide_res, self.params(), d.folder.path(),
                                         f"{self._name}_landslide", d.crs.code(), d.selection())
            except Exception as e:
                QMessageBox.warning(self, "Export failed", str(e))
                return
            a = self.slide_res.stats.get("affected_ha", 0)
            self._save_scenario("landslide", d.folder.path(), f"{self._name}_landslide", d.crs.code(),
                                f"\u2714 Exported \u00b7 {a:,.2f} ha affected")
            self._done(files, d.folder.path())

    # ================================================================ helpers
    def _default_folder(self, kind):
        data = self.source.currentData()
        base = os.path.dirname(data[0]) if data else os.path.expanduser("~")
        if os.path.basename(base) == "GIS":
            base = os.path.dirname(base)
        return os.path.join(base, "Scenarios", kind)

    def _save_scenario(self, kind, folder, name, crs_code, status):
        """Write <name>.scenario.json next to the outputs and list it in Jobs."""
        data = self.source.currentData()
        if not data or self.dem is None:
            return
        t = self.dem.transform
        sc = {"kind": kind, "dem": data[0], "ortho": data[1], "name": data[2], "tab": self.tabs.currentIndex(),
              "flood": {"mode": self.fmode.currentData(), "connect": self.connect.currentData(),
                        "rise": self.rise.value(),
                        "seed_xy": list(self.dem.rc_to_xy(*self.seed)) if self.seed else None},
              "landslide": {"soil": self.soil.currentIndex(), "depth": self.depth.value(), "coh": self.coh.value(),
                            "phi": self.phi.value(), "gam": self.gam.value(), "wet": self.wet.value(),
                            "trav": self.trav.value(), "force": self.force.isChecked(),
                            "poly_xy": [[t.c + x * t.a, t.f + y * t.e] for x, y in self.poly]}}
        path = os.path.join(folder, f"{name}.scenario.json")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(sc, fh, indent=1)
        except OSError:
            return
        activity.record(activity.Activity(kind=kind, name=name, path=path, folder=folder,
                                          size=f"Terrain {self.dem.cell:.2f} m cells",
                                          crs=crsmod.label(crs_code) if crs_code else "", status=status))

    def load_scenario(self, path: str):
        try:
            with open(path, encoding="utf-8") as fh:
                sc = json.load(fh)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Cannot open scenario", f"{os.path.basename(path)}: {e}")
            return
        if not os.path.isfile(sc.get("dem", "")):
            QMessageBox.warning(self, "Terrain missing", f"The terrain model is no longer at:\n{sc.get('dem')}")
            return
        data = (sc["dem"], sc.get("ortho"), sc.get("name") or "model")
        i = self.source.findData(data)
        if i < 0:
            self.source.addItem(f"{data[2]} \u2013 {os.path.basename(data[0])}", data)
            i = self.source.count() - 1
        if self.source.currentIndex() == i:
            self._source_changed(i)
        else:
            self.source.setCurrentIndex(i)
        if self.dem is None:
            return
        f, s_ = sc.get("flood", {}), sc.get("landslide", {})
        self.soil.setCurrentIndex(s_.get("soil", self.soil.currentIndex()))
        for spin, k in ((self.depth, "depth"), (self.coh, "coh"), (self.phi, "phi"), (self.gam, "gam")):
            if k in s_:
                spin.setValue(s_[k])
        self.wet.setValue(int(s_.get("wet", self.wet.value())))
        self.trav.setValue(int(s_.get("trav", self.trav.value())))
        self.force.setChecked(bool(s_.get("force", False)))
        self.fmode.setCurrentIndex(max(0, self.fmode.findData(f.get("mode", "river"))))
        self.connect.setCurrentIndex(max(0, self.connect.findData(f.get("connect", "seeds"))))
        t = self.dem.transform
        if sc.get("kind") == "landslide":
            self.tabs.setCurrentIndex(1)
            self.poly = [((x - t.c) / t.a, (y - t.f) / t.e) for x, y in s_.get("poly_xy", [])]
            self._draw_vectors()
            if len(self.poly) >= 3:
                self._run_slide()
        else:
            self.tabs.setCurrentIndex(0)
            self.rise.setValue(float(f.get("rise", 0)))
            if f.get("seed_xy"):
                x, y = f["seed_xy"]
                self._click((x - t.c) / t.a, (y - t.f) / t.e)

    def _done(self, files, folder):
        if QMessageBox.question(self, "Export finished", f"{len(files)} files written.\n\nOpen the folder?") \
                == QMessageBox.Yes:
            system.open_path(folder)


def ex_auto_interval(relief: float) -> float:
    from ..core.gisexport import auto_interval
    return auto_interval(relief)
