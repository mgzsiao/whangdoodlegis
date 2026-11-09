"""List of georeferenced maps and flood/landslide scenarios shown in the Jobs list.

Drone mapping jobs keep their own job folder (job.json) and are listed from Settings.recent_jobs.
Georeferencing and modelling have no job folder, so each saved session / export is recorded here:
    %APPDATA%\\WhangDoodleGIS\\activity.json
Each record points at a session file (georef) or a scenario file (flood / landslide) that holds
everything needed to reopen it.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .settings import config_dir

KINDS = {"georef": "Georeference", "flood": "Flood scenario", "landslide": "Landslide scenario"}
MAX_ITEMS = 200


@dataclass
class Activity:
    kind: str                      # georef | flood | landslide
    name: str
    path: str                      # session / scenario file to reopen
    folder: str = ""               # output folder (Show in folder)
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    size: str = ""                 # e.g. "2560 x 2327 px" or "DTM 0.5 m"
    crs: str = ""                  # coordinate system label
    status: str = ""               # e.g. "Exported" / "Saved"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def kind_label(self) -> str:
        return KINDS.get(self.kind, self.kind)

    def exists(self) -> bool:
        return os.path.isfile(self.path)


def _path() -> str:
    return os.path.join(config_dir(), "activity.json")


def load() -> list[Activity]:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        try:
            out.append(Activity(**{k: v for k, v in d.items() if k in Activity.__dataclass_fields__}))
        except TypeError:
            continue
    return out


def save_all(items: list[Activity]) -> None:
    p = _path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump([asdict(a) for a in items[:MAX_ITEMS]], f, indent=1)
    os.replace(tmp, p)


def record(item: Activity) -> Activity:
    """Add an item, replacing an earlier record of the same file (newest first)."""
    key = os.path.normcase(os.path.abspath(item.path))
    items = [a for a in load() if os.path.normcase(os.path.abspath(a.path)) != key]
    item.path = os.path.abspath(item.path)
    try:
        save_all([item] + items)
    except OSError:
        pass
    return item


def remove(item_id: str) -> None:
    try:
        save_all([a for a in load() if a.id != item_id])
    except OSError:
        pass
