"""1.2 report (ReportLab, B + C style): formats, the paper rules for drawing sheets, sheet splitting and the PDF."""
import math
import os
import re

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from fieldortho.core import drawing_sheets as dsh, earthworks as ew, linear_report as lr, road as rdm, road_report as rr
from fieldortho.core.report_style import PAPERS, dms, kst

X0, Y0 = 500000.0, 950000.0


def _dem(path, W=700, H=300):
    r_, c_ = np.mgrid[0:H, 0:W]
    x, y = X0 + c_ + 0.5, Y0 - r_ - 0.5
    z = 300 + 0.012 * (x - X0) + 3 * np.sin((x - X0) / 80) + 2 * np.cos((y - Y0) / 50)
    with rasterio.open(path, "w", driver="GTiff", width=W, height=H, count=1, dtype="float32", crs="EPSG:32651",
                       transform=from_origin(X0, Y0, 1.0, 1.0), nodata=-9999) as ds:
        ds.write(z.astype("float32"), 1)
    return str(path)


def _road(dem, length_pts):
    rd = rdm.RoadDesign(**{k: v for k, v in rdm.STANDARDS["fmr_low"].items()
                           if k in rdm.RoadDesign.__dataclass_fields__ and k not in ("speed", "label")})
    rd.standard, rd.vcurves, rd.speed, rd.interval = "fmr_low", True, 30, 20.0
    rd.radii = [40.0] * (len(length_pts) - 2)
    plan = ew.plan_dict(type="road", name="Test road", source="dtm", points=[list(p) for p in length_pts], road=rd)
    return ew.run_plan(plan, dem), plan


def test_station_and_dms_formats():
    assert kst(0) == "K0+000.000"
    assert kst(60) == "K0+060.000"
    assert kst(1234.5678) == "K1+234.568"
    assert dms(8.5, "N", "S") == "8°30′00.00″ N"
    assert dms(-124.25, "E", "W") == "124°15′00.00″ W"
    assert dms(8.9999999, "N", "S") == "9°00′00.00″ N"


def _xs(width, height):
    x = dsh.XS(station=None)
    x.left, x.right, x.zmin, x.zmax = -width / 2 + 1.25, width / 2 - 1.25, 0.0, height - 0.8
    return x


def test_cross_section_layout_rules():
    # small sections: four per column on A3 at 1:200 or larger
    rows, den = dsh.choose_xs_layout([_xs(20, 3)], PAPERS["a4_a3"])
    assert rows == 4 and den <= 200
    # a wide section that needs smaller than 1:200 with four per column: three per column on A3
    rows, den = dsh.choose_xs_layout([_xs(20, 3), _xs(60, 4)], PAPERS["a4_a3"])
    assert rows == 3
    # A4: four per column only down to 1:150, else three down to 1:250, else two
    rows, den = dsh.choose_xs_layout([_xs(15, 2)], PAPERS["a4"])
    assert rows == 4 and den <= 150
    rows, den = dsh.choose_xs_layout([_xs(30, 4)], PAPERS["a4"])
    assert rows == 3 and den <= 250
    # wider than 1:250 allows across half an A4 sheet: three per column at the scale the width needs
    assert dsh.choose_xs_layout([_xs(40, 4)], PAPERS["a4"]) == (3, 400)
    # very tall sections: two per column (larger scale than three would allow)
    rows, _den = dsh.choose_xs_layout([_xs(40, 18)], PAPERS["a4"])
    assert rows == 2


def test_long_road_splits_at_match_lines(tmp_path):
    dem = _dem(tmp_path / "dem.tif")
    r, _plan = _road(dem, [(X0 + 30, Y0 - 150), (X0 + 330, Y0 - 100), (X0 + 670, Y0 - 160)])
    assert r.length > 600
    for key, scale in (("a4_a3", 1250), ("a4", 2000)):
        ctx = dsh.Ctx(PAPERS[key], "Test", "", False, rdm.design_from(_plan), lr.end_points(r, "EPSG:3125", False))
        sheets = dsh.plan_profile_sheets(r, ctx)
        W = PAPERS[key].sheet[0]
        avail_m = (W - 16 * dsh.mm - 45 * dsh.mm) / dsh.mm * scale / 1000
        assert len(sheets) == math.ceil(r.length / (math.floor(avail_m / 50) * 50) - 1e-9), key
        ranges = [re.findall(r"K(\d)\+(\d{3}\.\d{3})", t) for _fn, t in sheets]
        starts = [int(a[0][0]) * 1000 + float(a[0][1]) for a in ranges]
        assert starts[0] == 0 and all(abs(s % 50) < 1e-6 for s in starts)      # match lines at round stations


def test_short_road_one_sheet_at_a_larger_scale(tmp_path):
    dem = _dem(tmp_path / "dem.tif")
    r, plan = _road(dem, [(X0 + 30, Y0 - 150), (X0 + 150, Y0 - 120)])
    ctx = dsh.Ctx(PAPERS["a4_a3"], "Test", "", False, rdm.design_from(plan), lr.end_points(r, "EPSG:3125", False))
    assert len(dsh.plan_profile_sheets(r, ctx)) == 1


def test_end_points_coordinates(tmp_path):
    from pyproj import Transformer
    dem = _dem(tmp_path / "dem.tif")
    r, _plan = _road(dem, [(X0 + 30, Y0 - 150), (X0 + 150, Y0 - 120)])
    b, e = lr.end_points(r, "EPSG:3125", False)
    t = Transformer.from_crs("EPSG:32651", "EPSG:3125", always_xy=True)
    east, north = t.transform(X0 + 30, Y0 - 150)
    assert (b.name, e.name) == ("BEGIN", "END")
    assert b.east == pytest.approx(east, abs=1e-3) and b.north == pytest.approx(north, abs=1e-3)
    lon, lat = Transformer.from_crs("EPSG:32651", "EPSG:4326", always_xy=True).transform(X0 + 150, Y0 - 120)
    assert e.lat == pytest.approx(lat, abs=1e-9) and e.lon == pytest.approx(lon, abs=1e-9)
    assert "EL" in b.lines(True)[-1] and len(b.lines(False)) == 5


def test_road_sections_labels_and_layers(tmp_path):
    dem = _dem(tmp_path / "dem.tif")
    r, plan = _road(dem, [(X0 + 30, Y0 - 150), (X0 + 150, Y0 - 120)])
    xss = dsh.road_sections(r, rdm.design_from(plan))
    assert xss[0].mark == "BEGINNING" and xss[-1].mark == "END"
    assert [x.number for x in xss] == list(range(1, len(xss) + 1))
    for x in xss:
        us = [u for u, _z in x.labels]
        assert 0.0 in us and min(us) == pytest.approx(x.left, abs=1e-3) and max(us) == pytest.approx(x.right,
                                                                                                         abs=1e-3)
        kinds = {k for k, _p in x.layers}
        assert {"pccp", "subbase", "shoulder"} <= kinds
        # the centre label is the finished level at the centre line
        assert dict(x.labels)[0.0] == pytest.approx(round(x.station.design, 3), abs=1e-3)


def _pages(pdf):
    raw = open(pdf, "rb").read()
    boxes = re.findall(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", raw)
    return [(round(float(w)), round(float(h))) for w, h in boxes]


@pytest.mark.parametrize("paper,portrait,sheet", [("a4_a3", (595, 842), (1191, 842)),
                                                  ("a4", (595, 842), (842, 595)),
                                                  ("long", (612, 936), (936, 612))])
def test_report_pages_and_papers(tmp_path, paper, portrait, sheet):
    dem = _dem(tmp_path / "dem.tif")
    r, plan = _road(dem, [(X0 + 30, Y0 - 150), (X0 + 150, Y0 - 120), (X0 + 260, Y0 - 160)])
    info = {"project": "Test road", "paper": paper, "report_no": "T-1", "show_elev": True,
            "signatories": [("Prepared by", "A. Engineer", "Civil Engineer"), ("Approved by", "", "")]}
    files = rr.export_road(r, plan, dem, None, str(tmp_path / "out"), "EPSG:3125", info, None, ("kml",))
    pdf = [f for f in files if f.endswith("_report.pdf")][0]
    pages = _pages(pdf)
    ctx = dsh.Ctx(PAPERS[paper], "Test", "", False, rdm.design_from(plan), lr.end_points(r, "EPSG:3125", False))
    n_sheets = len(dsh.plan_profile_sheets(r, ctx)) + len(dsh.xs_sheets(dsh.road_sections(r, ctx.design), ctx))
    assert pages.count(sheet) == n_sheets
    # cover, contents, sections 1-6 and annexes A-C are portrait (section 3 may run onto a second page)
    assert pages.count(portrait) >= 11 and set(pages) == {portrait, sheet}
    assert pages[0] == portrait and pages[-1] == portrait


def test_lot_and_pile_reports(tmp_path):
    """The lot / pad and pile packages use the same report style: A4 report pages and landscape drawing sheets."""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    import synthetic_site as site
    from fieldortho.core import earthworks_report as er
    jd = site.synthetic_job(str(tmp_path / "jobs"))
    X, Y = site.X0, site.Y0
    pad = [(X + 10, Y - 60), (X + 40, Y - 58), (X + 42, Y - 90), (X + 12, Y - 92)]
    d = ew.PadDesign(level_mode="balanced", sides="slopes", cut_ratio=1.5, fill_ratio=2.0)
    for kind, pts, kw, src in (("pad", pad, {"design": d}, "dtm"),
                               ("pile", [(X + 25, Y - 25), (X + 75, Y - 25), (X + 75, Y - 75), (X + 25, Y - 75)],
                                {"pile": {"reference": "edge", "swell_pct": 25}}, "dsm")):
        plan = ew.plan_dict(type=kind, name=f"Test {kind}", source=src, points=[list(p) for p in pts], **kw)
        dem = os.path.join(jd, "GIS", f"site_{src.upper()}.tif")
        t = ew.run_plan(plan, dem)
        files = er.export_package(t, plan, dem, None, str(tmp_path / kind), "EPSG:3125",
                                  {"project": "Test", "paper": "a4", "show_elev": True}, None, ("kml",))
        pages = _pages([f for f in files if f.endswith("_report.pdf")][0])
        assert pages[0] == (595, 842) and (842, 595) in pages
        sheets = pages.count((842, 595))
        assert sheets == (1 if kind == "pile" else 1 + len(dsh.xs_sheets(
            dsh.lot_sections(__import__("fieldortho.core.pad_sections", fromlist=["x"]).sections(t, pts, d)[0]),
            dsh.Ctx(PAPERS["a4"], "", "", False, None, [], lot=True))))
    hdr, rows, _g = __import__("fieldortho.core.lot_report", fromlist=["x"]).corner_rows(t, pts, "EPSG:3125", True)
    assert len(rows) == 4 and hdr[-1] == "Finished (m)"
