"""3-D models of a drone map for SketchUp and other 3-D programs.

"photo" : the surface model (DSM) with the drone photo as its texture - looks like the site.
"ground": the bare-ground model (DTM), plain - light and smooth, good to design on.
Formats: Collada .dae (SketchUp: File > Import; textured) and binary .stl (no texture).

SketchUp handles large coordinates badly, so the model is moved near the origin; the real map coordinates of
the origin are written in the README. Units are metres.
"""
from __future__ import annotations

import math
import os
import struct
import xml.etree.ElementTree as ET

import numpy as np

from .. import APP_NAME, __version__
from . import measure as ms

DETAIL = {"light": 100_000, "normal": 300_000, "detailed": 800_000}     # triangles (about)


def grid_mesh(dem_path: str, bounds, max_tris: int, poly=None):
    """Triangles over the height grid. Returns verts (N,3, local), faces (M,3), uv (N,2), origin, grid Dem."""
    dem = ms.read_area(dem_path, bounds, max_cells=max(4, max_tris // 2), pad_cells=0)
    z = dem.z.copy()
    if poly is not None:
        from rasterio.features import geometry_mask
        inside = geometry_mask([poly], out_shape=z.shape, transform=dem.transform, invert=True, all_touched=True)
        z[~inside] = np.nan
    ok = np.isfinite(z)
    if ok.sum() < 4:
        raise ValueError("There is no elevation data in this area.")
    h, w = z.shape
    t = dem.transform
    rows, cols = np.nonzero(ok)
    xs = t.c + (cols + 0.5) * t.a
    ys = t.f + (rows + 0.5) * t.e
    ox, oy = round(float(np.mean(xs))), round(float(np.mean(ys)))
    oz = math.floor(float(np.nanmin(z)))
    idx = np.full((h, w), -1, np.int64)
    idx[ok] = np.arange(ok.sum())
    verts = np.column_stack([xs - ox, ys - oy, z[ok] - oz]).astype(np.float64)
    uv = np.column_stack([(cols + 0.5) / w, 1 - (rows + 0.5) / h])
    a, b, c, e = idx[:-1, :-1], idx[:-1, 1:], idx[1:, :-1], idx[1:, 1:]
    t1 = (a >= 0) & (b >= 0) & (c >= 0)
    t2 = (b >= 0) & (c >= 0) & (e >= 0)
    faces = np.vstack([np.column_stack([a[t1], c[t1], b[t1]]), np.column_stack([b[t2], c[t2], e[t2]])])
    return verts, faces, uv, (ox, oy, oz), dem


def texture(ortho_path: str, dem, max_px: int = 4096):
    """The drone photo over the same area as the mesh grid (PIL image)."""
    import rasterio
    from affine import Affine
    from PIL import Image
    from rasterio.warp import Resampling, reproject
    h, w = dem.shape
    t = dem.transform
    with rasterio.open(ortho_path) as src:
        native = abs(src.transform.a)
        width_m, height_m = w * abs(t.a), h * abs(t.e)
        k = min(max_px / max(width_m / native, height_m / native, 1), 1.0)
        tw, th = max(2, int(width_m / native * k)), max(2, int(height_m / native * k))
        dt = Affine(width_m / tw, 0, t.c, 0, -height_m / th, t.f)
        out = np.full((3, th, tw), 255, np.uint8)
        for b in range(1, min(3, src.count) + 1):
            reproject(rasterio.band(src, b), out[b - 1], src_transform=src.transform, src_crs=src.crs,
                      dst_transform=dt, dst_crs=rasterio.crs.CRS.from_wkt(dem.crs_wkt), resampling=Resampling.average)
    return Image.fromarray(np.moveaxis(out, 0, -1))


def write_stl(path: str, verts, faces, name="model") -> str:
    v = verts.astype(np.float32)
    tri = v[faces]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.where(ln > 0, n / np.maximum(ln, 1e-12), 0).astype(np.float32)
    rec = np.zeros(len(faces), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    rec["n"], rec["v"] = n, tri
    with open(path, "wb") as f:
        f.write(f"{APP_NAME} {name}".encode("ascii", "replace")[:80].ljust(80, b" "))
        f.write(struct.pack("<I", len(faces)))
        f.write(rec.tobytes())
    return path


def write_dae(path: str, verts, faces, uv=None, texture_file: str | None = None, name="model") -> str:
    C = "http://www.collada.org/2005/11/COLLADASchema"
    root = ET.Element("COLLADA", {"xmlns": C, "version": "1.4.1"})
    asset = ET.SubElement(root, "asset")
    ET.SubElement(ET.SubElement(asset, "contributor"), "authoring_tool").text = f"{APP_NAME} {__version__}"
    ET.SubElement(asset, "unit", {"name": "meter", "meter": "1"})
    ET.SubElement(asset, "up_axis").text = "Z_UP"
    textured = texture_file is not None and uv is not None
    if textured:
        ET.SubElement(ET.SubElement(ET.SubElement(root, "library_images"), "image", {"id": "photo"}),
                      "init_from").text = os.path.basename(texture_file)
    fx = ET.SubElement(ET.SubElement(root, "library_effects"), "effect", {"id": "fx"})
    prof = ET.SubElement(fx, "profile_COMMON")
    if textured:
        np_ = ET.SubElement(prof, "newparam", {"sid": "surf"})
        ET.SubElement(ET.SubElement(np_, "surface", {"type": "2D"}), "init_from").text = "photo"
        np2 = ET.SubElement(prof, "newparam", {"sid": "samp"})
        ET.SubElement(ET.SubElement(np2, "sampler2D"), "source").text = "surf"
    lam = ET.SubElement(ET.SubElement(ET.SubElement(prof, "technique", {"sid": "common"}), "lambert"), "diffuse")
    if textured:
        ET.SubElement(lam, "texture", {"texture": "samp", "texcoord": "UV0"})
    else:
        ET.SubElement(lam, "color").text = "0.72 0.66 0.55 1"
    mat = ET.SubElement(ET.SubElement(root, "library_materials"), "material", {"id": "mat", "name": "ground"})
    ET.SubElement(mat, "instance_effect", {"url": "#fx"})
    geo = ET.SubElement(ET.SubElement(root, "library_geometries"), "geometry", {"id": "geo", "name": name})
    mesh = ET.SubElement(geo, "mesh")

    def source(sid, arr, params):
        s = ET.SubElement(mesh, "source", {"id": sid})
        flat = np.asarray(arr, np.float64).ravel()
        ET.SubElement(s, "float_array", {"id": sid + "-a", "count": str(flat.size)}).text = \
            " ".join(f"{v:.4f}" if abs(v) >= 1e-4 else "0" for v in flat)
        acc = ET.SubElement(ET.SubElement(s, "technique_common"), "accessor",
                            {"source": f"#{sid}-a", "count": str(len(arr)), "stride": str(len(params))})
        for p in params:
            ET.SubElement(acc, "param", {"name": p, "type": "float"})
    source("pos", verts, "XYZ")
    if textured:
        source("uv", uv, "ST")
    vtx = ET.SubElement(mesh, "vertices", {"id": "vtx"})
    ET.SubElement(vtx, "input", {"semantic": "POSITION", "source": "#pos"})
    tr = ET.SubElement(mesh, "triangles", {"material": "m", "count": str(len(faces))})
    ET.SubElement(tr, "input", {"semantic": "VERTEX", "source": "#vtx", "offset": "0"})
    if textured:
        ET.SubElement(tr, "input", {"semantic": "TEXCOORD", "source": "#uv", "offset": "1", "set": "0"})
        idx = np.repeat(faces.ravel(), 2)
    else:
        idx = faces.ravel()
    ET.SubElement(tr, "p").text = " ".join(map(str, idx.tolist()))
    vs = ET.SubElement(ET.SubElement(root, "library_visual_scenes"), "visual_scene", {"id": "scene"})
    node = ET.SubElement(vs, "node", {"id": "site", "name": name})
    ig = ET.SubElement(node, "instance_geometry", {"url": "#geo"})
    im = ET.SubElement(ET.SubElement(ET.SubElement(ig, "bind_material"), "technique_common"), "instance_material",
                       {"symbol": "m", "target": "#mat"})
    if textured:
        ET.SubElement(im, "bind_vertex_input", {"semantic": "UV0", "input_semantic": "TEXCOORD", "input_set": "0"})
    ET.SubElement(ET.SubElement(root, "scene"), "instance_visual_scene", {"url": "#scene"})
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def export_model(files: dict, kind: str, out_dir: str, name: str, bounds=None, poly=None, detail="normal",
                 formats=("dae",), progress=None) -> list[str]:
    """files: the job's outputs (dsm, dtm, ortho). kind: photo | ground."""
    import rasterio
    progress = progress or (lambda p, s: None)
    dem_path = files.get("dsm" if kind == "photo" else "dtm") or files.get("dtm") or files.get("dsm")
    if not dem_path:
        raise ValueError("This job has no elevation model.")
    if bounds is None:
        with rasterio.open(dem_path) as ds:
            b = ds.bounds
            bounds = (b.left, b.bottom, b.right, b.top)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, name.replace(" ", "_") + ("_photo_model" if kind == "photo" else "_ground_model"))
    progress(10, "Building the 3-D surface")
    verts, faces, uv, origin, dem = grid_mesh(dem_path, bounds, DETAIL.get(detail, DETAIL["normal"]), poly)
    files_out = []
    tex = None
    if kind == "photo" and "dae" in formats and files.get("ortho"):
        progress(40, "Draping the drone photo")
        tex = base + "_texture.jpg"
        texture(files["ortho"], dem).save(tex, quality=88)
        files_out.append(tex)
    if "dae" in formats:
        progress(65, "Writing Collada (.dae)")
        files_out.append(write_dae(base + ".dae", verts, faces, uv, tex, name))
    if "stl" in formats:
        progress(85, "Writing STL")
        files_out.append(write_stl(base + ".stl", verts, faces, name))
    readme = base + "_README.txt"
    with rasterio.open(dem_path) as ds:
        crs_name = ds.crs.to_string()
    w = abs(dem.transform.a)
    with open(readme, "w", encoding="utf-8") as f:
        f.write(f"{APP_NAME} {__version__} - 3-D model: {name}\n\n")
        f.write(("Photo model: surface model (DSM) with the drone photo.\n" if kind == "photo" else
                 "Clean ground model: bare-ground model (DTM), no trees or buildings.\n"))
        f.write(f"Triangles: {len(faces):,}   Grid spacing: {w:.2f} m   Units: metres, Z up\n\n")
        f.write("The model was moved near the origin because SketchUp handles large coordinates badly.\n")
        f.write("Model point (0, 0, 0) is at these real coordinates:\n")
        f.write(f"   Easting  {origin[0]:.3f} m\n   Northing {origin[1]:.3f} m\n   Height   {origin[2]:.3f} m\n")
        f.write(f"   Coordinate system: {crs_name}\n\n")
        f.write("SketchUp: File > Import, choose the .dae file (keep the texture .jpg next to it).\n"
                "Heights are as in the drone job (see its README for the height datum).\n")
    files_out.append(readme)
    progress(100, "Done")
    return files_out
