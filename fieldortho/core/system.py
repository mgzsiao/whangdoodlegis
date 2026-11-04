"""Small, dependency-free helpers for system information."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys

IS_WINDOWS = sys.platform.startswith("win")


def total_ram_gb() -> float:
    """Total physical memory in GB (0 if unknown)."""
    try:
        if IS_WINDOWS:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / 1024**3
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / 1024**2
        if sys.platform == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
            return int(out.strip()) / 1024**3
    except Exception:
        pass
    return 0.0


def cpu_count() -> int:
    return os.cpu_count() or 2


def free_disk_gb(path: str) -> float:
    try:
        p = path
        while p and not os.path.exists(p):
            p = os.path.dirname(p)
        return shutil.disk_usage(p or ".").free / 1024**3
    except Exception:
        return 0.0


def open_path(path: str) -> None:
    """Open a file or folder with the default OS application."""
    if IS_WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def kill_tree(pid: int) -> None:
    """Terminate a process and all of its children."""
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, creationflags=0x08000000)
        else:
            import signal
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        pass


def find_qgis() -> str | None:
    """Locate a QGIS executable so results can be opened with one click."""
    exe = shutil.which("qgis") or shutil.which("qgis-bin")
    if exe:
        return exe
    if IS_WINDOWS:
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\OSGeo4W"]
        candidates = []
        for root in roots:
            if not os.path.isdir(root):
                continue
            for name in os.listdir(root):
                if name.lower().startswith("qgis") or name.lower() == "osgeo4w":
                    for exe_name in ("qgis-ltr-bin.exe", "qgis-bin.exe"):
                        p = os.path.join(root, name, "bin", exe_name)
                        if os.path.exists(p):
                            candidates.append(p)
        if candidates:
            return sorted(candidates)[-1]  # newest version folder
    if sys.platform == "darwin" and os.path.exists("/Applications/QGIS.app"):
        return "/Applications/QGIS.app/Contents/MacOS/QGIS"
    return None


def keep_awake(on: bool) -> None:
    """Stop Windows from sleeping while a job runs (laptops in the field)."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def describe_machine() -> str:
    return f"{platform.system()} {platform.release()}, {cpu_count()} CPU threads, {total_ram_gb():.0f} GB RAM"
