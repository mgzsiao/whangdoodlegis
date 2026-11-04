"""Automated tests. Uses the public OpenDroneMap sample dataset (odm_data_aukerman,
77 photos from a senseFly eBee) when available, and a test double of the ODM
command line (tests/fake_odm) because the real engine is installed separately."""
import os
import shutil

import numpy as np
import pytest
import rasterio

from fieldortho.core import photos as ph, jobs, engine, gisexport, presets, gcp
from fieldortho.core.pipeline import JobProcessor
from fieldortho.core.settings import Settings

DATA = os.environ.get("FIELDORTHO_TEST_PHOTOS", "/tmp/data/aukerman/images")
FAKE_ODM = os.path.join(os.path.dirname(__file__), "fake_odm")
need_data = pytest.mark.skipif(not os.path.isdir(DATA), reason="sample photos not available")


@pytest.fixture(scope="module")
def photos():
    return ph.scan_folder(DATA)


@need_data
def test_exif_reading(photos):
    assert len(photos) == 77
    p = photos[0]
    assert p.has_gps and 41.2 < p.lat < 41.4 and -81.8 < p.lon < -81.7
    assert p.camera == "SONY DSC-WX220" and p.focal_mm == pytest.approx(4.45)
    assert p.width == 4896 and p.sharpness and p.sharpness > 0


@need_data
def test_survey_analysis(photos):
    s = ph.analyze(photos, flight_height_m=100)
    assert s.epsg == 32617                       # Ohio -> UTM 17N
    assert s.can_process and s.usable >= 70
    assert 2.0 < s.gsd_cm < 4.0                  # 1/2.3" sensor, 4.45 mm lens, 100 m
    assert s.coverage and s.coverage >= 9
    assert "DSC00308.JPG" in s.blurry            # visibly motion-blurred photo


def test_survey_errors():
    s = ph.analyze([ph.Photo(path="a.jpg", name="a.jpg")])
    assert not s.can_process
    assert any("GPS" in i.title for i in s.issues)


def test_preset_args():
    a = presets.build_args("standard", dsm=True, dtm=True, threads=4, gcp_file="g.txt")
    assert "--dsm" in a and "--dtm" in a and a[a.index("--gcp") + 1] == "g.txt"
    q = presets.build_args("quick", dsm=True, dtm=True, threads=4)
    assert "--fast-orthophoto" in q and "--dsm" not in q


def test_error_explanations():
    assert "memory" in engine.explain_error("... std::bad_alloc ...", 1)
    assert "matched" in engine.explain_error("Reconstruction could not be generated", 1)


def _make_job(tmp_path, photos, **opt):
    settings = Settings(odm_path=FAKE_ODM, jobs_root=str(tmp_path), threads=2)
    job_dir = jobs.unique_job_dir(str(tmp_path), "Aukerman test")
    job = jobs.Job(name="Aukerman test", job_dir=job_dir, photos_dir=DATA,
                   photos=[p.path for p in photos if not p.blurry][:40])
    for k, v in opt.items():
        setattr(job.options, k, v)
    job.save()
    info = engine.detect(FAKE_ODM)
    assert info.ok, info.message
    return job, settings, info


@need_data
def test_full_pipeline_with_reprojection(tmp_path, photos):
    ph.analyze(photos, 100)
    job, settings, info = _make_job(tmp_path, photos, target_crs="EPSG:4326", heights="offset",
                                    height_offset_m=-10.0)
    progress, lines = [], []
    ok = JobProcessor(job, settings, info, lambda p, t: progress.append((p, t)), lines.append).run()
    assert ok, job.error
    assert progress == sorted(progress, key=lambda t: t[0])  # never goes backwards
    assert any("Matching photos" in t for _p, t in progress)
    files = job.summary["files"]
    for k in ("ortho", "dsm", "dtm", "laz", "report", "readme"):
        assert os.path.isfile(files[k]), k
    with rasterio.open(files["ortho"]) as ds:
        assert ds.crs.to_epsg() == 4326 and ds.count == 4
    with rasterio.open(files["dtm"]) as ds:
        z = ds.read(1)
        assert ds.nodata == -9999
        valid = z[z != -9999]
        assert 280 < valid.mean() < 300            # synthetic ground ~300 m shifted by -10 m
    # contours need metres: skipped for lat/long output, with a plain warning
    assert "contours" not in files
    assert any("projected" in w for w in job.summary["warnings"])


@need_data
def test_full_pipeline_utm_contours_and_checkpoints(tmp_path, photos):
    ph.analyze(photos, 100)
    cp = tmp_path / "check.csv"
    job, settings, info = _make_job(tmp_path, photos, contour_interval=1.0)
    # build check points from the synthetic DTM after a first run
    assert JobProcessor(job, settings, info).run(), job.error
    dtm = job.summary["files"]["dtm"]
    with rasterio.open(dtm) as ds:
        cx, cy = (ds.bounds.left + ds.bounds.right) / 2, (ds.bounds.bottom + ds.bounds.top) / 2
        z = list(ds.sample([(cx, cy)]))[0][0]
    cp.write_text(f"name,easting,northing,elevation\nCP1,{cx},{cy},{z - 0.05}\n")
    pts = gisexport.read_points_csv(str(cp))
    res = gisexport.check_heights(dtm, pts, "EPSG:32617")
    assert res["n"] == 1 and res["rmse_z"] == pytest.approx(0.05, abs=0.02)
    import shapefile
    r = shapefile.Reader(job.summary["files"]["contours"])
    assert len(r) > 5 and r.fields[1][0] == "ELEV"
    assert os.path.isfile(job.summary["files"]["contours"].replace(".shp", ".prj"))


@need_data
def test_failure_is_explained_and_resumable(tmp_path, photos, monkeypatch):
    job, settings, info = _make_job(tmp_path, photos)
    monkeypatch.setenv("FAKE_ODM_FAIL", "opensfm")
    assert not JobProcessor(job, settings, info).run()
    assert job.status == jobs.STATUS_FAILED and "matched" in job.error
    assert engine.resume_stage(job.job_dir) == "opensfm"
    monkeypatch.delenv("FAKE_ODM_FAIL")
    assert JobProcessor(job, settings, info).run()
    assert jobs.Job.load(job.job_dir).status == jobs.STATUS_DONE


@need_data
def test_gcp_file(tmp_path, photos):
    s = ph.analyze(photos, 100)
    p0 = photos[10]
    csvp = tmp_path / "gcp.csv"
    csvp.write_text(f"GCP1,{p0.x + 5},{p0.y - 3},300.0\nGCP2,{p0.x + 40},{p0.y},301.0\n")
    gs = gcp.GcpSet.from_csv(str(csvp), f"EPSG:{s.epsg}")
    cands = gcp.candidate_photos(gs.points[0], gs.crs, photos, s.epsg, s.footprint_m)
    assert cands and cands[0][0].name == p0.name
    for c, _d in cands[:3]:
        gs.points[0].marks.append(gcp.GcpMark(c.name, 100.0, 200.0))
    out = tmp_path / "gcp_list.txt"
    assert gs.write_odm(str(out)) == 3
    lines = out.read_text().splitlines()
    assert lines[0] == "EPSG:32617" and lines[1].endswith("GCP1")


def test_engine_path_is_absolute(monkeypatch):
    monkeypatch.chdir(os.path.dirname(FAKE_ODM))
    info = engine.detect("fake_odm")
    assert info.ok and os.path.isabs(info.path)


def test_engine_env_drops_venv_leftovers():
    """ODM's win32env.bat restores PATH from _OLD_VIRTUAL_PATH (set by an activated venv),
    which removes ODM's DLL folder; such variables must not reach the engine."""
    env = engine.engine_env({"PATH": r"C:\x", "_OLD_VIRTUAL_PATH": r"C:\y", "VIRTUAL_ENV": r"C:\v",
                             "PYTHONHOME": r"C:\p", "NODEFAULTCURRENTDIRECTORYINEXEPATH": "1",
                             "SYSTEMROOT": r"C:\Windows"})
    assert env["PATH"] == r"C:\x" and env["SYSTEMROOT"] == r"C:\Windows"
    assert env["ODM_NONINTERACTIVE"] == "1"
    for k in ("_OLD_VIRTUAL_PATH", "VIRTUAL_ENV", "PYTHONHOME", "NODEFAULTCURRENTDIRECTORYINEXEPATH"):
        assert k not in env


@need_data
def test_sea_level_conversion_with_geoid_grid(tmp_path, photos):
    """EGM96 option: uses a synthetic global geoid grid with N = -33.5 m."""
    from rasterio.transform import from_origin
    grid = tmp_path / "egm.tif"
    with rasterio.open(grid, "w", driver="GTiff", width=360, height=180, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(-180, 90, 1, 1)) as ds:
        ds.write(np.full((1, 180, 360), -33.5, dtype="float32"))
    ph.analyze(photos, 100)
    job, settings, info = _make_job(tmp_path, photos, contours=False)
    assert JobProcessor(job, settings, info).run()
    before = rasterio.open(job.summary["files"]["dtm"]).read(1)
    res = gisexport.export(job.job_dir, str(tmp_path / "gis2"), "x",
                           gisexport.ExportOptions(heights="egm96", geoid_grid=str(grid), contours=False))
    after = rasterio.open(res.files["dtm"]).read(1)
    m = before != -9999
    assert np.allclose(after[m] - before[m], 33.5, atol=1e-3)     # H = h - N
    assert "EGM96" in res.summary["vertical"]


def test_changed_settings_force_full_rerun(tmp_path):
    job = jobs.Job(name="j", job_dir=str(tmp_path / "j"))
    os.makedirs(job.path("opensfm"))
    p = JobProcessor(job, Settings(threads=2), engine.EngineInfo(True, "native"))
    first = p.odm_args()
    assert "--rerun-all" not in first
    job.summary["odm_args"] = first
    assert "--rerun-all" not in p.odm_args()        # same settings -> resume
    job.options.preset = "quick"
    assert "--rerun-all" in p.odm_args()            # new settings -> recompute
