"""Earthworks plan -> PDF report, Excel workbook, CSV, GIS layers and rasters (one folder, for engineers)."""
from __future__ import annotations

import csv
import html
import math
import os
import shutil
import tempfile
from datetime import datetime

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont

from .. import APP_NAME, DISCLAIMER_PARAGRAPHS, __version__
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
    "All volumes are as measured in the ground. Soil loosens when dug (swell) and packed fill needs extra soil "
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
                 ("Dig inside the outline", f"{t.pad_cut:,.1f} m³", ""),
                 ("Fill inside the outline", f"{t.pad_fill:,.1f} m³", "")]
        if t.side_cut == t.side_cut and (t.side_cut > 0 or t.side_fill > 0):
            rows += [("Dig on the sloped sides", f"{t.side_cut:,.1f} m³", ""),
                     ("Fill on the sloped sides", f"{t.side_fill:,.1f} m³", ""),
                     ("Ground worked, sides included", f"{t.works_area:,.1f} m²", "")]
    if t.kind == "pile":
        rows += [("Measured from", t.reference_text, ""),
                 ("ABOVE (PILE) total", f"{t.cut:,.1f} m³", f"over {t.cut_area:,.1f} m², highest {t.max_cut:.2f} m"),
                 ("BELOW (HOLE) total", f"{t.fill:,.1f} m³", f"over {t.fill_area:,.1f} m², deepest {t.max_fill:.2f} m"),
                 ("Net (above − below)", f"{t.net:,.1f} m³", "")]
    else:
        rows += [("DIG (CUT) total", f"{t.cut:,.1f} m³", f"over {t.cut_area:,.1f} m², deepest {t.max_cut:.2f} m"),
                 ("FILL total", f"{t.fill:,.1f} m³", f"over {t.fill_area:,.1f} m², deepest {t.max_fill:.2f} m"),
                 ("Net (dig − fill)", f"{t.net:,.1f} m³", ""),
                 ("Total earth moved (dig + fill)", f"{t.total:,.1f} m³", "")]
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
    items = [(CUT_RGB, "Dig (cut)"), (FILL_RGB, "Fill"), (AMBER, "Outline")]
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


def section_line(t: ew.Takeoff, outline) -> tuple[np.ndarray, np.ndarray, str]:
    """A line through the middle of the outline: along the drainage slope, else along the longer side."""
    from shapely.geometry import Polygon
    poly = Polygon(outline)
    c = poly.centroid
    if t.kind == "pad" and t.slope_pct > 0 and t.flow_deg is not None:
        az = t.flow_deg
    else:
        x0, y0, x1, y1 = poly.bounds
        az = 90.0 if (x1 - x0) >= (y1 - y0) else 0.0
    ux, uy = math.sin(math.radians(az)), math.cos(math.radians(az))
    h, w = t.dz.shape
    tr = t.transform
    half = 0.5 * math.hypot(w * tr.a, h * tr.e)
    a = np.array([c.x - ux * half, c.y - uy * half])
    b = np.array([c.x + ux * half, c.y + uy * half])
    return a, b, ew.compass_name(az + 180) + " → " + ew.compass_name(az)


def section_image(t: ew.Takeoff, outline, width: int = 1600, height: int = 560) -> Image.Image:
    a, b, label = section_line(t, outline)
    g = Dem(t.ground, t.transform, t.crs_wkt, "")
    f = Dem(t.finished, t.transform, t.crs_wkt, "")
    xy, dist = ms.densify([a, b], max(t.cell, 0.1))
    zg = ms.sample(g, xy[:, 0], xy[:, 1])
    # finished: nearest cell, only where worked
    tr = t.transform
    cc = np.clip(((xy[:, 0] - tr.c) / tr.a).astype(int), 0, t.dz.shape[1] - 1)
    rr = np.clip(((xy[:, 1] - tr.f) / tr.e).astype(int), 0, t.dz.shape[0] - 1)
    zf = t.finished[rr, cc]
    worked = np.isfinite(zf)
    keep = np.isfinite(zg)
    if worked.any():                         # show the worked part plus 20% each side
        i0, i1 = np.flatnonzero(worked)[[0, -1]]
        pad = max(5, int((i1 - i0) * 0.2))
        keep &= (np.arange(len(zg)) >= i0 - pad) & (np.arange(len(zg)) <= i1 + pad)
    d0 = dist[keep][0] if keep.any() else 0.0
    dd, zg, zf = dist[keep] - d0, zg[keep], zf[keep]
    im = Image.new("RGB", (width, height), "white")
    dr = ImageDraw.Draw(im)
    if len(dd) < 2:
        dr.text((20, 20), "No elevation data along the side view.", font=_font(20), fill=INK)
        return im
    zall = np.concatenate([zg, zf[np.isfinite(zf)]])
    z0, z1 = float(np.nanmin(zall)), float(np.nanmax(zall))
    padz = max(0.5, (z1 - z0) * 0.15)
    z0, z1 = z0 - padz, z1 + padz
    L, R, T, B = 110, 30, 60, 70
    pw, ph = width - L - R, height - T - B
    dmax = float(dd[-1]) or 1.0

    def P(di, zi):
        return (L + di / dmax * pw, T + (z1 - zi) / (z1 - z0) * ph)
    f14, f16 = _font(22), _font(24, True)
    for k in range(6):
        zz = z0 + (z1 - z0) * k / 5
        y = T + ph - ph * k / 5
        dr.line([(L, y), (L + pw, y)], fill=(225, 229, 236), width=1)
        dr.text((10, y - 12), f"{zz:.1f} m", font=f14, fill=MUTED)
    step = ms_nice(dmax / 6)
    x = 0.0
    while x <= dmax + 1e-9:
        X = P(x, z0)[0]
        dr.line([(X, T + ph), (X, T + ph + 6)], fill=MUTED, width=2)
        dr.text((X - 20, T + ph + 10), f"{x:g} m", font=f14, fill=MUTED)
        x += step
    # cut / fill shading, column by column
    for i in range(len(dd) - 1):
        if not (np.isfinite(zf[i]) and np.isfinite(zg[i])):
            continue
        xa, xb = P(dd[i], 0)[0], P(dd[i + 1], 0)[0]
        ya, yb = P(0, zg[i])[1], P(0, zf[i])[1]
        if abs(ya - yb) < 0.5:
            continue
        col = CUT_RGB if zg[i] > zf[i] else FILL_RGB
        dr.rectangle([xa, min(ya, yb), max(xb, xa + 1), max(ya, yb)], fill=tuple(int(c * 0.35 + 255 * 0.65)
                                                                                  for c in col))
    pts = [P(x, z) for x, z in zip(dd, zg)]
    dr.line(pts, fill=GROUND_RGB, width=4)
    seg = []
    for x, z in zip(dd, zf):
        if np.isfinite(z):
            seg.append(P(x, z))
        elif seg:
            if len(seg) > 1:
                dr.line(seg, fill=NAVY, width=5)
            seg = []
    if len(seg) > 1:
        dr.line(seg, fill=NAVY, width=5)
    dr.text((L, 14), f"Side view through the middle ({label})", font=f16, fill=INK)
    lx = width - 560
    for col, txt in ((GROUND_RGB, "Ground now"), (NAVY, "Finished" if t.kind == "pad" else "Measured from")):
        dr.rectangle([lx, 24, lx + 30, 30], fill=col)
        dr.text((lx + 40, 12), txt, font=f14, fill=INK)
        lx += 230
    return im


# ============================================================================ tables
def write_xlsx(path: str, title: str, meta: list, q: list, inp: list, spots: list, notes: list) -> str:
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
    cols = ("Point", "Easting (m)", "Northing (m)", "Ground now (m)", "Finished (m)", "Dig + / Fill − (m)")
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
def _ensure_qt():
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.instance() is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _ensure_qt.app = QGuiApplication([])          # keep a reference


def _qimage(im: Image.Image):
    from PySide6.QtGui import QImage
    data = im.convert("RGB").tobytes()
    return QImage(data, im.width, im.height, 3 * im.width, QImage.Format_RGB888).copy()


def write_pdf(path: str, body_html: str, images: dict) -> str:
    _ensure_qt()
    from PySide6.QtCore import QMarginsF, QSizeF, QUrl
    from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPdfWriter, QTextDocument
    w = QPdfWriter(path)
    w.setPageSize(QPageSize(QPageSize.A4))
    w.setPageMargins(QMarginsF(14, 14, 14, 16), QPageLayout.Millimeter)
    w.setTitle("Earthworks estimate")
    w.setCreator(f"{APP_NAME} {__version__}")
    doc = QTextDocument()
    doc.setDefaultFont(QFont("Segoe UI", 9))
    doc.setPageSize(QSizeF(520, 760))            # points: A4 minus margins; print_ scales to the page
    doc.setDocumentMargin(0)
    for name, im in images.items():
        doc.addResource(QTextDocument.ImageResource, QUrl(name), _qimage(im))
    doc.setHtml(body_html)
    doc.print_(w)
    return path


def _table(rows, widths=("45%", "25%", "30%"), header=None, big=False) -> str:
    out = ['<table width="100%" cellspacing="0" cellpadding="3" style="border-collapse: collapse; font-size: 8pt;">']
    if header:
        out.append("<tr>" + "".join(f'<th align="left" width="{w}" style="background:#1F3F77; color:white;">'
                                     f"{html.escape(h)}</th>" for h, w in zip(header, widths)) + "</tr>")
    for i, row in enumerate(rows):
        bg = "#F3F5F9" if i % 2 else "#FFFFFF"
        cells = []
        for j, v in enumerate(row):
            txt = html.escape(str(v))
            if j == 1 and big:
                txt = f"<b>{txt}</b>"
            if j == 0 and str(v).isupper():
                txt = f"<b>{txt}</b>"
            cells.append(f'<td width="{widths[min(j, len(widths) - 1)]}" style="background:{bg};">{txt}</td>')
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</table>")
    return "".join(out)


def report_html(t: ew.Takeoff, plan: dict, meta: list, q: list, inp: list, quality: list, files: list,
                map_size=(1000, 800)) -> str:
    name = html.escape(plan.get("name", "Earthworks"))
    kind = ew.TYPES.get(t.kind, t.kind)
    h = t.haul
    if t.kind == "pile":
        big = [("Above (pile)", f"{t.cut:,.0f} m³"), ("Below (hole)", f"{t.fill:,.0f} m³"),
               ("Net", f"{t.net:,.0f} m³"), ("Area", f"{t.area.area_2d:,.0f} m²")]
    else:
        big = [("Dig (cut)", f"{t.cut:,.0f} m³"), ("Fill", f"{t.fill:,.0f} m³"), ("Net", f"{t.net:,.0f} m³"),
               ("Total moved", f"{t.total:,.0f} m³")]
    cells = "".join(f'<td width="25%" align="center" style="background:#E7EDF7; padding:5px;">'
                    f'<span style="color:#5D677A; font-size:8pt;">{html.escape(k.upper())}</span><br>'
                    f'<span style="font-size:15pt; font-weight:600;">{html.escape(v)}</span></td>' for k, v in big)
    parts = [
        f'<p style="color:#5D677A; font-size:8pt; margin:0;">{APP_NAME} {__version__} · Earthworks estimate</p>',
        f'<h1 style="font-size:16pt; color:#1A2233; margin:2px 0 4px 0;">{name}</h1>',
        f'<p style="font-size:10pt; color:#1F3F77; margin:0 0 8px 0;">{html.escape(kind)}</p>',
        _table(meta, ("30%", "70%")),
        '<h2 style="font-size:12pt; margin-top:8px;">At a glance</h2>',
        f'<p style="font-size:9pt;">{html.escape(t.headline())}<br>{html.escape(h.sentence())}</p>',
        f'<table width="100%" cellspacing="4" cellpadding="0"><tr>{cells}</tr></table>',
    ]
    warn = []
    if t.reaches_edge:
        warn.append("The sloped sides reach the edge of the area read from the drone map (or of the map itself). "
                    "The real side volumes may be larger.")
    if t.no_data_pct > 2:
        warn.append(f"{t.no_data_pct:.0f}% of the outline has no height data; those parts are not counted.")
    for wtxt in warn:
        parts.append(f'<p style="background:#FFF4E0; color:#9A5B00; padding:6px;">⚠ {html.escape(wtxt)}</p>')
    mw, mh = map_size
    scale = min(520 / mw, 500 / mh)
    parts += [
        '<h2 style="font-size:12pt; margin-top:8px;">Quantities</h2>',
        _table(q, header=("Quantity", "Value", "Note"), big=True),
        '<h2 style="font-size:12pt; page-break-before: always;">Map</h2>',
        f'<p align="center"><img src="map.png" width="{int(mw * scale)}" height="{int(mh * scale)}"></p>',
        '<p style="color:#5D677A; font-size:8pt;">Red = ground to dig away, blue = ground to fill; stronger colour = '
        'deeper. Amber line = the outline.</p>',
        '<h2 style="font-size:12pt; margin-top:10px;">Side view</h2>',
        '<p><img src="section.png" width="520" height="182"></p>',
        '<h2 style="font-size:12pt; page-break-before: always;">Design inputs</h2>',
        _table(inp, ("40%", "60%")),
        '<h2 style="font-size:12pt; margin-top:14px;">Data quality</h2>',
        _table(quality, ("40%", "60%")),
    ]
    if files:
        parts.append('<h2 style="font-size:12pt; margin-top:14px;">Files in this package</h2>')
        parts.append(_table([(os.path.basename(f), FILE_NOTES.get(os.path.splitext(f)[1].lower(), ""))
                             for f in files], ("50%", "50%")))
    parts.append('<h2 style="font-size:12pt; margin-top:14px;">Notes and disclaimer</h2>')
    for p in EARTHWORKS_NOTES + DISCLAIMER_PARAGRAPHS:
        parts.append(f'<p style="font-size:8pt; color:#5D677A;">{html.escape(p)}</p>')
    return "".join(parts)


FILE_NOTES = {".pdf": "This report", ".xlsx": "Quantities, inputs and spot levels (Excel)",
              ".csv": "Table for any spreadsheet", ".gpkg": "GIS layers (QGIS, ArcGIS)", ".shp": "GIS layer (Shapefile)",
              ".geojson": "GIS layer (GeoJSON)", ".kml": "Google Earth", ".dxf": "CAD (AutoCAD, Civil 3D)",
              ".tif": "GeoTIFF raster", ".png": "Picture with world file", ".json": "Plan settings (reopen in the app)",
              ".txt": "Read me"}


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
        for mask, kind in ((t.dz > 0.005, "dig"), (t.dz < -0.005, "fill")):
            for g, _v in ex.polygons_from_mask(mask, t.transform,
                                               simplify=cell * 1.5, min_area=(cell * 4) ** 2):
                works.append(ex.Feature(g, {"type": kind, "area_m2": round(g.area, 2)}))
        spot_feats = [ex.Feature(Point(r[1], r[2]), {"point": r[0], "ground_m": r[3], "finished_m": r[4],
                                                     "dig_fill_m": r[5]}) for r in spots]
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
            files += ex.export_raster(p, base + "_dig_fill_depth_m", ["gtiff"], dst_crs, kind="dem", lonlat=lonlat)
            p = write_array(g, t.finished, os.path.join(tmp, "fin.tif"))
            files += ex.export_raster(p, base + ("_finished_ground_m" if t.kind == "pad" else "_reference_surface_m"),
                                      ["gtiff"], dst_crs, kind="dem", lonlat=lonlat)
            from .model_export import _rgba_tif
            rgba = ms.overlay_rgba(t.dz)
            rgba[..., 3] = np.where(np.isfinite(t.dz), 255, 0)
            p = _rgba_tif(g, rgba, os.path.join(tmp, "cf.tif"))
            files += ex.export_raster(p, base + "_dig_fill_map", ["png"], dst_crs, kind="classes", lonlat=lonlat)

        progress(55, "Tables")
        files.append(write_xlsx(base + "_quantities.xlsx", title, meta, q, inp, spots,
                                list(EARTHWORKS_NOTES + DISCLAIMER_PARAGRAPHS)))
        files.append(write_csv(base + "_quantities.csv", ("Quantity", "Value", "Note"), q))
        files.append(write_csv(base + "_spot_levels.csv", ("Point", "Easting_m", "Northing_m", "Ground_m",
                                                           "Finished_m", "Dig+_Fill-_m"), spots))
        plan_copy = base + ".plan.json"
        import json
        with open(plan_copy, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=1, ensure_ascii=False)
        files.append(plan_copy)

        progress(70, "Pictures")
        mimg = map_image(t, outline, dem_path, ortho_path)
        simg = section_image(t, outline)
        mimg.save(base + "_map.png")
        files.append(base + "_map.png")

        readme = base + "_README.txt"
        notes = list(EARTHWORKS_NOTES) + [f"Spot levels every {spacing:g} m over the ground worked "
                                          "(dig + / fill - = ground now minus finished)."]
        for fmt in gis_formats:
            n = ex.format_note(fmt, dst_crs)
            if n and n not in notes:
                notes.append(n)
        pdf_path = base + "_report.pdf"
        listed = [f for f in dict.fromkeys([pdf_path] + files + [readme])
                  if not f.lower().endswith((".aux.xml", ".pgw", ".wld", ".jgw", ".tfw"))]
        progress(85, "PDF report")
        write_pdf(pdf_path, report_html(t, plan, meta, q, inp, quality, listed, mimg.size),
                  {"map.png": mimg, "section.png": simg})
        files.insert(0, pdf_path)
        ex.write_export_readme(readme, f"{APP_NAME} {title}", dst_crs, lonlat, files, notes)
        files.append(readme)
        progress(100, "Done")
        return list(dict.fromkeys(files))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
