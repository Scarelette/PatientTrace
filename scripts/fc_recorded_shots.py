"""Formulation Contrast recorded mode at 1226x847: T0 and T6 screenshots, badge, Try-it-yourself, controls on the
title row, latest reflection row visible. Usage: python scripts/fc_recorded_shots.py URL OUTDIR"""
import sys, os
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1].rstrip("/"), sys.argv[2]; os.makedirs(OUT, exist_ok=True)
res = []
def check(n, ok, x=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + (f"  ({x})" if x else ""), flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    pg = b.new_page(viewport={"width": 1226, "height": 847}); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL + "/"); pg.wait_for_timeout(700); pg.evaluate("localStorage.clear()"); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
    pg.evaluate("App._ext.State.selectedIdx = 0; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=30000); pg.wait_for_timeout(1500)
    pg.click("#fc-rec-link"); pg.wait_for_selector("#screen-fc.active"); pg.wait_for_timeout(1200)
    t, c = pg.locator(".fc-row1 h1").bounding_box(), pg.locator("#fc-ctl").bounding_box()
    check("recorded badge next to the title", pg.locator("#fc-recbadge:not(.hidden)").count() == 1 and pg.inner_text("#fc-recbadge") == "Recorded example")
    check("'Try it yourself →' beside Back/Play/Next; controls on the title row", "Try it yourself →" in pg.inner_text("#fc-ctl") and abs((c["y"] + c["height"] / 2) - (t["y"] + t["height"] / 2)) < 12,
          f"title centre {t['y'] + t['height'] / 2:.0f}, controls centre {c['y'] + c['height'] / 2:.0f}")
    check("bottom bar 'This is a recorded example. Try it yourself →'", pg.inner_text("#fc-recbar").replace("\n", " ") == "This is a recorded example. Try it yourself →")
    pg.screenshot(path=f"{OUT}/fc_recorded_T0.png")
    for _ in range(6): pg.click("#fc-ctl button >> text=Next"); pg.wait_for_timeout(900)
    pg.wait_for_timeout(3800)
    vis = pg.evaluate("""(() => { const h = document.querySelector('#fc-hist').getBoundingClientRect(), r = document.querySelector('#fc-rows .fc-row.latest .fc-reflect').getBoundingClientRect(),
      head = document.querySelector('#fc-hist h2').getBoundingClientRect().bottom, foot = document.querySelector('#fc-recbar').getBoundingClientRect().top; return r.top >= head && r.bottom <= foot; })()""")
    check("T6: the latest turn's reflection row fully visible above the bottom bar", vis)
    pg.screenshot(path=f"{OUT}/fc_recorded_T6.png")
    pg.click("#fc-try2"); pg.wait_for_selector(".pk-card", timeout=60000)
    check("'Try it yourself →' opens the contrast picker (live) for the current patient", "Contrast Leila" in pg.inner_text(".pk-h"))
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
