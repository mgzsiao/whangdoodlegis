"""Shared state passed between wizard pages."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core import engine
from ..core.jobs import Job
from ..core.photos import Photo, Survey
from ..core.settings import Settings


@dataclass
class Session:
    settings: Settings
    engine: engine.EngineInfo
    job: Job | None = None
    photos: list[Photo] = field(default_factory=list)
    survey: Survey | None = None
    flight_height: float | None = None

    def refresh_engine(self):
        s = self.settings
        self.engine = engine.detect(s.odm_path, s.engine_mode, s.docker_image)

    def selected_photos(self) -> list[Photo]:
        excl = self.job.options.exclude_blurry if self.job else True
        return [p for p in self.photos if p.has_gps and not (excl and p.blurry)]
