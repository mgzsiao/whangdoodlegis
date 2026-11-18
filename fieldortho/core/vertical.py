"""Vertical alignment of a road: straight grades between PVIs joined by parabolic vertical curves.

PVI  = point of vertical intersection (where two straight grades meet), at a station and an elevation.
PVC  = start of the vertical curve, PVT = end; the curve is centred on the PVI (equal tangents).
On the curve, x metres after the PVC:   z = z_PVC + g1 x + (g2 - g1) x² / (2 L)
A    = g2 - g1 in percent, K = L / |A| (metres of curve per 1 % of grade change).
Crest curve when the road goes over a hump (g2 < g1), sag curve in a dip (g2 > g1).

Minimum curve lengths come from the design speed: L = K x |A|, with the K values of AASHTO,
"A Policy on Geometric Design of Highways and Streets" (Green Book), 7th ed. 2018: Table 3-35 (crest,
stopping sight distance, driver eye 1.08 m, object 0.60 m) and Table 3-37 (sag, headlight sight distance),
and a minimum length of 0.6 x design speed (km/h) in metres (Green Book 3.4.6). Values rounded as
printed in the Green Book for design. Since 1.2 the designer also sets a shortest curve (office rule,
60 m by default): L = max(K x |A|, 0.6 V, shortest). A curve that does not fit between its neighbours is
shortened, warned about and marked in the report.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

AASHTO_SOURCE = ("AASHTO, A Policy on Geometric Design of Highways and Streets (Green Book), 7th ed. 2018, "
                 "Table 3-35 (crest) and Table 3-37 (sag); minimum length 0.6 x design speed")
# design speed km/h: (stopping sight distance m, K crest, K sag)
K_TABLE = {20: (20, 1, 3), 30: (35, 2, 6), 40: (50, 4, 9), 50: (65, 7, 13), 60: (85, 11, 18),
           70: (105, 17, 23), 80: (130, 26, 30)}
SPEEDS = tuple(K_TABLE)
MIN_A = 0.2            # % : smaller grade changes need no curve


def k_values(speed: int) -> tuple[float, float]:
    """(K crest, K sag) for a design speed (km/h); speeds between table rows use the next higher row."""
    for v in SPEEDS:
        if speed <= v:
            return float(K_TABLE[v][1]), float(K_TABLE[v][2])
    return float(K_TABLE[SPEEDS[-1]][1]), float(K_TABLE[SPEEDS[-1]][2])


def min_length(speed: int) -> float:
    return 0.6 * speed


@dataclass
class VCurve:
    n: int
    pvi_s: float
    pvi_z: float
    g1: float               # % in
    g2: float               # % out
    length: float           # L (m), 0 = no curve
    length_asked: float     # what the speed / user asked (before fitting between PVIs)
    k_min: float            # K needed for the design speed
    typed: bool = False     # length typed by the user

    @property
    def a(self) -> float:
        return self.g2 - self.g1

    @property
    def kind(self) -> str:
        return "crest" if self.a < 0 else "sag"

    @property
    def k(self) -> float:
        return self.length / abs(self.a) if abs(self.a) > 1e-9 else float("inf")

    @property
    def pvc_s(self) -> float:
        return self.pvi_s - self.length / 2

    @property
    def pvt_s(self) -> float:
        return self.pvi_s + self.length / 2

    @property
    def pvc_z(self) -> float:
        return self.pvi_z - self.g1 / 100 * self.length / 2

    @property
    def pvt_z(self) -> float:
        return self.pvi_z + self.g2 / 100 * self.length / 2

    @property
    def middle_offset(self) -> float:
        """Vertical distance between the PVI and the curve (e = A L / 800)."""
        return abs(self.a) * self.length / 800

    def turning_point(self) -> tuple[float, float] | None:
        """Highest point of a crest / lowest of a sag, when it lies on the curve."""
        if self.length <= 0 or abs(self.a) < 1e-9:
            return None
        x = -self.g1 * self.length / self.a
        if not (0 <= x <= self.length):
            return None
        return self.pvc_s + x, self.pvc_z + self.g1 / 100 * x + self.a / 100 * x * x / (2 * self.length)

    @property
    def reduced(self) -> bool:
        return self.length < self.length_asked - 0.01

    @property
    def short(self) -> bool:
        """Shorter than the design speed needs (sight distance)."""
        return abs(self.a) >= MIN_A and self.k < self.k_min - 1e-6


def propose(d: np.ndarray, z: np.ndarray, tol: float = 0.10, min_gap: float = 20.0,
            end_gap: float | None = None) -> list[list[float]]:
    """PVIs [[station, elevation, 0], ...] that follow a road height line within `tol` metres, at least
    `min_gap` apart and at least `end_gap` (default min_gap) from the start and the end, so curves of
    min_gap length fit (Douglas-Peucker on the side view)."""
    end_gap = min_gap if end_gap is None else end_gap
    d = np.asarray(d, float)
    z = np.asarray(z, float)
    keep = {0, len(d) - 1}

    def split(i, j):
        if j <= i + 1:
            return
        t = (d[i + 1:j] - d[i]) / max(d[j] - d[i], 1e-9)
        line = z[i] + t * (z[j] - z[i])
        err = np.abs(z[i + 1:j] - line)
        k = int(np.argmax(err))
        if err[k] > tol:
            m = i + 1 + k
            keep.add(m)
            split(i, m)
            split(m, j)
    split(0, len(d) - 1)
    idx = sorted(keep)
    # merge points closer than min_gap (end_gap next to the start / end; keep the one where the grade changes most)
    while len(idx) > 2:
        gaps = np.diff(d[idx])
        need = np.full(len(gaps), float(min_gap))
        need[0] = need[-1] = end_gap
        short = gaps < need - 1e-9
        if not short.any():
            break
        k = int(np.argmin(np.where(short, gaps - need, np.inf)))
        if k == 0:
            del idx[1]
        elif k == len(idx) - 2:
            del idx[-2]
        else:
            def bend(m):
                a, b, c = idx[m - 1], idx[m], idx[m + 1]
                return abs((z[c] - z[b]) / max(d[c] - d[b], 1e-9) - (z[b] - z[a]) / max(d[b] - d[a], 1e-9))
            del idx[k if bend(k) < bend(k + 1) else k + 1]
    return [[float(d[i]), round(float(z[i]), 3), 0.0] for i in idx]


def build(pvis: list, speed: int, end_s: float | None = None, min_len: float = 0.0) -> list[VCurve]:
    """Curves at the inner PVIs. pvis: [[station, elevation, length (0 = from design speed)], ...].
    min_len: the designer's shortest curve (PVC to PVT), on top of the speed's K and 0.6 V."""
    p = sorted([float(r[0]), float(r[1]), float(r[2]) if len(r) > 2 and r[2] else 0.0] for r in pvis)
    if len(p) < 2:
        raise ValueError("At least a start and an end point are needed for the road heights.")
    for a, b in zip(p, p[1:]):
        if b[0] - a[0] < 1.0:
            raise ValueError(f"Two grade change points are too close (stations {a[0]:.1f} and {b[0]:.1f} m).")
    kc, ks = k_values(speed)
    lmin = max(min_length(speed), float(min_len or 0.0))
    out = []
    for i in range(1, len(p) - 1):
        (s0, z0, _), (s1, z1, typed_l), (s2, z2, _) = p[i - 1], p[i], p[i + 1]
        g1 = (z1 - z0) / (s1 - s0) * 100
        g2 = (z2 - z1) / (s2 - s1) * 100
        a = g2 - g1
        kmin = kc if a < 0 else ks
        if typed_l > 0:
            asked = typed_l
        elif abs(a) < MIN_A:
            asked = 0.0
        else:
            asked = max(kmin * abs(a), lmin)
        out.append(VCurve(i, s1, z1, g1, g2, asked, asked, kmin, typed_l > 0))
    # curves must fit: half lengths within the grades on both sides, without overlapping
    st = [q[0] for q in p]
    for _ in range(3):
        for j, c in enumerate(out):
            i = j + 1
            room_l = st[i] - st[i - 1] - (out[j - 1].length / 2 if j > 0 else 0.0)
            room_r = st[i + 1] - st[i] - (out[j + 1].length / 2 if j + 1 < len(out) else 0.0)
            c.length = max(0.0, min(c.length, 2 * room_l, 2 * room_r))
    # fair split where two curves compete for one grade
    for j in range(len(out) - 1):
        a, b = out[j], out[j + 1]
        gap = b.pvi_s - a.pvi_s
        if a.length / 2 + b.length / 2 > gap + 1e-9:
            f = gap / (a.length / 2 + b.length / 2)
            a.length *= f
            b.length *= f
    return out


def heights(d: np.ndarray, pvis: list, curves: list[VCurve]) -> np.ndarray:
    """Road height on the stations d: straight grades between the PVIs with the parabolic curves."""
    p = sorted((float(r[0]), float(r[1])) for r in pvis)
    ps, pz = np.array([a for a, _ in p]), np.array([b for _, b in p])
    d = np.asarray(d, float)
    z = np.interp(d, ps, pz)
    # grades extend past the first / last PVI if the line is longer
    if len(ps) >= 2:
        g0 = (pz[1] - pz[0]) / (ps[1] - ps[0])
        gn = (pz[-1] - pz[-2]) / (ps[-1] - ps[-2])
        z = np.where(d < ps[0], pz[0] + g0 * (d - ps[0]), z)
        z = np.where(d > ps[-1], pz[-1] + gn * (d - ps[-1]), z)
    for c in curves:
        if c.length <= 0:
            continue
        x = d - c.pvc_s
        on = (x >= 0) & (x <= c.length)
        z[on] = c.pvc_z + c.g1 / 100 * x[on] + (c.g2 - c.g1) / 100 * x[on] ** 2 / (2 * c.length)
    return z


def warnings(curves: list[VCurve], speed: int) -> list[tuple[str, str]]:
    out = []
    for c in curves:
        if c.reduced:
            out.append((f"Vertical curve {c.n} shortened", f"At {c.pvi_s:.0f} m there was room for only "
                        f"{c.length:.0f} m of curve (wanted {c.length_asked:.0f} m): the grade change points next to it "
                        "are too close. Move them further apart, or accept it; the report marks this curve."))
        if c.short:
            out.append((f"Vertical curve {c.n} too short for {speed} km/h",
                        f"{c.kind.title()} curve K = {c.k:.1f}; {speed} km/h needs K at least {c.k_min:g} "
                        f"({'stopping sight distance over the crest' if c.kind == 'crest' else 'headlight reach in the dip'}"
                        f", AASHTO 2018). Make it longer or lower the speed limit there."))
    return out


ROW_HEADER = ("Curve", "Type", "PVI station", "PVI elevation (m)", "Grade in g1 (%)", "Grade out g2 (%)", "A (%)",
              "Length L (m)", "K (m/%)", "K needed", "PVC station", "PVC elevation (m)", "PVT station",
              "PVT elevation (m)", "Middle offset e (m)", "High / low point", "Note")


def note(c: VCurve) -> str:
    """Why a curve is not as long as asked / needed (shown in the report)."""
    parts = []
    if c.reduced:
        parts.append(f"SHORTENED to fit (wanted {c.length_asked:.0f} m)")
    if c.short:
        parts.append(f"K below {c.k_min:g} needed")
    return "; ".join(parts) or "-"


def rows(curves: list[VCurve], label) -> list[tuple]:
    out = []
    for c in curves:
        tp = c.turning_point()
        hl = f"{label(tp[0])} at {tp[1]:.3f} m" if tp else "-"
        out.append((c.n, c.kind if c.length > 0 else "none (grade change too small)" if abs(c.a) < MIN_A else "none",
                    label(c.pvi_s), round(c.pvi_z, 3), round(c.g1, 3), round(c.g2, 3), round(c.a, 3),
                    round(c.length, 2), round(c.k, 2) if math.isfinite(c.k) else "-", c.k_min,
                    label(c.pvc_s), round(c.pvc_z, 3), label(c.pvt_s), round(c.pvt_z, 3),
                    round(c.middle_offset, 3), hl, note(c)))
    return out
