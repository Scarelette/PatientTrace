"""Keep one patient-model worker during demo periods (run every minute by the site watchdog on login01).
While .state/live/demo_until is in the future: if no worker job is running or pending, submit one; if the running
one has under 15 minutes left and none is pending, submit its replacement (a short overlap, so visitors never see
a gap). Outside demo periods it does nothing (workers stop after 20 idle minutes; autostart covers visitors).
Only actions are logged (stdout -> .state/logs/keepalive.log)."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PATIENTTRACE_HOME", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".state"))
from patienttrace import live as L

def secs(t: str) -> int:                 # Slurm time "D-HH:MM:SS" / "HH:MM:SS" / "MM:SS"
    if not t or t in ("INVALID", "UNLIMITED", "NOT_SET"): return 10 ** 9
    d, _, hms = t.rpartition("-"); parts = [int(x) for x in hms.split(":")]
    while len(parts) < 3: parts.insert(0, 0)
    return (int(d) if d else 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]

if L.demo_until() > time.time():
    jobs = L.worker_jobs()
    running = [j for j in jobs if j["state"] in ("RUNNING", "CONFIGURING")]
    pending = [j for j in jobs if j["state"] == "PENDING"]
    stamp = time.strftime("%F %T")
    if not jobs:
        print(stamp, "demo period: no worker job, submitting", L.submit_worker("keepalive: demo period"), flush=True)
    elif running and not pending and min(secs(j["time_left"]) for j in running) < 15 * 60:
        print(stamp, "demo period: worker", running[0]["job_id"], "has", running[0]["time_left"], "left, submitting its replacement",
              L.submit_worker("keepalive: replace before the time limit"), flush=True)
