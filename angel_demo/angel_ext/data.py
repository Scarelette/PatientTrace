"""Shared data access: patient ids, profiles, networks in the SPEC §2 format.

Patient id = "p" + zero-padded line index in data/patients_adult.jsonl (the 330
case library the demo's picker lists, in the same order; the demo addresses
profiles by this index via /api/existing_profiles/{idx}).

The demo's network generator (network_model.generate_network) identifies nodes by
NAME and edges by (from-name, to-name), with no ids or descriptions. `to_spec_network`
converts that to the SPEC §2 contract with stable ids:
  node id  = "n{k}"  in the generator's node order
  edge id  = "e{k}"  in the generator's edge order
Extra fields kept from the generator: edge "evidence", "confidence", "verify_score".
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from angel_demo import paths as P

REPO_ROOT = P.DATA_DIR   # kept name: other modules join data paths onto it

PROFILES_PATH = P.data("patients_adult.jsonl")
PRECOMP_DIR = P.data("precomputed")
SPLITS_PATH = P.data("splits.json")
CONCEPTS_DIR = P.data("concepts")
DIALOGUES_DIR = P.data("dialogues")

NODE_TYPES = ["Stimulus", "Emotion", "Cognition", "Behavior", "Physiological Sensation"]
SURFACE_TYPES = {"Emotion", "Behavior", "Physiological Sensation"}


def pid_of(idx: int) -> str:
    return f"p{idx:03d}"


def idx_of(pid: str) -> int:
    return int(pid.lstrip("p"))


_PROFILES: Optional[List[Dict[str, Any]]] = None


def short_profiles() -> List[Dict[str, Any]]:
    global _PROFILES
    if _PROFILES is None:
        with open(PROFILES_PATH, encoding="utf-8") as f:
            _PROFILES = [json.loads(l) for l in f if l.strip()]
    return _PROFILES


def all_patient_ids() -> List[str]:
    return [pid_of(i) for i in range(len(short_profiles()))]


def primary_disorder(profile: Dict[str, Any]) -> str:
    from angel_demo import profile_meta as pm
    return pm.disorders_of(profile)[0]


def patient_tags(pid: str) -> Dict[str, Any]:
    from angel_demo import profile_meta as pm
    p = short_profiles()[idx_of(pid)]
    ident = p.get("identity", {}) or {}
    return {"patient_id": pid, "name": pm.display_name(idx_of(pid), p),
            "gender": pm.norm_gender(ident.get("gender")), "age_bucket": pm.age_bucket(ident.get("age")),
            "culture": pm.infer_culture(p) or "Unspecified", "disorders": pm.disorders_of(p),
            "primary_disorder": pm.disorders_of(p)[0]}


def to_spec_network(pid: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    nodes, name2id = [], {}
    for k, n in enumerate(raw.get("nodes") or []):
        nid = f"n{k + 1}"
        name2id[n["name"]] = nid
        nodes.append({"id": nid, "type": n["type"], "label": n["name"], "description": ""})
    edges = []
    for e in raw.get("edges") or []:
        s, t = name2id.get(e["from"]), name2id.get(e["to"])
        if s is None or t is None:
            continue
        edges.append({"id": f"e{len(edges) + 1}", "source": s, "target": t,
                      "label": f"{e['from']} → {e['to']}", "evidence": e.get("evidence", ""),
                      "confidence": e.get("confidence"), "verify_score": e.get("verify_score")})
    return {"patient_id": pid, "nodes": nodes, "edges": edges}


# ---- precomputed artefacts (S0.1) ------------------------------------------
def precomp_path(pid: str, name: str) -> str:
    return os.path.join(PRECOMP_DIR, pid, name)


def load_network(pid: str) -> Dict[str, Any]:
    with open(precomp_path(pid, "network.json"), encoding="utf-8") as f:
        return json.load(f)


def load_long_profile(pid: str) -> Dict[str, Any]:
    with open(precomp_path(pid, "long_profile.json"), encoding="utf-8") as f:
        return json.load(f)


def load_embeddings(pid: str):
    """-> (node_ids, node_vecs [n,d], edge_ids, edge_vecs [m,d]); vectors L2-normalised."""
    import numpy as np
    z = np.load(precomp_path(pid, "embeddings.npz"), allow_pickle=False)
    return list(z["node_ids"]), z["node_vecs"], list(z["edge_ids"]), z["edge_vecs"]


def load_splits() -> Dict[str, List[str]]:
    with open(SPLITS_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_concepts():
    with open(os.path.join(CONCEPTS_DIR, "concepts.json"), encoding="utf-8") as f:
        concepts = json.load(f)
    with open(os.path.join(CONCEPTS_DIR, "node_to_concept.json"), encoding="utf-8") as f:
        n2c = json.load(f)
    return concepts, n2c


def load_dialogue(split: str, pid: str) -> Dict[str, Any]:
    with open(os.path.join(DIALOGUES_DIR, split, f"{pid}.json"), encoding="utf-8") as f:
        return json.load(f)


def node_text(n: Dict[str, Any]) -> str:
    """Text used to embed a node."""
    return f"{n['type']}: {n['label']}"


def edge_text(e: Dict[str, Any], nodes_by_id: Dict[str, Dict[str, Any]]) -> str:
    """Text used to embed an edge (cause → effect)."""
    a, b = nodes_by_id[e["source"]], nodes_by_id[e["target"]]
    return f"{a['label']} ({a['type']}) leads to {b['label']} ({b['type']})"
