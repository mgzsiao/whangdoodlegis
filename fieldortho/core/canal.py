"""Lined drainage canal along a line drawn on the elevation model (1.2).

The canal is a trapezoid lined with concrete: bottom width b, depth d (bed to the top of the lining), side
slope z : 1 (horizontal : vertical), lining t thick. The bed (invert) falls by the same amount every metre
(the bed slope S), starting at the ground at the upstream end minus the depth, so the water always flows.
At each station the works are: the excavation for the channel and its lining; at the top of the lining a
berm (where the ground is higher) with a cut slope up to the ground, or an earth bank (where the ground is
lower than the canal top) with a fill slope down to the ground. Cut and fill areas give volumes between
stations by the average end-area method; the lining area gives the concrete volume.

Hydraulics (Manning): Q = A R^(2/3) S^(1/2) / n with A = (b + z y) y, P = b + 2 y √(1 + z²), R = A / P.
Capacity at full depth less the freeboard; water depth at the design flow by solving Manning for y.

Design flow by the Rational Method: Q = C i A / 360 (m³/s; C runoff coefficient, i mm/h, A hectares), which is
DPWH's c i A / 3.6 with A in km². Runoff coefficients: DPWH Design Guidelines, Criteria and Standards Vol. 1
(MPWH 1987), as printed in the DPWH-JICA Manual on Flood Control Planning (2003), Table 4.2; paved surfaces
from the TxDOT Hydraulic Design Manual Table 4-10; paddy from the same DPWH manual, Table 4.3. The starting
value is the upper end of each range (the safe side). Time of concentration: Kirpich (1940)
tc = 0.0195 L^0.77 S^-0.385 min, or the DPWH manual's inlet time ti = 30 √(A/2) min (A km²) plus flow time
L / V with Kraven's velocity (3.5 / 3.0 / 2.1 m/s for slopes above 1/100 / 1/100 - 1/200 / below 1/200).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

from . import alignment as alm, earthworks as ew, measure as ms, road as rdm

RUNOFF_SOURCE = ("DPWH Design Guidelines, Criteria and Standards Vol. 1 (MPWH 1987), as printed in the DPWH-JICA "
                 "Manual on Flood Control Planning (2003), Table 4.2")
PAVED_SOURCE = "TxDOT Hydraulic Design Manual, Table 4-10"
PADDY_SOURCE = "DPWH-JICA Manual on Flood Control Planning (2003), Table 4.3 (JICA Metro Manila study, 1990)"
# key: (land cover, low, high, source)
COVERS = {
    "lawn": ("Lawn, gardens, meadows and cultivated lands", 0.05, 0.25, RUNOFF_SOURCE),
    "open": ("Parks, open spaces incl. unpaved surfaces and vacant lots", 0.20, 0.30, RUNOFF_SOURCE),
    "suburb_few": ("Suburban districts with few buildings", 0.25, 0.35, RUNOFF_SOURCE),
    "res_low": ("Residential districts, not densely built", 0.30, 0.55, RUNOFF_SOURCE),
    "res_high": ("Residential districts, densely built", 0.50, 0.75, RUNOFF_SOURCE),
    "steep": ("Watershed with steep gullies, not heavily timbered", 0.55, 0.70, RUNOFF_SOURCE),
    "timber": ("Watershed with moderate slope, cultivated and heavily timbered", 0.45, 0.55, RUNOFF_SOURCE),
    "suburban": ("Suburban areas", 0.34, 0.45, RUNOFF_SOURCE),
    "agri": ("Agricultural areas", 0.15, 0.25, RUNOFF_SOURCE),
    "paddy": ("Paddy fields", 0.10, 0.10, PADDY_SOURCE),
    "concrete": ("Concrete pavement", 0.90, 0.95, PAVED_SOURCE),
    "asphalt": ("Asphalt pavement", 0.85, 0.95, PAVED_SOURCE),
    "roof": ("Roofs", 0.75, 0.95, PAVED_SOURCE),
}
# suggested starting sizes (agreed with the owner 18 November 2026; not a DPWH standard)
SUGGESTED = dict(bw=0.5, depth=0.8, z=0.5, lining_t=0.10, n=0.015, freeboard=0.20, slope_pct=0.5, berm_w=0.5,
                 cut_ratio=1.5, fill_ratio=2.0)
KRAVEN = ((1 / 100, 3.5), (1 / 200, 3.0), (0.0, 2.1))            # (slope above, velocity m/s)
V_MIN, V_MAX = 0.6, 3.0       # m/s suggested checks: silt settles below, lining wears above (concrete)


@dataclass
class CanalDesign:
    interval: float = 10.0
    bw: float = 0.5               # bottom width (m)
    depth: float = 0.8            # bed to the top of the lining (m)
    z: float = 0.5                # side slope, horizontal : 1
    lining_t: float = 0.10        # concrete lining (m)
    n: float = 0.015              # Manning roughness (float-finished concrete, Chow)
    freeboard: float = 0.20       # m
    slope_pct: float = 0.5        # bed slope (%), always downhill along the line
    start_mode: str = "ground"    # ground: top of the lining at the ground at the start | typed: invert typed
    start_invert: float = float("nan")
    berm_w: float = 0.5           # flat strip / bank top beside the canal (m)
    cut_ratio: float = 1.5        # earth sides above the canal, horizontal : 1
    fill_ratio: float = 2.0       # bank sides
    swell_pct: float = ms.DEFAULT_SWELL
    shrink_pct: float = ew.DEFAULT_SHRINK
    truck_m3: float = 0.0
    # design flow
    q_mode: str = "rain"          # rain (Rational Method) | flow (typed)
    q_design: float = 0.6         # m³/s when typed
    rain_mm: float = 18.0
    rain_min: float = 15.0
    return_period: int = 10
    area_ha: float = 0.0          # catchment (0 = measure it on the elevation model)
    flow_len: float = 0.0         # longest flow path (m)
    drop: float = 0.0             # fall along it (m)
    area_src: str = "map"         # map | own | typed
    own_dem: str = ""             # own elevation file for the catchment
    tc_method: str = "kirpich"    # kirpich | dpwh
    covers: dict = field(default_factory=dict)     # {key: [share %, C]}

    @property
    def top_half(self) -> float:
        """Half the width at the top of the lining, inside."""
        return self.bw / 2 + self.z * self.depth


# ============================================================================ hydraulics
def q_text(q: float) -> str:
    """A flow for display: 3 decimals below 1 m³/s (small canals), 2 above."""
    return f"{q:.3f}" if abs(q) < 1 else f"{q:,.2f}"


def flow(bw: float, z: float, y: float, n: float, s: float) -> tuple[float, float, float, float, float]:
    """(Q m³/s, A m², P m, R m, V m/s) for water depth y in the trapezoid (Manning)."""
    if y <= 0 or s <= 0:
        return 0.0, 0.0, bw, 0.0, 0.0
    a = (bw + z * y) * y
    p = bw + 2 * y * math.sqrt(1 + z * z)
    r = a / p
    q = a * r ** (2 / 3) * math.sqrt(s) / n
    return q, a, p, r, q / a


def normal_depth(q: float, bw: float, z: float, n: float, s: float, ymax: float = 20.0) -> float:
    """Water depth carrying q (Manning, uniform flow); NaN if more than ymax."""
    if q <= 0:
        return 0.0
    if flow(bw, z, ymax, n, s)[0] < q:
        return float("nan")
    lo, hi = 0.0, ymax
    for _ in range(80):
        m = (lo + hi) / 2
        if flow(bw, z, m, n, s)[0] < q:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


# ============================================================================ design flow
def c_mix(covers: dict) -> tuple[float, float]:
    """(weighted C, total share %) from {key: [share %, C]}."""
    tot = sum(max(0.0, float(v[0])) for v in covers.values())
    if tot <= 0:
        return 0.0, 0.0
    return sum(max(0.0, float(v[0])) * float(v[1]) for v in covers.values()) / tot, tot


def tc_kirpich(length_m: float, drop_m: float) -> float:
    """Kirpich (1940), minutes."""
    if length_m <= 0 or drop_m <= 0:
        return float("nan")
    return 0.0195 * length_m ** 0.77 * (drop_m / length_m) ** -0.385


def kraven_velocity(slope: float) -> float:
    for s_above, v in KRAVEN:
        if slope > s_above:
            return v
    return KRAVEN[-1][1]


def tc_dpwh(area_ha: float, length_m: float, drop_m: float) -> float:
    """DPWH-JICA Manual on Flood Control Planning (2003): inlet time 30 √(A/2) min (A km², at most 30 min)
    plus flow time L / V with Kraven's velocity for the slope of the flow path."""
    if area_ha <= 0 or length_m <= 0:
        return float("nan")
    a_km2 = min(area_ha / 100, 2.0)
    ti = 30 * math.sqrt(a_km2 / 2)
    v = kraven_velocity(drop_m / length_m if length_m > 0 else 0.0)
    return ti + length_m / v / 60


def tc_minutes(cd: CanalDesign) -> float:
    return tc_dpwh(cd.area_ha, cd.flow_len, cd.drop) if cd.tc_method == "dpwh" else tc_kirpich(cd.flow_len, cd.drop)


def intensity(cd: CanalDesign) -> float:
    """mm per hour from the rainfall amount and how long it fell."""
    return cd.rain_mm / (cd.rain_min / 60) if cd.rain_min > 0 else float("nan")


def rational_q(cd: CanalDesign) -> float:
    c, _tot = c_mix(cd.covers)
    return c * intensity(cd) * cd.area_ha / 360


def design_q(cd: CanalDesign) -> float:
    return rational_q(cd) if cd.q_mode == "rain" else cd.q_design


# ============================================================================ catchment on the elevation model
@dataclass
class Catchment:
    area_ha: float
    flow_len: float
    drop: float
    outlet: tuple
    mask: np.ndarray = field(repr=False)
    transform: object = field(repr=False, default=None)
    at_edge: bool = False         # the catchment reaches the edge of the map: it may be larger


def _line_cells(pts, t, H, W) -> dict[int, float]:
    """Cells under the canal line -> distance along the canal from the cell to the downstream end. Cells that
    only touch diagonally are bridged, so no D8 flow path slips between them."""
    pts = [tuple(map(float, p)) for p in pts]
    seg = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts[:-1], pts[1:])]
    total, cell = sum(seg), abs(t.a)
    out: dict[int, float] = {}
    prev = None
    s0 = 0.0

    def put(r, c, rest):
        if 0 <= r < H and 0 <= c < W:
            i = r * W + c
            out[i] = min(out.get(i, rest), rest)
    for (a, b), L in zip(zip(pts[:-1], pts[1:]), seg):
        n = max(1, int(math.ceil(L / (cell / 4))))
        for k in range(n + 1):
            f = k / n
            x, y = a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])
            r, c = int(math.floor((y - t.f) / t.e)), int(math.floor((x - t.c) / t.a))
            rest = total - (s0 + f * L)
            if prev is not None and prev[0] != r and prev[1] != c:
                put(prev[0], c, rest)
            put(r, c, rest)
            prev = (r, c)
        s0 += L
    return out


def _widen(cells: dict[int, float], H: int, W: int, k: int) -> dict[int, float]:
    """The strip k cells each side of the line; each added cell takes the distance of the nearest line cell."""
    from scipy import ndimage as ndi
    line = np.zeros((H, W), bool)
    rest = np.zeros((H, W))
    for i, d in cells.items():
        line.flat[i] = True
        rest.flat[i] = d
    dist, (ir, ic) = ndi.distance_transform_edt(~line, return_indices=True)
    strip = dist <= k + 1e-9
    near = rest[ir, ic]
    return {int(i): float(near.flat[i]) for i in np.flatnonzero(strip)}


def catchment(path: str, line_xy, half_width: float = 2.0, max_cells: int = 400_000,
              outlet_crs_wkt: str = "") -> Catchment:
    """The land draining into the canal anywhere along its line line_xy (drawn upstream to downstream), on the
    elevation model `path` (read at most about max_cells cells): its area, the longest flow path (over the land
    to the canal, then along the canal to its downstream end) and the fall along that path. half_width: the
    strip each side of the centre line that collects the water (canal top + berm; at least one cell), so flow
    running beside the line is caught. outlet_crs_wkt: the system of line_xy when it differs from the elevation
    model's (e.g. the job map vs an own IFSAR file)."""
    from . import hydro
    from .terrain import load_dem
    dem = load_dem(path, max_size=int(math.sqrt(max_cells)))
    pts = [tuple(map(float, p[:2])) for p in line_xy]
    if len(pts) == 1:
        pts = pts * 2
    if outlet_crs_wkt:
        from pyproj import CRS, Transformer
        a, b = CRS.from_user_input(outlet_crs_wkt), CRS.from_user_input(dem.crs_wkt)
        if not a.equals(b):
            tr = Transformer.from_crs(a, b, always_xy=True)
            pts = [tr.transform(*p) for p in pts]
    dr = hydro.drainage(dem)
    H, W = dem.z.shape
    t = dem.transform
    canal = _widen(_line_cells(pts, t, H, W), H, W, max(1, int(math.ceil(half_width / abs(t.a)))))
    zf = dem.z.ravel()
    canal = {i: d for i, d in canal.items() if np.isfinite(zf[i])}
    if not canal:
        raise ValueError("The canal is outside the elevation model.")
    o = min(canal, key=canal.get)                 # the downstream end
    inside = np.zeros(H * W, bool)
    dist = np.zeros(H * W)
    for i, d in canal.items():
        inside[i] = True
        dist[i] = d
    cell = abs(t.a)
    for i in dr.order.tolist():                   # downstream cells come first
        if i in canal:
            continue
        p = dr.parent[i]
        if p >= 0 and inside[p]:
            inside[i] = True
            pr, pc = divmod(p, W)
            ir, ic = divmod(i, W)
            dist[i] = dist[p] + cell * (math.sqrt(2) if (pr != ir and pc != ic) else 1.0)
    mask = inside.reshape(H, W)
    n_cells = int(mask.sum())
    far = int(np.argmax(np.where(inside, dist, -1)))
    zf = dem.z.ravel()
    drop = float(zf[far] - zf[o]) if np.isfinite(zf[far]) and np.isfinite(zf[o]) else 0.0
    from scipy import ndimage as ndi
    valid = np.isfinite(dem.z)
    border = np.zeros_like(valid)
    border[:2, :] = border[-2:, :] = border[:, :2] = border[:, -2:] = True
    border |= ndi.binary_dilation(~valid, structure=np.ones((5, 5), bool))
    at_edge = bool((mask & border).sum() > max(3, 0.002 * n_cells))
    return Catchment(n_cells * cell * cell / 10_000, float(dist[far]), max(drop, 0.0), (int(o // W), int(o % W)),
                     mask, t, at_edge)


# ============================================================================ cross-section
def _lining_area(cd: CanalDesign) -> float:
    s = math.sqrt(1 + cd.z * cd.z)
    t, b2, d = cd.lining_t, cd.bw / 2, cd.depth
    u_top = b2 + cd.z * d + t * s
    u_c = b2 + t * (s - cd.z)
    outer = (2 * u_top + 2 * u_c) / 2 * (d + t)
    inner = (2 * (b2 + cd.z * d) + 2 * b2) / 2 * d
    return outer - inner


def side_line(u: np.ndarray, g: np.ndarray, inv: float, cd: CanalDesign):
    """Bottom of the works across one side (u outward from the centre line): the outside of the lining, then a
    berm and a cut slope up to the ground, or a bank top and a fill slope down to the ground.
    Returns (design, catch u, bank: True where a bank is built)."""
    s = math.sqrt(1 + cd.z * cd.z)
    t, b2, d = cd.lining_t, cd.bw / 2, cd.depth
    top = inv + d
    u_c = b2 + t * (s - cd.z)                       # outer bottom corner of the lining
    u_t = b2 + cd.z * d + t * s                     # outer top edge of the lining
    D = np.where(u <= u_c, inv - t, inv - t + (u - u_c) * (d + t) / max(u_t - u_c, 1e-9))
    D = np.minimum(D, top)
    gt = np.interp(u_t, u, g) if np.isfinite(g).any() else np.nan
    if not np.isfinite(gt):
        return np.where(u <= u_t, D, np.nan), u_t, False
    bank = gt < top
    ub = u_t + cd.berm_w
    if bank:
        line = np.where(u <= ub, top, top - (u - ub) / cd.fill_ratio)
        hit = (u >= ub) & (line <= g)
    else:
        line = np.where(u <= ub, top, top + (u - ub) / cd.cut_ratio)
        hit = (u >= ub) & (line >= g)
    catch = rdm._crossing(u, line, g, hit)
    if catch is None:
        catch = float(u[-1])
    out = np.where(u <= u_t, D, np.where(u <= catch + 1e-9, line, np.nan))
    return out, float(catch), bool(bank)


# ============================================================================ the whole canal
@dataclass
class CanalResult(rdm.RoadResult):
    lining_vol: float = 0.0
    lining_area: float = 0.0       # m² of lining per section
    q_design: float = 0.0
    q_capacity: float = 0.0
    v_capacity: float = 0.0
    y_design: float = float("nan")  # water depth at the design flow
    v_design: float = float("nan")
    tc: float = float("nan")
    c_weighted: float = 0.0
    i_design: float = float("nan")
    bank_len: float = 0.0          # m of canal with an earth bank on at least one side
    hydraulics: list = field(default_factory=list)      # (label, value, note) rows

    def headline(self) -> str:
        st = self.stations
        ok = self.q_capacity >= self.q_design - 1e-9
        return (f"For the {self.length:,.0f} m canal ({st[0].label} to {st[-1].label}, {len(st)} stations), cut about "
                f"{self.cut:,.0f} m³ and build about {self.fill:,.0f} m³ of banks; {self.lining_vol:,.1f} m³ of concrete "
                f"lining. It carries {q_text(self.q_capacity)} m³/s at full depth less the freeboard; the design flow is "
                f"{q_text(self.q_design)} m³/s ({'enough' if ok else 'NOT enough'}).")


def design_canal(path: str, pts, cd: CanalDesign, max_cells: int = ms.MAX_CELLS) -> CanalResult:
    from shapely.geometry import LineString
    if cd.bw < 0 or cd.depth <= 0 or cd.z < 0:
        raise ValueError("The canal needs a depth and a bottom width of 0 or more.")
    if cd.slope_pct <= 0:
        raise ValueError("The bed slope must be more than 0% so the water flows.")
    if cd.freeboard >= cd.depth:
        raise ValueError("The freeboard must be less than the depth.")
    al = alm.build(pts, [0.0] * max(0, len(pts) - 2))
    stations = rdm.make_stations(pts, cd.interval, al=al)
    cxy, _ = al.densify(1.0)
    reach = cd.top_half + cd.lining_t * 2 + cd.berm_w + rdm.MAX_SIDE
    dem = ms.read_area(path, LineString(cxy).buffer(reach).bounds, max_cells)
    step = max(dem.cell, 0.1)
    dxy, dd = al.densify(max(step, 0.5))
    dg = ms.sample(dem, dxy[:, 0], dxy[:, 1])
    ok = np.isfinite(dg)
    if not ok.any():
        raise ValueError("There is no elevation data along the canal line.")
    g0 = float(dg[ok][0])
    inv0 = g0 - cd.depth if cd.start_mode == "ground" else float(cd.start_invert)
    if not math.isfinite(inv0):
        raise ValueError("Type the bed level at the start.")
    S = cd.slope_pct / 100
    dz = inv0 - S * dd
    sd = np.array([s_.s for s_ in stations])
    sg = ms.sample(dem, np.array([s_.x for s_ in stations]), np.array([s_.y for s_ in stations]))
    la = _lining_area(cd)
    sq = math.sqrt(1 + cd.z * cd.z)
    u_c = cd.bw / 2 + cd.lining_t * (sq - cd.z)
    u_t = cd.top_half + cd.lining_t * sq
    # samples every `step`, plus the corners of the lining and the end of the berm (exact areas there)
    near = u_t + cd.berm_w + 2.0                # the canal itself is small: 5 cm samples across it
    u = np.union1d(np.union1d(np.arange(0, near, min(step, 0.05)), np.arange(near, reach + step / 2, step)),
                   [u_c, u_t, u_t + cd.berm_w])
    cc = cf = 0.0
    bank_len = 0.0
    prev_bank = False
    for i, (st, g) in enumerate(zip(stations, sg)):
        st.ground, st.design = float(g), float(inv0 - S * st.s)
        rx, ry = st.ty, -st.tx
        res = {}
        for side, (nx, ny) in ((1, (rx, ry)), (-1, (-rx, -ry))):
            gg = ms.sample(dem, st.x + nx * u, st.y + ny * u)
            D, catch, bank = side_line(u, gg, st.design, cd)
            ca, fa = rdm._areas(u, gg, D, catch)
            res[side] = (gg, D, catch, ca, fa, bank)
        gl, Dl, cl, cal, fal, bl = res[-1]
        gr, Dr, cr, car, far, br = res[1]
        st.cut_area, st.fill_area = cal + car, fal + far
        st.left_catch, st.right_catch = -cl, cr
        st.cut_side = (not bl, not br)
        st.pccp_area = la                          # lining area (shown with the concrete quantities)
        w = max(cl, cr) + max(2.0, 0.25 * max(cl, cr))
        keep = u <= w
        st.offsets = np.concatenate([-u[keep][:0:-1], u[keep]])
        st.ground_xs = np.concatenate([gl[keep][:0:-1], gr[keep]])
        st.design_xs = np.concatenate([Dl[keep][:0:-1], Dr[keep]])
        if i:
            a, L = stations[i - 1], st.s - stations[i - 1].s
            st.cut_vol = (a.cut_area + st.cut_area) / 2 * L
            st.fill_vol = (a.fill_area + st.fill_area) / 2 * L
            st.pccp_vol = la * L
            if prev_bank or bl or br:
                bank_len += L
        prev_bank = bl or br
        cc += st.cut_vol
        cf += st.fill_vol
        st.cum_cut, st.cum_fill, st.mass = cc, cf, cc - cf * (1 + cd.shrink_pct / 100)
    L_total = float(sd[-1])
    # hydraulics
    q_cap, a_cap, _p, _r, v_cap = flow(cd.bw, cd.z, cd.depth - cd.freeboard, cd.n, S)
    qd = design_q(cd)
    yd = normal_depth(qd, cd.bw, cd.z, cd.n, S, ymax=max(cd.depth * 4, 5.0)) if qd > 0 else 0.0
    vd = flow(cd.bw, cd.z, yd, cd.n, S)[4] if yd == yd and yd > 0 else float("nan")
    cmix, _tot = c_mix(cd.covers)
    warn = canal_warnings(cd, stations, dg, q_cap, qd, yd, vd)
    hyd = hydraulics_rows(cd, q_cap, v_cap, a_cap, qd, yd, vd, cmix)
    inv = np.array([s_.design for s_ in stations])
    bed_depth = sg - inv                        # ground above the bed at the centre line
    bank_h = inv + cd.depth - sg                # canal top above the ground (bank height)
    inputs = canal_inputs(cd, cmix)
    return CanalResult(stations, L_total, dd, dg, dz, cc, cf,
                       ew.make_haul(cc, cf, cd.swell_pct, cd.shrink_pct, cd.truck_m3), inputs,
                       cd.slope_pct, 0.0,
                       float(np.nanmax(bed_depth)) if np.isfinite(bed_depth).any() else 0.0,
                       max(0.0, float(np.nanmax(bank_h))) if np.isfinite(bank_h).any() else 0.0,
                       0, int((~np.isfinite(sg)).sum()), dem.crs_wkt, dem.cell, al, cxy, warn, "", [], [], 0,
                       pccp_vol=la * L_total, pccp_area=0.0, agg_vol=0.0, min_radius=0.0, supers=[],
                       lining_vol=la * L_total, lining_area=la, q_design=qd, q_capacity=q_cap, v_capacity=v_cap,
                       y_design=yd, v_design=vd, tc=tc_minutes(cd), c_weighted=cmix, i_design=intensity(cd),
                       bank_len=bank_len, hydraulics=hyd)


def canal_warnings(cd: CanalDesign, stations, dg, q_cap, qd, yd, vd) -> list[tuple[str, str]]:
    out = []
    ok = np.isfinite(dg)
    if ok.sum() > 2 and dg[ok][-1] > dg[ok][0] + 0.5:
        out.append(("The ground rises toward the end", "The canal bed always falls from the first point to the "
                    "last. Draw the canal from its upstream (high) end to its downstream (low) end, or the cut gets "
                    "deep toward the end."))
    if qd > 0 and q_cap < qd:
        out.append(("Not enough capacity", f"The canal carries {q_cap:.3f} m³/s at full depth less the freeboard; "
                    f"the design flow is {qd:.3f} m³/s. Make it wider or deeper, or the bed steeper."))
    if yd == yd and yd > cd.depth - cd.freeboard + 1e-6:
        out.append(("Freeboard not kept", f"At the design flow the water is {yd:.2f} m deep, leaving "
                    f"{max(0.0, cd.depth - yd):.2f} m to the top (asked {cd.freeboard:.2f} m)."))
    if vd == vd and vd < V_MIN:
        out.append(("Slow flow", f"{vd:.2f} m/s at the design flow, below {V_MIN} m/s: silt may settle. A steeper bed "
                    "or a narrower bottom helps."))
    if vd == vd and vd > V_MAX:
        out.append(("Fast flow", f"{vd:.2f} m/s at the design flow, above {V_MAX} m/s: check the lining against wear "
                    "and the outlet against scour."))
    if cd.q_mode == "rain":
        _c, tot = c_mix(cd.covers)
        if abs(tot - 100) > 0.5:
            out.append(("Land cover shares", f"The shares add up to {tot:g}%, not 100%."))
        if cd.rain_min >= 360:
            out.append(("Long rainfall period", "The rainfall period is 6 hours or more. Use the heaviest rain over "
                        "about the time of concentration, not a daily or monthly total."))
        for k, (share, c) in cd.covers.items():
            if k in COVERS and float(share) > 0:
                name, lo, hi, _src = COVERS[k]
                if not (lo - 1e-9 <= float(c) <= hi + 1e-9):
                    out.append(("Runoff coefficient outside its range",
                                f"{name}: C {float(c):.2f} is outside the table's {lo:.2f} - {hi:.2f}."))
    return out


def hydraulics_rows(cd, q_cap, v_cap, a_cap, qd, yd, vd, cmix) -> list[tuple[str, str, str]]:
    rows = [("Capacity at full depth less freeboard", f"{q_cap:.3f} m³/s",
             f"water {cd.depth - cd.freeboard:.2f} m deep, {v_cap:.2f} m/s (Manning, n {cd.n:g}, S {cd.slope_pct:g}%)"),
            ("Design flow", f"{qd:.3f} m³/s",
             (f"Rational Method: C {cmix:.3f} × i {intensity(cd):.1f} mm/h × A {cd.area_ha:.2f} ha / 360"
              if cd.q_mode == "rain" else "typed"))]
    if yd == yd:
        rows.append(("Water depth at the design flow", f"{yd:.3f} m",
                     f"{vd:.2f} m/s; {cd.depth - yd:.2f} m to the top of the lining"))
    rows.append(("Use of the capacity", f"{(qd / q_cap * 100 if q_cap > 0 else 0):.0f}%", ""))
    return rows


def canal_inputs(cd: CanalDesign, cmix: float) -> list[tuple[str, str]]:
    inp = [("Canal", f"lined trapezoid: bottom {cd.bw:g} m, depth {cd.depth:g} m, sides {cd.z:g} : 1, "
                     f"concrete lining {cd.lining_t * 1000:.0f} mm, Manning n {cd.n:g}"),
           ("Freeboard", f"{cd.freeboard:g} m"),
           ("Bed", f"falls {cd.slope_pct:g}% from the start (" + ("top of the lining at the ground there" if
                                                                  cd.start_mode == "ground" else
                                                                  f"bed at {cd.start_invert:.3f} m") + ")"),
           ("Beside the canal", f"{cd.berm_w:g} m berm / bank top; cut sides {cd.cut_ratio:g} : 1, bank sides "
                                f"{cd.fill_ratio:g} : 1"),
           ("Stations every", f"{cd.interval:g} m")]
    if cd.q_mode == "rain":
        cov = "; ".join(f"{COVERS[k][0]} {float(v[0]):g}% (C {float(v[1]):.2f})" for k, v in cd.covers.items()
                        if k in COVERS and float(v[0]) > 0)
        tcm = ("Kirpich (1940)" if cd.tc_method == "kirpich" else
               "DPWH-JICA Manual on Flood Control Planning (2003): inlet time + Kraven flow time")
        inp += [("Design flow", "Rational Method Q = C i A / 360"),
                ("Rainfall", f"{cd.rain_mm:g} mm in {cd.rain_min:g} min = {intensity(cd):.1f} mm/h "
                             f"({cd.return_period}-year)"),
                ("Catchment", f"{cd.area_ha:.2f} ha, longest flow path {cd.flow_len:.0f} m, fall {cd.drop:.1f} m "
                              f"({ {'map': 'measured on the drone map', 'own': 'measured on my own elevation file', 'typed': 'typed'}.get(cd.area_src, '') })"),
                ("Time of concentration", f"{tc_minutes(cd):.1f} min, {tcm}"),
                ("Land cover", (cov or "none") + f"; weighted C {cmix:.3f} ({RUNOFF_SOURCE})")]
    else:
        inp += [("Design flow", f"{cd.q_design:g} m³/s (typed)")]
    return inp


def design_dict(cd: CanalDesign) -> dict:
    return asdict(cd)


def design_from(plan: dict) -> CanalDesign:
    raw = plan.get("canal") or {}
    return CanalDesign(**{k: v for k, v in raw.items() if k in CanalDesign.__dataclass_fields__})


def default_covers() -> dict:
    """Starting land cover for the capacity check: shares 0 except agricultural land, C at the upper end."""
    return {k: [100.0 if k == "agri" else 0.0, v[2]] for k, v in COVERS.items()}
