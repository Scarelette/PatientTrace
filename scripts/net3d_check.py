"""3D Network view: screenshots (deviceScaleFactor 1, light + dark) and behaviour checks.
Usage: python scripts/net3d_check.py URL OUTDIR [idx ...]   (defaults: 1 = Emily, 0 = Leila; perf on p096)
Screens per profile and scheme: flat start, one theme open, Expand all, hovered node, selected node,
plus (Emily, light) a trace in progress and a feedback loop."""
import os, sys
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1], sys.argv[2]
IDX = [int(x) for x in sys.argv[3:]] or [1, 0]
NAMES = {1: "emily", 0: "leila"}
os.makedirs(OUT, exist_ok=True)
res = []
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)
GL = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
V = "App._ext.State.n3"

def open_profile(pg, idx):
    pg.goto(URL); pg.wait_for_timeout(700); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
    pg.evaluate(f"App._ext.State.selectedIdx = {idx}; App.generateFromExisting()")
    pg.wait_for_selector("#graph-slot canvas.net3d-canvas", timeout=20000); pg.wait_for_timeout(1500)

def overview(pg):
    btn = pg.locator("#layer-ctl button", has_text="Overview")
    if btn.is_enabled(): btn.click(); pg.wait_for_timeout(1500)

def click_at(pg, pos): pg.mouse.move(pos["x"], pos["y"]); pg.mouse.down(); pg.mouse.up()

with sync_playwright() as p:
    b = p.chromium.launch(args=GL)
    errs = []
    for idx in ([] if os.environ.get('ONLY') == 'tail' else IDX):
        tag = NAMES.get(idx, f"p{idx:03d}")
        for scheme in ("light", "dark"):
            pg = b.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=1, color_scheme=scheme)
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
            open_profile(pg, idx)
            first = scheme == "light"
            st = pg.evaluate(f"{V}.state()")
            if first: check(f"{tag}: starts flat, nothing open", st["mode"] == "flat" and st["open"] == 0, str(st))
            pg.screenshot(path=f"{OUT}/{tag}_{scheme}_1_flat.png")
            # open the biggest theme by clicking its sphere
            ti = pg.evaluate("""() => { const g = App._ext.State.graphs.long, grps = g.groups.filter(x => x.members.length > 1);
                grps.sort((a, b) => b.members.length - a.members.length); return App._ext.State.n3.themeIndexOf(grps[0].label); }""")
            click_at(pg, pg.evaluate(f"{V}.screenPos('theme', {ti})")); pg.wait_for_timeout(1900)
            st = pg.evaluate(f"{V}.state()")
            if first: check(f"{tag}: clicking a theme opens it and tilts to 3D", st["open"] == 1 and st["mode"] == "3d", str(st))
            pg.screenshot(path=f"{OUT}/{tag}_{scheme}_2_one_open.png")
            pg.click("#layer-ctl button >> text=Expand all"); pg.wait_for_timeout(2200)
            st = pg.evaluate(f"{V}.state()"); n = pg.evaluate(f"{V}.themeCount()")
            if first: check(f"{tag}: Expand all opens every theme", st["open"] == n, f"{st['open']}/{n}, labels shown {st['labelsShown']}")
            pg.screenshot(path=f"{OUT}/{tag}_{scheme}_3_expanded.png")
            hub = pg.evaluate("""() => { const g = App._ext.State.graphs.long, d = {}; g.edges.forEach(e => { d[e.from] = (d[e.from] || 0) + 1; d[e.to] = (d[e.to] || 0) + 1; });
                return Object.keys(d).sort((a, b) => d[b] - d[a])[0]; }""")
            pos = pg.evaluate(f"{V}.screenPos('node', {hub!r})"); pg.mouse.move(pos["x"], pos["y"]); pg.wait_for_timeout(700)
            st = pg.evaluate(f"{V}.state()"); tipt = pg.inner_text("#g-tip")
            if first: check(f"{tag}: hover shows the full name in the tooltip", st["hover"] == hub and hub in tipt and pg.evaluate("document.querySelector('#g-tip').classList.contains('show')"), hub)
            pg.screenshot(path=f"{OUT}/{tag}_{scheme}_4_hover.png")
            click_at(pg, pos); pg.wait_for_timeout(900)
            st = pg.evaluate(f"{V}.state()")
            if first:
                check(f"{tag}: clicking a node selects it and opens the details panel", st["selected"] == hub and pg.is_visible("#net-panel")
                      and pg.inner_text(".np-title").strip().lower()[:20] == hub.lower()[:20], pg.inner_text(".np-counts"))
            pg.mouse.move(5, 795); pg.wait_for_timeout(500)
            pg.screenshot(path=f"{OUT}/{tag}_{scheme}_5_selected.png")
            if first:
                pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
                check(f"{tag}: Esc closes the panel and clears the selection", not pg.is_visible("#net-panel") and pg.evaluate(f"{V}.state()")["selected"] is None)
                # light -> dark toggle: labels are redrawn, nothing breaks
                pg.evaluate("App.toggleTheme()"); pg.wait_for_timeout(600)
                check(f"{tag}: theme toggle keeps the view (labels redrawn)", pg.locator("#graph-slot canvas.net3d-canvas").count() == 1 and pg.evaluate(f"{V}.state()")["open"] == n)
                pg.evaluate("App.toggleTheme()"); pg.wait_for_timeout(300)
                pg.click("#layer-ctl button >> text=Overview"); pg.wait_for_timeout(1800)
                check(f"{tag}: Overview closes all layers and returns flat", pg.evaluate(f"{V}.state()")["mode"] == "flat" and pg.evaluate(f"{V}.state()")["open"] == 0)
                # unconnected nodes: hidden by default, shown on request
                iso = pg.evaluate("App._ext.State.isoCount")
                if iso:
                    before = pg.evaluate("document.querySelectorAll('#graph-slot canvas').length")
                    pg.click("#layer-ctl button >> text=Show unconnected"); pg.wait_for_timeout(1200)
                    shown = pg.evaluate(f"{V}.state()")
                    check(f"{tag}: 'Show unconnected nodes' remounts with them ({iso})", pg.locator("#layer-ctl button", has_text="Hide unconnected").count() == 1 and before == 1
                          and pg.evaluate("document.querySelectorAll('#graph-slot canvas').length") == 1)
                    pg.click("#layer-ctl button >> text=Hide unconnected"); pg.wait_for_timeout(1000)
                # most central chip -> opens theme, glides, selects
                nm = pg.locator("#central-select option").nth(1).get_attribute("value")
                pg.select_option("#central-select", nm); pg.wait_for_timeout(1800); st = pg.evaluate(f"{V}.state()")
                check(f"{tag}: Most central dropdown opens its theme and selects the node", st["selected"] is not None and st["open"] >= 1 and pg.is_visible("#net-panel"), nm)
                pg.keyboard.press("Escape"); overview(pg)
                # feedback loops
                lb = pg.locator("#insight-ctl button", has_text="Feedback loops"); cnt = lb.inner_text() if lb.count() else pg.inner_text("#insight-ctl .no-loops")
                if lb.count():
                    lb.click(); pg.wait_for_timeout(1800); st = pg.evaluate(f"{V}.state()")
                    check(f"{tag}: feedback loop is marked in 3D", st["marked"] >= 2 and st["open"] >= 1, pg.inner_text("#net-bar .nb-cap")[:90])
                    pg.screenshot(path=f"{OUT}/{tag}_{scheme}_6_loop.png")
                    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
                else:
                    check(f"{tag}: no feedback loops -> 'No feedback loops' text", cnt == "No feedback loops", cnt)
                # trace from trigger
                overview(pg)
                trig = pg.locator("#trace-select option").nth(1).get_attribute("value")
                pg.select_option("#trace-select", trig); pg.wait_for_timeout(1500)
                st = pg.evaluate(f"{V}.state()")
                check(f"{tag}: trace in progress marks and pulses reached nodes", st["marked"] >= 2 and st["pulsing"] >= 1, f"{st['marked']} marked, {st['pulsing']} pulsing; " + pg.inner_text("#net-bar .nb-cap"))
                pg.screenshot(path=f"{OUT}/{tag}_{scheme}_7_trace.png")
                pg.keyboard.press("Escape")
            pg.close()
    # ---- clean-up: re-open patients many times; WebGL contexts must not pile up ----
    pg = b.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=1); warn = []
    pg.on("console", lambda m: warn.append(m.text)); pg.on("pageerror", lambda e: errs.append(str(e)))
    TAIL = os.environ.get("ONLY") == "tail"
    open_profile(pg, 1)
    for k in range(20):
        pg.evaluate(f"App._ext.State.selectedIdx = {[1, 0][k % 2]}; App.generateFromExisting()"); pg.wait_for_timeout(350)
    pg.wait_for_timeout(800)
    check("re-opening patients 20x: one canvas, no 'too many WebGL contexts'", pg.locator("canvas.net3d-canvas").count() == 1
          and not any("Too many active WebGL contexts" in w for w in warn), f"canvases {pg.locator('canvas.net3d-canvas').count()}")
    pg.click("#layer-ctl .seg-btn >> text=2D"); pg.wait_for_timeout(1500)
    check("2D toggle: Flow graph instead, 3D torn down", pg.locator("canvas.net3d-canvas").count() == 0 and pg.locator("#graph-slot svg.graph-svg").count() == 1)
    pg.click("#layer-ctl .seg-btn >> text=3D"); pg.wait_for_timeout(1200)
    check("3D toggle back", pg.locator("canvas.net3d-canvas").count() == 1)
    # ---- performance: largest profile, Expand all, then 3 s of steady frames ----
    open_profile(pg, 96); pg.click("#layer-ctl button >> text=Expand all"); pg.mouse.move(5, 790); pg.wait_for_timeout(5000)   # measure at rest
    pg.evaluate(f"{V}.resetStats()"); pg.wait_for_timeout(3000)
    stt = pg.evaluate(f"{V}.stats()"); n = pg.evaluate(f"{V}.state()"); js, rd = stt["js"], stt["render"]
    check("p096 (73 nodes, 149 links) all expanded: our JS per frame < 4 ms (p95)", js["p95"] < 4,
          f"JS avg {js['avg']:.2f} / p95 {js['p95']:.2f} / max {js['max']:.2f} ms; render (software GL here) avg {rd['avg']:.1f} ms; {stt['frames']} frames, {n['edges']} edges")
    pg.screenshot(path=f"{OUT}/p096_light_expanded.png")
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
