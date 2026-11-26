"""High-quality network-model (DAG) generation from a long-profile JSON, using
claude-fable-5.

Quality-first design: ONE profile at a time, but MANY Claude calls per profile,
several of them in parallel. The goal is a correct, verified, acyclic symptom
network (a DAG), not throughput.

Per-profile pipeline:
  1. Node refinement  — 5 PARALLEL calls, one per taxonomy. Each specialist
     cleans/atomizes/validates the seed nodes of its type taken from the
     profile fields.
  2. Edge generation  — 1 call proposing directed causal edges (cause -> effect)
     among the validated nodes, with evidence + confidence.
  3. Edge verification — PARALLEL batched calls scoring each candidate edge 0-1
     for support by the profile; edges below threshold are dropped.
  4. Direction resolution — for RECIPROCAL pairs (both A->B and B->A proposed),
     one PARALLEL LLM call each decides: a_to_b, b_to_a, both (a mutual-
     reinforcement loop, kept), or neither. Cycles are allowed on purpose —
     they represent constructs that contribute to each other.

Only "right" nodes/edges are produced (no distractor / wrong-edge generation).

Node taxonomy: Behavior, Emotion, Physiological Sensation, Cognition, Stimulus.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

NODE_TYPES = ["Behavior", "Emotion", "Physiological Sensation", "Cognition", "Stimulus"]

TYPE_DEFINITIONS = {
    "Behavior": "actions the patient takes or omits (e.g., avoidance, withdrawal, checking, substance use). Discrete; start and stop.",
    "Emotion": "subjective feelings, usually a single emotion word (e.g., sadness, anger, shame, fear, guilt).",
    "Physiological Sensation": "internal bodily sensations (e.g., fast heartbeat, tightness in chest, fatigue, insomnia, nausea, shortness of breath).",
    "Cognition": "thoughts, beliefs, appraisals, worries, interpretations (e.g., 'I am about to lose control', 'people can't be trusted').",
    "Stimulus": "an EXTERNAL event or situation perceived by the patient — an adverse life event or trigger (e.g., job loss, a partner's late-night calls, a move).",
}

DEFAULT_MODEL = "claude-fable-5"

# ---------------------------------------------------------------------------
# Multi-provider LLM dispatch (speed): route each phase to a model by name.
#   * models starting with "gpt" -> Azure OpenAI (fast; ~3x quicker than fable-5)
#   * everything else            -> the Anthropic endpoint (claude-fable-5)
# Per-phase model choice (env-overridable) lets the mechanical phases run on the
# fast GPT model while edge generation can stay on a stronger model if desired.
# ---------------------------------------------------------------------------
FAST_MODEL = os.environ.get("DEMO_FAST_MODEL", "gpt-4o")

# GPT models for building networks of NEW profiles (off in the packaged demo, DEMO_FEAT_CUSTOM=0)
# come from an Azure OpenAI resource: DEMO_GPT_ENDPOINT + DEMO_GPT_KEY.
_GPT_ENDPOINT = os.environ.get("DEMO_GPT_ENDPOINT", "")
_GPT_API_KEY = os.environ.get("DEMO_GPT_KEY", "")
_GPT_API_VERSION = os.environ.get("DEMO_GPT_API_VERSION", "2024-12-01-preview")
_GPT5_REASONING = os.environ.get("DEMO_GPT5_REASONING", "low")  # reasoning_effort for gpt-5*

_GPT_SEM = threading.Semaphore(int(os.environ.get("DEMO_GPT_CONCURRENCY", "12")))
_CLAUDE_SEM = threading.Semaphore(int(os.environ.get("DEMO_CLAUDE_CONCURRENCY", "8")))

_ANTHROPIC = None
_AZURE = None
_CLIENT_LOCK = threading.Lock()


def _anthropic_client():
    global _ANTHROPIC
    if _ANTHROPIC is None:
        with _CLIENT_LOCK:
            if _ANTHROPIC is None:
                import anthropic
                _ANTHROPIC = anthropic.Anthropic()
    return _ANTHROPIC


def _azure_client():
    global _AZURE
    if _AZURE is None:
        with _CLIENT_LOCK:
            if _AZURE is None:
                from openai import AzureOpenAI
                _AZURE = AzureOpenAI(api_version=_GPT_API_VERSION,
                                     azure_endpoint=_GPT_ENDPOINT, api_key=_GPT_API_KEY)
    return _AZURE


def _is_gpt(model: str) -> bool:
    return model.lower().startswith("gpt")


def _llm(model: str, system: str, user: str, max_tokens: int = 3072, max_retries: int = 5) -> str:
    """Provider-dispatching chat call. Returns the assistant text."""
    last: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            if _is_gpt(model):
                msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
                if model.lower().startswith("gpt-5"):
                    # reasoning model: budget must cover reasoning tokens; no custom temperature
                    kw = {"max_completion_tokens": max(max_tokens, 6000)}
                    if _GPT5_REASONING:
                        kw["reasoning_effort"] = _GPT5_REASONING
                else:
                    kw = {"max_tokens": max_tokens, "temperature": 0.4}
                with _GPT_SEM:
                    r = _azure_client().chat.completions.create(model=model, messages=msgs, **kw)
                return r.choices[0].message.content or ""
            with _CLAUDE_SEM:
                m = _anthropic_client().messages.create(
                    model=model, max_tokens=max_tokens, system=system,
                    messages=[{"role": "user", "content": user}],
                )
            return "".join(b.text for b in m.content if getattr(b, "type", None) == "text")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"LLM call failed ({model}) after {max_retries} retries: {last}")

# ---------------------------------------------------------------------------
# Seed-node collection (verbatim from the profile's structured fields)
# ---------------------------------------------------------------------------
# (dotted path, taxonomy). Covers the rich 14-key schema and falls back to the
# adapted 11-key schema. Missing paths are skipped.
NODE_FIELD_MAP: List[Tuple[str, str]] = [
    ("triggers", "Stimulus"),
    ("symptom_details.triggers", "Stimulus"),
    ("risk_and_protective_factors.risk_factors", "Stimulus"),
    ("emotional_profile.dominant_emotions", "Emotion"),
    ("emotional_profile.shame_or_fear_points", "Emotion"),
    ("emotions", "Emotion"),
    ("cognitive_patterns.core_beliefs", "Cognition"),
    ("cognitive_patterns.automatic_thoughts", "Cognition"),
    ("cognitive_patterns.worries_or_ruminations", "Cognition"),
    ("cognitive_patterns.interpretation_biases", "Cognition"),
    ("hidden_state", "Cognition"),  # 11-key short profile: hidden concerns / core beliefs (a list)
    ("behavior_patterns.avoidance_behaviors", "Behavior"),
    ("behavior_patterns.safety_behaviors", "Behavior"),
    ("behavior_patterns.coping_strategies", "Behavior"),
    ("behavior_patterns.interpersonal_patterns", "Behavior"),
    ("behaviors", "Behavior"),
    ("symptom_details.functional_impairment", "Behavior"),
    ("presenting_problems", "Physiological Sensation"),  # mixed; specialist re-types
]


def _get_path(obj: Dict[str, Any], dotted: str) -> Any:
    cur: Any = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def collect_seed_nodes(profile: Dict[str, Any]) -> Dict[str, List[str]]:
    """Seed candidate node strings grouped by taxonomy (deduped within a type)."""
    seeds: Dict[str, List[str]] = {t: [] for t in NODE_TYPES}
    seen: Dict[str, set] = {t: set() for t in NODE_TYPES}
    # presenting_problems seed goes to a general bucket so every specialist sees it
    general: List[str] = []
    for dotted, type_hint in NODE_FIELD_MAP:
        for item in _as_list(_get_path(profile, dotted)):
            key = _norm(item)
            if not key:
                continue
            if dotted == "presenting_problems":
                if item not in general:
                    general.append(item)
                continue
            if key in seen[type_hint]:
                continue
            seen[type_hint].add(key)
            seeds[type_hint].append(item)
    seeds["_general"] = general
    return seeds


# ---------------------------------------------------------------------------
# Profile context (evidence)
# ---------------------------------------------------------------------------
def render_context(profile: Dict[str, Any]) -> str:
    parts: List[str] = []

    def add(title: str, value: Any) -> None:
        items = _as_list(value)
        if items:
            parts.append(title + ":\n" + "\n".join(f"- {x}" for x in items))
        elif isinstance(value, str) and value.strip():
            parts.append(f"{title}: {value.strip()}")

    add("Background", profile.get("background"))
    add("Presenting problems", profile.get("presenting_problems"))
    sd = profile.get("symptom_details") or {}
    if isinstance(sd, dict):
        for k in ("onset_and_course", "frequency_and_intensity", "maintaining_factors"):
            add(f"Symptom detail ({k.replace('_', ' ')})", sd.get(k))
    ep = profile.get("emotional_profile") or {}
    if isinstance(ep, dict):
        add("Typical reaction when distressed", ep.get("typical_reaction_when_distressed"))
    sfc = profile.get("social_and_family_context")
    if isinstance(sfc, dict):
        for k, v in sfc.items():
            add(f"Context ({k.replace('_', ' ')})", v)
    else:
        add("Family / social context", sfc or profile.get("family_context"))
    add("Brief summary", profile.get("brief_summary"))
    return "\n\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Claude call + JSON extraction
# ---------------------------------------------------------------------------
def _extract_json(text: str, tag: Optional[str] = None) -> Any:
    """Extract a JSON value. If `tag` given, look inside <tag>...</tag> first;
    otherwise (or on miss) take the first balanced {...} or [...] value."""
    raw = None
    if tag:
        m = re.search(rf"<{tag}>\s*(?P<body>[\[{{].*?[\]}}])\s*</{tag}>", text, re.S)
        if m:
            raw = m.group("body")
    if raw is None:
        for opener, closer in (("{", "}"), ("[", "]")):
            start = text.find(opener)
            if start == -1:
                continue
            depth = 0
            for i in range(start, len(text)):
                if text[i] == opener:
                    depth += 1
                elif text[i] == closer:
                    depth -= 1
                    if depth == 0:
                        cand = text[start : i + 1]
                        try:
                            return json.loads(cand)
                        except Exception:  # noqa: BLE001
                            break
        raise ValueError("No JSON value found in model output.")
    return json.loads(raw)


def _iter_json_objects(text: str) -> List[Any]:
    """Return every balanced {...} object parseable as JSON, at ANY nesting depth
    (string-literal aware). Descending into nesting means: (a) edge objects wrapped
    in {"edges":[...]} are still found, and (b) if the outer wrapper's closing brace
    is truncated, the complete inner objects are still recovered — only an
    incomplete trailing object is lost."""
    objs: List[Any] = []
    n = len(text)
    for i in range(n):
        if text[i] != "{":
            continue
        depth, in_str, esc = 0, False, False
        for j in range(i, n):
            c = text[j]
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = not in_str
            elif not in_str:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            objs.append(json.loads(text[i : j + 1]))
                        except Exception:  # noqa: BLE001
                            pass
                        break
    return objs


def _call_claude(client, model: str, system: str, user: str,
                 max_tokens: int = 3072, max_retries: int = 5) -> str:
    """Back-compat shim: dispatches by model name (the `client` arg is ignored;
    provider is chosen from the model). Lets every existing call site pick a fast
    GPT model or claude-fable-5 just by the model string it passes."""
    return _llm(model, system, user, max_tokens=max_tokens, max_retries=max_retries)


# ---------------------------------------------------------------------------
# Step 1 — node refinement (one specialist call per taxonomy)
# ---------------------------------------------------------------------------
def _refine_nodes_for_type(client, model: str, ntype: str,
                           seeds: List[str], general: List[str], context: str) -> List[str]:
    system = (
        "You are an expert clinical psychologist curating nodes for a patient's "
        "symptom network. You handle exactly ONE node type at a time."
    )
    seed_block = "\n".join(f"- {s}" for s in seeds) or "- (none)"
    general_block = "\n".join(f"- {s}" for s in general) or "- (none)"
    user = f"""NODE TYPE: {ntype}
Definition: {TYPE_DEFINITIONS[ntype]}

Candidate {ntype} items drawn from the profile:
{seed_block}

Other presenting-problem items that MIGHT contain a {ntype} (include only if truly this type):
{general_block}

PROFILE CONTEXT (evidence):
{context}

Task: return the validated list of {ntype} nodes for this patient.
Rules:
- Each node must be a single, atomic construct of type {ntype} (split compound items into separate nodes).
- Keep only nodes actually supported by the profile.
- Drop items that are NOT {ntype}, are duplicates, or are vague/non-clinical.
- You may lightly rephrase to a concise clinical phrase, but do not invent new content.

Output ONLY JSON between <NODES> and </NODES>:
<NODES>
{{"nodes": ["node 1", "node 2"]}}
</NODES>"""
    text = _call_claude(client, model, system, user)
    items: List[Any] = []
    try:
        obj = _extract_json(text, tag="NODES")
        items = obj.get("nodes", []) if isinstance(obj, dict) else []
    except Exception:  # noqa: BLE001
        for o in _iter_json_objects(text):  # truncation-tolerant fallback
            if isinstance(o, dict) and isinstance(o.get("nodes"), list):
                items = o["nodes"]
                break
    out, seen = [], set()
    for it in items:
        s = str(it).strip()
        k = _norm(s)
        if s and k not in seen:
            seen.add(k)
            out.append(s)
    return out


def build_nodes(client, model: str, profile: Dict[str, Any], context: str,
                workers: int = 5) -> List[Dict[str, Any]]:
    """Run the 5 taxonomy specialists in parallel, merge into a typed node set.

    A name claimed by more than one type is kept once, assigned to the type of
    the specialist that returned it first in NODE_TYPES priority order."""
    seeds = collect_seed_nodes(profile)
    general = seeds.get("_general", [])

    results: Dict[str, List[str]] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {
            pool.submit(_refine_nodes_for_type, client, model, t, seeds.get(t, []), general, context): t
            for t in NODE_TYPES
        }
        for fut in futs:
            t = futs[fut]
            try:
                results[t] = fut.result()
            except Exception:  # noqa: BLE001
                results[t] = []

    nodes: List[Dict[str, Any]] = []
    claimed: set = set()
    for t in NODE_TYPES:  # priority order for cross-type dedup
        for name in results.get(t, []):
            k = _norm(name)
            if k in claimed:
                continue
            claimed.add(k)
            nodes.append({"id": len(nodes), "name": name, "type": t})

    # Fallback: if refinement produced nothing (e.g. transient LLM failure) but the
    # profile does have content, use the raw seed nodes so we never fail with
    # "no nodes" on a non-empty profile.
    if not nodes:
        for t in NODE_TYPES:
            for name in seeds.get(t, []):
                k = _norm(name)
                if k and k not in claimed:
                    claimed.add(k)
                    nodes.append({"id": len(nodes), "name": name, "type": t})
        for name in general:  # presenting problems (mixed) as a last resort
            k = _norm(name)
            if k and k not in claimed:
                claimed.add(k)
                nodes.append({"id": len(nodes), "name": name, "type": "Physiological Sensation"})
    return nodes


# ---------------------------------------------------------------------------
# Step 2 — edge generation
# ---------------------------------------------------------------------------
def _edges_from_source_type(client, model: str, source_type: str,
                            source_nodes: List[Dict[str, Any]],
                            all_nodes: List[Dict[str, Any]], context: str) -> List[Dict[str, Any]]:
    """Propose edges whose CAUSE is a node of `source_type` (target may be any
    node). One focused call -> smaller output, less truncation risk."""
    if not source_nodes:
        return []
    system = (
        "You are an expert clinical psychologist building a DIRECTED causal "
        "symptom network for one patient. Edges point from cause to effect."
    )
    all_lines = "\n".join(f'{n["id"]}. [{n["type"]}] {n["name"]}' for n in all_nodes)
    src_ids = [n["id"] for n in source_nodes]
    user = f"""ALL NODES (possible effects / targets), id. [type] text:
{all_lines}

SOURCE NODES — every edge's "from" MUST be one of these {source_type} ids: {src_ids}

PROFILE CONTEXT (evidence):
{context}

Task: propose directed causal edges whose CAUSE ("from") is one of the SOURCE ids above.
Rules:
- Create an edge from A to B only if the profile supports that A causes, triggers, worsens, or maintains B.
- Direction must be cause -> effect; "from" MUST be a SOURCE id, "to" may be ANY node id.
- Use ONLY the node ids listed. Do not invent nodes.
- Include only direct, strong links grounded in the profile — NOT general medical knowledge or mere co-occurrence.
- Prefer a parsimonious network; omit weak or speculative links.
- For each edge give a confidence in [0,1] and a short evidence phrase from the profile.

Output ONLY JSON between <GRAPH> and </GRAPH>:
<GRAPH>
{{"edges": [{{"from": <source id>, "to": <any id>, "confidence": 0.9, "evidence": "..."}}]}}
</GRAPH>"""
    text = _call_claude(client, model, system, user, max_tokens=4096)
    return [o for o in _iter_json_objects(text)
            if isinstance(o, dict) and "from" in o and "to" in o]


def generate_candidate_edges(client, model: str, nodes: List[Dict[str, Any]],
                             context: str, workers: int = 5) -> List[Dict[str, Any]]:
    """Fan out edge generation across source taxonomies IN PARALLEL. Edge sets
    are disjoint by source type, so results merge without conflict. Each call is
    ~1/5 the size of a single monolithic call -> faster wall-clock, less truncation."""
    by_type: Dict[str, List[Dict[str, Any]]] = {t: [] for t in NODE_TYPES}
    for n in nodes:
        by_type.get(n["type"], by_type.setdefault(n["type"], [])).append(n)
    tasks = [(t, src) for t, src in by_type.items() if src]

    raw: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(_edges_from_source_type, client, model, t, src, nodes, context)
                for (t, src) in tasks]
        for f in futs:
            try:
                raw.extend(f.result())
            except Exception:  # noqa: BLE001
                continue

    id_set = {n["id"] for n in nodes}
    out, seen = [], set()
    for e in raw:
        try:
            a, b = int(e.get("from")), int(e.get("to"))
        except (TypeError, ValueError):
            continue
        if a not in id_set or b not in id_set or a == b or (a, b) in seen:
            continue
        seen.add((a, b))
        try:
            conf = float(e.get("confidence", 0.7))
        except (TypeError, ValueError):
            conf = 0.7
        out.append({"from": a, "to": b, "confidence": max(0.0, min(1.0, conf)),
                    "evidence": str(e.get("evidence", "")).strip()})
    return out


# ---------------------------------------------------------------------------
# Step 3 — edge verification (parallel batched plausibility scoring)
# ---------------------------------------------------------------------------
def build_candidate_edges_from_links(nodes: List[Dict[str, Any]],
                                     links: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert name-based {"from","to"} links (e.g. from the local Observer model)
    into id-based candidate edges, matching link text back to nodes by name."""
    idx = {_norm(n["name"]): n["id"] for n in nodes}
    items = list(idx.items())

    def match(s: Any) -> Optional[int]:
        k = _norm(str(s))
        if not k:
            return None
        if k in idx:
            return idx[k]
        for kk, i in items:  # loose containment fallback
            if kk and (kk in k or k in kk):
                return i
        return None

    out, seen = [], set()
    for l in links:
        a, b = match(l.get("from")), match(l.get("to"))
        if a is None or b is None or a == b or (a, b) in seen:
            continue
        seen.add((a, b))
        out.append({"from": a, "to": b, "confidence": 0.7, "evidence": str(l.get("evidence", "")).strip()})
    return out


def _score_edge_batch(client, model: str, nodes_by_id: Dict[int, Dict[str, Any]],
                      batch: List[Dict[str, Any]], context: str) -> Dict[int, float]:
    system = "You are a professional mental health expert scoring the plausibility of causal links."
    lines = []
    for i, e in enumerate(batch):
        a = nodes_by_id[e["from"]]
        b = nodes_by_id[e["to"]]
        lines.append(f'{i}. [{a["type"]}] {a["name"]}  ->  [{b["type"]}] {b["name"]}')
    edge_block = "\n".join(lines)
    user = f"""PROFILE CONTEXT (evidence):
{context}

Below are proposed directed causal relationships (cause -> effect).
For each, judge whether the link is clinically plausible AND supported by this profile.
Score each from 0 to 1:
  1 = clearly supported by the profile
  0 = clearly not supported / wrong direction / spurious
  0.5 = uncertain / weakly implied

Relationships:
{edge_block}

Output ONLY a JSON list:
[{{"edge_id": 0, "score": 0.9}}]"""
    text = _call_claude(client, model, system, user, max_tokens=2048)
    scores: Dict[int, float] = {}
    for item in _iter_json_objects(text):  # truncation-tolerant
        if isinstance(item, dict) and "edge_id" in item and "score" in item:
            try:
                scores[int(item["edge_id"])] = max(0.0, min(1.0, float(item["score"])))
            except (TypeError, ValueError):
                continue
    return scores


def verify_edges(client, model: str, nodes: List[Dict[str, Any]],
                 edges: List[Dict[str, Any]], context: str,
                 threshold: float = 0.6, batch_size: int = 12,
                 workers: int = 6) -> List[Dict[str, Any]]:
    if not edges:
        return []
    nodes_by_id = {n["id"]: n for n in nodes}
    batches = [edges[i : i + batch_size] for i in range(0, len(edges), batch_size)]

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(_score_edge_batch, client, model, nodes_by_id, b, context)
                for b in batches]
        batch_scores = [f.result() for f in futs]

    kept: List[Dict[str, Any]] = []
    for b, scores in zip(batches, batch_scores):
        for i, e in enumerate(b):
            score = scores.get(i)
            if score is None:
                score = e["confidence"]  # fall back to generator confidence
            if score >= threshold:
                # blend generator confidence with verifier score for DAG ordering
                kept.append({**e, "score": score,
                             "confidence": round(0.5 * e["confidence"] + 0.5 * score, 4)})
    return kept


# ---------------------------------------------------------------------------
# Step 4 — direction resolution for reciprocal pairs (cycles ARE allowed)
# ---------------------------------------------------------------------------
_VALID_DIRECTIONS = {"a_to_b", "b_to_a", "both", "neither"}


def _resolve_pair(client, model: str, a: Dict[str, Any], b: Dict[str, Any], context: str) -> str:
    system = ("You are an expert clinical psychologist determining the causal "
              "direction between two clinical constructs for one patient.")
    user = f"""Two constructs from the patient's profile:
A: [{a["type"]}] {a["name"]}
B: [{b["type"]}] {b["name"]}

PROFILE CONTEXT (evidence):
{context}

Both A->B and B->A were proposed. Decide the causal relationship the profile actually supports:
- "a_to_b": A causes / triggers / worsens / maintains B (and not the reverse)
- "b_to_a": B causes / triggers / worsens / maintains A (and not the reverse)
- "both": they mutually reinforce each other (a genuine feedback loop) — keep both directions
- "neither": no directly supported causal link

Output ONLY JSON: {{"direction": "both", "reason": "short reason"}}"""
    text = _call_claude(client, model, system, user, max_tokens=500)
    d = "both"
    for obj in _iter_json_objects(text):
        if isinstance(obj, dict) and "direction" in obj:
            d = str(obj.get("direction", "both")).strip()
            break
    return d if d in _VALID_DIRECTIONS else "both"


def resolve_reciprocal_directions(
    client, model: str, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]],
    context: str, workers: int = 6,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """For every reciprocal pair (A->B and B->A both present), ask the model which
    direction(s) the profile supports. Non-reciprocal edges pass through
    untouched, so ordinary (longer) cycles are preserved."""
    id2 = {n["id"]: n for n in nodes}
    present = {(e["from"], e["to"]) for e in edges}

    pairs, seen = [], set()
    for (a, b) in present:
        if (b, a) in present and (a, b) not in seen and (b, a) not in seen:
            pairs.append((a, b))
            seen.add((a, b))
            seen.add((b, a))
    if not pairs:
        return edges, []

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(_resolve_pair, client, model, id2[a], id2[b], context): (a, b)
                for (a, b) in pairs}
        decisions: Dict[Tuple[int, int], str] = {}
        for fut in futs:
            a, b = futs[fut]
            try:
                decisions[(a, b)] = fut.result()
            except Exception:  # noqa: BLE001
                decisions[(a, b)] = "both"

    drop: set = set()
    for (a, b), d in decisions.items():
        if d == "a_to_b":
            drop.add((b, a))
        elif d == "b_to_a":
            drop.add((a, b))
        elif d == "neither":
            drop.add((a, b))
            drop.add((b, a))
        # "both" -> keep the loop

    kept, removed = [], []
    for e in edges:
        if (e["from"], e["to"]) in drop:
            removed.append({**e, "removed_reason": "direction resolution"})
        else:
            kept.append(e)
    return kept, removed


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def generate_network(
    profile: Dict[str, Any],
    client=None,
    model: str = DEFAULT_MODEL,
    verify: bool = True,
    threshold: float = 0.6,
    resolve_conflicts: bool = True,
    node_workers: int = 5,
    edge_workers: int = 5,
    verify_workers: int = 8,
    resolve_workers: int = 8,
    node_model: Optional[str] = None,
    edge_model: Optional[str] = None,
    verify_model: Optional[str] = None,
    direction_model: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a verified symptom network for one profile (short or long).

    Each phase picks its own model (`client` is ignored — provider is chosen from
    the model name). Defaults: the mechanical phases (nodes/verify/direction) use
    the fast GPT model; edge generation uses `model`. Per-phase env overrides:
    DEMO_NODE_MODEL / DEMO_EDGE_MODEL / DEMO_VERIFY_MODEL / DEMO_DIRECTION_MODEL.
    Cycles are allowed: reciprocal pairs settled by direction resolution."""
    nm = node_model or os.environ.get("DEMO_NODE_MODEL", FAST_MODEL)
    em = edge_model or os.environ.get("DEMO_EDGE_MODEL", model)
    vm = verify_model or os.environ.get("DEMO_VERIFY_MODEL", FAST_MODEL)
    dm = direction_model or os.environ.get("DEMO_DIRECTION_MODEL", FAST_MODEL)

    context = render_context(profile)

    nodes = build_nodes(client, nm, profile, context, workers=node_workers)
    if not nodes:
        return {"nodes": [], "edges": [], "removed_edges": [], "error": "no nodes"}

    candidates = generate_candidate_edges(client, em, nodes, context, workers=edge_workers)
    if verify:
        candidates = verify_edges(client, vm, nodes, candidates, context,
                                  threshold=threshold, workers=verify_workers)

    if resolve_conflicts:
        final_edges, removed = resolve_reciprocal_directions(
            client, dm, nodes, candidates, context, workers=resolve_workers)
    else:
        final_edges, removed = candidates, []

    id2 = {n["id"]: n for n in nodes}

    def fmt(e: Dict[str, Any]) -> Dict[str, Any]:
        a, b = id2[e["from"]], id2[e["to"]]
        out = {"from": a["name"], "to": b["name"],
               "from_type": a["type"], "to_type": b["type"],
               "confidence": e.get("confidence"), "evidence": e.get("evidence", "")}
        if "score" in e:
            out["verify_score"] = e["score"]
        if "removed_reason" in e:
            out["removed_reason"] = e["removed_reason"]
        return out

    try:
        groups = cluster_nodes(nodes, context)   # high-level themes for the layered view (large graphs)
    except Exception:  # noqa: BLE001
        groups = []

    return {
        "nodes": [{"name": n["name"], "type": n["type"]} for n in nodes],
        "edges": [fmt(e) for e in final_edges],
        "removed_edges": [fmt(e) for e in removed],
        "groups": groups,
        "error": None,
    }


def edges_to_true_label_rows(edges: List[Dict[str, Any]]) -> List[List[str]]:
    """CSV rows matching the original network_model.py true_label format:
    [parent_tag, parent_node, child_tag, child_node]."""
    return [[e["from_type"], e["from"], e["to_type"], e["to"]] for e in edges]


# ---------------------------------------------------------------------------
# Layered view: cluster nodes into high-level themes (for large graphs)
# ---------------------------------------------------------------------------
LAYER_MIN_NODES = int(os.environ.get("DEMO_LAYER_MIN_NODES", "16"))
CLUSTER_MODEL = os.environ.get("DEMO_CLUSTER_MODEL", "gpt-4o")


def _fmt_edges(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    id2 = {n["id"]: n for n in nodes}
    out = []
    for e in edges:
        a, b = id2.get(e["from"]), id2.get(e["to"])
        if not a or not b:
            continue
        o = {"from": a["name"], "to": b["name"], "from_type": a["type"], "to_type": b["type"],
             "confidence": e.get("confidence"), "evidence": e.get("evidence", "")}
        if "score" in e:
            o["verify_score"] = e["score"]
        out.append(o)
    return out


def _groups_by_type(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by: Dict[str, List[str]] = {}
    for n in nodes:
        by.setdefault(n["type"], []).append(n["name"])
    return [{"label": t, "type": t, "members": m} for t, m in by.items()]


def cluster_nodes(nodes: List[Dict[str, Any]], context: str,
                  model: str = CLUSTER_MODEL, max_groups: int = 14) -> List[Dict[str, Any]]:
    """Group nodes into <= max_groups high-level clinical themes (one LLM call).
    Returns [{label, type, members:[node name,...]}]. Empty for small graphs."""
    if len(nodes) < LAYER_MIN_NODES:
        return []
    lines = "\n".join(f'{n["id"]}. [{n["type"]}] {n["name"]}' for n in nodes)
    system = "You organize a patient's symptom-network nodes into a few high-level clinical themes."
    user = f"""NODES:
{lines}

Group ALL nodes above into at most {max_groups} high-level, clinically meaningful themes
(e.g. "Sleep disturbance", "Interpersonal mistrust", "Avoidance behaviours"). Every node id
must appear in exactly ONE group. Give each group a short label (2-5 words).

Output ONLY JSON:
{{"groups": [{{"label": "theme", "members": [0, 3, 7]}}]}}"""
    try:
        text = _llm(model, system, user, max_tokens=2000)
    except Exception:  # noqa: BLE001
        return _groups_by_type(nodes)

    obj = next((o for o in _iter_json_objects(text) if isinstance(o, dict) and "groups" in o), None)
    if not obj:
        return _groups_by_type(nodes)

    id2 = {n["id"]: n for n in nodes}
    groups, assigned = [], set()
    for g in obj.get("groups", []):
        mems = []
        for m in g.get("members", []):
            try:
                mi = int(m)
            except (TypeError, ValueError):
                continue
            if mi in id2 and mi not in assigned:
                assigned.add(mi)
                mems.append(id2[mi])
        if mems:
            groups.append({"label": str(g.get("label", "")).strip() or "Group", "members": mems})
    leftover = [id2[i] for i in id2 if i not in assigned]
    if leftover:
        groups.append({"label": "Other", "members": leftover})

    out = []
    for g in groups:
        types = [m["type"] for m in g["members"]]
        maj = max(set(types), key=types.count) if types else "Cognition"
        out.append({"label": g["label"], "type": maj, "members": [m["name"] for m in g["members"]]})
    return out


# ---------------------------------------------------------------------------
# Lazy hierarchical generation: fast theme-level OVERVIEW, detailed edges on
# demand per theme. Nodes stay verbatim; theme + member edges are both fully
# verified (Opus generate -> verify -> direction), so accuracy matches the
# monolithic path — laziness only defers WHEN member edges are computed.
# ---------------------------------------------------------------------------
def _theme_context(context: str, groups: List[Dict[str, Any]]) -> str:
    lines = "\n".join(f"- {g['label']} [{g['type']}]: " + ", ".join(g["members"][:8]) for g in groups)
    return context + "\n\nTHEMES (each groups several nodes):\n" + lines


def generate_theme_edges(groups: List[Dict[str, Any]], context: str,
                         edge_model: str, verify_model: str, direction_model: str,
                         verify: bool = True, threshold: float = 0.6) -> List[Dict[str, Any]]:
    """Directed causal edges BETWEEN themes (accurate: generate -> verify -> resolve).
    Reuses the node-level pipeline by treating each theme as a node."""
    theme_nodes = [{"id": i, "name": g["label"], "type": g["type"]} for i, g in enumerate(groups)]
    if len(theme_nodes) < 2:
        return []
    ctx = _theme_context(context, groups)
    cand = generate_candidate_edges(None, edge_model, theme_nodes, ctx, workers=5)
    if verify:
        cand = verify_edges(None, verify_model, theme_nodes, cand, ctx, threshold=threshold, workers=8)
    kept, _ = resolve_reciprocal_directions(None, direction_model, theme_nodes, cand, ctx, workers=8)
    out = _fmt_edges(theme_nodes, kept)
    # tag endpoints with their theme index (frontend maps by index)
    label2i = {g["label"]: i for i, g in enumerate(groups)}
    for e in out:
        e["from_theme"] = label2i.get(e["from"])
        e["to_theme"] = label2i.get(e["to"])
    return out


def generate_overview(profile: Dict[str, Any], client=None, model: str = DEFAULT_MODEL,
                      node_model: Optional[str] = None, edge_model: Optional[str] = None,
                      verify_model: Optional[str] = None, direction_model: Optional[str] = None,
                      verify: bool = True, threshold: float = 0.6) -> Dict[str, Any]:
    """Fast phase-1: verbatim nodes + themes + theme-level edges. No member edges
    yet (those come from expand_theme). Returns a `_state` handle payload the
    caller stashes so expansion can run later."""
    nm = node_model or os.environ.get("DEMO_NODE_MODEL", FAST_MODEL)
    em = edge_model or os.environ.get("DEMO_EDGE_MODEL", model)
    vm = verify_model or os.environ.get("DEMO_VERIFY_MODEL", FAST_MODEL)
    dm = direction_model or os.environ.get("DEMO_DIRECTION_MODEL", FAST_MODEL)

    context = render_context(profile)
    nodes = build_nodes(None, nm, profile, context, workers=5)
    if not nodes:
        return {"nodes": [], "groups": [], "theme_edges": [], "error": "no nodes"}

    groups = cluster_nodes(nodes, context)
    if not groups:  # small graph -> no layering; fall back to a full network
        full = generate_network(profile, None, model=model, verify=verify, threshold=threshold,
                                node_model=nm, edge_model=em, verify_model=vm, direction_model=dm)
        full["layered"] = False
        return full

    theme_edges = generate_theme_edges(groups, context, em, vm, dm, verify=verify, threshold=threshold)
    return {
        "layered": True,
        "nodes": [{"name": n["name"], "type": n["type"]} for n in nodes],
        "groups": groups,
        "theme_edges": theme_edges,
        "error": None,
        "_state": {"nodes": nodes, "groups": groups, "context": context,
                   "models": {"edge": em, "verify": vm, "direction": dm},
                   "verify": verify, "threshold": threshold},
    }


def expand_theme(state: Dict[str, Any], theme_idx: int) -> Dict[str, Any]:
    """Phase-2: accurate member-level edges incident to one theme (either endpoint
    a theme member). generate -> verify -> resolve, same rigor as the full graph."""
    nodes = state["nodes"]
    groups = state["groups"]
    context = state["context"]
    m = state["models"]
    if theme_idx < 0 or theme_idx >= len(groups):
        return {"nodes": [], "edges": [], "error": "bad theme index"}

    by_name = {n["name"]: n for n in nodes}
    focus_ids = {by_name[nm]["id"] for nm in groups[theme_idx]["members"] if nm in by_name}
    if not focus_ids:
        return {"nodes": [], "edges": [], "error": "empty theme"}

    all_lines = "\n".join(f'{n["id"]}. [{n["type"]}] {n["name"]}' for n in nodes)
    system = ("You are an expert clinical psychologist building a DIRECTED causal symptom "
              "network. Edges point from cause to effect.")
    user = f"""ALL NODES (id. [type] text):
{all_lines}

FOCUS ids (this theme's nodes): {sorted(focus_ids)}

Propose directed causal edges where AT LEAST ONE endpoint is a FOCUS id (the cause may be a
focus node and the effect any node, or vice versa). Rules:
- Edge A->B only if the profile supports A causes/triggers/worsens/maintains B; direction cause->effect.
- Use ONLY the node ids above; only direct, strong, profile-grounded links.
- For each edge give confidence in [0,1] and a short evidence phrase.

PROFILE CONTEXT (evidence):
{context}

Output ONLY JSON between <GRAPH> and </GRAPH>:
<GRAPH>
{{"edges": [{{"from": <id>, "to": <id>, "confidence": 0.9, "evidence": "..."}}]}}
</GRAPH>"""
    text = _call_claude(None, m["edge"], system, user, max_tokens=4096)
    id_set = {n["id"] for n in nodes}
    raw, seen = [], set()
    for o in _iter_json_objects(text):
        if not (isinstance(o, dict) and "from" in o and "to" in o):
            continue
        try:
            a, b = int(o["from"]), int(o["to"])
        except (TypeError, ValueError):
            continue
        if a not in id_set or b not in id_set or a == b or (a, b) in seen:
            continue
        if a not in focus_ids and b not in focus_ids:   # must touch the theme
            continue
        seen.add((a, b))
        try:
            conf = float(o.get("confidence", 0.7))
        except (TypeError, ValueError):
            conf = 0.7
        raw.append({"from": a, "to": b, "confidence": max(0.0, min(1.0, conf)),
                    "evidence": str(o.get("evidence", "")).strip()})

    if state.get("verify"):
        raw = verify_edges(None, m["verify"], nodes, raw, context,
                           threshold=state.get("threshold", 0.6), workers=8)
    kept, _ = resolve_reciprocal_directions(None, m["direction"], nodes, raw, context, workers=8)
    return {"edges": _fmt_edges(nodes, kept), "error": None}
