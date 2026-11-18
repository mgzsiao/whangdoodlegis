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
from fieldortho.gui.theme import palette  # noqa: E402
app.setPalette(palette())
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
# 1.2 guided road steps: standard, then one part of the road per step
assert P.step() == "std" and P.r_std.currentData() == "fmr_low"
P.r_std.setCurrentIndex(P.r_std.findData("fmr"))
assert abs(P.r_width.value() - 6.1) < 1e-9 and abs(P.r_min_radius.value() - 30) < 1e-9
shot("51a_road_standard")
for key in ("carriage", "shoulders", "layers", "ditch", "slopes"):
    P.next()
    assert P.step() == key and P.change_btn.isVisible() and P.next_btn.text().startswith("OK, next")
    form = P.part_forms[key]
    assert not form.editing and all(m.text() in ("", "standard") for m in form.marks), [m.text() for m in form.marks]
    if key == "layers":                       # Change dimensions: the values become editable and are marked
        P._toggle_change()
        assert form.editing and not P.r_pccp.isHidden() and P.change_btn.text() == "Done changing"
        P.r_pccp.setValue(180)
        assert "your value" in form.marks[0].text() and "200 mm" in form.marks[0].text(), form.marks[0].text()
        assert "Thinner than the DPWH minimum" in P.part_warn["layers"].text()
        shot("54b_road_layers_changed")
        P.r_pccp.setValue(200)
        P._toggle_change()
        assert not form.editing and P.r_pccp.isHidden() and form.shown[0].text() == "200 mm"
    shot(f"54_road_{key}")
P.next()
assert P.step() == "curves" and P.curve_rows, "one bend expected"
pump()
shot("51b_road_curves")
assert m.preview_al is not None and len(m.preview_al.curves) == 1
P.next()
shot("52_road_stations")
P.next()
wait(lambda: idle())
assert P.step() == "rise" and P.max_grade.value() == 12 and P.max_fill.value() == 1.5 and P.max_cut.value() == 1.5
assert all(m.text() == "starting value" for m in P.req_form.marks), [m.text() for m in P.req_form.marks]
P.max_cut.setValue(1.0)
assert "your value" in P.req_form.marks[3].text()
assert not P.offset_m.isEnabled()
_set_r = [b_ for b_ in P.pgroup.buttons() if b_.property("key") == "raised"][0]
_set_r.setChecked(True)
assert P.offset_m.isEnabled() and P.road_design().profile == "raised"
wait(lambda: idle())
shot("53a_road_raised")
[b_ for b_ in P.pgroup.buttons() if b_.property("key") == "gentle"][0].setChecked(True)
wait(lambda: idle())
assert P.road_design().max_cut == 1.0
shot("53_road_height")
P.next()
assert P.step() == "vcurves" and P.vc_yes.isChecked()
wait(lambda: idle())
r_ = P.takeoff
print("vertical:", [(round(q[0], 1), round(q[1], 2)) for q in r_.pvis],
      [(c.kind, round(c.length, 1), round(c.k, 1)) for c in r_.vcurves])
assert len(r_.pvis) >= 2 and P.pvi_table.rowCount() == len(r_.pvis)
assert P.vc_min.value() == 60 and all(c.length >= 60 - 1e-6 or c.reduced or c.length == 0 for c in r_.vcurves)
shot("53b_road_vertical_curves")
P.next()
assert P.step() == "soil"
P.next()
wait(lambda: idle() and P.takeoff is not None)
P.select_station(4)
shot("55_road_results")
r = P.takeoff
ref = rdm.design_road(m._dem_path(), line, P.road_design())
assert len(r.alignment.curves) == 1 and r.standard == "fmr"
print("warnings:", r.warnings)
m.road_profile.set_mode("mass")
shot("55b_road_mass_haul")
m.road_profile.set_mode("profile")
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
