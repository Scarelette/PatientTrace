"""Formulation Contrast layout sweep: every recorded FC example x light/dark x 1226x847 / 1440x900. Runs each
round to the end, reflects on the last turn, opens the AI reading, a basket and the reveal, and reports
overflow / clipping / overlapping labels. With --figures DIR, also writes the paper figures (2x) for the
default example. Usage: python scripts/fc_sweep.py URL OUTDIR [--figures docs/figures]"""
import os, sys
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1], sys.argv[2]; os.makedirs(OUT, exist_ok=True)
FIG = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--figures=")), None)
SIZES = [(1226, 847), (1440, 900)]
PROBE = """() => {
  const out = [], app = document.querySelector('#screen-fc');
  if (document.scrollingElement.scrollWidth > innerWidth + 1) out.push('page scrolls sideways');
  const SCROLLERS = new Set(['fc-hist', 'fc-baskets', 'fc-svgwrap']);
  app.querySelectorAll('*').forEach(e => {
    if (e.closest('svg') || e.closest('.hidden') || e.classList.contains('clamp') || SCROLLERS.has(e.id) || e.matches('.fc-items, .fc-items *')) return;
    const cs = getComputedStyle(e); if (cs.display === 'none' || !e.offsetParent) return;
    if ((cs.overflowX === 'hidden' || cs.overflow === 'hidden' || cs.textOverflow === 'ellipsis') && e.scrollWidth > e.clientWidth + 1)
      out.push('clipped: ' + (e.className || e.tagName) + ' "' + e.textContent.trim().slice(0, 40) + '"');
  });
  const svg = document.querySelector('#fc-svg'), W = svg.viewBox.baseVal.width, H = svg.viewBox.baseVal.height;
  const T = [...svg.querySelectorAll('text')].map(t => [t, t.getBBox()]).filter(([t, b]) => b.width);
  T.forEach(([t, b]) => { if (b.x < -1 || b.x + b.width > W + 1 || b.y < -1 || b.y + b.height > H + 1) out.push('svg text outside: "' + t.textContent.slice(0, 30) + '"'); });
  const G = T.filter(([t]) => t.classList.contains('fc-gist'));
  for (let i = 0; i < G.length; i++) for (let j = i + 1; j < G.length; j++) { const [a, A] = G[i], [b, B] = G[j];
    if (A.x < B.x + B.width - 2 && B.x < A.x + A.width - 2 && A.y < B.y + B.height - 2 && B.y < A.y + A.height - 2)
      out.push('gists overlap: "' + a.textContent.slice(0, 20) + '" / "' + b.textContent.slice(0, 20) + '"'); }
  return out; }"""
REVEAL_PROBE = """() => { const out = [], r = document.querySelector('#fc-reveal');
  if (document.scrollingElement.scrollWidth > innerWidth + 1) out.push('page scrolls sideways (reveal)');
  r.querySelectorAll('*').forEach(e => { if (e.closest('svg')) return; if (e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== 'visible') out.push('reveal clipped: ' + e.className); });
  const tl = r.querySelector('svg.fc-tl'); if (tl && tl.getBoundingClientRect().right > r.getBoundingClientRect().right + 1) out.push('timeline wider than the card');
  return out; }"""

def open_case(pg, case):
    pg.goto(URL); pg.wait_for_timeout(700); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
    pg.evaluate("App._ext.State.selectedIdx = 9; App.generateFromExisting()"); pg.wait_for_timeout(2500)
    pg.evaluate("localStorage.clear()"); pg.evaluate(f"FC.openRecorded('{case}')"); pg.wait_for_selector("#screen-fc.active"); pg.wait_for_timeout(800)

def run_round(pg):
    n = pg.evaluate("FC.state().turns.length")
    for _ in range(n): pg.click("#fc-ctl button >> text=Next"); pg.wait_for_timeout(700)
    pg.wait_for_timeout(3600)   # peek folds back
    pg.locator('#fc-rows .fc-row.latest .fc-choice >> text=Clearly different').click(); pg.wait_for_timeout(700)
    link_ok = pg.evaluate("""(() => { const l = document.querySelector('#fc-rows .fc-row.latest .fc-readlink'), h = document.querySelector('#fc-hist').getBoundingClientRect();
      if (!l) return null; const r = l.getBoundingClientRect(); return r.top >= h.top && r.bottom <= h.bottom; })()""")
    pg.locator('#fc-rows .fc-row.latest .fc-readlink').click(); pg.wait_for_timeout(800); pg.mouse.move(3, 3)
    read_ok = pg.evaluate("""(() => { const e = document.querySelector('#fc-rows .fc-row.latest .fc-reading'), h = document.querySelector('#fc-hist').getBoundingClientRect();
      if (!e) return null; const r = e.getBoundingClientRect(); return r.top >= h.top && r.bottom <= h.bottom + 1; })()""")
    return n, link_ok, read_ok

problems, results = [], []
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    cases = None
    for W, H in SIZES:
        for scheme in ("light", "dark"):
            pg = b.new_page(viewport={"width": W, "height": H}, color_scheme=scheme)
            pg.on("pageerror", lambda e: errs.append(str(e)))
            if cases is None:
                pg.goto(URL); pg.wait_for_timeout(500)
                cases = [c["case_id"] for c in pg.evaluate("fetch('/api/ext/replay').then(r => r.json())")["cases"] if c.get("kind") == "formulation-contrast"]
                print("FC examples:", cases)
            for case in cases:
                tag = f"{case}_{scheme}_{W}x{H}"
                open_case(pg, case)
                intro = pg.inner_text("#fc-intro span") if pg.locator("#fc-intro:not(.hidden)").count() else None
                n, link_ok, read_ok = run_round(pg)
                still = pg.locator("#fc-intro:not(.hidden)").count() == 1
                pr = pg.evaluate(PROBE)
                pg.screenshot(path=f"{OUT}/{tag}_t{n}_reading.png")
                pg.click(".fc-basket.pb summary"); pg.wait_for_timeout(400); pr += pg.evaluate(PROBE)
                pg.screenshot(path=f"{OUT}/{tag}_basket.png"); pg.click(".fc-basket.pb summary")
                pg.click("#fc-ctl button >> text=Finish and compare"); pg.wait_for_timeout(2500)
                pr += pg.evaluate(REVEAL_PROBE)
                pg.screenshot(path=f"{OUT}/{tag}_reveal.png", full_page=False)
                pg.click("#fc-rv-close"); pg.wait_for_timeout(300)
                if not link_ok: pr.append("AI-reading link not fully in view after reflecting")
                if not read_ok: pr.append("AI reading not fully in view after opening")
                if not intro: pr.append("intro missing")
                if not still: pr.append("intro disappeared during the round")
                results.append((tag, n, intro, sorted(set(pr))))
                print(("OK   " if not pr else "FIX  ") + tag + f"  ({n} turns) intro: {intro!r}" + ("".join("\n       - " + x for x in sorted(set(pr))) if pr else ""), flush=True)
            pg.close()
    # intro dismissal remembered per browser
    ctx = b.new_context(viewport={"width": 1226, "height": 847}); pg = ctx.new_page(); open_case(pg, cases[0])
    pg.click("#fc-intro .fc-intro-x"); pg.wait_for_timeout(200); pg.reload(); pg.wait_for_timeout(700)
    pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card"); pg.evaluate("App._ext.State.selectedIdx = 9; App.generateFromExisting()"); pg.wait_for_timeout(2500)
    pg.evaluate(f"FC.openRecorded('{cases[0]}')"); pg.wait_for_timeout(600)
    print(("OK   " if pg.locator("#fc-intro.hidden").count() == 1 else "FIX  ") + "intro stays dismissed after a reload")
    ctx.close()
    if FIG:   # paper figures: default example, 1226x847 at 2x, light and dark
        os.makedirs(FIG, exist_ok=True)
        for scheme in ("light", "dark"):
            pg = b.new_page(viewport={"width": 1226, "height": 847}, device_scale_factor=2, color_scheme=scheme, reduced_motion="reduce")   # no scroll/peek motion mid-capture
            open_case(pg, cases[0]); pg.click("#fc-intro .fc-intro-x"); run_round(pg)
            pg.screenshot(path=f"{FIG}/fc_round_t6_reading_{scheme}.png")
            pg.click(".fc-basket.pa summary"); pg.wait_for_timeout(500); pg.mouse.move(3, 3)
            pg.screenshot(path=f"{FIG}/fc_basket_expanded_{scheme}.png"); pg.click(".fc-basket.pa summary")
            pg.click("#fc-ctl button >> text=Finish and compare"); pg.wait_for_timeout(2500)
            pg.set_viewport_size({"width": 1226, "height": 1700}); pg.evaluate("document.querySelector('#fc-reveal').scrollIntoView({block: 'start', behavior: 'instant'})"); pg.wait_for_timeout(500)   # the card is taller than 847 px: same width, room for all of it
            pg.locator("#fc-reveal").screenshot(path=f"{FIG}/fc_end_of_round_{scheme}.png")   # the card alone, no sticky nav over it
            pg.close()
        print("figures:", sorted(os.listdir(FIG)))
    print("JS errors:", errs or "none")
    print(f"{sum(1 for r in results if not r[3])}/{len(results)} runs clean")
    b.close()
