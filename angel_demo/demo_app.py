"""Patient_demo web app.

Flow:
  1. Create a new patient profile (form + per-field writing assist + quality
     review) OR pick an existing profile from data/patients_adult.jsonl.
  2. Generate the long-version profile (stage-1) and TWO network models
     (short-profile graph + long-profile graph). The short graph is returned as
     soon as it's ready so the UI can show it first; the long graph follows.

This app runs on the LOGIN node (has internet for the Claude/GPT calls). Stage-1
runs on a GPU node that is allocated ON DEMAND via SLURM (gpu_manager): the first
time a profile is generated a 2h GPU job is submitted (reused afterwards); it is
auto-cancelled after 20 min idle. The short-profile graph (API) runs concurrently
with stage-1; the long-profile graph runs after.

Run (LOGIN node; env with anthropic+fastapi+requests; ANTHROPIC_API_KEY set):
    python demo_app.py --host 0.0.0.0 --port 7871
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import threading
import time
import traceback
import sys
import uuid
from typing import Any, Dict, List, Optional

import anthropic
import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from angel_demo import paths as P
from angel_demo import audio_service
from angel_demo import gpu_manager
from angel_demo.assist_review import assist_field, review_profile
from angel_demo.demo_config import PROFILE_FIELDS, SPEAKING_STYLE_DEFAULTS, empty_profile
from angel_demo.network_model import DEFAULT_MODEL, generate_network
from angel_demo.profile_text import profile_to_short_text

if __name__ == "__main__":   # one module instance even when extension code does `import demo_app`
    sys.modules.setdefault("demo_app", sys.modules[__name__])

# Chat sessions (angel stage-2 roleplay): session_id -> {system_prompt, conversation}.
CHAT: Dict[str, Dict[str, Any]] = {}
CHAT_LOCK = threading.Lock()

WORKER_TIMEOUT = float(os.environ.get("DEMO_WORKER_HTTP_TIMEOUT", "180"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = P.STATIC_DIR
EXISTING_PATH = os.environ.get("DEMO_EXISTING_PROFILES", P.data("patients_adult.jsonl"))
# Model routing (quality vs time) for building networks of new profiles:
#   * SHORT graph  -> gpt-4o for every phase (fast; it overlaps stage-1 anyway).
#   * LONG graph   -> claude-opus-4-5 for EVERY phase (highest quality).
# Every choice is env-overridable.
SHORT_MODEL = os.environ.get("DEMO_SHORT_MODEL", "gpt-4o")
LONG_NODE_MODEL = os.environ.get("DEMO_LONG_NODE_MODEL", "claude-opus-4-5")
LONG_EDGE_MODEL = os.environ.get("DEMO_LONG_EDGE_MODEL", "claude-opus-4-5")
LONG_VERIFY_MODEL = os.environ.get("DEMO_LONG_VERIFY_MODEL", "claude-opus-4-5")
LONG_DIRECTION_MODEL = os.environ.get("DEMO_LONG_DIRECTION_MODEL", "claude-opus-4-5")

app = FastAPI(title="Patient_demo")


@app.middleware("http")
async def _no_cache_static(request, call_next):
    """Never cache the frontend, so edits show up on a normal reload (no more
    stale app.js / styles.css)."""
    resp = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


# ---------------------------------------------------------------------------
# Shared resources
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Failure logging + robust graph generation (retry once, fall back to gpt-4o)
# ---------------------------------------------------------------------------
FAILURE_LOG = os.path.join(P.LOG_DIR, "failures.jsonl")
_GPT4O = {"model": "gpt-4o", "node_model": "gpt-4o", "edge_model": "gpt-4o",
          "verify_model": "gpt-4o", "direction_model": "gpt-4o"}


def _log_failure(kind: str, profile: Dict[str, Any], exc: Exception, models: Any = None) -> None:
    try:
        os.makedirs(os.path.dirname(FAILURE_LOG), exist_ok=True)
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "kind": kind,
               "error": repr(exc), "traceback": traceback.format_exc(), "models": models,
               "profile": profile}
        with open(FAILURE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
    print(f"[fail] {kind}: {exc}", flush=True)


def _graph_with_retry(profile: Dict[str, Any], kind: str, primary: Dict[str, str]) -> Dict[str, Any]:
    """Generate a graph; on error/empty result, log it and retry ONCE with gpt-4o."""
    for attempt, models in ((1, primary), (2, _GPT4O)):
        try:
            r = generate_network(profile, **models)
            if r.get("error") is None and r.get("nodes"):
                if attempt == 2:
                    r["fallback"] = "gpt-4o"
                return r
            raise RuntimeError(r.get("error") or "empty graph (no nodes)")
        except Exception as exc:  # noqa: BLE001
            _log_failure(f"{kind}{'' if attempt == 1 else '-fallback(gpt-4o)'}", profile, exc, models)
    return {"nodes": [], "edges": [], "removed_edges": [], "groups": [],
            "error": "generation failed after retry (see logs/failures.jsonl)"}


def _expand_profile(short_text: str) -> Dict[str, Any]:
    """Stage-1 on the on-demand GPU worker: allocate/reuse a GPU, then POST the
    short profile text and get the long profile back."""
    url = gpu_manager.ensure_worker()          # blocks until a GPU worker is ready
    r = requests.post(url + "/expand", json={"short_text": short_text}, timeout=WORKER_TIMEOUT)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Existing profiles
# ---------------------------------------------------------------------------
def _load_existing() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not os.path.exists(EXISTING_PATH):
        return out
    with open(EXISTING_PATH, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return out


EXISTING = _load_existing()


def _summary(idx: int, p: Dict[str, Any]) -> Dict[str, Any]:
    from angel_demo import profile_meta as pm
    ident = p.get("identity", {}) or {}
    dx = ident.get("diagnosis_hint", []) or []
    pp = p.get("presenting_problems", []) or []
    culture = pm.infer_culture(p)
    return {
        "index": idx,
        "name": pm.display_name(idx, p),
        "age": ident.get("age"),
        "age_bucket": pm.age_bucket(ident.get("age")),
        "gender": pm.norm_gender(ident.get("gender")),
        "culture": culture or "Unspecified",
        "disorders": pm.disorders_of(p),
        "title": ident.get("source_title", ""),
        "diagnosis": dx if isinstance(dx, list) else [str(dx)],
        "summary": " • ".join(str(x) for x in pp[:3]),
    }


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def _new_job() -> str:
    jid = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[jid] = {
            "status": "running", "stage": "starting",
            "short_graph": None, "long_profile": None, "long_graph": None,
            "timings": {}, "error": None,
        }
    return jid


def _update(jid: str, **kw) -> None:
    with JOBS_LOCK:
        if jid in JOBS:
            JOBS[jid].update(kw)


def _run_job(jid: str, profile: Dict[str, Any]) -> None:
    t0 = time.time()
    timings: Dict[str, float] = {}

    # Full graph (nodes + verified edges + theme groups). Themes are aggregated
    # from the ONE real edge set client-side, so expand/collapse is instant and
    # in/out edges stay coherent.
    def short_graph(p: Dict[str, Any]) -> Dict[str, Any]:
        return _graph_with_retry(p, "short", {"model": SHORT_MODEL, "node_model": SHORT_MODEL,
                                              "edge_model": SHORT_MODEL, "verify_model": SHORT_MODEL,
                                              "direction_model": SHORT_MODEL})

    def long_graph(p: Dict[str, Any]) -> Dict[str, Any]:
        return _graph_with_retry(p, "long", {"node_model": LONG_NODE_MODEL, "edge_model": LONG_EDGE_MODEL,
                                            "verify_model": LONG_VERIFY_MODEL, "direction_model": LONG_DIRECTION_MODEL})

    # SHORT-profile graph (API) — runs while stage-1 (GPU) runs.
    short_holder: Dict[str, Any] = {}

    def run_short():
        ts = time.time()
        try:
            short_holder["g"] = short_graph(profile)
        except Exception as exc:  # noqa: BLE001
            short_holder["g"] = {"nodes": [], "edges": [], "removed_edges": [], "error": repr(exc)}
        timings["short_graph"] = round(time.time() - ts, 1)
        with JOBS_LOCK:
            if jid in JOBS:
                JOBS[jid]["short_graph"] = short_holder["g"]
                JOBS[jid]["timings"] = dict(timings)

    short_thread = threading.Thread(target=run_short, daemon=True)
    short_thread.start()

    # STAGE-1 on the on-demand GPU worker (allocate GPU if needed, then expand).
    gpu_manager.touch()
    _update(jid, stage=("allocating_gpu" if not gpu_manager.status().get("ready") else "stage1"))
    try:
        short_text = profile_to_short_text(profile)
        ts = time.time()
        s1 = _expand_profile(short_text)   # ensures GPU + POST /expand
        timings["stage1"] = round(time.time() - ts, 1)
        long_profile = s1.get("profile_json") or s1.get("long_profile")
    except Exception as exc:  # noqa: BLE001
        short_thread.join()
        _log_failure("stage1", profile, exc)
        _update(jid, status="error", error=f"stage-1 failed: {exc}", timings=dict(timings))
        return

    if long_profile is None:
        short_thread.join()
        _log_failure("stage1-parse", profile, RuntimeError(str(s1.get("parse_error"))))
        _update(jid, status="error", error=f"stage-1 parse failed: {s1.get('parse_error')}",
                timings=dict(timings))
        return
    _update(jid, long_profile=long_profile, stage="long_graph", timings=dict(timings))

    # Warm the stage-2 roleplay model NOW (GPU worker is up and idle while the
    # long graph runs on the API) so a later chat's first reply is fast.
    def _warm_stage2():
        try:
            requests.post(gpu_manager.ensure_worker() + "/warm_chat", timeout=WORKER_TIMEOUT)
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=_warm_stage2, daemon=True).start()

    # LONG-profile graph (API).
    ts = time.time()
    try:
        long_result = long_graph(long_profile)
    except Exception as exc:  # noqa: BLE001
        long_result = {"nodes": [], "edges": [], "removed_edges": [], "error": repr(exc)}
    timings["long_graph"] = round(time.time() - ts, 1)

    short_thread.join()
    timings["total"] = round(time.time() - t0, 1)
    _update(jid, status="done", stage="done", long_graph=long_result,
            short_graph=short_holder.get("g"), timings=dict(timings))


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------
class AssistReq(BaseModel):
    field_key: str
    field_label: str = ""
    field_type: str = "text"
    instruction: str = ""
    current_value: Any = ""
    profile: Dict[str, Any] = {}


class ProfileReq(BaseModel):
    profile: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# Optional URL prefix so the app can be served at e.g. https://host/demo instead
# of the host root (set DEMO_BASE_PATH=/demo). Empty => served at "/" (default,
# unchanged for local runs). The index HTML has root-absolute asset URLs, so when
# a base path is set we rewrite them and expose it to the frontend as DEMO_BASE.
DEMO_BASE_PATH = os.environ.get("DEMO_BASE_PATH", "").rstrip("/")


@app.get("/health")
async def health():
    """Liveness probe: answered on the event loop, never touches the thread pool, GPU or any API."""
    return {"ok": True, "time": time.time()}


@app.on_event("startup")
async def _bigger_threadpool() -> None:
    """Sync handlers run in anyio's thread pool (default 40). Slow calls (cold GPU wait, patient
    model) must never exhaust it and queue fast requests behind them."""
    import anyio.to_thread
    anyio.to_thread.current_default_thread_limiter().total_tokens = int(os.environ.get("DEMO_THREADPOOL", "200"))


@app.get("/")
async def index():                      # async: served on the event loop, independent of the thread pool
    html = open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8").read()
    if DEMO_BASE_PATH:
        html = html.replace('href="/static/', f'href="{DEMO_BASE_PATH}/static/')
        html = html.replace('src="/static/', f'src="{DEMO_BASE_PATH}/static/')
    html = html.replace("</head>", f'<script>window.DEMO_BASE="{DEMO_BASE_PATH}";</script></head>', 1)
    return HTMLResponse(html)


@app.get("/api/config")
def config():
    from angel_demo import ext_routes
    return {"profile_fields": PROFILE_FIELDS, "speaking_style_defaults": SPEAKING_STYLE_DEFAULTS,
            "empty_profile": empty_profile(), "audio": audio_service.audio_available(),
            "ext": ({"flags": ext_routes.flags(), "disclaimer": ext_routes.DISCLAIMER}
                    if ext_routes.any_on() else None)}


@app.get("/api/status")
def status():
    return {"n_existing": len(EXISTING), "gpu": gpu_manager.status()}


@app.get("/api/gpu/status")
def gpu_status():
    return gpu_manager.status()


@app.post("/api/gpu/ensure")
def gpu_ensure():
    """Pre-allocate a GPU (called when the user starts creating/choosing a profile)
    so it's warming up before they hit Generate. Non-blocking."""
    gpu_manager.touch()
    return gpu_manager.status()   # the packaged manager has nothing to allocate: the worker is started by the user


@app.get("/api/existing_profiles")
def existing_profiles():
    summaries = [_summary(i, p) for i, p in enumerate(EXISTING)]

    def facet(key):
        vals = {}
        for s in summaries:
            v = s.get(key)
            for x in (v if isinstance(v, list) else [v]):
                vals[x] = vals.get(x, 0) + 1
        return sorted(vals.keys(), key=lambda k: (-vals[k], str(k)))

    return {"profiles": summaries,
            "facets": {"gender": facet("gender"), "age_bucket": facet("age_bucket"),
                       "culture": facet("culture"), "disorder": facet("disorders")}}


@app.get("/api/existing_profiles/{idx}")
def existing_profile(idx: int):
    if idx < 0 or idx >= len(EXISTING):
        raise HTTPException(status_code=404, detail="profile index out of range")
    from angel_demo import profile_meta as pm
    prof = dict(EXISTING[idx])
    ident = dict(prof.get("identity", {}) or {})
    if not str(ident.get("name") or "").strip():   # give nameless profiles their display name
        ident["name"] = pm.display_name(idx, prof)
        prof["identity"] = ident
    return {"profile": prof}


@app.post("/api/profile/assist")
def api_assist(req: AssistReq):
    return {"assist": assist_field(req.field_key, req.field_label, req.field_type,
                                   req.instruction, req.current_value, req.profile or {})}


@app.post("/api/profile/review")
def api_review(req: ProfileReq):
    return {"review": review_profile(req.profile or {})}


class ChatStartReq(BaseModel):
    profile: Dict[str, Any] = {}
    # Stage-1 output for this profile, when the pipeline has already produced it.
    # Supplying it makes the actor role-play the EXPANDED profile rather than the
    # profile as authored — the two stages chain instead of forking.
    long_profile: Optional[Any] = None   # dict; a list wrapper is unwrapped below


class ChatSendReq(BaseModel):
    session_id: str
    message: str


# Observer fields the schema adapter has no slot for (the rich schema is flatter),
# rendered as extra sections so the actor still sees the whole expanded profile.
_OBSERVER_EXTRAS = [
    ("How the problem developed", [("symptom_details", "onset_and_course"),
                                   ("symptom_details", "frequency_and_intensity"),
                                   ("symptom_details", "maintaining_factors")]),
    ("Shame or fear points", [("emotional_profile", "shame_or_fear_points")]),
    ("Typical reaction when distressed", [("emotional_profile", "typical_reaction_when_distressed")]),
    ("Risk factors", [("risk_and_protective_factors", "risk_factors"),
                      ("risk_and_protective_factors", "safety_notes")]),
    ("Protective factors", [("risk_and_protective_factors", "protective_factors")]),
    ("When the therapist challenges you", [("disclosure_rules", "when_challenged")]),
    ("When the therapist is supportive", [("disclosure_rules", "when_supported")]),
    ("Example phrases (style only, do not repeat verbatim)", [("speaking_style", "example_phrases")]),
    # key None = top-level field; the adapter keeps these as simulation_rules.observer_notes,
    # which profile_to_short_text never renders. They mix patient behaviour ("Responds
    # with guardedness") with therapist advice ("Validate feelings"), so the heading
    # says how to read the latter: as sensitivities, never as lines to speak.
    # identity.source_title (the case-study paper title) is left out on purpose.
    ("Case notes (lines about how you respond describe your behaviour; lines advising "
     "the therapist mark topics you are sensitive about. Never act like the therapist)",
     [("simulation_rules", None)]),
]


def _observer_extras_text(long_profile: Dict[str, Any]) -> str:
    from angel_demo.schema_adapter import coerce_list
    blocks = []
    for title, paths in _OBSERVER_EXTRAS:
        items = []
        for section, key in paths:
            sec = long_profile.get(section)
            if key is None:
                items += coerce_list(sec)
            else:
                items += coerce_list(sec.get(key) if isinstance(sec, dict) else None)
        if items:
            blocks.append(f"{title}:\n" + "\n".join(f"- {x}" for x in items))
    return "\n\n".join(blocks)


def _chat_profile_text(profile: Dict[str, Any], long_profile: Any) -> tuple[str, str]:
    """Render the profile block for the actor's system prompt.

    Prefer stage-1's expanded profile so the actor role-plays what the observer
    produced. It uses the observer's nested clinical schema, not the flat rich
    schema `profile_to_short_text` reads, so it goes through the adapter first —
    passing it straight in would silently render "(none provided)" for every
    section. Fields the adapter has no slot for are appended after it. Falls back
    to the authored profile only when stage 1 failed or adaptation fails (the UI
    holds chat until stage 1 finishes).
    """
    if isinstance(long_profile, list):  # stray list wrapper: take the profile inside
        long_profile = next((x for x in long_profile if isinstance(x, dict)), None)
    if long_profile:
        try:
            from angel_demo.schema_adapter import adapt_observer_profile
            source_title = (profile.get("identity") or {}).get("source_title", "")
            adapted = adapt_observer_profile(long_profile, source_title=source_title)
            text = profile_to_short_text(adapted)
            extras = _observer_extras_text(long_profile)
            return (text + "\n\n" + extras if extras else text), "long"
        except Exception as exc:  # noqa: BLE001
            _log_failure("chat-adapt-long-profile", profile, exc)
    return profile_to_short_text(profile), "short"


@app.post("/api/chat/start")
def chat_start(req: ChatStartReq):
    if not req.profile:
        raise HTTPException(status_code=400, detail="profile is required")
    from angel_demo.patient_chat import build_patient_system_prompt
    profile_text, profile_source = _chat_profile_text(req.profile, req.long_profile)
    sp = build_patient_system_prompt(profile_text)
    sid = uuid.uuid4().hex[:12]
    with CHAT_LOCK:
        CHAT[sid] = {"system_prompt": sp, "conversation": [], "profile": req.profile,
                     "profile_source": profile_source}
    gpu_manager.touch()

    def _prewarm_chat():   # allocate GPU + load the roleplay model while the user reads/types
        try:
            url = gpu_manager.ensure_worker()
            requests.post(url + "/warm_chat", timeout=WORKER_TIMEOUT)
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=_prewarm_chat, daemon=True).start()

    ident = req.profile.get("identity", {}) or {}
    return {"session_id": sid, "patient_name": ident.get("name") or "the patient",
            "system_prompt": sp, "profile_source": profile_source}


@app.post("/api/chat/send")
def chat_send(req: ChatSendReq):
    with CHAT_LOCK:
        sess = CHAT.get(req.session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="unknown or expired chat session")
    msg = (req.message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="empty message")
    gpu_manager.touch()
    url = gpu_manager.ensure_worker()
    convo = sess["conversation"] + [{"role": "therapist", "content": msg}]
    try:
        r = requests.post(url + "/chat",
                          json={"system_prompt": sess["system_prompt"], "conversation": convo},
                          timeout=WORKER_TIMEOUT)
        r.raise_for_status()
        from angel_demo.patient_chat import clean_reply
        reply = clean_reply(r.json().get("reply") or "")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"patient model error: {exc}")
    with CHAT_LOCK:
        sess["conversation"].append({"role": "therapist", "content": msg})
        sess["conversation"].append({"role": "patient", "content": reply})
    return {"reply": reply}


@app.post("/api/chat/transcribe")
async def chat_transcribe(audio: UploadFile = File(...)):
    """Speech-to-text for the therapist's mic input."""
    if not audio_service.audio_available():
        raise HTTPException(status_code=503, detail="audio not configured")
    import tempfile
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty audio")
    if len(data) < 1200:
        raise HTTPException(status_code=400, detail="recording too short")
    suffix = os.path.splitext(audio.filename or "")[1].lower()
    if suffix not in {".webm", ".ogg", ".oga", ".mp3", ".mp4", ".m4a", ".wav", ".mpeg", ".mpga", ".flac"}:
        suffix = ".webm"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
            tmp = f.name
            f.write(data)
        text = await asyncio.to_thread(audio_service.transcribe, tmp)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"transcription failed: {exc}")
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return {"transcript": text}


class TTSReq(BaseModel):
    session_id: str = ""
    text: str


async def _tts_response(session_id: str, text: str):
    """Shared TTS: instant cache hit, else stream mp3 chunks as they arrive."""
    if not audio_service.audio_available():
        raise HTTPException(status_code=503, detail="audio not configured")
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    with CHAT_LOCK:
        sess = CHAT.get(session_id)
    profile = (sess or {}).get("profile", {})
    try:
        cached, gen = await asyncio.to_thread(audio_service.synthesize_stream, text, profile)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"tts failed: {exc}")
    if cached is not None:                       # instant replay from cache
        if not cached:
            raise HTTPException(status_code=500, detail="empty audio")
        return Response(content=cached, media_type="audio/mpeg")

    async def stream():                          # pump the blocking generator off the event loop
        loop = asyncio.get_event_loop()
        it = iter(gen)
        while True:
            chunk = await loop.run_in_executor(None, lambda: next(it, None))
            if chunk is None:
                break
            yield chunk

    return StreamingResponse(stream(), media_type="audio/mpeg")


@app.post("/api/chat/tts")
async def chat_tts(req: TTSReq):
    return await _tts_response(req.session_id, req.text)


@app.get("/api/chat/tts")
async def chat_tts_get(text: str, session_id: str = ""):
    """GET variant so an <audio> element can stream + play progressively."""
    return await _tts_response(session_id, text)


# ANGEL demo extension (F1/F2/F3, SPEC §7): only mounted when a DEMO_FEAT_* flag is on.
def _include_ext_routes() -> None:
    from angel_demo import ext_routes
    if ext_routes.any_on():
        app.include_router(ext_routes.make_router(CHAT, CHAT_LOCK, gpu_manager.ensure_worker,
                                                  gpu_manager.touch, EXISTING))


_include_ext_routes()


@app.post("/api/generate")
def api_generate(req: ProfileReq):
    if not req.profile:
        raise HTTPException(status_code=400, detail="profile is required")
    gpu_manager.touch()
    jid = _new_job()
    threading.Thread(target=_run_job, args=(jid, req.profile), daemon=True).start()
    return {"job_id": jid}


@app.get("/api/job/{jid}")
def api_job(jid: str):
    with JOBS_LOCK:
        job = JOBS.get(jid)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.get("status") == "running":
            gpu_manager.touch()   # keep GPU alive while a job is active
        return dict(job)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7871)
    parser.add_argument("--warm", action="store_true", help="Pre-allocate a GPU at startup.")
    args = parser.parse_args()

    gpu_manager.start_watchdog()   # idle-kills the GPU job after 20 min
    if args.warm:
        threading.Thread(target=lambda: gpu_manager.ensure_worker(), daemon=True).start()

    import socket
    hostname = socket.gethostname()
    try:
        ip = socket.gethostbyname(hostname)
    except Exception:  # noqa: BLE001
        ip = args.host
    print("\n" + "=" * 60, flush=True)
    print("  ANGEL demo is starting. Open one of:", flush=True)
    if args.host not in ("127.0.0.1", "localhost"):
        print(f"    http://{hostname}:{args.port}/", flush=True)
        print(f"    http://{ip}:{args.port}/", flush=True)
    if args.host in ("127.0.0.1", "localhost"):
        print(f"    http://localhost:{args.port}/", flush=True)
    print("=" * 60 + "\n", flush=True)

    import uvicorn
    # When DEMO_BASE_PATH is set, mount the whole app under that prefix so it is
    # reachable at https://host<base>/ (e.g. /demo), with the bare root redirecting
    # there. Otherwise serve the app at "/" exactly as before.
    if DEMO_BASE_PATH:
        root_app = FastAPI()

        @root_app.get("/")
        def _to_base():
            return RedirectResponse(DEMO_BASE_PATH + "/")

        root_app.mount(DEMO_BASE_PATH, app)
        asgi_app = root_app
    else:
        asgi_app = app
    uvicorn.run(asgi_app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
