"""'My own files': elevation models and photo maps made outside the app (other drone software,
NAMRIA IFSAR / LiDAR, SRTM ...) used for Flood & landslide and Measure & earthworks.

A file set is a small workspace folder (it holds measurements, earthworks plans and any converted
copies), listed in the Jobs list like the other activities:

    <jobs_root>/Own files/<name>/
        fileset.json          which files are used and where they came from
        dtm_metres.tif ...    only when a file had to be converted (latitude/longitude or feet)
        measurements.json     Measure tool (same layout as a drone job)
        Earthworks/           earthworks plans

The original files are used where they are (they can be large) unless they must be converted.
Nothing is downloaded: all checks and conversions run on this computer.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np

from . import activity

MARKER = "fileset.json"
KINDS = ("dtm", "dsm", "ortho")
LABELS = {"dtm": "Ground elevation (DTM / bare-earth DEM)", "dsm": "Surface elevation (DSM)",
          "ortho": "Photo map (orthomosaic)"}
FT = 0.3048


@dataclass
class FileSet:
    name: str
    job_dir: str                                  # workspace folder (same attribute name as jobs.Job)
    files: dict = field(default_factory=dict)     # kind -> path actually used
    original: dict = field(default_factory=dict)  # kind -> path the user picked
    notes: list = field(default_factory=list)     # what was converted, shown to the user
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    kind = "fileset"

    @property
    def summary(self) -> dict:                   # same shape as jobs.Job.summary
        return {"files": dict(self.files)}

    @property
    def marker(self) -> str:
        return os.path.join(self.job_dir, MARKER)

    def missing(self) -> list[str]:
        return [k for k, p in self.files.items() if not os.path.exists(p)]

    def save(self) -> None:
        os.makedirs(self.job_dir, exist_ok=True)
        with open(self.marker, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, folder: str) -> "FileSet":
        with open(os.path.join(folder, MARKER), encoding="utf-8") as f:
            d = json.load(f)
        d = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        d["job_dir"] = folder
        return cls(**d)

    @staticmethod
    def is_fileset_dir(folder: str) -> bool:
        return bool(folder) and os.path.isfile(os.path.join(folder, MARKER))


def is_source_dir(folder: str) -> bool:
    from . import jobs
    return jobs.Job.is_job_dir(folder) or FileSet.is_fileset_dir(folder)


def load_source(folder: str):
    """A drone job or a file set from its folder."""
    from . import jobs
    if FileSet.is_fileset_dir(folder):
        return FileSet.load(folder)
    return jobs.Job.load(folder)


def all_filesets() -> list[FileSet]:
    out, seen = [], set()
    for a in activity.load():
        if a.kind != "fileset" or not a.exists():
            continue
        folder = os.path.dirname(a.path)
        if os.path.normcase(folder) in seen:
            continue
        seen.add(os.path.normcase(folder))
        try:
            out.append(FileSet.load(folder))
        except (OSError, ValueError, TypeError):
            continue
    return out


# ---------------------------------------------------------------- checking files
class FileProblem(ValueError):
    pass


def inspect(path: str, kind: str) -> dict:
    """Check one file. Returns facts used for the summary; raises FileProblem with a plain message."""
    import rasterio
    if not os.path.isfile(path):
        raise FileProblem(f"{LABELS[kind]}: the file was not found.")
    try:
        ds = rasterio.open(path)
    except Exception:
        raise FileProblem(f"{LABELS[kind]}: {os.path.basename(path)} is not a map file the app can read "
                          "(use a GeoTIFF, .tif).")
    with ds:
        if ds.crs is None:
            raise FileProblem(f"{LABELS[kind]}: {os.path.basename(path)} has no coordinate system, so the app "
                              "cannot tell where it is. Export it again as a GeoTIFF with its coordinate system.")
        info = {"path": path, "crs": ds.crs.to_string(), "geographic": ds.crs.is_geographic,
                "width": ds.width, "height": ds.height, "bands": ds.count, "dtype": ds.dtypes[0]}
        try:
            unit = (ds.crs.linear_units or "").lower()
        except Exception:
            unit = ""
        info["feet_xy"] = (not ds.crs.is_geographic) and ("foot" in unit or "feet" in unit or unit == "us survey foot")
        if kind == "ortho":
            if ds.count < 3 and ds.dtypes[0] != "uint8":
                raise FileProblem(f"Photo map: {os.path.basename(path)} looks like an elevation model, not a "
                                  "photo (it has one band of heights). Put it in a DTM or DSM box instead.")
        else:
            if ds.count >= 3 and ds.dtypes[0] == "uint8":
                raise FileProblem(f"{LABELS[kind]}: {os.path.basename(path)} is a colour picture, not heights. "
                                  "Put it in the Photo map box and choose the elevation file here.")
            step = max(1, int(max(ds.width, ds.height) / 600))
            z = ds.read(1, out_shape=(max(1, ds.height // step), max(1, ds.width // step)), masked=True)
            z = z.astype("float64").filled(np.nan)
            z[z < -1000] = np.nan                    # unflagged no-data such as -9999 / -32767
            if not np.isfinite(z).any():
                raise FileProblem(f"{LABELS[kind]}: {os.path.basename(path)} has no height values.")
            info["zmin"], info["zmax"] = float(np.nanmin(z)), float(np.nanmax(z))
            info["cell"] = abs(ds.transform.a)
        b = ds.bounds
        info["bounds_wgs84"] = _bounds_wgs84(ds.crs, (b.left, b.bottom, b.right, b.top))
    return info


def _bounds_wgs84(crs, b):
    from rasterio.warp import transform_bounds
    try:
        return tuple(transform_bounds(crs, "EPSG:4326", *b, densify_pts=21))
    except Exception:
        return None


def _overlap(a, b) -> bool:
    if not a or not b:
        return True
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def check(picked: dict) -> tuple[dict, list[str]]:
    """picked: kind -> path ('' = not given). Returns (info per kind, warnings).
    Raises FileProblem when the set cannot be used."""
    picked = {k: v for k, v in picked.items() if v}
    if not ("dtm" in picked or "dsm" in picked):
        raise FileProblem("An elevation model is required: choose a Ground elevation (DTM) or a Surface "
                          "elevation (DSM) file. The app cannot measure heights, slopes, volumes, floods or "
                          "landslides without one.")
    info = {k: inspect(p, k) for k, p in picked.items()}
    warns = []
    keys = list(info)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if not _overlap(info[a]["bounds_wgs84"], info[b]["bounds_wgs84"]):
                raise FileProblem(f"{LABELS[a]} and {LABELS[b]} do not cover the same place. Choose files "
                                  "of the same site.")
    for k in ("dtm", "dsm"):
        if k in info:
            i = info[k]
            if i["geographic"]:
                warns.append(f"{LABELS[k]} is in latitude/longitude; a copy in metres (UTM) will be made.")
            elif i["feet_xy"]:
                warns.append(f"{LABELS[k]} uses feet; a copy in metres (UTM) will be made.")
            if i["zmax"] - i["zmin"] > 9000:
                warns.append(f"{LABELS[k]}: heights range {i['zmin']:.0f} to {i['zmax']:.0f}; check that "
                             "no-data cells are flagged in the file.")
    if "dtm" in info and "dsm" in info and info["dtm"]["path"] == info["dsm"]["path"]:
        warns.append("The same file is used for DTM and DSM.")
    return info, warns


# ---------------------------------------------------------------- creating a set
def safe_name(name: str) -> str:
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return n or "My files"


def _to_metres(src_path: str, dst_path: str, heights_feet: bool) -> str:
    """Reproject an elevation file to UTM metres (and feet heights to metres). Returns the EPSG."""
    import rasterio
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    from . import crs as crsmod
    with rasterio.open(src_path) as ds:
        b = ds.bounds
        lon0, lat0, lon1, lat1 = _bounds_wgs84(ds.crs, (b.left, b.bottom, b.right, b.top))
        dst = rasterio.crs.CRS.from_epsg(crsmod.utm_epsg((lat0 + lat1) / 2, (lon0 + lon1) / 2))
        if ds.crs == dst and not heights_feet:
            return dst.to_string()
        tr, w, h = calculate_default_transform(ds.crs, dst, ds.width, ds.height, *b)
        nd = ds.nodata
        src = ds.read(1, masked=True).astype("float32").filled(np.nan)
        out = np.full((h, w), np.nan, np.float32)
        reproject(src, out, src_transform=ds.transform, src_crs=ds.crs, dst_transform=tr, dst_crs=dst,
                  src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
        del nd
    if heights_feet:
        out = out * FT
    out = np.where(np.isfinite(out), out, -9999.0).astype("float32")
    prof = dict(driver="GTiff", width=w, height=h, count=1, dtype="float32", crs=dst, transform=tr,
                nodata=-9999.0, compress="deflate", tiled=True, BIGTIFF="IF_SAFER")
    with rasterio.open(dst_path, "w", **prof) as o:
        o.write(out, 1)
    return dst.to_string()


def create(name: str, picked: dict, root: str, heights_feet: bool = False) -> FileSet:
    """Check the files, convert what must be converted and record the set."""
    info, warns = check(picked)
    base = os.path.join(root, "Own files", safe_name(name))
    folder, n = base, 2
    while os.path.exists(folder):
        folder, n = f"{base} ({n})", n + 1
    os.makedirs(folder)
    fs = FileSet(name=name.strip() or os.path.basename(folder), job_dir=folder)
    for k, i in info.items():
        fs.original[k] = i["path"]
        if k != "ortho" and (i["geographic"] or i["feet_xy"] or heights_feet):
            out = os.path.join(folder, f"{k}_metres.tif")
            code = _to_metres(i["path"], out, heights_feet)
            fs.files[k] = out
            why = ["latitude/longitude" if i["geographic"] else "feet" if i["feet_xy"] else "",
                   "heights in feet" if heights_feet else ""]
            fs.notes.append(f"{LABELS[k]} converted to metres ({code}; was {', '.join(w for w in why if w)}).")
        else:
            fs.files[k] = i["path"]
    fs.notes.extend(w for w in warns if "a copy in metres" not in w)
    fs.save()
    kinds = "+".join(k.upper() for k in KINDS if k in fs.files)
    activity.record(activity.Activity("fileset", fs.name, fs.marker, folder, size=kinds,
                                   crs=info.get("dtm", info.get("dsm"))["crs"], status="Ready"))
    return fs
