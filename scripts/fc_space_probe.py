"""Probe: how d_t behaves for the FC example under different node texts and calibrations (stored alignments,
no re-alignment). Prints per-turn similarity matrices so the 'Both said' threshold can be chosen.
Usage: PATIENTTRACE_DATA=... python scripts/fc_space_probe.py fc.p009-p290"""
import json, random, sys
import numpy as np
from patienttrace import contrast as FC, paths as P
from patienttrace.angel_ext import data as D
from patienttrace.angel_ext.demo_focus import contrast_graph
TW = FC.TYPE_WORD
def txt(space, pid, nid):
    n = {x["id"]: x for x in D.load_network(pid)["nodes"]}[nid]; a = FC.annotations(pid).get(nid) or {}
    t = TW.get(n["type"], n["type"])
    return {"func": f"{t}: {a.get('function') or n['label']}", "label": f"{t}: {n['label']}",
            "both": f"{t}: {n['label']}. {a.get('function') or ''}"}[space]
PAIRS = [("p009", "p290"), ("p001", "p179"), ("p010", "p189")]
rec = json.load(open(P.data("replay", sys.argv[1] + ".json"))); pa, pb = rec["patient_a"], rec["patient_b"]
for space in ("func", "label", "both"):
    same, rand = [], []
    for a, b in PAIRS:
        for c in contrast_graph(a, b)["concepts"]:
            same += [(txt(space, a, x["id"]), txt(space, b, y["id"])) for x in c["nodes"]["a"] for y in c["nodes"]["b"]]
        A = [n["id"] for n in D.load_network(a)["nodes"]]; B = [n["id"] for n in D.load_network(b)["nodes"]]
        random.seed(7); rand += [(txt(space, a, random.choice(A)), txt(space, b, random.choice(B))) for _ in range(80)]
    def cos(pairs):
        V = FC._embed([p[0] for p in pairs] + [p[1] for p in pairs]); n = len(pairs); return np.sum(V[:n] * V[n:], axis=1)
    cs, cr = cos(same), cos(rand)
    print(f"\n===== space {space}: random p10/50/90 {np.percentile(cr,[10,50,90]).round(3)} | same p25/50/75 {np.percentile(cs,[25,50,75]).round(3)}")
    for lo_name, lo in (("rand-med", float(np.median(cr))), ("rand-p10", float(np.percentile(cr, 10)))):
        hi = float(np.median(cs)); ds = []
        for t in rec["turns"]:
            An, Bn = FC._nodes(t["a"]["alignment"]), FC._nodes(t["b"]["alignment"])
            if An and Bn:
                V = FC._embed([txt(space, pa, n["node"]) for n in An] + [txt(space, pb, n["node"]) for n in Bn]); S = V[:len(An)] @ V[len(An):].T
                adj = np.array([[S[i, j] + (FC.SAME_TYPE if An[i]["type"] == Bn[j]["type"] else FC.OTHER_TYPE) for j in range(len(Bn))] for i in range(len(An))])
                C = np.clip((adj - lo) / (hi - lo), 0, 1); d = float((np.mean(1 - C.max(1)) + np.mean(1 - C.max(0))) / 2)
                if lo_name == "rand-p10" and space != "label":
                    print(f"  T{len(ds)+1} raw cos:", [[f"{An[i]['label'][:22]}|{Bn[j]['label'][:22]}:{S[i,j]:.2f}/C{C[i,j]:.2f}" for j in range(len(Bn))] for i in range(len(An))])
            else:
                V = FC._embed([t["a"]["response"], t["b"]["response"]]); d = float(1 - np.clip((float(V[0] @ V[1]) - lo) / (hi - lo), 0, 1))
            ds.append(round(d, 3))
        print(f"  lo={lo_name} ({lo:.3f}) hi={hi:.3f}: d_t {ds} | g_t {FC.running(ds)}")
