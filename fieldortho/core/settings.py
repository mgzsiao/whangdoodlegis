"""Persistent app settings (stored per user)."""
from __future__ import annotations

from .. import APP_NAME, OLD_APP_NAMES

import json
import os
import shutil
from dataclasses import dataclass, asdict, field

from .system import IS_WINDOWS, cpu_count


def _config_base() -> str:
    if IS_WINDOWS:
        return os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def config_dir() -> str:
    d = os.path.join(_config_base(), APP_NAME)
    if not os.path.isdir(d):
        _migrate_old_config(d)
    os.makedirs(d, exist_ok=True)
    return d


def _migrate_old_config(new_dir: str) -> None:
    """First start after a rename: carry over settings (jobs folder, recent jobs, engine path)
    and the activity list from the newest earlier app name."""
    for old in OLD_APP_NAMES:
        src = os.path.join(_config_base(), old)
        if os.path.isfile(os.path.join(src, "settings.json")):
            try:
                os.makedirs(new_dir, exist_ok=True)
                for f in ("settings.json", "activity.json"):
                    if os.path.isfile(os.path.join(src, f)):
                        shutil.copy2(os.path.join(src, f), os.path.join(new_dir, f))
            except OSError:
                pass
            return


def default_jobs_root() -> str:
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), f"{APP_NAME} Jobs")


@dataclass
class Settings:
    odm_path: str = ""            # folder containing run.bat / run.sh, or empty = auto-detect
    engine_mode: str = "native"   # "native" or "docker"
    docker_image: str = "opendronemap/odm:latest"
    jobs_root: str = field(default_factory=default_jobs_root)
    threads: int = field(default_factory=cpu_count)
    keep_intermediate: bool = True   # allows resuming a failed job
    recent_jobs: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Settings":
        path = os.path.join(config_dir(), "settings.json")
        s = cls()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if hasattr(s, k):
                    setattr(s, k, v)
        except (OSError, ValueError):
            pass
        return s

    def save(self) -> None:
        path = os.path.join(config_dir(), "settings.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp, path)

    def add_recent(self, job_dir: str) -> None:
        job_dir = os.path.abspath(job_dir)
        self.recent_jobs = [job_dir] + [j for j in self.recent_jobs if j != job_dir]
        self.recent_jobs = self.recent_jobs[:30]
        self.save()
