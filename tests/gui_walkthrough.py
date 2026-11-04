"""Drive the whole GUI flow headless with the test-double engine and save a
screenshot of every screen. Run:  QT_QPA_PLATFORM=offscreen python tests/gui_walkthrough.py OUTDIR"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "shots")
os.makedirs(OUT, exist_ok=True)
tmp = tempfile.mkdtemp()
os.environ["HOME"] = tmp
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "cfg")
os.environ.setdefault("FAKE_ODM_DELAY", "0.4")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from fieldortho.core.settings import Settings  # noqa: E402
from fieldortho.gui.main_window import MainWindow  # noqa: E402
from fieldortho.gui.theme import STYLESHEET  # noqa: E402

PHOTOS = os.environ.get("FIELDORTHO_TEST_PHOTOS", "/tmp/data/aukerman/images")
app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(STYLESHEET)
st = Settings(odm_path=os.path.join(HERE, "fake_odm"), jobs_root=os.path.join(tmp, "jobs"), threads=2)
st.save()
w = MainWindow(st)
w.resize(1360, 860)
w.show()


def wait(cond, timeout=600):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.02)
        if time.time() - t0 > timeout:
            raise TimeoutError
    for _ in range(20):
        app.processEvents()


def shot(name):
    for _ in range(10):
        app.processEvents()
    w.grab().save(os.path.join(OUT, name + ".png"))
    print("saved", name)


shot("1_home_empty")
w.new_job()
shot("2_photos_empty")
w.photos.load_folder(PHOTOS)
wait(lambda: bool(w.s.photos))
w.photos.name.setText("Aukerman Park 2026-11-04")
shot("3_photos_loaded")
w.photos.next_btn.click()
w.check.height.setValue(100)
w.check._reanalyze()
shot("4_check")
w.check.next_btn.click()
shot("5_options")
w.options.next_btn.click()
wait(lambda: w.process.pct > 40)
w.process._toggle_log()
shot("6_processing")
wait(lambda: w.stack.currentWidget() is w.results)
shot("7_results_ortho")
w.results.tabs.setCurrentIndex(1)
shot("8_results_dsm")
w.results.tabs.setCurrentIndex(2)
shot("9_results_dtm")
w.show_home()
shot("10_home_with_job")

# failure path
os.environ["FAKE_ODM_FAIL"] = "openmvs"
w.new_job()
w.photos.load_folder(PHOTOS)
wait(lambda: bool(w.s.photos))
w.photos.name.setText("Failing job")
w.photos.next_btn.click()
w.check.next_btn.click()
w.options.next_btn.click()
wait(lambda: w.process.retry_btn.isVisible())
shot("11_failed")
print("OK")

# GCP tagging dialog on the finished job
from fieldortho.gui.dialogs import GcpDialog, SettingsDialog  # noqa: E402
w.open_job(w.s.settings.recent_jobs[-1])
ph0 = sorted([p for p in w.s.photos if p.x is not None], key=lambda p: p.name)[30]
csvp = os.path.join(tmp, "gcps.csv")
with open(csvp, "w") as f:
    f.write("name,easting,northing,elevation\n")
    f.write(f"GCP1,{ph0.x + 4:.3f},{ph0.y - 6:.3f},301.25\nGCP2,{ph0.x + 60:.3f},{ph0.y + 20:.3f},302.10\n"
            f"CP1,{ph0.x - 50:.3f},{ph0.y - 30:.3f},299.80\n")
from fieldortho.core import gcp as gcpmod  # noqa: E402
d = GcpDialog(w.s, w)
d.gs = gcpmod.GcpSet.from_csv(csvp, f"EPSG:{w.s.survey.epsg}")
d._fill()
d.show()
d.resize(1300, 800)
wait(lambda: d.thumbs.count() > 0, 30)
d._mark(2440, 1830)
d.thumbs.setCurrentRow(1)
d._mark(2300, 1700)
d.thumbs.setCurrentRow(0)
for _ in range(30):
    app.processEvents()
d.grab().save(os.path.join(OUT, "12_gcp_dialog.png"))
print("saved 12_gcp_dialog")
d.close()
sd = SettingsDialog(w.s, w)
sd.show()
for _ in range(20):
    app.processEvents()
sd.grab().save(os.path.join(OUT, "13_settings.png"))
print("saved 13_settings")
