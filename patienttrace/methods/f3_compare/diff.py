"""diff_summary: one sentence on how the two answers to a shared question differ, citing the
edges each answer was aligned to (SPEC §5), with programmatic citation validation.

Citations are inline tags in the sentence — `[A:e7]`, `[B:e12, e15]` — and `cited_edges` is
parsed from the tags, so the text and the citation lists cannot disagree. Validation rules
(mirroring SPEC §5 / EVAL_PLAN §6.5 definitions, not the judge's wording):
  1. every cited id was aligned to THAT side's answer in THIS turn (hence also exists);
  2. each side with >=1 aligned edge cites >=1 of them;
  3. if neither answer has an aligned edge, nothing is cited;
  4. no bare edge ids outside tags; a non-empty, single sentence.
On failure: regenerate once with the problems fed back. If still invalid: emit the summary
with all citation tags removed and `cited_edges` empty, `valid=False`, `status="citations_dropped"`.
If the LLM call fails (e.g. content filter): a programmatic template sentence citing the
aligned edges, `status="template_fallback"`.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Tuple

from patienttrace.angel_ext import data as D
from patienttrace.angel_ext.llm import get_client

DEFAULT_EFFORT = os.environ.get("F3_DIFF_EFFORT", "low")
MAX_TOKENS = 1500
VERSION = os.environ.get("F3_DIFF_VERSION", "f3-diff-v2")   # v1 kept for reproducing dev round 1

TAG_RE = re.compile(r"\[\s*([AaBb])\s*:\s*((?:e\d+)(?:\s*,\s*e\d+)*)\s*\]")
BARE_RE = re.compile(r"\be\d+\b")

SYSTEM_V1 = """You write the one-sentence "difference summary" in a clinical-training tool. A trainee asked two simulated patients (A and B) the same question. Each answer has already been aligned to that patient's own case network: the nodes (Stimulus, Emotion, Cognition, Behavior, Physiological Sensation) and the cause -> effect edges the answer expresses. Your sentence tells the trainee how the two answers differ, in terms of the mechanisms (edges) each answer expressed.

Rules:
- Exactly ONE sentence, at most ~45 words, plain language, about what the patients actually said in THESE answers. Do not add anything that is not in the answers; do not diagnose or speculate beyond them.
- Cite edges with inline tags right after the claim they support: [A:e7] for patient A's edge e7, [B:e12, e15] for patient B's edges. Edge ids are per patient: an A tag may only contain ids from A's aligned-edge list, a B tag only ids from B's list.
- If a patient's answer has at least one aligned edge, cite at least one of that patient's aligned edges (the ones that best carry the contrast). Never cite an edge that is not in that patient's aligned-edge list.
- If a patient has no aligned edges, cite nothing for that patient (you may still describe what they said, e.g. that they deflected or stayed vague).
- If neither patient has aligned edges, write the sentence with no tags at all.
- If the two answers do not really differ, say so honestly (still citing aligned edges where they exist).
- Do not write edge ids anywhere except inside the tags.

Return ONLY a JSON object: {"summary": "<the sentence with inline tags>"}"""

# v2 (dev round 2): three changes, each tied to a round-1 diff_support error type
#  (1) no claims about what an answer did NOT express (no cause/link/mechanism) — an empty
#      alignment means "no network edge matched", not "no causal content" (E1, 44% of non-supported)
#  (2) ground each claim in the answer's own words; edge labels identify WHICH edge to cite and must
#      not be paraphrased into the patient's mouth (E2: causal wording imported from edge labels)
#  (3) shorter (<= 35 words), one specific point per patient, no "both" unless both answers say
#      it (E3: overstatement / blending in long sentences)
#  (1) and (3) are also checked programmatically as SOFT problems (trigger the one regeneration,
#  never cause citations to be dropped).
SYSTEM_V2 = """You write the one-sentence "difference summary" in a clinical-training tool. A trainee asked two simulated patients (A and B) the same question. Each answer has already been aligned to that patient's own case network: nodes (Stimulus, Emotion, Cognition, Behavior, Physiological Sensation) and cause -> effect edges, each with the evidence span from the answer. Your sentence tells the trainee how the two answers differ, citing the edges each answer was aligned to.

Rules:
- Exactly ONE sentence, at most 35 words, plain language. Make one concrete point about each patient's answer.
- Every claim must be something the patient actually said in THIS answer; stay close to the answer's own words and the evidence spans. The edge labels only tell you WHICH edge to cite: do not borrow wording, causes or interpretations from an edge label (or from your own clinical knowledge) that the answer itself does not state.
- Describe only what each answer DID say. Do not claim that an answer lacks something (e.g. "did not link it to a cause", "no clear mechanism", "neither connected ..."): an answer with no aligned edges may still contain causal content the network does not capture. For such an answer just state briefly what the patient said (e.g. named a setting, a feeling, stayed vague, deflected).
- Say "both" only for something both answers explicitly contain; never attribute one patient's content to the other.
- Cite edges with inline tags right after the claim they support: [A:e7] for patient A's edge e7, [B:e12, e15] for patient B's edges. Edge ids are per patient: an A tag may only contain ids from A's aligned-edge list, a B tag only ids from B's list.
- If a patient's answer has at least one aligned edge, cite at least one of that patient's aligned edges (the one that best carries the contrast). Never cite an edge that is not in that patient's aligned-edge list.
- If a patient has no aligned edges, cite nothing for that patient. If neither has aligned edges, write the sentence with no tags.
- If the two answers do not really differ, say so (still citing aligned edges where they exist).
- Do not write edge ids anywhere except inside the tags.

Return ONLY a JSON object: {"summary": "<the sentence with inline tags>"}"""

# v3-demo (Phase 5, 2026-11-09, user decision; DEMO ONLY - the evaluated method is v2, which stays the
#  default): a side with no aligned edge but aligned nodes cites >= 1 of those nodes ([A:n3]), so both
#  sides of every summary are grounded in the UI. Everything else is v2.
SYSTEM_V3_DEMO = SYSTEM_V2.replace(
    "- If a patient has no aligned edges, cite nothing for that patient. If neither has aligned edges, write the sentence with no tags.",
    "- If a patient has no aligned edges but has aligned nodes, cite at least one of that patient's aligned nodes instead, with the same tag form: [A:n3], [B:n5, n8]. Node ids are per patient and may only come from that patient's aligned-node list. Cite edges whenever a patient has aligned edges.\n"
    "- If a patient has neither aligned edges nor aligned nodes, cite nothing for that patient. If neither patient has any, write the sentence with no tags.").replace(
    "- Do not write edge ids anywhere except inside the tags.",
    "- Do not write edge or node ids anywhere except inside the tags.").replace(
    "Aligned nodes of", "Aligned nodes of")
assert SYSTEM_V3_DEMO != SYSTEM_V2

# v4-demo (Phase 5 round 5, 2026-11-10, user decision; DEMO ONLY): the sentence names at least one aligned
#  cause of each patient in plain words, and says so explicitly when both answers point to the same kind
#  of cause (same coarse cause group, listed in the input). Everything else is v3-demo.
SYSTEM_V4_DEMO = SYSTEM_V3_DEMO.replace("at most 35 words", "at most 45 words") + """

Additional rules for this summary:
- For each patient with at least one aligned node or edge, NAME at least one of that patient's aligned causes in plain words in the sentence (use the node's own wording, e.g. "shame" or "the belief that strong people don't need help"), right before its tag.
- If the input lists SHARED KINDS OF CAUSE, say explicitly that both answers point to the same kind of cause (e.g. "both tie it to shame") and name it.
- Refer to the patients only as "Patient A" and "Patient B" (or "A" / "B"); repeat the label instead of using a pronoun. Never use he, she, him, her, his or hers for a patient."""

# v5-demo (Phase 5 round 10, 2026-11-11, user decision; DEMO ONLY - the evaluated method is still v2): the live
#  demo showed v4 calling the shared focus symptom a cause ("both point to the same kind of cause, insomnia") and
#  mangled names / pronouns (the UI swapped "A"/"B" for names). v5 gets the patients' names and pronouns, a
#  separate list of each answer's TRACED CAUSES (aligned nodes / edge sources that are not a shared symptom) and
#  the shared symptoms the answers touched (effects, never causes); one sentence <= 30 words,
#  "<A> ... (<cause>), while <B> ... (<cause>)."; shared kinds are computed over causes only.
SYSTEM_V5_DEMO = """You write the one-sentence "difference" line in a clinical-training tool. A trainee asked two simulated patients the same question. Each answer has been traced to that patient's own case network: nodes (Stimulus, Emotion, Cognition, Behavior, Physiological Sensation) and cause -> effect edges, each with the evidence span from the answer. The input gives, per patient: name and pronouns, the answer, the traced nodes and edges (the ONLY ids you may cite), and the TRACED CAUSES you may name. It also lists the SHARED SYMPTOMS the answers touched: what both patients have in common, i.e. the EFFECTS being explained, never causes.

Write exactly ONE sentence of at most 30 words (citation tags not counted), in this form:
  <Name A> <what A's answer says> (<A's cause>) [A:...], while <Name B> <what B's answer says> (<B's cause>) [B:...].
Rules:
- <A's cause> / <B's cause> is a short plain-words name of one of that patient's TRACED CAUSES, in the node's own wording (e.g. "fear of a crash", "the belief that resting is lazy").
- Never call a shared symptom a cause and never put a shared symptom in the parentheses. Do not write "the same kind of cause, <symptom>".
- If the input lists SHARED KINDS OF CAUSE, say so explicitly, e.g. "<Name A> and <Name B> both trace it to <the shared kind>: A ... (<A's cause>), while B ... (<B's cause>)" - still one sentence of at most 30 words.
- If a patient has no traced cause, say briefly what that patient said instead and put no parentheses for that patient (e.g. "while Natasha talks only about the panic itself").
- Every claim must be something that patient actually said in THIS answer; stay close to the answer's words. Describe only what each answer said; never claim an answer lacks a cause, link or mechanism.
- Use each patient's name exactly as given. A pronoun is allowed only if it is that patient's listed pronoun and cannot refer to the other patient; when in doubt repeat the name. Never use "themself", "Patient A", "Patient B" or a bare "A" / "B" for a patient. Do not repeat a name inside its own clause: write "Evelyn's body won't shut off", not "Evelyn says Evelyn's body won't shut off".
- Citations: put inline tags right after the claim they support: [A:e7] / [B:e12, e15] for traced edges, [A:n3] / [B:n5] for traced nodes. Ids are per patient and may only come from that patient's traced lists. A patient with a traced edge cites at least one traced edge; a patient with traced nodes but no traced edge cites at least one traced node; a patient with nothing traced cites nothing. Write ids only inside tags.

Return ONLY a JSON object: {"summary": "<the sentence with inline tags>"}"""

SYSTEMS = {"f3-diff-v1": SYSTEM_V1, "f3-diff-v2": SYSTEM_V2, "f3-diff-v3-demo": SYSTEM_V3_DEMO, "f3-diff-v4-demo": SYSTEM_V4_DEMO,
           "f3-diff-v5-demo": SYSTEM_V5_DEMO}
MAX_WORDS = {"f3-diff-v1": None, "f3-diff-v2": 40, "f3-diff-v3-demo": 40, "f3-diff-v4-demo": 52, "f3-diff-v5-demo": 30}     # soft limit
NODE_VERSIONS = {"f3-diff-v3-demo", "f3-diff-v4-demo", "f3-diff-v5-demo"}          # versions whose tags may cite node ids
CAUSE_VERSIONS = {"f3-diff-v4-demo", "f3-diff-v5-demo"}         # versions that must name causes / shared kinds
TAG_RE_N = re.compile(r"\[\s*([AaBb])\s*:\s*((?:[en]\d+)(?:\s*,\s*[en]\d+)*)\s*\]")
BARE_RE_N = re.compile(r"\b[en]\d+\b")
NEG_RE = re.compile(r"\b(neither|nor|no|not|without|never|n't|lacks?|lacking|rather than)\b[^.;:\[]{0,45}?"
                    r"\b(link\w*|connect\w*|caus\w*|mechanism\w*|tie|tied|ties|tying|explain\w*|reason\w*)\b", re.I)


def _edge_line(pid: str, eid: str, ev: str) -> str:
    g = _net(pid)
    e = g["edges"].get(eid)
    if e is None:
        return f"- {eid}: (unknown edge)"
    s, t = g["nodes"].get(e["source"], {}), g["nodes"].get(e["target"], {})
    return (f"- {eid}: {s.get('label', '?')} ({s.get('type', '?')}) -> {t.get('label', '?')} ({t.get('type', '?')})"
            f"  | evidence: \"{ev}\"")


_NETS: Dict[str, Dict[str, Any]] = {}


def _net(pid: str) -> Dict[str, Any]:
    if pid not in _NETS:
        g = D.load_network(pid)
        _NETS[pid] = {"nodes": {n["id"]: n for n in g["nodes"]}, "edges": {e["id"]: e for e in g["edges"]}}
    return _NETS[pid]


def aligned_edges(al: Dict[str, Any] | None) -> List[str]:
    return [e["edge_id"] for e in ((al or {}).get("edges") or []) if e.get("edge_id")]


def aligned_nodes(al: Dict[str, Any] | None) -> List[str]:
    return [n["node_id"] for n in ((al or {}).get("nodes") or []) if n.get("node_id")]


def _side_block(x: str, s: Dict[str, Any]) -> str:
    al = s.get("alignment") or {}
    pid = s["patient_id"]
    g = _net(pid)
    nodes = [f"- {n['node_id']}: {g['nodes'].get(n['node_id'], {}).get('label', '?')} "
             f"({g['nodes'].get(n['node_id'], {}).get('type', '?')})" for n in (al.get("nodes") or [])]
    edges = [_edge_line(pid, e["edge_id"], e.get("evidence", "")) for e in (al.get("edges") or [])]
    X = x.upper()
    return (f"PATIENT {X}'s answer:\n\"{s.get('response', '')}\"\n"
            f"Aligned nodes of {X}:\n" + ("\n".join(nodes) or "(none)") + "\n"
            f"Aligned edges of {X} (the ONLY ids you may cite as [{X}:...]):\n" + ("\n".join(edges) or "(none)"))


def _kinds(x: Dict[str, Any]) -> Dict[str, str]:
    """coarse cause group -> label, over a side's aligned nodes and aligned edge endpoints."""
    from patienttrace.angel_ext import concept_sd as CSD
    al = x.get("alignment") or {}
    pid = x["patient_id"]
    g = _net(pid)
    ids = [n["node_id"] for n in al.get("nodes") or []]
    for e in al.get("edges") or []:
        ed = g["edges"].get(e["edge_id"])
        if ed:
            ids += [ed["source"], ed["target"]]
    n2c, c2k = CSD._n2c(), CSD._c2k()
    out: Dict[str, str] = {}
    for nid in ids:
        c = n2c.get(f"{pid}:{nid}")
        if c:
            out.setdefault(c2k.get(c, c), g["nodes"].get(nid, {}).get("label", nid))
    return out


def shared_kinds(sides: Dict[str, Dict[str, Any]]) -> List[Tuple[str, str]]:
    ka, kb = _kinds(sides["a"]), _kinds(sides["b"])
    return [(ka[k], kb[k]) for k in ka if k in kb]


def build_user(question: str, sides: Dict[str, Dict[str, Any]], version: str = VERSION) -> str:
    if version == "f3-diff-v5-demo":
        return build_user_v5(question, sides)
    if version in CAUSE_VERSIONS:
        sk = shared_kinds(sides)
        extra = ("\n\nSHARED KINDS OF CAUSE (same cause group in both answers):\n"
                 + ("\n".join(f'- A: "{a}"  ~  B: "{b}"' for a, b in sk) if sk else "(none)"))
        return (f"Shared question: \"{question}\"\n\n" + _side_block("a", sides["a"]) + "\n\n"
                + _side_block("b", sides["b"]) + extra + "\n\nWrite the JSON now.")
    return (f"Shared question: \"{question}\"\n\n" + _side_block("a", sides["a"]) + "\n\n"
            + _side_block("b", sides["b"]) + "\n\nWrite the JSON now.")


def parse_citations(text: str, version: str = "f3-diff-v2") -> Tuple[Dict[str, List[str]], str]:
    """-> ({a: [...], b: [...]} cited EDGE ids, text with tags removed). Node ids (v3-demo) are
    returned by parse_node_citations; for v1/v2 the original edge-only tag syntax is used."""
    rx = TAG_RE_N if version in NODE_VERSIONS else TAG_RE
    cited: Dict[str, List[str]] = {"a": [], "b": []}
    for m in rx.finditer(text or ""):
        x = m.group(1).lower()
        for eid in re.split(r"\s*,\s*", m.group(2).strip()):
            if eid and eid.startswith("e") and eid not in cited[x]:
                cited[x].append(eid)
    plain = re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s{2,}", " ", rx.sub("", text or ""))).strip()
    return cited, plain


def parse_node_citations(text: str) -> Dict[str, List[str]]:
    cited: Dict[str, List[str]] = {"a": [], "b": []}
    for m in TAG_RE_N.finditer(text or ""):
        x = m.group(1).lower()
        for nid in re.split(r"\s*,\s*", m.group(2).strip()):
            if nid.startswith("n") and nid not in cited[x]:
                cited[x].append(nid)
    return cited


def _n_sentences(plain: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", plain.strip()) if s.strip()])


def soft_problems(text: str, version: str = VERSION) -> List[str]:
    """Style/faithfulness checks that trigger the regeneration but never drop citations (v2+)."""
    if version == "f3-diff-v1":
        return []
    plain = parse_citations(text, version)[1]
    out = []
    m = NEG_RE.search(plain)
    if m:
        out.append(f"claims an answer lacks a cause/link/mechanism (\"{m.group(0)}\"); describe only what each answer said")
    lim = MAX_WORDS.get(version)
    if lim and len(plain.split()) > lim:
        out.append(f"too long ({len(plain.split())} words; max {lim if version == 'f3-diff-v5-demo' else 35})")
    return out


def cause_problems(text: str, sides: Dict[str, Dict[str, Any]]) -> List[str]:
    """v4-demo soft checks: each side with alignments names one of its aligned causes in words;
    shared kinds of cause are stated as shared ("both")."""
    plain = parse_citations(text, "f3-diff-v4-demo")[1].lower()
    pw = {w[:5] for w in re.findall(r"[a-z]{4,}", plain)}
    out = []
    for x in ("a", "b"):
        labels = list(_kinds(sides[x]).values())
        if labels and not any({w[:5] for w in re.findall(r"[a-z]{4,}", l.lower())} & pw for l in labels):
            out.append(f"name at least one of patient {x.upper()}'s aligned causes in words (e.g. \"{labels[0]}\")")
    if shared_kinds(sides) and "both" not in plain:
        out.append("both answers point to the same kind of cause; say so explicitly (\"both ...\")")
    # gendered pronouns outside quotes (the demo shows names, so a guessed pronoun can misgender a patient)
    unquoted = re.sub(r"[\"“][^\"”]*[\"”]", " ", plain)
    if re.search(r"\b(he|she|him|her|his|hers|himself|herself)\b", unquoted):
        out.append("do not use he/she/him/her/his for a patient; repeat \"Patient A\" / \"Patient B\" instead")
    return out


# ---- v5-demo helpers --------------------------------------------------------------------------
_PRON = {"male": ("he", "him", "his", "himself"), "female": ("she", "her", "hers", "herself")}


def patient_names(sides: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """display names; two sessions of one patient get (1) / (2)"""
    n = {x: D.patient_tags(sides[x]["patient_id"]).get("name") or sides[x]["patient_id"] for x in ("a", "b")}
    if sides["a"]["patient_id"] == sides["b"]["patient_id"] or n["a"] == n["b"]:
        n = {"a": n["a"] + " (1)", "b": n["b"] + " (2)"}
    return n


def _pronouns(pid: str) -> Tuple[str, ...]:
    return _PRON.get(D.patient_tags(pid).get("gender") or "", ())


def _shared_symptom_nodes(sides: Dict[str, Dict[str, Any]]) -> Dict[str, set]:
    """per side: node ids that map to a surface concept both patients have (the shared symptoms)"""
    from patienttrace.angel_ext import concept_sd as CSD
    pa, pb = sides["a"]["patient_id"], sides["b"]["patient_id"]
    shared = set(CSD.shared_surface(pa, pb))
    n2c = CSD._n2c()
    out = {}
    for x in ("a", "b"):
        pid = sides[x]["patient_id"]
        g = _net(pid)
        out[x] = {nid for nid, n in g["nodes"].items()
                  if n.get("type") in D.SURFACE_TYPES and n2c.get(f"{pid}:{nid}") in shared}
    return out


def traced(x: Dict[str, Any], symptom_nodes: set) -> Dict[str, Any]:
    """{causes: [node ids], symptoms: [node ids]} of one answer. Causes = sources of aligned edges and aligned
    nodes that are not a shared symptom; symptoms = aligned shared-symptom nodes that are no edge source."""
    al = x.get("alignment") or {}
    g = _net(x["patient_id"])
    srcs, causes, symptoms = [], [], []
    for e in al.get("edges") or []:
        ed = g["edges"].get(e.get("edge_id"))
        if ed:
            srcs.append(ed["source"])
            if ed["target"] in symptom_nodes:
                symptoms.append(ed["target"])
            elif ed["target"] not in srcs:
                causes.append(ed["target"])
    for nid in srcs:
        if nid not in causes:
            causes.insert(0, nid)
    for n in al.get("nodes") or []:
        nid = n.get("node_id")
        if not nid or nid in causes or nid in symptoms:
            continue
        (symptoms if nid in symptom_nodes and nid not in srcs else causes).append(nid)
    return {"causes": causes, "symptoms": [s for s in symptoms if s not in srcs]}


def _kind_map(pid: str, ids: List[str]) -> Dict[str, str]:
    from patienttrace.angel_ext import concept_sd as CSD
    n2c, c2k = CSD._n2c(), CSD._c2k()
    g = _net(pid)
    out: Dict[str, str] = {}
    for nid in ids:
        c = n2c.get(f"{pid}:{nid}")
        if c:
            out.setdefault(c2k.get(c, c), g["nodes"].get(nid, {}).get("label", nid))
    return out


def v5_context(sides: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    sn = _shared_symptom_nodes(sides)
    tr = {x: traced(sides[x], sn[x]) for x in ("a", "b")}
    km = {x: _kind_map(sides[x]["patient_id"], tr[x]["causes"]) for x in ("a", "b")}
    shared = [(km["a"][k], km["b"][k]) for k in km["a"] if k in km["b"]]
    return {"names": patient_names(sides), "traced": tr, "kinds": km, "shared_kinds": shared}


def build_user_v5(question: str, sides: Dict[str, Dict[str, Any]]) -> str:
    ctx = v5_context(sides)
    parts = [f"Shared question: \"{question}\""]
    sym_labels = []
    for x in ("a", "b"):
        X, pid = x.upper(), sides[x]["patient_id"]
        g = _net(pid)
        al = sides[x].get("alignment") or {}
        pr = _pronouns(pid)
        lab = lambda nid: g["nodes"].get(nid, {}).get("label", "?")
        typ = lambda nid: g["nodes"].get(nid, {}).get("type", "?")
        nodes = [f"- {n['node_id']}: {lab(n['node_id'])} ({typ(n['node_id'])})"
                 + ("  [shared symptom - an effect, not a cause]" if n["node_id"] in ctx["traced"][x]["symptoms"] else "")
                 for n in (al.get("nodes") or [])]
        edges = [_edge_line(pid, e["edge_id"], e.get("evidence", "")) for e in (al.get("edges") or [])]
        causes = [f"- {lab(c)}" for c in ctx["traced"][x]["causes"]]
        sym_labels += [lab(s) for s in ctx["traced"][x]["symptoms"]]
        parts.append(f"PATIENT {X}: name {ctx['names'][x]}; pronouns: "
                     + ("/".join(pr[:3]) if pr else "none given - use the name") + "\n"
                     f"Answer: \"{sides[x].get('response', '')}\"\n"
                     f"Traced nodes of {X} (cite as [{X}:n..]):\n" + ("\n".join(nodes) or "(none)") + "\n"
                     f"Traced edges of {X} (cite as [{X}:e..]):\n" + ("\n".join(edges) or "(none)") + "\n"
                     f"TRACED CAUSES of {X} (name one of these in the parentheses):\n" + ("\n".join(causes) or "(none)"))
    parts.append("SHARED SYMPTOMS touched by the answers (effects - never call these causes):\n"
                 + ("\n".join(f"- {s}" for s in dict.fromkeys(sym_labels)) or "(none)"))
    sk = ctx["shared_kinds"]
    parts.append("SHARED KINDS OF CAUSE (same cause group in both answers):\n"
                 + ("\n".join(f'- A: "{a}"  ~  B: "{b}"' for a, b in sk) if sk else "(none)"))
    return "\n\n".join(parts) + "\n\nWrite the JSON now."


def _stems(s: str) -> set:
    return {w[:5] for w in re.findall(r"[a-z]{4,}", (s or "").lower())}


def cause_problems_v5(text: str, sides: Dict[str, Dict[str, Any]]) -> List[str]:
    """v5-demo soft checks: names used (no Patient A/B), parenthesised causes are traced causes (never a shared
    symptom), each side with a traced cause names one, shared kinds stated, pronouns, <= 30 words."""
    ctx = v5_context(sides)
    plain = parse_citations(text, "f3-diff-v5-demo")[1]
    low = plain.lower()
    out = []
    for x in ("a", "b"):
        nm = ctx["names"][x].split(" (")[0].lower()
        if nm not in low:
            out.append(f"use the name {ctx['names'][x]} for patient {x.upper()}")
    if re.search(r"\bpatient [ab]\b", low) or re.search(r"(?<=\s)[AB](?![\w'’])", plain):
        out.append("refer to the patients by name, never \"Patient A/B\" or a bare A/B")
    parens = re.findall(r"\(([^()]*)\)", plain)
    labels = {x: [_net(sides[x]["patient_id"])["nodes"].get(n, {}).get("label", "") for n in ctx["traced"][x]["causes"]]
              for x in ("a", "b")}
    sym = [_net(sides[x]["patient_id"])["nodes"].get(n, {}).get("label", "")
           for x in ("a", "b") for n in ctx["traced"][x]["symptoms"]]
    sym += [n["label"] for x in ("a", "b") for n in _net(sides[x]["patient_id"])["nodes"].values()
            if n["id"] in _shared_symptom_nodes(sides)[x] and n["id"] not in ctx["traced"][x]["causes"]
            and _stems(n["label"]) & _stems(plain)]
    for p in parens:
        if re.fullmatch(r"\s*[12]\s*", p):
            continue
        ps = _stems(p)
        best_c = max((len(ps & _stems(l)) for x in ("a", "b") for l in labels[x]), default=0)
        best_s = max((len(ps & _stems(l)) for l in sym), default=0)
        if best_s > best_c:
            out.append(f"\"({p})\" names a shared symptom; the parentheses must name a traced CAUSE")
    for x in ("a", "b"):
        if labels[x] and not any(_stems(p) & _stems(l) for p in parens for l in labels[x]):
            out.append(f"name one of {ctx['names'][x]}'s traced causes in parentheses (e.g. \"({labels[x][0]})\")")
    if ctx["shared_kinds"] and not re.search(r"\bboth\b|\bsame kind\b", low):
        out.append("both answers trace to the same kind of cause; say so explicitly (\"both ...\")")
    if re.search(r"same kind of cause,? (?:of |namely |-|—)?", low) and sym and any(_stems(s) & _stems(low.split("same kind of cause", 1)[1][:60]) for s in sym):
        out.append("a shared symptom is not a kind of cause")
    allowed = set()
    pa, pb = (_pronouns(sides[x]["patient_id"]) for x in ("a", "b"))
    if set(pa) != set(pb):            # same pronouns on both sides = ambiguous -> names only
        allowed = set(pa) | set(pb)
    unquoted = re.sub(r"[\"“][^\"”]*[\"”]", " ", low)
    bad = [w for w in re.findall(r"\b(he|she|him|her|his|hers|himself|herself|themself|themselves)\b", unquoted)
           if w not in allowed]
    if bad:
        out.append(f"pronoun \"{bad[0]}\" does not fit the patients' listed pronouns; repeat the name instead")
    for x in ("a", "b"):              # "Evelyn says Evelyn's body ..." -> rephrase
        nm = re.escape(ctx["names"][x].split(" (")[0])
        if re.search(rf"\b{nm}\b(?:\W+\w+){{0,5}}?\W+{nm}\b", plain):
            out.append(f"do not repeat the name {ctx['names'][x]} within one clause; rephrase (e.g. \"{ctx['names'][x]}'s body ...\")")
    return out          # the 30-word limit is checked by soft_problems (MAX_WORDS)


def validate(text: str, aligned: Dict[str, List[str]], version: str = "f3-diff-v2",
             nodes: Dict[str, List[str]] | None = None) -> Tuple[Dict[str, List[str]], List[str]]:
    """HARD problems (citations, emptiness, sentence count). v3-demo also checks node citations."""
    cited, plain = parse_citations(text, version)
    probs: List[str] = []
    if version in NODE_VERSIONS:
        cn = parse_node_citations(text)
        for x in ("a", "b"):
            X = x.upper()
            bad = [n for n in cn[x] if n not in (nodes or {}).get(x, [])]
            if bad:
                probs.append(f"[{X}:...] cites node {', '.join(bad)}, not in {X}'s aligned-node list "
                             f"({', '.join((nodes or {}).get(x, [])) or 'empty'})")
            if not aligned[x] and (nodes or {}).get(x) and not cn[x]:
                probs.append(f"patient {X} has no aligned edge but aligned nodes ({', '.join(nodes[x])}); cite one of them")
    if not plain:
        probs.append("empty summary")
    for x in ("a", "b"):
        X = x.upper()
        bad = [e for e in cited[x] if e not in aligned[x]]
        if bad:
            probs.append(f"[{X}:...] cites {', '.join(bad)}, which {'is' if len(bad) == 1 else 'are'} not in "
                         f"{X}'s aligned-edge list ({', '.join(aligned[x]) or 'empty'})")
        if aligned[x] and not cited[x]:
            probs.append(f"patient {X} has aligned edges ({', '.join(aligned[x])}) but none is cited")
    if (BARE_RE_N if version in NODE_VERSIONS else BARE_RE).search(plain):
        probs.append("ids appear outside [A:..]/[B:..] tags")
    if plain and _n_sentences(plain) > 1:
        probs.append("more than one sentence")
    return cited, probs


def _template(sides: Dict[str, Dict[str, Any]], aligned: Dict[str, List[str]],
              nodes: Dict[str, List[str]] | None = None) -> str:
    parts = []
    for x in ("a", "b"):
        if aligned[x]:
            parts.append(f"patient {x.upper()}'s answer expressed {len(aligned[x])} mechanism edge(s) "
                         f"[{x.upper()}:{', '.join(aligned[x][:3])}]")
        elif nodes and nodes.get(x):
            parts.append(f"patient {x.upper()}'s answer matched {len(nodes[x])} node(s) "
                         f"[{x.upper()}:{', '.join(nodes[x][:3])}]")
        else:
            parts.append(f"patient {x.upper()}'s answer expressed no mechanism edge")
    return (parts[0][0].upper() + parts[0][1:] + ", while " + parts[1] + ".")


def diff_summary(question: str, sides: Dict[str, Dict[str, Any]], effort: str = DEFAULT_EFFORT,
                 sample_idx: int = 0, version: str = VERSION) -> Dict[str, Any]:
    """sides = {a|b: {patient_id, response, alignment(F1 record)}} -> diff record."""
    llm = get_client()
    aligned = {x: aligned_edges(sides[x].get("alignment")) for x in ("a", "b")}
    nodes = {x: aligned_nodes(sides[x].get("alignment")) for x in ("a", "b")}
    system, user = SYSTEMS[version], build_user(question, sides, version)
    best = None   # last attempt whose citations are valid (soft problems only)
    msgs: List[Dict[str, str]] = [{"role": "user", "content": user}]
    lat = 0.0
    tin = tout = 0
    history: List[Dict[str, Any]] = []
    text = ""
    for attempt in range(2):
        try:
            r = llm.chat(llm.method_model, system=system, messages=msgs, json_mode=True, effort=effort,
                         max_tokens=MAX_TOKENS, tag="f3.diff", sample_idx=sample_idx)
        except Exception as exc:  # noqa: BLE001  content filter / API failure
            history.append({"attempt": attempt + 1, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            break
        lat += r.latency_ms
        tin += r.tokens_in
        tout += r.tokens_out
        j = r.json if isinstance(r.json, dict) else {}
        text = str(j.get("summary") or "").strip()
        cited, hard = validate(text, aligned, version, nodes)
        if not j:
            hard = ["output was not a JSON object with a 'summary' field"] + hard
        soft = soft_problems(text, version) if not hard else []
        if not hard and version in CAUSE_VERSIONS:
            soft += cause_problems_v5(text, sides) if version == "f3-diff-v5-demo" else cause_problems(text, sides)
        probs = hard + soft
        history.append({"attempt": attempt + 1, "summary": text, "problems": probs, "soft": soft})
        if not probs:
            return _out(text, cited, True, "ok" if attempt == 0 else "ok_after_regen", history, lat, tin, tout,
                        effort, version)
        if not hard:
            best = (text, cited)
        msgs = msgs + [{"role": "assistant", "content": r.text},
                       {"role": "user", "content": "That summary is invalid: " + "; ".join(probs)
                        + ". Rewrite it following all rules. Return ONLY the JSON object."}]
    if best is not None:   # citations valid, only soft problems left -> keep it, flag the soft problems
        return _out(best[0], best[1], True, "ok_soft_problems", history, lat, tin, tout, effort, version)
    if text:
        _, plain = parse_citations(text, version)
        plain = (BARE_RE_N if version in NODE_VERSIONS else BARE_RE).sub("", plain).replace("  ", " ").strip()
        return _out(plain, {"a": [], "b": []}, False, "citations_dropped", history, lat, tin, tout, effort, version)
    t = _template(sides, aligned, nodes if version in NODE_VERSIONS else None)
    cited, probs = validate(t, aligned, version, nodes)
    return _out(t, cited, not probs, "template_fallback", history, lat, tin, tout, effort, version)


def _out(text, cited, valid, status, history, lat, tin, tout, effort, version=VERSION) -> Dict[str, Any]:
    extra = {"cited_nodes": parse_node_citations(text)} if version in NODE_VERSIONS else {}
    return {"diff_summary": text, "cited_edges": cited, **extra, "valid": valid, "status": status,
            "attempts": len(history), "problems": history[-1].get("problems", []) if history else [],
            "history": history, "latency_ms": round(lat, 1), "tokens": {"in": tin, "out": tout},
            "effort": effort, "version": version}


def strip_tags(text: str, version: str = "f3-diff-v2") -> str:
    return parse_citations(text, version)[1]


if __name__ == "__main__":  # tiny self-check of the validator (no LLM)
    al = {"a": ["e1", "e2"], "b": []}
    for s in ["A worries [A:e1] while B deflects.", "A worries [A:e9] while B deflects.",
              "A worries while B deflects.", "A worries [A:e1]. B deflects [B:e3].", "A e1 worries [A:e2]."]:
        print(json.dumps({"s": s, "res": validate(s, al)}))
