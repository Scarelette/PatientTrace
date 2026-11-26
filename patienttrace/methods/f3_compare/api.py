"""Frontend data endpoints for the dual-patient view (SPEC §7). All functions return JSON-ready
dicts; the FastAPI layer (Phase 5) only has to wrap them in routes, e.g.

    POST /api/f3/start      {patient_a, patient_b, probe_question?}  -> start_pair(...)
    POST /api/f3/shared     {session_id, question, wait?}            -> shared_question(...)
    POST /api/f3/follow_up  {session_id, side, question, wait?}      -> follow_up(...)
    GET  /api/f3/turn       ?session_id&turn_no                      -> get_turn(...)   (poll)
    GET  /api/f3/session    ?session_id                              -> get_session(...)
    GET  /api/f3/export     ?session_id                              -> export_session(...) (SPEC §2 F3 JSON)
    POST /api/f3/close      {session_id}                             -> close_session(...)

Flow (async alignment, SPEC §3.4 / §7): `shared_question(..., wait=False)` returns as soon as
both replies exist (status "replied"); alignment and then diff_summary run in the background.
Poll `get_turn` until `status == "done"` (statuses: replying -> replied -> aligning ->
summarizing -> done | error). `wait=True` blocks until done (scripts / tests).

Patient backend: the demo's GPU worker over HTTP (`PatientModel("http", url)`). The url is
taken from the `patient_url` argument, else `F3_PATIENT_URL`, else the worker state file
(`F3_WORKER_STATE`, default `<repo>/.worker.json`, written by stage1_worker). A custom
model object with `.reply(system_prompt, conversation)` can be passed via `model=`.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from patienttrace.angel_ext import data as D

from . import diff as DF
from .engine import SIDES, DualSession

_SESSIONS: Dict[str, DualSession] = {}
_LOCK = threading.Lock()
MAX_SESSIONS = int(os.environ.get("F3_MAX_SESSIONS", "200"))


# ---------------------------------------------------------------------------
def worker_url(patient_url: Optional[str] = None) -> str:
    if patient_url:
        return patient_url
    if os.environ.get("F3_PATIENT_URL"):
        return os.environ["F3_PATIENT_URL"]
    path = os.environ.get("F3_WORKER_STATE", D.P.WORKER_STATE)
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
    return f"http://{st['host']}:{st['port']}"


def _session(session_id: str) -> DualSession:
    s = _SESSIONS.get(session_id)
    if s is None:
        raise KeyError(f"unknown F3 session {session_id!r}")
    return s


def suggested_probe(patient_a: str, patient_b: str, allow_live: bool = False) -> Optional[str]:
    """F2 `ours` probe question if patient_b is among patient_a's F2 recommendations. Reads the
    precomputed F2 records (SPEC §2 F2 format, outputs/f2/ours/{dev,test,all}.jsonl); a live F2
    call (LLM rerank, tens of seconds) only with allow_live=True."""
    for name in ("all", "test", "dev"):
        path = D.P.data("f2", "ours", f"{name}.jsonl")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                if f'"{patient_a}"' not in line:
                    continue
                r = json.loads(line)
                if r.get("query_id") == patient_a:
                    for c in r.get("candidates") or []:
                        if c.get("patient_id") == patient_b and c.get("probe_question"):
                            return c["probe_question"]
    if allow_live:
        try:
            from patienttrace.methods.f2_recommend import recommend
            for c in recommend(patient_a)["candidates"]:
                if c.get("patient_id") == patient_b and c.get("probe_question"):
                    return c["probe_question"]
        except Exception:  # noqa: BLE001  F2 unavailable -> no suggestion
            return None
    return None


# ---------------------------------------------------------------------------
_NETS: Dict[str, Dict[str, Any]] = {}


def _net(pid: str) -> Dict[str, Any]:
    if pid not in _NETS:
        g = D.load_network(pid)
        _NETS[pid] = {"nodes": {n["id"]: n for n in g["nodes"]}, "edges": {e["id"]: e for e in g["edges"]}}
    return _NETS[pid]


def _alignment_view(pid: str, al: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """F1 record + resolved labels for highlighting."""
    if al is None:
        return None
    g = _net(pid)
    nodes = [{**n, "label": g["nodes"].get(n["node_id"], {}).get("label"),
              "type": g["nodes"].get(n["node_id"], {}).get("type")} for n in al.get("nodes") or []]
    edges = []
    for e in al.get("edges") or []:
        ed = g["edges"].get(e["edge_id"], {})
        edges.append({**e, "source": ed.get("source"), "target": ed.get("target"), "label": ed.get("label")})
    return {**al, "nodes": nodes, "edges": edges, "unmatched": bool(al.get("no_match"))}


def _diff_view(dual: DualSession, d: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if d is None:
        return None
    pids = {"a": dual.patient_a, "b": dual.patient_b}
    ver = d.get("version", DF.VERSION)
    cn = d.get("cited_nodes") or {}
    return {"diff_summary": d["diff_summary"], "text": DF.strip_tags(d["diff_summary"], ver),
            "cited_edges": d["cited_edges"], "cited_nodes": cn, "version": ver,
            "citations": [{"side": x, "edge_id": e, "label": _net(pids[x])["edges"].get(e, {}).get("label")}
                          for x in SIDES for e in d["cited_edges"].get(x, [])],
            "node_citations": [{"side": x, "node_id": n, "label": _net(pids[x])["nodes"].get(n, {}).get("label"),
                                "type": _net(pids[x])["nodes"].get(n, {}).get("type")}
                               for x in SIDES for n in cn.get(x, [])],
            "valid": d["valid"], "status": d["status"], "latency_ms": d["latency_ms"]}


def _turn_view(dual: DualSession, t: Dict[str, Any]) -> Dict[str, Any]:
    out = {"session_id": dual.session_id, "turn_no": t["turn_no"], "kind": t["kind"], "side": t["side"],
           "question": t["question"], "status": t["status"], "timing": dict(t["timing"])}
    if t.get("error"):
        out["error"] = t["error"]
    for x in SIDES:
        s = t.get(x)
        out[x] = None if s is None else {
            "patient_id": s["patient_id"], "utterance_id": s["utterance_id"], "response": s["response"],
            "reply_latency_ms": s["reply_latency_ms"], "alignment": _alignment_view(s["patient_id"], s["alignment"])}
    out["diff"] = _diff_view(dual, t.get("diff"))
    return out


# ---------------------------------------------------------------------------
def start_pair(patient_a: str, patient_b: str, probe_question: Optional[str] = None,
               patient_url: Optional[str] = None, model: Any = None,
               diff_effort: str = DF.DEFAULT_EFFORT, diff_version: str = DF.VERSION) -> Dict[str, Any]:
    """Create two fresh, isolated patient sessions (empty histories)."""
    for p in (patient_a, patient_b):
        D.idx_of(p)   # raises on unknown ids
    if model is None:
        from patienttrace.angel_ext.patient_sim import PatientModel
        model = PatientModel("http", worker_url(patient_url))
    sid = "f3-" + uuid.uuid4().hex[:10]
    dual = DualSession(patient_a, patient_b, model, session_id=sid, diff_effort=diff_effort,
                       diff_version=diff_version)
    with _LOCK:
        if len(_SESSIONS) >= MAX_SESSIONS:       # drop the oldest
            del _SESSIONS[min(_SESSIONS, key=lambda k: _SESSIONS[k].created)]
        _SESSIONS[sid] = dual
    probe = probe_question or suggested_probe(patient_a, patient_b)
    return {"session_id": sid,
            "patients": {x: {"patient_id": dual.sessions[x].pid, "name": dual.sessions[x].patient_name,
                             "tags": D.patient_tags(dual.sessions[x].pid)} for x in SIDES},
            "suggested_question": probe, "same_patient": patient_a == patient_b}


def shared_question(session_id: str, question: str, wait: bool = False) -> Dict[str, Any]:
    dual = _session(session_id)
    t = dual.ask_both(question)
    if wait:
        dual.annotate(t)
    else:
        dual.annotate_async(t)
    return _turn_view(dual, t)


def follow_up(session_id: str, side: str, question: str, wait: bool = False) -> Dict[str, Any]:
    dual = _session(session_id)
    t = dual.ask_one(side, question)
    if wait:
        dual.annotate(t)
    else:
        dual.annotate_async(t)
    return _turn_view(dual, t)


def get_turn(session_id: str, turn_no: int) -> Dict[str, Any]:
    dual = _session(session_id)
    return _turn_view(dual, dual.turns[int(turn_no) - 1])


def get_session(session_id: str) -> Dict[str, Any]:
    dual = _session(session_id)
    return {"session_id": session_id, "patient_a": dual.patient_a, "patient_b": dual.patient_b,
            "turns": [_turn_view(dual, t) for t in dual.turns]}


def export_session(session_id: str) -> Dict[str, Any]:
    """SPEC §2 F3 record (+ follow-up turns, marked kind=follow_up) for JSON download."""
    dual = _session(session_id)
    return {"pair_id": session_id, "pair_type": "interactive", "sample_idx": 0,
            "patient_a": dual.patient_a, "patient_b": dual.patient_b, "method": "ours",
            "exported_at": time.time(), "turns": dual.spec_turns(shared_only=False)}


def close_session(session_id: str) -> Dict[str, Any]:
    with _LOCK:
        return {"closed": _SESSIONS.pop(session_id, None) is not None}


__all__ = ["start_pair", "shared_question", "follow_up", "get_turn", "get_session", "export_session",
           "close_session", "suggested_probe", "worker_url"]
