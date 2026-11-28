"""Details panel: node click, edge click (+ long-profile passage), Esc / empty-space close, collapse button."""
import sys
from playwright.sync_api import sync_playwright

def _np(b, **kw):            # these checks cover the 2D (Flow) view; 3D is checked by net3d_check.py
    pg = b.new_page(**kw); pg.add_init_script("try { localStorage.setItem('pt-view', '2d'); } catch (e) {}"); return pg
URL, OUT = sys.argv[1], sys.argv[2]
IDX = int(sys.argv[3]) if len(sys.argv) > 3 else 1
res = []
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""))
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    for scheme in ("light", "dark"):
        pg = _np(b, viewport={"width": 1280, "height": 800}, color_scheme=scheme); pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL); pg.wait_for_timeout(800); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
        pg.evaluate(f"App._ext.State.selectedIdx = {IDX}; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot svg"); pg.wait_for_timeout(1500)
        pg.click("#layer-ctl button >> text=Expand all"); pg.wait_for_timeout(1500)
        hub = pg.evaluate("""() => { let best = null, d = -1; document.querySelectorAll('#graph-slot .g-node:not(.grp)').forEach(g => {
            const r = +g.querySelector('circle').getAttribute('r'); if (r > d) { d = r; best = g.getAttribute('data-key'); } }); return best; }""")
        pg.locator(f'#graph-slot .g-node[data-key="{hub}"] circle').click(); pg.wait_for_timeout(500)
        if scheme == "light":
            check("node click opens panel", pg.is_visible("#net-panel"))
            check("panel shows title, counts and links", pg.locator(".np-title").count() == 1 and "caused by" in pg.inner_text(".np-counts") and pg.locator(".np-edge").count() > 0,
                  pg.inner_text(".np-counts"))
            check("node stays selected after mouse leaves", pg.locator(".g-node.sel").count() == 1 if (pg.mouse.move(5, 790) or True) else False)
            check("collapse-theme button present", pg.locator(".np-actions button").count() == 1)
        pg.screenshot(path=f"{OUT}/p3_{scheme}_node.png")
        pg.locator(".np-edge").first.click(); pg.wait_for_timeout(500)
        if scheme == "light":
            check("edge row opens edge panel with evidence", pg.locator(".np-link .np-ev").count() >= 1)
            check("long-profile passage found", pg.locator(".np-quote").count() >= 1, (pg.inner_text(".np-quote")[:80] if pg.locator(".np-quote").count() else ""))
        pg.screenshot(path=f"{OUT}/p3_{scheme}_edge.png")
        if pg.locator(".np-link button >> text=Show in long profile").count():
            pg.click(".np-link button >> text=Show in long profile"); pg.wait_for_timeout(800)
            if scheme == "light": check("long profile opens on the passage", pg.locator("#long-profile-view .lp-hit").count() == 1 and pg.is_visible("#lp-overlay"))
            pg.screenshot(path=f"{OUT}/p3_{scheme}_profile.png")
            pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
            if scheme == "light": check("Esc closes the long profile first", not pg.is_visible("#lp-overlay") and pg.is_visible("#net-panel"))
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        if scheme == "light":
            check("Esc closes the panel", not pg.is_visible("#net-panel") and pg.locator(".g-node.sel, .g-edge.sel").count() == 0)
            # click an edge directly in the graph
            hit = pg.locator("#graph-slot .g-hit").nth(3); bb = hit.bounding_box()
            pg.evaluate("""() => { const h = document.querySelectorAll('#graph-slot .g-hit')[3]; h.dispatchEvent(new MouseEvent('click', { bubbles: true })); }""")
            pg.wait_for_timeout(400); check("clicking an edge in the graph opens it", pg.is_visible("#net-panel") and pg.locator(".np-link").count() >= 1)
            box = pg.locator("#graph-slot svg.graph-svg").bounding_box(); pg.mouse.click(box["x"] + 15, box["y"] + 15); pg.wait_for_timeout(300)
            check("clicking empty space closes the panel", not pg.is_visible("#net-panel"))
            pg.locator(f'#graph-slot .g-node[data-key="{hub}"] circle').click(); pg.wait_for_timeout(400)
            n0 = pg.locator("#graph-slot .g-node").count(); pg.click(".np-actions button"); pg.wait_for_timeout(1200)
            check("collapse-theme button collapses the theme", pg.locator("#graph-slot .g-node").count() < n0 and not pg.is_visible("#net-panel"))
        pg.close()
    m = _np(b, viewport={"width": 390, "height": 844}); m.on("pageerror", lambda e: errs.append("mobile " + str(e)))
    m.goto(URL); m.wait_for_timeout(800); m.evaluate("App.goExisting()"); m.wait_for_selector(".pick-card")
    m.evaluate(f"App._ext.State.selectedIdx = {IDX}; App.generateFromExisting()"); m.wait_for_selector("#graph-slot svg"); m.wait_for_timeout(1500)
    m.evaluate("""() => { const g = document.querySelector('#graph-slot .g-node:not(.grp)') || document.querySelector('#graph-slot .g-node'); }""")
    m.click("#layer-ctl button >> text=Expand all"); m.wait_for_timeout(1500)
    m.evaluate("""() => { const g = document.querySelector('#graph-slot .g-node:not(.grp)'); const r = g.getBoundingClientRect(),
        o = { clientX: r.x + r.width/2, clientY: r.y + r.height/2, bubbles: true, pointerId: 1 }; g.dispatchEvent(new PointerEvent('pointerdown', o)); g.dispatchEvent(new PointerEvent('pointerup', o)); }""")
    m.wait_for_timeout(600); m.screenshot(path=f"{OUT}/p3_mobile.png"); check("mobile: no sideways scroll", m.evaluate("document.documentElement.scrollWidth") <= 390)
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
