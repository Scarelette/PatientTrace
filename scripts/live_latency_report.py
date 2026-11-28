"""Summarise the live-mode latency log (state/logs/live_latency.jsonl) for the paper: per page and step,
count / median / p90 / max latency, patient-model tokens and GPU, cache hits and errors.
Usage: PATIENTTRACE_HOME=... python scripts/live_latency_report.py [--since EPOCH] [--json OUT]"""
import json, os, statistics, sys
from collections import defaultdict
from patienttrace import paths as P
since = float(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--since=")), 0))
out_json = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--json=")), None)
path = P.state("logs", "live_latency.jsonl")
rows = [json.loads(l) for l in open(path, encoding="utf-8")] if os.path.exists(path) else []
rows = [r for r in rows if r.get("ts", 0) >= since]
print(f"{path}: {len(rows)} entries")
groups = defaultdict(list)
for r in rows:
    groups[(r.get("page"), r.get("step"))].append(r)
pct = lambda xs, q: sorted(xs)[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]
summary = []
for (page, step), rs in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), ["patient_reply", "alignment", "guidance", "analysis", "learning"].index(kv[0][1]) if kv[0][1] in ["patient_reply", "alignment", "guidance", "analysis", "learning"] else 9)):
    ok = [r for r in rs if r.get("ok") and not r.get("cached") and r.get("ms") is not None]
    ms = [r["ms"] for r in ok]
    line = {"page": page, "step": step, "n": len(rs), "ok": len(ok), "cached": sum(1 for r in rs if r.get("cached")), "errors": [r.get("error") for r in rs if not r.get("ok")]}
    if ms:
        line.update(median_ms=round(statistics.median(ms)), p90_ms=pct(ms, .9), max_ms=max(ms))
    for k in ("first_token_ms", "gen_ms", "tokens_in", "tokens_out", "gpu_util", "gpu_mem_mb"):
        v = [r[k] for r in ok if r.get(k) is not None]
        if v:
            line[k + "_median"] = round(statistics.median(v))
    gn = {r.get("gpu_name") for r in ok if r.get("gpu_name")}
    if gn: line["gpu"] = ", ".join(sorted(map(str, gn)))
    if any(r.get("mock") for r in rs): line["mock_worker"] = True
    if any(r.get("retried") for r in rs): line["auto_retried"] = sum(1 for r in rs if r.get("retried"))
    summary.append(line)
    print(f"  {page:9s} {step:13s} n={line['n']:<3} ok={line['ok']:<3} cached={line['cached']:<2} "
          + (f"median {line['median_ms']/1000:.2f}s  p90 {line['p90_ms']/1000:.2f}s  max {line['max_ms']/1000:.2f}s" if ms else "no timings")
          + "".join(f"  {k}={line[k]}" for k in line if k.endswith("_median"))
          + (f"  gpu={line['gpu']}" if line.get("gpu") else "") + ("  [MOCK worker]" if line.get("mock_worker") else "") + (f"  auto-retried={line['auto_retried']}" if line.get("auto_retried") else "")
          + (f"  errors={line['errors']}" if line["errors"] else ""))
if out_json:
    json.dump(summary, open(out_json, "w"), indent=1)
