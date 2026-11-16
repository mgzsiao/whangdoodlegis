"""Helpers for pointing the grid by hand (when automatic grid detection fails).

The user clicks grid crossings and types their coordinates; the fit itself is core/georef.fit
(unchanged). This module only fine-tunes a click onto the crossing of the two grid lines and
suggests the coordinates of the next crossing from the ones already entered.
"""
from __future__ import annotations

import math

import numpy as np


def refine_crossing(rgb: np.ndarray, x: float, y: float, radius: int = 10, arm: int = 4
                    ) -> tuple[float, float] | None:
    """Move a click (image pixels, 0 = left edge of the first pixel) onto the crossing of a dark
    vertical and a dark horizontal stroke within `radius` pixels: full grid lines or small + marks.
    Diagonal lines (roads, rivers, boundaries) are ignored because they have no short vertical and
    horizontal stroke at the same place. None when no clear crossing is there (the click is then
    used as it is)."""
    from scipy.ndimage import uniform_filter1d
    h, w = rgb.shape[:2]
    cx, cy = int(math.floor(x)), int(math.floor(y))
    pad = radius + arm + 4
    x0, x1 = max(0, cx - pad), min(w, cx + pad + 1)
    y0, y1 = max(0, cy - pad), min(h, cy + pad + 1)
    if x1 - x0 < 2 * arm + 7 or y1 - y0 < 2 * arm + 7:
        return None
    win = rgb[y0:y1, x0:x1].astype(np.float32)
    gray = win.mean(axis=2) if win.ndim == 3 else win
    dark = np.clip(np.median(gray) - gray, 0, None)       # darker than the local background
    if dark.max() < 20:
        return None
    n = 2 * arm + 1
    vb = uniform_filter1d(dark, n, axis=0, mode="nearest")   # short vertical strokes
    hb = uniform_filter1d(dark, n, axis=1, mode="nearest")   # short horizontal strokes

    def beside(a, axis, k=3):
        return 0.5 * (np.roll(a, k, axis) + np.roll(a, -k, axis))
    V = np.clip(vb - beside(vb, 1), 0, None)               # a vertical stroke, not a dark area
    H = np.clip(hb - beside(hb, 0), 0, None)
    S = np.minimum(V, H)                                     # both strokes at the same place
    yy, xx = np.mgrid[y0:y1, x0:x1]
    S[np.hypot(xx + 0.5 - x, yy + 0.5 - y) > radius] = 0
    r, c = np.unravel_index(int(np.argmax(S)), S.shape)
    if S[r, c] < 0.25 * dark.max():
        return None

    def centre(p, i, k=1):                                   # weighted centre across the line width
        a, b = max(0, i - k), min(len(p), i + k + 1)
        wts = p[a:b]
        return float((wts * np.arange(a, b)).sum() / wts.sum()) if wts.sum() > 0 else float(i)
    return x0 + centre(V[r], c) + 0.5, y0 + centre(H[:, c], r) + 0.5


def suggest(fit, px: float, py: float, grid_crs: str, step: float | None) -> tuple[float, float] | None:
    """Coordinates (grid CRS) at an image pixel from the current fit, rounded to the grid spacing
    (degrees for latitude/longitude grids, metres for projected ones)."""
    if fit is None:
        return None
    from . import crs as crsmod
    X, Y = fit.forward([px], [py])
    gx, gy = crsmod.transform(X, Y, fit.work_crs, grid_crs, *fit.lonlat)
    gx, gy = float(gx[0]), float(gy[0])
    if not (np.isfinite(gx) and np.isfinite(gy)):
        return None
    if step and step > 0:
        rx, ry = round(gx / step) * step, round(gy / step) * step
        # only snap when the click is close to a grid value (within a quarter of the spacing)
        if abs(rx - gx) < 0.25 * step and abs(ry - gy) < 0.25 * step:
            return rx, ry
    return gx, gy
