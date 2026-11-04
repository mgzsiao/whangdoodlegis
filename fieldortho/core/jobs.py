"""A job = one site/flight processed into one set of GIS outputs.

Folder layout (also an OpenDroneMap project folder):

    <jobs_root>/<job name>/
        job.json              FieldOrtho settings and status
        images/               photos used (hard links where possible, else copies)
        gcp_list.txt          optional ground control points (ODM format)
        odm_orthophoto/ ...   OpenDroneMap working outputs
        GIS/                  clean, ready-for-GIS deliverables
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Callable

STATUS_NEW = "new"
STATUS_RUNNING = "processing"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_DONE = "done"


@dataclass
class JobOptions:
    preset: str = "standard"
    dsm: bool = True
    dtm: bool = True
    contours: bool = True
    contour_interval: float = 0.0        # 0 = automatic
    pointcloud: bool = True
    target_crs: str = "auto"
    heights: str = "as_recorded"         # as_recorded | egm96 | offset
    height_offset_m: float = 0.0
    exclude_blurry: bool = True
    gcp_file: str = ""                   # ODM gcp_list.txt inside the job
    checkpoints_file: str = ""           # CSV of independent check points
    checkpoints_crs: str = ""            # their coordinate system (e.g. EPSG:32651)


@dataclass
class Job:
    name: str
    job_dir: str
    photos_dir: str = ""
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    status: str = STATUS_NEW
    options: JobOptions = field(default_factory=JobOptions)
    photos: list[str] = field(default_factory=list)     # absolute source paths used
    survey: dict = field(default_factory=dict)
    started: str = ""
    finished: str = ""
    last_stage: str = ""
    error: str = ""
    summary: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- paths
    @property
    def images_dir(self) -> str:
        return os.path.join(self.job_dir, "images")

    @property
    def gis_dir(self) -> str:
        return os.path.join(self.job_dir, "GIS")

    @property
    def log_path(self) -> str:
        return os.path.join(self.job_dir, "processing_log.txt")

    def path(self, *parts: str) -> str:
        return os.path.join(self.job_dir, *parts)

    # ---------------------------------------------------------------- persistence
    def save(self) -> None:
        os.makedirs(self.job_dir, exist_ok=True)
        tmp = self.path("job.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp, self.path("job.json"))

    @classmethod
    def load(cls, job_dir: str) -> "Job":
        with open(os.path.join(job_dir, "job.json"), encoding="utf-8") as f:
            d = json.load(f)
        opts = JobOptions(**{k: v for k, v in d.pop("options", {}).items()
                             if k in JobOptions.__dataclass_fields__})
        d = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        d["job_dir"] = job_dir   # folder may have been moved
        return cls(options=opts, **d)

    @staticmethod
    def is_job_dir(path: str) -> bool:
        return os.path.isfile(os.path.join(path, "job.json"))


def safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:80] or "job"


def unique_job_dir(root: str, name: str) -> str:
    base = os.path.join(root, safe_name(name))
    path, i = base, 2
    while os.path.exists(path):
        path = f"{base} ({i})"
        i += 1
    return path


def suggest_name(photos_dir: str) -> str:
    folder = os.path.basename(os.path.normpath(photos_dir)) or "Site"
    return f"{folder} {datetime.now():%Y-%m-%d}"


def stage_photos(job: Job, progress: Callable[[int, int], None] | None = None) -> int:
    """Put the selected photos into job/images. Uses hard links when on the same
    drive (instant, no extra space), otherwise copies. Returns count."""
    os.makedirs(job.images_dir, exist_ok=True)
    wanted = {os.path.basename(p): p for p in job.photos}
    # remove photos no longer selected (e.g. blurry exclusion changed)
    for f in os.listdir(job.images_dir):
        if f not in wanted:
            try:
                os.remove(os.path.join(job.images_dir, f))
            except OSError:
                pass
    n = len(wanted)
    for i, (name, src) in enumerate(sorted(wanted.items())):
        dst = os.path.join(job.images_dir, name)
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
            if os.path.exists(dst):
                os.remove(dst)
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
        if progress:
            progress(i + 1, n)
    return n
