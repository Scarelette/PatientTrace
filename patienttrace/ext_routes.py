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
    f["live"] = gpu_manager.live_enabled() and has_key
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
        for c in out.get("cards", []):
            t = DF.target_for(pid, c["patient_id"])
            f = DF.default_focus(DF.contrast_graph(pid, c["patient_id"]), t)
            c["target_concept_id"] = t
            c["default_focus"] = f["concept_id"]
            c["focus_reason"] = f["reason"]
            c["focus_flag"] = f["flag"]
            c["distinctive_counts"] = {k: list(v) for k, v in f["counts"].items()}
        return out

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
