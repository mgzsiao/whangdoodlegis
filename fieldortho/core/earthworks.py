"""Earthworks for a lot / building pad and for piles, holes or landslide deposits.

Lot or pad: the ground inside an outline is made level (or gently sloped so rain drains) at a chosen or
balanced height. Outside the outline the work either stops at a wall, or meets the existing ground on
sloped sides (cut slopes rise, fill slopes fall away from the pad edge at H:V ratios).

All volumes are "bank" volumes (as measured in the ground). Hauling uses two soil factors:
  swell  - dug soil takes more space when loose (truck loads)
  shrink - fill packed down needs more soil than the space it fills
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np

from . import measure as ms
from .terrain import Dem

MAX_REACH = 200.0          # metres: furthest a side slope is followed beyond the outline
DEFAULT_SHRINK = 10.0      # %: packed fill needs about this much more soil
PILE_SWELL = 0.0           # %: a stockpile is already loose

# side-slope presets: key -> (label, cut H:V, fill H:V, explanation)
SOILS = {
    "firm": ("Firm soil (clay, hard ground)", 1.0, 1.5, "Cut sides 1 : 1, fill sides 1.5 : 1"),
    "loose": ("Loose or sandy soil", 2.0, 2.0, "Gentle sides, 2 : 1 for cut and fill"),
    "rock": ("Rock", 0.5, 1.5, "Steep rock cut 0.5 : 1, fill sides 1.5 : 1"),
    "custom": ("My own slopes", None, None, "Type the slopes below"),
}

COMPASS = [("north", 0), ("north-east", 45), ("east", 90), ("south-east", 135), ("south", 180),
           ("south-west", 225), ("west", 270), ("north-west", 315)]


def compass_name(az: float) -> str:
    return COMPASS[int(((az % 360) + 22.5) // 45) % 8][0]


@dataclass
class PadDesign:
    level_mode: str = "balanced"     # balanced | fixed
    level: float = float("nan")      # finished height at the centre of the outline (fixed mode)
    slope_pct: float = 0.0           # 0 = level; e.g. 1 = drops 1 cm per metre
    flow_deg: float | None = None    # direction rain water flows (0 = north, clockwise); None = downhill
    sides: str = "slopes"            # slopes | walls
    soil: str = "firm"
    cut_ratio: float = 1.0           # horizontal metres per 1 m height
    fill_ratio: float = 1.5
    swell_pct: float = ms.DEFAULT_SWELL
    shrink_pct: float = DEFAULT_SHRINK
    truck_m3: float = 0.0            # an extra truck size (0 = none)


@dataclass
class Haul:
    cut: float
    fill: float
    swell_pct: float
    shrink_pct: float
    sizes: tuple = ms.TRUCKS
    what: str = "pad"                   # pad | pile (only changes the wording)

    @property
    def needed(self) -> float:          # soil (in the ground) needed to make the fill
        return self.fill * (1 + self.shrink_pct / 100)

    @property
    def balance(self) -> float:         # + extra soil to haul away, - soil to bring in
        return self.cut - self.needed

    @property
    def loose(self) -> float:
        return abs(self.balance) * (1 + self.swell_pct / 100)

    @property
    def trips(self) -> list:
        return [(s, int(math.ceil(self.loose / s - 1e-9)) if self.loose > 0 else 0) for s in self.sizes if s > 0]

    def sentence(self) -> str:
        trips = " or ".join(f"~{n:,} trips with {s:g} m³ trucks" for s, n in self.trips)
        if self.what == "pile":
            if self.balance >= 0:
                return f"Hauling it all away: {self.loose:,.0f} m³ loose, {trips}."
            return f"Filling it needs about {-self.balance:,.0f} m³ of soil ({self.loose:,.0f} m³ loose): {trips}."
        if abs(self.balance) < max(1.0, 0.01 * (self.cut + self.fill)):
            return "The dug soil is just enough for the fill: almost nothing needs to be hauled."
        trips = " or ".join(f"~{n:,} trips with {s:g} m³ trucks" for s, n in self.trips)
        if self.balance > 0:
            return f"About {self.balance:,.0f} m³ of extra soil ({self.loose:,.0f} m³ loose) must be hauled away: {trips}."
        return f"About {-self.balance:,.0f} m³ more soil ({self.loose:,.0f} m³ loose) must be brought in: {trips}."


def make_haul(cut, fill, swell_pct, shrink_pct=0.0, extra_truck=0.0, what="pad") -> Haul:
    sizes = tuple(ms.TRUCKS) + ((extra_truck,) if extra_truck and extra_truck > 0 else ())
    return Haul(float(cut), float(fill), float(swell_pct), float(shrink_pct), sizes, what)


REF_SHORT = {"edge": "the ground around its edge", "edge_mean": "the average height of its edge",
             "level": "a fixed height", "lowest": "the lowest ground inside"}


@dataclass
class Takeoff:
    """Result of a plan, the same shape for a lot/pad and a pile/hole."""
    kind: str                         # pad | pile
    area: ms.AreaResult               # the outline
    cut: float
    fill: float
    cut_area: float
    fill_area: float
    max_cut: float
    max_fill: float
    works_area: float                 # ground touched, sides included
    haul: Haul
    inputs: list                      # [(label, value)] for the report
    dz: np.ndarray = field(repr=False)          # ground - finished (+ dig, - fill), NaN where untouched
    ground: np.ndarray = field(repr=False)
    finished: np.ndarray = field(repr=False)    # finished ground (pad) / reference surface (pile)
    transform: object = field(repr=False)
    crs_wkt: str = ""
    pad_mask: np.ndarray | None = field(default=None, repr=False)
    pad_cut: float = float("nan")
    pad_fill: float = float("nan")
    side_cut: float = float("nan")
    side_fill: float = float("nan")
    level: float = float("nan")       # finished height at the outline's centre
    level_low: float = float("nan")
    level_high: float = float("nan")
    slope_pct: float = 0.0
    flow_deg: float | None = None
    reference_text: str = ""
    reaches_edge: bool = False
    no_data_pct: float = 0.0

    @property
    def net(self) -> float:
        return self.cut - self.fill

    @property
    def total(self) -> float:
        return self.cut + self.fill

    @property
    def cell(self) -> float:
        return abs(self.transform.a)

    def headline(self) -> str:
        a = self.area.area_2d
        if self.kind == "pile":
            if self.cut >= self.fill:
                return (f"The pile or mound inside the {a:,.0f} m² outline holds about {self.cut:,.0f} m³ "
                        f"(measured from {self.reference_text}).")
            return (f"The hole inside the {a:,.0f} m² outline holds about {self.fill:,.0f} m³ "
                    f"(measured from {self.reference_text}).")
        if self.slope_pct > 0:
            shape = (f"with a gentle {self.slope_pct:g}% slope so rain runs toward the "
                     f"{compass_name(self.flow_deg or 0)} (finished ground {self.level_high:.2f} m falling to "
                     f"{self.level_low:.2f} m)")
        else:
            shape = f"level at {self.level:.2f} m"
        return f"To finish the {a:,.0f} m² lot {shape}, dig about {self.cut:,.0f} m³ and fill about " \
               f"{self.fill:,.0f} m³."


# ============================================================================ pad design
def _coords(dem: Dem):
    h, w = dem.shape
    t = dem.transform
    xs = t.c + (np.arange(w) + 0.5) * t.a
    ys = t.f + (np.arange(h) + 0.5) * t.e
    return np.meshgrid(xs, ys)


def downhill_azimuth(dem: Dem, mask: np.ndarray) -> float:
    """Direction the ground inside the mask falls (best-fit plane), 0 = north, clockwise."""
    X, Y = _coords(dem)
    ok = mask & np.isfinite(dem.z)
    if ok.sum() < 3:
        return 0.0
    idx = np.flatnonzero(ok)
    if idx.size > 200_000:
        idx = idx[:: idx.size // 200_000 + 1]
    x, y, z = X.ravel()[idx], Y.ravel()[idx], dem.z.ravel()[idx]
    A = np.column_stack([x - x.mean(), y - y.mean(), np.ones_like(x)])
    (a, b, _c), *_ = np.linalg.lstsq(A, z, rcond=None)
    if math.hypot(a, b) < 1e-6:
        return 0.0
    return math.degrees(math.atan2(-a, -b)) % 360


def design_pad(path: str, pts, d: PadDesign, max_cells: int = ms.MAX_CELLS) -> Takeoff:
    from scipy.ndimage import distance_transform_edt
    from rasterio.features import geometry_mask
    if d.level_mode == "fixed" and not math.isfinite(d.level):
        raise ValueError("Type the finished height.")
    poly = ms._polygon(pts)
    dem0, mask0 = ms._grid(path, poly, max_cells)
    outline = ms._area_from(dem0, mask0, poly)
    cx, cy = poly.centroid.x, poly.centroid.y
    flow = d.flow_deg if d.flow_deg is not None else downhill_azimuth(dem0, mask0)
    radius = max(math.dist((cx, cy), p) for p in poly.exterior.coords)
    drop = d.slope_pct / 100 * radius
    slopes = d.sides == "slopes"
    if slopes:
        if d.cut_ratio <= 0 or d.fill_ratio <= 0:
            raise ValueError("Side slopes must be more than 0.")
        lo, hi = outline.z_min, outline.z_max
        if d.level_mode == "fixed":
            lo, hi = min(lo, d.level), max(hi, d.level)
        reach = min(MAX_REACH, (hi - lo + 2 * drop + 2) * max(d.cut_ratio, d.fill_ratio) + 2 * dem0.cell)
    else:
        reach = 0.0
    dem = ms.read_area(path, poly.buffer(reach).bounds, max_cells) if reach > 0 else dem0
    z = dem.z
    mask = geometry_mask([poly], out_shape=dem.shape, transform=dem.transform, invert=True)
    if not mask.any():
        mask = geometry_mask([poly], out_shape=dem.shape, transform=dem.transform, invert=True, all_touched=True)
    X, Y = _coords(dem)
    if d.slope_pct > 0:
        ux, uy = math.sin(math.radians(flow)), math.cos(math.radians(flow))
        offset = -d.slope_pct / 100 * ((X - cx) * ux + (Y - cy) * uy)
    else:
        offset = np.zeros(dem.shape)
    ok = np.isfinite(z)
    ca = abs(dem.transform.a * dem.transform.e)
    ins = mask & ok
    g_in, off_in = z[ins], offset[ins]
    if slopes:
        dist, (ir, ic) = distance_transform_edt(~mask, sampling=(abs(dem.transform.e), abs(dem.transform.a)),
                                                return_indices=True)
        # distances are to the nearest pad cell centre: the pad edge is half a cell nearer
        dist = np.maximum(dist - 0.5 * dem.cell, 0.0)
        outs = ~mask & ok & (dist <= reach)
        g_out, d_out = z[outs], dist[outs]
        off_out = offset[ir[outs], ic[outs]]
    else:
        outs = np.zeros(dem.shape, bool)
        g_out = d_out = off_out = np.zeros(0)

    def volumes(L):
        di = g_in - (L + off_in)
        if slopes:
            e = L + off_out
            fo = np.clip(g_out, e - d_out / d.fill_ratio, e + d_out / d.cut_ratio)
            do = g_out - fo
        else:
            do = np.zeros(0)
        return (di[di > 0].sum() * ca, -di[di < 0].sum() * ca, do[do > 0].sum() * ca, -do[do < 0].sum() * ca)

    if d.level_mode == "balanced":
        k = 1 + d.shrink_pct / 100
        a, b = float(np.min(g_in - off_in)) - 1, float(np.max(g_in - off_in)) + 1
        for _ in range(60):
            m = (a + b) / 2
            pc, pf, sc, sf = volumes(m)
            if (pc + sc) - (pf + sf) * k > 0:
                a = m
            else:
                b = m
            if b - a < 2e-4:
                break
        L = round((a + b) / 2, 3)
    else:
        L = float(d.level)
    pc, pf, sc, sf = volumes(L)

    finished = np.full(dem.shape, np.nan)
    finished[ins] = L + off_in
    if slopes:
        e = L + off_out
        finished[outs] = np.clip(g_out, e - d_out / d.fill_ratio, e + d_out / d.cut_ratio)
    dz = z - finished
    touched = ins | (outs & (np.abs(np.nan_to_num(dz)) > 1e-3))
    dz[~touched] = np.nan
    finished[~touched] = np.nan
    border = np.zeros(dem.shape, bool)
    border[[0, -1], :] = True
    border[:, [0, -1]] = True
    edge = slopes and bool((touched & ~mask & border).any())
    up, dn = touched & (dz > 0), touched & (dz < 0)
    pad_vals = L + off_in
    soil_label = SOILS.get(d.soil, SOILS["custom"])[0]
    inputs = [("Heights from", ""), ("Finished height", "balanced: dig = fill" if d.level_mode == "balanced"
                                     else f"{L:.2f} m (typed)"),
              ("Finished height at the centre", f"{L:.2f} m"),
              ("Drainage slope", f"{d.slope_pct:g}% toward the {compass_name(flow)}" if d.slope_pct > 0
               else "none (level)"),
              ("Edges", f"sloped sides, {soil_label}: cut {d.cut_ratio:g} : 1, fill {d.fill_ratio:g} : 1 (H : V)"
               if slopes else "straight walls (retaining wall), no side slopes"),
              ("Soil loosening when dug (swell)", f"{d.swell_pct:g}%"),
              ("Extra soil for packed fill (shrink)", f"{d.shrink_pct:g}%")]
    cut, fill = pc + sc, pf + sf
    return Takeoff("pad", outline, cut, fill, float(up.sum() * ca), float(dn.sum() * ca),
                   float(dz[up].max()) if up.any() else 0.0, float(-dz[dn].min()) if dn.any() else 0.0,
                   float(touched.sum() * ca), make_haul(cut, fill, d.swell_pct, d.shrink_pct, d.truck_m3), inputs,
                   dz, z, finished, dem.transform, dem.crs_wkt, mask, pc, pf, sc, sf, L,
                   float(pad_vals.min()) if pad_vals.size else L, float(pad_vals.max()) if pad_vals.size else L,
                   d.slope_pct, flow if d.slope_pct > 0 else None, "", edge, outline.no_data_pct)


def pile_takeoff(path: str, pts, reference: str = "edge", level: float | None = None,
                 swell_pct: float = PILE_SWELL, truck_m3: float = 0.0, max_cells: int = ms.MAX_CELLS) -> Takeoff:
    v = ms.measure_volume(path, pts, reference, level, max_cells)
    area = ms.AreaResult(v.perimeter_2d, v.perimeter_3d, v.area_2d, v.area_3d, v.z_min, v.z_max, v.z_mean,
                         v.no_data_pct, v.cell)
    ref_surface = v.ground - v.dz
    from rasterio.features import geometry_mask
    mask = geometry_mask([ms._polygon(pts)], out_shape=v.dz.shape, transform=v.transform, invert=True)
    short = REF_SHORT.get(reference, reference) + (f" ({v.level:.2f} m)" if v.level == v.level else "")
    inputs = [("Heights from", ""), ("Measured from", v.reference_text),
              ("Soil loosening for hauling (swell)", f"{swell_pct:g}%")]
    # a pile is hauled away (cut); a hole is filled (fill)
    return Takeoff("pile", area, v.cut_m3, v.fill_m3, v.cut_area, v.fill_area, v.max_cut, v.max_fill,
                   v.area_2d, make_haul(v.cut_m3, v.fill_m3, swell_pct, 0.0, truck_m3, "pile"), inputs, v.dz,
                   v.ground, ref_surface, v.transform, v.crs_wkt, mask, reference_text=short,
                   no_data_pct=v.no_data_pct)


# ============================================================================ grid of spot levels
def grid_points(t: Takeoff, spacing: float = 5.0) -> list[tuple]:
    """Spot levels every `spacing` metres over the area worked: (id, E, N, ground, finished, cut+/fill-)."""
    h, w = t.dz.shape
    tr = t.transform
    x0, x1 = tr.c, tr.c + w * tr.a
    y1, y0 = tr.f, tr.f + h * tr.e
    xs = np.arange(math.ceil(x0 / spacing) * spacing, x1, spacing)
    ys = np.arange(math.floor(y1 / spacing) * spacing, y0, -spacing)
    rows = []
    n = 0
    gdem = Dem(t.ground, tr, t.crs_wkt, "")
    fdem = Dem(t.finished, tr, t.crs_wkt, "")
    for y in ys:
        for x in xs:
            c = int((x - tr.c) / tr.a)
            r = int((y - tr.f) / tr.e)
            if not (0 <= r < h and 0 <= c < w) or not np.isfinite(t.dz[r, c]):
                continue
            g = float(ms.sample(gdem, [x], [y])[0])
            f = float(ms.sample(fdem, [x], [y])[0])
            if not (math.isfinite(g) and math.isfinite(f)):
                continue
            n += 1
            rows.append((n, round(float(x), 3), round(float(y), 3), round(g, 3), round(f, 3), round(g - f, 3)))
    return rows


# ============================================================================ shapes from files / typed
def read_shapes(path: str, dst_crs_wkt: str, site_lonlat=None) -> list[dict]:
    """Lines and polygons in a GIS / GPS / CAD file -> [{name, points, closed}] in the map's coordinates."""
    import pyogrio
    import shapely
    from pyogrio.raw import read as ogr_read
    from . import crs as crsmod
    src = path
    if path.lower().endswith(".kmz"):
        import zipfile
        with zipfile.ZipFile(path) as z:
            kml = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
        if not kml:
            raise ValueError("This KMZ file has no KML inside.")
        src = f"/vsizip/{path}/{kml}"
    out = []
    for layer, _gt in pyogrio.list_layers(src):
        try:
            meta, _fids, geoms, fields = ogr_read(src, layer=layer)
        except Exception:
            continue
        names = list(meta.get("fields", []))
        low = [f.lower() for f in names]
        ncol = next((low.index(k) for k in ("name", "label", "layer", "description", "id") if k in low), None)
        crs = meta.get("crs")
        t = None
        if crs:
            from pyproj import CRS
            s, dcrs = CRS.from_user_input(crs), CRS.from_wkt(dst_crs_wkt)
            if not s.equals(dcrs):
                t = crsmod.transformer(s.to_wkt(), dst_crs_wkt, *(site_lonlat or (None, None)))
        for i, wkb in enumerate(geoms):
            if wkb is None:
                continue
            g = shapely.from_wkb(wkb)
            parts = list(g.geoms) if hasattr(g, "geoms") else [g]
            for p in parts:
                if p.geom_type == "Polygon":
                    xy, closed = np.asarray(p.exterior.coords)[:-1, :2], True
                elif p.geom_type == "LineString":
                    xy = np.asarray(p.coords)[:, :2]
                    closed = len(xy) > 3 and np.allclose(xy[0], xy[-1])
                    if closed:
                        xy = xy[:-1]
                else:
                    continue
                if len(xy) < 2:
                    continue
                if t is not None:
                    x, y = t.transform(xy[:, 0], xy[:, 1])
                    xy = np.column_stack([x, y])
                label = str(fields[ncol][i]) if ncol is not None and fields[ncol][i] not in (None, "") else \
                    f"{layer} #{i + 1}"
                out.append({"name": label, "points": [tuple(map(float, q)) for q in xy], "closed": closed,
                            "georeferenced": crs is not None})
    return out


def parse_points(text: str, src_code: str, dst_crs_wkt: str, swap: bool = False,
                 site_lonlat=None) -> list[tuple[float, float]]:
    """Typed / pasted coordinates, one point per line: 'easting, northing' (or longitude, latitude;
    degrees may be typed as 8°52'30\"N). swap=True when lines are 'northing, easting' / 'lat, lon'."""
    from . import crs as crsmod
    from .georef import parse_angle
    geo = crsmod.is_geographic(src_code)
    pts = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        toks = [t for t in re.split(r"[,;\t]+", line) if t.strip()] if re.search(r"[,;\t]", line) else line.split()
        if len(toks) < 2:
            raise ValueError(f"Line {n}: needs two numbers ({line}).")
        if len(toks) > 2 and not geo:            # 'id, E, N' or 'E, N, Z'
            nums = []
            for t in toks:
                try:
                    nums.append(float(t.replace(" ", "")))
                except ValueError:
                    pass
            if len(nums) < 2:
                raise ValueError(f"Line {n}: could not read the numbers ({line}).")
            toks = [str(v) for v in (nums[-2:] if len(nums) == 3 and abs(nums[0]) < 1e5 and abs(nums[1]) > 1e3
                                     else nums[:2])]
        a, b = toks[0].strip(), toks[1].strip()
        if swap:
            a, b = b, a
        try:
            x, y = (parse_angle(a), parse_angle(b)) if geo else (float(a.replace(" ", "")), float(b.replace(" ", "")))
        except ValueError:
            raise ValueError(f"Line {n}: could not read the numbers ({line}).")
        pts.append((x, y))
    if not pts:
        raise ValueError("No points typed.")
    lon, lat = pts[0] if geo else (site_lonlat or (None, None))
    xs, ys = crsmod.transform([p[0] for p in pts], [p[1] for p in pts], src_code, dst_crs_wkt, lon, lat)
    return [(float(x), float(y)) for x, y in zip(xs, ys)]


# ============================================================================ saved plans
TYPES = {"pad": "Level a lot or pad", "pile": "Pile, hole or deposit", "road": "Road, path or canal"}


def plans_dir(job_dir: str) -> str:
    return os.path.join(job_dir, "Earthworks")


def safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:80] or "plan"


def save_plan(job_dir: str, plan: dict) -> str:
    d = plans_dir(job_dir)
    os.makedirs(d, exist_ok=True)
    plan = dict(plan, version=1, updated=datetime.now().isoformat(timespec="seconds"), job_dir=job_dir)
    plan.setdefault("created", plan["updated"])
    p = os.path.join(d, safe(plan["name"]) + ".plan.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)
    return p


def load_plan(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    if plan.get("type") not in TYPES or len(plan.get("points") or []) < (2 if plan.get("type") == "road" else 3):
        raise ValueError("This is not a valid earthworks plan file.")
    return plan


def design_from(plan: dict) -> PadDesign:
    raw = plan.get("design") or {}
    return PadDesign(**{k: v for k, v in raw.items() if k in PadDesign.__dataclass_fields__})


def run_plan(plan: dict, dem_path: str):
    """Takeoff for a lot / pile, road.RoadResult for a road."""
    pts = [tuple(p) for p in plan["points"]]
    if plan["type"] == "road":
        from . import road
        return road.design_road(dem_path, pts, road.design_from(plan))
    if plan["type"] == "pad":
        return design_pad(dem_path, pts, design_from(plan))
    p = plan.get("pile") or {}
    return pile_takeoff(dem_path, pts, p.get("reference", "edge"), p.get("level"), p.get("swell_pct", PILE_SWELL),
                        p.get("truck_m3", 0.0))


def plan_dict(**kw) -> dict:
    """{type, name, source, points, design|pile} with design dataclass flattened."""
    d = dict(kw)
    if isinstance(d.get("design"), PadDesign):
        d["design"] = asdict(d["design"])
    if d.get("road") is not None and not isinstance(d["road"], dict):
        d["road"] = asdict(d["road"])
    return d
