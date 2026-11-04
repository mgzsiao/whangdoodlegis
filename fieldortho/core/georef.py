"""Georeferencing scanned / exported maps that have a coordinate grid.

Pipeline
  1. detect()      find the map frame (neatline), the grid tick marks around it and
                   the '+' grid crosses inside it  ->  sub-pixel grid line positions
  2. user          types the value of the first grid line + interval (label crops shown)
  3. make_gcps()   one control point per grid intersection (cross centre if found)
  4. fit()         affine (default) or 2nd-order polynomial, residuals in metres
  5. warp()        resample to a north-up GeoTIFF in any CRS (datum-aware)

Detection works on images that may be slightly rotated (scans): the skew is
estimated first and detection runs on a de-skewed copy; all pixel positions are
mapped back to the original image.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field, asdict

import numpy as np
from PIL import Image

from . import crs as crsmod

Image.MAX_IMAGE_PIXELS = 400_000_000


# ============================================================================
# Image helpers
# ============================================================================

def load_rgb(path: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def darkness(rgb: np.ndarray) -> np.ndarray:
    """0..255, high for black/grey ink only (coloured fills such as pure red stay low)."""
    return (255 - rgb.max(axis=2)).astype(np.int16)


# ============================================================================
# Skew
# ============================================================================

def estimate_skew(dark: np.ndarray, max_deg: float = 3.0) -> float:
    """Rotation (degrees, counter-clockwise positive) that makes long lines axis-aligned."""
    ys, xs = np.nonzero(dark > 140)
    if len(xs) < 1000:
        return 0.0
    if len(xs) > 400_000:
        idx = np.random.default_rng(0).choice(len(xs), 400_000, replace=False)
        xs, ys = xs[idx], ys[idx]
    best, best_a = -1.0, 0.0

    def score(a):
        t = math.radians(a)
        # project onto rotated y and x axes; long lines give sharp histogram peaks
        yr = (ys * math.cos(t) - xs * math.sin(t)).astype(np.int64)
        xr = (xs * math.cos(t) + ys * math.sin(t)).astype(np.int64)
        hy = np.bincount(yr - yr.min())
        hx = np.bincount(xr - xr.min())
        return float((hy.astype(np.float64) ** 2).sum() + (hx.astype(np.float64) ** 2).sum())

    for a in np.arange(-max_deg, max_deg + 1e-9, 0.1):
        sc = score(a)
        if sc > best:
            best, best_a = sc, a
    for a in np.arange(best_a - 0.1, best_a + 0.1 + 1e-9, 0.01):
        sc = score(a)
        if sc > best:
            best, best_a = sc, a
    return float(round(best_a, 3))


# ============================================================================
# Detection data
# ============================================================================

@dataclass
class GridLine:
    kind: str                       # "meridian" (x varies) or "parallel" (y varies)
    pos: float                      # position along the frame side (deskewed pixels)
    pos2: float | None = None       # position on the opposite side (None = perpendicular)
    sides: str = ""                 # which sides had ticks, e.g. "TB"
    value: float | None = None      # decimal degrees (or metres for projected grids)


@dataclass
class Detection:
    width: int
    height: int
    skew_deg: float
    frame: tuple                    # (left, top, right, bottom) in deskewed pixels
    meridians: list[GridLine] = field(default_factory=list)
    parallels: list[GridLine] = field(default_factory=list)
    crosses: list[tuple] = field(default_factory=list)   # (x, y) deskewed
    notes: list[str] = field(default_factory=list)

    # deskewed <-> original pixel coordinates -------------------------------
    def _center(self):
        return (self.width - 1) / 2.0, (self.height - 1) / 2.0

    def to_original(self, x, y):
        """Deskewed image pixel -> original image pixel."""
        cx, cy = self._center()
        t = math.radians(self.skew_deg)
        dx, dy = np.asarray(x) - cx, np.asarray(y) - cy
        # deskewed = rotate(original, -skew); invert
        xo = cx + dx * math.cos(t) - dy * math.sin(t)
        yo = cy + dx * math.sin(t) + dy * math.cos(t)
        return xo, yo

    def to_deskewed(self, x, y):
        cx, cy = self._center()
        t = math.radians(self.skew_deg)
        dx, dy = np.asarray(x) - cx, np.asarray(y) - cy
        xd = cx + dx * math.cos(t) + dy * math.sin(t)
        yd = cy - dx * math.sin(t) + dy * math.cos(t)
        return xd, yd

    def frame_corners_original(self):
        l, t, r, b = self.frame
        xs, ys = self.to_original([l, r, r, l], [t, t, b, b])
        return list(zip(np.atleast_1d(xs).tolist(), np.atleast_1d(ys).tolist()))

    def line_point(self, line: GridLine, other: float) -> tuple[float, float]:
        """Point on a grid line at a given coordinate along the other axis (deskewed)."""
        l, t, r, b = self.frame
        if line.kind == "meridian":
            p2 = line.pos if line.pos2 is None else line.pos2
            f = (other - t) / (b - t) if b != t else 0
            return line.pos + (p2 - line.pos) * f, other
        p2 = line.pos if line.pos2 is None else line.pos2
        f = (other - l) / (r - l) if r != l else 0
        return other, line.pos + (p2 - line.pos) * f

    def intersection(self, m: GridLine, p: GridLine) -> tuple[float, float]:
        y = p.pos
        for _ in range(3):        # two nearly-perpendicular lines converge immediately
            x, _y = self.line_point(m, y)
            _x, y = self.line_point(p, x)
        return x, y

    def to_dict(self):
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["meridians"] = [GridLine(**g) for g in d.get("meridians", [])]
        d["parallels"] = [GridLine(**g) for g in d.get("parallels", [])]
        d["frame"] = tuple(d["frame"])
        d["crosses"] = [tuple(c) for c in d.get("crosses", [])]
        return cls(**d)


# ============================================================================
# Frame and tick detection (on de-skewed darkness image)
# ============================================================================

def _runs(mask_1d: np.ndarray):
    """(start, length) of all True runs."""
    m = np.concatenate([[False], mask_1d, [False]])
    d = np.diff(m.astype(np.int8))
    starts = np.nonzero(d == 1)[0]
    ends = np.nonzero(d == -1)[0]
    return starts, ends - starts


def _long_lines(dark_mask: np.ndarray, axis: int, min_frac: float):
    """Long straight lines: list of (position, start, end). axis=0 -> horizontal lines (rows)."""
    arr = dark_mask if axis == 0 else dark_mask.T
    n, L = arr.shape
    raw = []
    for i in range(n):
        row = arr[i]
        if row.sum() < min_frac * L:
            continue
        # allow tiny gaps (JPEG / anti-aliasing): close gaps of <= 2 px
        r = row.copy()
        gaps_s, gaps_l = _runs(~r)
        for s, l in zip(gaps_s, gaps_l):
            if l <= 2 and s > 0 and s + l < L:
                r[s:s + l] = True
        st, ln = _runs(r)
        k = int(np.argmax(ln))
        if ln[k] >= min_frac * L:
            raw.append((i, int(st[k]), int(st[k] + ln[k] - 1)))
    # group adjacent rows into single lines
    lines = []
    for i, s, e in raw:
        if lines and i - lines[-1]["rows"][-1] <= 1 and abs(s - lines[-1]["s"]) < 6 and abs(e - lines[-1]["e"]) < 6:
            lines[-1]["rows"].append(i)
            lines[-1]["s"] = min(lines[-1]["s"], s)
            lines[-1]["e"] = max(lines[-1]["e"], e)
        else:
            lines.append({"rows": [i], "s": s, "e": e})
    return [(float(np.mean(g["rows"])), g["s"], g["e"], len(g["rows"])) for g in lines]


def _ticks_on_side(dk: np.ndarray, frame, side: str, max_len: int = 45):
    """Short ink strokes touching the outside of a frame side. Returns sorted sub-pixel positions."""
    l, t, r, b = frame
    H, W = dk.shape
    ink = dk > 110
    out = []
    if side in "TB":
        y_edge = int(round(t)) if side == "T" else int(round(b))
        step = -1 if side == "T" else 1
        x0, x1 = int(math.ceil(l)) + 3, int(math.floor(r)) - 3
        # skip the frame line itself (2-4 px thick)
        start = y_edge + step * 2
        while 0 <= start < H and ink[start, x0:x1].mean() > 0.5:
            start += step
        rows = [start + step * k for k in range(max_len) if 0 <= start + step * k < H]
        if len(rows) < 6:
            return []
        band = ink[min(rows):max(rows) + 1, x0:x1]
        if step < 0:
            band = band[::-1]           # first row = closest to frame
        lengths = _touching_lengths(band)
        cand = np.nonzero((lengths >= 5) & (lengths < max_len - 2))[0]
        for grp in _group(cand):
            if len(grp) > 8:
                continue
            cols = np.arange(grp[0] - 2, grp[-1] + 3) + x0
            cols = cols[(cols >= 0) & (cols < W)]
            prof = dk[min(rows):min(rows) + 6 if step > 0 else max(rows) + 1, cols] if False else \
                dk[(np.array(rows[:6]))[:, None], cols[None, :]].astype(float).sum(axis=0)
            prof = np.clip(prof - prof.min(), 0, None)
            if prof.sum() <= 0:
                continue
            out.append(float((cols * prof).sum() / prof.sum()))
    else:
        x_edge = int(round(l)) if side == "L" else int(round(r))
        step = -1 if side == "L" else 1
        y0, y1 = int(math.ceil(t)) + 3, int(math.floor(b)) - 3
        start = x_edge + step * 2
        while 0 <= start < W and ink[y0:y1, start].mean() > 0.5:
            start += step
        cols = [start + step * k for k in range(max_len) if 0 <= start + step * k < W]
        if len(cols) < 6:
            return []
        band = ink[y0:y1, min(cols):max(cols) + 1].T
        if step < 0:
            band = band[::-1]
        lengths = _touching_lengths(band)
        cand = np.nonzero((lengths >= 5) & (lengths < max_len - 2))[0]
        for grp in _group(cand):
            if len(grp) > 8:
                continue
            rws = np.arange(grp[0] - 2, grp[-1] + 3) + y0
            rws = rws[(rws >= 0) & (rws < H)]
            prof = dk[rws[:, None], np.array(cols[:6])[None, :]].astype(float).sum(axis=1)
            prof = np.clip(prof - prof.min(), 0, None)
            if prof.sum() <= 0:
                continue
            out.append(float((rws * prof).sum() / prof.sum()))
    return sorted(out)


def _touching_lengths(band: np.ndarray) -> np.ndarray:
    """For each column of band (row 0 = next to frame), length of ink run starting at rows 0-2."""
    n, m = band.shape
    lengths = np.zeros(m, dtype=int)
    for j in np.nonzero(band[:3].any(axis=0))[0]:
        col = band[:, j]
        k0 = int(np.argmax(col[:3]))
        k = k0
        while k < n and col[k]:
            k += 1
        lengths[j] = k - k0 if k0 <= 2 else 0
    return lengths


def _group(idx: np.ndarray, gap: int = 1):
    groups, cur = [], []
    for i in idx:
        if cur and i - cur[-1] > gap:
            groups.append(cur)
            cur = []
        cur.append(int(i))
    if cur:
        groups.append(cur)
    return groups


def _rotate(rgb: np.ndarray, deg: float) -> np.ndarray:
    if abs(deg) < 0.005:
        return rgb
    im = Image.fromarray(rgb).rotate(-deg, resample=Image.BICUBIC, expand=False, fillcolor=(255, 255, 255))
    return np.asarray(im)


def find_cross(dk: np.ndarray, x: float, y: float, search: int = 10, arm: int = 5):
    """Centre of a small '+' grid cross near (x, y). Returns (x, y, score) or None.
    Typical MGB/NAMRIA crosses: ~11 px wide, 1-2 px thin grey strokes."""
    H, W = dk.shape
    pad = search + arm + 6
    xi, yi = int(round(x)), int(round(y))
    if xi - pad < 0 or yi - pad < 0 or xi + pad >= W or yi + pad >= H:
        return None
    win = dk[yi - pad:yi + pad + 1, xi - pad:xi + pad + 1].astype(float)
    d = np.clip(win - np.median(win), 0, None)
    c = pad
    best = None
    for dy in range(-search, search + 1):
        for dx in range(-search, search + 1):
            cy, cx = c + dy, c + dx
            # two-pixel-wide arms tolerate anti-aliasing
            hl = np.maximum(d[cy, cx - arm:cx - 1], d[cy + 1, cx - arm:cx - 1])
            hr = np.maximum(d[cy, cx + 2:cx + arm + 1], d[cy + 1, cx + 2:cx + arm + 1])
            vu = np.maximum(d[cy - arm:cy - 1, cx], d[cy - arm:cy - 1, cx + 1])
            vd = np.maximum(d[cy + 2:cy + arm + 1, cx], d[cy + 2:cy + arm + 1, cx + 1])
            arms = np.array([hl.mean(), hr.mean(), vu.mean(), vd.mean()])
            quad = np.array([d[cy - 3, cx - 3], d[cy - 3, cx + 4], d[cy + 4, cx - 3], d[cy + 4, cx + 4],
                             d[cy - 2, cx - 4], d[cy + 4, cx + 2]])
            beyond = np.array([d[cy, cx - arm - 4], d[cy, cx + arm + 5], d[cy - arm - 4, cx], d[cy + arm + 5, cx]])
            sc = arms.min() - quad.max() - 0.5 * beyond.max()
            if best is None or sc > best[0]:
                best = (sc, cx, cy, arms.min())
    sc, cx, cy, amin = best
    if sc < 18 or amin < 25:
        return None
    # sub-pixel: centroid of the vertical arm across columns, horizontal arm across rows
    rows = np.r_[cy - arm:cy - 1, cy + 2:cy + arm + 1]
    cols_w = np.arange(cx - 2, cx + 4)
    pv = d[rows[:, None], cols_w[None, :]].sum(axis=0)
    cols = np.r_[cx - arm:cx - 1, cx + 2:cx + arm + 1]
    rows_w = np.arange(cy - 2, cy + 4)
    ph = d[rows_w[:, None], cols[None, :]].sum(axis=1)
    sx = (cols_w * pv).sum() / pv.sum() if pv.sum() > 0 else cx
    sy = (rows_w * ph).sum() / ph.sum() if ph.sum() > 0 else cy
    return xi - pad + float(sx), yi - pad + float(sy), float(sc)


def _frame_tilt(dk: np.ndarray, frame) -> float:
    """Measured tilt (degrees) of the frame's long sides - true scan rotation."""
    l, t, r, b = frame
    angs = []
    for y_line in (t, b):
        xs = np.linspace(l + 20, r - 20, 60).astype(int)
        cents = []
        for x in xs:
            y0 = int(round(y_line))
            col = dk[y0 - 4:y0 + 5, x].astype(float)
            if col.sum() > 0:
                cents.append((x, y0 - 4 + (np.arange(9) * col).sum() / col.sum()))
        if len(cents) > 10:
            a = np.polyfit([c[0] for c in cents], [c[1] for c in cents], 1)[0]
            angs.append(math.degrees(math.atan(a)))
    for x_line in (l, r):
        ys = np.linspace(t + 20, b - 20, 60).astype(int)
        cents = []
        for y in ys:
            x0 = int(round(x_line))
            row = dk[y, x0 - 4:x0 + 5].astype(float)
            if row.sum() > 0:
                cents.append((y, x0 - 4 + (np.arange(9) * row).sum() / row.sum()))
        if len(cents) > 10:
            a = np.polyfit([c[0] for c in cents], [c[1] for c in cents], 1)[0]
            angs.append(-math.degrees(math.atan(a)))
    return float(np.median(angs)) if angs else 0.0


def _find_frame(dk: np.ndarray):
    H, W = dk.shape
    ink = dk > 110
    hl = _long_lines(ink, 0, 0.25)
    vl = _long_lines(ink, 1, 0.25)
    rects = []
    tol = 12
    for i, (yt, hs, he, _) in enumerate(hl):
        for (yb, hs2, he2, _) in hl[i + 1:]:
            if yb - yt < 0.15 * H:
                continue
            for j, (xl, vs, ve, _) in enumerate(vl):
                if vs > yt + tol or ve < yb - tol:          # vertical must span top..bottom
                    continue
                for (xr, vs2, ve2, _) in vl[j + 1:]:
                    if xr - xl < 0.15 * W or vs2 > yt + tol or ve2 < yb - tol:
                        continue
                    # horizontals must span left..right (overshoot allowed)
                    if hs > xl + tol or he < xr - tol or hs2 > xl + tol or he2 < xr - tol:
                        continue
                    rects.append((xl, yt, xr, yb))
    best = None
    for fr in rects:
        ticks = {s_: _ticks_on_side(dk, fr, s_) for s_ in "TBLR"}
        n = sum(len(v) for v in ticks.values())
        # prefer rectangles with ticks; among equals the larger one
        key = (n, (fr[2] - fr[0]) * (fr[3] - fr[1]))
        if best is None or key > best[0]:
            best = (key, fr, ticks)
    return best


def detect(rgb: np.ndarray) -> Detection:
    H, W = rgb.shape[:2]
    dk = darkness(rgb)
    skew = 0.0
    best = _find_frame(dk)
    if best is not None:
        tilt = _frame_tilt(dk, best[1])
        if abs(tilt) >= 0.03:                      # a real scan rotation: de-skew and redo
            skew = tilt
    else:
        skew = estimate_skew(dk[::2, ::2])
        if abs(skew) < 0.03:
            skew = 0.0
    if skew:
        dk = darkness(_rotate(rgb, skew))
        best = _find_frame(dk)
    if best is None:
        raise ValueError("No map frame (neatline) found. Add control points by clicking instead.")
    (n_ticks, _a), frame, ticks = best
    det = Detection(W, H, skew, tuple(float(v) for v in frame))
    if n_ticks < 2:
        det.notes.append("Map frame found, but no grid tick marks around it.")
    det.meridians = _pair(ticks["T"], ticks["B"], "meridian", "TB")
    det.parallels = _pair(ticks["L"], ticks["R"], "parallel", "LR")
    for m in det.meridians:
        for p in det.parallels:
            x, y = det.intersection(m, p)
            c = find_cross(dk, x, y)
            if c and math.hypot(c[0] - x, c[1] - y) < 6:
                det.crosses.append((c[0], c[1]))
    if skew:
        det.notes.append(f"Image is rotated by {skew:.2f}\u00b0; corrected during detection.")
    return det


def _pair(a: list[float], b: list[float], kind: str, sides: str, tol: float = 25.0) -> list[GridLine]:
    lines = []
    used = set()
    for p in a:
        q = None
        if b:
            j = int(np.argmin([abs(p - v) for v in b]))
            if abs(b[j] - p) <= tol and j not in used:
                q = b[j]
                used.add(j)
        lines.append(GridLine(kind, p, q, sides if q is not None else sides[0]))
    for j, v in enumerate(b):
        if j not in used:
            lines.append(GridLine(kind, v, None, sides[1]))
    lines.sort(key=lambda g: g.pos)
    return lines


def label_box(det: Detection, line: GridLine) -> tuple[int, int, int, int]:
    """Region (original image pixels, axis-aligned) where a grid line's label is printed:
    just outside the frame, next to the tick."""
    l, t, r, b = det.frame
    if line.kind == "meridian":
        side = line.sides[0] if line.sides else "T"
        cx = line.pos if side == "T" else (line.pos2 if line.pos2 is not None else line.pos)
        box_d = (cx - 42, t - 46, cx + 62, t - 4) if side == "T" else (cx - 42, b + 4, cx + 62, b + 46)
    else:
        side = line.sides[0] if line.sides else "L"
        cy = line.pos if side == "L" else (line.pos2 if line.pos2 is not None else line.pos)
        box_d = (l - 78, cy - 34, l - 2, cy + 34) if side == "L" else (r + 2, cy - 34, r + 78, cy + 34)
    xs, ys = det.to_original([box_d[0], box_d[2], box_d[0], box_d[2]], [box_d[1], box_d[1], box_d[3], box_d[3]])
    x0, x1 = max(0, int(min(xs))), min(det.width, int(max(xs)))
    y0, y1 = max(0, int(min(ys))), min(det.height, int(max(ys)))
    return x0, y0, x1, y1


# ============================================================================
# Coordinates text
# ============================================================================

_DMS = re.compile(r"""^\s*([+-])?\s*(\d+(?:[.,]\d+)?)\s*(?:[°d:\s]\s*(\d+(?:[.,]\d+)?)?\s*(?:['m\u2032:\s]\s*
                   (\d+(?:[.,]\d+)?)?\s*(?:["s\u2033]|'')?)?)?\s*([NSEWnsew])?\s*$""", re.X)


def parse_angle(text: str) -> float:
    """'124°47'0"E', '124 47', '124.7833', '8°52'N', '-8.5' -> decimal degrees."""
    t = text.strip().replace("\u00ba", "\u00b0").replace("''", '"')
    m = _DMS.match(t)
    if not m:
        raise ValueError(f"Cannot read '{text}'. Use e.g. 124\u00b047'0\"E or 124.7833")
    sign, d, mi, s, hemi = m.groups()
    val = float(d.replace(",", ".")) + (float(mi.replace(",", ".")) / 60 if mi else 0) + \
        (float(s.replace(",", ".")) / 3600 if s else 0)
    if (mi and float(mi.replace(",", ".")) >= 60) or (s and float(s.replace(",", ".")) >= 60):
        raise ValueError(f"Minutes and seconds must be below 60 in '{text}'.")
    if sign == "-" or (hemi and hemi.upper() in "SW"):
        val = -val
    return val


def format_dms(v: float, is_lat: bool) -> str:
    hemi = ("N" if v >= 0 else "S") if is_lat else ("E" if v >= 0 else "W")
    v = abs(v)
    d = int(v)
    m = int((v - d) * 60 + 1e-9)
    s = (v - d - m / 60) * 3600
    if s > 59.995:
        s, m = 0, m + 1
    if m == 60:
        m, d = 0, d + 1
    return f"{d}\u00b0{m:02d}'{s:04.1f}\"{hemi}" if abs(s - round(s)) > 0.05 else f"{d}\u00b0{m:02d}'{int(round(s)):02d}\"{hemi}"


def parse_interval(text: str) -> float:
    """'1'' -> 1/60, '30"' -> 1/120, '0.5' -> 0.5 degrees, '1 min' -> 1/60."""
    t = text.strip().lower().replace(" ", "")
    m = re.match(r"^(\d+(?:[.,]\d+)?)(°|deg|d|'|min|m|\u2032|\"|sec|s|\u2033|'')?$", t)
    if not m:
        raise ValueError(f"Cannot read interval '{text}'. Use e.g. 1' or 30\" or 0.5\u00b0")
    v = float(m.group(1).replace(",", "."))
    u = m.group(2) or "°"
    if u in ("'", "min", "m", "\u2032"):
        return v / 60
    if u in ('"', "sec", "s", "\u2033", "''"):
        return v / 3600
    return v


# ============================================================================
# Control points and fitting
# ============================================================================

@dataclass
class GCP:
    px: float                 # original image pixel (column, 0 = left edge of first pixel)
    py: float
    gx: float                 # coordinate in the grid CRS (lon or easting)
    gy: float                 # (lat or northing)
    enabled: bool = True
    source: str = "grid"      # grid | cross | manual
    name: str = ""
    res_m: float | None = None
    res_dx: float | None = None
    res_dy: float | None = None


def make_gcps(det: Detection, prefer_crosses: bool = False) -> list[GCP]:
    """One GCP per grid intersection with known values."""
    gcps = []
    cr = np.array(det.crosses) if det.crosses else np.zeros((0, 2))
    for m in det.meridians:
        if m.value is None:
            continue
        for p in det.parallels:
            if p.value is None:
                continue
            x, y = det.intersection(m, p)
            src = "grid"
            if prefer_crosses and len(cr):
                d = np.hypot(cr[:, 0] - x, cr[:, 1] - y)
                k = int(np.argmin(d))
                if d[k] < 6:
                    x, y = cr[k]
                    src = "cross"
            ox, oy = det.to_original(x, y)
            gcps.append(GCP(float(ox) + 0.5, float(oy) + 0.5, m.value, p.value, True, src,
                            f"{format_dms(p.value, True)} {format_dms(m.value, False)}"))
    return gcps


def tick_cross_agreement(det: Detection) -> float | None:
    """Mean distance (px) between tick-line intersections and detected crosses - a
    built-in consistency check of the map's own drawing and of the detection."""
    if not det.crosses:
        return None
    ds = []
    cr = np.array(det.crosses)
    for m in det.meridians:
        for p in det.parallels:
            x, y = det.intersection(m, p)
            d = np.hypot(cr[:, 0] - x, cr[:, 1] - y).min()
            if d < 6:
                ds.append(d)
    return float(np.mean(ds)) if ds else None


def _design(u, v, order):
    u, v = np.asarray(u, float), np.asarray(v, float)
    cols = [np.ones_like(u), u, v]
    if order >= 2:
        cols += [u * u, u * v, v * v]
    if order >= 3:
        cols += [u ** 3, u * u * v, u * v * v, v ** 3]
    return np.column_stack(cols)


MIN_POINTS = {1: 3, 2: 6, 3: 10}


@dataclass
class Fit:
    order: int
    work_crs: str                 # projected CRS the fit is done in (WKT)
    grid_crs: str
    lonlat: tuple
    cx: np.ndarray                # forward coefficients (pixel -> work X)
    cy: np.ndarray
    ix: np.ndarray                # inverse coefficients (work -> pixel u)
    iy: np.ndarray
    norm: tuple                   # (u0, v0, su, X0, Y0, sX) normalisation
    rmse_m: float
    max_m: float
    n: int
    pixel_m: float                # ground size of one image pixel (m)

    def forward(self, u, v):
        u0, v0, su, X0, Y0, sX = self.norm
        A = _design((np.asarray(u) - u0) / su, (np.asarray(v) - v0) / su, self.order)
        return A @ self.cx * sX + X0, A @ self.cy * sX + Y0

    def inverse(self, X, Y):
        u0, v0, su, X0, Y0, sX = self.norm
        A = _design((np.asarray(X) - X0) / sX, (np.asarray(Y) - Y0) / sX, self.order)
        return A @ self.ix * su + u0, A @ self.iy * su + v0


def work_crs_for(grid_crs: str, lon: float, lat: float, dst_crs: str | None = None) -> str:
    """Projected CRS to fit in: the destination if projected, else UTM of the site."""
    if dst_crs and not crsmod.is_geographic(dst_crs):
        return dst_crs
    if not crsmod.is_geographic(grid_crs):
        return grid_crs
    return f"EPSG:{crsmod.utm_epsg(lat, lon)}"


def fit(gcps: list[GCP], grid_crs: str, order: int = 1, dst_crs: str | None = None) -> Fit:
    pts = [g for g in gcps if g.enabled]
    if len(pts) < MIN_POINTS[order]:
        raise ValueError(f"Need at least {MIN_POINTS[order]} enabled control points for this method "
                         f"(have {len(pts)}).")
    gx = np.array([g.gx for g in pts])
    gy = np.array([g.gy for g in pts])
    # site location (for datum parameters)
    if crsmod.is_geographic(grid_crs):
        lon, lat = float(gx.mean()), float(gy.mean())
    else:
        t = crsmod.transformer(grid_crs, "EPSG:4326")
        lon, lat = t.transform(float(gx.mean()), float(gy.mean()))
    wcrs = work_crs_for(grid_crs, lon, lat, dst_crs)
    X, Y = crsmod.transform(gx, gy, grid_crs, wcrs, lon, lat)
    X, Y = np.asarray(X), np.asarray(Y)
    u = np.array([g.px for g in pts])
    v = np.array([g.py for g in pts])
    u0, v0 = u.mean(), v.mean()
    su = max(u.std(), v.std(), 1.0)
    X0, Y0 = X.mean(), Y.mean()
    sX = max(X.std(), Y.std(), 1.0)
    A = _design((u - u0) / su, (v - v0) / su, order)
    cx, *_ = np.linalg.lstsq(A, (X - X0) / sX, rcond=None)
    cy, *_ = np.linalg.lstsq(A, (Y - Y0) / sX, rcond=None)
    B = _design((X - X0) / sX, (Y - Y0) / sX, order)
    ix, *_ = np.linalg.lstsq(B, (u - u0) / su, rcond=None)
    iy, *_ = np.linalg.lstsq(B, (v - v0) / su, rcond=None)
    f = Fit(order, crsmod.resolve(wcrs, lon, lat).to_wkt(), grid_crs, (lon, lat), cx, cy, ix, iy,
            (u0, v0, su, X0, Y0, sX), 0.0, 0.0, len(pts), 0.0)
    fx, fy = f.forward(u, v)
    dx, dy = fx - X, fy - Y
    r = np.hypot(dx, dy)
    for g, a, b, c in zip(pts, dx, dy, r):
        g.res_dx, g.res_dy, g.res_m = float(a), float(b), float(c)
    for g in gcps:
        if not g.enabled:
            # report how far a disabled point is from the fitted model (independent check)
            Xg, Yg = crsmod.transform([g.gx], [g.gy], grid_crs, wcrs, lon, lat)
            px_, py_ = f.forward([g.px], [g.py])
            g.res_dx, g.res_dy = float(px_[0] - Xg[0]), float(py_[0] - Yg[0])
            g.res_m = float(math.hypot(g.res_dx, g.res_dy))
    dof = max(1, len(pts) - A.shape[1])
    f.rmse_m = float(math.sqrt((r ** 2).sum() / dof)) if len(pts) > A.shape[1] else 0.0
    f.max_m = float(r.max())
    # pixel size from the local Jacobian at the centre
    x1, y1 = f.forward([u0, u0 + 1, u0], [v0, v0, v0 + 1])
    f.pixel_m = float(math.sqrt(abs((x1[1] - x1[0]) * (y1[2] - y1[0]) - (x1[2] - x1[0]) * (y1[1] - y1[0]))))
    return f


def rotation_deg(f: Fit) -> float:
    u0, v0 = f.norm[0], f.norm[1]
    x, y = f.forward([u0, u0 + 100], [v0, v0])
    return math.degrees(math.atan2(y[1] - y[0], x[1] - x[0]))


# ============================================================================
# Warping
# ============================================================================

def _points_in_poly(x: np.ndarray, y: np.ndarray, poly) -> np.ndarray:
    inside = np.zeros(x.shape, dtype=bool)
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cond = (y1 > y) != (y2 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (x2 - x1) * (y - y1) / (y2 - y1) + x1
        inside ^= cond & (x < xint)
    return inside


def warp_image(img: np.ndarray, f: Fit, dst_crs: str, out_path: str, *, crop: list | None = None,
               resampling: str = "cubic", pixel_size: float | None = None, progress=None) -> dict:
    """img: HxWxC uint8 (RGB) or HxW (class codes). Writes a GeoTIFF (RGBA or 1-band)."""
    import rasterio
    from rasterio.transform import from_origin
    from scipy.ndimage import map_coordinates

    lon, lat = f.lonlat
    H, W = img.shape[:2]
    poly = crop or [(0, 0), (W, 0), (W, H), (0, H)]
    pu = np.array([p[0] for p in poly], float)
    pv = np.array([p[1] for p in poly], float)
    # densify outline so curved (reprojected) edges are bounded correctly
    du, dv = [], []
    for i in range(len(pu)):
        j = (i + 1) % len(pu)
        t = np.linspace(0, 1, 50, endpoint=False)
        du.append(pu[i] + (pu[j] - pu[i]) * t)
        dv.append(pv[i] + (pv[j] - pv[i]) * t)
    du, dv = np.concatenate(du), np.concatenate(dv)
    wx, wy = f.forward(du, dv)
    dst = crsmod.resolve(dst_crs, lon, lat)
    w2d = crsmod.transformer(f.work_crs, dst.to_wkt(), lon, lat)
    d2w = crsmod.transformer(dst.to_wkt(), f.work_crs, lon, lat)
    ex_, ey_ = w2d.transform(wx, wy)
    xmin, xmax, ymin, ymax = float(np.min(ex_)), float(np.max(ex_)), float(np.min(ey_)), float(np.max(ey_))
    if pixel_size is None:
        if dst.is_geographic:
            pixel_size = f.pixel_m / (111320.0 * math.cos(math.radians(lat)))
        else:
            # metres in dst CRS per metre in work CRS (scale differs slightly between projections)
            sx, sy = w2d.transform([wx[0], wx[0] + 100.0], [wy[0], wy[0]])
            pixel_size = f.pixel_m * math.hypot(sx[1] - sx[0], sy[1] - sy[0]) / 100.0
    ncols = int(math.ceil((xmax - xmin) / pixel_size))
    nrows = int(math.ceil((ymax - ymin) / pixel_size))
    if ncols * nrows > 600_000_000:
        raise ValueError("Output would be too large; choose a larger pixel size.")
    tr = from_origin(xmin, ymax, pixel_size, pixel_size)
    order = {"nearest": 0, "bilinear": 1, "cubic": 3}[resampling]
    is_rgb = img.ndim == 3
    bands = [img[..., c] for c in range(img.shape[2])] if is_rgb else [img]
    out_count = 4 if is_rgb else 1
    prof = dict(driver="GTiff", width=ncols, height=nrows, count=out_count, dtype="uint8" if is_rgb else img.dtype,
                crs=rasterio.crs.CRS.from_wkt(dst.to_wkt()), transform=tr, tiled=True, blockxsize=256,
                blockysize=256, compress="DEFLATE", BIGTIFF="IF_SAFER")
    if is_rgb:
        prof["photometric"] = "RGB"
    else:
        prof["nodata"] = 0
    step = 16
    with rasterio.open(out_path, "w", **prof) as out:
        block = 256
        for r0 in range(0, nrows, block):
            r1 = min(nrows, r0 + block)
            # coarse coordinate grid -> bilinear interpolation (exact enough: < 0.01 px)
            cs = np.arange(0, ncols + step, step, dtype=float)
            rs = np.arange(r0, r1 + step, step, dtype=float)
            CC, RR = np.meshgrid(cs, rs)
            Xd = xmin + (CC + 0.5) * pixel_size
            Yd = ymax - (RR + 0.5) * pixel_size
            Xw, Yw = d2w.transform(Xd, Yd)
            U, V = f.inverse(np.asarray(Xw).ravel(), np.asarray(Yw).ravel())
            U = U.reshape(CC.shape)
            V = V.reshape(CC.shape)
            cc = np.arange(ncols, dtype=float)
            rr = np.arange(r0, r1, dtype=float)
            fc = cc / step
            fr = (rr - r0) / step
            Ufull = _bilinear_grid(U, fr, fc)
            Vfull = _bilinear_grid(V, fr, fc)
            # pixel centres: image pixel (i, j) covers [j, j+1) -> centre j+0.5
            uc, vc = Ufull - 0.5, Vfull - 0.5
            inside = (Ufull >= 0) & (Ufull <= W) & (Vfull >= 0) & (Vfull <= H)
            if crop:
                inside &= _points_in_poly(Ufull, Vfull, crop)
            for bi, band in enumerate(bands):
                vals = map_coordinates(band, [vc.ravel(), uc.ravel()], order=order, mode="nearest",
                                       prefilter=order > 1).reshape(uc.shape)
                if order > 1:
                    vals = np.clip(vals, 0, 255)
                vals = np.where(inside, vals, 0 if not is_rgb else 255)
                out.write(vals.astype(prof["dtype"]), bi + 1,
                          window=rasterio.windows.Window(0, r0, ncols, r1 - r0))
            if is_rgb:
                out.write((inside * 255).astype("uint8"), 4, window=rasterio.windows.Window(0, r0, ncols, r1 - r0))
            if progress:
                progress(100.0 * r1 / nrows)
        if is_rgb:
            out.colorinterp = [rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                               rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha]
    return {"width": ncols, "height": nrows, "pixel_size": pixel_size, "bounds": (xmin, ymin, xmax, ymax)}


def _bilinear_grid(G: np.ndarray, fr: np.ndarray, fc: np.ndarray) -> np.ndarray:
    r0 = np.clip(np.floor(fr).astype(int), 0, G.shape[0] - 2)
    c0 = np.clip(np.floor(fc).astype(int), 0, G.shape[1] - 2)
    tr_ = (fr - r0)[:, None]
    tc = (fc - c0)[None, :]
    g00 = G[r0][:, c0]
    g01 = G[r0][:, c0 + 1]
    g10 = G[r0 + 1][:, c0]
    g11 = G[r0 + 1][:, c0 + 1]
    return (g00 * (1 - tr_) * (1 - tc) + g01 * (1 - tr_) * tc + g10 * tr_ * (1 - tc) + g11 * tr_ * tc)


# ============================================================================
# GCP files
# ============================================================================

def write_qgis_points(gcps: list[GCP], path: str, grid_crs: str):
    """QGIS Georeferencer .points file (pixel Y negative, as QGIS expects)."""
    c = crsmod.resolve(grid_crs)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"#CRS: {c.to_wkt()}\n")
        fh.write("mapX,mapY,sourceX,sourceY,enable,dX,dY,residual\n")
        for g in gcps:
            fh.write(f"{g.gx:.10f},{g.gy:.10f},{g.px:.4f},{-g.py:.4f},{int(g.enabled)},"
                     f"{(g.res_dx or 0):.4f},{(g.res_dy or 0):.4f},{(g.res_m or 0):.4f}\n")


def read_qgis_points(path: str) -> list[GCP]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(("#", "mapX")) or not line.strip():
                continue
            p = line.strip().split(",")
            out.append(GCP(float(p[2]), -float(p[3]), float(p[0]), float(p[1]),
                           bool(int(float(p[4]))) if len(p) > 4 else True, "manual"))
    return out


def save_session(path: str, image_path: str, det: Detection | None, gcps: list[GCP], grid_crs: str, extra=None):
    d = {"image": image_path, "grid_crs": grid_crs, "detection": det.to_dict() if det else None,
         "gcps": [asdict(g) for g in gcps], "extra": extra or {}}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)


def load_session(path: str):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    det = Detection.from_dict(d["detection"]) if d.get("detection") else None
    return d["image"], det, [GCP(**g) for g in d.get("gcps", [])], d.get("grid_crs", "EPSG:4253"), d.get("extra", {})
