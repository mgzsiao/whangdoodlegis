"""The road and canal report (1.2): a project-proposal style PDF in the agreed B + C look.

Cover (plain), contents, 1 project at a glance, 2 site and survey data, 3 design standards and criteria,
4 quantities (DPWH pay items for roads; quantities only, no costs), 5 drawings (plan and profile sheets,
cross-section sheets of every station), 6 limitations, disclaimer and signatures, annexes A (quantities by
station), B (how the numbers were computed, with a worked example), C (files delivered).
"""
from __future__ import annotations

import math
import os
from datetime import datetime

from reportlab.lib.units import mm
from reportlab.platypus import CondPageBreak, KeepTogether, NextPageTemplate, PageBreak, Paragraph, Spacer, Table, \
    TableStyle

from .. import DISCLAIMER_PARAGRAPHS
from . import drawing_sheets as dsh, road as rdm, vertical as vt
from .report_style import (PAPERS, Cover, DocInfo, Markup, Picture, ReportDoc, Sheet, Tag, bullets,
                           contents, dms, esc, key_figures, kst, section, signatures, styles, table)

DEFAULT_SIGNATORIES = [("Prepared by", "", ""), ("Checked by", "", ""), ("Recommending approval", "", ""),
                       ("Approved by", "", "")]
FILE_KINDS = {".pdf": "This report", ".xlsx": "Excel workbook (quantities, stations, sections)",
              ".csv": "Table (CSV)", ".gpkg": "GIS layers (GeoPackage)", ".shp": "GIS layer (shapefile)",
              ".geojson": "GIS layer (GeoJSON)", ".kml": "Google Earth layer", ".dxf": "CAD drawing / layer",
              ".xml": "LandXML for Civil 3D", ".png": "Picture", ".json": "Saved plan (reopen in the app)",
              ".txt": "Read-me", ".tif": "GeoTIFF raster"}


# ============================================================================ coordinates of BEGIN and END
def end_points(r, dst_crs: str, canal: bool) -> list:
    from pyproj import CRS, Transformer
    src = CRS.from_user_input(r.crs_wkt)
    ll = Transformer.from_crs(src, CRS.from_epsg(4326), always_xy=True)
    grid = CRS.from_user_input(dst_crs) if dst_crs else src
    if grid.is_geographic:
        grid = src
    ne = Transformer.from_crs(src, grid, always_xy=True)
    out = []
    for name, s_ in (("BEGIN", r.stations[0]), ("END", r.stations[-1])):
        lon, lat = ll.transform(s_.x, s_.y)
        e, n = ne.transform(s_.x, s_.y)
        out.append(dsh.EndPoint(name, s_.s, s_.x, s_.y, lat, lon, n, e, s_.design, grid.name))
    return out


def background_fn(dem_path: str, ortho_path: str | None):
    from PIL import Image
    from . import measure as ms
    from .model_export import background_for

    def bg(bounds, px):
        grid = ms.read_area(dem_path, bounds, max_cells=px * px, pad_cells=0)
        rgb = background_for(grid, ortho_path)
        t = grid.transform
        h, w = rgb.shape[:2]
        return Image.fromarray(rgb), (t.c, t.f + h * t.e, t.c + w * t.a, t.f)
    return bg


# ============================================================================ design criteria
def _tag(used, std, exception=False):
    if exception:
        return "exception"
    if std is None:
        return "your value"
    return "standard" if used == std else "your value"


def road_criteria(r, rd) -> tuple[list, list]:
    """Rows (element, used, standard, source, tag) and notes for exceptions."""
    std = rdm.STANDARDS.get(rd.standard)
    src = "D.O. 112 s. 2019" if std else "Designer's values"
    rows, notes = [], []

    def sv(key, fmt):
        return fmt(std[key]) if std and key in std else "–"
    rows.append(("Road type", std["label"] if std else "My own values", "–", src if std else "–",
                 "standard" if std else "your value"))
    rows.append(("Design speed", f"{rd.speed} km/h", std["speed"] if std else "–",
                 "AASHTO 2018 (curves)" if not std else src, _tag(True, True if std else None)))
    rows.append(("Carriageway", f"{rd.width:.2f} m, {rd.crown_pct:g}% each way",
                 sv("width", lambda v: f"{v:.2f} m") + (f", {std['crown_pct']:g}%" if std else ""), src,
                 _tag((rd.width, rd.crown_pct), (std["width"], std["crown_pct"]) if std else None,
                      bool(std) and rd.width < std["width"] - 1e-9)))
    rows.append(("Shoulders", f"{rd.shoulder_w:.2f} m gravel, {rd.shoulder_pct:g}%" if rd.shoulders else "none",
                 sv("shoulder_w", lambda v: f"{v:.2f} m, {std['shoulder_pct']:g}%"), src,
                 _tag((rd.shoulders, rd.shoulder_w, rd.shoulder_pct),
                      (True, std["shoulder_w"], std["shoulder_pct"]) if std else None,
                      bool(std) and (not rd.shoulders or rd.shoulder_w < std["shoulder_w"] - 1e-9))))
    aash = ""
    if rd.aashto:
        try:
            from . import pavement as pv
            need = pv.needed_mm(pv.from_dict(rd.aashto))
            aash = f"; AASHTO 1993 check: {need:.0f} mm needed"
        except Exception:
            aash = ""
    rows.append(("PCCP", f"{rd.pccp_t * 1000:.0f} mm", sv("pccp_t", lambda v: f"at least {v * 1000:.0f} mm"),
                 src + aash, _tag(rd.pccp_t, std["pccp_t"] if std else None,
                                  bool(std) and rd.pccp_t < std["pccp_t"] - 1e-9)))
    rows.append(("Aggregate subbase", f"{rd.subbase_t * 1000:.0f} mm", sv("subbase_t", lambda v: f"{v * 1000:.0f} mm"),
                 rdm.SECTION_SOURCE if std else "–", _tag(rd.subbase_t, std["subbase_t"] if std else None)))
    rows.append(("Side slopes", f"fill {rd.fill_ratio:g} : 1 · cut {rd.cut_ratio:g} : 1",
                 sv("fill_ratio", lambda v: f"fill {v:g} : 1 · cut {std['cut_ratio']:g} : 1"),
                 src, _tag((rd.fill_ratio, rd.cut_ratio), (std["fill_ratio"], std["cut_ratio"]) if std else None,
                           rd.fill_ratio < 1.5 - 1e-9 or rd.cut_ratio < 1.0 - 1e-9)))
    if rd.ditch:
        dt = (f"{'V' if rd.ditch_w <= 0 else f'{rd.ditch_w:g} m bottom'}, {rd.ditch_d:g} m deep, "
              f"{rd.ditch_in:g} : 1 road side" + (f", toe ditch {rd.ditch_out:g} : 1" if rd.ditch_toe else ""))
    else:
        dt = "none"
    rows.append(("Road side ditch", dt, "V, 0.5 m, 2 : 1 / 1.5 : 1" if std else "–",
                 rdm.SECTION_SOURCE if std else "–",
                 _tag((rd.ditch, rd.ditch_w, rd.ditch_d, rd.ditch_in),
                      (std["ditch"], std["ditch_w"], std["ditch_d"], std["ditch_in"]) if std else None)))
    al = r.alignment
    curves = al.curves if al is not None else []
    small = [(j, c) for j, c in enumerate(curves, 1) if 0 < c.radius < rd.min_radius - 1e-6]
    if curves:
        used = f"{min(c.radius for c in curves):g} m smallest of {len(curves)} curve(s)"
    else:
        used = "no curves"
    rows.append(("Smallest curve radius", used, f"{rd.min_radius:g} m" if rd.min_radius else "–",
                 src if std else "Designer's minimum",
                 _tag(rd.min_radius, std["min_radius"] if std else None, bool(small))))
    for j, c in small:
        notes.append(f"<b>Curve C{j}</b> ({kst(c.pc_s)} to {kst(c.pt_s)}): radius {c.radius:g} m, below the "
                     f"{rd.min_radius:g} m minimum"
                     + (" (shrunk to fit between the bends)" if c.reduced else "") + ". Warn drivers with a curve sign; "
                     "superelevation and widening are applied there.")
    if curves:
        rows.append(("Superelevation", f"max {rd.emax:g}%, AASHTO Method 5" if rd.superelevation else "not applied",
                     "max 6%", "AASHTO 2018", _tag(rd.superelevation and rd.emax == 6.0, True)))
        ws = [s_.widen for s_ in r.stations if s_.widen > 0]
        rows.append(("Widening on curves", (f"SU truck, up to {max(ws):.2f} m" if ws else "SU truck, none needed")
                     if rd.widen else "not applied", "SU truck", "AASHTO 2018", _tag(bool(rd.widen), True)))
    rows.append(("Grades", f"steepest {r.steepest:.2f}%", f"max {rd.max_grade:g}%"
                 + (f", min {rd.min_grade:g}% in cut" if rd.min_grade else ""), src if std else "Designer's limits",
                 _tag(True, True if std else None, rd.max_grade > 0 and r.steepest > rd.max_grade + 0.01)))
    req = []
    if rd.max_fill:
        req.append(f"fill ≤ {rd.max_fill:g} m")
    if rd.max_cut:
        req.append(f"cut ≤ {rd.max_cut:g} m")
    if req:
        rows.append(("Height at the centre line", f"cut {r.deepest_cut:.2f} m, fill {r.highest_fill:.2f} m deepest",
                     ", ".join(req), "Designer's requirement",
                     _tag(True, True, (rd.max_fill and r.highest_fill > rd.max_fill + 0.01)
                          or (rd.max_cut and r.deepest_cut > rd.max_cut + 0.01))))
    if r.vcurves:
        short = [c for c in r.vcurves if 0 < c.length < c.length_asked - 0.01]
        ls = [c.length for c in r.vcurves if c.length > 0]
        rows.append(("Vertical curves", f"{len(ls)} curve(s)" + (f", {min(ls):.0f} – {max(ls):.0f} m" if ls else ""),
                     f"at least {rd.vc_min:g} m" if rd.vc_min else "AASHTO K", "Designer's rule; K from AASHTO 2018",
                     _tag(True, True, bool(short))))
        for c in short:
            notes.append(f"<b>Vertical curve {c.n}</b> at {kst(c.pvi_s)}: {c.length:.1f} m, shorter than asked "
                         f"({c.length_asked:.0f} m) because the grade change points are close.")
    rows.append(("Station spacing", f"{rd.interval:g} m plus bends and the end", "–", "Designer's choice",
                 "your value"))
    rows.append(("Haul", f"swell {rd.swell_pct:g}%, shrink {rd.shrink_pct:g}%"
                 + (f", {rd.truck_m3:g} m³ truck" if rd.truck_m3 else ""), "–", "Designer's choice", "your value"))
    return rows, notes


def canal_criteria(r, cd) -> tuple[list, list]:
    from . import canal as cn
    START = cn.SUGGESTED
    src = "Suggested size (not a DPWH standard)"

    def tg(key, used, exception=False):
        return "exception" if exception else ("suggested" if abs(used - START[key]) < 1e-9 else "your value")
    ok = r.q_capacity >= r.q_design - 1e-9
    rows = [("Bottom width", f"{cd.bw:.2f} m", f"{START['bw']:.2f} m", src, tg("bw", cd.bw)),
            ("Depth (bed to top of lining)", f"{cd.depth:.2f} m", f"{START['depth']:.2f} m", src,
             tg("depth", cd.depth, not ok)),
            ("Side slope", f"{cd.z:g} : 1", f"{START['z']:g} : 1", src, tg("z", cd.z)),
            ("Concrete lining", f"{cd.lining_t * 1000:.0f} mm", f"{START['lining_t'] * 1000:.0f} mm", src,
             tg("lining_t", cd.lining_t)),
            ("Manning n", f"{cd.n:.3f}", "0.013 – 0.017 concrete", "Chow (1959), Table 5-6", tg("n", cd.n)),
            ("Freeboard", f"{cd.freeboard:.2f} m", f"{START['freeboard']:.2f} m", src,
             tg("freeboard", cd.freeboard, r.y_design == r.y_design and r.y_design > cd.depth - cd.freeboard + 1e-6)),
            ("Bed slope", f"{cd.slope_pct:g}%", f"{START['slope_pct']:g}%", src, tg("slope_pct", cd.slope_pct)),
            ("Berm / bank top", f"{cd.berm_w:.2f} m", f"{START['berm_w']:.2f} m", src, tg("berm_w", cd.berm_w)),
            ("Cut / bank sides", f"cut {cd.cut_ratio:g} : 1 · banks {cd.fill_ratio:g} : 1",
             f"cut {START['cut_ratio']:g} : 1 · banks {START['fill_ratio']:g} : 1", src,
             "suggested" if (cd.cut_ratio, cd.fill_ratio) == (START["cut_ratio"], START["fill_ratio"])
             else ("exception" if cd.fill_ratio < 1.5 else "your value")),
            ("Velocity at the design flow", f"{r.v_design:.2f} m/s" if r.v_design == r.v_design else "–",
             f"{cn.V_MIN} – {cn.V_MAX} m/s", "Silt / wear checks",
             "exception" if r.v_design == r.v_design and not (cn.V_MIN <= r.v_design <= cn.V_MAX) else "standard"),
            ("Station spacing", f"{cd.interval:g} m plus bends and the end", "–", "Designer's choice", "your value")]
    notes = [f"<b>{esc(t)}:</b> {esc(x)}" for t, x in r.warnings]
    return rows, notes


# ============================================================================ the report
def write_report(path: str, r, plan: dict, info: dict, dem_path: str, ortho_path: str | None, job, dst_crs: str,
                 files: list, map_img, worked: list, quality: list) -> str:
    from . import crs as crsmod
    canal = hasattr(r, "q_capacity")
    if canal:
        from . import canal as cn
        des = cn.design_from(plan)
    else:
        des = rdm.design_from(plan)
    paper = PAPERS.get(info.get("paper") or "a4_a3", PAPERS["a4_a3"])
    S = styles()
    project = info.get("project") or plan.get("name", "")
    location = info.get("location") or ""
    today = datetime.now()
    sign = [tuple(x) for x in (info.get("signatories") or DEFAULT_SIGNATORIES)]
    if info.get("prepared_by") and not sign[0][1]:
        sign[0] = (sign[0][0], info["prepared_by"], sign[0][2])
    ends = end_points(r, dst_crs, canal)
    show_elev = bool(info.get("show_elev"))
    st = r.stations
    h = r.haul
    std = rdm.STANDARDS.get(getattr(des, "standard", ""))
    # ---------------------------------------------------------------- cover
    if canal:
        facts = [(f"{r.length:,.0f} m", f"{kst(0)} to {kst(r.length)}"),
                 (f"{des.bw:g} × {des.depth:g} m", f"lined canal, {des.z:g} : 1 sides"),
                 (f"{r.lining_vol:,.1f} m³", f"concrete lining, {des.lining_t * 1000:.0f} mm"),
                 (f"{cn.q_text(r.q_capacity)} m³/s", "capacity less freeboard")]
        kind = "Drainage canal design and quantities"
        standard = "Suggested canal sizes · Manning · Rational Method"
    else:
        facts = [(f"{r.length:,.0f} m", f"{kst(0)} to {kst(r.length)}"),
                 (f"{des.width:.2f} m", f"PCCP carriageway, {des.pccp_t * 1000:.0f} mm"),
                 (f"{r.pccp_area:,.0f} m²", "concrete pavement"),
                 (f"{r.cut:,.0f} m³", "cut (roadway excavation)")]
        kind = "Road design and quantities"
        standard = (f"DPWH D.O. 112 s. 2019 · {std['label'].lower()}" if std else "Designer's own values")
    survey = ""
    if job is not None:
        dt = (job.finished or job.created or "")[:10]
        try:
            survey = "Drone survey processed " + datetime.strptime(dt, "%Y-%m-%d").strftime("%d %B %Y")
        except ValueError:
            survey = ""
    else:
        survey = "Elevation model: your own file"
    control = [(rl, nm, ps, today.strftime("%Y-%m-%d") if i == 0 else "") for i, (rl, nm, ps) in enumerate(sign)
               if rl.strip()]
    office = [ln for ln in (info.get("office") or []) if ln.strip()]
    doc_info = DocInfo(project, kind, location, info.get("report_no", ""), info.get("revision", "0"),
                       today.strftime("%d %B %Y"), office, info.get("logo", ""), facts, control, standard, survey)
    doc = ReportDoc(path, paper, doc_info)
    BW = doc.body_width
    story = [Cover(doc_info, paper.page), NextPageTemplate("body"), PageBreak()]
    story += [Paragraph("Contents", S["h2"]), Spacer(1, 4), contents(S), PageBreak()]
    # ---------------------------------------------------------------- 1 at a glance
    story += section(S, "1", "Project at a glance")
    if canal:
        ok = r.q_capacity >= r.q_design - 1e-9
        txt = (f"This report sets out <b>{r.length:,.0f} m</b> of lined drainage canal from {kst(0)} to "
               f"{kst(r.length)}: a concrete-lined trapezoid {des.bw:.2f} m wide at the bottom and {des.depth:.2f} m "
               f"deep with {des.z:g} : 1 sides, its bed falling {des.slope_pct:g}%. It carries "
               f"<b>{cn.q_text(r.q_capacity)} m³/s</b> with {des.freeboard:.2f} m freeboard; the design flow is "
               f"{cn.q_text(r.q_design)} m³/s ({'enough' if ok else '<b>not enough</b>'}). About "
               f"<b>{r.cut:,.0f} m³</b> is excavated and <b>{r.fill:,.0f} m³</b> of earth banks built; "
               f"{r.lining_vol:,.1f} m³ of concrete lining.")
        kf = [(f"{r.length:,.0f} m", "length"), (f"{r.lining_vol:,.1f} m³", "concrete lining"),
              (f"{cn.q_text(r.q_capacity)} m³/s", "capacity"), (f"{r.cut:,.0f} m³", "cut"),
              (f"{r.fill:,.0f} m³", "banks (fill)")]
    else:
        sh = (f" with {des.shoulder_w:.2f} m gravel shoulders" if des.shoulders else "") + \
             (" and road side ditches" if des.ditch else "")
        txt = (f"This report sets out <b>{r.length:,.0f} m</b> of road from {kst(0)} to {kst(r.length)}"
               + (f", designed for <b>{esc(std['label'].lower())}</b> under DPWH D.O. 112 s. 2019" if std else "")
               + f": a {des.width:.2f} m concrete carriageway (PCCP {des.pccp_t * 1000:.0f} mm on "
               f"{des.subbase_t * 1000:.0f} mm aggregate subbase){sh}. The steepest grade is {r.steepest:.2f}%"
               + (f", with {len(r.alignment.curves)} curve(s)" if r.alignment is not None and r.alignment.curves
                  else "") + f". About <b>{r.cut:,.0f} m³</b> of earth is cut and <b>{r.fill:,.0f} m³</b> placed as "
               f"embankment.")
        trips = h.trips[-1] if h.trips else (10, 0)
        kf = [(f"{r.length:,.0f} m", "length"), (f"{r.pccp_area:,.0f} m²", f"PCCP, {des.pccp_t * 1000:.0f} mm"),
              (f"{r.cut:,.0f} m³", "cut"), (f"{r.fill:,.0f} m³", "fill"),
              (f"{trips[1]:,}", f"truck trips, {trips[0]:g} m³")]
    story += [Paragraph(txt + " " + esc(h.sentence()), S["body"]), Spacer(1, 4), key_figures(S, kf, BW),
              Spacer(1, 8)]
    if map_img is not None:
        story += [Picture(map_img, BW, max_height=doc.body_height * 0.42), Spacer(1, 6)]
    grid_name = ends[0].grid
    hdr = ["Point", "Station", "Latitude, longitude (WGS 84)", f"N / E ({grid_name})"] + \
          ([("Bed" if canal else "FGE") + " (m)"] if show_elev else [])
    rows = [[e.name, kst(e.s), f"{dms(e.lat, 'N', 'S')}, {dms(e.lon, 'E', 'W')}",
             f"{e.north:,.3f} / {e.east:,.3f}"] + ([f"{e.z:.3f}"] if show_elev else []) for e in ends]
    widths = [0.1, 0.17, 0.36, 0.37] if not show_elev else [0.09, 0.15, 0.33, 0.31, 0.12]
    story.append(table(S, rows, [w * BW for w in widths], header=hdr, num_cols=(4,) if show_elev else ()))
    # ---------------------------------------------------------------- 2 site and survey data
    story += [PageBreak()] + section(S, "2", "Site and survey data")
    story.append(Paragraph(
        "The design uses the elevation model listed below. "
        + ("The bare-ground model (DTM) removes trees and buildings; levels and volumes are worked out on it."
           if plan.get("source", "dtm") == "dtm" else ""), S["body"]))
    q = [(a, b) for a, b in quality] + [("Coordinates in the files", crsmod.label(dst_crs)),
                                        ("N / E in this report", grid_name)]
    story.append(table(S, q, [0.36 * BW, 0.64 * BW]))
    story.append(Paragraph("What this means for accuracy", S["h3"]))
    gcp = bool(job is not None and getattr(job.options, "gcp_file", ""))
    acc = ["<b>Within the site</b>, distances, slopes, levels and volumes are reliable to a few centimetres where "
           "the ground is visible from the air."]
    if job is None:
        acc.append("<b>As a whole</b>, the accuracy is that of your own elevation file; check its source and date.")
    elif gcp:
        acc.append("<b>As a whole</b>, the survey is tied to ground control points (see the table above).")
    else:
        acc.append("<b>As a whole</b>, without ground control points the survey can sit a few metres off its true "
                   "position and height (the drone's own GPS). Tie the project to a survey monument before staking "
                   "out.")
    acc.append("<b>Under dense trees</b> the ground model is estimated from the nearest visible ground. Check levels "
               "in the field where the map shows tree cover.")
    story += bullets(S, acc)
    g_ok = [s_ for s_ in st if s_.ground == s_.ground]
    if g_ok:
        lo = min(g_ok, key=lambda s_: s_.ground)
        hi = max(g_ok, key=lambda s_: s_.ground)
        story += [Paragraph("Site", S["h3"]), Paragraph(
            f"The natural ground along the centre line goes from EL {g_ok[0].ground:.2f} m at BEGIN to EL "
            f"{g_ok[-1].ground:.2f} m at END; lowest EL {lo.ground:.2f} m near {kst(lo.s)}, highest EL "
            f"{hi.ground:.2f} m near {kst(hi.s)}.", S["body"])]
    # ---------------------------------------------------------------- 3 design criteria
    story += [PageBreak()] + section(S, "3", "Design standards and criteria")
    story.append(Paragraph("Values used in this design, the standard each one comes from, and whether it is the "
                           + ("suggested value" if canal else "standard value")
                           + " or one the designer changed. Exceptions break a limit and are explained below.",
                           S["body"]))
    crit, notes = canal_criteria(r, des) if canal else road_criteria(r, des)
    crow = [(a, b, c_, d, Tag(t)) for a, b, c_, d, t in crit]
    story.append(table(S, crow, [w * BW for w in (0.2, 0.25, 0.2, 0.23, 0.12)],
                       header=("Element", "Used", "Standard" if not canal else "Suggested", "Source", "")))
    for n_ in notes:
        story.append(Paragraph(n_, S["small"]))
    if not canal and std:
        story.append(Paragraph(f"Source: {esc(rdm.DPWH_SOURCE)}. {esc(rdm.STANDARD_NOTES)}", S["small"]))
    if canal:
        story += [Paragraph("Capacity and design flow", S["h3"]),
                  table(S, list(r.hydraulics), [0.36 * BW, 0.22 * BW, 0.42 * BW], header=("Check", "Value", "How"))]
        if des.q_mode == "rain":
            cov = [(cn.COVERS[k][0], f"{v[0]:g}%", f"{v[1]:.2f}") for k, v in des.covers.items()
                   if k in cn.COVERS and v[0] > 0]
            if cov:
                story += [Paragraph("Land cover of the catchment", S["h3"]),
                          table(S, cov, [0.62 * BW, 0.18 * BW, 0.2 * BW], header=("Land cover", "Share", "C"),
                                num_cols=(1, 2)),
                          Paragraph(f"Runoff coefficients: {esc(cn.RUNOFF_SOURCE)}; paved surfaces: "
                                    f"{esc(cn.PAVED_SOURCE)}; paddy: {esc(cn.PADDY_SOURCE)}.", S["small"])]
    else:
        al = r.alignment
        if al is not None and al.curves:
            cr = rdm.curve_rows(al, r.min_radius)
            hrows = []
            for j, (cv, x) in enumerate(zip(al.curves, cr), 1):
                ext = cv.radius * (1 / math.cos(math.radians(cv.delta) / 2) - 1) if cv.radius > 0 else 0.0
                hrows.append((f"C{j}", f"{cv.delta:.3f}", f"{cv.radius:g}", f"{cv.tangent:.3f}", f"{cv.length:.3f}",
                              f"{ext:.3f}", kst(cv.pc_s), kst(cv.pt_s), "left" if cv.left else "right",
                              x[-1] if x[-1] not in ("", "-") else "–"))
            story += [CondPageBreak(40 * mm), Paragraph("Horizontal curves", S["h3"]),
                      table(S, hrows, [w * BW for w in (0.06, 0.08, 0.07, 0.08, 0.08, 0.08, 0.13, 0.13, 0.07, 0.22)],
                            header=("Curve", "Δ (°)", "R (m)", "T (m)", "L (m)", "E (m)", "PC", "PT", "Turns",
                                    "Note"), num_cols=(1, 2, 3, 4, 5)),
                      Paragraph("Δ deflection, R radius, T tangent = R tan(Δ/2), L curve length, E external = "
                                "R (sec(Δ/2) − 1); PC curve start, PT curve end.", S["small"])]
        if r.vcurves:
            vr = vt.rows(r.vcurves, kst)
            idx = (0, 1, 2, 3, 4, 5, 7, 8, 10, 12, 16)
            story += [CondPageBreak(40 * mm), Paragraph("Vertical curves", S["h3"]),
                      table(S, [[x[i] for i in idx] for x in vr], [w * BW for w in
                                                                   (0.06, 0.07, 0.13, 0.09, 0.07, 0.07, 0.07, 0.07,
                                                                    0.13, 0.13, 0.11)],
                            header=("No.", "Type", "PVI", "PVI EL (m)", "g1 (%)", "g2 (%)", "L (m)", "K", "PVC",
                                    "PVT", "Note"), num_cols=(3, 4, 5, 6, 7)),
                      Paragraph("g1 grade in, g2 grade out, L curve length (PVC to PVT), K = L / |g2 − g1| "
                                f"(AASHTO 2018 crest and sag K for {r.speed} km/h).", S["small"])]
    # ---------------------------------------------------------------- 4 quantities
    story += [PageBreak()] + section(S, "4", "Quantities")
    if canal:
        story.append(Paragraph(f"Quantities measured from the elevation model and the design between {kst(0)} and "
                               f"{kst(r.length)}; volumes between stations by the average end-area method. Pay item "
                               "numbers are left to the estimator.", S["body"]))
        pay = [("–", "Excavation (cut), including the channel", f"{r.cut:,.2f}", "m³"),
               ("–", "Embankment (earth banks)", f"{r.fill:,.2f}", "m³"),
               ("–", f"Concrete lining, {des.lining_t * 1000:.0f} mm", f"{r.lining_vol:,.2f}", "m³"),
               ("–", "Concrete lining area (bed and sides)",
                f"{(des.bw + 2 * des.depth * math.sqrt(1 + des.z ** 2)) * r.length:,.2f}", "m²")]
    else:
        story.append(Paragraph(f"Quantities by DPWH pay item, measured from the elevation model and the design between "
                               f"{kst(0)} and {kst(r.length)}. Cut and fill are measured to the bottom of the subbase "
                               "(the subgrade); volumes between stations by the average end-area method.", S["body"]))
        pay = [("102", "Roadway Excavation (cut, including ditches)", f"{r.cut:,.2f}", "m³"),
               ("104", "Embankment (fill)", f"{r.fill:,.2f}", "m³")]
        if des.subbase_t > 0 or des.shoulders:
            pay.append(("200", f"Aggregate Subbase Course, {des.subbase_t * 1000:.0f} mm, incl. aggregate shoulders",
                        f"{r.agg_vol:,.2f}", "m³"))
        if des.pccp_t > 0:
            pay.append(("311", f"Portland Cement Concrete Pavement, {des.pccp_t * 1000:.0f} mm",
                        f"{r.pccp_area:,.2f}", "m²"))
            pay.append(("311", "  (same, as volume)", f"{r.pccp_vol:,.2f}", "m³"))
    story.append(table(S, pay, [0.1 * BW, 0.58 * BW, 0.2 * BW, 0.12 * BW],
                       header=("Item", "Description", "Quantity", "Unit"), num_cols=(2,)))
    story.append(Paragraph("Quantities only. Costs are estimated outside the app, for example from the Excel workbook "
                           "delivered with this report.", S["small"]))
    story.append(Paragraph("Earthwork balance", S["h3"]))
    bal = [("Cut", f"{r.cut:,.1f} m³"), ("Fill" if not canal else "Banks (fill)", f"{r.fill:,.1f} m³"),
           ("Fill needs in the ground", f"{h.needed:,.1f} m³ (shrink {h.shrink_pct:g}%)")]
    bal.append((Markup("<b>Surplus to haul away</b>") if h.balance >= 0 else Markup("<b>Soil to bring in</b>"),
                Markup(f"<b>{abs(h.balance):,.1f} m³</b>")))
    bal.append((f"Loose volume (swell {h.swell_pct:g}%)", f"{h.loose:,.1f} m³"))
    side = [(f"Trips, {s_:g} m³ truck", f"{n_:,}") for s_, n_ in h.trips]
    side += [("Deepest cut at the centre line" if not canal else "Deepest bed below the ground",
              f"{r.deepest_cut:.2f} m"),
             ("Highest fill at the centre line" if not canal else "Highest bank above the ground",
              f"{r.highest_fill:.2f} m")]
    t1 = table(S, bal, [0.27 * BW, 0.21 * BW], num_cols=(1,))
    t2 = table(S, side, [0.28 * BW, 0.16 * BW], num_cols=(1,))
    two = Table([[t1, t2]], colWidths=[0.5 * BW, 0.5 * BW], hAlign="LEFT")
    two.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    story += [two, Spacer(1, 6), Paragraph("Annex A lists the areas and volumes at every station. The Excel workbook "
                                           "and CSV files delivered with this report hold the same numbers.",
                                           S["small"])]
    # ---------------------------------------------------------------- 5 drawings
    ctx = dsh.Ctx(paper, project, info.get("report_no", ""), canal, des, ends, show_elev,
                  f"latitude / longitude WGS 84 · N / E {grid_name}", background_fn(dem_path, ortho_path))
    pp = dsh.plan_profile_sheets(r, ctx)
    xss = dsh.canal_sections(r, des) if canal else dsh.road_sections(r, des)
    xsheets = dsh.xs_sheets(xss, ctx, first=len(pp) + 1)
    ctx.sheets_total = len(pp) + len(xsheets)
    rows_, den = dsh.choose_xs_layout(xss, paper) if xss else (0, 0)
    story += [PageBreak()] + section(S, "5", "Drawings")
    story.append(Paragraph(
        f"{ctx.sheets_total} drawing sheet(s) on {paper.sheet_name} landscape"
        + (" (fold into the A4 report)" if paper.sheet_name == "A3" else "") + ": "
        + ("sheet 1 is the plan and profile" if len(pp) == 1 else f"sheets 1 to {len(pp)} are the plan and profile")
        + (f", sheets {len(pp) + 1} to {ctx.sheets_total} the cross-sections of all {len(xss)} stations at 1:{den:,}"
           if len(xsheets) > 1 else (f", sheet {len(pp) + 1} the cross-sections of all {len(xss)} stations at "
                                     f"1:{den:,}" if xsheets else "")) + ". The plan and profile share the "
        "horizontal scale; the profile is drawn ten times taller (or more when the ground is steep). Cross-sections "
        "are at true scale, numbered down the left column, then the right.", S["body"]))
    story.append(NextPageTemplate("sheet"))
    story.append(PageBreak())
    hdr_r = project + (f" · Report {info['report_no']}" if info.get("report_no") else "")
    all_sheets = [(fn, t) for fn, t in pp] + [(fn, t) for fn, t in xsheets]
    for i, (fn, title) in enumerate(all_sheets, 1):
        story.append(Sheet(fn, paper.sheet, (f"Section 5 · Drawings · Sheet {i} of {len(all_sheets)}", hdr_r),
                           toc_text=f"Sheet {i} · {title}"))
    story.append(NextPageTemplate("body"))
    # ---------------------------------------------------------------- 6 limitations, disclaimer, signatures
    story += section(S, "6", "Limitations, disclaimer and signatures")
    story.append(Paragraph("Limitations", S["h3"]))
    lim = ["Levels and volumes come from the elevation model described in section 2; confirm benchmark levels in "
           "the field before construction.",
           "Quantities are neat volumes: no allowance for waste or overbreak; compaction only through the shrink "
           "and swell factors stated.",
           "Cross-sections are taken square to the centre line at each station; no curvature correction of "
           "volumes on curves."]
    if canal:
        lim += ["The design flow uses the rainfall, catchment and land cover entered; confirm the rainfall intensity "
                "with PAGASA's rainfall intensity-duration-frequency data for the nearest station.",
                "Inlets, culverts, outfalls and structures along the canal are not designed in this report."]
    else:
        lim += ["Pavement thickness follows the standard shown in section 3"
                + (" and the AASHTO 1993 check for the traffic entered" if des.aashto else "")
                + "; a full pavement design needs measured soil strength (CBR) and traffic counts.",
                "Drainage structures (culverts, cross drains) are not designed in this report."]
    story += bullets(S, lim)
    story.append(Paragraph("Disclaimer", S["h3"]))
    for p_ in DISCLAIMER_PARAGRAPHS:
        story.append(Paragraph(esc(p_), S["small"]))
    story += [Spacer(1, 10), KeepTogether([signatures(S, [x for x in sign if x[0].strip()], BW)])]
    # ---------------------------------------------------------------- annexes
    from .road_report import station_rows
    story += [PageBreak()] + section(S, "A", "Annex A · Quantities by station", eyebrow="ANNEX A")
    srows = []
    for x in station_rows(r):
        srows.append((kst(st[len(srows)].s), x[4], x[5], x[6], x[7], x[8], x[9], x[10], x[13]))
    srows = [tuple("–" if v is None else (f"{v:,.3f}" if isinstance(v, float) and i in (1, 2, 3)
                                          else (f"{v:,.2f}" if isinstance(v, float) else v)) for i, v in enumerate(rw))
             for rw in srows]
    story.append(table(S, srows, [w * BW for w in (0.14, 0.1, 0.1, 0.11, 0.09, 0.09, 0.12, 0.12, 0.13)],
                       header=("Station", "Ground", "Bed" if canal else "FGE", "Cut + / Fill −", "Cut m²",
                               "Fill m²", "Cut m³", "Fill m³", "Balance m³"),
                       num_cols=(1, 2, 3, 4, 5, 6, 7, 8), zebra=True))
    story.append(Paragraph("Levels in metres. Volumes are from the previous station (average end-area: V = (A1 + A2) "
                           "/ 2 × L); the balance is the cut so far less the fill so far × (1 + shrink).", S["small"]))
    story += [PageBreak()] + section(S, "B", "Annex B · How the numbers were computed", eyebrow="ANNEX B")
    story.append(Paragraph("One line of each table recomputed by hand with the numbers of this design. The full "
                           "method statements, with formulas and sources, are in the app under Help › How things "
                           "are computed.", S["body"]))
    if canal:
        forms = ["Q = A × R^(2/3) × S^(1/2) / n,  A = (b + z y) y,  P = b + 2 y √(1 + z²),  R = A / P",
                 "Q(design) = C × i × A / 360   (m³/s; i mm/h, A hectares)",
                 "A(lining) = t (b + 2 d √(1 + z²)) + t² (2 √(1 + z²) − z)",
                 "V = (A1 + A2) / 2 × L   (average end-area)"]
    else:
        forms = ["V = (A1 + A2) / 2 × L   (average end-area)",
                 "A(cut) = Σ max(ground − design, 0) × Δoffset",
                 "A(PCCP) = PCCP thickness × carriageway width",
                 "balance = cut so far − fill so far × (1 + shrink)"]
    story.append(Paragraph("<br/>".join(esc(f_) for f_ in forms), S["formula"]))
    for line in worked:
        story.append(Paragraph(esc(line), S["body"]))
    story += [PageBreak()] + section(S, "C", "Annex C · Files delivered with this report", eyebrow="ANNEX C")
    frows = []
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        frows.append((os.path.basename(f), FILE_KINDS.get(ext, ext.lstrip(".").upper())))
    story.append(table(S, frows, [0.62 * BW, 0.38 * BW], header=("File", "What it is")))
    doc.make(story)
    return path
