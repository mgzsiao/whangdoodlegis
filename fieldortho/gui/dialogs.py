"""Settings dialog and ground-control-point tagging dialog."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QPointF, QSize, Signal
from PySide6.QtGui import QColor, QIcon, QImageReader, QPen, QPixmap, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGraphicsEllipseItem, QGraphicsLineItem, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
                               QGraphicsView)

from ..core import crs as crsmod, engine, gcp as gcpmod, system
from . import theme
from .session import Session
from .widgets import IssueBox, RasterView, label


class SettingsDialog(QDialog):
    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.s = session
        self.setWindowTitle("Settings")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)
        row = QHBoxLayout()
        self.odm = QLineEdit(session.settings.odm_path)
        self.odm.setPlaceholderText("Automatic (C:\\ODM or bundled engine)")
        row.addWidget(self.odm, 1)
        b = QPushButton("Browse\u2026")
        b.clicked.connect(self._browse_odm)
        row.addWidget(b)
        form.addRow("OpenDroneMap folder", row)
        self.mode = QComboBox()
        self.mode.addItem("Installed on this computer (recommended)", "native")
        self.mode.addItem("Docker (advanced)", "docker")
        self.mode.setCurrentIndex(max(0, self.mode.findData(session.settings.engine_mode)))
        form.addRow("Engine type", self.mode)
        self.threads = QSpinBox()
        self.threads.setRange(1, max(1, system.cpu_count()))
        self.threads.setValue(min(session.settings.threads, system.cpu_count()))
        self.threads.setToolTip("Lower this to keep the computer usable while processing.")
        form.addRow("CPU threads to use", self.threads)
        row2 = QHBoxLayout()
        self.jobs = QLineEdit(session.settings.jobs_root)
        row2.addWidget(self.jobs, 1)
        b2 = QPushButton("Browse\u2026")
        b2.clicked.connect(lambda: self._browse_into(self.jobs, "Folder for jobs"))
        row2.addWidget(b2)
        form.addRow("Jobs folder", row2)
        lay.addLayout(form)
        self.status_box = QVBoxLayout()
        lay.addLayout(self.status_box)
        brow = QHBoxLayout()
        test = QPushButton("Check engine")
        test.clicked.connect(self._check)
        brow.addWidget(test)
        self.get_btn = QPushButton("\u2B07 Download and install engine (internet needed once)")
        self.get_btn.clicked.connect(self._download_engine)
        self.get_btn.setVisible(system.IS_WINDOWS)
        brow.addWidget(self.get_btn)
        brow.addStretch(1)
        lay.addLayout(brow)
        from PySide6.QtWidgets import QProgressBar
        self.dl_bar = QProgressBar()
        self.dl_bar.setVisible(False)
        lay.addWidget(self.dl_bar)
        self.dl_text = label("", "Muted")
        lay.addWidget(self.dl_text)
        lay.addWidget(label(f"This computer: {system.describe_machine()}", "Muted"))
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._check()

    def _browse_odm(self):
        self._browse_into(self.odm, "OpenDroneMap folder (contains run.bat)")
        self._check()

    def _browse_into(self, edit: QLineEdit, title: str):
        d = QFileDialog.getExistingDirectory(self, title, edit.text() or os.path.expanduser("~"))
        if d:
            edit.setText(d)

    def _check(self):
        info = engine.detect(self.odm.text().strip(), self.mode.currentData(), self.s.settings.docker_image)
        while self.status_box.count():
            w = self.status_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.status_box.addWidget(IssueBox("ok" if info.ok else "error",
                                           info.message, info.path if info.ok else ""))

    def _download_engine(self):
        from ..core import odm_install
        from .workers import Task
        if QMessageBox.question(self, "Download engine",
                                "Download the latest OpenDroneMap engine (about 1-2 GB) and install it to "
                                "C:\\ODM? This needs internet and takes a while.") != QMessageBox.Yes:
            return
        self.get_btn.setEnabled(False)
        self.dl_bar.setVisible(True)
        t = Task(lambda tk: odm_install.download_and_install(lambda p, s: tk.report(p, s), lambda: tk.cancelled), self)
        t.progress.connect(lambda p, s: (self.dl_bar.setValue(int(p)), self.dl_text.setText(s)))
        t.done.connect(lambda d: (self.odm.setText(d), self._check(), self.get_btn.setEnabled(True),
                                  self.dl_text.setText("\u2714 Engine installed.")))
        t.failed.connect(lambda e: (self.get_btn.setEnabled(True),
                                    QMessageBox.warning(self, "Engine download failed", e)))
        self._dl_task = t
        t.start()

    def _save(self):
        st = self.s.settings
        st.odm_path = self.odm.text().strip()
        st.engine_mode = self.mode.currentData()
        st.threads = self.threads.value()
        st.jobs_root = self.jobs.text().strip() or st.jobs_root
        st.save()
        self.s.refresh_engine()
        self.accept()


# ============================================================================
# GCP tagging
# ============================================================================

class TagView(RasterView):
    """Photo viewer where a click places the GCP mark (in full-resolution pixels)."""
    marked = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self._press = None
        self.cross: list = []

    def show_photo(self, path: str):
        self.scene().clear()
        self.cross = []
        reader = QImageReader(path)
        reader.setAutoTransform(False)  # GCP pixel coordinates refer to the raw image
        img = reader.read()
        self.item = self.scene().addPixmap(QPixmap.fromImage(img))
        self.scene().setSceneRect(self.item.boundingRect())
        self.geo = None
        self.fit()

    def set_mark(self, x: float | None, y: float | None):
        for it in self.cross:
            self.scene().removeItem(it)
        self.cross = []
        if x is None:
            return
        pen = QPen(QColor(theme.ERROR), 0)
        pen.setCosmetic(True)
        pen.setWidth(2)
        r = 90
        self.cross = [self.scene().addLine(x - r, y, x + r, y, pen), self.scene().addLine(x, y - r, x, y + r, pen),
                      self.scene().addEllipse(x - r / 2, y - r / 2, r, r, pen)]

    def mousePressEvent(self, e):
        self._press = e.position()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if self._press is not None and (e.position() - self._press).manhattanLength() < 4 and self.item:
            pt = self.mapToScene(e.position().toPoint())
            if self.item.boundingRect().contains(pt):
                self.marked.emit(pt.x(), pt.y())
        self._press = None


class GcpDialog(QDialog):
    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.s = session
        self.job = session.job
        self.setWindowTitle("Ground control points")
        self.resize(1200, 760)
        lay = QVBoxLayout(self)
        lay.addWidget(label(
            "1) Load the surveyed points (CSV: name, easting/X, northing/Y, elevation).  "
            "2) For each point, click the target's centre in at least 3 photos.  "
            "3) Optionally keep some points as check points to measure accuracy.", "Muted", wrap=True))
        top = QHBoxLayout()
        lb = QPushButton("Load points CSV\u2026")
        lb.clicked.connect(self._load_csv)
        top.addWidget(lb)
        top.addWidget(QLabel("Coordinate system of the points:"))
        self.crs = QComboBox()
        for code, text in crsmod.CHOICES:
            if code != crsmod.AUTO:
                self.crs.addItem(text, code)
        if session.survey and session.survey.epsg:
            code = f"EPSG:{session.survey.epsg}"
            if self.crs.findData(code) < 0:
                self.crs.insertItem(0, crsmod.label(code), code)
            self.crs.setCurrentIndex(self.crs.findData(code))
        top.addWidget(self.crs, 1)
        lay.addLayout(top)

        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Point", "Photos tagged", "Use as"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.currentCellChanged.connect(lambda r, *_: self._select_gcp(r))
        ll.addWidget(self.table, 1)
        tb = QPushButton("Switch control / check point")
        tb.clicked.connect(self._toggle_check)
        ll.addWidget(tb)
        ll.addWidget(label("Photos that should show this point:", "Muted"))
        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListWidget.IconMode)
        self.thumbs.setIconSize(QSize(120, 90))
        self.thumbs.setResizeMode(QListWidget.Adjust)
        self.thumbs.setMovement(QListWidget.Static)
        self.thumbs.setMinimumHeight(220)
        self.thumbs.currentRowChanged.connect(self._select_photo)
        ll.addWidget(self.thumbs, 1)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.hint = label("Select a point on the left.", "H2")
        rl.addWidget(self.hint)
        self.view = TagView()
        self.view.marked.connect(self._mark)
        rl.addWidget(self.view, 1)
        row = QHBoxLayout()
        rm = QPushButton("Remove mark in this photo")
        rm.clicked.connect(lambda: self._mark(None, None))
        row.addWidget(rm)
        row.addStretch(1)
        nx = QPushButton("Next photo \u25B6")
        nx.clicked.connect(lambda: self.thumbs.setCurrentRow(min(self.thumbs.currentRow() + 1,
                                                                 self.thumbs.count() - 1)))
        row.addWidget(nx)
        rl.addLayout(row)
        split.addWidget(right)
        split.setSizes([380, 820])
        lay.addWidget(split, 1)
        self.summary = label("", "Muted")
        lay.addWidget(self.summary)
        bb = QDialogButtonBox()
        clear = bb.addButton("Remove all GCPs", QDialogButtonBox.DestructiveRole)
        clear.clicked.connect(self._clear)
        bb.addButton(QDialogButtonBox.Save)
        bb.addButton(QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self.gs: gcpmod.GcpSet | None = None
        self.cands: list = []
        tags = self.job.path("gcp_tags.json")
        if os.path.isfile(tags):
            try:
                self.gs = gcpmod.GcpSet.load_json(tags)
                self.crs.setCurrentIndex(max(0, self.crs.findData(self.gs.crs)))
            except Exception:
                self.gs = None
        self._fill()

    # ------------------------------------------------------------------
    def _load_csv(self):
        p, _ = QFileDialog.getOpenFileName(self, "Ground control points", os.path.expanduser("~"),
                                           "CSV / text (*.csv *.txt)")
        if not p:
            return
        try:
            self.gs = gcpmod.GcpSet.from_csv(p, self.crs.currentData())
        except Exception as e:
            QMessageBox.warning(self, "Could not read file", str(e))
            return
        if not self.gs.points:
            QMessageBox.warning(self, "No points", "No rows with name, X, Y, Z were found in that file.")
        self._fill()

    def _fill(self):
        self.table.setRowCount(0)
        if not self.gs:
            self.summary.setText("No points loaded.")
            return
        for g in self.gs.points:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(g.name))
            n = QTableWidgetItem(f"{len(g.marks)}" + ("  \u2714" if len(g.marks) >= 3 else ""))
            self.table.setItem(r, 1, n)
            self.table.setItem(r, 2, QTableWidgetItem("check point" if g.is_checkpoint else "control"))
        self.summary.setText(gcpmod.summary(self.gs))
        if self.table.rowCount():
            self.table.selectRow(0)

    def _refresh_row(self, r):
        g = self.gs.points[r]
        self.table.item(r, 1).setText(f"{len(g.marks)}" + ("  \u2714" if len(g.marks) >= 3 else ""))
        self.table.item(r, 2).setText("check point" if g.is_checkpoint else "control")
        self.summary.setText(gcpmod.summary(self.gs))

    def _current(self):
        r = self.table.currentRow()
        return (r, self.gs.points[r]) if self.gs and 0 <= r < len(self.gs.points) else (r, None)

    def _select_gcp(self, r):
        _r, g = self._current()
        self.thumbs.clear()
        self.cands = []
        if not g:
            return
        sv = self.s.survey
        try:
            self.gs.crs = self.crs.currentData()
            self.cands = gcpmod.candidate_photos(g, self.gs.crs, self.s.selected_photos(), sv.epsg, sv.footprint_m)
        except Exception as e:
            self.hint.setText(f"Could not place point: {e}")
            return
        marked = {m.image for m in g.marks}
        for p, d in self.cands:
            reader = QImageReader(p.path)
            reader.setScaledSize(QSize(160, 120))
            icon = QIcon(QPixmap.fromImage(reader.read()))
            it = QListWidgetItem(icon, ("\u2714 " if p.name in marked else "") + p.name)
            it.setToolTip(f"{d:.0f} m from the point")
            self.thumbs.addItem(it)
        if not self.cands:
            self.hint.setText(f"{g.name}: no photo is near this point - check the coordinate system.")
        else:
            self.hint.setText(f"{g.name}: click the centre of the target")
            self.thumbs.setCurrentRow(0)

    def _select_photo(self, i):
        _r, g = self._current()
        if not g or not (0 <= i < len(self.cands)):
            return
        p = self.cands[i][0]
        self.view.show_photo(p.path)
        m = next((m for m in g.marks if m.image == p.name), None)
        self.view.set_mark(m.px if m else None, m.py if m else None)
        self.hint.setText(f"{g.name} \u00b7 {p.name}: click the centre of the target "
                          f"(scroll to zoom, drag to move)")

    def _mark(self, x, y):
        r, g = self._current()
        i = self.thumbs.currentRow()
        if not g or not (0 <= i < len(self.cands)):
            return
        name = self.cands[i][0].name
        g.marks = [m for m in g.marks if m.image != name]
        if x is not None:
            g.marks.append(gcpmod.GcpMark(name, float(x), float(y)))
        self.view.set_mark(x, y)
        self.thumbs.item(i).setText(("\u2714 " if x is not None else "") + name)
        self._refresh_row(r)

    def _toggle_check(self):
        r, g = self._current()
        if g:
            g.is_checkpoint = not g.is_checkpoint
            self._refresh_row(r)

    def _clear(self):
        if QMessageBox.question(self, "Remove all GCPs?", "Remove all ground control points from this job?") \
                != QMessageBox.Yes:
            return
        for f in ("gcp_tags.json", "gcp_list.txt", "checkpoints.csv"):
            try:
                os.remove(self.job.path(f))
            except OSError:
                pass
        self.job.options.gcp_file = ""
        self.job.options.checkpoints_file = ""
        self.job.save()
        self.accept()

    def _save(self):
        if not self.gs:
            self.reject()
            return
        self.gs.crs = self.crs.currentData()
        ready = len(self.gs.tagged())
        if ready and ready < 3:
            if QMessageBox.question(self, "Few control points",
                                    f"Only {ready} point(s) are tagged in 3+ photos. At least 3 (better 5+) "
                                    "spread around the site are recommended. Save anyway?") != QMessageBox.Yes:
                return
        os.makedirs(self.job.job_dir, exist_ok=True)
        self.gs.save_json(self.job.path("gcp_tags.json"))
        if ready:
            self.gs.write_odm(self.job.path("gcp_list.txt"))
            self.job.options.gcp_file = "gcp_list.txt"
        else:
            self.job.options.gcp_file = ""
        if any(g.is_checkpoint for g in self.gs.points):
            self.gs.write_checkpoints_csv(self.job.path("checkpoints.csv"))
            self.job.options.checkpoints_file = self.job.path("checkpoints.csv")
            self.job.options.checkpoints_crs = self.gs.crs
        else:
            self.job.options.checkpoints_file = ""
        self.job.save()
        self.accept()
