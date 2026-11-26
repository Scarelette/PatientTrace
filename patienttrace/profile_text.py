"""Serialize a structured patient profile into the short-profile text block that
the stage-1 (short -> long) model consumes.

Self-contained copy adapted from simulation_annotator/profile_schema.py
(`profile_to_short_text`). No imports from other repos.

Input profiles use the 11-key "angel" schema:
    identity, background, presenting_problems, triggers, emotions, behaviors,
    family_context, hidden_state, speaking_style, disclosure_rules,
    simulation_rules  (+ optional _meta)
"""

from __future__ import annotations

from typing import Any, Dict


def _bullets(items: Any) -> str:
    if not isinstance(items, list):
        items = [items] if items else []
    lines = [f"- {str(x).strip()}" for x in items if str(x).strip()]
    return "\n".join(lines) if lines else "- (none provided)"


def profile_to_short_text(profile: Dict[str, Any]) -> str:
    """Render the structured profile into a readable patient-profile block used
    as the `short_patient_profile` input to the stage-1 model."""
    identity = profile.get("identity", {}) or {}
    speaking = profile.get("speaking_style", {}) or {}
    dr = profile.get("disclosure_rules", {}) or {}

    background = profile.get("background", [])
    if isinstance(background, list):
        background_text = " ".join(str(x).strip() for x in background if str(x).strip())
    else:
        background_text = str(background)

    diagnosis_hint = identity.get("diagnosis_hint", [])
    if isinstance(diagnosis_hint, list):
        diagnosis_text = ", ".join(str(x).strip() for x in diagnosis_hint if str(x).strip())
    else:
        diagnosis_text = str(diagnosis_hint or "")

    style_lines = []
    for k in ["age_level", "tone", "vocabulary", "sentence_style", "disclosure_style"]:
        v = speaking.get(k)
        if v and str(v).strip():
            style_lines.append(f"- {k.replace('_', ' ')}: {str(v).strip()}")
    style_block = "\n".join(style_lines) if style_lines else "- natural and conversational"

    text = f"""Name: {identity.get('name') or 'Unknown'}
Age: {identity.get('age') or 'unknown'}
Gender: {identity.get('gender') or 'unspecified'}
Role: {identity.get('role') or 'patient'}
Diagnosis hint: {diagnosis_text or '(none provided)'}

Background: {background_text}

Presenting problems:
{_bullets(profile.get('presenting_problems'))}

Triggers:
{_bullets(profile.get('triggers'))}

Current emotions:
{_bullets(profile.get('emotions'))}

Current behaviors:
{_bullets(profile.get('behaviors'))}

Family / social context:
{_bullets(profile.get('family_context'))}

Core beliefs / hidden concerns (hold these inwardly; reveal cautiously):
{_bullets(profile.get('hidden_state'))}

Speaking style:
{style_block}

Likely early disclosures:
{_bullets(dr.get('topics_likely_early'))}

Likely late disclosures (only after trust/time):
{_bullets(dr.get('topics_likely_late'))}

Avoid unless directly asked:
{_bullets(dr.get('topics_avoid_unless_asked'))}
"""
    return text.strip()
