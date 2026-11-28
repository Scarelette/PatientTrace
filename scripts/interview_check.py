"""Interview page (redesign 2): screenshots in light + dark of the initial state, hovering a loop reply,
a reply touching 5+ themes, an eased reply and mid-replay (Emily), plus Leila; and behaviour checks.
Usage: python scripts/interview_check.py URL OUTDIR"""
import os, sys
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
res = []
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)

def open_iv(pg, idx):
    pg.goto(URL); pg.wait_for_timeout(700); pg.evaluate("App.goExisting()"); pg.wait_for_selector(".pick-card")
    pg.evaluate(f"App._ext.State.selectedIdx = {idx}; App.generateFromExisting()"); pg.wait_for_selector("#graph-slot canvas, #graph-slot svg", timeout=20000)
    pg.wait_for_timeout(800); pg.evaluate("localStorage.clear()")
    pg.evaluate("App.goChat()"); pg.wait_for_selector("#screen-interview.active", timeout=15000); pg.wait_for_timeout(1200)
    pg.evaluate("Interview.enterExample(); Interview.stopReplay()")      # the recorded example is opt-in now: show it whole
    pg.wait_for_selector("#iv-log .iv-msg.p", timeout=15000); pg.wait_for_timeout(1500)

PLAN = """() => { const S = Interview.state(), P = S.turns.map((t, i) => t.who === 'p' ? i : -1).filter(i => i >= 0);
  const themes = t => { const v = new Set([...(t.active || []), ...(t.eased || []), ...(t.path || []).flat(), ...(t.trigger ? [t.trigger.node] : [])]);
    const th = new Set([...v].map(n => S.net.themeOf[n]).filter(x => x != null)); (t.themes || []).forEach(m => { const x = (m.members || [])[0]; if (S.net.themeOf[x] != null) th.add(S.net.themeOf[x]); }); return th.size; };
  const rows = P.map((i, k) => ({ k: k + 1, i, themes: themes(S.turns[i]), eased: (S.turns[i].eased || []).length, loop: (S.turns[i].path || []).length }));
  const most = rows.slice().sort((a, b) => b.themes - a.themes)[0], ez = rows.find(r => r.eased > 0) || null;
  return { rows, most, ez }; }"""
def reply_index(pg, k): return pg.evaluate(f"Interview.state().turns.map((t, i) => t.who === 'p' ? i : -1).filter(i => i >= 0)[{k - 1}]")
OPEN = "document.querySelectorAll('#iv-net .iv-th-card').length && [...document.querySelectorAll('#iv-net .iv-th-card')].filter(r => +r.style.opacity > .5).length"
NODES = "document.querySelectorAll('#iv-net .iv-nd').length"

with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    for scheme in ("light", "dark"):
        pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1, color_scheme=scheme)
        pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
        open_iv(pg, 1); first = scheme == "light"
        if first:
            nb, cb = pg.locator("#iv-net").bounding_box(), pg.locator(".iv-chat").bounding_box()
            check("layout: network + explanation on the left, chat on the right", nb["x"] < cb["x"] and pg.locator(".iv-left #iv-detail").count() == 1)
            check("left column scrolls on its own", pg.evaluate("getComputedStyle(document.querySelector('.iv-left')).overflowY") == "auto")
            check("no floating popover any more", pg.locator("#iv-pop").count() == 0)
            st = pg.evaluate("Interview.state()")
            check("13 themes drawn as circles in five columns", len(st["net"]["groups"]) == pg.locator("#iv-net .iv-th").count() == 13, f"{pg.locator('#iv-net .iv-th').count()} themes")
            check("progress rings show surfaced share", pg.evaluate("[...document.querySelectorAll('#iv-net .ring')].some(r => parseFloat(r.getAttribute('stroke-dasharray')) > 0)"))
            check("header: 'N of M surfaced' and 'Not explored yet'", "surfaced" in pg.inner_text("#iv-cov") and pg.inner_text("#iv-unexp").startswith("Not explored yet"), pg.inner_text("#iv-cov"))
            # pinned reply 2 at start: only its themes open, only its nodes listed
            i2 = reply_index(pg, 2); t2 = st["turns"][i2]
            vis2 = set(t2["active"]) | set(t2["eased"]) | {x for e in t2["path"] for x in e} | ({t2["trigger"]["node"]} if t2.get("trigger") else set())
            check("opened themes list only the nodes the reply expressed", pg.evaluate(NODES) == len(vis2), f"{pg.evaluate(NODES)} nodes shown, reply has {len(vis2)}")
            check("untouched themes fade to ~50%", pg.evaluate("[...document.querySelectorAll('#iv-net .iv-th')].filter(g => Math.abs(+g.style.opacity - .5) < .05).length") >= 8)
        pg.screenshot(path=f"{OUT}/iv2_{scheme}_1_initial.png")
        # hover the reply touching the most themes (Emily: also the loop reply)
        plan = pg.evaluate(PLAN); M = plan["most"]
        i3 = M["i"]; b3 = pg.locator(f'#iv-log [data-i="{i3}"] .bubble'); b3.scroll_into_view_if_needed(); b3.hover(); pg.wait_for_timeout(1300)
        if first:
            check("hover previews in the explanation panel (dashed, 'previewing, click to pin')", "preview" in (pg.get_attribute("#iv-detail", "class") or "")
                  and "previewing" in pg.inner_text("#iv-detail").lower())
            check("the hovered reply opens its themes", pg.evaluate(OPEN) == M["themes"], f"{pg.evaluate(OPEN)} themes open, reply {M['k']} touches {M['themes']}")
        pg.screenshot(path=f"{OUT}/iv2_{scheme}_2_hover_loop.png")
        if first:
            pg.mouse.move(1430, 890); pg.wait_for_timeout(700)
            check("mouse leave returns to the pinned reply", "preview" not in (pg.get_attribute("#iv-detail", "class") or "") and "reply 2 of" in pg.inner_text("#iv-detail").lower())
        # pin reply 3: 5 themes, loop
        b3.click(); pg.mouse.move(1430, 890); pg.wait_for_timeout(4500)
        if first:
            check(f"pinned reply {M['k']} opens all {M['themes']} of its themes (most in the session); loop links move if it has a loop", pg.evaluate(OPEN) == M["themes"],
                  f"{pg.evaluate(OPEN)} themes, {pg.locator('#iv-net .iv-fx.flow').count()} loop links")
            check("'Why these links' is a collapsed section", pg.locator("#iv-detail details.iv-ev-w").count() == 1 and not pg.evaluate("document.querySelector('#iv-detail details.iv-ev-w').open"))
            check("guidance kept: she shows / underneath / tip / you could try + AI label", all(x in pg.inner_text("#iv-detail").lower() for x in ("shows", "underneath", "you could try", "ai-generated")))
        pg.screenshot(path=f"{OUT}/iv2_{scheme}_3_five_themes.png")
        # eased reply (if the session has one)
        if plan["ez"]:
            ie = plan["ez"]["i"]; pg.locator(f'#iv-log [data-i="{ie}"] .bubble').scroll_into_view_if_needed(); pg.locator(f'#iv-log [data-i="{ie}"] .bubble').click(); pg.mouse.move(1430, 890); pg.wait_for_timeout(3000)
            if first: check(f"eased reply ({plan['ez']['k']}): dashed eased nodes", pg.locator("#iv-net .iv-nd.ez").count() >= 1, f"{pg.locator('#iv-net .iv-nd.ez').count()} eased")
        elif first: print("(no eased reply in this session: eased check skipped)")
        pg.screenshot(path=f"{OUT}/iv2_{scheme}_4_eased.png")
        # collapsing reverses: back to the most-themes reply, only its cards stay open
        b3.scroll_into_view_if_needed(); b3.click(); pg.mouse.move(1430, 890); pg.wait_for_timeout(2500)
        if first: check("switching replies collapses the previous cards (only the new reply's themes open)", pg.evaluate(OPEN) == M["themes"])
        # replay
        pg.evaluate("void Interview.replay()"); pg.wait_for_timeout(7000)
        if first: n = pg.locator("#iv-log .iv-msg").count(); check("replay: messages arrive one by one; badge + 'Exit example'", 1 <= n < 16 and pg.inner_text("#iv-play span") == "Exit example"
                                                                   and pg.locator("#iv-exbadge:not(.hidden)").count() == 1, f"{n} messages")
        pg.screenshot(path=f"{OUT}/iv2_{scheme}_5_mid_replay.png")
        pg.evaluate("Interview.stopReplay()"); pg.wait_for_timeout(500)
        if first: check("stop replay restores the example transcript", pg.locator("#iv-log .iv-msg").count() == 16)
        pg.click("#iv-play"); pg.wait_for_timeout(600)
        if first: check("'Exit example' returns to the empty live session", pg.locator("#iv-log .iv-msg").count() == 0 and pg.locator("#iv-exbadge.hidden").count() == 1)
        pg.close()
        pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1, color_scheme=scheme); pg.on("pageerror", lambda e: errs.append(str(e)))
        open_iv(pg, 0)
        i4 = reply_index(pg, 4); pg.locator(f'#iv-log [data-i="{i4}"] .bubble').click(); pg.mouse.move(1430, 890); pg.wait_for_timeout(3500)
        if first: check("Leila: themes open for reply 4, trigger marked, no loop", pg.evaluate(OPEN) == pg.evaluate(PLAN)["rows"][3]["themes"] and pg.locator("#iv-log .iv-trig-word").count() >= 1 and pg.locator("#iv-net .iv-fx.flow").count() == 0)
        pg.screenshot(path=f"{OUT}/iv2_leila_{scheme}_pinned.png"); pg.close()
    m = b.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=1); m.on("pageerror", lambda e: errs.append("mobile " + str(e)))
    open_iv(m, 1)
    y = [m.locator(s).bounding_box()["y"] for s in ("#iv-net", "#iv-detail", ".iv-chat")]
    i2 = reply_index(m, 3); m.locator(f'#iv-log [data-i="{i2}"] .bubble').tap(); m.wait_for_timeout(1200)
    check("phone: stacked network, explanation, chat; tap pins; no sideways scroll", y[0] < y[1] < y[2] and m.evaluate("Interview.state().pinned") == i2
          and m.evaluate("document.documentElement.scrollWidth") <= 390)
    m.screenshot(path=f"{OUT}/iv2_mobile.png"); m.close()
    rm = b.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce"); rm.on("pageerror", lambda e: errs.append(str(e)))
    open_iv(rm, 1); M = rm.evaluate(PLAN)["most"]; rm.locator(f'#iv-log [data-i="{M["i"]}"] .bubble').click(); rm.wait_for_timeout(300)
    check("reduced motion: themes open and the path shows at once", rm.evaluate(OPEN) == M["themes"] and rm.locator("#iv-net .iv-fx").count() >= 1
          and rm.evaluate("[...document.querySelectorAll('#iv-net .iv-fx')].every(p => p.style.opacity !== '0')"))
    rm.close()
    print("JS errors:", errs or "none"); print(f"{sum(res)}/{len(res)} passed"); b.close()
