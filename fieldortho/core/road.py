"""Road / path earthworks along a centre line drawn on a drone elevation model.

Stations every 5-20 m along the line (plus the bends and the end). At each station a cross-section is
built from the user's road shape (carriageway with crown, shoulders, optional ditches in cut, pavement
thickness) and sloped sides that meet the ground ("daylight"). Cut and fill areas per station give the
volumes between stations by the average end-area method; a running balance gives the mass haul.

Offsets in cross-sections are measured to the right of the centre line looking toward higher stations.
Bends can be rounded into circular curves (core/alignment.py).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

from . import alignment as alm, earthworks as ew, measure as ms
from .terrain import Dem

MAX_SIDE = 60.0           # metres beyond the shoulder that a side slope is followed
PROFILE_MODES = ("follow", "gentle", "level", "straight", "targets")
VERY_TIGHT = 15.0         # m: below this radius a curve is flagged as very tight (no standard chosen)

# Road standards the user can choose. Values from DPWH Department Order No. 112, s. 2019 ("Revised Design
# Standards for Tourism and Farm to Market Roads") as amended by D.O. No. 15, s. 2020 (pavement width of
# farm-to-market roads with 200 or more vehicles a day). The order gives the concrete (PCCP) thickness only;
# base course is not specified there, so it is not included.
DPWH_SOURCE = ("DPWH D.O. No. 112, s. 2019 (Revised Design Standards for Tourism and Farm to Market Roads), as "
               "amended by D.O. No. 15, s. 2020")
STANDARDS = {
    "fmr_low": dict(label="DPWH farm-to-market road, under 200 vehicles a day",
                    width=5.0, crown_pct=1.5, shoulders=True, shoulder_w=1.5, shoulder_pct=3.0, pavement_t=0.20,
                    cut_ratio=1.0, fill_ratio=1.5, max_grade=12.0, min_radius=30.0, speed="30 km/h"),
    "fmr": dict(label="DPWH farm-to-market road, 200 or more vehicles a day",
                width=6.1, crown_pct=1.5, shoulders=True, shoulder_w=1.5, shoulder_pct=3.0, pavement_t=0.20,
                cut_ratio=1.0, fill_ratio=1.5, max_grade=12.0, min_radius=30.0, speed="30 km/h"),
    "tourism": dict(label="DPWH tourism road",
                    width=6.1, crown_pct=1.5, shoulders=True, shoulder_w=1.5, shoulder_pct=3.0, pavement_t=0.23,
                    cut_ratio=1.0, fill_ratio=1.5, max_grade=12.0, min_radius=50.0,
                    speed="60 / 40 / 30 km/h (flat / rolling / mountainous)"),
}
STANDARD_NOTES = (
    "Pavement width, concrete thickness (minimum), 1.5 m gravel shoulders, 1.50% cross slope on concrete, 3.0% on "
    "gravel shoulders, minimum curve radius, maximum grade 12% and minimum 0.50% on cut sections, fill slopes no "
    "steeper than 1.5 : 1 and cut slopes of 1.5 : 1 to 1 : 1 for common materials (0.5 : 1 to 1 : 1 rippable rock, "
    "0.25 : 1 to 0.5 : 1 hard rock), 30 m minimum straight between reverse curves.")


def station_label(s: float) -> str:
    k = int(s // 1000)
    m = s - 1000 * k
    if abs(m - round(m)) < 0.005:
        k, m = (k + 1, 0) if round(m) == 1000 else (k, round(m))
        return f"{k}+{int(m):03d}"
    return f"{k}+{m:06.2f}"


@dataclass
class RoadDesign:
    interval: float = 10.0
    start_mode: str = "ground"          # ground | typed
    start_z: float = float("nan")
    profile: str = "gentle"             # follow | gentle | level | straight | targets
    max_grade: float = 8.0              # %, for "gentle"
    end_mode: str = "ground"            # straight: ground | typed
    end_z: float = float("nan")
    targets: list = field(default_factory=list)      # [[station m, height m], ...]
    width: float = 6.0                  # carriageway (the part vehicles drive on), whole width
    crown_pct: float = 2.0              # cross slope from the centre to each edge
    shoulders: bool = True
    shoulder_w: float = 1.5             # each side
    shoulder_pct: float = 4.0
    ditch: bool = False                 # in cut only, both sides
    ditch_w: float = 0.5                # bottom width
    ditch_d: float = 0.5                # depth below the shoulder edge
    pavement_t: float = 0.0             # pavement + base: earthworks stop this far below the road surface
    soil: str = "firm"
    cut_ratio: float = 1.0
    fill_ratio: float = 1.5
    swell_pct: float = ms.DEFAULT_SWELL
    shrink_pct: float = ew.DEFAULT_SHRINK
    truck_m3: float = 0.0
    radii: list = field(default_factory=list)        # curve radius at each bend (0 = sharp bend)
    standard: str = ""                               # "" (own values) or a key of STANDARDS
    min_radius: float = 0.0                          # for the "too tight" warning (0 = none)

    @property
    def half(self) -> float:
        return self.width / 2 + (self.shoulder_w if self.shoulders else 0.0)


@dataclass
class Station:
    s: float
    x: float
    y: float
    tx: float                     # unit direction of the road here
    ty: float
    kind: str = ""                # "" | "bend" | "end"
    ground: float = float("nan")  # at the centre line
    design: float = float("nan")  # road surface at the centre line
    cut_area: float = 0.0
    fill_area: float = 0.0
    cut_vol: float = 0.0          # from the previous station to this one
    fill_vol: float = 0.0
    cum_cut: float = 0.0
    cum_fill: float = 0.0
    mass: float = 0.0             # running balance: cut - fill x (1 + shrink)
    kind_note: str = ""
    left_catch: float = 0.0       # where the sides meet the ground (offset, m)
    right_catch: float = 0.0
    side_limit: bool = False      # a side slope did not meet the ground within MAX_SIDE
    offsets: np.ndarray | None = field(default=None, repr=False)
    ground_xs: np.ndarray | None = field(default=None, repr=False)
    design_xs: np.ndarray | None = field(default=None, repr=False)     # NaN outside the works

    @property
    def label(self) -> str:
        return station_label(self.s)

    @property
    def depth(self) -> float:      # + dig, - fill at the centre line
        return self.ground - self.design

    def catch_xy(self, side: int) -> tuple[float, float]:
        o = self.right_catch if side > 0 else self.left_catch
        return self.x + self.ty * o, self.y - self.tx * o


@dataclass
class RoadResult:
    stations: list
    length: float
    prof_d: np.ndarray = field(repr=False)        # dense ground profile
    prof_g: np.ndarray = field(repr=False)
    prof_z: np.ndarray = field(repr=False)        # road surface on the dense profile
    cut: float = 0.0
    fill: float = 0.0
    haul: ew.Haul | None = None
    inputs: list = field(default_factory=list)
    steepest: float = 0.0                          # % (road surface)
    steepest_at: float = 0.0
    deepest_cut: float = 0.0
    highest_fill: float = 0.0
    side_limit: int = 0                            # number of sections where a side did not reach the ground
    no_data: int = 0                               # stations without ground height
    crs_wkt: str = ""
    cell: float = 0.0
    alignment: alm.Alignment | None = field(default=None, repr=False)
    centre: np.ndarray | None = field(default=None, repr=False)     # centre line points (curves included)
    warnings: list = field(default_factory=list)                     # (title, text)
    standard: str = ""

    @property
    def net(self) -> float:
        return self.cut - self.fill

    @property
    def total(self) -> float:
        return self.cut + self.fill

    @property
    def no_data_pct(self) -> float:
        return 100.0 * self.no_data / max(1, len(self.stations))

    def headline(self) -> str:
        st = self.stations
        return (f"For the {self.length:,.0f} m road ({st[0].label} to {st[-1].label}, {len(st)} stations), dig about "
                f"{self.cut:,.0f} m³ and fill about {self.fill:,.0f} m³. Steepest part {self.steepest:.1f}% near "
                f"{station_label(self.steepest_at)}.")


# ============================================================================ stations
def make_stations(pts, interval: float, radii=None, al: alm.Alignment | None = None) -> list[Station]:
    if interval <= 0:
        raise ValueError("The station spacing must be more than 0.")
    if al is None and radii and any(r > 0 for r in radii):
        al = alm.build(pts, radii)
    if al is not None and al.curves:
        return _stations_on(al, interval)
    p = np.asarray(pts, float)
    if len(p) < 2:
        raise ValueError("Click at least two points along the road.")
    seg = np.diff(p, axis=0)
    lens = np.hypot(seg[:, 0], seg[:, 1])
    keep = lens > 1e-6
    if not keep.all():
        p = np.vstack([p[:1], p[1:][keep]])
        seg, lens = np.diff(p, axis=0), lens[keep]
    if len(p) < 2 or lens.sum() <= 0:
        raise ValueError("The road line has no length.")
    if interval <= 0:
        raise ValueError("The station spacing must be more than 0.")
    cum = np.concatenate([[0.0], np.cumsum(lens)])
    L = float(cum[-1])
    units = seg / lens[:, None]
    marks = {round(s, 6): "" for s in np.arange(0, L, interval)}
    for s in cum[1:-1]:
        if all(abs(s - m) > 0.01 for m in marks):
            marks[round(float(s), 6)] = "bend"
        else:
            marks[min(marks, key=lambda m: abs(m - s))] = "bend"
    marks[round(L, 6)] = "end"
    out = []
    for s in sorted(marks):
        i = min(int(np.searchsorted(cum, s, side="right") - 1), len(units) - 1)
        x, y = p[i] + units[i] * (s - cum[i])
        u = units[i]
        if marks[s] == "bend" and 0 < i < len(units) and abs(s - cum[i]) < 0.011:
            b = units[i - 1] + units[i]                 # bisector at the bend
            n = math.hypot(*b)
            u = b / n if n > 1e-9 else units[i]
        out.append(Station(float(s), float(x), float(y), float(u[0]), float(u[1]), marks[s]))
    return out


def _stations_on(al: alm.Alignment, interval: float) -> list[Station]:
    L = al.length
    marks = {round(float(s), 6): "" for s in np.arange(0, L, interval)}
    for ks, kind in al.key_stations().items():
        near = [m for m in marks if abs(m - ks) <= 0.01]
        for m in near:
            del marks[m]
        marks[ks] = kind
    marks[round(L, 6)] = "end"
    out = []
    for s in sorted(marks):
        x, y, tx, ty = al.point_at(s)
        if marks[s] == "bend":
            _, _, ax, ay = al.point_at(s - 1e-4)
            _, _, bx, by = al.point_at(s + 1e-4)
            n = math.hypot(ax + bx, ay + by)
            if n > 1e-9:
                tx, ty = (ax + bx) / n, (ay + by) / n
        out.append(Station(float(s), float(x), float(y), float(tx), float(ty), marks[s]))
    return out


# ============================================================================ road height
def _smooth(z, d, window):
    if len(z) < 3 or window <= 0:
        return z.copy()
    step = d[1] - d[0] if len(d) > 1 else 1.0
    k = max(1, int(round(window / step)))
    if k < 2:
        return z.copy()
    pad = np.pad(z, k, mode="edge")
    c = np.convolve(pad, np.ones(2 * k + 1) / (2 * k + 1), mode="same")
    return c[k:-k]


def _limit_grades(z, d, g, fix_start=True):
    z = z.copy()
    for _ in range(4):
        for i in range(1, len(z)):
            dd = d[i] - d[i - 1]
            z[i] = min(max(z[i], z[i - 1] - g * dd), z[i - 1] + g * dd)
        for i in range(len(z) - 2, -1, -1):
            if i == 0 and fix_start:
                break
            dd = d[i + 1] - d[i]
            z[i] = min(max(z[i], z[i + 1] - g * dd), z[i + 1] + g * dd)
    return z


def road_heights(d: np.ndarray, ground: np.ndarray, rd: RoadDesign) -> np.ndarray:
    """Road surface height at the centre line on the distances d (ground: heights there)."""
    g = ground.copy()
    ok = np.isfinite(g)
    if not ok.any():
        raise ValueError("There is no elevation data along the road line.")
    if not ok.all():
        g[~ok] = np.interp(d[~ok], d[ok], g[ok])
    start = g[0] if rd.start_mode == "ground" else float(rd.start_z)
    if not math.isfinite(start):
        raise ValueError("Type the starting height.")
    mode = rd.profile
    if mode == "level":
        return np.full_like(d, start)
    if mode == "straight":
        end = g[-1] if rd.end_mode == "ground" else float(rd.end_z)
        if not math.isfinite(end):
            raise ValueError("Type the height at the end.")
        return start + (end - start) * d / max(d[-1], 1e-9)
    if mode == "targets":
        t = sorted((float(a), float(b)) for a, b in rd.targets if math.isfinite(float(b)))
        if not t:
            raise ValueError("Type at least one target height.")
        if t[0][0] > 1e-6:
            t = [(0.0, start)] + t
        return np.interp(d, [a for a, _ in t], [b for _, b in t])
    target = _smooth(g, d, max(20.0, 2 * rd.interval))
    target[0] = start
    grade = (rd.max_grade if mode == "gentle" else 25.0) / 100
    if grade <= 0:
        raise ValueError("The steepest slope must be more than 0%.")
    return _limit_grades(target, d, grade)


# ============================================================================ cross-sections
def template(u: np.ndarray, zc: float, rd: RoadDesign, earthwork: bool) -> np.ndarray:
    """Road surface (or, earthwork=True, the bottom of the pavement) across one side, u = 0..hinge."""
    w2 = rd.width / 2
    z = zc - rd.crown_pct / 100 * np.minimum(u, w2)
    if rd.shoulders:
        z = z - rd.shoulder_pct / 100 * np.clip(u - w2, 0, rd.shoulder_w)
    if earthwork and rd.pavement_t > 0:
        z = z - rd.pavement_t
    return z


def side_profile(u: np.ndarray, g: np.ndarray, zc: float, rd: RoadDesign):
    """Design across one side (u outward from the centre, ground g there). Returns (design, catch u, limited)."""
    uh = rd.half
    z_h = template(np.array([uh]), zc, rd, False)[0]
    D = np.full(u.shape, np.nan)
    inside = u <= uh + 1e-9
    D[inside] = template(u[inside], zc, rd, True)
    out = ~inside
    gi = np.interp(uh, u, g) if np.isfinite(g).any() else np.nan
    if not np.isfinite(gi):
        return D, uh, False
    cut = gi > z_h
    if cut:
        if rd.ditch and rd.ditch_d > 0:
            u1 = uh + rd.ditch_d                       # inner ditch side 1 : 1
            u2 = u1 + rd.ditch_w
            zb = z_h - rd.ditch_d
            line = np.where(u <= u1, z_h - (u - uh), np.where(u <= u2, zb, zb + (u - u2) / rd.cut_ratio))
            start = u2
        else:
            line = z_h + (u - uh) / rd.cut_ratio
            start = uh
        hit = out & (u >= start) & (line >= g)
    else:
        line = z_h - (u - uh) / rd.fill_ratio
        hit = out & (line <= g)
    if hit.any():
        k = int(np.argmax(hit))
        # exact crossing between samples k-1 and k
        if k > 0 and np.isfinite(g[k - 1]):
            a0, a1 = line[k - 1] - g[k - 1], line[k] - g[k]
            f = a0 / (a0 - a1) if a0 != a1 else 1.0
            catch = u[k - 1] + f * (u[k] - u[k - 1])
        else:
            catch = u[k]
        limited = False
    else:
        catch, limited = float(u[-1]), True
    sel = out & (u <= catch + 1e-9)
    D[sel] = line[sel]
    return D, float(catch), limited


def _areas(u, g, D, catch, step_at=None):
    """Cut / fill areas between ground and design from u=0 to the catch point (trapezoid rule).
    step_at = (u, design just outside): a vertical step in the design there (edge of the pavement)."""
    ok = np.isfinite(g) & np.isfinite(D) & (u <= catch + 1e-9)
    if ok.sum() < 2:
        return 0.0, 0.0
    uu, diff = u[ok], (g - D)[ok]
    if step_at is not None:
        i = np.flatnonzero(np.abs(uu - step_at[0]) < 1e-9)
        if i.size:
            k = int(i[0])
            uu = np.insert(uu, k + 1, uu[k])
            diff = np.insert(diff, k + 1, g[ok][k] - step_at[1])
    if catch > uu[-1] + 1e-9:          # the side meets the ground between samples: close the last strip there
        uu = np.append(uu, catch)
        diff = np.append(diff, 0.0)
    du = np.diff(uu)
    c = np.clip(diff, 0, None)
    f = np.clip(-diff, 0, None)
    return float(np.sum((c[1:] + c[:-1]) / 2 * du)), float(np.sum((f[1:] + f[:-1]) / 2 * du))


def section(dem: Dem, st: Station, rd: RoadDesign, step: float):
    reach = rd.half + MAX_SIDE
    u = np.union1d(np.arange(0, reach + step / 2, step), [rd.half])      # always a sample at the shoulder edge
    z_edge = template(np.array([rd.half]), st.design, rd, False)[0]
    step_at = (rd.half, z_edge) if rd.pavement_t > 0 else None
    rx, ry = st.ty, -st.tx                      # right-hand normal
    res = {}
    for side, (nx, ny) in ((1, (rx, ry)), (-1, (-rx, -ry))):
        g = ms.sample(dem, st.x + nx * u, st.y + ny * u)
        D, catch, lim = side_profile(u, g, st.design, rd)
        ca, fa = _areas(u, g, D, catch, step_at)
        res[side] = (g, D, catch, lim, ca, fa)
    gl, Dl, cl, ll, cal, fal = res[-1]
    gr, Dr, cr, lr, car, far = res[1]
    st.cut_area, st.fill_area = cal + car, fal + far
    st.left_catch, st.right_catch = -cl, cr
    st.side_limit = ll or lr
    # one array across the whole section, left to right (for drawing and the tables)
    w = max(cl, cr) + max(3.0, 0.25 * max(cl, cr))
    keep = u <= w
    st.offsets = np.concatenate([-u[keep][:0:-1], u[keep]])
    st.ground_xs = np.concatenate([gl[keep][:0:-1], gr[keep]])
    Dl2 = np.where(u <= cl + 1e-9, Dl, np.nan)
    Dr2 = np.where(u <= cr + 1e-9, Dr, np.nan)
    st.design_xs = np.concatenate([Dl2[keep][:0:-1], Dr2[keep]])


# ============================================================================ the whole road
def design_road(path: str, pts, rd: RoadDesign, max_cells: int = ms.MAX_CELLS) -> RoadResult:
    from shapely.geometry import LineString
    if rd.cut_ratio <= 0 or rd.fill_ratio <= 0:
        raise ValueError("Side slopes must be more than 0.")
    if rd.width <= 0:
        raise ValueError("The road width must be more than 0.")
    al = alm.build(pts, rd.radii)
    stations = make_stations(pts, rd.interval, al=al)
    cxy, _ = al.densify(1.0)
    line = LineString(cxy)
    dem = ms.read_area(path, line.buffer(rd.half + MAX_SIDE).bounds, max_cells)
    step = max(dem.cell, 0.2)
    # dense ground profile for the road height and the side view
    dxy, dd = al.densify(max(step, 0.5))
    dg = ms.sample(dem, dxy[:, 0], dxy[:, 1])
    dz = road_heights(dd, dg, rd)
    sd = np.array([s.s for s in stations])
    sx, sy = np.array([s.x for s in stations]), np.array([s.y for s in stations])
    sg = ms.sample(dem, sx, sy)
    sz = np.interp(sd, dd, dz)
    for st, g, z in zip(stations, sg, sz):
        st.ground, st.design = float(g), float(z)
        section(dem, st, rd, step)
    k = 1 + rd.shrink_pct / 100
    cc = cf = 0.0
    for i, st in enumerate(stations):
        if i:
            a, L = stations[i - 1], st.s - stations[i - 1].s
            st.cut_vol = (a.cut_area + st.cut_area) / 2 * L
            st.fill_vol = (a.fill_area + st.fill_area) / 2 * L
        cc += st.cut_vol
        cf += st.fill_vol
        st.cum_cut, st.cum_fill, st.mass = cc, cf, cc - cf * k
    grades = np.diff(sz) / np.maximum(np.diff(sd), 1e-9) * 100
    gi = int(np.argmax(np.abs(grades))) if len(grades) else 0
    depth = sg - sz
    soil = ew.SOILS.get(rd.soil, ew.SOILS["custom"])[0]
    prof = {"follow": "follow the ground (smoothed)", "gentle": f"follow the ground, no steeper than {rd.max_grade:g}%",
            "level": "level", "straight": "one straight slope from start to end",
            "targets": "through the heights typed: " + ", ".join(f"{station_label(a)} = {b:.2f} m"
                                                                  for a, b in rd.targets)}[rd.profile]
    shape = f"{rd.width:g} m wide, crown {rd.crown_pct:g}%"
    if rd.shoulders:
        shape += f"; shoulders {rd.shoulder_w:g} m at {rd.shoulder_pct:g}% each side"
    if rd.ditch:
        shape += f"; ditches in cut {rd.ditch_w:g} m wide, {rd.ditch_d:g} m deep"
    curves = ", ".join(f"R {c.radius:.0f} m" for c in al.curves) if al.curves else "none (sharp bends)"
    inputs = [("Heights from", ""),
              ("Road standard", STANDARDS[rd.standard]["label"] + f" ({DPWH_SOURCE})" if rd.standard in STANDARDS
               else "my own values"),
              ("Stations every", f"{rd.interval:g} m (plus bends, curve starts / ends and the end)"),
              ("Curves", curves),
              ("Starting height", (f"ground at the start ({stations[0].design:.2f} m)" if rd.start_mode == "ground"
                                   else f"{rd.start_z:.2f} m (typed)")),
              ("How the road rises and falls", prof), ("Road shape", shape),
              ("Pavement and base thickness", f"{rd.pavement_t:g} m (earthworks stop below it)"),
              ("Side slopes", f"{soil}: cut {rd.cut_ratio:g} : 1, fill {rd.fill_ratio:g} : 1 (out : up)"),
              ("Soil loosening when dug (swell)", f"{rd.swell_pct:g}%"),
              ("Extra soil for packed fill (shrink)", f"{rd.shrink_pct:g}%")]
    warn = road_warnings(al, rd, stations, grades, sd)
    return RoadResult(stations, float(sd[-1]), dd, dg, dz, cc, cf,
                      ew.make_haul(cc, cf, rd.swell_pct, rd.shrink_pct, rd.truck_m3), inputs,
                      float(abs(grades[gi])) if len(grades) else 0.0, float(sd[gi]) if len(grades) else 0.0,
                      float(np.nanmax(depth)) if np.isfinite(depth).any() else 0.0,
                      float(-np.nanmin(depth)) if np.isfinite(depth).any() else 0.0,
                      sum(s.side_limit for s in stations), int((~np.isfinite(sg)).sum()), dem.crs_wkt, dem.cell,
                      al, cxy, warn, rd.standard)


def road_warnings(al, rd: RoadDesign, stations, grades, sd) -> list[tuple[str, str]]:
    out = []
    std = STANDARDS.get(rd.standard)
    rmin = rd.min_radius or (std["min_radius"] if std else 0.0)
    for k, c in enumerate(al.curves, 1):
        if c.reduced:
            out.append((f"Curve {k} made smaller", f"The bend at point {c.vertex + 1} had room for a radius of only "
                        f"{c.radius:.0f} m (asked {c.radius_asked:.0f} m): the straights next to it are too short."))
        if rmin and c.radius < rmin - 1e-6:
            out.append((f"Curve {k} is too tight", f"Radius {c.radius:.0f} m is less than the minimum {rmin:g} m"
                        + (" of the chosen DPWH standard." if std else ".")))
        elif not rmin and c.radius < VERY_TIGHT:
            out.append((f"Curve {k} is very tight", f"Radius {c.radius:.0f} m: long trucks may not get around it."))
    if std:
        for a, b, L, rev in alm.straights_between(al):
            if rev and L < 30:
                out.append(("Reverse curves too close", f"Only {L:.0f} m of straight road between curves that turn "
                            "opposite ways (DPWH minimum 30 m)."))
        if len(grades) and np.max(np.abs(grades)) > std["max_grade"] + 1e-6:
            out.append(("Steeper than the standard", f"The road reaches {np.max(np.abs(grades)):.1f}%; the DPWH "
                        f"maximum is {std['max_grade']:g}%."))
        if rd.fill_ratio < 1.5 - 1e-9:
            out.append(("Fill sides too steep", f"{rd.fill_ratio:g} : 1 is steeper than the DPWH maximum fill slope "
                        "of 1.5 : 1."))
        flat_cut = 0
        for i, st_ in enumerate(stations[:-1]):
            g = abs(grades[i]) if i < len(grades) else 99
            if st_.depth == st_.depth and st_.depth > 0 and g < 0.5:
                flat_cut += 1
        if flat_cut:
            out.append(("Too flat in cut", f"{flat_cut} stretch(es) of the road are dug in and flatter than 0.50%: "
                        "water may not drain (DPWH minimum on cut sections)."))
    return out


def curve_rows(al) -> list[tuple]:
    """Curve table: (no., PI easting, PI northing, deflection deg, radius, tangent, length, external, mid-ordinate,
    PC station, PT station, turns)."""
    rows = []
    for k, c in enumerate(al.curves, 1):
        ext = c.radius * (1 / math.cos(math.radians(c.delta) / 2) - 1)
        rows.append((k, round(c.pi[0], 3), round(c.pi[1], 3), round(c.delta, 4), round(c.radius, 3),
                     round(c.tangent, 3), round(c.length, 3), round(ext, 3), round(c.mid_ordinate, 3),
                     station_label(c.pc_s), station_label(c.pt_s), "left" if c.left else "right"))
    return rows


CURVE_HEADER = ("Curve", "PI easting (m)", "PI northing (m)", "Deflection (°)", "Radius (m)", "Tangent T (m)",
                "Curve length (m)", "External E (m)", "Middle ordinate M (m)", "Curve start (PC)",
                "Curve end (PT)", "Turns")


def design_dict(rd: RoadDesign) -> dict:
    return asdict(rd)


def design_from(plan: dict) -> RoadDesign:
    raw = plan.get("road") or {}
    return RoadDesign(**{k: v for k, v in raw.items() if k in RoadDesign.__dataclass_fields__})
