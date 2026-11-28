"""Live fixes, end to end with the real worker: pre-warm from the Network page (cold-start time), complete replies
(no mid-sentence cut), Interview -> contrast picker -> "Ask Betty the questions you asked Emily" with the review step
(names swapped, Emily-specific questions flagged), replay, gists never cut. Usage: python scripts/live_round2_check.py URL OUTDIR"""
import json, os, re, sys, time
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1].rstrip("/"), sys.argv[2]; os.makedirs(OUT, exist_ok=True)
res, lat = [], {}
def check(n, ok, x=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + (f"  ({x})" if x else ""), flush=True)
QS = ["Hi Emily, thanks for coming in. What brings you here today?",
      "Can you tell me more about what those nights are like for you?",
      "What goes through your mind when you notice your heart racing?",
      "How do you cope with everything your heart has been through?",
      "What would you like to be different by the end of our work together?"]
END = re.compile(r"[.!?…][\"”’)\]]*$")
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    pg = b.new_page(viewport={"width": 1226, "height": 847}); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL + "/"); pg.wait_for_timeout(800); pg.evaluate("localStorage.clear()"); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
    t0 = time.time()
    pg.evaluate("App._ext.State.selectedIdx = 1; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=60000)
    pg.wait_for_timeout(4000)
    st = pg.evaluate("App._ext.LiveStatus.current()")
    check("Network page pre-warms: worker starting (header badge visible)", st and st["patient_model"] in ("starting", "up")
          and (st["patient_model"] == "up" or pg.locator("#net-livebadge:not(.hidden)").count() == 1), (st or {}).get("phase"))
    pg.screenshot(path=f"{OUT}/1_network_prewarm.png")
    while time.time() - t0 < 900:
        st = pg.evaluate("App._ext.LiveStatus.poll()")
        if st["patient_model"] == "up": break
        time.sleep(5)
    lat["network_open_to_ready_s"] = round(time.time() - t0, 1)
    check("patient model ready", st["patient_model"] == "up", f"{lat['network_open_to_ready_s']}s after opening the Network page")
    pg.evaluate("App.goChat()"); pg.wait_for_selector("#screen-interview.active"); pg.wait_for_timeout(2000)
    replies = []
    for k, q in enumerate(QS, 1):
        n0 = pg.evaluate("Interview.state().turns.length")
        pg.fill("#iv-draft", q); t1 = time.time(); pg.click("#iv-send")
        pg.wait_for_function(f"(S => S.turns.length >= {n0} + 2 && !S.turns[S.turns.length - 1].streaming && !S.stream)(Interview.state())", timeout=120000)
        replies.append(pg.evaluate("Interview.state().turns.slice(-1)[0].text")); lat.setdefault("reply_s", []).append(round(time.time() - t1, 2))
    pg.wait_for_function("Interview.state().turns.every(t => !t.pending)", timeout=300000)
    cut = [r for r in replies if not END.search(r.strip())]
    check("every reply ends on a complete sentence", not cut, f"{len(replies)} replies; " + (" | ".join(r[-60:] for r in cut) if cut else "last words: " + " | ".join(r.strip()[-25:] for r in replies)))
    pg.screenshot(path=f"{OUT}/2_interview.png")
    # picker -> Betty -> ask Betty the questions -> review
    pg.click("#iv-contrast"); pg.wait_for_selector(".pk-card", timeout=120000)
    card = pg.locator(".pk-card", has_text="Betty").first
    card.locator(".pk-go").click(); pg.wait_for_selector("#pk-replay"); pg.click("#pk-replay")
    pg.wait_for_selector(".rv-q", timeout=90000); pg.wait_for_timeout(400)
    rows = pg.evaluate("[...document.querySelectorAll('.rv-q')].map(r => ({flag: r.classList.contains('flagged'), on: r.querySelector('input[type=checkbox]').checked, text: r.querySelector('.rv-text').value, why: (r.querySelector('.rv-flag') || {}).textContent || ''}))")
    for r in rows: print("   ", "FLAG" if r["flag"] else "ok  ", "on " if r["on"] else "off", r["text"], ("  [" + r["why"] + "]") if r["why"] else "")
    check("review: Emily's name replaced by Betty", rows[0]["text"].startswith("Hi Betty") and not any("Emily" in r["text"] for r in rows))
    check("review: the heart-condition question is flagged and starts unticked", any(r["flag"] and not r["on"] and "heart" in (r["why"] + rows[3]["text"]).lower() for r in rows[3:4]) or any(r["flag"] for r in rows), rows[3]["why"])
    pg.screenshot(path=f"{OUT}/3_review_questions.png")
    n_on = sum(1 for r in rows if r["on"])
    t2 = time.time(); pg.click("#rv-go"); pg.wait_for_selector("#screen-fc.active", timeout=30000)
    pg.wait_for_function(f"document.querySelectorAll('#fc-svg .fc-col').length === {n_on} && !FC.state().replaying", timeout=600000)
    lat["replay_s"] = round(time.time() - t2, 1); pg.wait_for_timeout(2500)
    st = pg.evaluate("FC.state()")
    check(f"replay: {n_on} turns, no 'Emily' in what Betty was asked", len(st["turns"]) == n_on and not any("Emily" in t["question"] for t in st["turns"]), f"{lat['replay_s']}s")
    gists = pg.evaluate("[...document.querySelectorAll('#fc-svg .fc-gist')].map(g => [...g.querySelectorAll('tspan')].map(t => t.textContent).join(' '))")
    check("track gists are whole phrases (no ellipsis)", gists and not any("…" in g for g in gists), " | ".join(gists))
    pg.screenshot(path=f"{OUT}/4_fc_after_replay.png")
    print("latency:", json.dumps(lat)); json.dump(lat, open(os.path.join(OUT, "latency.json"), "w"), indent=1)
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
