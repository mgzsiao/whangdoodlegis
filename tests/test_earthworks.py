"""Lot / pad earthworks and pile take-offs against hand calculations."""
import math
import os

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from fieldortho.core import earthworks as ew, measure as ms

X0, Y0 = 500000.0, 1000100.0


def write_dem(path, z, cell):
    with rasterio.open(path, "w", driver="GTiff", width=z.shape[1], height=z.shape[0], count=1, dtype="float32",
                       crs="EPSG:32651", transform=from_origin(X0, Y0, cell, cell), nodata=-9999) as ds:
        ds.write(z.astype("float32"), 1)
    return str(path)


def xy(n, cell):
    r, c = np.mgrid[0:n, 0:n]
    return X0 + (c + 0.5) * cell, Y0 - (r + 0.5) * cell


def square(x0, y0, s):
    return [(X0 + x0, Y0 - y0), (X0 + x0 + s, Y0 - y0), (X0 + x0 + s, Y0 - y0 - s), (X0 + x0, Y0 - y0 - s)]


@pytest.fixture(scope="module")
def flat(tmp_path_factory):
    return write_dem(tmp_path_factory.mktemp("flat") / "flat.tif", np.full((800, 800), 100.0), 0.1)   # 80 x 80 m


@pytest.fixture(scope="module")
def tilted(tmp_path_factory):
    x, _ = xy(400, 0.25)
    return write_dem(tmp_path_factory.mktemp("tilt") / "tilt.tif", 50 + 0.2 * (x - X0), 0.25)     # 100 x 100 m


def test_raised_pad_with_walls(flat):
    t = ew.design_pad(flat, square(30, 30, 20), ew.PadDesign("fixed", 102.0, sides="walls", shrink_pct=0))
    assert t.fill == pytest.approx(800, rel=0.005)
    assert t.cut == 0
    assert t.side_fill == 0
    assert t.max_fill == pytest.approx(2.0)


def test_raised_pad_with_sloped_sides(flat):
    # 2 m high, fill sides 1.5:1 -> side prisms P*r*h^2/2 + rounded corners pi*r^2*h^3/3
    t = ew.design_pad(flat, square(30, 30, 20), ew.PadDesign("fixed", 102.0, fill_ratio=1.5, shrink_pct=0))
    sides = 80 * 1.5 * 4 / 2 + math.pi * 1.5 ** 2 * 8 / 3
    assert t.pad_fill == pytest.approx(800, rel=0.005)
    assert t.side_fill == pytest.approx(sides, rel=0.03)
    assert t.works_area == pytest.approx(400 + 80 * 3 + math.pi * 3 ** 2, rel=0.03)   # toe 3 m out
    assert not t.reaches_edge


def test_balanced_level_on_a_slope(tilted):
    t = ew.design_pad(tilted, square(10, 10, 20), ew.PadDesign("balanced", sides="walls", shrink_pct=0))
    assert t.level == pytest.approx(54.0, abs=0.005)
    assert t.cut == pytest.approx(200, rel=0.01) and t.fill == pytest.approx(200, rel=0.01)
    # packed fill needs 10% more soil -> cut = 1.1 x fill: crossing x0 = 40.488 / 2.0488 m
    t2 = ew.design_pad(tilted, square(10, 10, 20), ew.PadDesign("balanced", sides="walls", shrink_pct=10))
    x0 = (30 + 10 * math.sqrt(1.1)) / (1 + math.sqrt(1.1))
    assert t2.level == pytest.approx(50 + 0.2 * x0, abs=0.005)
    assert t2.cut == pytest.approx(1.1 * t2.fill, rel=0.01)
    assert abs(t2.haul.balance) < 2


def test_balanced_level_with_side_slopes_balances(tilted):
    t = ew.design_pad(tilted, square(30, 30, 30), ew.PadDesign("balanced", cut_ratio=1, fill_ratio=1.5,
                                                               shrink_pct=10))
    assert t.side_cut > 0 and t.side_fill > 0
    assert t.cut == pytest.approx(t.fill * 1.1, rel=0.01)


def test_drainage_slope_on_flat_ground(flat):
    t = ew.design_pad(flat, square(30, 30, 20), ew.PadDesign("fixed", 100.0, slope_pct=2, flow_deg=90,
                                                             sides="walls"))
    assert t.cut == pytest.approx(20, rel=0.01) and t.fill == pytest.approx(20, rel=0.01)
    assert t.level_high == pytest.approx(100.2, abs=0.01) and t.level_low == pytest.approx(99.8, abs=0.01)
    assert "east" in t.headline()


def test_downhill_direction(tilted):
    t = ew.design_pad(tilted, square(30, 30, 20), ew.PadDesign("balanced", slope_pct=1, sides="walls"))
    assert t.flow_deg == pytest.approx(270, abs=0.5)      # ground rises east, water runs west


def test_haul_with_soil_factors():
    h = ew.make_haul(1000, 500, 25, 10)
    assert h.needed == pytest.approx(550)
    assert h.balance == pytest.approx(450)
    assert h.loose == pytest.approx(562.5)
    assert h.trips == [(6.0, 94), (10.0, 57)]
    imp = ew.make_haul(100, 500, 25, 10, extra_truck=12)
    assert imp.balance == pytest.approx(-450)
    assert [s for s, _ in imp.trips] == [6.0, 10.0, 12.0]
    assert "brought in" in imp.sentence()


def test_pile_takeoff_and_spot_levels(tmp_path):
    cell, n = 0.1, 600
    x, y = xy(n, cell)
    cx, cy = X0 + 30, Y0 - 30
    z = 100 + np.clip(10 - np.hypot(x - cx, y - cy) * 0.5, 0, None)
    path = write_dem(tmp_path / "cone.tif", z, cell)
    ring = [(cx + 25 * math.cos(a), cy + 25 * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 96, endpoint=False)]
    t = ew.pile_takeoff(path, ring)
    cone = math.pi * 400 * 10 / 3
    assert t.cut == pytest.approx(cone, rel=0.005)
    assert t.haul.trips[0] == (6.0, math.ceil(t.cut / 6))       # already loose: no swell
    assert "pile" in t.headline()
    rows = ew.grid_points(t, 5.0)
    top = [r for r in rows if abs(r[1] - cx) < 1e-6 and abs(r[2] - cy) < 1e-6]
    assert top and top[0][5] == pytest.approx(10, abs=0.05)     # 10 m to dig at the centre


def test_typed_points():
    utm = rasterio.crs.CRS.from_epsg(32651).to_wkt()
    pts = ew.parse_points("1, 500010, 1000050\n500030 1000050\n500030;1000020\n", "EPSG:32651", utm)
    assert pts == [(500010.0, 1000050.0), (500030.0, 1000050.0), (500030.0, 1000020.0)]
    ll = ew.parse_points("8°30'N, 123°00'E\n8.51, 123.01", "EPSG:4326", utm, swap=True)
    from pyproj import Transformer
    tx, ty = Transformer.from_crs(4326, 32651, always_xy=True).transform(123.0, 8.5)
    assert ll[0] == pytest.approx((tx, ty), abs=0.01)
    with pytest.raises(ValueError):
        ew.parse_points("hello", "EPSG:32651", utm)


def test_shape_from_kml_and_plan_roundtrip(flat, tmp_path):
    from shapely.geometry import Polygon
    from fieldortho.core import exporters as ex
    utm = rasterio.crs.CRS.from_epsg(32651).to_wkt()
    sq = square(30, 30, 20)
    ex.write_vector([ex.Feature(Polygon(sq), {"name": "Lot 7"})], str(tmp_path / "lot.kml"), "kml", "EPSG:32651",
                    "EPSG:4326", layer="lots", lonlat=(123.0, 9.0))
    shapes = ew.read_shapes(str(tmp_path / "lot.kml"), utm)
    assert shapes and shapes[0]["closed"] and shapes[0]["name"] == "Lot 7"
    got = np.array(shapes[0]["points"])
    for p in sq:
        assert np.min(np.hypot(*(got - p).T)) < 0.01
    plan = ew.plan_dict(type="pad", name="Lot 7 / pad", source="dtm", points=[list(p) for p in sq],
                        design=ew.PadDesign("fixed", 101.0, sides="walls"))
    job = tmp_path / "job"
    job.mkdir()
    p = ew.save_plan(str(job), plan)
    assert os.path.basename(p) == "Lot 7 _ pad.plan.json"
    back = ew.load_plan(p)
    t = ew.run_plan(back, flat)
    assert t.fill == pytest.approx(400, rel=0.005)


def test_export_package(tilted, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from fieldortho.core import earthworks_report as er
    pts = square(30, 30, 30)
    plan = ew.plan_dict(type="pad", name="Barangay hall pad", source="dtm", points=[list(p) for p in pts],
                        design=ew.PadDesign("balanced", slope_pct=1.0, cut_ratio=1, fill_ratio=1.5))
    t = ew.run_plan(plan, tilted)
    files = er.export_package(t, plan, tilted, None, str(tmp_path / "pkg"), "EPSG:32651",
                              {"project": "Test", "location": "Somewhere", "prepared_by": "Tester"},
                              gis_formats=("gpkg", "dxf"))
    names = {os.path.basename(f) for f in files}
    for n in ("Barangay_hall_pad_report.pdf", "Barangay_hall_pad_quantities.xlsx", "Barangay_hall_pad.gpkg",
              "Barangay_hall_pad_spot_levels.csv", "Barangay_hall_pad_dig_fill_depth_m.tif",
              "Barangay_hall_pad_finished_ground_m.tif", "Barangay_hall_pad_dig_fill_map.png",
              "Barangay_hall_pad.plan.json", "Barangay_hall_pad_README.txt"):
        assert n in names, n
    assert os.path.getsize(tmp_path / "pkg" / "Barangay_hall_pad_report.pdf") > 20_000
    import pyogrio
    layers = {l[0] for l in pyogrio.list_layers(str(tmp_path / "pkg" / "Barangay_hall_pad.gpkg"))}
    assert layers == {"outline", "cut_fill_zones", "spot_levels"}
    from openpyxl import load_workbook
    wb = load_workbook(tmp_path / "pkg" / "Barangay_hall_pad_quantities.xlsx")
    assert wb.sheetnames == ["Summary", "Inputs", "Spot levels", "Notes"]
    assert wb["Spot levels"].max_row > 30
    with rasterio.open(tmp_path / "pkg" / "Barangay_hall_pad_dig_fill_depth_m.tif") as ds:
        dz = ds.read(1, masked=True)
        cut = float(dz[dz > 0].sum() * abs(ds.transform.a * ds.transform.e))
    assert cut == pytest.approx(t.cut, rel=0.01)
