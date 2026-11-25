"""Stage-1 HTTP worker — runs ON a GPU node (submitted as a SLURM job by the
login-node app). Loads the Observer model once and expands short profiles into
long profiles over HTTP. No internet needed here (the app on the login node makes
the Claude/GPT calls for the graphs).

On startup it writes a small state file (host/port/jobid) to shared GPFS so the
login app can discover it.

  POST /expand {short_text | profile}  -> {long_profile, parse_error}
  GET  /health                         -> {ok, ready}
"""

from __future__ import annotations

import argparse
import json
import os
import time
import socket

os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from angel_demo import paths as P
from angel_demo.profile_text import profile_to_short_text
from angel_demo.stage1_short2long import generate_stage1_profile

app = FastAPI(title="patient-demo-stage1-worker")
_GEN = None


def _get_gen():
    global _GEN
    if _GEN is None:
        backend = os.environ.get("STAGE1_BACKEND", "vllm").lower()
        if backend == "vllm":
            try:
                from angel_demo.vllm_backend import VLLMProfileGenerator
                _GEN = VLLMProfileGenerator()
            except Exception:  # noqa: BLE001 - fall back to HF
                from angel_demo.stage1_short2long import LocalProfileGenerator
                _GEN = LocalProfileGenerator()
        else:
            from angel_demo.stage1_short2long import LocalProfileGenerator
            _GEN = LocalProfileGenerator()
    return _GEN


class ExpandReq(BaseModel):
    short_text: str = ""
    profile: dict | None = None


class ChatReq(BaseModel):
    system_prompt: str
    conversation: list = []
    max_tokens: int = 90    # ceiling for the dynamic length plan (matches the study app)
    temperature: float = 0.8


@app.on_event("shutdown")
def _stop_engine_processes() -> None:
    """vLLM runs the model in child processes (EngineCore) that keep the GPU memory if this process
    exits without them (Ctrl+C, SIGTERM): stop every child before exiting."""
    try:
        import psutil
    except ImportError:
        return
    kids = psutil.Process().children(recursive=True)
    for k in kids:
        try:
            k.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(kids, timeout=15)
    for k in alive:
        try:
            k.kill()
        except psutil.Error:
            pass


@app.get("/health")
def health():
    return {"ok": True, "ready": True}   # the server only starts listening after the model has loaded


@app.post("/expand")
def expand(req: ExpandReq):
    text = req.short_text or (profile_to_short_text(req.profile) if req.profile else "")
    res = generate_stage1_profile(_get_gen(), text)
    return {"long_profile": res["profile_json"], "parse_error": res["profile_json_parse_error"]}


@app.post("/chat")
def chat(req: ChatReq):
    """Stage-2 roleplay: one patient turn given the system prompt + conversation.

    Regen backstop: if the (cleaned) reply is an 'I don't know'-style refusal or
    near-duplicates recent patient turns, regenerate with rising temperature and
    repetition penalty to escape the stuck reply (up to 3 retries)."""
    from angel_demo.patient_chat import (to_chat_messages, clean_reply, is_refusal, too_similar,
                              length_plan, latest_therapist, render_dynamic_state)
    from angel_demo.state_manager import PatientStateManager
    from angel_demo.vllm_backend import roleplay_reply
    # Rebuild the accumulated emotional state from the therapist turns (this endpoint
    # is stateless; replaying the sequence reproduces the same dynamic state as the
    # study app's per-turn updates). Feeds both the length plan and the prompt.
    sm = PatientStateManager({})
    for t in req.conversation:
        if isinstance(t, dict) and t.get("role") in ("therapist", "user"):
            c = (t.get("content") or "").strip()
            if c:
                sm.update_from_user_message(c)
    dyn = sm.get_dynamic_state()
    # Dynamic per-turn length: vary the token budget + sentence cap with the question
    # type + emotional state (+ a little randomness); open questions get a short-sentence
    # minimum. The state block + length cue are appended to the system prompt for this
    # turn. (Same behavior as the study angel.)
    budget, max_sents, cue, min_toks = length_plan(latest_therapist(req.conversation), dyn, req.max_tokens)
    sys_prompt = (req.system_prompt or "").rstrip()
    state_block = render_dynamic_state(dyn)
    if state_block:
        sys_prompt += "\n\n" + state_block
    sys_prompt += "\n\n" + cue
    msgs = to_chat_messages(sys_prompt, req.conversation)
    recent = [t.get("content", "") for t in req.conversation
              if isinstance(t, dict) and t.get("role") == "patient"][-3:]

    def _gen(temp: float, rep: float) -> str:
        return clean_reply(roleplay_reply(
            msgs, max_tokens=budget, temperature=temp, repetition_penalty=rep, min_tokens=min_toks),
            max_sents)

    reply = _gen(req.temperature, 1.15)
    attempts = 0
    while (is_refusal(reply) or too_similar(reply, recent)) and attempts < 3:
        attempts += 1
        temp = min(1.05, req.temperature + 0.08 * attempts)
        rep = min(1.25, 1.15 + 0.04 * attempts)
        reply = _gen(temp, rep)
    return {"reply": (reply or "").strip()}


@app.post("/warm_chat")
def warm_chat():
    """Load the stage-2 roleplay model ahead of the first message (called when the
    chat page opens) so the user's first reply is fast."""
    try:
        from angel_demo.vllm_backend import roleplay_reply
        roleplay_reply([{"role": "user", "content": "hi"}], max_tokens=1)
        return {"warmed": True}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"warm_chat failed: {exc}")


def _write_state(port: int, path: str) -> None:
    st = {"host": socket.gethostname(), "port": port,
          "jobid": os.environ.get("SLURM_JOB_ID", ""), "pid": os.getpid()}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, path)
    print(f"[worker] ready: {st}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="ANGEL patient-model GPU worker")
    ap.add_argument("--host", default=os.environ.get("DEMO_WORKER_HOST", "127.0.0.1"),
                    help="interface to listen on (use 0.0.0.0 only if the app runs on another machine)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("DEMO_WORKER_PORT", "7900")))
    ap.add_argument("--state-file", default=os.environ.get(
        "DEMO_WORKER_STATE", P.WORKER_STATE))
    args = ap.parse_args()
    # WORKER_PRELOAD=chat (demo extension: library patients have precomputed long profiles) loads the
    # stage-2 roleplay model first, so the first chat reply does not wait for the stage-1 Observer;
    # the Observer then loads lazily on the first /expand. Default (live demo) is unchanged.
    if os.environ.get("WORKER_PRELOAD", "chat").lower() == "chat":
        t0 = time.time()
        print("[worker] loading stage-2 roleplay model…", flush=True)
        from angel_demo.vllm_backend import roleplay_reply
        roleplay_reply([{"role": "user", "content": "hi"}], max_tokens=1)   # load + first-generation warm-up
        print(f"[worker] roleplay model ready in {time.time() - t0:.1f}s (stage-1 loads on first /expand)", flush=True)
    else:
        print("[worker] loading stage-1 model…", flush=True)
        _get_gen()  # load BEFORE advertising so /expand is ready immediately
    os.makedirs(os.path.dirname(os.path.abspath(args.state_file)), exist_ok=True)
    _write_state(args.port, args.state_file)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
