"""Stage-1 (short -> long profile) core, self-contained.

Copied/condensed from GRPO-Qwen3/GRPO/short2long_profile_generation.py and
simulate_patient/profile_expansion/angel_initializer.py so Patient_demo has no
cross-repo imports. This runs the trained "Observer" model (Qwen3-Observer-800)
locally on a GPU to expand a short patient description into a rich, structured
long-profile JSON.

Only the LOCAL HuggingFace path is kept (the Azure/Anthropic/Gemini stage-1
backends from the original are not needed here).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

# On H200 + recent torch, the cuDNN SDPA attention backend can fail with
# "No valid execution plans built". Disable it so PyTorch falls back to
# flash / mem-efficient / math kernels. (Same workaround as the angel worker.)
try:
    torch.backends.cuda.enable_cudnn_sdp(False)
    torch.backends.cuda.enable_mem_efficient_sdp(True)
    torch.backends.cuda.enable_flash_sdp(True)
except Exception:  # noqa: BLE001
    pass

# ---------------------------------------------------------------------------
# Model path resolution
# ---------------------------------------------------------------------------
# Default points at the existing merged Observer checkpoint. Override with the
# STAGE1_MODEL_PATH env var to make this fully portable.
DEFAULT_STAGE1_MODEL_PATH = os.environ.get(
    "STAGE1_MODEL_PATH",
    "ChengLi0228/Angel-Observer",
)


# ---------------------------------------------------------------------------
# Prompt construction (verbatim from short2long_profile_generation.py)
# ---------------------------------------------------------------------------
SHORT_TO_JSON_SYSTEM_PROMPT = """You are an expert clinical case-profile writer for mental-health patient simulation research.

Your task is to expand a short patient description into a rich, realistic, internally consistent simulated patient profile. The profile will be used to train and evaluate AI systems in clinical-style conversations.

Important requirements:
1. Preserve all explicit facts from the short description.
2. Do not contradict the short description.
3. You may infer reasonable missing details, but they must be clinically plausible and clearly consistent with the original description.
4. Do not overfit to a generic template. Create diverse patients with varied backgrounds, personalities, symptoms, coping styles, relationship patterns, emotional expression, and communication styles.
5. Avoid stereotypes. Demographic details should influence the profile only when clinically or contextually relevant.
6. The profile should be complete enough for multi-turn roleplay, including what the patient readily discloses, what they hide, how they respond emotionally, and how symptoms appear in conversation.
7. Do not write treatment recommendations. Focus on the patient profile, presentation, behavior, and simulation rules.
8. Do not diagnose beyond the information provided. If a diagnosis is included in the short description, treat it as a diagnosis hint, not as a final clinical conclusion.
9. The output must be valid JSON only. Do not include markdown, comments, or extra explanation.
"""


def build_long_profile_user_prompt(short_patient_profile: str) -> str:
    diversity_block = """
Diversity constraints:
- Vary the patient's openness level: some patients are guarded, some are talkative, some are vague, some intellectualize, and some minimize symptoms.
- Vary symptom presentation: symptoms should not always be severe, dramatic, or neatly organized.
- Vary emotional expression: include patients who are flat, irritable, ashamed, humorous, anxious, confused, defensive, or overly agreeable when appropriate.
- Vary social context: do not always give the patient supportive family or clear insight.
- Vary roleplay difficulty: some patients should resist direct questions, change topics, contradict themselves, or only reveal key information gradually.
- Avoid using the same phrases across profiles unless clinically appropriate.
"""

    prompt = f"""
Please expand the following short patient description into a complete long simulated patient profile.

Short patient description:
{short_patient_profile}

Generate a detailed patient profile in valid JSON format using the schema below.

Requirements:
- The profile should be clinically realistic and suitable for a mental-health patient simulation.
- Preserve the core facts from the short description.
- Add plausible missing details to make the patient usable in a long multi-turn dialogue.
- The patient should have a distinct personality, speech style, emotional pattern, and disclosure behavior.
- Include enough diversity across generated profiles. Avoid making every patient similarly introspective, cooperative, or articulate.
- The profile should support at least 15-20 turns of conversation.
- Include hidden information that the patient may reveal only after trust is built.
- Include simulation rules for how the patient should respond during roleplay.
- Keep the profile specific, concrete, and grounded in the given short description.
- Do not include therapist responses.
- Return valid JSON only.

{diversity_block}

Output JSON schema:

{{
  "identity": {{
    "name": "",
    "age": null,
    "gender": "",
    "role": "patient",
    "source_title": "",
    "diagnosis_hint": []
  }},
  "background": [
    ""
  ],
  "presenting_problems": [
    ""
  ],
  "symptom_details": {{
    "onset_and_course": "",
    "frequency_and_intensity": "",
    "triggers": [],
    "maintaining_factors": [],
    "functional_impairment": []
  }},
  "emotional_profile": {{
    "dominant_emotions": [],
    "emotional_expression_style": "",
    "shame_or_fear_points": [],
    "typical_reaction_when_distressed": ""
  }},
  "cognitive_patterns": {{
    "core_beliefs": [],
    "automatic_thoughts": [],
    "worries_or_ruminations": [],
    "interpretation_biases": []
  }},
  "behavior_patterns": {{
    "avoidance_behaviors": [],
    "safety_behaviors": [],
    "coping_strategies": [],
    "interpersonal_patterns": []
  }},
  "social_and_family_context": {{
    "family_relationships": "",
    "romantic_or_peer_relationships": "",
    "school_or_work_context": "",
    "social_support": "",
    "cultural_or_contextual_factors": ""
  }},
  "risk_and_protective_factors": {{
    "risk_factors": [],
    "protective_factors": [],
    "safety_notes": ""
  }},
  "hidden_state": {{
    "information_patient_initially_withholds": [],
    "information_revealed_after_trust": [],
    "topics_patient_avoids": [],
    "contradictions_or_ambivalence": []
  }},
  "speaking_style": {{
    "verbosity": "",
    "tone": "",
    "word_choice": "",
    "interaction_style": "",
    "example_phrases": []
  }},
  "disclosure_rules": {{
    "early_session": "",
    "middle_session": "",
    "late_session": "",
    "when_challenged": "",
    "when_supported": ""
  }},
  "simulation_rules": [
    "",
    "",
    ""
  ],
  "brief_summary": ""
}}
""".strip()

    return prompt


def build_long_profile_messages(short_patient_profile: str) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": SHORT_TO_JSON_SYSTEM_PROMPT},
        {"role": "user", "content": build_long_profile_user_prompt(short_patient_profile)},
    ]


# ---------------------------------------------------------------------------
# Output cleaning / JSON extraction
# ---------------------------------------------------------------------------
def strip_code_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def strip_think_block(text: str) -> str:
    text = text or ""
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.S).strip()


def clean_model_output(text: str) -> str:
    return strip_code_fences(strip_think_block(text))


def extract_first_json_object(text: str) -> Optional[str]:
    text = clean_model_output(text)
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for idx in range(start, len(text)):
        if text[idx] == "{":
            depth += 1
        elif text[idx] == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None


def parse_profile_json(text: str) -> Dict[str, Any]:
    json_str = extract_first_json_object(text)
    if not json_str:
        raise ValueError("Could not find a JSON object in model output.")
    return json.loads(json_str)


# ---------------------------------------------------------------------------
# Local HF generator (the Observer model)
# ---------------------------------------------------------------------------
class LocalProfileGenerator:
    def __init__(self, model_path: str = DEFAULT_STAGE1_MODEL_PATH, torch_dtype: str = "bfloat16"):
        dtype = getattr(torch, torch_dtype)
        self.model_path = model_path
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_path,
            torch_dtype=dtype,
            device_map="auto",
            trust_remote_code=True,
        )
        self.model.eval()

    def generate(
        self,
        messages: List[Dict[str, str]],
        max_new_tokens: int = 3072,
        temperature: float = 0.1,
        top_p: float = 0.9,
        apply_temperature: bool = False,
    ) -> str:
        prompt = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)

        gen_kwargs: Dict[str, Any] = dict(
            max_new_tokens=max_new_tokens,
            do_sample=True,
            top_p=top_p,
        )
        # NOTE: the original angel stage-1 code left `temperature` COMMENTED OUT,
        # so production runs with HF's default temperature and top_p only. We keep
        # that behavior by default (apply_temperature=False) to match Observer-800
        # as deployed; set apply_temperature=True to honor the `temperature` arg.
        if apply_temperature:
            gen_kwargs["temperature"] = temperature

        with torch.no_grad():
            outputs = self.model.generate(**inputs, **gen_kwargs)

        generated_tokens = outputs[0][inputs["input_ids"].shape[-1] :]
        return self.tokenizer.decode(generated_tokens, skip_special_tokens=True).strip()


def generate_stage1_profile(
    generator: LocalProfileGenerator,
    short_profile: str,
    max_new_tokens: int = 3072,
    temperature: float = 0.1,
    top_p: float = 0.9,
    max_attempts: int = 2,
) -> Dict[str, Any]:
    """Run stage-1 with retries until the output parses as JSON.

    Returns a dict with the raw output, cleaned output, parsed profile_json
    (or None), and any parse error — mirroring the original return shape.
    """
    result: Dict[str, Any] = {
        "raw_model_output": "",
        "cleaned_model_output": "",
        "profile_json": None,
        "profile_json_parse_error": None,
    }
    for _ in range(max(1, max_attempts)):
        raw_output = generator.generate(
            build_long_profile_messages(short_profile),
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
        )
        cleaned_output = clean_model_output(raw_output)
        result["raw_model_output"] = raw_output
        result["cleaned_model_output"] = cleaned_output
        try:
            result["profile_json"] = parse_profile_json(cleaned_output)
            result["profile_json_parse_error"] = None
            return result
        except Exception as exc:  # noqa: BLE001
            result["profile_json_parse_error"] = repr(exc)
    return result
