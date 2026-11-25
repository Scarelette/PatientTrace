"""F3 dual-patient trajectory-level comparison (SPEC §5). Public API:

Engine (methods/f3_compare/engine.py)
    DualSession(patient_a, patient_b, model, session_id, diff_effort="low", sample_idx=0)
        .ask_both(question, annotate=False) -> turn     # both answer in parallel
        .ask_one(side, question, annotate=False) -> turn # follow-up to one side
        .annotate(turn) / .annotate_async(turn)         # frozen F1 'ours' alignment (+ diff_summary)
        .spec_turns() -> SPEC §2 F3 `turns`
    model = angel_ext.patient_sim.PatientModel("local"|"http", url) (unmodified ANGEL patient model)

diff_summary (methods/f3_compare/diff.py)
    diff_summary(question, {a|b: {patient_id, response, alignment}}, effort="low") -> dict
    validate(text, {a: [aligned edge ids], b: [...]}) -> (cited_edges, problems)

Frontend endpoints (methods/f3_compare/api.py), JSON-ready dicts
    start_pair, shared_question, follow_up, get_turn, get_session, export_session, close_session

Batch: python -m methods.f3_compare.run --split dev --stage generate|annotate
"""

from .api import (close_session, export_session, follow_up, get_session, get_turn,  # noqa: F401
                  shared_question, start_pair, suggested_probe)
from .diff import diff_summary, validate  # noqa: F401
from .engine import DualSession, PatientSession  # noqa: F401
