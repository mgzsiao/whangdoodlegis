"""Headless walkthrough of the road assistant with screenshots.

QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR=C:\\Windows\\Fonts python tests/gui_walkthrough5.py OUTDIR [JOB_DIR]
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "shots5")
REAL_JOB = sys.argv[2] if len(sys.argv) > 2 else None
os.makedirs(OUT, exist_ok=True)
tmp = tempfile.mkdtemp()
os.environ["HOME"] = tmp
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "cfg")
os.environ["APPDATA"] = os.path.join(tmp, "cfg")

from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox, QProgressDialog  # noqa: E402

from fieldortho.core import activity, earthworks as ew, road as rdm  # noqa: E402
from fieldortho.core.settings import Settings  # noqa: E402
from fieldortho.gui import plan_panel  # noqa: E402
from fieldortho.gui.main_window import MainWindow  # noqa: E402
from fieldortho.gui.theme import STYLESHEET  # noqa: E402
import synthetic_site as site  # noqa: E402

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: print("WARNING DIALOG:", a[1:3]) or QMessageBox.Ok)
QMessageBox.exec = lambda self: 0
QProgressDialog.exec = lambda self: 0
QInputDialog.getText = staticmethod(lambda *a, **k: ("Farm to market road", True))
plan_panel.ExportDialog.exec = lambda self: QDialog.Accepted

app = QApplication.instance() or QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(STYLESHEET)
if REAL_JOB:      # work on a copy of the job list entry only; plans go to a temp folder below
    job_dir = REAL_JOB
else:
    job_dir = site.synthetic_job(os.path.join(tmp, "jobs"))
st = Settings(odm_path=os.path.join(HERE, "fake_odm"), jobs_root=os.path.dirname(job_dir), threads=2)
st.recent_jobs = [job_dir]
st.save()
if REAL_JOB:      # keep the real job folder clean: save plans in a temp folder
    ew.plans_dir = lambda _jd: os.path.join(tmp, "Earthworks")
w = MainWindow(st)
w.resize(1440, 900)
w.show()


def pump(n=20):
    for _ in range(n):
        app.processEvents()


def wait(cond, timeout=120):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.02)
        if time.time() - t0 > timeout:
            raise TimeoutError
    pump()


def shot(name):
    pump()
    w.grab().save(os.path.join(OUT, name + ".png"))
    print("saved", name)


m = w.measure
P = m.plan
w.show_page(m)
wait(lambda: m.view is not None)
m.set_mode("plan")


def idle():
    return not P.timer.isActive() and not (P.task is not None and P.task.isRunning())


t = m.view.transform
h, wd = m.view.shape
x0, y0 = t.c + wd * t.a * 0.08, t.f + h * t.e * 0.75
x1, y1 = t.c + wd * t.a * 0.5, t.f + h * t.e * 0.42
x2, y2 = t.c + wd * t.a * 0.92, t.f + h * t.e * 0.5
line = [(x0, y0), (x1, y1), (x2, y2)]

P._choose_type("road")
shot("50_road_type")
P.next()
for x, y in line:
    m._click(*m._px(x, y))
m._finish()
assert P.next_btn.isEnabled()
shot("51_road_line")
P.next()
shot("52_road_stations")
P.next()
wait(lambda: idle())
shot("53_road_height")
P.next()
P.r_ditch.setChecked(True)
P.r_pave.setValue(0.35)
wait(lambda: idle())
shot("54_road_shape")
P.next()
P.next()
wait(lambda: idle() and P.takeoff is not None)
P.select_station(4)
shot("55_road_results")
r = P.takeoff
ref = rdm.design_road(m._dem_path(), line, rdm.RoadDesign(interval=10, ditch=True, pavement_t=0.35))
print(f"road: {r.headline()}  cut {r.cut:,.1f}  fill {r.fill:,.1f}")
assert abs(r.cut - ref.cut) < 0.01 and abs(r.fill - ref.fill) < 0.01
assert len(r.stations) == len(ref.stations)

P.export()
pdir = ew.plans_dir(job_dir)
pkg = os.path.join(pdir, "Farm to market road")
files = sorted(os.listdir(pkg))
print("package:", files)
assert "Farm_to_market_road_report.pdf" in files and "Farm_to_market_road_stations.csv" in files
acts = [a for a in activity.load() if a.kind == "earthworks"]
assert acts and acts[0].size.endswith("m road"), acts

m.set_mode("measure")
w.show_page(w.jobs)
row = next(rw for rw in w.jobs.table.rows if rw.kind == "earthworks")
w.open_row(row)
wait(lambda: idle() and P.takeoff is not None)
assert P.kind == "road" and P.step() == "result"
assert abs(P.takeoff.cut - r.cut) < 0.01
shot("56_road_reopened")
print("OK road walkthrough")
