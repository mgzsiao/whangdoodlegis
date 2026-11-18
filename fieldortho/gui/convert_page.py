"""Change SHP CRS: change the coordinate system of shapefiles (and GeoPackage, KML, DXF, GPX, rasters).

Shapefiles come in as single parts, whole sets, folders (with sub-folders if ticked), .zip files, or by
dragging them onto the page. Each row shows which parts were found and the coordinate system read from
the .prj with its EPSG code and how it was identified."""
from __future__ import annotations

import html
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..core import convert as cv, crs as crsmod, exporters as ex, system
from . import theme
from .export_widgets import CrsPicker, FolderPicker
from .pages_setup import Page
from .widgets import card, label
from .workers import Task

FILE_FILTER = ("Shapefiles, their parts and .zip (*.shp *.shx *.dbf *.prj *.cpg *.zip);;"
               "All map files (*.shp *.shx *.dbf *.prj *.cpg *.zip *.gpkg *.geojson *.json *.kml *.kmz *.dxf *.gpx "
               "*.tif *.tiff *.img *.asc *.jp2);;All files (*)")
SOURCE_CHOICES = [(c, t) for c, t in crsmod.EXPORT_CHOICES]
TYPE_NAMES = {".gpkg": "GeoPackage", ".geojson": "GeoJSON", ".json": "GeoJSON", ".kml": "KML (Google Earth)",
              ".kmz": "KMZ (Google Earth)", ".dxf": "DXF (CAD)", ".gpx": "GPX (GPS)"}


def crs_name(code: str) -> str:
    try:
        t = crsmod.label(code)
        if t == code or t.startswith("EPSG"):
            from pyproj import CRS
            t = CRS.from_user_input(code).name
    except Exception:
        t = code
    return t


def crs_html(it: cv.Item) -> str:
    """'PRS92 / Philippines zone 5' over 'EPSG 3125 · read from the .prj' (or a warning)."""
    code = it.source_code
    if not code:
        what = "No .prj" if it.parts else "Not stated"
        return (f'<span style="color:{theme.WARN}; font-weight:600;">⚠ {what}: choose it</span><br>'
                f'<span style="color:{theme.MUTED};">double-click or “Set coordinate system…”</span>')
    e = crsmod._epsg_of(code)
    how = "set by you" if it.crs_code else it.how
    sub = " · ".join(x for x in ((f"EPSG {e}" if e else ""), how) if x)
    return (f'<b>{html.escape(crs_name(code))}</b><br>'
            f'<span style="color:{theme.MUTED};">{html.escape(sub)}</span>')


def parts_html(it: cv.Item) -> str:
    """Chips for .shp .shx .dbf .prj .cpg (missing ones struck through); the file type otherwise."""
    if not it.parts:
        e = os.path.splitext(it.name)[1].lower()
        return f'<span style="color:{theme.MUTED};">{TYPE_NAMES.get(e, e[1:].upper() + " raster" if it.kind == "raster" else e[1:].upper())}</span>'
    out = []
    for e in cv.SHP_PARTS:
        if it.parts.get(e):
            out.append(f'<span style="color:{theme.ACCENT};">{e}</span>')
        elif e == ".cpg":                       # optional: text encoding
            out.append(f'<span style="color:{theme.MUTED};">{e}</span>')
        else:
            out.append(f'<span style="color:{theme.ERROR}; text-decoration:line-through;">{e}</span>')
    return "&nbsp; ".join(out)


def _cell_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setTextFormat(Qt.RichText)
    lab.setContentsMargins(8, 4, 6, 4)
    lab.setStyleSheet("background: transparent;")
    lab.setAttribute(Qt.WA_TransparentForMouseEvents)
    return lab


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
    TITLE = "Change SHP CRS"

    def __init__(self, session):
        super().__init__(session)
        self.items: list[cv.Item] = []
        self.task = None
        self.setAcceptDrops(True)
        addf = QPushButton("Add files…")
        addf.setObjectName("Primary")
        addf.setToolTip("Pick shapefiles: any part (.shp, .dbf, .prj …) brings in the whole set.")
        addf.clicked.connect(self._add_files)
        addd = QPushButton("Add a folder…")
        addd.clicked.connect(self._add_folder)
        addz = QPushButton("Add a .zip…")
        addz.clicked.connect(self._add_zip)
        self.set_actions([addf, addd, addz])

        self.root.addWidget(label(
            "Change the coordinate system of shapefiles, for example from PRS92 or Luzon 1911 to WGS 84, or back. "
            "Add the shapefiles, a whole folder or a .zip, or drag them onto this page: the app keeps each "
            "shapefile's parts together (.shp, .shx, .dbf, .prj, .cpg) and reads its coordinate system from the "
            ".prj. Attributes are kept, and everything runs on this computer.", wrap=True))
        self.root.addWidget(label("Also works for GeoPackage, KML / KMZ, GeoJSON, DXF, GPX and rasters such as "
                                  "GeoTIFF.", "Muted", wrap=True))

        c1 = card()
        v = QVBoxLayout(c1)
        v.setContentsMargins(14, 10, 14, 10)
        top = QHBoxLayout()
        top.addWidget(label("1  FILES", "Section"), 1)
        self.sub = QCheckBox("Include sub-folders")
        self.sub.setToolTip("When adding a folder or dropping one here, also look inside its sub-folders.")
        top.addWidget(self.sub)
        v.addLayout(top)
        self.table = QTableWidget(0, 4)
        self.table.setObjectName("Flat")
        self.table.setHorizontalHeaderLabels(["FILE", "PARTS FOUND", "CONTENTS", "COORDINATE SYSTEM NOW (DETECTED)"])
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 220)
        self.table.setColumnWidth(2, 170)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(190)
        self.table.cellDoubleClicked.connect(lambda r, c: self._set_source())
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.count = label("No files yet. Click “Add files…”, “Add a folder…” or “Add a .zip…”, "
                           "or drag files here.", "Muted", wrap=True)
        row.addWidget(self.count, 1)
        b = QPushButton("Set coordinate system…")
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
        g.addWidget(label("2  CONVERT ALL TO", "Section"), 0, 0, 1, 2)
        g.addWidget(QLabel("Coordinate system"), 1, 0, Qt.AlignTop)
        self.target = CrsPicker(crsmod.EXPORT_CHOICES, "EPSG:4326")
        g.addWidget(self.target, 1, 1)
        g.addWidget(QLabel("Save shapefiles as"), 2, 0)
        self.vfmt = QComboBox()
        self.vfmt.addItem("Shapefile (same as now)", cv.SAME)
        for k, (t, ext, _d) in ex.VECTOR_FORMATS.items():
            if k != "shp":
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

    # ---------------------------------------------------------------- drag and drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.add_paths(paths)

    # ---------------------------------------------------------------- list
    def add_paths(self, paths, recursive: bool | None = None):
        recursive = self.sub.isChecked() if recursive is None else recursive
        have = {(i.path.lower() if i.in_zip else os.path.normcase(os.path.abspath(i.path))) for i in self.items}
        bad, found = [], cv.collect(paths, recursive=recursive)
        for p in found:
            k = p.lower() if p.startswith("/vsizip/") else os.path.normcase(os.path.abspath(p))
            if k in have:
                continue
            it = cv.inspect(p)
            if it.error:
                bad.append(f"{it.name}: {it.error}")
                continue
            self.items.append(it)
            have.add(k)
        self._fill()
        if bad:
            QMessageBox.warning(self, "Some files were skipped", "\n".join(bad[:15]))
        return len(found)

    def _add_files(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "Shapefiles or other map files", os.path.expanduser("~"),
                                             FILE_FILTER)
        if ps:
            self.add_paths(ps)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Folder with shapefiles", os.path.expanduser("~"))
        if d and not self.add_paths([d]):
            QMessageBox.information(self, "No map files", "No map files were found in that folder" +
                                    ("." if self.sub.isChecked() else
                                     " (sub-folders were not searched: tick “Include sub-folders”)."))

    def _add_zip(self):
        ps, _ = QFileDialog.getOpenFileNames(self, ".zip files with shapefiles", os.path.expanduser("~"),
                                             "Zip files (*.zip)")
        if ps and not self.add_paths(ps):
            QMessageBox.information(self, "No map files", "No shapefiles or other map files were found in the .zip.")

    def _fill(self):
        self.table.setRowCount(len(self.items))
        for r, it in enumerate(self.items):
            where = it.path if not it.in_zip else "{} (inside {})".format(*reversed(cv._zip_split(it.path)))
            name = html.escape(it.name)
            sub = [f'<span style="color:{theme.MUTED};">in {html.escape(os.path.basename(cv._zip_split(it.path)[0]))}'
                   '</span>'] if it.in_zip else []
            sub += [f'<span style="color:{theme.WARN};">{html.escape(n)}</span>' for n in it.notes]
            cells = (f"<b>{name}</b>" + "".join("<br>" + s for s in sub), parts_html(it),
                     html.escape(it.size), crs_html(it))
            for c, t in enumerate(cells):
                x = QTableWidgetItem("")
                x.setToolTip(where if c == 0 else it.how if c == 3 else "")
                self.table.setItem(r, c, x)
                self.table.setCellWidget(r, c, _cell_label(t))
        self.table.resizeRowsToContents()
        n = len(self.items)
        miss = sum(1 for i in self.items if not i.source_code)
        shp = sum(1 for i in self.items if i.parts)
        txt = "No files yet." if not n else (f"{n} file{'s' * (n != 1)}" +
                                            (f" ({shp} shapefile{'s' * (shp != 1)})." if shp else "."))
        if miss:
            txt += f"  ⚠ {miss} without a coordinate system: double-click the row or click “Set coordinate system…”."
        self.count.setText(txt)
        if self.items:
            try:
                b = next((i for i in self.items if i.source_code), None)
                if b is not None:
                    self.target.set_lonlat(self._lonlat(b))
            except Exception:
                pass

    def _lonlat(self, it):
        if it.kind == "raster":
            return ex.site_lonlat(it.path)
        import pyogrio
        info = pyogrio.read_info(cv.gdal_path(it.src_path), layer=it.layers[0] if it.layers else None)
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
            cv.cleanup(self.items[r])
            del self.items[r]
        self._fill()

    def _clear(self):
        for it in self.items:
            cv.cleanup(it)
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
                                "\n".join(miss[:12]) + "\n\nDouble-click each one, or select them and click "
                                "“Set coordinate system…”.")
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
        same_dir = [i.name for i in items if not i.in_zip and
                    os.path.normcase(os.path.dirname(os.path.abspath(i.path))) == os.path.normcase(os.path.abspath(out))]
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
