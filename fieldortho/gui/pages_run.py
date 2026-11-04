"""Wizard steps 4 and 5: processing and results."""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from ..core import jobs, presets, system
from ..core.pipeline import JobProcessor, cleanup_intermediate
from . import theme
from .pages_setup import Page, clear_layout, fmt_minutes
from .widgets import IssueBox, RasterView, StatCard, card, label
from .workers import Task


class ProcessPage(Page):
    finished = Signal(bool)
    change_settings = Signal()

    def __init__(self, session):
        super().__init__(session)
        self.title = label("Step 4 · Processing", "H1")
        self.root.addWidget(self.title)
        c = card()
        cl = QVBoxLayout(c)
        cl.setContentsMargins(24, 20, 24, 20)
        cl.setSpacing(10)
        self.stage = label("Starting…", "Stage")
        cl.addWidget(self.stage)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        cl.addWidget(self.bar)
        self.times = label("", "Muted")
        cl.addWidget(self.times)
        steps = QGridLayout()
        steps.setHorizontalSpacing(30)
        self.step_labels: dict[str, QLabel] = {}
        shown = [("prepare", "Preparing photos")] + \
                [(k, t) for k, t, _p in presets.STAGES if k not in ("split", "merge")] + \
                [("export", "Saving GIS files")]
        seen = set()
        i = 0
        for k, t in shown:
            if t in seen:
                continue
            seen.add(t)
            lab = QLabel(f"○  {t}")
            lab.setObjectName("StepTodo")
            self.step_labels[k] = lab
            steps.addWidget(lab, i % 7, i // 7)
            i += 1
        cl.addLayout(steps)
        self.root.addWidget(c)
        self.notice = QVBoxLayout()
        self.root.addLayout(self.notice)
        self.notice.addWidget(IssueBox("info", "You can leave this running",
                                       "Keep the laptop plugged in. FieldOrtho keeps it awake while working. "
                                       "If it stops, you can continue later from the Home screen."))
        drow = QHBoxLayout()
        self.details_btn = QPushButton("Show technical details")
        self.details_btn.setObjectName("Link")
        self.details_btn.clicked.connect(self._toggle_log)
        drow.addWidget(self.details_btn)
        drow.addStretch(1)
        self.root.addLayout(drow)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setVisible(False)
        self.root.addWidget(self.log, 1)
        self.spacer = QWidget()
        self.root.addWidget(self.spacer, 1)

        row = QHBoxLayout()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.clicked.connect(self._cancel)
        row.addWidget(self.cancel_btn)
        row.addStretch(1)
        self.save_log_btn = QPushButton("Save log…")
        self.save_log_btn.clicked.connect(self._save_log)
        self.settings_btn = QPushButton("Change settings")
        self.settings_btn.clicked.connect(self.change_settings.emit)
        self.retry_btn = QPushButton("↻  Try again")
        self.retry_btn.setObjectName("Primary")
        self.retry_btn.clicked.connect(self.start)
        for b in (self.save_log_btn, self.settings_btn, self.retry_btn):
            row.addWidget(b)
            b.setVisible(False)
        self.root.addLayout(row)
        self.task: Task | None = None
        self.proc: JobProcessor | None = None
        self.t0 = 0.0
        self.pct = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    # ---------------------------------------------------------------- UI helpers
    def _toggle_log(self):
        v = not self.log.isVisible()
        self.log.setVisible(v)
        self.spacer.setVisible(not v)
        self.details_btn.setText("Hide technical details" if v else "Show technical details")

    def _mark(self, key: str):
        order = list(self.step_labels)
        if key not in self.step_labels:
            return
        idx = order.index(key)
        for i, k in enumerate(order):
            lab = self.step_labels[k]
            text = lab.text()[3:]
            if i < idx:
                lab.setText(f"✔  {text}")
                lab.setObjectName("StepDone")
            elif i == idx:
                lab.setText(f"●  {text}")
                lab.setObjectName("StepActive")
            else:
                lab.setText(f"○  {text}")
                lab.setObjectName("StepTodo")
            lab.style().unpolish(lab)
            lab.style().polish(lab)

    def _tick(self):
        el = time.time() - self.t0
        txt = f"{self.pct:.0f}%   ·   elapsed {int(el // 3600):d}:{int(el % 3600 // 60):02d}:{int(el % 60):02d}"
        if 8 < self.pct < 99 and el > 60:
            remaining = el / (self.pct / 100.0) - el
            txt += f"   ·   {fmt_minutes(remaining / 60)} left (rough)"
        self.times.setText(txt)

    # ---------------------------------------------------------------- run
    def start(self):
        job = self.s.job
        self.log.clear()
        clear_layout(self.notice)
        self.notice.addWidget(IssueBox("info", "You can leave this running",
                                       "Keep the laptop plugged in. FieldOrtho keeps it awake while working. "
                                       "If it stops, you can continue later from the Home screen."))
        for b in (self.save_log_btn, self.settings_btn, self.retry_btn):
            b.setVisible(False)
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)
        self.title.setText(f"Step 4 · Processing “{job.name}”")
        self.pct = 0.0
        self.bar.setValue(0)
        self._mark("prepare")
        self.t0 = time.time()
        self.timer.start(1000)
        system.keep_awake(True)
        self.proc = JobProcessor(job, self.s.settings, self.s.engine)
        self.s.settings.add_recent(job.job_dir)

        def work(t: Task):
            self.proc._user_progress = lambda p, txt: t.report(p, txt)
            self.proc.on_line = t.log
            t.on_cancel = self.proc.cancel
            return self.proc.run()

        self.task = Task(work, self)
        self.task.progress.connect(self._progress)
        self.task.line.connect(self.log.appendPlainText)
        self.task.done.connect(self._done)
        self.task.failed.connect(lambda e: self._done(False, e))
        self.task.start()

    def _progress(self, pct: float, text: str):
        self.pct = pct
        self.bar.setValue(int(pct * 10))
        self.stage.setText(text)
        key = None
        for k, t, _p in presets.STAGES:
            if t == text:
                key = k
        if text.startswith("Preparing photos"):
            key = "prepare"
        elif key is None and pct >= 92.9:   # engine finished; GIS export phase
            key = "export"
        if key in ("split", "merge"):
            key = "dataset"
        if key:
            self._mark(key)
        self._tick()

    def _done(self, ok, crash: str = ""):
        self.timer.stop()
        system.keep_awake(False)
        job = self.s.job
        self.cancel_btn.setVisible(False)
        clear_layout(self.notice)
        if ok:
            self.finished.emit(True)
            return
        if job.status == jobs.STATUS_CANCELLED:
            self.stage.setText("Cancelled")
            self.notice.addWidget(IssueBox("info", "Processing was cancelled",
                                           "Press “Try again” to continue where it stopped."))
        else:
            self.stage.setText("Stopped")
            msg = job.error or (f"Unexpected problem: {crash}" if crash else "Unknown error")
            self.notice.addWidget(IssueBox("error", "Processing did not finish", msg))
        for b in (self.save_log_btn, self.settings_btn, self.retry_btn):
            b.setVisible(True)

    def _cancel(self):
        if QMessageBox.question(self, "Cancel processing?",
                                "Stop processing now? You can continue later from where it stopped.") \
                == QMessageBox.Yes and self.task:
            self.cancel_btn.setEnabled(False)
            self.stage.setText("Stopping…")
            self.task.cancel()

    def _save_log(self):
        job = self.s.job
        p, _ = QFileDialog.getSaveFileName(self, "Save log", os.path.join(os.path.expanduser("~"),
                                                                          f"{job.name} log.txt"), "Text (*.txt)")
        if p and os.path.exists(job.log_path):
            shutil.copy2(job.log_path, p)

    def busy(self) -> bool:
        return bool(self.task and self.task.isRunning())


# ============================================================================
# Results
# ============================================================================

class ResultsPage(Page):
    new_job = Signal()
    home = Signal()
    model = Signal()

    def __init__(self, session):
        super().__init__(session)
        top = QHBoxLayout()
        self.title = label("Map ready", "H1")
        top.addWidget(self.title, 1)
        hb = QPushButton("⌂  Home")
        hb.clicked.connect(self.home.emit)
        top.addWidget(hb)
        nb = QPushButton("＋  New job")
        nb.clicked.connect(self.new_job.emit)
        top.addWidget(nb)
        self.root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(16)
        left = QVBoxLayout()
        self.tabs = QTabWidget()
        self.views: dict[str, RasterView] = {}
        left.addWidget(self.tabs, 1)
        self.readout = label("Scroll to zoom, drag to move. Coordinates show under the mouse.", "Muted")
        left.addWidget(self.readout)
        body.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(10)
        grid = QGridLayout()
        grid.setSpacing(10)
        self.c_area = StatCard("Area")
        self.c_res = StatCard("Pixel size")
        self.c_photos = StatCard("Photos used")
        self.c_acc = StatCard("Position accuracy")
        for i, c in enumerate((self.c_area, self.c_res, self.c_photos, self.c_acc)):
            grid.addWidget(c, i // 2, i % 2)
        right.addLayout(grid)
        self.crs_label = label("", "Muted", wrap=True)
        right.addWidget(self.crs_label)
        self.notes = QVBoxLayout()
        right.addLayout(self.notes)
        right.addStretch(1)
        self.export_btn = QPushButton("\u2B07  Export\u2026  (formats & coordinate system)")
        self.export_btn.setObjectName("Primary")
        self.export_btn.clicked.connect(self._export)
        right.addWidget(self.export_btn)
        self.model_btn = QPushButton("\U0001F30A  Flood & landslide model")
        self.model_btn.clicked.connect(self.model.emit)
        right.addWidget(self.model_btn)
        self.open_btn = QPushButton("\U0001F4C2  Open GIS folder")
        self.open_btn.clicked.connect(lambda: system.open_path(self.s.job.gis_dir))
        right.addWidget(self.open_btn)
        self.qgis_btn = QPushButton("Open in QGIS")
        self.qgis_btn.clicked.connect(self._open_qgis)
        right.addWidget(self.qgis_btn)
        self.copy_btn = QPushButton("Copy to USB drive / folder…")
        self.copy_btn.clicked.connect(self._copy)
        right.addWidget(self.copy_btn)
        self.report_btn = QPushButton("Quality report (PDF)")
        self.report_btn.clicked.connect(lambda: system.open_path(self.s.job.summary["files"]["report"]))
        right.addWidget(self.report_btn)
        self.clean_btn = QPushButton("Free up disk space")
        self.clean_btn.setToolTip("Deletes the engine's working files. GIS files and photos are kept.")
        self.clean_btn.clicked.connect(self._cleanup)
        right.addWidget(self.clean_btn)
        body.addLayout(right, 1)
        self.root.addLayout(body, 1)

    def enter(self):
        job = self.s.job
        sm = job.summary
        files = sm.get("files", {})
        self.title.setText(f"✔  {job.name}")
        self.tabs.clear()
        self.views.clear()
        names = {"ortho": "Orthomosaic", "dsm": "Surface model (DSM)", "dtm": "Terrain model (DTM)"}
        for kind in ("ortho", "dsm", "dtm"):
            png = sm.get("previews", {}).get(kind)
            if not png or not os.path.exists(png) or kind not in files:
                continue
            v = RasterView()
            v.hovered.connect(lambda t: self.readout.setText(t) if t else self._legend())
            info = sm.get(f"{kind}_preview", {})
            values = self._dem_values(files[kind], png) if kind != "ortho" else None
            self.tabs.addTab(v, names[kind])
            v.load(png, info.get("transform"), values, "m")
            self.views[kind] = v
        self.tabs.currentChanged.connect(self._legend)
        self._legend(0)
        if sm.get("contour_lines"):
            self.tabs.setTabToolTip(0, f"{sm['contour_lines']} contour lines saved to the GIS folder")

        self.c_area.set(f"{sm['area_ha']:.1f} ha" if sm.get("area_ha") else "-")
        res = sm.get("ortho_resolution_cm")
        self.c_res.set(f"{res:g} cm" if res else "-",
                       f"elevation {sm['dsm_resolution_cm'] or sm.get('dtm_resolution_cm'):g} cm"
                       if sm.get("dsm_resolution_cm") or sm.get("dtm_resolution_cm") else "")
        if sm.get("photos_used") is not None:
            self.c_photos.set(f"{sm['photos_used']}", f"of {sm.get('photos_input')} matched")
        else:
            self.c_photos.set(str(len(job.photos)))
        if sm.get("gcp_error_m") is not None:
            self.c_acc.set(f"{sm['gcp_error_m'] * 100:.1f} cm", "average GCP error")
        elif sm.get("gps_error_m") is not None:
            self.c_acc.set(f"~{sm['gps_error_m']:.1f} m", "drone GPS only (no GCPs)")
        else:
            self.c_acc.set("-")
        self.crs_label.setText(f"Coordinate system: {sm.get('crs_name', '-')}\n{sm.get('vertical', '')}")
        clear_layout(self.notes)
        chk = sm.get("checkpoints") or {}
        if chk.get("n"):
            self.notes.addWidget(IssueBox("info", f"Height check: RMSE {chk['rmse_z']:.3f} m",
                                          f"{chk['n']} check point(s), average difference {chk['mean_dz']:+.3f} m."))
        for w in sm.get("warnings", []):
            self.notes.addWidget(IssueBox("warning", w))
        self.qgis_btn.setVisible(bool(system.find_qgis()))
        self.report_btn.setVisible("report" in files)
        self.clean_btn.setEnabled(os.path.isdir(job.path("opensfm")) or os.path.isdir(job.path("odm_orthophoto")))
        self.model_btn.setVisible("dtm" in files or "dsm" in files)

    def _legend(self, _i=0):
        sm = self.s.job.summary if self.s.job else {}
        kind = next((k for k, v in self.views.items() if v is self.tabs.currentWidget()), None)
        info = sm.get(f"{kind}_preview", {}) if kind else {}
        if kind in ("dsm", "dtm") and info.get("min") is not None:
            self.readout.setText(f"Heights {info['p1']:,.1f} – {info['p99']:,.1f} m   "
                                 f"(blue = low, green/yellow = middle, brown/white = high).  "
                                 f"Scroll to zoom, drag to move.")
        else:
            self.readout.setText("Scroll to zoom, drag to move. Coordinates show under the mouse.")

    @staticmethod
    def _dem_values(tif: str, png: str):
        try:
            import rasterio
            from PIL import Image
            w, h = Image.open(png).size
            with rasterio.open(tif) as ds:
                return ds.read(1, out_shape=(h, w)).astype(np.float32)
        except Exception:
            return None

    def _export(self):
        from .export_dialog import JobExportDialog
        JobExportDialog(self.s.job, self).exec()

    def _open_qgis(self):
        exe = system.find_qgis()
        f = self.s.job.summary.get("files", {})
        layers = [f[k] for k in ("ortho", "dtm", "dsm", "contours") if k in f]
        if exe:
            subprocess.Popen([exe, *layers])

    def _copy(self):
        d = QFileDialog.getExistingDirectory(self, "Copy GIS files to…")
        if not d:
            return
        dst = os.path.join(d, jobs.safe_name(self.s.job.name))
        try:
            shutil.copytree(self.s.job.gis_dir, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("previews", "summary.json"))
            QMessageBox.information(self, "Copied", f"GIS files copied to:\n{dst}")
        except OSError as e:
            QMessageBox.warning(self, "Copy failed", str(e))

    def _cleanup(self):
        if QMessageBox.question(self, "Free up disk space?",
                                "Delete the processing engine's working files?\n\nThe GIS files and your "
                                "photos are kept, but the job can no longer be re-processed with "
                                "different settings without starting from scratch.") != QMessageBox.Yes:
            return
        freed = cleanup_intermediate(self.s.job)
        self.clean_btn.setEnabled(False)
        QMessageBox.information(self, "Done", f"Freed {freed / 1024**3:.1f} GB.")
