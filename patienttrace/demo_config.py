"""Config for the Patient_demo web app.

PROFILE_FIELDS is the 9-key patient-profile form schema copied verbatim from
simulation_annotator/study_config.py (per-field instructions from the study's
"Patient Profile Writing Requirements"). Kept as an embedded JSON literal so this
repo stays self-contained (no import from simulation_annotator).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

_PROFILE_FIELDS_JSON = r"""
[
  {"key": "identity", "label": "Identity", "type": "object",
   "instruction": "Real, plausible-but-anonymous identity. No identifiable names; do not put a clinical diagnosis in name/role. (Prompt field — shown to the model.)",
   "subfields": [
     {"key": "name", "label": "Name", "type": "text", "required": true, "instruction": "First name or alias only. No real, identifiable names."},
     {"key": "age", "label": "Age", "type": "text", "required": true, "instruction": "A number (e.g. \"34\") or a stage word (e.g. \"adolescent\", \"older adult\")."},
     {"key": "gender", "label": "Gender", "type": "text", "required": true, "instruction": "e.g. \"female\", \"male\", \"non-binary\"."},
     {"key": "role", "label": "Role", "type": "text", "required": true, "instruction": "The patient's role/occupation, e.g. \"college student\", \"retired teacher\"."}
   ]},
  {"key": "background", "label": "Background", "type": "list", "required": false, "min_items": 3,
   "instruction": "3-7 short factual sentences (past tense, third-person facts): living situation, life stressors, relevant history, how/why they came to the conversation. Do NOT write it as the patient speaking. (Prompt)"},
  {"key": "presenting_problems", "label": "Presenting problems", "type": "list", "required": false, "min_items": 4,
   "instruction": "4-8 concrete current complaints in plain language (e.g. \"Frequent panic attacks, often unexpected\", not just \"Panic Disorder\"). What they struggle with now. (Prompt)"},
  {"key": "triggers", "label": "Triggers", "type": "list", "required": false, "min_items": 3,
   "instruction": "3-7 situations/cues that worsen symptoms (e.g. \"being alone\", \"crowded rooms\")."},
  {"key": "emotions", "label": "Current emotions", "type": "list", "required": false, "min_items": 4,
   "instruction": "4-10 adjectives/short phrases for how the patient generally feels (e.g. \"anxious\", \"guarded\", \"exhausted\", \"ashamed\"). (Prompt)"},
  {"key": "behaviors", "label": "Current behaviors", "type": "list", "required": false, "min_items": 4,
   "instruction": "4-8 observable behaviors / coping patterns (e.g. \"avoids difficult topics\", \"overthinks\", \"becomes defensive\"). (Prompt)"},
  {"key": "family_context", "label": "Family context", "type": "list", "required": false, "min_items": 3,
   "instruction": "3-6 facts about family/relationships relevant to the case."},
  {"key": "hidden_state", "label": "Core beliefs / hidden concerns", "type": "list", "required": false, "min_items": 3,
   "instruction": "3-6 deeper fears, core beliefs, or secrets the patient holds but does not blurt out (e.g. \"People don't really understand what's wrong with me\"). Revealed cautiously. (Prompt)"},
  {"key": "speaking_style", "label": "Speaking style", "type": "object",
   "instruction": "Describe HOW the patient talks, not what they say. Common defaults are pre-filled — edit any field that doesn't fit your case.",
   "subfields": [
     {"key": "age_level", "label": "Age level", "type": "text", "required": true, "instruction": "How their speech maps to an age. Examples: \"adult\", \"speaks like a teenager\", \"older adult, formal\"."},
     {"key": "tone", "label": "Tone", "type": "text", "required": true, "instruction": "Emotional tone. Examples: \"hesitant and worried\", \"flat and withdrawn\", \"calm but tired\"."},
     {"key": "vocabulary", "label": "Vocabulary", "type": "text", "required": true, "instruction": "Word choice. Examples: \"everyday words, avoids medical terms\", \"articulate, uses some clinical terms\"."},
     {"key": "sentence_style", "label": "Sentence style", "type": "text", "required": true, "instruction": "Sentence rhythm. Examples: \"short sentences, sometimes trails off\", \"long, rambling, jumps between topics\"."},
     {"key": "disclosure_style", "label": "Disclosure style", "type": "text", "required": true, "instruction": "How readily they open up. Examples: \"reluctant, answers slowly\", \"deflects with humor\", \"minimizes problems\"."}
   ]}
]
"""

PROFILE_FIELDS: List[Dict[str, Any]] = json.loads(_PROFILE_FIELDS_JSON)

# Sensible defaults pre-filled into the speaking_style object on the form.
SPEAKING_STYLE_DEFAULTS: Dict[str, str] = {
    "age_level": "adult",
    "tone": "hesitant and worried",
    "vocabulary": "everyday words, avoids medical terms",
    "sentence_style": "short sentences, sometimes trails off",
    "disclosure_style": "reluctant at first, opens up once comfortable",
}


def empty_profile() -> Dict[str, Any]:
    """A blank profile matching the form schema."""
    prof: Dict[str, Any] = {}
    for f in PROFILE_FIELDS:
        if f["type"] == "object":
            prof[f["key"]] = {sf["key"]: "" for sf in f.get("subfields", [])}
        elif f["type"] == "list":
            prof[f["key"]] = []
        else:
            prof[f["key"]] = ""
    prof["speaking_style"] = dict(SPEAKING_STYLE_DEFAULTS)
    return prof
