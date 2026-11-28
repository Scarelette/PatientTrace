"""Live mode end to end in a browser (1226x847): a 4-turn Interview with Emily and a 4-turn Formulation
Contrast with Max vs Natasha (opened from "Find a contrast patient" -> "Compare side by side"), with
screenshots mid-turn and after the last turn, client-side step timings, and (with --mock) the robustness
paths: Stop, error + Retry, automatic retry, question never lost.
Usage: python scripts/live_check.py URL OUTDIR --state=STATE_DIR [--mock]"""
import json, os, re, sys, time
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1].rstrip("/"), sys.argv[2]; os.makedirs(OUT, exist_ok=True)
STATE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--state=")), "")
MOCK = "--mock" in sys.argv
DATA = os.environ.get("PATIENTTRACE_DATA", "data-local")
res, timings = [], {"interview": [], "fc": []}
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)
emily = [t["text"] for t in json.load(open(os.path.join(DATA, "interview", "p001.json")))["turns"] if t["who"] == "t"][:4]
fcq = [t["question"] for t in json.load(open(os.path.join(DATA, "replay", "fc.p009-p290.json")))["turns"]][:4]
LONG = 120_000                                         # ms: generous waits; the server's own timeout is 60 s

def load_patient(pg, idx):
    pg.goto(URL + "/"); pg.wait_for_timeout(800); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
    pg.evaluate(f"App._ext.State.selectedIdx = {idx}; App.generateFromExisting()")
    pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=30000); pg.wait_for_timeout(800)

with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    pg = b.new_page(viewport={"width": 1226, "height": 847}, device_scale_factor=1)
    pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    pg.goto(URL + "/"); pg.wait_for_timeout(800)
    cfg = pg.evaluate("fetch('/api/config').then(r => r.json())")
    check("server reports live mode on", cfg["ext"]["flags"].get("live") is True, str(cfg["ext"].get("live_missing")))

    # ================= Interview: Emily, 4 live turns =================
    load_patient(pg, 1); pg.evaluate("App.goChat()"); pg.wait_for_selector("#screen-interview.active"); pg.wait_for_timeout(1500)
    check("interview: input enabled, live-off note hidden, training notice visible",
          not pg.is_disabled("#iv-draft") and pg.locator("#iv-off.hidden").count() == 1 and "Simulated patients" in pg.inner_text(".iv-disc"))
    P_COUNT = "document.querySelectorAll('#iv-log .iv-msg.p:not(.iv-typing)').length"
    for k, q in enumerate(emily, 1):
        n0 = pg.evaluate("(S => S.fromRecorded ? 0 : S.turns.length)(Interview.state())")   # the first live question clears the recorded session
        pg.fill("#iv-draft", q); t0 = time.time(); pg.click("#iv-send")
        pg.wait_for_function(f"(S => !S.fromRecorded && S.turns.length >= {n0} + 2 && S.turns[S.turns.length - 1].who === 'p' && S.turns[S.turns.length - 1].text.trim().length > 0)(Interview.state())", timeout=LONG)
        t_first = time.time()
        if k == 2:
            check("send disabled and Stop shown while the reply streams", pg.locator("#iv-send.hidden").count() == 1 and pg.locator("#iv-stop:not(.hidden)").count() == 1)
            pg.wait_for_timeout(250); pg.screenshot(path=f"{OUT}/interview_mid_turn_streaming.png")
        pg.wait_for_function("!document.querySelector('#iv-send').classList.contains('hidden') && !document.querySelector('#iv-send').disabled", timeout=LONG)
        t_reply = time.time()
        if k == 2:   # the next question may go out while this reply is still being analysed
            analysing = pg.locator("#iv-analyzing:not(.hidden)").count() == 1
            check("reply done: composer free while 'Analyzing…' runs in the left panel", analysing and not pg.is_disabled("#iv-draft"))
            pg.screenshot(path=f"{OUT}/interview_mid_turn_analyzing.png")
            timings["interview"].append({"turn": k, "first_text_s": round(t_first - t0, 2), "reply_done_s": round(t_reply - t0, 2), "analysis_done_s": None, "overlapped": True})
            continue
        pg.wait_for_function("document.querySelector('#iv-analyzing').classList.contains('hidden') && ![...document.querySelectorAll('#iv-log .tag')].some(t => t.textContent.includes('analysing'))", timeout=LONG)
        t_an = time.time()
        timings["interview"].append({"turn": k, "first_text_s": round(t_first - t0, 2), "reply_done_s": round(t_reply - t0, 2), "analysis_done_s": round(t_an - t0, 2)})
    pg.wait_for_timeout(4500)                                            # network animation settles
    st = pg.evaluate("(() => { const S = Interview.state(); return { n: S.turns.length, p: S.turns.filter(t => t.who === 'p').map(t => ({ a: (t.active || []).length, g: !!(t.guidance && (t.guidance.surface || t.guidance.title)), err: t.analysisError || null })), pinned: S.pinned, sid: S.sid }; })()")
    check("4 trainee questions + 4 analysed replies, the last one pinned", st["n"] == 8 and all(not x["err"] for x in st["p"]) and st["pinned"] == 7, json.dumps(st["p"]))
    check("network animates for the pinned reply (themes open) and guidance is labelled AI-generated",
          pg.evaluate("[...document.querySelectorAll('#iv-net .iv-th-card')].filter(r => +r.style.opacity > .5).length") >= 1 and "ai-generated" in pg.inner_text("#iv-detail").lower())
    pg.screenshot(path=f"{OUT}/interview_after_last_turn.png")
    if STATE:
        f = os.path.join(STATE, "sessions", "interview", f"p001_{st['sid']}.json")
        ok = os.path.exists(f) and len(json.load(open(f))["turns"]) == 8
        check("session saved in the recorded-example format (8 turns)", ok, f)
    if MOCK:   # robustness: Stop, error + Retry; the question is never lost
        pg.fill("#iv-draft", "[hang] Can you tell me more about that?"); pg.click("#iv-send"); pg.wait_for_timeout(1500); pg.click("#iv-stop")
        pg.wait_for_selector("#iv-err:not(.hidden)", timeout=10000)
        check("Stop cancels the stream; the question goes back into the box", "Stopped" in pg.inner_text("#iv-err") and pg.input_value("#iv-draft").startswith("[hang]")
              and pg.evaluate("Interview.state().turns.length") == 8)
        pg.fill("#iv-draft", "[fail] What happens next?"); pg.click("#iv-send")
        pg.wait_for_function("!document.querySelector('#iv-err').classList.contains('hidden') && document.querySelector('#iv-err').textContent.includes('did not reply')", timeout=30000)
        check("an error after the automatic retry shows inline with Retry; question kept", pg.locator("#iv-err button").is_visible() and pg.input_value("#iv-draft").startswith("[fail]"))
        pg.fill("#iv-draft", "")

    # ================= Formulation Contrast: Max vs Natasha, 4 live turns =================
    load_patient(pg, 9)
    pg.evaluate("Ext.findContrast()"); pg.wait_for_selector(".ccard", timeout=60000); pg.wait_for_timeout(1500)
    card = pg.locator(".ccard", has_text="Natasha")
    if card.count():
        card.first.locator("button", has_text="Compare side by side").click(); how = "contrast card"
    else:
        pg.evaluate("FC.openLive('p009', 'p290', null, 'B0044')"); how = "direct (Natasha not among the cards)"
    pg.wait_for_selector("#screen-fc.active", timeout=20000); pg.wait_for_timeout(1200)
    check("FC opens live from the pair: empty trajectory, input focused", pg.evaluate("FC.state().mode") == "live" and pg.locator("#fc-svg .fc-col").count() == 0
          and pg.evaluate("document.activeElement && document.activeElement.id") == "fc-q", how)
    for k, q in enumerate(fcq, 1):
        pg.fill("#fc-q", q); t0 = time.time(); pg.click("#fc-ask")
        pg.wait_for_function("[...document.querySelectorAll('.fc-row.pending .fc-stream')].some(p => p.textContent.trim().length > 0)", timeout=LONG); t_first = time.time()
        if k == 2:
            pg.wait_for_function("[...document.querySelectorAll('.fc-row.pending .fc-stream')].every(p => p.textContent.trim().length > 0)", timeout=LONG)
            pg.wait_for_timeout(200)
            check("both answers stream side by side; Ask hidden, Stop shown", pg.locator("#fc-ask.hidden").count() == 1 and pg.locator("#fc-stop:not(.hidden)").count() == 1)
            pg.screenshot(path=f"{OUT}/fc_mid_turn_streaming.png")
        pg.wait_for_function("document.querySelector('#fc-status').textContent.includes('Placing') || !document.querySelector('.fc-row.pending')", timeout=LONG); t_both = time.time()
        pg.wait_for_function(f"document.querySelectorAll('#fc-svg .fc-col').length === {k} && !document.querySelector('.fc-row.pending') && !document.querySelector('#fc-ask').disabled", timeout=LONG); t_placed = time.time()
        timings["fc"].append({"turn": k, "first_text_s": round(t_first - t0, 2), "both_replies_s": round(t_both - t0, 2), "placed_s": round(t_placed - t0, 2)})
        pg.wait_for_timeout(1000)             # let the new turn's draw-in animation finish before the next question
    pg.wait_for_timeout(1500)
    st = pg.evaluate("FC.state()")
    check("4 turns placed: trajectory, gap, baskets, reflection row", len(st["turns"]) == 4 and all(t["fc"].get("d") is not None for t in st["turns"])
          and pg.locator("#fc-rows .fc-row.latest .fc-reflect").count() == 1, str([round(g, 3) for g in pg.evaluate("FC.gaps()")]))
    check("AI reading hidden until the trainee reflects", pg.locator("#fc-rows .fc-readlink").count() == 0)
    pg.locator('#fc-rows .fc-row.latest .fc-choice >> text=Starting to differ').click(); pg.wait_for_timeout(800)
    has_reading = bool((st["turns"][-1]["fc"].get("reading") or "").strip())
    if has_reading:
        pg.locator('#fc-rows .fc-row.latest .fc-readlink').click(); pg.wait_for_timeout(700)
    check("after reflecting: AI reading available and labelled", (not has_reading) or "AI-generated" in pg.inner_text("#fc-rows .fc-row.latest .fc-reading"), "reading present" if has_reading else "no reading (no model)")
    check("training notice visible in live FC", "Simulated patients" in pg.inner_text("#fc-live .live-note"))
    pg.mouse.move(3, 3); pg.screenshot(path=f"{OUT}/fc_after_last_turn.png")
    t0 = time.time(); pg.click("#fc-ctl button >> text=Finish and compare")
    pg.wait_for_selector("#fc-reveal:not(.hidden)", timeout=LONG); t_lp = time.time()
    lp = pg.locator("#fc-reveal .fc-learn p")
    check("Finish and compare: learning point generated live", lp.count() == 1 and len(lp.inner_text()) > 40, (lp.inner_text()[:140] if lp.count() else "none") + f" ({t_lp - t0:.1f}s)")
    timings["fc_finish_s"] = round(t_lp - t0, 2)
    pg.evaluate("document.querySelector('#fc-reveal').scrollIntoView({block: 'start'})"); pg.wait_for_timeout(500)
    pg.screenshot(path=f"{OUT}/fc_end_of_round_live.png")
    if MOCK:
        pg.click("#fc-rv-close"); pg.wait_for_timeout(400)
        pg.fill("#fc-q", "[hang] And then?"); pg.click("#fc-ask"); pg.wait_for_timeout(1500); pg.click("#fc-stop")
        pg.wait_for_selector("#fc-err:not(.hidden)", timeout=10000)
        check("FC Stop: question back in the box, no turn added", "Stopped" in pg.inner_text("#fc-err") and pg.input_value("#fc-q").startswith("[hang]") and pg.locator("#fc-svg .fc-col").count() == 4)
        pg.fill("#fc-q", "[fail] Anything else?"); pg.click("#fc-ask")
        pg.wait_for_function("!document.querySelector('#fc-err').classList.contains('hidden') && document.querySelector('#fc-err').textContent.includes('No reply')", timeout=30000)
        check("FC error after the automatic retry: inline error + Retry, question kept", pg.locator("#fc-err button").is_visible() and pg.input_value("#fc-q").startswith("[fail]"))
    if STATE:
        fs = [f for f in os.listdir(os.path.join(STATE, "sessions", "fc"))] if os.path.isdir(os.path.join(STATE, "sessions", "fc")) else []
        check("FC round saved in the recorded-example format", any(json.load(open(os.path.join(STATE, "sessions", "fc", f))).get("turns") and len(json.load(open(os.path.join(STATE, "sessions", "fc", f)))["turns"]) == 4 for f in fs), ", ".join(fs))
    print("client timings:", json.dumps(timings))
    json.dump(timings, open(os.path.join(OUT, "client_timings.json"), "w"), indent=1)
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
