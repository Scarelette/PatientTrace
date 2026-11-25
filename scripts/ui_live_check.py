"""Live-mode browser click-through: interview (reply + tracing chips) and side-by-side compare."""
import os, sys, time
from playwright.sync_api import sync_playwright
URL, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
errs, res = [], []
def check(name, ok, extra=""): res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    pg.goto(URL); pg.wait_for_timeout(1500)
    check("browse note hidden in live mode", pg.locator("#browse-note.hidden").count() == 1)
    pg.click('.choose-card[onclick="App.goExisting()"]'); pg.wait_for_selector(".pick-card", timeout=15000)
    pg.fill("#filter-search", "Max"); pg.wait_for_timeout(500)
    pg.locator(".pick-card").first.click(); pg.click("#existing-generate")
    pg.wait_for_selector("#graph-slot svg", timeout=20000); pg.wait_for_timeout(1500)
    check("chat button enabled", not pg.locator("#chat-btn").is_disabled())
    pg.click("#chat-btn"); pg.wait_for_timeout(2000)
    pg.fill("#chat-text", "What has been worrying you most lately?"); pg.click("#chat-send")
    t0 = time.time()
    pg.wait_for_function("document.querySelectorAll('#chat-log .bubble.patient:not(.typing)').length >= 1", timeout=180000)
    check("patient reply shown", True, f"{time.time() - t0:.1f}s")
    try:
        pg.wait_for_selector(".chip-l", timeout=30000); check("reply traced onto network", True, f"{pg.locator('.chip-l').count()} chips")
    except Exception:
        check("reply traced onto network", False, "no chips within 30 s")
    pg.wait_for_timeout(1500); pg.screenshot(path=f"{OUT}/1_interview.png")
    pg.get_by_text("Network", exact=True).first.click(); pg.wait_for_selector("#contrast-btn", state="visible", timeout=15000)
    pg.click("#contrast-btn")
    pg.wait_for_selector(".ccard", timeout=30000); pg.wait_for_timeout(1000)
    go = pg.locator(".ccard button.primary").first
    check("compare enabled", not go.is_disabled()); go.click()
    t0 = time.time()
    pg.wait_for_selector("#dual-shared-text", state="visible", timeout=60000); pg.wait_for_timeout(1500)
    pg.fill("#dual-shared-text", "What usually happens right before you start feeling anxious?"); pg.click("#dual-shared-send")
    pg.wait_for_function("document.querySelectorAll('.dual-turn .dual-ans .btext').length >= 2", timeout=240000)
    check("both patients answered", True, f"{time.time() - t0:.1f}s")
    try:
        pg.wait_for_selector(".dual-turn .diff-label", timeout=90000); check("difference sentence shown", True)
    except Exception:
        check("difference sentence shown", False)
    pg.wait_for_timeout(2000); pg.screenshot(path=f"{OUT}/2_compare.png")
    b.close()
check("no JS errors", not errs)
for e in errs[:10]: print("  JS:", e[:200])
sys.exit(0 if all(res) else 1)
