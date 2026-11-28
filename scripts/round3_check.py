"""Checks for: non-overlapping open layers, canvas use, two-row toolbar, grouped feedback loops.
Usage: python scripts/round3_check.py URL [idx ...]   (default: 1 = Emily, 0 = Leila, 96 = largest)"""
import sys
from playwright.sync_api import sync_playwright
URL = sys.argv[1]
IDX = [int(x) for x in sys.argv[2:]] or [1, 0, 96]
V = "App._ext.State.n3"
res = []
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)
SPAN = """() => { const v = App._ext.State.n3, c = document.querySelector('canvas.net3d-canvas').getBoundingClientRect();
  let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
  for (let i = 0; i < v.themeCount(); i++) { const p = v.screenPos('theme', i); x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y); }
  return { w: Math.round((x1 - x0) / c.width * 100), h: Math.round((y1 - y0) / c.height * 100) }; }"""
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    errs = []
    for idx in IDX:
        pg = b.new_page(viewport={"width": 1300, "height": 820}, device_scale_factor=1)
        pg.add_init_script("try { localStorage.setItem('pt-view', '3d'); } catch (e) {}")
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL); pg.wait_for_timeout(700); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
        pg.evaluate(f"App._ext.State.selectedIdx = {idx}; App.generateFromExisting()"); pg.wait_for_selector("canvas.net3d-canvas"); pg.wait_for_timeout(1800)
        tag = f"p{idx:03d}"
        rows = pg.evaluate("""() => ['#layer-ctl', '#insight-ctl'].map(s => { const e = document.querySelector(s), kids = [...e.children].filter(k => k.offsetParent);
            const cy = kids.map(k => { const r = k.getBoundingClientRect(); return (r.top + r.bottom) / 2; }).sort((a, b) => a - b);
            return cy.reduce((n, y, i) => n + (i && y - cy[i - 1] > 12 ? 1 : 0), cy.length ? 1 : 0); })""")
        check(f"{tag}: toolbar is two rows at 1300px (one row each)", rows == [1, 1], f"rows per bar {rows}")
        sp = pg.evaluate(f"{V}.extent()")
        check(f"{tag}: flat view fills the canvas (width or height ~90%)", max(sp["w"], sp["h"]) >= 85, f"graph spans {sp['w']}% x {sp['h']}% of the canvas")
        idxs = pg.evaluate("""() => { const g = App._ext.State.graphs.long, v = App._ext.State.n3;
            return g.groups.filter(x => x.members.length > 1).sort((a, b) => b.members.length - a.members.length).slice(0, 5).map(x => v.themeIndexOf(x.label)).filter(i => i >= 0); }""")
        worst = 0
        for i in idxs:
            pg.evaluate(f"{V}.toggle({i})"); pg.wait_for_timeout(2400); o = pg.evaluate(f"{V}.overlap()"); worst = max(worst, o["pairs"])
        check(f"{tag}: opening the 5 largest themes one by one never overlaps layers", worst == 0, f"depths {pg.evaluate(f'{V}.depths()')}")
        sp5 = pg.evaluate(f"{V}.extent()")
        check(f"{tag}: 5 open: graph fills the canvas (width or height ~90%)", max(sp5["w"], sp5["h"]) >= 85, f"{sp5['w']}% x {sp5['h']}%")
        pg.click("#layer-ctl button >> text=Expand all"); pg.wait_for_timeout(2800); o = pg.evaluate(f"{V}.overlap()")
        check(f"{tag}: Expand all: no overlapping layers", o["pairs"] == 0, f"{o}, depths {pg.evaluate(f'{V}.depths()')}")
        pg.click("#layer-ctl button >> text=Overview"); pg.wait_for_timeout(2000)
        moved = pg.evaluate("() => App._ext.State.n3.debugLayout().themes.reduce((m, t) => Math.max(m, Math.hypot(t.x - t.bx, t.z - t.bz)), 0)")
        check(f"{tag}: closing everything returns themes to their original spots", moved < 0.5, f"max offset {moved:.2f}")
        lb = pg.locator("#insight-ctl button", has_text="Feedback loops"); label = lb.inner_text() if lb.count() else pg.inner_text("#insight-ctl .no-loops")
        if lb.count():
            total = pg.evaluate("App._ext.State.graphs.long._an.loops.length"); top = pg.evaluate("App._ext.State.graphs.long._an.top.length")
            check(f"{tag}: loop button reads 'N of total'", label == f"Feedback loops ({top} of {total})" and top <= 8, label)
            lb.click(); pg.wait_for_timeout(2200)
            need = pg.evaluate("""() => { const g = App._ext.State.graphs.long, c = g._an.top[0], v = App._ext.State.n3;
                return new Set(c.nodes.map(n => v.themeOf(n).label)).size; }""")
            st = pg.evaluate(f"{V}.state()")
            check(f"{tag}: loop 1 opens only the themes it needs", st["open"] == need and st["marked"] >= 2, f"open {st['open']} / needed {need}")
            pg.click("#net-bar button[aria-label='Next loop']"); pg.wait_for_timeout(2200)
            need2 = pg.evaluate("""() => { const g = App._ext.State.graphs.long, c = g._an.top[1], v = App._ext.State.n3;
                return new Set(c.nodes.map(n => v.themeOf(n).label)).size; }""")
            st = pg.evaluate(f"{V}.state()")
            check(f"{tag}: next loop closes the previous loop's themes", st["open"] == need2, f"open {st['open']} / needed {need2}")
            sa = pg.locator("#net-bar button", has_text="Show all")
            if sa.count():
                sa.click(); pg.wait_for_timeout(1500)
                check(f"{tag}: 'Show all' steps through every loop", f"of {total}" in pg.inner_text("#net-bar b"), pg.inner_text("#net-bar b"))
            pg.keyboard.press("Escape"); pg.wait_for_timeout(2000)
            check(f"{tag}: leaving loop mode closes the themes it opened", pg.evaluate(f"{V}.state()")["open"] == 0)
        else:
            check(f"{tag}: no loops -> 'No feedback loops' text", label == "No feedback loops", label)
        pg.close()
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
