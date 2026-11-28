"""Network page header (live entry groups) at 1226 and 600 px. Usage: python scripts/header_shots.py URL OUTDIR TAG"""
import sys, os
from playwright.sync_api import sync_playwright
URL, OUT, TAG = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]; os.makedirs(OUT, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    for w in (1226, 600):
        pg = b.new_page(viewport={"width": w, "height": 900 if w < 700 else 847}); pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL + "/"); pg.wait_for_timeout(800); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
        pg.evaluate("App._ext.State.selectedIdx = 0; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=30000)
        pg.wait_for_timeout(2500); pg.evaluate("App._ext.LiveStatus.poll()"); pg.wait_for_timeout(1200)
        g = lambda sel: pg.locator(sel).bounding_box()
        bi, bf, li, lf = g("#chat-btn"), g("#contrast-btn"), g("#iv-rec-link"), g("#fc-rec-link")
        cx = lambda r: r["x"] + r["width"] / 2
        if w > 700:
            print(f"{w}px {TAG}: buttons top {bi['y']:.0f}/{bf['y']:.0f}, links top {li['y']:.0f}/{lf['y']:.0f}, link centred under button: "
                  f"{abs(cx(li) - cx(bi)) < 1.5 and abs(cx(lf) - cx(bf)) < 1.5}, order:",
                  pg.evaluate("[...document.querySelectorAll('.gen-head-right > *')].filter(e => e.offsetParent && !e.classList.contains('gen-status-inline')).map(e => (e.querySelector('button') || e).textContent.trim())"))
        else:
            print(f"{w}px {TAG}: stacked: Interview link under its button {li['y'] > bi['y'] and li['y'] < bf['y']}, FC link under FC button {lf['y'] > bf['y']}, no sideways scroll {pg.evaluate('document.documentElement.scrollWidth') <= w}")
        print("   dots:", pg.evaluate("[...document.querySelectorAll('#iv-dot, #fc-dot')].map(d => d.className + ' | ' + (d.getAttribute('aria-label') || '-') + ' | ' + (d.title || '-'))"), "| button title:", pg.get_attribute("#chat-btn", "title"))
        pg.evaluate("document.querySelector('#fc-rec-link').focus()"); pg.wait_for_timeout(150)
        box = pg.locator(".gen-head").bounding_box()
        pg.screenshot(path=f"{OUT}/header_{w}_{TAG}.png", clip={"x": 0, "y": max(0, box["y"] - 8), "width": w, "height": box["height"] + 16})
        pg.close()
    print("JS errors:", errs or "none"); b.close()
