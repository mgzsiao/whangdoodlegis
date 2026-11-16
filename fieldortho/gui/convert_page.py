"""Convert files: change the coordinate system of shapefiles, GeoPackages, KML, DXF and rasters."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..core import convert as cv, crs as crsmod, exporters as ex, system
from .export_widgets import CrsPicker, FolderPicker
from .pages_setup import Page
from .widgets import card, label
from .workers import Task

FILE_FILTER = ("Map files (*.shp *.gpkg *.geojson *.json *.kml *.kmz *.dxf *.gpx *.tif *.tiff *.img *.asc *.jp2);;"
               "Shapefile (*.shp);;All files (*)")
SOURCE_CHOICES = [(c, t) for c, t in crsmod.EXPORT_CHOICES]


def _crs_text(it: cv.Item) -> str:
    code = it.source_code
    if not code:
        return "⚠ Not stated – choose it"
    try:
        t = crsmod.label(code)
        if t == code or t.startswith("EPSG"):
            from pyproj import CRS
            t = CRS.from_user_input(code).name
    except Exception:
        t = code
    return (t + ("  (set by you)" if it.crs_code else ""))


class SourceCrsDialog(QDialog):
    def __init__(self, names, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Coordinate system of the file")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.addWidget(label("Which coordinate system are these files in now?\n" + ", ".join(names), wrap=True))
        lay.addWidget(label("Use this when a shapefile has no .prj file, or when you know the .prj is wrong. "
                            "Ask whoever made the files if you are not sure: a wrong choice moves "
                            "everything by metres to hundreds of kilometres.", "Muted", wrap=True))
        self.picker = CrsPicker(SOURCE_CHOICES, "EPSG:3125")
        lay.addWidget(self.picker)
        row = QHBoxLayout()
        row.addStretch(1)
        c = QPushButton("Cancel")
        c.clicked.connect(self.reject)
        ok = QPushButton("Use this system")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        row.addWidget(c)
        row.addWidget(ok)
        lay.addLayout(row)


class ConvertPage(Page):
    CRUMB = "Tools"
    TITLE = "Convert files"

    def __init__(self, session):
        super().__init__(session)
        self.items: list[cv.Item] = []
        self.task = None
        addf = QPushButton("Add files…")
        addf.setObjectName("Primary")
        addf.clicked.connect(self._add_files)
        addd = QPushButton("Add a folder…")
        addd.clicked.connect(self._add_folder)
        self.set_actions([addf, addd])

        intro = label("Change the coordinate system of map files, for example a shapefile set from PRS92 to "
                      "WGS 84. Attributes are kept. A shapefile is the .shp with its .dbf, .shx and .prj next "
                      "to it: add the .shp. Everything runs on this computer.", "Muted", wrap=True)
        self.root.addWidget(intro)

        c1 = card()
        v = QVBoxLayout(c1)
        v.setContentsMargins(14, 10, 14, 10)
        v.addWidget(label("1  FILES TO CONVERT", "Section"))
        self.table = QTableWidget(0, 4)
        self.table.setObjectName("Flat")
        self.table.setHorizontalHeaderLabels(["FILE", "TYPE", "CONTENTS", "NOW IN"])
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 200)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(170)
        self.table.cellDoubleClicked.connect(lambda r, c: self._set_source() if c == 3 else None)
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.count = label("No files yet. Click “Add files…” or “Add a folder…”.",
                           "Muted")
        row.addWidget(self.count, 1)
        b = QPushButton("Set “Now in”…")
        b.setToolTip("Say which coordinate system the selected files are in (missing or wrong .prj).")
        b.clicked.connect(self._set_source)
        row.addWidget(b)
        b = QPushButton("Remove")
        b.clicked.connect(self._remove)
        row.addWidget(b)
        b = QPushButton("Clear list")
        b.clicked.connect(self._clear)
        row.addWidget(b)
        v.addLayout(row)
        self.root.addWidget(c1, 1)

        c2 = card()
        g = QGridLayout(c2)
        g.setContentsMargins(14, 10, 14, 12)
        g.setHorizontalSpacing(12)
        g.addWidget(label("2  CONVERT TO", "Section"), 0, 0, 1, 2)
        g.addWidget(QLabel("Coordinate system"), 1, 0, Qt.AlignTop)
        self.target = CrsPicker(crsmod.EXPORT_CHOICES, "EPSG:4326")
        g.addWidget(self.target, 1, 1)
        g.addWidget(QLabel("Vector format"), 2, 0)
        self.vfmt = QComboBox()
        self.vfmt.addItem("Same as the original", cv.SAME)
        for k, (t, ext, _d) in ex.VECTOR_FORMATS.items():
            self.vfmt.addItem(f"{t} ({ext})", k)
        g.addWidget(self.vfmt, 2, 1)
        g.addWidget(QLabel("Raster format"), 3, 0)
        self.rfmt = QComboBox()
        self.rfmt.addItem("Same as the original (GeoTIFF if not possible)", cv.SAME)
        for k in ("gtiff", "png", "jpeg", "kmz", "asc"):
            self.rfmt.addItem(ex.RASTER_FORMATS[k][0], k)
        g.addWidget(self.rfmt, 3, 1)
        g.addWidget(QLabel("Save to folder"), 4, 0)
        self.folder = FolderPicker(os.path.join(self.s.settings.jobs_root, "Converted"))
        g.addWidget(self.folder, 4, 1)
        g.setColumnStretch(1, 1)
        self.root.addWidget(c2)

        bot = QHBoxLayout()
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        bot.addWidget(self.bar, 1)
        self.status = label("", "Muted", wrap=True)
        bot.addWidget(self.status, 2)
        self.open_btn = QPushButton("Open folder")
        self.open_btn.setVisible(False)
        self.open_btn.clicked.connect(lambda: system.open_path(self.folder.path()))
        bot.addWidget(self.open_btn)
        self.go = QPushButton("Convert")
        self.go.setObjectName("Primary")
        self.go.setMinimumWidth(160)
        self.go.clicked.connect(self._convert)
        bot.addWidget(self.go)
        self.root.addLayout(bot)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        self.log.setVisible(False)
        self.root.addWidget(self.log)

    # ---------------------------------------------------------------- list
    def add_paths(self, paths):
        have = {os.path.normcase(os.path.abspath(i.path)) for i in self.items}
        bad = []
        for p in cv.collect(paths):
            if os.path.normcase(os.path.abspath(p)) in have:
                continue
            it = cv.inspect(p)
            if it.error:
                bad.append(f"{it.name}: {it.error}")
                continue
            self.items.append(it)
        self._fill()
        if bad:
            QMessageBox.warning(self, "Some files were skipped", "\n".join(bad[:15]))

    def _add_files(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "Map files to convert", os.path.expanduser("~"), FILE_FILTER)
        if ps:
            self.add_paths(ps)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Folder with map files", os.path.expanduser("~"))
        if d:
            n = len(self.items)
            self.add_paths([d])
            if len(self.items) == n:
                QMessageBox.information(self, "No map files", "No map files were found in that folder "
                                        "(sub-folders are not searched).")

    def _fill(self):
        self.table.setRowCount(len(self.items))
        for r, it in enumerate(self.items):
            vals = (it.name, "Vector" if it.kind == "vector" else "Raster", it.size, _crs_text(it))
            for c, t in enumerate(vals):
                x = QTableWidgetItem(t)
                x.setToolTip(it.path if c == 0 else t)
                if c == 3 and not it.source_code:
                    x.setForeground(Qt.darkRed)
                self.table.setItem(r, c, x)
        n = len(self.items)
        miss = sum(1 for i in self.items if not i.source_code)
        self.count.setText(("No files yet." if not n else f"{n} file{'s' * (n != 1)}.") +
                           (f"  ⚠ {miss} without a coordinate system: select and click "
                            "“Set ‘Now in’…”." if miss else ""))
        if self.items:
            try:
                b = self.items[0]
                if b.source_code:
                    import rasterio  # noqa: F401  (lon/lat for the datum note under the target picker)
                    self.target.set_lonlat(self._lonlat(b))
            except Exception:
                pass

    def _lonlat(self, it):
        if it.kind == "raster":
            return ex.site_lonlat(it.path)
        import pyogrio
        info = pyogrio.read_info(cv.gdal_path(it.path), layer=it.layers[0] if it.layers else None)
        b = info.get("total_bounds")
        return cv._lonlat_of(b, it.source_code)

    def _rows(self):
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def _set_source(self):
        rows = self._rows() or [r for r, i in enumerate(self.items) if not i.source_code]
        if not rows:
            QMessageBox.information(self, "Select files", "Select the files in the list first.")
            return
        d = SourceCrsDialog([self.items[r].name for r in rows], self)
        if d.exec() and d.picker.code():
            for r in rows:
                self.items[r].crs_code = d.picker.code()
            self._fill()

    def _remove(self):
        for r in reversed(self._rows()):
            del self.items[r]
        self._fill()

    def _clear(self):
        self.items = []
        self._fill()

    # ---------------------------------------------------------------- convert
    def _convert(self):
        if not self.items:
            QMessageBox.information(self, "No files", "Add the files to convert first.")
            return
        miss = [i.name for i in self.items if not i.source_code]
        if miss:
            QMessageBox.warning(self, "Coordinate system needed",
                                "We cannot convert these files without knowing their coordinate system:\n\n" +
                                "\n".join(miss[:12]) + "\n\nSelect them and click “Set ‘Now in’…”.")
            return
        dst = self.target.code()
        if not dst:
            QMessageBox.warning(self, "Coordinate system", "Choose the coordinate system to convert to.")
            return
        out = self.folder.path()
        if not out:
            QMessageBox.warning(self, "Folder", "Choose the folder to save to.")
            return
        items = list(self.items)
        vf, rf = self.vfmt.currentData(), self.rfmt.currentData()
        same_dir = [i.name for i in items if os.path.normcase(os.path.dirname(os.path.abspath(i.path))) ==
                    os.path.normcase(os.path.abspath(out))]
        if same_dir and QMessageBox.question(
                self, "Same folder", "The new files will be saved next to the originals (with the coordinate "
                "system in the name, e.g. _EPSG4326). Continue?") != QMessageBox.Yes:
            return

        def work(tk):
            done, errors = [], []
            for k, it in enumerate(items):
                tk.report(100 * k / len(items), it.name)
                try:
                    files, notes = cv.convert(it, out, dst, vf if it.kind == "vector" else rf)
                    done.append((it.name, files, notes))
                    tk.log(f"✔ {it.name} → " + ", ".join(os.path.basename(f) for f in files
                                                                if not f.lower().endswith((".shx", ".dbf", ".cpg"))))
                except Exception as e:
                    errors.append(f"{it.name}: {e}")
                    tk.log(f"✖ {it.name}: {e}")
            if done:
                cv.write_notes(out, dst, done)
            return done, errors

        self.log.clear()
        self.log.setVisible(True)
        self.bar.setVisible(True)
        self.bar.setValue(0)
        self.go.setEnabled(False)
        self.open_btn.setVisible(False)
        self.task = Task(work, self)
        self.task.progress.connect(lambda p, t: (self.bar.setValue(int(p)), self.status.setText(t)))
        self.task.line.connect(lambda t: self.log.appendPlainText(t) if not t.startswith("Traceback") else None)
        self.task.done.connect(lambda r: self._done(r, dst))
        self.task.failed.connect(lambda m: self._done(([], [m]), dst))
        self.task.start()

    def _done(self, result, dst):
        done, errors = result
        self.go.setEnabled(True)
        self.bar.setValue(100)
        self.bar.setVisible(False)
        n = sum(len([f for f in fs if not f.lower().endswith((".shx", ".dbf", ".cpg", ".prj"))])
                for _n, fs, _x in done)
        self.status.setText(f"Converted {len(done)} of {len(done) + len(errors)} file(s) to "
                            f"{crsmod.label(dst)}: {n} new file(s). Notes are in conversion_notes.txt.")
        self.open_btn.setVisible(bool(done))
        if errors:
            QMessageBox.warning(self, "Some files were not converted", "\n".join(errors[:15]))
