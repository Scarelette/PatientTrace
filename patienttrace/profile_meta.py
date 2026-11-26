"""Derived metadata for existing profiles: display names for nameless profiles,
age buckets, inferred culture, and disorder categories — used for the picker's
names, tags, and filters. All deterministic (stable across reloads).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Name banks (multicultural). A nameless profile gets a stable name from the bank
# for its gender, biased toward a detected culture when possible.
# ---------------------------------------------------------------------------
_NAMES = {
    "female": ["Emma", "Olivia", "Grace", "Chloe", "Zoe", "Maya", "Elena", "Rosa", "Ingrid", "Nina",
               "Clara", "Ruth", "Diana", "Alice", "Julia", "Hannah", "Laura", "Iris", "Vera", "Stella"],
    "male": ["Liam", "Noah", "Samuel", "Lucas", "Marco", "Erik", "Ivan", "Daniel", "Andre", "Victor",
             "Oscar", "Leo", "Adam", "Paul", "Simon", "Felix", "Hugo", "Karl", "Nathan", "Owen"],
    "neutral": ["Alex", "Sam", "Jordan", "Taylor", "Casey", "Riley", "Jamie", "Morgan", "Avery", "Quinn"],
}
_CULTURE_NAMES = {
    "Persian / Iranian": {"female": ["Leila", "Nadia", "Yasmin", "Roya", "Shirin", "Parisa"],
                          "male": ["Omar", "Reza", "Kian", "Darius", "Farid", "Navid"]},
    "East Asian": {"female": ["Mei", "Yuki", "Hana", "Lin", "Sora", "Jia"],
                   "male": ["Chen", "Kenji", "Wei", "Jin", "Haru", "Tao"]},
    "South Asian": {"female": ["Priya", "Aisha", "Anaya", "Diya", "Meera", "Sana"],
                    "male": ["Arjun", "Ravi", "Rohan", "Vikram", "Amir", "Dev"]},
    "Arab / Middle Eastern": {"female": ["Fatima", "Layla", "Amira", "Huda", "Salma", "Rania"],
                              "male": ["Hassan", "Tariq", "Yusuf", "Karim", "Sami", "Bilal"]},
    "Hispanic / Latino": {"female": ["Sofia", "Ana", "Lucia", "Carmen", "Valeria", "Marisol"],
                          "male": ["Diego", "Mateo", "Javier", "Carlos", "Andres", "Rafael"]},
    "African": {"female": ["Amara", "Zola", "Nia", "Ayana", "Imani", "Folake"],
                "male": ["Kwame", "Kofi", "Jabari", "Sefu", "Tendai", "Obi"]},
}

_CULTURE_KEYWORDS = {
    "Persian / Iranian": ["iranian", "persian", "jinn", "tehran"],
    "East Asian": ["chinese", "japanese", "korean", "taiwan", "confucian", "east asian"],
    "South Asian": ["indian", "pakistani", "bangladesh", "hindu", "south asian", "sikh"],
    "Arab / Middle Eastern": ["arab", "muslim", "islam", "middle eastern", "egypt", "syria", "lebanon"],
    "Hispanic / Latino": ["latino", "latina", "hispanic", "mexican", "cuban", "colombian", "puerto rican"],
    "African": ["nigerian", "ghana", "kenyan", "african", "yoruba", "ethiopia"],
}

# ---------------------------------------------------------------------------
# Disorder categories from diagnosis_hint text.
# ---------------------------------------------------------------------------
_DISORDER_KEYWORDS = {
    "Anxiety": ["anxiety", "panic", "phobia", "gad", "worry", "social anxiety"],
    "Depression": ["depress", "dysthymia", "low mood", "anhedonia", "mdd"],
    "PTSD / Trauma": ["ptsd", "trauma", "post-traumatic", "abuse", "assault"],
    "OCD": ["ocd", "obsessive", "compulsive", "intrusive thought"],
    "Bipolar": ["bipolar", "mania", "manic", "hypomania"],
    "Psychosis": ["psychosis", "psychotic", "schizophren", "hallucinat", "delusion"],
    "Substance use": ["substance", "alcohol", "addiction", "drug", "opioid"],
    "Eating disorder": ["eating disorder", "anorexia", "bulimia", "binge"],
    "Personality": ["personality", "borderline", "bpd", "narcissi"],
    "Self-harm / Suicidality": ["self-harm", "self harm", "suicid", "self-injur", "self-mutilation"],
}


def _text_blob(profile: Dict[str, Any]) -> str:
    ident = profile.get("identity", {}) or {}
    parts: List[str] = [str(ident.get("source_title", ""))]
    parts += [str(x) for x in (ident.get("diagnosis_hint") or [])]
    parts += [str(x) for x in (profile.get("background") or [])]
    parts += [str(x) for x in (profile.get("presenting_problems") or [])]
    return " ".join(parts).lower()


def infer_culture(profile: Dict[str, Any]) -> Optional[str]:
    blob = _text_blob(profile)
    for culture, kws in _CULTURE_KEYWORDS.items():
        if any(k in blob for k in kws):
            return culture
    return None


def norm_gender(g: Any) -> str:
    s = str(g or "").strip().lower()
    if s.startswith("f"):
        return "female"
    if s.startswith("m"):
        return "male"
    return "neutral"


def assigned_name(idx: int, gender: str, culture: Optional[str]) -> str:
    g = norm_gender(gender)
    if culture and culture in _CULTURE_NAMES and g in _CULTURE_NAMES[culture]:
        pool = _CULTURE_NAMES[culture][g]
    else:
        pool = _NAMES.get(g, _NAMES["neutral"])
    return pool[(idx * 2654435761) % len(pool)]


def age_int(age: Any) -> Optional[int]:
    if isinstance(age, int):
        return age
    m = re.search(r"\d+", str(age or ""))
    return int(m.group(0)) if m else None


def age_bucket(age: Any) -> str:
    n = age_int(age)
    if n is None:
        return "Unknown"
    if n < 13:
        return "Child (<13)"
    if n < 18:
        return "Teen (13-17)"
    if n < 26:
        return "Young adult (18-25)"
    if n < 41:
        return "Adult (26-40)"
    if n < 65:
        return "Middle age (41-64)"
    return "Older adult (65+)"


def disorders_of(profile: Dict[str, Any]) -> List[str]:
    blob = _text_blob(profile)
    cats = [c for c, kws in _DISORDER_KEYWORDS.items() if any(k in blob for k in kws)]
    return cats or ["Other"]


def display_name(idx: int, profile: Dict[str, Any]) -> str:
    ident = profile.get("identity", {}) or {}
    nm = str(ident.get("name") or "").strip()
    if nm:
        return nm
    return assigned_name(idx, ident.get("gender"), infer_culture(profile))
