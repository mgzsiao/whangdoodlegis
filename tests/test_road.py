"""Road earthworks against hand-worked cross-sections."""
import math

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from fieldortho.core import road as rdm

X0, Y0 = 500000.0, 1000200.0


def write_dem(path, z, cell):
    with rasterio.open(path, "w", driver="GTiff", width=z.shape[1], height=z.shape[0], count=1, dtype="float32",
                       crs="EPSG:32651", transform=from_origin(X0, Y0, cell, cell), nodata=-9999) as ds:
        ds.write(z.astype("float32"), 1)
    return str(path)


@pytest.fixture(scope="module")
def flat(tmp_path_factory):
    return write_dem(tmp_path_factory.mktemp("rflat") / "flat.tif", np.full((800, 800), 100.0), 0.25)   # 200 m


@pytest.fixture(scope="module")
def slope(tmp_path_factory):
    r, c = np.mgrid[0:800, 0:800]
    x = (c + 0.5) * 0.25
    return write_dem(tmp_path_factory.mktemp("rslope") / "slope.tif", 100 + 0.05 * x, 0.25)


LINE = [(X0 + 50, Y0 - 100), (X0 + 150, Y0 - 100)]          # 100 m west -> east through the middle


def plain(**kw):
    d = dict(interval=10, start_mode="typed", profile="level", width=6, crown_pct=0, shoulders=False,
             cut_ratio=1, fill_ratio=2, shrink_pct=0)
    d.update(kw)
    return rdm.RoadDesign(**d)


def test_station_labels_and_spacing():
    st = rdm.make_stations([(0, 0), (0, 45), (20, 45)], 20)
    assert [s.label for s in st] == ["0+000", "0+020", "0+040", "0+045", "0+060", "0+065"]
    assert [s.kind for s in st][3] == "bend" and st[-1].kind == "end"
    b = st[3]
    assert (b.tx, b.ty) == pytest.approx((math.sqrt(0.5), math.sqrt(0.5)))       # bisector at the bend
    assert rdm.station_label(1234.5) == "1+234.50"


def test_level_fill_road(flat):
    # 1 m high, 6 m top, 2:1 sides: 6 x 1 + 2 x (2 x 1 / 2) = 8 m2 per section
    r = rdm.design_road(flat, LINE, plain(start_z=101.0))
    for s in r.stations:
        assert s.fill_area == pytest.approx(8.0, rel=0.005)
        assert s.cut_area == 0
        assert s.right_catch == pytest.approx(5.0, abs=0.01) and s.left_catch == pytest.approx(-5.0, abs=0.01)
    assert r.fill == pytest.approx(800, rel=0.005)
    assert r.highest_fill == pytest.approx(1.0)


def test_level_cut_road_with_and_without_ditch(flat):
    r = rdm.design_road(flat, LINE, plain(start_z=99.0))
    assert r.cut == pytest.approx(700, rel=0.005)          # 6 x 1 + 2 x (1 x 1 / 2)
    d = rdm.design_road(flat, LINE, plain(start_z=99.0, ditch=True, ditch_w=0.5, ditch_d=0.5))
    assert d.cut == pytest.approx(1100, rel=0.005)         # each side 0.625 + 0.75 + 1.125


def test_pavement_thickness_lowers_the_earthworks(flat):
    r = rdm.design_road(flat, LINE, plain(start_z=101.0, pavement_t=0.3))
    assert r.stations[3].fill_area == pytest.approx(6 * 0.7 + 2, rel=0.005)


def test_crown_and_shoulders(flat):
    # road at ground level: crown 2% over 3 m (cut 0.03*... ) and 4% shoulders 1.5 m, no side slopes needed
    rd = plain(start_z=100.0, crown_pct=2, shoulders=True, shoulder_w=1.5, shoulder_pct=4)
    r = rdm.design_road(flat, LINE, rd)
    side = 3 * 3 * 0.02 / 2 + 1.5 * (0.06 + 0.06 + 0.06) / 2      # triangle under the crown + shoulder strip
    # the shoulder edge is 0.12 m below ground -> a 1:1 cut slope of 0.12 m
    side += 0.12 * 0.12 / 2
    assert r.stations[2].cut_area == pytest.approx(2 * side, rel=0.02)


def test_road_following_a_steady_slope_moves_little_earth(slope):
    rd = plain(start_mode="ground", profile="straight", end_mode="ground")
    r = rdm.design_road(slope, LINE, rd)
    assert r.total < 1.0
    assert r.steepest == pytest.approx(5.0, abs=0.05)


def test_gentle_profile_respects_the_steepest_slope(tmp_path):
    cell, n = 0.5, 400
    r, c = np.mgrid[0:n, 0:n]
    x = (c + 0.5) * cell
    hill = 100 + 12 * np.exp(-((x - 100) / 15) ** 2)          # a 12 m hill across the road
    path = write_dem(tmp_path / "hill.tif", hill, cell)
    res = rdm.design_road(path, LINE, plain(start_mode="ground", profile="gentle", max_grade=8, fill_ratio=1.5))
    g = np.diff(res.prof_z) / np.diff(res.prof_d) * 100
    assert np.max(np.abs(g)) <= 8.0 + 1e-6
    assert res.steepest <= 8.0 + 1e-6
    assert res.cut > 100                                       # it has to cut through the top of the hill
    assert res.stations[0].design == pytest.approx(res.stations[0].ground, abs=0.05)


def test_targets_and_mass_haul(flat):
    rd = plain(start_z=100.0, profile="targets", targets=[[50, 101.0], [100, 100.0]], shrink_pct=10)
    r = rdm.design_road(flat, LINE, rd)
    mid = next(s for s in r.stations if abs(s.s - 50) < 1e-6)
    assert mid.design == pytest.approx(101.0)
    assert r.stations[-1].mass == pytest.approx(r.cut - r.fill * 1.1, rel=1e-6)
    assert r.haul.balance == pytest.approx(r.stations[-1].mass, rel=1e-6)


def test_no_data_and_bad_input(flat):
    with pytest.raises(ValueError):
        rdm.design_road(flat, [(X0 + 1, Y0 - 1)], plain(start_z=100))
    with pytest.raises(ValueError):
        rdm.design_road(flat, LINE, plain(start_mode="typed", start_z=float("nan")))


# --------------------------------------------------------------------------- curves (stage 4)
from fieldortho.core import alignment as alm  # noqa: E402


def test_curve_geometry_90_degrees():
    al = alm.build([(0, 0), (0, 100), (100, 100)], [20])
    c = al.curves[0]
    assert c.delta == pytest.approx(90) and not c.left                      # north then east = right turn
    assert c.tangent == pytest.approx(20) and c.length == pytest.approx(10 * math.pi)
    assert c.pc == pytest.approx((0, 80)) and c.pt == pytest.approx((20, 100))
    assert al.length == pytest.approx(80 + 10 * math.pi + 80)
    x, y, tx, ty = al.point_at(c.pc_s + c.length / 2)
    assert math.dist((x, y), c.center) == pytest.approx(20)
    assert (tx, ty) == pytest.approx((math.sqrt(0.5), math.sqrt(0.5)))
    row = rdm.curve_rows(al)[0]
    assert row[7] == pytest.approx(20 * (math.sqrt(2) - 1), abs=1e-3)       # external
    assert row[8] == pytest.approx(20 * (1 - math.sqrt(0.5)), abs=1e-3)     # middle ordinate
    st = rdm.make_stations([(0, 0), (0, 100), (100, 100)], 20, al=al)
    kinds = {round(s.s, 3): s.kind for s in st}
    assert kinds[80.0] == "curve start" and kinds[round(80 + 10 * math.pi, 3)] == "curve end"


def test_curves_made_smaller_to_fit():
    al = alm.build([(0, 0), (0, 100), (30, 100), (30, 200)], [50, 50])
    assert all(c.reduced for c in al.curves)
    assert al.curves[0].tangent + al.curves[1].tangent <= 30 + 1e-6
    assert al.curves[0].radius == pytest.approx(15, rel=0.01)


def test_level_road_around_a_curve(flat):
    pts = [(X0 + 40, Y0 - 160), (X0 + 40, Y0 - 60), (X0 + 140, Y0 - 60)]
    r = rdm.design_road(flat, pts, plain(start_z=101.0, radii=[30]))
    assert len(r.alignment.curves) == 1
    assert r.length == pytest.approx(70 + 15 * math.pi + 70, rel=1e-6)
    assert r.fill == pytest.approx(8 * r.length, rel=0.01)
    assert any(s.kind == "curve start" for s in r.stations)


def test_dpwh_checks(flat):
    pts = [(X0 + 40, Y0 - 160), (X0 + 40, Y0 - 60), (X0 + 140, Y0 - 60)]
    rd = plain(start_z=101.0, radii=[20], standard="fmr", fill_ratio=1.2)
    r = rdm.design_road(flat, pts, rd)
    titles = [t for t, _ in r.warnings]
    assert "Curve 1 is too tight" in titles and "Fill sides too steep" in titles
    assert rdm.STANDARDS["fmr"]["width"] == 6.1 and rdm.STANDARDS["fmr_low"]["width"] == 5.0
    assert rdm.STANDARDS["tourism"]["min_radius"] == 50 and rdm.STANDARDS["fmr"]["min_radius"] == 30
