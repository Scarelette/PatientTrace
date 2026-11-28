"""Live mode helpers: streaming patient replies from the GPU worker, two patients at once, latency log,
saved sessions.

Worker protocol (stage1_worker /chat_stream, NDJSON): {"text": cumulative} ... {"reset": true} on a
regeneration ... {"done": true, "reply", "tokens_in", "tokens_out", "gen_ms", "first_token_ms", "gpu_*"}
or {"error": "..."}. The app re-emits these with timing, retries a failed stream once (only if no text
reached the trainee yet), and gives up after STREAM_TIMEOUT seconds without output.

Latency log: state/logs/live_latency.jsonl, one line per step:
  {ts, page: interview|fc, patient, turn, step: patient_reply|alignment|guidance|analysis|learning, ms,
   ok, error?, tokens_in?, tokens_out?, gen_ms?, first_token_ms?, gpu_util?, gpu_mem_mb?, cached?}
scripts/live_latency_report.py summarises it.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional

import requests

from patienttrace import paths as P

STREAM_TIMEOUT = float(os.environ.get("PATIENTTRACE_STREAM_TIMEOUT", "60"))   # seconds without any output
LOG_LOCK = threading.Lock()


def log_step(**rec: Any) -> None:
    rec = {"ts": round(time.time(), 3), **rec}
    path = P.state("logs", "live_latency.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with LOG_LOCK, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def missing() -> List[str]:
    """What live mode lacks on this server, in words for the page ("Live chat is off: …")."""
    from patienttrace import gpu_manager
    out = []
    if os.environ.get("PATIENTTRACE_WORKER_FILE") and not os.environ.get("ANGEL_WORKER_URL"):
        pass                                              # follows the worker job: availability comes from /api/live-status
    elif gpu_manager.mode() == "none":
        out.append("no GPU worker for the patient model (start the app with --start-worker on a GPU machine, or set ANGEL_WORKER_URL)")
    elif not gpu_manager.status().get("ready", False) and gpu_manager.mode() == "url":
        out.append(f"the GPU worker at {gpu_manager.worker_url()} is not answering")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AZURE_FOUNDRY_API_KEY")):
        out.append("no API key for alignment and guidance (set ANTHROPIC_API_KEY, e.g. in .env)")
    return out


# ---------------- live status: is the patient model up, starting or down? (never waits) ----------------
# Control files (scripts/live_mode.sh writes them): .state/live/autostart ("on"/"off"), .state/live/demo_until (epoch).
_STATUS: Dict[str, Any] = {"t": 0.0, "v": None}
_STATUS_LOCK = threading.Lock()
LAST = {"latency_ms": None, "at": None}
WORKER_JOB = os.environ.get("PATIENTTRACE_WORKER_JOB", "patienttrace-worker")
WORKER_SCRIPT = os.environ.get("PATIENTTRACE_WORKER_SCRIPT", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "live_worker.slurm"))
AUTOSTART_GAP_S = int(os.environ.get("PATIENTTRACE_AUTOSTART_GAP_S", "300"))      # at most one submission per 5 min
AUTOSTART_DAILY = int(os.environ.get("PATIENTTRACE_AUTOSTART_DAILY", "8"))         # and at most 8 a day


def _ctl(name: str) -> str:
    try:
        with open(P.state("live", name), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:  # noqa: BLE001
        return ""


def autostart_on() -> bool:
    v = _ctl("autostart") or os.environ.get("PATIENTTRACE_AUTOSTART", "")
    return v.lower() in ("1", "on", "true", "yes")


def demo_until() -> float:
    try:
        return float(_ctl("demo_until") or 0)
    except ValueError:
        return 0.0


def _ping(url: str, timeout: float = 2.0) -> bool:
    from patienttrace.gpu_manager import http
    try:
        r = http().get(url + "/health", timeout=timeout)
        return r.ok and bool(r.json().get("ready", True))
    except Exception:  # noqa: BLE001
        return False


def worker_jobs() -> List[Dict[str, str]]:
    """This user's patient-model worker jobs: id, state, start, time limit, time left, node ([] without Slurm)."""
    import subprocess
    try:
        out = subprocess.run(["squeue", "-h", "-u", os.environ.get("USER", ""), "-n", WORKER_JOB, "-o", "%i|%T|%S|%l|%L|%N"],
                             capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
    except Exception:  # noqa: BLE001
        return []
    keys = ("job_id", "state", "start", "time_limit", "time_left", "node")
    return [dict(zip(keys, l.split("|"))) for l in out if l.strip()]


def _submissions_today() -> List[Dict[str, Any]]:
    path = P.state("logs", "worker_jobs.jsonl")
    day = time.strftime("%Y-%m-%d")
    try:
        with open(path, encoding="utf-8") as f:
            return [r for r in (json.loads(l) for l in f if l.strip()) if r.get("day") == day]
    except Exception:  # noqa: BLE001
        return []


def site_settings() -> Dict[str, str]:
    """scripts/site.env (gitignored; see scripts/site.env.example) overlaid by the environment."""
    out: Dict[str, str] = {}
    path = os.path.join(os.path.dirname(WORKER_SCRIPT), "site.env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    out.update({k: v for k, v in os.environ.items() if k.startswith("PT_")})
    return out


def sbatch_args() -> List[str]:
    """sbatch options for the worker job from the site settings (empty ones are left out)."""
    st = site_settings()
    args = []
    for key, flag in (("PT_SBATCH_ACCOUNT", "--account"), ("PT_SBATCH_PARTITION", "--partition"), ("PT_SBATCH_QOS", "--qos")):
        if st.get(key):
            args.append(f"{flag}={st[key]}")
    return args


def submit_worker(reason: str) -> Optional[str]:
    """sbatch scripts/live_worker.slurm (from the repo root, with the site's account / partition / QoS); logs
    {day, ts, job_id, reason}. Returns the job id or None."""
    import subprocess
    repo = os.path.dirname(os.path.dirname(WORKER_SCRIPT))
    os.makedirs(os.path.join(repo, ".state", "logs"), exist_ok=True)      # Slurm does not create the --output folder
    try:
        out = subprocess.run(["sbatch", "--parsable", *sbatch_args(), WORKER_SCRIPT], capture_output=True, text=True, timeout=30, cwd=repo)
    except Exception as exc:  # noqa: BLE001
        print(f"[live] sbatch failed: {exc}", flush=True)
        return None
    jid = (out.stdout or "").strip().split(";")[0]
    rec = {"day": time.strftime("%Y-%m-%d"), "ts": round(time.time()), "job_id": jid or None, "reason": reason, "error": (out.stderr or "").strip()[:200] or None}
    os.makedirs(P.state("logs"), exist_ok=True)
    with LOG_LOCK, open(P.state("logs", "worker_jobs.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"[live] submitted patient-model worker job {jid or '?'} ({reason})", flush=True)
    return jid or None


_AUTO_LOCK = threading.Lock()


def maybe_autostart() -> Optional[str]:
    """Start a worker for a visitor who opened a live page while none runs: only if autostart is on, no worker job
    is running or pending, the last submission was over AUTOSTART_GAP_S ago and today's cap is not used up."""
    if not autostart_on():
        return None
    with _AUTO_LOCK:
        if worker_jobs():
            return None
        subs = _submissions_today()
        if len(subs) >= AUTOSTART_DAILY or (subs and time.time() - subs[-1]["ts"] < AUTOSTART_GAP_S):
            return None
        return submit_worker("autostart: a visitor opened a live page")


def live_status(force: bool = False) -> Dict[str, Any]:
    """{patient_model: up|starting|down, phase, gpu_queue, last_latency_ms, detail} for the page (plain words) plus
    an "admin" block for direct requests. A 2 s worker ping and the Slurm queue, cached 15 s; never the model."""
    with _STATUS_LOCK:
        if not force and _STATUS["v"] is not None and time.time() - _STATUS["t"] < 15:
            return _STATUS["v"]
    from patienttrace import gpu_manager
    url = gpu_manager.worker_url()
    up = bool(url) and _ping(url)
    jobs = worker_jobs()
    running = [j for j in jobs if j["state"] in ("RUNNING", "CONFIGURING", "COMPLETING")]
    pending = [j for j in jobs if j["state"] == "PENDING"]
    local_loading = gpu_manager.mode() == "local" and getattr(gpu_manager, "_proc", None) is not None and gpu_manager._proc.poll() is None
    if up:
        state, phase, detail = "up", "ready", "The patient model is ready."
    elif local_loading or running:
        state, phase, detail = "starting", "loading", "The patient model is loading on a GPU (usually about a minute)."
    elif pending:
        state, phase, detail = "starting", "queued", "Waiting for a free GPU to start the patient model (may take a few minutes)."
    else:
        state, phase = "down", "stopped"
        detail = ("The patient model is not running right now; it starts when someone opens a live page."
                  if autostart_on() and len(_submissions_today()) < AUTOSTART_DAILY else "The patient model is not running right now.")
    subs = _submissions_today()
    v = {"patient_model": state, "phase": phase, "gpu_queue": len(pending), "last_latency_ms": LAST["latency_ms"], "detail": detail,
         "api_key": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AZURE_FOUNDRY_API_KEY")), "checked": round(time.time()),
         "admin": {"worker_url": url or None, "jobs": jobs, "start_with": "sbatch scripts/live_worker.slurm",
                   "autostart": {"on": autostart_on(), "today": len(subs), "daily_cap": AUTOSTART_DAILY, "last": subs[-1] if subs else None},
                   "demo_until": demo_until() or None}}
    with _STATUS_LOCK:
        _STATUS.update(t=time.time(), v=v)
    return v


def public_status(v: Dict[str, Any], admin: bool) -> Dict[str, Any]:
    return v if admin else {k: x for k, x in v.items() if k != "admin"}


class Unavailable(RuntimeError):
    pass


def _worker_url() -> str:
    """The worker URL if it answers right now; otherwise fail at once with what is going on (never wait)."""
    from patienttrace import gpu_manager
    url = gpu_manager.worker_url()
    if url and _ping(url, 3.0):
        return url
    st = live_status(force=True)
    if st["patient_model"] == "starting":
        raise Unavailable("the patient model is warming up: " + st["detail"])
    raise Unavailable("patient model unavailable: " + st["detail"])


_WARM = {"t": 0.0}


def warm() -> Dict[str, Any]:
    """Lightweight warm-up when Interview / Formulation Contrast opens in live mode: if the worker is up, load the
    roleplay model / caches in the background (at most once a minute). Returns the live status at once."""
    st = live_status()
    if st["patient_model"] == "down" and maybe_autostart():   # nobody is serving: start a worker (rate-limited)
        st = live_status(force=True)
        if st["patient_model"] == "down":
            st = {**st, "patient_model": "starting", "phase": "queued", "detail": "Starting the patient model (may take a few minutes)."}
    if st["patient_model"] == "up" and time.time() - _WARM["t"] > 60:
        _WARM["t"] = time.time()
        def run():
            try:
                from patienttrace.gpu_manager import http, worker_url
                http().post(worker_url() + "/warm_chat", timeout=120)
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=run, daemon=True).start()
    return st


def worker_stream(system_prompt: str, conversation: List[Dict[str, str]], stop: Optional[threading.Event] = None) -> Iterator[Dict[str, Any]]:
    """Events from one /chat_stream call. Raises on HTTP errors, an {"error"} event or STREAM_TIMEOUT."""
    url = _worker_url()
    from patienttrace.gpu_manager import http as _http
    with _http().post(url + "/chat_stream", json={"system_prompt": system_prompt, "conversation": conversation},
                       stream=True, timeout=(10, STREAM_TIMEOUT)) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if stop is not None and stop.is_set():
                return                                   # closing the response aborts the GPU request
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("error"):
                raise RuntimeError(ev["error"])
            yield ev
            if ev.get("done"):
                return
    raise RuntimeError("the patient model stream ended without a reply")


def stream_reply(system_prompt: str, conversation: List[Dict[str, str]], stop: Optional[threading.Event] = None,
                 retries: int = 1) -> Iterator[Dict[str, Any]]:
    """worker_stream with one automatic retry when nothing has been shown yet; adds app-side timing.
    Yields {"text"} / {"reset"} / {"retry", "error"} / final {"done", "reply", ..., "ms"}."""
    t0 = time.time()
    for attempt in range(retries + 1):
        shown = False
        try:
            for ev in worker_stream(system_prompt, conversation, stop):
                if ev.get("text"):
                    shown = True
                if ev.get("done"):
                    if not (ev.get("reply") or "").strip():
                        raise RuntimeError("the patient model returned an empty reply")
                    ev = {**ev, "ms": round((time.time() - t0) * 1000), "retried": attempt}
                    LAST.update(latency_ms=ev["ms"], at=round(time.time()))
                yield ev
                if ev.get("done"):
                    return
            if stop is not None and stop.is_set():
                yield {"stopped": True}
                return
        except Exception as exc:  # noqa: BLE001
            msg = ("the patient model did not answer within %d s" % STREAM_TIMEOUT if isinstance(exc, requests.exceptions.Timeout)
                   else str(exc) if isinstance(exc, RuntimeError) else f"{type(exc).__name__}: {exc}")
            if isinstance(exc, Unavailable):              # down or warming up: say so at once, no retry
                yield {"error": msg, "unavailable": True, "ms": round((time.time() - t0) * 1000)}
                return
            if attempt < retries and not shown and not (stop is not None and stop.is_set()):
                yield {"retry": attempt + 1, "error": msg}
                continue
            yield {"error": msg, "ms": round((time.time() - t0) * 1000)}
            return


def ndjson(events: Iterator[Dict[str, Any]]) -> Iterator[str]:
    for ev in events:
        yield json.dumps(ev, ensure_ascii=False) + "\n"


# ---------------- Formulation Contrast: two patients, one question ----------------
FC: Dict[str, Dict[str, Any]] = {}
FC_LOCK = threading.Lock()


def fc_start(pa: str, pb: str) -> Dict[str, Any]:
    from patienttrace.angel_ext.patient_sim import build_system_prompt
    from patienttrace.angel_ext import data as D
    sides = {}
    for s, pid in (("a", pa), ("b", pb)):
        sp = build_system_prompt(pid)
        ident = (D.short_profiles()[D.idx_of(pid)].get("identity") or {})
        sides[s] = {"pid": pid, "system_prompt": sp["system_prompt"], "name": sp["patient_name"] or pid,
                    "gender": ident.get("gender") or "", "conversation": []}
    fid = uuid.uuid4().hex[:12]
    with FC_LOCK:
        FC[fid] = {"sides": sides, "turns": 0, "created": time.time()}
    def warm_annotations() -> None:                       # one-time node annotations (first /fc/turn for a new pair)
        from patienttrace import contrast
        for pid in (pa, pb):
            try:
                contrast.annotations(pid)
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=warm_annotations, daemon=True).start()
    threading.Thread(target=warm, daemon=True).start()    # load the model while the trainee types (never waits)
    return {"fid": fid, "names": {s: v["name"] for s, v in sides.items()}, "genders": {s: v["gender"] for s, v in sides.items()}}


def fc_ask(fid: str, question: str, stop: threading.Event, given: Optional[Dict[str, str]] = None) -> Iterator[Dict[str, Any]]:
    """Both patients answer in parallel (one worker stream each; the GPU decodes them in one batch).
    Events carry "side". Each conversation advances only when its reply finished."""
    with FC_LOCK:
        sess = FC.get(fid)
    if sess is None:
        yield {"error": "unknown or expired live session; start the round again"}
        return
    turn = sess["turns"] + 1
    q: "queue.Queue" = queue.Queue()

    def run(s: str) -> None:
        side = sess["sides"][s]
        if (given or {}).get(s):                        # answer already known (the trainee's own Interview): no model call
            q.put({"side": s, "text": given[s]}); q.put({"side": s, "done": True, "reply": given[s], "given": True, "ms": 0})
            q.put({"side": s, "_end": True})
            return
        convo = side["conversation"] + [{"role": "therapist", "content": question}]
        try:
            for ev in stream_reply(side["system_prompt"], convo, stop):
                q.put({**ev, "side": s})
        finally:
            q.put({"side": s, "_end": True})

    for s in "ab":
        threading.Thread(target=run, args=(s,), daemon=True).start()
    ended, final = set(), {}
    while len(ended) < 2:
        ev = q.get()
        if ev.get("_end"):
            ended.add(ev["side"])
            continue
        if ev.get("done") and ev.get("given"):
            final[ev["side"]] = ev
        elif ev.get("done"):
            final[ev["side"]] = ev
            side = sess["sides"][ev["side"]]
            log_step(page="fc", patient=side["pid"], turn=turn, step="patient_reply", ok=True,
                     **{k: ev.get(k) for k in ("ms", "gen_ms", "first_token_ms", "tokens_in", "tokens_out", "attempts", "retried", "gpu_util", "gpu_mem_mb", "gpu_name", "mock")})
        elif ev.get("error") and not ev.get("retry"):
            log_step(page="fc", patient=sess["sides"][ev["side"]]["pid"], turn=turn, step="patient_reply", ok=False, error=ev["error"], ms=ev.get("ms"))
        yield ev
    if len(final) == 2:                                   # both answered: advance both conversations together
        with FC_LOCK:
            for s in "ab":
                sess["sides"][s]["conversation"] += [{"role": "therapist", "content": question}, {"role": "patient", "content": final[s]["reply"]}]
            sess["turns"] = turn
        yield {"both_done": True, "turn": turn}


# ---------------- saved live sessions (same format as data/interview/pNNN.json) ----------------
def save_interview(pid: str, name: str, sid: str, turns: List[Dict[str, Any]]) -> str:
    """Write a live Interview session so it can be replayed like the recorded examples
    (state/sessions/interview/<pid>_<sid>.json; copy it to data/interview/<pid>.json to make it the default)."""
    import datetime
    from patienttrace.interview import ALIGN_VERSION
    keep = ("who", "text", "active", "kinds", "confidence", "spans", "themes", "path", "trigger", "trigger_link", "eased", "guidance", "alignment")
    rec = {"patient_id": pid, "index": int(pid[1:]), "name": name, "kind": "live interview",
           "recorded": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M"), "source": "live session: trainee questions typed in the app; "
           "patient replies streamed from the patient model; alignment and explanations AI-generated (patienttrace/interview.py)",
           "turns": [{k: t[k] for k in keep if k in t} for t in turns], "aligner": ALIGN_VERSION, "session_id": sid}
    path = P.state("sessions", "interview", f"{pid}_{sid}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def save_fc(fid: str, rec: Dict[str, Any]) -> str:
    """A live Formulation Contrast round in the recorded-example format (state/sessions/fc/<case>.json)."""
    path = P.state("sessions", "fc", f"fc.live-{fid}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    return path
