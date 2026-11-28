"""Counts overlapping node labels at rest (collapsed and all expanded) and screenshots a hover state."""
import sys
from playwright.sync_api import sync_playwright

def _np(b, **kw):            # these checks cover the 2D (Flow) view; 3D is checked by net3d_check.py
    pg = b.new_page(**kw); pg.add_init_script("try { localStorage.setItem('pt-view', '2d'); } catch (e) {}"); return pg
URL, OUT = sys.argv[1], sys.argv[2]
IDX = [int(x) for x in sys.argv[3:]] or [1, 15]
OVER = """() => { const bs = [...document.querySelectorAll('#graph-slot .g-node:not(.lbl-off) text')].map(t => t.getBoundingClientRect()).filter(r => r.width);
  let n = 0; for (let i = 0; i < bs.length; i++) for (let j = i + 1; j < bs.length; j++) { const a = bs[i], b = bs[j];
  if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) n++; } return [n, bs.length]; }"""
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    for idx in IDX:
        pg = _np(b, viewport={"width": 1280, "height": 800}); pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL); pg.wait_for_timeout(800); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
        pg.evaluate(f"App._ext.State.selectedIdx = {idx}; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot svg"); pg.wait_for_timeout(1500)
        print(f"p{idx:03d} collapsed: overlapping label pairs / labels = {pg.evaluate(OVER)}")
        pg.click("#layer-ctl button >> text=Expand all"); pg.wait_for_timeout(1500)
        print(f"p{idx:03d} expanded:  overlapping label pairs / labels = {pg.evaluate(OVER)}")
        # hover the best-connected node
        key = pg.evaluate("""() => { let best = null, d = -1; document.querySelectorAll('#graph-slot .g-node').forEach(g => {
            const r = +g.querySelector('circle').getAttribute('r'); if (r > d) { d = r; best = g.getAttribute('data-key'); } }); return best; }""")
        g = pg.locator(f'#graph-slot .g-node[data-key="{key}"] circle'); g.hover(); pg.wait_for_timeout(500)
        st = pg.evaluate("""() => ({ out: document.querySelectorAll('.g-edge.e-out').length, inn: document.querySelectorAll('.g-edge.e-in').length,
            dimN: document.querySelectorAll('.g-node.dim').length, full: document.querySelectorAll('.g-node tspan').length })""")
        print(f"p{idx:03d} hover {key}: {st}")
        pg.screenshot(path=f"{OUT}/p2_p{idx:03d}_hover.png"); pg.close()
    print("JS errors:", errs or "none"); b.close()
