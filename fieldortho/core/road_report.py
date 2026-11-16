"""Road plan -> PDF report, Excel workbook, CSVs and GIS layers (one folder, for engineers)."""
from __future__ import annotations

import html
import json
import math
import os
from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw

from .. import APP_NAME, DISCLAIMER_PARAGRAPHS
from . import earthworks as ew, earthworks_report as er, exporters as ex, road as rdm, vertical as vt
from . import xsection_draw as xd
from .earthworks_report import AMBER, CUT_RGB, FILL_RGB, GROUND_RGB, INK, MUTED, NAVY, _font, ms_nice

ROAD_NOTES = (
    "Volumes between stations use the average end-area method: the dig and fill areas of two neighbouring "
    "cross-sections are averaged and multiplied by the distance between them. Closer stations follow the ground "
    "more closely.",
    "On curves the average end-area method is used without a curvature correction; the error is small unless the "
    "cut or fill is much larger on one side of a tight curve.",
) + er.EARTHWORKS_NOTES


def quantities(r: rdm.RoadResult) -> list[tuple[str, str, str]]:
    h = r.haul
    st = r.stations
    rows = [("Road length", f"{r.length:,.1f} m", f"{st[0].label} to {st[-1].label}"),
            ("Stations", f"{len(st)}", "including bends and the end"),
            ("Steepest part of the road", f"{r.steepest:.1f} %", f"near {rdm.station_label(r.steepest_at)}"),
            ("Deepest dig at the centre line", f"{r.deepest_cut:.2f} m", ""),
            ("Highest fill at the centre line", f"{r.highest_fill:.2f} m", ""),
            ("DIG (CUT) total", f"{r.cut:,.1f} m³", ""),
            ("FILL total", f"{r.fill:,.1f} m³", ""),
            ("Net (dig − fill)", f"{r.net:,.1f} m³", ""),
            ("Total earth moved (dig + fill)", f"{r.total:,.1f} m³", ""),
            ("Soil needed for the fill", f"{h.needed:,.1f} m³", f"fill + {h.shrink_pct:g}% for packing")]
    if h.balance >= 0:
        rows.append(("Extra soil to haul away", f"{h.balance:,.1f} m³", f"{h.loose:,.1f} m³ loose (+{h.swell_pct:g}%)"))
    else:
        rows.append(("Soil to bring in", f"{-h.balance:,.1f} m³", f"{h.loose:,.1f} m³ loose (+{h.swell_pct:g}%)"))
    rows.append(("Truck trips", "  ·  ".join(f"{n:,}" for _s, n in h.trips),
                 "  ·  ".join(f"{s:g} m³ truck" for s, _n in h.trips)))
    return rows


def mass_haul_image(r: rdm.RoadResult, width=1800, height=520) -> Image.Image:
    """Running balance of earth along the road: rising = more dug than filled so far."""
    im = Image.new("RGB", (width, height), "white")
    dr = ImageDraw.Draw(im)
    sd = np.array([s.s for s in r.stations])
    m = np.array([s.mass for s in r.stations])
    lo, hi = min(0.0, float(m.min())), max(0.0, float(m.max()))
    pad = max(1.0, (hi - lo) * 0.12)
    lo, hi = lo - pad, hi + pad
    L, R, T, B = 140, 30, 70, 70
    pw, ph = width - L - R, height - T - B
    dmax = float(sd[-1]) or 1.0

    def P(a, b):
        return (L + a / dmax * pw, T + (hi - b) / (hi - lo) * ph)
    f, fb = _font(22), _font(24, True)
    for k in range(6):
        v = lo + (hi - lo) * k / 5
        y = P(0, v)[1]
        dr.line([(L, y), (L + pw, y)], fill=(225, 229, 236))
        dr.text((8, y - 12), f"{v:,.0f} m³", font=f, fill=MUTED)
    step = ms_nice(dmax / 8)
    x = 0.0
    while x <= dmax + 1e-9:
        X = P(x, lo)[0]
        dr.text((X - 32, T + ph + 10), rdm.station_label(x), font=f, fill=MUTED)
        x += step
    y0 = P(0, 0)[1]
    dr.line([(L, y0), (L + pw, y0)], fill=INK, width=2)
    pts = [P(a, b) for a, b in zip(sd, m)]
    for (xa, ya), (xb, yb), mb in zip(pts[:-1], pts[1:], m[1:]):
        col = CUT_RGB if mb >= 0 else FILL_RGB
        dr.polygon([(xa, y0), (xa, ya), (xb, yb), (xb, y0)], fill=tuple(int(c * 0.3 + 255 * 0.7) for c in col))
    dr.line(pts, fill=NAVY, width=4)
    for i in range(1, len(m)):                      # balance points: dug soil = soil needed so far
        if (m[i - 1] > 0) != (m[i] > 0) and m[i - 1] != m[i]:
            sx = sd[i - 1] + (sd[i] - sd[i - 1]) * m[i - 1] / (m[i - 1] - m[i])
            X, Y = P(sx, 0)
            dr.ellipse([X - 7, Y - 7, X + 7, Y + 7], outline=INK, width=3)
            dr.text((X - 40, Y + 10), rdm.station_label(sx), font=f, fill=INK)
    dr.text((L, 16), "Earth balance along the road (mass haul)", font=fb, fill=INK)
    dr.text((width - 900, 18), "above zero: more dug than needed so far · circles: balance points",
            font=f, fill=MUTED)
    return im


QTY_HEADER = ("Station", "Distance (m)", "Cut area (m²)", "Fill area (m²)", "Cut volume (m³)", "Fill volume (m³)")


def quantity_rows(r: rdm.RoadResult, total: bool = True) -> list[tuple]:
    """Per station: distance from the previous station, cut / fill areas, volumes since the previous station
    (average end-area)."""
    out, prev = [], None
    for s in r.stations:
        dist = 0.0 if prev is None else s.s - prev
        out.append((s.label, f"{dist:.2f}", f"{s.cut_area:.2f}", f"{s.fill_area:.2f}", f"{s.cut_vol:.2f}",
                    f"{s.fill_vol:.2f}"))
        prev = s.s
    if total:
        out.append(("TOTAL", f"{r.length:.2f}", "", "", f"{r.cut:.2f}", f"{r.fill:.2f}"))
    return out


def worked_example(r: rdm.RoadResult, shrink_pct: float) -> list[str]:
    """Step-by-step check of one line of the tables with this road's numbers (plain text paragraphs)."""
    st = r.stations
    out = []
    if len(st) > 1:
        i = max(range(1, len(st)), key=lambda k: st[k].cut_vol + st[k].fill_vol)
        a, b = st[i - 1], st[i]
        L = b.s - a.s
        out.append(f"Volume between {a.label} and {b.label} (average end-area). Distance L = {b.s:.2f} − {a.s:.2f} "
                   f"= {L:.2f} m. Cut: V = (A1 + A2) / 2 × L = ({a.cut_area:.2f} + {b.cut_area:.2f}) / 2 × {L:.2f} "
                   f"= {b.cut_vol:.2f} m³. Fill: V = ({a.fill_area:.2f} + {b.fill_area:.2f}) / 2 × {L:.2f} = "
                   f"{b.fill_vol:.2f} m³.")
        k = 1 + shrink_pct / 100
        out.append(f"Running balance at {b.label}: cut so far − fill so far × (1 + shrink) = {b.cum_cut:,.2f} − "
                   f"{b.cum_fill:,.2f} × {k:.2f} = {b.mass:,.2f} m³ (positive: soil to spare so far).")
        out.append(f"Cut and fill areas at {b.label}: the strip between the ground and the finished section is "
                   f"added up across the section in {r.cell:.2f} m steps (trapezoids), from where the left side "
                   f"meets the ground ({b.left_catch:.2f} m) to the right ({b.right_catch:+.2f} m). Ground "
                   f"{b.ground:.3f} m, finished road {b.design:.3f} m at the centre: "
                   f"{'dig' if b.depth > 0 else 'fill'} {abs(b.depth):.3f} m there.")
    c = next((c for c in r.vcurves if c.length > 0), None)
    if c is not None:
        sx = next((s for s in st if c.pvc_s < s.s < c.pvt_s), None)
        x = (sx.s if sx else c.pvi_s) - c.pvc_s
        z = c.pvc_z + c.g1 / 100 * x + c.a / 100 * x * x / (2 * c.length)
        where = sx.label if sx else rdm.station_label(c.pvi_s)
        out.append(f"Road height on vertical curve {c.n} at {where}: grades g1 = {c.g1:+.3f}% and g2 = {c.g2:+.3f}% "
                   f"meet at the PVI {rdm.station_label(c.pvi_s)} ({c.pvi_z:.3f} m); A = g2 − g1 = {c.a:+.3f}%, "
                   f"L = {c.length:.2f} m, K = L / |A| = {c.k:.2f} (needs {c.k_min:g} at {r.speed} km/h). "
                   f"PVC at {rdm.station_label(c.pvc_s)}, elevation {c.pvi_z:.3f} − {c.g1:+.3f}% × {c.length / 2:.2f}"
                   f" = {c.pvc_z:.3f} m. x = {x:.2f} m after the PVC: z = z_PVC + g1·x + A·x² / (200·L) = "
                   f"{c.pvc_z:.3f} + {c.g1 / 100:+.5f} × {x:.2f} + {c.a:+.3f} × {x:.2f}² / (200 × {c.length:.2f}) = "
                   f"{z:.3f} m.")
    return out


def vcurve_html_rows(r: rdm.RoadResult) -> list[tuple]:
    out = []
    for row in vt.rows(r.vcurves, rdm.station_label):
        out.append((row[0], row[1], row[2], f"{row[3]:.3f}", f"{row[4]:+.2f}", f"{row[5]:+.2f}", f"{row[7]:.1f}",
                    row[8], row[9], row[10], row[12], row[15]))
    return out


def station_rows(r: rdm.RoadResult) -> list[tuple]:
    out = []
    for s in r.stations:
        out.append((s.label, round(s.s, 3), round(s.x, 3), round(s.y, 3),
                    round(s.ground, 3) if s.ground == s.ground else None, round(s.design, 3),
                    round(s.depth, 3) if s.ground == s.ground else None, round(s.cut_area, 3), round(s.fill_area, 3),
                    round(s.cut_vol, 2), round(s.fill_vol, 2), round(s.cum_cut, 2), round(s.cum_fill, 2),
                    round(s.mass, 2), s.kind))
    return out


STATION_HEADER = ("Station", "Distance (m)", "Easting (m)", "Northing (m)", "Ground (m)", "Road (m)",
                  "Dig + / Fill − at centre (m)", "Dig area (m²)", "Fill area (m²)", "Dig since last (m³)",
                  "Fill since last (m³)", "Dig so far (m³)", "Fill so far (m³)", "Running balance (m³)", "Note")


def section_rows(r: rdm.RoadResult, every: float = 0.5) -> list[tuple]:
    out = []
    for s in r.stations:
        o, g, d = s.offsets, s.ground_xs, s.design_xs
        if o is None:
            continue
        step = max(1, int(round(every / max(o[1] - o[0], 1e-6)))) if len(o) > 1 else 1
        idx = sorted(set(range(0, len(o), step)) | {len(o) - 1})
        for i in idx:
            out.append((s.label, round(float(o[i]), 2), round(float(g[i]), 3) if np.isfinite(g[i]) else None,
                        round(float(d[i]), 3) if np.isfinite(d[i]) else None))
    return out


# ============================================================================ pictures
def profile_image(r: rdm.RoadResult, width=1800, height=620, mark: float | None = None) -> Image.Image:
    d, g, z = r.prof_d, r.prof_g, r.prof_z
    im = Image.new("RGB", (width, height), "white")
    dr = ImageDraw.Draw(im)
    ok = np.isfinite(g)
    allz = np.concatenate([g[ok], z])
    z0, z1 = float(allz.min()), float(allz.max())
    pz = max(0.5, (z1 - z0) * 0.12)
    z0, z1 = z0 - pz, z1 + pz
    L, R, T, B = 120, 30, 70, 70
    pw, ph = width - L - R, height - T - B
    dmax = float(d[-1]) or 1.0

    def P(di, zi):
        return (L + di / dmax * pw, T + (z1 - zi) / (z1 - z0) * ph)
    f, fb = _font(22), _font(24, True)
    for k in range(6):
        zz = z0 + (z1 - z0) * k / 5
        y = T + ph - ph * k / 5
        dr.line([(L, y), (L + pw, y)], fill=(225, 229, 236))
        dr.text((10, y - 12), f"{zz:.1f} m", font=f, fill=MUTED)
    step = ms_nice(dmax / 8)
    x = 0.0
    while x <= dmax + 1e-9:
        X = P(x, z0)[0]
        dr.line([(X, T + ph), (X, T + ph + 6)], fill=MUTED, width=2)
        lab = rdm.station_label(x)
        dr.text((X - 32, T + ph + 10), lab, font=f, fill=MUTED)
        x += step
    for i in range(len(d) - 1):
        if not ok[i]:
            continue
        xa, xb = P(d[i], 0)[0], P(d[i + 1], 0)[0]
        ya, yb = P(0, g[i])[1], P(0, z[i])[1]
        if abs(ya - yb) < 0.5:
            continue
        col = CUT_RGB if g[i] > z[i] else FILL_RGB
        dr.rectangle([xa, min(ya, yb), max(xb, xa + 1), max(ya, yb)],
                     fill=tuple(int(c * 0.35 + 255 * 0.65) for c in col))
    seg = []
    for di, gi in zip(d, g):
        if np.isfinite(gi):
            seg.append(P(di, gi))
        elif len(seg) > 1:
            dr.line(seg, fill=GROUND_RGB, width=4)
            seg = []
    if len(seg) > 1:
        dr.line(seg, fill=GROUND_RGB, width=4)
    dr.line([P(a, b) for a, b in zip(d, z)], fill=NAVY, width=5)
    if r.pvis:
        _profile_vertical(dr, r, P, f, fb)
    sd = np.array([s.s for s in r.stations])
    sz = np.array([s.design for s in r.stations])
    gr = np.diff(sz) / np.maximum(np.diff(sd), 1e-9) * 100
    runs, a = [], 0                       # stretches of similar grade (within 1%)
    for i in range(1, len(gr) + 1):
        if i == len(gr) or abs(gr[i] - gr[a]) > 1.0:
            runs.append((a, i))
            a = i
    for a, b in ([] if r.pvis else sorted(runs, key=lambda ab: sd[ab[1]] - sd[ab[0]], reverse=True)[:10]):
        if sd[b] - sd[a] < 0.06 * dmax:
            continue
        gg = (sz[b] - sz[a]) / max(sd[b] - sd[a], 1e-9) * 100
        X, Y = P((sd[a] + sd[b]) / 2, (sz[a] + sz[b]) / 2)
        dr.text((X - 30, Y - 36), f"{gg:+.1f}%", font=fb, fill=NAVY)
    if mark is not None:
        X = P(mark, z0)[0]
        dr.line([(X, T), (X, T + ph)], fill=INK, width=2)
    dr.text((L, 16), "Side view along the road", font=fb, fill=INK)
    lx = width - 760
    for col, txt in ((GROUND_RGB, "Ground now"), (NAVY, "Road surface")):
        dr.rectangle([lx, 26, lx + 30, 32], fill=col)
        dr.text((lx + 40, 14), txt, font=f, fill=INK)
        lx += 260
    for col, txt in ((CUT_RGB, "Dig"), (FILL_RGB, "Fill")):
        dr.rectangle([lx, 18, lx + 24, 38], fill=tuple(int(c * 0.35 + 255 * 0.65) for c in col))
        dr.text((lx + 34, 14), txt, font=f, fill=INK)
        lx += 120
    return im


def _profile_vertical(dr, r, P, f, fb):
    """Grade lines between the PVIs (dashed), PVI / PVC / PVT marks and grade labels."""
    pv = [(float(p[0]), float(p[1])) for p in r.pvis]
    for (a0, b0), (a1, b1) in zip(pv, pv[1:]):
        xa, ya = P(a0, b0)
        xb, yb = P(a1, b1)
        n = max(1, int(math.hypot(xb - xa, yb - ya) / 18))
        for k in range(0, n, 2):
            t0, t1 = k / n, min(1.0, (k + 1) / n)
            dr.line([(xa + (xb - xa) * t0, ya + (yb - ya) * t0), (xa + (xb - xa) * t1, ya + (yb - ya) * t1)],
                    fill=(120, 130, 150), width=2)
        g = (b1 - b0) / max(a1 - a0, 1e-9) * 100
        X, Y = P((a0 + a1) / 2, (b0 + b1) / 2)
        txt = f"{g:+.2f}%"
        dr.text((X - dr.textlength(txt, font=fb) / 2, Y - 44), txt, font=fb, fill=(24, 130, 70))
    for c in r.vcurves:
        X, Y = P(c.pvi_s, c.pvi_z)
        dr.polygon([(X, Y - 10), (X - 9, Y + 6), (X + 9, Y + 6)], outline=INK, fill=(255, 255, 255))
        if c.length <= 0:
            continue
        for lab, s_, z_ in (("PVC", c.pvc_s, c.pvc_z), ("PVT", c.pvt_s, c.pvt_z)):
            x_, y_ = P(s_, z_)
            dr.line([(x_, y_ - 16), (x_, y_ + 16)], fill=(160, 40, 140), width=3)
        lab = f"VC{c.n}  L {c.length:.0f} m  K {c.k:.1f}"
        dr.text((X - dr.textlength(lab, font=f) / 2, Y + 14), lab, font=f, fill=(160, 40, 140))


def vcurve_image_note(r) -> str:
    return ("Dashed: straight grades between the grade change points (PVI, triangles); purple ticks: start (PVC) "
            "and end (PVT) of each vertical curve.") if r.pvis else ""


def section_data(r: rdm.RoadResult, rd: rdm.RoadDesign | None = None) -> list[xd.SectionData]:
    rd = rd or rdm.RoadDesign()
    out, prev = [], None
    w2 = rd.width / 2
    for s in r.stations:
        if s.offsets is None:
            prev = s.s
            continue
        dist = 0.0 if prev is None else s.s - prev
        prev = s.s
        info = [("Distance from last", f"{dist:.2f} m"),
                ("Dig area", f"{s.cut_area:.2f} m²"), ("Fill area", f"{s.fill_area:.2f} m²"),
                ("Dig volume since last", f"{s.cut_vol:.2f} m³"), ("Fill volume since last", f"{s.fill_vol:.2f} m³"),
                ("Finished level", f"{s.design:.3f} m")]
        if s.ground == s.ground:
            info += [("Ground level", f"{s.ground:.3f} m"),
                     ("On grade at centre" if abs(s.depth) < 0.0005 else
                      "Dig at centre" if s.depth > 0 else "Fill at centre", f"{abs(s.depth):.3f} m")]
        slopes = []
        for sign in (-1, 1):
            slopes.append((0, sign * w2, xd.slope_text(rd.crown_pct)))
            if rd.shoulders:
                slopes.append((sign * w2, sign * rd.half, xd.slope_text(rd.shoulder_pct)))
            catch = s.right_catch if sign > 0 else s.left_catch
            zh = np.interp(sign * rd.half, s.offsets, np.where(np.isfinite(s.design_xs), s.design_xs, np.nan))
            gh = np.interp(sign * rd.half, s.offsets, s.ground_xs)
            cut = bool(np.isfinite(gh) and np.isfinite(zh) and gh > zh)
            start = rd.half + (rd.ditch_d + rd.ditch_w if cut and rd.ditch else 0.0)
            if abs(catch) > start + 0.5:
                slopes.append((sign * start, catch, xd.ratio_text(rd.cut_ratio if cut else rd.fill_ratio, cut)))
        out.append(xd.SectionData(f"STA. {s.label}", s.offsets, s.ground_xs, s.design_xs, s.ground, s.design,
                                  info, slopes))
    return out


def sections_sheets(r: rdm.RoadResult, rd: rdm.RoadDesign | None = None) -> list[Image.Image]:
    return xd.pages(xd.panels(section_data(r, rd)))


def map_image(r: rdm.RoadResult, pts, dem_path: str, ortho_path: str | None, max_px: int = 1800) -> Image.Image:
    from .model_export import background_for
    xs = [s.catch_xy(k)[0] for s in r.stations for k in (-1, 1)] + [p[0] for p in pts]
    ys = [s.catch_xy(k)[1] for s in r.stations for k in (-1, 1)] + [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    m = max(15.0, 0.12 * max(x1 - x0, y1 - y0))
    grid = er._target_grid(dem_path, (x0 - m, y0 - m, x1 + m, y1 + m), max_px)
    im = Image.fromarray(background_for(grid, ortho_path)).convert("RGBA")
    scale = 1.0
    if im.width < 1200:
        scale = 1200 / im.width
        im = im.resize((1200, int(round(im.height * scale))), Image.LANCZOS)
    gt = grid.transform

    def px(x, y):
        return ((x - gt.c) / gt.a * scale, (y - gt.f) / gt.e * scale)
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    st = r.stations
    for a, b in zip(st[:-1], st[1:]):
        cut = (a.cut_area + b.cut_area) >= (a.fill_area + b.fill_area)
        col = CUT_RGB if cut else FILL_RGB
        poly = [px(*a.catch_xy(-1)), px(*b.catch_xy(-1)), px(*b.catch_xy(1)), px(*a.catch_xy(1))]
        od.polygon(poly, fill=col + (110,))
    im = Image.alpha_composite(im, ov).convert("RGB")
    d = ImageDraw.Draw(im)
    W = im.width
    lw = max(3, W // 450)
    line = [px(*p) for p in (r.centre if r.centre is not None else pts)]
    d.line(line, fill=INK, width=lw + 2)
    d.line(line, fill=AMBER, width=lw)
    f = _font(max(16, W // 60), True)
    every = max(1, int(math.ceil(len(st) / 10)))
    shown = [i for i in range(len(st)) if i % every == 0]
    if shown[-1] != len(st) - 1:
        if math.dist(px(st[shown[-1]].x, st[shown[-1]].y), px(st[-1].x, st[-1].y)) < 8 * f.size:
            shown.pop()
        shown.append(len(st) - 1)
    for i, s in enumerate(st):
        X, Y = px(s.x, s.y)
        if i in shown:
            lx, ly = X, Y
            d.ellipse([X - 5, Y - 5, X + 5, Y + 5], fill=(255, 255, 255), outline=INK, width=2)
            tw = d.textlength(s.label, font=f)
            th = f.size + 6
            d.rectangle([lx + 8, ly - th - 2, lx + 16 + tw, ly - 2], fill=INK)
            d.text((lx + 12, ly - th - 1), s.label, font=f, fill=(255, 255, 255))
    metres_per_px = gt.a / scale
    nice = ms_nice(W * 0.2 * metres_per_px)
    bar = nice / metres_per_px
    k = max(1.0, W / 1200)                    # text scale: the picture is shrunk to the page width
    fs = _font(int(18 * k))
    bx, by = int(20 * k), im.height - int(30 * k)
    d.rectangle([bx - 10, by - int(36 * k), bx + bar + int(80 * k), by + int(16 * k)], fill=(255, 255, 255))
    d.rectangle([bx, by - int(6 * k), bx + bar, by], fill=INK)
    d.text((bx, by - int(32 * k)), "0", font=fs, fill=INK)
    d.text((bx + bar - 10, by - int(32 * k)), f"{nice:g} m", font=fs, fill=INK)
    nx_, ny_ = W - int(40 * k), int(20 * k)
    d.rectangle([nx_ - int(22 * k), ny_ - 6, nx_ + int(22 * k), ny_ + int(58 * k)], fill=(255, 255, 255))
    d.polygon([(nx_, ny_), (nx_ - int(10 * k), ny_ + int(28 * k)), (nx_ + int(10 * k), ny_ + int(28 * k))], fill=INK)
    d.text((nx_ - int(7 * k), ny_ + int(30 * k)), "N", font=_font(int(20 * k), True), fill=INK)
    items = [(CUT_RGB, "Mostly dig"), (FILL_RGB, "Mostly fill"), (AMBER, "Road centre line")]
    lx, ly, rh = int(20 * k), int(16 * k), int(30 * k)
    d.rectangle([lx - 8, ly - 8, lx + int(230 * k), ly + rh * len(items)], fill=(255, 255, 255))
    for i, (col, txt) in enumerate(items):
        d.rectangle([lx, ly + i * rh + 4, lx + int(22 * k), ly + i * rh + int(20 * k)], fill=col)
        d.text((lx + int(32 * k), ly + i * rh), txt, font=fs, fill=INK)
    return im


# ============================================================================ files
def write_workbook(path, title, meta, q, inp, stations, sections, notes, curves=(), vrows=(), qty=(), worked=()):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    head, fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3F77")

    def table(ws, header, rows, r0=1, widths=None):
        for c, h in enumerate(header, 1):
            ws.cell(r0, c, h).font = head
            ws.cell(r0, c).fill = fill
            ws.cell(r0, c).alignment = Alignment(wrap_text=True, vertical="top")
        for i, row in enumerate(rows, r0 + 1):
            for c, v in enumerate(row, 1):
                ws.cell(i, c, v)
        for c, wdt in enumerate(widths or [], 1):
            ws.column_dimensions[ws.cell(1, c).column_letter].width = wdt
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=14)
    for i, (k, v) in enumerate(meta, 3):
        ws.cell(i, 1, k).font = Font(bold=True)
        ws.cell(i, 2, v)
    table(ws, ("Quantity", "Value", "Note"), q, len(meta) + 4, [40, 26, 46])
    table(wb.create_sheet("Inputs"), ("Setting", "Value"), inp, 1, [40, 80])
    wst = wb.create_sheet("Stations")
    table(wst, STATION_HEADER, stations, 1, [11, 12, 14, 14, 11, 11, 14, 12, 12, 13, 13, 13, 13, 14, 8])
    wst.freeze_panes = "B2"
    wst.row_dimensions[1].height = 45
    if curves:
        table(wb.create_sheet("Curves"), rdm.CURVE_HEADER, curves, 1, [8, 15, 15, 13, 11, 13, 14, 13, 16, 15, 15, 8])
    if qty:
        wq = wb.create_sheet("Quantities by station")
        table(wq, QTY_HEADER, qty, 1, [12, 13, 14, 14, 16, 16])
        wq.freeze_panes = "A2"
    if vrows:
        table(wb.create_sheet("Vertical curves"), vt.ROW_HEADER, vrows, 1,
              [7, 9, 12, 14, 12, 12, 9, 11, 9, 9, 12, 14, 12, 14, 14, 26])
        wk = wb.create_sheet("K values (AASHTO 2018)")
        table(wk, ("Design speed (km/h)", "Stopping sight distance (m)", "K crest", "K sag", "Minimum length (m)"),
              [(v, a, b, c, round(vt.min_length(v), 1)) for v, (a, b, c) in vt.K_TABLE.items()], 1,
              [18, 24, 10, 10, 18])
        wk.cell(len(vt.K_TABLE) + 3, 1, "Source: " + vt.AASHTO_SOURCE)
    if worked:
        ww = wb.create_sheet("Worked example")
        ww.column_dimensions["A"].width = 130
        for i, t in enumerate(worked, 1):
            ww.cell(i, 1, t).alignment = Alignment(wrap_text=True, vertical="top")
    wsx = wb.create_sheet("Cross-sections")
    table(wsx, ("Station", "Offset (m, + right)", "Ground (m)", "Design (m)"), sections, 1, [11, 18, 12, 12])
    wsx.freeze_panes = "A2"
    wn = wb.create_sheet("Notes")
    wn.column_dimensions["A"].width = 120
    for i, n in enumerate(notes, 1):
        wn.cell(i, 1, n).alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)
    return path


def report_html(r: rdm.RoadResult, plan: dict, meta, q, inp, quality, files, map_size, n_sections,
                curves=(), sheet_sizes=(), worked=()) -> str:
    h = r.haul
    big = [("Dig (cut)", f"{r.cut:,.0f} m³"), ("Fill", f"{r.fill:,.0f} m³"), ("Length", f"{r.length:,.0f} m"),
           ("Steepest", f"{r.steepest:.1f} %")]
    cells = "".join(f'<td width="25%" align="center" style="background:#E7EDF7; padding:5px;">'
                    f'<span style="color:#5D677A; font-size:8pt;">{html.escape(k.upper())}</span><br>'
                    f'<span style="font-size:15pt; font-weight:600;">{html.escape(v)}</span></td>' for k, v in big)
    H2 = '<h2 style="font-size:12pt; margin-top:8px;">'
    parts = [f'<p style="color:#5D677A; font-size:8pt; margin:0;">{APP_NAME} · Road earthworks estimate</p>',
             f'<h1 style="font-size:16pt; margin:2px 0 4px 0;">{html.escape(plan.get("name", "Road"))}</h1>',
             '<p style="font-size:10pt; color:#1F3F77; margin:0 0 8px 0;">Road, path or canal</p>',
             er._table(meta, ("30%", "70%")), f"{H2}At a glance</h2>",
             f'<p style="font-size:9pt;">{html.escape(r.headline())}<br>{html.escape(h.sentence())}</p>',
             f'<table width="100%" cellspacing="4" cellpadding="0"><tr>{cells}</tr></table>']
    warn = []
    if r.side_limit:
        warn.append(f"At {r.side_limit} station(s) a side slope did not meet the ground within {rdm.MAX_SIDE:g} m of "
                    "the shoulder: the real volume there is larger.")
    if r.no_data:
        warn.append(f"{r.no_data} station(s) are off the drone map or in a gap; their heights were estimated.")
    warn += [f"{t}: {x}" for t, x in r.warnings]
    for wt in warn:
        parts.append(f'<p style="background:#FFF4E0; color:#9A5B00; padding:6px;">⚠ {html.escape(wt)}</p>')
    mw, mh = map_size
    s = min(520 / mw, 470 / mh)
    parts += [f"{H2}Quantities</h2>", er._table(q, header=("Quantity", "Value", "Note"), big=True),
              '<h2 style="font-size:12pt; page-break-before: always;">Map</h2>',
              f'<p align="center"><img src="map.png" width="{int(mw * s)}" height="{int(mh * s)}"></p>',
              '<p style="color:#5D677A; font-size:8pt;">The strip shows the ground worked, from where the sides meet '
              'the ground on the left to the right. Red = mostly dig, blue = mostly fill.</p>',
              f"{H2}Side view</h2>", '<p><img src="profile.png" width="520" height="179"></p>',
              (f'<p style="color:#5D677A; font-size:8pt;">{html.escape(vcurve_image_note(r))}</p>'
               if r.pvis else ""),
              f"{H2}Earth balance (mass haul)</h2>", '<p><img src="mass.png" width="520" height="150"></p>',
              '<p style="color:#5D677A; font-size:8pt;">Where the line rises, more soil is dug than the fill needs so '
              'far; where it falls, fill uses up soil. Between two balance points (circles) the dug soil can be '
              'moved to the fill without hauling in or out.</p>']
    if curves:
        parts.append('<h2 style="font-size:12pt; page-break-before: always;">Curves</h2>')
        parts.append(er._table([(c[0], c[3], c[4], c[5], c[6], c[9], c[10], c[11]) for c in curves],
                               ("8%", "14%", "12%", "12%", "14%", "14%", "14%", "12%"),
                               header=("No.", "Deflection °", "Radius m", "Tangent m", "Length m", "Start (PC)",
                                       "End (PT)", "Turns")))
        parts.append(f"{H2}Stations</h2>")
    else:
        parts.append('<h2 style="font-size:12pt; page-break-before: always;">Stations</h2>')
    if r.vcurves:
        parts.insert(-1, f"{H2}Vertical curves</h2>")
        parts.insert(-1, er._table(vcurve_html_rows(r), ("5%", "7%", "9%", "9%", "8%", "8%", "7%", "6%", "7%", "9%",
                                                         "9%", "16%"),
                                   header=("No.", "Type", "PVI", "PVI elev. m", "g1 %", "g2 %", "L m", "K", "K needed",
                                           "PVC", "PVT", "High / low point")))
        parts.insert(-1, f'<p style="color:#5D677A; font-size:8pt;">Design speed {r.speed} km/h. Parabolic curves '
                         'centred on the PVI; z = z_PVC + g1·x + (g2 − g1)·x² / (2L). Minimum K for this speed '
                         'from the table below (L = K × |A|, at least 0.6 × speed).</p>')
        kt = [(f"{v} km/h", f"{a} m", b, c, f"{vt.min_length(v):.0f} m") for v, (a, b, c) in vt.K_TABLE.items()]
        parts.insert(-1, er._table(kt, ("20%", "25%", "15%", "15%", "25%"),
                                   header=("Design speed", "Stopping sight distance", "K crest", "K sag",
                                           "Minimum length")))
        parts.insert(-1, f'<p style="color:#5D677A; font-size:8pt;">Reference: {html.escape(vt.AASHTO_SOURCE)}.</p>')
    rows = [(x[0], x[4] if x[4] is not None else "-", x[5], x[6] if x[6] is not None else "-", x[7], x[8],
             x[9], x[10], x[13]) for x in station_rows(r)]
    hdr = ("Station", "Ground m", "Road m", "Dig+/Fill− m", "Dig m²", "Fill m²", "Dig m³", "Fill m³", "Balance m³")
    parts.append(er._table(rows, ("12%", "11%", "11%", "12%", "9%", "9%", "11%", "11%", "14%"), header=hdr))
    parts.append('<h2 style="font-size:12pt; page-break-before: always;">Quantities by station</h2>')
    parts.append(er._table(quantity_rows(r), ("18%", "16%", "16%", "16%", "17%", "17%"),
                           header=("Station", "Distance m", "Cut area m²", "Fill area m²", "Cut volume m³",
                                   "Fill volume m³")))
    parts.append('<p style="color:#5D677A; font-size:8pt;">Distance and volumes are from the previous station '
                 '(average end-area: V = (A1 + A2) / 2 × L).</p>')
    for k in range(n_sections):
        parts.append(f'<h2 style="font-size:12pt; page-break-before: always;">Cross-sections'
                     f'{" (continued)" if k else ""}</h2>')
        if not k:
            parts.append('<p style="color:#5D677A; font-size:8pt;">Looking toward higher stations; offsets in metres, '
                         'left negative; true scale (1 m across = 1 m up). Brown = ground now, navy = finished road '
                         'and sides (bottom of the pavement under the road), red = dig, blue = fill. Green labels: '
                         'cross slopes and side slopes. The box beside each section gives its areas and the volumes '
                         'since the previous station.</p>')
        sw, sh = sheet_sizes[k] if k < len(sheet_sizes) else (1600, 1550)
        parts.append(f'<p><img src="sections{k}.png" width="520" height="{int(round(520 * sh / sw))}"></p>')
    if worked:
        parts.append('<h2 style="font-size:12pt; page-break-before: always;">How the numbers were computed '
                     '(worked example)</h2>')
        parts.append('<p style="font-size:8.5pt;">One line of each table recomputed by hand with this road\'s '
                     'numbers, so every other line can be trusted the same way. The full methods are in the app under '
                     '\u201cHow things are computed\u201d.</p>')
        for t in worked:
            parts.append(f'<p style="font-size:8.5pt;">\u2022 {html.escape(t)}</p>')
    parts += ['<h2 style="font-size:12pt; page-break-before: always;">Design inputs</h2>', er._table(inp, ("40%", "60%")),
              f"{H2}Data quality</h2>", er._table(quality, ("40%", "60%"))]
    if files:
        parts.append(f"{H2}Files in this package</h2>")
        parts.append(er._table([(os.path.basename(f), er.FILE_NOTES.get(os.path.splitext(f)[1].lower(), ""))
                                for f in files], ("50%", "50%")))
    parts.append(f"{H2}Notes and disclaimer</h2>")
    notes = list(ROAD_NOTES)
    if r.standard in rdm.STANDARDS:
        notes.insert(0, f"Road standard: {rdm.STANDARDS[r.standard]['label']}, from {rdm.DPWH_SOURCE}: "
                        + rdm.STANDARD_NOTES + " Design speed: " + rdm.STANDARDS[r.standard]["speed"] + ".")
    for p in notes + list(DISCLAIMER_PARAGRAPHS):
        parts.append(f'<p style="font-size:8pt; color:#5D677A;">{html.escape(p)}</p>')
    return "".join(parts)


def export_road(r: rdm.RoadResult, plan: dict, dem_path: str, ortho_path: str | None, out_dir: str, dst_crs: str,
                info: dict, job=None, gis_formats=("gpkg", "kml", "dxf"), progress=None) -> list[str]:
    from shapely.geometry import LineString, Point, Polygon
    from . import crs as crsmod
    progress = progress or (lambda p, s: None)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, ew.safe(plan.get("name", "road")).replace(" ", "_"))
    pts = [tuple(p) for p in plan["points"]]
    src = r.crs_wkt
    lonlat = ex.site_lonlat(dem_path)
    files: list[str] = []
    q = quantities(r)
    inp = [(k, er.source_label(plan.get("source", "dtm")) if k == "Heights from" else v) for k, v in r.inputs]
    quality = er.job_quality(job, r, plan.get("source", "dtm"), dem_path)
    meta = [("Project", info.get("project") or plan.get("name", "")), ("Location", info.get("location") or "-"),
            ("Prepared by", info.get("prepared_by") or "-"), ("Date", datetime.now().strftime("%d %B %Y")),
            ("Coordinates in files", crsmod.label(dst_crs))]
    title = f"Road earthworks estimate - {plan.get('name', '')}"
    progress(10, "GIS layers")
    st = r.stations
    centre = [ex.Feature(LineString(pts), {"name": plan.get("name", ""), "length_m": round(r.length, 2),
                                           "cut_m3": round(r.cut, 1), "fill_m3": round(r.fill, 1)})]
    spts = [ex.Feature(Point(s.x, s.y), {"station": s.label, "dist_m": round(s.s, 3),
                                         "ground_m": round(s.ground, 3) if s.ground == s.ground else None,
                                         "road_m": round(s.design, 3), "cut_m2": round(s.cut_area, 3),
                                         "fill_m2": round(s.fill_area, 3)}) for s in st]
    strips = []
    for a, b in zip(st[:-1], st[1:]):
        poly = Polygon([a.catch_xy(-1), b.catch_xy(-1), b.catch_xy(1), a.catch_xy(1)])
        if poly.is_valid and poly.area > 0:
            strips.append(ex.Feature(poly, {"from": a.label, "to": b.label, "cut_m3": round(b.cut_vol, 2),
                                            "fill_m3": round(b.fill_vol, 2),
                                            "type": "dig" if b.cut_vol >= b.fill_vol else "fill"}))
    centre[0] = ex.Feature(LineString([tuple(p) for p in r.centre]), centre[0].properties)
    edges = [ex.Feature(LineString([s.catch_xy(k) for s in st]), {"side": "left" if k < 0 else "right"})
             for k in (-1, 1)]
    xs = [ex.Feature(LineString([s.catch_xy(-1), s.catch_xy(1)]), {"station": s.label}) for s in st]
    layers = [("centre_line", centre), ("stations", spts), ("works_strips", strips), ("works_edges", edges),
              ("cross_section_lines", xs)]
    for fmt in gis_formats:
        extn = ex.VECTOR_FORMATS[fmt][1]
        for lname, feats in layers:
            if feats:
                path = base + extn if fmt == "gpkg" else f"{base}_{lname}{extn}"
                files += ex.write_vector(feats, path, fmt, src, dst_crs, layer=lname, lonlat=lonlat,
                                         dxf_layer_field="station" if lname in ("stations", "cross_section_lines")
                                         else None, append=(fmt == "gpkg"))
    progress(35, "Tables")
    srows, xrows = station_rows(r), section_rows(r)
    crows = rdm.curve_rows(r.alignment) if r.alignment is not None else []
    vrows = vt.rows(r.vcurves, rdm.station_label)
    qrows = quantity_rows(r)
    rd = rdm.design_from(plan)
    worked = worked_example(r, rd.shrink_pct)
    notes = list(ROAD_NOTES + DISCLAIMER_PARAGRAPHS)
    files.append(write_workbook(base + "_quantities.xlsx", title, meta, q, inp, srows, xrows, notes, crows, vrows,
                                qrows, worked))
    if crows:
        files.append(er.write_csv(base + "_curves.csv", rdm.CURVE_HEADER, crows))
    if vrows:
        files.append(er.write_csv(base + "_vertical_curves.csv", vt.ROW_HEADER, vrows))
    files.append(er.write_csv(base + "_quantities_by_station.csv", QTY_HEADER, qrows))
    progress(45, "Civil 3D (LandXML) and CAD drawing")
    from . import cad, landxml
    files.append(landxml.road_xml(base + "_civil3d.xml", r, plan.get("name", "Road"), dem_path))
    files.append(cad.road_dxf(base + "_drawing.dxf", r, plan.get("name", "Road")))
    files.append(er.write_csv(base + "_stations.csv", STATION_HEADER, srows))
    files.append(er.write_csv(base + "_cross_sections.csv", ("Station", "Offset_m", "Ground_m", "Design_m"), xrows))
    files.append(er.write_csv(base + "_quantities.csv", ("Quantity", "Value", "Note"), q))
    with open(base + ".plan.json", "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=1, ensure_ascii=False)
    files.append(base + ".plan.json")
    progress(55, "Pictures")
    mimg = map_image(r, pts, dem_path, ortho_path)
    pimg = profile_image(r)
    himg = mass_haul_image(r)
    sheets = sections_sheets(r, rd)
    mimg.save(base + "_map.png")
    pimg.save(base + "_side_view.png")
    himg.save(base + "_mass_haul.png")
    files += [base + "_map.png", base + "_side_view.png", base + "_mass_haul.png"]
    readme = base + "_README.txt"
    pdf = base + "_report.pdf"
    listed = [f for f in dict.fromkeys([pdf] + files + [readme])
              if not f.lower().endswith((".aux.xml", ".pgw", ".wld", ".jgw", ".tfw"))]
    progress(75, "PDF report")
    images = {"map.png": mimg, "profile.png": pimg, "mass.png": himg}
    images.update({f"sections{k}.png": im for k, im in enumerate(sheets)})
    er.write_pdf(pdf, report_html(r, plan, meta, q, inp, quality, listed, mimg.size, len(sheets), crows,
                                  [im.size for im in sheets], worked), images)
    files.insert(0, pdf)
    rn = list(ROAD_NOTES)
    for fmt in gis_formats:
        n = ex.format_note(fmt, dst_crs)
        if n and n not in rn:
            rn.append(n)
    ex.write_export_readme(readme, f"{APP_NAME} {title}", dst_crs, lonlat, files, rn)
    files.append(readme)
    progress(100, "Done")
    return list(dict.fromkeys(files))
