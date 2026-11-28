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
import re
import socket

os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from patienttrace import paths as P
from patienttrace.profile_text import profile_to_short_text
from patienttrace.stage1_short2long import generate_stage1_profile

app = FastAPI(title="patient-demo-stage1-worker")
_GEN = None


def _get_gen():
    global _GEN
    if _GEN is None:
        backend = os.environ.get("STAGE1_BACKEND", "vllm").lower()
        if backend == "vllm":
            try:
                from patienttrace.vllm_backend import VLLMProfileGenerator
                _GEN = VLLMProfileGenerator()
            except Exception:  # noqa: BLE001 - fall back to HF
                from patienttrace.stage1_short2long import LocalProfileGenerator
                _GEN = LocalProfileGenerator()
        else:
            from patienttrace.stage1_short2long import LocalProfileGenerator
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


# Idle exit: WORKER_IDLE_EXIT_S > 0 stops the worker after that many seconds without a patient request, so a
# forgotten GPU job does not keep billing (the shutdown hook below also stops vLLM's engine processes).
_LAST = {"t": time.time()}


@app.middleware("http")
async def _touch(request, call_next):
    if request.url.path in ("/chat", "/chat_stream", "/warm_chat", "/expand"):
        _LAST["t"] = time.time()
    return await call_next(request)


def _idle_watch(limit: float) -> None:
    import signal
    import threading
    def in_demo() -> bool:                    # scripts/live_mode.sh demo on: no idle exit until the period ends
        try:
            with open(os.path.join(P.HOME_DIR, "live", "demo_until"), encoding="utf-8") as f:
                return time.time() < float(f.read().strip() or 0)
        except Exception:  # noqa: BLE001
            return False

    def run():
        while True:
            time.sleep(30)
            if in_demo():
                _LAST["t"] = time.time()
            if time.time() - _LAST["t"] > limit:
                print(f"[worker] idle for {limit:.0f}s: exiting to free the GPU", flush=True)
                os.kill(os.getpid(), signal.SIGTERM)
                return
    threading.Thread(target=run, daemon=True).start()


@app.get("/health")
def health():
    return {"ok": True, "ready": True}   # the server only starts listening after the model has loaded


@app.post("/expand")
def expand(req: ExpandReq):
    text = req.short_text or (profile_to_short_text(req.profile) if req.profile else "")
    res = generate_stage1_profile(_get_gen(), text)
    return {"long_profile": res["profile_json"], "parse_error": res["profile_json_parse_error"]}


def _prepare(req: "ChatReq"):
    """System prompt + messages for one patient turn: replay the dynamic state from the therapist turns
    (this endpoint is stateless), then the per-turn length plan (same behaviour as the study app)."""
    from patienttrace.patient_chat import to_chat_messages, length_plan, latest_therapist, render_dynamic_state
    from patienttrace.state_manager import PatientStateManager
    sm = PatientStateManager({})
    for t in req.conversation:
        if isinstance(t, dict) and t.get("role") in ("therapist", "user"):
            c = (t.get("content") or "").strip()
            if c:
                sm.update_from_user_message(c)
    dyn = sm.get_dynamic_state()
    budget, max_sents, cue, min_toks = length_plan(latest_therapist(req.conversation), dyn, req.max_tokens)
    # headroom so the model can finish its last sentence; the sentence cap (and finish_sentences) keep it short
    budget = budget + int(os.environ.get("PATIENT_TOKEN_HEADROOM", "40"))
    sys_prompt = (req.system_prompt or "").rstrip()
    state_block = render_dynamic_state(dyn)
    if state_block:
        sys_prompt += "\n\n" + state_block
    sys_prompt += "\n\n" + cue
    msgs = to_chat_messages(sys_prompt, req.conversation)
    recent = [t.get("content", "") for t in req.conversation if isinstance(t, dict) and t.get("role") == "patient"][-3:]
    return msgs, budget, max_sents, min_toks, recent


def _attempts(req: "ChatReq"):
    """(temperature, repetition_penalty) for the first try and up to 3 regenerations."""
    yield req.temperature, 1.15
    for k in range(1, 4):
        yield min(1.05, req.temperature + 0.08 * k), min(1.25, 1.15 + 0.04 * k)


@app.post("/chat")
def chat(req: ChatReq):
    """Stage-2 roleplay: one patient turn given the system prompt + conversation.

    Regen backstop: if the (cleaned) reply is an 'I don't know'-style refusal or
    near-duplicates recent patient turns, regenerate with rising temperature and
    repetition penalty to escape the stuck reply (up to 3 retries)."""
    from patienttrace.patient_chat import clean_reply, is_refusal, too_similar, finish_sentences
    from patienttrace.vllm_backend import roleplay_reply
    msgs, budget, max_sents, min_toks, recent = _prepare(req)
    reply = ""
    for temp, rep in _attempts(req):
        reply = finish_sentences(clean_reply(roleplay_reply(msgs, max_tokens=budget, temperature=temp, repetition_penalty=rep,
                                                            min_tokens=min_toks), max_sents))
        if not (is_refusal(reply) or too_similar(reply, recent)):
            break
    return {"reply": (reply or "").strip()}


@app.post("/chat_stream")
def chat_stream(req: ChatReq):
    """The same patient turn as /chat, streamed as NDJSON lines:
      {"text": "<cleaned reply so far>"}  ...  (repeated; cumulative, so a client just replaces the bubble)
      {"reset": true, "attempt": n}          a regeneration started (refusal / repeat backstop)
      {"done": true, "reply": "...", "tokens_in", "tokens_out", "gen_ms", "first_token_ms", "attempts",
       "gpu_util", "gpu_mem_mb", "gpu_name"}  or  {"error": "..."}
    Stopping the read (Stop button, client gone) aborts the generation on the GPU."""
    import json as _json
    from fastapi.responses import StreamingResponse
    from patienttrace.patient_chat import clean_reply, is_refusal, too_similar, finish_sentences
    from patienttrace.vllm_backend import roleplay_stream, gpu_snapshot
    msgs, budget, max_sents, min_toks, recent = _prepare(req)

    def gen():
        tot = {"tokens_in": 0, "tokens_out": 0, "gen_ms": 0}
        first, reply, n = None, "", 0
        try:
            for n, (temp, rep) in enumerate(_attempts(req), 1):
                if n > 1:
                    yield _json.dumps({"reset": True, "attempt": n}) + "\n"
                last = ""
                for text, fin, stats in roleplay_stream(msgs, budget, temp, 0.9, rep, 3, min_toks, timeout=60):
                    if fin and stats and stats.get("error"):
                        raise RuntimeError(stats["error"])
                    shown = clean_reply(text if fin else re.sub(r"<[^>]*$", "", text), max_sents)   # no half-written tag mid-stream
                    if shown and shown != last:
                        last = shown
                        yield _json.dumps({"text": shown}) + "\n"
                    if fin and stats:
                        for k in tot:
                            tot[k] += stats.get(k, 0)
                        if first is None:
                            first = stats.get("first_token_ms")
                reply = finish_sentences((clean_reply(text, max_sents) or "").strip())
                if not (is_refusal(reply) or too_similar(reply, recent)):
                    break
            yield _json.dumps({"done": True, "reply": reply, **tot, "first_token_ms": first, "attempts": n, **gpu_snapshot()}) + "\n"
        except Exception as exc:  # noqa: BLE001
            yield _json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/warm_chat")
def warm_chat():
    """Load the stage-2 roleplay model ahead of the first message (called when the
    chat page opens) so the user's first reply is fast."""
    try:
        from patienttrace.vllm_backend import roleplay_reply
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
        from patienttrace.vllm_backend import roleplay_reply
        roleplay_reply([{"role": "user", "content": "hi"}], max_tokens=1)   # load + first-generation warm-up
        print(f"[worker] roleplay model ready in {time.time() - t0:.1f}s (stage-1 loads on first /expand)", flush=True)
    else:
        print("[worker] loading stage-1 model…", flush=True)
        _get_gen()  # load BEFORE advertising so /expand is ready immediately
    os.makedirs(os.path.dirname(os.path.abspath(args.state_file)), exist_ok=True)
    _write_state(args.port, args.state_file)
    idle = float(os.environ.get("WORKER_IDLE_EXIT_S", "0") or 0)
    if idle > 0:
        _LAST["t"] = time.time()
        _idle_watch(idle)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
