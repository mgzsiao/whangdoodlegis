"""LandXML 1.2 for Civil 3D and other road / survey software.

Road plan: existing ground surface (TIN) around the road, the alignment (lines and circular curves), the ground
profile and the road (design) profile, and cross-sections at every station.
Lot / pile plan: existing ground and finished (or reference) surfaces.

LandXML writes coordinates as Northing Easting (Elevation).
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from datetime import datetime

import numpy as np

from .. import APP_NAME, __version__
from . import measure as ms

NS = "http://www.landxml.org/schema/LandXML-1.2"


def _ne(x, y) -> str:
    return f"{y:.4f} {x:.4f}"


def _root(crs_wkt: str):
    now = datetime.now()
    root = ET.Element("LandXML", {"xmlns": NS, "version": "1.2", "date": now.strftime("%Y-%m-%d"),
                                  "time": now.strftime("%H:%M:%S")})
    ET.SubElement(ET.SubElement(root, "Units"), "Metric", {
        "areaUnit": "squareMeter", "linearUnit": "meter", "volumeUnit": "cubicMeter", "temperatureUnit": "celsius",
        "pressureUnit": "milliBars", "angularUnit": "decimal degrees", "directionUnit": "decimal degrees"})
    attrs = {}
    try:
        from pyproj import CRS
        c = CRS.from_wkt(crs_wkt)
        if c.to_epsg():
            attrs["epsgCode"] = str(c.to_epsg())
        attrs["name"] = c.name
        attrs["ogcWktCode"] = c.to_wkt("WKT1_GDAL")
    except Exception:
        pass
    ET.SubElement(root, "CoordinateSystem", attrs)
    ET.SubElement(root, "Application", {"name": APP_NAME, "version": __version__, "manufacturer": APP_NAME})
    return root


def tin(z: np.ndarray, transform, name: str, desc: str = ""):
    """<Surface> from a height grid: two triangles per grid cell, cells with gaps left out."""
    h, w = z.shape
    ok = np.isfinite(z)
    idx = np.full((h, w), -1, np.int64)
    idx[ok] = np.arange(1, ok.sum() + 1)
    surf = ET.Element("Surface", {"name": name, "desc": desc})
    d = ET.SubElement(surf, "Definition", {"surfType": "TIN"})
    pn = ET.SubElement(d, "Pnts")
    rows, cols = np.nonzero(ok)
    xs = transform.c + (cols + 0.5) * transform.a
    ys = transform.f + (rows + 0.5) * transform.e
    for i, x, y, zz in zip(idx[ok], xs, ys, z[ok]):
        ET.SubElement(pn, "P", {"id": str(int(i))}).text = f"{y:.4f} {x:.4f} {zz:.4f}"
    fc = ET.SubElement(d, "Faces")
    a, b, c, e = idx[:-1, :-1], idx[:-1, 1:], idx[1:, :-1], idx[1:, 1:]
    t1 = (a > 0) & (c > 0) & (b > 0)
    t2 = (b > 0) & (c > 0) & (e > 0)
    # counter-clockwise seen from above (rows go south)
    for p, q, r in zip(a[t1], c[t1], b[t1]):
        ET.SubElement(fc, "F").text = f"{p} {q} {r}"
    for p, q, r in zip(b[t2], c[t2], e[t2]):
        ET.SubElement(fc, "F").text = f"{p} {q} {r}"
    return surf, int(t1.sum() + t2.sum())


def _surface_grid(dem_path, bounds, max_points):
    dem = ms.read_area(dem_path, bounds, max_cells=max_points, pad_cells=1)
    return dem.z, dem.transform


def road_xml(path: str, r, name: str, dem_path: str, max_points: int = 150_000) -> str:
    from shapely.geometry import LineString
    al = r.alignment
    root = _root(r.crs_wkt)
    xs = [st.catch_xy(k) for st in r.stations for k in (-1, 1)]
    ln = LineString(list(r.centre) + xs)
    z, tr = _surface_grid(dem_path, ln.buffer(10).bounds, max_points)
    surfs = ET.SubElement(root, "Surfaces")
    s, _n = tin(z, tr, "Existing ground", "Drone elevation model around the road")
    surfs.append(s)
    als = ET.SubElement(root, "Alignments", {"name": name})
    a = ET.SubElement(als, "Alignment", {"name": name, "length": f"{al.length:.4f}", "staStart": "0"})
    cg = ET.SubElement(a, "CoordGeom")
    for e in al.elements:
        if e.kind == "line":
            ln_ = ET.SubElement(cg, "Line", {"length": f"{e.length:.4f}"})
            ET.SubElement(ln_, "Start").text = _ne(*e.start)
            ET.SubElement(ln_, "End").text = _ne(*e.end)
        else:
            c = e.curve
            chord = 2 * c.radius * math.sin(math.radians(c.delta) / 2)
            cv = ET.SubElement(cg, "Curve", {
                "rot": "ccw" if c.left else "cw", "crvType": "arc", "radius": f"{c.radius:.4f}",
                "length": f"{c.length:.4f}", "delta": f"{c.delta:.6f}", "tangent": f"{c.tangent:.4f}",
                "chord": f"{chord:.4f}", "midOrd": f"{c.mid_ordinate:.4f}",
                "external": f"{c.radius * (1 / math.cos(math.radians(c.delta) / 2) - 1):.4f}"})
            ET.SubElement(cv, "Start").text = _ne(*c.pc)
            ET.SubElement(cv, "Center").text = _ne(*c.center)
            ET.SubElement(cv, "End").text = _ne(*c.pt)
            ET.SubElement(cv, "PI").text = _ne(*c.pi)
    prof = ET.SubElement(a, "Profile", {"name": name})
    ok = np.isfinite(r.prof_g)
    step = max(1, int(len(r.prof_d) / 2000))
    gd = [f"{d:.3f} {g:.3f}" for d, g, o in zip(r.prof_d[::step], r.prof_g[::step], ok[::step]) if o]
    ET.SubElement(ET.SubElement(prof, "ProfSurf", {"name": "Existing ground"}), "PntList2D").text = " ".join(gd)
    pa = ET.SubElement(prof, "ProfAlign", {"name": f"{name} road surface"})
    sd = np.array([st.s for st in r.stations])
    sz = np.array([st.design for st in r.stations])
    keep = [0]
    for i in range(1, len(sd) - 1):        # keep the points where the grade changes (PVIs)
        g1 = (sz[i] - sz[keep[-1]]) / max(sd[i] - sd[keep[-1]], 1e-9)
        g2 = (sz[i + 1] - sz[i]) / max(sd[i + 1] - sd[i], 1e-9)
        if abs(g1 - g2) > 1e-4:
            keep.append(i)
    keep.append(len(sd) - 1)
    for i in keep:
        ET.SubElement(pa, "PVI").text = f"{sd[i]:.4f} {sz[i]:.4f}"
    xss = ET.SubElement(a, "CrossSects", {"name": f"{name} sections"})
    for st in r.stations:
        if st.offsets is None:
            continue
        cs = ET.SubElement(xss, "CrossSect", {"sta": f"{st.s:.4f}", "name": st.label,
                                              "areaCut": f"{st.cut_area:.4f}", "areaFill": f"{st.fill_area:.4f}"})
        o, g, d = st.offsets, st.ground_xs, st.design_xs
        stp = max(1, int(round(0.5 / max(o[1] - o[0], 1e-6)))) if len(o) > 1 else 1
        sel = sorted(set(range(0, len(o), stp)) | {len(o) - 1})
        ET.SubElement(ET.SubElement(cs, "CrossSectSurf", {"name": "Existing ground"}), "PntList2D").text = \
            " ".join(f"{o[i]:.3f} {g[i]:.3f}" for i in sel if np.isfinite(g[i]))
        dd = [i for i in range(len(o)) if np.isfinite(d[i])]
        if dd:
            # every design point (they carry the shape: crown, shoulders, ditches)
            pts = " ".join(f"{o[i]:.3f} {d[i]:.3f}" for i in dd)
            ET.SubElement(ET.SubElement(cs, "DesignCrossSectSurf", {"name": "Road and sides", "closedArea": "false"}),
                          "PntList2D").text = pts
    return _write(root, path)


def surfaces_xml(path: str, t, name: str, dem_path: str, max_points: int = 150_000) -> str:
    """Lot / pile: existing ground around the works and the finished (or reference) surface."""
    root = _root(t.crs_wkt)
    surfs = ET.SubElement(root, "Surfaces")
    h, w = t.dz.shape
    tr = t.transform
    b = (tr.c, tr.f + h * tr.e, tr.c + w * tr.a, tr.f)
    m = 0.1 * max(b[2] - b[0], b[3] - b[1])
    z, gtr = _surface_grid(dem_path, (b[0] - m, b[1] - m, b[2] + m, b[3] + m), max_points)
    s, _ = tin(z, gtr, "Existing ground", "Drone elevation model")
    surfs.append(s)
    f = t.finished
    k = max(1, int(math.ceil(math.sqrt(np.isfinite(f).sum() / max_points)))) if np.isfinite(f).any() else 1
    if k > 1:
        hh, ww = (h // k) * k, (w // k) * k
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            f = np.nanmean(f[:hh, :ww].reshape(hh // k, k, ww // k, k), axis=(1, 3))
        from affine import Affine
        tr = tr * Affine.scale(k, k)
    s2, _ = tin(f, tr, "Finished ground" if t.kind == "pad" else "Measured from",
                f"{name}: surface after the works" if t.kind == "pad" else f"{name}: reference surface")
    surfs.append(s2)
    return _write(root, path)


def _write(root, path):
    ET.indent(root, space=" ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path
