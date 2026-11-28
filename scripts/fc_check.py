"""Formulation Contrast (review round 3: conversation history) at 1226x847: screenshots (light + dark) after T2,
after T6 (history full) and hovering T3's row; plus behaviour checks. Usage: python scripts/fc_check.py URL OUTDIR"""
import os, re, sys
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1], sys.argv[2]; os.makedirs(OUT, exist_ok=True)
W, H = 1226, 847
res = []
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)
def open_fc(pg):
    pg.goto(URL); pg.wait_for_timeout(700); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
    pg.evaluate("App._ext.State.selectedIdx = 9; App.generateFromExisting()"); pg.wait_for_timeout(2500)
    pg.evaluate("localStorage.clear()"); pg.evaluate("FC.openRecorded()"); pg.wait_for_selector("#screen-fc.active"); pg.wait_for_timeout(800)
    pg.evaluate("window.scrollTo(0, 0)")
def nxt(pg, n=1, wait=900):
    for _ in range(n): pg.click("#fc-ctl button >> text=Next"); pg.wait_for_timeout(wait)
def in_view(pg, sel):
    return pg.evaluate(f"(() => {{ const r = document.querySelector('{sel}').getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }})()")
def in_hist(pg, k):
    return pg.evaluate(f"""(() => {{ const h = document.querySelector('#fc-hist').getBoundingClientRect(), r = document.querySelector('#fc-rows .fc-row[data-k="{k}"]').getBoundingClientRect();
      return r.top >= h.top - 1 && r.top < h.bottom - 30; }})()""")
def full_in_hist(pg, sel):
    return pg.evaluate(f"""(() => {{ const h = document.querySelector('#fc-hist').getBoundingClientRect(), r = document.querySelector('{sel}').getBoundingClientRect(),
      head = document.querySelector('#fc-hist h2').getBoundingClientRect().bottom,
      foot = [...document.querySelectorAll('#fc-live, #fc-recbar')].filter(e => !e.classList.contains('hidden')).reduce((n, e) => n + e.offsetHeight, 0);
      return r.top >= head - 1 && r.bottom <= h.bottom - foot + 1 && h.bottom <= innerHeight; }})()""")
GISTS = "[...document.querySelectorAll('#fc-svg .fc-gist')].map(g => [...g.querySelectorAll('tspan')].map(t => t.textContent).join(' '))"
NOGRADE = re.compile(r"\+1|\bcorrect\b(?!”)|\bscore|★|discriminating|well done|great question", re.I)
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    for scheme in ("light", "dark"):
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1, color_scheme=scheme)
        pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
        open_fc(pg); first = scheme == "light"
        if first:
            check("single-turn panel removed; history area present", pg.locator("#fc-turn").count() == 0 and pg.locator("#fc-hist #fc-rows").count() == 1)
        nxt(pg, 1, 1300)
        if first:
            st = pg.evaluate("FC.state()"); both1 = st["turns"][0]["fc"]["both"]
            allb = [x for t in st["turns"] for x in t["fc"]["both"]]
            check("'Both said' only pairs of one node type, each with a function reason", all(x["type"] and "/" not in x["type"] and x.get("reason") for x in allb),
                  "; ".join(f"T{k+1} {x['type']}: {x['short_a']} ~ {x['short_b']} ({x['reason']})" for k, t in enumerate(st["turns"]) for x in t["fc"]["both"]) or "none")
            g1 = pg.evaluate("FC.gaps()[0]")
            check("T1 opens only partly (g_1 < 0.35)", g1 < 0.35, f"d_1 {st['turns'][0]['fc']['d']:.3f}, g_1 {g1:.3f}")
        nxt(pg, 1, 1400)
        if first:
            rows = pg.locator("#fc-rows .fc-row")
            check("after T2: two rows, oldest first, latest expanded + highlighted with reflection open", rows.count() == 2 and "T1" in rows.nth(0).inner_text()
                  and "latest" in rows.nth(1).get_attribute("class") and rows.nth(1).locator(".fc-reflect").count() == 1 and rows.nth(0).locator(".fc-reflect").count() == 0)
            check("earlier turn compact: answers clamped to 2 lines, '+ reflect' chip", pg.locator('#fc-rows .fc-row[data-k="0"] .fc-ans p.clamp').count() == 2
                  and pg.inner_text('#fc-rows .fc-row[data-k="0"] .fc-chip').strip() == "+ reflect")
            check("new turn slid in (enter animation on the latest row)", "enter" in rows.nth(1).get_attribute("class"))
        pg.screenshot(path=f"{OUT}/fc_{scheme}_t2.png")
        nxt(pg, 4, 1200); pg.wait_for_timeout(3500)
        if first:
            st = pg.evaluate("FC.state()"); gs = pg.evaluate("FC.gaps()")
            print("per-turn d_t / g_t:", [(round(t["fc"]["d"], 3), round(g, 3)) for t, g in zip(st["turns"], gs)])
            check("gists are complete phrases (no ellipsis, 2-7 words) or 'no matching node'", all("…" not in g and 2 <= len(g.split()) <= 7 for g in pg.evaluate(GISTS)), " | ".join(pg.evaluate(GISTS)))
            st6 = st["turns"]
            un = pg.evaluate("[...document.querySelectorAll('#fc-svg .fc-gist.none')].map(g => [g.textContent, g.querySelector('title').textContent])")
            check("unaligned answers: muted italic gist (not 'no matching node'), hollow dot, tooltip 'not in this patient's network'",
                  len(un) == sum(1 for t in st6 for s in "ab" if not t[s]["alignment"]["nodes"]) == pg.locator("#fc-svg .fc-dot.hollow").count() and len(un) > 0
                  and all("no matching node" not in g and "Not in this patient's network" in ttl for g, ttl in un), " | ".join(g for g, _ in un))
            cov = pg.evaluate("[...document.querySelectorAll('#fc-cover .fc-cov')].map(r => [...r.querySelectorAll('.fc-part')].map(p => [p.textContent, p.classList.contains('on'), p.title]))")
            want = [{n["type"] for t in st6 for n in t[s]["alignment"]["nodes"]} for s in "ab"]
            W5 = {"Trigger": "Stimulus", "Thought": "Cognition", "Feeling": "Emotion", "Body": "Physiological Sensation", "Behaviour": "Behavior"}
            check("coverage strip: one row per patient, five parts, filled = aligned types, tooltip 'asked about at T…', no percentages",
                  len(cov) == 2 and all(len(r) == 5 for r in cov) and all({W5[x[0]] for x in r if x[1]} == want[i] for i, r in enumerate(cov))
                  and all(("asked about at T" in x[2]) == x[1] for r in cov for x in r) and "%" not in pg.inner_text("#fc-cover"),
                  " / ".join(",".join(x[0] for x in r if x[1]) for r in cov))
            dots = pg.evaluate("[...document.querySelectorAll('.fc-basket')].map(d => d.querySelectorAll('summary .fc-tdots i').length)")
            col = [len({x["ntype"] for t in st6 for x in t["fc"][s]["items"]}) for s in "ab"]
            check("collapsed baskets show up to 5 type dots (one per collected type)", dots[0] == min(5, col[0]) and dots[1] == min(5, col[1]) and max(dots) <= 5, str(dots))
            check("AI reading hidden before any reflection", pg.locator("#fc-rows .fc-readlink").count() == 0)
            check("T6: Max's self-blame rumination and Natasha's intrusive memory are not 'Both said'", not st6[5]["fc"]["both"],
                  "; ".join(f"{c['type']} {c['a_node']}~{c['b_node']}: {'same' if c['same'] else 'different'} ({c['reason']})" for c in st6[5]["fc"].get("checked", [])) or "no same-type candidate")
            check("page itself does not scroll; trajectory + baskets stay on screen", pg.evaluate("document.scrollingElement.scrollHeight <= innerHeight + 2") and in_view(pg, ".fc-traj") and in_view(pg, ".fc-bask"),
                  f"{pg.evaluate('document.scrollingElement.scrollHeight')} vs {H}")
            check("history scrolls inside its own area and shows the latest turn", pg.evaluate("(h => h.scrollHeight > h.clientHeight)(document.querySelector('#fc-hist'))") and in_hist(pg, 5))
            check("latest turn fully visible incl. its reflection row", full_in_hist(pg, '#fc-rows .fc-row.latest'))
            prev_seen = pg.evaluate("""(() => { const h = document.querySelector('#fc-hist').getBoundingClientRect(), r = document.querySelector('#fc-rows .fc-row[data-k="4"]').getBoundingClientRect(); return r.bottom > h.top + 40; })()""")
            check("at 1226x847: the latest turn fully visible (above the bottom bar), the previous one partly", full_in_hist(pg, '#fc-rows .fc-row.latest') and prev_seen,
                  pg.evaluate("(() => [document.querySelector('#fc-hist').clientHeight, ...[...document.querySelectorAll('#fc-rows .fc-row')].slice(-2).map(r => r.offsetHeight)])()"))
            pg.evaluate("""(() => { const h = document.querySelector('#fc-hist'), r = document.querySelector('#fc-rows .fc-row[data-k="4"]'); h.scrollTop = r.offsetTop - h.querySelector('h2').offsetHeight - 6; })()"""); pg.wait_for_timeout(300)
            pg.locator('#fc-rows .fc-row[data-k="4"] .fc-chip').click(); pg.wait_for_timeout(300)
            pg.locator('#fc-rows .fc-row[data-k="4"] .fc-choice >> text=Starting to differ').click(); pg.wait_for_timeout(300)
            check("chip reopens the reflection inline; choice shows as '◐ Starting to differ'", pg.inner_text('#fc-rows .fc-row[data-k="4"] .fc-chip').strip() == "Starting to differ" and pg.locator('#fc-rows .fc-row[data-k="4"] .fc-chip svg .mk-fill').count() == 1
                  and pg.locator("#fc-svg .fc-refmark").count() == 1)
            check("reflecting reveals the 'Compare with an AI reading' link for that turn only", pg.locator("#fc-rows .fc-readlink").count() == 1
                  and pg.locator('#fc-rows .fc-row[data-k="4"] .fc-readlink').count() == 1)
            pg.locator('#fc-rows .fc-row[data-k="4"] .fc-chip').click(); pg.wait_for_timeout(200)   # fold T5's controls back
            pg.evaluate("(h => h.scrollTop = 0)(document.querySelector('#fc-hist'))"); pg.wait_for_timeout(200)
            pg.locator('#fc-svg .fc-refmark[data-k="4"]').click(); pg.wait_for_timeout(900)
            check("clicking a reflection marker jumps to that row's reflection (opened)", in_hist(pg, 4) and pg.locator('#fc-rows .fc-row[data-k="4"] .fc-reflect').count() == 1)
            pg.locator("#fc-svg .fc-col .fc-hit").nth(0).click(position={"x": 12, "y": 40}); pg.wait_for_timeout(900)
            check("clicking a column scrolls the history to its row and flashes it", in_hist(pg, 0) and "flash" in pg.get_attribute('#fc-rows .fc-row[data-k="0"]', "class"))
            page = pg.evaluate("(() => { const c = document.querySelector('#screen-fc').cloneNode(true); c.querySelectorAll('.fc-note').forEach(n => n.remove()); return c.innerText; })()")
            check("no grading anywhere on the page", NOGRADE.search(page) is None, (NOGRADE.search(page) or [None])[0])
        pg.locator("#fc-svg .fc-col .fc-hit").nth(5).click(position={"x": 12, "y": 40}); pg.wait_for_timeout(900)
        pg.locator('#fc-rows .fc-row.latest .fc-choice >> text=Clearly different').click(); pg.wait_for_timeout(300)
        pg.evaluate("(h => h.scrollTop = h.scrollHeight)(document.querySelector('#fc-hist'))"); pg.wait_for_timeout(300)
        pg.locator('#fc-rows .fc-row.latest .fc-readlink').click(); pg.wait_for_timeout(600); pg.mouse.move(5, 5)
        if first:
            rd = pg.inner_text('#fc-rows .fc-row.latest .fc-reading')
            check("AI reading expands inline, labelled AI-generated, no right/wrong wording", "AI-generated" in rd and not re.search(r"\b(right|wrong|correct|better|should)\b", rd.split("AI-generated")[0], re.I), rd.split("\n")[0])
            check("latest turn still fully visible with the reading open", full_in_hist(pg, '#fc-rows .fc-row.latest'))
        pg.screenshot(path=f"{OUT}/fc_{scheme}_t6_reading.png")
        pg.click(".fc-basket.pb summary"); pg.wait_for_timeout(400)
        pg.screenshot(path=f"{OUT}/fc_{scheme}_t6_natasha.png")
        pg.click(".fc-basket.pb summary"); pg.wait_for_timeout(300)
        pg.click(".fc-basket.pboth summary"); pg.wait_for_timeout(400)
        if first: check("Both said basket expands with type-consistent items", pg.locator(".fc-basket.pboth[open]").count() == 1 and "/" not in pg.inner_text(".fc-basket.pboth .fc-items" if pg.locator(".fc-basket.pboth .fc-items").count() else ".fc-basket.pboth"))
        pg.click(".fc-basket.pboth summary"); pg.wait_for_timeout(300)
        pg.evaluate("""(() => { const h = document.querySelector('#fc-hist'), r = document.querySelector('#fc-rows .fc-row[data-k="2"]'); h.scrollTop = r.offsetTop - h.querySelector('h2').offsetHeight - 6; })()"""); pg.wait_for_timeout(300)
        pg.locator('#fc-rows .fc-row[data-k="2"]').hover(); pg.wait_for_timeout(500)
        if first:
            check("hovering T3's row lights its column and both dots", pg.locator('#fc-svg .fc-col.hl[data-k="2"]').count() == 1 and pg.locator('#fc-svg .fc-dot.hl').count() == 2)
        pg.screenshot(path=f"{OUT}/fc_{scheme}_hover_t3.png")
        if first:
            pg.mouse.move(5, 5); pg.wait_for_timeout(200)
            check("leaving the row clears the highlight", pg.locator("#fc-svg .hl").count() == 0)
            pg.click("#fc-ctl button >> text=Finish and compare"); pg.wait_for_timeout(2500)
            rv = pg.inner_text("#fc-reveal")
            check("end of round: gap curve with the reflection markers on one timeline", pg.locator("#fc-reveal svg.fc-tl .fc-tl-line").count() == 1
                  and pg.locator("#fc-reveal svg.fc-tl .fc-refmark").count() == 2 and pg.locator("#fc-reveal svg.fc-tl .fc-tl-dot").count() == 6)
            check("reveal still works: distinctive formulation, hints, reflections", "distinctive formulation" in rv.lower() and "Clearly different" in rv
                  and pg.locator(".fc-rv li.not").count() == pg.locator(".fc-rv li.not .fc-hint").count())
            pg.evaluate("document.querySelector('#fc-reveal h4:nth-of-type(1)') && document.querySelector('svg.fc-tl').scrollIntoView({block: 'center'})"); pg.wait_for_timeout(400)
            pg.screenshot(path=f"{OUT}/fc_{scheme}_reveal_timeline.png")
            pg.click("#fc-rv-close"); pg.wait_for_timeout(400)
            check("back to the round restores the fixed layout", pg.evaluate("document.scrollingElement.scrollHeight <= innerHeight + 2"))
        pg.close()
    pg = b.new_page(viewport={"width": W, "height": 757}); pg.on("pageerror", lambda e: errs.append(str(e)))
    open_fc(pg); ok = True
    for k in range(6):
        nxt(pg, 1, 1300); ok = ok and full_in_hist(pg, '#fc-rows .fc-row.latest')
    check("1226x757 viewport (847 window minus browser bars): the latest turn stays fully visible after every Next", ok)
    pg.close()
    pg = b.new_page(viewport={"width": W, "height": H}); pg.on("pageerror", lambda e: errs.append(str(e)))
    open_fc(pg); pg.click("#fc-ctl button >> text=Play"); pg.wait_for_timeout(6000); n = pg.evaluate("FC.state().shown")
    check("Play adds rows as the trajectory extends", pg.locator("#fc-rows .fc-row").count() == n == pg.locator("#fc-svg .fc-col").count() and n >= 2, f"{n} turns")
    pg.click("#fc-ctl button >> text=Pause"); pg.click("#fc-ctl button >> text=Back"); pg.wait_for_timeout(500)
    check("Back removes the last row", pg.locator("#fc-rows .fc-row").count() == n - 1)
    pg.close()
    m = b.new_page(viewport={"width": 820, "height": 1000}); m.on("pageerror", lambda e: errs.append(str(e)))
    open_fc(m); nxt(m, 2)
    y = [m.locator(s).bounding_box()["y"] for s in (".fc-traj", ".fc-bask", "#fc-hist")]
    check("below 900 px: stacked; no sideways scroll", y[0] < y[1] < y[2] and m.evaluate("document.documentElement.scrollWidth") <= 820)
    m.close()
    rm = b.new_page(viewport={"width": W, "height": H}, reduced_motion="reduce"); rm.on("pageerror", lambda e: errs.append(str(e)))
    open_fc(rm); rm.click("#fc-ctl button >> text=Next"); rm.wait_for_timeout(150)
    check("reduced motion: the new turn appears at once", rm.evaluate("+getComputedStyle(document.querySelector('#fc-svg .fc-gist')).opacity") == 1
          and rm.evaluate("getComputedStyle(document.querySelector('#fc-rows .fc-row')).animationName") == "none")
    rm.close()
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
