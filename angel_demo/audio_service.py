"""Audio service for the patient chat (speech-to-text + text-to-speech).

Optional: needs an Azure OpenAI resource (DEMO_GPT_ENDPOINT + DEMO_GPT_KEY) with two
deployments:
  * gpt-4o-transcribe   -> speech-to-text (therapist mic input)
  * gpt-4o-mini-tts     -> text-to-speech (patient voice reply)

Voice + delivery are chosen from the patient profile (identity gender/age/role,
speaking style, current emotions) so the patient "sounds" like the profile.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any, Dict, Optional

# Azure OpenAI credentials (same variables as network_model). Voice is off unless both are set.
_ENDPOINT = os.environ.get("DEMO_GPT_ENDPOINT", "")
_API_KEY = os.environ.get("DEMO_GPT_KEY", "")
_API_VERSION = os.environ.get("DEMO_GPT_API_VERSION", "2024-12-01-preview")

STT_MODEL = os.environ.get("DEMO_STT_MODEL", "gpt-4o-transcribe")
TTS_MODEL = os.environ.get("DEMO_TTS_MODEL", "gpt-4o-mini-tts")

_CLIENT = None
_LOCK = threading.Lock()


def audio_available() -> bool:
    return bool(_API_KEY and _ENDPOINT)


def _client():
    global _CLIENT
    if _CLIENT is None:
        with _LOCK:
            if _CLIENT is None:
                from openai import AzureOpenAI
                _CLIENT = AzureOpenAI(api_version=_API_VERSION,
                                      azure_endpoint=_ENDPOINT, api_key=_API_KEY)
    return _CLIENT


# ---------------------------------------------------------------------------
# Speech-to-text
# ---------------------------------------------------------------------------
_STT_PROMPT = ("A therapist speaking to a patient in a psychotherapy session: "
               "questions about mood, anxiety, sleep, relationships, and feelings.")


def transcribe(audio_path: str) -> str:
    with open(audio_path, "rb") as f:
        try:
            r = _client().audio.transcriptions.create(
                model=STT_MODEL, file=f, prompt=_STT_PROMPT)
        except TypeError:
            f.seek(0)
            r = _client().audio.transcriptions.create(model=STT_MODEL, file=f)
    return (getattr(r, "text", "") or "").strip()


# ---------------------------------------------------------------------------
# Voice + delivery from the profile
# ---------------------------------------------------------------------------
def _select_voice(profile: Dict[str, Any]) -> str:
    """Pick an OpenAI voice from the patient's gender/age/role."""
    ident = (profile or {}).get("identity", {}) or {}
    role = str(ident.get("role", "patient")).lower()
    gender = str(ident.get("gender", "")).lower()
    try:
        age = int(ident.get("age")) if ident.get("age") is not None else None
    except (ValueError, TypeError):
        age = None

    if role and role != "patient":
        if role in {"mother", "wife", "daughter"}:
            return "coral"
        if role in {"father", "husband", "son"}:
            return "ash"

    if any(t in gender for t in ("female", "woman", "girl")):
        return "shimmer" if age and age < 18 else "coral"
    if any(t in gender for t in ("male", "man", "boy")):
        return "fable" if age and age < 18 else "ash"
    return "fable" if (age is not None and age < 18) else "alloy"


def _delivery(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Speed + a natural-language style instruction from speaking_style/emotions."""
    style = (profile or {}).get("speaking_style", {}) or {}
    tone = str(style.get("tone", "")).lower()
    disclosure = str(style.get("disclosure_style", "")).lower()
    emotions = " ".join(str(e).lower() for e in (profile or {}).get("emotions", []) or [])
    blob = " ".join((tone, disclosure, emotions))

    speed = 1.0
    if any(x in blob for x in ("anxious", "nervous", "agitated", "panicked", "pressured")):
        speed = 1.08
    elif any(x in blob for x in ("sad", "depress", "tired", "withdrawn", "hopeless", "empty", "low")):
        speed = 0.93
    elif "slow" in blob:
        speed = 0.94

    descriptors = []
    if any(x in blob for x in ("guarded", "cautious", "hesitant")):
        descriptors.append("guarded and slightly hesitant")
    if any(x in blob for x in ("soft", "quiet")):
        descriptors.append("soft spoken")
    if any(x in blob for x in ("sad", "empty", "hopeless", "down", "depress")):
        descriptors.append("subdued, low mood")
    if any(x in blob for x in ("anxious", "nervous")):
        descriptors.append("mild anxiety")
    if any(x in blob for x in ("irritable", "angry", "resent")):
        descriptors.append("slight irritability")
    if not descriptors:
        descriptors.append("natural and conversational")

    instructions = ("Speak as this patient in a therapy session: "
                    + ", ".join(dict.fromkeys(descriptors))
                    + ". Keep the emotional tone and pacing consistent for the whole reply.")
    return {"speed": speed, "instructions": instructions}


# ---------------------------------------------------------------------------
# Text-to-speech (streamed, with a small per-reply cache)
# ---------------------------------------------------------------------------
import collections
import hashlib

_CACHE_MAX = int(os.environ.get("DEMO_TTS_CACHE", "64"))
_CACHE: "collections.OrderedDict[str, bytes]" = collections.OrderedDict()
_CACHE_LOCK = threading.Lock()


def _tts_kwargs(text: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    voice = _select_voice(profile)
    d = _delivery(profile)
    kw = {"model": TTS_MODEL, "voice": voice, "input": text,
          "speed": d["speed"], "response_format": "mp3"}
    if d.get("instructions"):
        kw["instructions"] = d["instructions"]
    return kw


def _cache_key(kw: Dict[str, Any]) -> str:
    raw = "|".join(str(kw.get(k, "")) for k in ("model", "voice", "input", "speed", "instructions"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> Optional[bytes]:
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    return None


def _cache_put(key: str, data: bytes) -> None:
    if not data:
        return
    with _CACHE_LOCK:
        _CACHE[key] = data
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


def synthesize(text: str, profile: Optional[Dict[str, Any]] = None) -> bytes:
    """Render the patient reply to mp3 bytes, voiced per the profile (cached)."""
    plain = re.sub(r"<[^>]+>", "", text or "").strip()
    if not plain:
        return b""
    kw = _tts_kwargs(plain, profile or {})
    key = _cache_key(kw)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    r = _client().audio.speech.create(**kw)
    data = r.read() if hasattr(r, "read") else r.content
    _cache_put(key, data)
    return data


def synthesize_stream(text: str, profile: Optional[Dict[str, Any]] = None):
    """Yield mp3 chunks as they arrive (so playback can start sooner), and
    cache the full clip when done. Falls back to a single cached blob if the
    SDK has no streaming helper. Returns (already_cached_bytes_or_None, generator)."""
    plain = re.sub(r"<[^>]+>", "", text or "").strip()
    if not plain:
        return b"", None
    kw = _tts_kwargs(plain, profile or {})
    key = _cache_key(kw)
    hit = _cache_get(key)
    if hit is not None:
        return hit, None   # instant replay

    def gen():
        buf = bytearray()
        try:
            with _client().audio.speech.with_streaming_response.create(**kw) as resp:
                for chunk in resp.iter_bytes(4096):
                    if chunk:
                        buf.extend(chunk)
                        yield chunk
        except AttributeError:
            # older SDK: no streaming helper -> one shot
            r = _client().audio.speech.create(**kw)
            data = r.read() if hasattr(r, "read") else r.content
            buf.extend(data)
            yield data
        _cache_put(key, bytes(buf))

    return None, gen()
