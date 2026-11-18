"""The lot / pad and pile report (1.2): the same B + C style as the road and canal report.

Cover, contents, 1 at a glance (map, corner coordinates), 2 site and survey data, 3 design inputs,
4 quantities and earth balance, 5 drawings (lot plan sheet at a standard scale with spot levels; cross-sections
across the lot for pads), 6 limitations, disclaimer and signatures, annexes (quantities by section, worked
example, files).
"""
from __future__ import annotations

import numpy as np
from reportlab.platypus import NextPageTemplate, PageBreak, Paragraph, Spacer, Table, TableStyle

from . import drawing_sheets as dsh, earthworks as ew, measure as ms
from .linear_report import background_fn, closing_section, files_annex, start_document, survey_section
from .report_style import Markup, Picture, Sheet, dms, esc, key_figures, section, table
from .terrain import Dem


def corner_rows(t, outline, dst_crs: str, show_elev: bool):
    """(header, rows, grid name) for the outline corners: lat / lon, N / E and (if asked) the ground and finished
    levels."""
    from pyproj import CRS, Transformer
    src = CRS.from_user_input(t.crs_wkt)
    grid = CRS.from_user_input(dst_crs) if dst_crs else src
    if grid.is_geographic:
        grid = src
    ll = Transformer.from_crs(src, CRS.from_epsg(4326), always_xy=True)
    ne = Transformer.from_crs(src, grid, always_xy=True)
    xs = np.array([p[0] for p in outline], float)
    ys = np.array([p[1] for p in outline], float)
    zg = ms.sample(Dem(t.ground, t.transform, t.crs_wkt, ""), xs, ys)
    zf = ms.sample(Dem(t.finished, t.transform, t.crs_wkt, ""), xs, ys)
    rows = []
    for i, (x, y) in enumerate(outline):
        lon, lat = ll.transform(x, y)
        e, n = ne.transform(x, y)
        r = [f"P{i + 1}", f"{dms(lat, 'N', 'S')}, {dms(lon, 'E', 'W')}", f"{n:,.3f} / {e:,.3f}"]
        if show_elev:
            r += [f"{zg[i]:.3f}" if np.isfinite(zg[i]) else "–", f"{zf[i]:.3f}" if np.isfinite(zf[i]) else "–"]
        rows.append(r)
    hdr = ["Corner", "Latitude, longitude (WGS 84)", f"N / E ({grid.name})"] + (["Ground (m)", "Finished (m)"]
                                                                              if show_elev else [])
    return hdr, rows, grid.name


def write_lot_report(path: str, t, plan: dict, info: dict, dem_path: str, ortho_path: str | None, job,
                     dst_crs: str, files: list, map_img, q: list, inp: list, quality: list, spots: list,
                     secs: list, qty: list, step: float, along: str, worked: list) -> str:
    pile = t.kind == "pile"
    h = t.haul
    outline = [tuple(p) for p in plan["points"]]
    area = t.area.area_2d
    show_elev = bool(info.get("show_elev"))
    if pile:
        kind = "Pile, hole or deposit measurement"
        facts = [(f"{area:,.0f} m²", "inside the outline"), (f"{t.cut:,.0f} m³", "above the reference (pile)"),
                 (f"{t.fill:,.0f} m³", "below the reference (hole)"), (f"{t.net:,.0f} m³", "net")]
        standard = "Volume from the elevation model"
    else:
        kind = "Lot earthworks and quantities"
        lvl = f"{t.level:.2f} m" if t.level == t.level else "–"
        facts = [(f"{area:,.0f} m²", "lot area"), (lvl, "finished level at the centre"),
                 (f"{t.cut:,.0f} m³", "cut"), (f"{t.fill:,.0f} m³", "fill")]
        standard = "Earthworks estimate from the elevation model"
    doc, S, story, paper, sign = start_document(path, info, plan, job, kind, facts, standard)
    BW = doc.body_width
    project = info.get("project") or plan.get("name", "")
    # ---------------------------------------------------------------- 1 at a glance
    story += section(S, "1", "Project at a glance")
    story.append(Paragraph(esc(t.headline()) + " " + esc(h.sentence()), S["body"]))
    trips = h.trips[-1] if h.trips else (10, 0)
    kf = facts[:4] + [(f"{trips[1]:,}", f"truck trips, {trips[0]:g} m³")]
    story += [Spacer(1, 4), key_figures(S, kf, BW), Spacer(1, 8)]
    warn = []
    if t.reaches_edge:
        warn.append("The sloped sides reach the edge of the area read from the elevation model (or of the model "
                    "itself): the real side volumes may be larger.")
    if t.no_data_pct > 2:
        warn.append(f"{t.no_data_pct:.0f}% of the outline has no height data; those parts are not counted.")
    for w_ in warn:
        story.append(Paragraph("<b>Note:</b> " + esc(w_), S["small"]))
    if map_img is not None:
        story += [Picture(map_img, BW, max_height=doc.body_height * 0.4), Spacer(1, 6)]
    hdr, rows, grid_name = corner_rows(t, outline, dst_crs, show_elev)
    widths = [0.1, 0.44, 0.46] if not show_elev else [0.08, 0.34, 0.32, 0.13, 0.13]
    story.append(table(S, rows, [w * BW for w in widths], header=hdr, num_cols=(3, 4) if show_elev else ()))
    # ---------------------------------------------------------------- 2 site and survey
    story += [PageBreak()] + section(S, "2", "Site and survey data")
    story += survey_section(S, BW, plan, quality, job, dst_crs, grid_name)
    # ---------------------------------------------------------------- 3 design
    story += [PageBreak()] + section(S, "3", "Reference surface and inputs" if pile else "Design inputs")
    story.append(Paragraph(
        "How the volume was measured: every cell of the elevation model inside the outline is compared with the "
        "reference surface below; above it counts as pile, below it as hole." if pile else
        "The finished lot as designed: its level, drainage slope, sides and soil factors. Every value was chosen by "
        "the designer in the app.", S["body"]))
    story.append(table(S, [(a, b) for a, b in inp], [0.36 * BW, 0.64 * BW], header=("Input", "Value")))
    # ---------------------------------------------------------------- 4 quantities
    story += [PageBreak()] + section(S, "4", "Quantities")
    story.append(Paragraph(
        "Measured on the elevation model cell by cell inside the outline"
        + ("" if pile else " and on the sloped sides around it") + ". Quantities only; costs are estimated outside "
        "the app, for example from the Excel workbook delivered with this report.", S["body"]))
    story.append(table(S, [(a, b, c_) for a, b, c_ in q], [0.34 * BW, 0.22 * BW, 0.44 * BW],
                       header=("Quantity", "Value", "Note"), num_cols=(1,)))
    story.append(Paragraph("Earth balance", S["h3"]))
    bal = [("Cut" if not pile else "Above (pile)", f"{t.cut:,.1f} m³"),
           ("Fill" if not pile else "Below (hole)", f"{t.fill:,.1f} m³")]
    if not pile:
        bal.append(("Fill needs in the ground", f"{h.needed:,.1f} m³ (shrink {h.shrink_pct:g}%)"))
    bal.append((Markup("<b>To haul away</b>") if h.balance >= 0 else Markup("<b>To bring in</b>"),
                Markup(f"<b>{abs(h.balance):,.1f} m³</b>")))
    bal.append((f"Loose volume (swell {h.swell_pct:g}%)", f"{h.loose:,.1f} m³"))
    side = [(f"Trips, {s_:g} m³ truck", f"{n_:,}") for s_, n_ in h.trips]
    t1 = table(S, bal, [0.27 * BW, 0.21 * BW], num_cols=(1,))
    t2 = table(S, side, [0.28 * BW, 0.16 * BW], num_cols=(1,))
    two = Table([[t1, t2]], colWidths=[0.5 * BW, 0.5 * BW], hAlign="LEFT")
    two.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    story.append(two)
    # ---------------------------------------------------------------- 5 drawings
    ctx = dsh.Ctx(paper, project, info.get("report_no", ""), False, None, [], show_elev, "",
                  background_fn(dem_path, ortho_path), lot=True)
    sheets = [dsh.lot_plan_sheet(t, outline, spots, ctx, "PILE PLAN" if pile else "LOT PLAN", pile=pile)]
    xss = dsh.lot_sections(secs) if secs else []
    xsheets = dsh.xs_sheets(xss, ctx, first=2) if xss else []
    sheets += xsheets
    ctx.sheets_total = len(sheets)
    story += [PageBreak()] + section(S, "5", "Drawings")
    txt = (f"{len(sheets)} drawing sheet(s) on {paper.sheet_name} landscape"
           + (" (fold into the A4 report)" if paper.sheet_name == "A3" else "") + ": sheet 1 is the plan with "
           "the cut / fill colours and spot levels")
    if xsheets:
        _rows, den = dsh.choose_xs_layout(xss, paper)
        txt += (f"; sheet{'s' if len(xsheets) > 1 else ''} 2" + (f" to {len(sheets)}" if len(xsheets) > 1 else "")
                + f" the {len(xss)} cross-sections across the lot every {step:g} m ({esc(along)}) at true scale "
                f"1:{den:,}, numbered down the left column, then the right")
    story.append(Paragraph(txt + ".", S["body"]))
    story += [NextPageTemplate("sheet"), PageBreak()]
    hdr_r = project + (f" · Report {info['report_no']}" if info.get("report_no") else "")
    for i, (fn, title) in enumerate(sheets, 1):
        story.append(Sheet(fn, paper.sheet, (f"Section 5 · Drawings · Sheet {i} of {len(sheets)}", hdr_r),
                           toc_text=f"Sheet {i} · {title}"))
    story.append(NextPageTemplate("body"))
    # ---------------------------------------------------------------- 6 closing
    lim = ["Levels and volumes come from the elevation model described in section 2; confirm benchmark levels in "
           "the field before construction.",
           "Quantities are neat volumes: no allowance for waste or overbreak; compaction only through the shrink "
           "and swell factors stated."]
    if pile:
        lim.append("For stockpiles use the surface model (DSM): the bare-ground model removes piles as if they "
                   "were objects.")
    else:
        lim += ["The cross-sections and their end-area volumes are a check; the quantities come from the finer "
                "cell-by-cell method.",
                "Retaining walls, slope protection and drainage structures are not designed in this report."]
    story += closing_section(S, BW, lim, sign)
    # ---------------------------------------------------------------- annexes
    letters = iter("ABC")
    if qty:
        a = next(letters)
        story += [PageBreak()] + section(S, a, f"Annex {a} · Quantities by section", eyebrow=f"ANNEX {a}")
        rows_ = [(r_[0] if r_[0].startswith(("TOTAL", "Plan")) else f"Section {k + 1} · {r_[0]}",) + tuple(r_[1:])
                 for k, r_ in enumerate(qty)]
        story.append(table(S, rows_, [w * BW for w in (0.3, 0.12, 0.14, 0.14, 0.15, 0.15)],
                           header=("Section", "Distance m", "Cut area m²", "Fill area m²", "Cut volume m³",
                                   "Fill volume m³"), num_cols=(1, 2, 3, 4, 5), zebra=True,
                           bold_rows=(len(rows_) - 2, len(rows_) - 1)))
        story.append(Paragraph(f"Sections every {step:g} m along the lot ({esc(along)}), each one across it. Volumes "
                               "between sections by the average end-area method as a check; the plan totals use the "
                               "finer grid method.", S["small"]))
    a = next(letters)
    story += [PageBreak()] + section(S, a, f"Annex {a} · How the numbers were computed", eyebrow=f"ANNEX {a}")
    story.append(Paragraph("The totals recomputed by hand with this plan's numbers. The full method statements, with "
                           "formulas and sources, are in the app under Help › How things are computed.", S["body"]))
    forms = (["V(pile) = Σ max(surface − reference, 0) × cell area", "V(hole) = Σ max(reference − surface, 0) × cell area"]
             if pile else
             ["V(cut) = Σ max(ground − finished, 0) × cell area", "V(fill) = Σ max(finished − ground, 0) × cell area",
              "fill in the ground = fill × (1 + shrink),   loose = |balance| × (1 + swell)"])
    story.append(Paragraph("<br/>".join(esc(f_) for f_ in forms), S["formula"]))
    for line in worked:
        story.append(Paragraph(esc(line), S["body"]))
    story += files_annex(S, BW, files, next(letters))
    doc.make(story)
    return path


__all__ = ["write_lot_report", "corner_rows", "ew"]
