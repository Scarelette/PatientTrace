"""F1 utterance–network alignment (SPEC §3). Public API:

    align(utterance, patient_id, context=[], method="ours", utterance_id=None) -> F1 record
    align_network(utterance, context_turns, network, method="ours", utterance_id=None) -> F1 record
    await align_async(utterance, patient_id, context=[], method="ours", utterance_id=None)
    submit_align(utterance, patient_id, context, callback, method="ours", utterance_id=None) -> Future

`context` = earlier turns [{"role": "therapist"|"patient", "content": str}], most recent last
(the therapist question being answered should be the last entry). Any utterance can be aligned
against any patient's network (F3 cross-alignment). Never raises on LLM/API failures: the record
then has no items, no_match=true and an `error` field.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from .core import CONFIG, LLM_METHODS, METHODS, align_res, config_hash, net_res, net_res_from_network

__all__ = ["align", "align_network", "align_async", "submit_align", "METHODS", "CONFIG", "config_hash"]


def align(utterance: str, patient_id: str, context: Optional[List[Dict[str, Any]]] = None,
          method: str = "ours", utterance_id: Optional[str] = None) -> Dict[str, Any]:
    """Align one utterance against patient `patient_id`'s (long-profile) network."""
    return align_res(net_res(patient_id), utterance, context, method, utterance_id)


def align_network(utterance: str, context_turns: Optional[List[Dict[str, Any]]], network: Dict[str, Any],
                  method: str = "ours", utterance_id: Optional[str] = None) -> Dict[str, Any]:
    """Role-file interface: align against an explicit SPEC-format network dict (e.g. a network
    generated live in the demo). Node embeddings are taken from the precomputed store when the
    network matches it, otherwise computed (and cached) via the S0.3 wrapper."""
    return align_res(net_res_from_network(network), utterance, context_turns, method, utterance_id)


async def align_async(utterance: str, patient_id: str, context: Optional[List[Dict[str, Any]]] = None,
                      method: str = "ours", utterance_id: Optional[str] = None) -> Dict[str, Any]:
    """Non-blocking variant for the FastAPI backend: send the patient reply to the browser first,
    then `await align_async(...)` (runs the blocking call in a worker thread) and push the record
    (e.g. over SSE/WebSocket or a poll endpoint keyed by utterance_id)."""
    return await asyncio.to_thread(align, utterance, patient_id, context, method, utterance_id)


_POOL: Optional[ThreadPoolExecutor] = None


def submit_align(utterance: str, patient_id: str, context: Optional[List[Dict[str, Any]]],
                 callback: Optional[Callable[[Dict[str, Any]], None]] = None, method: str = "ours",
                 utterance_id: Optional[str] = None) -> Future:
    """Fire-and-forget variant for sync code: returns a Future immediately; `callback(record)`
    is invoked when the alignment is ready (callback exceptions are swallowed)."""
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="f1-align")
    fut = _POOL.submit(align, utterance, patient_id, context, method, utterance_id)
    if callback is not None:
        def _done(f: Future):
            try:
                callback(f.result())
            except Exception:  # noqa: BLE001
                pass
        fut.add_done_callback(_done)
    return fut
