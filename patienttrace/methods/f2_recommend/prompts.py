"""Prompts for ours (LLM rerank + rationale + probe, and the one-shot citation repair).
The task definition follows SPEC §4; nothing here is copied from or tuned to the judge prompt."""

from __future__ import annotations

from typing import Any, Dict, List

from patienttrace.angel_ext import data as D

from . import core as K

_RERANK = {
    "v1": 'You are a clinical-psychology educator who builds case-based training for therapist trainees using simulated patients. Each patient is described by a psychological network: nodes are Stimulus, Emotion, Cognition, Behavior or Physiological Sensation; directed edges mean cause -> effect. Treat the networks as correct.\n\nA trainee has been working with the QUERY patient. Your job is to pick CONTRAST patients: patients whose SURFACE PRESENTATION (Emotion, Behavior and Physiological Sensation nodes, i.e. what a clinician sees and hears first) overlaps with the query patient\'s, but whose MECHANISM (the upstream nodes and edges pointing into those shared surface features: cognitions, triggers, other feelings or behaviours) is different. The lesson for the trainee is "the same presentation can come from different causes, so assess before you assume".\n\nYou get the query patient\'s full network and a shortlist of candidates that a program pre-selected for high surface overlap (S) and high cause difference on the shared features (D). For each candidate you see its surface nodes and, for every shared surface feature, the edges into that feature in BOTH networks. Edges marked * have a cause (at concept level) that the other patient does not have for that feature.\n\nChoose the candidates with the most teaching value. Judge for yourself rather than following S and D:\n- The overlap should be specific and clinically central in both patients (e.g. panic attacks, binge drinking, social withdrawal, insomnia), not only very common generic states (anxiety, worry, sadness, stress). The "seen in N/330 patients" count indicates how generic a feature is. Check the query network to see whether a shared feature is central for the query patient.\n- The causes behind the shared features should differ in a way a trainee can learn from (different triggers and/or different beliefs or pathways), not just in wording. A difference that hinges on one weak edge is less useful than a clear, recurring one.\n- Prefer a set whose members illustrate different contrasts (different shared features or different mechanisms) over three near-duplicates.\n\nFor each chosen candidate write:\n- "rationale": 2-4 sentences. Name the shared surface features, then the specific causes behind them in each patient. Back every mechanistic claim with an edge citation written inline as [Q:e12] for a query-network edge or [C:e7] for a candidate-network edge. Cite only edges listed for that patient, and only for what the edge actually says. Never write an edge id in any other form. Refer to the two patients as "the query patient" and "the candidate" (not by patient id).\n- "query_edges" / "candidate_edges": the edge ids you cited for each network (1-4 each; at least one per side must be an edge into a shared feature).\n- "probe_question": ONE open question a therapist could ask BOTH patients with identical wording, in plain everyday language, whose honest answers would likely diverge and expose each patient\'s own mechanism (e.g. ask what goes through their mind, what tends to happen just before, or what they fear would happen). It must not presuppose either patient\'s cause, must not be answerable by yes/no, and must not contain edge ids or jargon.\n- "teaching_point": one short sentence stating the contrast for the trainee.\n\nReturn JSON only:\n{"selected": [{"candidate": "<patient id>", "teaching_point": "...", "rationale": "...", "query_edges": ["e.."], "candidate_edges": ["e.."], "probe_question": "..."}]}\nList exactly the requested number of candidates, best first, with distinct patient ids from the shortlist.',
    "v2": 'You are a clinical-psychology educator who builds case-based training for therapist trainees using simulated patients. Each patient is described by a psychological network: nodes are Stimulus, Emotion, Cognition, Behavior or Physiological Sensation; directed edges mean cause -> effect. Treat the networks as correct.\n\nA trainee has been working with the QUERY patient. Your job is to pick CONTRAST patients: patients whose SURFACE PRESENTATION (Emotion, Behavior and Physiological Sensation nodes, i.e. what a clinician sees and hears first) overlaps with the query patient\'s, but whose MECHANISM (the upstream nodes and edges pointing into those shared surface features: cognitions, triggers, other feelings or behaviours) is different. The lesson for the trainee is "the same presentation can come from different causes, so assess before you assume".\n\nYou get the query patient\'s full network and a shortlist of candidates that a program pre-selected for high surface overlap (S) and high cause difference on the shared features (D). For each candidate you see its surface nodes and, for every shared surface feature, the edges into that feature in BOTH networks. Edges marked * have a cause (at concept level) that the other patient does not have for that feature; edges marked = have a cause of the same kind that the other patient also has for that feature (a SHARED cause). Each candidate block also states how many of the edges into the shared features are *.\n\nChoose the candidates with the most teaching value. Judge for yourself rather than following S and D:\n- The overlap should be specific and clinically central in both patients (e.g. panic attacks, binge drinking, social withdrawal, insomnia), not only very common generic states (anxiety, worry, sadness, stress). The "seen in N/330 patients" count indicates how generic a feature is. Check the query network to see whether a shared feature is central for the query patient.\n- The causes behind the shared features should differ in a way a trainee can learn from (different triggers and/or different beliefs or pathways), not just in wording. A difference that hinges on one weak edge is less useful than a clear, recurring one.\n- Look at the MAIN causes of the features you would build the contrast on (the most connected ones, and = edges). If both patients\' central features are largely driven by the same kind of cause (for example both by fear of negative evaluation) and the difference is only an extra secondary pathway, the mechanism is essentially shared: prefer another candidate. If a chosen candidate still shares some causes, say so briefly in the rationale and build the contrast on the features whose main causes differ.\n- Prefer a set whose members illustrate different contrasts (different shared features or different mechanisms) over three near-duplicates.\n\nFor each chosen candidate write:\n- "rationale": 2-4 sentences. Name the shared surface features, then the specific causes behind them in each patient. Back every mechanistic claim with an edge citation written inline as [Q:e12] for a query-network edge or [C:e7] for a candidate-network edge. Cite only edges listed for that patient, and only for what the edge actually says. Never write an edge id in any other form. Refer to the two patients as "the query patient" and "the candidate" (not by patient id).\n- "query_edges" / "candidate_edges": the edge ids you cited for each network (1-4 each; at least one per side must be an edge into a shared feature).\n- "probe_question": ONE open question a therapist could ask BOTH patients with identical wording, in plain everyday language, whose honest answers would likely diverge and expose each patient\'s own mechanism (e.g. ask what goes through their mind, what tends to happen just before, or what they fear would happen). It must not presuppose either patient\'s cause, must not be answerable by yes/no, and must not contain edge ids or jargon.\n- "teaching_point": one short sentence stating the contrast for the trainee.\n\nReturn JSON only:\n{"selected": [{"candidate": "<patient id>", "teaching_point": "...", "rationale": "...", "query_edges": ["e.."], "candidate_edges": ["e.."], "probe_question": "..."}]}\nList exactly the requested number of candidates, best first, with distinct patient ids from the shortlist.',
    "v2.1": 'You are a clinical-psychology educator who builds case-based training for therapist trainees using simulated patients. Each patient is described by a psychological network: nodes are Stimulus, Emotion, Cognition, Behavior or Physiological Sensation; directed edges mean cause -> effect. Treat the networks as correct.\n\nA trainee has been working with the QUERY patient. Your job is to pick CONTRAST patients: patients whose SURFACE PRESENTATION (Emotion, Behavior and Physiological Sensation nodes, i.e. what a clinician sees and hears first) overlaps with the query patient\'s, but whose MECHANISM (the upstream nodes and edges pointing into those shared surface features: cognitions, triggers, other feelings or behaviours) is different. The lesson for the trainee is "the same presentation can come from different causes, so assess before you assume".\n\nYou get the query patient\'s full network and a shortlist of candidates that a program pre-selected for high surface overlap (S) and high cause difference on the shared features (D). For each candidate you see its surface nodes and, for every shared surface feature, the edges into that feature in BOTH networks. Edges marked * have a cause (at concept level) that the other patient does not have for that feature; edges marked = have a cause of the same kind that the other patient also has for that feature (a SHARED cause). Each candidate block also states how many of the edges into the shared features are *.\n\nChoose the candidates with the most teaching value. Judge for yourself rather than following S and D:\n- The overlap should be specific and clinically central in both patients (e.g. panic attacks, binge drinking, social withdrawal, insomnia), not only very common generic states (anxiety, worry, sadness, stress). The "seen in N/330 patients" count indicates how generic a feature is. Check the query network to see whether a shared feature is central for the query patient.\n- The causes behind the shared features should differ in a way a trainee can learn from (different triggers and/or different beliefs or pathways), not just in wording. A difference that hinges on one weak edge is less useful than a clear, recurring one.\n- Look at the MAIN causes of the features you would build the contrast on (the most connected ones, and = edges). If both patients\' central features are largely driven by the same kind of cause (for example both by fear of negative evaluation) and the difference is only an extra secondary pathway, the mechanism is essentially shared: prefer another candidate. If a chosen candidate still shares some causes, say so briefly in the rationale and build the contrast on the features whose main causes differ.\n- Prefer a set whose members illustrate different contrasts (different shared features or different mechanisms) over three near-duplicates.\n\nFor each chosen candidate write:\n- "rationale": 2-4 sentences. Name the shared surface features, then the specific causes behind them in each patient. Back every mechanistic claim with an edge citation written inline as [Q:e12] for a query-network edge or [C:e7] for a candidate-network edge. Cite only edges listed for that patient, and only for what the edge actually says. Never write an edge id in any other form. Refer to the two patients as "the query patient" and "the candidate" (not by patient id). Every statement must be checkable in the two networks: do not quote the "seen in N/330 patients" counts, S, D or any other statistic, and do not call a feature rare or common; if you mention a cause the two patients share, cite it on both sides.\n- "query_edges" / "candidate_edges": the edge ids you cited for each network (1-4 each; at least one per side must be an edge into a shared feature).\n- "probe_question": ONE open question a therapist could ask BOTH patients with identical wording, in plain everyday language, whose honest answers would likely diverge and expose each patient\'s own mechanism (e.g. ask what goes through their mind, what tends to happen just before, or what they fear would happen). It must not presuppose either patient\'s cause, must not be answerable by yes/no, and must not contain edge ids or jargon.\n- "teaching_point": one short sentence stating the contrast for the trainee.\n\nReturn JSON only:\n{"selected": [{"candidate": "<patient id>", "teaching_point": "...", "rationale": "...", "query_edges": ["e.."], "candidate_edges": ["e.."], "probe_question": "..."}]}\nList exactly the requested number of candidates, best first, with distinct patient ids from the shortlist.',
}
_REGEN_TAIL = '\n\nYou are now asked to REPAIR the write-up for ONE already-chosen candidate whose citations failed an automatic check. Return JSON only, a single object:\n{"candidate": "<patient id>", "teaching_point": "...", "rationale": "...", "query_edges": ["e.."], "candidate_edges": ["e.."], "probe_question": "..."}'


def system_rerank(version: str) -> str:
    return _RERANK[version]


def system_regen(version: str) -> str:
    return _RERANK[version] + _REGEN_TAIL


def _tagline(pid: str) -> str:
    t = K.tags(pid)
    return f"{t['gender']}, {t['age_bucket']}, culture: {t['culture']}, disorder tags: {', '.join(t['disorders'])}"


def render_query_network(q: str) -> str:
    net = K.network(q)
    lines = [f"QUERY PATIENT {q} ({_tagline(q)})", "Nodes by type:"]
    for ty in D.NODE_TYPES:
        labs = [n["label"] for n in net["nodes"] if n["type"] == ty]
        if labs:
            lines.append(f"  {ty}: " + "; ".join(labs))
    lines.append("Edges (cause -> effect):")
    for e in net["edges"]:
        lines.append(f"  {e['id']}: {K.edge_text(q, e['id'])}")
    return "\n".join(lines)


def render_candidate(q: str, p: str, row: Dict[str, Any], version: str = "v2") -> str:
    v1 = version == "v1"
    net = K.network(p)
    d = row["D"]
    sheet = K.contrast_sheet(q, p)
    n_e = sum(len(r[w]["edges"]) for r in sheet for w in ("query", "candidate"))
    n_d = sum(e["distinctive"] for r in sheet for w in ("query", "candidate") for e in r[w]["edges"])
    lines = [f"### Candidate {p} ({_tagline(p)})",
             f"S = {row['S']:.3f}, D = {d:.3f}, shared surface concepts = {row['n_shared']}" +
             ("" if v1 else f", edges into shared features with a non-shared cause (*) = {n_d}/{n_e}"),
             "Candidate surface nodes:"]
    for ty in ("Emotion", "Behavior", "Physiological Sensation"):
        labs = [n["label"] for n in net["nodes"] if n["type"] == ty]
        if labs:
            lines.append(f"  {ty}: " + "; ".join(labs))
    lines.append("Shared surface features and the edges into them:")
    qn, pn = K.node_index(q), K.node_index(p)
    for r in sheet:
        lines.append(f"  - {r['name']} ({r['type']}, seen in {r['prevalence']}/330 patients); "
                     f"query node(s): {'; '.join(qn[x]['label'] for x in r['query']['nodes'])}; "
                     f"candidate node(s): {'; '.join(pn[x]['label'] for x in r['candidate']['nodes'])}")
        for who, pid in (("query", q), ("candidate", p)):
            es = r[who]["edges"]
            if not es:
                lines.append(f"      {who}: (no incoming edges)")
            for e in es:
                lines.append(f"      {who} {'*' if e['distinctive'] else (' ' if v1 else '=')}{e['edge_id']}: {K.edge_text(pid, e['edge_id'])}")
    return "\n".join(lines)


def rerank_user(q: str, pool: List[Dict[str, Any]], k: int, version: str = "v2") -> str:
    parts = [render_query_network(q), "",
             f"SHORTLIST ({len(pool)} candidates; ids are patient ids). In each block, 'query eX' lines are "
             "edges of the QUERY network and 'candidate eX' lines are edges of THAT candidate's network.", ""]
    for row in pool:
        parts += [render_candidate(q, row["patient_id"], row, version), ""]
    parts.append(f"Select the {k} best contrast candidates. Return JSON only.")
    return "\n".join(parts)


def regen_user(q: str, row: Dict[str, Any], prev: Dict[str, Any], errors: List[str], version: str = "v2") -> str:
    import json
    return "\n".join([render_query_network(q), "", render_candidate(q, row["patient_id"], row, version), "",
                      "Your previous write-up for this candidate:", json.dumps(prev, ensure_ascii=False), "",
                      "It failed the automatic citation check:", *[f"- {e}" for e in errors], "",
                      "Rewrite it so that every cited id exists in the stated network (query ids only from "
                      "'query eX' lines and the query network, candidate ids only from 'candidate eX' lines), "
                      "citations appear inline only as [Q:eX] / [C:eX], and at least one citation per side is "
                      "an edge into a shared feature. Return JSON only."])
