"""Formulation Contrast: what happened when one question went to two look-alike patients.

Alignment: both answers go through the Interview page's aligner (interview.align_v2: said / implied nodes,
onset and causes mapped to Stimulus nodes). The paper's frozen F1 aligner is not changed.
Shared space: literal node labels of two different networks almost never match, so every node is annotated
once per patient (one LLM call, cached in data/fc/nodes_pNNN.json): a short label, a 3-6 word gist and a
one-line "function" (what it does for the patient). Nodes are compared as "<type>: <label>. <function>"
(the label keeps the shared content such as cars or accidents; the function line adds the role it plays).
Per turn:
  d_t    1 - best calibrated similarity, averaged over A's aligned nodes, symmetrised with B (0 = the same,
         1 = completely different). Calibration (data/fc/calibration.json): cosine `lo` -> 0, `hi` -> 1, where
         lo = 10th percentile of random cross-patient node pairs (unrelated), hi = median of pairs the concept
         vocabulary maps to one concept, both measured in the same text space. Same type +0.05, other type -0.10. If a side has no
         node: distance between the two answer texts (same calibration).
  g_t    running gap drawn on the page: g_t = 0.6 g_{t-1} + 0.4 d_t, g_0 = 0 at the shared start.
  gist   a complete 3-6 word phrase written from the answer's main ALIGNED node (the one least like the other
         side), so gist and underlined spans come from one alignment; for an answer with no aligned node, a gist
         of the answer text flagged `unaligned` (muted on the track: "not in this patient's network").
  reading  one AI-generated line contrasting the two answers' mechanisms; the page reveals it only after the
         trainee has reflected on the turn.
  items  basket entries per node (short label + the patient's words). 'Both said': same node type, calibrated
         similarity >= 0.45, and the model confirms both serve the same function (yes/no + one-line reason,
         cached); without a model, same type and similarity >= 0.8.
Reveal: each patient's distinctive causes of the shared concept, with a hint of the kind of question that
would reach the ones not reached. Learning point: one AI-generated sentence. Nothing here grades a question.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from patienttrace import paths as P
from patienttrace.angel_ext import data as D

SAME_TYPE, OTHER_TYPE, BOTH = 0.05, -0.10, 0.80
TYPE_WORD = {"Cognition": "Thought", "Emotion": "Feeling", "Physiological Sensation": "Body", "Behavior": "Behaviour", "Stimulus": "Trigger"}
VERSION = "fc-gap-v6"


def has_model() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AZURE_FOUNDRY_API_KEY"))


def calibration() -> Dict[str, float]:
    try:
        with open(P.data("fc", "calibration.json"), encoding="utf-8") as f:
            c = json.load(f)
            return {"lo": float(c["lo"]), "hi": float(c["hi"])}
    except Exception:  # noqa: BLE001
        return {"lo": 0.245, "hi": 0.78}


def _cal(c: float, cal: Dict[str, float]) -> float:
    return max(0.0, min(1.0, (c - cal["lo"]) / (cal["hi"] - cal["lo"])))


def _embed(texts: List[str]):
    from patienttrace.angel_ext.llm import get_client
    return get_client().embed(texts, tag="fc")


def fit(s: str, n: int = 24) -> str:
    """Cut a label at a word boundary (never mid-word) so it is at most n characters, ellipsis included."""
    s = str(s or "").strip()
    if len(s) <= n:
        return s
    cut = s[: n - 1].rsplit(" ", 1)[0].rstrip(",.;:/-–— ")
    return (cut or s[: n - 1]) + "…"


def short_quote(s: str, n: int = 6) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip().strip('"“”')
    w = s.split(" ")
    return " ".join(w[:n]).rstrip(",.;:—-") + ("…" if len(w) > n else "")


# ---------------- node annotations (shared space) ----------------
ANN_SYSTEM = """For each node of one patient's symptom network write:
  "short": a display label of at most 22 characters,
  "gist": 3-6 plain words from the patient's side, lower case, no quotes (e.g. "fears causing an accident",
          "stays home", "heart races"),
  "function": one line (at most 18 words) on what this node does for the patient in the problem cycle, in
          general terms that could describe another person too (e.g. "avoidance that prevents feared harm
          to others", "body alarm read as danger").
Return only JSON: {"<node id>": {"short": "...", "gist": "...", "function": "..."}, ...} covering every node."""


def annotations(pid: str) -> Dict[str, Dict[str, str]]:
    for path in (P.data("fc", f"nodes_{pid}.json"), P.state("fc", f"nodes_{pid}.json")):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    if not has_model():
        return {}
    from patienttrace.angel_ext.llm import get_client
    net = D.load_network(pid)
    llm = get_client()
    r = llm.chat(llm.method_model, system=ANN_SYSTEM, json_mode=True, max_tokens=6000, tag="fc-nodes-v1",
                 user="\n".join(f"{n['id']} | {n['type']} | {n['label']}" for n in net["nodes"]))
    j = r.json if isinstance(r.json, dict) else {}
    ids = {n["id"] for n in net["nodes"]}
    out = {k: {"short": fit(v.get("short"), 24), "gist": phrase(v.get("gist")), "function": str(v.get("function") or "")[:200]}
           for k, v in j.items() if k in ids and isinstance(v, dict)}
    path = P.state("fc", f"nodes_{pid}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def node_text(pid: str, nid: str) -> str:
    n = {x["id"]: x for x in D.load_network(pid)["nodes"]}[nid]
    a = annotations(pid).get(nid) or {}
    return f"{TYPE_WORD.get(n['type'], n['type'])}: {n['label']}. {a.get('function') or ''}".strip()


# ---------------- alignment (Interview aligner) ----------------
def align(pid: str, question: str, response: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """Interview aligner result as the node list this module uses ([{node, type, label, words, kind}])."""
    from patienttrace import interview as IV
    al = IV.align_v2(pid, response, question, history or [], "normal")
    net = {n["id"]: n for n in D.load_network(pid)["nodes"]}
    return {"version": al["version"], "nodes": [{"node": x["node"], "type": net[x["node"]]["type"], "label": net[x["node"]]["label"],
                                                 "words": (x.get("span") or {}).get("text", ""), "kind": x["kind"], "confidence": x["confidence"]}
                                                for x in al["nodes"]], "themes": al.get("themes") or []}


def _nodes(al: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not al or not al.get("nodes"):
        return []
    if "node" in al["nodes"][0]:
        return al["nodes"]
    return [{"node": x["node_id"], "type": x.get("type"), "label": x.get("label"), "words": x.get("evidence") or ""} for x in al["nodes"]]


GIST_SYSTEM = """Each line gives a patient's answer to a therapist question and the ONE symptom-network node the
answer was aligned to (type | label | the patient's words that expressed it). For each line write a gist: a
complete 3-6 word phrase, lower case, from the patient's side, saying what the answer expresses ABOUT THAT NODE,
in plain words close to the patient's own (e.g. "started at 35", "hands shake", "fears causing an accident").
Never a cut-off sentence, never about anything the node does not cover.
Return only JSON: {"<key>": "<gist>", ...} using the keys given (the part before the first " | ")."""

FUNC_SYSTEM = """Two simulated patients answered the same therapist question. Each candidate pairs one node of
patient A's symptom network with one node of patient B's, both of the same type, with what the node does for that
patient (its function) and the patient's words. Decide whether the two serve the SAME FUNCTION for each patient
(the same mechanism in the problem cycle), not merely similar wording or topic. Example of NOT the same: A replays
every decision looking for where he messed up (responsibility / self-blame rumination) vs B keeps thinking about
how quick the accident was (intrusive trauma memory). Be strict.
Return only JSON: {"<key>": {"same": true|false, "reason": "<one line, at most 18 words>", "label": "<3-5 words
naming the shared thing, only when same>"}, ...} using the keys given."""

BOTH_MIN = 0.45   # calibrated similarity a same-type pair needs before the function check (no model: BOTH)


def _first_words(s: str, n: int = 6) -> str:
    """Model-free fallback gist: the first clause, at most n words, no ellipsis."""
    c = re.split(r"[.;:!?—–]|,\s", str(s or "").strip(), maxsplit=1)[0].strip()
    return phrase(c.lower(), n=n, keep=n)


def _llm_json(system: str, user: str, tag: str, max_tokens: int = 700) -> Dict[str, Any]:
    if not has_model():
        return {}
    from patienttrace.angel_ext.llm import get_client
    llm = get_client()
    try:
        r = llm.chat(llm.method_model, system=system, user=user, json_mode=True, max_tokens=max_tokens, tag=tag)   # cached by prompt
        return r.json if isinstance(r.json, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def gists(question: str, mains: Dict[str, Any]) -> Dict[str, str]:
    """mains = {"a": (response, node) | None, ...} -> {"a": gist, ...}; gist written from the aligned node only."""
    todo = {s: v for s, v in mains.items() if v}
    if not todo:
        return {}
    user = f"Question: {question}\n" + "\n".join(f"{s} | answer: {r} | node: {n['type']} | {n['label']} | words: {n['words']}" for s, (r, n) in todo.items())
    j = _llm_json(GIST_SYSTEM, user, "fc-gist-v2")
    return {s: _gfix(j.get(s)) for s in todo if j.get(s)}


ANS_GIST_SYSTEM = """Each line gives a patient's answer to a therapist question. The answer matched no node of the
patient's symptom network. Write a gist: a complete 3-6 word phrase, lower case, from the patient's side, in
plain words close to the patient's own (e.g. "started at 35", "hands shake"). Never a cut-off sentence.
Return only JSON: {"<key>": "<gist>", ...} using the keys given (the part before the first " | ")."""

READ_SYSTEM = """A trainee therapist asked two simulated patients the same question. You get each answer and the
symptom-network nodes it was aligned to (type | label | what the node does for that patient). Write ONE line
(at most 30 words) contrasting the mechanism each answer reveals, naming both patients, in the form
"<A> <does what> (<mechanism>); <B> <does what> (<mechanism>)", e.g. "Max replays his own decisions
(self-blame); Natasha relives the accident (intrusive memory)". If the answers reveal the same mechanism, say so
plainly. Describe the patients only: no judgment of the question or the trainee, no right/wrong, no advice.
Use plain clinical words; never mention nodes, networks, mapping or alignment.
Return only JSON: {"reading": "..."}"""


_STOP = {"a", "an", "the", "and", "or", "but", "to", "of", "in", "on", "at", "for", "with", "about", "from", "by", "as",
         "my", "your", "his", "her", "their", "our", "its", "i", "it", "is", "was", "be", "that", "this", "so", "if", "when"}


def phrase(text: Any, n: int = 6, keep: int = 8) -> str:
    """A complete short phrase, never cut mid-phrase and never with an ellipsis: up to `keep` words are kept as they
    are; a longer text is cut to n words and then back past trailing function words ("about", "the", "my"…)."""
    w = str(text or "").replace('"', "").replace("“", "").replace("”", "").split()
    if len(w) > keep:
        w = w[:n]
        while len(w) > 2 and w[-1].lower().strip(",.;:") in _STOP:
            w.pop()
    return re.sub(r"\bi(?=\b|’|')", "I", " ".join(w).strip(" .,;:—–-"))


def _gfix(g: Any) -> str:
    return phrase(g)


def answer_gists(question: str, answers: Dict[str, str]) -> Dict[str, str]:
    """Gists for answers with no aligned node (shown muted on the track), written from the answer text."""
    if not answers:
        return {}
    user = f"Question: {question}\n" + "\n".join(f"{s} | answer: {r}" for s, r in answers.items())
    j = _llm_json(ANS_GIST_SYSTEM, user, "fc-ansgist-v1")
    return {s: _gfix(j.get(s)) for s in answers if j.get(s)}


def patient_name(pid: str) -> str:
    try:
        return (D.short_profiles()[D.idx_of(pid)].get("identity") or {}).get("name") or pid
    except Exception:  # noqa: BLE001
        return pid


def reading(pa: str, pb: str, question: str, a: Dict[str, Any], b: Dict[str, Any]) -> str:
    """One-line AI contrast of the two answers' mechanisms (cached); "" without a model."""
    if not has_model():
        return ""
    def side(pid, x):
        ann, N = annotations(pid), _nodes(x.get("alignment"))
        nodes = "\n".join(f"  {n['type']} | {n['label']} | {(ann.get(n['node']) or {}).get('function', '')}" for n in N) or "  (no matching node)"
        return f"{patient_name(pid)} answered: {x.get('response')}\n nodes:\n{nodes}"
    j = _llm_json(READ_SYSTEM, f"Question: {question}\n\n{side(pa, a)}\n\n{side(pb, b)}", "fc-reading-v1", 400)
    return str(j.get("reading") or "").strip()[:300]


def function_check(pa: str, pb: str, question: str, pairs: List[Any]) -> Dict[str, Dict[str, Any]]:
    """pairs = [(A node, B node)] of the same type -> {"i": {same, reason, label}} (cached)."""
    if not pairs:
        return {}
    annA, annB = annotations(pa), annotations(pb)
    fn = lambda ann, n: (ann.get(n["node"]) or {}).get("function") or ""
    user = f"Question: {question}\n" + "\n".join(
        f"{k} | type {x['type']} | A: {x['label']} (function: {fn(annA, x)}) said as \"{x['words']}\" | "
        f"B: {y['label']} (function: {fn(annB, y)}) said as \"{y['words']}\"" for k, (x, y) in enumerate(pairs))
    j = _llm_json(FUNC_SYSTEM, user, "fc-func-v1")
    out = {}
    for k in range(len(pairs)):
        v = j.get(str(k))
        if isinstance(v, dict):
            out[str(k)] = {"same": bool(v.get("same")), "reason": str(v.get("reason") or "")[:160], "label": str(v.get("label") or "")[:40]}
    return out


def turn_analysis(pa: str, pb: str, a: Dict[str, Any], b: Dict[str, Any], question: str = "") -> Dict[str, Any]:
    """a / b = {response, alignment (this module's node list)}: d_t, gists, quotes, basket items and 'Both said'.
    Both said: same node type, calibrated similarity >= BOTH_MIN, and the model confirms the same function
    (without a model: same type and similarity >= BOTH). Every candidate and its verdict is kept in `checked`."""
    import numpy as np
    cal = calibration()
    A, B = _nodes(a.get("alignment")), _nodes(b.get("alignment"))
    annA, annB = annotations(pa), annotations(pb)
    best_a, best_b, both, checked = [1.0] * len(A), [1.0] * len(B), [], []
    if A and B:
        V = _embed([node_text(pa, n["node"]) for n in A] + [node_text(pb, n["node"]) for n in B])
        S = V[:len(A)] @ V[len(A):].T
        adj = np.array([[S[i, j] + (SAME_TYPE if A[i]["type"] == B[j]["type"] else OTHER_TYPE) for j in range(len(B))] for i in range(len(A))])
        C = np.vectorize(lambda x: _cal(x, cal))(adj)
        best_a, best_b = list(1 - C.max(axis=1)), list(1 - C.max(axis=0))
        d, method = float((np.mean(best_a) + np.mean(best_b)) / 2), "nodes"
        cand = sorted(((float(C[i, j]), i, j) for i in range(len(A)) for j in range(len(B))
                       if A[i]["type"] == B[j]["type"] and C[i, j] >= (BOTH_MIN if has_model() else BOTH)), reverse=True)
        ui, uj, pairs = set(), set(), []
        for c, i, j in cand:                       # one partner per node, best first
            if i not in ui and j not in uj:
                ui.add(i); uj.add(j); pairs.append((c, i, j))
        verdict = function_check(pa, pb, question, [(A[i], B[j]) for _, i, j in pairs]) if has_model() else {}
        for k, (c, i, j) in enumerate(pairs):
            v = verdict.get(str(k)) if has_model() else {"same": True, "reason": "very similar nodes (no model check)", "label": ""}
            v = v or {"same": False, "reason": "no verdict", "label": ""}
            checked.append({"a_node": A[i]["node"], "b_node": B[j]["node"], "type": TYPE_WORD.get(A[i]["type"], A[i]["type"]), "sim": round(c, 2), **v})
            if v["same"]:
                both.append({"a": A[i], "b": B[j], "label": v.get("label") or "", "reason": v.get("reason") or "", "sim": round(c, 2)})
    else:
        V = _embed([a.get("response") or " ", b.get("response") or " "])
        d, method = float(1 - _cal(float(V[0] @ V[1]), cal)), "text"
    in_both = {("a", x["a"]["node"]) for x in both} | {("b", x["b"]["node"]) for x in both}
    main = {s: (N[int(np.argmax(best))] if N else None) for s, N, best in (("a", A, best_a), ("b", B, best_b))}
    G = gists(question, {s: ((a if s == "a" else b).get("response"), main[s]) if main[s] else None for s in "ab"})
    U = answer_gists(question, {s: (a if s == "a" else b).get("response") or "" for s in "ab" if not main[s]})

    def short(ann, n):
        return (ann.get(n["node"]) or {}).get("short") or short_quote(n["label"], 4)

    def side(s, ann, N, resp):
        m = main[s]
        gist = (G.get(s) or phrase((ann.get(m["node"]) or {}).get("gist")) or phrase(m["label"].lower())) if m else (U.get(s) or _first_words(resp))
        items = [{"node": n["node"], "ntype": n["type"], "type": TYPE_WORD.get(n["type"], n["type"]), "short": short(ann, n),
                  "words": n["words"] or "", "kind": n.get("kind", "said")} for n in N if (s, n["node"]) not in in_both]
        return {"gist": gist, "unaligned": not m, "quote": (m["words"] or m["label"]) if m else resp, "main": m["node"] if m else None,
                "items": items, "n_nodes": len(N)}

    return {"version": VERSION, "d": round(d, 3), "method": method, "checked": checked, "reading": reading(pa, pb, question, a, b),
            "a": side("a", annA, A, a.get("response")), "b": side("b", annB, B, b.get("response")),
            "both": [{"a_node": x["a"]["node"], "b_node": x["b"]["node"], "ntype": x["a"]["type"], "type": TYPE_WORD.get(x["a"]["type"], x["a"]["type"]),
                      "short_a": short(annA, x["a"]), "short_b": short(annB, x["b"]), "words_a": x["a"]["words"], "words_b": x["b"]["words"],
                      "label": x["label"], "reason": x["reason"], "sim": x["sim"]} for x in both]}


UNALIGNED_LOG = ("fc", "unaligned.jsonl")


def log_unaligned(pid: str, turn: int, question: str, text: str, source: str, alignment: Optional[Dict[str, Any]]) -> bool:
    """Append an answer that matched no node of the patient's network to state/fc/unaligned.jsonl
    (patient, turn, question, text, source), so the rate at which the patient model goes beyond its network
    can be reported (scripts/unaligned_report.py). Returns whether it was logged."""
    if _nodes(alignment):
        return False
    import time
    path = P.state(*UNALIGNED_LOG)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rec = {"ts": round(time.time(), 1), "patient": pid, "name": patient_name(pid), "turn": turn, "question": question,
           "text": text, "source": source, "themes": [t.get("theme") for t in (alignment or {}).get("themes") or []]}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def running(ds: List[float], keep: float = 0.6) -> List[float]:
    """g_t = keep * g_{t-1} + (1 - keep) * d_t, g_0 = 0 (the shared start)."""
    out, g = [], 0.0
    for d in ds:
        g = keep * g + (1 - keep) * d
        out.append(round(g, 3))
    return out


# ---------------- reveal ----------------
HINT_SYSTEM = """A trainee interviewed two simulated patients who share a presenting problem. For each cause of the
problem that the trainee did NOT reach, write a short hint (at most 12 words) of the KIND of question that would
have reached it, addressed to the trainee, using the patient's pronoun, without naming the cause outright
(e.g. "ask what she fears will happen to her body", "ask what he does right before leaving").
Return only JSON: {"<key>": "<hint>", ...} using the keys given (the part before the first " | ")."""


def reveal(pa: str, pb: str, focus: Optional[str] = None, reached: Optional[Dict[str, List[str]]] = None,
           names: Optional[Dict[str, str]] = None, genders: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Each patient's distinctive causes of the shared concept; with `reached`, hints for the others (model only)."""
    from patienttrace.angel_ext.demo_focus import contrast_graph
    cg = contrast_graph(pa, pb)
    concepts = cg.get("concepts") or []
    c = next((x for x in concepts if x["concept_id"] == focus), None)
    if c is None and concepts:
        c = max(concepts, key=lambda x: sum(1 for s in "ab" for e in cg["sides"][s]["edges"] if e.get("target_concept") == x["concept_id"] and e.get("distinctive")))
    out = {"concept_id": c["concept_id"] if c else None, "name": c["name"] if c else "", "a": [], "b": []}
    for s, pid in (("a", pa), ("b", pb)):
        net = {n["id"]: n for n in D.load_network(pid)["nodes"]}
        ann, seen = annotations(pid), set()
        for e in cg["sides"][s]["edges"]:
            if c and e.get("target_concept") == c["concept_id"] and e.get("distinctive") and e["source"] in net and e["source"] not in seen:
                seen.add(e["source"])
                n = net[e["source"]]
                out[s].append({"node": n["id"], "label": n["label"], "short": (ann.get(n["id"]) or {}).get("short") or n["label"],
                               "type": TYPE_WORD.get(n["type"], n["type"]), "ntype": n["type"]})
    if reached is not None and has_model():
        todo = [(s, x) for s in "ab" for x in out[s] if x["node"] not in set(reached.get(s) or [])]
        if todo:
            from patienttrace.angel_ext.llm import get_client
            llm = get_client()
            pron = lambda s: {"female": "she", "male": "he"}.get(((genders or {}).get(s) or "").lower(), "they")
            user = "\n".join(f"{s}:{x['node']} | patient {(names or {}).get(s, s)} ({pron(s)}) | {x['type']} | {x['label']}" for s, x in todo)
            try:
                r = llm.chat(llm.method_model, system=HINT_SYSTEM, user=user, json_mode=True, max_tokens=800, tag="fc-hints-v1")
                j = r.json if isinstance(r.json, dict) else {}
                for s, x in todo:
                    h = j.get(f"{s}:{x['node']}")
                    if h:
                        x["hint"] = str(h).strip()[:120]
            except Exception:  # noqa: BLE001  hints are optional
                pass
    return out


LEARN_SYSTEM = """You help trainee therapists reflect on a practice round in which every question went to two
simulated patients who share a presenting problem but differ in what drives it. You get each question and how
far apart the two answers were (gap 0 = same, 1 = completely different). Write ONE learning point (at most 45
words) about which KINDS of question (e.g. "what would happen if...", "what do you do to...", "how do you feel...")
opened the biggest gaps in this round and which kinds kept the answers alike. Describe; do not score, grade or
praise the trainee, and do not quote the gap numbers (say "widest", "closest", "mid-range").
Return only JSON: {"point": "..."}"""


def learning_point(names: Dict[str, str], turns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """{"point", "model"}, or {} when no model is available or the call fails (the page then hides the box)."""
    if not has_model():
        return {}
    from patienttrace.angel_ext.llm import get_client
    llm = get_client()
    user = f"Patients: {names.get('a')} and {names.get('b')}.\n" + "\n".join(
        f"Q{k + 1} (gap {t['d']:.2f}): {t['question']}" for k, t in enumerate(turns))
    try:
        r = llm.chat(llm.method_model, system=LEARN_SYSTEM, user=user, json_mode=True, max_tokens=1500, tag="fc-learning-v1")
        j = r.json if isinstance(r.json, dict) else {}
        p = str(j.get("point") or "").strip()[:400]
        if p:
            return {"point": p, "model": llm.method_model}
        return {"_error": "no point in the model output: " + str(getattr(r, "text", "") or "")[:160]}
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"{type(exc).__name__}: {str(exc)[:200]}"}
