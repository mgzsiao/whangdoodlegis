"""Accuracy of the Measure tools against hand calculations on synthetic terrain."""
import math
import os

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from fieldortho.core import measure as ms

X0, Y0 = 500000.0, 1000100.0


def write_dem(path, z, cell):
    with rasterio.open(path, "w", driver="GTiff", width=z.shape[1], height=z.shape[0], count=1, dtype="float32",
                       crs="EPSG:32651", transform=from_origin(X0, Y0, cell, cell), nodata=-9999) as ds:
        ds.write(z.astype("float32"), 1)
    return str(path)


def grid(n, cell):
    """Map x, y of cell centres for an n x n grid."""
    r, c = np.mgrid[0:n, 0:n]
    return X0 + (c + 0.5) * cell, Y0 - (r + 0.5) * cell


def circle(cx, cy, rad, n=128):
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return [(cx + rad * math.cos(t), cy + rad * math.sin(t)) for t in a]


@pytest.fixture(scope="module")
def cone(tmp_path_factory):
    # cone 10 m high, 20 m radius, on flat ground at 100 m; 0.1 m cells, 60 x 60 m
    cell, n = 0.1, 600
    x, y = grid(n, cell)
    cx, cy = X0 + 30, Y0 - 30
    r = np.hypot(x - cx, y - cy)
    z = 100 + np.clip(10 - r * 0.5, 0, None)
    return write_dem(tmp_path_factory.mktemp("cone") / "cone.tif", z, cell), (cx, cy)


CONE_V = math.pi * 20 ** 2 * 10 / 3        # 4188.79 m3


@pytest.mark.parametrize("ref", ["edge", "edge_mean", "lowest"])
def test_cone_volume_matches_formula(cone, ref):
    path, (cx, cy) = cone
    v = ms.measure_volume(path, circle(cx, cy, 25), ref)
    assert v.cut_m3 == pytest.approx(CONE_V, rel=0.005)
    assert v.fill_m3 < 0.5
    assert v.net_m3 == pytest.approx(CONE_V, rel=0.005)
    assert v.max_cut == pytest.approx(10, abs=0.1)


def test_cone_fixed_level_adds_slab(cone):
    path, (cx, cy) = cone
    pts = circle(cx, cy, 25)
    v = ms.measure_volume(path, pts, "level", level=95.0)
    from shapely.geometry import Polygon
    slab = Polygon(pts).area * 5
    assert v.cut_m3 == pytest.approx(CONE_V + slab, rel=0.005)
    assert v.fill_m3 == 0
    hi = ms.measure_volume(path, pts, "level", level=110.0)       # everything below the level: fill
    assert hi.cut_m3 == 0
    assert hi.fill_m3 == pytest.approx(Polygon(pts).area * 10 - CONE_V, rel=0.005)


def test_volume_still_right_when_thinned_out(cone):
    path, (cx, cy) = cone
    v = ms.measure_volume(path, circle(cx, cy, 25), "edge", max_cells=40_000)   # ~0.5 m cells
    assert v.cut_m3 == pytest.approx(CONE_V, rel=0.02)


@pytest.fixture(scope="module")
def plane(tmp_path_factory):
    # tilted plane rising 0.2 m per metre eastwards, 0.25 m cells, 100 x 100 m
    cell, n = 0.25, 400
    x, y = grid(n, cell)
    z = 50 + 0.2 * (x - X0)
    return write_dem(tmp_path_factory.mktemp("plane") / "plane.tif", z, cell)


def square(x0, y0, s):
    return [(X0 + x0, Y0 - y0), (X0 + x0 + s, Y0 - y0), (X0 + x0 + s, Y0 - y0 - s), (X0 + x0, Y0 - y0 - s)]


def test_edge_surface_follows_a_plane(plane):
    v = ms.measure_volume(plane, square(10, 10, 20), "edge")
    assert v.cut_m3 < 1 and v.fill_m3 < 1


def test_level_pad_on_slope_cut_equals_fill(plane):
    # level at the plane height of the square's centre (x = 20 m): cut = fill = 0.2 * 20 * 10^2 / 2
    v = ms.measure_volume(plane, square(10, 10, 20), "level", level=54.0)
    assert v.cut_m3 == pytest.approx(200, rel=0.01)
    assert v.fill_m3 == pytest.approx(200, rel=0.01)
    assert abs(v.net_m3) < 2
    assert v.total_m3 == pytest.approx(400, rel=0.01)
    assert v.cut_area == pytest.approx(200, rel=0.02)


def test_area_perimeter_and_surface_area(plane):
    a = ms.measure_area(plane, square(10, 10, 20))
    assert a.area_2d == pytest.approx(400)
    assert a.perimeter_2d == pytest.approx(80)
    assert a.area_3d == pytest.approx(400 * math.sqrt(1 + 0.2 ** 2), rel=0.002)
    assert a.z_min == pytest.approx(52, abs=0.2) and a.z_max == pytest.approx(56, abs=0.2)
    # perimeter along the ground: two level sides (north/south slope 0.2) + two flat east/west sides
    assert a.perimeter_3d == pytest.approx(2 * 20 * math.sqrt(1.04) + 2 * 20, rel=0.002)


@pytest.fixture(scope="module")
def slope30(tmp_path_factory):
    cell, n = 0.1, 500
    x, y = grid(n, cell)
    z = 10 + (x - X0) * math.tan(math.radians(30))
    return write_dem(tmp_path_factory.mktemp("s30") / "s30.tif", z, cell)


def test_path_on_30_degree_slope(slope30):
    a, b = (X0 + 5, Y0 - 25), (X0 + 45, Y0 - 25)
    p = ms.measure_path(slope30, [a, b])
    assert p.length_2d == pytest.approx(40)
    assert p.length_3d == pytest.approx(40 / math.cos(math.radians(30)), rel=0.002)
    assert p.steepest_pct == pytest.approx(100 * math.tan(math.radians(30)), rel=0.01)
    assert p.climb == pytest.approx(40 * math.tan(math.radians(30)), rel=0.01)
    assert p.descent == pytest.approx(0, abs=0.01)
    back = ms.measure_path(slope30, [b, a])
    assert back.descent == pytest.approx(p.climb, rel=0.001)


def test_point_and_height_difference(slope30):
    t = math.tan(math.radians(30))
    assert ms.point_elevation(slope30, X0 + 12.34, Y0 - 20) == pytest.approx(10 + 12.34 * t, abs=0.01)
    h = ms.height_between(slope30, (X0 + 5, Y0 - 5), (X0 + 35, Y0 - 45))
    assert h.horizontal == pytest.approx(50)
    assert h.rise == pytest.approx(30 * t, abs=0.01)
    assert h.slope_pct == pytest.approx(100 * 30 * t / 50, abs=0.05)


def test_outside_and_bad_shapes(slope30):
    with pytest.raises(ValueError):
        ms.point_elevation(slope30, X0 - 500, Y0)
    with pytest.raises(ValueError):
        ms.measure_area(slope30, [(X0 + 1, Y0 - 1), (X0 + 2, Y0 - 2)])
    with pytest.raises(ValueError):
        ms.measure_volume(slope30, square(1, 1, 5), "level")


def test_truck_trips():
    loose, trips = ms.truck_trips(2382, 25)
    assert loose == pytest.approx(2977.5)
    assert trips == [(6.0, 497), (10.0, 298)]
    assert ms.truck_trips(-50)[1] == [(6.0, 0), (10.0, 0)]
    assert ms.truck_trips(60, 0, (6.0,))[1] == [(6.0, 10)]


def test_save_load_and_export(cone, tmp_path):
    path, (cx, cy) = cone
    v = ms.measure_volume(path, circle(cx, cy, 25, 16), "edge")
    p = ms.measure_path(path, [(cx - 20, cy), (cx + 20, cy)])
    items = [ms.Measurement("volume", "Pile A", [list(q) for q in circle(cx, cy, 25, 16)],
                            results=ms.results_dict("volume", v)),
             ms.Measurement("path", "Across", [[cx - 20, cy], [cx + 20, cy]], results=ms.results_dict("path", p)),
             ms.Measurement("point", "Top", [[cx, cy]], results=ms.results_dict("point", 110.0))]
    ms.save_all(str(tmp_path), items)
    back = ms.load_all(str(tmp_path))
    assert [m.name for m in back] == ["Pile A", "Across", "Top"]
    assert back[0].results["net_m3"] == pytest.approx(CONE_V, rel=0.01)
    files = ms.export_measurements(back, str(tmp_path / "out"), "site", "EPSG:32651", "EPSG:4326",
                                   ["gpkg", "kml"], (123.0, 9.0), "test")
    names = sorted(os.path.basename(f) for f in files)
    assert "site.gpkg" in names and "site_measurements.csv" in names and "site_areas.kml" in names
    import pyogrio
    layers = {l[0] for l in pyogrio.list_layers(str(tmp_path / "out" / "site.gpkg"))}
    assert layers == {"points", "lines", "areas"}
    csv_text = open(tmp_path / "out" / "site_measurements.csv", encoding="utf-8-sig").read()
    assert "Pile A" in csv_text and "POLYGON" in csv_text
