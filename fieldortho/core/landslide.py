"""Landslide scenario model: where can the selected slope fail, and how far can it run?

1. Slope stability - infinite-slope model (Skempton & DeLory 1957), cell by cell:

       FS = [ c' + (gamma - m*gamma_w) * z * cos^2(b) * tan(phi') ] / [ gamma * z * sin(b) * cos(b) ]

   c' cohesion (kPa), phi' friction angle, gamma soil unit weight (kN/m3),
   gamma_w = 9.81 kN/m3, z soil depth (m), b slope angle, m = wetness (0 dry ... 1 fully
   saturated, i.e. groundwater at the surface during prolonged rain). FS < 1 = fails.

2. Runout - energy-line ('Fahrboeschung' / travel-angle) model (Heim 1932; Corominas 1996),
   as used by Flow-R and other regional tools: moving mass keeps a kinetic energy head
       E_next = E + (z_here - z_next) - distance * tan(travel_angle)
   and stops where E reaches 0; E is capped by a maximum velocity. Flow follows the
   fall line (the one or two grid directions bracketing steepest descent) and spreads freely
   on flat ground (< 4 deg), where it decelerates and deposits.
   Velocity estimate v = sqrt(2 g E).

Scenario tool, not a site-specific geotechnical design. Typical travel angles:
rock fall / small slides 30-35 deg, soil slides 20-30 deg, debris flows 11-20 deg.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np

from .terrain import Dem, slope_aspect

G = 9.81
GAMMA_W = 9.81

SOILS = {  # name: (cohesion kPa, friction deg, unit weight kN/m3)
    "Clay (stiff)": (15.0, 22.0, 18.5),
    "Silty / clayey soil (residual)": (8.0, 28.0, 18.0),
    "Sandy soil": (2.0, 32.0, 19.0),
    "Gravelly soil / weathered rock": (10.0, 36.0, 21.0),
    "Loose colluvium / fill": (1.0, 27.0, 17.0),
}


@dataclass
class Params:
    soil_depth: float = 2.0         # m (vertical)
    cohesion: float = 8.0           # kPa
    friction: float = 28.0          # degrees
    unit_weight: float = 18.0       # kN/m3
    wetness: float = 0.5            # 0..1 (saturated fraction of soil depth)
    travel_angle: float = 22.0      # degrees
    max_velocity: float = 20.0      # m/s
    force_release: bool = False     # treat whole selection (slope > 10 deg) as failing


@dataclass
class LandslideResult:
    fs: np.ndarray                  # factor of safety (NaN outside selection)
    release: np.ndarray             # bool
    energy: np.ndarray              # kinetic energy head (m); NaN = not reached
    affected: np.ndarray            # bool (release + runout)
    stats: dict = field(default_factory=dict)
    source: np.ndarray | None = None  # flat index of the source cell feeding each runout cell

    @property
    def velocity(self) -> np.ndarray:
        return np.sqrt(2 * G * np.where(np.isfinite(self.energy), self.energy, 0.0))


def factor_of_safety(slope_deg: np.ndarray, p: Params) -> np.ndarray:
    b = np.radians(np.clip(slope_deg, 0.0, 89.0))
    z, g, m = p.soil_depth, p.unit_weight, min(max(p.wetness, 0.0), 1.0)
    num = p.cohesion + (g - m * GAMMA_W) * z * np.cos(b) ** 2 * math.tan(math.radians(p.friction))
    den = g * z * np.sin(b) * np.cos(b)
    with np.errstate(divide="ignore", invalid="ignore"):
        fs = np.where(den > 1e-9, num / den, np.inf)
    return fs


def critical_wetness(slope_deg: float, p: Params) -> float | None:
    """Wetness at which a slope of this angle reaches FS = 1 (None if never / always)."""
    b = math.radians(slope_deg)
    t = math.tan(math.radians(p.friction))
    a = p.soil_depth * math.cos(b) ** 2 * t
    if a <= 0:
        return None
    # c + (g - m gw) a = g z sin cos  ->  m = (c + g a - g z s c) / (gw a)
    m = (p.cohesion + p.unit_weight * a - p.unit_weight * p.soil_depth * math.sin(b) * math.cos(b)) / (GAMMA_W * a)
    return m if 0 <= m <= 1 else None


_NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def runout(dem: Dem, release: np.ndarray, p: Params, flat_deg: float = 4.0,
           max_cells: int = 3_000_000, return_source: bool = False):
    """Energy-line runout from release cells. Returns kinetic energy head (m), NaN = not reached
    (and, if asked, the flat index of the source cell whose mass reached each cell)."""
    z = dem.z
    H, W = z.shape
    slope, aspect = slope_aspect(dem)
    mu = math.tan(math.radians(p.travel_angle))
    emax = p.max_velocity ** 2 / (2 * G)
    cx, cy = abs(dem.transform.a), abs(dem.transform.e)
    E = np.full(z.shape, -1.0)
    src = np.full(z.shape, -1, dtype=np.int64)
    heap = []
    rr, cc = np.nonzero(release & np.isfinite(z))
    for r, c in zip(rr.tolist(), cc.tolist()):
        E[r, c] = 0.0
        src[r, c] = r * W + c
        heap.append((-0.0, r, c))
    heapq.heapify(heap)
    # direction of each neighbour in the same convention as slope_aspect(): x = columns (east),
    # y = rows (south)
    nb_ang = [math.atan2(dr, dc) for dr, dc in _NB]
    nb_dist = [math.hypot(dr * cy, dc * cx) for dr, dc in _NB]
    # on slopes: only the one or two grid directions bracketing the true downslope direction
    # (D-infinity principle) - follows the fall line without artificial sideways fanning.
    lim = math.radians(45.0) - 1e-6
    steps = 0
    while heap and steps < max_cells:
        negE, r, c = heapq.heappop(heap)
        e = -negE
        if e < E[r, c] - 1e-9:
            continue
        steps += 1
        zr = z[r, c]
        flat = slope[r, c] < flat_deg
        asp = aspect[r, c]
        for k, (dr, dc) in enumerate(_NB):
            r2, c2 = r + dr, c + dc
            if r2 < 0 or c2 < 0 or r2 >= H or c2 >= W:
                continue
            z2 = z[r2, c2]
            if z2 != z2:  # NaN
                continue
            if not flat:
                d_ang = abs((nb_ang[k] - asp + math.pi) % (2 * math.pi) - math.pi)
                if d_ang > lim:
                    continue
            e2 = e + (zr - z2) - nb_dist[k] * mu
            if e2 <= 0:
                continue
            if e2 > emax:
                e2 = emax
            if e2 > E[r2, c2] + 1e-6:
                E[r2, c2] = e2
                src[r2, c2] = src[r, c]
                heapq.heappush(heap, (-e2, r2, c2))
    E[E < 0] = np.nan
    return (E, src) if return_source else E


def simulate(dem: Dem, selection: np.ndarray, p: Params) -> LandslideResult:
    slope, _ = slope_aspect(dem)
    sel = selection & np.isfinite(dem.z)
    fs = np.where(sel, factor_of_safety(slope, p), np.nan)
    if p.force_release:
        release = sel & (slope > 10.0)
    else:
        release = sel & (fs < 1.0)
    if release.any():
        E, src = runout(dem, release, p, return_source=True)
    else:
        E, src = np.full(dem.shape, np.nan), np.full(dem.shape, -1, dtype=np.int64)
    affected = release | np.isfinite(E)
    res = LandslideResult(fs, release, E, affected, source=src)
    res.stats = _stats(dem, res, slope, sel, p)
    return res


def _stats(dem: Dem, res: LandslideResult, slope, sel, p: Params) -> dict:
    cell_area = abs(dem.transform.a * dem.transform.e)
    s = {"selection_ha": float(sel.sum() * cell_area / 1e4),
         "mean_slope_deg": float(np.nanmean(slope[sel])) if sel.any() else 0.0,
         "max_slope_deg": float(np.nanmax(slope[sel])) if sel.any() else 0.0,
         "min_fs": float(np.nanmin(res.fs[sel])) if sel.any() else float("nan"),
         "release_ha": float(res.release.sum() * cell_area / 1e4),
         "volume_m3": float(res.release.sum() * cell_area * p.soil_depth),
         "affected_ha": float(res.affected.sum() * cell_area / 1e4),
         "runout_ha": float((res.affected & ~res.release).sum() * cell_area / 1e4)}
    if res.release.any():
        # Reach (travel) angle: for every runout cell, the angle from the source cell whose mass
        # got there; the toe is the cell with the lowest angle (Corominas 1996 definition: H/L
        # from the source to the farthest point of the deposit).
        run = res.affected & ~res.release
        src = res.source
        if run.any() and src is not None:
            ar, ac = np.nonzero(run)
            sidx = src[ar, ac]
            ok = sidx >= 0
            ar, ac, sidx = ar[ok], ac[ok], sidx[ok]
            sr, sc = np.divmod(sidx, dem.shape[1])
            x1, y1 = dem.rc_to_xy(ar, ac)
            x0, y0 = dem.rc_to_xy(sr, sc)
            dist = np.hypot(x1 - x0, y1 - y0)
            dz = dem.z[sr, sc] - dem.z[ar, ac]
            far = (dist > 2 * dem.cell) & (dz > 0)
            if far.any():
                ang = np.where(far, np.arctan2(dz, dist), np.inf)
                j = int(np.argmin(ang))
                s["runout_length_m"] = float(dist[j])
                s["drop_m"] = float(dz[j])
                s["apparent_travel_angle_deg"] = math.degrees(float(ang[j]))
        s["max_velocity_ms"] = float(np.nanmax(res.velocity))
    return s


VEL_CLASSES = [  # (min m/s, name, colour) for runout display
    (0.0, "Slow (< 1 m/s) - deposition", (250, 200, 60)),
    (1.0, "Moderate (1-5 m/s)", (240, 120, 30)),
    (5.0, "Fast (> 5 m/s)", (210, 20, 20)),
]
RELEASE_COLOR = (130, 40, 0)


def overlay_rgba(res: LandslideResult, alpha: int = 210) -> np.ndarray:
    out = np.zeros(res.release.shape + (4,), np.uint8)
    v = res.velocity
    run = res.affected & ~res.release
    for lo, _n, col in VEL_CLASSES:
        m = run & (v >= lo)
        out[m, :3] = col
        out[m, 3] = alpha
    out[res.release, :3] = RELEASE_COLOR
    out[res.release, 3] = 230
    return out


def zone_codes(res: LandslideResult) -> np.ndarray:
    """1 = release, 2/3/4 = runout slow/moderate/fast."""
    codes = np.zeros(res.release.shape, np.uint8)
    v = res.velocity
    run = res.affected & ~res.release
    codes[run & (v < 1)] = 2
    codes[run & (v >= 1) & (v < 5)] = 3
    codes[run & (v >= 5)] = 4
    codes[res.release] = 1
    return codes


ZONE_NAMES = {1: "Release (source) zone", 2: VEL_CLASSES[0][1], 3: VEL_CLASSES[1][1], 4: VEL_CLASSES[2][1]}


def features(dem: Dem, res: LandslideResult, p: Params):
    from .exporters import Feature, polygons_from_mask
    codes = zone_codes(res)
    feats = []
    for g, v in polygons_from_mask(codes > 0, dem.transform, values=codes, simplify=dem.cell * 0.7,
                                   min_area=dem.cell ** 2):
        props = {"zone": ZONE_NAMES[v], "code": int(v), "area_ha": round(g.area / 1e4, 4)}
        if v == 1:
            props["volume_m3"] = round(g.area * p.soil_depth, 1)
        props.update({"soil_depth": p.soil_depth, "cohesion": p.cohesion, "friction": p.friction,
                      "wetness": p.wetness, "travel_ang": p.travel_angle})
        feats.append(Feature(g, props))
    return feats
