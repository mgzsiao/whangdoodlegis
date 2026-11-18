"""'How things are computed': plain-language method statements with formulas and sources, one per process.

Shown in the app (Help > How things are computed) and printable to PDF. Keep in step with the code:
every number quoted here is the one the code uses.
"""
from __future__ import annotations

from . import road as rdm, vertical as vt
from .crs import LUZON_TOWGS84

F = '<p style="margin:6px 0 6px 18px; font-family: Consolas, monospace; background:#F3F5F9; padding:6px;">'


def _f(*lines: str) -> str:
    return F + "<br>".join(ln.replace("  ", "&nbsp;&nbsp;") for ln in lines) + "</p>"


def _sec(title: str, *paras: str) -> str:
    return f"<h3>{title}</h3>" + "".join(p if p.startswith("<") else f"<p>{p}</p>" for p in paras)


def _list(*items: str) -> str:
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def _k_table() -> str:
    rows = "".join(f"<tr><td>{v} km/h</td><td>{a} m</td><td>{b}</td><td>{c}</td><td>{vt.min_length(v):.0f} m</td></tr>"
                   for v, (a, b, c) in vt.K_TABLE.items())
    return ('<table cellpadding="4" cellspacing="0" border="1" style="border-collapse:collapse;">'
            "<tr><th>Design speed</th><th>Stopping sight distance</th><th>K crest</th><th>K sag</th>"
            f"<th>Minimum length</th></tr>{rows}</table>")


def _std_table() -> str:
    rows = "".join(f"<tr><td>{s['label']}</td><td>{s['width']:g} m</td><td>{s['pavement_t'] * 1000:.0f} mm</td>"
                   f"<td>{s['shoulder_w']:g} m @ {s['shoulder_pct']:g}%</td><td>{s['crown_pct']:g}%</td>"
                   f"<td>{s['min_radius']:g} m</td><td>{s['max_grade']:g}%</td></tr>"
                   for s in rdm.STANDARDS.values())
    return ('<table cellpadding="4" cellspacing="0" border="1" style="border-collapse:collapse;">'
            "<tr><th>Road</th><th>Width</th><th>Concrete</th><th>Shoulders</th><th>Cross slope</th>"
            f"<th>Min. radius</th><th>Max. grade</th></tr>{rows}</table>")


def topics() -> list[tuple[str, str, str]]:
    """[(key, title, html), ...]"""
    lm, lo = LUZON_TOWGS84["mindanao"], LUZON_TOWGS84["other"]
    return [
        ("drone", "Drone mapping (orthophoto, DSM, DTM, contours)", "".join([
            _sec("What it does", "The photos are processed by OpenDroneMap (ODM), an open-source photogrammetry "
                 "engine installed with the app. It finds the same features in overlapping photos, works out where "
                 "each photo was taken and how the camera was pointed, then builds a 3-D point cloud of the ground."),
            _sec("Steps", _list(
                "<b>Matching</b>: thousands of feature points are matched between overlapping photos.",
                "<b>Structure from motion</b>: camera positions and the 3-D positions of the matched points are solved "
                "together (bundle adjustment), using the drone's GPS (and ground control points if given).",
                "<b>Dense point cloud</b>: depth is computed for almost every pixel (multi-view stereo).",
                "<b>DSM</b> (surface model): the highest points per grid cell – ground, roofs, trees.",
                "<b>DTM</b> (bare ground): points are classified as ground with the SMRF filter (simple morphological "
                "filter) and only ground points are gridded; gaps under buildings are filled from the ground around.",
                "<b>Orthophoto</b>: the photos are projected onto the surface and blended into one map image.",
                "<b>Contours</b>: lines of equal height drawn from the DTM (or the DSM if there is no DTM).")),
            _sec("Heights", "Drone GPS heights are ellipsoid heights. With “Convert ellipsoid heights to sea "
                 "level” the app subtracts the EGM96 geoid height N at the site:",
                 _f("H (above sea level) = h (ellipsoid) − N (EGM96)"),
                 "EGM96 15-minute grid, US National Geospatial-Intelligence Agency (included with the app)."),
            _sec("Accuracy", "Without ground control points, positions are typically within a few metres and "
                 "heights can be off by several metres overall, but differences in height inside the same map "
                 "(slopes, volumes) are much more reliable. With well-spread ground control points accuracy is "
                 "usually a few centimetres to a decimetre; the README and quality report give the check results."),
        ])),
        ("georef", "Georeferencing a scanned map", "".join([
            _sec("What it does", "A scanned or exported map with a printed coordinate grid is given real-world "
                 "coordinates, so it can be overlaid on other maps."),
            _sec("Found by the app", _list(
                "The scan's small rotation is measured from its long straight lines and removed.",
                "The map frame (neat line), the grid ticks on the frame and the + grid crosses inside are found with "
                "sub-pixel precision.",
                "You type the value of the grid labels (or the first one and the spacing); every crossing of a "
                "vertical and a horizontal grid line becomes a control point.")),
            _sec("Point the grid myself", "You click grid crossings and type their coordinates. Each click is "
                 "fine-tuned onto the crossing: the app looks within 10 map pixels for a place where a short "
                 "vertical stroke and a short horizontal stroke meet (diagonal lines such as roads are ignored) and "
                 "takes the centre of the line width. After three crossings the app suggests the coordinates of the "
                 "next one from the fit so far, rounded to the grid spacing."),
            _sec("Fit", "The control points are fitted by least squares in a projected system in metres (UTM of the "
                 "site, or the chosen output system):",
                 _f("Affine:          X = a0 + a1·u + a2·v      Y = b0 + b1·u + b2·v",
                    "Polynomial 2/3:  adds u², u·v, v² (and cubic) terms for warped scans"),
                 "u, v are pixel positions. The error of each point (residual) is the distance between its typed "
                 "position and where the fit puts it; the average error is the root mean square:",
                 _f("RMSE = √( Σ residual² / n )"),
                 "Below 1 pixel of the map is excellent, up to 3 pixels acceptable. The app needs 3 points for the "
                 "affine fit (4 when pointing the grid yourself, so there is always a check)."),
            _sec("Datum (the system printed on the map)",
                 f"Older NAMRIA / MGB maps use Luzon 1911. Two official shifts to WGS 84 exist and the app chooses "
                 f"by location: Mindanao dx, dy, dz = {lm} m; rest of the Philippines {lo} m (EPSG:1162 and "
                 "EPSG:1161). PRS92 uses the official 7-parameter transformation (EPSG:15708). The shift between "
                 "Luzon 1911 and WGS 84 is about 200 m, so choosing the right datum matters."),
            _sec("Hazard classes to polygons", "Each pixel's colour is compared with colours measured from MGB "
                 "geohazard maps (the colours actually printed on the map, not the legend squares, which differ "
                 "after JPEG export). Pixels close enough to a class colour are grouped into polygons; debris-flow "
                 "zones (green with white hatching) are found by their texture."),
        ])),
        ("flood", "Flood scenarios", "".join([
            _sec("Still water (“bathtub”)", "A flat water surface is raised to the chosen level. Every "
                 "ground cell below that level and connected to the source (the river or sea point you clicked, or "
                 "the map edge) is flooded:", _f("depth = water level − ground level  (where > 0)"),
                 "Closed hollows that water cannot reach stay dry unless “every low cell” is chosen."),
            _sec("River rise (HAND)", "For a river the water surface follows the river's own slope. The app "
                 "computes, for every cell, its height above the river cell it drains to (HAND, height above "
                 "nearest drainage; Rennó et al. 2008, Nobre et al. 2011). Drainage directions come from the "
                 "Priority-Flood algorithm (Barnes et al. 2014), which handles pits and flat areas. If the river "
                 "rises by h metres:", _f("flooded where HAND < h,   depth = h − HAND")),
            _sec("Depth classes (MGB)", _list("Low: less than 0.5 m", "Moderate: 0.5 m to 1 m (both included)",
                                              "High: more than 1 m")),
            _sec("Limits", "No rainfall, timing, flow speed, dikes or drainage pipes. It answers “what is under "
                 "water if water stands at this level” – a screening tool. Use the bare-ground DTM: "
                 "buildings and trees in a DSM would block the water."),
        ])),
        ("landslide", "Landslide scenarios", "".join([
            _sec("Where slopes can fail (infinite slope)", "Each cell is treated as a long uniform slope "
                 "(Skempton & DeLory 1957). The factor of safety is the soil's strength divided by the pull "
                 "down the slope:",
                 _f("FS = [ c' + (γ − m·γw)·z·cos²β·tanφ' ] "
                    "/ [ γ·z·sinβ·cosβ ]"),
                 _list("c' cohesion (kPa), φ' friction angle, γ soil unit weight (kN/m³), "
                       "γw = 9.81 kN/m³ (water)",
                       "z soil depth (m), β slope angle from the terrain model",
                       "m ground wetness: 0 = dry, 1 = soaked to the surface (prolonged typhoon rain)",
                       "FS below 1: the slope can fail; these cells are release areas.")),
            _sec("How far it runs (energy line)", "The moving mass keeps an energy height E that grows when it drops "
                 "and is used up by friction along the travel angle (Heim 1932; Corominas 1996; as in Flow-R):",
                 _f("E(next) = E + (z here − z next) − distance × tan(travel angle)",
                    "speed v = √(2·g·E)"),
                 "It stops where E reaches 0. Flow follows the fall line and spreads on flat ground (< 4°). "
                 "Typical travel angles: debris flows 11–20°, soil slides 20–30°, rock fall "
                 "and small slides 30–35°."),
            _sec("Limits", "A scenario tool for planning, not a site-specific geotechnical design. Soil values are "
                 "typical ranges unless measured."),
        ])),
        ("measure", "Measure tools (point, path, area, volume)", "".join([
            _sec("Heights", "Heights are read from the elevation file itself (not the picture on screen), at full "
                 "detail for the part measured, by bilinear interpolation between the four nearest cells."),
            _sec("Path and side view", "Points every cell along the line. The flat length is the sum of straight "
                 "pieces; the 3-D (along the ground) length and slopes are measured over 0.5 m steps so that a "
                 "few centimetres of surface noise do not inflate them:",
                 _f("3-D length = Σ √(Δd² + Δz²)    slope % = Δz / Δd × 100")),
            _sec("Area", "Flat area and perimeter of the outline (shoelace formula). The surface area (along the "
                 "slopes) is the flat area times the average stretch factor of the ground inside, with slopes "
                 "measured over 0.5 m blocks:", _f("surface area = flat area × average(1 / cos(slope))")),
            _sec("Volume", "Every cell inside the outline is compared with a reference surface:",
                 _f("V(dig) = Σ max(ground − reference, 0) × cell area",
                    "V(fill) = Σ max(reference − ground, 0) × cell area"),
                 _list("Ground around the edge: a surface stretched across the outline through the heights along "
                       "it (triangulated) – best for piles and holes.",
                       "Average height of the edge, a fixed height you type, or the lowest ground inside.")),
            _sec("Trucks", _f("loose volume = bank volume × (1 + swell)", "trips = loose volume ÷ truck "
                 "size, rounded up"), "Trips are given for 6 m³ and 10 m³ trucks plus your own size."),
            _sec("Compare two surveys", "The later survey minus the earlier one inside the outline. With line-up, "
                 "the later survey is shifted up or down so the ground in a ring 3–15 m around the outline "
                 "matches on average (median); this removes most height offset between flights without ground "
                 "control."),
        ])),
        ("pad", "Earthworks: level a lot or pad", "".join([
            _sec("Finished ground", "Inside the outline the finished ground is level, or sloped for drainage by "
                 "s % toward the chosen direction:",
                 _f("finished = L − s/100 × (distance along the drainage direction from the centre)")),
            _sec("Balanced height", "With “balanced” the height L at the centre is found by bisection "
                 "(halving the search range until within 0.2 mm) so that the soil dug equals the soil the fill "
                 "needs after packing:", _f("dig = fill × (1 + shrink)")),
            _sec("Sloped sides", "Outside the outline the sides fall (fill) or rise (cut) at H : V until they meet "
                 "the ground (“daylight”). The distance d of each cell from the pad edge comes from a "
                 "distance transform (corrected by half a cell):",
                 _f("side = clip(ground,  edge − d / fill ratio,  edge + d / cut ratio)")),
            _sec("Volumes", _f("V = Σ (ground − finished) × cell area   (positive = dig, "
                               "negative = fill)"),
                 "The report adds cross-sections across the lot with end-area volumes between them as a check "
                 "you can follow by hand; they agree with the grid totals within a few percent."),
        ])),
        ("road", "Earthworks: road, path or canal", "".join([
            _sec("Stations", "Stations every 5, 10, 15 or 20 m along the centre line, plus every bend, curve "
                 "start and end, and the end of the road. Named k+mmm (0+120 = 120 m from the start)."),
            _sec("Horizontal curves", "At a bend of angle Δ the road follows a circular arc of radius R:",
                 _f("T = R·tan(Δ/2)   L = π·R·Δ/180   E = R·(1/cos(Δ/2) − 1)"
                    "   M = R·(1 − cos(Δ/2))"),
                 "Curves are made smaller if the straights next to them are too short."),
            _sec("Road height", _list(
                "Keep slopes gentle: the ground is smoothed (moving average over twice the station spacing, at "
                "least 20 m) and grades are limited forward and backward to the maximum %.",
                "Follow the ground (smoothed), keep it level, one straight slope, or through typed heights.")),
            _sec("Vertical curves", "Straight grades meet at grade change points (PVI). The app proposes the PVIs "
                 "by simplifying the chosen road height (Douglas–Peucker, 0.10 m, at least 20 m apart); you "
                 "can edit them. At each PVI a symmetric parabola joins the grades g1 and g2 (%):",
                 _f("A = g2 − g1     L = max(K·|A|, 0.6 × speed)     K = L / |A|",
                    "z(x) = z_PVC + g1·x/100 + A·x² / (200·L)    x = metres after the PVC",
                    "PVC = PVI − L/2,   PVT = PVI + L/2,   e = A·L / 800 (curve to PVI)",
                    "high / low point at x = −g1·L / A"),
                 "Crest curves (over a hump) use K for stopping sight distance, sag curves (in a dip) K for "
                 "headlight reach. Curves are shortened if they would overlap and then flagged.",
                 _k_table(), f"<p><i>Source: {vt.AASHTO_SOURCE}.</i></p>"),
            _sec("Cross-sections", "At each station, across the road (looking toward higher stations): carriageway "
                 "with a crown (cross slope from the centre), shoulders, optional ditches in cut (inner side 1 : 1), "
                 "the pavement thickness below the road surface, then side slopes at H : V that meet the ground. "
                 "Cut and fill areas are added up across the section in small steps (trapezoid rule):",
                 _f("A(cut) = Σ max(ground − design, 0) × Δoffset")),
            _sec("Volumes and mass haul", "Average end-area between neighbouring stations:",
                 _f("V = (A1 + A2) / 2 × L",
                    "running balance = cut so far − fill so far × (1 + shrink)"),
                 "Where the balance line crosses zero, the dug soil and the fill needs are equal (balance points). "
                 "On curves no curvature correction is applied (small unless the cut or fill is lopsided on a tight "
                 "curve)."),
            _sec("DPWH road standards", "From " + rdm.DPWH_SOURCE + ":", _std_table(),
                 "<p>" + rdm.STANDARD_NOTES + "</p>"),
            _sec("Files for Civil 3D and CAD", "LandXML 1.2: ground surface (TIN), alignment with lines and "
                 "curves, the profile (PVIs with ParaCurve vertical curves) and cross-sections. DXF: plan, profile "
                 "(heights ×10) and cross-section sheets."),
        ])),
        ("pile", "Piles, holes and deposits", "".join([
            _sec("Method", "As the Measure volume tool: every cell inside the outline is compared with the reference "
                 "surface you chose (the ground around the edge, its average height, a fixed height, or the lowest "
                 "ground). Above = pile, below = hole. Truck trips from the loose volume (swell)."),
            _sec("Tip", "Use the surface model (DSM) for stockpiles: the bare-ground DTM removes piles as if they "
                 "were objects."),
        ])),
        ("convert", "Coordinate systems and Change SHP CRS", "".join([
            _sec("Method", "Coordinates are transformed with PROJ (the same library as QGIS). The system of each "
                 "file is read from its .prj / header; ESRI names for PRS92 and Luzon 1911 are matched to their EPSG "
                 "codes so the official datum shifts are used. Attributes are copied unchanged."),
            _sec("Shapefiles", "A shapefile is several files with the same name: .shp (shapes), .shx (index), "
                 ".dbf (attributes), .prj (coordinate system) and .cpg (text encoding). Picking any of them brings "
                 "in the whole set; files inside a .zip are read in place. A missing .shx is rebuilt from the .shp "
                 "in a temporary copy (your folder is not changed); without a .dbf only the shapes can be "
                 "converted; without a .prj you say which system the file is in."),
            _sec("Datum shifts used", _list(
                "PRS92 ↔ WGS 84: official 7-parameter transformation (EPSG:15708).",
                f"Luzon 1911 ↔ WGS 84: Mindanao {lm} m, rest of the Philippines {lo} m (chosen by location).",
                "KML / KMZ are always WGS 84 (Google Earth standard).")),
            _sec("Rasters", "Grids are resampled to the new system: bilinear for elevations, nearest neighbour for "
                 "pictures and classes (so colours and class codes are not mixed)."),
            _sec("Own files", "Elevation files in latitude / longitude or feet are converted to metres (UTM of the "
                 "site, bilinear) before measuring, because slopes and volumes need metres."),
        ])),
        ("model3d", "3-D models", _sec("Method", "The elevation grid is turned into triangles (two per cell, thinned "
                                       "for lighter models). The photo model drapes the orthophoto as its texture. "
                                       "Models are moved near the origin for SketchUp; the README gives the real "
                                       "coordinates of the origin. Units are metres.")),
    ]


def document_html() -> str:
    from .. import APP_NAME, __version__
    parts = [f"<h1>How things are computed</h1><p>{APP_NAME} {__version__}. Plain-language method statements with "
             "the formulas and sources the app uses.</p>"]
    for _k, title, body in topics():
        parts.append(f'<h2 style="page-break-before: always;">{title}</h2>{body}')
    return "".join(parts)
