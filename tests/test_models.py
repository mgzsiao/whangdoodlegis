"""Accuracy tests of the flood and landslide models against hand calculations."""
import math

import numpy as np
import pytest
from rasterio.transform import from_origin

from fieldortho.core import flood, landslide as ls
from fieldortho.core.terrain import Dem, slope_aspect


def dem_from(z, cell=1.0):
    return Dem(np.asarray(z, float), from_origin(500000, 1000000, cell, cell), "EPSG:32651", "mem")


# --------------------------------------------------------------------------- flood
def test_flood_area_and_volume_on_v_valley():
    # V-shaped valley: z = |x| * 0.1 across 201 columns (1 m cells), 100 rows
    x = np.arange(-100, 101)
    z = np.tile(np.abs(x) * 0.1, (100, 1))
    d = dem_from(z)
    res = flood.simulate(d, level=2.0, seeds=[(50, 100)])
    # wet where |x|*0.1 < 2  -> |x| < 20 -> 39 columns (x=-19..19)
    assert res.stats["area_ha"] == pytest.approx(39 * 100 / 1e4)
    # volume = sum over columns (2 - 0.1|x|) * 100 rows
    vol = sum(2 - 0.1 * abs(i) for i in range(-19, 20)) * 100
    assert res.stats["volume_m3"] == pytest.approx(vol)
    assert res.stats["max_depth_m"] == pytest.approx(2.0)
    # MGB classes: high > 1 m -> |x| <= 9 (19 cols); moderate 0.5-1 m -> 10 <= |x| <= 15
    # (12 cols); low < 0.5 m -> 16 <= |x| <= 19 (8 cols)
    assert res.stats["classes"]["High (> 1 m)"] == pytest.approx(19 * 100 / 1e4)
    assert res.stats["classes"]["Moderate (0.5 - 1 m)"] == pytest.approx(12 * 100 / 1e4)
    assert res.stats["classes"]["Low (< 0.5 m)"] == pytest.approx(8 * 100 / 1e4)


def test_flood_connectivity_excludes_closed_depression():
    z = np.full((60, 60), 10.0)
    z[:, :10] = 0.0                 # river along the west edge
    z[30:40, 40:50] = 1.0           # closed pit inland, below the level, but walled off
    d = dem_from(z)
    con = flood.simulate(d, 2.0, seeds=[(5, 3)])
    allc = flood.simulate(d, 2.0, connect="none")
    assert con.stats["area_ha"] == pytest.approx(60 * 10 / 1e4)
    assert allc.stats["area_ha"] == pytest.approx((600 + 100) / 1e4)
    edge = flood.simulate(d, 2.0, connect="edges")
    assert edge.stats["area_ha"] == pytest.approx(600 / 1e4)


# --------------------------------------------------------------------------- slope
def test_slope_of_known_plane():
    cell = 2.0
    r, c = np.mgrid[0:50, 0:50]
    z = 100 - c * cell * math.tan(math.radians(30))     # dips east at 30 deg
    s, a = slope_aspect(dem_from(z, cell))
    assert np.allclose(s[5:-5, 5:-5], 30.0, atol=1e-6)
    assert np.allclose(a[5:-5, 5:-5], 0.0, atol=1e-6)    # downslope = east


# --------------------------------------------------------------------------- stability
def test_infinite_slope_matches_hand_calculation():
    p = ls.Params(soil_depth=2.0, cohesion=5.0, friction=30.0, unit_weight=18.0, wetness=0.5)
    b = math.radians(35)
    num = 5 + (18 - 0.5 * 9.81) * 2 * math.cos(b) ** 2 * math.tan(math.radians(30))
    den = 18 * 2 * math.sin(b) * math.cos(b)
    assert ls.factor_of_safety(np.array([35.0]), p)[0] == pytest.approx(num / den)
    # cohesionless dry slope: FS = tan(phi)/tan(beta)
    p0 = ls.Params(cohesion=0, friction=30, wetness=0)
    assert ls.factor_of_safety(np.array([30.0]), p0)[0] == pytest.approx(1.0)
    # critical wetness reproduces FS = 1
    m = ls.critical_wetness(35.0, p)
    p.wetness = m
    assert ls.factor_of_safety(np.array([35.0]), p)[0] == pytest.approx(1.0)


# --------------------------------------------------------------------------- runout
@pytest.mark.parametrize("travel", [15.0, 22.0, 30.0])
def test_runout_reaches_energy_line_distance(travel):
    """Uniform 35 deg slope (100 m drop) onto a flat plain: the mass must stop where the
    line from the top at the travel angle meets the ground: L_total = H / tan(alpha)."""
    cell = 1.0
    H = 100.0
    Ls = H / math.tan(math.radians(35))
    ncol = int(Ls + H / math.tan(math.radians(travel)) + 60)
    c = np.arange(ncol) * cell
    z = np.where(c < Ls, H - c * math.tan(math.radians(35)), 0.0)
    z = np.tile(z, (21, 1))
    d = dem_from(z, cell)
    release = np.zeros_like(z, bool)
    release[8:13, 0:3] = True
    p = ls.Params(travel_angle=travel, max_velocity=1e3)
    E = ls.runout(d, release, p)
    reached = np.nonzero(np.isfinite(E[10]))[0].max()
    expected = H / math.tan(math.radians(travel))
    assert reached * cell == pytest.approx(expected, abs=3 * cell)


@pytest.mark.parametrize("facing", ["east", "south", "west", "north"])
def test_runout_goes_downhill_in_every_direction(facing):
    """The mass must travel down the fall line whichever way the slope faces."""
    n = 160
    r, c = np.mgrid[0:n, 0:n].astype(float)
    t = math.tan(math.radians(35))
    dist = {"east": c, "west": n - 1 - c, "south": r, "north": n - 1 - r}[facing]
    z = np.where(dist < 60, 100 - dist * t, 100 - 60 * t)
    d = dem_from(z)
    rel = (dist < 4) & (np.abs({"east": r, "west": r, "south": c, "north": c}[facing] - n / 2) < 3)
    E = ls.runout(d, rel, ls.Params(travel_angle=25, max_velocity=1e3))
    reached = np.isfinite(E)
    expected = 60 * t / math.tan(math.radians(25))   # energy line: H / tan(travel angle) = 90 cells
    assert abs(dist[reached].max() - expected) <= 3
    lateral = np.abs({"east": r, "west": r, "south": c, "north": c}[facing] - n / 2)[reached & (dist < 60)]
    assert lateral.max() <= 4                  # stays in the fall line (source is 5 cells wide)


def test_simulate_release_and_stats():
    cell = 2.0
    r, c = np.mgrid[0:80, 0:120]
    slope = np.where(c < 50, 38.0, 3.0)
    z = np.zeros_like(c, float)
    for j in range(1, 120):
        z[:, j] = z[:, j - 1] - cell * math.tan(math.radians(slope[0, j - 1]))
    d = dem_from(z, cell)
    sel = np.zeros_like(z, bool)
    sel[30:50, 10:30] = True
    p = ls.Params(soil_depth=2, cohesion=3, friction=35, unit_weight=18, wetness=0.9, travel_angle=20)
    res = ls.simulate(d, sel, p)
    assert res.release.sum() == sel.sum()           # all selected cells fail at 38 deg & wet
    assert res.stats["volume_m3"] == pytest.approx(sel.sum() * cell * cell * 2)
    assert res.stats["runout_ha"] > 0
    # the 20 m/s velocity cap can only shorten the runout -> reach angle >= travel angle
    assert res.stats["apparent_travel_angle_deg"] >= 20 - 0.5
    free = ls.simulate(d, sel, ls.Params(soil_depth=2, cohesion=3, friction=35, unit_weight=18, wetness=0.9,
                                         travel_angle=20, max_velocity=1e3))
    assert abs(free.stats["apparent_travel_angle_deg"] - 20) < 1.5
    dry = ls.simulate(d, sel, ls.Params(soil_depth=2, cohesion=3, friction=35, wetness=0.0))  # FS 1.07
    assert dry.release.sum() == 0                   # FS > 1 when dry


def test_river_rise_follows_river_slope():
    """Sloping V-valley (bed falls 1 m per 100 m): a 2 m river rise must flood the same
    width everywhere (half-width = rise / side slope), unlike a flat 'bathtub' level."""
    from fieldortho.core import hydro
    x = np.arange(400)
    y = np.arange(-60, 61)
    X, Y = np.meshgrid(x, y)
    side = 0.2
    z = 5 + 0.01 * X + side * np.abs(Y)          # outlet at x = 0 (west edge)
    d = dem_from(z)
    dr = hydro.drainage(d)
    riv, snap, _ = hydro.river_network(dr, (60, 300))
    h, ref = hydro.hand(d, dr, riv)
    res = flood.simulate_river(d, 2.0, h, ref, riv)
    widths = np.isfinite(res.depth).sum(axis=0)[20:340]   # away from the upstream map edge
    # |y| < 2 / 0.2 = 10  ->  19 cells wide (y = -9..9), at every station along the river
    assert np.all(widths == 19), np.unique(widths)
    assert res.stats["max_depth_m"] == pytest.approx(2.0)
    bath = flood.simulate(d, z[60, 300] + 2.0, seeds=[(60, 300)])
    assert np.isfinite(bath.depth).sum(axis=0)[20] > 19   # flat level over-floods downstream
