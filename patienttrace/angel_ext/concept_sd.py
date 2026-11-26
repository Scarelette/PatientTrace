"""Shared, frozen reference implementation of SPEC §4 surface similarity S and
mechanism difference D over the S0.2 concept vocabulary. Used by F2 (method + eval)
and F3 (eval pair construction). Owned by the main session; do not modify — if a
definition needs to change, ask the main session.

Definitions (SPEC §4, with the choices the SPEC leaves open made explicit):
  * surface(p)  = set of concept_ids of p's nodes whose type ∈ {Emotion, Behavior,
                  Physiological Sensation}
  * causes(p, c) = set of COARSE concept ids (data/concepts/concept_to_coarse.json, S0.2b)
                  of every node with an edge INTO any of p's nodes that map to surface
                  concept c (one hop, any node type — Cognition, Stimulus, and also other
                  surface nodes, e.g. Emotion → Behavior). Causes are compared at the coarse
                  level because with fine concepts two patients almost never share a cause
                  and D saturated at 1.0 (Checkpoint 1 decision, 2026-11-07); surface
                  concepts (S, shared surface) stay fine-grained.
  * S(a, b) = Jaccard(surface(a), surface(b))            (0 if both empty)
  * D(a, b) = mean over c ∈ surface(a) ∩ surface(b) of
              1 − Jaccard(causes(a, c), causes(b, c))
              where Jaccard(∅, ∅) = 1 (no recorded causes on either side = no difference);
              D is None when the two patients share no surface concept.
  * distinctive edges: concept-level edges (coarse(src) → concept(tgt)) whose target
    concept is a shared surface concept and which exist in one patient's network but not
    the other's; mapped back to that patient's edge ids.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from patienttrace.angel_ext import data as D

SURFACE = D.SURFACE_TYPES


@lru_cache(maxsize=1)
def _n2c() -> Dict[str, str]:
    return D.load_concepts()[1]


@lru_cache(maxsize=1)
def _c2k() -> Dict[str, str]:
    import json, os
    with open(os.path.join(D.CONCEPTS_DIR, "concept_to_coarse.json"), encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=512)
def profile(pid: str):
    """-> (surface concepts, {surface concept: frozenset(coarse cause ids)},
           {(coarse src id, tgt concept): [edge ids]})"""
    net = D.load_network(pid)
    n2c = _n2c()
    cid = {n["id"]: n2c.get(f"{pid}:{n['id']}") for n in net["nodes"]}
    ntype = {n["id"]: n["type"] for n in net["nodes"]}
    surface: Set[str] = {cid[n] for n in cid if ntype[n] in SURFACE and cid[n]}
    causes: Dict[str, Set[str]] = {c: set() for c in surface}
    cedges: Dict[Tuple[str, str], List[str]] = {}
    c2k = _c2k()
    for e in net["edges"]:
        s, t = cid.get(e["source"]), cid.get(e["target"])
        if not s or not t:
            continue
        s = c2k.get(s, s)          # cause side at the coarse level
        cedges.setdefault((s, t), []).append(e["id"])
        if ntype[e["target"]] in SURFACE:
            causes[t].add(s)
    return frozenset(surface), {c: frozenset(v) for c, v in causes.items()}, cedges


def _jac(a: FrozenSet[str], b: FrozenSet[str], empty: float) -> float:
    u = a | b
    return len(a & b) / len(u) if u else empty


def surface_similarity(a: str, b: str) -> float:
    return _jac(profile(a)[0], profile(b)[0], 0.0)


def shared_surface(a: str, b: str) -> List[str]:
    return sorted(profile(a)[0] & profile(b)[0])


def mechanism_difference(a: str, b: str) -> Optional[float]:
    sa, ca, _ = profile(a)
    sb, cb, _ = profile(b)
    shared = sa & sb
    if not shared:
        return None
    return sum(1.0 - _jac(ca[c], cb[c], 1.0) for c in shared) / len(shared)


def distinctive_edges(a: str, b: str) -> Dict[str, List[str]]:
    """Edge ids (per patient) of mechanism edges into shared surface concepts that the
    other patient's network lacks at the concept level."""
    sa, _, ea = profile(a)
    sb, _, eb = profile(b)
    shared = sa & sb
    out = {"a": [], "b": []}
    for key, ids in ea.items():
        if key[1] in shared and key not in eb:
            out["a"] += ids
    for key, ids in eb.items():
        if key[1] in shared and key not in ea:
            out["b"] += ids
    return {k: sorted(v, key=lambda x: int(x[1:])) for k, v in out.items()}
