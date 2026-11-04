"""DEM loading and terrain derivatives shared by the flood and landslide models."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject

from . import crs as crsmod


@dataclass
class Dem:
    z: np.ndarray            # float64 metres, NaN = no data
    transform: object        # affine, metres
    crs_wkt: str
    path: str
    factor: float = 1.0      # downsampling factor applied (1 = full resolution)
    vertical_note: str = ""

    @property
    def cell(self) -> float:
        return abs(self.transform.a)

    @property
    def shape(self):
        return self.z.shape

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.z)

    def rc_to_xy(self, r, c):
        x = self.transform.c + (np.asarray(c) + 0.5) * self.transform.a
        y = self.transform.f + (np.asarray(r) + 0.5) * self.transform.e
        return x, y

    def xy_to_rc(self, x, y):
        c = (np.asarray(x) - self.transform.c) / self.transform.a - 0.5
        r = (np.asarray(y) - self.transform.f) / self.transform.e - 0.5
        return np.round(r).astype(int), np.round(c).astype(int)

    def profile(self) -> dict:
        h, w = self.z.shape
        return dict(driver="GTiff", width=w, height=h, count=1, dtype="float32",
                    crs=rasterio.crs.CRS.from_wkt(self.crs_wkt), transform=self.transform,
                    nodata=-9999.0, compress="DEFLATE", tiled=True, blockxsize=256, blockysize=256)


def load_dem(path: str, max_size: int = 1600) -> Dem:
    """Load a DEM in metres. Geographic DEMs are reprojected to UTM (models need metres).
    Large DEMs are averaged down so the interactive models stay fast."""
    with rasterio.open(path) as ds:
        nd = ds.nodata
        crs = ds.crs
        if crs is None:
            raise ValueError("This elevation file has no coordinate system.")
        if crs.is_geographic:
            from pyproj import CRS, Transformer
            b = ds.bounds
            lon, lat = (b.left + b.right) / 2, (b.bottom + b.top) / 2
            dst = rasterio.crs.CRS.from_epsg(crsmod.utm_epsg(lat, lon))
            tr, w, h = calculate_default_transform(crs, dst, ds.width, ds.height, *ds.bounds)
            arr = np.full((h, w), np.nan, dtype="float64")
            reproject(rasterio.band(ds, 1), arr, src_transform=ds.transform, src_crs=crs,
                      dst_transform=tr, dst_crs=dst, src_nodata=nd, dst_nodata=np.nan,
                      resampling=Resampling.bilinear)
            z, transform, crs_wkt = arr, tr, dst.to_wkt()
            factor = 1.0
        else:
            f = max(1.0, max(ds.width, ds.height) / max_size)
            w, h = int(round(ds.width / f)), int(round(ds.height / f))
            z = ds.read(1, out_shape=(h, w), resampling=Resampling.average if f > 1 else Resampling.nearest,
                        masked=True).astype("float64").filled(np.nan)
            transform = ds.transform * ds.transform.scale(ds.width / w, ds.height / h)
            crs_wkt = crs.to_wkt()
            factor = f
    if nd is not None:
        z[z == nd] = np.nan
    z[z < -1000] = np.nan
    if abs(transform.a - abs(transform.e)) > 1e-6 * abs(transform.a):
        pass  # non-square pixels are handled by slope() via separate dx/dy
    return Dem(z, transform, crs_wkt, path, factor)


def slope_aspect(dem: Dem) -> tuple[np.ndarray, np.ndarray]:
    """Slope (degrees) and downslope direction (radians, 0 = east, counter-clockwise, in
    raster row/col space with rows pointing south)."""
    z = dem.z
    zz = np.where(np.isfinite(z), z, np.nanmean(z) if np.isfinite(z).any() else 0.0)
    cy, cx = abs(dem.transform.e), abs(dem.transform.a)
    dzdr, dzdc = np.gradient(zz, cy, cx)
    slope = np.degrees(np.arctan(np.hypot(dzdr, dzdc)))
    # steepest descent direction in (col, row) components
    aspect = np.arctan2(-dzdr, -dzdc)
    slope[~np.isfinite(z)] = np.nan
    return slope, aspect


def hillshade(dem: Dem) -> np.ndarray:
    from .exporters import hillshade as hs
    return hs(dem.z, abs(dem.transform.a), abs(dem.transform.e))


def write_array(dem: Dem, arr: np.ndarray, path: str, dtype="float32", nodata=-9999.0):
    prof = dem.profile()
    prof.update(dtype=dtype, nodata=nodata)
    out = np.where(np.isfinite(arr), arr, nodata) if np.issubdtype(arr.dtype, np.floating) else arr
    with rasterio.open(path, "w", **prof) as ds:
        ds.write(out.astype(dtype), 1)
    return path


def lonlat(dem: Dem) -> tuple[float, float]:
    from pyproj import CRS, Transformer
    h, w = dem.shape
    x, y = dem.rc_to_xy(h / 2, w / 2)
    t = Transformer.from_crs(CRS.from_wkt(dem.crs_wkt), CRS.from_epsg(4326), always_xy=True)
    return t.transform(float(x), float(y))
