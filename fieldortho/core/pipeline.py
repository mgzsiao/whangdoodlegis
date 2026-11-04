"""End-to-end job processing used by both the GUI and the command line:
stage photos -> run OpenDroneMap -> export GIS deliverables."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime
from typing import Callable

from . import engine as eng
from . import gisexport, jobs, presets
from .settings import Settings

# Share of the overall progress bar for each phase
_W_STAGE, _W_ENGINE, _W_EXPORT = 3.0, 90.0, 7.0


def geoid_grid_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for up in (os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
        p = os.path.join(up, "data", "us_nga_egm96_15.tif")
        if os.path.isfile(p):
            return p
    return ""


class JobProcessor:
    """Runs one job. Thread-safe callbacks: on_progress(percent, text), on_line(str)."""

    def __init__(self, job: jobs.Job, settings: Settings, info: eng.EngineInfo,
                 on_progress: Callable[[float, str], None] | None = None,
                 on_line: Callable[[str], None] | None = None):
        self.job = job
        self.settings = settings
        self.info = info
        self._user_progress = on_progress or (lambda p, t: None)
        self._last_pct = 0.0
        self.on_line = on_line or (lambda s: None)
        self.runner: eng.OdmRunner | None = None
        self.cancelled = False

    def on_progress(self, pct: float, text: str):
        """Progress never moves backwards, whatever the engine reports."""
        self._last_pct = max(self._last_pct, min(100.0, pct))
        self._user_progress(self._last_pct, text)

    def cancel(self):
        self.cancelled = True
        if self.runner:
            self.runner.cancel()

    def odm_args(self) -> list[str]:
        o = self.job.options
        gcp = self.job.path("gcp_list.txt") if o.gcp_file and os.path.isfile(self.job.path("gcp_list.txt")) else None
        args = presets.build_args(o.preset, dsm=o.dsm or o.contours, dtm=o.dtm,
                                  threads=self.settings.threads, gcp_file=gcp)
        # Settings changed since the last run -> recompute everything
        prev = self.job.summary.get("odm_args")
        if prev is not None and prev != args and os.path.isdir(self.job.path("opensfm")):
            args = args + ["--rerun-all"]
        return args

    def run(self) -> bool:
        job = self.job
        job.status = jobs.STATUS_RUNNING
        job.error = ""
        job.started = datetime.now().isoformat(timespec="seconds")
        job.save()

        # 1. photos --------------------------------------------------------
        self.on_progress(0.5, "Preparing photos")
        jobs.stage_photos(job, lambda i, n: self.on_progress(_W_STAGE * i / n, "Preparing photos"))
        if self.cancelled:
            return self._finish(jobs.STATUS_CANCELLED)

        # 2. engine --------------------------------------------------------
        args = self.odm_args()
        clean_args = [a for a in args if a != "--rerun-all"]

        def prog(p, text):
            self.on_progress(_W_STAGE + _W_ENGINE * p / 100.0, text)

        self.runner = eng.OdmRunner(self.info, job.job_dir, args, on_line=self.on_line,
                                    on_progress=prog, log_path=job.log_path)
        res = self.runner.run()
        job.last_stage = res.last_stage
        job.summary["engine_seconds_wall"] = round(res.seconds)
        if res.cancelled or self.cancelled:
            return self._finish(jobs.STATUS_CANCELLED)
        if not res.ok:
            job.error = res.error
            return self._finish(jobs.STATUS_FAILED)
        job.summary["odm_args"] = clean_args

        # 3. GIS export ------------------------------------------------------
        o = job.options
        opts = gisexport.ExportOptions(
            target_crs=o.target_crs, heights=o.heights, height_offset_m=o.height_offset_m,
            contours=o.contours, contour_interval=o.contour_interval, pointcloud=o.pointcloud,
            checkpoints_csv=o.checkpoints_file, checkpoints_crs=o.checkpoints_crs, geoid_grid=geoid_grid_path())
        try:
            out = gisexport.export(job.job_dir, job.gis_dir, job.name, opts,
                                   lambda p, t: self.on_progress(_W_STAGE + _W_ENGINE + _W_EXPORT * p / 100.0, t))
        except Exception as e:  # keep ODM results; export can be retried
            job.error = f"The map was processed, but exporting GIS files failed: {e}"
            return self._finish(jobs.STATUS_FAILED)
        # Drop deliverables the user did not ask for (DSM may be needed only for contours)
        for kind, wanted in (("dsm", o.dsm), ("dtm", o.dtm)):
            if not wanted and kind in out.files:
                os.remove(out.files.pop(kind))
        job.summary.update(out.summary)
        job.summary["files"] = out.files
        job.summary["previews"] = out.previews
        self.on_progress(100.0, "Finished")
        return self._finish(jobs.STATUS_DONE)

    def _finish(self, status: str) -> bool:
        self.job.status = status
        self.job.finished = datetime.now().isoformat(timespec="seconds")
        self.job.save()
        return status == jobs.STATUS_DONE


def cleanup_intermediate(job: jobs.Job) -> int:
    """Delete OpenDroneMap working folders (keeps images, GIS/ and job.json).
    Returns bytes freed. After this a job cannot be resumed."""
    freed = 0
    for name in os.listdir(job.job_dir):
        p = job.path(name)
        if name in ("images", "GIS", "job.json", "gcp_list.txt", "gcp_tags.json",
                    "processing_log.txt", "checkpoints.csv"):
            continue
        if os.path.isdir(p) and (name.startswith(("odm_", "opensfm", "openmvs", "mve", "entwine",
                                                  "3d_tiles", "submodels"))):
            for root, _d, files in os.walk(p):
                for f in files:
                    try:
                        freed += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            shutil.rmtree(p, ignore_errors=True)
    return freed
