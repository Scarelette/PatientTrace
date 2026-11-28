"""Mock patient-model worker for testing live mode without a GPU. Speaks the real worker's protocol
(/health, /chat, /chat_stream NDJSON, /warm_chat) and answers from the recorded sessions: for a known
(patient, question) it streams the recorded reply word by word; otherwise a short generic reply.
Test hooks in the trainee message: "[fail]" -> error event, "[hang]" -> no output (timeout path),
"[empty]" -> empty reply. MOCK_FAIL_ONCE=1 makes the first stream of each question fail once (retry path).
Usage: python scripts/mock_worker.py --port 18901   (then ANGEL_WORKER_URL=http://127.0.0.1:18901)"""
import argparse, glob, json, os, re, time
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Any, Dict, List

app = FastAPI()
DATA = os.environ.get("PATIENTTRACE_DATA", "data-local")
KNOWN: Dict[str, Dict[str, str]] = {}          # patient name -> {normalised question: reply}
norm = lambda s: re.sub(r"\W+", " ", (s or "").lower()).strip()
for f in glob.glob(os.path.join(DATA, "interview", "p*.json")):
    if f.endswith((".f1.json",)) or "labels_" in f:
        continue
    d = json.load(open(f)); T = d.get("turns") or []
    for a, b in zip(T, T[1:]):
        if a.get("who") == "t" and b.get("who") == "p":
            KNOWN.setdefault(d.get("name", ""), {})[norm(a["text"])] = b["text"]
for f in glob.glob(os.path.join(DATA, "replay", "*.json")):
    if f.endswith("index.json"):
        continue
    d = json.load(open(f))
    for t in d.get("turns") or []:
        for s in "ab":
            if isinstance(t.get(s), dict) and t[s].get("response"):
                KNOWN.setdefault((d.get("names") or {}).get(s, ""), {})[norm(t["question"])] = t[s]["response"]
FAILED = set()
CALLS = {"n": 0}


class ChatReq(BaseModel):
    system_prompt: str
    conversation: List[Dict[str, Any]]
    max_tokens: int = 90
    temperature: float = 0.8


def _who(sp: str) -> str:
    for name in KNOWN:
        if name and re.search(r"\b" + re.escape(name) + r"\b", sp or ""):
            return name
    return ""


def _reply(req: ChatReq) -> str:
    q = next((t.get("content", "") for t in reversed(req.conversation) if t.get("role") in ("therapist", "user")), "")
    name = _who(req.system_prompt)
    r = KNOWN.get(name, {}).get(norm(q))
    return r or f"I'm not sure how to put it. It's been hard lately, and I keep thinking about it more than I'd like."


@app.get("/health")
def health():
    return {"ok": True, "ready": True, "mock": True}


@app.post("/warm_chat")
def warm():
    return {"warmed": True}


@app.post("/chat")
def chat(req: ChatReq):
    return {"reply": _reply(req)}


@app.post("/chat_stream")
def chat_stream(req: ChatReq):
    q = next((t.get("content", "") for t in reversed(req.conversation) if t.get("role") in ("therapist", "user")), "")
    CALLS["n"] += 1

    def gen():
        t0 = time.time()
        if "[fail]" in q:
            yield json.dumps({"error": "RuntimeError: mock failure"}) + "\n"; return
        if os.environ.get("MOCK_FAIL_ONCE") == "1" and (q, _who(req.system_prompt)) not in FAILED:
            FAILED.add((q, _who(req.system_prompt)))
            yield json.dumps({"error": "RuntimeError: mock transient failure"}) + "\n"; return
        if "[hang]" in q:
            time.sleep(120); return
        reply = "" if "[empty]" in q else _reply(req)
        time.sleep(0.5)                                   # time to first token
        words, out = reply.split(), ""
        for w in words:
            out = (out + " " + w).strip()
            yield json.dumps({"text": out}) + "\n"
            time.sleep(0.04)
        n = len(words)
        yield json.dumps({"done": True, "reply": reply, "tokens_in": 1800, "tokens_out": int(n * 1.3), "gen_ms": round((time.time() - t0) * 1000),
                          "first_token_ms": 500, "attempts": 1, "mock": True}) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=18901); a = ap.parse_args()
    print(f"[mock worker] {sum(len(v) for v in KNOWN.values())} recorded replies for {sorted(k for k in KNOWN if k)}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
