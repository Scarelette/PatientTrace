"""The existing ANGEL patient model, callable outside the web app (S0.5, F3).

Fidelity: the system prompt is built by the demo's own code
(demo_app._chat_profile_text + patient_chat.build_patient_system_prompt — i.e. the
stage-1 LONG profile, adapted, plus observer extras), and each turn is produced by the
GPU worker's own handler (stage1_worker.chat: dynamic state, length plan, regen
backstop, clean_reply) on the stage-2 roleplay model (qwen3-8b-dpo-merged).

Backends:
  local  in-process vLLM (inside a Slurm GPU job) — calls stage1_worker.chat directly
  http   a running demo GPU worker (POST {url}/chat), e.g. the demo's on-demand worker
Patient-model calls are local GPU inference, not API calls, so they do not go through
the S0.3 wrapper; their latency is recorded here instead.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from typing import Any, Dict, List, Optional


from patienttrace.angel_ext import data as D  # noqa: E402

PATIENT_MODEL = os.environ.get(
    "ROLEPLAY_MODEL_PATH", "ChengLi0228/Angel-Actor")


def build_system_prompt(pid: str) -> Dict[str, str]:
    """Exactly what /api/chat/start builds for an existing profile + its stage-1 output."""
    os.environ.setdefault("ANTHROPIC_API_KEY", "unused-by-this-import")  # demo_app builds a client at import
    from patienttrace import demo_app
    from patienttrace.patient_chat import build_patient_system_prompt
    from patienttrace import profile_meta as pm
    idx = D.idx_of(pid)
    prof = dict(D.short_profiles()[idx])
    ident = dict(prof.get("identity", {}) or {})
    if not str(ident.get("name") or "").strip():          # same as /api/existing_profiles/{idx}
        ident["name"] = pm.display_name(idx, prof)
        prof["identity"] = ident
    lp = D.load_long_profile(pid)["long_profile"]
    text, source = demo_app._chat_profile_text(prof, lp)
    sp = build_patient_system_prompt(text)
    return {"system_prompt": sp, "profile_source": source, "patient_name": ident.get("name") or "",
            "system_prompt_sha": hashlib.sha256(sp.encode()).hexdigest()[:16]}


class PatientModel:
    def __init__(self, backend: str = "local", url: Optional[str] = None, timeout: Optional[float] = None):
        import os
        if timeout is None:   # demo sets PATIENT_MODEL_TIMEOUT_S (scripts/serve_ext.sh); batch default 180 s
            timeout = float(os.environ.get("PATIENT_MODEL_TIMEOUT_S", "180"))
        self.backend, self.url, self.timeout = backend, url, timeout
        if backend == "local":
            from patienttrace import stage1_worker
            self._worker = stage1_worker

    def reply(self, system_prompt: str, conversation: List[Dict[str, str]],
              temperature: float = 0.8, max_tokens: int = 90) -> Dict[str, Any]:
        """conversation: [{role: therapist|patient, content}] ending with a therapist turn."""
        t0 = time.time()
        if self.backend == "local":
            req = self._worker.ChatReq(system_prompt=system_prompt, conversation=conversation,
                                       max_tokens=max_tokens, temperature=temperature)
            from patienttrace.patient_chat import clean_reply      # demo_app.chat_send cleans again app-side
            text = clean_reply(self._worker.chat(req)["reply"] or "")
        else:
            import requests
            r = __import__("patienttrace.gpu_manager", fromlist=["http"]).http().post(self.url.rstrip("/") + "/chat",
                              json={"system_prompt": system_prompt, "conversation": conversation,
                                    "max_tokens": max_tokens, "temperature": temperature},
                              timeout=self.timeout)
            r.raise_for_status()
            from patienttrace.patient_chat import clean_reply
            text = clean_reply(r.json().get("reply") or "")
        return {"text": text, "latency_ms": round((time.time() - t0) * 1000, 1)}
