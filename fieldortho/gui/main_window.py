"""Main window: home screen plus a five-step wizard."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..core import engine, jobs, photos as ph
from ..core.settings import Settings
from .dialogs import GcpDialog, SettingsDialog
from .pages_run import ProcessPage, ResultsPage
from .pages_setup import CheckPage, HomePage, OptionsPage, PhotosPage
from .georef_page import GeorefPage
from .model_page import ModelPage
from .session import Session

STEPS = ["Photos", "Check", "Outputs", "Processing", "Results"]


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        settings = settings or Settings.load()
        self.s = Session(settings, engine.detect(settings.odm_path, settings.engine_mode,
                                                 settings.docker_image))
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1280, 820)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # sidebar with steps
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(210)
        sl = QVBoxLayout(self.sidebar)
        sl.setContentsMargins(22, 26, 16, 20)
        sl.setSpacing(14)
        brand = QLabel(APP_NAME)
        brand.setStyleSheet("font-size: 18px; font-weight: 700; color: #1F6F5C;")
        sl.addWidget(brand)
        self.job_label = QLabel("")
        self.job_label.setObjectName("Muted")
        self.job_label.setWordWrap(True)
        sl.addWidget(self.job_label)
        sl.addSpacing(10)
        self.step_labels = []
        for i, name in enumerate(STEPS):
            lab = QLabel(f"{i + 1}   {name}")
            lab.setObjectName("StepTodo")
            sl.addWidget(lab)
            self.step_labels.append(lab)
        sl.addStretch(1)
        lay.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        s = self.s
        self.home = HomePage(s)
        self.photos = PhotosPage(s)
        self.check = CheckPage(s)
        self.options = OptionsPage(s)
        self.process = ProcessPage(s)
        self.results = ResultsPage(s)
        self.georef = GeorefPage(s)
        self.models = ModelPage(s)
        for p in (self.home, self.photos, self.check, self.options, self.process, self.results, self.georef,
                  self.models):
            self.stack.addWidget(p)

        self.home.new_job.connect(self.new_job)
        self.home.open_job.connect(self.open_job)
        self.home.open_settings.connect(self.open_settings)
        self.home.open_georef.connect(lambda: self.show_page(self.georef))
        self.home.open_models.connect(lambda: self.show_page(self.models))
        self.georef.home.connect(self.show_home)
        self.models.home.connect(self.show_home)
        self.results.model.connect(self._model_from_job)
        self.photos.go_back.connect(self.show_home)
        self.photos.go_next.connect(self._photos_done)
        self.check.go_back.connect(lambda: self.show_page(self.photos))
        self.check.go_next.connect(self._check_done)
        self.options.go_back.connect(lambda: self.show_page(self.check))
        self.options.go_next.connect(self._start)
        self.options.edit_gcps.connect(self._edit_gcps)
        self.process.finished.connect(lambda ok: self.show_page(self.results))
        self.process.change_settings.connect(lambda: self.show_page(self.options))
        self.results.home.connect(self.show_home)
        self.results.new_job.connect(self.new_job)
        self.show_home()

    # ------------------------------------------------------------------ navigation
    def show_page(self, page):
        self.stack.setCurrentWidget(page)
        wizard = page not in (self.home, self.georef, self.models)
        self.sidebar.setVisible(wizard)
        idx = {self.photos: 0, self.check: 1, self.options: 2, self.process: 3, self.results: 4}.get(page, -1)
        for i, lab in enumerate(self.step_labels):
            name = STEPS[i]
            if i < idx:
                lab.setText(f"✔   {name}")
                lab.setObjectName("StepDone")
            elif i == idx:
                lab.setText(f"{i + 1}   {name}")
                lab.setObjectName("StepActive")
            else:
                lab.setText(f"{i + 1}   {name}")
                lab.setObjectName("StepTodo")
            lab.style().unpolish(lab)
            lab.style().polish(lab)
        self.job_label.setText(self.s.job.name if self.s.job else "New job")
        page.enter()

    def _model_from_job(self):
        job = self.s.job
        self.show_page(self.models)
        self.models.use_job(job)

    def show_home(self):
        self.s.job = None
        self.show_page(self.home)

    def new_job(self):
        self.s.job = None
        self.s.photos, self.s.survey, self.s.flight_height = [], None, None
        self.photos.name.clear()
        self.photos.folder.setText("-")
        self.photos.status.setText("")
        self.show_page(self.photos)

    def open_settings(self):
        if SettingsDialog(self.s, self).exec():
            if self.stack.currentWidget() is self.home:
                self.home.enter()

    # ------------------------------------------------------------------ steps
    def _photos_done(self):
        name = self.photos.job_name()
        if self.s.job is None or self.s.job.name != name:
            self.s.job = jobs.Job(name=name, job_dir=jobs.unique_job_dir(self.s.settings.jobs_root, name),
                                  photos_dir=self.photos.photos_dir)
        self.show_page(self.check)

    def _check_done(self):
        job = self.s.job
        job.options.exclude_blurry = self.check.blur_cb.isChecked()
        job.photos = [p.path for p in self.s.selected_photos()]
        sv = self.s.survey.to_dict()
        sv["flight_height_input"] = self.s.flight_height
        job.survey = sv
        self.show_page(self.options)

    def _edit_gcps(self):
        self.options.apply()
        self.s.job.save()
        GcpDialog(self.s, self).exec()
        self.options.refresh_gcp_label()

    def _start(self):
        self.options.apply()
        self.s.job.save()
        self.show_page(self.process)
        self.process.start()

    # ------------------------------------------------------------------ existing jobs
    def open_job(self, job_dir: str):
        try:
            job = jobs.Job.load(job_dir)
        except Exception as e:
            QMessageBox.warning(self, "Cannot open job", str(e))
            return
        self.s.job = job
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            paths = [p for p in job.photos if os.path.isfile(p)]
            if len(paths) < len(job.photos) and os.path.isdir(job.images_dir):
                paths = ph.list_photos(job.images_dir)
            self.s.photos = [ph.read_photo(p, measure_sharpness=False) for p in paths]
            self.s.flight_height = (job.survey or {}).get("flight_height_input")
            self.s.survey = ph.analyze(self.s.photos, self.s.flight_height)
        finally:
            QGuiApplication.restoreOverrideCursor()
        self.s.settings.add_recent(job_dir)
        if job.status == jobs.STATUS_DONE and job.summary.get("files"):
            self.show_page(self.results)
        else:
            self.show_page(self.options)

    def closeEvent(self, e):
        if self.process.busy():
            if QMessageBox.question(self, "Stop processing?",
                                    "A job is still processing. Stop it and quit? You can continue it "
                                    "later from the Home screen.") != QMessageBox.Yes:
                e.ignore()
                return
            self.process.task.cancel()
            self.process.task.wait(15000)
        e.accept()
