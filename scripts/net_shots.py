"""Network-view screenshots for review: collapsed + all expanded, light + dark, for given profiles.
Usage: python scripts/net_shots.py URL OUTDIR TAG [idx ...]   (defaults: 1 = Emily, 15 = small)"""
import os, sys
from playwright.sync_api import sync_playwright

def _np(b, **kw):            # these checks cover the 2D (Flow) view; 3D is checked by net3d_check.py
    pg = b.new_page(**kw); pg.add_init_script("try { localStorage.setItem('pt-view', '2d'); } catch (e) {}"); return pg
URL, OUT, TAG = sys.argv[1], sys.argv[2], sys.argv[3]
IDX = [int(x) for x in sys.argv[4:]] or [1, 15]
os.makedirs(OUT, exist_ok=True)
errs = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in ("light", "dark"):
        pg = _np(b, viewport={"width": 1280, "height": 800}, color_scheme=scheme)
        pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
        for idx in IDX:
            pg.goto(URL); pg.wait_for_timeout(800)
            pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card", timeout=15000)
            pg.evaluate(f"App._ext.State.selectedIdx = {idx}; App.generateFromExisting()")
            pg.wait_for_selector("#graph-slot svg", timeout=20000); pg.wait_for_timeout(2500)
            pg.screenshot(path=f"{OUT}/{TAG}_p{idx:03d}_{scheme}_collapsed.png")
            btn = pg.locator("#layer-ctl button", has_text="Expand all")
            if btn.count(): btn.first.click(); pg.wait_for_timeout(2500)
            pg.screenshot(path=f"{OUT}/{TAG}_p{idx:03d}_{scheme}_expanded.png")
        pg.close()
    m = _np(b, viewport={"width": 390, "height": 844})
    m.on("pageerror", lambda e: errs.append("mobile: " + str(e)))
    m.goto(URL); m.wait_for_timeout(800); m.evaluate("App.goExisting()"); m.wait_for_selector(".pick-card", timeout=15000)
    m.evaluate(f"App._ext.State.selectedIdx = {IDX[0]}; App.generateFromExisting()"); m.wait_for_selector("#graph-slot svg", timeout=20000); m.wait_for_timeout(2500)
    m.screenshot(path=f"{OUT}/{TAG}_mobile.png")
    sw = m.evaluate("document.documentElement.scrollWidth"); print("mobile scrollWidth", sw)
    b.close()
print("JS errors:", errs or "none")
