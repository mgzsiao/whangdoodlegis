"""Vertical curves: parabola formulas, K values, fitting between PVIs, use in the road design."""
import numpy as np
import pytest

from fieldortho.core import vertical as vt


def test_k_table_and_min_length():
    assert vt.k_values(30) == (2.0, 6.0)
    assert vt.k_values(60) == (11.0, 18.0)
    assert vt.k_values(45) == (7.0, 13.0)        # between rows -> next higher speed
    assert vt.min_length(30) == pytest.approx(18.0)


def test_sag_curve_textbook_example():
    # g1 = -4 %, g2 = +2 %, PVI 0+200 at 100.00 m, L = 120 m (textbook values)
    pvis = [[0, 108.0, 0], [200, 100.0, 120], [400, 104.0, 0]]
    c, = vt.build(pvis, 60)
    assert c.kind == "sag" and c.g1 == pytest.approx(-4) and c.g2 == pytest.approx(2)
    assert c.pvc_s == pytest.approx(140) and c.pvt_s == pytest.approx(260)
    assert c.pvc_z == pytest.approx(102.4) and c.pvt_z == pytest.approx(101.2)
    assert c.k == pytest.approx(20) and not c.short          # sag K 18 needed at 60 km/h
    assert c.middle_offset == pytest.approx(6 * 120 / 800)    # e = A L / 800 = 0.90 m
    s, z = c.turning_point()                                  # lowest point x = -g1 L / A = 80 m after PVC
    assert s == pytest.approx(220) and z == pytest.approx(102.4 - 0.04 * 80 + 0.06 * 80 ** 2 / 240)
    d = np.array([0, 140, 200, 220, 260, 400.0])
    z = vt.heights(d, pvis, [c])
    assert z[2] == pytest.approx(100.0 + 0.90)               # curve passes e above the PVI
    assert z[1] == pytest.approx(102.4) and z[4] == pytest.approx(101.2)
    assert z[0] == pytest.approx(108) and z[-1] == pytest.approx(104)
    # the curve is smooth: slope matches the grades at PVC and PVT
    dd = np.linspace(139.9, 140.1, 3)
    assert np.diff(vt.heights(dd, pvis, [c]))[0] / 0.1 == pytest.approx(-0.04, abs=1e-4)


def test_length_from_speed_and_fit_between_pvis():
    # crest +6 % to -4 % at 30 km/h: K 2 -> L = 20 m (more than 0.6 x 30 = 18 m)
    c, = vt.build([[0, 100, 0], [100, 106, 0], [200, 102, 0]], 30)
    assert c.kind == "crest" and c.length == pytest.approx(20) and c.k_min == 2
    # tiny grade change: no curve
    c, = vt.build([[0, 100, 0], [100, 101, 0], [200, 102.1, 0]], 30)
    assert c.length == 0
    # two long curves on a short grade are shortened to fit and flagged
    cs = vt.build([[0, 100, 300], [100, 110, 300], [130, 100, 300], [300, 110, 0]], 60)
    a, b = cs[0], cs[1]
    assert a.pvt_s <= b.pvc_s + 1e-9 and a.reduced and b.reduced
    w = vt.warnings(cs, 60)
    assert any("made shorter" in t for t, _ in w)


def test_propose_follows_profile():
    d = np.linspace(0, 300, 301)
    z = np.interp(d, [0, 100, 220, 300], [50, 58, 52, 55])
    p = vt.propose(d, z)
    assert [round(r[0]) for r in p] == [0, 100, 220, 300]


def test_road_uses_vertical_curves(tmp_path):
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    from synthetic_site import synthetic_job
    from fieldortho.core import jobs, road
    jd = synthetic_job(str(tmp_path / "jobs"))
    dem = jobs.Job.load(jd).summary["files"]["dtm"]
    import rasterio
    with rasterio.open(dem) as ds:
        b = ds.bounds
    pts = [(b.left + 10, b.bottom + 10), (b.right - 10, b.top - 10)]
    rd = road.RoadDesign(interval=10, profile="targets", targets=[[60, 0.0]], vcurves=True, speed=30)
    r0 = road.design_road(dem, pts, road.RoadDesign(interval=10, profile="gentle"))
    rd.targets = [[60, float(np.interp(60, r0.prof_d, r0.prof_z)) + 4.0]]
    r = road.design_road(dem, pts, rd)
    assert len(r.pvis) == 3 and len(r.vcurves) == 1
    c = r.vcurves[0]
    assert c.length > 0 and c.kind == "crest"
    # station heights lie on the curve
    for st in r.stations:
        assert st.design == pytest.approx(float(vt.heights([st.s], r.pvis, r.vcurves)[0]), abs=1e-6)
    assert any(k == "Vertical curves" for k, _ in r.inputs)


def test_road_report_tables(tmp_path):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from synthetic_site import synthetic_job
    from fieldortho.core import jobs, road, road_report as rr
    import rasterio
    dem = jobs.Job.load(synthetic_job(str(tmp_path / "jobs"))).summary["files"]["dtm"]
    with rasterio.open(dem) as ds:
        b = ds.bounds
    pts = [(b.left + 10, b.bottom + 10), (b.right - 10, b.top - 10)]
    rd = road.RoadDesign(interval=20, vcurves=True, speed=30)
    r = road.design_road(dem, pts, rd)
    q = rr.quantity_rows(r)
    assert q[-1][0] == "TOTAL" and float(q[-1][4]) == pytest.approx(r.cut, abs=0.01)
    assert sum(float(x[4]) for x in q[:-1]) == pytest.approx(r.cut, abs=0.05)
    w = rr.worked_example(r, rd.shrink_pct)
    assert any("average end-area" in t for t in w)
    i = max(range(1, len(r.stations)), key=lambda k: r.stations[k].cut_vol + r.stations[k].fill_vol)
    assert f"= {r.stations[i].cut_vol:.2f} m³" in " ".join(w)
    secs = rr.section_data(r, rd)
    assert len(secs) == len(r.stations) and secs[1].info[0] == ("Distance from last", "20.00 m")
