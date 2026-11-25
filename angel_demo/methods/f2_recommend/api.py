"""Public API for F2.

    recommend(query_patient_id, method="ours", k=3) -> SPEC §2 F2 record
    recommendation_cards(query_patient_id, method="ours", k=3, precomputed=True) -> card data

The demo should serve PRECOMPUTED ours records (an LLM rerank over 10 candidate networks
takes tens of seconds). `recommendation_cards` first looks for the query in
outputs/f2/<method>/{all,dev,test}.jsonl and only computes live if it is absent.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

from angel_demo.angel_ext import data as D

from . import core as K

OUT_DIR = D.P.data("f2")


def recommend(query_patient_id: str, method: str = "ours", k: int = K.K_DEFAULT) -> Dict[str, Any]:
    q = query_patient_id
    if q not in K.all_pids():
        raise ValueError(f"unknown patient id {q}")
    if method == "ours":
        from .ours import recommend_ours
        return recommend_ours(q, k)
    if method in ("R1", "R2", "R3", "R4"):
        return K.recommend_baseline(q, method, k)
    raise ValueError(f"unknown method {method}; expected one of {K.METHODS}")


@lru_cache(maxsize=16)
def _precomputed(method: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for name in ("dev", "test", "all"):   # later files override earlier ones
        path = os.path.join(OUT_DIR, method, f"{name}.jsonl")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        rec = json.loads(line)
                        out[rec["query_id"]] = rec
    return out


def _node_view(pid: str, nid: str, highlight: bool = False) -> Dict[str, Any]:
    n = K.node_index(pid)[nid]
    c = K.node_concept(pid, nid)
    return {"id": nid, "type": n["type"], "label": n["label"], "concept_id": c,
            "concept_name": K.concept_name(c) if c else None, "highlight": highlight}


def ego_subgraph(pid: str, cited: List[str], focus_nodes: List[str], max_nodes: int = 18) -> Dict[str, Any]:
    """Small subgraph around the cited edges: endpoints of cited edges, the patient's nodes of
    the shared surface concepts, then one-hop in-neighbours of those (causes first) up to
    max_nodes. Edges = all edges among the kept nodes; cited edges flagged."""
    E, N = K.edge_index(pid), K.node_index(pid)
    cited = [e for e in cited if e in E]
    keep: List[str] = []
    for e in cited:
        for x in (E[e]["source"], E[e]["target"]):
            if x not in keep:
                keep.append(x)
    for x in focus_nodes:
        if x not in keep and len(keep) < max_nodes:
            keep.append(x)
    ins = K.in_edges(pid)
    for x in list(keep):
        for e in ins.get(x, []):
            s = E[e]["source"]
            if s not in keep and len(keep) < max_nodes:
                keep.append(s)
    ks = set(keep)
    focus = set(focus_nodes)
    nodes = [_node_view(pid, x, highlight=x in focus) for x in keep]
    edges = [{"id": e["id"], "source": e["source"], "target": e["target"], "label": e["label"],
              "cited": e["id"] in cited}
             for e in K.network(pid)["edges"] if e["source"] in ks and e["target"] in ks]
    return {"patient_id": pid, "nodes": nodes, "edges": edges}


def _resolve(pid: str, side: str, eids: List[str]) -> List[Dict[str, Any]]:
    E, N = K.edge_index(pid), K.node_index(pid)
    out = []
    for e in eids:
        if e in E:
            s, t = N[E[e]["source"]], N[E[e]["target"]]
            out.append({"side": side, "edge_id": e, "valid": True, "source": s["label"],
                        "source_type": s["type"], "target": t["label"], "target_type": t["type"]})
        else:
            out.append({"side": side, "edge_id": e, "valid": False})
    return out


def card_for(q: str, cand: Dict[str, Any]) -> Dict[str, Any]:
    p = cand["patient_id"]
    sheet = K.contrast_sheet(q, p)
    qn, pn = K.node_index(q), K.node_index(p)
    shared = [{"concept_id": r["concept_id"], "name": r["name"], "type": r["type"],
               "prevalence": r["prevalence"],
               "query_nodes": [qn[x]["label"] for x in r["query"]["nodes"]],
               "candidate_nodes": [pn[x]["label"] for x in r["candidate"]["nodes"]]} for r in sheet]
    cited = cand.get("cited_edges") or {}
    # focus nodes = this side's nodes of the shared concepts that the cited edges point into
    tq = {K.edge_index(q)[e]["target"] for e in cited.get("query", []) if e in K.edge_index(q)}
    tp = {K.edge_index(p)[e]["target"] for e in cited.get("candidate", []) if e in K.edge_index(p)}
    fq = [x for r in sheet for x in r["query"]["nodes"] if x in tq]
    fp = [x for r in sheet for x in r["candidate"]["nodes"] if x in tp]
    return {
        "rank": cand["rank"], "patient_id": p, "patient": K.tags(p),
        "surface_score": cand.get("surface_score"), "mechanism_score": cand.get("mechanism_score"),
        "shared_surface": shared,
        "teaching_point": cand.get("teaching_point", ""),
        "rationale": cand.get("rationale", ""),
        "citations": _resolve(q, "query", cited.get("query", [])) + _resolve(p, "candidate", cited.get("candidate", [])),
        "probe_question": cand.get("probe_question", ""),
        "ego_graphs": {"query": ego_subgraph(q, cited.get("query", []), fq),
                       "candidate": ego_subgraph(p, cited.get("candidate", []), fp)},
    }


def recommendation_cards(query_patient_id: str, method: str = "ours", k: int = K.K_DEFAULT,
                         precomputed: bool = True) -> Dict[str, Any]:
    """Everything the 'Find a contrast patient' panel needs, for one query patient."""
    q = query_patient_id
    rec: Optional[Dict[str, Any]] = _precomputed(method).get(q) if precomputed else None
    source = "precomputed" if rec else "live"
    if rec is None:
        rec = recommend(q, method, k)
    return {"query_id": q, "query": K.tags(q), "method": method, "source": source,
            "latency_ms": rec.get("latency_ms"),
            "cards": [card_for(q, c) for c in rec["candidates"][:k]]}
