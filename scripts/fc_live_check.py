"""Formulation Contrast, live pair in the browser (needs worker + key): ask 3 questions to Max vs Natasha,
check each turn reaches the trajectory and the baskets, finish the round. Usage: python scripts/fc_live_check.py URL OUTDIR"""
import os, sys
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1], sys.argv[2]; os.makedirs(OUT, exist_ok=True)
QS = ["When you picture getting into a car, what do you expect to happen?", "What do you do to make the trip feel safer?", "What would happen if you could not do that?"]
res = []
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    for scheme in ("light", "dark"):
        pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1, color_scheme=scheme); pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL); pg.wait_for_timeout(800); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
        pg.evaluate("App._ext.State.selectedIdx = 9; App.generateFromExisting()"); pg.wait_for_timeout(2500)
        pg.evaluate(f"FC.openLive('p009', 'p290', {QS[0]!r}, 'B0044')"); pg.wait_for_selector("#screen-fc.active"); pg.wait_for_timeout(1000)
        if scheme == "light": check("live: question box shown, prefilled with the probe", pg.is_visible("#fc-q") and pg.input_value("#fc-q") == QS[0])
        for k, q in enumerate(QS, 1):
            pg.fill("#fc-q", q); pg.click("#fc-ask")
            pg.wait_for_function(f"FC.state().turns.length >= {k}", timeout=300000); pg.wait_for_timeout(1500)
            st = pg.evaluate("(() => { const s = FC.state(), t = s.turns[s.turns.length - 1]; return { d: t.fc.d_raw, m: t.fc.method, qa: t.fc.a.quote, qb: t.fc.b.quote }; })()")
            if scheme == "light": check(f"live turn {k}: both answers placed on the trajectory", pg.locator("#fc-svg .fc-col").count() == k and pg.locator("#fc-svg .fc-dot").count() == 2 * k,
                                         f"d_raw {st['d']:.2f} [{st['m']}] | {st['qa']} | {st['qb']}")
        pg.screenshot(path=f"{OUT}/fc_live_{scheme}.png")
        pg.click("#fc-ctl button >> text=Finish and compare"); pg.wait_for_timeout(15000)
        if scheme == "light": check("live finish: reveal + a fresh AI learning point", "distinctive formulation" in pg.inner_text("#fc-reveal").lower() and len(pg.inner_text("#fc-reveal .fc-learn p")) > 30,
                                     pg.inner_text("#fc-reveal .fc-learn p")[:120])
        pg.screenshot(path=f"{OUT}/fc_live_{scheme}_reveal.png", full_page=True); pg.close()
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
