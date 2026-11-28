"""Record a demo screencast (WebM) from the recorded sessions only: library -> Emily's network -> her recorded
interview replaying -> contrast picker -> the recorded Max vs Natasha Formulation Contrast round (reflection,
AI reading, baskets) -> end-of-round reveal. A caption bar explains each step. Convert to MP4 with ffmpeg.
Usage: python scripts/screencast.py URL OUTDIR [--width 1280 --height 800]"""
import os, sys, time
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1].rstrip("/"), sys.argv[2]; os.makedirs(OUT, exist_ok=True)
W, H = 1280, 800
CAP = """(t) => { let c = document.getElementById('sc-cap');
  if (!c) { c = document.createElement('div'); c.id = 'sc-cap';
    c.style.cssText = 'position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:9999;max-width:88%;padding:10px 18px;border-radius:12px;'
      + 'background:rgba(20,22,30,.86);color:#fff;font:600 16px/1.4 system-ui,-apple-system,Segoe UI,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.25);'
      + 'text-align:center;transition:opacity .35s;pointer-events:none'; document.body.appendChild(c); }
  c.style.opacity = t ? 1 : 0; if (t) c.textContent = t; }"""
CURSOR = """() => { if (document.getElementById('sc-cur')) return; const d = document.createElement('div'); d.id = 'sc-cur';
  d.style.cssText = 'position:fixed;z-index:10000;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;background:rgba(255,80,80,.55);'
    + 'border:2px solid #fff;pointer-events:none;transition:left .35s ease,top .35s ease;left:-40px;top:-40px'; document.body.appendChild(d);
  document.addEventListener('mousemove', e => { d.style.left = e.clientX + 'px'; d.style.top = e.clientY + 'px'; }, true); }"""

def cap(pg, text, hold=0):
    pg.evaluate(CAP, text); pg.evaluate(CURSOR)
    if hold: pg.wait_for_timeout(hold)

def click(pg, sel, pause=700):          # move the visible cursor to the element, then click
    box = pg.locator(sel).first.bounding_box()
    if box: pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=12); pg.wait_for_timeout(350)
    pg.locator(sel).first.click(); pg.wait_for_timeout(pause)

def hover(pg, sel, pause=1200):
    box = pg.locator(sel).first.bounding_box()
    if box: pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + min(30, box["height"] / 2), steps=14)
    pg.wait_for_timeout(pause)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": W, "height": H}, record_video_dir=OUT, record_video_size={"width": W, "height": H}, color_scheme="light")
    pg = ctx.new_page(); t0 = time.time()
    pg.goto(URL + "/"); pg.wait_for_timeout(600); pg.evaluate("localStorage.clear()"); pg.reload(); pg.wait_for_timeout(1500)
    cap(pg, "PatientTrace: interview simulated patients and see what each answer reveals about their symptom network", 4200)
    pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card"); pg.wait_for_timeout(600)
    cap(pg, "Pick a patient from the library (330 simulated patients built from published case reports)", 3800)
    pg.evaluate("App._ext.State.selectedIdx = 1; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=60000)
    cap(pg, "Emily's symptom network: triggers, thoughts, feelings, body sensations and behaviours, and how they link", 5500)
    hover(pg, "#graph-slot", 2500)
    cap(pg, "Interview her live, or watch a recorded example", 1500)
    hover(pg, "#iv-rec-link", 1200); click(pg, "#iv-rec-link", 1500)
    cap(pg, "Recorded interview: each reply opens the network themes it touches and lights the nodes she expressed", 9000)
    cap(pg, "Underlined words are matched to network nodes; the panel explains what the reply shows and what sits underneath", 9000)
    cap(pg, "Loops in the network (a worry feeding a behaviour that feeds the worry) are drawn as they appear", 9000)
    pg.wait_for_function("!Interview.state().playing", timeout=120000); pg.wait_for_timeout(800)
    P = pg.evaluate("Interview.state().turns.map((t, i) => t.who === 'p' ? i : -1).filter(i => i >= 0)")
    cap(pg, "Hover or click any reply to see its part of the network again")
    for i in (P[2], P[5]):
        pg.locator(f'#iv-log [data-i="{i}"] .bubble').scroll_into_view_if_needed(); hover(pg, f'#iv-log [data-i="{i}"] .bubble', 2600)
    click(pg, f'#iv-log [data-i="{P[2]}"] .bubble', 2500)
    cap(pg, "Formulation Contrast: compare Emily with a look-alike patient", 1200)
    click(pg, "#iv-contrast", 2500); pg.wait_for_selector(".pk-card", timeout=60000)
    cap(pg, "Ranked look-alike patients: same presenting problem, different reasons behind it (causes stay hidden)", 3500)
    click(pg, ".pk-card:nth-child(2) .pk-qbtn", 3000)
    pg.evaluate("document.getElementById('pk-back') && document.getElementById('pk-back').remove()"); pg.wait_for_timeout(400)
    cap(pg, "Here is a recorded round: Max and Natasha both avoid driving, for different reasons", 1500)
    pg.evaluate("FC.openRecorded()"); pg.wait_for_selector("#screen-fc.active"); pg.wait_for_timeout(1500)
    click(pg, "#fc-ctl button:has-text('Play')", 400)
    cap(pg, "Every question goes to both patients; the gap between the tracks shows how differently they have been answering", 8000)
    cap(pg, "Short labels give the gist of each answer; baskets collect what each patient revealed, by part of the formulation", 9000)
    pg.wait_for_function("!FC.state().playing", timeout=60000); pg.wait_for_timeout(1500)
    cap(pg, "The trainee reflects on each turn first…")
    click(pg, "#fc-rows .fc-row.latest .fc-choice:has-text('Clearly different')", 1200)
    cap(pg, "…then can compare with an AI reading of the two mechanisms (no scores, no right answers)")
    click(pg, "#fc-rows .fc-row.latest .fc-readlink", 4200)
    cap(pg, "Baskets: one item per network node, with the patient's own words")
    click(pg, ".fc-basket.pa summary", 3500); click(pg, ".fc-basket.pa summary", 600)
    cap(pg, "Finish the round to see each patient's distinctive causes", 1200)
    click(pg, "#fc-ctl button:has-text('Finish and compare')", 2200)
    cap(pg, "Causes the questions reached are marked found; for the others, a hint at the kind of question that would reach them", 6000)
    pg.mouse.wheel(0, 500); pg.wait_for_timeout(1200)
    cap(pg, "Reflections sit next to the gap, with an AI-generated learning point about which kinds of question separated them", 6500)
    cap(pg, "PatientTrace: simulated patients for clinical-skills training. Not real people, not a clinical tool.", 4000)
    secs = time.time() - t0; ctx.close(); b.close()
    vids = sorted(f for f in os.listdir(OUT) if f.endswith(".webm"))
    print(f"recorded {secs:.0f}s -> {os.path.join(OUT, vids[-1]) if vids else 'no video'}")
