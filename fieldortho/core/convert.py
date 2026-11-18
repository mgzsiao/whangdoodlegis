"""Change SHP CRS: change the coordinate system of GIS files (e.g. a PRS92 shapefile set to WGS 84).

Vectors : Shapefile (.shp + .dbf/.shx/.prj/.cpg), GeoPackage (every layer), GeoJSON, KML / KMZ, DXF, GPX
Rasters : GeoTIFF and other GDAL-readable grids (.tif, .img, .asc, .jp2 ...)

Files can be picked one by one (any part of a shapefile brings in its .shp), as folders (optionally with
sub-folders) or inside .zip files (read in place through GDAL's /vsizip/). A shapefile without its .shx
is read from a temporary copy where GDAL rebuilds the index; one without a .dbf keeps its shapes only.

Attributes are kept. Datum changes use the same rules as every other export of the app
(core/crs.py): PRS92 <-> WGS 84 with the official 7-parameter transformation (EPSG:15708),
Luzon 1911 with the Mindanao / rest-of-the-Philippines parameters chosen by location.
Everything runs on this computer.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field

import numpy as np

from . import crs as crsmod
from . import exporters as ex

VECTOR_EXT = {".shp", ".gpkg", ".geojson", ".json", ".kml", ".kmz", ".dxf", ".gpx"}
RASTER_EXT = {".tif", ".tiff", ".img", ".asc", ".jp2", ".vrt", ".ecw", ".bil", ".dem"}
SHP_PARTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")
# files that belong to a shapefile: picking one of them means "this shapefile"
SIDECARS = {".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx", ".qix", ".qmd"}
SAME = "same"
EXT_FORMAT = {".shp": "shp", ".gpkg": "gpkg", ".geojson": "geojson", ".json": "geojson", ".kml": "kml",
              ".kmz": "kml", ".dxf": "dxf", ".gpx": "gpkg",
              ".tif": "gtiff", ".tiff": "gtiff", ".asc": "asc"}


class NeedCrs(ValueError):
    """The file does not say which coordinate system it is in."""


@dataclass
class Item:
    path: str
    kind: str                         # vector | raster
    layers: list = field(default_factory=list)       # vector layer names
    crs_wkt: str | None = None        # as stated in the file (None = not stated)
    crs_code: str | None = None       # set by the user, overrides crs_wkt
    count: int = 0                    # features (vector) or bands (raster)
    size: str = ""
    error: str = ""
    parts: dict = field(default_factory=dict)        # shapefiles: {".shp": True, ".shx": False, ...}
    notes: list = field(default_factory=list)        # things the user should know (missing parts ...)
    detected: str | None = None       # code identified from crs_wkt
    how: str = ""                     # how the system was found ("read from the .prj" ...)
    read_path: str = ""               # where the data is read from (a temporary copy when the .shx is rebuilt)
    tmpdir: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path.replace("\\", "/"))

    @property
    def src_path(self) -> str:
        return self.read_path or self.path

    @property
    def in_zip(self) -> bool:
        return self.path.startswith("/vsizip/")

    @property
    def source_code(self) -> str | None:
        if self.crs_code:
            return self.crs_code
        return self.detected


def kind_of(path: str) -> str | None:
    e = os.path.splitext(path)[1].lower()
    return "vector" if e in VECTOR_EXT else "raster" if e in RASTER_EXT else None


def _zip_split(path: str) -> tuple[str, str]:
    """'/vsizip/C:/x/pack.zip/sub/a.shp' -> ('C:/x/pack.zip', 'sub/a.shp')."""
    p = path[len("/vsizip/"):]
    k = p.lower().find(".zip/")
    return p[:k + 4], p[k + 5:]


def zip_members(zpath: str) -> list[str]:
    """Map files inside a .zip, as GDAL /vsizip/ paths (KMZ and zips inside the zip are skipped)."""
    try:
        with zipfile.ZipFile(zpath) as z:
            names = [n for n in z.namelist() if not n.endswith("/") and "__MACOSX" not in n]
    except (zipfile.BadZipFile, OSError):
        return []
    base = zpath.replace("\\", "/")
    out = []
    for n in sorted(names):
        e = os.path.splitext(n)[1].lower()
        if e == ".kmz" or not kind_of(n):
            continue
        out.append(f"/vsizip/{base}/{n}")
    return out


def _siblings(path: str) -> dict[str, str]:
    """Files next to `path` with the same name (any case): {'.shx': real path, ...}."""
    stem = os.path.splitext(os.path.basename(path.replace("\\", "/")))[0].lower()
    if path.startswith("/vsizip/"):
        zp, inner = _zip_split(path)
        folder = os.path.dirname(inner)
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
        out = {}
        for n in names:
            if os.path.dirname(n) == folder:
                s, e = os.path.splitext(os.path.basename(n))
                if s.lower() == stem:
                    out[e.lower()] = f"/vsizip/{zp}/{n}"
        return out
    folder = os.path.dirname(path) or "."
    out = {}
    try:
        for n in os.listdir(folder):
            s, e = os.path.splitext(n)
            if s.lower() == stem:
                out[e.lower()] = os.path.join(folder, n)
    except OSError:
        pass
    return out


def shp_parts(path: str) -> dict[str, bool]:
    """Which of .shp .shx .dbf .prj .cpg sit next to a shapefile."""
    have = _siblings(path)
    return {e: e in have for e in SHP_PARTS}


def _copy_parts(path: str, dst_dir: str) -> str:
    """Copy a shapefile's parts (from a folder or a zip) into dst_dir; returns the new .shp path."""
    stem = os.path.splitext(os.path.basename(path.replace("\\", "/")))[0]
    for e, src in _siblings(path).items():
        dst = os.path.join(dst_dir, stem + e)
        if src.startswith("/vsizip/"):
            zp, inner = _zip_split(src)
            with zipfile.ZipFile(zp) as z, z.open(inner) as fi, open(dst, "wb") as fo:
                shutil.copyfileobj(fi, fo)
        else:
            shutil.copyfile(src, dst)
    return os.path.join(dst_dir, stem + ".shp")


def cleanup(it: Item):
    """Remove the temporary copy made for a shapefile without .shx."""
    if it.tmpdir:
        shutil.rmtree(it.tmpdir, ignore_errors=True)
        it.tmpdir = ""


def gdal_path(path: str) -> str:
    """KMZ is a zip with a .kml inside."""
    if path.lower().endswith(".kmz"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".kml")]
        if not names:
            raise ValueError("No .kml inside this KMZ.")
        inner = "doc.kml" if "doc.kml" in names else names[0]
        return f"/vsizip/{path}/{inner}"
    return path


def identify(wkt_or_code) -> tuple[str, str]:
    """(code, how): an EPSG code when the file's system can be identified, with how it was found.
    ESRI .prj files name PRS92 / Luzon 1911 without the datum shift; with the EPSG code the
    official shift is used."""
    from pyproj import CRS
    try:
        c = CRS.from_user_input(wkt_or_code)
    except Exception:
        return str(wkt_or_code), "as written in the file (not recognised)"
    for conf in (90, 70):
        try:
            e = c.to_epsg(min_confidence=conf)
        except Exception:
            e = None
        if e:
            return f"EPSG:{e}", "read from the file"
    # ESRI names: GCS_PRS_1992 / PRS_1992_Philippines_Zone_V, GCS_Luzon_1911 ...
    n = (c.name or "").lower().replace(" ", "_")
    m = re.search(r"zone_([ivx]+)", n)
    zones = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5}
    code = None
    if "prs" in n and "1992" in n:
        if m and m.group(1) in zones:
            code = f"EPSG:{3120 + zones[m.group(1)]}"
        elif c.is_geographic:
            code = "EPSG:4683"
    if "luzon" in n:
        if m and m.group(1) in zones and zones[m.group(1)] > 0:
            code = f"EPSG:{25390 + zones[m.group(1)]}"
        elif c.is_geographic:
            code = "EPSG:4253"
    if code:
        return code, "ESRI name in the file, matched to its EPSG code"
    return c.to_wkt(), "read from the file (no EPSG code)"


def normalise(wkt_or_code) -> str:
    return identify(wkt_or_code)[0]


def _read_layers(gp: str):
    import pyogrio
    layers = [str(x[0]) for x in pyogrio.list_layers(gp)]
    n, wkt = 0, None
    for lay in layers:
        info = pyogrio.read_info(gp, layer=lay)
        n += int(info.get("features") or 0)
        wkt = wkt or info.get("crs")
    return layers, n, wkt


def inspect(path: str) -> Item:
    k = kind_of(path)
    it = Item(path=path, kind=k or "?")
    if k is None:
        it.error = "Not a map file the app can convert."
        return it
    low = path.lower()
    try:
        if k == "vector":
            if low.endswith(".shp"):
                it.parts = shp_parts(path)
                if not it.parts[".shx"]:
                    # GDAL rebuilds a missing index, but only next to the .shp: work on a copy
                    import pyogrio
                    it.tmpdir = tempfile.mkdtemp(prefix="wdg_shx_")
                    it.read_path = _copy_parts(path, it.tmpdir)
                    pyogrio.set_gdal_config_options({"SHAPE_RESTORE_SHX": True})
                    try:
                        pyogrio.read_info(it.read_path)
                    finally:
                        pyogrio.set_gdal_config_options({"SHAPE_RESTORE_SHX": None})
                    it.notes.append("No .shx (shape index): rebuilt from the .shp.")
                if not it.parts[".dbf"]:
                    it.notes.append("No .dbf (attribute table): only the shapes are converted, without attributes.")
            it.layers, n, wkt = _read_layers(gdal_path(it.src_path))
            it.count = n
            if low.endswith((".kml", ".kmz", ".gpx")) and not wkt:
                wkt = "EPSG:4326"
                it.how = "KML and GPX files are always WGS 84"
            it.crs_wkt = wkt
            it.size = f"{len(it.layers)} layer{'s' * (len(it.layers) != 1)}, {n} feature{'s' * (n != 1)}"
        else:
            import rasterio
            with rasterio.open(path) as ds:
                it.crs_wkt = ds.crs.to_wkt() if ds.crs else None
                it.count = ds.count
                it.size = f"{ds.width} x {ds.height} px, {ds.count} band{'s' * (ds.count != 1)}"
    except Exception as e:
        cleanup(it)
        it.error = f"Cannot read: {e}"
        return it
    if it.crs_wkt:
        it.detected, how = identify(it.crs_wkt)
        if not it.how:
            it.how = how.replace("the file", "the .prj") if low.endswith(".shp") else how
    elif low.endswith(".shp"):
        it.how = "no .prj"
    else:
        it.how = "not stated in the file"
    return it


def _add_file(f: str, out: list):
    e = os.path.splitext(f)[1].lower()
    if e == ".zip":
        out.extend(zip_members(f))
    elif e in SIDECARS:                         # a part of a shapefile: take its .shp
        shp = _siblings(f).get(".shp")
        if shp:
            out.append(shp)
    elif kind_of(f):
        out.append(f)


def collect(paths, recursive: bool = False) -> list[str]:
    """Files to convert from a mix of files, folders (with sub-folders when `recursive`) and .zip files.
    Any part of a shapefile (.dbf, .prj ...) stands for its .shp; zip contents are read in place."""
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, dirs, files in os.walk(p):
                    dirs.sort()
                    for n in sorted(files):
                        _add_file(os.path.join(root, n), out)
            else:
                for n in sorted(os.listdir(p)):
                    f = os.path.join(p, n)
                    if os.path.isfile(f):
                        _add_file(f, out)
        elif os.path.isfile(p):
            _add_file(p, out)
    seen, uniq = set(), []
    for p in out:
        k = p.lower() if p.startswith("/vsizip/") else os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def crs_tag(code: str) -> str:
    e = crsmod._epsg_of(code)
    return f"EPSG{e}" if e else "converted"


def _lonlat_of(bounds, src_code) -> tuple[float, float]:
    x0, y0, x1, y1 = bounds
    try:
        t = crsmod.transformer(src_code, "EPSG:4326")
        lon, lat = t.transform((x0 + x1) / 2, (y0 + y1) / 2)
        if np.isfinite(lon) and np.isfinite(lat):
            return float(lon), float(lat)
    except Exception:
        pass
    return 122.0, 12.0


def _pyval(v):
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if np.isnan(f) else f
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    if isinstance(v, (str, int, float)):
        return v
    return str(v)


GROUPS = (("points", {"Point", "MultiPoint"}), ("lines", {"LineString", "MultiLineString", "LinearRing"}),
          ("polygons", {"Polygon", "MultiPolygon"}))


def convert_vector(it: Item, out_dir: str, dst_code: str, fmt: str = SAME) -> tuple[list[str], list[str]]:
    """Returns (files written, notes)."""
    import shapely
    from pyogrio.raw import read as ogr_read
    src = it.source_code
    if not src:
        raise NeedCrs(f"{it.name}: the file does not say its coordinate system.")
    ext = os.path.splitext(it.path)[1].lower()
    fmt = EXT_FORMAT.get(ext, "gpkg") if fmt == SAME else fmt
    stem = os.path.splitext(it.name)[0]
    gp = gdal_path(it.src_path)
    files, notes = [], list(it.notes)
    gpkg_path = os.path.join(out_dir, f"{stem}_{crs_tag(dst_code)}.gpkg")
    for li, lay in enumerate(it.layers):
        meta, _fids, wkb, cols = ogr_read(gp, layer=lay, encoding=None)
        if wkb is None or len(wkb) == 0:
            continue
        names = list(meta.get("fields", []))
        feats = []
        geoms = shapely.from_wkb(wkb)
        for i, g in enumerate(geoms):
            if g is None or g.is_empty:
                continue
            props = {n: _pyval(cols[j][i]) for j, n in enumerate(names)}
            feats.append(ex.Feature(g, props))
        if not feats:
            continue
        b = shapely.total_bounds(np.array([f.geometry for f in feats], dtype=object))
        lonlat = _lonlat_of(b, src)
        # one output per geometry type when the target format needs it (Shapefile, KML/DXF are fine mixed)
        kinds = {f.geometry.geom_type for f in feats}
        split = fmt in ("shp", "geojson", "gpkg") and not any(kinds <= s for _n, s in GROUPS)
        parts = [(n, [f for f in feats if f.geometry.geom_type in s]) for n, s in GROUPS] if split \
            else [("", feats)]
        for suffix, fs in parts:
            if not fs:
                continue
            lname = re.sub(r"[^\w\-]+", "_", lay) + (f"_{suffix}" if suffix else "")
            if fmt == "gpkg":
                files += ex.write_vector(fs, gpkg_path, "gpkg", src, dst_code, layer=lname, lonlat=lonlat,
                                         append=bool(files))
                continue
            multi = len(it.layers) > 1 or split
            base = f"{stem}_{lname}" if multi else stem
            out = os.path.join(out_dir, f"{base}_{crs_tag(dst_code)}{ex.VECTOR_FORMATS[fmt][1]}")
            files += ex.write_vector(fs, out, fmt, src, dst_code, layer=lname, lonlat=lonlat,
                                     dxf_layer_field="Layer" if fmt == "dxf" and "Layer" in names else None)
        if li == 0:
            notes.append(crsmod.datum_note(src, dst_code, *lonlat))
        if split:
            notes.append(f"{it.name} ({lay}) mixes points, lines and polygons: written as separate files.")
    n = ex.format_note(fmt, dst_code)
    if n:
        notes.append(n)
    files = list(dict.fromkeys(files))
    return files, notes


def convert_raster(it: Item, out_dir: str, dst_code: str, fmt: str = SAME) -> tuple[list[str], list[str]]:
    import tempfile
    import rasterio
    src = it.source_code
    if not src:
        raise NeedCrs(f"{it.name}: the file does not say its coordinate system.")
    ext = os.path.splitext(it.path)[1].lower()
    fmt = EXT_FORMAT.get(ext, "gtiff") if fmt == SAME else fmt
    if fmt not in ex.RASTER_FORMATS:
        fmt = "gtiff"
    stem = os.path.splitext(it.name)[0]
    with rasterio.open(it.path) as ds:
        is_dem = ds.count == 1 and ds.dtypes[0].startswith(("float", "int16", "int32"))
        b = ds.bounds
    lonlat = _lonlat_of((b.left, b.bottom, b.right, b.top), src)
    tmpd = tempfile.mkdtemp(prefix="wdg_conv_")
    tmp = os.path.join(tmpd, "src.tif")
    try:
        srcp = it.path
        if it.crs_code:                         # the file has no/wrong system: tag it first
            with rasterio.open(it.path) as ds:
                prof = ds.profile.copy()
                prof.update(driver="GTiff", crs=ex.rio_crs(it.crs_code, *lonlat))
                with rasterio.open(tmp + ".tagged.tif", "w", **prof) as o:
                    o.write(ds.read())
            srcp = tmp + ".tagged.tif"
        ex.reproject_raster(srcp, tmp, dst_code, resampling="bilinear" if is_dem else "nearest",
                            lonlat=lonlat, is_dem=is_dem)
        out = os.path.join(out_dir, f"{stem}_{crs_tag(dst_code)}{ex.RASTER_FORMATS[fmt][1]}")
        files = ex.write_raster(tmp, out, fmt)
    finally:
        import shutil
        shutil.rmtree(tmpd, ignore_errors=True)
    notes = [crsmod.datum_note(src, dst_code, *lonlat)]
    n = ex.format_note(fmt, dst_code)
    if n:
        notes.append(n)
    return files, notes


def convert(it: Item, out_dir: str, dst_code: str, fmt: str = SAME) -> tuple[list[str], list[str]]:
    os.makedirs(out_dir, exist_ok=True)
    if it.kind == "vector":
        return convert_vector(it, out_dir, dst_code, fmt)
    return convert_raster(it, out_dir, dst_code, fmt)


def write_notes(out_dir: str, dst_code: str, done: list[tuple[str, list[str], list[str]]]) -> str:
    from .. import APP_NAME, __version__
    p = os.path.join(out_dir, "conversion_notes.txt")
    lines = [f"Converted with {APP_NAME} {__version__}", f"Target coordinate system: {crsmod.label(dst_code)} "
             f"({dst_code})", ""]
    for name, files, notes in done:
        lines.append(name)
        lines += [f"  -> {os.path.basename(f)}" for f in files]
        lines += [f"  note: {n}" for n in dict.fromkeys(notes)]
        lines.append("")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return p
