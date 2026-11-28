"""Phase 4: feedback loops (step through), most central list, trace from trigger (animated / reduced motion)."""
import sys
from playwright.sync_api import sync_playwright

def _np(b, **kw):            # these checks cover the 2D (Flow) view; 3D is checked by net3d_check.py
    pg = b.new_page(**kw); pg.add_init_script("try { localStorage.setItem('pt-view', '2d'); } catch (e) {}"); return pg
URL, OUT = sys.argv[1], sys.argv[2]
IDX = int(sys.argv[3]) if len(sys.argv) > 3 else 1
res = []
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""))
def openp(pg):
    pg.goto(URL); pg.wait_for_timeout(800); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
    pg.evaluate(f"App._ext.State.selectedIdx = {IDX}; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot svg.graph-svg"); pg.wait_for_timeout(1500)
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    pg = _np(b, viewport={"width": 1280, "height": 800}); pg.on("pageerror", lambda e: errs.append(str(e))); openp(pg)
    lb = pg.locator("#insight-ctl button", has_text="Feedback loops")
    NOLOOPS = lb.count() == 0
    check("loops: a count, or 'No feedback loops'", (lb.count() == 1) or pg.inner_text("#insight-ctl .no-loops") == "No feedback loops", lb.inner_text() if lb.count() else "No feedback loops")
    if NOLOOPS: print("(this profile has no directed cycles: loop checks skipped)")
    else: lb.click(); pg.wait_for_timeout(1500)
    if not NOLOOPS:
        import re as _re
        cap = pg.inner_text("#net-bar .nb-cap") if pg.locator("#net-bar .nb-cap").count() else ""
        nm = pg.locator("#graph-slot .g-node.mk").count(); ne = pg.locator("#graph-slot .g-edge.mk").count()
        check("loop 1 highlighted with caption", nm >= 2 and ne >= 2 and "→" in cap, f"{nm} nodes, {ne} edges: {cap}")
        check("caption starts and ends at the same node", cap.split(" → ")[0] == _re.sub(r"\s*\(\+\d+ similar\)$", "", cap.split(" → ")[-1]))
        pg.screenshot(path=f"{OUT}/p4_loop1.png")
        pg.click("#net-bar button[aria-label='Next loop']"); pg.wait_for_timeout(1200)
        cap2 = pg.inner_text("#net-bar .nb-cap"); check("next shows another loop", cap2 != cap, cap2)
        sa = pg.locator("#net-bar button", has_text="Show all")
        if sa.count(): sa.click(); pg.wait_for_timeout(1200)       # the top 8 are all 2-node loops; longer ones are under 'Show all'
        for _ in range(80):
            nb = pg.locator("#net-bar button[aria-label='Next loop']")
            if nb.is_disabled(): break
            nb.click(); pg.wait_for_timeout(200)
        pg.wait_for_timeout(1500); nm = pg.locator("#graph-slot .g-node.mk").count(); want = len(pg.inner_text("#net-bar .nb-cap").split(" → ")) - 1
        check("the last (longest) loop highlights exactly its nodes", nm == want and want >= 3, pg.inner_text("#net-bar b") + f", {nm} of {want} nodes"); pg.screenshot(path=f"{OUT}/p4_loop_long.png")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
        check("Esc stops loop mode", not pg.is_visible("#net-bar") and pg.locator("#graph-slot .g-node.mk").count() == 0)
    opts_c = pg.locator("#central-select option"); names = [opts_c.nth(i).get_attribute("value") for i in range(1, opts_c.count())]
    check("most central dropdown lists 5 nodes", len(names) == 5, " | ".join(opts_c.nth(i).inner_text() for i in range(1, opts_c.count())))
    first = names[0]; pg.select_option("#central-select", first); pg.wait_for_timeout(1500)
    check("choosing a central node selects it", pg.is_visible("#net-panel") and pg.inner_text(".np-title").strip().lower() == first.lower() and pg.locator(".g-node.sel").count() == 1, first)
    pg.keyboard.press("Escape")
    opts = pg.locator("#trace-select option"); trig = opts.nth(1).get_attribute("value")
    pg.select_option("#trace-select", trig); pg.wait_for_timeout(300); n0 = pg.locator("#graph-slot .g-node.mk").count()
    pg.screenshot(path=f"{OUT}/p4_trace_step0.png"); pg.wait_for_timeout(1300); n1 = pg.locator("#graph-slot .g-node.mk").count()
    for _ in range(30):
        if "…" not in pg.inner_text("#net-bar .nb-cap"): break
        pg.wait_for_timeout(500)
    pg.wait_for_timeout(300); n2 = pg.locator("#graph-slot .g-node.mk").count(); capt = pg.inner_text("#net-bar .nb-cap")
    check("trace grows one step at a time", n0 <= n1 <= n2 and n2 > n0, f"{n0} -> {n1} -> {n2}; {capt}")
    pg.screenshot(path=f"{OUT}/p4_trace_done.png"); pg.close()
    rm = _np(b, viewport={"width": 1280, "height": 800}, reduced_motion="reduce", color_scheme="dark"); rm.on("pageerror", lambda e: errs.append(str(e))); openp(rm)
    rm.select_option("#trace-select", trig); rm.wait_for_timeout(400)
    check("reduced motion: whole trace at once", rm.locator("#graph-slot .g-node.mk").count() == n2, f"{rm.locator('#graph-slot .g-node.mk').count()} nodes")
    rm.screenshot(path=f"{OUT}/p4_trace_dark_reduced.png"); rm.close()
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
