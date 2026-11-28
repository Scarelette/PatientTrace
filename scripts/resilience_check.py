"""Live-mode resilience, end to end with the real GPU worker (Tillicum): start the worker, ask Leila a question,
stop the worker, ask again (the app must stay responsive and show an inline error + Retry with the question kept),
restart the worker, and Retry. Reports latency per step and saves screenshots (1226x847).
Usage: python scripts/resilience_check.py URL OUTDIR"""
import json, os, re, subprocess, sys, time
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1].rstrip("/"), sys.argv[2]; os.makedirs(OUT, exist_ok=True)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
res, lat = [], {}
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)
def sh(*a): return subprocess.run(list(a), capture_output=True, text=True).stdout.strip()

def start_worker():
    out = sh("sbatch", os.path.join(REPO, "scripts", "live_worker.slurm"))
    jid = re.search(r"(\d+)", out).group(1); print("worker job", jid, flush=True); return jid

def status(pg):
    return pg.evaluate("fetch('/api/live-status').then(r => r.json())")

def wait_status(pg, want, limit):
    t = time.time()
    while time.time() - t < limit:
        st = pg.evaluate("App._ext.LiveStatus.poll().then(() => App._ext.LiveStatus.current())")
        if st and st.get("patient_model") == want: return time.time() - t
        time.sleep(5)
    return None

def health_ms(pg):     # from INSIDE the page: the browser's own connections must still be free
    return pg.evaluate("(async () => { const t = performance.now(); try { await Promise.race([fetch('/api/health?x=' + Math.random()), new Promise((_, r) => setTimeout(() => r(0), 8000))]); return Math.round(performance.now() - t); } catch (e) { return -1; } })()")

with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    pg = b.new_page(viewport={"width": 1226, "height": 847})
    pg.on("pageerror", lambda e: errs.append(str(e)))
    jid = start_worker()
    pg.goto(URL + "/"); pg.wait_for_timeout(800); pg.evaluate("localStorage.clear()")
    t = wait_status(pg, "up", 900); lat["worker_start_to_up_s"] = round(t, 1) if t else None
    check("worker comes up (status 'up')", t is not None, f"{t:.0f}s after sbatch" if t else "timeout")
    pg.reload(); pg.wait_for_timeout(800)
    pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
    pg.evaluate("App._ext.State.selectedIdx = 0; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=30000); pg.wait_for_timeout(800)
    pg.evaluate("App.goChat()"); pg.wait_for_selector("#screen-interview.active"); pg.wait_for_timeout(2500)
    check("header badge: 'Live · ready'", pg.inner_text("#iv-livebadge") == "Live · ready", pg.inner_text("#iv-livebadge"))
    pg.screenshot(path=f"{OUT}/1_live_ready.png")
    # one good turn
    q1 = "Hi Leila, thanks for coming in. What brings you here today?"
    pg.fill("#iv-draft", q1); t0 = time.time(); pg.click("#iv-send")
    pg.wait_for_function("(S => S.turns.length === 2 && !S.turns[1].streaming && !S.stream)(Interview.state())", timeout=120000); lat["reply_s"] = round(time.time() - t0, 2)
    pg.wait_for_function("(S => !S.turns[1].pending)(Interview.state())", timeout=150000); lat["reply_plus_analysis_s"] = round(time.time() - t0, 2)
    check("turn 1: reply + analysis", not pg.evaluate("Interview.state().turns[1].analysisError"), json.dumps(lat))
    # stop the worker
    sh("scancel", jid); t0 = time.time()
    while sh("squeue", "-h", "-j", jid): time.sleep(3)
    lat["scancel_to_gone_s"] = round(time.time() - t0, 1)
    q2 = "How have you been sleeping lately?"
    pg.fill("#iv-draft", q2); t0 = time.time(); pg.click("#iv-send")
    pg.wait_for_selector("#iv-err:not(.hidden)", timeout=90000); lat["worker_down_error_s"] = round(time.time() - t0, 2)
    err = pg.inner_text("#iv-err span")
    check("worker down: inline error fast, question kept, input enabled, Retry shown", lat["worker_down_error_s"] < 15 and pg.input_value("#iv-draft") == q2
          and not pg.is_disabled("#iv-draft") and pg.locator("#iv-err button").is_visible(), f"{lat['worker_down_error_s']}s: {err}")
    hm = [health_ms(pg) for _ in range(3)]
    check("app stays responsive: /api/health from the page", all(0 <= h < 1000 for h in hm), f"{hm} ms")
    pg.wait_for_timeout(16000)                                    # status cache expires, poller refreshes the badge
    pg.evaluate("App._ext.LiveStatus.poll()"); pg.wait_for_timeout(800)
    check("badge: 'Patient model unavailable'; recorded example offered", pg.inner_text("#iv-livebadge") == "Patient model unavailable"
          and pg.locator("#iv-off-ex, #iv-play.suggest").count() >= 1, pg.inner_text("#iv-livebadge"))
    pg.screenshot(path=f"{OUT}/2_worker_down_error.png")
    pg.click("#iv-err button"); pg.wait_for_timeout(4000)
    check("Retry while still down: error again, question still kept", pg.locator("#iv-err:not(.hidden)").count() == 1 and pg.input_value("#iv-draft") == q2)
    # restart the worker, Retry
    jid2 = start_worker()
    t = None; t0 = time.time()
    while time.time() - t0 < 900:
        st = pg.evaluate("App._ext.LiveStatus.poll().then(() => App._ext.LiveStatus.current())")
        if st["patient_model"] == "cold" and "warming" not in lat:
            lat["warming"] = True; pg.wait_for_timeout(500); pg.screenshot(path=f"{OUT}/3_warming_up.png")
            check("badge while loading: 'Warming up…' / 'Waiting for a GPU…'", pg.inner_text("#iv-livebadge") in ("Warming up…", "Waiting for a GPU…"), pg.inner_text("#iv-livebadge"))
        if st["patient_model"] == "up": t = time.time() - t0; break
        time.sleep(5)
    lat["worker_restart_to_up_s"] = round(t, 1) if t else None
    pg.wait_for_timeout(1000)
    check("badge back to 'Live · ready'", pg.inner_text("#iv-livebadge") == "Live · ready")
    t0 = time.time(); pg.click("#iv-err button")
    pg.wait_for_function("(S => S.turns.length === 4 && !S.turns[3].streaming && !S.stream)(Interview.state())", timeout=120000); lat["retry_reply_s"] = round(time.time() - t0, 2)
    pg.wait_for_function("(S => !S.turns[3].pending)(Interview.state())", timeout=150000); lat["retry_reply_plus_analysis_s"] = round(time.time() - t0, 2)
    check("Retry after restart: reply + analysis, question 2 in the transcript", pg.evaluate("Interview.state().turns[2].text") == q2 and not pg.evaluate("Interview.state().turns[3].analysisError")
          and pg.locator("#iv-err.hidden").count() == 1)
    pg.wait_for_timeout(2500); pg.screenshot(path=f"{OUT}/4_after_retry.png")
    print("latency:", json.dumps(lat))
    json.dump(lat, open(os.path.join(OUT, "latency.json"), "w"), indent=1)
    sh("scancel", jid2); print("worker", jid2, "cancelled")
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
