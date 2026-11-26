"""F2 contrastive patient recommendation — shared data helpers, candidate scoring,
template rationale/probe, and the five methods (ours, R1–R4).

S and D are ALWAYS the frozen shared reference `angel_ext.concept_sd` (SPEC §4 + the
Checkpoint-1 coarse-cause D); this module never redefines them. Pairwise S/D for a query
against the whole 329-patient pool is computed once per query and memoised
(`pool_scores`), and can be precomputed for all 330 patients with `precompute_sd()`.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

from patienttrace.angel_ext import concept_sd as C
from patienttrace.angel_ext import data as D

K_DEFAULT = 3
METHODS = ("ours", "R1", "R2", "R3", "R4")
HERE = os.path.dirname(os.path.abspath(__file__))
from patienttrace import paths as _P
CACHE_DIR = _P.data("f2_cache")

# ---------------------------------------------------------------------------
# Configuration (chosen from the dev pilot; see results/ledger.md Phase-2 entry)
# ---------------------------------------------------------------------------
CONFIG: Dict[str, Any] = {
    # FROZEN 2026-11-07 = preset v1 (dev round 1; round-3 freeze rule: v2.1 primary 92.08 < 94.0).
    "version": "f2-ours-v1",
    "prompt": "v1",            # prompts.system_rerank(version): v1 (round 1, frozen), v2 (round 2), v2.1 (round 3)
    # Step 1 filter: candidate must reach the query's own 90th-percentile S over the
    # 329-patient pool (query-relative, because surface-set sizes and hence typical S vary
    # 2x across queries) AND share >= 3 surface concepts (D averaged over 1-2 concepts is
    # mostly an averaging artefact, EVAL_PLAN §3.2.1).
    "s_pool_percentile": 90.0,
    "min_shared": 3,
    "top_n": 10,               # step 2: top-N by S x D go to the LLM
    # v2 (Phase 3, round 2): hub penalty for the ours shortlist. score = S*D * (E/max(f_p,E))^alpha,
    # f_p = how often p is in the top-N shortlist over ALL 330 queries (damped fixed point of
    # the penalised ranking itself), E = expected frequency top_n*330/329. R4 is unchanged.
    "hub_alpha": 0.0,         # v1: no hub penalty (v2/v2.1 used 0.25)
    "hub_iters": 30,
    "effort": "medium",        # step 3: METHOD_MODEL effort (pilot: see ledger)
    "max_tokens": 16000,
    "max_cites_per_side": 4,
    "seed": 20261204,          # random baselines: rng = Random(f"{seed}:{query}")
    "r3_text": "long_profile",  # R3 embeds the rendered stage-1 long profile
}


# Reproducible presets of ours (R1–R4 do not depend on them).
PRESETS: Dict[str, Dict[str, Any]] = {
    "v1": {"version": "f2-ours-v1", "prompt": "v1", "hub_alpha": 0.0},      # dev round 1
    "v2": {"version": "f2-ours-v2", "prompt": "v2", "hub_alpha": 0.25},     # dev round 2
    "v2.1": {"version": "f2-ours-v2.1", "prompt": "v2.1", "hub_alpha": 0.25},
}


def use_preset(name: str) -> None:
    CONFIG.update(PRESETS[name])
    hub_frequency.cache_clear()


def config_hash(extra: str = "") -> str:
    from . import prompts as P
    blob = (json.dumps(CONFIG, sort_keys=True) + P.system_rerank(CONFIG["prompt"])
            + P.system_regen(CONFIG["prompt"]) + extra)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
@lru_cache(maxsize=None)
def all_pids() -> Tuple[str, ...]:
    return tuple(D.all_patient_ids())


@lru_cache(maxsize=None)
def network(pid: str) -> Dict[str, Any]:
    return D.load_network(pid)


@lru_cache(maxsize=None)
def node_index(pid: str) -> Dict[str, Dict[str, Any]]:
    return {n["id"]: n for n in network(pid)["nodes"]}


@lru_cache(maxsize=None)
def edge_index(pid: str) -> Dict[str, Dict[str, Any]]:
    return {e["id"]: e for e in network(pid)["edges"]}


@lru_cache(maxsize=None)
def in_edges(pid: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for e in network(pid)["edges"]:
        out.setdefault(e["target"], []).append(e["id"])
    return out


@lru_cache(maxsize=1)
def _concepts():
    concepts, n2c = D.load_concepts()
    return {c["concept_id"]: c for c in concepts}, n2c


def concept_name(cid: str) -> str:
    return _concepts()[0].get(cid, {}).get("name", cid)


def concept_prevalence(cid: str) -> int:
    """Number of patients (of 330) that have a node in this concept."""
    return int(_concepts()[0].get(cid, {}).get("n_patients", 0))


def node_concept(pid: str, nid: str) -> Optional[str]:
    return _concepts()[1].get(f"{pid}:{nid}")


@lru_cache(maxsize=None)
def surface_nodes_by_concept(pid: str) -> Dict[str, List[str]]:
    """surface concept id -> this patient's surface node ids mapping to it."""
    out: Dict[str, List[str]] = {}
    for n in network(pid)["nodes"]:
        if n["type"] in D.SURFACE_TYPES:
            c = node_concept(pid, n["id"])
            if c:
                out.setdefault(c, []).append(n["id"])
    return out


@lru_cache(maxsize=None)
def tags(pid: str) -> Dict[str, Any]:
    return D.patient_tags(pid)


def edge_text(pid: str, eid: str) -> str:
    e, n = edge_index(pid)[eid], node_index(pid)
    s, t = n[e["source"]], n[e["target"]]
    return f"{s['label']} [{s['type']}] -> {t['label']} [{t['type']}]"


# ---------------------------------------------------------------------------
# S / D over the pool (frozen reference implementation)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=None)
def pool_scores(q: str) -> Tuple[Dict[str, Any], ...]:
    """All 329 candidates of query q with S, D, S*D (D None -> 0) and #shared."""
    rows = []
    for p in all_pids():
        if p == q:
            continue
        s = C.surface_similarity(q, p)
        d = C.mechanism_difference(q, p)
        rows.append({"patient_id": p, "S": s, "D": d, "SxD": s * (d or 0.0),
                     "n_shared": len(C.shared_surface(q, p))})
    return tuple(rows)


def _percentile(xs: Sequence[float], pct: float) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = (len(xs) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def s_threshold(q: str) -> float:
    return _percentile([r["S"] for r in pool_scores(q)], CONFIG["s_pool_percentile"])


def filtered_ranked(q: str) -> List[Dict[str, Any]]:
    """Steps 1–2: S >= threshold and #shared >= min_shared, ranked by S x D (ties: S, id)."""
    thr = s_threshold(q)
    rows = [r for r in pool_scores(q)
            if r["S"] >= thr and r["n_shared"] >= CONFIG["min_shared"] and r["D"] is not None]
    rows.sort(key=lambda r: (-r["SxD"], -r["S"], r["patient_id"]))
    return rows


@lru_cache(maxsize=1)
def hub_frequency() -> Dict[str, float]:
    """Shortlist frequency of every patient over all 330 queries under the penalised ranking
    (damped fixed-point iteration, starting from the unpenalised S x D shortlist). Computed
    from the whole case library only (no split / eval information); cached on disk."""
    path = os.path.join(CACHE_DIR, f"hub_freq_a{CONFIG['hub_alpha']}_n{CONFIG['top_n']}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    n, pids = CONFIG["top_n"], all_pids()
    ranked = {q: filtered_ranked(q) for q in pids}
    f: Dict[str, float] = {}
    for q in pids:
        for r in ranked[q][:n]:
            f[r["patient_id"]] = f.get(r["patient_id"], 0.0) + 1
    for _ in range(CONFIG["hub_iters"]):
        g: Dict[str, float] = {}
        for q in pids:
            for r in _penalised(ranked[q], f)[:n]:
                g[r["patient_id"]] = g.get(r["patient_id"], 0.0) + 1
        f = {p: 0.5 * f.get(p, 0.0) + 0.5 * g.get(p, 0.0) for p in set(f) | set(g)}
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(f, fh, sort_keys=True)
    return f


def _penalised(rows: List[Dict[str, Any]], f: Dict[str, float]) -> List[Dict[str, Any]]:
    E = CONFIG["top_n"] * len(all_pids()) / (len(all_pids()) - 1)
    a = CONFIG["hub_alpha"]
    out = [{**r, "hub_freq": round(f.get(r["patient_id"], 0.0), 2),
            "score": r["SxD"] * (E / max(f.get(r["patient_id"], 0.0), E)) ** a} for r in rows]
    out.sort(key=lambda r: (-r["score"], -r["S"], r["patient_id"]))
    return out


def shortlist(q: str) -> List[Dict[str, Any]]:
    """ours steps 1–2 (v2): filtered candidates ranked by hub-penalised S x D, top-N."""
    return _penalised(filtered_ranked(q), hub_frequency())[:CONFIG["top_n"]]


def precompute_sd(pids: Optional[Sequence[str]] = None, path: Optional[str] = None) -> str:
    """Precompute filtered + ranked S/D candidate lists for every patient (for the demo:
    interactive R4 / ours-pool construction then needs no pairwise computation)."""
    pids = list(pids or all_pids())
    path = path or os.path.join(CACHE_DIR, "sd_ranked.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = {q: {"s_threshold": s_threshold(q), "ranked": filtered_ranked(q)[:50]} for q in pids}
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"config": CONFIG, "by_query": out}, f)
    return path


# ---------------------------------------------------------------------------
# Shared-surface "contrast sheet" (used by the template, the LLM prompt and the cards)
# ---------------------------------------------------------------------------
def contrast_sheet(q: str, p: str) -> List[Dict[str, Any]]:
    """Per shared surface concept: the nodes on each side and the edges INTO them, with a
    flag for edges whose (coarse cause -> concept) pair is absent from the other network
    (= concept_sd.distinctive_edges)."""
    dist = C.distinctive_edges(q, p)
    dq, dp = set(dist["a"]), set(dist["b"])
    sq, sp = surface_nodes_by_concept(q), surface_nodes_by_concept(p)
    out = []
    for c in C.shared_surface(q, p):
        side = {}
        for who, pid, nodes, dset in (("query", q, sq.get(c, []), dq), ("candidate", p, sp.get(c, []), dp)):
            edges = []
            for nid in nodes:
                for eid in in_edges(pid).get(nid, []):
                    edges.append({"edge_id": eid, "distinctive": eid in dset})
            side[who] = {"nodes": nodes, "edges": edges}
        out.append({"concept_id": c, "name": concept_name(c), "prevalence": concept_prevalence(c),
                    "type": node_index(q)[sq[c][0]]["type"] if sq.get(c) else "",
                    **side})
    return out


def mechanism_edge_ids(q: str, p: str) -> Dict[str, set]:
    """Edges into nodes of shared surface concepts, per side."""
    out = {"query": set(), "candidate": set()}
    for row in contrast_sheet(q, p):
        for who in out:
            out[who] |= {e["edge_id"] for e in row[who]["edges"]}
    return out


# ---------------------------------------------------------------------------
# Template rationale + probe (R4; also used by R1–R3 per Checkpoint-1 decision 3)
# ---------------------------------------------------------------------------
_PROBE_LEAD = {"Emotion": "When you start feeling {x}",
               "Physiological Sensation": "When you notice {x}",
               "Behavior": "When it comes to {x}"}


def _focus_order(sheet: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Shared concepts most useful to explain: causes on both sides, some distinctive cause
    on both sides, then more specific (rarer) concepts first."""
    def key(r):
        qe, ce = r["query"]["edges"], r["candidate"]["edges"]
        both = bool(qe) and bool(ce)
        dist = any(e["distinctive"] for e in qe) and any(e["distinctive"] for e in ce)
        return (-int(both), -int(dist), r["prevalence"], r["concept_id"])
    return sorted(sheet, key=key)


_CAUSE_PRIORITY = {"Cognition": 0, "Stimulus": 1}


def _pick_edges(pid: str, edges: List[Dict[str, Any]], n: int) -> List[str]:
    """Distinctive edges first, then causes that are cognitions / stimuli (the mechanism
    proper) before other surface nodes, then edge order."""
    E, N = edge_index(pid), node_index(pid)
    es = sorted(edges, key=lambda e: (not e["distinctive"],
                                      _CAUSE_PRIORITY.get(N[E[e["edge_id"]]["source"]]["type"], 2),
                                      int(e["edge_id"][1:])))
    return [e["edge_id"] for e in es[:n]]


def _cause_phrase(pid: str, eids: List[str]) -> str:
    n, e = node_index(pid), edge_index(pid)
    parts = [f"\"{n[e[x]['source']]['label']}\" ({x})" for x in eids]
    return " and ".join(parts) if parts else "no recorded cause"


def _top_surface_edge(pid: str) -> List[str]:
    """Fallback citation when nothing is shared: one edge into the patient's most
    connected surface node."""
    ins = in_edges(pid)
    best = max((n for n in network(pid)["nodes"] if n["type"] in D.SURFACE_TYPES),
               key=lambda n: (len(ins.get(n["id"], [])), -int(n["id"][1:])), default=None)
    if best is None or not ins.get(best["id"]):
        es = network(pid)["edges"]
        return [es[0]["id"]] if es else []
    return [sorted(ins[best["id"]], key=lambda x: int(x[1:]))[0]]


def template_text(q: str, p: str) -> Dict[str, Any]:
    """Deterministic rationale + probe + citations for the pair (q, p)."""
    sheet = _focus_order(contrast_sheet(q, p))
    if not sheet:
        cq, cc = _top_surface_edge(q), _top_surface_edge(p)
        return {"rationale": ("These two patients share no surface presentation at the concept level; "
                              f"the query patient's presentation is driven by {_cause_phrase(q, cq)}, "
                              f"the candidate's by {_cause_phrase(p, cc)}."),
                "probe_question": "What has been the hardest part of things for you lately, and what do you think is behind it?",
                "cited_edges": {"query": cq, "candidate": cc}}
    names = [r["name"] for r in sheet[:3]]
    focus = [r for r in sheet[:2] if r["query"]["edges"] and r["candidate"]["edges"]] or sheet[:1]
    cq_all, cc_all, sents = [], [], []
    for r in focus:
        cq = _pick_edges(q, r["query"]["edges"], 2)
        cc = _pick_edges(p, r["candidate"]["edges"], 2)
        cq_all += cq
        cc_all += cc
        sents.append(f"Behind {r['name'].lower()}, the query patient's network shows {_cause_phrase(q, cq)}, "
                     f"whereas the candidate's shows {_cause_phrase(p, cc)}.")
    rationale = (f"Both patients present with {', '.join(names)}. " + " ".join(sents) +
                 " The shared presentation therefore rests on different upstream causes.")
    f0 = focus[0]
    x = f0["name"].lower()
    if f0["type"] == "Behavior" and x.split()[0].endswith("ing"):
        lead = f"When you find yourself {x}"
    else:
        lead = _PROBE_LEAD.get(f0["type"], "When it comes to {x}").format(x=x)
    probe = f"{lead}, what is usually happening just before, and what goes through your mind at that moment?"
    if not cq_all:
        cq_all = _top_surface_edge(q)
    if not cc_all:
        cc_all = _top_surface_edge(p)
    return {"rationale": rationale, "probe_question": probe,
            "cited_edges": {"query": cq_all, "candidate": cc_all}}


def make_candidate(q: str, p: str, rank: int, text: Dict[str, Any], **extra) -> Dict[str, Any]:
    d = C.mechanism_difference(q, p)
    return {"patient_id": p, "rank": rank,
            "surface_score": round(C.surface_similarity(q, p), 6),
            "mechanism_score": None if d is None else round(d, 6),
            "shared_surface": C.shared_surface(q, p),
            "rationale": text["rationale"], "cited_edges": text["cited_edges"],
            "probe_question": text["probe_question"], **extra}


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------
def _rng(q: str) -> random.Random:
    return random.Random(f"{CONFIG['seed']}:{q}")


def select_R1(q: str, k: int) -> List[str]:
    pool = [p for p in all_pids() if p != q]
    return _rng(q).sample(pool, k)


def select_R2(q: str, k: int) -> Tuple[List[str], str]:
    """Random among patients with the same PRIMARY disorder tag; if fewer than k exist,
    fill from patients sharing ANY disorder tag, then from the whole pool."""
    rng = _rng(q)
    prim, dis = tags(q)["primary_disorder"], set(tags(q)["disorders"])
    pool = [p for p in all_pids() if p != q]
    tier1 = [p for p in pool if tags(p)["primary_disorder"] == prim]
    out = rng.sample(tier1, min(k, len(tier1)))
    how = "primary"
    if len(out) < k:
        tier2 = [p for p in pool if p not in out and set(tags(p)["disorders"]) & dis]
        out += rng.sample(tier2, min(k - len(out), len(tier2)))
        how = "any_tag"
    if len(out) < k:
        rest = [p for p in pool if p not in out]
        out += rng.sample(rest, k - len(out))
        how = "pool"
    return out, how


_ROLEPLAY_KEYS = {"speaking_style", "disclosure_rules", "simulation_rules"}


def _render(v: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(v, dict):
        return "\n".join(f"{pad}{k}:\n{_render(x, indent + 1)}" if isinstance(x, (dict, list))
                         else f"{pad}{k}: {x}" for k, x in v.items())
    if isinstance(v, list):
        return "\n".join(_render(x, indent) if isinstance(x, (dict, list)) else f"{pad}- {x}" for x in v)
    return f"{pad}{v}"


def r3_text(pid: str) -> str:
    """Text embedded for R3: the stage-1 LONG profile (the clinical case the simulator
    role-plays and the networks are generated from), without the role-play-only keys
    (speaking style, disclosure / simulation rules) and without the name."""
    lp = D.load_long_profile(pid)["long_profile"]
    lp = {k: v for k, v in lp.items() if k not in _ROLEPLAY_KEYS}
    if isinstance(lp.get("identity"), dict):
        lp["identity"] = {k: v for k, v in lp["identity"].items() if k != "name"}
    return _render(lp)[:24000]


@lru_cache(maxsize=1)
def _r3_matrix():
    import numpy as np
    from patienttrace.angel_ext.llm import get_client
    pids = list(all_pids())
    vecs = get_client().embed([r3_text(p) for p in pids], tag="f2.embed")
    return pids, np.asarray(vecs)


def select_R3(q: str, k: int) -> List[Tuple[str, float]]:
    import numpy as np
    pids, M = _r3_matrix()
    i = pids.index(q)
    sims = M @ M[i]
    order = [j for j in np.argsort(-sims, kind="stable") if j != i]
    return [(pids[j], float(sims[j])) for j in order[:k]]


def select_R4(q: str, k: int) -> List[str]:
    return [r["patient_id"] for r in filtered_ranked(q)[:k]]


def recommend_baseline(q: str, method: str, k: int = K_DEFAULT) -> Dict[str, Any]:
    t0 = time.time()
    extra: Dict[str, Dict[str, Any]] = {}
    if method == "R1":
        picks = select_R1(q, k)
    elif method == "R2":
        picks, how = select_R2(q, k)
        extra = {p: {"r2_tier": how} for p in picks}
    elif method == "R3":
        res = select_R3(q, k)
        picks = [p for p, _ in res]
        extra = {p: {"profile_cosine": round(s, 6)} for p, s in res}
    elif method == "R4":
        picks = select_R4(q, k)
    else:
        raise ValueError(method)
    cands = [make_candidate(q, p, r, template_text(q, p), **extra.get(p, {}))
             for r, p in enumerate(picks, 1)]
    return {"query_id": q, "method": method, "latency_ms": round((time.time() - t0) * 1000, 1),
            "candidates": cands}


# ---------------------------------------------------------------------------
# Citation validation (ours)
# ---------------------------------------------------------------------------
INLINE = re.compile(r"\[(Q|C):\s*(e\d+)\]")
BARE_ID = re.compile(r"\be\d+\b")
STAT_RE = re.compile(r"\d+\s*/\s*330|\bof 330\b|\b[SD]\s*=\s*0?\.\d")


def validate(q: str, p: str, item: Dict[str, Any]) -> Tuple[Dict[str, List[str]], List[str]]:
    """-> (cited_edges, errors). Hard rules: both sides cite >= 1 edge; every cited id
    exists in the RIGHT network; every inline [Q:eX]/[C:eX] exists on its side (valid
    inline ids are merged into cited_edges); no bare edge id outside the [Q:]/[C:] form;
    >= 1 cited edge per side points into a shared-surface node (a mechanism edge)."""
    errs: List[str] = []
    cited = {"query": [str(x).strip() for x in item.get("query_edges") or []],
             "candidate": [str(x).strip() for x in item.get("candidate_edges") or []]}
    for tag_, eid in INLINE.findall(item.get("rationale", "") or ""):
        who = "query" if tag_ == "Q" else "candidate"
        if eid not in cited[who]:
            cited[who].append(eid)
    stripped = INLINE.sub("", (item.get("rationale", "") or "") + " " + (item.get("probe_question", "") or ""))
    if BARE_ID.search(stripped):
        errs.append("an edge id appears outside the [Q:eX]/[C:eX] citation form")
    idx = {"query": edge_index(q), "candidate": edge_index(p)}
    for who in cited:
        cited[who] = list(dict.fromkeys(cited[who]))
        if not cited[who]:
            errs.append(f"no {who}-side edge cited")
        bad = [e for e in cited[who] if e not in idx[who]]
        if bad:
            errs.append(f"{who}-side ids do not exist in that network: {bad}")
    mech = mechanism_edge_ids(q, p)
    for who in cited:
        if cited[who] and not (set(cited[who]) & mech[who]):
            errs.append(f"no {who}-side citation is an edge into a shared surface feature")
    if CONFIG["prompt"] not in ("v1", "v2") and STAT_RE.search(item.get("rationale", "") or ""):
        errs.append("the rationale quotes a statistic (N/330 prevalence, S or D) that is not part of the networks")
    if not (item.get("rationale") or "").strip():
        errs.append("empty rationale")
    if not (item.get("probe_question") or "").strip():
        errs.append("empty probe question")
    return cited, errs
