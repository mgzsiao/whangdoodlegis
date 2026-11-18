"""Superelevation (e) and traveled-way widening (w) on horizontal curves, AASHTO Green Book 2018.

Superelevation, Method 5 (rural roads): side friction f and superelevation e share the work along a
parabola so that e reaches e_max exactly at the smallest radius:
    1/R_min = 127 / V² x (e_max + f_max)              (V design speed km/h, f_max Table 3-7)
    1/R_PI  = 127 / V_R² x e_max                       (V_R average running speed)
    h_PI = e_max (V² / V_R² - 1),  tan a1 = h_PI R_PI,  tan a2 = (f_max - h_PI) / (1/R_min - 1/R_PI)
    M_O  = (1/R_PI)(1/R_min - 1/R_PI)(tan a2 - tan a1) / (2 / R_min)
    f(1/R) = M_O (R_PI/R)² + tan a1 / R                                  for 1/R <= 1/R_PI
           = M_O ((1/R_min - 1/R)/(1/R_min - 1/R_PI))² + h_PI + tan a2 (1/R - 1/R_PI)   otherwise
    e = V² / (127 R) - f, never below the normal crown (NC) and never above e_max.

Transitions (rotation about the centre line): superelevation runoff L_r = w n1 e_d b_w / Δ, with w the lane
width, n1 = 1 lane rotated each side, b_w = 1.0 and Δ the maximum relative gradient by speed (Table 3-15);
tangent runout L_t = e_NC / e_d x L_r. Two thirds of the runoff lie on the tangent and one third on the
curve. Over the runout the outer half goes from normal crown to level; over the runoff it goes on to -e_NC
(reverse crown) and then, together with the inner half, to the full e.

Widening (traveled way on curves, Green Book 3.3.10): W_C = N (U + C) + (N - 1) F_A + Z with
U = u + R - sqrt(R² - L²), F_A = sqrt(R² + A (2L + A)) - R, Z = 0.1 V / sqrt(R); widening = W_C - W_N.
Design vehicle SU (single-unit truck): wheelbase L 6.1 m, front overhang A 1.2 m, track width u 2.44 m.
C is the clearance the truck has on the straight: lane width - u. Applied on the inside of the curve and
attained over the same length as the superelevation runoff.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

SOURCE = ("AASHTO, A Policy on Geometric Design of Highways and Streets (Green Book), 7th ed. 2018: Table 3-7 "
          "(side friction), superelevation distribution Method 5, Table 3-15 (relative gradients), traveled-way "
          "widening on curves (3.3.10)")
FMAX = {20: 0.35, 30: 0.28, 40: 0.23, 50: 0.19, 60: 0.17, 70: 0.15, 80: 0.14}
V_RUN = {20: 20, 30: 30, 40: 40, 50: 47, 60: 55, 70: 63, 80: 70}
REL_GRADIENT = {20: 0.80, 30: 0.75, 40: 0.70, 50: 0.65, 60: 0.60, 70: 0.55, 80: 0.50}    # % (Table 3-15)
SU = dict(name="SU (single-unit truck, 6-wheeler)", wheelbase=6.1, overhang=1.2, track=2.44)
MIN_WIDENING = 0.6     # m: AASHTO lets smaller widening be left out; the app still applies and reports it


def _row(table: dict, speed: int):
    for v in sorted(table):
        if speed <= v:
            return table[v]
    return table[max(table)]


def r_min(speed: int, emax_pct: float) -> float:
    """Smallest radius for the speed and e_max (V² / 127 (e_max + f_max))."""
    return speed * speed / (127 * (emax_pct / 100 + _row(FMAX, speed)))


def e_needed(radius: float, speed: int, emax_pct: float) -> float:
    """Superelevation (fraction, Method 5) before the normal-crown floor; NaN below the smallest radius."""
    V, VR, f, em = speed, _row(V_RUN, speed), _row(FMAX, speed), emax_pct / 100
    if radius <= 0:
        return float("nan")
    iR, iRmin, iRpi = 1 / radius, (em + f) * 127 / (V * V), em * 127 / (VR * VR)
    if iR > iRmin + 1e-12:
        return float("nan")
    hpi = em * V * V / (VR * VR) - em
    t1 = hpi / iRpi
    t2 = (f - hpi) / (iRmin - iRpi)
    mo = iRpi * (iRmin - iRpi) * (t2 - t1) / (2 * iRmin)
    if iR <= iRpi:
        fr = mo * (iR / iRpi) ** 2 + iR * t1
    else:
        fr = mo * ((iRmin - iR) / (iRmin - iRpi)) ** 2 + hpi + (iR - iRpi) * t2
    return V * V / (127 * radius) - fr


def design_e(radius: float, speed: int, emax_pct: float, crown_pct: float) -> float:
    """Superelevation to build (%): Method 5, at least the normal crown, at most e_max (also below R_min)."""
    e = e_needed(radius, speed, emax_pct)
    if not math.isfinite(e):
        return emax_pct
    e = math.ceil(e * 1000 - 1e-9) / 10                  # rounded up to 0.1 %
    return min(emax_pct, max(crown_pct, e))


def widening(radius: float, speed: int, width: float, lanes: int = 2, vehicle: dict = SU) -> float:
    """Traveled-way widening (m) on a curve of this radius for the design vehicle."""
    if radius <= 0:
        return 0.0
    L, A, u0 = vehicle["wheelbase"], vehicle["overhang"], vehicle["track"]
    if radius <= L:
        radius = L + 0.01
    c = max(0.0, width / lanes - u0)
    U = u0 + radius - math.sqrt(radius * radius - L * L)
    fa = math.sqrt(radius * radius + A * (2 * L + A)) - radius
    z = 0.1 * speed / math.sqrt(radius)
    return max(0.0, lanes * (U + c) + (lanes - 1) * fa + z - width)


def runoff(e_pct: float, lane_w: float, speed: int) -> float:
    """Superelevation runoff length (m): one lane rotated about the centre line."""
    return lane_w * 1 * e_pct / _row(REL_GRADIENT, speed)


@dataclass
class CurveSuper:
    """Superelevation and widening of one horizontal curve."""
    radius: float
    e: float              # % design superelevation
    widen: float          # m, on the inside
    runoff: float         # m
    runout: float         # m
    pc_s: float           # curve start / end stations
    pt_s: float
    left: bool            # the curve turns left (the inside is on the left)
    nc: bool              # the bend is gentle enough to keep the normal crown

    def at(self, s: float, crown: float) -> tuple[float, float, float]:
        """(slope of the left half, slope of the right half, widening) at station s. Slopes in % falling
        outward from the centre line (normal crown = +crown on both sides; negative = rising outward)."""
        if self.nc:
            frac_w = self._fraction(s)
            return crown, crown, self.widen * frac_w
        e, lr, lt = self.e, self.runoff, self.runout
        # distance into the superelevated part, measured from where the full e starts (1/3 of runoff on curve)
        start_full, end_full = self.pc_s + lr / 3, self.pt_s - lr / 3
        if start_full > end_full:                        # short curve: full e only at the middle
            start_full = end_full = (self.pc_s + self.pt_s) / 2
        if s <= start_full:
            x = start_full - s                           # metres before full superelevation
        elif s >= end_full:
            x = s - end_full
        else:
            x = 0.0
        # outer slope: -e (full) .. -crown at x = lr * (e - crown) / e .. 0 at x = lr .. +crown at x = lr + lt
        if x >= lr + lt:
            outer, inner = crown, crown
        elif x >= lr:
            outer, inner = crown * (x - lr) / max(lt, 1e-9), crown
        else:
            outer = -e * (1 - x / lr)                    # 0 at x = lr, -e at x = 0
            inner = max(crown, -outer) if -outer >= crown else crown
        frac_w = max(0.0, min(1.0, 1 - x / max(lr, 1e-9))) if x < lr else 0.0
        w = self.widen * frac_w
        return (inner, outer, w) if self.left else (outer, inner, w)

    def _fraction(self, s: float) -> float:
        lr = max(self.runoff, 10.0)
        if self.pc_s <= s <= self.pt_s:
            return 1.0
        x = self.pc_s - s if s < self.pc_s else s - self.pt_s
        return max(0.0, 1 - x / lr)


def for_curves(curves, speed: int, emax_pct: float, crown_pct: float, width: float,
               apply_e: bool = True, apply_w: bool = True) -> list[CurveSuper]:
    out = []
    lane = width / 2
    for c in curves:
        e = design_e(c.radius, speed, emax_pct, crown_pct) if apply_e else crown_pct
        nc = (not apply_e) or e <= crown_pct + 1e-9
        lr = runoff(e, lane, speed) if not nc else runoff(crown_pct, lane, speed)
        lt = crown_pct / e * lr if not nc and e > 0 else 0.0
        w = widening(c.radius, speed, width) if apply_w else 0.0
        out.append(CurveSuper(c.radius, e if not nc else crown_pct, w, lr, lt, c.pc_s, c.pt_s, c.left, nc))
    return out


def at_station(sups: list[CurveSuper], s: float, crown: float) -> tuple[float, float, float, str]:
    """(left slope %, right slope %, widening m, side 'L' / 'R' / '') at station s: the curve whose transition
    covers s and tilts the road most wins."""
    best = (crown, crown, 0.0, "")
    for c in sups:
        sl, sr, w = c.at(s, crown)
        if (sl, sr, w) != (crown, crown, 0.0) and (abs(sl - sr) + w > abs(best[0] - best[1]) + best[2]):
            best = (sl, sr, w, "L" if c.left else "R")
    return best
