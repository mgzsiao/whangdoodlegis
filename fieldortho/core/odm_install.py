"""Download and install the OpenDroneMap engine from inside the app (needs internet once).

Used when WhangDoodleGIS was installed with the small 'online' installer on a computer that
was offline at the time, or when the engine was removed. The official ODM Windows setup
is run silently into C:\\ODM (it does not need administrator rights).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import urllib.request
from typing import Callable

from .system import IS_WINDOWS

API = "https://api.github.com/repos/OpenDroneMap/ODM/releases/latest"
DEFAULT_DIR = r"C:\ODM"
UA = {"User-Agent": "WhangDoodleGIS-installer"}


def latest_release(timeout: float = 20) -> dict:
    req = urllib.request.Request(API, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rel = json.load(r)
    for a in rel.get("assets", []):
        n = a.get("name", "")
        if n.startswith("ODM_Setup_") and n.endswith(".exe"):
            return {"version": rel.get("tag_name", ""), "name": n, "url": a["browser_download_url"],
                    "size": int(a.get("size", 0))}
    raise RuntimeError("The latest OpenDroneMap release has no Windows installer.")


def download(url: str, dest: str, progress: Callable[[float, str], None] | None = None,
             cancelled: Callable[[], bool] | None = None) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r, open(dest + ".part", "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            if cancelled and cancelled():
                raise RuntimeError("Download cancelled.")
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if progress:
                pct = 100.0 * got / total if total else 0.0
                progress(pct, f"Downloading engine: {got / 2**20:,.0f} of {total / 2**20:,.0f} MB")
    os.replace(dest + ".part", dest)
    return dest


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def install(setup_exe: str, target_dir: str = DEFAULT_DIR) -> int:
    if not IS_WINDOWS:
        raise RuntimeError("Automatic engine installation is available on Windows only.")
    args = [setup_exe, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", f"/DIR={target_dir}",
            "/TASKS="]
    return subprocess.run(args).returncode


def download_and_install(progress: Callable[[float, str], None] | None = None,
                         cancelled: Callable[[], bool] | None = None, target_dir: str = DEFAULT_DIR) -> str:
    progress = progress or (lambda p, t: None)
    progress(0, "Looking up the latest OpenDroneMap release\u2026")
    rel = latest_release()
    tmp = tempfile.mkdtemp(prefix="fieldortho_odm_")
    exe = os.path.join(tmp, rel["name"])
    download(rel["url"], exe, lambda p, t: progress(p * 0.9, t), cancelled)
    progress(92, f"Installing OpenDroneMap {rel['version']} (a few minutes)\u2026")
    code = install(exe, target_dir)
    try:
        os.remove(exe)
    except OSError:
        pass
    if code != 0 or not os.path.exists(os.path.join(target_dir, "run.bat")):
        raise RuntimeError(f"The engine installer finished with code {code}.")
    progress(100, "Engine installed")
    return target_dir
