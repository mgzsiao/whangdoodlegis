"""Headless walkthrough of the lined drainage canal (1.2) with screenshots.

QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR=C:\\Windows\\Fonts python tests/gui_walkthrough8.py OUTDIR
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "shots8")
os.makedirs(OUT, exist_ok=True)
tmp = tempfile.mkdtemp()
os.environ["HOME"] = tmp
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "cfg")
os.environ["APPDATA"] = os.path.join(tmp, "cfg")

from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox, QProgressDialog  # noqa: E402

from fieldortho.core import activity, canal as cn, earthworks as ew  # noqa: E402
from fieldortho.core.settings import Settings  # noqa: E402
from fieldortho.gui import plan_panel  # noqa: E402
from fieldortho.gui.main_window import MainWindow  # noqa: E402
from fieldortho.gui.theme import STYLESHEET, palette  # noqa: E402
import synthetic_site as site  # noqa: E402

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: print("WARNING DIALOG:", a[1:3]) or QMessageBox.Ok)
QMessageBox.exec = lambda self: 0
QProgressDialog.exec = lambda self: 0
QInputDialog.getText = staticmethod(lambda *a, **k: ("Barangay drainage canal", True))
plan_panel.ExportDialog.exec = lambda self: QDialog.Accepted

app = QApplication.instance() or QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(STYLESHEET)
app.setPalette(palette())
job_dir = site.synthetic_job(os.path.join(tmp, "jobs"))
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


def idle():
    return not P.timer.isActive() and not (P.task is not None and P.task.isRunning())


# the synthetic ground rises 5% to the east: draw the canal from the east (high) end to the west (low) end,
# south of the pile, so the canal leaves the ground and gets earth banks
X0, Y0 = site.X0, site.Y0
line = [(X0 + 92, Y0 - 85), (X0 + 50, Y0 - 80), (X0 + 8, Y0 - 88)]

P._choose_type("canal")
assert P.steps()[2:7] == list(plan_panel.cp.PART_STEPS), P.steps()
shot("80_canal_type")
P.next()
for x, y in line:
    m._click(*m._px(x, y))
m._finish()
assert P.next_btn.isEnabled()
shot("81_canal_line")
for key in plan_panel.cp.PART_STEPS:
    P.next()
    assert P.step() == key and P.change_btn.isVisible() and P.next_btn.text().startswith("OK, next"), P.step()
    form = P.part_forms[key]
    assert not form.editing and all(t.text() in ("", "suggested") for t in form.marks), [t.text() for t in form.marks]
    if key == "c_shape":                      # Change dimensions: editable, marked as the user's value
        P._toggle_change()
        assert form.editing and not P.c_bw.isHidden()
        P.c_bw.setValue(0.6)
        assert "your value" in form.marks[0].text() and "0.5 m" in form.marks[0].text(), form.marks[0].text()
        shot("82b_canal_shape_changed")
        P.c_bw.setValue(0.5)
        P._toggle_change()
        assert not form.editing and form.shown[0].text() == "0.50 m", form.shown[0].text()
    if key == "c_fb":
        P._toggle_change()
        P.c_fb.setValue(0.9)
        assert "freeboard must be less" in P.part_warn["c_fb"].text(), P.part_warn["c_fb"].text()
        P.c_fb.setValue(0.2)
        P._toggle_change()
    shot(f"82_canal_{key}")
P.next()
assert P.step() == "stations"
P.next()
assert P.step() == "c_flow" and P.cq_rain.isChecked()
# 18 mm in 15 min = 72 mm/h
assert "72.0 mm/h" in P.c_i_label.text(), P.c_i_label.text()
P._measure_catchment()
wait(lambda: P._cat_task is None)
cd = P.canal_design()
print("catchment:", cd.area_ha, cd.flow_len, cd.drop, "|", P.c_cat_note.text())
assert cd.area_ha > 0.05 and cd.flow_len > 20 and cd.drop > 0
# land cover: half agricultural, half concrete -> C = (0.25 + 0.95) / 2
P.c_cov["agri"][0].setValue(50)
P.c_cov["concrete"][0].setValue(50)
assert "0.600" in P.c_cmix.text(), P.c_cmix.text()
q = cn.design_q(P.canal_design())
assert abs(q - 0.6 * cn.intensity(P.canal_design()) * cd.area_ha / 360) < 1e-9
assert f"{q:.3f}" in P.c_q_label.text(), P.c_q_label.text()
P.c_tc_method.setCurrentIndex(P.c_tc_method.findData("dpwh"))
assert "tc =" in P.c_tc_label.text()
shot("83_canal_flow")
# nothing on the flow page may be wider than the side panel
sa = P.findChild(plan_panel.QScrollArea)
assert sa is None or sa.widget().width() <= sa.viewport().width() + 1, (sa.widget().width(), sa.viewport().width())
sa.verticalScrollBar().setValue(sa.verticalScrollBar().maximum() // 2)
shot("83b_canal_flow_catchment")
sa.verticalScrollBar().setValue(sa.verticalScrollBar().maximum())
shot("83c_canal_flow_land_cover")
sa.verticalScrollBar().setValue(0)
P.next()
assert P.step() == "soil"
P.next()
wait(lambda: idle() and P.takeoff is not None)
P.select_station(3)
shot("84_canal_results")
r = P.takeoff
ref = cn.design_canal(m._dem_path(), line, P.canal_design())
assert abs(r.cut - ref.cut) < 0.01 and abs(r.fill - ref.fill) < 0.01 and r.fill > 0, (r.cut, r.fill)
print("canal:", r.headline())
print("warnings:", r.warnings)

P.export()
pdir = ew.plans_dir(job_dir)
pkg = os.path.join(pdir, "Barangay drainage canal")
files = sorted(os.listdir(pkg))
print("package:", files)
assert "Barangay_drainage_canal_report.pdf" in files and "Barangay_drainage_canal_stations.csv" in files
acts = [a for a in activity.load() if a.kind == "earthworks"]
assert acts and acts[0].size.endswith("m canal"), acts

m.set_mode("measure")
w.show_page(w.jobs)
row = next(rw for rw in w.jobs.table.rows if rw.kind == "earthworks")
w.open_row(row)
wait(lambda: idle() and P.takeoff is not None)
assert P.kind == "canal" and P.step() == "result"
assert abs(P.takeoff.cut - r.cut) < 0.01
assert abs(P.c_cov["concrete"][0].value() - 50) < 1e-9 and P.c_tc_method.currentData() == "dpwh"
shot("85_canal_reopened")
print("OK canal walkthrough")
