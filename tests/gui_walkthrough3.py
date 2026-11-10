"""Headless walkthrough of the Measure & earthworks screen with screenshots.

QT_QPA_PLATFORM=offscreen python tests/gui_walkthrough3.py OUTDIR [JOB_DIR]

Without JOB_DIR a synthetic job is made (sloping ground with a 10 m cone-shaped pile in the
surface model), and the measured numbers are checked against the cone formula.
"""
import math
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "shots3")
REAL_JOB = sys.argv[2] if len(sys.argv) > 2 else None
os.makedirs(OUT, exist_ok=True)
tmp = tempfile.mkdtemp()
os.environ["HOME"] = tmp
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "cfg")
os.environ["APPDATA"] = os.path.join(tmp, "cfg")          # keep the real settings untouched

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from fieldortho.core import measure as ms  # noqa: E402
from fieldortho.core.settings import Settings  # noqa: E402
from fieldortho.gui.main_window import MainWindow  # noqa: E402
from fieldortho.gui.theme import STYLESHEET  # noqa: E402

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: print("WARNING DIALOG:", a[1:3]) or QMessageBox.Ok)
_names = iter(["Pile A", "Access path", "Gate to top", "Lot 1", "Spot height"])
QInputDialog.getText = staticmethod(lambda *a, **k: (next(_names, "Saved"), True))

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(STYLESHEET)

sys.path.insert(0, HERE)
from synthetic_site import CX, CY, synthetic_job  # noqa: E402


job_dir = REAL_JOB or synthetic_job(os.path.join(tmp, "jobs"))
later_dir = None if REAL_JOB else synthetic_job(os.path.join(tmp, "jobs"), "Later survey", pile_h=12.0, shift=0.3)
_store = ms.store_path(job_dir)
_backup = open(_store, "rb").read() if os.path.exists(_store) else None     # real job: put back afterwards
if REAL_JOB and _backup is not None:
    os.remove(_store)
st = Settings(odm_path=os.path.join(HERE, "fake_odm"), jobs_root=os.path.dirname(job_dir), threads=2)
st.recent_jobs = [job_dir] + ([later_dir] if later_dir else [])
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


def busy():
    return m.task is not None and m.task.isRunning()


def tool(key):
    i = next(k for k, t in enumerate(m_tools) if t[0] == key)
    m.tool_group.button(i).setChecked(True)
    m._tool_changed(i)


def click_xy(x, y):
    m._click(*m._px(x, y))


w.show_home()
shot("30_home_with_measure")
m = w.measure
from fieldortho.gui.measure_page import TOOLS as m_tools  # noqa: E402
w.show_page(m)
wait(lambda: m.view is not None)
t = m.view.transform
h, wd = m.view.shape
if REAL_JOB:
    cx, cy = t.c + wd / 2 * t.a, t.f + h / 2 * t.e
    span = min(wd * t.a, -h * t.e) * 0.12
else:
    cx, cy, span = CX, CY, 25
shot("31_measure_open")

# point
tool("point")
click_xy(cx, cy)
wait(lambda: not busy() and m.result is not None)
shot("32_point")
m._save()

# path across
tool("path")
for k in (-2, -1, 0, 1, 2):
    click_xy(cx + k * span * 0.9, cy + (k % 2) * span * 0.2)
m._finish()
wait(lambda: not busy() and m.result is not None)
shot("33_path_profile")
m._save()
if not REAL_JOB:
    assert abs(m.result.length_2d - sum(math.dist(a, b) for a, b in zip(m.pts[:-1], m.pts[1:]))) < 1e-6

# height difference
tool("height")
click_xy(cx - span, cy - span)
click_xy(cx, cy)
wait(lambda: not busy() and m.result is not None)
shot("34_height")
m._save()

# volume of the pile on the surface model (DSM)
tool("volume")
m.src_kind.setCurrentIndex(m.src_kind.findData("dsm"))
wait(lambda: m.view is not None)
for a in np.linspace(0, 2 * math.pi, 24, endpoint=False):
    click_xy(cx + span * math.cos(a), cy + span * math.sin(a))
m._finish()
wait(lambda: not busy() and m.result is not None)
shot("35_volume_pile")
r = m.result
print(f"volume: cut {r.cut_m3:,.1f}  fill {r.fill_m3:,.1f}  net {r.net_m3:,.1f}  area {r.area_2d:,.1f}")
m._save()

# the same outline on the bare ground: the difference is the pile alone
m.src_kind.setCurrentIndex(m.src_kind.findData("dtm"))
wait(lambda: not busy() and m.result is not None and m.result is not r and not m.rerun.isActive())
if not REAL_JOB:
    cone = math.pi * 20 ** 2 * 10 / 3
    pile = r.net_m3 - m.result.net_m3
    print(f"pile alone: {pile:,.1f} m3 (cone formula {cone:,.1f})")
    assert abs(pile - cone) / cone < 0.005, (pile, cone)

# level the same area to a fixed elevation on the bare ground
lvl = next(b for b in m.ref_group.buttons() if b.property("ref") == "level")
lvl.setChecked(True)
m._ref_changed()
wait(lambda: not m.rerun.isActive() and not busy())
m.level.setValue(round(m.result.z_mean, 1))
wait(lambda: not m.rerun.isActive() and not busy())
m.truck.setValue(12)
shot("36_volume_level_pad")
print(f"level pad: cut {m.result.cut_m3:,.1f}  fill {m.result.fill_m3:,.1f}")
m._save()

# compare with a later survey (synthetic only): the pile grew from 10 m to 12 m, the later flight sits 0.3 m higher
if not REAL_JOB:
    m.src_kind.setCurrentIndex(m.src_kind.findData("dsm"))
    wait(lambda: not busy())
    tool("compare")
    m.cmp_job.setCurrentIndex(m.cmp_job.findData(later_dir))
    m.cmp_order.setCurrentIndex(m.cmp_order.findData("earlier"))
    for a in np.linspace(0, 2 * math.pi, 32, endpoint=False):
        click_xy(cx + 25 * math.cos(a), cy + 25 * math.sin(a))
    m._finish()
    wait(lambda: not busy() and m.result is not None and not m.rerun.isActive())
    shot("38_compare")
    r = m.result
    grow = math.pi / 3 * (24 ** 2 * 12 - 20 ** 2 * 10)
    print(f"compare: raised {r.raised_m3:,.1f} (expected {grow:,.1f})  lowered {r.lowered_m3:,.1f}  "
          f"shift {r.offset:+.3f}")
    assert abs(r.raised_m3 - grow) / grow < 0.01 and abs(r.offset - 0.3) < 0.01
    m._save()
    # 3-D model of the outline (dialog answered automatically)
    import fieldortho.gui.measure_page as mp
    out3d = os.path.join(tmp, "3D")
    mp._model3d_dialog = lambda page: {"kind": "ground", "shape": True, "detail": "light", "formats": ["dae", "stl"],
                                       "folder": out3d}
    from PySide6.QtWidgets import QProgressDialog
    QProgressDialog.exec = lambda self: 0
    m._export_3d()
    print("3D:", sorted(os.listdir(out3d)))
    assert any(f.endswith(".dae") for f in os.listdir(out3d))

# saved list, show one again, export
m.saved_list.setCurrentRow(0)
m._show_saved()
wait(lambda: not busy())
shot("37_saved_list")
saved = ms.load_all(job_dir)
print("saved:", [(s.name, s.summary()) for s in saved])
assert len(saved) == (5 if REAL_JOB else 6)
out = os.path.join(tmp, "export")
files = ms.export_measurements(saved, out, "site", m.view.crs_wkt, "EPSG:4326", ["gpkg", "kml"],
                               (124.6, 8.5), "walkthrough")
print("exported:", [os.path.basename(f) for f in files])

print("OK measure walkthrough")
if REAL_JOB:      # leave the real job's measurements as they were
    if _backup is None:
        os.remove(_store)
    else:
        open(_store, "wb").write(_backup)
