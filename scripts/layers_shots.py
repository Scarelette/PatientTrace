"""Emily with her 5 largest themes open, then Expand all; light + dark. deviceScaleFactor 1, 1300x820.
Usage: python scripts/layers_shots.py URL OUTDIR TAG   -> OUTDIR/TAG_{light,dark}_{five,all}.png
Also prints the toolbar height (rows) and how much of the canvas the graph spans."""
import os, sys
from playwright.sync_api import sync_playwright
URL, OUT, TAG = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)
V = "App._ext.State.n3"
SPAN = """() => { const v = App._ext.State.n3, g = App._ext.State.graphs.long, c = document.querySelector('canvas.net3d-canvas').getBoundingClientRect();
  let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
  const add = p => { x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y); };
  for (let i = 0; i < v.themeCount(); i++) add(v.screenPos('theme', i));
  const st = v.state(); return { w: Math.round((x1 - x0) / c.width * 100), h: Math.round((y1 - y0) / c.height * 100), canvas: [Math.round(c.width), Math.round(c.height)] }; }"""
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    errs = []
    for scheme in ("light", "dark"):
        pg = b.new_page(viewport={"width": 1300, "height": 820}, device_scale_factor=1, color_scheme=scheme)
        pg.add_init_script("try { localStorage.setItem('pt-view', '3d'); } catch (e) {}")
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL); pg.wait_for_timeout(700); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
        pg.evaluate("App._ext.State.selectedIdx = 1; App.generateFromExisting()"); pg.wait_for_selector("canvas.net3d-canvas"); pg.wait_for_timeout(1500)
        if scheme == "light":
            tb = pg.evaluate("""() => { const a = document.querySelector('#layer-ctl').getBoundingClientRect(), b = document.querySelector('#insight-ctl').getBoundingClientRect();
                return { top: Math.round(a.top), bottom: Math.round(Math.max(a.bottom, b.bottom)), canvasTop: Math.round(document.querySelector('#graph-stage').getBoundingClientRect().top) }; }""")
            print(f"toolbar {tb['top']}..{tb['bottom']} px ({tb['bottom'] - tb['top']} px tall), canvas starts at {tb['canvasTop']} px")
            print("flat: graph spans", pg.evaluate(SPAN))
        idx = pg.evaluate("""() => { const g = App._ext.State.graphs.long, v = App._ext.State.n3;
            return g.groups.filter(x => x.members.length > 1).sort((a, b) => b.members.length - a.members.length).slice(0, 5).map(x => v.themeIndexOf(x.label)).filter(i => i >= 0); }""")
        for i in idx: pg.evaluate(f"{V}.toggle({i})"); pg.wait_for_timeout(300)
        pg.wait_for_timeout(2600); pg.mouse.move(5, 815)
        pg.screenshot(path=f"{OUT}/{TAG}_{scheme}_five.png")
        if scheme == "light": print("5 open: graph spans", pg.evaluate(SPAN), "| state", {k: v for k, v in pg.evaluate(f"{V}.state()").items() if k in ("open", "edges")})
        pg.click("#layer-ctl button >> text=Expand all"); pg.wait_for_timeout(2800)
        pg.screenshot(path=f"{OUT}/{TAG}_{scheme}_all.png")
        pg.close()
    print("JS errors:", errs or "none"); b.close()
