"""Three short screencast clips (WebM, one per page) from recorded sessions only: the network model, the Interview
page, and the Formulation Contrast (contrast interview) page. Convert / shrink with ffmpeg afterwards.
Usage: python scripts/screencast_clips.py URL OUTDIR [--dark]"""
import os, sys, time
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1].rstrip("/"), sys.argv[2]; os.makedirs(OUT, exist_ok=True)
W, H = 1280, 800
SCHEME = "dark" if "--dark" in sys.argv else "light"
CAP = """(t) => { let c = document.getElementById('sc-cap');
  if (!c) { c = document.createElement('div'); c.id = 'sc-cap';
    c.style.cssText = 'position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:9999;max-width:86%;padding:9px 16px;border-radius:12px;'
      + 'background:rgba(20,22,30,.9);color:#fff;border:1px solid rgba(255,255,255,.18);font:600 15px/1.4 system-ui,-apple-system,Segoe UI,sans-serif;'
      + 'box-shadow:0 6px 24px rgba(0,0,0,.3);text-align:center;transition:opacity .3s;pointer-events:none'; document.body.appendChild(c); }
  c.style.opacity = t ? 1 : 0; if (t) c.textContent = t; }"""
CURSOR = """() => { if (document.getElementById('sc-cur')) return; const d = document.createElement('div'); d.id = 'sc-cur';
  d.style.cssText = 'position:fixed;z-index:10000;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;background:rgba(255,90,90,.6);'
    + 'border:2px solid #fff;pointer-events:none;left:-40px;top:-40px'; document.body.appendChild(d);
  document.addEventListener('mousemove', e => { d.style.left = e.clientX + 'px'; d.style.top = e.clientY + 'px'; }, true); }"""

def cap(pg, t, hold=0):
    pg.evaluate(CAP, t); pg.evaluate(CURSOR)
    if hold: pg.wait_for_timeout(hold)
def to(pg, sel):
    box = pg.locator(sel).first.bounding_box()
    if box: pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=12); pg.wait_for_timeout(300)
def click(pg, sel, pause=700): to(pg, sel); pg.locator(sel).first.click(); pg.wait_for_timeout(pause)

def prepare(b, name):
    """Load Emily's network off camera, then start recording on a fresh page of the same session."""
    ctx = b.new_context(viewport={"width": W, "height": H}, color_scheme=SCHEME, record_video_dir=os.path.join(OUT, name), record_video_size={"width": W, "height": H})
    pg = ctx.new_page()
    pg.goto(URL + "/"); pg.wait_for_timeout(500); pg.evaluate("localStorage.clear()"); pg.reload(); pg.wait_for_timeout(800)
    pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
    # recordings show the recorded examples; the browse-mode "live chat is off" notice is not part of what they show
    pg.add_style_tag(content="#iv-off, #fc-off { display: none !important; }")
    return ctx, pg

def emily(pg):
    pg.evaluate("App._ext.State.selectedIdx = 1; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=60000)

def finish(ctx, name):
    v = ctx.pages[0].video; ctx.close(); src = v.path()
    dst = os.path.join(OUT, f"{name}.webm"); os.replace(src, dst); print(name, "->", dst, flush=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    # ---- 1. network model
    ctx, pg = prepare(b, "network")
    cap(pg, "Pick a simulated patient from the library", 1800)
    emily(pg); cap(pg, "Emily's symptom network: triggers, thoughts, feelings, body and behaviours, and how they link", 3500)
    box = pg.locator("#graph-slot").bounding_box(); cx, cy = box["x"] + box["width"] * .55, box["y"] + box["height"] * .45
    cap(pg, "The layered 3D view: drag to turn it")
    pg.mouse.move(cx, cy); pg.mouse.down(); pg.mouse.move(cx + 220, cy + 30, steps=30); pg.mouse.move(cx - 60, cy - 20, steps=30); pg.mouse.up(); pg.wait_for_timeout(1200)
    cap(pg, "Or read it as a flat network in the 2D view", 600)
    if pg.locator("button:has-text('2D')").count(): click(pg, "button:has-text('2D')", 3500)
    cap(pg, "", 300); finish(ctx, "network")
    # ---- 2. interview page
    ctx, pg = prepare(b, "interview")
    emily(pg); pg.wait_for_timeout(800)
    cap(pg, "Interview: live with the patient model, or watch a recorded example", 1200)
    click(pg, "#iv-rec-link", 1200)
    cap(pg, "Each reply opens the network themes it touches and lights the nodes she expressed", 9000)
    cap(pg, "Underlined words are matched to network nodes; loops are drawn as they appear", 8000)
    pg.evaluate("Interview.stopReplay()"); pg.wait_for_timeout(600)
    P = pg.evaluate("Interview.state().turns.map((t, i) => t.who === 'p' ? i : -1).filter(i => i >= 0)")
    cap(pg, "Click any reply: the panel explains what it shows and what sits underneath")
    pg.locator(f'#iv-log [data-i="{P[2]}"] .bubble').scroll_into_view_if_needed(); click(pg, f'#iv-log [data-i="{P[2]}"] .bubble', 5000)
    cap(pg, "", 300); finish(ctx, "interview")
    # ---- 3. contrast interview page (Formulation Contrast)
    ctx, pg = prepare(b, "contrast")
    emily(pg); pg.wait_for_timeout(600)
    cap(pg, "Formulation Contrast: pick a look-alike patient (same presenting problem, different reasons)", 600)
    click(pg, "#contrast-btn", 600); pg.wait_for_selector(".pk-card", timeout=60000); pg.wait_for_timeout(2600)
    pg.evaluate("document.getElementById('pk-back') && document.getElementById('pk-back').remove()")
    cap(pg, "A recorded round: Max and Natasha both avoid driving, for different reasons", 400)
    click(pg, "#fc-rec-link", 1200)
    click(pg, "#fc-ctl button:has-text('Play')", 300)
    cap(pg, "Each question goes to both; the gap between the tracks shows how differently they answer", 9000)
    pg.wait_for_function("!FC.state().playing", timeout=60000); pg.wait_for_timeout(800)
    cap(pg, "Reflect on the turn first, then compare with an AI reading")
    click(pg, "#fc-rows .fc-row.latest .fc-choice:has-text('Clearly different')", 900)
    click(pg, "#fc-rows .fc-row.latest .fc-readlink", 3200)
    cap(pg, "End of round: each patient's distinctive causes, found or not reached, with hints")
    click(pg, "#fc-ctl button:has-text('Finish and compare')", 4200)
    cap(pg, "", 300); finish(ctx, "contrast")
    b.close()
