"""F3 dual-session engine (SPEC §5).

Two strictly isolated patient sessions (own system prompt, own history list, own network),
each starting from an empty history. Patient turns come from the unmodified ANGEL patient
model (`angel_ext.patient_sim.PatientModel`: demo system prompt, T=0.8, length plan, regen
backstop, clean_reply) — nothing is added to the patient prompt.

    dual = DualSession(pa, pb, model, session_id="...")
    turn = dual.ask_both(question)            # both patients answer in parallel
    turn = dual.ask_one("a", question)        # follow-up to one patient only
    dual.annotate(turn)                       # F1 alignment of every answer (+ diff_summary
                                              # for shared turns); or ask_*(..., annotate=True)

Alignment = frozen F1 `align(..., method="ours")`. The alignment context of an answer is
that session's own transcript from its 3rd-most-recent therapist question up to (and
including) the question being answered — the same window the F3 evaluator uses for
cross-alignment (eval/f3_compare/common._context), so own- and cross-alignments see
identical context.
"""

from __future__ import annotations

import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout   # != builtin TimeoutError on 3.10
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional

from . import diff as DF

SIDES = ("a", "b")
CTX_QUESTIONS = 3       # therapist questions in the alignment window (incl. the current one)

# Separate pools so nested waits cannot deadlock: reply/align tasks are leaves; annotate_async
# (background alignment + diff for the frontend) waits on align tasks.
_REPLY_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="f3-reply")
_ALIGN_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="f3-align")
_BG_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="f3-bg")
_DIFF_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="f3-diff")
# Interactive (demo) bounds on outbound waits; unset = wait as long as the call takes (batch eval).
import os as _os
ALIGN_TIMEOUT_S = float(_os.environ["F3_ALIGN_TIMEOUT_S"]) if _os.environ.get("F3_ALIGN_TIMEOUT_S") else None
DIFF_TIMEOUT_S = float(_os.environ["F3_DIFF_TIMEOUT_S"]) if _os.environ.get("F3_DIFF_TIMEOUT_S") else None
UNAVAILABLE_ALIGNMENT = {"status": "unavailable", "error": "alignment unavailable (timeout)",
                         "nodes": [], "edges": [], "no_match": False}


@lru_cache(maxsize=512)
def system_prompt(pid: str) -> Dict[str, str]:
    from angel_demo.angel_ext.patient_sim import build_system_prompt
    return build_system_prompt(pid)


def _f1_align():
    from angel_demo.methods.f1_align import align
    return align


def align_context(history: List[Dict[str, str]], n_questions: int = CTX_QUESTIONS) -> List[Dict[str, str]]:
    """history = the session transcript up to and including the question being answered."""
    qidx = [i for i, t in enumerate(history) if t["role"] == "therapist"]
    start = qidx[-n_questions] if len(qidx) >= n_questions else 0
    return [dict(t) for t in history[start:]]


class PatientSession:
    """One patient, one independent history. Never shares mutable state with another session."""

    def __init__(self, pid: str, model: Any, side: str, session_id: str):
        self.pid, self.side, self.model, self.session_id = pid, side, model, session_id
        # model=None: replay-only session (batch annotate stage) — no patient prompt needed.
        sp = system_prompt(pid) if model is not None else {"system_prompt": "", "patient_name": "",
                                                            "system_prompt_sha": ""}
        self.system_prompt: str = sp["system_prompt"]
        self.patient_name: str = sp.get("patient_name", "")
        self.system_prompt_sha: str = sp.get("system_prompt_sha", "")
        self.history: List[Dict[str, str]] = []
        self._lock = threading.Lock()          # one turn at a time per session

    def ask(self, question: str, turn_no: int, response: Optional[str] = None) -> Dict[str, Any]:
        """Send `question`, get the patient's reply, append both to THIS session's history.
        `response` given = replay a pre-generated answer (batch annotate stage)."""
        with self._lock:
            conv = copy.deepcopy(self.history) + [{"role": "therapist", "content": question}]
            ctx = align_context(conv)
            if response is None:
                if self.model is None:
                    raise RuntimeError("replay-only session: pass `response`")
                r = self.model.reply(self.system_prompt, conv)
                response, lat = r["text"], r["latency_ms"]
            else:
                lat = None
            self.history = conv + [{"role": "patient", "content": response}]
            return {"utterance_id": f"{self.session_id}.t{turn_no}.{self.side}", "patient_id": self.pid,
                    "question": question, "response": response, "reply_latency_ms": lat,
                    "context": ctx, "alignment": None}


    def rollback(self, n: int) -> None:
        with self._lock:
            del self.history[n:]


class DualSession:
    def __init__(self, patient_a: str, patient_b: str, model: Any, session_id: str,
                 b_model: Optional[Any] = None, diff_effort: str = DF.DEFAULT_EFFORT, sample_idx: int = 0,
                 diff_version: str = DF.VERSION):
        self.session_id = session_id
        self.patient_a, self.patient_b = patient_a, patient_b
        self.sessions = {"a": PatientSession(patient_a, model, "a", session_id),
                         "b": PatientSession(patient_b, b_model or model, "b", session_id)}
        self.turns: List[Dict[str, Any]] = []
        self.diff_effort, self.sample_idx = diff_effort, sample_idx
        self.diff_version = diff_version            # demo passes f3-diff-v3-demo; eval keeps the default
        self._lock = threading.Lock()
        self.created = time.time()

    # ---- asking -----------------------------------------------------------
    def _new_turn(self, kind: str, side: str, question: str) -> Dict[str, Any]:
        with self._lock:
            t = {"turn_no": len(self.turns) + 1, "kind": kind, "side": side, "question": question,
                 "status": "replying", "a": None, "b": None, "diff": None, "timing": {}}
            self.turns.append(t)
            return t

    def _drop_turn(self, t: Dict[str, Any]) -> None:
        with self._lock:
            if self.turns and self.turns[-1] is t:
                self.turns.pop()
            else:
                t["status"] = "error"

    def ask_both(self, question: str, annotate: bool = False,
                 responses: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Shared question: both patients answer in parallel (two threads, two histories)."""
        t = self._new_turn("shared", "both", question)
        t0 = time.time()
        before = {x: len(self.sessions[x].history) for x in SIDES}
        futs = {x: _REPLY_POOL.submit(self.sessions[x].ask, question, t["turn_no"],
                                (responses or {}).get(x)) for x in SIDES}
        errs = {}
        for x in SIDES:
            try:
                t[x] = futs[x].result()
            except Exception as exc:  # noqa: BLE001
                errs[x] = exc
        if errs:   # keep the two sessions in step: undo the side that did answer, drop the turn
            for x in SIDES:
                self.sessions[x].rollback(before[x])
            self._drop_turn(t)
            raise RuntimeError(f"patient reply failed for side(s) {sorted(errs)}: {errs[sorted(errs)[0]]!r}")
        t["timing"]["replies_wall_ms"] = round((time.time() - t0) * 1000, 1)
        t["status"] = "replied"
        if annotate:
            self.annotate(t)
        return t

    def ask_one(self, side: str, question: str, annotate: bool = False,
                response: Optional[str] = None) -> Dict[str, Any]:
        """Follow-up to one patient only; the other session's history is untouched."""
        if side not in SIDES:
            raise ValueError(f"side must be 'a' or 'b', got {side!r}")
        t = self._new_turn("follow_up", side, question)
        t0 = time.time()
        try:
            t[side] = self.sessions[side].ask(question, t["turn_no"], response)
        except Exception:
            self._drop_turn(t)
            raise
        t["timing"]["replies_wall_ms"] = round((time.time() - t0) * 1000, 1)
        t["status"] = "replied"
        if annotate:
            self.annotate(t)
        return t

    # ---- alignment + diff ---------------------------------------------------
    def align_turn(self, t: Dict[str, Any]) -> None:
        align = _f1_align()
        sides = [x for x in SIDES if t.get(x)]
        t["status"] = "aligning"
        t0 = time.time()
        futs = {x: _ALIGN_POOL.submit(align, t[x]["response"], t[x]["patient_id"], t[x]["context"],
                                "ours", t[x]["utterance_id"]) for x in sides}
        for x in sides:
            try:
                t[x]["alignment"] = futs[x].result(timeout=ALIGN_TIMEOUT_S)
            except FutureTimeout:
                t[x]["alignment"] = dict(UNAVAILABLE_ALIGNMENT, utterance_id=t[x]["utterance_id"])
        t["timing"]["align_wall_ms"] = round((time.time() - t0) * 1000, 1)

    def summarize_turn(self, t: Dict[str, Any]) -> None:
        if t["kind"] != "shared":
            return
        t["status"] = "summarizing"
        t0 = time.time()
        fut = _DIFF_POOL.submit(DF.diff_summary, t["question"],
                                {x: {"patient_id": t[x]["patient_id"], "response": t[x]["response"],
                                     "alignment": t[x]["alignment"]} for x in SIDES},
                                effort=self.diff_effort, sample_idx=self.sample_idx,
                                version=self.diff_version)
        try:
            t["diff"] = fut.result(timeout=DIFF_TIMEOUT_S)
        except FutureTimeout:
            raise RuntimeError("difference summary unavailable (timeout)")
        t["timing"]["diff_wall_ms"] = round((time.time() - t0) * 1000, 1)

    def annotate(self, t: Dict[str, Any], on_aligned: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        try:
            self.align_turn(t)
            if on_aligned:
                on_aligned(t)
            self.summarize_turn(t)
            t["status"] = "done"
        except Exception as exc:  # noqa: BLE001  never crash the frontend
            t["status"] = "error"
            t["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        return t

    def annotate_async(self, t: Dict[str, Any]):
        return _BG_POOL.submit(self.annotate, t)

    # ---- export -------------------------------------------------------------
    def history(self, side: str) -> List[Dict[str, str]]:
        return copy.deepcopy(self.sessions[side].history)

    def spec_turns(self, shared_only: bool = True) -> List[Dict[str, Any]]:
        """SPEC §2 F3 `turns` (shared questions only by default)."""
        out = []
        for t in self.turns:
            if shared_only and t["kind"] != "shared":
                continue
            d = t.get("diff") or {}
            rec = {"question": t["question"]}
            for x in SIDES:
                if t.get(x):
                    rec[f"patient_{x}"] = {"response": t[x]["response"], "alignment": t[x]["alignment"]}
            if t["kind"] == "shared":
                rec["diff_summary"] = d.get("diff_summary", "")
                rec["cited_edges"] = d.get("cited_edges", {"a": [], "b": []})
                rec["diff_meta"] = {k: d.get(k) for k in ("valid", "status", "attempts", "problems", "version",
                                                          "latency_ms", "tokens", "effort")}
            else:
                rec["kind"] = "follow_up"
                rec["side"] = t["side"]
            rec["timing"] = {**t["timing"], **{f"reply_ms_{x}": t[x]["reply_latency_ms"] for x in SIDES if t.get(x)}}
            out.append(rec)
        return out
