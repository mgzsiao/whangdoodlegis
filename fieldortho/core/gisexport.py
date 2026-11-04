"""Turn OpenDroneMap outputs into a clean, GIS-ready delivery folder.

GIS/
    <job>_orthomosaic.tif     Cloud-Optimized GeoTIFF (RGB + alpha)
    <job>_DSM.tif             surface model, float32 metres, nodata -9999
    <job>_DTM.tif             bare-earth terrain model
    <job>_contours_1m.shp     contour lines from the DTM (or DSM), field ELEV
    <job>_pointcloud.laz      classified point cloud (CRS of ODM output)
    <job>_quality_report.pdf  OpenDroneMap quality report
    <job>_README.txt          coordinate system, heights, resolution, accuracy
    previews/*.png            small images shown inside the app
"""
from __future__ import annotations

import csv
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import rasterio
from rasterio.enums import Resampling, WktVersion
from rasterio.warp import calculate_default_transform, reproject
from rasterio.shutil import copy as rio_copy
from PIL import Image

from .crs import AUTO, label as crs_label

NODATA = -9999.0
PREVIEW_MAX = 1600


@dataclass
class OdmOutputs:
    ortho: str | None = None
    dsm: str | None = None
    dtm: str | None = None
    laz: str | None = None
    report: str | None = None
    stats: str | None = None

    @classmethod
    def find(cls, project_dir: str) -> "OdmOutputs":
        def f(*p):
            path = os.path.join(project_dir, *p)
            return path if os.path.isfile(path) else None
        return cls(
            ortho=f("odm_orthophoto", "odm_orthophoto.tif"),
            dsm=f("odm_dem", "dsm.tif"),
            dtm=f("odm_dem", "dtm.tif"),
            laz=f("odm_georeferencing", "odm_georeferenced_model.laz"),
            report=f("odm_report", "report.pdf"),
            stats=f("odm_report", "stats.json"),
        )


@dataclass
class ExportOptions:
    target_crs: str = AUTO
    heights: str = "as_recorded"   # as_recorded | egm96 | offset
    height_offset_m: float = 0.0
    contours: bool = True
    contour_interval: float = 0.0  # 0 = auto
    pointcloud: bool = True
    checkpoints_csv: str = ""
    checkpoints_crs: str = ""
    geoid_grid: str = ""           # path to EGM96 grid GeoTIFF (us_nga_egm96_15.tif)


@dataclass
class ExportResult:
    files: dict = field(default_factory=dict)      # kind -> path
    previews: dict = field(default_factory=dict)   # kind -> png path
    summary: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


# ----------------------------------------------------------------------------
# Raster helpers
# ----------------------------------------------------------------------------

def _write_cog(src_path: str, dst_path: str, *, is_dem: bool):
    # Lossless DEFLATE for everything: keeps the transparency band exact and is
    # readable by every GIS (QGIS, ArcGIS, Global Mapper, AutoCAD Map).
    opts = dict(driver="COG", compress="DEFLATE", BIGTIFF="IF_SAFER", OVERVIEWS="AUTO",
                NUM_THREADS="ALL_CPUS",
                PREDICTOR="FLOATING_POINT" if is_dem else "YES",
                RESAMPLING="BILINEAR" if is_dem else "CUBIC")
    rio_copy(src_path, dst_path, **opts)


def _reproject_to(src_path: str, dst_path: str, dst_crs: str, *, is_dem: bool):
    with rasterio.open(src_path) as src:
        transform, w, h = calculate_default_transform(src.crs, dst_crs, src.width, src.height, *src.bounds)
        prof = src.profile.copy()
        prof.update(driver="GTiff", crs=dst_crs, transform=transform, width=w, height=h,
                    tiled=True, blockxsize=512, blockysize=512, compress="DEFLATE", BIGTIFF="IF_SAFER")
        if is_dem:
            prof.update(dtype="float32", nodata=NODATA)
        with rasterio.open(dst_path, "w", **prof) as dst:
            for b in range(1, src.count + 1):
                reproject(rasterio.band(src, b), rasterio.band(dst, b),
                          src_transform=src.transform, src_crs=src.crs,
                          dst_transform=transform, dst_crs=dst_crs,
                          src_nodata=src.nodata, dst_nodata=NODATA if is_dem else src.nodata,
                          resampling=Resampling.bilinear, num_threads=os.cpu_count() or 2)
            if not is_dem:
                dst.colorinterp = src.colorinterp


def geoid_undulation(grid_path: str, lon: float, lat: float) -> float | None:
    """EGM96 geoid height N (m) at a point, from a PROJ geoid grid GeoTIFF."""
    if not grid_path or not os.path.isfile(grid_path):
        return None
    with rasterio.open(grid_path) as g:
        lon_ = lon
        if g.bounds.left >= 0 and lon_ < 0:
            lon_ += 360
        row, col = g.index(lon_, lat)
        row = min(max(row, 0), g.height - 1)
        col = min(max(col, 0), g.width - 1)
        # bilinear on a 2x2 window
        win = rasterio.windows.Window(max(col - 1, 0), max(row - 1, 0), 3, 3)
        arr = g.read(1, window=win).astype(float)
        return float(np.nanmean(arr)) if arr.size else None


def _dem_fix(path: str, shift: float) -> None:
    """Make nodata consistent (-9999) and optionally shift heights, in place."""
    with rasterio.open(path, "r+") as ds:
        nd = ds.nodata
        for _ji, win in ds.block_windows(1):
            a = ds.read(1, window=win).astype("float32")
            mask = ~np.isfinite(a)
            if nd is not None:
                mask |= a == nd
            if shift:
                a = a + np.float32(shift)
            a[mask] = NODATA
            ds.write(a, 1, window=win)
        ds.nodata = NODATA


def _read_preview(path: str, max_size: int = PREVIEW_MAX, band_count: int | None = None):
    with rasterio.open(path) as ds:
        scale = max(ds.width, ds.height) / max_size
        scale = max(scale, 1.0)
        w, h = max(1, int(ds.width / scale)), max(1, int(ds.height / scale))
        bands = list(range(1, (band_count or ds.count) + 1))
        arr = ds.read(bands, out_shape=(len(bands), h, w), resampling=Resampling.average
                      if ds.dtypes[0].startswith("float") else Resampling.bilinear)
        return arr, ds.transform @ ds.transform.scale(ds.width / w, ds.height / h), ds.nodata, ds.crs


def _ortho_preview(path: str, out_png: str) -> dict:
    with rasterio.open(path) as ds:
        count = ds.count
    arr, transform, _nd, crs = _read_preview(path, band_count=min(count, 4))
    rgb = np.moveaxis(arr[:3], 0, -1)
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    if arr.shape[0] >= 4:
        alpha = arr[3].astype(np.uint8)
    else:
        alpha = np.where(rgb.sum(axis=2) > 0, 255, 0).astype(np.uint8)
    Image.fromarray(np.dstack([rgb, alpha]), "RGBA").save(out_png, optimize=True)
    return {"transform": list(transform)[:6], "crs": str(crs)}


# Terrain colour ramp: low (blue-green) -> green -> yellow -> brown -> white
_RAMP = np.array([
    [0.00, 30, 110, 150], [0.15, 60, 160, 110], [0.35, 140, 200, 90],
    [0.55, 235, 220, 110], [0.75, 190, 130, 70], [0.90, 140, 100, 80], [1.00, 250, 250, 250]])


def _colorize(norm: np.ndarray) -> np.ndarray:
    out = np.zeros(norm.shape + (3,), dtype=np.float32)
    for c in range(3):
        out[..., c] = np.interp(norm, _RAMP[:, 0], _RAMP[:, c + 1])
    return out


def hillshade(z: np.ndarray, cellsize: float, azimuth=315.0, altitude=45.0, zfactor=1.0) -> np.ndarray:
    zz = np.where(np.isfinite(z), z, np.nanmean(z) if np.isfinite(z).any() else 0)
    dy, dx = np.gradient(zz, cellsize)
    slope = np.arctan(zfactor * np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    az, alt = math.radians(360 - azimuth + 90), math.radians(altitude)
    hs = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(hs, 0, 1)


def _dem_preview(path: str, out_png: str) -> dict:
    arr, transform, nd, crs = _read_preview(path, band_count=1)
    z = arr[0].astype("float64")
    valid = np.isfinite(z)
    if nd is not None:
        valid &= z != nd
    z[~valid] = np.nan
    if not valid.any():
        Image.new("RGBA", (z.shape[1], z.shape[0])).save(out_png)
        return {"min": None, "max": None}
    lo, hi = np.nanpercentile(z, [1, 99])
    hi = hi if hi > lo else lo + 1
    norm = np.clip((z - lo) / (hi - lo), 0, 1)
    color = _colorize(np.nan_to_num(norm))
    cell = abs(transform.a) if not crs or not crs.is_geographic else abs(transform.a) * 111_000
    hs = hillshade(z, cell)
    shaded = color * (0.45 + 0.55 * hs[..., None])
    rgba = np.dstack([np.clip(shaded, 0, 255).astype(np.uint8), np.where(valid, 255, 0).astype(np.uint8)])
    Image.fromarray(rgba, "RGBA").save(out_png, optimize=True)
    return {"min": float(np.nanmin(z)), "max": float(np.nanmax(z)), "p1": float(lo), "p99": float(hi),
            "transform": list(transform)[:6]}


# ----------------------------------------------------------------------------
# Contours
# ----------------------------------------------------------------------------

def auto_interval(relief_m: float) -> float:
    for step in (0.25, 0.5, 1, 2, 5, 10, 20, 50):
        if relief_m / step <= 40:
            return float(step)
    return 100.0


def write_contours(dem_path: str, shp_path: str, interval: float = 0.0,
                   target_cell_m: float = 0.5) -> tuple[float, int]:
    """Contours as a line shapefile (field ELEV). Returns (interval, line count)."""
    import contourpy
    import shapefile  # pyshp

    with rasterio.open(dem_path) as ds:
        cell = abs(ds.transform.a)
        factor = max(1, int(round(target_cell_m / cell))) if cell < target_cell_m else 1
        w, h = max(2, ds.width // factor), max(2, ds.height // factor)
        z = ds.read(1, out_shape=(h, w), resampling=Resampling.average).astype("float64")
        tr = ds.transform @ ds.transform.scale(ds.width / w, ds.height / h)
        nd = ds.nodata
        crs_wkt = ds.crs.to_wkt(version=WktVersion.WKT1_ESRI) if ds.crs else None
    mask = ~np.isfinite(z)
    if nd is not None:
        mask |= z == nd
    if mask.all():
        return interval, 0
    # light smoothing so contours are not jagged (3x3 mean on valid cells)
    zf = np.where(mask, 0, z)
    wgt = (~mask).astype(float)
    k = lambda a: sum(np.roll(np.roll(a, i, 0), j, 1) for i in (-1, 0, 1) for j in (-1, 0, 1))
    with np.errstate(invalid="ignore", divide="ignore"):
        zs = k(zf) / k(wgt)
    zs = np.ma.array(zs, mask=mask)
    lo, hi = float(zs.min()), float(zs.max())
    if interval <= 0:
        interval = auto_interval(hi - lo)
    levels = np.arange(math.ceil(lo / interval) * interval, hi + 1e-9, interval)
    gen = contourpy.contour_generator(z=zs, line_type=contourpy.LineType.Separate)
    n = 0
    with shapefile.Writer(shp_path, shapeType=shapefile.POLYLINE) as w:
        w.field("ELEV", "N", decimal=2, size=12)
        w.field("MAJOR", "N", size=1)
        for lev in levels:
            for line in gen.lines(lev):
                if len(line) < 3:
                    continue
                cols, rows = line[:, 0] + 0.5, line[:, 1] + 0.5
                xs = tr.c + cols * tr.a + rows * tr.b
                ys = tr.f + cols * tr.d + rows * tr.e
                w.line([list(zip(xs.tolist(), ys.tolist()))])
                major = int(round(lev / interval)) % 5 == 0
                w.record(round(float(lev), 2), 1 if major else 0)
                n += 1
    if crs_wkt:
        with open(os.path.splitext(shp_path)[0] + ".prj", "w") as f:
            f.write(crs_wkt)
        with open(os.path.splitext(shp_path)[0] + ".cpg", "w") as f:
            f.write("UTF-8")
    return interval, n


# ----------------------------------------------------------------------------
# Check points (independent accuracy check of heights)
# ----------------------------------------------------------------------------

def read_points_csv(path: str) -> list[dict]:
    """CSV with columns name,x,y,z (header names flexible: e/easting/lon etc.)."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(2048)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t ") if sample else csv.excel
        has_header = csv.Sniffer().has_header(sample) if sample else False
        reader = csv.reader(f, dialect)
        header = [h.strip().lower() for h in next(reader)] if has_header else ["name", "x", "y", "z"]

        def idx(*names, default):
            for n in names:
                if n in header:
                    return header.index(n)
            return default
        i_n = idx("name", "id", "label", "point", default=0)
        i_x = idx("x", "e", "easting", "east", "lon", "longitude", default=1)
        i_y = idx("y", "n", "northing", "north", "lat", "latitude", default=2)
        i_z = idx("z", "h", "elev", "elevation", "height", "alt", default=3)
        for r in reader:
            if len(r) < 4:
                continue
            try:
                rows.append({"name": r[i_n].strip(), "x": float(r[i_x]), "y": float(r[i_y]),
                             "z": float(r[i_z])})
            except ValueError:
                continue
    return rows


def check_heights(dem_path: str, points: list[dict], points_crs: str) -> dict:
    from .crs import transform as tf
    with rasterio.open(dem_path) as ds:
        xs, ys = tf([p["x"] for p in points], [p["y"] for p in points], points_crs, ds.crs.to_string())
        vals = [v[0] for v in ds.sample(list(zip(xs, ys)))]
        nd = ds.nodata
    out = []
    for p, v in zip(points, vals):
        if v is None or not np.isfinite(v) or (nd is not None and v == nd):
            out.append({**p, "dem": None, "dz": None})
        else:
            out.append({**p, "dem": float(v), "dz": float(v) - p["z"]})
    dz = [o["dz"] for o in out if o["dz"] is not None]
    res = {"points": out, "n": len(dz)}
    if dz:
        res["rmse_z"] = float(np.sqrt(np.mean(np.square(dz))))
        res["mean_dz"] = float(np.mean(dz))
        res["max_abs_dz"] = float(np.max(np.abs(dz)))
    return res


# ----------------------------------------------------------------------------
# Main export
# ----------------------------------------------------------------------------

def _stats_summary(stats_path: str | None) -> dict:
    if not stats_path:
        return {}
    try:
        with open(stats_path, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return {}
    s = {}
    rec = st.get("reconstruction_statistics", {})
    if rec:
        s["photos_used"] = rec.get("reconstructed_shots_count")
        s["photos_input"] = rec.get("initial_shots_count")
        s["points"] = rec.get("reconstructed_points_count")
    proc = st.get("processing_statistics", {})
    if proc.get("area"):
        s["area_ha"] = proc["area"] / 10_000.0
    odm = st.get("odm_processing_statistics", {})
    if odm.get("average_gsd"):
        s["gsd_cm"] = odm["average_gsd"]
    if odm.get("total_time"):
        s["engine_seconds"] = odm["total_time"]
    for key, name in (("gps_errors", "gps"), ("gcp_errors", "gcp")):
        e = st.get(key) or {}
        if e.get("average_error") is not None:
            s[f"{name}_error_m"] = e["average_error"]
        err = e.get("error") or {}
        if err:
            s[f"{name}_rmse_xyz"] = [err.get("x"), err.get("y"), err.get("z")]
    return s


def export(project_dir: str, gis_dir: str, job_name: str, opts: ExportOptions,
           progress: Callable[[float, str], None] | None = None) -> ExportResult:
    progress = progress or (lambda p, s: None)
    outs = OdmOutputs.find(project_dir)
    res = ExportResult()
    os.makedirs(gis_dir, exist_ok=True)
    prev_dir = os.path.join(gis_dir, "previews")
    os.makedirs(prev_dir, exist_ok=True)
    prefix = os.path.join(gis_dir, job_name.replace(" ", "_"))
    target = None if opts.target_crs in ("", AUTO) else opts.target_crs
    tmp = tempfile.mkdtemp(prefix="fieldortho_")
    native_crs = None
    try:
        # --- vertical shift ---------------------------------------------------
        shift = 0.0
        vertical_note = "Heights as recorded by the drone/GCPs (no conversion)."
        if opts.heights == "offset" and opts.height_offset_m:
            shift = float(opts.height_offset_m)
            vertical_note = f"Heights shifted by {shift:+.3f} m (user-defined offset)."
        elif opts.heights == "egm96":
            ref = outs.dsm or outs.dtm
            if ref:
                with rasterio.open(ref) as ds:
                    from .crs import transform as tf
                    cx, cy = (ds.bounds.left + ds.bounds.right) / 2, (ds.bounds.bottom + ds.bounds.top) / 2
                    lon, lat = tf([cx], [cy], ds.crs.to_string(), "EPSG:4326")
                n = geoid_undulation(opts.geoid_grid, lon[0], lat[0])
                if n is None:
                    res.warnings.append("Sea-level conversion skipped: geoid file (EGM96) not installed.")
                else:
                    shift = -n
                    vertical_note = (f"Ellipsoidal heights converted to EGM96 sea level "
                                     f"(geoid height N = {n:.3f} m at site centre).")

        steps = [k for k in ("ortho", "dsm", "dtm") if getattr(outs, k)]
        total = len(steps) + 3
        done = 0

        def tick(text):
            nonlocal done
            done += 1
            progress(100.0 * done / total, text)

        # --- rasters -----------------------------------------------------------
        for kind in steps:
            src = getattr(outs, kind)
            is_dem = kind != "ortho"
            name = {"ortho": "orthomosaic", "dsm": "DSM", "dtm": "DTM"}[kind]
            progress(100.0 * done / total, f"Exporting {name}")
            with rasterio.open(src) as ds:
                native_crs = native_crs or (ds.crs.to_string() if ds.crs else None)
            work = src
            if target and native_crs and target != native_crs:
                from .exporters import reproject_raster
                work = os.path.join(tmp, f"{kind}_reproj.tif")
                reproject_raster(src, work, target, is_dem=is_dem)   # datum-aware (core/crs.py)
            if is_dem:
                fixed = os.path.join(tmp, f"{kind}_fixed.tif")
                with rasterio.open(work) as ds:
                    prof = ds.profile.copy()
                    prof.update(driver="GTiff", dtype="float32", tiled=True, blockxsize=512,
                                blockysize=512, compress="DEFLATE", BIGTIFF="IF_SAFER")
                    prof["nodata"] = ds.nodata
                    with rasterio.open(fixed, "w", **prof) as out:
                        for _ji, win in ds.block_windows(1):
                            out.write(ds.read(1, window=win).astype("float32"), 1, window=win)
                _dem_fix(fixed, shift)
                work = fixed
            dst = f"{prefix}_{name}.tif"
            if os.path.exists(dst):
                os.remove(dst)
            _write_cog(work, dst, is_dem=is_dem)
            res.files[kind] = dst
            png = os.path.join(prev_dir, f"{kind}.png")
            info = _ortho_preview(dst, png) if not is_dem else _dem_preview(dst, png)
            res.previews[kind] = png
            res.summary[f"{kind}_preview"] = info
            with rasterio.open(dst) as ds:
                res.summary[f"{kind}_resolution_cm"] = round(abs(ds.transform.a) * 100, 2) \
                    if not ds.crs.is_geographic else None
                res.summary[f"{kind}_size_px"] = [ds.width, ds.height]
            tick(f"{name} ready")

        # --- contours ---------------------------------------------------------
        dem_for_contours = res.files.get("dtm") or res.files.get("dsm")
        if opts.contours and dem_for_contours:
            progress(100.0 * done / total, "Drawing contour lines")
            tmp_shp = os.path.join(tmp, "contours.shp")
            with rasterio.open(dem_for_contours) as ds:
                geographic = ds.crs.is_geographic
            if geographic:
                res.warnings.append("Contours not created: choose a projected coordinate system (metres).")
            else:
                interval, n = write_contours(dem_for_contours, tmp_shp, opts.contour_interval)
                label = f"{interval:g}".replace(".", "_")
                base = f"{prefix}_contours_{label}m"
                for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                    s = os.path.splitext(tmp_shp)[0] + ext
                    if os.path.exists(s):
                        shutil.move(s, base + ext)
                res.files["contours"] = base + ".shp"
                res.summary["contour_interval_m"] = interval
                res.summary["contour_lines"] = n
                res.summary["contours_from"] = "DTM" if res.files.get("dtm") else "DSM"
        tick("Contours ready")

        # --- point cloud, report -------------------------------------------------
        if opts.pointcloud and outs.laz:
            dst = f"{prefix}_pointcloud.laz"
            shutil.copy2(outs.laz, dst)
            res.files["laz"] = dst
        if outs.report:
            dst = f"{prefix}_quality_report.pdf"
            shutil.copy2(outs.report, dst)
            res.files["report"] = dst
        tick("Point cloud and report copied")

        # --- accuracy -----------------------------------------------------------
        res.summary.update(_stats_summary(outs.stats))
        if opts.checkpoints_csv and (res.files.get("dtm") or res.files.get("dsm")):
            try:
                pts = read_points_csv(opts.checkpoints_csv)
                pts_crs = opts.checkpoints_crs or target or native_crs
                chk = check_heights(res.files.get("dtm") or res.files["dsm"], pts, pts_crs)
                res.summary["checkpoints"] = chk
            except Exception as e:
                res.warnings.append(f"Check points could not be read: {e}")

        out_crs = target or native_crs
        res.summary["crs"] = out_crs
        res.summary["crs_name"] = crs_label(out_crs) if target else _crs_name(out_crs)
        res.summary["vertical"] = vertical_note
        res.summary["height_shift_m"] = shift
        res.summary["warnings"] = res.warnings
        _write_readme(f"{prefix}_README.txt", job_name, res)
        res.files["readme"] = f"{prefix}_README.txt"
        with open(os.path.join(gis_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump({"files": res.files, "previews": res.previews, "summary": res.summary}, f, indent=2)
        tick("Done")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def _crs_name(code: str | None) -> str:
    if not code:
        return "unknown"
    try:
        from pyproj import CRS
        c = CRS.from_user_input(code)
        epsg = c.to_epsg()
        return f"{c.name} (EPSG:{epsg})" if epsg else c.name
    except Exception:
        return code


def _write_readme(path: str, job_name: str, res: ExportResult) -> None:
    s = res.summary
    lines = [
        f"FieldOrtho delivery - {job_name}",
        "=" * 60,
        "",
        f"Coordinate system : {s.get('crs_name')}  [{s.get('crs')}]",
        f"Heights           : {s.get('vertical')}",
        "Units             : metres",
        "",
        "Files",
        "-----",
    ]
    desc = {
        "ortho": "Orthomosaic (Cloud-Optimized GeoTIFF, RGB + transparency)",
        "dsm": "DSM - Digital Surface Model: top of trees/buildings (float32, nodata -9999)",
        "dtm": "DTM - Digital Terrain Model: bare ground (float32, nodata -9999)",
        "contours": f"Contour lines every {s.get('contour_interval_m')} m from the "
                    f"{s.get('contours_from')} (field ELEV; MAJOR=1 every 5th line)",
        "laz": "Classified point cloud (LAZ). Note: kept in the engine's native UTM system.",
        "report": "OpenDroneMap quality report (PDF)",
    }
    for k, d in desc.items():
        if k in res.files:
            lines.append(f"{os.path.basename(res.files[k]):<45} {d}")
    lines += ["", "Quality", "-------"]
    if s.get("ortho_resolution_cm"):
        lines.append(f"Orthomosaic pixel size : {s['ortho_resolution_cm']} cm")
    if s.get("dsm_resolution_cm"):
        lines.append(f"Elevation pixel size   : {s['dsm_resolution_cm']} cm")
    if s.get("photos_used") is not None:
        lines.append(f"Photos used            : {s['photos_used']} of {s.get('photos_input')}")
    if s.get("gcp_error_m") is not None:
        lines.append(f"GCP average error      : {s['gcp_error_m']:.3f} m")
    elif s.get("gps_error_m") is not None:
        lines.append(f"GPS average error      : {s['gps_error_m']:.3f} m "
                     "(no GCPs: absolute position only as good as the drone GPS)")
    chk = s.get("checkpoints")
    if chk and chk.get("n"):
        lines.append(f"Check points (height)  : {chk['n']} points, RMSE {chk['rmse_z']:.3f} m, "
                     f"mean {chk['mean_dz']:+.3f} m")
    if res.warnings:
        lines += ["", "Notes", "-----"] + [f"- {w}" for w in res.warnings]
    lines += ["", "Open the .tif and .shp files directly in QGIS, ArcGIS Pro or Global Mapper."]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
