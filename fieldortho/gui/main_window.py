"""Main window: navigation rail, title bar, pages, status bar."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, DISCLAIMER_SHORT, __version__
from ..core import engine, jobs, photos as ph, system
from ..core.settings import Settings
from . import theme
from .about import AboutDialog, SupportDialog, logo
from .dialogs import GcpDialog, SettingsDialog
from .georef_page import GeorefPage
from .jobs_page import JobsPage
from .measure_page import MeasurePage
from .model_page import ModelPage
from .pages_run import ProcessPage, ResultsPage
from .pages_setup import CheckPage, HomePage, OptionsPage, PhotosPage
from .session import Session
from .widgets import label

STEPS = ["Photos", "Check", "Outputs", "Processing", "Results"]


class NavButton(QPushButton):
    """A navigation entry: icon + text, highlighted when its section is open."""

    def __init__(self, icon: str, text: str, checkable: bool = True):
        super().__init__()
        self.setObjectName("NavItem")
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(38)
        self.setAccessibleName(text)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 0, 10, 0)
        h.setSpacing(12)
        self.ic = QLabel(theme.ICONS[icon])
        self.ic.setFixedWidth(18)
        self.tx = QLabel(text)
        for lab in (self.ic, self.tx):
            lab.setAttribute(Qt.WA_TransparentForMouseEvents)
            h.addWidget(lab)
        h.addStretch(1)
        self.toggled.connect(self._style)
        self._style(False)

    def _style(self, on):
        self.ic.setStyleSheet(f"font-family: {theme.ICON_FONT}; font-size: 16px; background: transparent; "
                              f"color: {theme.HIGHLIGHT if on else theme.NAV_INK};")
        self.tx.setStyleSheet(f"font-size: 13px; background: transparent; color: {'white' if on else theme.NAV_INK};"
                              f" font-weight: {600 if on else 400};")


class Stepper(QWidget):
    """1 Photos — 2 Check — 3 Outputs — 4 Processing — 5 Results"""

    def __init__(self):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.dots, self.names, self.lines = [], [], []
        for i, s in enumerate(STEPS):
            d = QLabel(str(i + 1))
            d.setFixedSize(24, 24)
            d.setAlignment(Qt.AlignCenter)
            t = QLabel(s)
            h.addWidget(d)
            h.addWidget(t)
            self.dots.append(d)
            self.names.append(t)
            if i < len(STEPS) - 1:
                ln = QFrame()
                ln.setFixedSize(26, 2)
                h.addWidget(ln)
                self.lines.append(ln)
        self.set_active(0)

    def set_active(self, idx: int):
        for i, (d, t) in enumerate(zip(self.dots, self.names)):
            done, cur = i < idx, i == idx
            d.setText("✔" if done else str(i + 1))
            bg = theme.ACCENT if (done or cur) else "#E3E8F0"
            d.setStyleSheet(f"background: {bg}; color: {'white' if (done or cur) else theme.MUTED}; "
                            "border-radius: 12px; font-weight: 600; font-size: 12px;")
            t.setStyleSheet(f"color: {theme.INK if cur else theme.MUTED}; font-weight: {600 if cur else 400};"
                            "background: transparent;")
        for i, ln in enumerate(self.lines):
            ln.setStyleSheet(f"background: {theme.ACCENT if i < idx else theme.LINE};")


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        settings = settings or Settings.load()
        self.s = Session(settings, engine.detect(settings.odm_path, settings.engine_mode,
                                                 settings.docker_image))
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1360, 860)
        self.setMinimumSize(1100, 700)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_nav())

        right = QWidget()
        right.setObjectName("Content")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        rv.addWidget(self._build_header())
        self.stack = QStackedWidget()
        rv.addWidget(self.stack, 1)
        rv.addWidget(self._build_status())
        lay.addWidget(right, 1)
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
        self.measure = MeasurePage(s)
        self.jobs = JobsPage(s)
        ob = QPushButton("Open job folder…")
        ob.clicked.connect(self._browse_job)
        self.jobs.set_actions([ob])
        self.wizard = [self.photos, self.check, self.options, self.process, self.results]
        for p in (self.home, *self.wizard, self.georef, self.models, self.measure, self.jobs):
            self.stack.addWidget(p)
            p.heading_changed.connect(self._refresh_header)
        self._last_wizard = None

        self.home.new_job.connect(self.new_job)
        self.home.open_row.connect(self.open_row)
        self.jobs.open_row.connect(self.open_row)
        self.home.open_georef.connect(lambda: self.show_page(self.georef))
        self.home.open_models.connect(lambda: self.show_page(self.models))
        self.home.open_measure.connect(lambda: self.show_page(self.measure))
        self.results.model.connect(self._model_from_job)
        self.results.measure.connect(self._measure_from_job)
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

    # ------------------------------------------------------------------ chrome
    def _build_nav(self) -> QFrame:
        nav = QFrame()
        nav.setObjectName("Nav")
        nav.setFixedWidth(236)
        v = QVBoxLayout(nav)
        v.setContentsMargins(14, 18, 14, 16)
        v.setSpacing(2)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.addWidget(logo(36))
        bt = QVBoxLayout()
        bt.setSpacing(0)
        bt.addWidget(label(APP_NAME, "Brand"))
        bt.addWidget(label("Offline mapping toolkit", "BrandSub"))
        brand.addLayout(bt, 1)
        v.addLayout(brand)
        v.addSpacing(18)
        self.nav: dict[str, NavButton] = {}

        def section(t):
            v.addSpacing(8)
            s = label(t, "NavSection")
            s.setContentsMargins(10, 0, 0, 4)
            v.addWidget(s)

        section("TOOLS")
        for key, icon, text, fn in (("home", "home", "Home", self.show_home),
                                    ("drone", "drone", "Drone mapping", self._nav_drone),
                                    ("georef", "georef", "Georeference a map", lambda: self.show_page(self.georef)),
                                    ("flood", "flood", "Flood & landslide", lambda: self.show_page(self.models)),
                                    ("measure", "measure", "Measure & earthworks",
                                     lambda: self.show_page(self.measure))):
            b = NavButton(icon, text)
            b.clicked.connect(fn)
            v.addWidget(b)
            self.nav[key] = b
        section("PROJECTS")
        b = NavButton("jobs", "Jobs")
        b.clicked.connect(lambda: self.show_page(self.jobs))
        v.addWidget(b)
        self.nav["jobs"] = b
        b = NavButton("settings", "Settings", checkable=False)
        b.clicked.connect(self.open_settings)
        v.addWidget(b)
        v.addStretch(1)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #22355A;")
        v.addWidget(sep)
        v.addSpacing(8)
        sup = QPushButton("♥  Support the Developer")
        sup.setObjectName("NavLink")
        sup.setCursor(Qt.PointingHandCursor)
        sup.clicked.connect(self.show_support)
        v.addWidget(sup)
        ab = QPushButton("ⓘ  About " + APP_NAME)
        ab.setObjectName("NavLink")
        ab.setCursor(Qt.PointingHandCursor)
        ab.clicked.connect(self.show_about)
        v.addWidget(ab)
        v.addSpacing(6)
        foot = label(f"Version {__version__} · {DISCLAIMER_SHORT}", "NavFoot", wrap=True)
        foot.setTextInteractionFlags(Qt.NoTextInteraction)
        v.addWidget(foot)
        return nav

    def _build_header(self) -> QFrame:
        hd = QFrame()
        hd.setObjectName("Header")
        hd.setFixedHeight(68)
        h = QHBoxLayout(hd)
        h.setContentsMargins(24, 8, 20, 8)
        h.setSpacing(12)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        self.crumb = label("", "Crumb")
        self.title = label("", "H1")
        tv.addWidget(self.crumb)
        tv.addWidget(self.title)
        h.addLayout(tv)
        h.addStretch(1)
        self.stepper = Stepper()
        h.addWidget(self.stepper)
        self.hdr_actions = QHBoxLayout()
        self.hdr_actions.setSpacing(8)
        h.addLayout(self.hdr_actions)
        self._action_widgets: list[QWidget] = []
        return hd

    def _build_status(self) -> QFrame:
        st = QFrame()
        st.setObjectName("Status")
        st.setFixedHeight(30)
        h = QHBoxLayout(st)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(16)
        self.status_left = label("", "StatusText")
        h.addWidget(self.status_left)
        h.addStretch(1)
        self.status_dot = QLabel("●")
        h.addWidget(self.status_dot)
        self.status_right = label("", "StatusText")
        h.addWidget(self.status_right)
        return st

    def _refresh_status(self):
        root = self.s.settings.jobs_root
        free = system.free_disk_gb(root) if os.path.isdir(root) else 0
        self.status_left.setText(f"Jobs folder: {root}" + (f"   ·   Free disk: {free:,.0f} GB" if free else ""))
        e = self.s.engine
        self.status_dot.setStyleSheet(f"color: {theme.OK if e.ok else theme.ERROR}; font-size: 10px;")
        self.status_right.setText((e.message if e.ok else "Processing engine not installed") +
                                  f"   ·   {system.describe_machine()}")

    def _refresh_header(self):
        page = self.stack.currentWidget()
        crumb, title = page.heading()
        wizard = page in self.wizard
        if wizard:
            crumb = "Drone mapping  ›  " + (self.s.job.name if self.s.job else "New job")
        self.crumb.setText(crumb)
        self.title.setText(title)
        self.stepper.setVisible(wizard)
        if wizard:
            self.stepper.set_active(self.wizard.index(page))
        aw = page.header_actions()
        for w in self._action_widgets:
            w.setVisible(w is aw)
        if aw is not None and aw not in self._action_widgets:
            self.hdr_actions.addWidget(aw)
            self._action_widgets.append(aw)
            aw.setVisible(True)

    # ------------------------------------------------------------------ navigation
    def show_page(self, page):
        self.stack.setCurrentWidget(page)
        if page in self.wizard:
            self._last_wizard = page
        key = ("home" if page is self.home else "georef" if page is self.georef else
               "flood" if page is self.models else "measure" if page is self.measure else
               "jobs" if page is self.jobs else "drone")
        for k, b in self.nav.items():
            b.setChecked(k == key)
        page.enter()
        self._refresh_header()
        self._refresh_status()

    def _busy(self) -> bool:
        if self.process.busy():
            QMessageBox.information(self, "A job is processing",
                                    "A drone mapping job is still processing. Open “Drone mapping” to follow it, "
                                    "or cancel it there before starting or opening another job.")
            return True
        return False

    def _nav_drone(self):
        if self.s.job is not None and self._last_wizard is not None:
            self.show_page(self._last_wizard)
        else:
            self.new_job()

    def _model_from_job(self):
        job = self.s.job
        self.show_page(self.models)
        self.models.use_job(job)

    def _measure_from_job(self):
        job = self.s.job
        self.show_page(self.measure)
        self.measure.use_job(job)

    def show_home(self):
        if not self.process.busy():
            self.s.job = None
            self._last_wizard = None
        self.show_page(self.home)

    def new_job(self):
        if self._busy():
            self.show_page(self.process)
            return
        self.s.job = None
        self.s.photos, self.s.survey, self.s.flight_height = [], None, None
        self.photos.name.clear()
        self.photos.folder.setText("-")
        self.photos.status.setText("")
        self.show_page(self.photos)

    def show_about(self):
        d = AboutDialog(self)
        d.exec()

    def show_support(self):
        SupportDialog(self).exec()

    def open_settings(self):
        if SettingsDialog(self.s, self).exec():
            self.stack.currentWidget().enter()
            self._refresh_status()

    def open_row(self, row):
        if row.kind == "drone":
            self.open_job(row.ref)
        elif row.kind == "georef":
            self.show_page(self.georef)
            self.georef.load_session(row.ref)
        else:
            self.show_page(self.models)
            self.models.load_scenario(row.ref)

    def _browse_job(self):
        d = QFileDialog.getExistingDirectory(self, f"Open a {APP_NAME} job folder", self.s.settings.jobs_root)
        if d:
            if jobs.Job.is_job_dir(d):
                self.open_job(d)
            else:
                QMessageBox.information(self, "Not a job folder",
                                        f"That folder is not a {APP_NAME} drone mapping job (no job.json inside).")

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
        if self._busy():
            return
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
