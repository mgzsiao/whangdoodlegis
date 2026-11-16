"""'Use my own files': checks, conversion to metres, grouping in the Jobs list."""
import os

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path


def _dem(path, crs, transform, z, nodata=-9999.0):
    with rasterio.open(path, "w", driver="GTiff", width=z.shape[1], height=z.shape[0], count=1,
                       dtype="float32", crs=crs, transform=transform, nodata=nodata) as ds:
        ds.write(z.astype("float32"), 1)
    return str(path)


def _ortho(path, crs, transform, shape):
    with rasterio.open(path, "w", driver="GTiff", width=shape[1], height=shape[0], count=3,
                       dtype="uint8", crs=crs, transform=transform) as ds:
        ds.write(np.full((3,) + shape, 120, np.uint8))
    return str(path)


def test_utm_set_uses_files_in_place(cfg):
    from fieldortho.core import activity, filesets as fsm
    tr = from_origin(700000, 980000, 1.0, 1.0)
    z = 100 + np.add.outer(np.arange(50), np.arange(60)) * 0.1
    dtm = _dem(cfg / "dtm.tif", "EPSG:32651", tr, z)
    ortho = _ortho(cfg / "ortho.tif", "EPSG:32651", from_origin(700000, 980000, 0.5, 0.5), (100, 120))
    fs = fsm.create("Site A", {"dtm": dtm, "dsm": "", "ortho": ortho}, str(cfg / "jobs"))
    assert fs.files == {"dtm": dtm, "ortho": ortho}
    assert fsm.FileSet.is_fileset_dir(fs.job_dir) and fsm.is_source_dir(fs.job_dir)
    again = fsm.load_source(fs.job_dir)
    assert again.name == "Site A" and again.summary["files"]["dtm"] == dtm
    assert [a.kind for a in activity.load()] == ["fileset"]
    assert [f.name for f in fsm.all_filesets()] == ["Site A"]
    # same name again gets its own workspace
    fs2 = fsm.create("Site A", {"dsm": dtm}, str(cfg / "jobs"))
    assert fs2.job_dir != fs.job_dir


def test_latlon_dem_is_converted_to_metres(cfg):
    from fieldortho.core import filesets as fsm
    # ~ 1 arc-second SRTM-like tile near Cagayan de Oro
    tr = from_origin(124.60, 8.50, 1 / 3600, 1 / 3600)
    z = np.full((120, 120), 250.0)
    dem = _dem(cfg / "srtm.tif", "EPSG:4326", tr, z)
    fs = fsm.create("SRTM", {"dtm": dem}, str(cfg / "jobs"))
    out = fs.files["dtm"]
    assert out != dem and out.startswith(fs.job_dir)
    with rasterio.open(out) as ds:
        assert ds.crs.to_epsg() == 32651
        assert 25 < abs(ds.transform.a) < 35          # ~31 m cells
        a = ds.read(1, masked=True)
        assert abs(float(a.mean()) - 250.0) < 1e-3
    assert any("converted to metres" in n for n in fs.notes)


def test_feet_heights(cfg):
    from fieldortho.core import filesets as fsm
    dem = _dem(cfg / "ft.tif", "EPSG:32651", from_origin(700000, 980000, 1, 1), np.full((20, 20), 100.0))
    fs = fsm.create("Feet", {"dtm": dem}, str(cfg / "jobs"), heights_feet=True)
    with rasterio.open(fs.files["dtm"]) as ds:
        assert abs(float(ds.read(1, masked=True).mean()) - 30.48) < 1e-3


def test_problems_are_explained(cfg):
    from fieldortho.core import filesets as fsm
    tr = from_origin(700000, 980000, 1, 1)
    dem = _dem(cfg / "d.tif", "EPSG:32651", tr, np.ones((10, 10)))
    ortho = _ortho(cfg / "o.tif", "EPSG:32651", tr, (10, 10))
    far = _dem(cfg / "far.tif", "EPSG:32651", from_origin(300000, 1600000, 1, 1), np.ones((10, 10)))
    nocrs = cfg / "nocrs.tif"
    with rasterio.open(nocrs, "w", driver="GTiff", width=5, height=5, count=1, dtype="float32") as ds:
        ds.write(np.ones((5, 5), np.float32), 1)
    with pytest.raises(fsm.FileProblem, match="elevation model is required"):
        fsm.check({"ortho": ortho})
    with pytest.raises(fsm.FileProblem, match="colour picture"):
        fsm.check({"dtm": ortho})
    with pytest.raises(fsm.FileProblem, match="looks like an elevation"):
        fsm.check({"dtm": dem, "ortho": dem})
    with pytest.raises(fsm.FileProblem, match="same place"):
        fsm.check({"dtm": dem, "dsm": far})
    with pytest.raises(fsm.FileProblem, match="no coordinate system"):
        fsm.check({"dtm": str(nocrs)})
    with pytest.raises(fsm.FileProblem, match="not found"):
        fsm.check({"dtm": str(cfg / "missing.tif")})
    assert not os.path.exists(cfg / "jobs")
