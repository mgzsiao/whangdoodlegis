"""Exporting flood and landslide model results in any format / CRS."""
from __future__ import annotations

from .. import APP_NAME

import os
import shutil
import tempfile

import numpy as np
import rasterio

from . import exporters as ex, flood, landslide
from .terrain import Dem, lonlat as dem_lonlat, write_array


def _rgba_tif(dem: Dem, rgba: np.ndarray, path: str) -> str:
    prof = dem.profile()
    prof.update(count=4, dtype="uint8", nodata=None, photometric="RGB")
    with rasterio.open(path, "w", **prof) as ds:
        ds.write(np.moveaxis(rgba, -1, 0))
        ds.colorinterp = [rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                          rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha]
    return path


def export_flood(dem: Dem, res: flood.FloodResult, out_dir: str, name: str, crs: str, sel: dict,
                 source_note: str = "") -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, name.replace(" ", "_"))
    ll = dem_lonlat(dem)
    files, notes = [], []
    tmp = tempfile.mkdtemp(prefix="fo_fl_")
    try:
        if sel.get(("r", "Water depth (m)")):
            p = write_array(dem, res.depth, os.path.join(tmp, "depth.tif"))
            files += ex.export_raster(p, base + "_depth_m", sel[("r", "Water depth (m)")], crs, kind="dem", lonlat=ll)
        if sel.get(("r", "Flood map image")):
            p = _rgba_tif(dem, flood.overlay_rgba(res.depth, 255), os.path.join(tmp, "img.tif"))
            files += ex.export_raster(p, base + "_flood_map", sel[("r", "Flood map image")], crs, kind="classes",
                                      lonlat=ll)
        if sel.get(("v", "Flood zones (MGB depth classes)")):
            feats = flood.features(dem, res)
            files += ex.export_vector(feats, base + "_flood_zones", sel[("v", "Flood zones (MGB depth classes)")],
                                      dem.crs_wkt, crs, layer="flood_zones", lonlat=ll, dxf_layer_field="class")
        s = res.stats
        lvl = (f"River risen {res.stats.get('rise_m', 0):.2f} m above its bed along its length (HAND model: "
               f"water surface follows the river slope)." if np.isnan(res.level) else
               f"Water surface level: {res.level:.2f} m (same vertical datum as the elevation model).")
        notes += [lvl,
                  f"Flooded area {s['area_ha']:.2f} ha, volume {s['volume_m3']:,.0f} m3, max depth {s['max_depth_m']:.2f} m.",
                  "Depth classes follow the MGB legend: low < 0.5 m, moderate 0.5-1 m, high > 1 m.",
                  "Static inundation model: no flow dynamics, rainfall or duration.",
                  source_note]
        for k, v in sel.items():
            for f in v:
                n = ex.format_note(f, crs)
                if n and n not in notes:
                    notes.append(n)
        readme = base + "_README.txt"
        ex.write_export_readme(readme, f"{APP_NAME} flood scenario - {name}", crs, ll, files, [n for n in notes if n])
        return files + [readme]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def export_landslide(dem: Dem, res: landslide.LandslideResult, p: landslide.Params, out_dir: str, name: str,
                     crs: str, sel: dict) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, name.replace(" ", "_"))
    ll = dem_lonlat(dem)
    files = []
    tmp = tempfile.mkdtemp(prefix="fo_ls_")
    try:
        if sel.get(("r", "Factor of safety")):
            fs = np.where(np.isfinite(res.fs), np.minimum(res.fs, 10.0), np.nan)
            f = write_array(dem, fs, os.path.join(tmp, "fs.tif"))
            files += ex.export_raster(f, base + "_factor_of_safety", sel[("r", "Factor of safety")], crs, kind="dem",
                                      lonlat=ll)
        if sel.get(("r", "Velocity (m/s)")):
            v = np.where(res.affected, res.velocity, np.nan)
            f = write_array(dem, v, os.path.join(tmp, "vel.tif"))
            files += ex.export_raster(f, base + "_velocity_ms", sel[("r", "Velocity (m/s)")], crs, kind="dem", lonlat=ll)
        if sel.get(("r", "Landslide zones image")):
            f = _rgba_tif(dem, landslide.overlay_rgba(res, 255), os.path.join(tmp, "img.tif"))
            files += ex.export_raster(f, base + "_landslide_map", sel[("r", "Landslide zones image")], crs,
                                      kind="classes", lonlat=ll)
        if sel.get(("v", "Release and runout zones")):
            feats = landslide.features(dem, res, p)
            files += ex.export_vector(feats, base + "_landslide_zones", sel[("v", "Release and runout zones")],
                                      dem.crs_wkt, crs, layer="landslide_zones", lonlat=ll, dxf_layer_field="zone")
        s = res.stats
        notes = [f"Soil depth {p.soil_depth} m, cohesion {p.cohesion} kPa, friction {p.friction} deg, unit weight "
                 f"{p.unit_weight} kN/m3, wetness {p.wetness:.0%}, travel angle {p.travel_angle} deg.",
                 f"Release area {s.get('release_ha', 0):.2f} ha, volume {s.get('volume_m3', 0):,.0f} m3, "
                 f"runout length {s.get('runout_length_m', 0):.0f} m, affected area {s.get('affected_ha', 0):.2f} ha.",
                 "Infinite-slope stability + energy-line (travel angle) runout. Scenario model, not a "
                 "geotechnical design."]
        readme = base + "_README.txt"
        ex.write_export_readme(readme, f"{APP_NAME} landslide scenario - {name}", crs, ll, files, notes)
        return files + [readme]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def background_for(dem: Dem, ortho_path: str | None) -> np.ndarray:
    """RGB background on the DEM grid: the orthomosaic if available, else shaded relief."""
    if ortho_path and os.path.exists(ortho_path):
        from rasterio.warp import reproject, Resampling
        h, w = dem.shape
        out = np.full((4, h, w), 255, np.uint8)
        with rasterio.open(ortho_path) as src:
            n = min(src.count, 4)
            for b in range(1, n + 1):
                reproject(rasterio.band(src, b), out[b - 1], src_transform=src.transform, src_crs=src.crs,
                          dst_transform=dem.transform, dst_crs=rasterio.crs.CRS.from_wkt(dem.crs_wkt),
                          resampling=Resampling.average)
        rgb = np.moveaxis(out[:3], 0, -1)
        if n >= 4:
            rgb = np.where(out[3][..., None] > 0, rgb, 235).astype(np.uint8)
        return np.ascontiguousarray(rgb)
    valid = dem.valid
    rgba = ex.relief_rgba(np.where(valid, dem.z, np.nan), valid, dem.cell)
    rgb = np.where(rgba[..., 3:4] > 0, rgba[..., :3], 235).astype(np.uint8)
    return np.ascontiguousarray(rgb)


def slope_rgb(dem: Dem) -> np.ndarray:
    from .terrain import slope_aspect
    s, _ = slope_aspect(dem)
    ramp = np.array([[0, 255, 255, 204], [10, 161, 218, 180], [20, 254, 224, 139], [30, 253, 174, 97],
                     [40, 244, 109, 67], [55, 165, 0, 38]], float)
    ss = np.nan_to_num(s)
    out = np.zeros(s.shape + (3,), np.float32)
    for c in range(3):
        out[..., c] = np.interp(ss, ramp[:, 0], ramp[:, c + 1])
    out[~np.isfinite(s)] = 235
    return out.astype(np.uint8)
