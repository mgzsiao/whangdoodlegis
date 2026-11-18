"""Lined drainage canal: sections, quantities, hydraulics and design flow against hand calculations."""
import math

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from fieldortho.core import canal as cn

X0, Y0 = 500000.0, 1000200.0
LINE = [(X0 + 50, Y0 - 100), (X0 + 150, Y0 - 100)]          # 100 m west -> east


def write_dem(path, z, cell=0.25):
    with rasterio.open(path, "w", driver="GTiff", width=z.shape[1], height=z.shape[0], count=1, dtype="float32",
                       crs="EPSG:32651", transform=from_origin(X0, Y0, cell, cell), nodata=-9999) as ds:
        ds.write(z.astype("float32"), 1)
    return str(path)


@pytest.fixture(scope="module")
def flat(tmp_path_factory):
    return write_dem(tmp_path_factory.mktemp("cflat") / "flat.tif", np.full((800, 800), 100.0))


def plain(**kw):
    d = dict(interval=10, q_mode="flow", q_design=0.5, shrink_pct=0)
    d.update(kw)
    return cn.CanalDesign(**d)


S5 = math.sqrt(1.25)
U_T = 0.25 + 0.5 * 0.8 + 0.1 * S5          # outer top edge of the lining
U_C = 0.25 + 0.1 * (S5 - 0.5)              # outer bottom corner
OUTER = (2 * U_T + 2 * U_C) / 2 * 0.9      # outside of the lining, bed - 0.1 to the top


def test_lining_area():
    # outside trapezoid (0.9 m high) minus the water trapezoid (0.8 m high)
    assert cn._lining_area(cn.CanalDesign()) == pytest.approx(OUTER - 0.9 * 0.8, rel=1e-9)
    assert cn._lining_area(cn.CanalDesign()) == pytest.approx(0.24625, abs=1e-4)


def test_canal_into_flat_ground(flat):
    # top of the lining at the ground at the start, bed falling 0.5%: at 100 m the top is 0.5 m below ground:
    # cut = outside of the lining + 0.5 m over the lining and both 0.5 m berms + two 1.5 : 1 triangles
    r = cn.design_canal(flat, LINE, plain())
    s0, s100 = r.stations[0], r.stations[-1]
    assert s0.cut_area == pytest.approx(OUTER, rel=0.01) and s0.fill_area == pytest.approx(0, abs=1e-3)
    d = 0.5
    assert s100.cut_area == pytest.approx(OUTER + 2 * d * (U_T + 0.5) + 1.5 * d * d, rel=0.01)
    assert s100.design == pytest.approx(100 - 0.8 - 0.5)                        # bed at 100 m
    assert r.lining_vol == pytest.approx(cn._lining_area(plain()) * 100, rel=1e-6)
    assert r.fill == pytest.approx(0, abs=0.05) and r.bank_len == 0


def test_canal_on_banks(flat):
    # bed typed 0.4 m higher than "top at ground": the top is 0.4 m above the ground -> earth banks
    r = cn.design_canal(flat, LINE, plain(start_mode="typed", start_invert=99.6, slope_pct=0.01))
    s = r.stations[0]
    e = 0.5                                     # outside of the lining below the ground: bed - 0.1 .. 100
    u_g = U_C + e * (U_T - U_C) / 0.9
    assert s.cut_area == pytest.approx((2 * U_C + 2 * u_g) / 2 * e, rel=0.01)
    # banks a side: wedge against the lining 0.2 x 0.4 / 2, top 0.5 x 0.4, 2 : 1 side 0.8 x 0.4 / 2
    assert s.fill_area == pytest.approx(2 * ((U_T - u_g) * 0.4 / 2 + 0.5 * 0.4 + 0.8 * 0.4 / 2), rel=0.02)
    assert r.bank_len == pytest.approx(100) and r.highest_fill == pytest.approx(0.4, abs=0.02)


def test_manning_and_normal_depth():
    q, a, p, rh, v = cn.flow(0.5, 0.5, 0.6, 0.015, 0.005)
    assert (a, p) == (pytest.approx(0.48), pytest.approx(0.5 + 1.2 * S5))
    assert q == pytest.approx(0.48 * (0.48 / (0.5 + 1.2 * S5)) ** (2 / 3) * math.sqrt(0.005) / 0.015)
    assert q == pytest.approx(0.9233, abs=1e-4)
    assert cn.normal_depth(q, 0.5, 0.5, 0.015, 0.005) == pytest.approx(0.6, abs=1e-6)


def test_design_flow_rational_and_tc():
    cd = plain(q_mode="rain", rain_mm=18, rain_min=15, area_ha=12.5, flow_len=650, drop=22,
               covers={"timber": [40, 0.55], "agri": [40, 0.25], "res_low": [15, 0.55], "concrete": [5, 0.95]})
    c, tot = cn.c_mix(cd.covers)
    assert tot == 100 and c == pytest.approx((40 * 0.55 + 40 * 0.25 + 15 * 0.55 + 5 * 0.95) / 100)
    assert cn.intensity(cd) == pytest.approx(72.0)
    assert cn.rational_q(cd) == pytest.approx(c * 72 * 12.5 / 360)
    assert cn.tc_kirpich(650, 22) == pytest.approx(0.0195 * 650 ** 0.77 * (22 / 650) ** -0.385)
    assert cn.tc_kirpich(650, 22) == pytest.approx(10.52, abs=0.01)
    # DPWH manual: 30 √(0.125 / 2) = 7.5 min inlet + 650 m at 3.5 m/s (slope 3.4% > 1/100)
    assert cn.tc_dpwh(12.5, 650, 22) == pytest.approx(7.5 + 650 / 3.5 / 60)
    assert cn.kraven_velocity(1 / 150) == 3.0 and cn.kraven_velocity(1 / 500) == 2.1
    # starting land cover: C at the upper end of the DPWH range
    assert cn.default_covers()["res_low"] == [0.0, 0.55] and cn.default_covers()["agri"][0] == 100


def test_catchment_of_a_valley(tmp_path):
    # a valley falling to the south (2%), sides rising 5% from its middle: the canal ends near the south edge
    r_, c_ = np.mgrid[0:200, 0:200]
    x, y = (c_ + 0.5) * 1.0, 200 - (r_ + 0.5) * 1.0
    z = 100 + 0.02 * y + 0.05 * np.abs(x - 100)
    path = write_dem(tmp_path / "valley.tif", z, 1.0)
    # a canal down the valley floor collects nearly the whole map; the rest leaves over the map edges
    cat = cn.catchment(path, [(X0 + 100, Y0 - 20), (X0 + 100, Y0 - 195)])
    assert cat.area_ha > 0.7 * 4.0 and cat.at_edge
    # longest path: from the far upper corner (about 100 m from the valley) over the land, then down the canal
    assert 175 + 40 < cat.flow_len < 175 + 100 * 1.42 and cat.drop > 3.0 + 0.05 * 90
    # only its end: the land draining to that point (the valley above it, caught by the canal's width)
    end = cn.catchment(path, [(X0 + 100, Y0 - 195)])
    assert 0.6 * 4.0 < end.area_ha <= cat.area_ha + 1e-9
    # a canal across the slope on the east side, drawn downhill: it collects only the land above it
    # (north-east of it, 70 m x 50 m at most), none of the land below it
    side = cn.catchment(path, [(X0 + 190, Y0 - 50), (X0 + 120, Y0 - 50)])
    assert 0.15 < side.area_ha < 0.35 + 0.07 and side.drop > 0
    assert not side.mask[60:, :].any()


def test_canal_plan_runs_and_exports(flat, tmp_path):
    from fieldortho.core import earthworks as ew, road_report as rr
    cd = plain(q_mode="rain", area_ha=2.0, flow_len=200, drop=4,
               covers={"agri": [80, 0.25], "concrete": [20, 0.95]})
    plan = ew.plan_dict(type="canal", name="Drainage canal", source="dtm",
                        points=[list(p) for p in LINE], canal=cd)
    assert isinstance(plan["canal"], dict) and ew.load_plan.__name__
    r = ew.run_plan(plan, flat)
    assert r.q_design == pytest.approx(cn.rational_q(cd)) and r.q_capacity > 0
    assert "canal" in r.headline() and "enough" in r.headline()
    files = rr.export_road(r, plan, flat, None, str(tmp_path / "out"), "EPSG:32651",
                           {"project": "Test canal"}, None, ("gpkg",))
    names = {f.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for f in files}
    assert "Drainage_canal_report.pdf" in names and "Drainage_canal_quantities.xlsx" in names
    q = dict((a, b) for a, b, _c in rr.quantities(r))
    assert "Concrete lining" in q and "Design flow" in q
