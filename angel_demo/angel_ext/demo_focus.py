"""Demo-only helpers for the contrast cards' focus symptom (Phase 5, round 8).

* contrast_graph(a, b)       - the ContrastGraph payload (moved here from ext_routes so offline
                                scripts compute exactly what the page shows).
* distinctive_counts(cg)     - per shared concept: number of distinctive cause NODES per side.
* default_focus(cg, target)  - the symptom a card opens on: the probe's stored target concept whenever it
                                is a shared symptom (round 10), otherwise the shared symptom with the most
                                distinctive causes on both sides.
* targets()                  - data/f2_targets.json: {query: {candidate: {target_concept_id, valid}}},
                                written by scripts/p5_f2_targets.py (one LLM call per recommendation,
                                given the frozen probe question). The evaluated F2 outputs are untouched.

Nothing here is used by the evaluation code.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from angel_demo.angel_ext import data as D

TARGETS_PATH = D.P.data("f2_targets.json")


def contrast_graph(a: str, b: str) -> Dict[str, Any]:
    from angel_demo.angel_ext import concept_sd as CSD
    concepts = {c["concept_id"]: c for c in D.load_concepts()[0]}
    n2c = D.load_concepts()[1]
    shared = CSD.shared_surface(a, b)
    sh = set(shared)
    dist = CSD.distinctive_edges(a, b) if a != b else {"a": [], "b": []}
    out_c = [{"concept_id": c, "name": concepts[c]["name"], "type": concepts[c]["type"],
              "nodes": {"a": [], "b": []}} for c in shared]
    cidx = {c["concept_id"]: c for c in out_c}
    sides = {}
    for side, pid in (("a", a), ("b", b)):
        net = D.load_network(pid)
        nodes = {n["id"]: n for n in net["nodes"]}
        centre = {}
        for n in net["nodes"]:
            c = n2c.get(f"{pid}:{n['id']}")
            if c in sh and n["type"] in D.SURFACE_TYPES:
                centre[n["id"]] = c
                cidx[c]["nodes"][side].append({"id": n["id"], "label": n["label"], "type": n["type"]})
        ds = set(dist[side])
        edges, used = [], {}
        for e in net["edges"]:
            if e["target"] not in centre:
                continue
            edges.append({"id": e["id"], "source": e["source"], "target": e["target"],
                          "target_concept": centre[e["target"]],
                          "source_concept": centre.get(e["source"]),
                          "distinctive": e["id"] in ds})
            if e["source"] not in centre:
                used[e["source"]] = nodes[e["source"]]
        sides[side] = {"patient_id": pid, "tags": D.patient_tags(pid),
                       "causes": [{"id": n["id"], "label": n["label"], "type": n["type"]} for n in used.values()],
                       "edges": edges}
    # node -> coarse cause group for every node of both networks (UI: "shared kind of cause")
    c2k = CSD._c2k()
    kinds = {}
    for side, pid in (("a", a), ("b", b)):
        kinds[side] = {}
        for n in D.load_network(pid)["nodes"]:
            c = n2c.get(f"{pid}:{n['id']}")
            if c:
                kinds[side][n["id"]] = c2k.get(c, c)
    return {"patient_a": a, "patient_b": b, "same_patient": a == b, "concepts": out_c, "sides": sides,
            "distinctive": dist, "node_kinds": kinds}


def distinctive_counts(cg: Dict[str, Any]) -> Dict[str, Tuple[int, int]]:
    """concept -> (#distinctive cause nodes of A, of B); a cause is a node outside that concept
    with a distinctive edge into one of the concept's nodes (what the card's column lists as ◆)."""
    out = {}
    for c in cg["concepts"]:
        cid = c["concept_id"]
        n = []
        for s in ("a", "b"):
            n.append(len({e["source"] for e in cg["sides"][s]["edges"]
                          if e["target_concept"] == cid and e["distinctive"] and e.get("source_concept") != cid}))
        out[cid] = (n[0], n[1])
    return out


def default_focus(cg: Dict[str, Any], target: Optional[str]) -> Dict[str, Any]:
    """{concept_id, reason: target|fallback|none, flag: bool, counts}. flag = no shared symptom has
    distinctive causes on both sides (the card cannot show a two-sided contrast)."""
    cnt = distinctive_counts(cg)
    if not cnt:
        return {"concept_id": None, "reason": "none", "flag": True, "counts": {}}
    order = [c["concept_id"] for c in cg["concepts"]]
    both = [c for c in order if min(cnt[c]) > 0]
    # round 10 (user decision 2026-11-11): the stored target concept of the recommendation always wins when it is
    # a shared symptom, even if one patient has no distinctive cause into it (flag marks the one-sided case)
    if target in cnt:
        return {"concept_id": target, "reason": "target", "flag": min(cnt[target]) == 0, "counts": cnt}
    if both:
        best = max(both, key=lambda c: (min(cnt[c]), sum(cnt[c]), -order.index(c)))
        return {"concept_id": best, "reason": "fallback", "flag": False, "counts": cnt}
    # no two-sided contrast anywhere: keep the target if it has any distinctive cause, else the most
    best = target if target in cnt and sum(cnt[target]) > 0 else max(order, key=lambda c: (sum(cnt[c]), -order.index(c)))
    return {"concept_id": best, "reason": "fallback", "flag": True, "counts": cnt}


_T: Dict[str, Any] = {"mtime": None, "data": {}}


def targets() -> Dict[str, Dict[str, Any]]:
    """data/f2_targets.json, re-read when the file changes."""
    try:
        m = os.path.getmtime(TARGETS_PATH)
    except OSError:
        return {}
    if m != _T["mtime"]:
        with open(TARGETS_PATH, encoding="utf-8") as f:
            _T["data"], _T["mtime"] = json.load(f), m
    return _T["data"]


def target_for(q: str, p: str) -> Optional[str]:
    t = targets().get(q, {}).get(p) or {}
    return t.get("target_concept_id") if t.get("valid") else None


# ---- the previous default (for the round-8 report): cited-edge votes, else probe keyword match (ext.js)
_STOP = set("what when would could about your yours that this there their with have having feel feeling feels were "
            "into from they them then than else someone like just does doesn really mean means thing things most more "
            "much very tell".split())


def _words(s: str) -> set:
    return {w[:5] for w in re.split(r"[^a-z]+", (s or "").lower()) if len(w) >= 4 and w not in _STOP}


def keyword_ranked(cg: Dict[str, Any], probe: str) -> List[str]:
    pw = _words(probe)
    score = {}
    for c in cg["concepts"]:
        cw = _words(c["name"] + " " + " ".join(" ".join(n["label"] for n in c["nodes"][s]) for s in ("a", "b")))
        hit = len(cw & pw)
        dist = sum(1 for s in ("a", "b") for e in cg["sides"][s]["edges"] if e["target_concept"] == c["concept_id"] and e["distinctive"])
        allc = sum(1 for s in ("a", "b") for e in cg["sides"][s]["edges"] if e["target_concept"] == c["concept_id"])
        score[c["concept_id"]] = hit * 100 + dist * 2 + allc * 0.1
    order = [c["concept_id"] for c in cg["concepts"]]
    return sorted(order, key=lambda c: -score[c])        # stable, like Array.sort


def old_card_default(cg: Dict[str, Any], cited: Dict[str, List[str]], probe: str) -> Optional[str]:
    votes: Dict[str, int] = {}
    for s, key in (("a", "query"), ("b", "candidate")):
        cs = set(cited.get(key, []))
        for e in cg["sides"][s]["edges"]:
            if e["id"] in cs:
                votes[e["target_concept"]] = votes.get(e["target_concept"], 0) + 1
    if votes:
        m = max(votes.values())
        return next(c for c in votes if votes[c] == m)
    r = keyword_ranked(cg, probe)
    return r[0] if r else None
