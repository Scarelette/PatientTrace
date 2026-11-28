"""Record a scripted interview through the running app (live mode) and save it for the Interview page.
Usage: python scripts/record_interview.py URL IDX OUT.json   (questions from scripts/interview_scripts.json)
Per turn: /api/chat/send (patient model) -> /api/ext/align (F1) -> /api/ext/interview/turn (spans, path,
trigger, eased, AI guidance)."""
import datetime, json, os, sys, time
import requests
URL, IDX, OUT = sys.argv[1].rstrip("/"), int(sys.argv[2]), sys.argv[3]
QS = json.load(open(os.path.join(os.path.dirname(__file__), "interview_scripts.json")))[str(IDX)]
S = requests.Session(); S.trust_env = False
def post(path, body, timeout=300):
    r = S.post(URL + path, json=body, timeout=timeout); r.raise_for_status(); return r.json()
pat = S.get(f"{URL}/api/ext/patient/{IDX}", timeout=60).json()
pid, name = pat["patient_id"], (pat["profile"].get("identity") or {}).get("name")
sid = post("/api/chat/start", {"profile": pat["profile"], "long_profile": pat.get("long_profile")})["session_id"]
turns, earlier, t0 = [], [], time.time()
for k, q in enumerate(QS, 1):
    turns.append({"who": "t", "text": q})
    a = time.time(); reply = post("/api/chat/send", {"session_id": sid, "message": q})["reply"]; t_chat = time.time() - a
    al = None
    for attempt in range(4):
        a = time.time(); al = post("/api/ext/align", {"session_id": sid, "patient_id": pid, "turn": k})
        if al.get("status") != "unavailable": break
        print(f"  align retry ({al.get('reason')})", flush=True); time.sleep(3)
    t_al = time.time() - a
    a = time.time(); en = post("/api/ext/interview/turn", {"patient_id": pid, "trainee": q, "reply": reply, "alignment": al, "earlier": earlier}); t_g = time.time() - a
    turns.append(dict({"who": "p", "text": reply}, **en))
    earlier = list(dict.fromkeys(earlier + en["active"]))
    print(f"turn {k}: reply {t_chat:.1f}s, align {t_al:.1f}s, guidance {t_g:.1f}s | {len(en['active'])} nodes, {len(en['spans'])} spans, "
          f"{len(en['path'])} links, trigger={bool(en['trigger'])}, eased={len(en['eased'])} | {en['guidance'].get('title') or en['guidance'].get('error')}", flush=True)
rec = {"patient_id": pid, "index": IDX, "name": name, "kind": "recorded interview",
       "recorded": datetime.datetime.now().isoformat(timespec="minutes"),
       "source": "scripted trainee questions (scripts/interview_scripts.json); patient replies from the deployed patient model; "
                 "alignment by the frozen F1 aligner; explanations AI-generated (patienttrace/interview.py)",
       "turns": turns}
os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
json.dump(rec, open(OUT, "w"), indent=1, ensure_ascii=False)
print(f"saved {OUT} ({len(turns)} messages, {time.time() - t0:.0f}s)")
