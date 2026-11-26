"""Finds the GPU worker (patienttrace.stage1_worker) that runs the ANGEL patient model.

Three modes, picked by `patienttrace serve`:
  url    ANGEL_WORKER_URL points at a worker you started yourself (`patienttrace worker`),
         on this machine or another one.
  local  `patienttrace serve --start-worker`: the app starts the worker as a child process on
         this machine and stops it on exit.
  none   no worker: browse/replay mode. Live interview and compare are switched off.

The interface (ensure_worker / status / touch / start_watchdog / shutdown) is the one the
original Slurm-based manager exposed, so demo_app and ext_routes are unchanged.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

import requests

READY_TIMEOUT = int(os.environ.get("DEMO_GPU_READY_SECONDS", str(15 * 60)))  # model load can take minutes
HEALTH_TIMEOUT = float(os.environ.get("DEMO_GPU_HEALTH_TIMEOUT", "4"))

_lock = threading.Lock()
_last_active = time.time()
_proc: Optional[subprocess.Popen] = None


class WorkerUnavailable(RuntimeError):
    pass


def worker_url() -> str:
    return os.environ.get("ANGEL_WORKER_URL", "").strip().rstrip("/")


def mode() -> str:
    if _proc is not None:
        return "local"
    return "url" if worker_url() else "none"


def live_enabled() -> bool:
    return mode() != "none"


def _die_with_parent() -> None:
    """Linux: SIGTERM the worker when the app process ends, however it ends (Ctrl+C, kill, crash),
    so the patient model never keeps holding the GPU on its own."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
    except Exception:  # noqa: BLE001  not Linux: shutdown() still stops it on a clean exit
        pass


def start_local_worker(port: int, gpu_mem: Optional[float] = None, log_path: Optional[str] = None) -> str:
    """Start `python -m patienttrace.stage1_worker` on this machine; returns its URL."""
    global _proc
    env = dict(os.environ, WORKER_PRELOAD="chat")
    if gpu_mem is not None:
        env["VLLM_GPU_MEM"] = str(gpu_mem)
    out = open(log_path, "a") if log_path else None
    _proc = subprocess.Popen([sys.executable, "-m", "patienttrace.stage1_worker", "--host", "127.0.0.1",
                              "--port", str(port)], env=env, stdout=out, stderr=subprocess.STDOUT if out else None,
                             preexec_fn=_die_with_parent if sys.platform.startswith("linux") else None,
                             start_new_session=True)   # own process group: shutdown() stops vLLM's children too
    url = f"http://127.0.0.1:{port}"
    os.environ["ANGEL_WORKER_URL"] = url
    proc = _proc

    def _watch() -> None:            # say so at once if the worker dies (e.g. model load failed)
        code = proc.wait()
        if _proc is proc:            # not a shutdown we asked for
            print(f"\n[patienttrace] The GPU worker exited (code {code}); live mode is unavailable. "
                  f"See {log_path or 'the output above'}.", flush=True)
    threading.Thread(target=_watch, daemon=True).start()
    return url


def touch() -> None:
    global _last_active
    _last_active = time.time()


def _healthy(url: str) -> bool:
    try:
        r = requests.get(url + "/health", timeout=HEALTH_TIMEOUT)
        return r.ok and bool(r.json().get("ready", True))
    except Exception:  # noqa: BLE001
        return False


def status() -> Dict[str, Any]:
    m = mode()
    url = worker_url()
    ready = bool(url) and _healthy(url)
    if m == "local" and _proc is not None and _proc.poll() is not None:
        state = "FAILED"
    elif m == "none":
        state = "none"
    else:
        state = "RUNNING" if ready else "PENDING"
    return {"allocated": m != "none", "ready": ready, "jobid": None, "host": url or None,
            "state": state, "mode": m, "idle_seconds": int(time.time() - _last_active)}


def ensure_worker(timeout: int = READY_TIMEOUT) -> str:
    """Return the worker URL once it answers /health. Blocks while the model loads."""
    touch()
    if mode() == "none":
        raise WorkerUnavailable("Live mode is off: no GPU worker is configured. Start one with "
                                "`patienttrace worker` and set ANGEL_WORKER_URL, or run "
                                "`patienttrace serve --start-worker` on a GPU machine.")
    url = worker_url()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _healthy(url):
            return url
        if _proc is not None and _proc.poll() is not None:
            raise WorkerUnavailable(f"The local GPU worker exited (code {_proc.returncode}); see its log.")
        time.sleep(5)
    raise WorkerUnavailable(f"GPU worker at {url} did not become ready in {timeout} s.")


def start_watchdog() -> None:
    """No idle cancellation is needed outside Slurm; kept for interface compatibility."""


def shutdown() -> None:
    global _proc
    proc, _proc = _proc, None
    if proc is None:
        return
    import signal
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        pgid = None
    if proc.poll() is None:
        proc.terminate()                         # the worker stops its vLLM engine processes itself
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    if pgid is not None:                         # anything of the worker's group still alive
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pgid, sig)
            except OSError:
                break
            time.sleep(3)
