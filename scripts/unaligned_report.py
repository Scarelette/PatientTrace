"""How often does the patient model answer beyond its network? Reads state/fc/unaligned.jsonl (answers that
matched no node; written by /api/ext/fc/turn and scripts/recompute_fc_example.py) and the recorded FC examples
(for the denominator). Usage: PATIENTTRACE_DATA=... PATIENTTRACE_HOME=... python scripts/unaligned_report.py"""
import glob, json, os
from collections import Counter
from patienttrace import contrast as FC, paths as P
path = P.state(*FC.UNALIGNED_LOG)
rows = [json.loads(l) for l in open(path, encoding="utf-8")] if os.path.exists(path) else []
uniq = {(r["patient"], r["question"], r["text"]): r for r in rows}   # re-runs append again: count each answer once
print(f"log: {path} | {len(rows)} lines, {len(uniq)} distinct unaligned answers")
tot = un = 0
for f in sorted(glob.glob(P.data("replay", "fc.*.json"))):
    c = json.load(open(f)); n = sum(1 for t in c["turns"] for s in "ab")
    m = sum(1 for t in c["turns"] for s in "ab" if not FC._nodes(t[s].get("alignment")))
    tot += n; un += m; print(f"  {os.path.basename(f)}: {m}/{n} answers matched no node")
if tot: print(f"recorded FC examples: {un}/{tot} = {un / tot:.0%} of answers go beyond the network")
for (pid, q, text), r in uniq.items():
    print(f"  {r['name']} ({pid}) T{r['turn']} [{r['source']}] Q: {q}\n      A: {text}")
print("by patient:", dict(Counter(r["name"] for r in uniq.values())))
