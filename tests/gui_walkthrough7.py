"""Headless walkthrough: georeferencing by pointing the grid by hand (grid crossings).

QT_QPA_PLATFORM=offscreen python tests/gui_walkthrough7.py OUTDIR

Uses the MGB test map. The true crossing positions come from the automatic detection; the
'user' clicks a few pixels off each crossing and types the values, and the result must match
the automatic one (RMSE < 1 m).
"""
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "shots7")
os.makedirs(OUT, exist_ok=True)
tmp = tempfile.mkdtemp()
os.environ["HOME"] = tmp
os.environ["APPDATA"] = os.path.join(tmp, "cfg")
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "cfg")

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from fieldortho.core import georef as G  # noqa: E402
from fieldortho.core.settings import Settings  # noqa: E402
from fieldortho.gui import georef_page as GP  # noqa: E402
from fieldortho.gui.main_window import MainWindow  # noqa: E402
from fieldortho.gui.theme import STYLESHEET  # noqa: E402

QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: print("WARNING:", a[1:3]) or QMessageBox.Ok)

MAP = os.path.join(HERE, "data", "maps", "poblacion_landslide.jpg")
LON0, LAT0, STEP = G.parse_angle("124d47'E"), G.parse_angle("8d52'N"), 1 / 60

# reference: automatic detection with the known labels (as the CLI does)
rgb = G.load_rgb(MAP)
det = G.detect(rgb)
for i, m in enumerate(det.meridians):
    m.value = LON0 + i * STEP
for i, p in enumerate(det.parallels):
    p.value = LAT0 - i * STEP
auto = G.make_gcps(det)
ref_fit = G.fit(G.make_gcps(det), "EPSG:4253", 1)
print(f"automatic: {ref_fit.n} points, RMSE {ref_fit.rmse_m:.2f} m")
# four corners + centre of the crossings
P = np.array([[g.px, g.py] for g in auto])
pick = sorted({int(np.argmin(P @ np.array(v))) for v in ((1, 1), (-1, 1), (1, -1), (-1, -1))})
pick.append(int(np.argmin(np.hypot(*(P - P.mean(0)).T))))
targets = [auto[i] for i in dict.fromkeys(pick)]


class FakeCoord:
    """Stands in for the coordinates window: types the true values (or accepts the suggestion)."""
    queue = []
    seen_presets = []

    def __init__(self, geo, lab, x, y, parent=None, title="", note="", preset=None):
        self.v = FakeCoord.queue.pop(0)
        FakeCoord.seen_presets.append(preset)

    def exec(self):
        return True

    def values(self):
        return self.v


app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(STYLESHEET)
from fieldortho.gui.theme import palette  # noqa: E402
app.setPalette(palette())
st = Settings(odm_path=os.path.join(HERE, "fake_odm"), jobs_root=os.path.join(tmp, "jobs"), threads=2)
st.save()
w = MainWindow(st)
w.resize(1440, 900)
w.show()
w.show_page(w.georef)
pg = w.georef
pg.open_image(MAP)
pg.task.wait()
app.processEvents()
pg.set_grid_mode(1)
app.processEvents()
assert pg.grid_stack.currentIndex() == 1 and pg.cross_btn.isChecked()
w.grab().save(os.path.join(OUT, "70_manual_grid_start.png"))

GP.CoordDialog = FakeCoord
rng = np.random.default_rng(1)
for g in targets:
    FakeCoord.queue.append((g.gx, g.gy))
    dx, dy = rng.uniform(-2.5, 2.5, 2)
    pg._canvas_click(g.px + dx, g.py + dy)          # the user's click, a few pixels off
    app.processEvents()
cr = pg._crosses()
print("crossings:", len(cr), "suggested values offered:", sum(p is not None for p in FakeCoord.seen_presets))
assert len(cr) == len(targets) and pg.cross_next.isEnabled()
err_px = [np.hypot(c.px - g.px, c.py - g.py) for c, g in zip(cr, targets)]
print("click fine-tuning error (px):", [round(e, 2) for e in err_px])
assert FakeCoord.seen_presets[-1] is not None
sx, sy = FakeCoord.seen_presets[-1]
assert abs(sx - targets[-1].gx) < 1e-9 and abs(sy - targets[-1].gy) < 1e-9, "suggestion should match the grid"
w.grab().save(os.path.join(OUT, "71_manual_grid_points.png"))
pg._crosses_done()
app.processEvents()
f = pg.fit
print(f"manual: {f.n} points, RMSE {f.rmse_m:.2f} m, pixel {f.pixel_m:.2f} m")
assert f.rmse_m < 1.0
# same place as the automatic fit (centre of the map within 1.5 m)
h, wd = rgb.shape[:2]
a = np.array(ref_fit.forward([wd / 2], [h / 2])).ravel()
b = np.array(f.forward([wd / 2], [h / 2])).ravel()
print(f"difference to automatic at map centre: {np.hypot(*(a - b)):.2f} m")
assert np.hypot(*(a - b)) < 1.5
w.grab().save(os.path.join(OUT, "72_manual_grid_fit.png"))
print("OK")
