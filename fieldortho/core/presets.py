"""Processing presets: plain-language choices mapped to OpenDroneMap options."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    elevation: bool          # can produce DSM/DTM
    odm_args: tuple[str, ...]


PRESETS: dict[str, Preset] = {
    "quick": Preset(
        "quick", "Quick preview",
        "Fast check while still in the field. Orthomosaic only, lower detail. "
        "Use it to confirm the flight covered the whole site.",
        elevation=False,
        odm_args=("--fast-orthophoto", "--feature-quality", "medium",
                  "--orthophoto-resolution", "8", "--skip-3dmodel"),
    ),
    "standard": Preset(
        "standard", "Standard",
        "Good orthomosaic plus surface (DSM) and terrain (DTM) models. "
        "Right for most mapping jobs.",
        elevation=True,
        odm_args=("--feature-quality", "high", "--pc-quality", "medium",
                  "--orthophoto-resolution", "3", "--dem-resolution", "6",
                  "--skip-3dmodel"),
    ),
    "high": Preset(
        "high", "High detail",
        "Full-resolution orthomosaic and denser elevation models. Takes several times "
        "longer and needs a powerful computer (32 GB RAM or more).",
        elevation=True,
        odm_args=("--feature-quality", "ultra", "--pc-quality", "high",
                  "--orthophoto-resolution", "1", "--dem-resolution", "3",
                  "--skip-3dmodel"),
    ),
}

# Always applied
COMMON_ARGS = ("--auto-boundary", "--cog", "--build-overviews", "--dem-gapfill-steps", "3")


def build_args(preset_key: str, *, dsm: bool, dtm: bool, threads: int,
               gcp_file: str | None = None, geo_file: str | None = None,
               rerun_from: str | None = None) -> list[str]:
    p = PRESETS[preset_key]
    args = list(p.odm_args) + list(COMMON_ARGS)
    if p.elevation:
        if dsm:
            args.append("--dsm")
        if dtm:
            args.append("--dtm")
    if threads:
        args += ["--max-concurrency", str(threads)]
    if gcp_file:
        args += ["--gcp", gcp_file]
    if geo_file:
        args += ["--geo", geo_file]
    if rerun_from:
        args += ["--rerun-from", rerun_from]
    return args


# ODM stage name -> (friendly text, progress % when the stage *finishes*)
STAGES: list[tuple[str, str, float]] = [
    ("dataset", "Reading photos", 5),
    ("split", "Preparing", 6),
    ("merge", "Preparing", 7),
    ("opensfm", "Matching photos and finding camera positions", 25),
    ("openmvs", "Building the dense 3D point cloud", 50),
    ("odm_filterpoints", "Cleaning the point cloud", 52),
    ("odm_meshing", "Building the surface", 60),
    ("mvs_texturing", "Colouring the surface with the photos", 70),
    ("odm_georeferencing", "Placing everything on the map", 80),
    ("odm_dem", "Creating elevation models (DSM / DTM)", 90),
    ("odm_orthophoto", "Creating the orthomosaic", 98),
    ("odm_report", "Writing the quality report", 99),
    ("odm_postprocess", "Finishing up", 100),
]
STAGE_TEXT = {k: t for k, t, _ in STAGES}
STAGE_START = {}
_prev = 0.0
for _k, _t, _p in STAGES:
    STAGE_START[_k] = _prev
    _prev = _p
