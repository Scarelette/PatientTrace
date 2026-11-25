"""Evidence-span repair: map an LLM-produced evidence string back to an EXACT substring
of the utterance (SPEC §3 step 3). Patient text contains curly quotes / dashes / ellipses
that LLMs often normalise; we search in a normalised view and map the match back to the
original character offsets, so the returned span is always `utterance[s:e]`."""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Tuple

_CHARMAP = {"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
            "“": '"', "”": '"', "„": '"', "″": '"',
            "—": "-", "–": "-", "‒": "-", "‐": "-", "‑": "-",
            "…": "...", " ": " "}


def _norm_with_map(s: str) -> Tuple[str, List[int]]:
    """Normalised string + for every normalised char the index of the source char.
    Lower-cases, maps quotes/dashes/ellipsis, collapses whitespace runs to one space."""
    out: List[str] = []
    idx: List[int] = []
    prev_space = True
    for i, ch in enumerate(s):
        rep = _CHARMAP.get(ch)
        if rep is None:
            rep = unicodedata.normalize("NFKC", ch).lower()
        for c in rep:
            if c.isspace():
                if prev_space:
                    continue
                c = " "
                prev_space = True
            else:
                prev_space = False
            out.append(c)
            idx.append(i)
    # strip trailing space
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def _strip_ev(ev: str) -> str:
    ev = ev.strip()
    # LLMs sometimes wrap the span in quotes
    while len(ev) >= 2 and ev[0] in "\"'“‘" and ev[-1] in "\"'”’":
        ev = ev[1:-1].strip()
    return ev


def _find_norm(ev: str, utt: str) -> Optional[Tuple[int, int]]:
    nu, mu = _norm_with_map(utt)
    ne, _ = _norm_with_map(ev)
    ne = ne.strip()
    if not ne:
        return None
    p = nu.find(ne)
    if p < 0:
        return None
    s, e = mu[p], mu[p + len(ne) - 1] + 1
    return s, e


def _longest_common(ev: str, utt: str) -> Optional[Tuple[int, int, int]]:
    """Longest common substring (normalised) -> (orig_start, orig_end, normalised_len)."""
    nu, mu = _norm_with_map(utt)
    ne, _ = _norm_with_map(ev)
    if not nu or not ne:
        return None
    best, best_end = 0, 0
    prev = [0] * (len(ne) + 1)
    for i in range(1, len(nu) + 1):
        cur = [0] * (len(ne) + 1)
        ci = nu[i - 1]
        for j in range(1, len(ne) + 1):
            if ci == ne[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best, best_end = cur[j], i
        prev = cur
    if best == 0:
        return None
    ps = best_end - best
    # trim leading/trailing spaces of the normalised match
    while ps < best_end and nu[ps] == " ":
        ps += 1
    pe = best_end
    while pe > ps and nu[pe - 1] == " ":
        pe -= 1
    if pe <= ps:
        return None
    return mu[ps], mu[pe - 1] + 1, pe - ps


def repair_evidence(ev, utt: str) -> Tuple[Optional[str], str]:
    """-> (exact substring of utt or None, status) with status in
    exact | normalised | fragment | partial | empty | unrepairable."""
    if not isinstance(ev, str) or not ev.strip():
        return None, "empty"
    if ev in utt and ev.strip():
        return ev, "exact"
    ev2 = _strip_ev(ev)
    if ev2 and ev2 in utt:
        return ev2, "exact"
    span = _find_norm(ev2, utt)
    if span:
        return utt[span[0]:span[1]], "normalised"
    # elided spans "I can't sleep ... my heart races": keep the longest piece that matches
    pieces = [p.strip(" ,;:") for p in re.split(r"\.\.\.|…|\[\.\.\.\]", ev2) if p.strip(" ,;:")]
    if len(pieces) > 1:
        found = []
        for p in pieces:
            sp = _find_norm(p, utt)
            if sp:
                found.append(sp)
        if found:
            s, e = max(found, key=lambda x: x[1] - x[0])
            return utt[s:e], "fragment"
    # last resort: long common substring covering most of the claimed span
    lc = _longest_common(ev2, utt)
    if lc:
        s, e, n = lc
        ne, _ = _norm_with_map(ev2)
        if n >= 8 and n >= 0.6 * len(ne):
            return utt[s:e], "partial"
    return None, "unrepairable"


def cover_span(a: str, b: str, utt: str) -> str:
    """Smallest exact substring of utt containing both exact substrings a and b."""
    pa, pb = utt.find(a), utt.find(b)
    if pa < 0 or pb < 0:
        return a if pa >= 0 else b
    s, e = min(pa, pb), max(pa + len(a), pb + len(b))
    return utt[s:e]
