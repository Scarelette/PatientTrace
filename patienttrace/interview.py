"""Interview page: turn a patient reply + its F1 alignment into what the Interview view shows.

For each patient turn:
  spans    [{text, node}]   where in the reply each aligned node is expressed (the aligner's evidence quote,
                            located in the reply text; quotes that cannot be found are dropped)
  active   [node ids]       nodes the aligner found in the reply
  path     [[src, tgt]]     network edges whose two ends are both active in this turn (only real edges)
  trigger  {phrase, node}   a Stimulus node set off by the trainee's previous message, or null
  eased    [node ids]       nodes active in earlier turns that this reply shows easing, or []
  guidance {title, surface, under, tip, instead}   AI-generated (LLM), labelled as such in the UI

trigger: first a word match of Stimulus node labels against the trainee message; when that finds nothing,
  the LLM's proposal is used if its phrase occurs in the trainee message and its node is a Stimulus node.
  TODO(model): replace both with an aligner pass over the trainee message.
eased: proposed by the LLM, kept only if each node was active earlier and is not active now.
  TODO(model): the F1 aligner has no notion of a node being reduced; a dedicated "easing" label is needed.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from patienttrace.angel_ext import data as D

GUIDANCE_VERSION = "interview-guidance-v1"
STOP = set("""a an the and or but of to in on for with at by from as is are was were be been being it its this that these
those i you he she they we me my your her his their our them us not no so do does did have has had just really very
what when where how why who which can could would should will about into than then there here like get got""".split())


def _words(s: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9']+", (s or "").lower()) if len(w) > 2 and w not in STOP]


def _stem(w: str) -> str:
    return re.sub(r"(ing|ed|es|s)$", "", w)


def locate(quote: str, text: str) -> Optional[tuple]:
    """(start, end) of `quote` in `text`: exact (case-insensitive) first, then the longest run of the
    quote's words that appears verbatim."""
    if not quote or not text:
        return None
    low, q = text.lower(), quote.strip().strip('"“”').lower()
    i = low.find(q)
    if i >= 0:
        return i, i + len(q)
    ws = q.split()
    for n in range(len(ws) - 1, 2, -1):                     # at least 3 words
        for k in range(0, len(ws) - n + 1):
            frag = " ".join(ws[k:k + n])
            i = low.find(frag)
            if i >= 0:
                return i, i + len(frag)
    return None


def spans_for(reply: str, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out, taken = [], []
    for n in nodes:
        loc = locate(n.get("evidence") or "", reply)
        if not loc or any(not (loc[1] <= a or loc[0] >= b) for a, b in taken):
            continue
        taken.append(loc)
        out.append({"start": loc[0], "end": loc[1], "text": reply[loc[0]:loc[1]], "node": n["node_id"]})
    return sorted(out, key=lambda s: s["start"])


def match_trigger(trainee: str, net: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Stimulus node whose label shares the most content words with the trainee message (>= 2 words or
    >= half of the label's words). Returns the matching phrase as it appears in the message."""
    tw = {_stem(w) for w in _words(trainee)}
    best = None
    for n in net["nodes"]:
        if n.get("type") != "Stimulus":
            continue
        lw = {_stem(w) for w in _words(n["label"])}
        hit = tw & lw
        if not lw or (len(hit) < 2 and len(hit) / len(lw) < 0.5):
            continue
        score = len(hit) + len(hit) / len(lw)
        if not best or score > best[0]:
            best = (score, n["id"], hit)
    if not best:
        return None
    toks = list(re.finditer(r"[A-Za-z0-9']+", trainee))
    idx = [k for k, m in enumerate(toks) if _stem(m.group(0).lower()) in best[2]]
    phrase = trainee[toks[idx[0]].start():toks[idx[-1]].end()] if idx else ""
    if len(phrase) > 60:
        phrase = toks[idx[0]].group(0)
    return {"phrase": phrase, "node": best[1], "source": "word-match"}


SYSTEM = """You supervise trainee therapists who practise interviewing simulated patients. For one patient
reply you explain what is going on, using the patient's symptom network (nodes and cause -> effect links)
and the nodes an automatic aligner found in the reply. Be concrete, warm and brief; no diagnosis, no
jargon the trainee would not know. Refer to the patient by first name or pronoun as given.

Return only a JSON object:
{"title": "2-5 word label for this reply (e.g. 'The checking loop')",
 "surface": "what the patient shows on the surface (<= 25 words)",
 "under": "what is happening underneath, in terms of the network nodes and links (<= 40 words)",
 "tip": "one tip for the trainee about this moment (<= 40 words)",
 "instead": "one thing the trainee could say next (a single sentence, in quotes-free plain text)",
 "trigger": {"phrase": "exact words from the trainee's message", "node": "node id of a Stimulus node"} or null,
 "eased": ["ids of nodes from EARLIER_ACTIVE that this reply shows loosening or easing"]}
Only use node ids that appear in the lists given, and only in the "trigger" and "eased" fields: never write
node ids in the text fields (say the thing in plain words). Use null / [] when nothing fits."""


def _brief(pid: str) -> Dict[str, Any]:
    lp = D.load_long_profile(pid)
    lp = lp.get("long_profile", lp) if isinstance(lp, dict) else {}
    prof = D.short_profiles()[D.idx_of(pid)] if hasattr(D, "short_profiles") else {}
    ident = (prof.get("identity") if isinstance(prof, dict) else None) or {}
    return {"name": ident.get("name") or "the patient", "gender": ident.get("gender") or "",
            "age": ident.get("age") or "", "summary": (lp or {}).get("brief_summary") or ""}


def guidance(pid: str, trainee: str, reply: str, active: List[str], path: List[List[str]],
             earlier: List[str], net: Dict[str, Any], spans: List[Dict[str, Any]]) -> Dict[str, Any]:
    from patienttrace.angel_ext.llm import get_client
    nodes = {n["id"]: n for n in net["nodes"]}
    ev = {(e["source"], e["target"]): e.get("evidence") or e.get("label") or "" for e in net["edges"]}
    b = _brief(pid)
    lines = [f"PATIENT: {b['name']}, {b['age']}, {b['gender']}. {b['summary']}",
             "", "NETWORK NODES (id | type | label):"]
    lines += [f"{n['id']} | {n['type']} | {n['label']}" for n in net["nodes"]]
    lines += ["", f"TRAINEE SAID: {trainee}", f"PATIENT REPLIED: {reply}", "",
              "ACTIVE IN THIS REPLY (id | quote):"]
    q = {s["node"]: s["text"] for s in spans}
    lines += [f"{a} | {q.get(a, '(implied)')}" for a in active] or ["(none)"]
    lines += ["", "LINKS BETWEEN ACTIVE NODES (from -> to | evidence from the profile):"]
    lines += [f"{a} -> {c} | {ev.get((a, c), '')}" for a, c in path] or ["(none)"]
    lines += ["", "EARLIER_ACTIVE (nodes seen in earlier replies, not active now):",
              ", ".join(e for e in earlier if e not in active) or "(none)"]
    llm = get_client()
    try:
        r = llm.chat(llm.method_model, system=SYSTEM, user="\n".join(lines), json_mode=True,
                     max_tokens=1500, tag=GUIDANCE_VERSION)
        j = r.json if isinstance(r.json, dict) else {}
    except Exception as exc:  # noqa: BLE001  API failure: the page still shows the alignment
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    clean = lambda k, n=400: re.sub(r"\s*\((?:n\d+(?:,\s*)?)+\)|\bn\d+\b", "", str(j.get(k) or "")).strip()[:n]   # no stray node ids
    out = {k: clean(k) for k in ("title", "surface", "under", "tip", "instead")}
    out["title"] = out["title"][:60] or "Reply"
    out["instead"] = out["instead"].strip('"“”')
    t = j.get("trigger") if isinstance(j.get("trigger"), dict) else None
    if t and t.get("node") in nodes and nodes[t["node"]].get("type") == "Stimulus" \
            and str(t.get("phrase") or "").strip() and str(t["phrase"]).lower() in trainee.lower():
        out["trigger"] = {"phrase": str(t["phrase"]).strip(), "node": t["node"], "source": "llm"}
    out["eased"] = [x for x in (j.get("eased") or []) if x in nodes and x in earlier and x not in active]
    out["model"] = llm.method_model
    out["version"] = GUIDANCE_VERSION
    return out


def enrich_turn(pid: str, trainee: str, reply: str, alignment: Dict[str, Any], earlier: List[str],
                with_guidance: bool = True) -> Dict[str, Any]:
    """alignment = the /api/ext/align view ({nodes:[{node_id, evidence, ...}], edges:[...]})."""
    net = D.load_network(pid)
    ids = {n["id"] for n in net["nodes"]}
    anodes = [n for n in (alignment.get("nodes") or []) if n.get("node_id") in ids]
    active = list(dict.fromkeys(n["node_id"] for n in anodes))
    aset = set(active)
    path = [[e["source"], e["target"]] for e in net["edges"] if e["source"] in aset and e["target"] in aset and e["source"] != e["target"]]
    spans = spans_for(reply, anodes)
    trig = match_trigger(trainee, net)
    g = guidance(pid, trainee, reply, active, path, earlier, net, spans) if with_guidance else {}
    if not trig and g.get("trigger"):
        trig = g["trigger"]
    eased = g.get("eased") or []
    return {"active": active, "spans": spans, "path": path, "trigger": trig, "eased": eased,
            "guidance": {k: g.get(k) for k in ("title", "surface", "under", "tip", "instead", "model", "version", "error") if g.get(k)},
            "alignment": {"nodes": anodes, "edges": alignment.get("edges") or [], "no_match": bool(alignment.get("no_match"))}}


LABELS_SYSTEM = """Give each symptom-network node a short display label for a small diagram.
Rules: at most 18 characters; keep the meaning; plain words; thoughts in quotes if they are first-person
beliefs (e.g. \u201cHeart failing\u201d); no ellipsis. Return only a JSON object {node id: short label}."""


def short_labels(pid: str) -> Dict[str, str]:
    """Short labels for the Interview network. Shipped file data/interview/labels_pNNN.json wins; otherwise
    one cached LLM call (state/interview/labels_pNNN.json). Labels over 18 characters are dropped (the
    page then truncates the full label)."""
    import os
    from patienttrace import paths as P
    for path in (P.data("interview", f"labels_{pid}.json"), P.state("interview", f"labels_{pid}.json")):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AZURE_FOUNDRY_API_KEY")):
        return {}                                        # no model configured (browse mode): the page truncates labels
    net = D.load_network(pid)
    from patienttrace.angel_ext.llm import get_client
    llm = get_client()
    user = "\n".join(f"{n['id']} | {n['type']} | {n['label']}" for n in net["nodes"])
    r = llm.chat(llm.method_model, system=LABELS_SYSTEM, user=user, json_mode=True, max_tokens=3000, tag="interview-labels-v1")
    j = r.json if isinstance(r.json, dict) else {}
    ids = {n["id"] for n in net["nodes"]}
    out = {k: str(v).strip() for k, v in j.items() if k in ids and 0 < len(str(v).strip()) <= 18}
    path = P.state("interview", f"labels_{pid}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


# =====================================================================================================
# Interview aligner v1 (used only by the Interview page; the paper's frozen F1 aligner is not touched).
# Sees the previous trainee message, the last two turns and every node with its type and theme; returns
# per node {node, span, kind: said|implied, confidence}, or theme-level matches when no node fits.
# =====================================================================================================
ALIGN_VERSION = "interview-align-v2"
GUIDANCE2_VERSION = "interview-guidance-v2"
THRESH = {"normal": {"said": 0.5, "implied": 0.6, "theme": 0.6}, "low": {"said": 0.3, "implied": 0.4, "theme": 0.4}}

ALIGN_SYSTEM = """You map one reply of a simulated patient onto the patient's symptom network.
For each network node the reply expresses, give:
  "kind": "said"     the reply puts it into words (give "span": the exact words copied from the reply), or
          "implied"  clearly conveyed but not said in so many words (span optional),
  "confidence": 0..1 that this node is really expressed in THIS reply (not just earlier in the session).
Use the conversation context to resolve what the reply refers to ("it", "that", short answers).
Onset, origin and cause: when the reply says when or why the problem started ("since the accident",
"it started when I turned 35", "after my mother died"), map it to the Stimulus node for that event or
circumstance, not to the behaviour or feeling it led to. Example: "Since the accident." answering "How long
has this been going on?" -> the Stimulus node about the accident (kind "said", span "Since the accident"),
not "Avoids cars and travel". Networks often have no node for the original event itself; then use the Stimulus
node most directly tied to that event (e.g. "Accident-related thoughts" or "Accident-related conversations"
for the accident), kind "implied", span still the onset words. Only when no Stimulus node relates to the
event or circumstance at all, fall back to a theme.
Feelings: emotion words ("scared", "afraid", "terrified", "anxious", "panicky", "guilty", "ashamed", "worried
sick") name an Emotion node. List the matching Emotion node (kind "said", span the emotion word or phrase) IN
ADDITION to any Thought node for what the fear is about. Example: "I'm scared I'd cause an accident" -> the
Emotion node for fear or dread of causing harm (span "I'm scared") AND the Cognition node about causing an
accident. A described fear reaction without the word (dreading going out, being too frightened to get in a car)
is the Emotion node, kind "implied". Body sensations alone (shaking, heart racing) are Physiological Sensation
nodes, not Emotion nodes.
If no single node fits but one of the THEMES clearly does, list the theme under "themes" instead.
Also say which Stimulus node, if any, the TRAINEE'S QUESTION touched on (what set the reply off), with the
exact words from the question.
Return only JSON: {"nodes": [{"node": "<node id>", "span": "<exact words or empty>", "kind": "said|implied",
"confidence": 0.0}], "themes": [{"theme": "<theme id>", "span": "<exact words or empty>", "confidence": 0.0}],
"trigger": {"node": "<Stimulus node id>", "phrase": "<exact words from the trainee's question>"} or null,
"eased": ["ids from EARLIER_ACTIVE that this reply shows loosening or easing (e.g. relief, a softer belief)"]}
Use only ids from the lists. Prefer precision: a node the reply does not express must not be listed."""
ALIGN_LOW = """
This is a second, more inclusive pass: the first pass found nothing. Also list nodes or themes that are
plausible readings of the reply (confidence 0.35 or more), still only ids from the lists."""


def themes_of(pid: str) -> List[Dict[str, Any]]:
    """The Network page's themes (raw long-graph groups) re-keyed to SPEC node ids; ungrouped nodes alone."""
    from patienttrace import paths as P
    import os
    net = D.load_network(pid)
    by_label = {n["label"]: n["id"] for n in net["nodes"]}
    groups = []
    try:
        with open(D.precomp_path(pid, "raw_network.json"), encoding="utf-8") as f:
            groups = json.load(f).get("groups") or []
    except Exception:  # noqa: BLE001
        pass
    if not groups:
        gp = D.precomp_path(pid, "groups.json")
        if os.path.exists(gp):
            with open(gp, encoding="utf-8") as f:
                groups = json.load(f).get("groups") or []
    out, seen = [], set()
    for g in groups:
        ids = [by_label[m] for m in g.get("members") or [] if m in by_label and by_label[m] not in seen]
        seen.update(ids)
        if ids:
            out.append({"id": f"t{len(out)}", "label": g.get("label") or "", "type": g.get("type"), "members": ids})
    for n in net["nodes"]:
        if n["id"] not in seen:
            out.append({"id": f"t{len(out)}", "label": n["label"], "type": n["type"], "members": [n["id"]]})
    return out


def align_v2(pid: str, reply: str, trainee: str, history: List[Dict[str, str]], level: str = "normal",
             earlier: Optional[List[str]] = None) -> Dict[str, Any]:
    from patienttrace.angel_ext.llm import get_client
    net, themes = D.load_network(pid), themes_of(pid)
    nodes = {n["id"]: n for n in net["nodes"]}
    tof = {m: t for t in themes for m in t["members"]}
    b = _brief(pid)
    lines = [f"PATIENT: {b['name']}, {b['age']}, {b['gender']}. {b['summary']}", "", "THEMES (id | type | label):"]
    lines += [f"{t['id']} | {t['type']} | {t['label']}" for t in themes]
    lines += ["", "NODES (id | type | theme | label):"]
    lines += [f"{n['id']} | {n['type']} | {tof[n['id']]['label']} | {n['label']}" for n in net["nodes"]]
    lines += ["", "CONTEXT (last turns before this one):"]
    lines += [f"{'Trainee' if h.get('who') == 't' else 'Patient'}: {h.get('text', '')}" for h in (history or [])[-4:]] or ["(start of the session)"]
    lines += ["", "EARLIER_ACTIVE (nodes expressed in earlier replies of this session):", ", ".join(earlier or []) or "(none)"]
    lines += ["", f"TRAINEE'S QUESTION: {trainee}", f"PATIENT'S REPLY: {reply}"]
    llm = get_client()
    r = llm.chat(llm.method_model, system=ALIGN_SYSTEM + (ALIGN_LOW if level == "low" else ""), user="\n".join(lines),
                 json_mode=True, max_tokens=2000, tag=f"{ALIGN_VERSION}.{level}")
    j = r.json if isinstance(r.json, dict) else {}
    th = THRESH.get(level, THRESH["normal"])
    out_nodes, taken = [], []
    for x in j.get("nodes") or []:
        nid, kind = x.get("node"), x.get("kind") if x.get("kind") in ("said", "implied") else "implied"
        try:
            conf = float(x.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        if nid not in nodes or any(o["node"] == nid for o in out_nodes):
            continue
        loc = locate(str(x.get("span") or ""), reply) if x.get("span") else None
        if loc and any(not (loc[1] <= a or loc[0] >= c) for a, c in taken):
            loc = None                                   # overlapping underline: keep the node, drop the span
        if kind == "said" and not loc:
            kind = "implied"                             # a 'said' node must point at words in the reply
        if conf < th[kind]:
            continue
        if loc:
            taken.append(loc)
        out_nodes.append({"node": nid, "kind": kind, "confidence": round(conf, 2),
                          **({"span": {"start": loc[0], "end": loc[1], "text": reply[loc[0]:loc[1]]}} if loc else {})})
    out_themes = []
    if not out_nodes:                                    # theme-level matches only when no node fits
        tids = {t["id"]: t for t in themes}
        for x in j.get("themes") or []:
            try:
                conf = float(x.get("confidence") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            if x.get("theme") in tids and conf >= th["theme"] and not any(o["theme"] == x["theme"] for o in out_themes):
                loc = locate(str(x.get("span") or ""), reply) if x.get("span") else None
                out_themes.append({"theme": x["theme"], "label": tids[x["theme"]]["label"], "members": tids[x["theme"]]["members"],
                                   "confidence": round(conf, 2), **({"span": {"start": loc[0], "end": loc[1], "text": reply[loc[0]:loc[1]]}} if loc else {})})
    trig = None
    t = j.get("trigger") if isinstance(j.get("trigger"), dict) else None
    if t and t.get("node") in nodes and nodes[t["node"]].get("type") == "Stimulus":
        ph = str(t.get("phrase") or "").strip()
        loc = locate(ph, trainee) if ph else None
        if loc:
            trig = {"phrase": trainee[loc[0]:loc[1]], "node": t["node"], "source": "aligner"}
    act = {o["node"] for o in out_nodes}
    eased = [x for x in (j.get("eased") or []) if x in nodes and x in (earlier or []) and x not in act]
    return {"version": ALIGN_VERSION, "level": level, "nodes": out_nodes, "themes": out_themes, "trigger": trig,
            "eased": list(dict.fromkeys(eased)), "model": llm.method_model}


GUIDE2_SYSTEM = SYSTEM.replace("and the nodes an automatic aligner found in the reply.",
    "and the nodes (or themes) an aligner matched to the reply. Base the explanation ONLY on those matched\n"
    "nodes/themes and the links among them; do not bring in other network nodes. 'implied' nodes were not said\n"
    "outright: say so ('likely'). If nothing matched, explain the reply on its own terms and note that it did not\n"
    "map onto the network.")


def guidance_v2(pid, trainee, reply, al, path, trig, earlier, net) -> Dict[str, Any]:
    from patienttrace.angel_ext.llm import get_client
    nodes = {n["id"]: n for n in net["nodes"]}
    ev = {(e["source"], e["target"]): e.get("evidence") or e.get("label") or "" for e in net["edges"]}
    b = _brief(pid)
    lines = [f"PATIENT: {b['name']}, {b['age']}, {b['gender']}. {b['summary']}", "",
             f"TRAINEE SAID: {trainee}", f"PATIENT REPLIED: {reply}", "", "MATCHED NODES (id | type | kind | confidence | words | label):"]
    lines += [f"{x['node']} | {nodes[x['node']]['type']} | {x['kind']} | {x['confidence']} | {x.get('span', {}).get('text', '')} | {nodes[x['node']]['label']}"
              for x in al["nodes"]] or ["(none)"]
    if al["themes"]:
        lines += ["", "THEME-LEVEL MATCHES (no single node fitted):"] + [f"{t['theme']} | {t['label']} | {t['confidence']}" for t in al["themes"]]
    if trig:
        lines += ["", f"TRIGGER in the trainee's message: \"{trig['phrase']}\" -> {nodes[trig['node']]['label']} ({trig['node']})"]
    lines += ["", "LINKS AMONG THESE NODES (from -> to | evidence from the profile):"]
    lines += [f"{a} -> {c} | {ev.get((a, c), '')}" for a, c in path] or ["(none)"]
    act = [x["node"] for x in al["nodes"]]
    if al.get("eased"):
        lines += ["", "EASED (earlier nodes this reply shows loosening):"] + [f"{x} | {nodes[x]['label']}" for x in al["eased"]]
    lines += ["", "EARLIER_ACTIVE (nodes seen in earlier replies, not matched now):", ", ".join(e for e in earlier if e not in act) or "(none)"]
    llm = get_client()
    try:
        r = llm.chat(llm.method_model, system=GUIDE2_SYSTEM, user="\n".join(lines), json_mode=True, max_tokens=1500, tag=GUIDANCE2_VERSION)
        j = r.json if isinstance(r.json, dict) else {}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    clean = lambda k, n=400: re.sub(r"\s*\((?:[nt]\d+(?:,\s*)?)+\)|\b[nt]\d+\b", "", str(j.get(k) or "")).strip()[:n]
    out = {k: clean(k) for k in ("title", "surface", "under", "tip", "instead")}
    out["title"] = out["title"][:60] or "Reply"
    out["instead"] = out["instead"].strip('"“”')
    t = j.get("trigger") if isinstance(j.get("trigger"), dict) else None
    if t and t.get("node") in nodes and nodes[t["node"]].get("type") == "Stimulus" \
            and str(t.get("phrase") or "").strip() and str(t["phrase"]).lower() in trainee.lower():
        out["trigger"] = {"phrase": str(t["phrase"]).strip(), "node": t["node"], "source": "llm"}
    out["eased"] = [x for x in (j.get("eased") or []) if x in nodes and x in earlier and x not in act]
    out["model"], out["version"] = llm.method_model, GUIDANCE2_VERSION
    return out


def enrich_turn_v2(pid: str, trainee: str, reply: str, history: List[Dict[str, str]], earlier: List[str],
                   level: str = "normal") -> Dict[str, Any]:
    """Interview turn from the interview aligner: active (said + implied), kinds, spans, theme-level
    matches, trigger, path = network edges among {trigger} U matched nodes, a dotted trigger link when
    the network has no edge from the trigger to a matched node, eased, and guidance written from the match."""
    net = D.load_network(pid)
    nodes = {n["id"]: n for n in net["nodes"]}
    import time as _t
    t0 = _t.time()
    al = align_v2(pid, reply, trainee, history, level, earlier)
    t1 = _t.time()
    active = [x["node"] for x in al["nodes"]]
    kinds = {x["node"]: x["kind"] for x in al["nodes"]}
    conf = {x["node"]: x["confidence"] for x in al["nodes"]}
    spans = sorted([{**x["span"], "node": x["node"]} for x in al["nodes"] if x.get("span")], key=lambda s: s["start"])
    trig = al.get("trigger") or match_trigger(trainee, net)     # aligner first; word match as fallback
    shown = set(active) | ({trig["node"]} if trig else set())
    path = [[e["source"], e["target"]] for e in net["edges"] if e["source"] in shown and e["target"] in shown and e["source"] != e["target"]]
    g = guidance_v2(pid, trainee, reply, al, path, trig, earlier, net)
    t2 = _t.time()
    if not trig and g.get("trigger"):                    # the LLM's (validated) trigger proposal
        trig = g["trigger"]
        shown.add(trig["node"])
        path = [[e["source"], e["target"]] for e in net["edges"] if e["source"] in shown and e["target"] in shown and e["source"] != e["target"]]
    trig_link = None
    if trig and active and not any(a == trig["node"] for a, _ in path):
        COLS = ["Stimulus", "Cognition", "Emotion", "Physiological Sensation", "Behavior"]
        first = sorted(active, key=lambda n: COLS.index(nodes[n]["type"]) if nodes[n]["type"] in COLS else 9)[0]
        trig_link = {"from": trig["node"], "to": first, "dotted": True}
    return {"active": active, "kinds": kinds, "confidence": conf, "spans": spans, "themes": al["themes"],
            "path": path, "trigger": trig, "trigger_link": trig_link, "eased": al.get("eased") or g.get("eased") or [],
            "guidance": {k: g.get(k) for k in ("title", "surface", "under", "tip", "instead", "model", "version", "error") if g.get(k)},
            "alignment": al, "timing": {"alignment_ms": round((t1 - t0) * 1000), "guidance_ms": round((t2 - t1) * 1000)}}
