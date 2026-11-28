"""Re-run recorded interview sessions through the Interview page's own aligner (interview-align-v1) and
guidance-from-alignment. Replies are unchanged. Writes data/interview/pNNN.json (old file kept as
pNNN.f1.json) and prints nodes per reply before (frozen F1) vs after.
Usage: PATIENTTRACE_DATA=... python scripts/reenrich_sessions.py p001 p000"""
import json, os, shutil, sys
from patienttrace import interview as I
from patienttrace import paths as P
for pid in sys.argv[1:]:
    path = P.data("interview", f"{pid}.json"); old = P.data("interview", f"{pid}.f1.json")
    if not os.path.exists(old): shutil.copy(path, old)
    d = json.load(open(old)); T = d["turns"]; earlier = []; rows = []
    for i, t in enumerate(T):
        if t["who"] != "p": continue
        trainee = T[i - 1]["text"] if i and T[i - 1]["who"] == "t" else ""
        hist = [{"who": x["who"], "text": x["text"]} for x in T[max(0, i - 5):i - 1]]
        new = I.enrich_turn_v2(pid, trainee, t["text"], hist, earlier)
        if not new["active"] and not new["themes"]:
            sug = I.enrich_turn_v2(pid, trainee, t["text"], hist, earlier, level="low")
            new["suggested"] = sug if (sug["active"] or sug["themes"]) else {"empty": True}
        before = len(t.get("active") or [])
        T[i] = dict({"who": "p", "text": t["text"]}, **new, f1_active=t.get("active") or [])
        earlier = list(dict.fromkeys(earlier + new["active"]))
        said = sum(1 for k in new["kinds"].values() if k == "said"); imp = len(new["active"]) - said
        rows.append((len(rows) + 1, before, said, imp, len(new["themes"]), "suggested" in new and not new["suggested"].get("empty"), new["guidance"].get("title")))
    d.update(turns=T, aligner=I.ALIGN_VERSION, reenriched="interview-align-v1 + guidance-v2 from the alignment; replies unchanged")
    json.dump(d, open(path, "w"), indent=1, ensure_ascii=False)
    print(f"\n{d['name']} ({pid})  reply | before (F1) | after said + implied | theme-level | suggest pass | title")
    for r in rows: print(f"  {r[0]}  | {r[1]} | {r[2]} + {r[3]} = {r[2] + r[3]} | {r[4]} | {'yes' if r[5] else '-'} | {r[6]}")
    print(f"  total nodes: before {sum(r[1] for r in rows)}, after {sum(r[2] + r[3] for r in rows)}; replies with nothing: before {sum(1 for r in rows if r[1] == 0)}, after {sum(1 for r in rows if r[2] + r[3] + r[4] == 0)}")
