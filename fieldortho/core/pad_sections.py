"""Cross-sections across a lot / pad for the report: stations along the long side of the lot (or along the
drainage slope), each section across it with its cut / fill areas, and end-area volumes between them.

The quantities of the plan are computed on the elevation grid cell by cell (more exact); the sections
and their end-area volumes are a check that engineers can follow by hand.
"""
from __future__ import annotations

import math

import numpy as np

from . import earthworks as ew, measure as ms
from . import xsection_draw as xd
from .road import station_label
from .terrain import Dem

QTY_HEADER = ("Section", "Distance (m)", "Cut area (m²)", "Fill area (m²)", "Cut volume (m³)", "Fill volume (m³)")


def _axis(t: ew.Takeoff, outline):
    from shapely.geometry import Polygon
    poly = Polygon(outline)
    if t.slope_pct > 0 and t.flow_deg is not None:
        az = t.flow_deg
    else:                                     # long side of the smallest rotated rectangle
        rect = np.asarray(poly.minimum_rotated_rectangle.exterior.coords)[:4]
        e = [rect[1] - rect[0], rect[2] - rect[1]]
        v = max(e, key=lambda q: math.hypot(*q))
        az = math.degrees(math.atan2(v[0], v[1])) % 180
    return poly, az


def interval_for(length: float) -> float:
    for s in (1, 2, 5, 10, 20, 25, 50, 100):        # up to 20 sections: the end-area check stays within a few %
        if length / s <= 20:
            return float(s)
    return 200.0


def sections(t: ew.Takeoff, outline, design: ew.PadDesign | None = None):
    """(list of xsection_draw.SectionData, quantity rows, interval m, along-axis compass text)."""
    poly, az = _axis(t, outline)
    ux, uy = math.sin(math.radians(az)), math.cos(math.radians(az))      # along the lot
    nx, ny = uy, -ux                                                      # across (to the right)
    worked = np.isfinite(t.dz)
    tr = t.transform
    rows_, cols_ = np.nonzero(worked)
    if not len(rows_):
        return [], [], 0.0, ""
    X = tr.c + (cols_ + 0.5) * tr.a
    Y = tr.f + (rows_ + 0.5) * tr.e
    c = poly.centroid
    along = (X - c.x) * ux + (Y - c.y) * uy
    across = (X - c.x) * nx + (Y - c.y) * ny
    a0, a1 = float(along.min()), float(along.max())
    w0, w1 = float(across.min()), float(across.max())
    L = a1 - a0
    step = interval_for(L)
    stations = list(np.arange(0.0, L, step)) + [L]
    if len(stations) > 1 and stations[-1] - stations[-2] < 0.2 * step:
        stations.pop(-2)
    g = Dem(t.ground, tr, t.crs_wkt, "")
    cell = abs(tr.a)
    margin = max(2.0, 0.1 * (w1 - w0))
    offs = np.arange(w0 - margin, w1 + margin + cell / 2, max(cell, 0.1))
    mid = (w0 + w1) / 2
    offs_c = offs - mid                                                   # offsets from the lot's middle line
    h, w = t.dz.shape

    out, qty = [], []
    prev = None
    cum_c = cum_f = 0.0
    for s in stations:
        ox = c.x + ux * (a0 + s) + nx * offs
        oy = c.y + uy * (a0 + s) + ny * offs
        zg = ms.sample(g, ox, oy)
        cc = np.clip(((ox - tr.c) / tr.a).astype(int), 0, w - 1)
        rr = np.clip(((oy - tr.f) / tr.e).astype(int), 0, h - 1)
        zf = t.finished[rr, cc]
        ok = np.isfinite(zg) & np.isfinite(zf)
        diff = np.where(ok, zg - zf, 0.0)
        du = np.diff(offs)
        cp, fp = np.clip(diff, 0, None), np.clip(-diff, 0, None)
        ca = float(np.sum((cp[1:] + cp[:-1]) / 2 * du))
        fa = float(np.sum((fp[1:] + fp[:-1]) / 2 * du))
        dist = 0.0 if prev is None else s - prev[0]
        cv = 0.0 if prev is None else (prev[1] + ca) / 2 * dist
        fv = 0.0 if prev is None else (prev[2] + fa) / 2 * dist
        cum_c += cv
        cum_f += fv
        prev = (s, ca, fa)
        zc_g = float(np.interp(0.0, offs_c, np.where(np.isfinite(zg), zg, np.nan)))
        zc_f = float(np.interp(0.0, offs_c, np.where(np.isfinite(zf), zf, np.nan)))
        info = [("Distance from last", f"{dist:.2f} m"), ("Cut area", f"{ca:.2f} m²"), ("Fill area", f"{fa:.2f} m²"),
                ("Cut volume since last", f"{cv:.2f} m³"), ("Fill volume since last", f"{fv:.2f} m³")]
        if math.isfinite(zc_f):
            info.append(("Finished level", f"{zc_f:.3f} m"))
        if math.isfinite(zc_g):
            info.append(("Ground level", f"{zc_g:.3f} m"))
        slopes = []
        if t.pad_mask is not None:
            pm = t.pad_mask[rr, cc] & ok
            if pm.any():
                i0, i1 = np.flatnonzero(pm)[[0, -1]]
                if i1 > i0:
                    gr = (zf[i1] - zf[i0]) / max(offs[i1] - offs[i0], 1e-9) * 100
                    slopes.append((offs_c[i0], offs_c[i1], f"top {gr:+.1f}%"))
                if design and design.sides == "slopes":
                    for j, side in ((i0, -1), (i1, 1)):
                        cut = bool(np.isfinite(zg[j]) and zg[j] > zf[j])
                        r_ = design.cut_ratio if cut else design.fill_ratio
                        rng = offs_c[:i0][ok[:i0]] if side < 0 else offs_c[i1 + 1:][ok[i1 + 1:]]
                        if len(rng) > 2:
                            e = rng[0] if side < 0 else rng[-1]
                            slopes.append((offs_c[j], e, xd.ratio_text(r_, cut)))
        out.append(xd.SectionData(f"SEC. {station_label(s)}", offs_c, zg, np.where(np.isfinite(zf), zf, np.nan),
                                  zc_g, zc_f, info, slopes))
        qty.append((station_label(s), f"{dist:.2f}", f"{ca:.2f}", f"{fa:.2f}", f"{cv:.2f}", f"{fv:.2f}"))
    qty.append(("TOTAL (end-area check)", f"{L:.2f}", "", "", f"{cum_c:.2f}", f"{cum_f:.2f}"))
    qty.append(("Plan totals (grid method)", "", "", "", f"{t.cut:.2f}", f"{t.fill:.2f}"))
    return out, qty, step, ew.compass_name(az + 180) + " → " + ew.compass_name(az)


def sheets(t: ew.Takeoff, outline, design=None):
    secs, qty, step, along = sections(t, outline, design)
    return xd.pages(xd.panels(secs)), qty, step, along
