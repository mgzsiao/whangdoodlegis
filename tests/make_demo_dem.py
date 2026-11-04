"""Synthetic demo terrain (DTM) for testing the flood and landslide models.

NOT real elevation data. Placed at Salay, Misamis Oriental (WGS 84 / UTM 51N) so
coordinates look familiar: a coastal plain in the west, a meandering river with a
flat floodplain, and steep ridges inland (up to ~180 m).
"""
import sys

import numpy as np
import rasterio
from rasterio.transform import from_origin


def make(path: str, cell: float = 2.0, w: int = 1400, h: int = 1100, seed: int = 7):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w] * cell
    # fractal noise (spectral synthesis)
    f = np.fft.fftfreq(w)[None, :] ** 2 + np.fft.fftfreq(h)[:, None] ** 2
    f[0, 0] = 1
    spec = (rng.normal(size=(h, w)) + 1j * rng.normal(size=(h, w))) / f ** 1.1
    noise = np.real(np.fft.ifft2(spec))
    noise = (noise - noise.mean()) / noise.std()
    inland = np.clip((xx - 250) / 2000.0, 0, 1)          # 0 at coast -> 1 inland
    z = 2.0 + 160 * inland ** 1.3 + 18 * noise * (0.2 + inland)
    # river: meandering channel from east to west
    yc = h * cell * 0.55 + 140 * np.sin(xx / 330.0) + 60 * np.sin(xx / 97.0)
    d = np.abs(yy - yc)
    bed = 0.5 + 0.012 * xx                                # gentle gradient to the sea
    valley = bed + 1.2 + np.clip(d - 90, 0, None) * 0.8  # floodplain ~180 m wide, then steep sides
    z = np.minimum(z, valley)
    z = np.where(d < 12, np.minimum(z, bed + (d / 12) ** 2 * 1.2), z)   # channel 24 m wide
    z = np.where(xx < 120, -2.0 + xx / 60.0, z)          # beach / sea edge
    z = np.maximum(z, -3)
    tr = from_origin(695600, 982600, cell, cell)
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1, dtype="float32",
                       crs="EPSG:32651", transform=tr, nodata=-9999, compress="DEFLATE") as ds:
        ds.write(z.astype("float32"), 1)
    return path


if __name__ == "__main__":
    print(make(sys.argv[1] if len(sys.argv) > 1 else "demo_dtm.tif"))
