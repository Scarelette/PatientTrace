"""ours: programmatic S filter -> S x D top-N -> LLM rerank to top-k with rationale +
probe -> programmatic citation validation (one repair call per invalid candidate, else drop)."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from patienttrace.angel_ext.llm import get_client

from . import core as K
from . import prompts as P


def _call(system: str, user: str, effort: str, tag: str, sample_idx: int = 0):
    llm = get_client()
    return llm.chat(llm.method_model, user=user, system=system, json_mode=True,
                    max_tokens=K.CONFIG["max_tokens"], effort=effort, tag=tag, sample_idx=sample_idx)


def recommend_ours(q: str, k: int = K.K_DEFAULT, effort: Optional[str] = None,
                   top_n: Optional[int] = None) -> Dict[str, Any]:
    effort = effort or K.CONFIG["effort"]
    top_n = top_n or K.CONFIG["top_n"]
    t0 = time.time()
    llm_ms, tin, tout, calls = 0.0, 0, 0, []
    wall = {"llm": 0.0}
    pool = K.shortlist(q)[:top_n]
    by_id = {r["patient_id"]: r for r in pool}
    log: Dict[str, Any] = {"pool": [{"patient_id": r["patient_id"], "S": round(r["S"], 4),
                                     "D": round(r["D"], 4), "SxD": round(r["SxD"], 4),
                                     "hub_freq": r.get("hub_freq")} for r in pool],
                           "s_threshold": round(K.s_threshold(q), 4), "effort": effort,
                           "fallback": None, "repairs": [], "dropped": []}
    user = P.rerank_user(q, pool, k, K.CONFIG["prompt"])

    # step 3: rerank (one retry with a different sample if the JSON is unusable)
    selected: List[Dict[str, Any]] = []
    for attempt in range(2):
        tc = time.time()
        r = _call(P.system_rerank(K.CONFIG["prompt"]), user, effort, "f2.ours.rerank", sample_idx=attempt)
        wall["llm"] += time.time() - tc
        llm_ms += r.latency_ms
        tin += r.tokens_in
        tout += r.tokens_out
        calls.append({"kind": "rerank", "cached": r.cached, "latency_ms": r.latency_ms})
        sel = (r.json or {}).get("selected") if isinstance(r.json, dict) else None
        if isinstance(sel, list):
            seen = set()
            for it in sel:
                pid = str((it or {}).get("candidate", "")).strip()
                if pid in by_id and pid not in seen:
                    seen.add(pid)
                    selected.append(it)
            if selected:
                break
    selected = selected[:k]

    if not selected:   # LLM unusable twice: fall back to the programmatic order + template
        log["fallback"] = "rerank_failed_template"
        cands = [K.make_candidate(q, r["patient_id"], i, K.template_text(q, r["patient_id"]))
                 for i, r in enumerate(pool[:k], 1)]
        return _record(q, cands, t0, llm_ms, tin, tout, calls, log, wall)

    # step 4: validate; one repair call per invalid candidate (in parallel), else drop
    checked = [(it, *K.validate(q, it["candidate"], it)) for it in selected]
    bad = [i for i, (_, _, errs) in enumerate(checked) if errs]

    def repair(i):
        it, _, errs = checked[i]
        return i, errs, _call(P.system_regen(K.CONFIG["prompt"]), P.regen_user(q, by_id[it["candidate"]], it, errs, K.CONFIG["prompt"]),
                              effort, "f2.ours.regen")

    if bad:
        tc = time.time()
        with ThreadPoolExecutor(max_workers=len(bad)) as ex:
            results = list(ex.map(repair, bad))
        wall["llm"] += time.time() - tc
        llm_ms += max(r.latency_ms for _, _, r in results)   # repairs run concurrently
        for i, errs, r in results:
            tin += r.tokens_in
            tout += r.tokens_out
            calls.append({"kind": "regen", "cached": r.cached, "latency_ms": r.latency_ms})
            it = checked[i][0]
            new = r.json if isinstance(r.json, dict) else {}
            new = {**new, "candidate": it["candidate"]}
            cited, errs2 = K.validate(q, it["candidate"], new)
            log["repairs"].append({"candidate": it["candidate"], "errors": errs, "after": errs2})
            checked[i] = (new, cited, errs2)

    cands, rank = [], 0
    for it, cited, errs in checked:
        pid = it["candidate"]
        if errs:
            log["dropped"].append({"candidate": pid, "errors": errs})
            continue
        rank += 1
        cands.append(K.make_candidate(
            q, pid, rank, {"rationale": it["rationale"].strip(), "cited_edges": cited,
                           "probe_question": it["probe_question"].strip()},
            teaching_point=(it.get("teaching_point") or "").strip(),
            pool_rank=[r["patient_id"] for r in pool].index(pid) + 1))
    return _record(q, cands, t0, llm_ms, tin, tout, calls, log, wall)


def _record(q, cands, t0, llm_ms, tin, tout, calls, log, wall):
    # end-to-end latency = original LLM latency (cache-stable) + programmatic time of this run
    prog_ms = (time.time() - t0) * 1000 - sum(c["latency_ms"] for c in calls if not c["cached"])
    return {"query_id": q, "method": "ours", "latency_ms": round(llm_ms + max(prog_ms, 0.0), 1),
            "candidates": cands, "tokens": {"in": tin, "out": tout}, "llm_calls": calls,
            "config_hash": K.config_hash(), "debug": log}
