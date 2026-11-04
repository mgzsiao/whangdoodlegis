# PyInstaller spec - builds dist/FieldOrtho/ (one-folder app).
#   pyinstaller packaging/fieldortho.spec --noconfirm
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [(os.path.join(ROOT, "fieldortho", "resources"), "fieldortho/resources")]
datas += collect_data_files("rasterio")     # gdal_data, proj_data
datas += collect_data_files("pyproj")
datas += collect_data_files("pyogrio")      # GDAL/PROJ data for vector export
geoid = os.path.join(ROOT, "data", "us_nga_egm96_15.tif")
if os.path.exists(geoid):
    datas.append((geoid, "data"))

hidden = (collect_submodules("rasterio") + collect_submodules("pyogrio") + collect_submodules("shapely")
          + ["shapefile", "contourpy", "scipy.ndimage"])

common = dict(pathex=[ROOT], datas=datas, hiddenimports=hidden,
              excludes=["tkinter", "matplotlib", "IPython", "pytest"])

a = Analysis([os.path.join(ROOT, "packaging", "launch_gui.py")], **common)
b = Analysis([os.path.join(ROOT, "packaging", "launch_cli.py")], **common)

icon = os.path.join(ROOT, "fieldortho", "resources", "icon.ico")
pyz_a = PYZ(a.pure)
exe_a = EXE(pyz_a, a.scripts, [], exclude_binaries=True, name="FieldOrtho", console=False,
            icon=icon, version=None)
pyz_b = PYZ(b.pure)
exe_b = EXE(pyz_b, b.scripts, [], exclude_binaries=True, name="fieldortho-cli", console=True, icon=icon)
coll = COLLECT(exe_a, a.binaries, a.datas, exe_b, b.binaries, b.datas, name="FieldOrtho")
