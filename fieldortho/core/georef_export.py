"""Exporting a georeferenced map: image, control points, extracted classes."""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import crs as crsmod
from . import exporters as ex
from . import georef as G
from . import mapclasses as M


@dataclass
class GeorefExport:
    out_dir: str
    name: str
    crs: str = "EPSG:4326"
    order: int = 1
    crop: bool = True
    resampling: str = "cubic"
    pixel_size: float | None = None
    raster_formats: list = field(default_factory=lambda: ["gtiff"])
    write_points: bool = True
    gcp_vector_formats: list = field(default_factory=list)
    classes_preset: str | None = None
    class_vector_formats: list = field(default_factory=lambda: ["gpkg"])
    class_raster_formats: list = field(default_factory=list)   # gtiff / png / kmz of class map


def export(rgb: np.ndarray, det: G.Detection | None, gcps: list[G.GCP], grid_crs: str, req: GeorefExport,
           progress: Callable[[float, str], None] | None = None) -> tuple[list[str], dict]:
    progress = progress or (lambda p, t: None)
    os.makedirs(req.out_dir, exist_ok=True)
    base = os.path.join(req.out_dir, req.name.replace(" ", "_"))
    progress(2, "Fitting control points")
    fit = G.fit(gcps, grid_crs, req.order, dst_crs=req.crs)
    lonlat = fit.lonlat
    crop = det.frame_corners_original() if (req.crop and det is not None) else None
    files: list[str] = []
    notes: list[str] = []
    info = {"rmse_m": fit.rmse_m, "max_m": fit.max_m, "n": fit.n, "pixel_m": fit.pixel_m,
            "datum": crsmod.datum_note(grid_crs, req.crs, *lonlat)}
    tmpd = tempfile.mkdtemp(prefix="fo_geo_")
    try:
        if req.raster_formats:
            progress(5, "Resampling the map image")
            warped = os.path.join(tmpd, "warped.tif")
            w = G.warp_image(rgb, fit, req.crs, warped, crop=crop, resampling=req.resampling,
                             pixel_size=req.pixel_size, progress=lambda p: progress(5 + 0.45 * p, "Resampling the map image"))
            info["output_pixel"] = w["pixel_size"]
            for fmt in req.raster_formats:
                progress(52, f"Writing {ex.RASTER_FORMATS[fmt][0]}")
                files += ex.write_raster(warped, base + "_georef" + ex.RASTER_FORMATS[fmt][1], fmt)
        if req.write_points:
            p = base + "_gcps.points"
            G.write_qgis_points(gcps, p, grid_crs)
            files.append(p)
        if req.gcp_vector_formats:
            from shapely.geometry import Point
            feats = []
            X, Y = crsmod.transform([g.gx for g in gcps], [g.gy for g in gcps], grid_crs, fit.work_crs, *lonlat)
            for g, x, y in zip(gcps, X, Y):
                feats.append(ex.Feature(Point(x, y), {
                    "name": g.name, "grid_x": g.gx, "grid_y": g.gy, "pixel_x": round(g.px, 3),
                    "pixel_y": round(g.py, 3), "used": int(g.enabled), "source": g.source,
                    "resid_m": round(g.res_m, 3) if g.res_m is not None else None}))
            files += ex.export_vector(feats, base + "_gcps", req.gcp_vector_formats, fit.work_crs, req.crs,
                                      layer="control_points", lonlat=lonlat)
        if req.classes_preset:
            preset = M.PRESETS[req.classes_preset]
            progress(60, "Reading map classes")
            region = M.region_mask(rgb.shape, crop) if crop else None
            codes = M.classify(rgb, preset, region)
            progress(75, "Tracing class polygons")
            feats = M.class_polygons(codes, preset, fit)
            info["class_areas_ha"] = M.area_summary(feats)
            if req.class_vector_formats:
                files += ex.export_vector(feats, base + "_classes", req.class_vector_formats, fit.work_crs,
                                          req.crs, layer="hazard_classes", lonlat=lonlat, dxf_layer_field="class")
            if req.class_raster_formats:
                progress(85, "Writing class raster")
                ctif = os.path.join(tmpd, "classes.tif")
                G.warp_image(codes, fit, req.crs, ctif, crop=crop, resampling="nearest", pixel_size=req.pixel_size)
                _add_colormap(ctif, preset)
                for fmt in req.class_raster_formats:
                    if fmt == "gtiff":
                        files += ex.write_raster(ctif, base + "_classes.tif", "gtiff")
                    else:   # coloured image for PNG/JPEG/KMZ
                        rgba = os.path.join(tmpd, "classes_rgba.tif")
                        _class_rgba(ctif, rgba, preset)
                        files += ex.write_raster(rgba, base + "_classes" + ex.RASTER_FORMATS[fmt][1], fmt)
        for fmts in (req.raster_formats, req.gcp_vector_formats, req.class_vector_formats, req.class_raster_formats):
            for f in fmts:
                n = ex.format_note(f, req.crs)
                if n and n not in notes:
                    notes.append(n)
        notes = [f"Control points: {fit.n} used, RMSE {fit.rmse_m:.2f} m (max {fit.max_m:.2f} m); "
                 f"source pixel = {fit.pixel_m:.2f} m on the ground.",
                 f"Grid coordinates read as {crsmod.label(grid_crs)}. {info['datum']}"] + notes
        readme = base + "_README.txt"
        ex.write_export_readme(readme, f"FieldOrtho georeferenced map - {req.name}", req.crs, lonlat, files, notes)
        files.append(readme)
        progress(100, "Done")
        return list(dict.fromkeys(files)), info
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def _add_colormap(path: str, preset: M.Preset):
    import rasterio
    cmap = {0: (0, 0, 0, 0)}
    for mc in preset.classes:
        cmap[mc.code] = tuple(mc.display) + ((0,) if mc.code == M.NONE_CODE else (255,))
    with rasterio.open(path, "r+") as ds:
        ds.write_colormap(1, cmap)


def _class_rgba(src: str, dst: str, preset: M.Preset):
    import rasterio
    with rasterio.open(src) as ds:
        codes = ds.read(1)
        prof = ds.profile.copy()
    rgba = M.colorize_codes(codes, preset)
    prof.update(count=4, dtype="uint8", nodata=None, photometric="RGB")
    with rasterio.open(dst, "w", **prof) as out:
        out.write(np.moveaxis(rgba, -1, 0))
