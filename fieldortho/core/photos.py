"""Reading drone photos (EXIF/XMP) and checking a photo set before processing.

Everything here is plain-language oriented: the `Survey.warnings` list is shown
directly to field staff, so messages explain what is wrong and what to do.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from statistics import median
from typing import Callable, Iterable

import numpy as np
from PIL import Image, ExifTags

from . import crs as crsmod
from . import system

PHOTO_EXTS = {".jpg", ".jpeg", ".tif", ".tiff", ".dng"}

# Sensor widths (mm) for common mapping cameras, keyed by EXIF model.
# Used only to *estimate* ground resolution and overlap before processing.
SENSOR_WIDTH_MM = {
    "FC6310": 13.2, "FC6310R": 13.2, "FC6310S": 13.2,   # Phantom 4 Pro / RTK
    "FC330": 6.17, "FC6510": 13.2, "FC6520": 17.3,        # Phantom 4, X4S, X5S
    "FC220": 6.17, "FC2103": 6.17, "FC2204": 6.17,        # Mavic Pro / Air / Mini
    "FC7203": 6.17, "FC7303": 6.17, "FC3170": 6.4,        # Mini, Mini 2, Air 2
    "FC3411": 13.2, "FC3582": 9.7, "FC8482": 9.7,         # Air 2S, Mini 3 Pro, Mini 4 Pro
    "L1D-20C": 13.2, "L2D-20C": 17.3,                    # Mavic 2 Pro, Mavic 3
    "M3E": 17.3, "M3M": 17.3, "M3T": 6.4, "M4E": 17.3,   # Mavic 3 Enterprise / 4E
    "ZENMUSEP1": 35.9, "ZENMUSEL1": 13.2,
    "FC6360": 4.96,                                      # P4 Multispectral
    "DSC-WX220": 6.17, "DSC-RX100M2": 13.2, "ILCE-6000": 23.5,
}
DEFAULT_SENSOR_WIDTH_MM = 6.17  # 1/2.3" sensor - most common small drone camera

_XMP_NUM = r'([+-]?\d+(?:\.\d+)?)'


@dataclass
class Photo:
    path: str
    name: str
    lat: float | None = None
    lon: float | None = None
    alt: float | None = None            # GPS altitude from EXIF (m)
    rel_alt: float | None = None        # height above take-off (DJI XMP), m
    make: str = ""
    model: str = ""
    width: int = 0
    height: int = 0
    focal_mm: float | None = None
    focal35: float | None = None
    taken: str | None = None           # ISO timestamp
    gimbal_pitch: float | None = None  # degrees, -90 = straight down
    sharpness: float | None = None
    x: float | None = None             # projected (UTM) coordinates, filled by analyze()
    y: float | None = None
    blurry: bool = False

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lon is not None and not (self.lat == 0 and self.lon == 0)

    @property
    def camera(self) -> str:
        return f"{self.make} {self.model}".strip() or "Unknown camera"


def _ratio(v) -> float:
    try:
        return float(v)
    except Exception:
        try:
            return v[0] / v[1]
        except Exception:
            return float("nan")


def _dms_to_deg(dms, ref) -> float | None:
    try:
        d, m, s = (_ratio(x) for x in dms)
        deg = d + m / 60.0 + s / 3600.0
        if isinstance(ref, bytes):
            ref = ref.decode(errors="ignore")
        if str(ref).upper().startswith(("S", "W")):
            deg = -deg
        return deg if math.isfinite(deg) else None
    except Exception:
        return None


def _read_xmp(path: str, limit: int = 256 * 1024) -> str:
    """Return the raw XMP packet text if present in the first part of the file."""
    try:
        with open(path, "rb") as f:
            head = f.read(limit)
        start = head.find(b"<x:xmpmeta")
        if start < 0:
            return ""
        end = head.find(b"</x:xmpmeta>", start)
        return head[start:end if end > 0 else start + 65536].decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _xmp_value(xmp: str, key: str) -> float | None:
    m = re.search(key + r'="' + _XMP_NUM + '"', xmp) or \
        re.search(r'<[\w-]*:?' + key + r'>\s*' + _XMP_NUM, xmp)
    return float(m.group(1)) if m else None


def _sharpness(img: Image.Image) -> float:
    """Variance of the Laplacian on a small greyscale copy (higher = sharper)."""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def read_photo(path: str, measure_sharpness: bool = True) -> Photo:
    p = Photo(path=path, name=os.path.basename(path))
    try:
        with Image.open(path) as im:
            p.width, p.height = im.size
            exif = im.getexif()
            p.make = str(exif.get(0x010F, "")).strip("\x00 ").strip()
            p.model = str(exif.get(0x0110, "")).strip("\x00 ").strip()
            sub = exif.get_ifd(0x8769)
            fl = sub.get(0x920A)
            p.focal_mm = _ratio(fl) if fl is not None else None
            f35 = sub.get(0xA405)
            p.focal35 = float(f35) if f35 else None
            ew, eh = sub.get(0xA002), sub.get(0xA003)
            if ew and eh:
                p.width, p.height = int(ew), int(eh)
            dt = sub.get(0x9003) or exif.get(0x0132)
            if dt:
                try:
                    p.taken = datetime.strptime(str(dt).strip("\x00"), "%Y:%m:%d %H:%M:%S").isoformat()
                except ValueError:
                    pass
            gps = exif.get_ifd(0x8825)
            if gps:
                p.lat = _dms_to_deg(gps.get(2), gps.get(1, "N")) if 2 in gps else None
                p.lon = _dms_to_deg(gps.get(4), gps.get(3, "E")) if 4 in gps else None
                if 6 in gps:
                    alt = _ratio(gps[6])
                    if math.isfinite(alt):
                        ref = gps.get(5, 0)
                        if isinstance(ref, bytes):
                            ref = ref[0] if ref else 0
                        p.alt = -alt if ref == 1 else alt
            if measure_sharpness and im.format in ("JPEG", "MPO"):
                im.draft("L", (im.size[0] // 8, im.size[1] // 8))  # fast reduced decode
                p.sharpness = _sharpness(im)
    except Exception:
        return p
    xmp = _read_xmp(path)
    if xmp:
        p.rel_alt = _xmp_value(xmp, "RelativeAltitude")
        p.gimbal_pitch = _xmp_value(xmp, "GimbalPitchDegree")
        if p.alt is None:
            p.alt = _xmp_value(xmp, "AbsoluteAltitude")
    return p


def list_photos(folder: str) -> list[str]:
    out = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if os.path.splitext(f)[1].lower() in PHOTO_EXTS:
                out.append(os.path.join(root, f))
        break  # only the chosen folder, not sub-folders (avoids mixing flights by accident)
    return sorted(out)


def scan_folder(folder: str, progress: Callable[[int, int], None] | None = None,
                cancelled: Callable[[], bool] | None = None) -> list[Photo]:
    files = list_photos(folder)
    photos = []
    for i, f in enumerate(files):
        if cancelled and cancelled():
            break
        photos.append(read_photo(f))
        if progress:
            progress(i + 1, len(files))
    return photos


# ----------------------------------------------------------------------------
# Survey analysis
# ----------------------------------------------------------------------------

@dataclass
class Issue:
    level: str      # "error" (cannot continue), "warning", "info"
    title: str
    detail: str = ""


@dataclass
class Survey:
    total: int = 0
    with_gps: int = 0
    usable: int = 0
    cameras: list[str] = field(default_factory=list)
    epsg: int | None = None
    area_ha: float | None = None
    flight_height_m: float | None = None
    height_source: str = ""
    gsd_cm: float | None = None
    coverage: float | None = None          # median photos seeing each ground point
    low_coverage_pct: float | None = None  # % of site seen by fewer than 5 photos
    blurry: list[str] = field(default_factory=list)
    oblique: int = 0
    duration_min: float | None = None
    issues: list[Issue] = field(default_factory=list)
    footprint_m: tuple[float, float] | None = None

    @property
    def can_process(self) -> bool:
        return not any(i.level == "error" for i in self.issues)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _convex_hull(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(pts))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _polygon_area_perimeter(poly) -> tuple[float, float]:
    if len(poly) < 3:
        return 0.0, 0.0
    a = per = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        a += x1 * y2 - x2 * y1
        per += math.hypot(x2 - x1, y2 - y1)
    return abs(a) / 2.0, per


def _inside(points: np.ndarray, poly) -> np.ndarray:
    """Vectorised point-in-polygon (ray casting)."""
    x, y = points[:, 0], points[:, 1]
    inside = np.zeros(len(points), dtype=bool)
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cond = (y1 > y) != (y2 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (x2 - x1) * (y - y1) / (y2 - y1) + x1
        inside ^= cond & (x < xint)
    return inside


def sensor_width_mm(photo: Photo) -> tuple[float, bool]:
    """Return (sensor width mm, is_known)."""
    model = photo.model.upper().replace(" ", "")
    if model in SENSOR_WIDTH_MM:
        return SENSOR_WIDTH_MM[model], True
    if photo.focal35 and photo.focal_mm:
        return 36.0 * photo.focal_mm / photo.focal35, True
    return DEFAULT_SENSOR_WIDTH_MM, False


def analyze(photos: list[Photo], flight_height_m: float | None = None) -> Survey:
    """Check a photo set. `flight_height_m` lets the user supply the height above
    ground when the camera does not record it."""
    s = Survey(total=len(photos))
    if not photos:
        s.issues.append(Issue("error", "No photos found",
                              "Choose the folder that contains the drone's JPG photos."))
        return s

    gps = [p for p in photos if p.has_gps]
    s.with_gps = len(gps)
    s.cameras = sorted({p.camera for p in photos})

    # --- Sharpness --------------------------------------------------------
    sharp = [p.sharpness for p in photos if p.sharpness]
    if len(sharp) >= 5:
        med = median(sharp)
        for p in photos:
            if p.sharpness is not None and p.sharpness < 0.30 * med:
                p.blurry = True
                s.blurry.append(p.name)

    s.oblique = sum(1 for p in photos if p.gimbal_pitch is not None and p.gimbal_pitch > -60)
    s.usable = sum(1 for p in gps if not p.blurry)

    if s.with_gps == 0:
        s.issues.append(Issue("error", "Photos have no GPS location",
                              "These photos are not geotagged, so the map cannot be placed on the "
                              "Earth. Check that GPS was on during the flight, or that you copied "
                              "the original photos (some apps remove location data)."))
        return s
    if s.with_gps < s.total:
        s.issues.append(Issue("warning", f"{s.total - s.with_gps} photo(s) have no GPS",
                              "They will be left out. This is normal for test shots taken on the ground."))
    if s.usable < 5:
        s.issues.append(Issue("error", "Not enough photos",
                              "At least 5 overlapping geotagged photos are needed. Mapping flights "
                              "usually have 50 or more."))
    elif s.usable < 30:
        s.issues.append(Issue("warning", "Small photo set",
                              f"Only {s.usable} usable photos. Results can have holes near the edges."))
    if len(s.cameras) > 1:
        s.issues.append(Issue("warning", "Photos from more than one camera",
                              "Mixing cameras is possible but can lower quality: " + ", ".join(s.cameras)))
    if s.blurry:
        s.issues.append(Issue("warning", f"{len(s.blurry)} photo(s) look blurry",
                              "They can be left out automatically (recommended). Blur usually "
                              "comes from flying too fast or low light."))
    if s.oblique:
        s.issues.append(Issue("info", f"{s.oblique} tilted (oblique) photo(s)",
                              "Fine for 3D, but straight-down photos give the best orthomosaic."))

    # --- Projection to metres -------------------------------------------
    lat0 = median(p.lat for p in gps)
    lon0 = median(p.lon for p in gps)
    s.epsg = crsmod.utm_epsg(lat0, lon0)
    xs, ys = crsmod.project_lonlat([p.lon for p in gps], [p.lat for p in gps], s.epsg)
    for p, x, y in zip(gps, xs, ys):
        p.x, p.y = x, y

    # Spread check: GPS points all in one place = probably not a mapping flight
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    if span > 20_000:
        s.issues.append(Issue("error", "Photos are spread over more than 20 km",
                              "This folder seems to mix different sites or flights. Put each site "
                              "in its own folder."))

    # --- Flight height ----------------------------------------------------
    rel = [p.rel_alt for p in gps if p.rel_alt is not None]
    if len(rel) >= max(3, len(gps) // 2):
        s.flight_height_m = median(rel)
        s.height_source = "drone (height above take-off)"
    else:
        alts = [p.alt for p in gps if p.alt is not None]
        if len(alts) >= 3 and (max(alts) - min(alts)) > 20:
            # climb/descent recorded: the lowest photo approximates the take-off level
            s.flight_height_m = median(alts) - min(alts)
            s.height_source = "estimated from GPS altitudes"
    if s.flight_height_m is not None and s.flight_height_m < 5:
        s.flight_height_m = None
    if flight_height_m:
        s.flight_height_m = float(flight_height_m)
        s.height_source = "entered by user"

    # --- Ground resolution and footprint ----------------------------------
    ref = gps[0]
    sw, known = sensor_width_mm(ref)
    if s.flight_height_m and ref.focal_mm and ref.width:
        gsd_m = (s.flight_height_m * sw / 1000.0) / (ref.focal_mm / 1000.0) / ref.width
        s.gsd_cm = gsd_m * 100.0
        s.footprint_m = (gsd_m * ref.width, gsd_m * ref.height)
        if not known:
            s.issues.append(Issue("info", "Camera not in the built-in list",
                                  "Ground resolution and overlap shown here are rough estimates."))

    # --- Area ----------------------------------------------------------------
    hull = _convex_hull(list(zip(xs, ys)))
    area, perim = _polygon_area_perimeter(hull)
    if s.footprint_m:
        b = min(s.footprint_m) / 2.0
        area = area + perim * b + math.pi * b * b
    s.area_ha = area / 10_000.0 if area > 0 else None

    # --- Overlap estimate ---------------------------------------------------
    # Count how many photos see each point of the site (orientation-agnostic:
    # each footprint is approximated by a circle of the same area). A regular
    # grid flown at 75% front / 65% side overlap gives ~11 photos per point.
    if s.footprint_m and len(gps) >= 3:
        fw, fh = s.footprint_m
        r = math.sqrt(fw * fh / math.pi)
        pts = np.array([[p.x, p.y] for p in gps])
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        step = max(r / 4.0, 1.0)
        gx, gy = np.meshgrid(np.arange(x0, x1 + step, step), np.arange(y0, y1 + step, step))
        grid = np.column_stack([gx.ravel(), gy.ravel()])
        if len(hull) >= 3:
            grid = grid[_inside(grid, hull)]
        if len(grid):
            counts = np.zeros(len(grid), dtype=int)
            for px, py in pts:
                counts += ((grid[:, 0] - px) ** 2 + (grid[:, 1] - py) ** 2) <= r * r
            s.coverage = float(np.median(counts))
            s.low_coverage_pct = float(np.mean(counts < 5) * 100.0)
            if s.coverage < 5:
                s.issues.append(Issue("warning", f"Low overlap (each spot seen in ~{s.coverage:.0f} photos)",
                                      "Aim for 9 or more (about 75% front and 65% side overlap). "
                                      "Expect gaps or failed matching. Next time increase overlap "
                                      "or fly higher."))
            elif s.low_coverage_pct > 25:
                s.issues.append(Issue("info", f"{s.low_coverage_pct:.0f}% of the area has low overlap",
                                      "Usually the edges of the flight. Results near the edges "
                                      "may be lower quality."))

    times = sorted(p.taken for p in photos if p.taken)
    if len(times) >= 2:
        try:
            dt = datetime.fromisoformat(times[-1]) - datetime.fromisoformat(times[0])
            s.duration_min = dt.total_seconds() / 60
        except ValueError:
            pass
    return s


# ----------------------------------------------------------------------------
# Estimates
# ----------------------------------------------------------------------------

# minutes per photo on a typical 8-thread / 16 GB laptop
_MIN_PER_PHOTO = {"quick": 0.06, "standard": 0.30, "high": 0.90}
_RAM_GB_PER_100 = {"quick": 3, "standard": 8, "high": 20}


def estimate_minutes(n_photos: int, preset: str) -> float:
    base = _MIN_PER_PHOTO.get(preset, 0.3) * n_photos
    return max(2.0, base * 8.0 / max(2, system.cpu_count()))


def estimate_ram_gb(n_photos: int, preset: str) -> float:
    return max(4.0, _RAM_GB_PER_100.get(preset, 8) * n_photos / 100.0)


def estimate_disk_gb(photos: Iterable[Photo], preset: str) -> float:
    size = 0
    for p in photos:
        try:
            size += os.path.getsize(p.path)
        except OSError:
            pass
    factor = {"quick": 2.0, "standard": 4.0, "high": 8.0}.get(preset, 4.0)
    return size * factor / 1024**3
