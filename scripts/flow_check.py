"""Interview -> Formulation Contrast flow, live (1226x847): Leila's empty interview, 3 live questions, the
recorded example as a separate mode, persistence, the contrast picker, option (b) "ask <B> the questions you
already asked Leila" (trajectory fills with 3 turns), and back to the interview. Screenshots of each step.
Usage: python scripts/flow_check.py URL OUTDIR"""
import os, sys, time
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1].rstrip("/"), sys.argv[2]; os.makedirs(OUT, exist_ok=True)
QS = ["Hi Leila, thanks for coming in. What brings you here today?",
      "How have you been sleeping and eating lately?",
      "When that emptiness comes, what goes through your mind?"]
res = []
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)
LONG = 120_000
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    pg = b.new_page(viewport={"width": 1226, "height": 847})
    pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    pg.goto(URL + "/"); pg.wait_for_timeout(800); pg.evaluate("localStorage.clear()")
    pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
    pg.evaluate("App._ext.State.selectedIdx = 0; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=30000); pg.wait_for_timeout(800)
    pg.evaluate("App.goChat()"); pg.wait_for_selector("#screen-interview.active"); pg.wait_for_timeout(2500)
    # 1. empty state
    check("1 empty interview: no messages, input focused, placeholder, hint line",
          pg.locator("#iv-log .iv-msg").count() == 0 and pg.evaluate("document.activeElement.id") == "iv-draft"
          and pg.get_attribute("#iv-draft", "placeholder") == "Type your question…"
          and pg.inner_text("#iv-hint") == "Start the interview: greet Leila and ask what brings them here.", pg.inner_text("#iv-hint"))
    check("1 network: themes only, nothing open, 0 surfaced; explanation hint", pg.inner_text("#iv-cov").startswith("0 of")
          and pg.evaluate("[...document.querySelectorAll('#iv-net .iv-th-card')].filter(r => +r.style.opacity > .5).length") == 0
          and "Hover or click a reply to see what's happening." in pg.inner_text("#iv-detail"), pg.inner_text("#iv-cov"))
    check("1 header: example is a secondary button, contrast button visible", pg.locator("#iv-play:not([hidden])").count() == 1 and "Replay recorded example" in pg.inner_text("#iv-play")
          and pg.locator("#iv-contrast:not(.hidden)").count() == 1 and pg.locator("#iv-exbadge.hidden").count() == 1)
    pg.screenshot(path=f"{OUT}/1_interview_empty.png")
    # 2. three live questions
    for k, q in enumerate(QS, 1):
        n0 = pg.evaluate("Interview.state().turns.length")
        pg.fill("#iv-draft", q); pg.click("#iv-send")
        pg.wait_for_function(f"(S => S.turns.length >= {n0} + 2 && S.turns[S.turns.length - 1].who === 'p' && !S.turns[S.turns.length - 1].streaming && !S.stream)(Interview.state())", timeout=LONG)
        if k == 1: check("2 hint line disappears once the interview starts", pg.locator("#iv-hint.hidden").count() == 1)
    pg.wait_for_function("document.querySelector('#iv-analyzing').classList.contains('hidden') && Interview.state().turns.every(t => !t.pending)", timeout=LONG)
    pg.wait_for_timeout(3000)
    check("2 three live turns analysed", pg.evaluate("Interview.state().turns.length") == 6 and pg.evaluate("Interview.state().turns.filter(t => t.who === 'p' && t.active).length") == 3)
    pg.screenshot(path=f"{OUT}/2_interview_three_questions.png")
    # 3. example mode, then back to the trainee's session
    pg.click("#iv-play"); pg.wait_for_timeout(2500)
    check("3 recorded example: badge, 'Exit example', example transcript", pg.locator("#iv-exbadge:not(.hidden)").count() == 1 and "Exit example" in pg.inner_text("#iv-play")
          and "Recorded example" in pg.inner_text("#iv-sub"))
    pg.screenshot(path=f"{OUT}/3_recorded_example.png")
    pg.click("#iv-play"); pg.wait_for_timeout(1200)
    check("3 Exit example restores the trainee's own 3 questions", pg.evaluate("Interview.state().turns.length") == 6 and not pg.evaluate("Interview.state().example")
          and QS[2] in pg.inner_text("#iv-log"))
    # 4. leave and come back
    pg.evaluate("App._ext.show('generate')"); pg.wait_for_timeout(600); pg.evaluate("Interview.open()"); pg.wait_for_timeout(1500)
    check("4 leaving and returning shows the trainee's conversation, not the example", pg.locator("#iv-log .iv-msg").count() == 6 and QS[0] in pg.inner_text("#iv-log"))
    # 5. picker
    pg.click("#iv-contrast"); pg.wait_for_selector(".pk-card", timeout=120000); pg.wait_for_timeout(400)
    items = pg.locator(".pk-card")
    check("5 picker: 2-3 look-alike patients with name, shared presenting problem and one line; Browse all",
          2 <= items.count() <= 3 and all("Both:" in items.nth(i).inner_text() for i in range(items.count())) and pg.locator("#pk-all").count() == 1,
          " | ".join(items.nth(i).inner_text().split("\n")[0] for i in range(items.count())))
    pg.screenshot(path=f"{OUT}/5_picker.png")
    bname = items.nth(0).locator(".pk-name").inner_text()
    items.nth(0).locator(".pk-go").click(); pg.wait_for_selector("#pk-replay"); pg.wait_for_timeout(300)
    check("5 options: start fresh / ask B the 3 questions already asked", "Start fresh" in pg.inner_text("#pk-fresh") and f"Ask {bname} the questions you already asked Leila" in pg.inner_text("#pk-replay")
          and "3 questions" in pg.inner_text("#pk-replay") and not pg.is_disabled("#pk-replay"))
    pg.screenshot(path=f"{OUT}/6_picker_options.png")
    # 6. option (b): replay
    t0 = time.time(); pg.click("#pk-replay")
    pg.wait_for_selector("#screen-fc.active", timeout=20000)
    pg.wait_for_selector("#fc-progress:not(.hidden)", timeout=30000)
    pg.wait_for_function("document.querySelectorAll('#fc-svg .fc-col').length >= 1", timeout=LONG); pg.wait_for_timeout(1200)
    check("6 replay shows progress", "Replaying your interview" in pg.inner_text("#fc-status") or pg.locator("#fc-progress:not(.hidden)").count() == 1, pg.inner_text("#fc-status"))
    pg.screenshot(path=f"{OUT}/7_fc_replaying.png")
    pg.wait_for_function("document.querySelectorAll('#fc-svg .fc-col').length === 3 && !FC.state().replaying", timeout=LONG * 2); t1 = time.time()
    pg.wait_for_timeout(2500)
    st = pg.evaluate("FC.state()")
    check("6 trajectory shows 3 turns; Leila's answers are her interview answers", len(st["turns"]) == 3 and pg.locator("#fc-svg .fc-col").count() == 3
          and all(st["turns"][i]["question"] == QS[i] for i in range(3))
          and pg.evaluate("(() => { const S = Interview.state(); const a = S.turns.filter(t => t.who === 'p').map(t => t.text); return FC.state().turns.every((t, i) => t.a.response === a[i]); })()"),
          f"{t1 - t0:.1f}s, gaps {[round(g, 3) for g in pg.evaluate('FC.gaps()')]}")
    check("6 baskets filled from the replay; input free again", pg.evaluate("[...document.querySelectorAll('.fc-basket .fc-bcount')].map(e => parseInt(e.textContent))")[0] > 0 and not pg.is_disabled("#fc-q"))
    check("6 header: '← Back to interview with Leila'", pg.locator("#fc-ctl .fc-back-iv").inner_text() == "← Back to interview with Leila")
    pg.screenshot(path=f"{OUT}/8_fc_after_replay.png")
    # 7. back to the interview
    pg.click("#fc-ctl .fc-back-iv"); pg.wait_for_selector("#screen-interview.active"); pg.wait_for_timeout(1500)
    check("7 back in Leila's interview with her 3 questions", pg.locator("#iv-log .iv-msg").count() == 6 and QS[2] in pg.inner_text("#iv-log"))
    pg.screenshot(path=f"{OUT}/9_back_to_interview.png")
    # 8. the Formulation Contrast step opens the picker (from Interview and from Network)
    pg.click('#steps [data-step="compare"]'); pg.wait_for_selector(".pk-card", timeout=30000)
    has_return = pg.locator("#pk-return").count() == 1
    pg.evaluate("document.getElementById('pk-back').remove()")
    pg.evaluate("App._ext.show('generate')"); pg.wait_for_timeout(500)
    pg.click('#steps [data-step="compare"]'); pg.wait_for_selector(".pk-card", timeout=30000)
    check("8 Formulation Contrast step opens the picker from Interview and Network; picker offers a return to the round", has_return)
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
