"""The README's guided path in browse mode (no GPU, no API key), as a reviewer would click it:
Emily -> Network -> Interview "Watch a recorded example" -> Formulation Contrast "Watch a recorded example"
(Play, reflect, AI reading, Finish) -> the contrast picker. Usage: python scripts/browse_path_check.py URL OUTDIR"""
import os, sys
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1].rstrip("/"), sys.argv[2]; os.makedirs(OUT, exist_ok=True)
res = []
def check(n, ok, x=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + (f"  ({x})" if x else ""), flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    pg = b.new_page(viewport={"width": 1280, "height": 800}); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL + "/"); pg.wait_for_timeout(1000); pg.evaluate("localStorage.clear()")
    pg.locator("[onclick='App.goExisting()']").first.click(); pg.wait_for_selector(".pick-card")
    pg.locator(".pick-card", has_text="Emily").first.click(); pg.wait_for_timeout(300)
    pg.click("#existing-generate")
    pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=60000); pg.wait_for_timeout(2500)
    check("Network page: Interview + Formulation Contrast, each with 'Watch a recorded example'", pg.locator("#iv-rec-link:not(.hidden)").count() == 1
          and pg.locator("#fc-rec-link:not(.hidden)").count() == 1 and pg.inner_text("#contrast-btn") == "Formulation Contrast", pg.inner_text("#chat-btn"))
    pg.screenshot(path=f"{OUT}/1_network.png")
    pg.click("#iv-rec-link"); pg.wait_for_selector("#screen-interview.active"); pg.wait_for_timeout(6000)
    check("recorded Interview example plays (badge, replies arrive, network themes open)", pg.locator("#iv-exbadge:not(.hidden)").count() == 1
          and pg.locator("#iv-log .iv-msg.p").count() >= 1 and pg.evaluate("[...document.querySelectorAll('#iv-net .iv-th-card')].filter(r => +r.style.opacity > .5).length") >= 1)
    check("browse mode says live is off and why", "Live chat is off" in (pg.get_attribute("#iv-draft", "placeholder") or "") or pg.locator("#iv-off:not(.hidden)").count() == 1)
    pg.screenshot(path=f"{OUT}/2_interview_example.png")
    pg.click('#steps [data-step="network"]'); pg.wait_for_timeout(1200)
    pg.click("#fc-rec-link"); pg.wait_for_selector("#screen-fc.active"); pg.wait_for_timeout(1000)
    pg.click("#fc-ctl button:has-text('Play')"); pg.wait_for_function("!FC.state().playing && FC.state().shown === FC.state().turns.length", timeout=90000); pg.wait_for_timeout(1500)
    pg.locator("#fc-rows .fc-row.latest .fc-choice:has-text('Clearly different')").click(); pg.wait_for_timeout(600)
    pg.locator("#fc-rows .fc-row.latest .fc-readlink").click(); pg.wait_for_timeout(800)
    check("recorded Formulation Contrast round: 6 turns, reflection, AI reading", pg.locator("#fc-svg .fc-col").count() == 6 and "AI-generated" in pg.inner_text("#fc-rows .fc-row.latest .fc-reading"))
    pg.screenshot(path=f"{OUT}/3_fc_round.png")
    pg.click("#fc-ctl button:has-text('Finish and compare')"); pg.wait_for_selector("#fc-reveal:not(.hidden)", timeout=30000); pg.wait_for_timeout(1500)
    check("end of round: distinctive causes, hints, learning point", "distinctive formulation" in pg.inner_text("#fc-reveal").lower() and pg.locator("#fc-reveal .fc-learn").count() == 1)
    pg.screenshot(path=f"{OUT}/4_reveal.png")
    pg.click("#fc-rv-close"); pg.wait_for_timeout(400); pg.click("#fc-ctl button:has-text('Try it yourself')"); pg.wait_for_selector(".pk-card", timeout=60000); pg.wait_for_timeout(500)
    whys = pg.evaluate("[...document.querySelectorAll('.pk-why')].map(e => e.textContent)")
    check("contrast picker opens with ranked cards and a 'why' line each", pg.locator(".pk-card").count() >= 2 and len(whys) == pg.locator(".pk-card").count(), whys[0][:90] if whys else "")
    pg.screenshot(path=f"{OUT}/5_picker.png")
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
