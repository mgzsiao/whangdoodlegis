"""Persistent app settings (stored per user)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field

from .system import IS_WINDOWS, cpu_count


def config_dir() -> str:
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    d = os.path.join(base, "FieldOrtho")
    os.makedirs(d, exist_ok=True)
    return d


def default_jobs_root() -> str:
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "FieldOrtho Jobs")


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
