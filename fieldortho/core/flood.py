"""Flood inundation from a terrain model ('bathtub' model with connectivity).

What it does
    Raises a flat water surface to a chosen level and floods every cell of the
    terrain below that level that is hydraulically connected to the chosen
    source (a clicked river/sea point, or the map edge). Depth = level - ground.

What it does not do
    No flow dynamics, rainfall-runoff, timing, velocities or defences. It shows
    'what is under water if water stands at level X' - the standard first-order
    screening model. Use DTM (bare ground): buildings and trees in a DSM block water.

Depth classes follow the MGB flood susceptibility legend (heights):
    Low < 0.5 m, Moderate 0.5 - 1 m, High > 1 m.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .terrain import Dem

CLASSES = [  # (min depth, max depth, name, colour)
    (0.0, 0.5, "Low (< 0.5 m)", (227, 209, 255)),
    (0.5, 1.0, "Moderate (0.5 - 1 m)", (175, 69, 255)),
    (1.0, float("inf"), "High (> 1 m)", (90, 0, 254)),
]


@dataclass
class FloodResult:
    level: float                 # water surface elevation (m, DEM vertical datum)
    depth: np.ndarray            # metres, NaN where dry
    stats: dict = field(default_factory=dict)


def reference_elevation(dem: Dem, row: int, col: int, radius: int = 2) -> float:
    """Ground level at a clicked river/sea point: lowest valid cell nearby
    (the click is rarely exactly on the channel bottom)."""
    h, w = dem.shape
    win = dem.z[max(0, row - radius):min(h, row + radius + 1), max(0, col - radius):min(w, col + radius + 1)]
    if not np.isfinite(win).any():
        raise ValueError("No elevation data at that point.")
    return float(np.nanmin(win))


def simulate(dem: Dem, level: float, seeds: list[tuple[int, int]] | None = None,
             connect: str = "seeds") -> FloodResult:
    """connect: 'seeds' (connected to clicked points), 'edges' (connected to map edge,
    e.g. sea), 'none' (every cell below level, including closed depressions)."""
    from scipy import ndimage as ndi
    z = dem.z
    wet = np.isfinite(z) & (z < level)
    if connect != "none" and wet.any():
        lab, n = ndi.label(wet, structure=np.ones((3, 3), bool))
        keep = set()
        if connect == "seeds" and seeds:
            for r, c in seeds:
                if 0 <= r < z.shape[0] and 0 <= c < z.shape[1]:
                    # snap to the nearest wet cell within 3 cells
                    r0, r1, c0, c1 = max(0, r - 3), r + 4, max(0, c - 3), c + 4
                    ids = lab[r0:r1, c0:c1]
                    keep.update(int(v) for v in np.unique(ids) if v)
        elif connect == "edges":
            edge = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
            keep.update(int(v) for v in np.unique(edge) if v)
        wet = np.isin(lab, list(keep)) if keep else np.zeros_like(wet)
    depth = np.where(wet, level - z, np.nan)
    return FloodResult(level, depth, _stats(dem, depth))


def simulate_river(dem: Dem, rise: float, hand_m: np.ndarray, river_ref: np.ndarray,
                   river_cells: np.ndarray) -> FloodResult:
    """River rises `rise` metres above its own bed along its whole length (HAND model).
    Floods cells draining to the chosen river whose height above that river < rise,
    and that are connected to the river through flooded cells."""
    from scipy import ndimage as ndi
    on_river = np.zeros(dem.shape, bool)
    ok = river_ref >= 0
    on_river[ok] = river_cells.ravel()[river_ref[ok]]
    wet = on_river & np.isfinite(hand_m) & (hand_m < rise) & np.isfinite(dem.z)
    if wet.any():
        lab, _ = ndi.label(wet, structure=np.ones((3, 3), bool))
        ids = np.unique(lab[river_cells & wet])
        wet = np.isin(lab, ids[ids > 0])
    depth = np.where(wet, rise - hand_m, np.nan)
    res = FloodResult(float("nan"), depth, _stats(dem, depth))
    res.stats["rise_m"] = rise
    return res


def _stats(dem: Dem, depth: np.ndarray) -> dict:
    cell_area = abs(dem.transform.a * dem.transform.e)
    wet = np.isfinite(depth)
    s = {"area_ha": float(wet.sum() * cell_area / 1e4), "volume_m3": float(np.nansum(depth) * cell_area),
         "max_depth_m": float(np.nanmax(depth)) if wet.any() else 0.0,
         "mean_depth_m": float(np.nanmean(depth)) if wet.any() else 0.0, "classes": {}}
    codes = class_codes(depth)
    for i, (_lo, _hi, name, _col) in enumerate(CLASSES, start=1):
        s["classes"][name] = float((codes == i).sum() * cell_area / 1e4)
    return s


EPS = 1e-6


def class_codes(depth: np.ndarray) -> np.ndarray:
    """MGB wording: low < 0.5 m, moderate 0.5 to 1 m (inclusive), high > 1 m."""
    codes = np.zeros(depth.shape, np.uint8)
    wet = np.isfinite(depth)
    d = np.where(wet, depth, 0.0)
    codes[wet] = 1
    codes[wet & (d >= 0.5 - EPS)] = 2
    codes[wet & (d > 1.0 + EPS)] = 3
    return codes


def overlay_rgba(depth: np.ndarray, alpha: int = 200) -> np.ndarray:
    codes = class_codes(depth)
    out = np.zeros(depth.shape + (4,), np.uint8)
    for i, (_lo, _hi, _n, col) in enumerate(CLASSES, start=1):
        m = codes == i
        out[m, :3] = col
        out[m, 3] = alpha
    return out


def features(dem: Dem, res: FloodResult, simplify: bool = True):
    from .exporters import Feature, polygons_from_mask
    codes = class_codes(res.depth)
    feats = []
    for g, v in polygons_from_mask(codes > 0, dem.transform, values=codes,
                                   simplify=dem.cell * 0.7 if simplify else 0.0, min_area=dem.cell ** 2):
        lo, hi, name, _ = CLASSES[v - 1]
        lvl = None if res.level != res.level else round(res.level, 3)
        feats.append(Feature(g, {"class": name, "min_depth": lo, "max_depth": None if hi == float("inf") else hi,
                                 "level_m": lvl, "rise_m": res.stats.get("rise_m"),
                                 "area_ha": round(g.area / 1e4, 4)}))
    return feats
