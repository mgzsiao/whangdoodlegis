"""Command line for power users and batch processing.

    WhangDoodleGIS-cli check   D:\\Flights\\SiteA
    WhangDoodleGIS-cli process D:\\Flights\\SiteA --name "Site A" --preset standard --crs EPSG:32651
    WhangDoodleGIS-cli engine
"""
from __future__ import annotations

import argparse
import os
import sys

from .core import engine, jobs, photos as ph, presets
from .core.pipeline import JobProcessor
from .core.settings import Settings


def _print_survey(s: ph.Survey):
    print(f"Photos: {s.total} ({s.with_gps} with GPS, {s.usable} usable)   Camera: {', '.join(s.cameras)}")
    if s.area_ha:
        print(f"Area: {s.area_ha:.1f} ha   UTM: EPSG:{s.epsg}")
    if s.gsd_cm:
        print(f"Estimated ground resolution: {s.gsd_cm:.1f} cm/px   Overlap: ~{s.coverage or 0:.0f} photos/spot")
    for i in s.issues:
        print(f"  [{i.level.upper()}] {i.title} - {i.detail}")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):   # Windows consoles (cp1252) cannot print every symbol
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="WhangDoodleGIS-cli", description="Offline drone photo processing")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check a photo folder")
    c.add_argument("photos")
    c.add_argument("--height", type=float, help="flight height above ground (m) if not in EXIF")
    p = sub.add_parser("process", help="process a photo folder into GIS outputs")
    p.add_argument("photos")
    p.add_argument("--name")
    p.add_argument("--preset", choices=list(presets.PRESETS), default="standard")
    p.add_argument("--crs", default="auto", help="output CRS, e.g. EPSG:32651 (default: auto UTM)")
    p.add_argument("--no-dtm", action="store_true")
    p.add_argument("--no-contours", action="store_true")
    p.add_argument("--contour-interval", type=float, default=0.0)
    p.add_argument("--keep-blurry", action="store_true")
    p.add_argument("--odm", help="OpenDroneMap folder (overrides settings)")
    p.add_argument("--jobs-root")
    sub.add_parser("engine", help="show which processing engine is found")
    g = sub.add_parser("georef", help="georeference a map image that has a coordinate grid")
    g.add_argument("image")
    g.add_argument("--lon", required=True, help="value of the LEFTMOST grid line, e.g. 124d47'0\"E")
    g.add_argument("--lat", required=True, help="value of the TOP grid line, e.g. 8d52'0\"N")
    g.add_argument("--interval", default="1'", help="grid spacing, e.g. 1' or 30\" (default 1')")
    g.add_argument("--grid-crs", default="EPSG:4253", help="datum of the grid labels (default Luzon 1911)")
    g.add_argument("--crs", default="EPSG:4326", help="output coordinate system")
    g.add_argument("--formats", default="gtiff", help="comma list: gtiff,png,jpeg,kmz")
    g.add_argument("--classes", choices=["mgb_flood", "mgb_landslide"], help="also extract hazard polygons")
    g.add_argument("--out", help="output folder (default: next to the image)")
    pl = sub.add_parser("plan", help="recalculate a saved earthworks plan (.plan.json) and write its report and files")
    pl.add_argument("plan")
    pl.add_argument("--out", help="output folder (default: next to the plan)")
    pl.add_argument("--crs", help="coordinate system for the files (default: the drone map's)")
    pl.add_argument("--formats", default="gpkg,kml,dxf", help="GIS formats: gpkg,shp,geojson,kml,dxf")
    pl.add_argument("--prepared-by", default="")
    pl.add_argument("--location", default="")
    m3 = sub.add_parser("model3d", help="3-D model of a processed drone job for SketchUp (.dae) / STL")
    m3.add_argument("job", help="drone mapping job folder")
    m3.add_argument("--kind", choices=["photo", "ground"], default="photo")
    m3.add_argument("--detail", choices=["light", "normal", "detailed"], default="normal")
    m3.add_argument("--formats", default="dae", help="dae,stl")
    m3.add_argument("--out", help="output folder (default: <job>/3D)")
    a = ap.parse_args(argv)
    if a.cmd == "georef":
        return _georef(a)
    if a.cmd == "plan":
        return _plan(a)
    if a.cmd == "model3d":
        return _model3d(a)

    st = Settings.load()
    if a.cmd == "engine":
        info = engine.detect(st.odm_path, st.engine_mode, st.docker_image)
        print(("OK  " if info.ok else "MISSING  ") + info.message, info.path)
        return 0 if info.ok else 1

    print("Reading photos...")
    photos = ph.scan_folder(a.photos)
    s = ph.analyze(photos, getattr(a, "height", None))
    _print_survey(s)
    if a.cmd == "check":
        return 0 if s.can_process else 1
    if not s.can_process:
        return 1

    if a.odm:
        st.odm_path = a.odm
    info = engine.detect(st.odm_path, st.engine_mode, st.docker_image)
    if not info.ok:
        print(info.message, file=sys.stderr)
        return 2
    name = a.name or jobs.suggest_name(a.photos)
    job = jobs.Job(name=name, job_dir=jobs.unique_job_dir(a.jobs_root or st.jobs_root, name),
                   photos_dir=os.path.abspath(a.photos))
    o = job.options
    o.preset, o.target_crs, o.contour_interval = a.preset, a.crs, a.contour_interval
    o.dtm, o.contours, o.exclude_blurry = not a.no_dtm, not a.no_contours, not a.keep_blurry
    job.photos = [x.path for x in photos if x.has_gps and not (o.exclude_blurry and x.blurry)]
    job.survey = s.to_dict()
    job.save()
    st.add_recent(job.job_dir)
    last = [-1]

    def prog(pct, text):
        if int(pct) != last[0]:
            last[0] = int(pct)
            print(f"\r{pct:5.1f}%  {text:<60}", end="", flush=True)

    ok = JobProcessor(job, st, info, prog).run()
    print()
    if ok:
        print("Done. GIS files in:", job.gis_dir)
        return 0
    print("FAILED:", job.error, f"\nLog: {job.log_path}", file=sys.stderr)
    return 3


def _georef(a) -> int:
    from .core import georef as G
    from .core.georef_export import GeorefExport, export
    rgb = G.load_rgb(a.image)
    det = G.detect(rgb)
    print(f"Frame found; {len(det.meridians)} vertical and {len(det.parallels)} horizontal grid lines, "
          f"{len(det.crosses)} crosses.")
    step = G.parse_interval(a.interval)
    lon0, lat0 = G.parse_angle(a.lon), G.parse_angle(a.lat)
    for i, m in enumerate(det.meridians):
        m.value = lon0 + i * step
    for i, p in enumerate(det.parallels):
        p.value = lat0 - i * step
    gcps = G.make_gcps(det)
    name = os.path.splitext(os.path.basename(a.image))[0]
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.image)), name + "_georeferenced")
    req = GeorefExport(out_dir=out, name=name, crs=a.crs, raster_formats=a.formats.split(","),
                       classes_preset=a.classes, gcp_vector_formats=["gpkg"],
                       resampling="nearest" if a.classes else "cubic")
    files, info = export(rgb, det, gcps, a.grid_crs, req)
    print(f"Control points: {info['n']}, RMSE {info['rmse_m']:.2f} m (map pixel {info['pixel_m']:.2f} m).")
    print(info["datum"])
    if info.get("class_areas_ha"):
        for k, v in info["class_areas_ha"].items():
            print(f"  {k}: {v:,.1f} ha")
    print(f"{len(files)} files written to {out}")
    return 0




def _job_for_plan(plan_path: str, plan: dict):
    jd = plan.get("job_dir", "")
    if not jobs.Job.is_job_dir(jd):
        jd = os.path.dirname(os.path.dirname(os.path.abspath(plan_path)))
    if not jobs.Job.is_job_dir(jd):
        raise ValueError("The drone mapping job of this plan was not found.")
    return jobs.Job.load(jd)


def _plan(a) -> int:
    from .core import earthworks as ew, earthworks_report as er, road_report as rr
    try:
        plan = ew.load_plan(a.plan)
        job = _job_for_plan(a.plan, plan)
    except (OSError, ValueError) as e:
        print("ERROR:", e, file=sys.stderr)
        return 1
    files = job.summary.get("files", {})
    dem = files.get(plan.get("source", "dtm")) or files.get("dtm") or files.get("dsm")
    if not dem or not os.path.exists(dem):
        print("ERROR: the job has no elevation model.", file=sys.stderr)
        return 1
    import rasterio
    with rasterio.open(dem) as ds:
        epsg = ds.crs.to_epsg()
    crs = a.crs or (f"EPSG:{epsg}" if epsg else "EPSG:4326")
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.plan)), ew.safe(plan["name"]))
    print(f"{ew.TYPES[plan['type']]}: {plan['name']}  (drone map: {job.name})")
    t = ew.run_plan(plan, dem)
    print(t.headline())
    print(t.haul.sentence())
    fmts = [f.strip() for f in a.formats.split(",") if f.strip()]
    info = {"project": plan["name"], "location": a.location, "prepared_by": a.prepared_by}
    if plan["type"] == "road":
        for title, text in t.warnings:
            print(f"WARNING {title}: {text}")
        written = rr.export_road(t, plan, dem, files.get("ortho"), out, crs, info, job, fmts)
    else:
        written = er.export_package(t, plan, dem, files.get("ortho"), out, crs, info, job, fmts)
    print(f"{len(written)} files written to {out}")
    return 0


def _model3d(a) -> int:
    from .core import model3d
    if not jobs.Job.is_job_dir(a.job):
        print("ERROR: not a drone mapping job folder (no job.json).", file=sys.stderr)
        return 1
    job = jobs.Job.load(a.job)
    files = {k: v for k, v in job.summary.get("files", {}).items() if v and os.path.exists(v)}
    out = a.out or os.path.join(job.job_dir, "3D")
    fm = [f.strip() for f in a.formats.split(",") if f.strip()]
    written = model3d.export_model(files, a.kind, out, job.name, detail=a.detail, formats=fm)
    print(f"{len(written)} files written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
