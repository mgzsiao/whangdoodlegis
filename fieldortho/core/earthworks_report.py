"""Earthworks plan -> PDF report, Excel workbook, CSV, GIS layers and rasters (one folder, for engineers)."""
from __future__ import annotations

import csv
import math
import os
import shutil
import tempfile
from datetime import datetime

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont

from .. import APP_NAME, DISCLAIMER_PARAGRAPHS
from . import earthworks as ew, exporters as ex, measure as ms
from .terrain import Dem, write_array

AMBER = (245, 184, 65)
NAVY = (31, 63, 119)
INK = (26, 34, 51)
MUTED = (93, 103, 122)
CUT_RGB = (196, 40, 32)
FILL_RGB = (30, 90, 170)
GROUND_RGB = (138, 106, 58)

EARTHWORKS_NOTES = (
    "Volumes are estimates from a drone elevation model, for planning and budgeting. Check them against a "
    "ground survey before using them for contracts or payments.",
    "All volumes are as measured in the ground. Soil loosens when excavated (swell) and packed fill needs extra soil "
    "(shrink); the hauling figures include the factors shown.",
)


def _font(size: int, bold: bool = False):
    for name in (("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else
                 ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ============================================================================ quantities
def source_label(source: str) -> str:
    return ms.SOURCES.get(source, source)


def quantities(t: ew.Takeoff) -> list[tuple[str, str, str]]:
    """(label, value, note) rows, in report order."""
    a, h = t.area, t.haul
    rows = [("Flat area (as on a map)", f"{a.area_2d:,.1f} m²", f"{a.area_2d / 1e4:.4f} ha"),
            ("Surface area (following the slopes)", f"{a.area_3d:,.1f} m²", ""),
            ("Perimeter", f"{a.perimeter_2d:,.2f} m", f"along the ground {a.perimeter_3d:,.2f} m"),
            ("Existing ground inside", f"{a.z_min:.2f} – {a.z_max:.2f} m", f"average {a.z_mean:.2f} m")]
    if t.kind == "pad":
        lvl = (f"{t.level_high:.2f} → {t.level_low:.2f} m" if t.slope_pct > 0 else f"{t.level:.2f} m")
        rows += [("Finished ground", lvl, f"{t.level:.2f} m at the centre"),
                 ("Cut inside the outline", f"{t.pad_cut:,.1f} m³", ""),
                 ("Fill inside the outline", f"{t.pad_fill:,.1f} m³", "")]
        if t.side_cut == t.side_cut and (t.side_cut > 0 or t.side_fill > 0):
            rows += [("Cut on the sloped sides", f"{t.side_cut:,.1f} m³", ""),
                     ("Fill on the sloped sides", f"{t.side_fill:,.1f} m³", ""),
                     ("Ground worked, sides included", f"{t.works_area:,.1f} m²", "")]
    if t.kind == "pile":
        rows += [("Measured from", t.reference_text, ""),
                 ("ABOVE (PILE) total", f"{t.cut:,.1f} m³", f"over {t.cut_area:,.1f} m², highest {t.max_cut:.2f} m"),
                 ("BELOW (HOLE) total", f"{t.fill:,.1f} m³", f"over {t.fill_area:,.1f} m², deepest {t.max_fill:.2f} m"),
                 ("Net (above − below)", f"{t.net:,.1f} m³", "")]
    else:
        rows += [("CUT total", f"{t.cut:,.1f} m³", f"over {t.cut_area:,.1f} m², deepest {t.max_cut:.2f} m"),
                 ("FILL total", f"{t.fill:,.1f} m³", f"over {t.fill_area:,.1f} m², deepest {t.max_fill:.2f} m"),
                 ("Net (cut − fill)", f"{t.net:,.1f} m³", ""),
                 ("Total earth moved (cut + fill)", f"{t.total:,.1f} m³", "")]
    if t.kind == "pad":
        rows.append(("Soil needed for the fill", f"{h.needed:,.1f} m³", f"fill + {h.shrink_pct:g}% for packing"))
    if h.balance >= 0:
        rows.append(("Extra soil to haul away", f"{h.balance:,.1f} m³", f"{h.loose:,.1f} m³ loose (+{h.swell_pct:g}%)"))
    else:
        rows.append(("Soil to bring in", f"{-h.balance:,.1f} m³", f"{h.loose:,.1f} m³ loose (+{h.swell_pct:g}%)"))
    rows.append(("Truck trips", "  ·  ".join(f"{n:,}" for _s, n in h.trips),
                 "  ·  ".join(f"{s:g} m³ truck" for s, _n in h.trips)))
    return rows


def inputs(t: ew.Takeoff, source: str) -> list[tuple[str, str]]:
    return [(k, source_label(source) if k == "Heights from" else v) for k, v in t.inputs]


# ============================================================================ pictures
def _target_grid(dem_path: str, bounds, max_px: int) -> Dem:
    return ms.read_area(dem_path, bounds, max_cells=max_px * max_px, pad_cells=0)


def map_image(t: ew.Takeoff, outline, dem_path: str, ortho_path: str | None, max_px: int = 1600) -> Image.Image:
    """Drone photo (or shaded relief) with cut/fill colours, the outline, scale bar, north arrow and legend."""
    from rasterio.warp import Resampling, reproject
    from .model_export import background_for
    h, w = t.dz.shape
    tr = t.transform
    x0, x1 = tr.c, tr.c + w * tr.a
    y1, y0 = tr.f, tr.f + h * tr.e
    m = max(10.0, 0.25 * max(x1 - x0, y1 - y0))
    grid = _target_grid(dem_path, (x0 - m, y0 - m, x1 + m, y1 + m), max_px)
    rgb = background_for(grid, ortho_path).astype(np.float32)
    dz = np.full(grid.shape, np.nan)
    crs = rasterio.crs.CRS.from_wkt(t.crs_wkt)
    reproject(t.dz, dz, src_transform=tr, src_crs=crs, dst_transform=grid.transform, dst_crs=crs,
              src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.average)
    ov = ms.overlay_rgba(dz).astype(np.float32)
    a = ov[..., 3:4] / 255.0
    rgb = rgb * (1 - a) + ov[..., :3] * a
    im = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    scale = 1.0
    if im.width < 1000:                      # small sites: upscale so lines and text stay crisp
        scale = 1000 / im.width
        im = im.resize((1000, int(round(im.height * scale))), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    gt = grid.transform

    def px(x, y):
        return ((x - gt.c) / gt.a * scale, (y - gt.f) / gt.e * scale)
    ring = [px(*p) for p in outline] + [px(*outline[0])]
    lw = max(3, im.width // 400)
    d.line(ring, fill=INK, width=lw + 2, joint="curve")
    d.line(ring, fill=AMBER, width=lw, joint="curve")
    # scale bar
    W = im.width
    metres_per_px = gt.a / scale
    target = W * 0.22 * metres_per_px
    nice = ms_nice(target)
    bar = nice / metres_per_px
    f = _font(max(14, W // 70))
    bx, by = 20, im.height - 30
    d.rectangle([bx - 10, by - 34, bx + bar + 70, by + 16], fill=(255, 255, 255))
    d.rectangle([bx, by - 6, bx + bar, by], fill=INK)
    d.text((bx, by - 30), "0", font=f, fill=INK)
    d.text((bx + bar - 10, by - 30), f"{nice:g} m", font=f, fill=INK)
    # north arrow
    nx, ny = W - 40, 20
    d.rectangle([nx - 22, ny - 6, nx + 22, ny + 54], fill=(255, 255, 255))
    d.polygon([(nx, ny), (nx - 10, ny + 28), (nx + 10, ny + 28)], fill=INK)
    d.text((nx - 6, ny + 30), "N", font=_font(max(14, W // 70), True), fill=INK)
    # legend
    lf = _font(max(13, W // 80))
    items = [(CUT_RGB, "Cut"), (FILL_RGB, "Fill"), (AMBER, "Outline")]
    lx, ly = 20, 16
    d.rectangle([lx - 8, ly - 8, lx + 150, ly + 28 * len(items)], fill=(255, 255, 255))
    for i, (col, txt) in enumerate(items):
        d.rectangle([lx, ly + i * 28 + 2, lx + 22, ly + i * 28 + 18], fill=col)
        d.text((lx + 32, ly + i * 28), txt, font=lf, fill=INK)
    return im


def ms_nice(target: float) -> float:
    if target <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(target))
    for k in (5, 2, 1):
        if p * k <= target:
            return p * k
    return p


# ============================================================================ tables
def worked_example(t: ew.Takeoff, qty: list) -> list[str]:
    """Step-by-step check with this plan's numbers (plain text paragraphs)."""
    out = []
    cell_a = abs(t.transform.a * t.transform.e)
    dz = t.dz[np.isfinite(t.dz)]
    n_c, n_f = int((dz > 0).sum()), int((dz < 0).sum())
    out.append(f"Grid method (used for the totals): the ground worked is split into {dz.size:,} cells of "
               f"{abs(t.transform.a):.2f} \u00d7 {abs(t.transform.e):.2f} m = {cell_a:.4f} m\u00b2. In each cell, "
               f"depth = ground now \u2212 finished level. Cut = sum of the positive depths \u00d7 cell area = "
               f"{float(dz[dz > 0].sum()):,.2f} m \u00d7 {cell_a:.4f} m\u00b2 = {float(dz[dz > 0].sum()) * cell_a:,.2f} m\u00b3 "
               f"({n_c:,} cells). Fill = {float(-dz[dz < 0].sum()):,.2f} m \u00d7 {cell_a:.4f} m\u00b2 = "
               f"{float(-dz[dz < 0].sum()) * cell_a:,.2f} m\u00b3 ({n_f:,} cells).")
    body = [r for r in qty if r[0][:1].isdigit()]
    if len(body) > 1:
        i = max(range(1, len(body)), key=lambda k: float(body[k][4]) + float(body[k][5]))
        a, b = body[i - 1], body[i]
        out.append(f"End-area check between sections {a[0]} and {b[0]}: L = {b[1]} m. Cut V = (A1 + A2) / 2 \u00d7 L = "
                   f"({a[2]} + {b[2]}) / 2 \u00d7 {b[1]} = {b[4]} m\u00b3; fill V = ({a[3]} + {b[3]}) / 2 \u00d7 "
                   f"{b[1]} = {b[5]} m\u00b3. The end-area totals differ a little from the grid totals because "
                   "sections only sample the ground every few metres.")
    if t.kind == "pad":
        h = t.haul
        out.append(f"Soil needed for the fill = fill \u00d7 (1 + shrink) = {t.fill:,.2f} \u00d7 "
                   f"{1 + h.shrink_pct / 100:.2f} = {h.needed:,.2f} m\u00b3. Truck trips = loose volume \u00f7 truck "
                   f"size, rounded up; loose volume = volume \u00d7 (1 + swell {h.swell_pct:g}%).")
    return out


def write_xlsx(path: str, title: str, meta: list, q: list, inp: list, spots: list, notes: list, qty=(),
               worked=()) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="1F3F77")
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=14)
    r = 3
    for k, v in meta:
        ws.cell(r, 1, k).font = Font(bold=True)
        ws.cell(r, 2, v)
        r += 1
    r += 1
    for c, h in enumerate(("Quantity", "Value", "Note"), 1):
        ws.cell(r, c, h).font = head
        ws.cell(r, c).fill = fill
    for k, v, n in q:
        r += 1
        ws.cell(r, 1, k)
        ws.cell(r, 2, v)
        ws.cell(r, 3, n)
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 46
    wi = wb.create_sheet("Inputs")
    for c, h in enumerate(("Setting", "Value"), 1):
        wi.cell(1, c, h).font = head
        wi.cell(1, c).fill = fill
    for i, (k, v) in enumerate(inp, 2):
        wi.cell(i, 1, k)
        wi.cell(i, 2, v)
    wi.column_dimensions["A"].width = 40
    wi.column_dimensions["B"].width = 70
    wg = wb.create_sheet("Spot levels")
    cols = ("Point", "Easting (m)", "Northing (m)", "Ground now (m)", "Finished (m)", "Cut + / Fill − (m)")
    for c, h in enumerate(cols, 1):
        wg.cell(1, c, h).font = head
        wg.cell(1, c).fill = fill
        wg.column_dimensions[chr(64 + c)].width = 18
    for i, row in enumerate(spots, 2):
        for c, v in enumerate(row, 1):
            cell = wg.cell(i, c, v)
            if c >= 2:
                cell.number_format = "0.000"
    wg.freeze_panes = "A2"
    if qty:
        from .pad_sections import QTY_HEADER
        wq = wb.create_sheet("Quantities by section")
        for c, h in enumerate(QTY_HEADER, 1):
            wq.cell(1, c, h).font = head
            wq.cell(1, c).fill = fill
            wq.column_dimensions[chr(64 + c)].width = 24 if c == 1 else 16
        for i, row in enumerate(qty, 2):
            for c, v in enumerate(row, 1):
                try:
                    wq.cell(i, c, float(v) if c > 1 and v != "" else v)
                except ValueError:
                    wq.cell(i, c, v)
    if worked:
        ww = wb.create_sheet("Worked example")
        ww.column_dimensions["A"].width = 130
        for i, tx in enumerate(worked, 1):
            ww.cell(i, 1, tx).alignment = Alignment(wrap_text=True, vertical="top")
    wn = wb.create_sheet("Notes")
    wn.column_dimensions["A"].width = 120
    for i, n in enumerate(notes, 1):
        wn.cell(i, 1, n).alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)
    return path


def write_csv(path: str, header, rows) -> str:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


# ============================================================================ PDF


# ============================================================================ package
def job_quality(job, t: ew.Takeoff, source: str, dem_path: str) -> list[tuple[str, str]]:
    with rasterio.open(dem_path) as ds:
        native = abs(ds.transform.a)
    q = [("Heights from", source_label(source)),
         ("Elevation model detail", f"{native:.2f} m cells" + (f" (calculated on {t.cell:.2f} m cells for speed)"
                                                                if t.cell > native * 1.01 else ""))]
    if job is not None:
        o = job.options
        gcp = bool(o.gcp_file)
        err = job.summary.get("gcp_error_m")
        q.append(("Ground control points", ("used" + (f", average error {err:.2f} m" if err is not None else ""))
                  if gcp else "none: heights can be off by a few metres overall; height differences and volumes "
                              "are much more reliable"))
        q.append(("Heights", {"as_recorded": "as recorded by the drone", "egm96": "above mean sea level (EGM96)",
                              "offset": f"drone heights shifted by {o.height_offset_m:+.2f} m"}.get(o.heights, o.heights)))
        q.append(("Drone map", f"{job.name} (processed {job.finished[:10] or job.created[:10]})"))
    q.append(("Missing height data", f"{t.no_data_pct:.1f}%"))
    if source == "dtm":
        q.append(("Note", "The bare-ground model removes trees and buildings; dense vegetation can still leave "
                          "errors."))
    return q


def export_package(t: ew.Takeoff, plan: dict, dem_path: str, ortho_path: str | None, out_dir: str, dst_crs: str,
                   info: dict, job=None, gis_formats=("gpkg", "kml"), rasters: bool = True, spacing: float = 5.0,
                   progress=None) -> list[str]:
    """Writes the whole package. info: project, location, prepared_by."""
    progress = progress or (lambda p, s: None)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, ew.safe(plan.get("name", "earthworks")).replace(" ", "_"))
    outline = [tuple(p) for p in plan["points"]]
    src = t.crs_wkt
    lonlat = ex.site_lonlat(dem_path)
    files: list[str] = []
    tmp = tempfile.mkdtemp(prefix="fo_ew_")
    try:
        progress(5, "Spot levels")
        spots = ew.grid_points(t, spacing)
        q = quantities(t)
        inp = inputs(t, plan.get("source", "dtm")) + [("Spot level spacing", f"{spacing:g} m")]
        quality = job_quality(job, t, plan.get("source", "dtm"), dem_path)
        from . import crs as crsmod
        meta = [("Project", info.get("project") or plan.get("name", "")),
                ("Location", info.get("location") or "-"),
                ("Prepared by", info.get("prepared_by") or "-"),
                ("Date", datetime.now().strftime("%d %B %Y")),
                ("Coordinates in files", crsmod.label(dst_crs))]
        title = f"Earthworks estimate - {plan.get('name', '')}"

        progress(15, "GIS layers")
        from shapely.geometry import Point, Polygon
        poly = Polygon(outline)
        out_feats = [ex.Feature(poly, {"name": plan.get("name", ""), "type": ew.TYPES.get(t.kind, t.kind),
                                       "area_m2": round(t.area.area_2d, 2), "cut_m3": round(t.cut, 2),
                                       "fill_m3": round(t.fill, 2), "net_m3": round(t.net, 2),
                                       "level_m": round(t.level, 3) if t.level == t.level else None})]
        cell = t.cell
        works = []
        for mask, kind in ((t.dz > 0.005, "cut"), (t.dz < -0.005, "fill")):
            for g, _v in ex.polygons_from_mask(mask, t.transform,
                                               simplify=cell * 1.5, min_area=(cell * 4) ** 2):
                works.append(ex.Feature(g, {"type": kind, "area_m2": round(g.area, 2)}))
        spot_feats = [ex.Feature(Point(r[1], r[2]), {"point": r[0], "ground_m": r[3], "finished_m": r[4],
                                                     "cut_fill_m": r[5]}) for r in spots]
        layers = [("outline", out_feats), ("cut_fill_zones", works), ("spot_levels", spot_feats)]
        for fmt in gis_formats:
            extn = ex.VECTOR_FORMATS[fmt][1]
            for lname, feats in layers:
                if not feats:
                    continue
                path = base + extn if fmt == "gpkg" else f"{base}_{lname}{extn}"
                files += ex.write_vector(feats, path, fmt, src, dst_crs, layer=lname, lonlat=lonlat,
                                         dxf_layer_field="type" if lname == "cut_fill_zones" else None,
                                         append=(fmt == "gpkg"))
        if rasters:
            progress(35, "Rasters")
            g = Dem(t.dz, t.transform, src, "")
            p = write_array(g, t.dz, os.path.join(tmp, "dz.tif"))
            files += ex.export_raster(p, base + "_cut_fill_depth_m", ["gtiff"], dst_crs, kind="dem", lonlat=lonlat)
            p = write_array(g, t.finished, os.path.join(tmp, "fin.tif"))
            files += ex.export_raster(p, base + ("_finished_ground_m" if t.kind == "pad" else "_reference_surface_m"),
                                      ["gtiff"], dst_crs, kind="dem", lonlat=lonlat)
            from .model_export import _rgba_tif
            rgba = ms.overlay_rgba(t.dz)
            rgba[..., 3] = np.where(np.isfinite(t.dz), 255, 0)
            p = _rgba_tif(g, rgba, os.path.join(tmp, "cf.tif"))
            files += ex.export_raster(p, base + "_cut_fill_map", ["png"], dst_crs, kind="classes", lonlat=lonlat)

        progress(55, "Tables")
        secs, qty, step, along = [], [], 0.0, ""
        if t.kind == "pad":
            from . import pad_sections
            design = ew.PadDesign(**{k: v for k, v in (plan.get("design") or {}).items()
                                     if k in ew.PadDesign.__dataclass_fields__})
            secs, qty, step, along = pad_sections.sections(t, outline, design)
        worked = worked_example(t, qty)
        files.append(write_xlsx(base + "_quantities.xlsx", title, meta, q, inp, spots,
                                list(EARTHWORKS_NOTES + DISCLAIMER_PARAGRAPHS), qty, worked))
        if qty:
            from .pad_sections import QTY_HEADER
            files.append(write_csv(base + "_quantities_by_section.csv", QTY_HEADER, qty))
        files.append(write_csv(base + "_quantities.csv", ("Quantity", "Value", "Note"), q))
        files.append(write_csv(base + "_spot_levels.csv", ("Point", "Easting_m", "Northing_m", "Ground_m",
                                                           "Finished_m", "Cut+_Fill-_m"), spots))
        progress(62, "Civil 3D (LandXML) and CAD drawing")
        from . import cad, landxml
        files.append(landxml.surfaces_xml(base + "_civil3d.xml", t, plan.get("name", ""), dem_path))
        files.append(cad.pad_dxf(base + "_drawing.dxf", t, outline, spots, plan.get("name", "")))
        plan_copy = base + ".plan.json"
        import json
        with open(plan_copy, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=1, ensure_ascii=False)
        files.append(plan_copy)

        progress(70, "Pictures")
        mimg = map_image(t, outline, dem_path, ortho_path)
        mimg.save(base + "_map.png")
        files.append(base + "_map.png")

        readme = base + "_README.txt"
        notes = list(EARTHWORKS_NOTES) + [f"Spot levels every {spacing:g} m over the ground worked "
                                          "(cut + / fill - = ground now minus finished)."]
        for fmt in gis_formats:
            n = ex.format_note(fmt, dst_crs)
            if n and n not in notes:
                notes.append(n)
        pdf_path = base + "_report.pdf"
        listed = [f for f in dict.fromkeys([pdf_path] + files + [readme])
                  if not f.lower().endswith((".aux.xml", ".pgw", ".wld", ".jgw", ".tfw"))]
        progress(85, "PDF report")
        from . import lot_report
        lot_report.write_lot_report(pdf_path, t, plan, info, dem_path, ortho_path, job, dst_crs, listed, mimg, q, inp,
                                    quality, spots, secs, qty, step, along, worked)
        files.insert(0, pdf_path)
        ex.write_export_readme(readme, f"{APP_NAME} {title}", dst_crs, lonlat, files, notes)
        files.append(readme)
        progress(100, "Done")
        return list(dict.fromkeys(files))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
