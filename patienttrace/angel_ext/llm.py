"""S0.3 — single entry point for every LLM / embedding call in the extension.

Every call is:
  * logged (one JSON line per call to logs/llm_calls.jsonl): model, prompt hash,
    input/output tokens, latency_ms, cache hit, caller tag, error;
  * disk-cached (.llm_cache/<2-hex shard>/<sha256>.json) keyed on
    (model, system, messages, generation params) — a cache hit returns the stored
    text with latency_ms of the ORIGINAL call plus cached=True, so latency stats
    stay meaningful (use `fresh_latency_ms` if you need wall time of this call);
  * retried with exponential backoff on transient errors;
  * run at a fixed temperature/seed where the model supports it (the parameters a
    model rejects are learned on the first 400 and dropped afterwards).

Providers (Azure AI Foundry, one resource, one key — or, when AZURE_FOUNDRY_API_KEY is unset, the
public Anthropic / OpenAI APIs via ANTHROPIC_API_KEY / OPENAI_API_KEY):
  * model name starts with "claude" -> anthropic.AnthropicFoundry
  * anything else                    -> openai.OpenAI on the resource's /openai/v1/

Config (env, or secrets.env at the repo root which is gitignored):
  AZURE_FOUNDRY_RESOURCE, AZURE_FOUNDRY_API_KEY,
  METHOD_MODEL, JUDGE_MODEL_A, JUDGE_MODEL_B, EMBED_MODEL (+ EMBED_BACKEND).
Both judges must come from a different model family than METHOD_MODEL (asserted).

Usage:
    from patienttrace.angel_ext.llm import get_client
    llm = get_client()
    r = llm.chat(llm.method_model, system="...", user="...", tag="f1.ours", json_mode=True)
    r.text, r.json, r.tokens_in, r.tokens_out, r.latency_ms, r.cached
    vecs = llm.embed(["text a", "text b"], tag="s0.1")   # np.ndarray [n, d], L2-normalised
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from patienttrace import paths as P

DEFAULT_CACHE_DIR = P.state("llm_cache")
DEFAULT_LOG_PATH = os.path.join(P.LOG_DIR, "llm_calls.jsonl")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _load_secrets_env(path: str = os.path.join(os.getcwd(), ".env")) -> None:
    """Populate os.environ from secrets.env without overriding existing vars."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def model_family(model: str) -> str:
    m = model.lower()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4", "text-embedding")):
        return "openai"
    if m.startswith("grok"):
        return "xai"
    if m.startswith(("llama", "meta-")):
        return "meta"
    if m.startswith(("mistral", "codestral")):
        return "mistral"
    if m.startswith("deepseek"):
        return "deepseek"
    return m.split("-")[0]


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_hash: str
    tokens_in: int
    tokens_out: int
    latency_ms: float          # latency of the call that produced the text (original call if cached)
    cached: bool
    fresh_latency_ms: float    # wall time of THIS call (≈0 on a cache hit)
    json: Any = None           # parsed JSON when json_mode=True and parsing succeeded
    json_error: Optional[str] = None

    @property
    def tokens(self) -> Dict[str, int]:
        return {"in": self.tokens_in, "out": self.tokens_out}


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in `text` (tolerates ```json fences / prose)."""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        s = t.find(open_c)
        if s < 0:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(s, len(t)):
            c = t[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == open_c:
                depth += 1
            elif c == close_c:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[s:i + 1])
                    except Exception:  # noqa: BLE001
                        break
    raise ValueError("no JSON object found in model output")


class LLMClient:
    # Parameters a model rejected with a 400 — learned at runtime, shared by all clients.
    _REJECTED: Dict[str, set] = {}
    # Known up front (saves one failed call per process).
    _KNOWN_REJECTED = {
        "claude-opus-5-5": {"temperature", "top_p"},
        "claude-fable-5": {"temperature", "top_p"},
        "gpt-6-astra": {"temperature", "seed"},
    }
    # Minimum max_tokens for models that otherwise return empty/truncated JSON.
    _MIN_MAX_TOKENS = {"claude-fable-5": 1500}

    def __init__(self, cache_dir: str = DEFAULT_CACHE_DIR, log_path: str = DEFAULT_LOG_PATH,
                 use_cache: bool = True, max_retries: Optional[int] = None,
                 concurrency: Optional[Dict[str, int]] = None):
        _load_secrets_env()
        # Provider: Azure AI Foundry when AZURE_FOUNDRY_API_KEY is set (the setup used for the paper),
        # otherwise the public Anthropic / OpenAI APIs (ANTHROPIC_API_KEY / OPENAI_API_KEY).
        # Keys are checked when a call is made, so the app starts without any key (browse mode).
        self.resource = os.environ.get("AZURE_FOUNDRY_RESOURCE", "")
        self._key = os.environ.get("AZURE_FOUNDRY_API_KEY", "")
        self.foundry = bool(self._key)
        self.method_model = os.environ.get("METHOD_MODEL", "claude-opus-5-5")
        self.judge_model_a = os.environ.get("JUDGE_MODEL_A", "gpt-6-astra")
        self.judge_model_b = os.environ.get("JUDGE_MODEL_B", "grok-4.7")
        self.embed_model = os.environ.get("EMBED_MODEL", "text-embedding-3-large")
        mf = model_family(self.method_model)
        for j in (self.judge_model_a, self.judge_model_b):
            if model_family(j) == mf:
                raise RuntimeError(f"judge {j} is the same family ({mf}) as METHOD_MODEL {self.method_model}")
        self.cache_dir, self.log_path = cache_dir, log_path
        # the web server sets LLM_MAX_RETRIES / LLM_HTTP_TIMEOUT low (cli.SERVE_ENV) so a live turn fails fast;
        # batch scripts keep the patient defaults
        self.use_cache, self.max_retries = use_cache, (max_retries if max_retries is not None else int(os.environ.get("LLM_MAX_RETRIES", "6")))
        conc = {"anthropic": 8, "openai": 12, "embed": 4}
        for k in conc:   # env override, e.g. LLM_CONC_ANTHROPIC=24
            if os.environ.get(f"LLM_CONC_{k.upper()}"):
                conc[k] = int(os.environ[f"LLM_CONC_{k.upper()}"])
        conc.update(concurrency or {})
        self._sem = {k: threading.BoundedSemaphore(v) for k, v in conc.items()}
        self._log_lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._anthropic = None
        self._openai = None
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

    # -- providers ---------------------------------------------------------
    def _anthropic_client(self):
        with self._client_lock:
            if self._anthropic is None:
                if self.foundry:
                    from anthropic import AnthropicFoundry
                    self._anthropic = AnthropicFoundry(api_key=self._key, resource=self.resource,
                                                       max_retries=0, timeout=float(os.environ.get("LLM_HTTP_TIMEOUT", "300")))
                else:
                    if not os.environ.get("ANTHROPIC_API_KEY"):
                        raise RuntimeError("ANTHROPIC_API_KEY is not set (needed for live alignment "
                                           "and difference summaries)")
                    from anthropic import Anthropic
                    self._anthropic = Anthropic(max_retries=0, timeout=float(os.environ.get("LLM_HTTP_TIMEOUT", "300")))
        return self._anthropic

    def _openai_client(self):
        with self._client_lock:
            if self._openai is None:
                from openai import OpenAI
                if self.foundry:
                    self._openai = OpenAI(base_url=f"https://{self.resource}.openai.azure.com/openai/v1/",
                                          api_key=self._key, max_retries=0, timeout=float(os.environ.get("LLM_HTTP_TIMEOUT", "300")))
                else:
                    if not os.environ.get("OPENAI_API_KEY"):
                        raise RuntimeError("OPENAI_API_KEY is not set")
                    self._openai = OpenAI(max_retries=0, timeout=float(os.environ.get("LLM_HTTP_TIMEOUT", "300")))
        return self._openai

    # -- cache / log ---------------------------------------------------------
    @staticmethod
    def _hash(payload: Dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _cache_path(self, h: str) -> str:
        return os.path.join(self.cache_dir, h[:2], h + ".json")

    def _cache_get(self, h: str) -> Optional[Dict[str, Any]]:
        if not self.use_cache:
            return None
        p = self._cache_path(h)
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None

    def _cache_put(self, h: str, rec: Dict[str, Any]) -> None:
        if not self.use_cache:
            return
        p = self._cache_path(h)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = f"{p}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, p)

    def _log(self, rec: Dict[str, Any]) -> None:
        line = json.dumps(rec, ensure_ascii=False)
        with self._log_lock:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    # -- chat ------------------------------------------------------------------
    def chat(self, model: str, user: Optional[str] = None, system: str = "",
             messages: Optional[List[Dict[str, str]]] = None, max_tokens: int = 2048,
             temperature: float = 0.0, seed: int = 1234, json_mode: bool = False,
             tag: str = "", cache: bool = True, sample_idx: int = 0,
             effort: Optional[str] = None) -> LLMResult:
        """One chat completion. Pass either `user` (single user turn) or `messages`
        ([{role: user|assistant, content}]). `sample_idx` is part of the cache key so
        repeated stochastic samples (e.g. F3's 3 samples) are cached separately.
        `effort` (claude-* only): low|medium|high|xhigh|max via output_config; None = model
        default (claude-opus-5-5 defaults to medium). Part of the cache key."""
        if messages is None:
            messages = [{"role": "user", "content": user or ""}]
        max_tokens = max(max_tokens, self._MIN_MAX_TOKENS.get(model, 0))
        payload = {"model": model, "system": system, "messages": messages, "max_tokens": max_tokens,
                   "temperature": temperature, "seed": seed, "json_mode": json_mode,
                   "sample_idx": sample_idx}
        if effort:
            payload["effort"] = effort
        h = self._hash(payload)
        t0 = time.time()
        hit = self._cache_get(h) if cache else None
        if hit is not None:
            res = LLMResult(hit["text"], model, h, hit["tokens_in"], hit["tokens_out"],
                            hit["latency_ms"], True, round((time.time() - t0) * 1000, 1))
            self._log({"ts": time.time(), "kind": "chat", "model": model, "prompt_hash": h,
                       "tokens_in": res.tokens_in, "tokens_out": res.tokens_out,
                       "latency_ms": res.latency_ms, "cached": True, "tag": tag, "ok": True})
        else:
            res = self._chat_uncached(model, system, messages, max_tokens, temperature, seed,
                                      json_mode, tag, h, effort)
            if cache:
                self._cache_put(h, {"text": res.text, "tokens_in": res.tokens_in,
                                    "tokens_out": res.tokens_out, "latency_ms": res.latency_ms,
                                    "model": model, "ts": time.time()})
        if json_mode:
            try:
                res.json = extract_json(res.text)
            except Exception as exc:  # noqa: BLE001
                res.json_error = str(exc)
        return res

    def _chat_uncached(self, model, system, messages, max_tokens, temperature, seed,
                       json_mode, tag, h, effort=None) -> LLMResult:
        fam = "anthropic" if model.lower().startswith("claude") else "openai"
        last: Optional[Exception] = None
        for attempt in range(self.max_retries):
            rejected = self._REJECTED.setdefault(model, set(self._KNOWN_REJECTED.get(model, set())))
            t0 = time.time()
            try:
                with self._sem[fam]:
                    if fam == "anthropic":
                        kw: Dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
                        if system:
                            kw["system"] = system
                        if "temperature" not in rejected:
                            kw["temperature"] = temperature
                        if effort:
                            kw["extra_body"] = {"output_config": {"effort": effort}}
                        r = self._anthropic_client().messages.create(**kw)
                        text = "".join(b.text for b in r.content if getattr(b, "type", None) == "text")
                        tin, tout = r.usage.input_tokens, r.usage.output_tokens
                    else:
                        msgs = ([{"role": "system", "content": system}] if system else []) + messages
                        kw = {"model": model, "messages": msgs, "max_completion_tokens": max_tokens}
                        if "temperature" not in rejected:
                            kw["temperature"] = temperature
                        if "seed" not in rejected:
                            kw["seed"] = seed
                        if json_mode and "response_format" not in rejected:
                            kw["response_format"] = {"type": "json_object"}
                        r = self._openai_client().chat.completions.create(**kw)
                        text = r.choices[0].message.content or ""
                        tin, tout = r.usage.prompt_tokens, r.usage.completion_tokens
                lat = round((time.time() - t0) * 1000, 1)
                self._log({"ts": time.time(), "kind": "chat", "model": model, "prompt_hash": h,
                           "tokens_in": tin, "tokens_out": tout, "latency_ms": lat, "cached": False,
                           "tag": tag, "ok": True, "attempt": attempt})
                return LLMResult(text, model, h, tin, tout, lat, False, lat)
            except Exception as exc:  # noqa: BLE001
                last = exc
                msg = str(exc)
                status = getattr(exc, "status_code", None)
                # Learn unsupported params from 400s and retry immediately without them.
                if status == 400:
                    learned = False
                    for p in ("temperature", "seed", "response_format", "top_p"):
                        if p in msg and p not in rejected:
                            rejected.add(p)
                            learned = True
                    if learned:
                        continue
                    self._log({"ts": time.time(), "kind": "chat", "model": model, "prompt_hash": h,
                               "tag": tag, "ok": False, "error": msg[:500], "attempt": attempt})
                    raise
                self._log({"ts": time.time(), "kind": "chat", "model": model, "prompt_hash": h,
                           "tag": tag, "ok": False, "error": msg[:500], "attempt": attempt})
                time.sleep(min(60, 2 ** attempt) + random.random())
        raise RuntimeError(f"LLM call failed ({model}) after {self.max_retries} attempts: {last}")

    # -- embeddings --------------------------------------------------------------
    def embed(self, texts: Sequence[str], tag: str = "", batch_size: int = 128):
        """Embed texts; returns an L2-normalised float32 array [n, d]. Each text is
        cached individually, so re-embedding a node set only pays for new texts."""
        import numpy as np
        backend = os.environ.get("EMBED_BACKEND", "azure")
        model = self.embed_model
        keys = [self._hash({"embed_backend": backend, "model": model, "text": t}) for t in texts]
        out: List[Optional[List[float]]] = [None] * len(texts)
        todo: List[int] = []
        for i, k in enumerate(keys):
            hit = self._cache_get(k)
            if hit is not None:
                out[i] = hit["vec"]
            else:
                todo.append(i)
        n_cached = len(texts) - len(todo)
        for s in range(0, len(todo), batch_size):
            idx = todo[s:s + batch_size]
            batch = [texts[i] or " " for i in idx]
            vecs, tin, lat = self._embed_uncached(backend, model, batch)
            self._log({"ts": time.time(), "kind": "embed", "model": model, "backend": backend,
                       "n": len(batch), "tokens_in": tin, "tokens_out": 0, "latency_ms": lat,
                       "cached": False, "tag": tag, "ok": True})
            for i, v in zip(idx, vecs):
                out[i] = v
                self._cache_put(keys[i], {"vec": v})
        if n_cached:
            self._log({"ts": time.time(), "kind": "embed", "model": model, "backend": backend,
                       "n": n_cached, "tokens_in": 0, "tokens_out": 0, "latency_ms": 0,
                       "cached": True, "tag": tag, "ok": True})
        arr = np.asarray(out, dtype="float32")
        if arr.ndim == 2 and len(arr):
            arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12
        return arr

    def _embed_uncached(self, backend: str, model: str, batch: List[str]):
        if backend != "azure":
            raise RuntimeError(f"EMBED_BACKEND={backend} not implemented")
        last = None
        for attempt in range(self.max_retries):
            t0 = time.time()
            try:
                with self._sem["embed"]:
                    r = self._openai_client().embeddings.create(model=model, input=batch)
                lat = round((time.time() - t0) * 1000, 1)
                return [d.embedding for d in r.data], getattr(r.usage, "prompt_tokens", 0), lat
            except Exception as exc:  # noqa: BLE001
                last = exc
                if getattr(exc, "status_code", None) in (400, 401, 403, 404):
                    raise
                time.sleep(min(60, 2 ** attempt) + random.random())
        raise RuntimeError(f"embedding failed after {self.max_retries} attempts: {last}")


_CLIENT: Optional[LLMClient] = None
_CLIENT_LOCK = threading.Lock()


def get_client(**kw) -> LLMClient:
    """Process-wide shared client (shares semaphores + learned param rejections)."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = LLMClient(**kw)
    return _CLIENT


def usage_summary(log_path: str = DEFAULT_LOG_PATH, tag_prefix: str = "") -> Dict[str, Any]:
    """Aggregate the call log: per model/tag counts, tokens, latency median/P90."""
    import statistics
    agg: Dict[str, Dict[str, Any]] = {}
    if not os.path.exists(log_path):
        return {}
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if tag_prefix and not str(r.get("tag", "")).startswith(tag_prefix):
                continue
            key = f"{r.get('model')}|{r.get('tag')}"
            a = agg.setdefault(key, {"calls": 0, "cached": 0, "errors": 0, "tokens_in": 0,
                                     "tokens_out": 0, "lat": []})
            if not r.get("ok"):
                a["errors"] += 1
                continue
            a["calls"] += 1
            a["cached"] += int(bool(r.get("cached")))
            a["tokens_in"] += r.get("tokens_in") or 0
            a["tokens_out"] += r.get("tokens_out") or 0
            if not r.get("cached") and r.get("kind") == "chat":
                a["lat"].append(r.get("latency_ms") or 0)
    for a in agg.values():
        lat = sorted(a.pop("lat"))
        a["latency_median_ms"] = statistics.median(lat) if lat else None
        a["latency_p90_ms"] = lat[int(0.9 * (len(lat) - 1))] if lat else None
    return agg
