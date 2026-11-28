"""Expanding one theme (Flow layout) must move only nodes in that theme's column; Expand all must not
reshuffle: every theme still collapsed keeps its spot. Prints the nodes that moved."""
import sys
from playwright.sync_api import sync_playwright

def _np(b, **kw):            # these checks cover the 2D (Flow) view; 3D is checked by net3d_check.py
    pg = b.new_page(**kw); pg.add_init_script("try { localStorage.setItem('pt-view', '2d'); } catch (e) {}"); return pg
URL, IDX = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1
with sync_playwright() as p:
    b = p.chromium.launch(); pg = _np(b, viewport={"width": 1280, "height": 800}); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL); pg.wait_for_timeout(800); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
    pg.evaluate(f"App._ext.State.selectedIdx = {IDX}; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot svg"); pg.wait_for_timeout(1500)
    snap = lambda: pg.evaluate("""() => { const S = App._ext.State, P = S.pos[S.activeTab + ':flow'], o = {};
        document.querySelectorAll('#graph-slot .g-node').forEach(g => { const k = g.getAttribute('data-key'); if (P[k]) o[k] = [Math.round(P[k].x), Math.round(P[k].y)]; }); return o; }""")
    before = snap()
    themes = [k for k in before if k.startswith("g:")]
    worst = None
    for k in themes:
        x = before[k][0]
        pg.evaluate("""k => { const g = document.querySelector(`#graph-slot .g-node[data-key="${k}"]`);
            const r = g.getBoundingClientRect(), o = { clientX: r.x + r.width/2, clientY: r.y + r.height/2, bubbles: true, pointerId: 1 };
            g.dispatchEvent(new PointerEvent('pointerdown', o)); g.dispatchEvent(new PointerEvent('pointerup', o)); }""", k)
        pg.wait_for_timeout(900); after = snap()
        other = [n for n in before if n != k and n in after and before[n][0] != x and before[n] != after[n]]
        same = [n for n in before if n != k and n in after and before[n][0] == x and before[n] != after[n]]
        print(f"expand {k:6s}: other-column nodes moved {len(other)}, same-column nodes shifted {len(same)}")
        if other: worst = (k, other)
        pg.evaluate("App._ext.State.expanded[App._ext.State.activeTab].clear()"); pg.evaluate("App.showGraph(App._ext.State.activeTab)"); pg.wait_for_timeout(900)
    print("JS errors:", errs or "none"); b.close()
