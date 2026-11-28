"""Text for the contrast picker: short "Both:" chip labels for the shared symptoms and ONE "Why her/him/them"
sentence per recommended patient, written from the ranking's teaching point. The sentence names two possible
mechanisms behind the shared symptom and the kind of question that separates them, without saying which
mechanism belongs to which patient. One model call per query patient, cached in state/fc/picker_<pid>.json
(a shipped copy in data/fc/ is used first). Without a model: the ranking's own teaching point and the shared
symptom names."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

from patienttrace import paths as P

PICK_SYSTEM = """You write one line for a trainee therapist choosing a patient to contrast with the one they are
interviewing. The two patients share surface symptoms but different mechanisms. For each candidate you get its
key, the candidate's pronoun, the shared symptoms, and the recommender's teaching point.
Return for each key:
  "chips": a short lower-case label (1-4 words, plain language, e.g. "sadness", "avoids social gatherings",
           "keeps people at a distance") for EACH shared symptom, in the order given;
  "why": ONE sentence (at most 32 words) that (a) names two possible mechanisms behind the main shared symptom
         and (b) suggests the kind of question that separates them, using the candidate's pronoun where natural
         (e.g. "Pulling away from people can be driven by low mood and loss, or by fear of being judged. Ask what
         she expects would happen if she went."). It must NOT say which mechanism belongs to which patient, must
         not name either patient, and must not be generic boilerplate.
Return only JSON: {"<key>": {"chips": [...], "why": "..."}, ...}"""


def _names(card: Dict[str, Any]) -> List[str]:
    out = []
    for x in card.get("shared_surface") or []:
        n = x.get("name") if isinstance(x, dict) else x
        if n:
            out.append(str(n))
    return out


def pronoun(gender: str) -> str:
    return {"female": "her", "male": "him"}.get((gender or "").strip().lower(), "them")


def _load(pid: str) -> Dict[str, Any]:
    for path in (P.state("fc", f"picker_{pid}.json"), P.data("fc", f"picker_{pid}.json")):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            continue
    return {}


def _fallback(card: Dict[str, Any]) -> Dict[str, Any]:
    chips = [re.sub(r"\s+", " ", n).strip().lower() for n in _names(card)]
    return {"chips": chips, "why": str(card.get("teaching_point") or "").strip(), "generated": False}


def picker_text(pid: str, cards: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """{candidate pid: {chips, why, generated}} for the given ranked cards."""
    from patienttrace.contrast import has_model
    cache = _load(pid)
    todo = [c for c in cards if c["patient_id"] not in cache]
    if todo and has_model():
        from patienttrace.angel_ext.llm import get_client
        lines = []
        for c in todo:
            pr = pronoun((c.get("patient") or {}).get("gender"))
            lines.append(f"{c['patient_id']} | pronoun: {pr} | shared: {'; '.join(_names(c))} | teaching point: {c.get('teaching_point') or ''}")
        llm = get_client()
        try:
            r = llm.chat(llm.method_model, system=PICK_SYSTEM, user="\n".join(lines), json_mode=True, max_tokens=1500, tag="fc-picker-v1")
            j = r.json if isinstance(r.json, dict) else {}
        except Exception:  # noqa: BLE001
            j = {}
        for c in todo:
            v = j.get(c["patient_id"])
            if isinstance(v, dict) and v.get("why"):
                n = len(_names(c))
                chips = [str(x).strip().lower() for x in (v.get("chips") or [])][:n]
                cache[c["patient_id"]] = {"chips": chips or _fallback(c)["chips"], "why": str(v["why"]).strip()[:300], "generated": True}
        path = P.state("fc", f"picker_{pid}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    return {c["patient_id"]: cache.get(c["patient_id"]) or _fallback(c) for c in cards}


# ---------------- replaying a trainee's Interview questions to the contrast patient ----------------
ADAPT_SYSTEM = """A trainee interviewed patient A and will now ask patient B the same questions (to compare how two
patients with the same presenting problem answer). For EACH question, in order:
  "adapted": the question for B: replace A's name with B's name and A's pronouns with B's; change nothing else;
  "flag": true if the question depends on details only A has told the trainee (A's own condition, events, people,
          places or phrases A used, e.g. "everything your heart has been through" when A has a heart condition),
          because B may not share them; false for questions that work for anyone with the presenting problem;
  "reason": for flagged questions, a few plain words naming the A-specific detail (e.g. "refers to her heart condition");
  "neutral": for flagged questions, the same question without the A-specific detail (still addressed to B).
Return only JSON: {"items": [{"adapted": "...", "flag": false, "reason": "", "neutral": ""}, ...]} with one item per question."""


def _swap_name(q: str, a: str, b: str) -> str:
    return re.sub(r"\b" + re.escape(a) + r"\b", b, q) if a and b else q


def adapt_questions(a_pid: str, b_pid: str, questions: List[str]) -> List[Dict[str, Any]]:
    """[{original, adapted, flag, reason, neutral}] for replaying A's Interview questions to B. Model (cached by
    prompt) when available; otherwise only A's name is replaced and nothing is flagged."""
    from patienttrace.angel_ext import data as D
    from patienttrace.contrast import has_model
    def ident(pid):
        x = D.short_profiles()[D.idx_of(pid)].get("identity") or {}
        return x.get("name") or pid, {"female": "she/her", "male": "he/him"}.get((x.get("gender") or "").lower(), "they/them")
    (an, ap), (bn, bp) = ident(a_pid), ident(b_pid)
    base = [{"original": q, "adapted": _swap_name(q, an, bn), "flag": False, "reason": "", "neutral": ""} for q in questions]
    if not questions or not has_model():
        return base
    from patienttrace.angel_ext.llm import get_client
    llm = get_client()
    user = f"Patient A: {an} ({ap}). Patient B: {bn} ({bp}).\nQuestions:\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    try:
        r = llm.chat(llm.method_model, system=ADAPT_SYSTEM, user=user, json_mode=True, max_tokens=2500, tag="fc-adapt-v1")
        items = (r.json or {}).get("items") if isinstance(r.json, dict) else None
    except Exception:  # noqa: BLE001
        items = None
    if not isinstance(items, list) or len(items) != len(questions):
        return base
    out = []
    for b0, it in zip(base, items):
        it = it if isinstance(it, dict) else {}
        adapted = str(it.get("adapted") or "").strip() or b0["adapted"]
        adapted = _swap_name(adapted, an, bn)                     # belt and braces: never A's name in B's question
        out.append({"original": b0["original"], "adapted": adapted, "flag": bool(it.get("flag")),
                    "reason": str(it.get("reason") or "").strip()[:120], "neutral": _swap_name(str(it.get("neutral") or "").strip(), an, bn)})
    return out
