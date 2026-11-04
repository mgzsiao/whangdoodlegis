"""Exporting rasters and vectors in common GIS formats, in any coordinate system.

Rasters : GeoTIFF (Cloud-Optimized), PNG + world file, JPEG + world file,
          KMZ (Google Earth), ESRI ASCII grid, XYZ text
Vectors : GeoPackage, Shapefile, GeoJSON, KML, DXF (AutoCAD)

Every output carries its coordinate system (GeoTIFF keys, .aux.xml for PNG/JPEG,
.prj for ASCII grid and Shapefile, CRS in GeoPackage/GeoJSON, KML is always
WGS 84 by definition). Luzon 1911 outputs carry explicit +towgs84 parameters
so QGIS applies the right datum shift (see core/crs.py).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass, field

import numpy as np
import rasterio
from rasterio.crs import CRS as RioCRS
from rasterio.enums import Resampling
from rasterio.shutil import copy as rio_copy
from rasterio.warp import calculate_default_transform, reproject

from . import crs as crsmod

NODATA = -9999.0

RASTER_FORMATS = {
    "gtiff": ("GeoTIFF", ".tif"),
    "png": ("PNG + world file", ".png"),
    "jpeg": ("JPEG + world file", ".jpg"),
    "kmz": ("KMZ (Google Earth)", ".kmz"),
    "asc": ("ASCII grid (.asc)", ".asc"),
    "xyz": ("XYZ points (.xyz)", ".xyz"),
}
VECTOR_FORMATS = {
    "gpkg": ("GeoPackage", ".gpkg", "GPKG"),
    "shp": ("Shapefile", ".shp", "ESRI Shapefile"),
    "geojson": ("GeoJSON", ".geojson", "GeoJSON"),
    "kml": ("KML (Google Earth)", ".kml", "KML"),
    "dxf": ("DXF (AutoCAD)", ".dxf", "DXF"),
}
RESAMPLING = {"nearest": Resampling.nearest, "bilinear": Resampling.bilinear,
              "cubic": Resampling.cubic, "average": Resampling.average}


def rio_crs(code, lon=None, lat=None) -> RioCRS:
    return RioCRS.from_wkt(crsmod.resolve(code, lon, lat).to_wkt())


def site_lonlat(path: str) -> tuple[float, float]:
    """Centre of a raster in WGS 84 lon/lat (used to choose datum parameters)."""
    with rasterio.open(path) as ds:
        b = ds.bounds
        cx, cy = (b.left + b.right) / 2, (b.bottom + b.top) / 2
        src = ds.crs.to_wkt()
    from pyproj import CRS, Transformer
    t = Transformer.from_crs(CRS.from_wkt(src), CRS.from_epsg(4326), always_xy=True)
    return t.transform(cx, cy)


# ----------------------------------------------------------------------------
# Rasters
# ----------------------------------------------------------------------------

def reproject_raster(src_path: str, dst_path: str, dst_code, *, resampling: str = "bilinear",
                     lonlat: tuple[float, float] | None = None, is_dem: bool = False) -> str:
    """Reproject to dst CRS (GeoTIFF). If already there, plain copy."""
    lon, lat = lonlat or site_lonlat(src_path)
    dst = rio_crs(dst_code, lon, lat)
    with rasterio.open(src_path) as src:
        same = src.crs is not None and src.crs == dst
        if same or (src.crs and crsmod.resolve(src.crs.to_wkt()).equals(crsmod.resolve(dst_code, lon, lat))):
            shutil.copy2(src_path, dst_path)
            return dst_path
        src_crs = rio_crs(src.crs.to_wkt(), lon, lat)
        transform, w, h = calculate_default_transform(src_crs, dst, src.width, src.height, *src.bounds)
        prof = src.profile.copy()
        nd = src.nodata
        if is_dem:
            prof.update(dtype="float32", nodata=NODATA)
            nd_out = NODATA
        else:
            nd_out = nd
        prof.update(driver="GTiff", crs=dst, transform=transform, width=w, height=h, tiled=True,
                    blockxsize=512, blockysize=512, compress="DEFLATE", BIGTIFF="IF_SAFER")
        prof.pop("photometric", None) if src.count != 3 else None
        with rasterio.open(dst_path, "w", **prof) as out:
            for b in range(1, src.count + 1):
                reproject(rasterio.band(src, b), rasterio.band(out, b), src_transform=src.transform,
                          src_crs=src_crs, dst_transform=transform, dst_crs=dst,
                          src_nodata=nd, dst_nodata=nd_out, resampling=RESAMPLING[resampling],
                          num_threads=os.cpu_count() or 2)
            try:
                out.colorinterp = src.colorinterp
            except Exception:
                pass
    return dst_path


def _rgba_flatten(src_path: str, dst_path: str, background=(255, 255, 255)) -> str:
    """RGBA -> RGB on a white background (for JPEG)."""
    with rasterio.open(src_path) as src:
        prof = src.profile.copy()
        arr = src.read()
    rgb = arr[:3].astype(np.float32)
    if arr.shape[0] >= 4:
        a = arr[3:4].astype(np.float32) / 255.0
        bg = np.array(background, dtype=np.float32)[:, None, None]
        rgb = rgb * a + bg * (1 - a)
    prof.update(count=3, dtype="uint8", nodata=None, photometric="RGB")
    with rasterio.open(dst_path, "w", **prof) as out:
        out.write(np.clip(rgb, 0, 255).astype(np.uint8))
    return dst_path


def write_raster(src_gtiff: str, out_path: str, fmt: str) -> list[str]:
    """Write `src_gtiff` (already in the final CRS) to `fmt`. Returns files written."""
    tmpd = tempfile.mkdtemp(prefix="fo_rast_")
    try:
        with rasterio.open(src_gtiff) as ds:
            count, dtype = ds.count, ds.dtypes[0]
            is_float = dtype.startswith("float")
        if os.path.exists(out_path):
            os.remove(out_path)
        if fmt == "gtiff":
            opts = dict(driver="COG", compress="DEFLATE", BIGTIFF="IF_SAFER", OVERVIEWS="AUTO",
                        PREDICTOR="FLOATING_POINT" if is_float else "YES",
                        RESAMPLING="BILINEAR" if is_float else ("NEAREST" if count == 1 else "CUBIC"))
            rio_copy(src_gtiff, out_path, **opts)
        elif fmt in ("png", "jpeg"):
            if is_float:
                raise ValueError("Elevation values cannot be stored in PNG/JPEG - export a coloured "
                                 "relief (hillshade) instead.")
            src = src_gtiff
            if fmt == "jpeg" and count >= 4:
                src = _rgba_flatten(src_gtiff, os.path.join(tmpd, "rgb.tif"))
            opts = {"WORLDFILE": "YES"}
            if fmt == "jpeg":
                opts["QUALITY"] = "92"
            rio_copy(src, out_path, driver="PNG" if fmt == "png" else "JPEG", **opts)
            wld = os.path.splitext(out_path)[0] + ".wld"
            if os.path.exists(wld):   # standard names so PNG and JPEG don't overwrite each other
                os.replace(wld, os.path.splitext(out_path)[0] + (".pgw" if fmt == "png" else ".jgw"))
        elif fmt == "kmz":
            if is_float:
                raise ValueError("KMZ needs an image - export a coloured relief instead.")
            ll = os.path.join(tmpd, "ll.tif")
            reproject_raster(src_gtiff, ll, "EPSG:4326", resampling="bilinear")
            rio_copy(ll, out_path, driver="KMLSUPEROVERLAY", FORMAT="PNG", FIX_ANTIMERIDIAN="NO")
        elif fmt == "asc":
            if not is_float and count > 1:
                raise ValueError("ASCII grid is for single-band values (elevation, depth).")
            sq = _square_pixels(src_gtiff, os.path.join(tmpd, "sq.tif"))
            rio_copy(sq, out_path, driver="AAIGrid", DECIMAL_PRECISION="3")
        elif fmt == "xyz":
            if count > 1:
                raise ValueError("XYZ is for single-band values (elevation, depth).")
            rio_copy(src_gtiff, out_path, driver="XYZ", DECIMAL_PRECISION="3")
        else:
            raise ValueError(f"Unknown raster format {fmt}")
        base = os.path.splitext(out_path)[0]
        side = {"png": [".pgw"], "jpeg": [".jgw"], "asc": [".prj"], "xyz": [], "gtiff": [], "kmz": []}[fmt]
        written = [out_path] + [base + e for e in side if os.path.exists(base + e)]
        if fmt in ("png", "jpeg", "asc") and os.path.exists(out_path + ".aux.xml"):
            written.append(out_path + ".aux.xml")
        return written
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def _square_pixels(src_path: str, dst_path: str) -> str:
    with rasterio.open(src_path) as ds:
        a, e = abs(ds.transform.a), abs(ds.transform.e)
        if abs(a - e) / max(a, e) < 1e-6 and ds.transform.b == 0 and ds.transform.d == 0:
            return src_path
        res = min(a, e)
        b = ds.bounds
        w, h = int(round((b.right - b.left) / res)), int(round((b.top - b.bottom) / res))
        tr = rasterio.transform.from_origin(b.left, b.top, res, res)
        prof = ds.profile.copy()
        prof.update(width=w, height=h, transform=tr, driver="GTiff")
        with rasterio.open(dst_path, "w", **prof) as out:
            reproject(rasterio.band(ds, 1), rasterio.band(out, 1), src_transform=ds.transform,
                      src_crs=ds.crs, dst_transform=tr, dst_crs=ds.crs, src_nodata=ds.nodata,
                      dst_nodata=ds.nodata, resampling=Resampling.bilinear)
    return dst_path


def export_raster(src_path: str, out_base: str, formats: list[str], dst_code, *, kind: str = "image",
                  resampling: str | None = None, lonlat=None) -> list[str]:
    """Reproject once, then write each requested format. kind: image | dem | classes."""
    if not formats:
        return []
    tmpd = tempfile.mkdtemp(prefix="fo_exp_")
    try:
        rs = resampling or {"image": "bilinear", "dem": "bilinear", "classes": "nearest"}[kind]
        work = reproject_raster(src_path, os.path.join(tmpd, "w.tif"), dst_code, resampling=rs,
                                lonlat=lonlat, is_dem=(kind == "dem"))
        out = []
        for fmt in formats:
            out += write_raster(work, out_base + RASTER_FORMATS[fmt][1], fmt)
        return out
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


# ----------------------------------------------------------------------------
# DEM visualisation rasters
# ----------------------------------------------------------------------------

_RAMP = np.array([[0.00, 30, 110, 150], [0.15, 60, 160, 110], [0.35, 140, 200, 90],
                  [0.55, 235, 220, 110], [0.75, 190, 130, 70], [0.90, 140, 100, 80],
                  [1.00, 250, 250, 250]])


def colorize(norm: np.ndarray, ramp=_RAMP) -> np.ndarray:
    out = np.zeros(norm.shape + (3,), dtype=np.float32)
    for c in range(3):
        out[..., c] = np.interp(norm, ramp[:, 0], ramp[:, c + 1])
    return out


def hillshade(z: np.ndarray, cell_x: float, cell_y: float | None = None, azimuth=315.0, altitude=45.0):
    cell_y = cell_y or cell_x
    zz = np.where(np.isfinite(z), z, np.nanmean(z) if np.isfinite(z).any() else 0)
    dy, dx = np.gradient(zz, cell_y, cell_x)
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    az, alt = math.radians(360 - azimuth + 90), math.radians(altitude)
    hs = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(hs, 0, 1)


def relief_rgba(dem: np.ndarray, valid: np.ndarray, cell: float) -> np.ndarray:
    z = np.where(valid, dem, np.nan).astype("float64")
    if not valid.any():
        return np.zeros(dem.shape + (4,), np.uint8)
    lo, hi = np.nanpercentile(z, [1, 99])
    hi = hi if hi > lo else lo + 1
    col = colorize(np.nan_to_num(np.clip((z - lo) / (hi - lo), 0, 1)))
    hs = hillshade(z, cell)
    rgb = np.clip(col * (0.45 + 0.55 * hs[..., None]), 0, 255).astype(np.uint8)
    return np.dstack([rgb, np.where(valid, 255, 0).astype(np.uint8)])


def write_relief_geotiff(dem_path: str, out_path: str) -> str:
    """Coloured, hill-shaded relief image of a DEM (RGBA GeoTIFF) - viewable anywhere."""
    with rasterio.open(dem_path) as ds:
        z = ds.read(1).astype("float64")
        valid = np.isfinite(z) & (z != ds.nodata if ds.nodata is not None else True)
        cell = abs(ds.transform.a) * (111_000 if ds.crs.is_geographic else 1)
        rgba = relief_rgba(z, valid, cell)
        prof = ds.profile.copy()
    prof.update(driver="GTiff", count=4, dtype="uint8", nodata=None, compress="DEFLATE", photometric="RGB")
    with rasterio.open(out_path, "w", **prof) as out:
        out.write(np.moveaxis(rgba, -1, 0))
        out.colorinterp = [rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                           rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha]
    return out_path


# ----------------------------------------------------------------------------
# Vectors
# ----------------------------------------------------------------------------

@dataclass
class Feature:
    geometry: object                 # shapely geometry
    properties: dict = field(default_factory=dict)


def _transform_geoms(geoms, src_code, dst_code, lonlat):
    import shapely
    lon, lat = lonlat
    s, d = crsmod.resolve(src_code, lon, lat), crsmod.resolve(dst_code, lon, lat)
    if s.equals(d):
        return list(geoms)
    t = crsmod.transformer(s.to_wkt(), d.to_wkt(), lon, lat)

    def f(coords):
        x, y = t.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])
    out = []
    for g in geoms:
        has_z = shapely.has_z(g)
        if has_z:
            zc = shapely.get_coordinates(g, include_z=True)
            xy = f(zc[:, :2])
            out.append(shapely.set_coordinates(g, np.column_stack([xy, zc[:, 2]])))
        else:
            out.append(shapely.transform(g, f))
    return out


def _orient(geom):
    """Exterior rings counter-clockwise (OGC); GDAL fixes for Shapefile."""
    from shapely.geometry.polygon import orient
    from shapely.geometry import Polygon, MultiPolygon
    if isinstance(geom, Polygon):
        return orient(geom, 1.0)
    if isinstance(geom, MultiPolygon):
        return MultiPolygon([orient(p, 1.0) for p in geom.geoms])
    return geom


def write_vector(features: list[Feature], out_path: str, fmt: str, src_code, dst_code, *,
                 layer: str = "layer", lonlat=(0.0, 0.0), dxf_layer_field: str | None = None,
                 append: bool = False) -> list[str]:
    """Write features (geometry in src CRS) to out_path in dst CRS."""
    import shapely
    from pyogrio.raw import write as ogr_write

    if not features:
        return []
    driver = VECTOR_FORMATS[fmt][2]
    if fmt == "kml" or (fmt == "geojson" and crsmod.is_luzon1911(dst_code)):
        dst_code = "EPSG:4326"
    geoms = _transform_geoms([f.geometry for f in features], src_code, dst_code, lonlat)
    geoms = [_orient(g) for g in geoms]
    names = []
    for f in features:
        for k in f.properties:
            if k not in names:
                names.append(k)
    if fmt == "dxf":
        # DXF supports only a few fields; use 'Layer' for classes/elevations
        vals = [str(f.properties.get(dxf_layer_field, layer)) if dxf_layer_field else layer for f in features]
        fields, data = ["Layer"], [np.array(vals, dtype=object)]
    else:
        fields = names
        data = []
        for k in names:
            col = [f.properties.get(k) for f in features]
            if all(isinstance(v, (int, np.integer)) or v is None for v in col) and any(v is not None for v in col):
                data.append(np.array([v if v is not None else 0 for v in col], dtype=np.int64))
            elif all(isinstance(v, (int, float, np.number)) or v is None for v in col):
                data.append(np.array([np.nan if v is None else float(v) for v in col], dtype=np.float64))
            else:
                data.append(np.array(["" if v is None else str(v) for v in col], dtype=object))
        if fmt == "kml" and "Name" not in fields:
            label_key = next((k for k in ("class", "name", "ELEV", "label") if k in names), None)
            if label_key:
                fields = ["Name"] + fields
                data = [np.array([str(f.properties.get(label_key, "")) for f in features], dtype=object)] + data
    gtype = _geom_type(geoms)
    wkb = np.array([shapely.to_wkb(g, output_dimension=3 if shapely.has_z(g) else 2) for g in geoms], dtype=object)
    d_crs = crsmod.resolve(dst_code, *lonlat)
    if os.path.exists(out_path) and not (append and fmt == "gpkg"):
        _remove_vector(out_path, fmt)
    kwargs = {}
    if fmt == "gpkg":
        kwargs["layer"] = layer
        if append and os.path.exists(out_path):
            kwargs["append"] = False
    if fmt == "geojson":
        kwargs["layer_options"] = {"RFC7946": "NO", "WRITE_NAME": "NO"}
    ogr_write(out_path, wkb, data, fields, driver=driver, crs=d_crs.to_wkt(), geometry_type=gtype,
              encoding="UTF-8", **kwargs)
    base = os.path.splitext(out_path)[0]
    return [out_path] + [base + e for e in (".shx", ".dbf", ".prj", ".cpg") if fmt == "shp" and os.path.exists(base + e)]


def _geom_type(geoms) -> str:
    import shapely
    kinds = {g.geom_type for g in geoms}
    z = any(shapely.has_z(g) for g in geoms)
    if kinds <= {"Polygon", "MultiPolygon"}:
        t = "MultiPolygon" if "MultiPolygon" in kinds else "Polygon"
    elif kinds <= {"LineString", "MultiLineString"}:
        t = "MultiLineString" if "MultiLineString" in kinds else "LineString"
    elif kinds <= {"Point"}:
        t = "Point"
    else:
        t = "Unknown"
    return t + (" Z" if z and t != "Unknown" else "")


def _remove_vector(path: str, fmt: str):
    base = os.path.splitext(path)[0]
    exts = (".shp", ".shx", ".dbf", ".prj", ".cpg") if fmt == "shp" else (os.path.splitext(path)[1],)
    for e in exts:
        try:
            os.remove(base + e)
        except OSError:
            pass


def format_note(fmt: str, dst_code) -> str | None:
    """Plain-language caveat for a format/CRS combination (None if fine)."""
    if fmt == "shp" and crsmod.is_luzon1911(dst_code):
        return ("Shapefile .prj files cannot store the Luzon 1911 datum-shift parameters; QGIS may "
                "apply a default shift that is ~20 m off on Mindanao. Prefer the GeoPackage.")
    if fmt == "geojson" and crsmod.is_luzon1911(dst_code):
        return "GeoJSON was written in WGS 84 (GeoJSON cannot carry the Luzon 1911 datum parameters)."
    if fmt in ("kml", "kmz"):
        return "KML/KMZ files are always in WGS 84 (Google Earth standard)."
    return None


def export_vector(features: list[Feature], out_base: str, formats: list[str], src_code, dst_code, *,
                  layer: str, lonlat, dxf_layer_field: str | None = None) -> list[str]:
    out = []
    for fmt in formats:
        out += write_vector(features, out_base + VECTOR_FORMATS[fmt][1], fmt, src_code, dst_code,
                            layer=layer, lonlat=lonlat, dxf_layer_field=dxf_layer_field)
    return out


# ----------------------------------------------------------------------------
# Raster -> vector helpers
# ----------------------------------------------------------------------------

def polygons_from_mask(mask: np.ndarray, transform, *, simplify: float = 0.0, min_area: float = 0.0,
                       values: np.ndarray | None = None) -> list[tuple[object, int]]:
    """Polygons (map coordinates) for each connected region of mask (or each value of `values`)."""
    from rasterio.features import shapes
    from shapely.geometry import shape
    src = (values if values is not None else mask.astype(np.uint8)).astype(np.int32)
    out = []
    for geom, val in shapes(src, mask=mask.astype(bool), transform=transform, connectivity=8):
        g = shape(geom)
        if simplify:
            g = g.simplify(simplify, preserve_topology=True)
        if g.is_empty or g.area < min_area:
            continue
        if not g.is_valid:
            g = g.buffer(0)
        out.append((g, int(val)))
    return out


def contour_features(dem: np.ndarray, valid: np.ndarray, transform, interval: float, *,
                     smooth: bool = True, as_3d: bool = False) -> list[Feature]:
    """Contour lines as features with ELEV (m) and MAJOR (every 5th) fields."""
    import contourpy
    from shapely.geometry import LineString
    z = np.where(valid, dem, 0.0).astype("float64")
    if smooth:
        w = valid.astype(float)
        k = lambda a: sum(np.roll(np.roll(a, i, 0), j, 1) for i in (-1, 0, 1) for j in (-1, 0, 1))
        with np.errstate(invalid="ignore", divide="ignore"):
            z = np.where(valid, k(z) / k(w), 0.0)
    zm = np.ma.array(z, mask=~valid)
    lo, hi = float(zm.min()), float(zm.max())
    levels = np.arange(math.ceil(lo / interval) * interval, hi + 1e-9, interval)
    gen = contourpy.contour_generator(z=zm, line_type=contourpy.LineType.Separate)
    feats = []
    tr = transform
    for lev in levels:
        for line in gen.lines(lev):
            if len(line) < 3:
                continue
            cols, rows = line[:, 0] + 0.5, line[:, 1] + 0.5
            xs = tr.c + cols * tr.a + rows * tr.b
            ys = tr.f + cols * tr.d + rows * tr.e
            coords = np.column_stack([xs, ys, np.full(len(xs), lev)]) if as_3d else np.column_stack([xs, ys])
            major = int(round(lev / interval)) % 5 == 0
            feats.append(Feature(LineString(coords), {"ELEV": round(float(lev), 3), "MAJOR": int(major)}))
    return feats


def rasterize_lines(features: list[Feature], like_path: str, out_path: str, value_key: str = "ELEV") -> str:
    """Burn line features into a float raster aligned with `like_path`."""
    from rasterio.features import rasterize
    with rasterio.open(like_path) as ds:
        prof = ds.profile.copy()
        shape = (ds.height, ds.width)
        tr = ds.transform
    arr = rasterize(((f.geometry, f.properties[value_key]) for f in features), out_shape=shape,
                    transform=tr, fill=NODATA, dtype="float32", all_touched=True)
    prof.update(driver="GTiff", count=1, dtype="float32", nodata=NODATA, compress="DEFLATE")
    prof.pop("photometric", None)
    with rasterio.open(out_path, "w", **prof) as out:
        out.write(arr, 1)
    return out_path


def write_export_readme(path: str, title: str, dst_code, lonlat, files: list[str], notes: list[str] = ()):
    lon, lat = lonlat
    c = crsmod.resolve(dst_code, lon, lat)
    lines = [title, "=" * len(title), "",
             f"Coordinate system : {crsmod.label(dst_code)}  [{dst_code}]",
             f"Units             : {crsmod.units_label(dst_code)}"]
    if crsmod.is_luzon1911(dst_code):
        lines.append("Datum shift       : " + crsmod.datum_note(dst_code, "EPSG:4326", lon, lat))
    lines += ["", "Files", "-----"] + [os.path.basename(f) for f in files]
    if notes:
        lines += ["", "Notes", "-----"] + [f"- {n}" for n in notes]
    lines += ["", "PNG/JPEG: the .pgw/.jgw world file and .aux.xml carry the position and CRS - keep them "
              "next to the image.", "KML/KMZ are always WGS 84 (Google Earth standard)."]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    _ = c
