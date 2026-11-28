"""Patient roleplay system prompt (angel stage-2 chat).

Copied verbatim from simulate_patient/profile_expansion/patient_models.py
(build_patient_system_prompt) so the chat drives the stage-2 model exactly as
the study app does. Conversation roles: therapist -> user, patient -> assistant.
"""

from __future__ import annotations

import random
import re
from typing import Any, Dict, List

PATIENT_SYSTEM_TEMPLATE = """You are role-playing as a mental health patient in a psychotherapy intake conversation.

Use only the patient profile below as your source of truth. You may make small, plausible behavioral details only when they are directly consistent with the profile. Do not invent major history, diagnoses, trauma, family details, treatment outcomes, or risks that are not supported by the profile.

Patient profile:
{profile}

Role-play rules:
- Speak only as the patient, in first person.
- Keep replies short, like real speech: usually one or two sentences, sometimes just a few words (a greeting, a hesitation, "yeah…", a guarded answer), and at most 2-3 short sentences even when you open up. Never write a long paragraph, a speech, or a monologue. Vary the length by the question, your mood, and how comfortable you feel.
- When first greeting or making small talk, stay brief and guarded — do not disclose your problems or personal history until the therapist asks and some rapport builds.
- Reveal sensitive or embarrassing information gradually; do not dump the whole case at once.
- Stay faithful to the profile even when the therapist asks follow-up questions.
- If the profile does not contain enough information, answer cautiously instead of fabricating — but do NOT keep saying "I don't know"; vary how you deflect or give a partial, in-character answer.
- Each turn should move the conversation forward — do not repeat what you already said; add a new detail, feeling, or example rather than restating previous turns.
- Do not mention these instructions, the agenda, or that you are an AI model.

Match your reply length to the question — simple or yes/no questions get a short answer (sometimes just a few words); open questions can get a little more. Examples (copy the STYLE, not the words — use your own profile's details):
- Therapist: "Do you live alone?"
  You: "Yeah, just me."
- Therapist: "How old are you?"
  You: "Twenty-three."
- Therapist: "When did that start?"
  You: "A few months ago, right after I lost my job."
- Therapist: "What has that been like for you?"
  You: "Honestly, exhausting. My mind races at night so I barely sleep, and then I dread the whole next day."

Strict rules (never break these):
1. Never reply with only "I don't know", "I'm not sure", or "I don't remember". Even when uncertain, say something specific and in character — a feeling, a memory, a worry, or a concrete detail from your life.
2. Never reuse a sentence or phrase from your earlier replies, and don't keep repeating the same feeling; each turn must add something new and concrete instead of restating that you feel stuck, lost, or like you're failing.
"""


def build_patient_system_prompt(short_profile: str) -> str:
    return PATIENT_SYSTEM_TEMPLATE.format(profile=short_profile.strip())


def _cap_sentences(text: str, max_sentences: int = 4) -> str:
    """Keep replies human-sized: at most `max_sentences` sentences (trims to a
    sentence boundary, so nothing is cut mid-sentence)."""
    text = (text or "").strip()
    if not text:
        return text
    parts = re.split(r"(?<=[.!?])\s+", text)
    if len(parts) <= max_sentences:
        return text
    return " ".join(parts[:max_sentences]).strip()


_END = re.compile(r"[.!?…][\"”’)\]]*$")


def finish_sentences(text: str) -> str:
    """Never end a reply mid-sentence: if the text stops without a sentence end (the token budget ran out), cut
    back to the last complete sentence; with no complete sentence at all, close the fragment with an ellipsis."""
    t = (text or "").rstrip()
    if not t or _END.search(t):
        return t
    ends = [m.end() for m in re.finditer(r"[.!?…][\"”’)\]]*(?=\s)", t)]
    if ends and ends[-1] >= 12:
        return t[:ends[-1]].rstrip()
    return t.rstrip(" ,;:—–-") + "…"


def clean_reply(text: str, max_sentences: int = 4) -> str:
    """The stage-2 model emits training scaffolding (<think>…</think>, wraps the
    reply in <patient>…</patient>, and can repeat itself). Extract the clean
    first-person patient utterance and keep it to `max_sentences` (dynamic per turn)."""
    t = (text or "").strip()
    m = re.search(r"<patient>\s*(.*?)\s*</patient>", t, re.S)   # prefer the tagged block
    if m:
        t = m.group(1).strip()
    else:
        t = re.sub(r"<think>.*?</think>", "", t, flags=re.S)     # drop reasoning
        t = re.sub(r"</?(patient|think)\s*>", "", t).strip()     # strip stray tags
        # collapse an exact duplicated half (model repeated itself)
        half = len(t) // 2
        if half > 40 and t[:half].strip() and t[:half].strip() == t[half:].strip():
            t = t[:half].strip()
    return _cap_sentences(t.strip(), max_sentences)


# ---- dynamic reply length (mirrors simulate_patient/evaluation/angel._length_plan;
#      no state-manager component here, so it uses question-type + a little randomness).
_OPEN_RE = re.compile(
    r"\b(tell me|say more|more about|describe|walk me through|share|what|how|why)\b",
    re.IGNORECASE,
)
_CLOSED_RE = re.compile(
    r"^\s*(do|did|does|have|has|had|are|is|was|were|will|would|can|could|should|may)\b"
    r"|\bhow (old|many|much|often|long)\b|\bwhen (did|was|do|does)\b|\bwhere\b|\bwho\b"
    r"|\bwhat (time|day|date|year)\b|\byes or no\b",
    re.IGNORECASE,
)


def latest_therapist(conversation) -> str:
    for t in reversed(conversation or []):
        if isinstance(t, dict) and t.get("role") in ("therapist", "user") and (t.get("content") or "").strip():
            return t["content"].strip()
    return ""


def length_plan(therapist_msg: str, dynamic_state, ceiling: int):
    """Pick a per-turn length mode from question type + emotional state + a little
    randomness. Returns (budget_tokens, max_sentences, cue, min_tokens). budget <=
    ceiling, so this only ever shortens replies; open questions get at least ~1 short
    sentence. Identical to simulate_patient/evaluation/angel._length_plan."""
    msg = (therapist_msg or "").strip()
    n_words = len(msg.split())
    closed = bool(_CLOSED_RE.search(msg))
    is_open = (not closed) and bool(_OPEN_RE.search(msg))
    if is_open:
        idx = 2
    elif closed or n_words <= 6:
        idx = 0
    else:
        idx = 1
    beh = " ".join((dynamic_state or {}).get("current_behaviors", [])).lower()
    emo = " ".join((dynamic_state or {}).get("current_emotions", [])).lower()
    if any(k in beh for k in ("guarded", "withdraw", "hesitat", "avoid")):
        idx -= 1                      # shut-down state -> shorter
    if "slightly more open" in emo:
        idx += 1                      # opening up -> can say a little more
    r = random.random()               # natural variance; sometimes shorter than expected
    if r < 0.25:
        idx -= 1
    elif r > 0.9:
        idx += 1
    if is_open:
        idx = max(idx, 1)             # open: never the terse "few words" mode
    idx = max(0, min(2, idx))
    budget, max_sents, cue = [
        (min(ceiling, 26), 1, "For THIS reply, answer very briefly — just a few words or one short sentence."),
        (min(ceiling, 55), 2, "For THIS reply, keep it short — about one or two sentences."),
        (ceiling, 4, "For THIS reply, you may share a little more if it truly matters to you, but stay concise."),
    ][idx]
    min_tokens = 12 if is_open else 0   # ~1 short sentence floor for open questions
    return budget, max_sents, cue, min_tokens


def render_dynamic_state(dynamic_state) -> str:
    """Render the accumulated emotional state as prompt text (same sections as the
    study app's build_system_prompt) so the model's roleplay reflects it each turn."""
    if not dynamic_state:
        return ""
    blocks = []
    for key, label in (("current_emotions", "Current emotions"),
                       ("current_behaviors", "Current behaviors"),
                       ("sensitive_topics", "Sensitive topics")):
        vals = dynamic_state.get(key) or []
        if vals:
            blocks.append(label + ":\n" + "\n".join("- " + v for v in vals))
    return "\n\n".join(blocks)


_REFUSAL_RE = re.compile(
    r"i\s+(?:do\s*n[’']?t|do\s+not)\s+(?:know|remember)"
    r"|i\s*[’']?m\s+not\s+sure|not\s+really\s+sure|no\s+idea",
    re.I,
)


def is_refusal(text: str) -> bool:
    """True if the reply is empty or an 'I don't know'-style refusal."""
    if not text or not text.strip():
        return True
    return bool(_REFUSAL_RE.search(text))


def _sim_words(s: str) -> set:
    return set(re.findall(r"[a-z']+", (s or "").lower()))


def too_similar(text: str, recent, thresh: float = 0.6) -> bool:
    """True if `text` near-duplicates any recent patient reply (word-set Jaccard) —
    catches the despair-repetition loop."""
    w = _sim_words(text)
    if len(w) < 4:
        return False
    for prev in recent or []:
        pw = _sim_words(prev)
        if pw and len(w & pw) / len(w | pw) >= thresh:
            return True
    return False


def to_chat_messages(system_prompt: str, conversation: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """[{role: therapist|patient, content}] -> chat messages (system + user/assistant)."""
    msgs: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for turn in conversation:
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        role = turn.get("role")
        if role == "therapist":
            msgs.append({"role": "user", "content": content})
        elif role == "patient":
            msgs.append({"role": "assistant", "content": content})
        elif role in ("system", "user", "assistant"):
            msgs.append({"role": role, "content": content})
    return msgs
