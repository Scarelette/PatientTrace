"""Expand all + a selected node, light + dark (1300x820, dsf 1). Prints what is off-screen.
Usage: python scripts/sel_shots.py URL OUTDIR TAG IDX 'node name'"""
import os, sys
from playwright.sync_api import sync_playwright
URL, OUT, TAG, IDX, NAME = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
os.makedirs(OUT, exist_ok=True)
VIS = """() => { const v = App._ext.State.n3, c = document.querySelector('canvas.net3d-canvas').getBoundingClientRect(), pnl = document.querySelector('#net-panel');
  const right = pnl && !pnl.classList.contains('hidden') ? pnl.getBoundingClientRect().left : c.right;
  const g = App._ext.State.graphs.long, names = g.nodes.map(n => n.name); let off = 0, hidden = 0, tot = 0;
  names.forEach(n => { try { const p = v.screenPos('node', n); tot++; if (p.x < c.left || p.x > c.right || p.y < c.top || p.y > c.bottom) off++; else if (p.x > right) hidden++; } catch (e) {} });
  return { nodesOffCanvas: off, nodesUnderPanel: hidden, of: tot }; }"""
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]); errs = []
    for scheme in ("light", "dark"):
        pg = b.new_page(viewport={"width": 1300, "height": 820}, device_scale_factor=1, color_scheme=scheme)
        pg.add_init_script("try { localStorage.setItem('pt-view', '3d'); } catch (e) {}"); pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL); pg.wait_for_timeout(700); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
        pg.evaluate(f"App._ext.State.selectedIdx = {IDX}; App.generateFromExisting()"); pg.wait_for_selector("canvas.net3d-canvas"); pg.wait_for_timeout(1500)
        pg.click("#layer-ctl button >> text=Expand all"); pg.wait_for_timeout(3000)
        pos = pg.evaluate(f"App._ext.State.n3.screenPos('node', {NAME!r})"); pg.mouse.move(pos["x"], pos["y"]); pg.mouse.down(); pg.mouse.up()
        pg.wait_for_timeout(2600); pg.mouse.move(5, 815); pg.wait_for_timeout(400)
        if scheme == "light":
            print("after selecting:", pg.evaluate(VIS), "| overlap", pg.evaluate("App._ext.State.n3.overlap()"),
                  "| selected", pg.evaluate("App._ext.State.n3.state().selected"))
        pg.screenshot(path=f"{OUT}/{TAG}_{scheme}_selected.png"); pg.close()
    print("JS errors:", errs or "none"); b.close()
