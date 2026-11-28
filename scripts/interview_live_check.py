"""Live mode on the Interview page (needs a running worker + key): type questions, wait for the reply,
its alignment and enrichment, check the page pins it and opens its themes. Screenshots light + dark.
Usage: python scripts/interview_live_check.py URL OUTDIR"""
import os, sys
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1], sys.argv[2]; os.makedirs(OUT, exist_ok=True)
QS = {1: ["Hi Emily, what has been weighing on you most this week?", "When that happens at night, what do you do?"],
      0: ["Hi Leila, how have you been sleeping lately?", "What goes through your mind when your husband comes home late?"]}
res = []
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    for idx, scheme in ((1, "light"), (0, "dark")):
        pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1, color_scheme=scheme)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL); pg.wait_for_timeout(800); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
        pg.evaluate(f"App._ext.State.selectedIdx = {idx}; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=30000)
        pg.wait_for_timeout(1000); pg.evaluate("App.goChat()"); pg.wait_for_selector("#iv-net .iv-th", timeout=20000); pg.wait_for_timeout(800)
        name = pg.evaluate("Interview.state().name")
        check(f"{name}: live composer enabled", pg.is_enabled("#iv-draft") and pg.evaluate("Interview.state().live"))
        for k, q in enumerate(QS[idx], 1):
            pg.fill("#iv-draft", q); pg.press("#iv-draft", "Enter")
            pg.wait_for_function("() => { const t = Interview.state().turns; const l = t[t.length - 1]; return l && l.who === 'p' && !l.pending; }", timeout=240000)
            pg.wait_for_timeout(3000)
            st = pg.evaluate("(() => { const s = Interview.state(), t = s.turns[s.turns.length - 1]; return { n: s.turns.length, err: t.analysisError || null, active: (t.active || []).length, spans: (t.spans || []).length, title: t.guidance && t.guidance.title, pinned: s.pinned === s.turns.length - 1 }; })()")
            opened = pg.evaluate("[...document.querySelectorAll('#iv-net .iv-th-card')].filter(r => +r.style.opacity > .5).length")
            check(f"{name} live turn {k}: reply analysed, pinned, themes open", st["err"] is None and st["pinned"] and (st["active"] == 0 or opened >= 1),
                  f"{st['active']} nodes, {st['spans']} spans, {opened} themes open, title '{st['title']}'")
        pg.screenshot(path=f"{OUT}/iv2_live_{name.lower()}_{scheme}.png"); pg.close()
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
