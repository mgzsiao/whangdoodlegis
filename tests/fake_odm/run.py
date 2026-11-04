"""TEST DOUBLE for OpenDroneMap's run.py - used only by the automated tests.

It mimics ODM's command-line interface, log format, UDP progress broadcast,
log.json and output file layout, so FieldOrtho's runner, progress display,
error handling and GIS export can be tested without the real engine.

The "orthomosaic" it writes is the real ODM result image shipped with the
public odm_data_aukerman dataset, georeferenced to the photos' GPS extent;
the DSM/DTM are synthetic terrain. Do not use for anything else.
"""
import json
import math
import os
import socket
import sys
import time

import numpy as np
import rasterio
from rasterio.transform import from_origin
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from fieldortho.core import photos as ph  # noqa: E402
from fieldortho.core import crs as crsmod  # noqa: E402

STAGES = [("dataset", 5), ("opensfm", 25), ("openmvs", 50), ("odm_filterpoints", 52),
          ("odm_meshing", 60), ("mvs_texturing", 70), ("odm_georeferencing", 80),
          ("odm_dem", 90), ("odm_orthophoto", 98), ("odm_report", 99), ("odm_postprocess", 100)]
SOURCE_PNG = os.environ.get("FAKE_ODM_ORTHO", "/tmp/data/aukerman/aukerman.png")


def log(level, msg):
    print("\033[92m" + ("[" + level + "]").ljust(9) + " " + msg + "\033[0m", flush=True)


def main():
    argv = sys.argv[1:]
    project = os.path.abspath(argv[0])
    flags = set(a for a in argv[1:] if a.startswith("--"))
    name = os.path.basename(project)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fail_at = os.environ.get("FAKE_ODM_FAIL", "")
    delay = float(os.environ.get("FAKE_ODM_DELAY", "0.15"))
    log_json = {"odmVersion": "3.6.2-fake", "options": argv[1:], "stages": [], "success": False}

    imgs = sorted(os.listdir(os.path.join(project, "images")))
    log("INFO", "Initializing ODM 3.6.2 - %s" % time.strftime("%c"))
    for stage, pct in STAGES:
        if stage in ("odm_dem",) and not ({"--dsm", "--dtm"} & flags):
            continue
        log("INFO", "Running %s stage" % stage)
        log_json["stages"].append({"name": stage})
        if stage == "dataset":
            log("INFO", "Found %d usable images" % len(imgs))
        for k in range(3):
            time.sleep(delay)
            prev = dict(STAGES).get(stage, 0)
            sock.sendto(("PGUP/%d/%s/%s" % (os.getpid(), name, float(pct - (2 - k)))).encode(),
                        ("127.0.0.1", 6367))
        if stage == fail_at:
            log("ERROR", "The program could not process this dataset using the current settings. "
                         "Reconstruction could not be generated")
            json.dump(log_json, open(os.path.join(project, "log.json"), "w"))
            sys.exit(1)
        if stage == "odm_orthophoto":
            write_outputs(project, imgs, flags)
        log("INFO", "Finished %s stage" % stage)
    log_json["success"] = True
    json.dump(log_json, open(os.path.join(project, "log.json"), "w"))
    log("INFO", "ODM app finished - %s" % time.strftime("%c"))


def write_outputs(project, imgs, flags):
    pts = [ph.read_photo(os.path.join(project, "images", f), measure_sharpness=False) for f in imgs]
    pts = [p for p in pts if p.has_gps]
    epsg = crsmod.utm_epsg(pts[0].lat, pts[0].lon)
    xs, ys = crsmod.project_lonlat([p.lon for p in pts], [p.lat for p in pts], epsg)
    pad = 45.0
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    src = Image.open(SOURCE_PNG).convert("RGBA")
    # the sample image has an opaque white background: make it transparent like real ODM output
    arr = np.asarray(src).copy()
    arr[..., 3] = np.where(arr[..., :3].min(axis=2) > 245, 0, 255)
    src = Image.fromarray(arr, "RGBA")
    # fit the image into the extent keeping aspect
    w_m, h_m = x1 - x0, y1 - y0
    aspect = src.width / src.height
    if w_m / h_m > aspect:
        w_m = h_m * aspect
    else:
        h_m = w_m / aspect
    res = 0.10
    W, H = int(w_m / res), int(h_m / res)
    img = np.asarray(src.resize((W, H), Image.BICUBIC))
    tr = from_origin(x0, y0 + h_m, res, res)
    crs = f"EPSG:{epsg}"
    os.makedirs(os.path.join(project, "odm_orthophoto"), exist_ok=True)
    with rasterio.open(os.path.join(project, "odm_orthophoto", "odm_orthophoto.tif"), "w", driver="GTiff",
                       width=W, height=H, count=4, dtype="uint8", crs=crs, transform=tr,
                       tiled=True, compress="DEFLATE", photometric="RGB") as ds:
        ds.write(np.moveaxis(img, -1, 0))
        ds.colorinterp = [rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                          rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha]

    if {"--dsm", "--dtm"} & flags:
        dres = 0.25
        dW, dH = int(w_m / dres), int(h_m / dres)
        small = np.asarray(src.resize((dW, dH), Image.BILINEAR)).astype(float)
        yy, xx = np.mgrid[0:dH, 0:dW] * dres
        ground = 300 + 0.03 * xx - 0.02 * yy + 3 * np.sin(xx / 60) * np.cos(yy / 45)
        r, g, b, a = small[..., 0], small[..., 1], small[..., 2], small[..., 3]
        tree = np.clip((g - r) / 40.0, 0, 1) * np.clip((150 - g) / 60.0, 0, 1)
        dsm = ground + 14 * tree
        dsm[a < 128] = -9999
        dtm = ground.copy()
        dtm[a < 128] = -9999
        dtr = from_origin(x0, y0 + h_m, dres, dres)
        os.makedirs(os.path.join(project, "odm_dem"), exist_ok=True)
        for fname, arr, flag in (("dsm.tif", dsm, "--dsm"), ("dtm.tif", dtm, "--dtm")):
            if flag in flags:
                with rasterio.open(os.path.join(project, "odm_dem", fname), "w", driver="GTiff",
                                   width=dW, height=dH, count=1, dtype="float32", crs=crs,
                                   transform=dtr, nodata=-9999) as ds:
                    ds.write(arr.astype("float32"), 1)

    os.makedirs(os.path.join(project, "odm_georeferencing"), exist_ok=True)
    with open(os.path.join(project, "odm_georeferencing", "odm_georeferenced_model.laz"), "wb") as f:
        f.write(b"LASF" + b"\0" * 400)
    os.makedirs(os.path.join(project, "odm_report"), exist_ok=True)
    with open(os.path.join(project, "odm_report", "report.pdf"), "wb") as f:
        f.write(b"%PDF-1.4\n% fake report\n%%EOF\n")
    stats = {"reconstruction_statistics": {"reconstructed_shots_count": len(pts),
                                           "initial_shots_count": len(imgs),
                                           "reconstructed_points_count": 123456},
             "processing_statistics": {"area": w_m * h_m * 0.8},
             "odm_processing_statistics": {"average_gsd": 2.6, "total_time": 999},
             "gps_errors": {"average_error": 1.84, "error": {"x": 1.2, "y": 1.1, "z": 2.9}}}
    json.dump(stats, open(os.path.join(project, "odm_report", "stats.json"), "w"))


if __name__ == "__main__":
    main()
