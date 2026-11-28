"""Interview page, aligner v1 round: trigger with edge, theme-level match, no-match + Suggest nodes, resting
view with a hovered theme, page fit. Screenshots light + dark. Usage: python scripts/interview_r5_check.py URL OUTDIR"""
import os, sys
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1], sys.argv[2]; os.makedirs(OUT, exist_ok=True)
res = []
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)
def open_iv(pg, idx):
    pg.goto(URL); pg.wait_for_timeout(700); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
    pg.evaluate(f"App._ext.State.selectedIdx = {idx}; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=20000)
    pg.wait_for_timeout(800); pg.evaluate("App.goChat()"); pg.wait_for_selector("#iv-log .iv-msg.p", timeout=15000); pg.wait_for_timeout(1500)
def ridx(pg, k): return pg.evaluate(f"Interview.state().turns.map((t, i) => t.who === 'p' ? i : -1).filter(i => i >= 0)[{k - 1}]")
def pin(pg, k): i = ridx(pg, k); pg.locator(f'#iv-log [data-i="{i}"] .bubble').scroll_into_view_if_needed(); pg.locator(f'#iv-log [data-i="{i}"] .bubble').click(); pg.mouse.move(700, 120); pg.wait_for_timeout(3200); return i
OPENT = "[...document.querySelectorAll('#iv-net .iv-th-card')].filter(r => +r.style.opacity > .5).length"
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    for scheme in ("light", "dark"):
        pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1, color_scheme=scheme)
        pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
        open_iv(pg, 0); first = scheme == "light"
        if first:
            nav = pg.locator(".topbar").bounding_box() if pg.locator(".topbar").count() else {"y": 0, "height": 80}
            top = pg.locator(".iv-top").bounding_box(); app = pg.locator(".iv-app").bounding_box()
            check("page fills the viewport under the nav; no page scroll", app["y"] + app["height"] <= 900 and abs(app["y"] + app["height"] - 888) < 30
                  and pg.evaluate("document.documentElement.scrollHeight <= innerHeight + 1 || getComputedStyle(document.documentElement).overflow === 'hidden'"),
                  f"app {app['y']:.0f}..{app['y'] + app['height']:.0f}")
            check("left column and chat scroll on their own", pg.evaluate("getComputedStyle(document.querySelector('.iv-left')).overflowY") == "auto" and pg.evaluate("getComputedStyle(document.querySelector('#iv-log')).overflowY") == "auto")
            pg.mouse.move(1000, 600); pg.mouse.wheel(0, 2000); pg.wait_for_timeout(400)
            check("scrolling the chat never slides the patient header under the nav", abs(pg.locator(".iv-top").bounding_box()["y"] - top["y"]) < 1)
        # reply 6: trigger with edge
        i6 = pin(pg, 6); t6 = pg.evaluate(f"Interview.state().turns[{i6}]")
        if first:
            trig = t6.get("trigger") or {}
            fx_from_trig = pg.evaluate(f"Interview.state().FX.filter(f => f.a === {trig.get('node')!r}).length") if trig else 0
            check("Leila reply 6: trigger node shown (its theme opens) with an edge to the reply's nodes", bool(trig) and pg.locator(f'#iv-net .iv-nd[data-n="{trig.get("node")}"]').count() == 1 and fx_from_trig >= 1,
                  f"trigger '{trig.get('phrase')}' ({trig.get('source')}), {fx_from_trig} edge(s) from it")
            chip = pg.locator("#iv-detail .iv-trig .iv-chip")
            check("trigger chip shows the full label (wraps, no ellipsis)", chip.count() == 1 and "…" not in chip.inner_text() and pg.evaluate("getComputedStyle(document.querySelector('#iv-detail .iv-trig .iv-chip')).whiteSpace") == "normal", chip.inner_text() if chip.count() else "")
        pg.screenshot(path=f"{OUT}/r5_leila6_trigger_{scheme}.png")
        # reply 7: whispers -> theme-level
        i7 = pin(pg, 7)
        if first:
            check("Leila reply 7 ('whispers'): theme-level match opens a dashed card", pg.locator("#iv-net .iv-th-card.tm").count() == 1 and "theme-level" in pg.inner_text("#iv-netcap")
                  and "theme-level match" in pg.inner_text("#iv-detail"), pg.inner_text("#iv-netcap"))
        pg.screenshot(path=f"{OUT}/r5_leila7_theme_{scheme}.png")
        # reply 8: no match -> previous view kept, dimmed, caption + Suggest nodes
        before = pg.evaluate(OPENT); i8 = pin(pg, 8)
        if first:
            check("no-match reply keeps the previous view dimmed, with a caption and 'Suggest nodes'", pg.evaluate("document.querySelector('#iv-net').classList.contains('stale')")
                  and pg.evaluate(OPENT) == before and "No network node matched this reply" in pg.inner_text("#iv-netcap") and pg.locator("#iv-netcap .iv-suggest").count() == 1)
        pg.screenshot(path=f"{OUT}/r5_leila8_nomatch_{scheme}.png")
        if first:
            pg.click("#iv-netcap .iv-suggest"); pg.wait_for_timeout(800)
            check("'Suggest nodes' with nothing plausible says so", pg.evaluate("Interview.state().turns[%d].suggestedApplied" % i8) in (None, False))
        # resting view + hovered theme
        pg.evaluate("Interview.rest()"); pg.wait_for_timeout(1500)
        op = pg.evaluate("[...document.querySelectorAll('#iv-net .iv-te')].map(p => +p.style.opacity)")
        ws = pg.evaluate("[...new Set([...document.querySelectorAll('#iv-net .iv-te')].map(p => p.getAttribute('stroke-width')))].length")
        if first: check("resting view: theme links at ~0.2, width varies with the number of links", max(op) <= 0.21 and ws >= 2, f"max opacity {max(op):.2f}, {ws} widths")
        th = pg.locator("#iv-net .iv-th").nth(pg.evaluate("Interview.state().net.colThemes[2][0]")).locator("circle").nth(2)
        bb = th.bounding_box(); pg.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2); pg.wait_for_timeout(600)
        if first: check("hovering a theme brightens only its links", pg.locator("#iv-net .iv-te.hot").count() >= 1 and pg.locator("#iv-net .iv-te.hot").count() < pg.locator("#iv-net .iv-te").count(),
                         f"{pg.locator('#iv-net .iv-te.hot').count()} of {pg.locator('#iv-net .iv-te').count()} links lit")
        pg.screenshot(path=f"{OUT}/r5_leila_resting_{scheme}.png")
        pg.close()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1); pg.on("pageerror", lambda e: errs.append(str(e)))
    open_iv(pg, 1); i7 = pin(pg, 7)
    pg.click("#iv-netcap .iv-suggest"); pg.wait_for_timeout(3000)
    st = pg.evaluate(f"Interview.state().turns[{i7}]")
    check("Emily reply 7: 'Suggest nodes' applies the lower-threshold match (marked tentative)", st.get("suggestedApplied") and (len(st.get("active") or []) + len(st.get("themes") or [])) >= 1
          and "tentative" in pg.inner_text("#iv-detail"), f"{len(st.get('active') or [])} nodes, {len(st.get('themes') or [])} themes")
    pg.screenshot(path=f"{OUT}/r5_emily7_suggested_light.png"); pg.close()
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
