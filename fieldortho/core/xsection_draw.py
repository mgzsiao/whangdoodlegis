"""Cross-section sheets for the earthworks reports (road and lot / pad).

Each section is drawn at TRUE SCALE (one metre across = one metre up, the same scale for every
section of the report) on a 1 m / 2 m / 5 m grid with offsets along the bottom and elevations on the
left, the ground (brown) and the finished surface (navy) with dig / fill shading, slope labels on each
part of the finished surface, the ground and finished elevations at the centre line, and a box with that
station's areas and volumes, so a section can be checked without turning to other tables.
Sections are packed onto A4 pages as many as fit while staying readable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

from .earthworks_report import CUT_RGB, FILL_RGB, GROUND_RGB, INK, MUTED, NAVY, _font

PANEL_W = 1600                # px; the PDF shows it 520 pt wide
PAGE_H = 2150                 # px of drawing per A4 page (below the heading)
GRID_RGB = (222, 226, 234)
GRID_MAJOR = (196, 202, 214)
SLOPE_RGB = (24, 130, 70)
BOX_W = 390                   # info box on the right


@dataclass
class SectionData:
    title: str                                   # e.g. "STA. 0+060"
    offsets: np.ndarray
    ground: np.ndarray
    design: np.ndarray                           # NaN outside the works
    centre_ground: float = float("nan")
    centre_design: float = float("nan")
    info: list = field(default_factory=list)     # [(label, value), ...] for the box
    slopes: list = field(default_factory=list)   # [(offset a, offset b, text), ...] parts of the design


def _scale_for(sections: list[SectionData], draw_w: int) -> tuple[float, float]:
    """(px per metre, half width in m) shared by all sections."""
    half = 2.0
    for s in sections:
        ok = np.isfinite(s.design)
        if ok.any():
            half = max(half, float(np.max(np.abs(s.offsets[ok]))) + 1.5)
        elif s.offsets is not None and len(s.offsets):
            half = max(half, float(np.max(np.abs(s.offsets))))
    half = min(half, float(max(np.max(np.abs(s.offsets)) for s in sections if len(s.offsets))))
    half = max(half, 2.0)
    return draw_w / (2 * half), half


def _zwindow(s: SectionData, half: float) -> tuple[float, float]:
    sel = np.abs(s.offsets) <= half + 1e-9
    zs = np.concatenate([s.ground[sel & np.isfinite(s.ground)], s.design[sel & np.isfinite(s.design)]])
    if not len(zs):
        return 0.0, 1.0
    return float(zs.min()), float(zs.max())


def _grid_step(span_m: float) -> float:
    for st in (0.5, 1, 2, 5, 10, 20, 50):
        if span_m / st <= 24:
            return st
    return 100


def panels(sections: list[SectionData], note: str = "") -> list[Image.Image]:
    """One image per section, all at the same true scale."""
    if not sections:
        return []
    L, T, B = 120, 64, 64
    draw_w = PANEL_W - L - BOX_W - 40
    ppm, half = _scale_for(sections, draw_w)
    step = _grid_step(2 * half)
    out = []
    for s in sections:
        z0, z1 = _zwindow(s, half)
        zc = (z0 + z1) / 2
        span = max(z1 - z0 + 2 * step, 4 * step)
        z0, z1 = math.floor((zc - span / 2) / step) * step, math.ceil((zc + span / 2) / step) * step
        ph = int(round((z1 - z0) * ppm))
        need = T + 36 * (len(s.info) + 1) + 30          # the info box must fit beside the drawing
        while T + ph + B < need:                         # add grid rows above and below
            z0, z1 = z0 - step, z1 + step
            ph = int(round((z1 - z0) * ppm))
        H = T + ph + B
        im = Image.new("RGB", (PANEL_W, H), "white")
        dr = ImageDraw.Draw(im)
        f, fs, fb = _font(24), _font(20), _font(30, True)

        def P(o, z):
            return (L + (o + half) * ppm, T + (z1 - z) * ppm)
        # grid
        k = math.ceil(-half / step)
        while k * step <= half + 1e-9:
            X = P(k * step, 0)[0]
            major = abs(k * step) < 1e-9 or abs((k * step) % (5 * step)) < 1e-9
            dr.line([(X, T), (X, T + ph)], fill=GRID_MAJOR if major else GRID_RGB, width=2 if major else 1)
            if k % 2 == 0:
                lab = f"{k * step:g}"
                tw = dr.textlength(lab, font=fs)
                dr.text((X - tw / 2, T + ph + 6), lab, font=fs, fill=MUTED)
            k += 1
        zz = z0
        while zz <= z1 + 1e-9:
            Y = P(0, zz)[1]
            dr.line([(L, Y), (L + 2 * half * ppm, Y)], fill=GRID_RGB)
            lab = f"{zz:.1f}" if step < 1 else f"{zz:.0f}"
            tw = dr.textlength(lab, font=fs)
            dr.text((L - 10 - tw, Y - 11), lab, font=fs, fill=MUTED)
            zz += step
        dr.rectangle([L, T, L + 2 * half * ppm, T + ph], outline=MUTED, width=2)
        # dig / fill shading
        o, g, d = s.offsets, s.ground, s.design
        for i in range(len(o) - 1):
            if not (np.isfinite(g[i]) and np.isfinite(d[i]) and np.isfinite(g[i + 1]) and np.isfinite(d[i + 1])):
                continue
            if abs(o[i]) > half or abs(o[i + 1]) > half:
                continue
            a0, a1 = g[i] - d[i], g[i + 1] - d[i + 1]
            col = CUT_RGB if (a0 + a1) > 0 else FILL_RGB
            poly = [P(o[i], g[i]), P(o[i + 1], g[i + 1]), P(o[i + 1], d[i + 1]), P(o[i], d[i])]
            dr.polygon(poly, fill=tuple(int(c * 0.3 + 255 * 0.7) for c in col))
        # ground and finished surface
        seg = []
        for oi, gi in zip(o, g):
            if np.isfinite(gi) and abs(oi) <= half:
                seg.append(P(oi, gi))
            elif len(seg) > 1:
                dr.line(seg, fill=GROUND_RGB, width=3)
                seg = []
        if len(seg) > 1:
            dr.line(seg, fill=GROUND_RGB, width=3)
        seg = []
        for oi, di in zip(o, d):
            if np.isfinite(di) and abs(oi) <= half:
                seg.append(P(oi, di))
            elif len(seg) > 1:
                dr.line(seg, fill=NAVY, width=4)
                seg = []
        if len(seg) > 1:
            dr.line(seg, fill=NAVY, width=4)
        # slope labels on the parts of the finished surface
        for a, b, txt in s.slopes:
            m = (a + b) / 2
            if abs(m) > half:
                continue
            zm = float(np.interp(m, o[np.isfinite(d)], d[np.isfinite(d)])) if np.isfinite(d).any() else None
            if zm is None:
                continue
            X, Y = P(m, zm)
            tw = dr.textlength(txt, font=fs)
            dr.text((X - tw / 2, Y + 6), txt, font=fs, fill=SLOPE_RGB)
        # centre line and its elevations
        Xc = P(0, 0)[0]
        dr.line([(Xc, T), (Xc, T + ph)], fill=(150, 160, 178), width=2)
        lab = []
        if math.isfinite(s.centre_design):
            lab.append((f"finished {s.centre_design:.3f}", NAVY))
        if math.isfinite(s.centre_ground):
            lab.append((f"ground {s.centre_ground:.3f}", GROUND_RGB))
        for j, (txt, col) in enumerate(lab):
            tw = dr.textlength(txt, font=fs)
            timg = Image.new("RGBA", (int(tw) + 4, 26), (255, 255, 255, 0))
            ImageDraw.Draw(timg).text((2, 0), txt, font=fs, fill=col)
            timg = timg.rotate(90, expand=True)
            im.paste(timg, (int(Xc + 4 + j * 26), int(T + ph - timg.height - 4)), timg)
        # title + scale
        dr.text((L, 14), s.title, font=fb, fill=INK)
        dr.text((L + 2 * half * ppm - 470, 20), f"true scale · grid {step:g} m · offsets in m (+ right)",
                font=fs, fill=MUTED)
        # info box
        bx = PANEL_W - BOX_W - 10
        rows = s.info
        rh = 36
        bh = rh * (len(rows) + 1) + 10
        by = T
        dr.rectangle([bx, by, bx + BOX_W, by + bh], outline=NAVY, width=2)
        dr.rectangle([bx, by, bx + BOX_W, by + rh], fill=NAVY)
        dr.text((bx + 12, by + 5), s.title, font=_font(22, True), fill=(255, 255, 255))
        for j, (k_, v) in enumerate(rows):
            yy = by + rh * (j + 1) + 6
            col = CUT_RGB if k_.lower().startswith("dig") else FILL_RGB if k_.lower().startswith("fill") else INK
            dr.text((bx + 12, yy), k_, font=f, fill=col)
            tw = dr.textlength(v, font=_font(24, True))
            dr.text((bx + BOX_W - 12 - tw, yy), v, font=_font(24, True), fill=INK)
            if j:
                dr.line([(bx + 8, yy - 4), (bx + BOX_W - 8, yy - 4)], fill=GRID_RGB)
        out.append(im)
    return out


def pages(imgs: list[Image.Image], page_h: int = PAGE_H) -> list[Image.Image]:
    """Stack section pictures onto pages (as many as fit)."""
    out, cur, h = [], [], 0
    gap = 26
    for im in imgs:
        if cur and h + gap + im.height > page_h:
            out.append(cur)
            cur, h = [], 0
        cur.append(im)
        h += im.height + (gap if len(cur) > 1 else 0)
    if cur:
        out.append(cur)
    sheets = []
    for group in out:
        H = sum(i.height for i in group) + gap * (len(group) - 1)
        sh = Image.new("RGB", (PANEL_W, H), "white")
        y = 0
        for im in group:
            sh.paste(im, (0, y))
            y += im.height + gap
            if y < H:
                ImageDraw.Draw(sh).line([(40, y - gap // 2), (PANEL_W - 40, y - gap // 2)], fill=GRID_RGB, width=2)
        sheets.append(sh)
    return sheets


def slope_text(pct_down_outward: float) -> str:
    """Cross slope as shown on drawings: negative = falls away from the centre."""
    return f"{-pct_down_outward:+.1f}%" if pct_down_outward else "0.0%"


def ratio_text(r: float, cut: bool) -> str:
    return f"{r:g} : 1 {'cut' if cut else 'fill'}"
