"""ANGEL demo extension routes (SPEC §7): F1 alignment, F2 contrast cards, F3 dual view.

Everything here sits behind env feature flags (all default OFF, so the demo is unchanged
when none is set):

    DEMO_FEAT_PRECOMPUTED=1  library patients load the precomputed long profile + long
                             network from data/precomputed/ instead of live generation
    DEMO_FEAT_ALIGN=1        chat page: network beside the dialogue + async F1 alignment
    DEMO_FEAT_CONTRAST=1     "Find a contrast patient" (F2 recommendation cards)
    DEMO_FEAT_DUAL=1         dual-patient view (F3)

The three feature flags imply precomputed loading for library patients (the methods
work on the precomputed networks). `demo_app` includes `make_router(...)` only when at
least one flag is on. Method code (methods/, angel_ext/) is called, never modified.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

from patienttrace import paths as P
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_TRUE = ("1", "true", "yes", "on")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def flags() -> Dict[str, bool]:
    f = {"align": _flag("DEMO_FEAT_ALIGN"), "contrast": _flag("DEMO_FEAT_CONTRAST"),
         "dual": _flag("DEMO_FEAT_DUAL")}
    f["precomputed"] = _flag("DEMO_FEAT_PRECOMPUTED") or any(f.values())
    # Packaged demo: live interview / compare need a GPU worker AND an API key for alignment and
    # summaries; without them the app runs in browse/replay mode. Creating new profiles is off.
    from patienttrace import gpu_manager
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AZURE_FOUNDRY_API_KEY"))
    # "live" = this server CAN run live sessions (a worker is configured, or follows a worker job's state file);
    # whether the patient model is up right now comes from /api/live-status, which the pages poll
    capable = gpu_manager.mode() != "none" or bool(os.environ.get("PATIENTTRACE_WORKER_FILE"))
    f["live"] = capable and has_key
    f["custom"] = _flag("DEMO_FEAT_CUSTOM")
    return f


def any_on() -> bool:
    return any(flags().values())


MAX_ALIGN_SESSIONS = int(os.environ.get("DEMO_EXT_MAX_ALIGN_SESSIONS", "500"))
ALIGN_TIMEOUT_S = float(os.environ.get("DEMO_EXT_ALIGN_TIMEOUT_S", "10"))
# Demo diff_summary (DEMO ONLY): v5-demo since round 10 (2026-11-11); also cites aligned NODES for a side without aligned edges (user decision
# 2026-11-09). The evaluated F3 method is f3-diff-v2 (methods/f3_compare/diff.py default).
DEMO_DIFF_VERSION = os.environ.get("DEMO_EXT_DIFF_VERSION", "f3-diff-v5-demo")   # round 10: names + traced causes, <= 30 words

DISCLAIMER = ("Simulated patients for clinical-skills training only. Not real people and "
              "not a clinical or diagnostic tool.")


class AlignReq(BaseModel):
    session_id: str
    patient_id: str
    turn: int                                  # 1-based index of the patient reply in the session
    utterance: Optional[str] = None            # only used when the session is unknown (tests)
    context: Optional[List[Dict[str, Any]]] = None


class InterviewTurnReq(BaseModel):
    patient_id: str
    trainee: str = ""                          # the trainee message this reply answers
    reply: str
    history: List[Dict[str, Any]] = []         # earlier turns [{who: t|p, text}] (the aligner uses the last ones)
    earlier: List[str] = []                    # node ids active in earlier turns of the session
    level: str = "normal"                      # "low": second, more inclusive pass ("Suggest nodes")
    turn: Optional[int] = None                 # for the latency log only


class LiveFCStartReq(BaseModel):
    patient_a: str
    patient_b: str


class LiveFCAskReq(BaseModel):
    fid: str
    question: str
    stream_id: str = ""                        # lets the Stop button cancel this stream
    given: Dict[str, str] = {}                 # {"a": answer}: that side's answer is already known (its saved Interview)


class AdaptReq(BaseModel):
    patient_a: str
    patient_b: str
    questions: List[str]


class LiveStopReq(BaseModel):
    stream_id: str


class SaveInterviewReq(BaseModel):
    patient_id: str
    name: str = ""
    session_id: str
    turns: List[Dict[str, Any]]


class SaveFCReq(BaseModel):
    fid: str
    record: Dict[str, Any]


# ---- per-(patient, turn text) cache for alignment / guidance / FC analysis (bounded, in memory) ----
_CACHE: Dict[str, Any] = {}
STOPS: Dict[str, threading.Event] = {}        # stream_id -> Event: the Stop button sets it


def _ck(*parts: Any) -> str:
    import hashlib
    import json as _j
    return hashlib.sha256(_j.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


async def _capped(fn: Callable[[], Any], seconds: float, what: str, retries: int = 1) -> Any:
    """Run a blocking model step in a worker thread with a time cap per attempt and one retry, so a slow or
    hung model call never ties up a request (or the event loop) for longer than ~2 x seconds."""
    last: Optional[BaseException] = None
    for _ in range(retries + 1):
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout=seconds)
        except asyncio.TimeoutError:
            last = TimeoutError(f"{what} timed out after {seconds:.0f} s")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
    if isinstance(last, TimeoutError):
        raise TimeoutError(f"{last} (tried {retries + 1} times)")
    raise RuntimeError(f"{what} failed: {type(last).__name__}: {str(last)[:200]}")


def _cache_put(key: str, val: Any, cap: int = 2000) -> None:
    if len(_CACHE) >= cap:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = val


class FCTurnReq(BaseModel):
    patient_a: str
    patient_b: str
    question: str = ""
    a: Dict[str, Any]                          # {response}
    b: Dict[str, Any]
    history_a: List[Dict[str, Any]] = []       # earlier [{who: t|p, text}] for this patient
    history_b: List[Dict[str, Any]] = []


class FCRevealReq(BaseModel):
    patient_a: str
    patient_b: str
    focus: Optional[str] = None
    reached: Dict[str, List[str]] = {}
    names: Dict[str, str] = {}
    genders: Dict[str, str] = {}


class FCLearnReq(BaseModel):
    names: Dict[str, str] = {}
    turns: List[Dict[str, Any]] = []           # [{question, d}]


class F3StartReq(BaseModel):
    patient_a: str
    patient_b: str
    probe_question: Optional[str] = None


class F3SharedReq(BaseModel):
    session_id: str
    question: str


class F3FollowReq(BaseModel):
    session_id: str
    side: str
    question: str


class F3CloseReq(BaseModel):
    session_id: str


def make_router(chat_store: Dict[str, Dict[str, Any]], chat_lock: threading.Lock,
                ensure_worker: Callable[[], str], touch: Callable[[], None],
                existing: List[Dict[str, Any]]) -> APIRouter:
    """chat_store/chat_lock: demo_app.CHAT / CHAT_LOCK (read-only use);
    ensure_worker/touch: gpu_manager functions (the instance's own, isolated worker)."""
    from patienttrace.angel_ext import data as D

    F = flags()
    r = APIRouter(prefix="/api/ext")
    n_lib = len(existing)

    # session_id -> {"patient_id": pid, "records": {turn: F1 record}}
    ALIGN: Dict[str, Dict[str, Any]] = {}
    ALIGN_LOCK = threading.Lock()

    def _pid(pid: str) -> str:
        try:
            idx = D.idx_of(pid)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=404, detail=f"unknown patient id {pid!r}")
        if idx < 0 or idx >= n_lib:
            raise HTTPException(status_code=404, detail=f"unknown patient id {pid!r}")
        return D.pid_of(idx)

    def _need(name: str) -> None:
        if not F.get(name):
            raise HTTPException(status_code=404, detail=f"feature '{name}' is disabled")

    @r.get("/config")
    def ext_config():
        return {"flags": F, "disclaimer": DISCLAIMER, "n_library": n_lib}

    # ---------------------------------------------------------------- precomputed
    @r.get("/patient/{idx}")
    def patient(idx: int):
        """Precomputed bundle for library patient `idx`: profile (as /api/existing_profiles),
        stage-1 long profile, raw long graph (for the network page), SPEC network."""
        _need("precomputed")
        if idx < 0 or idx >= n_lib:
            raise HTTPException(status_code=404, detail="profile index out of range")
        from patienttrace import profile_meta as pm
        pid = D.pid_of(idx)
        prof = dict(existing[idx])
        ident = dict(prof.get("identity", {}) or {})
        if not str(ident.get("name") or "").strip():
            ident["name"] = pm.display_name(idx, prof)
            prof["identity"] = ident
        import json
        with open(D.precomp_path(pid, "raw_network.json"), encoding="utf-8") as f:
            raw = json.load(f)
        lp = D.load_long_profile(pid)
        return {"patient_id": pid, "index": idx, "profile": prof,
                "long_profile": lp.get("long_profile") if isinstance(lp, dict) else lp,
                "long_graph": {k: raw.get(k) for k in ("nodes", "edges", "removed_edges")} | {
                    "groups": raw.get("groups") or _raw_groups_file(pid), "error": raw.get("error")},
                "network": D.load_network(pid), "tags": D.patient_tags(pid)}

    def _raw_groups_file(pid: str) -> List[Dict[str, Any]]:
        """Name-keyed theme groups from groups.json (p000-p003, scripts/p5_fill_groups.py)."""
        import json
        gp = D.precomp_path(pid, "groups.json")
        if not os.path.exists(gp):
            return []
        with open(gp, encoding="utf-8") as f:
            return json.load(f).get("groups") or []

    def _groups(pid: str) -> List[Dict[str, Any]]:
        """Theme clusters of the raw long graph, re-keyed from node names to SPEC node ids."""
        import json
        with open(D.precomp_path(pid, "raw_network.json"), encoding="utf-8") as f:
            raw = json.load(f)
        by_label = {n["label"]: n["id"] for n in D.load_network(pid)["nodes"]}
        groups = raw.get("groups") or _raw_groups_file(pid)   # p000-p003: display-only fill
        out = []
        for g in groups or []:
            ids = [by_label[m] for m in g.get("members") or [] if m in by_label]
            if ids:
                out.append({"label": g.get("label") or "", "type": g.get("type"), "members": ids})
        return out

    @r.get("/network/{pid}")
    def network(pid: str):
        _need("precomputed")
        pid = _pid(pid)
        return {"patient_id": pid, "network": D.load_network(pid), "groups": _groups(pid),
                "tags": D.patient_tags(pid)}

    # ---------------------------------------------------------------- replay (no LLM / GPU)
    REPLAY_DIR = P.data("replay")

    # ---- Interview page: per-turn explanation (spans, path, trigger, eased + AI guidance) ----
    @r.post("/interview/turn")
    async def interview_turn(req: InterviewTurnReq):
        _need("align")
        pid = _pid(req.patient_id)
        from patienttrace import interview as IV
        # the Interview page's own aligner (the paper's frozen F1 aligner is not used or changed here)
        from patienttrace import live as L
        key = _ck("iv", pid, req.trainee, req.reply, req.history, req.earlier, req.level)
        if key in _CACHE:                                   # re-renders / retries never call the model again
            L.log_step(page="interview", patient=pid, turn=req.turn, step="analysis", ms=0, ok=True, cached=True)
            return _CACHE[key]
        t0 = time.time()
        try:                                                # 45 s per attempt, one retry, then a clear error
            out = await _capped(lambda: IV.enrich_turn_v2(pid, req.trainee, req.reply, req.history, req.earlier,
                                                          "low" if req.level == "low" else "normal"), 45, "Analysis (alignment + guidance)")
        except Exception as exc:  # noqa: BLE001
            L.log_step(page="interview", patient=pid, turn=req.turn, step="analysis", ms=round((time.time() - t0) * 1000), ok=False, error=str(exc)[:200])
            raise HTTPException(status_code=504 if "timed out" in str(exc) else 502, detail=str(exc)[:300])
        tm = out.get("timing") or {}
        L.log_step(page="interview", patient=pid, turn=req.turn, step="alignment", ms=tm.get("alignment_ms"), ok=True)
        L.log_step(page="interview", patient=pid, turn=req.turn, step="guidance", ms=tm.get("guidance_ms"), ok=not (out.get("guidance") or {}).get("error"))
        _cache_put(key, out)
        return out

    @r.get("/interview/sessions")
    def interview_sessions():
        """Library patients that have a recorded interview: [{index, name}]."""
        import json, re
        d = P.data("interview")
        out = []
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            m = re.fullmatch(r"p(\d{3})\.json", f)
            if m:
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        out.append({"index": int(m.group(1)), "name": json.load(fh).get("name") or f"p{m.group(1)}"})
                except Exception:  # noqa: BLE001
                    pass
        return {"recorded": out}

    @r.get("/interview/labels/{idx}")
    async def interview_labels(idx: int):
        """Short node labels for the Interview network ({} when none are stored and no LLM key is set)."""
        from patienttrace import interview as IV
        try:
            return await asyncio.to_thread(IV.short_labels, f"p{idx:03d}")
        except Exception:  # noqa: BLE001  no key / API error: the page truncates labels itself
            return {}

    @r.get("/interview/session/{idx}")
    def interview_session(idx: int):
        """A recorded interview for library patient `idx` (data/interview/pNNN.json), or 404."""
        path = P.data("interview", f"p{idx:03d}.json")
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="no recorded interview for this patient")
        import json
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @r.get("/replay")
    def replay_list():
        import json
        p = os.path.join(REPLAY_DIR, "index.json")
        if not os.path.exists(p):
            return {"cases": []}
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    @r.get("/replay/{case_id}")
    def replay_case(case_id: str):
        import json, re
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", case_id):
            raise HTTPException(status_code=400, detail="bad case id")
        p = os.path.join(REPLAY_DIR, case_id + ".json")
        if not os.path.exists(p):
            raise HTTPException(status_code=404, detail="unknown replay case")
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    # ---- Formulation Contrast (redesigned Compare): gap per turn, baskets, end-of-round reveal ----
    @r.post("/fc/turn")
    async def fc_turn(req: FCTurnReq):
        _need("dual")
        from patienttrace import contrast as FC
        try:
            pa, pb = _pid(req.patient_a), _pid(req.patient_b)
            from patienttrace import live as L
            key = _ck("fc", pa, pb, req.question, req.a.get("response"), req.b.get("response"), req.history_a, req.history_b)
            turn = len(req.history_a or []) // 2 + 1
            if key in _CACHE:
                L.log_step(page="fc", patient=f"{pa}|{pb}", turn=turn, step="analysis", ms=0, ok=True, cached=True)
                return _CACHE[key]

            def run():
                import concurrent.futures as cf
                t0 = time.time()
                with cf.ThreadPoolExecutor(2) as ex:      # both alignments at once
                    fa = ex.submit(FC.align, pa, req.question, req.a.get("response") or "", req.history_a)
                    fb = ex.submit(FC.align, pb, req.question, req.b.get("response") or "", req.history_b)
                    a, b = dict(req.a, alignment=fa.result()), dict(req.b, alignment=fb.result())
                t1 = time.time()
                for pid, x in ((pa, a), (pb, b)):
                    FC.log_unaligned(pid, turn, req.question, x.get("response") or "", "live", x["alignment"])
                out = {"a_alignment": a["alignment"], "b_alignment": b["alignment"], **FC.turn_analysis(pa, pb, a, b, req.question)}
                t2 = time.time()
                L.log_step(page="fc", patient=f"{pa}|{pb}", turn=turn, step="alignment", ms=round((t1 - t0) * 1000), ok=True)
                L.log_step(page="fc", patient=f"{pa}|{pb}", turn=turn, step="analysis", ms=round((t2 - t1) * 1000), ok=True,
                           note="gap, gists, Both said check, AI reading")
                out["timing"] = {"alignment_ms": round((t1 - t0) * 1000), "analysis_ms": round((t2 - t1) * 1000)}
                return out
            try:
                out = await _capped(run, 60, "Placing the answers (alignment + gap)")
            except Exception as exc:  # noqa: BLE001
                L.log_step(page="fc", patient=f"{pa}|{pb}", turn=turn, step="analysis", ok=False, error=str(exc)[:200])
                raise HTTPException(status_code=504 if "timed out" in str(exc) else 502, detail=str(exc)[:300])
            _cache_put(key, out)
            return out
        except Exception as exc:  # noqa: BLE001  embeddings need the model key (live mode)
            raise HTTPException(status_code=503, detail=f"gap unavailable: {type(exc).__name__}: {str(exc)[:160]}")

    @r.get("/fc/reveal/{a}/{b}")
    def fc_reveal(a: str, b: str, focus: Optional[str] = None):
        _need("dual")
        from patienttrace import contrast as FC
        return FC.reveal(_pid(a), _pid(b), focus)

    @r.post("/fc/reveal")
    async def fc_reveal_post(req: FCRevealReq):
        _need("dual")
        from patienttrace import contrast as FC
        return await asyncio.to_thread(FC.reveal, _pid(req.patient_a), _pid(req.patient_b), req.focus, req.reached, req.names, req.genders)

    @r.post("/fc/learning")
    async def fc_learning(req: FCLearnReq):
        _need("dual")
        from patienttrace import contrast as FC, live as L
        t0 = time.time()
        try:
            out = await _capped(lambda: FC.learning_point(req.names, req.turns), 45, "The learning point")
        except Exception as exc:  # noqa: BLE001
            out = {"_error": str(exc)[:200]}
        err = out.pop("_error", None) if isinstance(out, dict) else None
        L.log_step(page="fc", patient="|".join(req.names.values()), turn=len(req.turns), step="learning", ms=round((time.time() - t0) * 1000), ok=bool(out.get("point")), error=err)
        return out                                   # {} -> the page hides the box

    # ---- live mode: streamed patient replies (Interview uses /api/chat/stream in demo_app) ----
    @r.post("/fc/live/start")
    async def fc_live_start(req: LiveFCStartReq):
        _need("dual")
        from patienttrace import live as L
        if not flags().get("live"):
            raise HTTPException(status_code=503, detail="Live mode is off: " + "; ".join(L.missing()))
        return await asyncio.to_thread(L.fc_start, _pid(req.patient_a), _pid(req.patient_b))

    @r.post("/fc/live/ask")
    def fc_live_ask(req: LiveFCAskReq):
        """NDJSON: {"side": a|b, "text"} ... {"side", "done", "reply", timings} ... {"both_done"} or {"side", "error"}."""
        from fastapi.responses import StreamingResponse
        from patienttrace import live as L
        stop = STOPS.setdefault(req.stream_id or _ck(req.fid, req.question, time.time()), threading.Event())

        def gen():
            try:
                yield from L.ndjson(L.fc_ask(req.fid, req.question.strip(), stop, req.given))
            finally:
                STOPS.pop(req.stream_id, None)
        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @r.post("/fc/adapt")
    async def fc_adapt(req: AdaptReq):
        """The trainee's Interview questions for patient A, adapted for patient B (names / pronouns swapped) with
        the ones that depend on A's own details flagged, for review before replaying them."""
        from patienttrace.picker import adapt_questions
        try:
            items = await _capped(lambda: adapt_questions(_pid(req.patient_a), _pid(req.patient_b), req.questions), 45, "Adapting the questions", retries=0)
        except Exception:  # noqa: BLE001  model slow or failing: swap the name only
            from patienttrace import picker as PK
            from patienttrace.angel_ext import data as D
            names = [(D.short_profiles()[D.idx_of(_pid(p))].get("identity") or {}).get("name") or "" for p in (req.patient_a, req.patient_b)]
            items = [{"original": q, "adapted": PK._swap_name(q, *names), "flag": False, "reason": "", "neutral": ""} for q in req.questions]
        return {"items": items}

    @r.post("/live/stop")
    def live_stop(req: LiveStopReq):
        ev = STOPS.get(req.stream_id)
        if ev:
            ev.set()
        return {"stopped": bool(ev)}

    @r.post("/interview/save")
    def interview_save(req: SaveInterviewReq):
        from patienttrace import live as L
        return {"path": os.path.relpath(L.save_interview(_pid(req.patient_id), req.name, req.session_id, req.turns), P.state())}

    @r.post("/fc/save")
    def fc_save(req: SaveFCReq):
        from patienttrace import live as L
        return {"path": os.path.relpath(L.save_fc(req.fid, req.record), P.state())}

    @r.get("/contrast-graph/{a}/{b}")
    def contrast_graph(a: str, b: str):
        """ContrastGraph data (PHASE5_CONTRACTS): shared surface concepts merged in the centre,
        each side's direct causes into them, distinctive edges flagged (angel_ext.concept_sd,
        the same definition the F3 evaluation uses). Read-only use of method/eval helpers."""
        if not (F.get("dual") or F.get("contrast")):
            raise HTTPException(status_code=404, detail="feature 'dual' is disabled")
        a, b = _pid(a), _pid(b)
        from patienttrace.angel_ext.demo_focus import contrast_graph as _cg
        return _cg(a, b)

    # ---------------------------------------------------------------- F1 alignment
    @r.post("/align")
    async def align(req: AlignReq):
        """Align the `turn`-th patient reply of a live chat session (async; the reply itself
        was already returned by /api/chat/send). utterance_id = "{session}.t{turn}.P"."""
        _need("align")
        pid = _pid(req.patient_id)
        if req.turn < 1:
            raise HTTPException(status_code=400, detail="turn must be >= 1")
        with chat_lock:
            sess = chat_store.get(req.session_id)
            convo = list(sess["conversation"]) if sess else None
        if convo is not None:
            pidx = [i for i, t in enumerate(convo) if t.get("role") == "patient"]
            if req.turn > len(pidx):
                raise HTTPException(status_code=409, detail="no patient reply for that turn yet")
            i = pidx[req.turn - 1]
            utterance, context = convo[i]["content"], convo[:i]
        elif req.utterance is not None:
            utterance, context = req.utterance, list(req.context or [])
        else:
            raise HTTPException(status_code=404, detail="unknown or expired chat session")
        with ALIGN_LOCK:
            cached = ALIGN.get(req.session_id, {}).get("records", {}).get(req.turn)
        if cached is not None and cached.get("_utt") == utterance:
            return _view(pid, cached)
        from patienttrace.methods.f1_align import align_async
        uid = f"{req.session_id}.t{req.turn}.P"
        try:
            rec = await asyncio.wait_for(align_async(utterance, pid, context, method="ours", utterance_id=uid),
                                         timeout=ALIGN_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001  (timeout or aligner error -> UI shows a retry icon)
            return {"status": "unavailable", "turn": req.turn, "utterance_id": uid,
                    "reason": "timeout" if isinstance(exc, asyncio.TimeoutError) else type(exc).__name__}
        rec = dict(rec, _utt=utterance, turn=req.turn)
        with ALIGN_LOCK:
            if req.session_id not in ALIGN and len(ALIGN) >= MAX_ALIGN_SESSIONS:   # drop the oldest
                ALIGN.pop(next(iter(ALIGN)))
            s = ALIGN.setdefault(req.session_id, {"patient_id": pid, "records": {}})
            s["patient_id"] = pid
            s["records"][req.turn] = rec
        return _view(pid, rec)

    def _view(pid: str, rec: Dict[str, Any]) -> Dict[str, Any]:
        """F1 record + node/edge labels for the UI (record fields unchanged)."""
        g = D.load_network(pid)
        nodes = {n["id"]: n for n in g["nodes"]}
        edges = {e["id"]: e for e in g["edges"]}
        out = {k: v for k, v in rec.items() if k != "_utt"}
        out["nodes"] = [{**n, "label": nodes.get(n["node_id"], {}).get("label"),
                         "type": nodes.get(n["node_id"], {}).get("type")} for n in rec.get("nodes") or []]
        out["edges"] = [{**e, "source": edges.get(e["edge_id"], {}).get("source"),
                         "target": edges.get(e["edge_id"], {}).get("target"),
                         "label": edges.get(e["edge_id"], {}).get("label")} for e in rec.get("edges") or []]
        out["unmatched"] = bool(rec.get("no_match"))
        return out

    @r.get("/align/export")
    def align_export(session_id: str):
        _need("align")
        with ALIGN_LOCK:
            s = ALIGN.get(session_id)
            recs = dict(s["records"]) if s else {}
            pid = s["patient_id"] if s else None
        with chat_lock:
            sess = chat_store.get(session_id)
            convo = list(sess["conversation"]) if sess else []
        utts = []
        pturn = tturn = 0
        for t in convo:
            if t.get("role") == "patient":
                pturn += 1
                utts.append({"utterance_id": f"{session_id}.t{pturn}.P", "role": "patient",
                             "text": t["content"]})
            else:
                tturn += 1
                utts.append({"utterance_id": f"{session_id}.t{tturn}.T", "role": "therapist",
                             "text": t["content"]})
        return {"session_id": session_id, "patient_id": pid, "method": "ours", "exported_at": time.time(),
                "dialogue": utts,
                "alignments": [{k: v for k, v in recs[k2].items() if k != "_utt"} for k2 in sorted(recs)]}

    # ---------------------------------------------------------------- F2 contrast
    @r.get("/recommend/{pid}")
    async def recommend(pid: str):
        _need("contrast")
        pid = _pid(pid)
        from patienttrace.methods.f2_recommend import recommendation_cards
        try:
            out = await asyncio.to_thread(recommendation_cards, pid, "ours", 3)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"recommendation failed: {exc}")
        # demo-only: the symptom each probe question was written for (data/f2_targets.json) and the
        # card's default focus symptom (never one where either patient has no distinctive cause)
        from patienttrace.angel_ext import demo_focus as DF

        def focus_all():
            for c in out.get("cards", []):
                t = DF.target_for(pid, c["patient_id"])
                f = DF.default_focus(DF.contrast_graph(pid, c["patient_id"]), t)
                c["target_concept_id"] = t
                c["default_focus"] = f["concept_id"]
                c["focus_reason"] = f["reason"]
                c["focus_flag"] = f["flag"]
                c["distinctive_counts"] = {k: list(v) for k, v in f["counts"].items()}
        await asyncio.to_thread(focus_all)                  # nothing blocking runs on the event loop
        return out

    @r.get("/recommend/{pid}/picker")
    async def recommend_picker(pid: str):
        """Short chip labels + one 'why' sentence per ranked contrast patient for the picker (cached)."""
        out = await recommend(pid)
        from patienttrace.picker import picker_text
        txt = await asyncio.to_thread(picker_text, _pid(pid), out.get("cards", []))
        return {"cards": [{"patient_id": c["patient_id"], **txt.get(c["patient_id"], {})} for c in out.get("cards", [])]}

    # ---------------------------------------------------------------- F3 dual view
    def _f3():
        from patienttrace.methods.f3_compare import api as f3
        return f3

    def _f3_call(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=f"unavailable (timeout): {exc}")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (ValueError, IndexError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            if "timed out" in str(exc).lower() or "timeout" in type(exc).__name__.lower():
                raise HTTPException(status_code=504, detail=f"unavailable (timeout): {exc}")
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}")

    @r.post("/f3/start")
    async def f3_start(req: F3StartReq):
        _need("dual")
        a, b = _pid(req.patient_a), _pid(req.patient_b)
        touch()
        try:
            url = await asyncio.to_thread(ensure_worker)   # this instance's GPU worker
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"patient model unavailable: {exc}")
        out = await asyncio.to_thread(_f3_call, _f3().start_pair, a, b, req.probe_question, url,
                                      diff_version=DEMO_DIFF_VERSION)
        out["disclaimer"] = DISCLAIMER
        return out

    @r.post("/f3/shared")
    async def f3_shared(req: F3SharedReq):
        _need("dual")
        if not req.question.strip():
            raise HTTPException(status_code=400, detail="empty question")
        touch()
        return await asyncio.to_thread(_f3_call, _f3().shared_question, req.session_id, req.question.strip(), False)

    @r.post("/f3/follow_up")
    async def f3_follow(req: F3FollowReq):
        _need("dual")
        if not req.question.strip():
            raise HTTPException(status_code=400, detail="empty question")
        touch()
        return await asyncio.to_thread(_f3_call, _f3().follow_up, req.session_id, req.side,
                                       req.question.strip(), False)

    @r.get("/f3/turn")
    def f3_turn(session_id: str, turn_no: int):
        _need("dual")
        touch()
        return _f3_call(_f3().get_turn, session_id, turn_no)

    @r.get("/f3/session")
    def f3_session(session_id: str):
        _need("dual")
        return _f3_call(_f3().get_session, session_id)

    @r.get("/f3/export")
    def f3_export(session_id: str):
        _need("dual")
        return _f3_call(_f3().export_session, session_id)

    @r.post("/f3/close")
    def f3_close(req: F3CloseReq):
        _need("dual")
        return _f3().close_session(req.session_id)

    return r
