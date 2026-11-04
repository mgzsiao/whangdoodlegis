"""Turning the coloured classes of a georeferenced map into GIS polygons.

Presets are measured from MGB detailed geohazard maps (Region 10, 2012). The map
FILL colours differ from the legend swatches (JPEG export / transparency), e.g.
'moderate landslide' is (56,167,2) on the map but (0,128,1) in the legend, so
both are listed. Debris-flow zones are green with white diagonal hatching and
are found by texture, not colour.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

NONE_CODE = 255      # "not susceptible" / plain background (kept for filling, not exported)


@dataclass
class MapClass:
    code: int
    name: str
    colors: list
    description: str = ""
    display: tuple = (128, 128, 128)
    export: bool = True


@dataclass
class Preset:
    key: str
    label: str
    classes: list
    hatch: tuple | None = None        # (code, base code) for hatched classes
    tolerance: float = 48.0
    white_is_unknown: bool = False    # True when white only appears as halos/text
    # opening iterations per class code: removes line symbols drawn in a class colour
    # (e.g. the red-brown 'Main Road' symbol looks like 'High landslide' red)
    thin_iterations: dict = field(default_factory=dict)


PRESETS = {
    "mgb_landslide": Preset("mgb_landslide", "MGB landslide susceptibility map", [
        MapClass(4, "Very high landslide susceptibility", [(169, 56, 0), (143, 36, 0)],
                 "Steep to very steep slopes, weak materials; recent landslides, escarpments, tension cracks.",
                 (150, 45, 0)),
        MapClass(3, "High landslide susceptibility", [(254, 0, 0), (251, 1, 0)],
                 "Steep to very steep slopes, weak materials; numerous old/inactive landslides.", (240, 0, 0)),
        MapClass(2, "Moderate landslide susceptibility", [(56, 167, 2), (0, 128, 1)],
                 "Moderately steep slopes; soil creep and other indications of possible landslides.",
                 (40, 150, 0)),
        MapClass(1, "Low landslide susceptibility", [(255, 255, 1), (253, 253, 1)],
                 "Gently sloping areas with no identified landslides.", (250, 240, 0)),
        MapClass(5, "Debris flow path / possible accumulation zone", [],
                 "Areas that could be affected by landslide debris.", (90, 170, 60)),
        MapClass(10, "Sea / water body", [(1, 197, 255)], "", (0, 190, 250)),
        MapClass(NONE_CODE, "White / unmapped", [(255, 255, 255)], "", (255, 255, 255), export=False),
    ], hatch=(5, 2), white_is_unknown=True, thin_iterations={3: 2, 4: 2, 10: 2}),
    "mgb_flood": Preset("mgb_flood", "MGB flood susceptibility map", [
        MapClass(3, "High flood susceptibility", [(82, 7, 253), (90, 0, 254)],
                 "Flood heights > 1 m and/or > 3 days; flooded during heavy rain of several hours; "
                 "active/abandoned river channels, river banks; flashflood prone.", (85, 0, 255)),
        MapClass(2, "Moderate flood susceptibility", [(174, 68, 254), (175, 69, 255)],
                 "Flood heights 0.5-1 m and/or 1-3 days during prolonged heavy rain; fluvial terraces, "
                 "alluvial fans, infilled valleys.", (175, 70, 255)),
        MapClass(1, "Low flood susceptibility", [(227, 209, 255)],
                 "Flood heights < 0.5 m and/or < 1 day; low hills and gentle slopes.", (225, 210, 255)),
        MapClass(10, "Sea / water body", [(1, 197, 255)], "", (0, 190, 250)),
        MapClass(NONE_CODE, "Not mapped as flood-susceptible", [(255, 255, 255)], "", (255, 255, 255),
                 export=False),
    ], thin_iterations={10: 2}),
}


def classify(rgb: np.ndarray, preset: Preset, region: np.ndarray | None = None,
             fill_px: int = 25, min_px: int = 60) -> np.ndarray:
    """Per-pixel class codes (uint8; 0 = unknown). region: bool mask of the map area."""
    from scipy import ndimage as ndi

    H, W = rgb.shape[:2]
    img = rgb.astype(np.int16)
    best_d = np.full((H, W), 1e9, dtype=np.float32)
    codes = np.zeros((H, W), dtype=np.uint8)
    for mc in preset.classes:
        for col in mc.colors:
            d = np.sqrt(((img - np.array(col, dtype=np.int16)) ** 2).sum(axis=2, dtype=np.int32)).astype(np.float32)
            better = d < best_d
            best_d[better] = d[better]
            codes[better] = mc.code
    codes[best_d > preset.tolerance] = 0

    if preset.hatch:
        # hatched zones: green hue everywhere, but strongly striped (green/white blend)
        hcode, _base = preset.hatch
        R, Gc, B = img[..., 0], img[..., 1], img[..., 2]
        lum = img.sum(axis=2).astype(np.float32) / 3.0
        greenish = ((Gc - R) > 25) & ((Gc - B) > 25)
        whiteish = (lum > 200) & (np.abs(R - B) < 70)
        stripe = greenish | whiteish
        win = 7
        m1 = ndi.uniform_filter(lum, win)
        m2 = ndi.uniform_filter(lum * lum, win)
        with np.errstate(invalid="ignore"):
            std = np.sqrt(np.clip(np.nan_to_num(m2 - m1 * m1), 0, None))
        f_stripe = ndi.uniform_filter(stripe.astype(np.float32), win)
        f_light = ndi.uniform_filter((lum > 150).astype(np.float32), win)
        f_green = ndi.uniform_filter(greenish.astype(np.float32), win)
        seed = (f_stripe > 0.6) & (f_green > 0.3) & (f_light > 0.15) & (std > 18) & stripe
        seed = ndi.binary_opening(seed, iterations=1)
        lab, n = ndi.label(seed)
        if n:   # drop specks (e.g. white text halos on green)
            sizes = ndi.sum(np.ones_like(lab), lab, index=np.arange(1, n + 1))
            seed = np.isin(lab, np.nonzero(sizes >= 150)[0] + 1)
        # grow over the stripes and the lines (roads/rivers) running inside the zone
        allowed = stripe | (lum < 120) | ((B - R) > 60)
        hatch = ndi.binary_closing(seed, iterations=4) & allowed
        hatch = ndi.binary_fill_holes(ndi.binary_closing(hatch, iterations=2))
        codes[hatch] = hcode
    if preset.white_is_unknown:
        codes[codes == NONE_CODE] = 0      # white = symbol halos / text, not a map class

    # thin features (road symbols, river lines, frame lines) are not areas: drop them
    for mc in preset.classes:
        m = codes == mc.code
        it = preset.thin_iterations.get(mc.code, 1)
        if m.any() and it:
            opened = ndi.binary_opening(m, structure=np.ones((3, 3), bool), iterations=it)
            codes[m & ~opened] = 0

    if region is not None:
        codes[~region] = 0
    # fill unknown pixels (roads, rivers drawn as lines, text, symbols) from the nearest class
    unknown = codes == 0
    if region is not None:
        unknown &= region
    if unknown.any() and (codes > 0).any():
        dist, (iy, ix) = ndi.distance_transform_edt(codes == 0, return_indices=True)
        fillable = unknown & (dist <= fill_px)
        codes[fillable] = codes[iy[fillable], ix[fillable]]

    # majority clean-up and removal of tiny islands
    present = [mc.code for mc in preset.classes if (codes == mc.code).any()]
    if present:
        stack = np.stack([ndi.uniform_filter((codes == c).astype(np.float32), 5) for c in present])
        maj = np.array(present, dtype=np.uint8)[np.argmax(stack, axis=0)]
        keep = codes > 0
        codes = np.where(keep, maj, 0).astype(np.uint8)
        small = np.zeros_like(keep)
        for c in present:
            lab, n = ndi.label(codes == c)
            if n:
                sizes = ndi.sum(np.ones_like(lab), lab, index=np.arange(1, n + 1))
                small |= np.isin(lab, np.nonzero(sizes < min_px)[0] + 1)
        if small.any():
            codes[small] = 0
            dist, (iy, ix) = ndi.distance_transform_edt(codes == 0, return_indices=True)
            fix = small & (dist <= 25)
            codes[fix] = codes[iy[fix], ix[fix]]
    if region is not None:
        codes[~region] = 0
    return codes


def region_mask(shape, polygon) -> np.ndarray:
    from PIL import Image, ImageDraw
    im = Image.new("1", (shape[1], shape[0]), 0)
    ImageDraw.Draw(im).polygon([tuple(p) for p in polygon], fill=1)
    return np.asarray(im, dtype=bool)


def colorize_codes(codes: np.ndarray, preset: Preset) -> np.ndarray:
    out = np.zeros(codes.shape + (4,), np.uint8)
    for mc in preset.classes:
        m = codes == mc.code
        out[m, :3] = mc.display
        out[m, 3] = 0 if mc.code == NONE_CODE else 255
    return out


def class_polygons(codes: np.ndarray, preset: Preset, fit, simplify_px: float = 0.8):
    """Polygons in the fit's working CRS with class name/description/area."""
    import shapely
    from .exporters import Feature, polygons_from_mask
    from affine import Affine

    names = {mc.code: mc for mc in preset.classes}
    mask = np.isin(codes, [mc.code for mc in preset.classes if mc.export])
    polys = polygons_from_mask(mask, Affine.identity(), simplify=simplify_px, min_area=4.0, values=codes)

    def to_work(c):
        x, y = fit.forward(c[:, 0], c[:, 1])
        return np.column_stack([x, y])
    feats = []
    for g, val in polys:
        mc = names.get(val)
        if mc is None or not mc.export:
            continue
        gw = shapely.transform(g, to_work)
        if not gw.is_valid:
            gw = gw.buffer(0)
        feats.append(Feature(gw, {"class": mc.name, "code": int(mc.code),
                                  "area_ha": round(gw.area / 1e4, 4), "descr": mc.description}))
    return feats


def area_summary(feats) -> dict:
    out: dict[str, float] = {}
    for f in feats:
        out[f.properties["class"]] = out.get(f.properties["class"], 0.0) + f.properties["area_ha"]
    return {k: round(v, 2) for k, v in out.items()}
