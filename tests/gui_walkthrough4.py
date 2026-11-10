"""Headless walkthrough of Plan earthworks (lot / pad and pile) with screenshots.

QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR=C:\\Windows\\Fonts python tests/gui_walkthrough4.py OUTDIR
"""
import math
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "shots4")
os.makedirs(OUT, exist_ok=True)
tmp = tempfile.mkdtemp()
os.environ["HOME"] = tmp
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "cfg")
os.environ["APPDATA"] = os.path.join(tmp, "cfg")

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox, QProgressDialog  # noqa: E402

from fieldortho.core import activity, earthworks as ew  # noqa: E402
from fieldortho.core.settings import Settings  # noqa: E402
from fieldortho.gui import plan_panel  # noqa: E402
from fieldortho.gui.main_window import MainWindow  # noqa: E402
from fieldortho.gui.theme import STYLESHEET  # noqa: E402
import synthetic_site as w3  # noqa: E402

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: print("WARNING DIALOG:", a[1:3]) or QMessageBox.Ok)
QMessageBox.exec = lambda self: 0
QProgressDialog.exec = lambda self: 0
QInputDialog.getText = staticmethod(lambda *a, **k: ("Barangay hall pad", True))
plan_panel.ExportDialog.exec = lambda self: QDialog.Accepted

app = QApplication.instance() or QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(STYLESHEET)
job_dir = w3.synthetic_job(os.path.join(tmp, "jobs"))
st = Settings(odm_path=os.path.join(HERE, "fake_odm"), jobs_root=os.path.dirname(job_dir), threads=2)
st.recent_jobs = [job_dir]
st.save()
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
shot("40_plan_type")


def idle():
    return not P.timer.isActive() and not (P.task is not None and P.task.isRunning())


# ---- lot / pad: 30 x 30 m square on the gently sloping ground, west of the pile
CX, CY = w3.CX - 25, w3.CY + 5
sq = [(CX - 15, CY - 15), (CX + 15, CY - 15), (CX + 15, CY + 15), (CX - 15, CY + 15)]
P._choose_type("pad")
P.next()
assert P.step() == "draw" and not P.next_btn.isEnabled()
for x, y in sq:
    m._click(*m._px(x, y))
m._finish()
assert P.next_btn.isEnabled()
shot("41_plan_draw")
P.next()
P.slope_cb.setChecked(True)
P.slope.setValue(1.0)
wait(lambda: idle() and P.takeoff is not None)
shot("42_plan_height")
P.next()
wait(idle)
shot("43_plan_edges")
P.next()
P.truck.setValue(12)
P.next()
wait(lambda: idle() and P.takeoff is not None)
shot("44_plan_results")
t = P.takeoff
ref = ew.design_pad(m._dem_path(), sq, ew.PadDesign("balanced", slope_pct=1.0, truck_m3=12))
print(f"pad: level {t.level:.3f}  cut {t.cut:,.1f}  fill {t.fill:,.1f}  sides {t.side_cut:,.1f}/{t.side_fill:,.1f}")
assert abs(t.cut - ref.cut) < 0.01 and abs(t.level - ref.level) < 1e-6
assert abs(t.haul.balance) < 0.02 * t.total + 1, t.haul.balance
assert [s for s, _ in t.haul.trips] == [6.0, 10.0, 12.0]

# save + report
P.export()
plans = os.listdir(ew.plans_dir(job_dir))
print("plans folder:", plans)
pkg = os.path.join(ew.plans_dir(job_dir), "Barangay hall pad")
files = sorted(os.listdir(pkg))
print("package:", files)
assert "Barangay_hall_pad_report.pdf" in files and "Barangay_hall_pad_quantities.xlsx" in files
acts = [a for a in activity.load() if a.kind == "earthworks"]
assert acts and acts[0].name == "Barangay hall pad", acts

# reopen from the Jobs list
m.set_mode("measure")
w.show_page(w.jobs)
row = next(r for r in w.jobs.table.rows if r.kind == "earthworks")
w.open_row(row)
wait(lambda: idle() and P.takeoff is not None)
assert m.mode == "plan" and P.step() == "result"
assert abs(P.takeoff.cut - t.cut) < 0.01
shot("45_plan_reopened")

# ---- pile on the surface model
P.new_plan()
P._choose_type("pile")
P.next()
assert m.src_kind.currentData() == "dsm"
ring = [(w3.CX + 25 * math.cos(a), w3.CY + 25 * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 40, endpoint=False)]
m.set_shape(ring)
P.next()
wait(idle)
shot("46_pile_ref")
P.next()
P.next()
wait(lambda: idle() and P.takeoff is not None)
shot("47_pile_results")
print("pile:", P.takeoff.headline(), P.takeoff.haul.sentence())
print("OK plan walkthrough")
