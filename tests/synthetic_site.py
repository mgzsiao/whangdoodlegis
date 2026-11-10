"""A synthetic processed drone job for GUI walkthroughs: 100 x 100 m of gently sloping ground (DTM)
with a cone-shaped pile 10 m high and 20 m in radius in the surface model (DSM). NOT real data."""
import os

import numpy as np
import rasterio
from rasterio.transform import from_origin

from fieldortho.core import jobs

X0, Y0, CELL, N = 695600.0, 982600.0, 0.1, 1000        # UTM 51N
CX, CY = X0 + 50, Y0 - 50                               # centre of the pile


def synthetic_job(root):
    job_dir = os.path.join(root, "Synthetic pile site")
    gis = os.path.join(job_dir, "GIS")
    os.makedirs(gis)
    r, c = np.mgrid[0:N, 0:N]
    x, y = X0 + (c + 0.5) * CELL, Y0 - (r + 0.5) * CELL
    ground = 120 + 0.05 * (x - X0) + 0.5 * np.sin((y - Y0) / 15)
    pile = np.clip(10 - np.hypot(x - CX, y - CY) * 0.5, 0, None)
    files = {}
    for key, z in (("dtm", ground), ("dsm", ground + pile)):
        p = os.path.join(gis, f"site_{key.upper()}.tif")
        with rasterio.open(p, "w", driver="GTiff", width=N, height=N, count=1, dtype="float32", crs="EPSG:32651",
                           transform=from_origin(X0, Y0, CELL, CELL), nodata=-9999) as ds:
            ds.write(z.astype("float32"), 1)
        files[key] = p
    job = jobs.Job(name="Synthetic pile site", job_dir=job_dir, status=jobs.STATUS_DONE, summary={"files": files})
    job.save()
    return job_dir
