"""Headless walkthrough of the georeferencer, modelling and export screens.
QT_QPA_PLATFORM=offscreen python tests/gui_walkthrough2.py OUTDIR MAP_IMAGE"""
import math
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "shots2")
MAP = sys.argv[2] if len(sys.argv) > 2 else "/mnt/user-data/uploads/8cc09f41-image.jpg"
os.makedirs(OUT, exist_ok=True)
tmp = tempfile.mkdtemp()
os.environ["HOME"] = tmp
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "cfg")
os.environ["APPDATA"] = os.path.join(tmp, "cfg")          # Windows: keep the real settings untouched
os.environ["FAKE_ODM_DELAY"] = "0.01"

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from fieldortho.core import jobs, engine, photos as ph  # noqa: E402
from fieldortho.core.pipeline import JobProcessor  # noqa: E402
from fieldortho.core.settings import Settings  # noqa: E402
from fieldortho.gui.main_window import MainWindow  # noqa: E402
from fieldortho.gui.theme import STYLESHEET  # noqa: E402
import make_demo_dem  # noqa: E402

from PySide6.QtWidgets import QMessageBox  # noqa: E402
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)      # no blocking dialogs headless
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: print("WARNING DIALOG:", a[1:3]) or QMessageBox.Ok)
app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(STYLESHEET)
st = Settings(odm_path=os.path.join(HERE, "fake_odm"), jobs_root=os.path.join(tmp, "jobs"), threads=2)
# one processed job so 'Export...' and the job list have content
photos = ph.scan_folder(os.environ.get("FIELDORTHO_TEST_PHOTOS", "/tmp/data/aukerman/images"))
job = jobs.Job(name="Aukerman Park", job_dir=jobs.unique_job_dir(st.jobs_root, "Aukerman Park"),
               photos=[p.path for p in photos][:40])
assert JobProcessor(job, st, engine.detect(st.odm_path)).run(), job.error
st.recent_jobs = [job.job_dir]
st.save()
w = MainWindow(st)
w.resize(1440, 900)
w.show()


def pump(n=20):
    for _ in range(n):
        app.processEvents()


def wait(cond, timeout=300):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.02)
        if time.time() - t0 > timeout:
            raise TimeoutError
    pump()


def shot(name, widget=None):
    pump()
    (widget or w).grab().save(os.path.join(OUT, name + ".png"))
    print("saved", name)


w.show_home()
shot("20_home_tools")

# ---------------------------------------------------------------- georeferencer
g = w.georef
w.show_page(g)
g.open_image(MAP)
wait(lambda: g.det is not None)
mer = [ed for ln, ed in g.value_edits if ln.kind == "meridian"]
par = [ed for ln, ed in g.value_edits if ln.kind == "parallel"]
mer[0].setText("124°47'0\"E")
par[0].setText("8°52'0\"N")
g.interval.setText("1'")
g._fill_series()
shot("21_georef_grid")
g._make_points()
shot("22_georef_points")
g.cls_on.setChecked(True)
g.cls_preset.setCurrentIndex(g.cls_preset.findData("mgb_landslide"))
g._preview_classes()
wait(lambda: g.canvas.overlay is not None, 120)
g.tabs.setCurrentIndex(2)
g.out_crs.set_code("EPSG:3125")
shot("23_georef_export")
g.out_dir.edit.setText(os.path.join(tmp, "georef_out"))
g._export()
t0 = time.time()
while "files written" not in g.exp_status.text() and time.time() - t0 < 300:
    app.processEvents()
    time.sleep(0.05)
    if int((time.time() - t0) * 20) % 200 == 0:
        print("  export:", g.exp_bar.value(), g.exp_status.text()[:60], g.task.isRunning(), flush=True)
print("georef export:", g.exp_status.text()[:160])
from PySide6.QtWidgets import QMessageBox  # noqa: E402
for wdg in app.topLevelWidgets():
    if isinstance(wdg, QMessageBox):
        wdg.done(0)

# ---------------------------------------------------------------- models
dem_path = make_demo_dem.make(os.path.join(tmp, "demo_dtm.tif"))
m = w.models
w.show_page(m)
m.source.addItem("Demo terrain (synthetic)", (dem_path, None, "demo"))
m.source.setCurrentIndex(m.source.count() - 1)
pump()
rc = int((0.55 * 1100 * 2 + 140 * math.sin(1400 / 330) + 60 * math.sin(1400 / 97)) / 2)
m._click(700.5, rc + 0.5)
wait(lambda: m.hand is not None, 120)
m.rise.setValue(2.5)
m._run_flood()
shot("24_model_flood_river")
scen = os.path.join(tmp, "scenarios")
os.makedirs(scen, exist_ok=True)
m._save_scenario("flood", scen, "demo_river_2.5m", "EPSG:32651", "✔ Exported · test")
m.fmode.setCurrentIndex(1)
m.connect.setCurrentIndex(m.connect.findData("edges"))
m.rise.setValue(4.0)
m._click(30.5, 300.5)
m._run_flood()
shot("25_model_flood_still")
# landslide on the steep north valley wall
m.tabs.setCurrentIndex(1)
c0 = 1000
rcc = int((0.55 * 1100 * 2 + 140 * math.sin(2 * c0 / 330) + 60 * math.sin(2 * c0 / 97)) / 2)
m.bg_group.button(2).setChecked(True)
m._show_background()
m.draw_btn.setChecked(True)
for x, y in [(950, rcc - 105), (1060, rcc - 105), (1060, rcc - 60), (950, rcc - 60)]:
    m._click(x, y)
m.draw_btn.setChecked(False)
m.wet.setValue(70)
m._run_slide()
wait(lambda: m.slide_res is not None, 300)
m.bg_group.button(0).setChecked(True)
m._show_background()
shot("26_model_landslide")
m._save_scenario("landslide", scen, "demo_landslide", "EPSG:32651", "✔ Exported · test")
print("landslide stats", {k: round(v, 2) for k, v in m.slide_res.stats.items()})

# ---------------------------------------------------------------- export dialog
w.open_job(job.job_dir)
pump()
shot("27_results_with_export")
from fieldortho.gui.export_dialog import JobExportDialog  # noqa: E402
dlg = JobExportDialog(w.s.job, w)
dlg.crs.set_code("EPSG:3125")
dlg.show()
pump(30)
shot("28_export_dialog", dlg)
dlg.close()

# ---------------------------------------------------------------- jobs list, dialogs
w.show_page(w.jobs)
shot("29_jobs")
from fieldortho.gui.about import AboutDialog, SupportDialog  # noqa: E402
from fieldortho.gui.inputs import CoordDialog  # noqa: E402
for name, d in (("30_about", AboutDialog(w)), ("31_support", SupportDialog(w)),
                ("32_coord_dialog", CoordDialog(True, "Luzon 1911", 812.4, 655.0, w))):
    d.show()
    pump(20)
    if name == "32_coord_dialog":
        d.lon.setText("124 48 0")
        d.lat.deg.setText("8"); d.lat.min.setText("61"); d.lat._edited("61")
        pump(10)
    shot(name, d)
    d.close()
# reopen the georef session and the scenarios from the Jobs list
from fieldortho.gui.jobs_page import collect  # noqa: E402
rows = collect(w.s.settings)
print("jobs list:", [(r.kind, r.name, r.status) for r in rows])
assert {r.kind for r in rows} >= {"drone", "georef", "flood", "landslide"}, rows
for r in rows:
    if r.kind == "flood":
        w.open_row(r)
        wait(lambda: m.flood_res is not None and m.hand is not None, 120)
        shot("33_reopened_flood")
        assert abs(m.rise.value() - 2.5) < 1e-6
    elif r.kind == "landslide":
        m.slide_res = None
        w.open_row(r)
        wait(lambda: m.slide_res is not None, 300)
        shot("34_reopened_landslide")
    elif r.kind == "georef":
        g.gcps = []
        w.open_row(r)
        pump()
        assert len(g.gcps) >= 9 and g.fit is not None, len(g.gcps)
        shot("35_reopened_georef")
print("OK")
