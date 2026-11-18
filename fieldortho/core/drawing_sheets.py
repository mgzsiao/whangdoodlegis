"""Drawing sheets of the road and canal reports (1.2), drawn with ReportLab on landscape pages.

Plan and profile: the plan on top (drone map turned so the road runs left to right, works strip, centre line,
stations, curves, BEGIN / END with coordinates, north arrow, scale bar), the profile below at the same
horizontal scale (natural ground dashed, finished grade solid, cut red / fill blue, PVIs with their curves,
grades) and a data band (station, OGE, FGE, cut / fill). Long lines continue on the next sheet at a MATCH
LINE. Horizontal scale by paper (A3 1:1250, A4 / long bond 1:2000, larger when the whole line fits),
vertical scale one tenth of it.

Cross-sections: every station, true scale, two columns numbered down the left column and then the right,
four per column when they fit at the paper's smallest scale (A3 1:200, A4 / long bond 1:150), else three.
Each shows the natural ground, the finished section with its layers, cut and fill, slope labels and the
elevation over offset of each break point in staggered rows; BEGINNING and END are marked.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from reportlab.lib.colors import Color, white
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth

from . import road as rdm
from .report_style import (CUT, FILL, GROUND, HEAD, HEAD_B, INK, INK2, MONO, MONO_M, MUTED, NAVY,
                           RULE, SANS, SANS_B, Paper, _clip, dms, jpeg_reader, kst)

SCALES = (20, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 750, 1000, 1250, 1500, 2000, 2500, 5000, 10000,
          20000)
CUT_SOFT = Color(196 / 255, 40 / 255, 32 / 255, 0.16)
FILL_SOFT = Color(30 / 255, 90 / 255, 170 / 255, 0.16)
WATER = Color(74 / 255, 144 / 255, 200 / 255, 0.35)
GRID = Color(0.86, 0.88, 0.92)
AMBER_LINE = Color(0.96, 0.72, 0.25)
GRID_MAJ = Color(0.76, 0.79, 0.85)


def nice_scale(needed: float) -> int:
    for s in SCALES:
        if s >= needed - 1e-9:
            return s
    return int(math.ceil(needed / 10000.0) * 10000)


@dataclass
class EndPoint:
    name: str                     # BEGIN / END
    s: float
    x: float
    y: float
    lat: float
    lon: float
    north: float
    east: float
    z: float                      # finished level (road) or bed (canal)
    grid: str = ""                # name of the N / E grid

    def lines(self, elev: bool, canal: bool = False) -> list[str]:
        out = [f"{self.name} {kst(self.s)}", dms(self.lat, "N", "S"), dms(self.lon, "E", "W"),
               f"N {self.north:,.3f}", f"E {self.east:,.3f}"]
        if elev and self.z == self.z:
            out.append(f"{'BED' if canal else 'EL'} {self.z:.3f}")
        return out


@dataclass
class Ctx:
    paper: Paper
    project: str
    report_no: str
    canal: bool
    design: object                                # RoadDesign or CanalDesign
    ends: list                                    # [EndPoint begin, EndPoint end]
    show_elev: bool = False
    coord_note: str = ""
    background: object = None                     # fn(bounds, px) -> (PIL image, bounds) or None
    sheets_total: int = 0                         # set once all sheets are known
    lot: bool = False                             # lot / pad or pile sections (no road layers)
    first_sheet: int = 1
    header_right: str = ""


# ============================================================================ small drawing helpers
def _frame(W, H):
    """Drawing area inside the header band and the footer line: (x0, y0, x1, y1)."""
    return 8 * mm, 11.6 * mm, W - 8 * mm, H - 8.6 * mm


def _poly(c, pts, stroke=1, fill=0):
    p = c.beginPath()
    p.moveTo(*pts[0])
    for q in pts[1:]:
        p.lineTo(*q)
    p.close()
    c.drawPath(p, stroke=stroke, fill=fill)
    return p


def _path(pts, close=False):
    from reportlab.pdfgen.pathobject import PDFPathObject
    p = PDFPathObject()
    p.moveTo(*pts[0])
    for q in pts[1:]:
        p.lineTo(*q)
    if close:
        p.close()
    return p


def _line(c, pts):
    pts = [q for q in pts if q is not None and all(map(math.isfinite, q))]
    if len(pts) >= 2:
        c.drawPath(_path(pts), stroke=1, fill=0)


def _segments(xs, ys):
    """Finite runs of a polyline."""
    run = []
    for x, y in zip(xs, ys):
        if math.isfinite(x) and math.isfinite(y):
            run.append((x, y))
        elif run:
            yield run
            run = []
    if run:
        yield run


def hatch(c, pts, step=1.4 * mm, angle=45.0, color=INK, lw=0.35, fill=None, dots=False):
    """Fill a polygon (page points) with a colour and parallel hatch lines (or dots)."""
    if len(pts) < 3:
        return
    c.saveState()
    if fill is not None:
        c.setFillColor(fill)
        c.drawPath(_path(pts, True), stroke=0, fill=1)
    c.clipPath(_path(pts, True), stroke=0, fill=0)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(lw)
    if dots:
        y = y0 + step / 2
        k = 0
        while y < y1:
            x = x0 + (step / 2 if k % 2 else 0)
            while x < x1:
                c.circle(x, y, 0.22, stroke=0, fill=1)
                x += step
            y += step * 0.8
            k += 1
    else:
        a = math.radians(angle)
        dx, dy = math.cos(a), math.sin(a)
        nx, ny = -dy, dx
        span = math.hypot(x1 - x0, y1 - y0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        t = -span
        while t <= span:
            px, py = cx + nx * t, cy + ny * t
            c.line(px - dx * span, py - dy * span, px + dx * span, py + dy * span)
            t += step
    c.restoreState()


def _text_box(c, x, y, lines, size=6.6, font=MONO, bold_first=True, pad=1.6 * mm, align="left", border=INK):
    """A white box with lines of text; (x, y) = bottom left (align left) or bottom right (align right)."""
    lh = size * 1.32
    w = max(stringWidth(t, MONO_M if (bold_first and i == 0) else font, size) for i, t in enumerate(lines)) + 2 * pad
    h = lh * len(lines) + 2 * pad - (lh - size)
    if align == "right":
        x -= w
    c.setFillColor(white)
    c.setStrokeColor(border)
    c.setLineWidth(0.6)
    c.rect(x, y, w, h, stroke=1, fill=1)
    c.setFillColor(INK)
    for i, t in enumerate(lines):
        c.setFont(MONO_M if (bold_first and i == 0) else font, size)
        c.drawString(x + pad, y + h - pad - size * 0.8 - i * lh, t)
    return x, y, w, h


def title_block(c, x1, y0, w, h, title, scale_txt, sub, right_sub):
    x0 = x1 - w
    c.setFillColor(white)
    c.setStrokeColor(INK)
    c.setLineWidth(0.9)
    c.rect(x0, y0, w, h, stroke=1, fill=1)
    xm, ym = x0 + w * 0.64, y0 + h / 2
    c.setLineWidth(0.5)
    c.line(x0, ym, x1, ym)
    c.line(xm, y0, xm, y0 + h)
    c.setFillColor(NAVY)
    c.setFont(HEAD_B, 9.6)
    c.drawString(x0 + 2.6 * mm, ym + h / 4 - 3.3, _clip(title, HEAD_B, 9.6, xm - x0 - 4 * mm))
    c.setFillColor(INK)
    c.setFont(MONO, 6.8)
    c.drawString(xm + 2.2 * mm, ym + h / 4 - 2.4, scale_txt)
    c.setFont(SANS, 6.8)
    c.drawString(x0 + 2.6 * mm, y0 + h / 4 - 2.4, _clip(sub, SANS, 6.8, xm - x0 - 4 * mm))
    c.drawString(xm + 2.2 * mm, y0 + h / 4 - 2.4, _clip(right_sub, SANS, 6.8, x1 - xm - 3 * mm))


def _runs(d, a, b):
    """Split a(d) vs b(d) into runs where a > b (sign +1) or a < b (-1), with exact crossings.
    Yields (sign, xs, a_part, b_part)."""
    diff = a - b
    i, n = 0, len(d)
    while i < n:
        if not np.isfinite(diff[i]) or abs(diff[i]) < 1e-12:
            i += 1
            continue
        sg = 1 if diff[i] > 0 else -1
        xs, ta, tb = [], [], []
        if i > 0 and np.isfinite(diff[i - 1]):
            t = diff[i - 1] / (diff[i - 1] - diff[i]) if diff[i - 1] != diff[i] else 0.0
            xc = d[i - 1] + t * (d[i] - d[i - 1])
            zc = b[i - 1] + t * (b[i] - b[i - 1])
            xs.append(xc), ta.append(zc), tb.append(zc)
        j = i
        while j < n and np.isfinite(diff[j]) and diff[j] * sg > 0:
            xs.append(d[j]), ta.append(a[j]), tb.append(b[j])
            j += 1
        if j < n and np.isfinite(diff[j]):
            t = diff[j - 1] / (diff[j - 1] - diff[j]) if diff[j - 1] != diff[j] else 0.0
            xc = d[j - 1] + t * (d[j] - d[j - 1])
            zc = b[j - 1] + t * (b[j] - b[j - 1])
            xs.append(xc), ta.append(zc), tb.append(zc)
        if len(xs) >= 2:
            yield sg, np.array(xs), np.array(ta), np.array(tb)
        i = j


# ============================================================================ plan and profile
@dataclass
class _PP:
    s0: float
    s1: float
    hden: int
    vden: int
    zlo: float
    zhi: float


def _centre_param(r):
    c = np.asarray(r.centre if r.centre is not None else [(s.x, s.y) for s in r.stations], float)
    d = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(c, axis=0).T))])
    if d[-1] > 0:
        d *= r.length / d[-1]
    return c, d


def _at(c, d, s):
    return float(np.interp(s, d, c[:, 0])), float(np.interp(s, d, c[:, 1]))


def plan_profile_sheets(r, ctx: Ctx) -> list:
    """[(draw fn, sheet title, station range text)] for the plan and profile sheets."""
    W, H = ctx.paper.sheet
    x0f, y0f, x1f, y1f = _frame(W, H)
    px0, px1 = x0f + 30 * mm, x1f - 15 * mm
    avail_m = (px1 - px0) / mm                       # mm of drawing width
    L = max(r.length, 1e-6)
    fit = [s for s in SCALES if 200 <= s <= ctx.paper.pp_scale and L * 1000 / s <= avail_m]
    if fit:
        hden, chunks = min(fit), [(0.0, L)]
    else:
        hden = ctx.paper.pp_scale
        per = avail_m * hden / 1000
        step = 50.0 if per > 150 else 10.0
        per = max(step, math.floor(per / step) * step)
        n = int(math.ceil(L / per - 1e-9))
        chunks = [(i * per, min(L, (i + 1) * per)) for i in range(n)]
    out = []
    for k, (s0, s1) in enumerate(chunks):
        def fn(c, w, h, s0=s0, s1=s1, k=k, n=len(chunks)):
            _draw_pp(c, w, h, r, ctx, s0, s1, hden, k, n)
        out.append((fn, f"Plan and profile, {kst(s0)} to {kst(s1)}"))
    return out


def _layout_pp(W, H, paper):
    x0f, y0f, x1f, y1f = _frame(W, H)
    big = paper.sheet_name == "A3"
    fs = 6.4 if big else 5.8
    h_st = stringWidth("K0+000.000", MONO, fs) + 3.2 * mm
    h_v = stringWidth("0000.000", MONO, fs) + 3.0 * mm
    bottom = (18 if big else 15) * mm
    data_y0 = y0f + bottom
    data_h = h_st + 3 * h_v
    rest = y1f - (data_y0 + data_h) - 3 * mm
    plan_h = rest * (0.42 if big else 0.43)
    prof_y0 = data_y0 + data_h + 3 * mm
    prof_y1 = y1f - plan_h - 3 * mm
    return dict(fs=fs, h_st=h_st, h_v=h_v, bottom=bottom, data_y0=data_y0, data_h=data_h, plan_y0=y1f - plan_h,
                plan_y1=y1f, prof_y0=prof_y0, prof_y1=prof_y1, labels_h=(15 if big else 12) * mm)


def _draw_pp(c, W, H, r, ctx: Ctx, s0, s1, hden, k, n):
    x0f, y0f, x1f, y1f = _frame(W, H)
    lay = _layout_pp(W, H, ctx.paper)
    px0, px1 = x0f + 30 * mm, x1f - 15 * mm
    kpt = 1000.0 / hden * mm                         # points per metre along the line
    pad = ((px1 - px0) - (s1 - s0) * kpt) / 2 if n == 1 else 0.0

    def X(s):
        return px0 + pad + (s - s0) * kpt
    # ---------------------------------------------------------------- profile window and vertical scale
    d, g, z = np.asarray(r.prof_d, float), np.asarray(r.prof_g, float), np.asarray(r.prof_z, float)
    sel = (d >= s0 - 1e-6) & (d <= s1 + 1e-6)
    top = z + ctx.design.depth if ctx.canal else z
    vals = np.concatenate([g[sel], z[sel], top[sel]])
    vals = vals[np.isfinite(vals)]
    zmin, zmax = (float(vals.min()), float(vals.max())) if len(vals) else (0.0, 1.0)
    gy0, gy1 = lay["prof_y0"], lay["prof_y1"] - lay["labels_h"]
    grid_mm = (gy1 - gy0) / mm
    vden = max(hden // 10, 1)
    need = (zmax - zmin + 1.0) * 1000 / max(grid_mm, 1)
    if need > vden:
        vden = nice_scale(need)
    vpt = 1000.0 / vden * mm
    zspan = (gy1 - gy0) / vpt
    zstep = next(s for s in (0.25, 0.5, 1, 2, 5, 10, 20, 50, 100) if s * vpt >= 3.6 * mm)
    zlo = math.floor(((zmin + zmax) / 2 - zspan / 2) / zstep) * zstep
    if zlo + zspan < zmax + 0.2:
        zlo = zmax + 0.2 - zspan
    if zlo > zmin - 0.2:
        zlo = zmin - 0.2

    def Y(zz):
        return gy0 + (zz - zlo) * vpt
    c.setStrokeColor(INK)
    c.setLineWidth(0.9)
    c.rect(x0f, y0f, x1f - x0f, y1f - y0f)
    _draw_plan(c, r, ctx, s0, s1, kpt, X, x0f, lay["plan_y0"], x1f, lay["plan_y1"])
    # ---------------------------------------------------------------- profile grid
    c.setFont(HEAD, 8.6)
    c.setFillColor(NAVY)
    c.drawString(x0f + 3 * mm, lay["prof_y1"] - 4.2 * mm, "PROFILE")
    c.setFont(SANS, 6.6)
    c.setFillColor(MUTED)
    c.drawString(x0f + 3 * mm, lay["prof_y1"] - 7.4 * mm,
                 "along the canal centre line" if ctx.canal else "along the centre line")
    xa, xb = X(s0), X(s1)
    c.setLineWidth(0.3)
    zz = math.ceil(zlo / zstep) * zstep
    c.setFont(MONO, 6)
    while zz <= zlo + zspan + 1e-9:
        y = Y(zz)
        major = abs(zz / (zstep * 5) - round(zz / (zstep * 5))) < 1e-6
        c.setStrokeColor(GRID_MAJ if major else GRID)
        c.line(xa, y, xb, y)
        c.setFillColor(INK2)
        lbl = f"{zz:.0f}" if zstep >= 1 else f"{zz:.2f}"
        c.drawRightString(xa - 1.6 * mm, y - 2, lbl)
        c.drawString(xb + 1.6 * mm, y - 2, lbl)
        zz += zstep
    st = [s_ for s_ in r.stations if s0 - 1e-6 <= s_.s <= s1 + 1e-6]
    shown = _pick_stations(st, s0, s1, X, stringWidth("0", MONO, lay["fs"]) * 1.0 + 1.6 * mm)
    for s_ in shown:
        c.setStrokeColor(GRID)
        c.line(X(s_.s), gy0, X(s_.s), gy1)
    c.setStrokeColor(INK)
    c.setLineWidth(0.7)
    c.line(xa, gy0, xa, gy1)
    c.line(xb, gy0, xb, gy1)
    # ---------------------------------------------------------------- cut / fill, ground, design
    ds = d[sel]
    if len(ds) >= 2:
        upper = top[sel] if ctx.canal else z[sel]
        for sg, xs, ta, tb in _runs(ds, g[sel], upper):
            pts = [(X(a), Y(b)) for a, b in zip(xs, ta)] + [(X(a), Y(b)) for a, b in zip(xs[::-1], tb[::-1])]
            c.setFillColor(CUT_SOFT if sg > 0 else FILL_SOFT)
            c.drawPath(_path(pts, True), stroke=0, fill=1)
        c.setStrokeColor(GROUND)
        c.setLineWidth(1.0)
        c.setDash(3, 2)
        for run in _segments([X(a) for a in ds], [Y(b) for b in g[sel]]):
            _line(c, run)
        c.setDash()
        c.setStrokeColor(INK)
        c.setLineWidth(1.3)
        for run in _segments([X(a) for a in ds], [Y(b) for b in z[sel]]):
            _line(c, run)
        if ctx.canal:
            c.setStrokeColor(NAVY)
            c.setLineWidth(0.7)
            _line(c, [(X(a), Y(b)) for a, b in zip(ds, top[sel])])
            yd = getattr(r, "y_design", float("nan"))
            if yd == yd and yd > 0:
                c.setStrokeColor(Color(0.29, 0.56, 0.78))
                c.setDash(4, 2)
                _line(c, [(X(a), Y(b + yd)) for a, b in zip(ds, z[sel])])
                c.setDash()
    if not ctx.canal:
        _draw_vertical(c, r, s0, s1, X, Y, gy1, lay["prof_y1"], ctx)
    # ---------------------------------------------------------------- BEGIN / END / match lines on the profile
    for e in ctx.ends:
        if s0 - 1e-6 <= e.s <= s1 + 1e-6:
            zl = float(np.interp(e.s, d, z))
            _end_on_profile(c, X(e.s), gy0, gy1, e.lines(ctx.show_elev, ctx.canal), left=(e.name == "BEGIN"),
                            at_top=Y(zl) < (gy0 + gy1) / 2)
    for sm in (s0, s1):
        if 1e-6 < sm < r.length - 1e-6:
            c.setStrokeColor(INK)
            c.setLineWidth(1.2)
            c.setDash(6, 2)
            c.line(X(sm), gy0, X(sm), gy1)
            c.setDash()
            c.saveState()
            c.translate(X(sm) + (2.6 * mm if sm == s0 else -1.4 * mm), gy0 + 2 * mm)
            c.rotate(90)
            c.setFont(MONO_M, 6.8)
            c.setFillColor(INK)
            c.drawString(0, 0, f"MATCH LINE {kst(sm)}")
            c.restoreState()
    # ---------------------------------------------------------------- data band
    _data_band(c, r, ctx, lay, shown, X, x0f, xa, xb, x1f)
    # ---------------------------------------------------------------- notes and title block
    big = ctx.paper.sheet_name == "A3"
    tbw, tbh = (128 if big else 104) * mm, (15 if big else 12.5) * mm
    notes = [f"Coordinates: {ctx.coord_note}." if ctx.coord_note else "",
             "Plan and profile share the horizontal scale, so each station lines up"
             + (". The line continues on the next sheet at a MATCH LINE." if n > 1 else "."),
             ("Cut red, bank (fill) blue · natural ground dashed, canal bed solid, top of lining thin navy, water at "
              "the design flow dashed blue." if ctx.canal else
              "Cut red, fill blue · natural ground dashed, finished grade solid"
              + (f" · vertical curves at least {ctx.design.vc_min:g} m (PVC to PVT), K from AASHTO 2018."
                 if getattr(ctx.design, "vcurves", False) and getattr(ctx.design, "vc_min", 0) else "."))]
    c.setFont(SANS, 6.6 if big else 6.0)
    c.setFillColor(INK2)
    ny = y0f + lay["bottom"] - 5 * mm
    for t in [t for t in notes if t]:
        c.drawString(x0f + 3 * mm, ny, _clip(t, SANS, 6.6, x1f - x0f - tbw - 8 * mm))
        ny -= 3.4 * mm
    sheet_no = ctx.first_sheet + k
    title_block(c, x1f, y0f, tbw, tbh, "PLAN AND PROFILE",
                f"{ctx.paper.sheet_name} · H 1:{hden:,} V 1:{vden:,}",
                f"{ctx.project} · {kst(s0)} to {kst(s1)}",
                f"Sheet {sheet_no} of {ctx.sheets_total}" if ctx.sheets_total else "")


def _pick_stations(st, s0, s1, X, min_gap):
    """Stations to label in the data band: the ends always, the others when there is room."""
    if not st:
        return []
    keep = [st[0]]
    last = st[-1]
    for s_ in st[1:-1]:
        if X(s_.s) - X(keep[-1].s) >= min_gap and X(last.s) - X(s_.s) >= min_gap:
            keep.append(s_)
    if last is not keep[-1]:
        keep.append(last)
    return keep


def _data_band(c, r, ctx, lay, shown, X, x0f, xa, xb, x1f):
    fs = lay["fs"]
    rows = [("STATION", lay["h_st"]), ("OGE (m)", lay["h_v"])]
    if ctx.canal:
        rows += [("BED (m)", lay["h_v"]), ("TOP OF LINING (m)", lay["h_v"])]
    else:
        rows += [("FGE (m)", lay["h_v"]), ("CUT + / FILL − (m)", lay["h_v"])]
    y_top = lay["data_y0"] + lay["data_h"]
    x_end = min(x1f - 2 * mm, xb + 12 * mm)
    c.setStrokeColor(INK)
    c.setLineWidth(0.7)
    c.rect(x0f + 2 * mm, lay["data_y0"], x_end - x0f - 2 * mm, lay["data_h"])
    c.line(xa - 4 * mm, lay["data_y0"], xa - 4 * mm, y_top)
    y = y_top
    for i, (label, h) in enumerate(rows):
        y -= h
        if i < len(rows) - 1:
            c.setLineWidth(0.5)
            c.line(x0f + 2 * mm, y, x_end, y)
        c.setFont(SANS_B, 6.6)
        c.setFillColor(INK)
        c.drawString(x0f + 4 * mm, y + h / 2 - 2.3, label)
        for s_ in shown:
            if i == 0:
                txt, col = kst(s_.s), INK
            elif i == 1:
                txt, col = (f"{s_.ground:.3f}" if s_.ground == s_.ground else "–"), INK
            elif i == 2:
                txt, col = f"{s_.design:.3f}", INK
            elif ctx.canal:
                txt, col = f"{s_.design + ctx.design.depth:.3f}", INK
            else:
                dv = s_.depth
                txt = "–" if dv != dv else ("0.000" if abs(dv) < 0.0005 else f"{dv:+.3f}".replace("-", "−"))
                col = CUT if dv == dv and dv > 0.0005 else (FILL if dv == dv and dv < -0.0005 else INK)
            c.saveState()
            c.translate(X(s_.s) + fs * 0.36, y + 1.6 * mm)
            c.rotate(90)
            c.setFont(MONO, fs)
            c.setFillColor(col)
            c.drawString(0, 0, txt)
            c.restoreState()


def _draw_vertical(c, r, s0, s1, X, Y, gy1, top_y, ctx):
    pv = sorted(r.pvis or [], key=lambda q: q[0])
    if len(pv) < 2:
        return
    # grade line (tangents) and grades
    c.saveState()
    c.clipPath(_path([(X(s0), Y(-1e4)), (X(s1), Y(-1e4)), (X(s1), gy1), (X(s0), gy1)], True), stroke=0, fill=0)
    c.setStrokeColor(MUTED)
    c.setLineWidth(0.4)
    c.setDash(1.5, 1.5)
    _line(c, [(X(q[0]), Y(q[1])) for q in pv])
    c.setDash()
    c.restoreState()
    c.setFont(SANS_B, 6.8)
    c.setFillColor(INK)
    for a, b in zip(pv, pv[1:]):
        lo, hi = max(a[0], s0), min(b[0], s1)
        if hi - lo <= 0 or (b[0] - a[0]) <= 0:
            continue
        gpc = (b[1] - a[1]) / (b[0] - a[0]) * 100
        t = f"{gpc:+.2f}%".replace("-", "−")
        sm = (lo + hi) / 2
        if X(hi) - X(lo) > stringWidth(t, SANS_B, 6.8) + 4 * mm:
            tw = stringWidth(t, SANS_B, 6.8)
            half = (tw / 2) / (X(hi) - X(lo)) * (hi - lo)
            zm = a[1] + (sm - a[0] + (half if gpc > 0 else -half)) * gpc / 100
            c.setFillColor(Color(1, 1, 1, 0.8))
            c.rect(X(sm) - tw / 2 - 1, Y(zm) + 2.2 * mm - 1.5, tw + 2, 8.6, stroke=0, fill=1)
            c.setFillColor(INK)
            c.drawCentredString(X(sm), Y(zm) + 2.2 * mm, t)
    # PVIs with their curves, labels staggered on two rows above the grid
    rows_end = [-1e9, -1e9]
    for vc in r.vcurves:
        if not (s0 - 1e-6 <= vc.pvi_s <= s1 + 1e-6):
            continue
        x, y = X(vc.pvi_s), Y(vc.pvi_z)
        c.setStrokeColor(INK)
        c.setFillColor(white)
        c.setLineWidth(0.6)
        c.circle(x, y, 1.0 * mm, stroke=1, fill=1)
        l1 = f"PVI {kst(vc.pvi_s)} · EL {vc.pvi_z:.3f}"
        if vc.length > 0:
            short = vc.length < vc.length_asked - 0.01
            l2 = f"L = {vc.length:.1f} m · K = {vc.k:.0f} ({vc.kind})" + (" · SHORTENED" if short else "")
        else:
            l2 = "no curve (small grade change)"
        w = max(stringWidth(l1, SANS, 6.4), stringWidth(l2, SANS, 6.4))
        row = 0 if x - w / 2 > rows_end[0] + 2 * mm else (1 if x - w / 2 > rows_end[1] + 2 * mm else 0)
        rows_end[row] = x + w / 2
        ly = top_y - (4.5 * mm if row == 0 else 10.5 * mm)
        c.setStrokeColor(MUTED)
        c.setLineWidth(0.35)
        c.line(x, y + 1.0 * mm, x, ly - 4.2 * mm)
        c.setFillColor(INK)
        c.setFont(SANS, 6.4)
        c.drawCentredString(x, ly, l1)
        c.setFillColor(Color(0.6, 0.16, 0.12) if vc.length > 0 and vc.length < vc.length_asked - 0.01 else INK2)
        c.drawCentredString(x, ly - 3.0 * mm, l2)
        if vc.length > 0:
            for nm, sx in (("PVC", vc.pvi_s - vc.length / 2), ("PVT", vc.pvi_s + vc.length / 2)):
                if not (s0 <= sx <= s1):
                    continue
                zx = float(np.interp(sx, r.prof_d, r.prof_z))
                xx, yy = X(sx), Y(zx)
                c.setStrokeColor(INK)
                c.setLineWidth(0.5)
                c.line(xx, yy, xx, yy + 3 * mm)
                c.saveState()
                c.translate(xx + 2.1, yy + 3.6 * mm)
                c.rotate(90)
                c.setFont(MONO, 5.6)
                c.setFillColor(INK2)
                c.drawString(0, 0, f"{nm} {kst(sx)}")
                c.restoreState()


def _end_on_profile(c, x, gy0, gy1, lines, left, at_top=False):
    c.setStrokeColor(INK)
    c.setLineWidth(1.4)
    c.line(x, gy0, x, gy1)
    lh = 2.5 * mm
    bw = lh * len(lines) + 1.4 * mm
    bx = x + 1.2 * mm if left else x - 1.2 * mm - bw
    tw = max(stringWidth(t, MONO_M if i == 0 else MONO, 6.0) for i, t in enumerate(lines)) + 2.4 * mm
    y0 = gy1 - tw - 1 * mm if at_top else gy0 + 1 * mm
    c.setFillColor(Color(1, 1, 1, 0.85))
    c.rect(bx, y0, bw, tw, stroke=0, fill=1)
    for i, t in enumerate(lines):
        c.saveState()
        c.translate(bx + 0.9 * mm + (i + 0.78) * lh, y0 + 1.2 * mm)
        c.rotate(90)
        c.setFont(MONO_M if i == 0 else MONO, 6.0)
        c.setFillColor(INK)
        c.drawString(0, 0, t)
        c.restoreState()


def _inside(cands, x0, y0, x1, y1, m=5 * mm):
    """The first candidate point inside the panel (with a margin), else the first one moved inside."""
    for x, y in cands:
        if x0 + m <= x <= x1 - m and y0 + m <= y <= y1 - m:
            return x, y
    x, y = cands[0]
    return min(max(x, x0 + m + 6 * mm), x1 - m - 6 * mm), min(max(y, y0 + m), y1 - m)


def _draw_plan(c, r, ctx: Ctx, s0, s1, kpt, X, x0, y0, x1, y1):
    cl, cd = _centre_param(r)
    a, b = _at(cl, cd, s0), _at(cl, cd, s1)
    phi = math.atan2(b[1] - a[1], b[0] - a[0]) if math.dist(a, b) > 1e-6 else 0.0
    cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    cs, sn = math.cos(phi), math.sin(phi)

    def rel(x, y):
        dx, dy = x - cx, y - cy
        return kpt * (cs * dx + sn * dy), kpt * (-sn * dx + cs * dy)
    # vertical centring on the works of this stretch
    st_in = [s_ for s_ in r.stations if s0 - 1e-6 <= s_.s <= s1 + 1e-6]
    ys = [rel(*s_.catch_xy(k))[1] for s_ in st_in for k in (-1, 1)] + [rel(*_at(cl, cd, s))[1]
                                                                       for s in np.linspace(s0, s1, 30)]
    xm = (X(s0) + X(s1)) / 2
    ym = (y0 + y1) / 2 - (min(ys) + max(ys)) / 2 if ys else (y0 + y1) / 2

    def P(x, y):
        u, v = rel(x, y)
        return xm + u, ym + v

    def inv(X_, Y_):
        u, v = (X_ - xm) / kpt, (Y_ - ym) / kpt
        return cx + cs * u - sn * v, cy + sn * u + cs * v
    c.saveState()
    c.clipPath(_path([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], True), stroke=0, fill=0)
    c.setFillColor(Color(0.95, 0.96, 0.97))
    c.rect(x0, y0, x1 - x0, y1 - y0, stroke=0, fill=1)
    # background (drone map) turned with the plan
    if ctx.background is not None:
        corners = [inv(x0, y0), inv(x1, y0), inv(x1, y1), inv(x0, y1)]
        bx = [q[0] for q in corners]
        by = [q[1] for q in corners]
        px = int(min(2600, max(600, (x1 - x0) / 72 * 170)))
        try:
            got = ctx.background((min(bx), min(by), max(bx), max(by)), px)
        except Exception:
            got = None
        if got is not None:
            im, (bx0, by0, bx1, by1) = got
            c.saveState()
            c.transform(kpt * cs, -kpt * sn, kpt * sn, kpt * cs, xm, ym)
            c.drawImage(jpeg_reader(im, 82), bx0 - cx, by0 - cy, bx1 - bx0, by1 - by0)
            c.restoreState()
            c.setFillColor(Color(1, 1, 1, 0.18))
            c.rect(x0, y0, x1 - x0, y1 - y0, stroke=0, fill=1)
    st = r.stations
    near = [i for i, s_ in enumerate(st) if s0 - 60 <= s_.s <= s1 + 60]
    # works strips (cut red / fill blue) and edges
    for i in near:
        if i == 0:
            continue
        p_, q_ = st[i - 1], st[i]
        cut = (p_.cut_area + q_.cut_area) >= (p_.fill_area + q_.fill_area)
        c.setFillColor(Color(196 / 255, 40 / 255, 32 / 255, 0.33) if cut else Color(30 / 255, 90 / 255, 170 / 255, 0.33))
        c.drawPath(_path([P(*p_.catch_xy(-1)), P(*q_.catch_xy(-1)), P(*q_.catch_xy(1)), P(*p_.catch_xy(1))], True),
                   stroke=0, fill=1)
    c.setStrokeColor(INK)
    c.setLineWidth(0.6)
    c.setDash(2.4, 1.6)
    for k_ in (-1, 1):
        _line(c, [P(*st[i].catch_xy(k_)) for i in near])
    c.setDash()
    # carriageway / shoulder (road) or lining / berm (canal) edges
    des = ctx.design
    if ctx.canal:
        sq = math.sqrt(1 + des.z * des.z)
        edges = [(des.top_half + des.lining_t * sq, 0.8, NAVY), (des.top_half + des.lining_t * sq + des.berm_w, 0.4, INK2)]
        fill_band = (des.top_half, Color(0.29, 0.56, 0.78, 0.55))
    else:
        edges = [(None, 0.8, INK)] + ([("sh", 0.45, INK2)] if des.shoulders else [])
        fill_band = None
    for kind, lw, col in edges:
        c.setStrokeColor(col)
        c.setLineWidth(lw)
        for sign in (-1, 1):
            pts = []
            for i in near:
                s_ = st[i]
                if ctx.canal:
                    o = kind
                else:
                    sd = rdm.station_side(des, s_, sign)
                    o = sd.w2 if kind is None else sd.half(des)
                pts.append(P(s_.x + s_.ty * o * sign, s_.y - s_.tx * o * sign))
            _line(c, pts)
    if fill_band is not None:
        o, col = fill_band
        c.setFillColor(col)
        pl = [P(st[i].x - st[i].ty * o, st[i].y + st[i].tx * o) for i in near]
        pr = [P(st[i].x + st[i].ty * o, st[i].y - st[i].tx * o) for i in near]
        if len(pl) >= 2:
            c.drawPath(_path(pl + pr[::-1], True), stroke=0, fill=1)
    # centre line
    sel = (cd >= s0 - 60) & (cd <= s1 + 60)
    c.setStrokeColor(Color(0.78, 0.13, 0.12))
    c.setLineWidth(0.9)
    c.setDash([7, 1.6, 1.2, 1.6])
    _line(c, [P(*q) for q in cl[sel]])
    c.setDash()
    # station ticks and labels
    span = max(1.0, s1 - s0)
    every = next(v for v in (10, 20, 50, 100, 200, 500, 1000, 2000) if v * kpt >= 24 * mm or v >= span / 2)
    for i in near:
        s_ = st[i]
        if not (s0 - 1e-6 <= s_.s <= s1 + 1e-6):
            continue
        if ctx.canal:
            h = des.top_half + des.lining_t * 2 + des.berm_w
        else:
            h = max(rdm.station_side(des, s_, sg).half(des) for sg in (-1, 1))
        major = abs(s_.s / every - round(s_.s / every)) < 1e-6
        c.setStrokeColor(INK)
        c.setLineWidth(0.9 if major else 0.45)
        _line(c, [P(s_.x - s_.ty * (h + 0.6), s_.y + s_.tx * (h + 0.6)),
                  P(s_.x + s_.ty * (h + 0.6), s_.y - s_.tx * (h + 0.6))])
        if major or i in (0, len(st) - 1):
            gap = 5 * mm / kpt
            lx, ly = _inside([P(s_.x + s_.ty * o, s_.y - s_.tx * o)
                              for o in (s_.left_catch - gap, s_.right_catch + gap)], x0, y0, x1, y1)
            t = kst(s_.s)[:-4] if abs(s_.s - round(s_.s)) < 1e-6 else kst(s_.s)
            c.setFont(MONO_M, 6.6)
            w = stringWidth(t, MONO_M, 6.6)
            c.setFillColor(Color(1, 1, 1, 0.8))
            c.rect(lx - w / 2 - 1, ly - 2.6, w + 2, 8.4, stroke=0, fill=1)
            c.setFillColor(INK)
            c.drawCentredString(lx, ly, t)
    # curves
    al = r.alignment
    if al is not None:
        for j, cv in enumerate(al.curves, 1):
            sm = (cv.pc_s + cv.pt_s) / 2
            if not (s0 <= sm <= s1) or cv.radius <= 0:
                continue
            q = _at(cl, cd, sm)
            k_ = min(range(len(st)), key=lambda i: abs(st[i].s - sm))
            s_ = st[k_]
            out = 1 if cv.left else -1               # outside of a left turn is the right side
            o = (s_.right_catch if out > 0 else -s_.left_catch) + 9 * mm / kpt
            lx, ly = _inside([P(q[0] + s_.ty * o * out, q[1] - s_.tx * o * out)], x0, y0, x1, y1)
            t = f"C{j} · R {cv.radius:g} m · Δ {cv.delta:.1f}°"
            c.setFont(SANS_B, 6.4)
            w = stringWidth(t, SANS_B, 6.4)
            c.setFillColor(Color(1, 1, 1, 0.85))
            c.rect(lx - w / 2 - 1.5, ly - 2.4, w + 3, 8, stroke=0, fill=1)
            c.setFillColor(NAVY)
            c.drawCentredString(lx, ly, t)
    # match lines
    for sm in (s0, s1):
        if 1e-6 < sm < r.length - 1e-6:
            k_ = min(range(len(st)), key=lambda i: abs(st[i].s - sm))
            s_ = st[k_]
            q = _at(cl, cd, sm)
            o = max(abs(s_.left_catch), abs(s_.right_catch)) + 8
            c.setStrokeColor(INK)
            c.setLineWidth(1.2)
            c.setDash(6, 2)
            _line(c, [P(q[0] - s_.ty * o, q[1] + s_.tx * o), P(q[0] + s_.ty * o, q[1] - s_.tx * o)])
            c.setDash()
            lx, ly = _inside([P(q[0] + s_.ty * o, q[1] - s_.tx * o), P(q[0] - s_.ty * o, q[1] + s_.tx * o)],
                             x0, y0 + 14 * mm, x1, y1 - 8 * mm)
            t = f"MATCH LINE {kst(sm)}"
            c.setFont(MONO_M, 6.6)
            w = stringWidth(t, MONO_M, 6.6)
            c.setFillColor(Color(1, 1, 1, 0.85))
            c.rect(lx - w / 2 - 1, ly - 2.4, w + 2, 8.4, stroke=0, fill=1)
            c.setFillColor(INK)
            c.drawCentredString(lx, ly, t)
    c.restoreState()
    # BEGIN / END boxes (outside the clip so they can sit in the margins of the panel)
    for e in ctx.ends:
        if not (s0 - 1e-6 <= e.s <= s1 + 1e-6):
            continue
        ex, ey = P(e.x, e.y)
        lines = e.lines(ctx.show_elev, ctx.canal)
        left = e.name == "BEGIN"
        lh = 6.4 * 1.32
        bh = lh * len(lines) + 3.2 * mm - (lh - 6.4)
        by = min(max(ey - bh / 2, y0 + 2 * mm), y1 - bh - 2 * mm)
        if left:
            bx = max(x0 + 2 * mm, ex - 6 * mm - 40 * mm)
            _x, _y, w_, _h = _text_box(c, bx, by, lines)
            if bx + w_ > ex - 2 * mm:
                by = y1 - bh - 2 * mm if ey < (y0 + y1) / 2 else y0 + 2 * mm
                c.setFillColor(white)
            c.setStrokeColor(INK)
            c.setLineWidth(0.5)
            c.line(bx + w_, by + bh / 2, ex, ey)
        else:
            bx = min(x1 - 2 * mm, ex + 6 * mm + 40 * mm)
            _x, _y, w_, _h = _text_box(c, bx, by, lines, align="right")
            c.setStrokeColor(INK)
            c.setLineWidth(0.5)
            c.line(_x, by + bh / 2, ex, ey)
        c.setFillColor(white)
        c.setStrokeColor(INK)
        c.setLineWidth(0.8)
        c.circle(ex, ey, 1.2 * mm, stroke=1, fill=1)
    # frame, title, north arrow, scale bar
    c.setStrokeColor(NAVY)
    c.setLineWidth(0.8)
    c.rect(x0, y0, x1 - x0, y1 - y0)
    c.setFillColor(white)
    c.rect(x0, y1 - 7 * mm, 18 * mm, 7 * mm, stroke=0, fill=1)
    c.setFont(HEAD, 8.6)
    c.setFillColor(NAVY)
    c.drawString(x0 + 3 * mm, y1 - 5 * mm, "PLAN")
    ax, ay = x1 - 12 * mm, y1 - 13 * mm
    nx, ny = sn, cs                                    # grid north turned with the plan
    c.setFillColor(Color(1, 1, 1, 0.85))
    c.circle(ax, ay, 7.5 * mm, stroke=0, fill=1)
    tip = (ax + nx * 6 * mm, ay + ny * 6 * mm)
    tail = (ax - nx * 4.5 * mm, ay - ny * 4.5 * mm)
    side = (-ny * 2.4 * mm, nx * 2.4 * mm)
    c.setFillColor(INK)
    c.drawPath(_path([tip, (tail[0] + side[0], tail[1] + side[1]), (ax - nx * 2 * mm, ay - ny * 2 * mm),
                      (tail[0] - side[0], tail[1] - side[1])], True), stroke=0, fill=1)
    c.setFont(SANS_B, 7)
    c.drawCentredString(ax - nx * 7.6 * mm, ay - ny * 7.6 * mm - 2.4, "N")
    target = 50 * mm / kpt
    bar = next(v for v in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000) if v >= target * 0.6)
    bw = bar * kpt
    pts_ = [P(*s_.catch_xy(k_)) for s_ in st for k_ in (-1, 1)] + [P(s_.x, s_.y) for s_ in st]

    def busy(xa, xb):
        return sum(1 for qx, qy in pts_ if xa <= qx <= xb and y0 <= qy <= y0 + 14 * mm)
    right = (x1 - bw - 14 * mm, x1)
    left = (x0, x0 + bw + 14 * mm)
    sx = (x1 - bw - 10 * mm) if busy(*right) <= busy(*left) else (x0 + 6 * mm)
    sy = y0 + 4 * mm
    c.setFillColor(Color(1, 1, 1, 0.9))
    c.rect(sx - 4 * mm, sy - 2 * mm, bw + 12 * mm, 10 * mm, stroke=0, fill=1)
    c.setStrokeColor(INK)
    c.setLineWidth(0.5)
    for j in range(4):
        c.setFillColor(INK if j % 2 == 0 else white)
        c.rect(sx + j * bw / 4, sy, bw / 4, 1.4 * mm, stroke=1, fill=1)
    c.setFillColor(INK)
    c.setFont(MONO, 6.2)
    c.drawString(sx - 1, sy + 2.6 * mm, "0")
    c.drawCentredString(sx + bw / 2, sy + 2.6 * mm, f"{bar / 2:g}")
    c.drawString(sx + bw - 2, sy + 2.6 * mm, f"{bar:g} m")


# ============================================================================ cross-sections
@dataclass
class XS:
    station: object
    number: int = 0
    mark: str = ""                                    # BEGINNING / END
    head_right: str = ""
    head_line2: str = ""
    u: np.ndarray = field(default=None, repr=False)   # offsets
    ground: np.ndarray = field(default=None, repr=False)
    finished: np.ndarray = field(default=None, repr=False)
    earth: np.ndarray = field(default=None, repr=False)
    layers: list = field(default_factory=list)        # (kind, [(u, z), ...])
    labels: list = field(default_factory=list)        # (u, z)
    slopes: list = field(default_factory=list)        # (u a, u b, text)
    title: str = ""                                   # instead of the station (lot sections)
    left: float = 0.0                                 # works extent
    right: float = 0.0
    zmin: float = 0.0
    zmax: float = 1.0

    def width_m(self) -> float:
        return 2 * max(abs(self.left), abs(self.right)) + 2.5

    def height_m(self) -> float:
        return self.zmax - self.zmin + 0.8


def _kinks(u, f, a, b, tol=0.12):
    """Offsets of slope changes of f between |u| = a and b (one side, u >= 0)."""
    ok = np.isfinite(f) & (u >= a - 1e-9) & (u <= b + 1e-9)
    uu, ff = u[ok], f[ok]
    out = []
    if len(uu) < 3:
        return out
    sl = np.diff(ff) / np.maximum(np.diff(uu), 1e-9)
    for i in range(1, len(sl)):
        if abs(sl[i] - sl[i - 1]) > tol and a + 0.08 < uu[i] < b - 0.08:
            if not out or uu[i] - out[-1] > 0.3:
                out.append(float(uu[i]))
    return out[:5]


def road_sections(r, rd) -> list[XS]:
    out, prev = [], None
    T, p = rd.pavement_t, rd.pccp_t
    sts = [s_ for s_ in r.stations if s_.offsets is not None]
    for n, s_ in enumerate(sts, 1):
        u = np.asarray(s_.offsets, float)
        g = np.asarray(s_.ground_xs, float)
        D = np.asarray(s_.design_xs, float)
        F = D.copy()
        xs = XS(s_, n)
        layers, labels, slopes = [], [(0.0, s_.design)], []
        for sign in (-1, 1):
            sd = rdm.station_side(rd, s_, sign)
            w2, uh = sd.w2, sd.half(rd)
            side = (u * sign) >= -1e-9
            au = np.abs(u)
            inside = side & (au <= uh + 1e-9)
            F[inside] = rdm.template(au[inside], s_.design, rd, False, sd)
            z_w = s_.design - sd.slope / 100 * w2
            z_h = rdm.template(np.array([uh]), s_.design, rd, False, sd)[0]
            cut = bool(s_.cut_side[0 if sign < 0 else 1])
            if not cut:                               # fill: the side slope starts at the shoulder edge
                outer = side & (au > uh) & (au <= uh + rd.fill_ratio * T + 1e-9)
                F[outer] = z_h - (au[outer] - uh) / rd.fill_ratio
            catch = abs(s_.right_catch if sign > 0 else s_.left_catch)

            def U(v):
                return sign * v
            if p > 0:
                layers.append(("pccp", [(0, s_.design), (U(w2), z_w), (U(w2), z_w - p), (0, s_.design - p)]))
            sub = T - p
            if sub > 0:
                layers.append(("subbase", [(0, s_.design - p), (U(w2), z_w - p), (U(w2), z_w - T),
                                           (0, s_.design - T)]))
            if T > 0:
                sh = [(U(w2), z_w)] + ([(U(uh), z_h)] if uh > w2 + 1e-9 else [])
                if cut:
                    sh += [(U(uh), z_h - T)] if uh > w2 + 1e-9 else []
                else:
                    sh += [(U(uh + rd.fill_ratio * T), z_h - T)]
                sh += [(U(w2), z_w - T)]
                if len(sh) >= 3:
                    layers.append(("shoulder", sh))
            labels.append((U(w2), z_w))
            if uh > w2 + 1e-9:
                labels.append((U(uh), z_h))
            fside = np.where(side, F, np.nan)
            for k in _kinks(au[side], fside[side], uh, catch):
                labels.append((U(k), float(np.interp(k, au[side], fside[side]))))
            gc = float(np.interp(catch, au[side], g[side])) if np.isfinite(g[side]).any() else float("nan")
            if catch > uh + 0.05 and gc == gc:
                labels.append((U(catch), gc))
            sl = sd.slope
            slopes.append((0, U(w2), f"{abs(sl):.1f}%" if sl >= 0 else f"−{abs(sl):.1f}%"))
            if uh > w2 + 1e-9:
                slopes.append((U(w2), U(uh), f"{sd.sh_slope:.1f}%"))
            start = uh + (rd.ditch_in * rd.ditch_d + rd.ditch_w if cut and rd.ditch else 0.0)
            if catch > start + 0.6:
                ratio = rd.cut_ratio if cut else rd.fill_ratio
                slopes.append((U(start), U(catch), f"{ratio:g}:1"))
            if sign < 0:
                xs.left = -catch
            else:
                xs.right = catch
        sel = np.abs(u) <= max(abs(xs.left), abs(xs.right)) + 1.5
        F[np.abs(u) > np.where(u < 0, abs(xs.left), xs.right) + 1e-9] = np.nan
        xs.u, xs.ground, xs.finished, xs.earth = u, g, F, D
        xs.layers, xs.labels, xs.slopes = layers, sorted(set((round(a, 3), round(b, 3)) for a, b in labels)), slopes
        zz = np.concatenate([g[sel], F[sel], D[sel]])
        zz = zz[np.isfinite(zz)]
        xs.zmin, xs.zmax = (float(zz.min()), float(zz.max())) if len(zz) else (s_.design - 1, s_.design + 1)
        dist = 0.0 if prev is None else s_.s - prev.s
        e = ("NC" if (s_.e_left != s_.e_left or (abs(s_.e_left - rd.crown_pct) < 1e-9
                                                and abs(s_.e_right - rd.crown_pct) < 1e-9))
             else f"{max(abs(s_.e_left), abs(s_.e_right)):.2f}% {'L' if s_.e_left > s_.e_right else 'R'}")
        w = f"{s_.widen:.2f} {s_.widen_side}".strip()
        og = f"{s_.ground:.3f}" if s_.ground == s_.ground else "–"
        xs.head_right = f"OGE = {og}   FGE = {s_.design:.3f}   e = {e}   w = {w}"
        xs.head_line2 = (f"Cut area {s_.cut_area:.2f} m² · Fill area {s_.fill_area:.2f} m²"
                         + (f" · since {kst(prev.s)} ({dist:.2f} m): cut {s_.cut_vol:,.1f} m³, fill "
                            f"{s_.fill_vol:,.1f} m³" if prev is not None else ""))
        out.append(xs)
        prev = s_
    if out:
        out[0].mark = "BEGINNING"
        out[-1].mark = "END" if len(out) > 1 else out[-1].mark
    return out


def canal_sections(r, cd) -> list[XS]:
    out, prev = [], None
    sq = math.sqrt(1 + cd.z * cd.z)
    b2, d, t, z = cd.bw / 2, cd.depth, cd.lining_t, cd.z
    u_c, u_t = b2 + t * (sq - z), b2 + z * d + t * sq
    sts = [s_ for s_ in r.stations if s_.offsets is not None]
    yd = getattr(r, "y_design", float("nan"))
    for n, s_ in enumerate(sts, 1):
        u = np.asarray(s_.offsets, float)
        g = np.asarray(s_.ground_xs, float)
        D = np.asarray(s_.design_xs, float)
        inv, top = s_.design, s_.design + d
        F = D.copy()
        inner = np.abs(u) <= b2 + z * d + 1e-9
        F[inner] = inv + np.maximum(np.abs(u[inner]) - b2, 0) / max(z, 1e-9) if z > 0 else inv
        if z <= 0:
            F[inner & (np.abs(u) >= b2 - 1e-9)] = inv
        lining = [(-b2 - z * d, top), (-b2, inv), (b2, inv), (b2 + z * d, top), (u_t, top), (u_c, inv - t),
                  (-u_c, inv - t), (-u_t, top)]
        layers = [("lining", lining)]
        if yd == yd and yd > 0:
            yy = min(yd, d)
            layers.insert(0, ("water", [(-b2 - z * yy, inv + yy), (b2 + z * yy, inv + yy), (b2, inv), (-b2, inv)]))
        xs = XS(s_, n)
        labels = [(0.0, inv)]
        for sign in (-1, 1):
            catch = abs(s_.right_catch if sign > 0 else s_.left_catch)
            labels += [(sign * b2, inv), (sign * (b2 + z * d), top)]
            if cd.berm_w > 0:
                labels.append((sign * (u_t + cd.berm_w), top))
            side = (u * sign) >= 0
            gc = float(np.interp(catch, np.abs(u[side]), g[side])) if np.isfinite(g[side]).any() else float("nan")
            if gc == gc and catch > u_t + cd.berm_w + 0.05:
                labels.append((sign * catch, gc))
            bank = not s_.cut_side[0 if sign < 0 else 1]
            if catch > u_t + cd.berm_w + 0.6:
                slopes_txt = f"{(cd.fill_ratio if bank else cd.cut_ratio):g}:1"
                xs.slopes.append((sign * (u_t + cd.berm_w), sign * catch, slopes_txt))
            if sign < 0:
                xs.left = -catch
            else:
                xs.right = catch
        xs.slopes.append((b2, b2 + z * d, f"{z:g}:1") if z * d > 0.6 else (0, 0, ""))
        F[np.abs(u) > np.where(u < 0, abs(xs.left), xs.right) + 1e-9] = np.nan
        xs.u, xs.ground, xs.finished, xs.earth, xs.layers = u, g, F, D, layers
        xs.labels = sorted(set((round(a, 3), round(b, 3)) for a, b in labels))
        sel = np.abs(u) <= max(abs(xs.left), abs(xs.right)) + 1.5
        zz = np.concatenate([g[sel], D[sel], [top, inv - t]])
        zz = zz[np.isfinite(zz)]
        xs.zmin, xs.zmax = float(zz.min()), float(zz.max())
        dist = 0.0 if prev is None else s_.s - prev.s
        og = f"{s_.ground:.3f}" if s_.ground == s_.ground else "–"
        xs.head_right = f"OGE = {og}   BED = {inv:.3f}   TOP = {top:.3f}"
        xs.head_line2 = (f"Cut area {s_.cut_area:.2f} m² · Bank (fill) area {s_.fill_area:.2f} m²"
                         + (f" · since {kst(prev.s)} ({dist:.2f} m): cut {s_.cut_vol:,.1f} m³, fill "
                            f"{s_.fill_vol:,.1f} m³" if prev is not None else ""))
        out.append(xs)
        prev = s_
    if out:
        out[0].mark = "BEGINNING"
        if len(out) > 1:
            out[-1].mark = "END"
    return out


def _xs_geometry(W, H, paper, rows):
    x0f, y0f, x1f, y1f = _frame(W, H)
    big = paper.sheet_name == "A3"
    bottom = (20 if big else 16) * mm
    gx0, gy0, gx1, gy1 = x0f + 2 * mm, y0f + bottom, x1f - 2 * mm, y1f - 2 * mm
    pw = (gx1 - gx0 - 2 * mm) / 2
    ph = (gy1 - gy0 - (rows - 1) * 1.6 * mm) / rows
    fs = 6.0 if big else 5.5
    head = (9.6 if big else 8.6) * mm
    lab = 3 * (2 * fs * 1.18) + 2.2 * mm
    aw = pw - 12 * mm
    ah = ph - head - lab - 1.5 * mm
    return dict(gx0=gx0, gy0=gy0, gx1=gx1, gy1=gy1, pw=pw, ph=ph, fs=fs, head=head, lab=lab, aw=aw, ah=ah,
                bottom=bottom)


def choose_xs_layout(xss: list[XS], paper: Paper) -> tuple[int, int]:
    """(sections per column, scale 1 : n) by the paper's rule."""
    W, H = paper.sheet
    tried = []
    for rows, limit in paper.xs_rows:
        g = _xs_geometry(W, H, paper, rows)
        need = max(max(x.width_m() * 1000 / (g["aw"] / mm), x.height_m() * 1000 / (g["ah"] / mm)) for x in xss)
        den = nice_scale(need)
        if limit and den <= limit:
            return rows, den
        tried.append((rows, den))
    # none within its limit (very wide or tall sections): the largest scale, more per column on a tie,
    # but never the four-per-column layout, which is only for scales down to its limit
    rest = tried[1:] or tried
    return min(rest, key=lambda t: (t[1], -t[0]))


def xs_sheets(xss: list[XS], ctx: Ctx, first: int = 1) -> list:
    """[(draw fn, toc text)] cross-section sheets."""
    if not xss:
        return []
    rows, den = choose_xs_layout(xss, ctx.paper)
    per = 2 * rows
    groups = [xss[i:i + per] for i in range(0, len(xss), per)]
    out = []
    for k, grp in enumerate(groups):
        def fn(c, W, H, grp=grp, k=k):
            _draw_xs_sheet(c, W, H, grp, ctx, rows, den, k, len(groups), first)
        fst, lst = grp[0].station, grp[-1].station
        out.append((fn, f"Cross-sections {k + 1} of {len(groups)}, " + (f"sections {grp[0].number} to {grp[-1].number}"
                                                                        if ctx.lot else f"{kst(fst.s)} to {kst(lst.s)}")))
    return out


def _draw_xs_sheet(c, W, H, grp, ctx: Ctx, rows, den, k, n, first=1):
    x0f, y0f, x1f, y1f = _frame(W, H)
    g = _xs_geometry(W, H, ctx.paper, rows)
    c.setStrokeColor(INK)
    c.setLineWidth(0.9)
    c.rect(x0f, y0f, x1f - x0f, y1f - y0f)
    kpt = 1000.0 / den * mm
    for i, xs in enumerate(grp):
        col, row = divmod(i, rows)
        px = g["gx0"] + col * (g["pw"] + 2 * mm)
        py = g["gy1"] - (row + 1) * g["ph"] - row * 1.6 * mm
        _draw_xs(c, xs, px, py, g, kpt, ctx)
    # legend and notes
    big = ctx.paper.sheet_name == "A3"
    ly = y0f + g["bottom"] - 7 * mm
    lx = x0f + 4 * mm
    if ctx.lot:
        items = [("fill", "Fill"), ("cut", "Cut")]
    elif ctx.canal:
        items = [("lining", f"Concrete lining {ctx.design.lining_t * 1000:.0f} mm"),
                 ("water", "Water at the design flow"), ("fill", "Earth bank (fill)"), ("cut", "Cut (excavation)")]
    else:
        rd = ctx.design
        items = []
        if rd.pccp_t > 0:
            items.append(("pccp", f"PCCP {rd.pccp_t * 1000:.0f} mm"))
        if rd.subbase_t > 0:
            items.append(("subbase", f"Aggregate subbase {rd.subbase_t * 1000:.0f} mm"))
        if rd.pavement_t > 0:
            items.append(("shoulder", "Aggregate shoulder / layer edge"))
        items += [("fill", "Fill (embankment)"), ("cut", "Cut (roadway excavation)")]
    c.setFont(SANS, 7 if big else 6.4)
    for kind, text in items:
        sw = [(lx, ly), (lx + 7 * mm, ly), (lx + 7 * mm, ly + 3.4 * mm), (lx, ly + 3.4 * mm)]
        _layer(c, kind, sw)
        c.setStrokeColor(INK)
        c.setLineWidth(0.4)
        c.drawPath(_path(sw, True), stroke=1, fill=0)
        c.setFillColor(INK)
        c.drawString(lx + 9 * mm, ly + 0.8 * mm, text)
        lx += 9 * mm + stringWidth(text, SANS, 7 if big else 6.4) + 8 * mm
    tbw, tbh = (128 if big else 104) * mm, (15 if big else 12.5) * mm
    c.setFont(SANS, 6.4 if big else 5.9)
    c.setFillColor(INK2)
    c.drawString(x0f + 4 * mm, ly - 4.2 * mm, _clip(
        ("Under each section: elevation (underlined) over offset from the middle line of the lot in metres (− = left), "
         "looking along the lot. Numbered down the left column, then the right. Volumes: average end-area (a check)."
         if ctx.lot else
         "Under each section: elevation (underlined) over offset from the centre line in metres (− = left), looking "
         "toward higher stations. Numbered down the left column, then the right. Volumes: average end-area."),
        SANS, 6.4, x1f - x0f - tbw - 10 * mm))
    rng = (f"sections {grp[0].number} to {grp[-1].number}" if ctx.lot
           else f"{kst(grp[0].station.s)} to {kst(grp[-1].station.s)}")
    title_block(c, x1f, y0f, tbw, tbh, f"CROSS-SECTIONS {k + 1} / {n}", f"{ctx.paper.sheet_name} · 1:{den:,}",
                f"{ctx.project} · {rng}",
                f"Sheet {first + k} of {ctx.sheets_total}" if ctx.sheets_total else "")


def _layer(c, kind, pts):
    if kind == "pccp":
        hatch(c, pts, step=1.1 * mm, fill=Color(0.93, 0.93, 0.9), dots=True)
    elif kind == "subbase":
        hatch(c, pts, step=1.0 * mm, angle=45, fill=Color(0.92, 0.9, 0.84), lw=0.3)
    elif kind == "shoulder":
        hatch(c, pts, step=1.0 * mm, angle=-45, fill=Color(0.9, 0.87, 0.8), lw=0.3)
    elif kind == "lining":
        hatch(c, pts, step=1.0 * mm, fill=Color(0.82, 0.84, 0.87), dots=True)
    elif kind == "water":
        c.setFillColor(WATER)
        c.drawPath(_path(pts, True), stroke=0, fill=1)
    elif kind == "cut":
        hatch(c, pts, step=1.5 * mm, angle=45, color=CUT, fill=CUT_SOFT, lw=0.35)
    elif kind == "fill":
        hatch(c, pts, step=1.5 * mm, angle=-45, color=FILL, fill=FILL_SOFT, lw=0.35)


def _draw_xs(c, xs: XS, px, py, g, kpt, ctx: Ctx):
    pw, ph, fs = g["pw"], g["ph"], g["fs"]
    c.setStrokeColor(RULE)
    c.setLineWidth(0.5)
    c.rect(px, py, pw, ph)
    # headings
    ty = py + ph - 4.0 * mm
    c.setFont(HEAD_B, 8.2)
    c.setFillColor(NAVY)
    t1 = xs.title or kst(xs.station.s)
    c.drawString(px + 2.4 * mm, ty, t1)
    if xs.mark:
        c.setFont(HEAD, 7.4)
        c.setFillColor(Color(0.69, 0.48, 0.05))
        c.drawString(px + 2.4 * mm + stringWidth(t1, HEAD_B, 8.2) + 1.6 * mm, ty, "· " + xs.mark)
    c.setFont(MONO, 6.2)
    c.setFillColor(INK)
    c.drawRightString(px + pw - 2.4 * mm, ty, xs.head_right)
    c.setFont(SANS, 6.1)
    c.setFillColor(MUTED)
    c.drawString(px + 2.4 * mm, ty - 3.4 * mm, _clip(xs.head_line2, SANS, 6.1, pw - 5 * mm))
    c.setFont(MONO, 5.6)
    c.drawRightString(px + pw - 1.6 * mm, py + 1.4 * mm, str(xs.number))
    # drawing area
    ax0, ax1 = px + 10 * mm, px + pw - 2 * mm
    ay0, ay1 = py + g["lab"] + 1.0 * mm, py + ph - g["head"]
    uc = (ax0 + ax1) / 2
    zc = (xs.zmin + xs.zmax) / 2
    yc = (ay0 + ay1) / 2

    def P(u, z):
        return uc + u * kpt, yc + (z - zc) * kpt
    half_u = (ax1 - ax0) / 2 / kpt
    zlo, zhi = zc - (ay1 - ay0) / 2 / kpt, zc + (ay1 - ay0) / 2 / kpt
    gstep = next(s for s in (0.5, 1, 2, 5, 10, 20, 50) if s * kpt >= 3.2 * mm)
    c.setLineWidth(0.25)
    c.setStrokeColor(GRID)
    v = math.ceil(-half_u / gstep) * gstep
    while v <= half_u:
        c.line(P(v, 0)[0], ay0, P(v, 0)[0], ay1)
        v += gstep
    v = math.ceil(zlo / gstep) * gstep
    c.setFont(MONO, 5.4)
    c.setFillColor(INK2)
    while v <= zhi + 1e-9:
        y = P(0, v)[1]
        c.line(ax0, y, ax1, y)
        c.drawRightString(ax0 - 1.0 * mm, y - 1.8, f"{v:.0f}" if gstep >= 1 else f"{v:.1f}")
        v += gstep
    c.saveState()
    c.clipPath(_path([(ax0, ay0), (ax1, ay0), (ax1, ay1), (ax0, ay1)], True), stroke=0, fill=0)
    u, gr, F, D = xs.u, xs.ground, xs.finished, xs.earth
    works = (u >= xs.left - 1e-9) & (u <= xs.right + 1e-9)
    if works.sum() >= 2:
        for sg, xx, ta, tb in _runs(u[works], gr[works], D[works]):
            pts = [P(a, b) for a, b in zip(xx, ta)] + [P(a, b) for a, b in zip(xx[::-1], tb[::-1])]
            _layer(c, "cut" if sg > 0 else "fill", pts)
    for kind, poly in xs.layers:
        pts = [P(a, b) for a, b in poly]
        _layer(c, kind, pts)
        if kind != "water":
            c.setStrokeColor(INK)
            c.setLineWidth(0.35)
            c.drawPath(_path(pts, True), stroke=1, fill=0)
    c.setStrokeColor(GROUND)
    c.setLineWidth(0.9)
    for run in _segments([P(a, 0)[0] for a in u], [P(0, b)[1] for b in gr]):
        _line(c, run)
    c.setStrokeColor(INK)
    c.setLineWidth(1.0)
    for run in _segments([P(a, 0)[0] for a in u], [P(0, b)[1] for b in F]):
        _line(c, run)
    # centre line
    c.setStrokeColor(INK2)
    c.setLineWidth(0.4)
    c.setDash([4, 1.2, 1, 1.2])
    top_z = xs.station.design + (ctx.design.depth if ctx.canal else 0)
    c.line(P(0, 0)[0], P(0, top_z)[1] - 6 * mm, P(0, 0)[0], P(0, top_z)[1] + 5 * mm)
    c.setDash()
    # slope labels
    c.setFont(SANS, 5.6)
    c.setFillColor(Color(0.09, 0.45, 0.25))
    for ua, ub, txt in [q for q in xs.slopes if q[2]]:
        um = (ua + ub) / 2
        zm = np.interp(um, u, np.where(np.isfinite(F), F, np.nan)) if np.isfinite(F).any() else float("nan")
        if zm == zm:
            x, y = P(um, zm)
            c.drawCentredString(x, y + 1.4 * mm, txt)
    c.restoreState()
    # elevation / offset labels in up to three staggered rows
    # the centre and the edges of the works first, then the other break points while there is room
    lh = fs * 1.18
    ends = (round(xs.left, 3), round(xs.right, 3), 0.0)
    order = sorted(xs.labels, key=lambda q: (0 if q[0] in ends else 1, -abs(q[0])))
    rows = [[], [], []]
    placed = []
    for uu, zz in order:
        if abs(uu) > half_u:
            continue
        x = P(uu, zz)[0]
        e_t, o_t = f"{zz:.3f}", (f"{uu:.2f}" if uu >= 0 else f"−{abs(uu):.2f}")
        w = max(stringWidth(e_t, MONO, fs), stringWidth(o_t, MONO, fs))
        a, b = x - w / 2 - 0.5 * mm, x + w / 2 + 0.5 * mm
        row = next((i for i in range(3) if all(b <= p0 or a >= p1 for p0, p1 in rows[i])), None)
        if row is None:
            continue
        rows[row].append((a, b))
        placed.append((uu, zz, row, e_t, o_t, w))
    for uu, zz, row, e_t, o_t, w in placed:
        x, y = P(uu, zz)
        top_y = py + g["lab"] - 0.6 * mm - row * 2 * lh
        c.setStrokeColor(Color(0.6, 0.63, 0.68))
        c.setLineWidth(0.3)
        c.setDash(1, 1)
        c.line(x, min(y, ay1) - 0.4 * mm, x, top_y + 0.4 * mm)
        c.setDash()
        c.setFont(MONO, fs)
        c.setFillColor(INK)
        c.drawCentredString(x, top_y - fs * 0.85, e_t)
        c.setLineWidth(0.3)
        c.setStrokeColor(INK)
        c.line(x - w / 2, top_y - fs * 1.05, x + w / 2, top_y - fs * 1.05)
        c.setFillColor(INK2)
        c.drawCentredString(x, top_y - fs * 0.85 - lh, o_t)


# ============================================================================ lot / pad and pile
def lot_sections(secs) -> list[XS]:
    """XS from pad_sections.sections (SectionData with .s = distance along the lot)."""
    from types import SimpleNamespace
    out = []
    for n, sd in enumerate(secs, 1):
        u = np.asarray(sd.offsets, float)
        g = np.asarray(sd.ground, float)
        f = np.asarray(sd.design, float)
        ok = np.isfinite(f)
        if not ok.any():
            continue
        xs = XS(SimpleNamespace(s=getattr(sd, "s", 0.0), design=sd.centre_design if sd.centre_design == sd.centre_design
                                else float(np.nanmean(f))), n)
        xs.title = f"Section {n} · {getattr(sd, 's', 0.0):.2f} m"
        xs.u, xs.ground, xs.finished, xs.earth = u, g, f, f
        xs.left, xs.right = float(u[ok][0]), float(u[ok][-1])
        labels = [(xs.left, float(f[ok][0])), (xs.right, float(f[ok][-1]))]
        for k in _kinks(u[ok] - xs.left, f[ok], 0.0, xs.right - xs.left, tol=0.08):
            labels.append((k + xs.left, float(np.interp(k + xs.left, u[ok], f[ok]))))
        if xs.left < 0 < xs.right:
            labels.append((0.0, float(np.interp(0.0, u[ok], f[ok]))))
        xs.labels = sorted(set((round(a, 3), round(b, 3)) for a, b in labels))
        xs.slopes = [(a, b, t) for a, b, t in sd.slopes]
        sel = (u >= xs.left - 1.5) & (u <= xs.right + 1.5)
        zz = np.concatenate([g[sel], f[sel]])
        zz = zz[np.isfinite(zz)]
        xs.zmin, xs.zmax = float(zz.min()), float(zz.max())
        info = dict(sd.info)
        og = f"{sd.centre_ground:.3f}" if sd.centre_ground == sd.centre_ground else "–"
        fg = f"{sd.centre_design:.3f}" if sd.centre_design == sd.centre_design else "–"
        xs.head_right = f"at the middle line: ground = {og}   finished = {fg}"
        xs.head_line2 = (f"Cut area {info.get('Cut area', '–')} · Fill area {info.get('Fill area', '–')}"
                         + (f" · since the previous section ({info.get('Distance from last', '')}): cut "
                            f"{info.get('Cut volume since last', '')}, fill {info.get('Fill volume since last', '')}"
                            if n > 1 else ""))
        out.append(xs)
    if out:
        out[0].mark = "FIRST"
        if len(out) > 1:
            out[-1].mark = "LAST"
    return out


def lot_plan_sheet(t, outline, spots, ctx: Ctx, title: str, first: int = 1, pile: bool = False):
    """(draw fn, toc text): the outline on the drone map with the cut / fill colours, numbered corners, spot levels
    where there is room, north arrow, scale bar, legend and title block, at a standard scale."""
    def fn(c, W, H):
        x0f, y0f, x1f, y1f = _frame(W, H)
        big = ctx.paper.sheet_name == "A3"
        bottom = (18 if big else 15) * mm
        ax0, ay0, ax1, ay1 = x0f, y0f + bottom, x1f, y1f
        c.setStrokeColor(INK)
        c.setLineWidth(0.9)
        c.rect(x0f, y0f, x1f - x0f, y1f - y0f)
        h_, w_ = t.dz.shape
        tr = t.transform
        xs_ = [p[0] for p in outline] + [tr.c, tr.c + w_ * tr.a]
        ys_ = [p[1] for p in outline] + [tr.f, tr.f + h_ * tr.e]
        bx0, bx1, by0, by1 = min(xs_), max(xs_), min(ys_), max(ys_)
        mx, my = (bx0 + bx1) / 2, (by0 + by1) / 2
        need = max((bx1 - bx0) * 1.25 * 1000 / ((ax1 - ax0) / mm), (by1 - by0) * 1.25 * 1000 / ((ay1 - ay0) / mm))
        den = nice_scale(max(need, 50))
        k = 1000.0 / den * mm
        cx, cy = (ax0 + ax1) / 2, (ay0 + ay1) / 2

        def P(x, y):
            return cx + (x - mx) * k, cy + (y - my) * k
        c.saveState()
        c.clipPath(_path([(ax0, ay0), (ax1, ay0), (ax1, ay1), (ax0, ay1)], True), stroke=0, fill=0)
        c.setFillColor(Color(0.95, 0.96, 0.97))
        c.rect(ax0, ay0, ax1 - ax0, ay1 - ay0, stroke=0, fill=1)
        vb = (mx - (cx - ax0) / k, my - (cy - ay0) / k, mx + (ax1 - cx) / k, my + (ay1 - cy) / k)
        if ctx.background is not None:
            try:
                got = ctx.background(vb, int(min(2600, max(600, (ax1 - ax0) / 72 * 170))))
            except Exception:
                got = None
            if got is not None:
                im, (gx0, gy0, gx1, gy1) = got
                p0, p1 = P(gx0, gy0), P(gx1, gy1)
                c.drawImage(jpeg_reader(im, 82), p0[0], p0[1], p1[0] - p0[0], p1[1] - p0[1])
                c.setFillColor(Color(1, 1, 1, 0.2))
                c.rect(ax0, ay0, ax1 - ax0, ay1 - ay0, stroke=0, fill=1)
        from PIL import Image
        from reportlab.lib.utils import ImageReader
        from . import measure as ms
        rgba = ms.overlay_rgba(t.dz)
        p0, p1 = P(tr.c, tr.f + h_ * tr.e), P(tr.c + w_ * tr.a, tr.f)
        c.drawImage(ImageReader(Image.fromarray(rgba, "RGBA")), p0[0], p0[1], p1[0] - p0[0], p1[1] - p0[1],
                    mask="auto")
        ring = [P(*q) for q in outline] + [P(*outline[0])]
        c.setStrokeColor(INK)
        c.setLineWidth(2.6)
        _line(c, ring)
        c.setStrokeColor(AMBER_LINE)
        c.setLineWidth(1.4)
        _line(c, ring)
        # spot levels where there is room
        if len(spots) > 1:
            dx = min((math.dist(spots[0][1:3], q[1:3]) for q in spots[1:]), default=0)
            gap = dx * k
            for row in spots:
                X, Y = P(row[1], row[2])
                if not (ax0 < X < ax1 and ay0 < Y < ay1) or gap < 6 * mm:
                    continue
                cf = row[5]
                if cf is None:
                    continue
                c.setFillColor(INK)
                c.circle(X, Y, 0.35 * mm, stroke=0, fill=1)
                c.setFont(MONO, 5.2 if gap < 11 * mm else 5.8)
                c.setFillColor(CUT if cf > 0.0005 else (FILL if cf < -0.0005 else INK))
                c.drawCentredString(X, Y + 1.0 * mm, ("0.00" if abs(cf) < 0.005 else f"{cf:+.2f}").replace("-", "−"))
                if gap >= 11 * mm and row[4] is not None:
                    c.setFillColor(INK2)
                    c.drawCentredString(X, Y - 2.6 * mm, f"{row[4]:.2f}")
        c.restoreState()
        # numbered corners
        for i, q in enumerate(outline, 1):
            X, Y = P(*q)
            c.setFillColor(white)
            c.setStrokeColor(INK)
            c.setLineWidth(0.7)
            c.circle(X, Y, 1.0 * mm, stroke=1, fill=1)
            t_ = f"P{i}"
            c.setFont(MONO_M, 6.6)
            w = stringWidth(t_, MONO_M, 6.6)
            c.setFillColor(Color(1, 1, 1, 0.85))
            c.rect(X + 1.4 * mm, Y + 1.0 * mm, w + 2, 8, stroke=0, fill=1)
            c.setFillColor(INK)
            c.drawString(X + 1.4 * mm + 1, Y + 1.0 * mm + 2, t_)
        c.setStrokeColor(NAVY)
        c.setLineWidth(0.8)
        c.rect(ax0, ay0, ax1 - ax0, ay1 - ay0)
        c.setFillColor(white)
        c.rect(ax0, ay1 - 7 * mm, 26 * mm, 7 * mm, stroke=0, fill=1)
        c.setFont(HEAD, 8.6)
        c.setFillColor(NAVY)
        c.drawString(ax0 + 3 * mm, ay1 - 5 * mm, "PLAN")
        # north arrow (north up) and scale bar
        nx_, ny_ = ax1 - 12 * mm, ay1 - 13 * mm
        c.setFillColor(Color(1, 1, 1, 0.85))
        c.circle(nx_, ny_, 7.5 * mm, stroke=0, fill=1)
        c.setFillColor(INK)
        c.drawPath(_path([(nx_, ny_ + 6 * mm), (nx_ - 2.4 * mm, ny_ - 4.5 * mm), (nx_, ny_ - 2 * mm),
                          (nx_ + 2.4 * mm, ny_ - 4.5 * mm)], True), stroke=0, fill=1)
        c.setFont(SANS_B, 7)
        c.drawCentredString(nx_, ny_ - 7.6 * mm - 2.4, "N")
        bar = next(v for v in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000) if v * k >= 30 * mm)
        bw = bar * k
        sx, sy = ax1 - bw - 10 * mm, ay0 + 4 * mm
        c.setFillColor(Color(1, 1, 1, 0.9))
        c.rect(sx - 4 * mm, sy - 2 * mm, bw + 12 * mm, 10 * mm, stroke=0, fill=1)
        c.setStrokeColor(INK)
        c.setLineWidth(0.5)
        for j in range(4):
            c.setFillColor(INK if j % 2 == 0 else white)
            c.rect(sx + j * bw / 4, sy, bw / 4, 1.4 * mm, stroke=1, fill=1)
        c.setFillColor(INK)
        c.setFont(MONO, 6.2)
        c.drawString(sx - 1, sy + 2.6 * mm, "0")
        c.drawCentredString(sx + bw / 2, sy + 2.6 * mm, f"{bar / 2:g}")
        c.drawString(sx + bw - 2, sy + 2.6 * mm, f"{bar:g} m")
        # legend and notes, title block
        tbw, tbh = (128 if big else 104) * mm, (15 if big else 12.5) * mm
        ly = y0f + bottom - 7 * mm
        lx = x0f + 4 * mm
        c.setFont(SANS, 6.8)
        for col, text in ((Color(196 / 255, 40 / 255, 32 / 255, 0.7),
                           "Above the reference (pile)" if pile else "Cut (deeper = stronger)"),
                          (Color(30 / 255, 90 / 255, 170 / 255, 0.7),
                           "Below the reference (hole)" if pile else "Fill (higher = stronger)"),
                          (AMBER_LINE, "Outline, corners P1, P2, …")):
            c.setFillColor(col)
            c.rect(lx, ly, 7 * mm, 3.4 * mm, stroke=0, fill=1)
            c.setFillColor(INK)
            c.drawString(lx + 9 * mm, ly + 0.8 * mm, text)
            lx += 9 * mm + stringWidth(text, SANS, 6.8) + 8 * mm
        c.setFont(SANS, 6.2)
        c.setFillColor(INK2)
        c.drawString(x0f + 4 * mm, ly - 4.2 * mm, _clip(
            ("Spot levels: height above + / below − the reference surface in metres, the reference level below when "
             "there is room. Corner coordinates are listed in section 1. North is up." if pile else
             "Spot levels: cut + / fill − in metres (ground now minus finished), finished level below when there is "
             "room. Corner coordinates are listed in section 1. North is up."), SANS, 6.2, x1f - x0f - tbw - 10 * mm))
        title_block(c, x1f, y0f, tbw, tbh, title, f"{ctx.paper.sheet_name} · 1:{den:,}", ctx.project,
                    f"Sheet {first} of {ctx.sheets_total}" if ctx.sheets_total else "")
    return fn, title.capitalize()
