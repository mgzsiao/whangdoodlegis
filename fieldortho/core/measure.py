"""Measurements on a processed drone map: points, paths, height differences, areas and volumes.

All coordinates are map coordinates (metres) in the elevation model's own projected system.
Calculations read the elevation file at full detail for the part that is measured
(thinned out only for very large areas), never the screen preview.
"""
from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.windows import Window

from .terrain import Dem

MAX_CELLS = 4_000_000       # cells read for one measurement (about 2000 x 2000)
SLOPE_STEP = 0.5            # metres: ground slopes and 3-D lengths are measured over this distance,
                            # so 5 cm grass/noise roughness does not inflate them
STEEP_BASE = 5.0            # metres: "steepest part" of a path is measured over this length
DEFAULT_SWELL = 25.0        # % : dug soil takes about this much more space when loose
TRUCKS = (6.0, 10.0)        # m3: common dump truck sizes in the Philippines

REFERENCES = {
    "edge": "the ground around the edge (a surface stretched across the outline)",
    "edge_mean": "one flat level at the average height of the edge",
    "level": "a fixed elevation",
    "lowest": "the lowest ground inside the outline",
}


# ============================================================================ reading
def read_area(path: str, bounds, max_cells: int = MAX_CELLS, pad_cells: int = 2) -> Dem:
    """Read the part of an elevation file covering bounds = (xmin, ymin, xmax, ymax)."""
    with rasterio.open(path) as ds:
        if ds.crs is None:
            raise ValueError("This elevation file has no coordinate system.")
        if ds.crs.is_geographic:
            raise ValueError("Measuring needs an elevation file in metres (a projected system such as UTM), "
                             "not latitude/longitude.")
        x0, y0, x1, y1 = bounds
        inv = ~ds.transform
        cols, rows = zip(*(inv * (x, y) for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1))))
        c0 = max(0, int(math.floor(min(cols))) - pad_cells)
        r0 = max(0, int(math.floor(min(rows))) - pad_cells)
        c1 = min(ds.width, int(math.ceil(max(cols))) + pad_cells)
        r1 = min(ds.height, int(math.ceil(max(rows))) + pad_cells)
        if c1 <= c0 or r1 <= r0:
            raise ValueError("That place is outside the elevation model.")
        w, h = c1 - c0, r1 - r0
        f = max(1.0, math.sqrt(w * h / max_cells))
        ow, oh = max(1, int(round(w / f))), max(1, int(round(h / f)))
        win = Window(c0, r0, w, h)
        z = ds.read(1, window=win, out_shape=(oh, ow), masked=True,
                    resampling=Resampling.average if f > 1 else Resampling.nearest)
        z = z.astype("float64").filled(np.nan)
        tr = ds.window_transform(win) * Affine.scale(w / ow, h / oh)
        crs_wkt = ds.crs.to_wkt()
    z[z < -1000] = np.nan
    return Dem(z, tr, crs_wkt, path, f)


def sample(dem: Dem, xs, ys) -> np.ndarray:
    """Heights at map points (bilinear; nearest cell next to gaps; NaN outside or no data)."""
    from scipy.ndimage import map_coordinates
    xs, ys = np.atleast_1d(np.asarray(xs, float)), np.atleast_1d(np.asarray(ys, float))
    t = dem.transform
    c = (xs - t.c) / t.a - 0.5
    r = (ys - t.f) / t.e - 0.5
    h, w = dem.shape
    z = map_coordinates(dem.z, [r, c], order=1, mode="nearest")
    rn = np.clip(np.round(r).astype(int), 0, h - 1)
    cn = np.clip(np.round(c).astype(int), 0, w - 1)
    z = np.where(np.isfinite(z), z, dem.z[rn, cn])
    outside = (r < -0.5) | (r > h - 0.5) | (c < -0.5) | (c > w - 0.5)
    z[outside] = np.nan
    return z


def densify(pts, step: float, closed: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Points every `step` metres along a line (vertices kept). Returns (xy, distance along)."""
    p = np.asarray(pts, float)
    if closed and len(p) > 2 and not np.allclose(p[0], p[-1]):
        p = np.vstack([p, p[:1]])
    out, dist, d0 = [p[0]], [0.0], 0.0
    for a, b in zip(p[:-1], p[1:]):
        seg = float(np.hypot(*(b - a)))
        n = max(1, int(math.ceil(seg / step)))
        for k in range(1, n + 1):
            out.append(a + (b - a) * k / n)
            dist.append(d0 + seg * k / n)
        d0 += seg
    return np.array(out), np.array(dist)


def _step(dem: Dem) -> float:
    return max(dem.cell, SLOPE_STEP)


def _length_3d(dist, z) -> float:
    ok = np.isfinite(z)
    if ok.sum() < 2:
        return float(dist[-1])
    d, zz = dist[ok], z[ok]
    return float(np.sum(np.hypot(np.diff(d), np.diff(zz))) + (dist[-1] - (d[-1] - d[0])))


# ============================================================================ point / height
def point_elevation(path: str, x: float, y: float) -> float:
    dem = read_area(path, (x, y, x, y), pad_cells=3)
    return float(sample(dem, [x], [y])[0])


@dataclass
class HeightResult:
    z_a: float
    z_b: float
    rise: float              # b - a
    horizontal: float
    slope_pct: float
    slope_deg: float
    straight: float          # straight-line (3-D) distance


def height_between(path: str, a, b) -> HeightResult:
    za, zb = point_elevation(path, *a), point_elevation(path, *b)
    hz = float(math.hypot(b[0] - a[0], b[1] - a[1]))
    rise = zb - za
    return HeightResult(za, zb, rise, hz, 100 * rise / hz if hz > 0 else float("nan"),
                        math.degrees(math.atan2(rise, hz)) if hz > 0 else float("nan"), math.hypot(hz, rise))


# ============================================================================ path
@dataclass
class PathResult:
    xy: np.ndarray
    dist: np.ndarray
    z: np.ndarray
    length_2d: float
    length_3d: float
    z_start: float
    z_end: float
    z_min: float
    z_max: float
    climb: float
    descent: float
    steepest_pct: float
    no_data_pct: float


def measure_path(path: str, pts) -> PathResult:
    pts = np.asarray(pts, float)
    if len(pts) < 2:
        raise ValueError("Click at least two points along the path.")
    x0, y0 = pts.min(0)
    x1, y1 = pts.max(0)
    dem = read_area(path, (x0, y0, x1, y1))
    xy, dist = densify(pts, _step(dem))
    z = sample(dem, xy[:, 0], xy[:, 1])
    ok = np.isfinite(z)
    if not ok.any():
        raise ValueError("There is no elevation data along this path.")
    zv = z[ok]
    dz = np.diff(zv)
    total = float(dist[-1])
    base = min(STEEP_BASE, total)
    steep = 0.0
    if base > 0:
        d_ok = dist[ok]
        probe = np.arange(d_ok[0], d_ok[-1] - base + 1e-9, max(_step(dem), base / 10)) if d_ok[-1] - d_ok[0] >= base \
            else np.array([d_ok[0]])
        za = np.interp(probe, d_ok, zv)
        zb = np.interp(probe + base, d_ok, zv)
        steep = float(np.max(np.abs(zb - za)) / base * 100)
    return PathResult(xy, dist, z, total, _length_3d(dist, z), float(zv[0]), float(zv[-1]), float(zv.min()),
                      float(zv.max()), float(dz[dz > 0].sum()), abs(float(dz[dz < 0].sum())), steep,
                      100.0 * (~ok).sum() / len(z))


# ============================================================================ area / volume
def _polygon(pts):
    from shapely.geometry import Polygon
    if len(pts) < 3:
        raise ValueError("Click at least three points around the area.")
    poly = Polygon(pts)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty or poly.area <= 0:
        raise ValueError("The outline does not enclose an area. Click around it without crossing the lines.")
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    return poly


def _grid(path: str, poly, max_cells: int):
    from rasterio.features import geometry_mask
    dem = read_area(path, poly.bounds, max_cells)
    mask = geometry_mask([poly], out_shape=dem.shape, transform=dem.transform, invert=True)
    if not mask.any():          # smaller than one cell
        mask = geometry_mask([poly], out_shape=dem.shape, transform=dem.transform, invert=True, all_touched=True)
    return dem, mask


def _slope_factor(dem: Dem, mask: np.ndarray) -> float:
    """Average (surface area / flat area) inside the mask, slopes measured over ~SLOPE_STEP."""
    z = dem.z
    f = max(1, int(round(SLOPE_STEP / dem.cell)))
    if f > 1:
        h, w = (z.shape[0] // f) * f, (z.shape[1] // f) * f
        if h == 0 or w == 0:
            return 1.0
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)      # blocks that are all no-data
            zs = np.nanmean(z[:h, :w].reshape(h // f, f, w // f, f), axis=(1, 3))
        ms = mask[:h, :w].reshape(h // f, f, w // f, f).any(axis=(1, 3))
    else:
        zs, ms = z, mask
    if min(zs.shape) < 2:
        return 1.0
    gy, gx = np.gradient(zs, abs(dem.transform.e) * f, abs(dem.transform.a) * f)
    k = np.sqrt(1 + gx ** 2 + gy ** 2)
    sel = k[ms & np.isfinite(k)]
    return float(sel.mean()) if sel.size else 1.0


@dataclass
class AreaResult:
    perimeter_2d: float
    perimeter_3d: float
    area_2d: float
    area_3d: float
    z_min: float
    z_max: float
    z_mean: float
    no_data_pct: float
    cell: float


def measure_area(path: str, pts, max_cells: int = MAX_CELLS) -> AreaResult:
    poly = _polygon(pts)
    dem, mask = _grid(path, poly, max_cells)
    return _area_from(dem, mask, poly)


def _area_from(dem, mask, poly) -> AreaResult:
    zin = dem.z[mask]
    ok = np.isfinite(zin)
    if not ok.any():
        raise ValueError("There is no elevation data inside this outline.")
    ring = np.asarray(poly.exterior.coords)
    xy, dist = densify(ring, _step(dem))
    zr = sample(dem, xy[:, 0], xy[:, 1])
    return AreaResult(float(poly.length), _length_3d(dist, zr), float(poly.area),
                      float(poly.area * _slope_factor(dem, mask)), float(zin[ok].min()), float(zin[ok].max()),
                      float(zin[ok].mean()), 100.0 * (~ok).sum() / ok.size, dem.cell)


@dataclass
class VolumeResult(AreaResult):
    reference: str = "edge"
    reference_text: str = ""
    level: float = float("nan")          # flat reference height (nan for the stretched edge surface)
    cut_m3: float = 0.0                  # ground above the reference: to dig away
    fill_m3: float = 0.0                 # ground below the reference: to fill in
    cut_area: float = 0.0
    fill_area: float = 0.0
    max_cut: float = 0.0
    max_fill: float = 0.0
    dz: np.ndarray | None = field(default=None, repr=False)          # ground - reference (NaN outside)
    transform: object = field(default=None, repr=False)
    ground: np.ndarray | None = field(default=None, repr=False)      # heights on the same grid
    crs_wkt: str = ""

    @property
    def net_m3(self) -> float:
        return self.cut_m3 - self.fill_m3

    @property
    def total_m3(self) -> float:
        return self.cut_m3 + self.fill_m3


def measure_volume(path: str, pts, reference: str = "edge", level: float | None = None,
                   max_cells: int = MAX_CELLS) -> VolumeResult:
    if reference not in REFERENCES:
        raise ValueError(f"Unknown reference '{reference}'.")
    if reference == "level" and (level is None or not math.isfinite(level)):
        raise ValueError("Type the elevation to measure from.")
    poly = _polygon(pts)
    dem, mask = _grid(path, poly, max_cells)
    base = _area_from(dem, mask, poly)
    z = dem.z
    rows, cols = np.nonzero(mask)
    xs, ys = dem.rc_to_xy(rows, cols)
    flat = float("nan")
    if reference in ("edge", "edge_mean"):
        ring = np.asarray(poly.exterior.coords)
        bxy, _ = densify(ring, _step(dem))
        bz = sample(dem, bxy[:, 0], bxy[:, 1])
        ok = np.isfinite(bz)
        if ok.sum() < 3:
            raise ValueError("There is not enough elevation data along the outline.")
        bxy, bz = bxy[ok], bz[ok]
        if reference == "edge_mean":
            flat = float(bz.mean())
            ref = np.full(len(xs), flat)
        else:
            from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
            ref = LinearNDInterpolator(bxy, bz)(xs, ys)
            gap = ~np.isfinite(ref)
            if gap.any():
                ref[gap] = NearestNDInterpolator(bxy, bz)(xs[gap], ys[gap])
    elif reference == "level":
        flat = float(level)
        ref = np.full(len(xs), flat)
    else:
        flat = base.z_min
        ref = np.full(len(xs), flat)
    d = z[rows, cols] - ref
    dz = np.full(z.shape, np.nan)
    dz[rows, cols] = d
    ca = abs(dem.transform.a * dem.transform.e)
    ok = np.isfinite(d)
    up, dn = ok & (d > 0), ok & (d < 0)
    text = REFERENCES[reference]
    if math.isfinite(flat):
        text += f" ({flat:.2f} m)"
    return VolumeResult(**asdict(base), reference=reference, reference_text=text, level=flat,
                        cut_m3=float(d[up].sum() * ca), fill_m3=float(-d[dn].sum() * ca),
                        cut_area=float(up.sum() * ca), fill_area=float(dn.sum() * ca),
                        max_cut=float(d[up].max()) if up.any() else 0.0,
                        max_fill=float(-d[dn].min()) if dn.any() else 0.0, dz=dz, transform=dem.transform,
                        ground=dem.z, crs_wkt=dem.crs_wkt)


# ============================================================================ hauling
def truck_trips(volume_m3: float, swell_pct: float = DEFAULT_SWELL, sizes=TRUCKS) -> tuple[float, list]:
    """Loose volume after digging and the trips needed per truck size: (loose_m3, [(size, trips)])."""
    loose = max(0.0, volume_m3) * (1 + swell_pct / 100.0)
    return loose, [(s, int(math.ceil(loose / s - 1e-9)) if loose > 0 else 0) for s in sizes if s > 0]


def overlay_rgba(dz: np.ndarray, scale_m: float | None = None) -> np.ndarray:
    """Red where ground is above the reference (dig), blue where below (fill)."""
    out = np.zeros(dz.shape + (4,), np.uint8)
    ok = np.isfinite(dz)
    if not ok.any():
        return out
    s = scale_m or max(0.2, float(np.nanpercentile(np.abs(dz[ok]), 95)))
    a = np.clip(np.abs(np.nan_to_num(dz)) / s, 0, 1)
    cut, fill = ok & (dz > 0), ok & (dz < 0)
    out[cut] = (196, 40, 32, 0)
    out[fill] = (30, 90, 170, 0)
    out[..., 3] = np.where(ok, (70 + 150 * a).astype(np.uint8), 0)
    return out


# ============================================================================ saved measurements
KINDS = {"point": "Point", "height": "Height difference", "path": "Path", "area": "Area", "volume": "Volume"}
SOURCES = {"dtm": "bare ground (DTM)", "dsm": "ground with trees, buildings and piles (DSM)"}

# result key -> (label, unit) in display order
RESULT_LABELS = {
    "elevation_m": ("Elevation", "m"),
    "z_a_m": ("Height at A", "m"), "z_b_m": ("Height at B", "m"), "rise_m": ("Rise A→B", "m"),
    "horizontal_m": ("Horizontal distance", "m"), "slope_pct": ("Slope", "%"),
    "length_m": ("Length (flat)", "m"), "length_3d_m": ("Length along the ground", "m"),
    "z_start_m": ("Start height", "m"), "z_end_m": ("End height", "m"),
    "climb_m": ("Total climb", "m"), "descent_m": ("Total descent", "m"), "steepest_pct": ("Steepest part", "%"),
    "perimeter_m": ("Perimeter", "m"), "area_m2": ("Flat area", "m²"), "surface_area_m2": ("Surface area", "m²"),
    "z_min_m": ("Lowest", "m"), "z_max_m": ("Highest", "m"), "z_mean_m": ("Average height", "m"),
    "reference": ("Measured from", ""), "cut_m3": ("Dig (cut)", "m³"), "fill_m3": ("Fill", "m³"),
    "net_m3": ("Net (cut − fill)", "m³"), "total_m3": ("Total moved", "m³"),
    "swell_pct": ("Soil loosening", "%"), "loose_m3": ("Loose volume to haul", "m³"),
}


@dataclass
class Measurement:
    kind: str
    name: str
    points: list                          # [[x, y], ...] map coordinates
    source: str = "dtm"
    settings: dict = field(default_factory=dict)
    results: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def summary(self) -> str:
        r = self.results
        if self.kind == "point":
            return f"{r.get('elevation_m', float('nan')):.2f} m"
        if self.kind == "height":
            return f"{r.get('rise_m', 0):+.2f} m"
        if self.kind == "path":
            return f"{r.get('length_m', 0):,.1f} m"
        if self.kind == "area":
            return f"{r.get('area_m2', 0):,.0f} m²"
        return f"{r.get('net_m3', 0):,.0f} m³ net"


def results_dict(kind: str, res, swell_pct: float = DEFAULT_SWELL) -> dict:
    """Plain numbers for saving and exporting."""
    if kind == "point":
        return {"elevation_m": round(res, 3)}
    if isinstance(res, HeightResult):
        return {"z_a_m": round(res.z_a, 3), "z_b_m": round(res.z_b, 3), "rise_m": round(res.rise, 3),
                "horizontal_m": round(res.horizontal, 2), "slope_pct": round(res.slope_pct, 2)}
    if isinstance(res, PathResult):
        return {"length_m": round(res.length_2d, 2), "length_3d_m": round(res.length_3d, 2),
                "z_start_m": round(res.z_start, 3), "z_end_m": round(res.z_end, 3), "z_min_m": round(res.z_min, 3),
                "z_max_m": round(res.z_max, 3), "climb_m": round(res.climb, 2), "descent_m": round(res.descent, 2),
                "steepest_pct": round(res.steepest_pct, 1)}
    d = {"perimeter_m": round(res.perimeter_2d, 2), "area_m2": round(res.area_2d, 2),
         "surface_area_m2": round(res.area_3d, 2), "z_min_m": round(res.z_min, 3), "z_max_m": round(res.z_max, 3),
         "z_mean_m": round(res.z_mean, 3)}
    if isinstance(res, VolumeResult):
        loose, _ = truck_trips(res.net_m3, swell_pct)
        d.update(reference=res.reference_text, cut_m3=round(res.cut_m3, 2), fill_m3=round(res.fill_m3, 2),
                 net_m3=round(res.net_m3, 2), total_m3=round(res.total_m3, 2), swell_pct=swell_pct,
                 loose_m3=round(loose, 2))
    return d


def store_path(job_dir: str) -> str:
    return os.path.join(job_dir, "measurements.json")


def load_all(job_dir: str) -> list[Measurement]:
    p = store_path(job_dir)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for d in raw.get("measurements", []):
        try:
            out.append(Measurement(**{k: v for k, v in d.items() if k in Measurement.__dataclass_fields__}))
        except TypeError:
            continue
    return out


def save_all(job_dir: str, items: list[Measurement]) -> None:
    p = store_path(job_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "measurements": [asdict(m) for m in items]}, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def _geometry(m: Measurement):
    from shapely.geometry import LineString, Point, Polygon
    p = [tuple(map(float, q)) for q in m.points]
    if m.kind == "point":
        return Point(p[0])
    if m.kind in ("height", "path"):
        return LineString(p)
    return Polygon(p)


def export_measurements(items: list[Measurement], out_dir: str, base_name: str, src_crs: str, dst_crs: str,
                        formats: list[str], lonlat, title: str) -> list[str]:
    """Saved measurements -> points / lines / areas layers in the chosen formats, plus a CSV table."""
    import csv
    from . import exporters as ex
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, base_name.replace(" ", "_"))
    groups = {"points": [], "lines": [], "areas": []}
    for m in items:
        g = "points" if m.kind == "point" else "lines" if m.kind in ("height", "path") else "areas"
        props = {"name": m.name, "kind": KINDS.get(m.kind, m.kind), "heights": m.source.upper(),
                 "created": m.created}
        props.update(m.results)
        groups[g].append(ex.Feature(_geometry(m), props))
    files: list[str] = []
    for fmt in formats:
        ext = ex.VECTOR_FORMATS[fmt][1]
        for g, feats in groups.items():
            if not feats:
                continue
            out = base + ext if fmt == "gpkg" else f"{base}_{g}{ext}"
            files += ex.write_vector(feats, out, fmt, src_crs, dst_crs, layer=g, lonlat=lonlat,
                                     dxf_layer_field="name", append=(fmt == "gpkg"))
    # table (coordinates in the chosen system)
    geoms = ex._transform_geoms([_geometry(m) for m in items], src_crs, dst_crs, lonlat)
    keys = [k for k in RESULT_LABELS if any(k in m.results for m in items)]
    csv_path = base + "_measurements.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["name", "kind", "heights from", "created"]
                   + [f"{RESULT_LABELS[k][0]} ({RESULT_LABELS[k][1]})" if RESULT_LABELS[k][1] else RESULT_LABELS[k][0]
                      for k in keys] + ["geometry (WKT)"])
        for m, g in zip(items, geoms):
            w.writerow([m.name, KINDS.get(m.kind, m.kind), SOURCES.get(m.source, m.source), m.created]
                       + [m.results.get(k, "") for k in keys] + [g.wkt])
    files = list(dict.fromkeys(files)) + [csv_path]
    readme = base + "_README.txt"
    notes = ["Measurements from a drone elevation model: for planning and estimates only, not a survey.",
             "Heights are as recorded by the drone job (see the job README for the vertical datum).",
             "Volumes: 'Dig (cut)' is ground above the chosen reference, 'Fill' is space below it. "
             "Net = cut - fill. Loose volume adds the soil loosening factor for hauling."]
    for fmt in formats:
        n = ex.format_note(fmt, dst_crs)
        if n and n not in notes:
            notes.append(n)
    ex.write_export_readme(readme, title, dst_crs, lonlat, files, notes)
    return files + [readme]
