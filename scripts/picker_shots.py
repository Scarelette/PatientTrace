"""Contrast picker for Leila and Emily at 1226x847 (one card's starter question expanded), light and dark.
Usage: python scripts/picker_shots.py URL OUTDIR"""
import os, re, sys
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1].rstrip("/"), sys.argv[2]; os.makedirs(OUT, exist_ok=True)
res = []
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    for scheme in ("light", "dark"):
        for idx, name in ((0, "Leila"), (1, "Emily")):
            pg = b.new_page(viewport={"width": 1226, "height": 847}, color_scheme=scheme)
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(URL + "/"); pg.wait_for_timeout(700); pg.evaluate("localStorage.clear()"); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
            pg.evaluate(f"App._ext.State.selectedIdx = {idx}; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=30000)
            pg.wait_for_timeout(800); pg.evaluate("App.goChat()"); pg.wait_for_selector("#screen-interview.active"); pg.wait_for_timeout(1500)
            pg.click("#iv-contrast"); pg.wait_for_selector(".pk-card", timeout=120000); pg.wait_for_timeout(500)
            cards = pg.locator(".pk-card")
            if scheme == "light":
                metas = pg.evaluate("[...document.querySelectorAll('.pk-meta')].map(e => e.textContent)")
                whys = pg.evaluate("[...document.querySelectorAll('.pk-why')].map(e => e.textContent)")
                names = pg.evaluate("[...document.querySelectorAll('.pk-name')].map(e => e.textContent)")
                check(f"{name}: 3 ranked cards '#n Name', meta without bare numbers", cards.count() == 3 and all(re.match(r"^#\d", c) for c in pg.evaluate("[...document.querySelectorAll('.pk-rank')].map(e => e.textContent)"))
                      and all(not re.fullmatch(r"\d+", m.split(" · ")[-1]) for m in metas), " | ".join(f"{n}: {m}" for n, m in zip(names, metas)))
                check(f"{name}: 'Both:' shows at most 2 chips then +N", all(pg.evaluate(f"document.querySelectorAll('.pk-card')[{i}].querySelectorAll('.pk-chip:not(.hidden)').length") <= 2 for i in range(3)))
                check(f"{name}: one 'Why' sentence per card, naming neither patient", len(whys) == 3 and all(name not in w and all(n not in w.split(" ", 2)[-1] for n in names) for w in whys))
                for w in whys: print("   ", w)
                check(f"{name}: question collapsed; footer note; no causes tree", pg.locator(".pk-q:not(.hidden)").count() == 0 and "causes stay hidden until you finish the round" in pg.inner_text(".pk-foot")
                      and pg.locator(".pk svg, .pk .ccard-mini").count() == 0)
            cards.nth(1).locator(".pk-qbtn").click(); pg.wait_for_timeout(300)
            pg.screenshot(path=f"{OUT}/picker_{name.lower()}_{scheme}.png")
            if cards.nth(0).locator(".pk-plus").count(): cards.nth(0).locator(".pk-plus").click(); pg.wait_for_timeout(200)
            if scheme == "light" and idx == 0:
                check("question expands; '+N' expands chips inline", pg.locator(".pk-q:not(.hidden)").count() == 1 and pg.locator(".pk-card").nth(0).locator(".pk-chip.hidden").count() == 0 and "Hide question" in cards.nth(1).locator(".pk-qbtn").inner_text())
                cards.nth(0).locator(".pk-go").click(); pg.wait_for_selector("#pk-replay")
                check("'Contrast →' leads to Start fresh / Ask … the questions", pg.locator("#pk-fresh").count() == 1 and "questions you already asked Leila" in pg.inner_text("#pk-replay"))
                pg.screenshot(path=f"{OUT}/picker_leila_options.png")
            pg.close()
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
