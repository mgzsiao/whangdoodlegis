"""'Export...' for a processed drone job: any layer, any format, any CRS."""
from __future__ import annotations

from .. import APP_NAME

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import rasterio

from . import exporters as ex
from .gisexport import auto_interval

# layer key -> (label, raster formats allowed, vector formats allowed)
LAYERS = {
    "ortho": ("Orthomosaic", ["gtiff", "png", "jpeg", "kmz"], []),
    "dsm": ("Surface model (DSM)", ["gtiff", "asc", "xyz"], []),
    "dtm": ("Terrain model (DTM)", ["gtiff", "asc", "xyz"], []),
    "relief": ("Shaded relief image of DTM/DSM", ["gtiff", "png", "jpeg", "kmz"], []),
    "contours": ("Contour lines", ["gtiff"], ["gpkg", "shp", "geojson", "kml", "dxf"]),
    "boundary": ("Mapped area outline", [], ["gpkg", "shp", "geojson", "kml", "dxf"]),
}


@dataclass
class ExportRequest:
    out_dir: str
    crs: str = "EPSG:4326"
    raster: dict = field(default_factory=dict)     # layer -> [formats]
    vector: dict = field(default_factory=dict)     # layer -> [formats]
    contour_interval: float = 0.0                  # 0 = auto
    contour_source: str = "dtm"                    # dtm | dsm
    copy_pointcloud: bool = False
    prefix: str = ""


def export_job(job, req: ExportRequest, progress: Callable[[float, str], None] | None = None) -> list[str]:
    progress = progress or (lambda p, t: None)
    files = job.summary.get("files", {})
    os.makedirs(req.out_dir, exist_ok=True)
    prefix = os.path.join(req.out_dir, (req.prefix or job.name).replace(" ", "_"))
    ref = files.get("ortho") or files.get("dtm") or files.get("dsm")
    if not ref:
        raise ValueError("This job has no processed outputs yet.")
    lonlat = ex.site_lonlat(ref)
    written: list[str] = []
    notes: list[str] = []
    steps = sum(len(v) for v in req.raster.values()) + sum(len(v) for v in req.vector.values()) + 1
    done = [0]

    def tick(text):
        done[0] += 1
        progress(100.0 * done[0] / max(steps, 1), text)

    tmpd = tempfile.mkdtemp(prefix="fo_job_")
    try:
        for kind in ("ortho", "dsm", "dtm"):
            fmts = req.raster.get(kind) or []
            if fmts and kind in files:
                progress(100.0 * done[0] / steps, f"Exporting {LAYERS[kind][0]}")
                written += ex.export_raster(files[kind], f"{prefix}_{kind.upper() if kind != 'ortho' else 'orthomosaic'}",
                                            fmts, req.crs, kind="image" if kind == "ortho" else "dem", lonlat=lonlat)
                for _ in fmts:
                    tick(LAYERS[kind][0])
        dem_key = req.contour_source if req.contour_source in files else ("dtm" if "dtm" in files else "dsm")
        dem_path = files.get(dem_key)

        if req.raster.get("relief") and dem_path:
            progress(100.0 * done[0] / steps, "Exporting shaded relief")
            rel = ex.write_relief_geotiff(dem_path, os.path.join(tmpd, "relief.tif"))
            written += ex.export_raster(rel, f"{prefix}_{dem_key.upper()}_relief", req.raster["relief"],
                                        req.crs, kind="image", lonlat=lonlat)
            for _ in req.raster["relief"]:
                tick("Shaded relief")

        want_c_vec = req.vector.get("contours") or []
        want_c_ras = req.raster.get("contours") or []
        if (want_c_vec or want_c_ras) and dem_path:
            progress(100.0 * done[0] / steps, "Drawing contour lines")
            # contours are computed in the target CRS so they align exactly with exported DEMs
            if ex.crsmod.is_geographic(req.crs):
                work_crs = f"EPSG:{ex.crsmod.utm_epsg(lonlat[1], lonlat[0])}"
                notes.append("Contours were traced in UTM (metres) and then converted to the chosen "
                             "lat/long system.")
            else:
                work_crs = req.crs
            dem_w = ex.reproject_raster(dem_path, os.path.join(tmpd, "dem_w.tif"), work_crs,
                                        lonlat=lonlat, is_dem=True)
            with rasterio.open(dem_w) as ds:
                z = ds.read(1).astype("float64")
                valid = np.isfinite(z) & (z != ds.nodata)
                tr, cell = ds.transform, abs(ds.transform.a)
                factor = max(1, int(round(0.5 / cell))) if cell < 0.5 else 1
                if factor > 1:      # trace on ~0.5 m grid: smoother lines, smaller files
                    h, w = ds.height // factor, ds.width // factor
                    z = ds.read(1, out_shape=(h, w), resampling=rasterio.enums.Resampling.average).astype("float64")
                    nd = ds.read_masks(1, out_shape=(h, w)) > 0
                    valid = nd & np.isfinite(z) & (z != ds.nodata)
                    tr = ds.transform * ds.transform.scale(ds.width / w, ds.height / h)
            relief = float(z[valid].max() - z[valid].min()) if valid.any() else 1.0
            interval = req.contour_interval or auto_interval(relief)
            feats3d = ex.contour_features(z, valid, tr, interval, as_3d=True)
            feats2d = [ex.Feature(ex_line_2d(f.geometry), f.properties) for f in feats3d]
            label = f"{interval:g}".replace(".", "_")
            for fmt in want_c_vec:
                fs = feats3d if fmt == "dxf" else feats2d
                written += ex.write_vector(fs, f"{prefix}_contours_{label}m{ex.VECTOR_FORMATS[fmt][1]}", fmt,
                                           work_crs, req.crs, layer=f"contours_{label}m", lonlat=lonlat,
                                           dxf_layer_field="ELEV")
                tick("Contours")
            if want_c_ras:
                rr = ex.rasterize_lines(feats2d, dem_w, os.path.join(tmpd, "cont.tif"))
                written += ex.export_raster(rr, f"{prefix}_contours_{label}m_raster", want_c_ras, req.crs,
                                            kind="classes", lonlat=lonlat)
                tick("Contour raster")
            notes.append(f"Contour interval {interval:g} m from the {dem_key.upper()}.")

        if req.vector.get("boundary") and files.get("ortho"):
            progress(100.0 * done[0] / steps, "Tracing mapped area outline")
            with rasterio.open(files["ortho"]) as ds:
                f = max(1, max(ds.width, ds.height) // 2000)
                h, w = ds.height // f, ds.width // f
                alpha = ds.read(ds.count, out_shape=(h, w)) if ds.count >= 4 else ds.read_masks(1, out_shape=(h, w))
                tr = ds.transform * ds.transform.scale(ds.width / w, ds.height / h)
                src_crs = ds.crs.to_wkt()
                cell = abs(tr.a)
            polys = ex.polygons_from_mask(alpha > 0, tr, simplify=cell * 1.5, min_area=(cell * 20) ** 2)
            feats = [ex.Feature(g, {"name": job.name, "area_ha": round(g.area / 1e4, 3)}) for g, _ in polys]
            for fmt in req.vector["boundary"]:
                written += ex.write_vector(feats, f"{prefix}_outline{ex.VECTOR_FORMATS[fmt][1]}", fmt, src_crs,
                                           req.crs, layer="outline", lonlat=lonlat)
                tick("Outline")

        if req.copy_pointcloud and files.get("laz"):
            dst = f"{prefix}_pointcloud.laz"
            shutil.copy2(files["laz"], dst)
            written.append(dst)
            notes.append("Point cloud copied unchanged (coordinates in the job's original UTM system).")
        for fmts in list(req.vector.values()) + list(req.raster.values()):
            for fmt in fmts:
                n = ex.format_note(fmt, req.crs)
                if n and n not in notes:
                    notes.append(n)
        readme = f"{prefix}_EXPORT_README.txt"
        written = list(dict.fromkeys(written))
        ex.write_export_readme(readme, f"{APP_NAME} export - {job.name}", req.crs, lonlat, written, notes)
        written.append(readme)
        progress(100.0, "Export finished")
        return written
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def ex_line_2d(g):
    import shapely
    return shapely.force_2d(g)
