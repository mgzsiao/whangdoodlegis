"""CAD drawing (DXF, AutoCAD R12 format: opens in AutoCAD, Civil 3D, BricsCAD, LibreCAD, QGIS).

Road: plan in map coordinates (centre line with curves, stations, curve points, edges of the works,
cross-section lines), a profile sheet and cross-section sheets placed beside the plan.
Lot / pile: outline, dig / fill zones, spot levels and a section through the middle.
All lengths in metres.
"""
from __future__ import annotations

import math

import numpy as np

# AutoCAD colour numbers
RED, YELLOW, GREEN, CYAN, BLUE, MAGENTA, WHITE, GREY, BROWN = 1, 2, 3, 4, 5, 6, 7, 8, 32


class Dxf:
    def __init__(self):
        self.out: list[str] = []
        self.ext = [math.inf, math.inf, -math.inf, -math.inf]

    def _g(self, *pairs):
        for code, val in pairs:
            self.out.append(f"{code}\n{val}\n")

    def _grow(self, x, y):
        e = self.ext
        e[0], e[1], e[2], e[3] = min(e[0], x), min(e[1], y), max(e[2], x), max(e[3], y)

    def line(self, a, b, layer, color=256):
        self._g((0, "LINE"), (8, layer), (62, color), (10, f"{a[0]:.4f}"), (20, f"{a[1]:.4f}"), (30, "0.0"),
                (11, f"{b[0]:.4f}"), (21, f"{b[1]:.4f}"), (31, "0.0"))
        self._grow(*a[:2])
        self._grow(*b[:2])

    def poly(self, pts, layer, color=256, closed=False, z=None):
        """2-D polyline, or 3-D when z values are given."""
        pts = [p for p in pts if all(np.isfinite(p[:2]))]
        if len(pts) < 2:
            return
        flag = (1 if closed else 0) | (8 if z is not None else 0)
        self._g((0, "POLYLINE"), (8, layer), (62, color), (66, 1), (10, "0.0"), (20, "0.0"), (30, "0.0"), (70, flag))
        for i, p in enumerate(pts):
            zz = z[i] if z is not None else 0.0
            self._g((0, "VERTEX"), (8, layer), (10, f"{p[0]:.4f}"), (20, f"{p[1]:.4f}"), (30, f"{zz:.4f}"),
                    (70, 32 if z is not None else 0))
            self._grow(p[0], p[1])
        self._g((0, "SEQEND"), (8, layer))

    def text(self, p, h, s, layer, color=256, rot=0.0, align="left"):
        s = str(s).replace("\n", " ")
        ha = {"left": 0, "center": 1, "right": 2}[align]
        g = [(0, "TEXT"), (8, layer), (62, color), (10, f"{p[0]:.4f}"), (20, f"{p[1]:.4f}"), (30, "0.0"),
             (40, f"{h:.3f}"), (1, s), (50, f"{rot:.3f}")]
        if ha:
            g += [(72, ha), (11, f"{p[0]:.4f}"), (21, f"{p[1]:.4f}"), (31, "0.0")]
        self._g(*g)
        self._grow(*p[:2])

    def point(self, p, layer, color=256):
        self._g((0, "POINT"), (8, layer), (62, color), (10, f"{p[0]:.4f}"), (20, f"{p[1]:.4f}"),
                (30, f"{p[2] if len(p) > 2 else 0.0:.4f}"))
        self._grow(p[0], p[1])

    def save(self, path):
        e = self.ext if math.isfinite(self.ext[0]) else [0, 0, 1, 1]
        head = ("0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n"
                f"9\n$EXTMIN\n10\n{e[0]:.3f}\n20\n{e[1]:.3f}\n30\n0.0\n9\n$EXTMAX\n10\n{e[2]:.3f}\n20\n{e[3]:.3f}\n"
                "30\n0.0\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n")
        with open(path, "w", encoding="ascii", errors="replace", newline="\r\n") as f:
            f.write(head)
            f.write("".join(self.out))
            f.write("0\nENDSEC\n0\nEOF\n")
        return path


def _grid(d: Dxf, x0, y0, w, h, nx, ny, layer):
    for i in range(nx + 1):
        x = x0 + w * i / nx
        d.line((x, y0), (x, y0 + h), layer, GREY)
    for j in range(ny + 1):
        y = y0 + h * j / ny
        d.line((x0, y), (x0 + w, y), layer, GREY)


def _nice(v):
    if v <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(v))
    for k in (1, 2, 5, 10):
        if p * k >= v:
            return p * k
    return p * 10


def road_dxf(path: str, r, name: str, vex: float = 10.0) -> str:
    from .road import station_label
    d = Dxf()
    st = r.stations
    # ---- plan (map coordinates)
    d.poly([tuple(p) for p in r.centre], "PLAN_CENTRE_LINE", RED)
    d.poly([tuple(p) for p in r.alignment.points], "PLAN_TANGENTS", GREY)
    d.poly([s.catch_xy(-1) for s in st], "PLAN_WORKS_EDGE", YELLOW)
    d.poly([s.catch_xy(1) for s in st], "PLAN_WORKS_EDGE", YELLOW)
    th = max(0.8, r.length / 250)
    for s in st:
        a, b = s.catch_xy(-1), s.catch_xy(1)
        d.line(a, b, "PLAN_SECTION_LINES", CYAN)
        ang = math.degrees(math.atan2(s.ty, s.tx)) + 90
        d.text((s.x - s.ty * 0 + th * 0.3, s.y + th * 0.3), th, s.label, "PLAN_STATIONS", WHITE, rot=ang - 90)
        d.point((s.x, s.y, s.design), "PLAN_STATIONS", WHITE)
    for k, c in enumerate(r.alignment.curves, 1):
        for lab, p in (("PC", c.pc), ("PT", c.pt), ("PI", c.pi)):
            d.point((p[0], p[1], 0.0), "PLAN_CURVE_POINTS", MAGENTA)
            d.text((p[0] + th * 0.4, p[1] - th * 1.2), th * 0.8, f"{lab}{k}", "PLAN_CURVE_POINTS", MAGENTA)
        d.text((c.pi[0] + th * 0.4, c.pi[1] + th * 0.4), th * 0.8,
               f"R={c.radius:.1f} L={c.length:.2f} T={c.tangent:.2f} D={c.delta:.2f}", "PLAN_CURVE_POINTS", MAGENTA)
    x_min, y_min, x_max, y_max = d.ext
    # ---- profile sheet to the right of the plan
    ox = x_max + max(30.0, 0.2 * (x_max - x_min))
    oy = y_min
    z0 = math.floor(min(np.nanmin(r.prof_g), np.nanmin(r.prof_z))) - 1
    z1 = math.ceil(max(np.nanmax(r.prof_g), np.nanmax(r.prof_z))) + 1
    L = r.length
    H = (z1 - z0) * vex
    gstep = _nice(L / 10)
    zstep = _nice((z1 - z0) / 6)
    d.text((ox, oy + H + 4 * th), th * 1.6, f"{name} - PROFILE (heights x{vex:g})", "PROFILE_TEXT", WHITE)
    for i in range(int(L // gstep) + 1):
        x = ox + i * gstep
        d.line((x, oy), (x, oy + H), "PROFILE_GRID", GREY)
        d.text((x, oy - 2 * th), th, station_label(i * gstep), "PROFILE_TEXT", WHITE, align="center")
    zz = math.ceil(z0 / zstep) * zstep
    while zz <= z1 + 1e-9:
        y = oy + (zz - z0) * vex
        d.line((ox, y), (ox + L, y), "PROFILE_GRID", GREY)
        d.text((ox - th, y - th / 2), th, f"{zz:.1f}", "PROFILE_TEXT", WHITE, align="right")
        zz += zstep
    ok = np.isfinite(r.prof_g)
    d.poly([(ox + a, oy + (b - z0) * vex) for a, b, o in zip(r.prof_d, r.prof_g, ok) if o], "PROFILE_GROUND", BROWN)
    d.poly([(ox + a, oy + (b - z0) * vex) for a, b in zip(r.prof_d, r.prof_z)], "PROFILE_ROAD", RED)
    for s in st:
        y = oy + (s.design - z0) * vex
        d.line((ox + s.s, oy), (ox + s.s, y), "PROFILE_STATIONS", GREY)
    # ---- cross-section sheets under the profile (true scale)
    cols = 4
    widths = [max(abs(s.offsets[0]), abs(s.offsets[-1])) for s in st if s.offsets is not None] or [10]
    cw = 2 * max(widths) + 6
    ch = max(8.0, max((np.nanmax(s.ground_xs) - np.nanmin(s.ground_xs)) if s.offsets is not None and
                      np.isfinite(s.ground_xs).any() else 4 for s in st) + 6)
    top = oy - 8 * th
    d.text((ox, top), th * 1.6, f"{name} - CROSS-SECTIONS (true scale, looking toward higher stations)",
           "SECTIONS_TEXT", WHITE)
    for i, s in enumerate(st):
        if s.offsets is None:
            continue
        cx = ox + (i % cols) * cw + cw / 2
        cy = top - 4 * th - (i // cols + 1) * ch
        zc = s.design
        d.line((cx, cy - ch / 2 + 1), (cx, cy + ch / 2 - 2), "SECTIONS_GRID", GREY)
        d.text((cx, cy + ch / 2 - 1.5), th, f"{s.label}  dig {s.cut_area:.2f} m2  fill {s.fill_area:.2f} m2",
               "SECTIONS_TEXT", WHITE, align="center")
        g = [(cx + o, cy + (z - zc)) for o, z in zip(s.offsets, s.ground_xs) if np.isfinite(z)]
        d.poly(g, "SECTIONS_GROUND", BROWN)
        dd = [(cx + o, cy + (z - zc)) for o, z in zip(s.offsets, s.design_xs) if np.isfinite(z)]
        d.poly(dd, "SECTIONS_ROAD", RED)
        d.text((cx - cw / 2 + 1, cy - ch / 2 + 1.5), th * 0.8, f"road {s.design:.2f} m", "SECTIONS_TEXT", WHITE)
    return d.save(path)


def pad_dxf(path: str, t, outline, spots, name: str) -> str:
    from . import exporters as ex
    d = Dxf()
    d.poly([tuple(p) for p in outline], "OUTLINE", YELLOW, closed=True)
    cell = t.cell
    for mask, layer, col in ((t.dz > 0.005, "ZONE_DIG", RED), (t.dz < -0.005, "ZONE_FILL", BLUE)):
        for g, _v in ex.polygons_from_mask(mask, t.transform, simplify=cell * 1.5, min_area=(cell * 4) ** 2):
            d.poly(list(g.exterior.coords), layer, col, closed=True)
    size = max(0.3, math.sqrt(t.area.area_2d) / 120)
    for row in spots:
        _n, x, y, g, f, dz = row
        d.point((x, y, f), "SPOT_LEVELS", WHITE)
        d.text((x + size * 0.4, y + size * 0.2), size, f"{f:.2f}", "SPOT_FINISHED", GREEN)
        d.text((x + size * 0.4, y - size * 1.2), size * 0.8, f"{dz:+.2f}", "SPOT_DIG_FILL", RED if dz > 0 else BLUE)
    x0, y0, x1, y1 = d.ext
    d.text((x0, y1 + 3 * size), size * 2, f"{name} - finished levels (green), dig + / fill - (red / blue)",
           "TEXT", WHITE)
    return d.save(path)
