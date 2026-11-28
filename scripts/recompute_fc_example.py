"""Recompute the Formulation Contrast example with the shared function space (needs the model key):
annotate nodes, measure the calibration, re-align both answers per turn with the Interview aligner, then
d_t, g_t, gists, baskets, reveal hints and a learning point per round length (3..N questions).
Usage: PATIENTTRACE_DATA=... python scripts/recompute_fc_example.py fc.p009-p290 [--keep-align]
--keep-align keeps the stored per-turn alignments (the aligner is nondeterministic) and only recomputes the rest."""
import json, os, random, shutil, sys
import numpy as np
from patienttrace import contrast as FC, paths as P
from patienttrace.angel_ext import data as D
from patienttrace.angel_ext.demo_focus import contrast_graph
case = sys.argv[1]; KEEP = "--keep-align" in sys.argv
REALIGN = {a.split("=", 1)[1] for a in sys.argv if a.startswith("--realign=")}   # e.g. --realign=a5 re-aligns side a of turn 5
os.makedirs(P.data("fc"), exist_ok=True)
# 1. node annotations (shipped with the data so browse mode needs no model)
CAL_PAIRS = [("p009", "p290"), ("p001", "p179"), ("p010", "p189")]
for pid in sorted({p for pair in CAL_PAIRS for p in pair}):
    FC.annotations(pid)
    src = P.state("fc", f"nodes_{pid}.json")
    if os.path.exists(src): shutil.copy(src, P.data("fc", f"nodes_{pid}.json"))
# 2. calibration in the function space
same, rand = [], []
for pa, pb in CAL_PAIRS:
    cg = contrast_graph(pa, pb)
    for c in cg["concepts"]:
        same += [(FC.node_text(pa, x["id"]), FC.node_text(pb, y["id"])) for x in c["nodes"]["a"] for y in c["nodes"]["b"]]
    A = [n["id"] for n in D.load_network(pa)["nodes"]]; B = [n["id"] for n in D.load_network(pb)["nodes"]]
    random.seed(7); rand += [(FC.node_text(pa, random.choice(A)), FC.node_text(pb, random.choice(B))) for _ in range(80)]
def cos(pairs):
    V = FC._embed([p[0] for p in pairs] + [p[1] for p in pairs]); n = len(pairs); return np.sum(V[:n] * V[n:], axis=1)
cs, cr = cos(same), cos(rand)
cal = {"lo": round(float(np.percentile(cr, 10)), 3), "hi": round(float(np.median(cs)), 3), "n_same": len(cs), "n_random": len(cr),
       "rule": "lo = 10th percentile of random cross-patient pairs, hi = median of same-concept pairs",
       "space": "type + label + LLM function line, text-embedding-3-large", "pairs": CAL_PAIRS}
json.dump(cal, open(P.data("fc", "calibration.json"), "w"), indent=1)
print("calibration (label + function space):", cal, "| same p25/75:", np.percentile(cs, [25, 75]).round(3), "| random p25/75:", np.percentile(cr, [25, 75]).round(3))
# 3. re-align and analyse every turn
path = P.data("replay", case + ".json")
FROM = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--from=")), None)   # build a new FC case from a live recording
if FROM:
    src = json.load(open(P.data("replay", FROM + ".json")))
    rec = {k: src[k] for k in ("patient_a", "patient_b", "names", "probe", "focus", "focus_name", "tags") if k in src}
    rec.update({"case_id": case, "kind": "formulation-contrast", "note": src.get("focus_name") or src.get("note"),
                "source": f"shared questions and answers from {FROM}",
                "turns": [{"question": t["question"], "a": {"response": t["a"]["response"]}, "b": {"response": t["b"]["response"]}} for t in src["turns"]]})
    idx = json.load(open(P.data("replay", "index.json")))
    if not any(c["case_id"] == case for c in idx["cases"]):
        idx["cases"].append({"case_id": case, "title": f"{rec['names']['a']} vs {rec['names']['b']} ({(rec.get('focus_name') or '').lower()})",
                             "probe": rec.get("probe"), "turns": len(rec["turns"]), "focus": rec.get("focus"), "focus_name": rec.get("focus_name"),
                             "kind": "formulation-contrast"})
        json.dump(idx, open(P.data("replay", "index.json"), "w"), indent=1, ensure_ascii=False)
else:
    rec = json.load(open(path))
pa, pb = rec["patient_a"], rec["patient_b"]
hist = {"a": [], "b": []}; ds = []
for k, t in enumerate(rec["turns"]):
    for s, pid in (("a", pa), ("b", pb)):
        if f"{s}{k + 1}" in REALIGN or not (KEEP and t[s].get("alignment", {}).get("version") == "interview-align-v2"):
            t[s]["alignment"] = FC.align(pid, t["question"], t[s]["response"], hist[s])
        hist[s] += [{"who": "t", "text": t["question"]}, {"who": "p", "text": t[s]["response"]}]
    for s, pid in (("a", pa), ("b", pb)):
        FC.log_unaligned(pid, k + 1, t["question"], t[s]["response"], f"recorded:{case}", t[s]["alignment"])
    t["fc"] = FC.turn_analysis(pa, pb, t["a"], t["b"], t["question"]); ds.append(t["fc"]["d"])
gs = FC.running(ds)
for t, g in zip(rec["turns"], gs): t["fc"]["g"] = g
print("\n turn | d_t   | g_t   | method | Max gist / Natasha gist | aligned (Max / Natasha)")
for k, t in enumerate(rec["turns"]):
    f = t["fc"]; print(f"  T{k + 1}  | {f['d']:.3f} | {f['g']:.3f} | {f['method']:5s} | {f['a']['gist']} / {f['b']['gist']} | "
                       f"{[(n['type'][:4], n['words'][:25]) for n in t['a']['alignment']['nodes']]} / {[(n['type'][:4], n['words'][:25]) for n in t['b']['alignment']['nodes']]}"
          + f"\n       reading: {f.get('reading')}" + "".join(f"\n       {'BOTH ' if c['same'] else 'not  '} {c['type']}: {c['a_node']} ~ {c['b_node']} sim {c['sim']} | {c.get('label') or ''} | {c['reason']}" for c in f['checked']))
# 4. reveal with hints for every cause (the page shows a hint only for causes not reached)
ident = {s: (D.short_profiles()[D.idx_of(p)].get("identity") or {}) for s, p in (("a", pa), ("b", pb))}
rec["reveal"] = FC.reveal(pa, pb, rec.get("focus"), reached={"a": [], "b": []}, names=rec["names"], genders={s: ident[s].get("gender") or "" for s in "ab"})
print("\nreveal:", {s: [(x["short"], x.get("hint")) for x in rec["reveal"][s]] for s in "ab"})
# 5. learning point for each round length
rec["learning_by_n"] = {}
for n in range(3, len(rec["turns"]) + 1):
    lp = FC.learning_point(rec["names"], [{"question": t["question"], "d": t["fc"]["g"]} for t in rec["turns"][:n]])
    if lp.get("point"): rec["learning_by_n"][str(n)] = lp
rec.pop("learning", None)
rec["source"] = rec.get("source", "") + f" | re-aligned with interview-align-v2, gap {FC.VERSION}"
json.dump(rec, open(path, "w"), indent=1, ensure_ascii=False)
print("\nlearning points for n =", list(rec["learning_by_n"]), "|", rec["learning_by_n"].get(str(len(rec["turns"])), {}).get("point"))
