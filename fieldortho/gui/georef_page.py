"""Georeferencer screen: open a map image -> grid detected -> confirm labels -> export."""
from __future__ import annotations

from .. import APP_NAME

import math
import os

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QProgressBar, QPushButton, QScrollArea, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..core import activity, crs as crsmod, georef as G, mapclasses as M, system
from ..core.georef_export import GeorefExport, export as georef_export
from . import theme
from .canvas import MapCanvas, map_card, qimage_from
from .export_widgets import CrsPicker, FolderPicker, FormatChecks
from .inputs import CoordDialog, DmsEdit, IntervalEdit
from .pages_setup import Page, clear_layout
from .widgets import IssueBox, card, label
from .workers import Task


class ClickLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, _e):
        self.clicked.emit()


class GeorefPage(Page):
    home = Signal()
    CRUMB = "Tools"
    TITLE = "Georeference a map"

    def __init__(self, session):
        super().__init__(session)
        acts = []
        for text, fn, primary in (("Save session", lambda: self.save_session(), False),
                                  ("Load session\u2026", lambda: self.load_session(), False),
                                  ("Open map image\u2026", lambda: self.open_image(), True)):
            b = QPushButton(text)
            if primary:
                b.setObjectName("Primary")
            b.clicked.connect(fn)
            acts.append(b)
        self.set_actions(acts)

        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        self.canvas = MapCanvas()
        self.canvas.clicked.connect(self._canvas_click)
        self.canvas.moved.connect(self._canvas_move)
        self.file_info = label("No map open", "Muted")
        ll.addWidget(map_card(self.canvas, [self.file_info]), 1)
        self.readout = label("Open a scanned or exported map with a coordinate grid (JPG, PNG, TIF).", "Muted")
        ll.addWidget(self.readout)
        split.addWidget(left)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._grid_tab(), "\u2460  Grid")
        self.tabs.addTab(self._points_tab(), "\u2461  Control points")
        self.tabs.addTab(self._export_tab(), "\u2462  Export")
        self.tabs.setMinimumWidth(500)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 1)
        split.setSizes([860, 520])
        self.root.addWidget(split, 1)

        self.image_path = ""
        self.rgb: np.ndarray | None = None
        self.det: G.Detection | None = None
        self.gcps: list[G.GCP] = []
        self.fit: G.Fit | None = None
        self.adding = False
        self.task = None

    # ================================================================ tabs
    def _grid_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        self.det_box = QVBoxLayout()
        lay.addLayout(self.det_box)
        lay.addWidget(IssueBox("info", "Type the numbers only \u2013 no \u00b0 symbol needed",
                               "Read each grid label from its small picture and type the degrees, minutes and "
                               "seconds into the boxes. Space or Tab jumps to the next box; choose E/W or N/S "
                               "from the list. Click a picture to zoom the map to that label."))
        lay.addWidget(label("MAP DATUM (PRINTED ON THE MAP)", "Section"))
        self.grid_crs = QComboBox()
        for code, text in crsmod.GRID_CHOICES:
            self.grid_crs.addItem(text, code)
        self.grid_crs.setToolTip("Check the map's datum note (e.g. 'Datum: Luzon 1911'). PRS92 maps say PRS92.")
        lay.addWidget(self.grid_crs)
        lay.addWidget(label("GRID SPACING", "Section"))
        self.interval = IntervalEdit(1, 0)
        self.interval.setToolTip("The difference between two neighbouring grid labels, e.g. 1 minute.")
        lay.addWidget(self.interval)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        holder = QWidget()
        holder.setObjectName("Page")
        holder.setStyleSheet("QWidget#Page { background: white; }")
        self.labels_lay = QVBoxLayout(holder)
        self.labels_lay.setContentsMargins(0, 4, 4, 0)
        self.labels_lay.setSpacing(6)
        sc.setWidget(holder)
        lay.addWidget(sc, 1)
        frow = QHBoxLayout()
        fill = QPushButton("Fill the rest from spacing")
        fill.setToolTip("Type the first longitude and the first latitude, then fill the other lines.")
        fill.clicked.connect(self._fill_series)
        frow.addWidget(fill)
        frow.addStretch(1)
        self.make_btn = QPushButton("Next: create control points  \u2192")
        self.make_btn.setObjectName("Primary")
        self.make_btn.clicked.connect(self._make_points)
        frow.addWidget(self.make_btn)
        lay.addLayout(frow)
        self.value_edits: list[tuple[G.GridLine, DmsEdit]] = []
        self._row_msgs: dict[int, tuple[QLabel, QLabel]] = {}
        return w

    def _points_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        row = QHBoxLayout()
        row.addWidget(QLabel("Method"))
        self.order = QComboBox()
        self.order.addItem("Affine (straight grid; recommended)", 1)
        self.order.addItem("Polynomial 2 (warped scans, 6+ points)", 2)
        self.order.addItem("Polynomial 3 (badly warped scans, 10+ points)", 3)
        self.order.currentIndexChanged.connect(self._refit)
        row.addWidget(self.order, 1)
        lay.addLayout(row)
        self.quality = QVBoxLayout()
        lay.addLayout(self.quality)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["#", "Use", "Pixel X", "Pixel Y", "Grid X / Lon", "Grid Y / Lat",
                                              "Error (m)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.cellChanged.connect(self._cell_changed)
        self.table.cellDoubleClicked.connect(self._zoom_point)
        lay.addWidget(self.table, 1)
        r2 = QHBoxLayout()
        self.add_btn = QPushButton("\uFF0B  Add point by clicking")
        self.add_btn.setToolTip("Click on the map where you know the coordinates; a window asks for them.")
        self.add_btn.setCheckable(True)
        self.add_btn.toggled.connect(self._toggle_add)
        r2.addWidget(self.add_btn)
        dl = QPushButton("Delete selected")
        dl.clicked.connect(self._delete_points)
        r2.addWidget(dl)
        r2.addStretch(1)
        imp = QPushButton("Import .points…")
        imp.clicked.connect(self._import_points)
        r2.addWidget(imp)
        lay.addLayout(r2)
        return w

    def _export_tab(self):
        w = QWidget()
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("Page")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.addWidget(label("Coordinate system of the output", "H2"))
        self.out_crs = CrsPicker(default="EPSG:4326")
        lay.addWidget(self.out_crs)
        lay.addWidget(label("Georeferenced image", "H2"))
        self.img_fmts = FormatChecks(["gtiff", "png", "jpeg", "kmz"], False, ["gtiff"])
        lay.addWidget(self.img_fmts)
        g = QGridLayout()
        self.crop = QCheckBox("Keep only the map (cut off legend and margins)")
        self.crop.setChecked(True)
        g.addWidget(self.crop, 0, 0, 1, 2)
        g.addWidget(QLabel("Resampling"), 1, 0)
        self.resamp = QComboBox()
        self.resamp.addItem("Cubic - smooth (scans, photos)", "cubic")
        self.resamp.addItem("Nearest - keeps exact colours (hazard/class maps)", "nearest")
        self.resamp.addItem("Bilinear", "bilinear")
        g.addWidget(self.resamp, 1, 1)
        lay.addLayout(g)
        lay.addWidget(label("Control points", "H2"))
        self.pts_qgis = QCheckBox("QGIS Georeferencer file (.points)")
        self.pts_qgis.setChecked(True)
        lay.addWidget(self.pts_qgis)
        self.pts_vec = FormatChecks(["gpkg", "shp", "kml"], True, [])
        lay.addWidget(self.pts_vec)
        lay.addWidget(label("Map classes → polygons", "H2"))
        crow = QHBoxLayout()
        self.cls_on = QCheckBox("Extract the coloured zones as")
        crow.addWidget(self.cls_on)
        self.cls_preset = QComboBox()
        for k, p in M.PRESETS.items():
            self.cls_preset.addItem(p.label, k)
        crow.addWidget(self.cls_preset, 1)
        lay.addLayout(crow)
        self.cls_vec = FormatChecks(["gpkg", "shp", "geojson", "kml", "dxf"], True, ["gpkg", "shp"])
        lay.addWidget(self.cls_vec)
        self.cls_ras = FormatChecks(["gtiff", "png", "kmz"], False, ["gtiff"])
        lay.addWidget(self.cls_ras)
        prev = QPushButton("Preview classes")
        prev.clicked.connect(self._preview_classes)
        lay.addWidget(prev, 0, Qt.AlignLeft)
        lay.addWidget(label("Save to folder", "H2"))
        self.out_dir = FolderPicker(os.path.join(os.path.expanduser("~"), "Documents", f"{APP_NAME} Georeferenced"))
        lay.addWidget(self.out_dir)
        self.exp_bar = QProgressBar()
        self.exp_bar.setVisible(False)
        lay.addWidget(self.exp_bar)
        self.exp_status = label("", "Muted", wrap=True)
        lay.addWidget(self.exp_status)
        self.exp_btn = QPushButton("Export georeferenced map")
        self.exp_btn.setObjectName("Primary")
        self.exp_btn.clicked.connect(self._export)
        lay.addWidget(self.exp_btn)
        lay.addStretch(1)
        sc.setWidget(inner)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(sc)
        return w

    # ================================================================ image + detection
    def open_image(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Map image", os.path.expanduser("~"),
                                                  "Images (*.jpg *.jpeg *.png *.tif *.tiff *.bmp)")
        if not path:
            return
        self.image_path = path
        self.rgb = G.load_rgb(path)
        self.canvas.set_base(self.rgb)
        self.gcps, self.fit, self.det = [], None, None
        self.file_info.setText(f"{os.path.basename(path)} \u00b7 {self.rgb.shape[1]} \u00d7 {self.rgb.shape[0]} px")
        self.readout.setText("Looking for the map frame and grid\u2026")
        name = os.path.splitext(os.path.basename(path))[0]
        self.out_dir.edit.setText(os.path.join(os.path.dirname(path), name + "_georeferenced"))
        self._name = name
        rgb = self.rgb
        self.task = Task(lambda t: G.detect(rgb), self)
        self.task.done.connect(self._detected)
        self.task.failed.connect(self._detect_failed)
        self.task.start()

    def _detect_failed(self, msg):
        self.det = None
        clear_layout(self.det_box)
        self.det_box.addWidget(IssueBox("warning", "No coordinate grid found automatically", msg +
                                        " Use “Add point by clicking” on the Control points tab."))
        self._fill_label_rows()
        self.readout.setText("Grid not detected – add control points manually.")

    def _detected(self, det: G.Detection):
        self.det = det
        clear_layout(self.det_box)
        agree = G.tick_cross_agreement(det)
        txt = (f"{len(det.meridians)} vertical and {len(det.parallels)} horizontal grid lines, "
               f"{len(det.crosses)} grid crosses.")
        if agree is not None:
            txt += f" Ticks and crosses agree within {agree:.2f} px."
        lvl = "ok" if det.meridians and det.parallels else "warning"
        self.det_box.addWidget(IssueBox(lvl, "Map frame and grid found" if lvl == "ok" else "Grid incomplete",
                                        txt + (" " + " ".join(det.notes) if det.notes else "")))
        self._fill_label_rows()
        self._draw()
        self.readout.setText("Scroll to zoom, drag to move. Type the grid values on the right.")

    def _fill_label_rows(self):
        clear_layout(self.labels_lay)
        self.value_edits = []
        self._row_msgs = {}
        if not self.det:
            self.labels_lay.addStretch(1)
            return
        for title, lines, is_lat in (("LONGITUDE \u2013 VERTICAL GRID LINES, LEFT \u2192 RIGHT", self.det.meridians, False),
                                     ("LATITUDE \u2013 HORIZONTAL GRID LINES, TOP \u2192 BOTTOM", self.det.parallels, True)):
            self.labels_lay.addSpacing(4)
            self.labels_lay.addWidget(label(title, "Section"))
            for ln in lines:
                row = QHBoxLayout()
                row.setSpacing(8)
                x0, y0, x1, y1 = G.label_box(self.det, ln)
                crop = self.rgb[y0:y1, x0:x1]
                pic = ClickLabel()
                if crop.size:
                    pm = QPixmap.fromImage(qimage_from(np.ascontiguousarray(crop)))
                    pic.setPixmap(pm.scaled(146, 58, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                pic.setFixedSize(150, 62)
                pic.setAlignment(Qt.AlignCenter)
                pic.setToolTip("Click to zoom the map to this label")
                pic.clicked.connect(lambda cx=(x0 + x1) / 2, cy=(y0 + y1) / 2: self.canvas.zoom_to(cx, cy, 5))
                pic.setCursor(Qt.PointingHandCursor)
                pic.setStyleSheet(f"border: 1px solid {theme.LINE}; border-radius: 4px; background: white;")
                row.addWidget(pic)
                col = QVBoxLayout()
                col.setSpacing(2)
                ed = DmsEdit(is_lat)
                if ln.value is not None:
                    ed.set_value(ln.value)
                ed.changed.connect(self._check_values)
                er = QHBoxLayout()
                er.addWidget(ed)
                mark = QLabel("")
                mark.setFixedWidth(18)
                er.addWidget(mark)
                er.addStretch(1)
                col.addLayout(er)
                msg = label("", "Error", wrap=True)
                msg.setVisible(False)
                col.addWidget(msg)
                row.addLayout(col, 1)
                self.labels_lay.addLayout(row)
                self.value_edits.append((ln, ed))
                self._row_msgs[id(ed)] = (mark, msg)
        self.labels_lay.addStretch(1)

    def _fill_series(self):
        step = self.interval.degrees()
        if step is None:
            QMessageBox.warning(self, "Grid spacing", "Type the grid spacing first, e.g. 1 minute.")
            return
        done = False
        for kind, sign in (("meridian", 1), ("parallel", -1)):    # latitude decreases downwards
            rows = [(ln, ed) for ln, ed in self.value_edits if ln.kind == kind]
            first = next(((i, ed) for i, (ln, ed) in enumerate(rows) if ed.value() is not None), None)
            if not first:
                continue
            i0, ed0 = first
            v0 = ed0.value()
            for i, (ln, ed) in enumerate(rows):
                if i != i0:
                    ed.set_value(v0 + sign * (i - i0) * step, auto=True)
            done = True
        if not done:
            QMessageBox.information(self, "Fill the rest", "Type the first longitude and the first latitude, "
                                                           "then press this button again.")
        self._check_values()

    def _check_values(self) -> bool:
        ok = True
        for ln, ed in self.value_edits:
            lvl, msg, v = ed.check()
            ln.value = v if lvl in ("", "warn") else None
            mark, lab = self._row_msgs.get(id(ed), (None, None))
            if mark is None:
                continue
            if lvl == "error":
                ok = False
            auto = (ed.deg.property("state") or "") == "auto"
            mark.setText({"": "\u2714", "warn": "\u26A0", "error": "\u2716", "empty": ""}[lvl])
            mark.setStyleSheet(f"color: {dict(error=theme.ERROR, warn=theme.WARN).get(lvl, theme.OK)}; "
                               "font-size: 14px;")
            mark.setToolTip("filled in from the spacing" if auto else "")
            lab.setObjectName("Error" if lvl == "error" else "Warn")
            theme.repolish(lab)
            lab.setText(msg)
            lab.setVisible(bool(msg))
        # consistency: equal spacing in pixels must mean equal spacing in degrees
        for kind in ("meridian", "parallel"):
            pairs = [(ln, ed) for ln, ed in self.value_edits if ln.kind == kind and ln.value is not None]
            if len(pairs) >= 3:
                pos = np.array([ln.pos for ln, _ in pairs])
                val = np.array([ln.value for ln, _ in pairs])
                a, b = np.polyfit(pos, val, 1)
                resid = np.abs(val - (a * pos + b))
                if resid.max() > 0.2 * abs(a) * np.diff(np.sort(pos)).min():
                    ok = False
                    ln, ed = pairs[int(resid.argmax())]
                    mark, lab = self._row_msgs[id(ed)]
                    mark.setText("\u26A0")
                    mark.setStyleSheet(f"color: {theme.WARN}; font-size: 14px;")
                    lab.setObjectName("Warn")
                    theme.repolish(lab)
                    lab.setText("This value does not fit the even spacing of the other lines \u2013 check for a "
                                "typo.")
                    lab.setVisible(True)
                    self.readout.setText("\u26A0 The values are not evenly spaced \u2013 check for a typo.")
        return ok

    def _make_points(self):
        if not self.det:
            QMessageBox.information(self, "No grid", "No grid was detected; add control points by clicking.")
            return
        if not self._check_values():
            if QMessageBox.question(self, "Check the values",
                                    "Some values look wrong (marked red or orange). Continue anyway?") \
                    != QMessageBox.Yes:
                return
        manual = [g for g in self.gcps if g.source == "manual"]
        self.gcps = G.make_gcps(self.det) + manual
        if len(self.gcps) < 3:
            QMessageBox.information(self, "Not enough values",
                                    "Type at least 2 longitude and 2 latitude values (or type the first of each "
                                    "and press \u201cFill the rest from spacing\u201d).")
            return
        self.tabs.setCurrentIndex(1)
        self._refit()

    # ================================================================ points
    def _grid_code(self) -> str:
        return self.grid_crs.currentData()

    def _refit(self):
        self.fit = None
        clear_layout(self.quality)
        order = self.order.currentData()
        if self.gcps:
            try:
                self.fit = G.fit(self.gcps, self._grid_code(), order)
            except ValueError as e:
                self.quality.addWidget(IssueBox("warning", "Cannot compute yet", str(e)))
        if self.fit:
            f = self.fit
            px = f.rmse_m / f.pixel_m if f.pixel_m else 0
            lvl = "ok" if px < 1 else ("warning" if px < 3 else "error")
            verdict = {"ok": "Excellent", "warning": "Acceptable", "error": "Poor - check the points"}[lvl]
            self.quality.addWidget(IssueBox(
                lvl, f"{verdict}: average error {f.rmse_m:.2f} m ({px:.2f} pixel)",
                f"{f.n} points used, largest error {f.max_m:.2f} m. One map pixel = {f.pixel_m:.2f} m on the "
                f"ground. Map rotation {G.rotation_deg(f):+.3f}°. "
                + crsmod.datum_note(self._grid_code(), "EPSG:4326", *f.lonlat)))
            self.out_crs.set_lonlat(f.lonlat)
        self._fill_table()
        self._draw()

    def _fill_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        geo = crsmod.is_geographic(self._grid_code())
        for i, g in enumerate(self.gcps):
            self.table.insertRow(i)
            it = QTableWidgetItem(str(i + 1))
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(i, 0, it)
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            use.setCheckState(Qt.Checked if g.enabled else Qt.Unchecked)
            self.table.setItem(i, 1, use)
            for c, v in ((2, f"{g.px:.2f}"), (3, f"{g.py:.2f}"),
                         (4, G.format_dms(g.gx, False) if geo else f"{g.gx:.3f}"),
                         (5, G.format_dms(g.gy, True) if geo else f"{g.gy:.3f}")):
                self.table.setItem(i, c, QTableWidgetItem(v))
            e = QTableWidgetItem("" if g.res_m is None else f"{g.res_m:.2f}" + ("" if g.enabled else " (not used)"))
            e.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if g.res_m is not None and self.fit and g.res_m > 3 * max(self.fit.rmse_m, self.fit.pixel_m * 0.5):
                e.setForeground(Qt.red)
            self.table.setItem(i, 6, e)
        self.table.blockSignals(False)

    def _cell_changed(self, r, c):
        if not (0 <= r < len(self.gcps)):
            return
        g = self.gcps[r]
        try:
            if c == 1:
                g.enabled = self.table.item(r, 1).checkState() == Qt.Checked
            elif c == 2:
                g.px = float(self.table.item(r, 2).text())
            elif c == 3:
                g.py = float(self.table.item(r, 3).text())
            elif c == 4:
                t = self.table.item(r, 4).text()
                g.gx = G.parse_angle(t) if crsmod.is_geographic(self._grid_code()) else float(t)
            elif c == 5:
                t = self.table.item(r, 5).text()
                g.gy = G.parse_angle(t) if crsmod.is_geographic(self._grid_code()) else float(t)
        except ValueError as e:
            QMessageBox.warning(self, "Value", str(e))
        self._refit()

    def _toggle_add(self, on):
        self.adding = on
        self.canvas.set_pick_cursor(on)
        self.readout.setText("Click exactly on a point whose coordinates you know (zoom in first)."
                             if on else "Scroll to zoom, drag to move.")

    def _canvas_click(self, x, y):
        if not self.adding or self.rgb is None:
            return
        code = self._grid_code()
        d = CoordDialog(crsmod.is_geographic(code), crsmod.label(code), x, y, self)
        if not d.exec():
            return
        vals = d.values()
        if vals is None:
            return
        gx, gy = vals
        self.gcps.append(G.GCP(x, y, gx, gy, True, "manual", f"P{len(self.gcps) + 1}"))
        self._refit()

    def _canvas_move(self, x, y):
        if self.rgb is None:
            return
        txt = f"Pixel {x:.1f}, {y:.1f}"
        if self.fit:
            X, Y = self.fit.forward([x], [y])
            lon, lat = crsmod.transform(X, Y, self.fit.work_crs, self._grid_code(), *self.fit.lonlat)
            if crsmod.is_geographic(self._grid_code()):
                txt += f"   →   {G.format_dms(lat[0], True)}  {G.format_dms(lon[0], False)}  " \
                       f"({crsmod.label(self._grid_code()).split(' (')[0]})"
        self.readout.setText(txt)

    def _zoom_point(self, r, _c):
        if 0 <= r < len(self.gcps):
            self.canvas.zoom_to(self.gcps[r].px, self.gcps[r].py, 6)

    def _delete_points(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            del self.gcps[r]
        self._refit()

    def _import_points(self):
        p, _ = QFileDialog.getOpenFileName(self, "QGIS .points file", os.path.expanduser("~"), "Points (*.points)")
        if p:
            self.gcps += G.read_qgis_points(p)
            self._refit()

    # ================================================================ drawing
    def _draw(self):
        self.canvas.clear_vectors()
        det = self.det
        if det:
            pts = det.frame_corners_original()
            self.canvas.add_polyline(pts, "#2E7D32", 2.0, closed=True)
            l, t, r, b = det.frame
            for m in det.meridians:
                p1 = det.line_point(m, t - 25)
                p2 = det.line_point(m, b + 25)
                o = det.to_original([p1[0], p2[0]], [p1[1], p2[1]])
                self.canvas.add_polyline(list(zip(o[0], o[1])), "#00A0D0", 1.2, style=Qt.DashLine)
            for p in det.parallels:
                p1 = det.line_point(p, l - 25)
                p2 = det.line_point(p, r + 25)
                o = det.to_original([p1[0], p2[0]], [p1[1], p2[1]])
                self.canvas.add_polyline(list(zip(o[0], o[1])), "#00A0D0", 1.2, style=Qt.DashLine)
        for i, g in enumerate(self.gcps):
            self.canvas.add_marker(g.px, g.py, str(i + 1), "#D32F2F" if g.enabled else "#9E9E9E")

    # ================================================================ classes + export
    def _preview_classes(self):
        if self.rgb is None:
            return
        preset = M.PRESETS[self.cls_preset.currentData()]
        region = M.region_mask(self.rgb.shape, self.det.frame_corners_original()) if self.det else None
        rgb = self.rgb
        self.readout.setText("Reading map classes…")
        t = Task(lambda _t: M.colorize_codes(M.classify(rgb, preset, region), preset), self)
        t.done.connect(lambda rgba: (self.canvas.set_overlay(rgba), self._draw(),
                                     self.readout.setText("Class preview shown over the map. Open the map "
                                                          "again to hide it.")))
        t.failed.connect(lambda e: QMessageBox.warning(self, "Classes", e))
        self._prev_task = t
        t.start()

    def _export(self):
        if self.rgb is None:
            return
        if not self.fit:
            QMessageBox.information(self, "Not ready", "Create control points first (Grid tab).")
            return
        code = self.out_crs.code()
        if not code:
            QMessageBox.warning(self, "Coordinate system", "Choose a valid output coordinate system.")
            return
        req = GeorefExport(out_dir=self.out_dir.path(), name=getattr(self, "_name", "map"), crs=code,
                           order=self.order.currentData(), crop=self.crop.isChecked() and self.det is not None,
                           resampling=self.resamp.currentData(), raster_formats=self.img_fmts.selected(),
                           write_points=self.pts_qgis.isChecked(), gcp_vector_formats=self.pts_vec.selected(),
                           classes_preset=self.cls_preset.currentData() if self.cls_on.isChecked() else None,
                           class_vector_formats=self.cls_vec.selected(), class_raster_formats=self.cls_ras.selected())
        rgb, det, gcps, gcrs = self.rgb, self.det, list(self.gcps), self._grid_code()
        self.exp_btn.setEnabled(False)
        self.exp_bar.setVisible(True)
        t = Task(lambda tk: georef_export(rgb, det, gcps, gcrs, req, lambda p, s: tk.report(p, s)), self)
        t.progress.connect(lambda p, s: (self.exp_bar.setValue(int(p)), self.exp_status.setText(s)))
        t.done.connect(lambda r: self._exported(r, req.out_dir))
        t.failed.connect(lambda e: (self.exp_btn.setEnabled(True), QMessageBox.warning(self, "Export failed", e)))
        self.task = t
        t.start()

    def _exported(self, result, folder):
        files, info = result
        self.exp_btn.setEnabled(True)
        txt = f"\u2714 {len(files)} files written. Error {info['rmse_m']:.2f} m."
        if info.get("class_areas_ha"):
            txt += " Class areas (ha): " + ", ".join(f"{k}: {v:,.1f}" for k, v in info["class_areas_ha"].items())
        self.exp_status.setText(txt)
        # keep a session next to the outputs so the job can be reopened from the Jobs list
        try:
            sp = os.path.join(folder, f"{getattr(self, '_name', 'map')}.georef-session.json")
            self._write_session(sp, f"\u2714 Exported \u00b7 error {info['rmse_m']:.2f} m", folder)
        except OSError:
            pass
        if QMessageBox.question(self, "Export finished", f"{len(files)} files written.\n\nOpen the folder?") \
                == QMessageBox.Yes:
            system.open_path(folder)

    # ================================================================ sessions
    def save_session(self, path: str | None = None):
        if not self.image_path:
            QMessageBox.information(self, "Nothing to save", "Open a map image first.")
            return
        p = path
        if not p:
            p, _ = QFileDialog.getSaveFileName(self, "Save georeferencing session",
                                               os.path.splitext(self.image_path)[0] + ".georef-session.json",
                                               "Session (*.json)")
        if p:
            status = (f"Saved \u00b7 error {self.fit.rmse_m:.2f} m" if self.fit else "Saved \u00b7 not fitted yet")
            self._write_session(p, status, os.path.dirname(p))

    def _write_session(self, path: str, status: str, folder: str):
        G.save_session(path, self.image_path, self.det, self.gcps, self._grid_code(),
                       {"order": self.order.currentData(), "out_crs": self.out_crs.code() or ""})
        h, w = self.rgb.shape[:2]
        activity.record(activity.Activity(
            kind="georef", name=getattr(self, "_name", os.path.basename(path)), path=path, folder=folder,
            size=f"{w} \u00d7 {h} px", crs=crsmod.label(self._grid_code()), status=status))

    def load_session(self, path: str | None = None):
        p = path
        if not p:
            p, _ = QFileDialog.getOpenFileName(self, "Load georeferencing session", os.path.expanduser("~"),
                                               "Session (*.json)")
        if not p:
            return
        try:
            img, det, gcps, gcrs, extra = G.load_session(p)
        except Exception as e:
            QMessageBox.warning(self, "Cannot open session", f"{os.path.basename(p)}: {e}")
            return
        if not os.path.exists(img):
            QMessageBox.warning(self, "Image missing", f"The map image is no longer at:\n{img}")
            return
        self.image_path = img
        self.rgb = G.load_rgb(img)
        self.canvas.set_base(self.rgb)
        self._name = os.path.splitext(os.path.basename(img))[0]
        self.file_info.setText(f"{os.path.basename(img)} \u00b7 {self.rgb.shape[1]} \u00d7 {self.rgb.shape[0]} px")
        if extra.get("out_crs"):
            self.out_crs.set_code(extra["out_crs"])
        self.grid_crs.setCurrentIndex(max(0, self.grid_crs.findData(gcrs)))
        self.order.setCurrentIndex(max(0, self.order.findData(extra.get("order", 1))))
        self.det = det
        self.gcps = gcps
        if det:
            self._detected(det)
        self._refit()
