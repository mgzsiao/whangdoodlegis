"""Coordinate reference systems, with care for Philippine datums.

Accuracy notes (checked against the EPSG database shipped with PROJ):

* Luzon 1911 (Clarke 1866) - the datum printed on older NAMRIA / MGB maps -
  has TWO official transformations to WGS 84 whose bounding boxes overlap:
      (1) Philippines excluding Mindanao   dx=-133 dy=-77 dz=-51   (EPSG:1161)
      (2) Mindanao                         dx=-133 dy=-79 dz=-72   (EPSG:1162)
  PROJ picks (1) everywhere because it has the better stated accuracy, which
  is wrong on Mindanao by ~20 m. FieldOrtho decides by location instead and
  writes the chosen parameters explicitly (+towgs84) into every output.
* PRS92 has one transformation to WGS 84 (7-parameter, EPSG:15708).
"""
from __future__ import annotations

import math
from functools import lru_cache

from pyproj import CRS, Transformer

AUTO = "auto"

# Output choices for drone processing (kept for job settings)
CHOICES: list[tuple[str, str]] = [
    (AUTO, "Automatic - WGS 84 / UTM zone of the site (recommended)"),
    ("EPSG:32651", "WGS 84 / UTM zone 51N  (most of the Philippines)"),
    ("EPSG:32650", "WGS 84 / UTM zone 50N  (western Palawan)"),
    ("EPSG:3125", "PRS92 / Philippines zone V  (124-126°E: Leyte, Samar, most of Mindanao)"),
    ("EPSG:3124", "PRS92 / Philippines zone IV  (122-124°E: Panay, Negros, Cebu, W. Mindanao)"),
    ("EPSG:3123", "PRS92 / Philippines zone III  (120-122°E: Metro Manila, S. Luzon)"),
    ("EPSG:3122", "PRS92 / Philippines zone II  (118-120°E: Palawan, W. Luzon)"),
    ("EPSG:3121", "PRS92 / Philippines zone I  (116-118°E)"),
    ("EPSG:4326", "WGS 84 latitude / longitude  (GPS, web maps)"),
    ("EPSG:3857", "Web Mercator  (web maps; not for measuring)"),
]

# Choices for exports: everything above plus geographic PRS92 / Luzon 1911.
EXPORT_CHOICES: list[tuple[str, str]] = [
    ("EPSG:4326", "WGS 84 - latitude / longitude (GPS, Google Earth)"),
    ("EPSG:32651", "WGS 84 / UTM zone 51N"),
    ("EPSG:32650", "WGS 84 / UTM zone 50N"),
    ("EPSG:32652", "WGS 84 / UTM zone 52N"),
    ("EPSG:3125", "PRS92 / Philippines zone V (124-126°E)"),
    ("EPSG:3124", "PRS92 / Philippines zone IV (122-124°E)"),
    ("EPSG:3123", "PRS92 / Philippines zone III (120-122°E)"),
    ("EPSG:3122", "PRS92 / Philippines zone II (118-120°E)"),
    ("EPSG:3121", "PRS92 / Philippines zone I (116-118°E)"),
    ("EPSG:4683", "PRS92 - latitude / longitude"),
    ("EPSG:4253", "Luzon 1911 - latitude / longitude (old NAMRIA/MGB maps)"),
    ("EPSG:25395", "Luzon 1911 / Philippines zone V"),
    ("EPSG:25394", "Luzon 1911 / Philippines zone IV"),
    ("EPSG:25393", "Luzon 1911 / Philippines zone III"),
    ("EPSG:3857", "Web Mercator (web maps; not for measuring)"),
]

# Grid systems found printed on maps (georeferencer)
GRID_CHOICES: list[tuple[str, str]] = [
    ("EPSG:4253", "Luzon 1911 latitude / longitude (NAMRIA, MGB maps before PRS92)"),
    ("EPSG:4683", "PRS92 latitude / longitude"),
    ("EPSG:4326", "WGS 84 latitude / longitude"),
]

LUZON_ZONES = {25391: 117, 25392: 119, 25393: 121, 25394: 123, 25395: 125}
LUZON_GEO = 4253
LUZON_TOWGS84 = {"mindanao": (-133, -79, -72), "other": (-133, -77, -51)}


def utm_epsg(lat: float, lon: float) -> int:
    zone = int((lon + 180.0) // 6.0) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


def prs92_zone_epsg(lon: float) -> int:
    """PRS92 Philippine zone for a longitude (zones are 2 degrees wide, CM 117..125)."""
    zone = int(min(max((lon - 116.0) // 2.0, 0), 4)) + 1
    return 3120 + zone


def in_mindanao(lon: float, lat: float) -> bool:
    """Coarse test matching EPSG area 'Philippines - Mindanao onshore'
    (Mindanao mainland, Camiguin, Surigao/Siargao/Dinagat, Basilan, Sulu)."""
    if not (119.7 <= lon <= 126.7 and 4.9 <= lat <= 10.6):
        return False
    if lat < 9.0:
        return True                       # all land south of 9N in the box is Mindanao/Sulu
    if 9.0 <= lat <= 9.45 and lon >= 124.55:
        return True                       # Camiguin, north tip of Misamis Oriental/Agusan
    if lon >= 125.3:
        return True                       # Surigao del Norte, Siargao, Dinagat
    return False                          # Bohol, Siquijor, Negros, Cebu, Leyte


def _epsg_of(code) -> int | None:
    try:
        return CRS.from_user_input(code).to_epsg()
    except Exception:
        return None


def is_luzon1911(code) -> bool:
    e = _epsg_of(code) if not isinstance(code, int) else code
    return e == LUZON_GEO or e in LUZON_ZONES


@lru_cache(maxsize=64)
def _resolve(code: str, region: str) -> CRS:
    epsg = _epsg_of(code)
    if epsg == LUZON_GEO or epsg in LUZON_ZONES:
        dx, dy, dz = LUZON_TOWGS84[region]
        tow = f"+ellps=clrk66 +towgs84={dx},{dy},{dz},0,0,0,0 +no_defs +type=crs"
        if epsg == LUZON_GEO:
            c = CRS.from_proj4(f"+proj=longlat {tow}")
        else:
            c = CRS.from_proj4(f"+proj=tmerc +lat_0=0 +lon_0={LUZON_ZONES[epsg]} +k=0.99995 "
                               f"+x_0=500000 +y_0=0 +units=m {tow}")
        # Readable names, but no EPSG authority: with an authority GDAL writes only
        # the EPSG code to GeoTIFF and the explicit datum shift would be lost.
        tag = "Mindanao" if region == "mindanao" else "outside Mindanao"
        name = CRS.from_epsg(epsg).name
        w = c.to_wkt("WKT1_GDAL")
        w = w.replace('PROJCS["unknown"', f'PROJCS["{name} ({tag})"', 1)
        w = w.replace('GEOGCS["unknown"', f'GEOGCS["Luzon 1911 ({tag})"' if epsg == LUZON_GEO else 'GEOGCS["Luzon 1911"', 1)
        return CRS.from_wkt(w)
    return CRS.from_user_input(code)


def resolve(code, lon: float | None = None, lat: float | None = None) -> CRS:
    """CRS object to use for computation. Luzon 1911 systems get the correct
    datum shift for the location (Mindanao vs rest of the Philippines)."""
    if isinstance(code, CRS):
        code = code.to_wkt()
    region = "mindanao" if (lon is not None and lat is not None and in_mindanao(lon, lat)) else "other"
    return _resolve(str(code), region)


@lru_cache(maxsize=64)
def _transformer_cached(src_wkt: str, dst_wkt: str) -> Transformer:
    return Transformer.from_crs(CRS.from_wkt(src_wkt), CRS.from_wkt(dst_wkt), always_xy=True)


def transformer(src, dst, lon: float | None = None, lat: float | None = None) -> Transformer:
    s = resolve(src, lon, lat)
    d = resolve(dst, lon, lat)
    return _transformer_cached(s.to_wkt(), d.to_wkt())


def project_lonlat(lons, lats, epsg: int):
    lons, lats = list(lons), list(lats)
    t = transformer("EPSG:4326", f"EPSG:{epsg}", lons[0] if lons else None, lats[0] if lats else None)
    xs, ys = t.transform(lons, lats)
    return list(xs), list(ys)


def transform(xs, ys, src, dst, lon: float | None = None, lat: float | None = None):
    return transformer(src, dst, lon, lat).transform(xs, ys)


def _geographic_code(code) -> str:
    """Geographic system underlying a CRS code (keeps Luzon 1911 identity)."""
    if is_luzon1911(code):
        return f"EPSG:{LUZON_GEO}"
    c = CRS.from_user_input(code)
    g = c.geodetic_crs
    e = g.to_epsg() if g is not None else None
    return f"EPSG:{e}" if e else g.to_wkt()


def datum_shift_m(src, dst, lon: float, lat: float) -> tuple[float, float]:
    """(east, north) metres between the same numeric lon/lat read in the src
    datum vs the dst datum - i.e. the error if the datum change were ignored."""
    to84_s = transformer(_geographic_code(src), "EPSG:4326", lon, lat)
    to84_d = transformer(_geographic_code(dst), "EPSG:4326", lon, lat)
    x1, y1 = to84_s.transform(lon, lat)
    x2, y2 = to84_d.transform(lon, lat)
    return ((x1 - x2) * 111320 * math.cos(math.radians(lat)), (y1 - y2) * 110574)


def datum_note(src, dst, lon: float, lat: float) -> str:
    """Human-readable description of the datum change applied (with shift size)."""
    gs, gd = _geographic_code(src), _geographic_code(dst)
    if gs == gd:
        return "Same datum - no datum shift."
    de, dn = datum_shift_m(src, dst, lon, lat)
    parts = []
    if is_luzon1911(src) or is_luzon1911(dst):
        mind = in_mindanao(lon, lat)
        reg = "Mindanao" if mind else "Philippines excl. Mindanao"
        parts.append(f"Luzon 1911 \u2192 WGS 84 with the {reg} parameters "
                     f"{LUZON_TOWGS84['mindanao' if mind else 'other']}")
    if "4683" in (gs + gd):
        parts.append("PRS92 \u2192 WGS 84 with the official 7-parameter transformation")
    txt = "; ".join(parts) or "Standard EPSG datum transformation"
    return f"{txt}. Datum difference at the site \u2248 {math.hypot(de, dn):.0f} m."


def label(code: str | None) -> str:
    if not code or code == AUTO:
        return "Automatic (UTM)"
    for c, lab in CHOICES + EXPORT_CHOICES + GRID_CHOICES:
        if c == code:
            return lab.split("  (")[0].split(" (")[0] if "(" in lab and c in dict(CHOICES) else lab
    try:
        return CRS.from_user_input(code).name
    except Exception:
        return code


def is_valid(code: str) -> bool:
    try:
        CRS.from_user_input(code)
        return True
    except Exception:
        return False


def is_geographic(code) -> bool:
    try:
        return resolve(code).is_geographic
    except Exception:
        return False


def units_label(code) -> str:
    return "degrees" if is_geographic(code) else "metres"
