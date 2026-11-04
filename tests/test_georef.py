"""Georeferencer accuracy tests on six MGB Region 10 geohazard maps of Salay, Misamis Oriental
(supplied by the user). Grid labels were read by eye from the maps; datum Luzon 1911."""
import math
import os

import numpy as np
import pytest
import rasterio

from fieldortho.core import crs as C, georef as G, mapclasses as M
from fieldortho.core.georef_export import GeorefExport, export

D = os.path.join(os.path.dirname(__file__), "data", "maps")
MAPS = {  # file: (minutes of the 3 meridians at 124 deg, minutes of the 3 parallels at 8 deg, preset)
    "poblacion_flood.jpg": ((47, 48, 49), (52, 51, 50), "mgb_flood"),
    "poblacion_landslide.jpg": ((47, 48, 49), (52, 51, 50), "mgb_landslide"),
    "matampa_flood.jpg": ((50, 51, 52), (53, 52, 51), "mgb_flood"),
    "matampa_landslide.jpg": ((50, 51, 52), (53, 52, 51), "mgb_landslide"),
    "ampenican_flood.jpg": ((47, 48, 49), (55, 54, 53), "mgb_flood"),
    "ampenican_landslide.jpg": ((47, 48, 49), (55, 54, 53), "mgb_landslide"),
}
have = pytest.mark.skipif(not os.path.isdir(D), reason="map images not present")


def prepared(name):
    rgb = G.load_rgb(os.path.join(D, name))
    det = G.detect(rgb)
    lons, lats, _ = MAPS[name]
    for m, v in zip(det.meridians, lons):
        m.value = 124 + v / 60
    for p, v in zip(det.parallels, lats):
        p.value = 8 + v / 60
    return rgb, det


@have
@pytest.mark.parametrize("name", list(MAPS))
def test_detection_and_fit(name):
    rgb, det = prepared(name)
    assert len(det.meridians) == 3 and len(det.parallels) == 3
    assert len(det.crosses) >= 4
    assert G.tick_cross_agreement(det) < 1.0                  # px
    f = G.fit(G.make_gcps(det), "EPSG:4253", 1)
    assert f.rmse_m < 1.0                                     # metres (pixel = 3.25 m)
    assert 3.2 < f.pixel_m < 3.3                              # 1:10,000 map at this scan size
    # map rotation in UTM 51N must equal the meridian convergence at 124.8E, ~8.9N (0.277 deg)
    lon, lat = f.lonlat
    gamma = math.degrees(math.atan(math.tan(math.radians(lon - 123)) * math.sin(math.radians(lat))))
    assert abs(G.rotation_deg(f) - gamma) < 0.03


@have
def test_adjacent_quadrangles_line_up(tmp_path):
    """Poblacion (south) and Ampenican (north) overlap by ~190 m: after georeferencing both
    independently the shared strip must coincide."""
    outs = {}
    for name in ("poblacion_flood.jpg", "ampenican_flood.jpg"):
        rgb, det = prepared(name)
        f = G.fit(G.make_gcps(det), "EPSG:4253", 1, dst_crs="EPSG:32651")
        p = str(tmp_path / (name + ".tif"))
        G.warp_image(rgb, f, "EPSG:32651", p, crop=det.frame_corners_original(), pixel_size=1.0)
        outs[name] = p
    with rasterio.open(outs["poblacion_flood.jpg"]) as a, rasterio.open(outs["ampenican_flood.jpg"]) as b:
        l, r = max(a.bounds.left, b.bounds.left), min(a.bounds.right, b.bounds.right)
        bt, tp = max(a.bounds.bottom, b.bounds.bottom), min(a.bounds.top, b.bounds.top)
        A = a.read(window=rasterio.windows.from_bounds(l, bt, r, tp, a.transform).round_offsets().round_lengths())
        B = b.read(window=rasterio.windows.from_bounds(l, bt, r, tp, b.transform).round_offsets().round_lengths())
    h, w = min(A.shape[1], B.shape[1]), min(A.shape[2], B.shape[2])
    A, B = A[:, :h, :w].astype(float), B[:, :h, :w].astype(float)
    both = (A[3] > 0) & (B[3] > 0)
    ga = (255 - A[:3].max(0)) * both
    gb = (255 - B[:3].max(0)) * both
    R = np.fft.fft2(ga - ga.mean()) * np.conj(np.fft.fft2(gb - gb.mean()))
    c = np.fft.ifft2(R / (np.abs(R) + 1e-9)).real
    iy, ix = np.unravel_index(np.argmax(c), c.shape)
    iy = iy - h if iy > h // 2 else iy
    ix = ix - w if ix > w // 2 else ix
    assert abs(ix) <= 2 and abs(iy) <= 2                       # metres (source pixel 3.25 m)


@have
def test_export_places_grid_cross_at_true_wgs84_position(tmp_path):
    """End-to-end: exported WGS 84 GeoTIFF; the 124d48' / 8d51' (Luzon 1911) cross must appear at
    the WGS 84 position given by the Mindanao datum transformation."""
    rgb, det = prepared("poblacion_flood.jpg")
    files, info = export(rgb, det, G.make_gcps(det), "EPSG:4253",
                         GeorefExport(out_dir=str(tmp_path), name="pob", crs="EPSG:4326", resampling="cubic"))
    tif = [f for f in files if f.endswith("_georef.tif")][0]
    lon_l, lat_l = 124 + 48 / 60, 8 + 51 / 60
    t = C.transformer("EPSG:4253", "EPSG:4326", lon_l, lat_l)
    lon, lat = t.transform(lon_l, lat_l)
    with rasterio.open(tif) as ds:
        arr = ds.read([1, 2, 3])
        row, col = ds.index(lon, lat)
        res_m = ds.res[0] * 111320 * math.cos(math.radians(lat))
    dk = 255 - arr.max(axis=0).astype(np.int16)
    found = G.find_cross(dk, col, row, search=8)
    assert found is not None
    err_px = math.hypot(found[0] - col, found[1] - row)
    assert err_px * res_m < 3.0                                # metres
    # and the wrong (non-Mindanao) transformation would be ~20 m away
    wrong = C.resolve("EPSG:4253", 121.0, 15.0)                # parameters for Luzon island
    from pyproj import Transformer
    lw, aw = Transformer.from_crs(wrong, "EPSG:4326", always_xy=True).transform(lon_l, lat_l)
    assert math.hypot((lw - lon) * 110000, (aw - lat) * 110000) > 15


@have
@pytest.mark.parametrize("name", list(MAPS))
def test_class_extraction_matches_fill_colours(name):
    rgb, det = prepared(name)
    preset = M.PRESETS[MAPS[name][2]]
    region = M.region_mask(rgb.shape, det.frame_corners_original())
    codes = M.classify(rgb, preset, region)
    img = rgb.astype(np.int16)
    tot = agree = 0
    for mc in preset.classes:
        if mc.code == M.NONE_CODE and preset.white_is_unknown:
            continue
        for col in mc.colors:
            m = (np.abs(img - np.array(col)).max(axis=2) < 8) & region
            tot += m.sum()
            agree += (m & ((codes == mc.code) | ((codes == 5) & (mc.code in (2, M.NONE_CODE))))).sum()
    assert agree / tot > 0.998
    assert ((codes == 0) & region).sum() == 0


def test_angle_parsing():
    assert G.parse_angle("124°47'0\"E") == pytest.approx(124 + 47 / 60)
    assert G.parse_angle("8 52 30 N") == pytest.approx(8 + 52.5 / 60)
    assert G.parse_angle("-8.5") == -8.5
    assert G.parse_angle("124°47.5'") == pytest.approx(124 + 47.5 / 60)
    assert G.parse_interval("1'") == pytest.approx(1 / 60)
    assert G.parse_interval('30"') == pytest.approx(1 / 120)
    with pytest.raises(ValueError):
        G.parse_angle("8°75'N")


def test_luzon1911_datum_choice():
    assert C.in_mindanao(124.79, 8.86)       # Salay
    assert not C.in_mindanao(123.9, 10.3)    # Cebu
    assert C.in_mindanao(124.7, 9.2)         # Camiguin
    assert "-79" in C.resolve("EPSG:4253", 124.79, 8.86).to_wkt()
    assert "-77" in C.resolve("EPSG:4253", 121.0, 14.6).to_wkt()
