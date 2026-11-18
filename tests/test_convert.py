"""Convert files: change the coordinate system of shapefiles and rasters (PRS92 -> WGS 84 etc.)."""
import os

import numpy as np
import pytest
import rasterio
import shapely
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, Polygon

from fieldortho.core import convert as cv, exporters as ex


def _read(path):
    from pyogrio.raw import read
    from pyproj import CRS
    meta, _f, wkb, cols = read(path)
    attrs = {n: list(cols[i]) for i, n in enumerate(meta["fields"])}
    return CRS.from_user_input(meta["crs"]), list(shapely.from_wkb(wkb)), attrs


def _features():
    # Cagayan de Oro area in PRS92 / Philippines zone V (EPSG:3125)
    x0, y0 = 475000.0, 940000.0
    return [ex.Feature(Polygon([(x0, y0), (x0 + 100, y0), (x0 + 100, y0 + 80), (x0, y0 + 80)]),
                       {"name": "Lot 1", "area_m2": 8000.0, "code": 7}),
            ex.Feature(Polygon([(x0 + 200, y0), (x0 + 260, y0), (x0 + 260, y0 + 50)]),
                       {"name": "Lot 2 (ñ)", "area_m2": 1500.0, "code": 8})]


def test_prs92_shapefile_to_wgs84(tmp_path):
    src = str(tmp_path / "lots.shp")
    ex.write_vector(_features(), src, "shp", "EPSG:3125", "EPSG:3125", layer="lots", lonlat=(124.6, 8.5))
    it = cv.inspect(src)
    assert it.kind == "vector" and it.count == 2 and it.source_code == "EPSG:3125", it
    files, notes = cv.convert(it, str(tmp_path / "out"), "EPSG:4326")
    out = [f for f in files if f.endswith(".shp")][0]
    assert os.path.basename(out) == "lots_EPSG4326.shp"
    assert any(f.endswith(".prj") for f in files) and any(f.endswith(".dbf") for f in files)
    assert any("7-parameter" in n for n in notes), notes
    crs, geoms, attrs = _read(out)
    assert crs.to_epsg() == 4326
    assert attrs["name"] == ["Lot 1", "Lot 2 (ñ)"] and [int(v) for v in attrs["code"]] == [7, 8]
    # official PRS92 -> WGS 84 (EPSG:15708 via EPSG:3125 -> 4326), centimetre agreement
    t = Transformer.from_crs("EPSG:3125", "EPSG:4326", always_xy=True)
    lon, lat = t.transform(475000.0, 940000.0)
    xs, ys = shapely.get_coordinates(geoms[0]).T
    d = np.hypot((xs - lon) * 110000, (ys - lat) * 110000)
    assert d.min() < 0.01, d
    # and back again within a few millimetres (the 7-parameter inverse is approximate)
    back, _ = cv.convert(cv.inspect(out), str(tmp_path / "back"), "EPSG:3125")
    g = _read([f for f in back if f.endswith(".shp")][0])[1][0]
    assert shapely.hausdorff_distance(g, _features()[0].geometry) < 0.005


def test_missing_prj_needs_crs(tmp_path):
    src = str(tmp_path / "noprj.shp")
    ex.write_vector(_features(), src, "shp", "EPSG:3125", "EPSG:3125", layer="x", lonlat=(124.6, 8.5))
    os.remove(str(tmp_path / "noprj.prj"))
    it = cv.inspect(src)
    assert it.source_code is None
    with pytest.raises(cv.NeedCrs):
        cv.convert(it, str(tmp_path / "out"), "EPSG:4326")
    it.crs_code = "EPSG:3125"
    files, _ = cv.convert(it, str(tmp_path / "out"), "EPSG:32651", fmt="gpkg")
    assert files == [str(tmp_path / "out" / "noprj_EPSG32651.gpkg")]


def test_esri_prs92_prj_is_identified():
    esri = ('PROJCS["PRS_1992_Philippines_Zone_V",GEOGCS["GCS_PRS_1992",DATUM["D_Philippine_Reference_System_1992",'
            'SPHEROID["Clarke_1866",6378206.4,294.9786982]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
            'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",500000.0],PARAMETER["False_Northing",0.0],'
            'PARAMETER["Central_Meridian",125.0],PARAMETER["Scale_Factor",0.99995],PARAMETER["Latitude_Of_Origin",0.0],'
            'UNIT["Meter",1.0]]')
    assert cv.normalise(esri) == "EPSG:3125"


def test_mixed_geometries_and_folder(tmp_path):
    f = [ex.Feature(Point(475000, 940000), {"k": "a"}), ex.Feature(LineString([(475000, 940000), (475100, 940050)]),
                                                                 {"k": "b"})]
    src = str(tmp_path / "in" / "mixed.gpkg")
    os.makedirs(tmp_path / "in")
    ex.write_vector(f[:1], src, "gpkg", "EPSG:3125", "EPSG:3125", layer="pts", lonlat=(124.6, 8.5))
    ex.write_vector(f[1:], src, "gpkg", "EPSG:3125", "EPSG:3125", layer="lines", lonlat=(124.6, 8.5), append=True)
    with rasterio.open(tmp_path / "in" / "dem.tif", "w", driver="GTiff", width=20, height=20, count=1,
                       dtype="float32", crs="EPSG:3125", transform=from_origin(475000, 940100, 5, 5)) as ds:
        ds.write(np.full((20, 20), 42.0, np.float32), 1)
    (tmp_path / "in" / "readme.txt").write_text("x")
    paths = cv.collect([str(tmp_path / "in")])
    assert [os.path.basename(p) for p in paths] == ["dem.tif", "mixed.gpkg"]
    it = cv.inspect(paths[1])
    assert sorted(it.layers) == ["lines", "pts"]
    files, _ = cv.convert(it, str(tmp_path / "out"), "EPSG:4326", fmt="shp")
    names = sorted(os.path.basename(x) for x in files if x.endswith(".shp"))
    assert names == ["mixed_lines_EPSG4326.shp", "mixed_pts_EPSG4326.shp"], names
    rfiles, _ = cv.convert(cv.inspect(paths[0]), str(tmp_path / "out"), "EPSG:32651")
    with rasterio.open(rfiles[0]) as ds:
        assert ds.crs.to_epsg() == 32651
        a = ds.read(1, masked=True)
        assert abs(float(a.mean()) - 42.0) < 1e-4
    p = cv.write_notes(str(tmp_path / "out"), "EPSG:4326", [("mixed.gpkg", files, ["n"])])
    assert "mixed_lines_EPSG4326.shp" in open(p, encoding="utf-8").read()


def test_luzon1911_mindanao_uses_mindanao_shift(tmp_path):
    # a point near Cagayan de Oro in Luzon 1911 lat/lon -> WGS 84 must use (-133,-79,-72)
    src = str(tmp_path / "old.geojson")
    import json
    json.dump({"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:4253"}},
               "features": [{"type": "Feature", "properties": {"n": 1},
                             "geometry": {"type": "Point", "coordinates": [124.65, 8.48]}}]},
              open(src, "w"))
    it = cv.inspect(src)
    assert it.source_code == "EPSG:4253"
    files, notes = cv.convert(it, str(tmp_path / "o"), "EPSG:4326", fmt="geojson")
    assert any("Mindanao parameters" in n for n in notes), notes
    from fieldortho.core import crs as crsmod
    lon, lat = crsmod.transform([124.65], [8.48], "EPSG:4253", "EPSG:4326", 124.65, 8.48)
    g = _read(files[0])[1][0]
    assert abs(g.x - lon[0]) < 1e-8 and abs(g.y - lat[0]) < 1e-8


# ---------------------------------------------------------------- Change SHP CRS (1.2): parts, zip, sub-folders
def _shp(folder, name, crs="EPSG:3125"):
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(str(folder), name + ".shp")
    ex.write_vector(_features(), p, "shp", crs, crs, layer=name, lonlat=(124.6, 8.5))
    return p


def test_any_part_brings_in_the_shapefile_and_parts_are_listed(tmp_path):
    p = _shp(tmp_path / "in", "lots")
    os.remove(str(tmp_path / "in" / "lots.cpg"))
    parts = [str(tmp_path / "in" / f"lots{e}") for e in (".dbf", ".prj", ".shx", ".shp")]
    assert cv.collect(parts) == [p]                              # four parts picked = one shapefile
    it = cv.inspect(p)
    assert it.parts == {".shp": True, ".shx": True, ".dbf": True, ".prj": True, ".cpg": False}
    assert it.source_code == "EPSG:3125" and it.how == "read from the .prj" and not it.notes


def test_subfolders_and_zip(tmp_path):
    import zipfile
    _shp(tmp_path / "in", "top")
    _shp(tmp_path / "in" / "deeper", "inner")
    assert [os.path.basename(p) for p in cv.collect([str(tmp_path / "in")])] == ["top.shp"]
    assert sorted(os.path.basename(p) for p in cv.collect([str(tmp_path / "in")], recursive=True)) == \
        ["inner.shp", "top.shp"]
    z = str(tmp_path / "pack.zip")
    with zipfile.ZipFile(z, "w") as zf:
        for n in os.listdir(tmp_path / "in"):
            if os.path.isfile(tmp_path / "in" / n):
                zf.write(tmp_path / "in" / n, "data/" + n)
    found = cv.collect([z])
    assert len(found) == 1 and found[0].startswith("/vsizip/") and found[0].endswith("data/top.shp"), found
    it = cv.inspect(found[0])
    assert it.in_zip and it.name == "top.shp" and it.count == 2 and it.source_code == "EPSG:3125"
    assert it.parts[".dbf"] and it.parts[".prj"]
    files, _ = cv.convert(it, str(tmp_path / "out"), "EPSG:4326")
    crs, geoms, attrs = _read([f for f in files if f.endswith(".shp")][0])
    assert crs.to_epsg() == 4326 and attrs["name"] == ["Lot 1", "Lot 2 (ñ)"]


def test_missing_shx_is_rebuilt_and_missing_dbf_keeps_shapes(tmp_path):
    p = _shp(tmp_path / "in", "noshx")
    os.remove(str(tmp_path / "in" / "noshx.shx"))
    it = cv.inspect(p)
    assert not it.error and it.parts[".shx"] is False and it.count == 2
    assert any("rebuilt" in n for n in it.notes)
    assert not os.path.exists(str(tmp_path / "in" / "noshx.shx"))      # the user's folder is left alone
    files, notes = cv.convert(it, str(tmp_path / "out"), "EPSG:4326")
    assert any("rebuilt" in n for n in notes)
    assert len(_read([f for f in files if f.endswith(".shp")][0])[1]) == 2
    tmp = it.tmpdir
    cv.cleanup(it)
    assert not os.path.exists(tmp)

    q = _shp(tmp_path / "in2", "nodbf")
    os.remove(str(tmp_path / "in2" / "nodbf.dbf"))
    it = cv.inspect(q)
    assert not it.error and it.count == 2 and any("without attributes" in n for n in it.notes)


def test_detection_explains_how():
    esri = ('PROJCS["Luzon_1911_Philippines_Zone_V",GEOGCS["GCS_Luzon_1911",DATUM["D_Luzon_1911",'
            'SPHEROID["Clarke_1866",6378206.4,294.9786982]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
            'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",500000.0],PARAMETER["False_Northing",0.0],'
            'PARAMETER["Central_Meridian",125.0],PARAMETER["Scale_Factor",0.99995],PARAMETER["Latitude_Of_Origin",0.0],'
            'UNIT["Meter",1.0]]')
    code, how = cv.identify(esri)
    assert code == "EPSG:25395" and how                      # by pyproj or by the ESRI name, same code
    assert cv.identify("EPSG:32651") == ("EPSG:32651", "read from the file")
