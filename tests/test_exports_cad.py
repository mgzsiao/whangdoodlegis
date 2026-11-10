"""LandXML, DXF drawings, survey comparison and 3-D models."""
import math
import os
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from fieldortho.core import cad, earthworks as ew, landxml, measure as ms, model3d, road as rdm

X0, Y0 = 500000.0, 1000200.0
NS = {"l": "http://www.landxml.org/schema/LandXML-1.2"}


def write_dem(path, z, cell, count=1, dtype="float32", nodata=-9999):
    with rasterio.open(path, "w", driver="GTiff", width=z.shape[-1], height=z.shape[-2], count=count, dtype=dtype,
                       crs="EPSG:32651", transform=from_origin(X0, Y0, cell, cell), nodata=nodata) as ds:
        ds.write(z.astype(dtype) if count > 1 else z.astype(dtype)[None] if z.ndim == 2 else z.astype(dtype))
    return str(path)


@pytest.fixture(scope="module")
def slope(tmp_path_factory):
    r, c = np.mgrid[0:600, 0:600]
    return write_dem(tmp_path_factory.mktemp("xs") / "slope.tif", 100 + 0.03 * c * 0.25 + 0.01 * r * 0.25, 0.25)


@pytest.fixture(scope="module")
def road_result(slope):
    pts = [(X0 + 20, Y0 - 120), (X0 + 60, Y0 - 40), (X0 + 130, Y0 - 50)]
    return rdm.design_road(slope, pts, rdm.RoadDesign(radii=[25], ditch=True)), pts


def test_landxml_road(road_result, slope, tmp_path):
    r, pts = road_result
    p = landxml.road_xml(str(tmp_path / "road.xml"), r, "Test road", slope)
    root = ET.parse(p).getroot()
    assert root.tag.endswith("LandXML")
    assert root.find("l:CoordinateSystem", NS).get("epsgCode") == "32651"
    faces = root.findall(".//l:Surface/l:Definition/l:Faces/l:F", NS)
    assert len(faces) > 1000
    al = root.find(".//l:Alignment", NS)
    assert float(al.get("length")) == pytest.approx(r.length, abs=1e-3)
    first = root.find(".//l:CoordGeom/l:Line/l:Start", NS).text.split()
    assert float(first[0]) == pytest.approx(pts[0][1]) and float(first[1]) == pytest.approx(pts[0][0])  # N E
    cv = root.find(".//l:CoordGeom/l:Curve", NS)
    assert float(cv.get("radius")) == pytest.approx(25, abs=1e-3)
    assert len(root.findall(".//l:ProfAlign/l:PVI", NS)) >= 2
    assert len(root.findall(".//l:CrossSect", NS)) == len(r.stations)


def test_landxml_pad(slope, tmp_path):
    sq = [(X0 + 40, Y0 - 40), (X0 + 80, Y0 - 40), (X0 + 80, Y0 - 80), (X0 + 40, Y0 - 80)]
    t = ew.design_pad(slope, sq, ew.PadDesign("balanced"))
    p = landxml.surfaces_xml(str(tmp_path / "pad.xml"), t, "Pad", slope)
    names = [s.get("name") for s in ET.parse(p).getroot().findall(".//l:Surface", NS)]
    assert names == ["Existing ground", "Finished ground"]


def test_dxf_drawings_read_back(road_result, slope, tmp_path):
    import pyogrio
    r, _ = road_result
    p = cad.road_dxf(str(tmp_path / "road.dxf"), r, "Test road")
    meta, _f, geoms, fields = pyogrio.raw.read(p)
    layers = set(fields[meta["fields"].tolist().index("Layer")])
    for lay in ("PLAN_CENTRE_LINE", "PLAN_STATIONS", "PLAN_CURVE_POINTS", "PROFILE_GROUND", "PROFILE_ROAD",
                "SECTIONS_GROUND", "SECTIONS_ROAD"):
        assert lay in layers, lay
    sq = [(X0 + 40, Y0 - 40), (X0 + 80, Y0 - 40), (X0 + 80, Y0 - 80), (X0 + 40, Y0 - 80)]
    t = ew.design_pad(slope, sq, ew.PadDesign("balanced"))
    p2 = cad.pad_dxf(str(tmp_path / "pad.dxf"), t, sq, ew.grid_points(t, 5), "Pad")
    meta, _f, geoms, fields = pyogrio.raw.read(p2)
    layers = set(fields[meta["fields"].tolist().index("Layer")])
    assert {"OUTLINE", "ZONE_DIG", "ZONE_FILL", "SPOT_LEVELS"} <= layers


def test_compare_surveys_lines_up_heights(tmp_path):
    cell, n = 0.2, 500
    r, c = np.mgrid[0:n, 0:n]
    x, y = (c + 0.5) * cell, (r + 0.5) * cell
    before = 50 + 0.02 * x
    cone = np.clip(5 - np.hypot(x - 50, y - 50) * 0.5, 0, None)       # 5 m high, 10 m radius
    after = before + cone + 0.5                                         # later flight sits 0.5 m higher
    b = write_dem(tmp_path / "b.tif", before, cell)
    a = write_dem(tmp_path / "a.tif", after, cell)
    ring = [(X0 + 50 + 14 * math.cos(t), Y0 - 50 + 14 * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 64,
                                                                                            endpoint=False)]
    v = math.pi * 100 * 5 / 3
    res = ms.compare_surveys(b, a, ring)
    assert res.offset == pytest.approx(0.5, abs=1e-3)
    assert res.raised_m3 == pytest.approx(v, rel=0.01)
    assert res.lowered_m3 < 1
    raw = ms.compare_surveys(b, a, ring, line_up=False)
    assert raw.raised_m3 == pytest.approx(v + 0.5 * raw.area.area_2d, rel=0.01)
    d = ms.results_dict("compare", res)
    assert d["change_m3"] == pytest.approx(res.net_m3, abs=0.01)


def test_3d_models(slope, tmp_path):
    rgb = np.zeros((3, 300, 300), np.uint8)
    rgb[0] = 200
    ortho = write_dem(tmp_path / "o.tif", rgb, 0.5, count=3, dtype="uint8", nodata=None)
    files = {"dsm": slope, "dtm": slope, "ortho": ortho}
    out = model3d.export_model(files, "photo", str(tmp_path / "3d"), "Site", detail="light", formats=("dae", "stl"))
    names = {os.path.basename(f) for f in out}
    assert {"Site_photo_model.dae", "Site_photo_model.stl", "Site_photo_model_texture.jpg",
            "Site_photo_model_README.txt"} <= names
    stl = str(tmp_path / "3d" / "Site_photo_model.stl")
    nf = int.from_bytes(open(stl, "rb").read()[80:84], "little")
    assert 80_000 < nf <= 100_000
    assert os.path.getsize(stl) == 84 + 50 * nf
    root = ET.parse(str(tmp_path / "3d" / "Site_photo_model.dae")).getroot()
    C = {"c": "http://www.collada.org/2005/11/COLLADASchema"}
    tri = root.find(".//c:triangles", C)
    assert int(tri.get("count")) == nf
    assert root.find(".//c:image/c:init_from", C).text == "Site_photo_model_texture.jpg"
    verts, faces, uv, origin, _ = model3d.grid_mesh(slope, (X0 + 10, Y0 - 60, X0 + 60, Y0 - 10), 20_000)
    assert abs(verts[:, 0]).max() < 40 and abs(verts[:, 1]).max() < 40       # moved near the origin
    assert verts[:, 2].min() >= 0
    n = np.cross(verts[faces[:, 1]] - verts[faces[:, 0]], verts[faces[:, 2]] - verts[faces[:, 0]])
    assert (n[:, 2] > 0).all()                                               # all facing up


def test_cli_plan_and_model3d(slope, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from fieldortho import cli
    from fieldortho.core import jobs
    job = jobs.Job(name="CLI site", job_dir=str(tmp_path / "job"), status=jobs.STATUS_DONE,
                   summary={"files": {"dtm": slope, "dsm": slope}})
    job.save()
    pts = [[X0 + 20, Y0 - 120], [X0 + 60, Y0 - 40], [X0 + 130, Y0 - 50]]
    plan = ew.plan_dict(type="road", name="CLI road", source="dtm", points=pts,
                        road=rdm.RoadDesign(radii=[25], standard="fmr"))
    path = ew.save_plan(job.job_dir, plan)
    assert cli.main(["plan", path, "--formats", "gpkg"]) == 0
    out = os.path.join(os.path.dirname(path), "CLI road")
    names = os.listdir(out)
    assert "CLI_road_report.pdf" in names and "CLI_road_civil3d.xml" in names and "CLI_road_drawing.dxf" in names
    assert cli.main(["model3d", job.job_dir, "--kind", "ground", "--detail", "light", "--formats", "stl"]) == 0
    assert os.path.exists(os.path.join(job.job_dir, "3D", "CLI_site_ground_model.stl"))
