"""Headless walkthrough of 'Use my own files' (Measure & earthworks, Flood & landslide) with screenshots.

QT_QPA_PLATFORM=offscreen python tests/gui_walkthrough6.py OUTDIR
"""
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "shots6")
os.makedirs(OUT, exist_ok=True)
tmp = tempfile.mkdtemp()
os.environ["HOME"] = tmp
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "cfg")
os.environ["APPDATA"] = os.path.join(tmp, "cfg")          # keep the real settings untouched

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from fieldortho.core.settings import Settings  # noqa: E402
from fieldortho.gui.main_window import MainWindow  # noqa: E402
from fieldortho.gui.own_files import HEAD_JOBS, HEAD_OWN, OwnFilesDialog  # noqa: E402
from fieldortho.gui.theme import STYLESHEET  # noqa: E402

WARN = []
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: WARN.append(a[1:3]) or QMessageBox.Ok)

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(STYLESHEET)
sys.path.insert(0, HERE)
from synthetic_site import synthetic_job  # noqa: E402

job_dir = synthetic_job(os.path.join(tmp, "jobs"))
st = Settings(odm_path=os.path.join(HERE, "fake_odm"), jobs_root=os.path.join(tmp, "jobs"), threads=2)
st.recent_jobs = [job_dir]
st.save()

# own files: a lat/lon DEM (like SRTM / IFSAR exports) of a valley
own = os.path.join(tmp, "my files")
os.makedirs(own)
n = 200
yy, xx = np.mgrid[0:n, 0:n]
z = 300 + 0.4 * np.abs(xx - n / 2) + 0.2 * yy
dem_path = os.path.join(own, "ifsar dtm.tif")
with rasterio.open(dem_path, "w", driver="GTiff", width=n, height=n, count=1, dtype="float32", crs="EPSG:4326",
                   transform=from_origin(124.60, 8.50, 1 / 3600, 1 / 3600), nodata=-9999) as ds:
    ds.write(z.astype("float32"), 1)


def shot(widget, name):
    app.processEvents()
    widget.grab().save(os.path.join(OUT, name + ".png"))


w = MainWindow(st)
w.resize(1440, 900)
w.show()
w.show_page(w.measure)
app.processEvents()
items = [w.measure.source.itemText(i) for i in range(w.measure.source.count())]
print("measure list:", items)
assert HEAD_JOBS in items and HEAD_OWN in items

# 1) dialog refuses without an elevation model
d = OwnFilesDialog(st, "measure", w)
d.resize(680, 640)
d.show()
shot(d, "60_own_files_dialog")
d._ok()
assert WARN and "required" in WARN[-1][0].lower(), WARN
print("refused without elevation:", WARN[-1][0])
# 2) a picture in the DTM box is refused with a reason
ortho = os.path.join(os.path.dirname(job_dir), os.path.basename(job_dir), "GIS")
d.rows["dtm"].edit.setText(dem_path)
d.name.setText("IFSAR valley")
d._ok()
assert d.fileset is not None, WARN
fs = d.fileset
print("created:", fs.job_dir, fs.files, fs.notes)

w.measure.use_job(fs)
app.processEvents()
assert w.measure.job is not None and w.measure.job.job_dir == fs.job_dir and w.measure.view is not None
items = [w.measure.source.itemText(i) for i in range(w.measure.source.count())]
print("measure list after:", items)
assert items.index(HEAD_OWN) < next(i for i, t in enumerate(items) if "IFSAR valley" in t)
shot(w, "61_measure_own_files")

w.show_page(w.models)
app.processEvents()
items = [w.models.source.itemText(i) for i in range(w.models.source.count())]
print("flood list:", items)
i = next(i for i, t in enumerate(items) if "IFSAR valley" in t)
w.models.source.setCurrentIndex(i)
app.processEvents()
assert w.models.dem is not None and abs(w.models.dem.cell - 31) < 3, w.models.dem.cell
shot(w, "62_flood_own_files")
w.models.source.showPopup()
shot(w, "63_flood_source_list")
w.models.source.hidePopup()

# Convert files: a PRS92 shapefile set (one without .prj) to WGS 84
from shapely.geometry import Polygon  # noqa: E402
from fieldortho.core import exporters as ex  # noqa: E402
shp_dir = os.path.join(tmp, "PRS92 shapefiles")
os.makedirs(shp_dir)
lots = [ex.Feature(Polygon([(475000, 940000), (475100, 940000), (475100, 940080)]), {"lot": "1", "area": 4000.0})]
ex.write_vector(lots, os.path.join(shp_dir, "lots.shp"), "shp", "EPSG:3125", "EPSG:3125", lonlat=(124.6, 8.5))
ex.write_vector(lots, os.path.join(shp_dir, "roads.shp"), "shp", "EPSG:3125", "EPSG:3125", lonlat=(124.6, 8.5))
os.remove(os.path.join(shp_dir, "roads.prj"))
w.show_page(w.convert)
w.convert.add_paths([shp_dir])
assert len(w.convert.items) == 2
shot(w, "64_convert_files")
w.convert.table.selectRow(1)
w.convert.items[1].crs_code = "EPSG:3125"
w.convert._fill()
w.convert.folder.edit.setText(os.path.join(tmp, "converted"))
w.convert._convert()
w.convert.task.wait()
app.processEvents()
outs = sorted(os.listdir(os.path.join(tmp, "converted")))
print("converted:", outs)
assert "lots_EPSG4326.shp" in outs and "roads_EPSG4326.shp" in outs and "conversion_notes.txt" in outs
shot(w, "65_convert_done")

# How things are computed
w.show_page(w.methods)
w.methods.show_topic("road")
shot(w, "66_methods_road")
w.methods.search.setText("HAND")
app.processEvents()
assert not w.methods.list.item(2).isHidden()          # flood topic matches
pdf = w.methods.save_pdf(os.path.join(tmp, "methods.pdf"))
assert pdf and os.path.getsize(pdf) > 20000, pdf
print("methods pdf", os.path.getsize(pdf))
print("OK")
