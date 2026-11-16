"""Pointing the grid by hand: click fine-tuning and suggested values, fit through core.georef."""
import numpy as np

from fieldortho.core import georef as G, georef_manual as GM


def _grid_image(w=1200, h=900, step=200, off=(103, 77), width=3):
    img = np.full((h, w, 3), 245, np.uint8)
    for x in range(off[0], w, step):
        img[:, x - width // 2: x + width // 2 + 1] = 30
    for y in range(off[1], h, step):
        img[y - width // 2: y + width // 2 + 1, :] = 30
    return img


def test_click_is_moved_onto_the_crossing():
    img = _grid_image()
    # true crossing centre: column 303 -> 303.5 in pixel-edge coordinates
    r = GM.refine_crossing(img, 299.2, 283.9)
    assert r is not None
    assert abs(r[0] - 303.5) < 0.6 and abs(r[1] - 277.5) < 0.6, r


def test_no_crossing_leaves_click_alone():
    img = _grid_image()
    assert GM.refine_crossing(img, 200.0, 180.0) is None          # blank paper between lines
    # only a vertical line near the click: no crossing
    assert GM.refine_crossing(img, 303.0, 180.0) is None


def test_manual_crossings_fit_and_suggest():
    img = _grid_image()
    step_deg = 1 / 60                                              # 1 minute grid
    lon0, lat0 = 124 + 47 / 60, 8 + 53 / 60
    gcps = []
    for i, j in ((0, 0), (5, 0), (0, 4), (5, 4), (2, 2)):
        x, y = 103.5 + 200 * i, 77.5 + 200 * j
        sx, sy = GM.refine_crossing(img, x + 3, y - 2)
        gcps.append(G.GCP(sx, sy, lon0 + i * step_deg, lat0 - j * step_deg, True, "crossing"))
    f = G.fit(gcps, "EPSG:4253", 1)
    assert f.rmse_m < 1.0
    lon, lat = GM.suggest(f, 703.5 + 4, 477.5 - 3, "EPSG:4253", step_deg)
    assert abs(lon - (lon0 + 3 * step_deg)) < 1e-9 and abs(lat - (lat0 - 2 * step_deg)) < 1e-9
