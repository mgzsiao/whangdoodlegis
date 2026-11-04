"""Ground control points: load surveyed coordinates, find candidate photos,
and write OpenDroneMap's gcp_list.txt.

gcp_list.txt format (ODM):
    EPSG:<code>
    geo_x geo_y geo_z im_x im_y image_name gcp_name
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, asdict

from .gisexport import read_points_csv
from . import crs as crsmod
from .photos import Photo


@dataclass
class GcpMark:
    image: str
    px: float
    py: float


@dataclass
class Gcp:
    name: str
    x: float
    y: float
    z: float
    marks: list[GcpMark] = field(default_factory=list)
    is_checkpoint: bool = False


@dataclass
class GcpSet:
    crs: str
    points: list[Gcp] = field(default_factory=list)

    @classmethod
    def from_csv(cls, path: str, crs: str) -> "GcpSet":
        pts = read_points_csv(path)
        return cls(crs=crs, points=[Gcp(p["name"] or f"GCP{i + 1}", p["x"], p["y"], p["z"])
                                    for i, p in enumerate(pts)])

    # -- persistence of in-progress tagging -------------------------------
    def save_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load_json(cls, path: str) -> "GcpSet":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        pts = []
        for p in d.get("points", []):
            marks = [GcpMark(**m) for m in p.pop("marks", [])]
            pts.append(Gcp(marks=marks, **p))
        return cls(crs=d["crs"], points=pts)

    # -- ODM file ------------------------------------------------------------
    def tagged(self, min_marks: int = 3) -> list[Gcp]:
        return [g for g in self.points if not g.is_checkpoint and len(g.marks) >= min_marks]

    def write_odm(self, path: str, min_marks: int = 3) -> int:
        rows = 0
        epsg_line = self.crs if self.crs.upper().startswith("EPSG:") else self.crs
        with open(path, "w", encoding="utf-8") as f:
            f.write(epsg_line + "\n")
            for g in self.tagged(min_marks):
                for m in g.marks:
                    f.write(f"{g.x:.4f} {g.y:.4f} {g.z:.4f} {m.px:.1f} {m.py:.1f} {m.image} {g.name}\n")
                    rows += 1
        return rows

    def write_checkpoints_csv(self, path: str) -> int:
        cps = [g for g in self.points if g.is_checkpoint]
        with open(path, "w", encoding="utf-8") as f:
            f.write("name,x,y,z\n")
            for g in cps:
                f.write(f"{g.name},{g.x},{g.y},{g.z}\n")
        return len(cps)


def candidate_photos(gcp: Gcp, gcp_crs: str, photos: list[Photo], survey_epsg: int,
                     footprint_m: tuple[float, float] | None, limit: int = 12) -> list[tuple[Photo, float]]:
    """Photos whose (approximate) footprint should contain the GCP, nearest first."""
    xs, ys = crsmod.transform([gcp.x], [gcp.y], gcp_crs, f"EPSG:{survey_epsg}")
    gx, gy = xs[0], ys[0]
    radius = (max(footprint_m) * 0.6) if footprint_m else 80.0
    out = []
    for p in photos:
        if p.x is None:
            continue
        d = math.hypot(p.x - gx, p.y - gy)
        if d <= radius:
            out.append((p, d))
    out.sort(key=lambda t: t[1])
    return out[:limit]


def summary(gs: GcpSet) -> str:
    ready = len(gs.tagged())
    cps = sum(1 for g in gs.points if g.is_checkpoint)
    return f"{ready} of {len(gs.points) - cps} control points tagged in 3+ photos" + \
        (f", {cps} check point(s)" if cps else "")
