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
from . import earthworks as ew, earthworks_report as er, exporters as ex, road as rdm
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
    # grade labels between bends of the road height
    sd = np.array([s.s for s in r.stations])
    sz = np.array([s.design for s in r.stations])
    gr = np.diff(sz) / np.maximum(np.diff(sd), 1e-9) * 100
    runs, a = [], 0                       # stretches of similar grade (within 1%)
    for i in range(1, len(gr) + 1):
        if i == len(gr) or abs(gr[i] - gr[a]) > 1.0:
            runs.append((a, i))
            a = i
    last_x = -1e9
    for a, b in sorted(runs, key=lambda ab: sd[ab[1]] - sd[ab[0]], reverse=True)[:10]:
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


def section_image(st: rdm.Station, width=860, height=420, vex: float = 1.0) -> Image.Image:
    im = Image.new("RGB", (width, height), "white")
    dr = ImageDraw.Draw(im)
    o, g, d = st.offsets, st.ground_xs, st.design_xs
    f, fb = _font(22), _font(26, True)
    dr.text((14, 6), f"{st.label}", font=fb, fill=INK)
    dr.text((150, 10), f"dig {st.cut_area:.2f} m²  fill {st.fill_area:.2f} m²  centre {st.depth:+.2f} m",
            font=f, fill=MUTED)
    if o is None or not np.isfinite(g).any():
        return im
    zs = np.concatenate([g[np.isfinite(g)], d[np.isfinite(d)]])
    z0, z1 = float(zs.min()), float(zs.max())
    span_o = float(o[-1] - o[0]) or 1.0
    L, R, T, B = 80, 16, 48, 40
    pw, ph = width - L - R, height - T - B
    # heights exaggerated (up to 5x) so low sections stay readable; the factor is printed
    sx = pw / span_o
    sc = min(ph * 0.8 / max(z1 - z0, 0.3), 5 * sx)
    sc = max(sc, sx)
    vex = sc / sx
    zc = (z0 + z1) / 2
    if vex > 1.05:
        dr.text((width - 190, T + 2), f"heights x{vex:.1f}", font=f, fill=MUTED)

    def P(oi, zi):
        return (L + (oi - o[0]) / span_o * pw, T + ph / 2 - (zi - zc) * sc)
    for k in range(-2, 3):
        zz = zc + k * ph / 4 / sc
        y = T + ph / 2 - k * ph / 4
        dr.line([(L, y), (L + pw, y)], fill=(232, 235, 241))
        dr.text((4, y - 12), f"{zz:.1f}", font=f, fill=MUTED)
    for oo in np.arange(math.ceil(o[0] / 5) * 5, o[-1] + 1e-9, 5):
        X = P(oo, zc)[0]
        dr.line([(X, T + ph), (X, T + ph + 5)], fill=MUTED)
        dr.text((X - 12, T + ph + 6), f"{oo:g}", font=f, fill=MUTED)
    for i in range(len(o) - 1):
        if not (np.isfinite(g[i]) and np.isfinite(d[i])):
            continue
        xa, xb = P(o[i], 0)[0], P(o[i + 1], 0)[0]
        ya, yb = P(0, g[i])[1], P(0, d[i])[1]
        if abs(ya - yb) < 0.5:
            continue
        col = CUT_RGB if g[i] > d[i] else FILL_RGB
        dr.rectangle([xa, min(ya, yb), max(xb, xa + 1), max(ya, yb)],
                     fill=tuple(int(c * 0.35 + 255 * 0.65) for c in col))
    gp = [P(a, b) for a, b in zip(o, g) if np.isfinite(b)]
    if len(gp) > 1:
        dr.line(gp, fill=GROUND_RGB, width=3)
    seg = []
    for a, b in zip(o, d):
        if np.isfinite(b):
            seg.append(P(a, b))
        elif len(seg) > 1:
            dr.line(seg, fill=NAVY, width=4)
            seg = []
    if len(seg) > 1:
        dr.line(seg, fill=NAVY, width=4)
    X = P(0, zc)[0]
    dr.line([(X, T), (X, T + ph)], fill=(200, 205, 215), width=1)
    return im


def sections_sheets(r: rdm.RoadResult, per_page: int = 8) -> list[Image.Image]:
    imgs = [section_image(s) for s in r.stations]
    pages = []
    for k in range(0, len(imgs), per_page):
        chunk = imgs[k:k + per_page]
        w, h = chunk[0].size
        sheet = Image.new("RGB", (2 * w + 20, 4 * h + 30), "white")
        for i, im in enumerate(chunk):
            sheet.paste(im, ((i % 2) * (w + 20), (i // 2) * (h + 10)))
        pages.append(sheet)
    return pages


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
def write_workbook(path, title, meta, q, inp, stations, sections, notes, curves=()):
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
                curves=()) -> str:
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
    rows = [(x[0], x[4] if x[4] is not None else "-", x[5], x[6] if x[6] is not None else "-", x[7], x[8],
             x[9], x[10], x[13]) for x in station_rows(r)]
    hdr = ("Station", "Ground m", "Road m", "Dig+/Fill− m", "Dig m²", "Fill m²", "Dig m³", "Fill m³", "Balance m³")
    parts.append(er._table(rows, ("12%", "11%", "11%", "12%", "9%", "9%", "11%", "11%", "14%"), header=hdr))
    for k in range(n_sections):
        parts.append(f'<h2 style="font-size:12pt; page-break-before: always;">Cross-sections'
                     f'{" (continued)" if k else ""}</h2>')
        if not k:
            parts.append('<p style="color:#5D677A; font-size:8pt;">Looking toward higher stations; offsets in metres, '
                         'left negative. Brown = ground now, navy = finished road and sides (bottom of the pavement '
                         'under the road).</p>')
        parts.append(f'<p><img src="sections{k}.png" width="520" height="505"></p>')
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
    notes = list(ROAD_NOTES + DISCLAIMER_PARAGRAPHS)
    files.append(write_workbook(base + "_quantities.xlsx", title, meta, q, inp, srows, xrows, notes, crows))
    if crows:
        files.append(er.write_csv(base + "_curves.csv", rdm.CURVE_HEADER, crows))
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
    sheets = sections_sheets(r)
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
    er.write_pdf(pdf, report_html(r, plan, meta, q, inp, quality, listed, mimg.size, len(sheets), crows), images)
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
