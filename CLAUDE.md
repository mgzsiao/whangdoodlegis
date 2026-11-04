# CLAUDE.md – handover for building the FieldOrtho Windows installer

You are picking up FieldOrtho (shipped as **WangDoodletheGIS** since 0.2.2 – see "Naming" below) on the owner's **Windows** PC. The code was written and tested on
Linux; it has never been built or run on real Windows. Your job: **build, test and install the
Windows installer on this PC**, fixing any Windows-only problems you find, without changing
results (see "Do not change" below).

## What FieldOrtho is
Offline desktop app (Python 3.12+/PySide6) for field mapping teams in the Philippines:
1. Drone photos → orthomosaic, DSM, DTM, contours, by driving **OpenDroneMap (ODM)** installed in `C:\ODM`.
2. Georeferencer for scanned/exported maps with a coordinate grid (MGB geohazard maps), with hazard-zone → polygon extraction.
3. Flood (still water + HAND river rise) and landslide (infinite slope + energy-line runout) scenarios.
4. Exports in GeoTIFF / PNG+world / JPEG+world / KMZ / ASCII / XYZ and GPKG / SHP / GeoJSON / KML / DXF in any CRS.

Layout: `fieldortho/core` (logic, no GUI), `fieldortho/gui` (PySide6), `fieldortho/cli.py`,
`tests/` (pytest + headless GUI walkthroughs), `packaging/` (PyInstaller spec, Inno Setup script,
build script, CI test scripts), `data/us_nga_egm96_15.tif` (EGM96 geoid, already included).

## The job, step by step

1. **Check prerequisites** (install what's missing, ask the owner before anything that needs admin):
   - 64-bit Python 3.12 or 3.14 (`python --version`, or `py -0p` to list installs). Note the full path to `python.exe`.
   - Internet access, ~15 GB free disk.
   - Inno Setup 6 (the build script installs it with winget if missing).
2. **Run the tests on real Windows first** (never done before):
   ```powershell
   cd <project folder>
   & "<path\to\python.exe>" -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt pytest pyinstaller
   .venv\Scripts\python -m pytest -q -W ignore tests
   ```
   Tests needing the 77-photo sample are skipped unless you set `FIELDORTHO_TEST_PHOTOS` to an
   `images` folder of https://github.com/OpenDroneMap/odm_data_aukerman (and `FAKE_ODM_ORTHO` to its
   `aukerman.png`). Cloning it (≈1.1 GB) is worthwhile: `git clone --depth 1 https://github.com/OpenDroneMap/odm_data_aukerman C:\data\aukerman`.
   On Linux all 41 tests pass. Fix Windows-only failures in the code (paths, quoting, file locking,
   encodings), not in the tests' expectations.
3. **Build both installers** (downloads ODM's latest Windows setup, compiles Online + Offline editions):
   ```powershell
   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Python "<path\to\python.exe>"
   ```
   Output in `dist\`: `WangDoodletheGIS_Setup_<version>_Online.exe`, `WangDoodletheGIS_Setup_<version>_Offline.exe`,
   `build-info.json`. `packaging\installer.iss` has never been compiled – fix any Inno Setup
   compile errors you hit (keep both editions and the silent-install behaviour).
4. **Install and verify on this PC.** `packaging\ci\smoke_test.ps1` automates this (silent install
   of the Online edition, engine download, CLI, georeferencer, GUI start). Run it from the project
   folder in an elevated PowerShell, or do the same checks by hand:
   - `"C:\Program Files\WangDoodletheGIS\WangDoodletheGIS-cli.exe" engine` → "OK OpenDroneMap … ready"
   - `"C:\Program Files\WangDoodletheGIS\WangDoodletheGIS-cli.exe" georef tests\data\maps\poblacion_landslide.jpg --lon "124d47'E" --lat "8d52'N" --crs EPSG:3125 --classes mgb_landslide --out C:\temp\georef_test`
     → RMSE ≈ 0.4 m (must be < 1 m), files `*_georef.tif` and `*_classes.gpkg`.
   - Start menu → WangDoodletheGIS opens; Home shows "OpenDroneMap … ready".
5. **Real engine test** (never done before): `packaging\ci\odm_test.ps1 -Photos 30 -Preset standard`
   (needs the aukerman photos in `C:\data\aukerman\images`). Takes 20-60 min. It must produce
   orthomosaic, DSM, DTM and contours in a job folder whose path contains spaces.
6. **Report to the owner**: where the installers are, test results, anything you changed and why.
   Commit fixes with clear messages if the folder is a git repository.

## Known facts / gotchas
- PyInstaller output is ~700-900 MB; Windows Defender real-time scanning can slow the build a lot.
- Windows PowerShell 5.1: native stderr + `$ErrorActionPreference="Stop"` = false errors. The build
  script already relaxes this around pytest; use the same pattern if you add native calls with `2>&1`.
- The installers are unsigned → SmartScreen warning ("More info → Run anyway"). Code signing needs a certificate the owner does not have.
- ODM is launched as `cmd.exe /s /c ""C:\ODM\run.bat" "<job dir>" args"` (engine.py) so job folders
  with spaces work. Keep that form.
- ODM 3.6.2 itself runs its SMRF ground classification with an *unquoted* path (opendm/dem/pdal.py),
  so a job folder with spaces silently gets no ground points and the DTM stage fails ("bad allocation").
  `engine.space_free_alias()` therefore hands ODM a space-free directory junction under
  `C:\ProgramData\WangDoodletheGIS\odm\` pointing at the job folder (removed after the run).
- ODM's win32env.bat restores PATH from `_OLD_VIRTUAL_PATH` (left by an activated Python venv), which
  breaks ODM's DLL loading; `engine.engine_env()` strips such variables for the ODM child process.
- Frozen app data: pip wheels of rasterio/pyogrio/pyproj each carry their own GDAL/PROJ data; do NOT
  set PROJ_DATA globally for a pip build (`app._set_gis_data()` only acts if `share\gdal` exists,
  which is the conda-forge build case).
- The offline installer bundles ODM's setup (~1-2 GB). That is expected.
- `.github/workflows/build-windows.yml` does all of the above on GitHub Actions if the owner later
  pushes this folder to a GitHub repository; it reports to a `ci-status` branch.

## Do not change (accuracy-critical, verified)
- `core/crs.py` Luzon 1911 handling: Mindanao uses towgs84 (-133,-79,-72), elsewhere (-133,-77,-51);
  Luzon outputs carry explicit TOWGS84 without EPSG authority (otherwise GDAL drops it from GeoTIFF).
- `core/georef.py` detection/fit (verified RMSE 0.23-0.39 m on six MGB maps; seam 0.09 m).
- `core/mapclasses.py` MGB colour presets (measured map fill colours, not legend swatches).
- `core/flood.py` MGB depth classes: low < 0.5 m, moderate 0.5-1 m inclusive, high > 1 m.
- `core/landslide.py` neighbour-angle convention (rows = +y) and the D-infinity fall-line limit.
If a Windows fix seems to need changing these, stop and explain to the owner first.

## Naming (0.2.2)
- User-facing name is **WangDoodletheGIS**, developed by **Migz** (`APP_NAME`, `AUTHOR`, `DISCLAIMER`
  in `fieldortho/__init__.py`). The Python package, repo folder and spec file keep the internal
  name `fieldortho`; do not rename them.
- The disclaimer appears on the installer's accept page (`LICENSE-NOTICE.txt`), in the sidebar
  footer / About box, and in every export README. Do not mention DILG in the app (owner's choice).
- The installer bundles ODM's OpenPointClass model (`packaging\redist\openpointclass\model.bin`) into
  `%ProgramData%\ODM\storage\models\openpointclass\v1_0_0`, which ODM otherwise downloads mid-run.

## Owner
Rictu (DILG Region 10, Philippines). Non-specialist in packaging: explain results plainly, give
the path of the finished installer, and say which file to give to field laptops without internet
(the `_Offline.exe`).
