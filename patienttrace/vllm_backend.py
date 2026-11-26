"""vLLM backend for the local Observer model (Qwen3-Observer-800).

One shared vLLM engine serves TWO roles on the GPU:
  1. stage-1 short->long profile expansion (VLLMProfileGenerator, drop-in for the
     HF LocalProfileGenerator);
  2. symptom-graph EDGE generation — the Observer was GRPO-trained for exactly this
     (short2long_profile_generation stage-2), so it can propose causal links
     locally/for free instead of a paid API call.

This cluster blocks `nvcc`, so vLLM runs eager (no torch.compile / CUDA graphs)
and the FlashInfer sampler is disabled — set before importing vllm.
"""

from __future__ import annotations

import os

os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")

import threading
from typing import Any, Dict, List, Optional

DEFAULT_MODEL_PATH = os.environ.get(
    "STAGE1_MODEL_PATH",
    "ChengLi0228/Angel-Observer",   # Hugging Face id or a local folder
)
# stage-2 roleplay model (angel stage-2) for the chat page.
ROLEPLAY_MODEL_PATH = os.environ.get(
    "ROLEPLAY_MODEL_PATH",
    "ChengLi0228/Angel-Actor",      # Hugging Face id or a local folder
)
# When one worker hosts BOTH stage-1 and the chat model, give each a smaller
# GPU fraction so they fit together (H200 has plenty; keep total < 1).
_GPU_MEM = float(os.environ.get("VLLM_GPU_MEM", "0.85"))
_MAX_LEN = int(os.environ.get("VLLM_MAX_LEN", "8192"))

_ENGINE = None
_ROLEPLAY = None
_ENGINE_LOCK = threading.Lock()


class VLLMEngine:
    """Thin wrapper around a single vllm.LLM instance (chat interface)."""

    def __init__(self, model_path: str = DEFAULT_MODEL_PATH,
                 gpu_mem: float = _GPU_MEM, max_len: int = _MAX_LEN):
        from vllm import LLM
        self.llm = LLM(model=model_path, dtype="bfloat16",
                       gpu_memory_utilization=gpu_mem, max_model_len=max_len,
                       enforce_eager=True)
        self._lock = threading.Lock()  # vLLM engine calls are serialized

    def chat(self, messages: List[Dict[str, str]], max_tokens: int = 3072,
             temperature: float = 0.7, top_p: float = 0.9, enable_thinking=None,
             repetition_penalty=None, no_repeat_ngram_size=None, min_tokens=None) -> str:
        return self.chat_batch([messages], max_tokens, temperature, top_p, enable_thinking,
                               repetition_penalty, no_repeat_ngram_size, min_tokens)[0]

    def chat_batch(self, conversations: List[List[Dict[str, str]]], max_tokens: int = 3072,
                   temperature: float = 0.7, top_p: float = 0.9, enable_thinking=None,
                   repetition_penalty=None, no_repeat_ngram_size=None, min_tokens=None) -> List[str]:
        from vllm import SamplingParams
        sp_kwargs = dict(temperature=temperature, top_p=top_p, max_tokens=max_tokens)
        if repetition_penalty is not None:
            sp_kwargs["repetition_penalty"] = repetition_penalty
        if no_repeat_ngram_size is not None:
            sp_kwargs["no_repeat_ngram_size"] = no_repeat_ngram_size
        if min_tokens:
            sp_kwargs["min_tokens"] = min_tokens
        try:
            sp = SamplingParams(**sp_kwargs)
        except TypeError:   # older vLLM missing an optional arg -> drop the extras
            sp_kwargs.pop("no_repeat_ngram_size", None)
            sp_kwargs.pop("min_tokens", None)
            sp = SamplingParams(**sp_kwargs)
        kwargs = {}
        if enable_thinking is not None:   # Qwen3: turn the <think> block on/off
            kwargs["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
        with self._lock:
            outs = self.llm.chat(conversations, sp, **kwargs)
        return [o.outputs[0].text for o in outs]


def get_engine() -> VLLMEngine:
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = VLLMEngine()
    return _ENGINE


def get_roleplay_engine() -> VLLMEngine:
    """Lazy-load the stage-2 roleplay model (separate GPU engine)."""
    global _ROLEPLAY
    if _ROLEPLAY is None:
        with _ENGINE_LOCK:
            if _ROLEPLAY is None:
                _ROLEPLAY = VLLMEngine(model_path=ROLEPLAY_MODEL_PATH)
    return _ROLEPLAY


def roleplay_reply(messages, max_tokens: int = 400, temperature: float = 0.8, top_p: float = 0.9,
                   repetition_penalty: float = 1.15, no_repeat_ngram_size: int = 3,
                   min_tokens: int = 0) -> str:
    """One patient turn from the stage-2 model. Thinking is OFF so it replies as
    the patient directly (no chain-of-thought eating the token budget)."""
    # repetition_penalty + no_repeat_ngram_size break the stage-2 "I don't know" /
    # despair-repetition loop; max_tokens/min_tokens implement the dynamic reply
    # length (same behavior as simulate_patient/evaluation/angel.py).
    return get_roleplay_engine().chat(messages, max_tokens=max_tokens,
                                      temperature=temperature, top_p=top_p, enable_thinking=False,
                                      repetition_penalty=repetition_penalty,
                                      no_repeat_ngram_size=no_repeat_ngram_size, min_tokens=min_tokens)


# ---------------------------------------------------------------------------
# Role 1: stage-1 generator (drop-in for stage1_short2long.LocalProfileGenerator)
# ---------------------------------------------------------------------------
class VLLMProfileGenerator:
    """Matches LocalProfileGenerator.generate(...) so generate_stage1_profile works
    unchanged. Uses the model's own chat template (keeps the <think> reasoning that
    the Observer was trained with)."""

    def __init__(self, engine: Optional[VLLMEngine] = None):
        self.engine = engine or get_engine()

    def generate(self, messages: List[Dict[str, str]], max_new_tokens: int = 3072,
                 temperature: float = 0.1, top_p: float = 0.9,
                 apply_temperature: bool = False) -> str:
        # Production sampled with top_p (default temperature); use a stable 0.7.
        temp = temperature if apply_temperature else 0.7
        return self.engine.chat(messages, max_tokens=max_new_tokens, temperature=temp, top_p=top_p)


# ---------------------------------------------------------------------------
# Role 2: Observer symptom-graph edge generation (GRPO stage-2 task)
# ---------------------------------------------------------------------------
# System/input prompts copied from GRPO-Qwen3/GRPO/data.py (the format the
# Observer was trained on: output {"links":[{"from","to"}]} inside <GRAPH>).
_S2_SYSTEM = """You are a clinical information extraction model.

Given the Symptoms and External Factor List and the patient's Presenting Complaints, construct a **directed symptom network**.

Rules:
- Use **only** symptoms or external factors from the provided list.
- Create a link A -> B **only if** the text supports that A causes, triggers, worsens, or maintains B.
- The link direction must reflect cause -> effect.
- Do **not** infer relationships from general medical knowledge or symptom co-occurrence.
- Do **not** create links involving symptoms not explicitly mentioned.
- The reasoning **must be** no longer than 500 words.

Put all reasoning between <think> and </think>.
Output **only valid JSON** between <GRAPH> and </GRAPH>.

Output format:
<GRAPH>
{
  "links": [
    { "from": "symptom 1", "to": "symptom 2" }
  ]
}
</GRAPH>"""


def observer_edges(engine: VLLMEngine, complaints: str, node_names: List[str],
                   max_tokens: int = 3072, temperature: float = 0.6) -> List[Dict[str, str]]:
    """Ask the local Observer to build the causal link set. Returns raw
    {"from","to"} link dicts (node TEXT, matched back to nodes by the caller)."""
    from patienttrace.network_model import _iter_json_objects  # tolerant object parser
    symptoms = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(node_names))
    user = f"Patient Presenting Complaints: {complaints}\n\nSymptoms and External Factor List: {symptoms}\n"
    text = engine.chat([{"role": "system", "content": _S2_SYSTEM},
                        {"role": "user", "content": user}],
                       max_tokens=max_tokens, temperature=temperature)
    return [o for o in _iter_json_objects(text)
            if isinstance(o, dict) and "from" in o and "to" in o]
