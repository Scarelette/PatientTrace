"""F1 utterance–network alignment: ours, B1, B2, A1, A2 (SPEC §3).

ours  whole network + dialogue context + one structured LLM judgment + programmatic validation
      (SPEC §3 as revised 2026-11-07: retrieval lowered accuracy and speed on ≈48-node networks)
A1    ours without dialogue context (whole network, utterance only)
A2    ours WITH retrieval: retrieved subgraph (top-k nodes by embedding + one-hop neighbours +
      edges incident to a seed node) instead of the whole network
B1    embedding threshold: node matches if cosine(utterance or one of its sentences, node) > θ;
      edge matches if both endpoints match (θ tuned on dev only)
B2    node-only LLM: all nodes + same context; edges inferred when both endpoints selected

Every method returns exactly one SPEC §2 F1 record (plus extra fields patient_id, config,
diag and, on failure, error). All LLM calls go through angel_ext.llm (S0.3).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from angel_demo.angel_ext import data as D
from angel_demo.angel_ext.llm import get_client

from .evidence import cover_span, repair_evidence

METHODS = ("ours", "B1", "B2", "A1", "A2")
LLM_METHODS = ("ours", "B2", "A1", "A2")

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
from angel_demo import paths as _P
CACHE_DIR = _P.state("f1_cache")

# ---------------------------------------------------------------------------
# Configuration (one place; the config hash goes into every record and the ledger)
# ---------------------------------------------------------------------------
CONFIG: Dict[str, Any] = {
    "version": "f1-v3-clean",
    "prompt": os.environ.get("F1_PROMPT", "v3clean"),   # v3clean (frozen) | v3 (round 2) | v4 (round-3 candidate)
    "effort": os.environ.get("F1_EFFORT", "low"),  # same for ours/B2/A1/A2 (chosen on a 30-utt pilot)
    "max_tokens": 6000,          # includes the (always-on) thinking budget
    "k": 12,                     # seed nodes retrieved by embedding
    "context_utts": 6,           # previous utterances shown (3 exchanges incl. the current question); v2 = 4
    "b1_threshold": float(os.environ.get("F1_B1_THRESHOLD", "0.35")),
}


def _struct_prompt() -> str:
    return {"v3": SYSTEM_STRUCT_V3, "v4": SYSTEM_STRUCT_V4}.get(CONFIG["prompt"], SYSTEM_STRUCT_V3_CLEAN)


def config_hash(method: str) -> str:
    cfg = {k: v for k, v in CONFIG.items() if (k == "b1_threshold") == (method == "B1") or k == "version"}
    payload = {"cfg": cfg, "method": method,
               "prompts": [_struct_prompt(), SYSTEM_B2_V3 if CONFIG["prompt"] in ("v3", "v4") else SYSTEM_B2] if method in LLM_METHODS else []}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
# v3 = frozen round-2 prompt (f1-v3). Note: the examples in the short-answer and thoroughness rules
# paraphrase dev utterances (see ledger); v4 = round-3 candidate (fit check + causal link,
# invented examples) -- evaluated in dev round 3 (-2.1 primary), not adopted.
SYSTEM_STRUCT_V3 = """You align one patient utterance from a therapy conversation with that patient's psychological network model.

The network has nodes of five types (Stimulus, Emotion, Cognition, Behavior, Physiological Sensation) and directed edges "source → target" meaning the source causes or influences the target. You are given network items with ids, possibly the preceding dialogue turns, and the CURRENT patient utterance.

Select every node and edge that the CURRENT utterance expresses:
- Node: the patient affirms the node's content as their own experience: the situation happens to them, or they feel, think, do or physically experience it. Paraphrase and clear implicit wording count; past or habitual experience counts.
- Edge: the utterance expresses that the source influences the target IN THAT DIRECTION ("because", "so", "it makes me", "when X I Y", "after X I Y", or a direct answer giving the consequence of a cause the therapist asked about). Two things merely mentioned together are NOT a causal link. If the utterance states the influence the other way round, do not select the edge. When you select an edge, also select both of its endpoint nodes.
- Do not select content that is denied, no longer applies, hypothetical, or attributed only to someone else.
- Preceding turns serve only to interpret the current utterance (pronouns, ellipsis, short answers like "Yes, every night"). Do not select content that appears only in the therapist's question or earlier turns and that the current utterance does not itself affirm.
- Short or elliptical answers: when the current utterance answers a question about an experience, or continues the patient's previous turn, by confirming it, saying how often, since when, where, with whom, or what happens next ("Most nights", "Since last month", "My husband and my daughter", "Yes, it started then"), then the utterance does affirm that experience: select the node(s) for it, quoting the answering words as evidence. Likewise, if the therapist asks what a named cause leads to and the patient states the consequence, that is a causal link from the cause to the consequence.
- Be thorough: check every node type. Feelings and bodily states are often conveyed without being named ("I'm afraid it'll lead to more tests" expresses fear; "all the air leaves my chest" is a physical panic sensation); select them when the wording makes them clear. A specific statement also expresses the more general nodes it falls under, and several near-duplicate nodes may all be selected if each is expressed. For every pair of selected nodes, check whether a listed edge between them is expressed as cause → effect (including "it's easier to X than face Y", "that's why", "ever since Y, I X").
- Precision on details: if a node's label adds a specific qualifier (a place, a time, a person, a frequency, a particular object or a specific reason) that neither the utterance nor the turn it answers supports, do not select that node; a vague statement ("I feel off") does not match a specific node.
- Small talk, logistics and content absent from the network get no items: return no_match true.

Evidence: for each item copy the shortest span of the CURRENT utterance that supports it, character for character (keep the original apostrophes and quotes; no ellipses, no paraphrase). If support comes partly from context, quote the part of the current utterance that answers it (e.g. "Yes, every night").

Output ONLY a JSON object:
{"nodes": [{"node_id": "n3", "evidence": "..."}], "edges": [{"edge_id": "e7", "evidence": "..."}], "no_match": false}
Use only ids listed in the input."""

SYSTEM_STRUCT_V4 = """You align one patient utterance from a therapy conversation with that patient's psychological network model.

The network has nodes of five types (Stimulus, Emotion, Cognition, Behavior, Physiological Sensation) and directed edges "source → target" meaning the source causes or influences the target. You are given network items with ids, possibly the preceding dialogue turns, and the CURRENT patient utterance.

Select every node and edge that the CURRENT utterance expresses:
- Node: the patient affirms the node's content as their own experience: the situation happens to them, or they feel, think, do or physically experience it. Paraphrase and clear implicit wording count; past or habitual experience counts.
- Edge: the utterance expresses that the source influences the target IN THAT DIRECTION ("because", "so", "it makes me", "when X I Y", "after X I Y", or a direct answer giving the consequence of a cause the therapist asked about). Two things merely mentioned together are NOT a causal link. If the utterance states the influence the other way round, do not select the edge. When you select an edge, also select both of its endpoint nodes.
- Do not select content that is denied, no longer applies, hypothetical, or attributed only to someone else.
- Preceding turns serve only to interpret the current utterance (pronouns, ellipsis, short answers like "Yes, every night"). Do not select content that appears only in the therapist's question or earlier turns and that the current utterance does not itself affirm.
- Short or elliptical answers: when the current utterance answers a question about an experience, or continues the patient's previous turn, by confirming it, saying how often, since when, where, with whom, or what happens next ("Pretty much every weekend", "Since the spring", "Mostly with my roommate", "Yes, that's when it began"), then the utterance does affirm that experience: select the node(s) for it, quoting the answering words as evidence. Likewise, if the therapist asks what a named cause leads to and the patient states the consequence, that is a causal link from the cause to the consequence.
- Be thorough: check every node type. Feelings and bodily states are often conveyed without being named ("I dread what the results will say" expresses fear; "my throat closes up and my hands go numb" is a physical panic sensation); select them when the wording makes them clear. A specific statement also expresses the more general nodes it falls under, and several near-duplicate nodes may all be selected if each is expressed. For every pair of selected nodes, check whether a listed edge between them is expressed as cause → effect (including "it's easier to X than face Y", "that's why", "ever since Y, I X").
- Precision on details: if a node's label adds a specific qualifier (a place, a time, a person, a frequency, a particular object or a specific reason) that neither the utterance nor the turn it answers supports, do not select that node; a vague statement ("I feel off") does not match a specific node.
- Small talk, logistics and content absent from the network get no items: return no_match true.

Evidence: for each item copy the shortest span of the CURRENT utterance that supports it, character for character (keep the original apostrophes and quotes; no ellipses, no paraphrase). If support comes partly from context, quote the part of the current utterance that answers it (e.g. "Yes, every night").

Fit check for nodes: for every node you list, set "fit". Read the whole label: besides its core content, a label often carries a qualifier such as a topic or target ("about son", "from strangers"), a place or time ("outside the home", "nightly"), a purpose ("to cope with sadness", "to avoid thoughts"), a trigger or consequence ("triggering trauma memories"), or an interpretation ("as personal attacks").
- "full": the core and every qualifier are expressed by the current utterance (read together with the turn it answers).
- "likely": the core is clearly expressed and the remaining detail is a natural reading of what the patient says (e.g. "I drink every evening" for "regular evening drinking despite health warnings"; "I keep wondering if my coworkers think I'm useless" for "fear of negative evaluation").
- "partial": a qualifier that changes the meaning (where, about whom, why, what triggers it, how it is interpreted) has no support, or only a vaguer version of the core is expressed (e.g. "I hear them muttering" for "hearing voices at night"; "my sister asks why I skip meals" for "skipping meals to control weight").
Partial nodes may be listed but are not used.

Causal link for edges: list every edge whose two ends the utterance expresses and that you consider expressed as cause → effect, and set "link" for each:
- the words of the CURRENT utterance that connect cause and effect, copied verbatim: an explicit connective ("because", "so", "makes me", "that's why", "easier to … than") or a reaction sequence the patient presents as one thing leading to the other ("when … I", "after … I", "and then I", "ever since");
- "ANSWER" when the therapist's question names the cause and the utterance states its consequence (or names the effect and the utterance states its cause);
- "none" when the two are only mentioned or listed side by side ("I'm tired, I'm irritable, I don't go out"). Edges with "none" are not used, and neither are edges with a "partial" endpoint (except that one end of an "ANSWER" link may come from the question).

Output ONLY a JSON object:
{"nodes": [{"node_id": "n3", "evidence": "...", "fit": "full|likely|partial"}], "edges": [{"edge_id": "e7", "evidence": "...", "link": "..."}], "no_match": false}
Use only ids listed in the input. no_match is true when every listed node is "partial" or nothing is listed."""

# v3-clean (FROZEN default): v3 rules unchanged; every illustrative example phrase replaced by an invented
# one, checked to score <= 0.42 similarity against every dev and test patient utterance (hygiene fix).
SYSTEM_STRUCT_V3_CLEAN = """You align one patient utterance from a therapy conversation with that patient's psychological network model.

The network has nodes of five types (Stimulus, Emotion, Cognition, Behavior, Physiological Sensation) and directed edges "source → target" meaning the source causes or influences the target. You are given network items with ids, possibly the preceding dialogue turns, and the CURRENT patient utterance.

Select every node and edge that the CURRENT utterance expresses:
- Node: the patient affirms the node's content as their own experience: the situation happens to them, or they feel, think, do or physically experience it. Paraphrase and clear implicit wording count; past or habitual experience counts.
- Edge: the utterance expresses that the source influences the target IN THAT DIRECTION ("because", "so", "it makes me", "when X I Y", "after X I Y", or a direct answer giving the consequence of a cause the therapist asked about). Two things merely mentioned together are NOT a causal link. If the utterance states the influence the other way round, do not select the edge. When you select an edge, also select both of its endpoint nodes.
- Do not select content that is denied, no longer applies, hypothetical, or attributed only to someone else.
- Preceding turns serve only to interpret the current utterance (pronouns, ellipsis, short answers like "Yes, pretty much every weekend"). Do not select content that appears only in the therapist's question or earlier turns and that the current utterance does not itself affirm.
- Short or elliptical answers: when the current utterance answers a question about an experience, or continues the patient's previous turn, by confirming it, saying how often, since when, where, with whom, or what happens next ("Only on Sundays", "Ever since the layoffs", "Mostly with my roommate", "Yes, that's when it began"), then the utterance does affirm that experience: select the node(s) for it, quoting the answering words as evidence. Likewise, if the therapist asks what a named cause leads to and the patient states the consequence, that is a causal link from the cause to the consequence.
- Be thorough: check every node type. Feelings and bodily states are often conveyed without being named ("I dread what the results will say" expresses fear; "my fingertips tingle and my throat tightens" is a physical panic sensation); select them when the wording makes them clear. A specific statement also expresses the more general nodes it falls under, and several near-duplicate nodes may all be selected if each is expressed. For every pair of selected nodes, check whether a listed edge between them is expressed as cause → effect (including "I'd rather X than risk Y", "that's why", "ever since Y, I X").
- Precision on details: if a node's label adds a specific qualifier (a place, a time, a person, a frequency, a particular object or a specific reason) that neither the utterance nor the turn it answers supports, do not select that node; a vague statement ("things feel kind of meh") does not match a specific node.
- Small talk, logistics and content absent from the network get no items: return no_match true.

Evidence: for each item copy the shortest span of the CURRENT utterance that supports it, character for character (keep the original apostrophes and quotes; no ellipses, no paraphrase). If support comes partly from context, quote the part of the current utterance that answers it (e.g. "Yes, pretty much every weekend").

Output ONLY a JSON object:
{"nodes": [{"node_id": "n3", "evidence": "..."}], "edges": [{"edge_id": "e7", "evidence": "..."}], "no_match": false}
Use only ids listed in the input."""

SYSTEM_STRUCT = SYSTEM_STRUCT_V3_CLEAN


SYSTEM_B2_V3 = """You align one patient utterance from a therapy conversation with the nodes of that patient's psychological network model.

Nodes have five types (Stimulus, Emotion, Cognition, Behavior, Physiological Sensation). You are given all nodes with ids, possibly the preceding dialogue turns, and the CURRENT patient utterance.

Select every node that the CURRENT utterance expresses:
- The patient affirms the node's content as their own experience: the situation happens to them, or they feel, think, do or physically experience it. Paraphrase and clear implicit wording count; past or habitual experience counts.
- Do not select content that is denied, no longer applies, hypothetical, or attributed only to someone else.
- Preceding turns serve only to interpret the current utterance (pronouns, ellipsis, short answers like "Yes, every night"). Do not select content that appears only in the therapist's question or earlier turns and that the current utterance does not itself affirm.
- The utterance must support the node's core meaning: a vague statement ("I feel off") does not match a specific node; a specific statement can match a more general node. Several near-duplicate nodes may all be selected if each is expressed.
- Small talk, logistics and content absent from the network get no nodes: return no_match true.

Evidence: for each node copy the shortest span of the CURRENT utterance that supports it, character for character (keep the original apostrophes and quotes; no ellipses, no paraphrase). If support comes partly from context, quote the part of the current utterance that answers it.

Output ONLY a JSON object:
{"nodes": [{"node_id": "n3", "evidence": "..."}], "no_match": false}
Use only ids listed in the input."""

# B2 v3-clean: same rules, invented example phrases (frozen default)
SYSTEM_B2 = """You align one patient utterance from a therapy conversation with the nodes of that patient's psychological network model.

Nodes have five types (Stimulus, Emotion, Cognition, Behavior, Physiological Sensation). You are given all nodes with ids, possibly the preceding dialogue turns, and the CURRENT patient utterance.

Select every node that the CURRENT utterance expresses:
- The patient affirms the node's content as their own experience: the situation happens to them, or they feel, think, do or physically experience it. Paraphrase and clear implicit wording count; past or habitual experience counts.
- Do not select content that is denied, no longer applies, hypothetical, or attributed only to someone else.
- Preceding turns serve only to interpret the current utterance (pronouns, ellipsis, short answers like "Yes, pretty much every weekend"). Do not select content that appears only in the therapist's question or earlier turns and that the current utterance does not itself affirm.
- The utterance must support the node's core meaning: a vague statement ("things feel kind of meh") does not match a specific node; a specific statement can match a more general node. Several near-duplicate nodes may all be selected if each is expressed.
- Small talk, logistics and content absent from the network get no nodes: return no_match true.

Evidence: for each node copy the shortest span of the CURRENT utterance that supports it, character for character (keep the original apostrophes and quotes; no ellipses, no paraphrase). If support comes partly from context, quote the part of the current utterance that answers it.

Output ONLY a JSON object:
{"nodes": [{"node_id": "n3", "evidence": "..."}], "no_match": false}
Use only ids listed in the input."""


# ---------------------------------------------------------------------------
# Network resources (any patient, or any SPEC-format network dict)
# ---------------------------------------------------------------------------
class NetRes:
    def __init__(self, net: Dict[str, Any], node_ids: Sequence[str], node_vecs: np.ndarray):
        self.net = net
        self.pid = net.get("patient_id", "")
        self.nodes = {n["id"]: n for n in net["nodes"]}
        self.edges = {e["id"]: e for e in net["edges"]}
        self.node_ids = list(node_ids)
        self.node_vecs = np.asarray(node_vecs, dtype="float32")
        self.adj: Dict[str, set] = {n: set() for n in self.nodes}
        self.inc: Dict[str, List[str]] = {n: [] for n in self.nodes}
        for e in net["edges"]:
            s, t = e["source"], e["target"]
            if s in self.adj and t in self.adj:
                self.adj[s].add(t)
                self.adj[t].add(s)
                self.inc[s].append(e["id"])
                self.inc[t].append(e["id"])


@lru_cache(maxsize=512)
def net_res(pid: str) -> NetRes:
    net = D.load_network(pid)
    try:
        nids, nvecs, _, _ = D.load_embeddings(pid)
        nids = [str(x) for x in nids]
    except FileNotFoundError:
        # Packaged demo: the data bundle has no embeddings. The frozen method ("ours") never
        # reads them (no retrieval); only the A2/B1 ablations do, and they are not part of the demo.
        nids, nvecs = [n["id"] for n in net["nodes"]], np.zeros((len(net["nodes"]), 0), dtype="float32")
    return NetRes(net, nids, nvecs)


def net_res_from_network(net: Dict[str, Any]) -> NetRes:
    """For a network dict (e.g. a live, freshly generated one): use precomputed embeddings if
    this is a known patient with the same node set, otherwise embed node texts (cached)."""
    pid = net.get("patient_id")
    if pid:
        try:
            r = net_res(pid)
            if set(r.nodes) == {n["id"] for n in net["nodes"]} and \
                    all(r.nodes[n["id"]]["label"] == n["label"] for n in net["nodes"]):
                return r
        except Exception:  # noqa: BLE001
            pass
    nids, nvecs = _embed_nodes(net)
    return NetRes(net, nids, nvecs)


def _embed_nodes(net: Dict[str, Any]):
    texts = [D.node_text(n) for n in net["nodes"]]
    vecs = get_client().embed(texts, tag="f1.embed_nodes")
    return [n["id"] for n in net["nodes"]], vecs


# ---------------------------------------------------------------------------
# Timed embeddings (cached-latency aware, like LLMResult.latency_ms on cache hits)
# ---------------------------------------------------------------------------
_LAT_LOCK = threading.Lock()
_LAT: Optional[Dict[str, float]] = None
_LAT_PATH = os.path.join(CACHE_DIR, "embed_latency_by_text.jsonl")


def _lat_table() -> Dict[str, float]:
    global _LAT
    if _LAT is None:
        _LAT = {}
        if os.path.exists(_LAT_PATH):
            with open(_LAT_PATH, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        _LAT[r["k"]] = r["ms"]
                    except Exception:  # noqa: BLE001
                        pass
    return _LAT


def _tkey(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:24]


def embed_timed(texts: List[str], tag: str) -> Tuple[np.ndarray, float, float]:
    """Embed via the S0.3 wrapper; returns (vecs, latency_ms, wall_ms).
    The wrapper caches embeddings per text, so we remember, per text, the wall time of the call
    that first embedded it (uncached). If any text is new, this call is a real API call and its
    wall time is reported; if all texts were embedded before, the max remembered latency is
    reported, so cached reruns (and methods re-using another method's embeddings) do not fake
    low latency."""
    keys = [_tkey(t) for t in texts]
    with _LAT_LOCK:
        tab = _lat_table()
        known = [tab.get(k) for k in keys]
    t0 = time.time()
    vecs = get_client().embed(texts, tag=tag)
    wall = (time.time() - t0) * 1000
    if all(v is not None for v in known):
        return vecs, max(known), wall
    with _LAT_LOCK:
        tab = _lat_table()
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_LAT_PATH, "a", encoding="utf-8") as f:
            for k, v in zip(keys, known):
                if v is None and k not in tab:
                    tab[k] = round(wall, 1)
                    f.write(json.dumps({"k": k, "ms": round(wall, 1)}) + "\n")
    return vecs, wall, wall


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------
def _ctx_norm(context: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    out = []
    for c in context or []:
        txt = c.get("content", c.get("text", "")) or ""
        role = (c.get("role") or "").lower()
        role = "therapist" if role in ("therapist", "user", "clinician") else "patient"
        out.append({"role": role, "content": str(txt)})
    return out


def _last_question(ctx: List[Dict[str, str]]) -> Optional[str]:
    if ctx and ctx[-1]["role"] == "therapist" and ctx[-1]["content"].strip():
        return ctx[-1]["content"].strip()
    return None


def _render_context(ctx: List[Dict[str, str]], n: int) -> str:
    sel = ctx[-n:] if n > 0 else []
    if not sel:
        return "(no preceding turns)"
    lines = []
    for i, c in enumerate(sel):
        tag = "Therapist" if c["role"] == "therapist" else "Patient"
        if i == len(sel) - 1 and c["role"] == "therapist":
            tag += " (question being answered)"
        lines.append(f"{tag}: {c['content']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def retrieve(res: NetRes, utterance: str, question: Optional[str], k: int
             ) -> Tuple[List[str], List[str], float, Dict[str, Any]]:
    """Top-k seed nodes by max cosine over query views (utterance; question+utterance when a
    question is available), + one-hop neighbours, + every edge incident to a seed node."""
    qtexts = [utterance]
    if question:
        qtexts.append(f"{question}\n{utterance}")
    V, lat, wall = embed_timed(qtexts, tag="f1.embed")
    sims = (res.node_vecs @ V.T).max(axis=1)
    order = np.argsort(-sims)[:k]
    seeds = [res.node_ids[i] for i in order]
    nodes = set(seeds)
    for s in seeds:
        nodes |= res.adj.get(s, set())
    edges = set()
    for s in seeds:
        edges.update(res.inc.get(s, []))
    # reported latency adjustment: remembered (original) embed latency minus this call's wall time
    return _sort_ids(nodes), _sort_ids(edges), lat - wall, {"n_seed": len(seeds), "n_nodes": len(nodes),
                                                            "n_edges": len(edges)}


def _id_key(x: str):
    return (x[:1], int(re.sub(r"\D", "", x) or 0), x)


def _sort_ids(ids) -> List[str]:
    return sorted(ids, key=_id_key)


def _render_items(res: NetRes, node_ids: List[str], edge_ids: Optional[List[str]]) -> str:
    L = ["Nodes:"]
    for nid in node_ids:
        n = res.nodes[nid]
        L.append(f"{nid} [{n['type']}] {n['label']}")
    if edge_ids is not None:
        L.append("")
        L.append("Edges (source → target):")
        for eid in edge_ids:
            e = res.edges[eid]
            L.append(f"{eid}: {e['source']} → {e['target']} | {res.nodes[e['source']]['label']} → "
                     f"{res.nodes[e['target']]['label']}")
        if not edge_ids:
            L.append("(none)")
    return "\n".join(L)


def _user_prompt(items: str, ctx_block: Optional[str], utterance: str) -> str:
    parts = ["## Network items", items, ""]
    if ctx_block is not None:
        parts += ["## Preceding turns", ctx_block, ""]
    parts += ["## CURRENT patient utterance", f'"""{utterance}"""', "", "Return the JSON object now."]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LLM call + validation
# ---------------------------------------------------------------------------
def _call(system: str, user: str, method: str) -> Tuple[Optional[dict], float, int, int, Optional[str], int]:
    """-> (json or None, latency_ms, tok_in, tok_out, error, n_calls). One resample on parse failure."""
    llm = get_client()
    lat = 0.0
    tin = tout = 0
    err = None
    n = 0
    for sample in range(2):
        try:
            r = llm.chat(llm.method_model, user=user, system=system, json_mode=True,
                         max_tokens=CONFIG["max_tokens"], effort=CONFIG["effort"],
                         tag=f"f1.{method}", sample_idx=sample)
        except Exception as exc:  # noqa: BLE001  (e.g. a content-filter 400)
            return None, lat, tin, tout, f"{type(exc).__name__}: {str(exc)[:300]}", n
        n += 1
        lat += r.latency_ms
        tin += r.tokens_in
        tout += r.tokens_out
        if isinstance(r.json, dict):
            return r.json, lat, tin, tout, None, n
        err = f"unparseable output: {r.json_error or type(r.json).__name__}"
    return None, lat, tin, tout, err, n


def _items(raw: Any, key: str, idk: str) -> List[Tuple[str, Any, Dict[str, Any]]]:
    out = []
    for it in (raw.get(key) or []) if isinstance(raw, dict) else []:
        if isinstance(it, dict):
            iid = it.get(idk, it.get("id"))
            out.append((str(iid) if iid is not None else "", it.get("evidence"), it))
        elif isinstance(it, str):
            out.append((it, None, {}))
    return out


def _link_ok(link: Any, utterance: str) -> bool:
    """An edge's causal link must be 'ANSWER' or (repairable to) words of the utterance."""
    if not isinstance(link, str) or not link.strip() or link.strip().lower() in ("none", "null", "n/a"):
        return False
    if link.strip().upper() == "ANSWER":
        return True
    pieces = [p for p in re.split(r"\.\.\.|\u2026", link) if p.strip()]
    return any(repair_evidence(p.strip(), utterance)[0] is not None for p in pieces)


def validate(res: NetRes, utterance: str, raw: dict, allowed_nodes: set, allowed_edges: set,
             infer_edges: bool = False) -> Tuple[List[dict], List[dict], Dict[str, int]]:
    """SPEC §3 step 3: ids exist (and were shown), evidence repaired to an exact substring
    (else the item is dropped), edge endpoints made consistent (missing endpoints are added
    with the edge's evidence). With infer_edges (B2): every network edge whose endpoints are
    both selected, evidence = smallest span covering both endpoint spans."""
    diag = {"bad_id": 0, "ev_exact": 0, "ev_repaired": 0, "ev_dropped": 0, "dup": 0, "endpoints_added": 0,
            "partial_dropped": 0, "no_link_dropped": 0, "partial_endpoint_dropped": 0}
    nodes: Dict[str, str] = {}
    edges: Dict[str, str] = {}
    partial: set = set()
    for key, idk, allowed, store in (("nodes", "node_id", allowed_nodes, nodes),
                                     ("edges", "edge_id", allowed_edges, edges)):
        if infer_edges and key == "edges":
            continue
        for iid, ev, it in _items(raw, key, idk):
            if iid not in allowed:
                diag["bad_id"] += 1
                continue
            if iid in store:
                diag["dup"] += 1
                continue
            # v4 fit check: nodes the model itself rated "partial" are not used
            if key == "nodes" and CONFIG["prompt"] == "v4" and str(it.get("fit", "full")).strip().lower() == "partial":
                partial.add(iid)
                diag["partial_dropped"] += 1
                continue
            # v4 causal-link check: an edge must name the words (or the Q→A) expressing the influence
            if key == "edges" and not infer_edges and CONFIG["prompt"] == "v4":
                e = res.edges[iid]
                if not _link_ok(it.get("link"), utterance):
                    diag["no_link_dropped"] += 1
                    continue
                is_answer = str(it.get("link", "")).strip().upper() == "ANSWER"
                bad_ends = {x for x in (e["source"], e["target"]) if x in partial}
                # an ANSWER link may take its cause from the question: a partial source is allowed
                if bad_ends and not (is_answer and bad_ends <= {e["source"], e["target"]} and len(bad_ends) == 1):
                    diag["partial_endpoint_dropped"] += 1
                    continue
            span, st = repair_evidence(ev, utterance)
            if span is None:
                diag["ev_dropped"] += 1
                continue
            diag["ev_exact" if st == "exact" else "ev_repaired"] += 1
            store[iid] = span
    if infer_edges:
        for eid, e in res.edges.items():
            if e["source"] in nodes and e["target"] in nodes:
                edges[eid] = cover_span(nodes[e["source"]], nodes[e["target"]], utterance)
    else:
        for eid, ev in edges.items():
            e = res.edges[eid]
            for end in (e["source"], e["target"]):
                if end not in nodes:
                    nodes[end] = ev
                    diag["endpoints_added"] += 1
    return ([{"node_id": k, "evidence": nodes[k]} for k in _sort_ids(nodes)],
            [{"edge_id": k, "evidence": edges[k]} for k in _sort_ids(edges)],
            diag)


# ---------------------------------------------------------------------------
# Methods
# ---------------------------------------------------------------------------
def _record(uid, method, pid, nodes, edges, lat, tin, tout, diag, error=None) -> Dict[str, Any]:
    rec = {"utterance_id": uid, "method": method, "nodes": nodes, "edges": edges,
           "no_match": not nodes and not edges, "latency_ms": round(lat, 1),
           "tokens": {"in": int(tin), "out": int(tout)},
           "patient_id": pid, "config": config_hash(method), "diag": diag}
    if error:
        rec["error"] = error
    return rec


def _align_struct(res: NetRes, utterance: str, ctx: List[Dict[str, str]], method: str
                  ) -> Tuple[List[dict], List[dict], float, int, int, Dict[str, Any], Optional[str]]:
    t0 = time.time()
    use_ctx = method in ("ours", "A2", "B2")   # A1 = no context
    question = _last_question(ctx) if use_ctx else None
    emb_adj = 0.0
    diag: Dict[str, Any] = {}
    if method == "A2":                         # retrieval ablation
        node_ids, edge_ids, emb_adj, rdiag = retrieve(res, utterance, question, CONFIG["k"])
        diag.update(rdiag)
    else:
        node_ids = _sort_ids(res.nodes)
        edge_ids = _sort_ids(res.edges)
    ctx_block = _render_context(ctx, CONFIG["context_utts"]) if use_ctx else None
    if method == "B2":
        system, user = (SYSTEM_B2_V3 if CONFIG["prompt"] in ("v3", "v4") else SYSTEM_B2), _user_prompt(_render_items(res, node_ids, None), ctx_block, utterance)
    else:
        system = _struct_prompt()
        user = _user_prompt(_render_items(res, node_ids, edge_ids), ctx_block, utterance)
    prep_ms = (time.time() - t0) * 1000
    raw, llm_lat, tin, tout, err, ncalls = _call(system, user, method)
    t1 = time.time()
    diag["llm_calls"] = ncalls
    diag["retrieval_ms"] = round(prep_ms + emb_adj, 1)
    diag["llm_ms"] = round(llm_lat, 1)
    if raw is None:
        return [], [], prep_ms + emb_adj + llm_lat, tin, tout, diag, err
    nodes, edges, vdiag = validate(res, utterance, raw, set(node_ids),
                                   set(edge_ids) if method != "B2" else set(), infer_edges=(method == "B2"))
    diag.update(vdiag)
    if raw.get("no_match") and (nodes or edges):
        diag["llm_no_match_with_items"] = 1
    post_ms = (time.time() - t1) * 1000
    # prep_ms contains this call's embedding wall time; emb_adj swaps it for the original latency
    total = prep_ms + emb_adj + post_ms + llm_lat
    return nodes, edges, total, tin, tout, diag, None


def _sentences(utterance: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", utterance.strip()) if p.strip()]
    return parts if len(parts) > 1 else []


def b1_scores(res: NetRes, utterance: str) -> Tuple[np.ndarray, List[str], np.ndarray, float]:
    """-> (node score = max cosine over [utterance] + sentences, view texts, argmax view per
    node, latency_ms)."""
    views = [utterance.strip()] + _sentences(utterance)
    V, lat, wall = embed_timed(views, tag="f1.B1")
    S = res.node_vecs @ V.T
    return S.max(axis=1), views, S.argmax(axis=1), lat - wall


def _align_b1(res: NetRes, utterance: str, threshold: float):
    t0 = time.time()
    score, views, arg, emb_adj = b1_scores(res, utterance)
    nodes: Dict[str, str] = {}
    for i in np.where(score > threshold)[0]:
        nodes[res.node_ids[i]] = views[arg[i]]
    edges = {eid: cover_span(nodes[e["source"]], nodes[e["target"]], utterance)
             for eid, e in res.edges.items() if e["source"] in nodes and e["target"] in nodes}
    wall = (time.time() - t0) * 1000 + emb_adj
    return ([{"node_id": k, "evidence": nodes[k]} for k in _sort_ids(nodes)],
            [{"edge_id": k, "evidence": edges[k]} for k in _sort_ids(edges)],
            wall, {"threshold": threshold, "max_score": round(float(score.max()), 4) if len(score) else None})


def align_res(res: NetRes, utterance: str, context: Optional[List[Dict[str, Any]]] = None,
              method: str = "ours", utterance_id: Optional[str] = None,
              b1_threshold: Optional[float] = None) -> Dict[str, Any]:
    if method not in METHODS:
        raise ValueError(f"unknown F1 method {method!r}; expected one of {METHODS}")
    uid = utterance_id or f"{res.pid}.adhoc.{hashlib.sha256(utterance.encode()).hexdigest()[:10]}"
    if not (utterance or "").strip():
        return _record(uid, method, res.pid, [], [], 0.0, 0, 0, {"empty_utterance": 1})
    ctx = _ctx_norm(context)
    try:
        if method == "B1":
            th = CONFIG["b1_threshold"] if b1_threshold is None else b1_threshold
            nodes, edges, lat, diag = _align_b1(res, utterance, th)
            return _record(uid, method, res.pid, nodes, edges, lat, 0, 0, diag)
        nodes, edges, lat, tin, tout, diag, err = _align_struct(res, utterance, ctx, method)
        return _record(uid, method, res.pid, nodes, edges, lat, tin, tout, diag, err)
    except Exception as exc:  # noqa: BLE001  never crash a batch / the frontend
        return _record(uid, method, res.pid, [], [], 0.0, 0, 0, {}, f"{type(exc).__name__}: {str(exc)[:300]}")
