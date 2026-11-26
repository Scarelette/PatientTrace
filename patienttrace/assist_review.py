"""Per-field writing assist + whole-profile quality review, for the demo form.

Logic (prompts, modes, output shapes) is ported from simulation_annotator's
llm_assist.py and profile_review.py, but the LLM backend is claude-fable-5 (the
same Anthropic endpoint the network-model generation uses) so Patient_demo needs
no OpenAI/Azure config and no cross-repo imports.
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, List

from patienttrace.demo_config import PROFILE_FIELDS
from patienttrace.network_model import _llm   # shared provider-dispatching call (fast GPT or claude)

# Assist/review are single quick calls — default to the fast GPT model so the
# per-field ✨ Assist feels snappy. Override with DEMO_ASSIST_MODEL.
MODEL = os.environ.get("DEMO_ASSIST_MODEL", os.environ.get("DEMO_FAST_MODEL", "gpt-4o-2"))
ASSIST_MAX_TOKENS = int(os.environ.get("DEMO_ASSIST_MAX_TOKENS", "700"))
REVIEW_MAX_TOKENS = int(os.environ.get("DEMO_REVIEW_MAX_TOKENS", "3000"))


# Demo extension build (scripts/serve_ext.sh sets DEMO_ASSIST_BACKEND=foundry): route through the Azure
# Foundry client every other extension call uses (no Azure OpenAI deployment exists there), with a hard
# wall-clock cap so the form never hangs. The live demo keeps the default path above.
ASSIST_BACKEND = os.environ.get("DEMO_ASSIST_BACKEND", "").lower()
ASSIST_TIMEOUT_S = float(os.environ.get("DEMO_ASSIST_TIMEOUT_S", "10"))
REVIEW_TIMEOUT_S = float(os.environ.get("DEMO_REVIEW_TIMEOUT_S", "25"))   # whole-profile review needs ~14 s
_POOL = None


def _call(system: str, user: str, max_tokens: int, timeout: float = None) -> str:
    if ASSIST_BACKEND != "foundry":
        return _llm(MODEL, system, user, max_tokens=max_tokens)
    global _POOL
    from concurrent.futures import ThreadPoolExecutor
    from patienttrace.angel_ext.llm import get_client
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="assist")
    llm = get_client()
    model = os.environ.get("DEMO_ASSIST_MODEL") or llm.method_model
    fut = _POOL.submit(lambda: llm.chat(model, user=user, system=system, max_tokens=max_tokens,
                                        effort="low", tag="demo.assist", cache=False).text)
    return fut.result(timeout=timeout or ASSIST_TIMEOUT_S)     # concurrent.futures.TimeoutError -> friendly error


def _log_failure(kind: str, exc: Exception) -> None:
    import sys
    print(f"[assist] {kind} failed: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr, flush=True)


def _parse_json(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        brace = re.search(r"\{.*\}", cleaned, re.S)
        if brace:
            return json.loads(brace.group(0))
        raise


# ---------------------------------------------------------------------------
# Per-field writing assist  (ported from llm_assist.py)
# ---------------------------------------------------------------------------
def _identity_line(profile: Dict[str, Any]) -> str:
    ident = (profile or {}).get("identity", {}) or {}
    bits = [str(ident.get(k, "")).strip() for k in ("name", "age", "gender", "role")]
    bits = [b for b in bits if b]
    return ", ".join(bits) if bits else "an unspecified patient"


def _assist_prompt(field_key, field_label, field_type, instruction, current_value, profile) -> str:
    if field_type == "list":
        items = [str(x).strip() for x in (current_value or []) if str(x).strip()]
        has_content = len(items) > 0
        current_repr = "\n".join(f"- {it}" for it in items) if items else "(empty)"
        shape = ('{"mode": "refine", "items": ["<improved/expanded item>", ...], "note": "<one short sentence>"}'
                 if has_content else
                 '{"mode": "prompts", "prompts": ["<short guiding question>", ...], "note": "<one short sentence>"}')
    else:
        text_val = str(current_value or "").strip()
        has_content = len(text_val) > 0
        current_repr = text_val if text_val else "(empty)"
        shape = ('{"mode": "refine", "text": "<improved version>", "note": "<one short sentence>"}'
                 if has_content else
                 '{"mode": "prompts", "prompts": ["<short guiding question>", ...], "note": "<one short sentence>"}')

    rule = (
        "The field already has content. Refine it for clarity, specificity, and realism, and you "
        "may lightly EXPAND it (add closely related detail in the same spirit). PRESERVE the facts "
        "the clinician wrote — do not contradict or replace their intent. Keep their voice."
        if has_content else
        "The field is empty. Do NOT invent clinical facts about this patient. Instead, return 2-4 "
        "short guiding questions/prompts that help the clinician decide what to write for this field."
    )
    return f"""You are helping a clinician author a *simulated patient profile* that an AI will use to \
role-play a mental-health client in a therapist intake interview. You are assisting with ONE field.

Patient identity (context only): {_identity_line(profile)}
Field: {field_key} ({field_label})
What this field should contain: {instruction}

The clinician's current input for this field:
{current_repr}

{rule}

Respond with ONLY a JSON object (no markdown, no code fences) of this shape:
{shape}

For list fields, keep each item concise (a phrase or one sentence). Stay grounded in what the \
clinician wrote; be practical and brief."""


def assist_field(field_key: str, field_label: str, field_type: str, instruction: str,
                 current_value: Any, profile: Dict[str, Any]) -> Dict[str, Any]:
    try:
        prompt = _assist_prompt(field_key, field_label, field_type, instruction, current_value, profile or {})
        content = _call("You are a concise, practical clinical-writing assistant.", prompt, ASSIST_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001
        _log_failure("assist", exc)
        return {"ok": False, "error": "Assist is unavailable right now — try again."}
    if not content:
        return {"ok": False, "error": "The assistant did not return a response."}
    try:
        data = _parse_json(content)
    except Exception:  # noqa: BLE001
        return {"ok": True, "mode": "note", "note": content.strip()[:1200], "items": [], "prompts": []}

    mode = str(data.get("mode", "")).strip().lower() or "refine"
    out: Dict[str, Any] = {"ok": True, "mode": mode, "note": str(data.get("note", "")).strip()}
    if field_type == "list":
        out["items"] = [str(x).strip() for x in (data.get("items") or []) if str(x).strip()]
    else:
        out["text"] = str(data.get("text", "")).strip()
    out["prompts"] = [str(x).strip() for x in (data.get("prompts") or []) if str(x).strip()]
    return out


# ---------------------------------------------------------------------------
# Whole-profile quality review  (ported from profile_review.py)
# ---------------------------------------------------------------------------
_LABELS = {f["key"]: f["label"] for f in PROFILE_FIELDS}


def _requirements_block() -> str:
    lines: List[str] = []
    for f in PROFILE_FIELDS:
        lines.append(f"- {f['key']} ({f['label']}): {f.get('instruction', '')}")
        for sf in f.get("subfields", []) or []:
            lines.append(f"    - {f['key']}.{sf['key']} ({sf['label']}): {sf.get('instruction', '')}")
    return "\n".join(lines)


def _review_prompt(profile: Dict[str, Any]) -> str:
    profile_json = json.dumps(profile, ensure_ascii=False, indent=2)
    return f"""You are helping a clinician author a *simulated patient profile* that an AI will use to \
role-play a mental-health client in a therapist intake interview. Review the profile below for quality \
and realism, and give concrete, actionable suggestions to improve it. Empty or thin fields are allowed \
but worth flagging.

Each field is expected to contain:
{_requirements_block()}

Here is the profile the user wrote (JSON):
{profile_json}

Assess it and respond with ONLY a JSON object (no markdown, no code fences) of exactly this shape:
{{
  "overall": "<1-3 sentence overall assessment of the profile's quality and realism>",
  "fields": [
    {{"field": "<one of the field keys above>", "quality": "<good | adequate | weak | empty>", "suggestion": "<a specific, concrete suggestion; empty string if already good>"}}
  ],
  "top_suggestions": ["<the 2-4 most important changes, each a short actionable sentence>"]
}}

Only include a "fields" entry for fields that are weak, empty, or could be meaningfully improved. Be \
specific and practical — reference what the user actually wrote. Do not invent clinical facts; suggest \
what kind of detail to add. Keep suggestions encouraging and brief."""


def review_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    try:
        raw = _call("You are a meticulous, encouraging clinical-writing reviewer.",
                    _review_prompt(profile or {}), REVIEW_MAX_TOKENS, REVIEW_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        _log_failure("review", exc)
        return {"ok": False, "error": "Quality review is unavailable right now — try again."}
    if not raw:
        return {"ok": False, "error": "The reviewer did not return a response."}
    try:
        data = _parse_json(raw)
    except Exception:  # noqa: BLE001
        return {"ok": True, "overall": raw.strip()[:2000], "fields": [], "top_suggestions": []}

    fields = []
    for item in data.get("fields", []) or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("field", "")).strip()
        fields.append({
            "field": key, "label": _LABELS.get(key, key),
            "quality": str(item.get("quality", "")).strip().lower(),
            "suggestion": str(item.get("suggestion", "")).strip(),
        })
    return {
        "ok": True,
        "overall": str(data.get("overall", "")).strip(),
        "fields": fields,
        "top_suggestions": [str(s).strip() for s in (data.get("top_suggestions") or []) if str(s).strip()],
    }
