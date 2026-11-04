"""Export dialog for a processed drone job."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout,
                               QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout)

from ..core import exporters as ex, system
from ..core.job_export import LAYERS, ExportRequest, export_job
from .export_widgets import CrsPicker, FolderPicker, FormatChecks
from .widgets import label
from .workers import Task

DEFAULTS = {"ortho": (["gtiff"], []), "dtm": (["gtiff"], []), "dsm": (["gtiff"], []),
            "relief": ([], []), "contours": ([], ["gpkg", "shp"]), "boundary": ([], ["gpkg"])}


class JobExportDialog(QDialog):
    def __init__(self, job, parent=None):
        super().__init__(parent)
        self.job = job
        self.setWindowTitle(f"Export – {job.name}")
        self.setMinimumWidth(1060)
        files = job.summary.get("files", {})
        lay = QVBoxLayout(self)
        lay.addWidget(label("Choose what to export, the file formats and the coordinate system.", "Muted"))
        lay.addWidget(label("Coordinate system", "H2"))
        ref = files.get("ortho") or files.get("dtm") or files.get("dsm")
        lonlat = ex.site_lonlat(ref) if ref else None
        self.crs = CrsPicker(default="EPSG:4326", lonlat=lonlat)
        lay.addWidget(self.crs)
        lay.addWidget(label("Layers", "H2"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.addWidget(label("Layer", "Muted"), 0, 0)
        grid.addWidget(label("Raster (image / grid)", "Muted"), 0, 1)
        grid.addWidget(label("Vector (lines / shapes)", "Muted"), 0, 2)
        self.rows = {}
        r = 1
        available = {"ortho": "ortho" in files, "dsm": "dsm" in files, "dtm": "dtm" in files,
                     "relief": "dtm" in files or "dsm" in files, "contours": "dtm" in files or "dsm" in files,
                     "boundary": "ortho" in files}
        for key, (name, rf, vf) in LAYERS.items():
            if not available.get(key):
                continue
            on = QCheckBox(name)
            dr, dv = DEFAULTS[key]
            on.setChecked(bool(dr or dv) and key != "dsm")
            rchk = FormatChecks(rf, False, dr) if rf else QLabel("–")
            vchk = FormatChecks(vf, True, dv) if vf else QLabel("–")
            grid.addWidget(on, r, 0)
            grid.addWidget(rchk, r, 1)
            grid.addWidget(vchk, r, 2)
            self.rows[key] = (on, rchk, vchk)
            r += 1
        lay.addLayout(grid)
        crow = QHBoxLayout()
        crow.addWidget(QLabel("Contours from"))
        self.csrc = QComboBox()
        for k, t in (("dtm", "Terrain (DTM)"), ("dsm", "Surface (DSM)")):
            if k in files:
                self.csrc.addItem(t, k)
        crow.addWidget(self.csrc)
        crow.addWidget(QLabel("every"))
        self.cint = QComboBox()
        for t, v in (("automatic", 0.0), ("0.25 m", 0.25), ("0.5 m", 0.5), ("1 m", 1.0), ("2 m", 2.0),
                     ("5 m", 5.0), ("10 m", 10.0)):
            self.cint.addItem(t, v)
        crow.addWidget(self.cint)
        crow.addStretch(1)
        self.laz = QCheckBox("Also copy the point cloud (LAZ)")
        self.laz.setVisible("laz" in files)
        crow.addWidget(self.laz)
        lay.addLayout(crow)
        lay.addWidget(label("Save to folder", "H2"))
        self.folder = FolderPicker(os.path.join(job.job_dir, "Exports"))
        lay.addWidget(self.folder)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        self.status = label("", "Muted")
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        bb = QDialogButtonBox()
        self.go = bb.addButton("Export", QDialogButtonBox.AcceptRole)
        self.go.setObjectName("Primary")
        bb.addButton(QDialogButtonBox.Close)
        self.go.clicked.connect(self._export)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.task = None

    def request(self) -> ExportRequest | None:
        code = self.crs.code()
        if not code:
            QMessageBox.warning(self, "Coordinate system", "Choose a valid coordinate system.")
            return None
        req = ExportRequest(out_dir=self.folder.path(), crs=code, contour_interval=float(self.cint.currentData()),
                            contour_source=self.csrc.currentData() or "dtm", copy_pointcloud=self.laz.isChecked())
        for key, (on, rchk, vchk) in self.rows.items():
            if not on.isChecked():
                continue
            if isinstance(rchk, FormatChecks) and rchk.selected():
                req.raster[key] = rchk.selected()
            if isinstance(vchk, FormatChecks) and vchk.selected():
                req.vector[key] = vchk.selected()
        if not req.raster and not req.vector and not req.copy_pointcloud:
            QMessageBox.information(self, "Nothing selected", "Tick at least one layer and format.")
            return None
        return req

    def _export(self):
        req = self.request()
        if not req:
            return
        self.go.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setValue(0)

        def work(t: Task):
            return export_job(self.job, req, lambda p, s: t.report(p, s))
        self.task = Task(work, self)
        self.task.progress.connect(lambda p, s: (self.bar.setValue(int(p)), self.status.setText(s)))
        self.task.done.connect(lambda files: self._done(files, req.out_dir))
        self.task.failed.connect(self._failed)
        self.task.start()

    def _done(self, files, folder):
        self.go.setEnabled(True)
        self.status.setText(f"✔ {len(files)} files written to {folder}")
        if QMessageBox.question(self, "Export finished", f"{len(files)} files written.\n\nOpen the folder?") \
                == QMessageBox.Yes:
            system.open_path(folder)

    def _failed(self, msg):
        self.go.setEnabled(True)
        self.status.setText("")
        QMessageBox.warning(self, "Export failed", msg)


class SimpleExportDialog(QDialog):
    """CRS + formats + folder, for model results and the georeferencer."""

    def __init__(self, title: str, raster: list[tuple[str, list[str], list[str]]],
                 vector: list[tuple[str, list[str], list[str]]], folder: str, lonlat=None,
                 default_crs="EPSG:4326", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(760)
        lay = QVBoxLayout(self)
        lay.addWidget(label("Coordinate system", "H2"))
        self.crs = CrsPicker(default=default_crs, lonlat=lonlat)
        lay.addWidget(self.crs)
        self.checks = {}
        if raster:
            lay.addWidget(label("Rasters", "H2"))
            for key, fmts, on in raster:
                row = QHBoxLayout()
                row.addWidget(QLabel(key), 0)
                fc = FormatChecks(fmts, False, on)
                row.addWidget(fc, 1)
                self.checks[("r", key)] = fc
                lay.addLayout(row)
        if vector:
            lay.addWidget(label("Vectors (polygons)", "H2"))
            for key, fmts, on in vector:
                row = QHBoxLayout()
                row.addWidget(QLabel(key), 0)
                fc = FormatChecks(fmts, True, on)
                row.addWidget(fc, 1)
                self.checks[("v", key)] = fc
                lay.addLayout(row)
        lay.addWidget(label("Save to folder", "H2"))
        self.folder = FolderPicker(folder)
        lay.addWidget(self.folder)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Export")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _ok(self):
        if not self.crs.code():
            QMessageBox.warning(self, "Coordinate system", "Choose a valid coordinate system.")
            return
        self.accept()

    def selection(self) -> dict:
        return {k: fc.selected() for k, fc in self.checks.items()}
