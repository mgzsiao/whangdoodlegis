"""Running OpenDroneMap (ODM) as an external, fully offline processing engine.

FieldOrtho never talks to the internet: ODM is installed locally (Windows
native build in C:\\ODM by default) and launched as a child process. Progress
comes from ODM's UDP progress broadcast (port 6367, the same mechanism NodeODM
uses) with a fallback to parsing "Running <stage> stage" log lines.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import presets
from .system import IS_WINDOWS, kill_tree

PROGRESS_PORT = 6367
ANSI = re.compile(r"\x1b\[[0-9;]*m")
STAGE_RE = re.compile(r"Running (\w+) stage")
FINISHED_RE = re.compile(r"Finished (\w+) stage")


# ----------------------------------------------------------------------------
# Locating ODM
# ----------------------------------------------------------------------------

@dataclass
class EngineInfo:
    ok: bool
    mode: str          # native | docker
    path: str = ""
    version: str = ""
    message: str = ""


def _candidate_dirs(configured: str) -> list[str]:
    c = []
    if configured:
        c.append(configured)
    if os.environ.get("ODM_HOME"):
        c.append(os.environ["ODM_HOME"])
    here = os.path.dirname(os.path.abspath(__file__))
    # bundled next to the app: <app>/engine/ODM
    for up in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here)),
               os.path.dirname(os.path.dirname(os.path.dirname(here)))):
        c.append(os.path.join(up, "engine", "ODM"))
    if IS_WINDOWS:
        c += [r"C:\ODM", os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "ODM"),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "ODM")]
    else:
        c += [os.path.expanduser("~/ODM"), "/code", "/opt/ODM"]
    return c


def _launcher(d: str) -> str | None:
    for name in (("run.bat",) if IS_WINDOWS else ("run.sh",)) + ("run.py",):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def detect(configured_path: str = "", mode: str = "native", docker_image: str = "") -> EngineInfo:
    if mode == "docker":
        exe = shutil.which("docker")
        if not exe:
            return EngineInfo(False, "docker", message="Docker is not installed.")
        try:
            r = subprocess.run([exe, "image", "inspect", docker_image], capture_output=True, timeout=20,
                               creationflags=_NO_WINDOW)
            if r.returncode != 0:
                return EngineInfo(False, "docker", exe,
                                  message=f"Docker image {docker_image} is not available offline. "
                                          f"Run 'docker pull {docker_image}' once while online.")
        except Exception as e:
            return EngineInfo(False, "docker", exe, message=f"Docker is not running ({e}).")
        return EngineInfo(True, "docker", exe, docker_image, "Docker engine ready")

    for d in _candidate_dirs(configured_path):
        if d and os.path.isdir(d) and _launcher(d):
            d = os.path.abspath(d)
            version = ""
            try:
                with open(os.path.join(d, "VERSION")) as f:
                    version = f.read().strip().splitlines()[0]
            except Exception:
                pass
            return EngineInfo(True, "native", d, version,
                              f"OpenDroneMap {version} ready" if version else "OpenDroneMap ready")
    return EngineInfo(False, "native",
                      message="The processing engine (OpenDroneMap) was not found. "
                              "Install it, or set its folder in Settings.")


_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def build_command(info: EngineInfo, project_dir: str, args: list[str]):
    """Command to launch ODM. On Windows with run.bat this is a single command-line string:
    cmd /s /c ""C:\\ODM\\run.bat" "C:\\path with spaces\\job" --args"  (the /s form keeps the
    inner quotes intact, so job folders such as 'Documents\\FieldOrtho Jobs' work)."""
    project_dir = os.path.abspath(project_dir)
    if info.mode == "docker":
        parent, name = os.path.split(project_dir)
        return [info.path, "run", "--rm", "-v", f"{parent}:/datasets", info.version,
                "--project-path", "/datasets", name, *[_docker_path(a, parent) for a in args]]
    launcher = _launcher(info.path)
    if launcher.endswith(".bat"):
        inner = subprocess.list2cmdline([launcher, project_dir, *args])
        return f'cmd.exe /s /c "{inner}"'
    if launcher.endswith(".py"):
        import sys
        py = sys.executable if not getattr(sys, "frozen", False) else "python"
        return [py, launcher, project_dir, *args]
    return ["bash", launcher, project_dir, *args]


def _docker_path(arg: str, parent: str) -> str:
    if os.path.isabs(arg) and arg.startswith(parent):
        return "/datasets/" + os.path.relpath(arg, parent).replace("\\", "/")
    return arg


# ----------------------------------------------------------------------------
# Friendly error messages
# ----------------------------------------------------------------------------

ERROR_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"MemoryError|bad_alloc|out of memory|Cannot allocate memory|exit code 137|0xC0000017",
                re.I),
     "The computer ran out of memory. Close other programs and try again, or choose "
     "'Quick preview' / 'Standard' instead of 'High detail'."),
    (re.compile(r"No space left on device|not enough space|disk full", re.I),
     "The disk is full. Free some space (each job needs several GB) or move the jobs "
     "folder to a bigger drive in Settings."),
    (re.compile(r"Not enough supported images|Reconstruction could not be generated|"
                r"could not process this dataset|Not enough images|No reconstructions", re.I),
     "The photos could not be matched together. This usually means too little overlap, "
     "blurry photos, or surfaces with no texture (water, bare sand, uniform crops). "
     "Re-fly with more overlap, or remove photos of water."),
    (re.compile(r"GCP.*(invalid|not found|cannot)|gcp_list", re.I),
     "There is a problem with the ground control points file. Check the GCP tagging "
     "or remove the GCPs and try again."),
    (re.compile(r"Permission denied|Access is denied", re.I),
     "Windows blocked access to a file. Make sure the job folder is not open in another "
     "program and is not inside a protected folder."),
]


def explain_error(log_tail: str, exit_code: int | None) -> str:
    for rx, msg in ERROR_HINTS:
        if rx.search(log_tail):
            return msg
    return ("Processing stopped with an error (code %s). Open 'Technical details' for the "
            "full log, or try again - processing continues from where it stopped." % exit_code)


# ----------------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------------

@dataclass
class RunResult:
    ok: bool
    cancelled: bool
    exit_code: int | None
    error: str = ""
    last_stage: str = ""
    seconds: float = 0.0


class OdmRunner:
    """Runs ODM in a background thread. Callbacks are invoked from worker threads."""

    def __init__(self, info: EngineInfo, project_dir: str, args: list[str], *,
                 on_line: Callable[[str], None] | None = None,
                 on_progress: Callable[[float, str], None] | None = None,
                 log_path: str | None = None):
        self.info = info
        self.project_dir = os.path.abspath(project_dir)
        self.project_name = os.path.basename(self.project_dir)
        self.args = args
        self.on_line = on_line or (lambda s: None)
        self.on_progress = on_progress or (lambda p, s: None)
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None
        self._cancel = threading.Event()
        self._progress = 0.0
        self._stage = ""
        self._tail: list[str] = []
        self._udp: socket.socket | None = None

    # -- progress ------------------------------------------------------
    def _set_progress(self, value: float | None = None, stage: str | None = None):
        changed = False
        if stage and stage != self._stage:
            self._stage = stage
            changed = True
            value = max(value or 0.0, presets.STAGE_START.get(stage, self._progress))
        if value is not None and value > self._progress:
            self._progress = min(100.0, value)
            changed = True
        if changed:
            self.on_progress(self._progress, presets.STAGE_TEXT.get(self._stage, self._stage))

    def _udp_listen(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", PROGRESS_PORT))
            s.settimeout(0.5)
            self._udp = s
        except OSError:
            return  # port busy: fall back to log parsing only
        while not self._cancel.is_set() and (self.proc is None or self.proc.poll() is None):
            try:
                data, _ = s.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            parts = data.decode("utf8", "ignore").split("/")
            if len(parts) >= 4 and parts[0] == "PGUP":
                name, value = "/".join(parts[2:-1]), parts[-1]
                if name == self.project_name or os.path.basename(name) == self.project_name:
                    try:
                        self._set_progress(float(value))
                    except ValueError:
                        pass
        try:
            s.close()
        except OSError:
            pass

    # -- run ------------------------------------------------------------
    def cancel(self):
        self._cancel.set()
        if self.proc and self.proc.poll() is None:
            kill_tree(self.proc.pid)

    def run(self) -> RunResult:
        """Blocking run. Call from a worker thread."""
        t0 = time.time()
        cmd = build_command(self.info, self.project_dir, self.args)
        env = dict(os.environ, ODM_NONINTERACTIVE="1", PYTHONUNBUFFERED="1")
        log = open(self.log_path, "a", encoding="utf-8") if self.log_path else None
        if log:
            shown = cmd if isinstance(cmd, str) else subprocess.list2cmdline(cmd)
            log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n$ {shown}\n")
        kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                      cwd=self.info.path if self.info.mode == "native" else None, env=env)
        if IS_WINDOWS:
            kwargs["creationflags"] = _NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(cmd, **kwargs)
        except OSError as e:
            if log:
                log.close()
            return RunResult(False, False, None, f"Could not start the processing engine: {e}")

        threading.Thread(target=self._udp_listen, daemon=True).start()
        self._set_progress(0.5, "dataset")
        assert self.proc.stdout is not None
        for raw in iter(self.proc.stdout.readline, b""):
            line = ANSI.sub("", raw.decode("utf-8", errors="replace")).rstrip()
            if not line:
                continue
            if log:
                log.write(line + "\n")
            self._tail.append(line)
            if len(self._tail) > 400:
                self._tail = self._tail[-300:]
            m = STAGE_RE.search(line)
            if m:
                self._set_progress(stage=m.group(1))
            m = FINISHED_RE.search(line)
            if m:
                done = dict((k, p) for k, _t, p in presets.STAGES).get(m.group(1))
                if done:
                    self._set_progress(done)
            self.on_line(line)
        code = self.proc.wait()
        if log:
            log.write(f"===== exit code {code} =====\n")
            log.close()
        cancelled = self._cancel.is_set()
        self._cancel.set()  # stop UDP listener
        ok = code == 0 and not cancelled and self._odm_success()
        if ok:
            self._set_progress(100.0, "odm_postprocess")
        err = "" if ok or cancelled else explain_error("\n".join(self._tail[-150:]), code)
        return RunResult(ok, cancelled, code, err, self._stage, time.time() - t0)

    def _odm_success(self) -> bool:
        """ODM writes log.json with success flag; trust exit code if missing."""
        p = os.path.join(self.project_dir, "log.json")
        try:
            with open(p, encoding="utf-8") as f:
                return bool(json.load(f).get("success", True))
        except Exception:
            return True


def resume_stage(project_dir: str) -> str | None:
    """Pick the ODM stage to restart from after a failure, based on log.json."""
    try:
        with open(os.path.join(project_dir, "log.json"), encoding="utf-8") as f:
            data = json.load(f)
        stages = data.get("stages") or []
        if stages and not data.get("success"):
            return stages[-1].get("name")
    except Exception:
        pass
    return None
