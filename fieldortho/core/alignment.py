"""Horizontal alignment: straight lines joined by circular curves at the bends of a clicked road line.

At a bend (PI) with deflection angle D and radius R the curve starts at PC, T = R tan(D/2) before the PI,
and ends at PT, T after it; its length is R D. A radius of 0 keeps a sharp bend. When two curves (or a curve
and the line's end) need more room than the straight between them has, both are made smaller to fit and the
bend is flagged.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Curve:
    vertex: int               # index of the PI in the clicked points
    pi: tuple
    delta: float              # deflection, degrees (always positive)
    left: bool                # turns left (anticlockwise) going forward
    radius_asked: float
    radius: float             # used (smaller if it had to fit)
    tangent: float            # T
    length: float             # arc length
    pc: tuple = (0.0, 0.0)
    pt: tuple = (0.0, 0.0)
    center: tuple = (0.0, 0.0)
    pc_s: float = 0.0         # stations of the curve start / end
    pt_s: float = 0.0

    @property
    def reduced(self) -> bool:
        return self.radius_asked > 0 and self.radius < self.radius_asked * 0.99

    @property
    def mid_ordinate(self) -> float:
        return self.radius * (1 - math.cos(math.radians(self.delta) / 2))


@dataclass
class Element:
    kind: str                 # line | arc
    s0: float
    length: float
    start: tuple
    end: tuple
    curve: Curve | None = None


@dataclass
class Alignment:
    points: list
    elements: list = field(default_factory=list)
    curves: list = field(default_factory=list)
    bends: list = field(default_factory=list)      # stations of sharp (uncurved) bends

    @property
    def length(self) -> float:
        e = self.elements[-1]
        return e.s0 + e.length

    def point_at(self, s: float):
        """(x, y, tx, ty) at station s."""
        s = min(max(s, 0.0), self.length)
        for e in self.elements:
            if s <= e.s0 + e.length + 1e-9:
                break
        ds = s - e.s0
        if e.kind == "line":
            ux, uy = (e.end[0] - e.start[0]) / e.length, (e.end[1] - e.start[1]) / e.length
            return e.start[0] + ux * ds, e.start[1] + uy * ds, ux, uy
        c = e.curve
        sg = 1.0 if c.left else -1.0
        th0 = math.atan2(e.start[1] - c.center[1], e.start[0] - c.center[0])
        th = th0 + sg * ds / c.radius
        x, y = c.center[0] + c.radius * math.cos(th), c.center[1] + c.radius * math.sin(th)
        return x, y, -sg * math.sin(th), sg * math.cos(th)

    def densify(self, step: float):
        """Points along the line: (xy array, stations)."""
        ss = []
        for e in self.elements:
            n = max(1, int(math.ceil(e.length / step)))
            ss.extend(e.s0 + e.length * k / n for k in range(n))
        ss.append(self.length)
        ss = np.array(ss)
        xy = np.array([self.point_at(s)[:2] for s in ss])
        return xy, ss

    def key_stations(self) -> dict:
        """station -> kind for curve starts / ends and sharp bends."""
        out = {}
        for c in self.curves:
            out[round(c.pc_s, 6)] = "curve start"
            out[round(c.pt_s, 6)] = "curve end"
        for b in self.bends:
            out[round(b, 6)] = "bend"
        return out


def deflections(pts) -> list[tuple[float, bool]]:
    """(deflection in degrees, turns left) at each interior point."""
    p = np.asarray(pts, float)
    out = []
    for i in range(1, len(p) - 1):
        a, b = p[i] - p[i - 1], p[i + 1] - p[i]
        cr = a[0] * b[1] - a[1] * b[0]
        dt = a @ b
        out.append((abs(math.degrees(math.atan2(cr, dt))), cr > 0))
    return out


def build(pts, radii=None) -> Alignment:
    p = [tuple(map(float, q)) for q in pts]
    # drop repeated points
    q = [p[0]]
    for x in p[1:]:
        if math.dist(x, q[-1]) > 1e-6:
            q.append(x)
    p = q
    if len(p) < 2:
        raise ValueError("Click at least two points along the road.")
    n = len(p)
    seg = [math.dist(p[i], p[i + 1]) for i in range(n - 1)]
    radii = list(radii or [])
    radii = (radii + [0.0] * n)[: n - 2]
    defl = deflections(p)
    want_T = [0.0] * n
    for i in range(1, n - 1):
        d, _ = defl[i - 1]
        r = float(radii[i - 1] or 0)
        if r > 0 and d > 0.01:
            want_T[i] = r * math.tan(math.radians(d) / 2)
    # make neighbouring curves fit on the straight between them
    T = want_T[:]
    for _ in range(3):
        for j in range(n - 1):
            need = T[j] + T[j + 1]
            if need > seg[j] and need > 0:
                k = seg[j] / need * 0.999
                T[j] *= k
                T[j + 1] *= k
    al = Alignment(p)
    s = 0.0
    cur = p[0]
    for i in range(1, n):
        if i == n - 1 or T[i] <= 1e-6:
            # straight to this point (a sharp bend or the end)
            L = math.dist(cur, p[i])
            if L > 1e-9:
                al.elements.append(Element("line", s, L, cur, p[i]))
                s += L
            cur = p[i]
            if i < n - 1:
                al.bends.append(s)
            continue
        d, left = defl[i - 1]
        u1 = ((p[i][0] - p[i - 1][0]) / seg[i - 1], (p[i][1] - p[i - 1][1]) / seg[i - 1])
        u2 = ((p[i + 1][0] - p[i][0]) / seg[i], (p[i + 1][1] - p[i][1]) / seg[i])
        t = T[i]
        r = t / math.tan(math.radians(d) / 2)
        pc = (p[i][0] - u1[0] * t, p[i][1] - u1[1] * t)
        pt = (p[i][0] + u2[0] * t, p[i][1] + u2[1] * t)
        nx, ny = (-u1[1], u1[0]) if left else (u1[1], -u1[0])
        center = (pc[0] + nx * r, pc[1] + ny * r)
        L = math.dist(cur, pc)
        if L > 1e-9:
            al.elements.append(Element("line", s, L, cur, pc))
            s += L
        lc = r * math.radians(d)
        c = Curve(i, p[i], d, left, float(radii[i - 1]), r, t, lc, pc, pt, center, s, s + lc)
        al.elements.append(Element("arc", s, lc, pc, pt, c))
        al.curves.append(c)
        s += lc
        cur = pt
    if not al.elements:
        raise ValueError("The road line has no length.")
    return al


def straights_between(al: Alignment) -> list[tuple[Curve, Curve, float, bool]]:
    """(curve, next curve, straight length between them, reverse = they turn opposite ways)."""
    out = []
    for a, b in zip(al.curves[:-1], al.curves[1:]):
        out.append((a, b, b.pc_s - a.pt_s, a.left != b.left))
    return out
