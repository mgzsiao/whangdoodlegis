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
import rasterio  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from fieldortho.core import jobs, measure as ms  # noqa: E402
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

X0, Y0, CELL, N = 695600.0, 982600.0, 0.1, 1000        # 100 x 100 m, UTM 51N
CX, CY = X0 + 50, Y0 - 50


def synthetic_job(root):
    job_dir = os.path.join(root, "Synthetic pile site")
    gis = os.path.join(job_dir, "GIS")
    os.makedirs(gis)
    r, c = np.mgrid[0:N, 0:N]
    x, y = X0 + (c + 0.5) * CELL, Y0 - (r + 0.5) * CELL
    ground = 120 + 0.05 * (x - X0) + 0.5 * np.sin((y - Y0) / 15)
    pile = np.clip(10 - np.hypot(x - CX, y - CY) * 0.5, 0, None)          # cone 10 m high, 20 m radius
    files = {}
    for key, z in (("dtm", ground), ("dsm", ground + pile)):
        p = os.path.join(gis, f"site_{key.upper()}.tif")
        with rasterio.open(p, "w", driver="GTiff", width=N, height=N, count=1, dtype="float32", crs="EPSG:32651",
                           transform=from_origin(X0, Y0, CELL, CELL), nodata=-9999) as ds:
            ds.write(z.astype("float32"), 1)
        files[key] = p
    job = jobs.Job(name="Synthetic pile site", job_dir=job_dir, status=jobs.STATUS_DONE,
                   summary={"files": files})
    job.save()
    return job_dir


job_dir = REAL_JOB or synthetic_job(os.path.join(tmp, "jobs"))
_store = ms.store_path(job_dir)
_backup = open(_store, "rb").read() if os.path.exists(_store) else None     # real job: put back afterwards
if REAL_JOB and _backup is not None:
    os.remove(_store)
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

# saved list, show one again, export
m.saved_list.setCurrentRow(0)
m._show_saved()
wait(lambda: not busy())
shot("37_saved_list")
saved = ms.load_all(job_dir)
print("saved:", [(s.name, s.summary()) for s in saved])
assert len(saved) == 5
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
