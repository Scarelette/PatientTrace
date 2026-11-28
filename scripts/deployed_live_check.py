"""Live mode on the deployed site (default https://eval.angel-simulation.org/patient-trace): worker stopped ->
unavailable + recorded fallback; worker started -> switches to live without a reload; a 3-turn live interview;
then autostart: opening a live page with no worker starts one ("Starting the patient model…"), and a question asked
meanwhile is answered once it is ready. Reports warm-up and per-turn latency; screenshots at 1226x847.
Usage: python scripts/deployed_live_check.py OUTDIR [URL]"""
import json, os, re, subprocess, sys, time
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]; URL = (sys.argv[2] if len(sys.argv) > 2 else "https://eval.angel-simulation.org/patient-trace").rstrip("/")
os.makedirs(OUT, exist_ok=True)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LM = os.path.join(REPO, "scripts", "live_mode.sh")
res, lat = [], {"turns": []}
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)
def lm(*a): return subprocess.run([LM, *a], capture_output=True, text=True).stdout.strip()
def badge(pg): return pg.inner_text("#iv-livebadge") if pg.locator("#iv-livebadge:not(.hidden)").count() else ""
QS = ["Hi Leila, thanks for coming in. What brings you here today?", "How have you been sleeping and eating lately?", "When that emptiness comes, what goes through your mind?"]

def open_leila(pg, via_network=True):
    pg.goto(URL + "/", timeout=60000); pg.wait_for_timeout(1000); pg.evaluate("localStorage.clear()")
    pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card", timeout=30000)
    pg.evaluate("App._ext.State.selectedIdx = 0; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=60000); pg.wait_for_timeout(1500)

with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    pg = b.new_page(viewport={"width": 1226, "height": 847})
    pg.on("pageerror", lambda e: errs.append(str(e)))
    lm("autostart", "off"); lm("stop"); time.sleep(5)
    # ---- 1. worker stopped: unavailable + recorded fallback
    open_leila(pg)
    check("Network page: 'Interview' (primary) + 'Watch a recorded example'; 'Formulation Contrast' + its recorded link",
          pg.inner_text("#chat-btn") == "Interview" and pg.locator("#iv-rec-link:not(.hidden)").count() == 1
          and pg.inner_text("#contrast-btn") == "Formulation Contrast" and pg.locator("#fc-rec-link:not(.hidden)").count() == 1)
    pg.screenshot(path=f"{OUT}/1_network_entries.png")
    pg.click("#chat-btn"); pg.wait_for_selector("#screen-interview.active"); pg.wait_for_timeout(3000)
    off = pg.inner_text("#iv-off")
    check("worker down: badge 'Live interview unavailable right now', plain reason, no sbatch command shown",
          badge(pg) == "Live interview unavailable right now" and "sbatch" not in pg.inner_text("#screen-interview") and "not running" in off, off)
    check("worker down: input disabled with the message as tooltip; 'Watch a recorded example' offered",
          pg.is_disabled("#iv-draft") and "Live interview unavailable right now" in (pg.get_attribute("#iv-draft", "title") or "")
          and pg.locator("#iv-off-ex").count() == 1)
    pub = pg.evaluate("fetch('api/live-status').then(r => r.json())")
    check("public /api/live-status has no admin block", "admin" not in pub, json.dumps(pub)[:160])
    pg.screenshot(path=f"{OUT}/2_unavailable.png")
    # ---- 2. start the worker: the open page switches to live by itself
    t0 = time.time(); out = lm("start"); jid = re.search(r"(\d+)", out).group(1); print("worker job", jid, flush=True)
    seen_start = None; t_up = None
    while time.time() - t0 < 900:
        bt = badge(pg)
        if seen_start is None and bt.startswith(("Warming up", "Starting")):
            seen_start = round(time.time() - t0, 1); pg.screenshot(path=f"{OUT}/3_warming_up.png")
            check("while starting: 'Warming up the patient model…' (or 'Starting…'), input enabled", not pg.is_disabled("#iv-draft"), bt)
        if bt == "Live · ready": t_up = round(time.time() - t0, 1); break
        time.sleep(2)
    lat["sbatch_to_starting_badge_s"], lat["sbatch_to_ready_s"] = seen_start, t_up
    check("switches to live without a reload: 'Live · ready', input enabled", t_up is not None and not pg.is_disabled("#iv-draft"), f"{t_up}s after sbatch")
    # ---- 3. three live turns
    for k, q in enumerate(QS, 1):
        n0 = pg.evaluate("Interview.state().turns.length")
        pg.fill("#iv-draft", q); t0 = time.time(); pg.click("#iv-send")
        pg.wait_for_function(f"(S => S.turns.length >= {n0} + 2 && S.turns[S.turns.length - 1].who === 'p' && S.turns[S.turns.length - 1].text.length > 0)(Interview.state())", timeout=120000)
        t_first = time.time() - t0
        pg.wait_for_function(f"(S => S.turns.length >= {n0} + 2 && !S.turns[S.turns.length - 1].streaming && !S.stream)(Interview.state())", timeout=120000)
        t_reply = time.time() - t0
        pg.wait_for_function(f"(S => !S.turns[{n0} + 1].pending)(Interview.state())", timeout=150000)
        t_an = time.time() - t0
        ok = not pg.evaluate(f"Interview.state().turns[{n0} + 1].analysisError")
        lat["turns"].append({"turn": k, "first_text_s": round(t_first, 2), "reply_s": round(t_reply, 2), "reply_plus_analysis_s": round(t_an, 2), "analysis_ok": ok})
    check("3 live turns, each analysed", all(t["analysis_ok"] for t in lat["turns"]) and pg.evaluate("Interview.state().turns.length") == 6, json.dumps(lat["turns"]))
    pg.wait_for_timeout(3000); pg.screenshot(path=f"{OUT}/4_three_turns.png")
    # ---- 4. autostart: a visitor opens a live page with no worker; their first question waits and is answered
    lm("stop"); time.sleep(20); lm("autostart", "on")
    pg2 = b.new_page(viewport={"width": 1226, "height": 847}); pg2.on("pageerror", lambda e: errs.append(str(e)))
    open_leila(pg2); t0 = time.time(); pg2.click("#chat-btn"); pg2.wait_for_selector("#screen-interview.active"); pg2.wait_for_timeout(3000)
    bt = pg2.inner_text("#iv-livebadge")
    check("autostart: opening the live page starts the patient model ('Starting the patient model…')", bt.startswith(("Starting", "Warming up")) and not pg2.is_disabled("#iv-draft"), bt)
    pg2.fill("#iv-draft", QS[0]); tq = time.time(); pg2.click("#iv-send"); pg2.wait_for_timeout(1500)
    check("question asked while starting is queued ('Waiting for the patient model to start…')", "Waiting for the patient model" in pg2.inner_text("#iv-log"))
    pg2.screenshot(path=f"{OUT}/5_autostart_queued.png")
    pg2.wait_for_function("(S => S.turns.length >= 2 && S.turns[1].who === 'p' && !S.turns[1].streaming && !S.stream)(Interview.state())", timeout=900000)
    lat["autostart_open_to_answer_s"] = round(time.time() - t0, 1); lat["autostart_question_to_answer_s"] = round(time.time() - tq, 1)
    check("queued question answered once the model is ready", pg2.evaluate("Interview.state().turns[1].text").strip() != "", f"{lat['autostart_open_to_answer_s']}s after opening the page")
    pg2.wait_for_timeout(2500); pg2.screenshot(path=f"{OUT}/6_autostart_answered.png")
    print("latency:", json.dumps(lat)); json.dump(lat, open(os.path.join(OUT, "latency.json"), "w"), indent=1)
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
