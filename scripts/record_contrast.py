"""Record a Formulation Contrast example through the live app: start an F3 pair, ask the shared questions,
wait for both answers and their alignment, then compute the gap / baskets per turn, the reveal and the
learning point. Saves data/replay/<case>.json and adds it to the replay index (as the default).
Usage: python scripts/record_contrast.py URL A_IDX B_IDX CASE_ID FOCUS "note" q1 q2 ..."""
import json, os, sys, time, datetime
import requests
from patienttrace import contrast as FC, paths as P
URL, ai, bi, case, focus, note, QS = sys.argv[1].rstrip("/"), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7:]
S = requests.Session(); S.trust_env = False
def post(p, body, t=300): r = S.post(URL + p, json=body, timeout=t); r.raise_for_status(); return r.json()
def get(p, t=120): r = S.get(URL + p, timeout=t); r.raise_for_status(); return r.json()
pa, pb = f"p{ai:03d}", f"p{bi:03d}"
st = post("/api/ext/f3/start", {"patient_a": pa, "patient_b": pb, "probe_question": QS[0]})
sid, names = st["session_id"], {s: st["patients"][s]["name"] for s in "ab"}
turns = []
for k, q in enumerate(QS, 1):
    t0 = time.time(); post("/api/ext/f3/shared", {"session_id": sid, "question": q})
    for _ in range(120):
        tv = get(f"/api/ext/f3/turn?session_id={sid}&turn_no={k}")
        if tv["a"] and tv["b"] and tv["a"].get("alignment") and tv["b"].get("alignment"): break
        time.sleep(2)
    an = FC.turn_analysis(pa, pb, tv["a"], tv["b"])
    turns.append({"question": q, "a": {"response": tv["a"]["response"], "alignment": tv["a"]["alignment"]},
                  "b": {"response": tv["b"]["response"], "alignment": tv["b"]["alignment"]}, "fc": an})
    print(f"T{k} ({time.time() - t0:.0f}s) d_raw={an['d_raw']:.3f} [{an['method']}] | {names['a']}: {an['a']['quote']} | {names['b']}: {an['b']['quote']} | both={len(an['both'])}", flush=True)
eased = FC.ease([t["fc"]["d_raw"] for t in turns])
for t, d in zip(turns, eased): t["fc"]["d"] = d
rv = FC.reveal(pa, pb, focus)
lp = FC.learning_point(names, [{"question": t["question"], "d": t["fc"]["d"]} for t in turns])
rec = {"case_id": case, "kind": "formulation-contrast", "patient_a": pa, "patient_b": pb, "names": names,
       "tags": {s: st["patients"][s]["tags"] for s in "ab"}, "probe": QS[0], "note": note, "focus": rv["concept_id"], "focus_name": rv["name"],
       "source": f"recorded {datetime.datetime.now().isoformat(timespec='minutes')} through the live app (patient model, frozen F1 aligner); gap {FC.VERSION}",
       "turns": turns, "reveal": rv, "learning": lp}
d = P.data("replay"); json.dump(rec, open(os.path.join(d, case + ".json"), "w"), indent=1, ensure_ascii=False)
ip = os.path.join(d, "index.json"); idx = json.load(open(ip))
idx["cases"] = [c for c in idx["cases"] if c["case_id"] != case]
idx["cases"].insert(0, {"case_id": case, "title": f"{names['a']} vs {names['b']} ({note})", "probe": QS[0], "turns": len(turns),
                        "focus": rv["concept_id"], "focus_name": rv["name"], "kind": "formulation-contrast"})
idx["default"] = case; json.dump(idx, open(ip, "w"), indent=1)
print("d_raw:", [t["fc"]["d_raw"] for t in turns]); print("d (eased):", eased)
print("reveal:", {s: [x["label"][:40] for x in rv[s]] for s in "ab"}); print("learning:", lp.get("point") or lp)
