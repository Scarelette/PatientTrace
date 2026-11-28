/* Formulation Contrast (the redesigned Compare step).
   One trainee question goes to two patients who share a presenting problem; the page shows what happened
   and lets the trainee judge it. It never scores or grades a question.
   - Trajectory: two tracks leave the shared presenting problem; the vertical gap follows the running gap
     g_t = 0.6 g_{t-1} + 0.4 d_t (g_0 = 0), d_t from /api/ext/fc/turn (how differently the two answers expressed
     their networks, compared in a shared "function" space). g = 1 uses the full height; at least 6 px apart.
     Each track is labelled with a 3-6 word gist of the answer's main node; the quote is on hover.
   - Baskets: one item per network node (repeats merged: "2 mentions · T1, T3"), collapsed; a new item peeks.
   - History: every turn, oldest first: question, both answers (aligned spans underlined), reflection (latest open,
     earlier as a chip). Scrolls on its own under the trajectory; rows and columns are linked both ways.
   - End of round: each patient's distinctive causes (found / not reached, with a question hint), reflections
     next to the gap, and an AI-generated learning point (hidden when none exists for this many questions).
   Sources: a recorded example (data/replay/<case>.json, precomputed; Back / Play / Next) or a live pair. */
window.FC = (function () {
  const X = () => App._ext;
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const REDUCED = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const NS = "http://www.w3.org/2000/svg";
  const TYPE_VAR = { "Stimulus": "--t-stimulus", "Emotion": "--t-emotion", "Cognition": "--t-cognition", "Physiological Sensation": "--t-physio", "Behavior": "--t-behavior" };
  const tvar = t => TYPE_VAR[t] || "--muted";
  const sv = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent && parent.appendChild(e); return e; };
  const wrap = (s, max, lines) => { const out = []; let cur = "";
    String(s).split(/\s+/).forEach(w => { const t = cur ? cur + " " + w : w; if (t.length > max && cur) { out.push(cur); cur = w; } else cur = t; });
    if (cur) out.push(cur); if (out.length > lines) { out.length = lines; out[lines - 1] = out[lines - 1].replace(/[,.;:?]?$/, "") + "…"; } return out; };
  let F = null;

  // ---------------- state ----------------
  const shown = () => F.turns.slice(0, F.shown);
  const dOf = t => t.fc ? (t.fc.d != null ? t.fc.d : t.fc.d_raw) : 0;
  function gaps() { let g = 0; return shown().map(t => (g = 0.6 * g + 0.4 * dOf(t))); }       // running gap, g_0 = 0
  const rkey = k => `fc-reflect:${F.key}:${k}`;
  function getRef(k) { try { return JSON.parse(localStorage.getItem(rkey(k)) || "null"); } catch (e) { return F.mem[k] || null; } }
  function setRef(k, v) { F.mem[k] = v; try { localStorage.setItem(rkey(k), JSON.stringify(v)); } catch (e) { /* kept for this visit */ } }
  function logGaps() {
    const g = gaps();
    try { console.table(shown().map((t, k) => ({ turn: "T" + (k + 1), d_t: +dOf(t).toFixed(3), g_t: +g[k].toFixed(3), method: t.fc && t.fc.method, question: t.question }))); } catch (e) {}
  }

  // ---------------- trajectory (sized to its card in real pixels; columns spread, max 220 px apart) ----------------
  const H = 264, TOP = 16, MID = 141, PILL = 150, MINGAP = 6, MARK_Y = 254;   // MARK_Y: reflection markers under the columns
  const AVAIL = 66;                                  // half-gap at g = 1: keeps two-line gists clear of the question rows and the bottom
  function geo(n) {
    const W = Math.max(520, Math.round($("#fc-svgwrap").clientWidth || 900));
    const room = W - PILL - 70 - 70, col = Math.min(220, room / Math.max(n - 1, 1));   // last column lands near the right edge
    return { W, col, x: k => PILL + 70 + k * col };
  }
  const halfGap = g => Math.max(MINGAP / 2, g * AVAIL);
  function points(G, gs) { return [{ x: PILL, ya: MID - MINGAP / 2, yb: MID + MINGAP / 2 }].concat(gs.map((g, k) => ({ x: G.x(k), ya: MID - halfGap(g), yb: MID + halfGap(g) }))); }
  function track(pts, key) {
    let d = `M${pts[0].x},${pts[0][key]}`;
    for (let i = 1; i < pts.length; i++) { const p = pts[i - 1], q = pts[i], dx = (q.x - p.x) / 2; d += ` C${p.x + dx},${p[key]} ${q.x - dx},${q[key]} ${q.x},${q[key]}`; }
    return d;
  }
  function draw(anim) {
    const svg = $("#fc-svg"), gs = gaps(), n = gs.length, G = geo(n);
    svg.setAttribute("viewBox", `0 0 ${G.W} ${H}`); svg.setAttribute("height", H); svg.innerHTML = "";
    const gCols = sv("g", {}, svg), gTr = sv("g", {}, svg), gDots = sv("g", {}, svg), gTxt = sv("g", {}, svg);
    const pill = sv("g", { class: "fc-pill" }, gTxt), pl = wrap("Both: " + F.presenting, 17, 3), ph = 20 + pl.length * 15;
    sv("rect", { x: 4, y: MID - ph / 2, width: PILL - 4, height: ph, rx: Math.min(24, ph / 2) }, pill);
    pl.forEach((l, i) => { sv("text", { x: (4 + PILL) / 2, y: MID + (i - (pl.length - 1) / 2) * 15 + 4, "text-anchor": "middle" }, pill).textContent = l; });
    shown().forEach((t, k) => {
      const x = G.x(k), g = sv("g", { class: "fc-col" + (k === F.sel ? " sel" : ""), "data-k": k, tabindex: 0, role: "button", "aria-label": `Turn ${k + 1}: ${t.question}` }, gCols);
      sv("rect", { class: "fc-hit", x: x - G.col / 2 + 4, y: 2, width: G.col - 8, height: H - 4, rx: 10 }, g);
      sv("line", { class: "fc-guide", x1: x, x2: x, y1: TOP + 32, y2: MARK_Y - 14 }, g);
      const qw = G.col, qx = Math.max(PILL / 2, Math.min(x, G.W - qw / 2 - 2));   // keep the selected question inside the card
      const q = sv("text", { class: "fc-q", x: qx, y: TOP, "text-anchor": "middle" }, g);
      (G.col < 150 ? [`T${k + 1}`] : wrap(`T${k + 1} · ${t.question}`, Math.max(10, Math.floor(qw / 7)), 2)).forEach((l, i) => { sv("tspan", { x: qx, dy: i ? 14 : 0 }, q).textContent = l; });
      sv("title", {}, g).textContent = `T${k + 1} · d_t ${dOf(t).toFixed(2)} · g_t ${gs[k].toFixed(2)}${t.fc && t.fc.method === "text" ? " (from the answer texts: one side had no aligned node)" : ""}`;
      g.addEventListener("click", () => select(k)); g.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(k); } });
    });
    const full = points(G, gs), pa = sv("path", { class: "fc-track a" }, gTr), pb = sv("path", { class: "fc-track b" }, gTr);
    const dots = [], labs = [];
    shown().forEach((t, k) => {
      const p = full[k + 1], w = Math.max(10, Math.floor((G.col - 8) / 7.4));
      const un = s => !!(t.fc && (t.fc[s].unaligned || !t.fc[s].main));   // the answer matched no node of this patient's network
      dots.push(["a", "b"].map(s => sv("circle", { class: `fc-dot ${s}${un(s) ? " hollow" : ""}`, cx: p.x, cy: s === "a" ? p.ya : p.yb, r: 6.5, "data-k": k }, gDots)));
      const mk = (s, y, up) => { const tx = sv("text", { class: "fc-gist " + s + (un(s) ? " none" : ""), x: p.x, y, "text-anchor": "middle" }, gTxt), f = (t.fc && t.fc[s]) || {};
        const L = wrap(f.gist || firstWords(t[s].response), w, 99); /* complete phrases: wrap onto as many lines as needed, never cut */ L.forEach((l, i) => { sv("tspan", { x: p.x, dy: i ? 13 : (up ? -(L.length - 1) * 13 : 0) }, tx).textContent = l; });
        sv("title", {}, tx).textContent = un(s) ? `Not in this patient's network. ${F.names[s]}: “${t[s].response}”` : `${F.names[s]}: “${f.quote || t[s].response}”`;
        tx.addEventListener("click", () => select(k)); return tx; };
      labs.push([mk("a", p.ya - 12, true), mk("b", p.yb + 20, false)]);
    });
    // reflection markers under the columns (nothing when the turn has no reflection); click jumps to that reflection
    shown().forEach((t, k) => { const r = getRef(k), sym = r && (CHOICES.find(c => c[0] === r.choice) || [])[1]; if (!sym) return;
      const m = sv("g", { class: "fc-refmark", transform: `translate(${full[k + 1].x},${MARK_Y - 5})`, "data-k": k, tabindex: 0, role: "button", "aria-label": `T${k + 1} reflection: ${r.choice}` }, gTxt);
      m.innerHTML = markShape(r.choice, 6); sv("title", {}, m).textContent = `T${k + 1}: ${r.choice}${r.text ? " · " + r.text : ""}`;
      m.addEventListener("click", ev => { ev.stopPropagation(); jumpReflect(k); });
      m.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); jumpReflect(k); } }); });
    let ruler = null;
    if (n) { const p = full[n], rx = Math.min(G.W - 14, p.x + Math.min(78, G.col * 0.42)); ruler = sv("g", { class: "fc-ruler" }, gTxt);
      sv("path", { d: `M${rx - 4},${p.ya} H${rx + 4} M${rx},${p.ya} V${p.yb} M${rx - 4},${p.yb} H${rx + 4}` }, ruler);
      sv("title", {}, ruler).textContent = `gap at T${n}: g_t ${gs[n - 1].toFixed(2)} (d_t ${dOf(F.turns[n - 1]).toFixed(2)})`; }
    const set = pts => { pa.setAttribute("d", track(pts, "ya")); pb.setAttribute("d", track(pts, "yb")); };
    if (!anim || REDUCED() || n === 0) { set(full); return; }
    const prev = full[n - 1], last = full[n];
    dots[n - 1].forEach(c => c.style.opacity = 0); labs[n - 1].forEach(q => q.style.opacity = 0); if (ruler) ruler.style.opacity = 0;
    const t0 = performance.now(), ease = t => 1 - Math.pow(1 - t, 3);
    let ended = false;
    const end = () => {                // the final state is applied exactly once, even if frames are throttled
      if (ended) return; ended = true; set(full);
      dots[n - 1].forEach(c => { c.style.opacity = 1; c.classList.add("pop"); }); labs[n - 1].forEach(q => { q.style.transition = "opacity .5s"; q.style.opacity = 1; });
      if (ruler) { ruler.style.transition = "opacity .5s"; ruler.style.opacity = 1; } };
    setTimeout(end, 750);
    (function step(now) {
      if (ended) return;
      const k = Math.min(1, Math.max(0, (now - t0) / 600)), e = ease(k);
      set(full.slice(0, n).concat([{ x: prev.x + (last.x - prev.x) * e, ya: prev.ya + (last.ya - prev.ya) * e, yb: prev.yb + (last.yb - prev.yb) * e }]));
      if (k < 1) requestAnimationFrame(step); else end();
    })(t0);
  }

  // ---------------- baskets: one item per node, collapsed; the newest item peeks, then folds back ----------------
  function collected() {
    const A = new Map(), B = new Map(), Both = new Map();
    const add = (M, key, base, k, words) => { let it = M.get(key); if (!it) { it = { ...base, first: k, turns: [], quotes: [] }; M.set(key, it); }
      if (!it.turns.includes(k)) it.turns.push(k); if (words && !it.quotes.includes(words)) it.quotes.push(words); return it; };
    shown().forEach((t, k) => { if (!t.fc) return;
      t.fc.a.items.forEach(x => add(A, x.node, x, k, x.words));
      t.fc.b.items.forEach(x => add(B, x.node, x, k, x.words));
      t.fc.both.forEach(x => add(Both, x.a_node + "|" + x.b_node, x, k, null)); });
    return { a: [...A.values()], b: [...B.values()], both: [...Both.values()] };
  }
  function baskets() {
    const c = collected(), latest = F.shown - 1, peekOn = F.peekUntil && Date.now() < F.peekUntil;
    const turns = it => it.turns.map(k => "T" + (k + 1)).join(", ");
    const item = (x, both) => `<li class="fc-item" style="--c:var(${tvar(x.ntype)})">
        <div class="fc-type">${esc(x.type)}</div>
        <div class="fc-short">${both ? esc(x.label || x.short_a) : esc(x.short)}</div>
        ${both ? `<ul class="fc-quotes"><li>${esc(F.names.a)}: ${esc(x.short_a)}${x.words_a ? ` · “${esc(x.words_a)}”` : ""}</li><li>${esc(F.names.b)}: ${esc(x.short_b)}${x.words_b ? ` · “${esc(x.words_b)}”` : ""}</li></ul>${x.reason ? `<div class="fc-why">${esc(x.reason)}</div>` : ""}` : ""}
        ${!both && x.quotes.length ? `<ul class="fc-quotes">${x.quotes.map(q => `<li>“${esc(q)}”</li>`).join("")}</ul>` : ""}
        <div class="fc-from">${x.turns.length > 1 ? x.turns.length + " mentions · " : "from "}${turns(x)}</div></li>`;
    const box = (key, title, list, cls) => { const fresh = list.filter(x => x.first === latest);
      return `<div class="fc-bwrap"><details class="fc-basket ${cls}" data-b="${key}"${F.open[key] ? " open" : ""}>
        <summary><span class="fc-bname">${esc(title)}</span><span class="fc-tdots" aria-hidden="true">${[...new Set(list.map(x => x.ntype))].slice(0, 5).map(t => `<i style="--c:var(${tvar(t)})" title="${esc(TYPE_WORD[t] || t)}"></i>`).join("")}</span><span class="fc-bcount">${list.length} collected</span></summary>
        ${list.length ? `<ul class="fc-items">${list.map(x => item(x, key === "both")).join("")}</ul>` : `<p class="fc-empty">${key === "both" ? "Nothing shared yet: needs the same node type doing the same job for both." : "Nothing collected yet."}</p>`}
        </details>${peekOn && !F.open[key] && fresh.length ? `<div class="fc-peek ${cls}" style="--c:var(${tvar(fresh[0].ntype)})">${esc(fresh[0].type)} · ${esc(key === "both" ? (fresh[0].label || fresh[0].short_a) : fresh[0].short)}${fresh.length > 1 ? ` <span>+${fresh.length - 1}</span>` : ""}</div>` : ""}</div>`; };   // the peek overlays (absolute), so nothing below shifts
    cover();
    $("#fc-baskets").innerHTML = box("a", F.names.a, c.a, "pa") + box("b", F.names.b, c.b, "pb") + box("both", "Both said", c.both, "pboth");
    $("#fc-baskets").querySelectorAll("details").forEach(d => d.addEventListener("toggle", () => { if (F.open[d.dataset.b] === d.open) return; F.open[d.dataset.b] = d.open; if (d.open) F.peekUntil = 0; baskets(); }));
    if (peekOn) { clearTimeout(F.peekT); F.peekT = setTimeout(() => { F.peekUntil = 0; baskets(); }, Math.max(0, F.peekUntil - Date.now())); }
  }

  // coverage strip: the five formulation parts per patient, filled once any answer aligned to that part (no percentages)
  const PARTS = ["Stimulus", "Cognition", "Emotion", "Physiological Sensation", "Behavior"];
  const TYPE_WORD = { "Stimulus": "Trigger", "Cognition": "Thought", "Emotion": "Feeling", "Physiological Sensation": "Body", "Behavior": "Behaviour" };
  function cover() {
    const row = s => { const at = {}; shown().forEach((t, k) => (((t[s].alignment || {}).nodes) || []).forEach(n => { (at[n.type] = at[n.type] || new Set()).add(k); }));
      return `<div class="fc-cov ${s === "a" ? "pa" : "pb"}"><span class="fc-cov-n">${esc(F.names[s])}</span>${PARTS.map(p => { const ks = at[p] ? [...at[p]].sort((x, y) => x - y) : [];
        return `<span class="fc-part${ks.length ? " on" : ""}" style="--c:var(${tvar(p)})" title="${esc(TYPE_WORD[p])}: ${ks.length ? "asked about at " + ks.map(k => "T" + (k + 1)).join(", ") : "not reached yet"}">${TYPE_WORD[p]}</span>`; }).join("")}</div>`; };
    $("#fc-cover").innerHTML = row("a") + row("b");
  }
  const STOPW = new Set("a an the and or but to of in on at for with about from by as my your his her their our its i it is was be that this so if when".split(" "));
  const firstWords = s => { const w = String(s || "").split(/[.;:!?—–]|,\s/)[0].trim().split(/\s+/).slice(0, 6);
    while (w.length > 2 && STOPW.has(w[w.length - 1].toLowerCase())) w.pop(); return w.join(" ").toLowerCase(); };   // a whole phrase, no ellipsis

  // ---------------- conversation history (replaces the single-turn panel) ----------------
  function marked(text, al) {
    const spans = [];
    ((al && al.nodes) || []).forEach(n => { const q = String(n.words || n.evidence || "").trim(); if (!q) return;
      const i = text.toLowerCase().indexOf(q.toLowerCase()); if (i < 0 || spans.some(s => !(i + q.length <= s.a || i >= s.b))) return;
      spans.push({ a: i, b: i + q.length, t: n.type }); });
    spans.sort((x, y) => x.a - y.a);
    let out = "", p = 0;
    spans.forEach(s => { out += esc(text.slice(p, s.a)) + `<mark class="fc-sp" style="--c:var(${tvar(s.t)})">${esc(text.slice(s.a, s.b))}</mark>`; p = s.b; });
    return out + esc(text.slice(p));
  }
  const CHOICES = [["Same so far", "○"], ["Starting to differ", "◐"], ["Clearly different", "●"]];
  // reflection markers as shapes (○ open, ◐ half, ● filled), not font glyphs: ◐ is missing from some system fonts
  function markShape(choice, r) {
    const ring = `<circle class="mk-ring" r="${r}"/>`;
    if (choice === "Clearly different") return `<circle class="mk-fill" r="${r}"/>`;
    if (choice === "Starting to differ") return ring + `<path class="mk-fill" d="M0,${-r} A${r},${r} 0 0 1 0,${r} Z"/>`;
    return ring;
  }
  function reflectHTML(k) {
    const r = getRef(k) || {};
    return `<div class="fc-reflect"><span><b>Your reflection:</b> did this answer change how you see them?</span>
      <div class="fc-choices" role="radiogroup">${CHOICES.map(([c]) => `<button type="button" role="radio" aria-checked="${r.choice === c}" class="fc-choice${r.choice === c ? " on" : ""}">${c}</button>`).join("")}</div>
      <input class="fc-what" type="text" placeholder="What's different? (optional)" value="${esc(r.text || "")}" aria-label="What's different?"></div>`;
  }
  function rowHTML(k, enter) {
    const t = F.turns[k], latest = k === F.shown - 1, r = getRef(k) || {}, open = latest || F.refOpen[k], full = latest || F.more[k];
    const sym = (CHOICES.find(c => c[0] === r.choice) || [])[1];
    const chip = latest ? "" : `<button type="button" class="fc-chip${r.choice ? "" : " empty"}" aria-expanded="${!!F.refOpen[k]}">${r.choice ? `<svg class="fc-refmark mk" viewBox="-8 -8 16 16" aria-hidden="true">${markShape(r.choice, 6)}</svg> ${esc(r.choice)}` : "+ reflect"}</button>`;
    const ans = s => `<div class="fc-ans ${s === "a" ? "pa" : "pb"}"><div class="fc-who">${esc(F.names[s])}</div><p class="${full ? "" : "clamp"}">${marked(t[s].response, t[s].alignment)}</p></div>`;
    return `<article class="fc-row${latest ? " latest" : ""}${enter ? " enter" : ""}" data-k="${k}">
      <div class="fc-rq"><span class="fc-tk">T${k + 1}</span><span class="fc-rqt">${esc(t.question)}</span>${latest ? "" : `<button type="button" class="fc-more link hidden">${full ? "show less" : "show more"}</button>`}${chip}</div>
      <div class="fc-answers">${ans("a")}${ans("b")}</div>
      ${open ? reflectHTML(k) : ""}<div class="fc-readslot"></div></article>`;
  }
  function history(enterK) {
    const box = $("#fc-rows");
    if (!F.shown && F.pendingRow) { box.innerHTML = ""; box.appendChild(F.pendingRow); return; }
    if (!F.shown) { box.innerHTML = `<p class="fc-empty">${F.mode === "recorded" ? "Press <b>Next</b> or <b>Play</b> to send the first question to both patients." : "Type your first question below; it goes to both patients at once."}</p>`; return; }
    box.innerHTML = shown().map((t, k) => rowHTML(k, k === enterK)).join("");
    if (F.pendingRow) box.appendChild(F.pendingRow);          // a live turn still streaming stays at the bottom
    box.querySelectorAll(".fc-row").forEach(row => {
      const k = +row.dataset.k;
      row.addEventListener("mouseenter", () => hl(k)); row.addEventListener("mouseleave", () => hl(-1));
      const ps = [...row.querySelectorAll(".fc-ans p")], more = row.querySelector(".fc-more");
      if (more && (F.more[k] || ps.some(p => p.scrollHeight > p.clientHeight + 1))) more.classList.remove("hidden");
      more && more.addEventListener("click", () => { F.more[k] = !F.more[k]; history(); });
      const chip = row.querySelector(".fc-chip"); chip && chip.addEventListener("click", () => { F.refOpen[k] = !F.refOpen[k]; history(); });
      row.querySelectorAll(".fc-choice").forEach(b => b.addEventListener("click", () => { const cur = getRef(k) || {};
        setRef(k, { ...cur, choice: cur.choice === b.textContent ? null : b.textContent }); if (k !== F.shown - 1) F.refOpen[k] = false;
        F.justReflected = k; history(); draw(false); }));
      const w = row.querySelector(".fc-what"); w && w.addEventListener("input", ev => { const cur = getRef(k) || {}; setRef(k, { ...cur, text: ev.target.value }); fillRead(row, k, true); });
      fillRead(row, k, F.justReflected === k);
    });
    F.justReflected = null;
    if (enterK != null) { F.stick = true; showLatest(!REDUCED()); }
  }
  // reflect, then reveal: the AI reading of the turn appears only after the trainee has reflected on it
  function fillRead(row, k, announce) {   // announce: the trainee just reflected, so bring the link into view
    const slot = row.querySelector(".fc-readslot"), r = getRef(k) || {}, text = ((F.turns[k].fc || {}).reading || "").trim();
    const ready = text && (r.choice || (r.text || "").trim());
    if (!ready) { slot.innerHTML = ""; return; }
    if (announce) requestAnimationFrame(() => reveal(row));
    if (slot.dataset.state === (F.readOpen[k] ? "open" : "closed") && slot.innerHTML) return;
    slot.dataset.state = F.readOpen[k] ? "open" : "closed";
    slot.innerHTML = `<button type="button" class="fc-readlink link" aria-expanded="${!!F.readOpen[k]}">${F.readOpen[k] ? "Hide the AI reading" : "Compare with an AI reading"}</button>
      ${F.readOpen[k] ? `<div class="fc-reading"><p>${esc(text)}</p><div class="iv-ai">AI-generated reading</div></div>` : ""}`;
    slot.querySelector(".fc-readlink").addEventListener("click", () => { F.readOpen[k] = !F.readOpen[k]; fillRead(row, k); if (F.readOpen[k]) reveal(row); });
  }
  // the scroll position that shows a whole row between the sticky heading and the sticky footer (bottom first; if the
  // row is taller than the window, its top wins)
  function fitTop(row) {
    const h = $("#fc-hist"), head = (h.querySelector("h2") || { offsetHeight: 0 }).offsetHeight, m = 4;
    let top = row.offsetTop + row.offsetHeight + m + footH() - h.clientHeight;
    if (row.offsetTop - top < head + m) top = row.offsetTop - head - m;
    return Math.max(0, top);
  }
  function reveal(row) {   // bring a row's bottom (e.g. a just-opened AI reading) into view inside the history
    const h = $("#fc-hist"), top = fitTop(row);
    if (top > h.scrollTop) h.scrollTo({ top, behavior: REDUCED() ? "auto" : "smooth" });
  }
  function jumpReflect(k) {
    if (k !== F.shown - 1) F.refOpen[k] = true;
    history(); select(k);
    const b = document.querySelector(`#fc-rows .fc-row[data-k="${k}"] .fc-choice.on, #fc-rows .fc-row[data-k="${k}"] .fc-choice`); b && b.focus({ preventScroll: true });
  }
  // the sticky footer of the conversation panel (live input, or the recorded-example bar) covers its bottom edge
  function footH() {
    return ["#fc-live", "#fc-recbar"].reduce((n, sel) => { const q = document.querySelector(sel); return n + (q && !q.classList.contains("hidden") ? q.offsetHeight : 0); }, 0);
  }
  // recorded example -> "Try it yourself": the contrast picker (live) for the patient the trainee came from, else the example's first patient
  async function tryIt() {
    const St = X().State;
    if (!St.patientId) { St.selectedIdx = parseInt(F.pa.slice(1), 10); await App.generateFromExisting(); }
    if (window.Ext && Ext.pickContrast) Ext.pickContrast();
  }
  // keep the latest turn (with its reflection row) fully in view; earlier turns scroll above it
  function showLatest(smooth) {
    const h = $("#fc-hist"), row = document.querySelector("#fc-rows .fc-row.latest"); if (!h || !row) return;
    h.scrollTo({ top: fitTop(row), behavior: smooth ? "smooth" : "auto" });
  }
  // trajectory <-> history: hovering a row lights its column and both dots; clicking a column scrolls to and flashes its row
  function hl(k) { document.querySelectorAll("#fc-svg [data-k]").forEach(e => e.classList.toggle("hl", +e.dataset.k === k)); }
  function select(k) {
    F.sel = k; F.stick = false; draw(false);
    const row = document.querySelector(`#fc-rows .fc-row[data-k="${k}"]`), h = $("#fc-hist"); if (!row) return;
    h.scrollTo({ top: row.offsetTop - h.querySelector("h2").offsetHeight - 6, behavior: REDUCED() ? "auto" : "smooth" });   // clear the sticky heading
    row.classList.remove("flash"); void row.offsetWidth; row.classList.add("flash");
  }
  function fitHeight() {   // the round fills the window: trajectory + baskets on top, history scrolls below
    const app = $("#screen-fc .fc-app"); if (!app) return;
    app.style.setProperty("--fc-top", Math.max(0, app.getBoundingClientRect().top + scrollY) + "px");
    const host = app.closest("main") || document.body;
    app.style.setProperty("--fc-bot", (parseFloat(getComputedStyle(host).paddingBottom) || 0) + 2 + "px");
  }

  // ---------------- header, controls, end of round ----------------
  const INTRO_KEY = "fc-intro-dismissed";
  function intro() {   // one-line intro under the title until dismissed (remembered per browser)
    const el = $("#fc-intro"); let gone = false;
    try { gone = localStorage.getItem(INTRO_KEY) === "1"; } catch (e) { gone = !!F.introGone; }
    el.classList.toggle("hidden", !!(gone || F.introGone));
    el.querySelector("span").textContent = `Both patients show ${F.presenting.toLowerCase()}. Ask questions to find out why each one does.`;
    el.querySelector("button").onclick = () => { F.introGone = true; try { localStorage.setItem(INTRO_KEY, "1"); } catch (e) {} el.classList.add("hidden"); fitHeight(); };
  }
  function header() {
    intro();
    $("#fc-meta").textContent = `${F.names.a} vs ${F.names.b} · both ${F.presenting.toLowerCase()} · ${F.shown} question${F.shown === 1 ? "" : "s"} asked`;
    const ctl = $("#fc-ctl"); ctl.innerHTML = "";
    const btn = (t, f, dis, cls) => { const b = X().el("button", { type: "button", class: cls || "ghost small" }, [t]); b.disabled = !!dis; b.addEventListener("click", f); ctl.appendChild(b); return b; };
    if (F.from) btn(`← Back to interview with ${F.from.name}`, backToInterview, false, "ghost small fc-back-iv");
    if (F.mode === "recorded") {
      btn("Try it yourself →", tryIt, false, "ghost small fc-try");
      btn("← Back", () => step(-1), F.shown === 0 || F.playing);
      btn(F.playing ? "Pause" : "▶ Play", () => F.playing ? stopPlay() : play(), F.shown >= F.turns.length && !F.playing);
      btn("Next →", () => step(1), F.shown >= F.turns.length || F.playing);
    }
    btn("Finish and compare", finish, F.shown < 3 || F.playing, "primary small").title = F.shown < 3 ? "Available after 3 questions" : "";
    $("#fc-live").classList.toggle("hidden", F.mode !== "live");
    $("#fc-recbadge").classList.toggle("hidden", F.mode !== "recorded");
    $("#fc-recbar").classList.toggle("hidden", F.mode !== "recorded");
    X().liveBadge($("#fc-livebadge"), F.mode === "live" ? X().LiveStatus.current() : null);
    if (F.mode !== "live") { $("#fc-off").classList.add("hidden"); }
    $("#fc-reveal").classList.add("hidden"); $("#screen-fc .fc-app").classList.remove("revealing");
  }
  function render(anim) { header(); fitHeight(); draw(anim); baskets(); history(anim ? F.shown - 1 : null); }
  function step(dir) {
    const n = Math.max(0, Math.min(F.turns.length, F.shown + dir)); if (n === F.shown) return;
    F.shown = n; F.sel = Math.max(0, n - 1); if (dir > 0) F.peekUntil = Date.now() + 3500;
    render(dir > 0); logGaps();
  }
  function play() { F.playing = true; header(); const tick = () => { if (!F.playing) return; if (F.shown >= F.turns.length) { stopPlay(); return; } step(1); F.timer = setTimeout(tick, REDUCED() ? 400 : 2600); }; tick(); }
  function stopPlay() { F.playing = false; clearTimeout(F.timer); header(); }
  async function finish() {
    const c = collected(), got = { a: new Set([...c.a.map(x => x.node), ...c.both.map(x => x.a_node)]), b: new Set([...c.b.map(x => x.node), ...c.both.map(x => x.b_node)]) };
    let rv = F.mode === "recorded" ? F.reveal : null;
    if (!rv) { try { rv = await X().api("/api/ext/fc/reveal", { patient_a: F.pa, patient_b: F.pb, focus: F.focus, reached: { a: [...got.a], b: [...got.b] }, names: F.names, genders: F.genders || {} }); }
      catch (e) { try { rv = await X().api(`/api/ext/fc/reveal/${F.pa}/${F.pb}` + (F.focus ? "?focus=" + encodeURIComponent(F.focus) : "")); } catch (e2) { X().toast("Reveal unavailable: " + e2.message); return; } } }
    // learning point: the stored one for this many questions, else a fresh one, else no box at all
    let lp = F.mode === "recorded" && F.learningByN ? F.learningByN[String(F.shown)] : null;
    if (!lp || !lp.point) { try { lp = await X().api("/api/ext/fc/learning", { names: F.names, turns: shown().map((t, k) => ({ question: t.question, d: gaps()[k] })) }); } catch (e) { lp = null; } }
    const gs = gaps(), maxg = Math.max(0.01, ...gs);
    const side = s => `<div class="fc-rv ${s === "a" ? "pa" : "pb"}"><h4>${esc(F.names[s])}'s distinctive formulation</h4><ul>${(rv[s] || []).map(x => { const f = got[s].has(x.node);
      return `<li class="${f ? "found" : "not"}" style="--c:var(${tvar(x.ntype)})"><span class="fc-type">${esc(x.type)}</span> ${esc(x.label)} <em>${f ? "found" : "not reached"}</em>${!f && x.hint ? `<div class="fc-hint">Try: ${esc(x.hint)}</div>` : ""}</li>`; }).join("") || "<li>No distinctive cause recorded.</li>"}</ul></div>`;
    $("#fc-reveal").innerHTML = `<div class="fc-rv-h"><h3>End of round: ${esc(rv.name || F.presenting)}</h3><button type="button" class="ghost small" id="fc-rv-close">Back to the round</button></div>
      <div class="fc-rv-grid">${side("a")}${side("b")}</div>
      <h4>Your reflections next to the gap</h4>
      ${timeline(gs)}
      <table class="fc-rv-tab"><thead><tr><th>Turn</th><th>Question</th><th>Gap</th><th>Your reflection</th></tr></thead><tbody>${shown().map((t, k) => { const r = getRef(k) || {};
        return `<tr><td>T${k + 1}</td><td>${esc(t.question)}</td><td><span class="fc-bar" title="g_t ${gs[k].toFixed(2)}"><i style="width:${(gs[k] / maxg * 100).toFixed(0)}%"></i></span></td><td>${r.choice ? esc(r.choice) : "<span class='muted'>–</span>"}${r.text ? `<div class="muted small">${esc(r.text)}</div>` : ""}</td></tr>`; }).join("")}</tbody></table>
      ${lp && lp.point ? `<div class="fc-learn"><h4>Learning point</h4><p>${esc(lp.point)}</p><div class="iv-ai">AI-generated guidance${lp.model ? " · " + esc(lp.model) : ""}</div></div>` : ""}`;
    $("#screen-fc .fc-app").classList.add("revealing"); const el = $("#fc-reveal"); el.classList.remove("hidden"); el.scrollIntoView({ block: "start", behavior: REDUCED() ? "auto" : "smooth" });
    $("#fc-rv-close").addEventListener("click", () => { el.classList.add("hidden"); $("#screen-fc .fc-app").classList.remove("revealing"); scrollTo(0, 0); });
  }

  // end of round: the gap curve and the trainee's reflection markers on one timeline (no commentary)
  function timeline(gs) {
    const n = gs.length, W = 640, L = 70, step = Math.min(90, (W - L - 30) / Math.max(n - 1, 1)), x = k => L + k * step, y = g => 70 - g * 56;
    const path = gs.map((g, k) => `${k ? "L" : "M"}${x(k)},${y(g)}`).join(" ");
    const marks = gs.map((g, k) => { const r = getRef(k) || {}, sym = (CHOICES.find(c => c[0] === r.choice) || [])[1];
      return `<circle class="fc-tl-dot" cx="${x(k)}" cy="${y(g)}" r="4"><title>T${k + 1} gap ${g.toFixed(2)}</title></circle>
        <text class="fc-tl-t" x="${x(k)}" y="128" text-anchor="middle">T${k + 1}</text>
        ${sym ? `<g class="fc-refmark" transform="translate(${x(k)},99)"><title>T${k + 1}: ${esc(r.choice)}</title>${markShape(r.choice, 6.5)}</g>` : ""}`; }).join("");
    return `<svg class="fc-tl" viewBox="0 0 ${W} 136" role="img" aria-label="Gap per turn with your reflection markers underneath">
      <text class="fc-tl-l" x="0" y="44">Gap</text><text class="fc-tl-l" x="0" y="104">You</text>
      <line class="fc-tl-axis" x1="${L - 10}" x2="${x(n - 1) + 10}" y1="70" y2="70"/><path class="fc-tl-line" d="${path}"/>${marks}</svg>`;
  }

  // ---------------- live: one question, both patients stream side by side ----------------
  // Ask is disabled for the whole turn (streaming, then alignment + gap); Stop cancels the streaming.
  // A failed or stopped turn puts the question back into the box with an inline error and Retry; the
  // server only advances the two conversations once both replies finished, so a retry re-asks both.
  function liveUI(state, status) {          // "idle" | "streaming" | "placing"; Ask disabled while the patient model is down
    const down = F && F.model === "down";
    $("#fc-ask").classList.toggle("hidden", state === "streaming"); $("#fc-stop").classList.toggle("hidden", state !== "streaming");
    $("#fc-ask").disabled = state !== "idle" || down || (F && F.replaying);
    $("#fc-q").disabled = down || (F && F.replaying);
    const why = down ? "Live interview unavailable right now: " + (F.modelWhy || "") : "";
    $("#fc-q").title = why; $("#fc-ask").title = why;
    if (status !== undefined) $("#fc-status").textContent = status || "";
  }
  function onStatus(st) {                    // badge, unavailable note with the recorded example, input on/off
    if (!F || F.mode !== "live") return;
    F.model = st.patient_model === "cold" ? "starting" : st.patient_model; F.modelWhy = st.detail || "";
    if (!$("#screen-fc").classList.contains("active")) return;
    X().liveBadge($("#fc-livebadge"), st);
    const off = $("#fc-off"), down = F.model === "down";
    off.classList.toggle("hidden", !down);
    off.innerHTML = down ? `<b>Live interview unavailable right now.</b> ${esc(F.modelWhy)} <button type="button" class="ghost small" id="fc-off-ex">Watch a recorded example</button>` : "";
    const ex = $("#fc-off-ex"); if (ex) ex.addEventListener("click", () => openRecorded());
    if (!F.stream && !F.placing) liveUI("idle");
  }
  function fcErr(msg, retry) {
    const e = $("#fc-err"); e.classList.toggle("hidden", !msg);
    if (!msg) return;
    e.querySelector("span").textContent = msg;
    e.querySelector("button").onclick = () => { e.classList.add("hidden"); retry(); };
  }
  function pendingRow(q) {
    $("#fc-rows").querySelectorAll(".fc-row.latest").forEach(r => r.classList.remove("latest"));
    const empty = $("#fc-rows .fc-empty"); if (empty) empty.remove();
    const row = document.createElement("article"); row.className = "fc-row latest pending enter";
    const col = s => `<div class="fc-ans ${s === "a" ? "pa" : "pb"}"><div class="fc-who">${esc(F.names[s])}</div><p class="fc-stream" data-s="${s}"><span class="fc-typing"><i></i><i></i><i></i></span></p></div>`;
    row.innerHTML = `<div class="fc-rq"><span class="fc-tk">T${F.turns.length + 1}</span><span class="fc-rqt">${esc(q)}</span><span class="fc-ai-tag">AI-simulated replies</span></div><div class="fc-answers">${col("a")}${col("b")}</div>`;
    $("#fc-rows").appendChild(row); F.pendingRow = row; F.stick = true; showLatest(!REDUCED());
    return row;
  }
  function saveRound() {
    if (!F.fid) return;
    const rec = { case_id: "fc.live-" + F.fid, kind: "formulation-contrast", mode: "live", patient_a: F.pa, patient_b: F.pb, names: F.names, focus: F.focus,
      note: F.presenting, recorded: new Date().toISOString().slice(0, 16), turns: F.turns };
    X().api("/api/ext/fc/save", { fid: F.fid, record: rec }).catch(() => {});
  }
  async function place(me, q, ans, row) {        // both answers in: alignment -> gap -> trajectory, baskets, reflection row
    liveUI("placing", me.replaying ? me.progress : "Placing the answers: alignment and gap…");
    const hist = s => me.turns.flatMap(t => [{ who: "t", text: t.question }, { who: "p", text: t[s].response }]);
    let fc;
    try { fc = await X().apiT("/api/ext/fc/turn", { patient_a: me.pa, patient_b: me.pb, question: q, a: { response: ans.a }, b: { response: ans.b }, history_a: hist("a"), history_b: hist("b") }, 130000); }
    catch (e) { if (F !== me) return; liveUI("idle"); fcErr("Both patients answered, but placing the answers failed: " + (e.message || e), () => { fcErr(null); place(me, q, ans, row); }); return; }
    if (F !== me) return;
    row.remove(); me.pendingRow = null;
    me.turns.push({ question: q, a: { response: ans.a, alignment: fc.a_alignment }, b: { response: ans.b, alignment: fc.b_alignment }, fc });
    me.shown = me.turns.length; me.sel = me.shown - 1; me.peekUntil = Date.now() + 3500; render(true); logGaps();
    liveUI(me.replaying ? "placing" : "idle", me.replaying ? me.progress : ""); saveRound(); if (!me.replaying) $("#fc-q").focus();
  }
  async function stopAsk() {
    if (!F || !F.stream) return;
    try { await X().api("/api/ext/live/stop", { stream_id: F.stream.id }); } catch (e) {}
    F.stream.ctl.abort();
  }
  async function ask() {
    const inp = $("#fc-q"), q = inp.value.trim(); if (!q || !F || F.stream || F.placing || F.replaying) return;
    inp.value = "";
    return askCore(q, null);
  }
  // one turn; given = {a: answer} when side a's answer is already known (replay of the trainee's Interview)
  async function askCore(q, given) {
    const inp = $("#fc-q");
    fcErr(null);
    const me = F, ctl = new AbortController(); me.stream = { id: X().newStreamId(), ctl };
    liveUI("streaming", given ? me.progress || "" : "Both patients are answering…");
    const row = pendingRow(q), text = { a: "", b: "" }, done = {}, err = {};
    const put = (s, v) => { const p = row.querySelector(`.fc-stream[data-s="${s}"]`); if (p) { p.textContent = v; } };
    try {
      if (me.model !== "up") {                 // starting: hold the question until the patient model is ready
        $("#fc-status").textContent = "Waiting for the patient model to start…";
        row.querySelectorAll(".fc-stream").forEach(p => { p.innerHTML = `<span class="fc-typing"><i></i><i></i><i></i></span> <span class="iv-wait">waiting for the patient model…</span>`; });
        await X().LiveStatus.waitUp(10 * 60 * 1000, ctl.signal);
        row.querySelectorAll(".fc-stream").forEach(p => { p.innerHTML = `<span class="fc-typing"><i></i><i></i><i></i></span>`; });
        $("#fc-status").textContent = given ? me.progress || "" : "Both patients are answering…";
      }
      if (!me.fid) { const st = await (me.starting || X().api("/api/ext/fc/live/start", { patient_a: me.pa, patient_b: me.pb })); me.starting = null;
        me.fid = st.fid; me.names = st.names; me.genders = st.genders; }
      let idle = null; const kick = () => { clearTimeout(idle); idle = setTimeout(() => { err.all = "no answer from the patient model within 75 s"; ctl.abort(); }, 75000); };
      kick();
      await X().stream("/api/ext/fc/live/ask", { fid: me.fid, question: q, stream_id: me.stream.id, given: given || {} }, ev => {
        kick();
        const s = ev.side;
        if (ev.retry) { $("#fc-status").textContent = `Retrying ${me.names[s] || s}…`; return; }
        if (s && (ev.text != null || ev.reset)) { text[s] = ev.reset ? "" : ev.text; put(s, text[s]); if (me.stick) showLatest(false); }
        if (s && ev.done) { done[s] = ev.reply; put(s, ev.reply); }
        if (s && ev.error) err[s] = ev.error;
        if (ev.stopped) err[s || "all"] = "stopped";
      }, ctl.signal);
      clearTimeout(idle);
    } catch (e) { err.all = err.all || (ctl.signal.aborted ? "stopped" : (e.message || String(e))); }
    finally { me.stream = null; }
    if (F !== me) return;
    if (!(done.a && done.b)) {
      row.remove(); me.pendingRow = null; history();
      liveUI("idle");
      const stopped = Object.values(err).includes("stopped");
      const why = "No reply: " + Object.entries(err).map(([s, m]) => (me.names[s] ? me.names[s] + ": " : "") + m).join("; ");
      if (given) { fcErr(stopped ? "Replay stopped." : why, () => replayInterview(me.replayLeft)); return false; }
      if (!inp.value.trim()) inp.value = q;
      fcErr(stopped ? "Stopped. Your question is back in the box." : why, () => ask());
      return false;
    }
    me.placing = true;
    try { await place(me, q, done, row); } finally { me.placing = false; }
    return me.turns.length > 0 && me.turns[me.turns.length - 1].question === q;
  }
  // "Ask <B> the questions you already asked <A>": one turn per Interview question, A's answer from that interview
  async function replayInterview(qa) {
    const me = F; if (!qa || !qa.length) return;
    me.replaying = true; $("#fc-q").disabled = true;
    const total = me.replayTotal || (me.replayTotal = qa.length);
    try {
      for (let i = 0; i < qa.length; i++) {
        if (F !== me) return;
        me.replayLeft = qa.slice(i);
        const k = total - qa.length + i + 1;
        me.progress = `Replaying your interview: question ${k} of ${total}. ${me.names.b} is answering; ${me.names.a}'s answer comes from your interview.`;
        $("#fc-progress").classList.remove("hidden"); $("#fc-progress").innerHTML = `<i style="width:${Math.round((k - 1) / total * 100)}%"></i>`;
        const ok = await askCore(qa[i].q, { a: qa[i].a });
        if (!ok) return;                                  // error shown with a Retry that resumes from here
        $("#fc-progress i").style.width = Math.round(k / total * 100) + "%";
      }
      me.replayLeft = null; liveUI("idle", `Replayed ${total} question${total === 1 ? "" : "s"} from your interview. Ask your next question.`);
    } finally {
      if (F === me) { me.replaying = false; $("#fc-q").disabled = false; setTimeout(() => { if (F === me && !me.replaying) $("#fc-progress").classList.add("hidden"); }, 1500);
        if (!me.replayLeft) $("#fc-q").focus(); }
    }
  }

  async function backToInterview() {
    const f = F && F.from; if (!f) return;
    const St = X().State;
    if (St.patientId !== f.pid) { St.selectedIdx = parseInt(f.pid.slice(1), 10); await App.generateFromExisting(); }
    if (window.Interview) Interview.open();
  }

  // ---------------- entry ----------------
  function base(o) {
    if (F) { F.playing = false; clearTimeout(F.timer); }
    F = Object.assign({ turns: [], shown: 0, sel: 0, open: { a: false, b: false, both: false }, mem: {}, more: {}, refOpen: {}, readOpen: {}, playing: false, reveal: null, learningByN: null }, o);
    X().show("fc"); render(false);
  }
  const sentence = t => /^([A-Z][a-z]+)( [A-Za-z]+)+$/.test(t) && t.split(" ").filter(w => /^[A-Z]/.test(w)).length > 1 ? t.charAt(0) + t.slice(1).toLowerCase() : t;   // "Insomnia and Sleep Disruption" -> "Insomnia and sleep disruption"
  async function openRecorded(caseId) {
    let idx = {}, c;
    try { idx = await X().api("/api/ext/replay"); } catch (e) { X().toast("Examples unavailable: " + e.message); return; }
    const id = caseId || (idx.cases || []).find(x => x.kind === "formulation-contrast")?.case_id || idx.default;
    try { c = await X().api("/api/ext/replay/" + encodeURIComponent(id)); } catch (e) { X().toast("Example unavailable: " + e.message); return; }
    if (!c.turns.every(t => t.fc)) { X().toast("This example has no Formulation Contrast data yet."); return; }
    base({ mode: "recorded", key: "rec:" + id, pa: c.patient_a, pb: c.patient_b, names: c.names, focus: c.focus,
           presenting: sentence(c.note || c.focus_name || "the same problem"), turns: c.turns, reveal: c.reveal || null, learningByN: c.learning_by_n || null });
  }
  let statusHooked = false;
  async function openLive(a, b, probe, focus, opts) {
    opts = opts || {};
    let names = { a, b }, genders = {};
    const name = opts.presenting || "";
    try { const L = X().State.existing || [], pa = L.find(p => p.index === +a.slice(1)) || {}, pb = L.find(p => p.index === +b.slice(1)) || {};
      names = { a: pa.name || a, b: pb.name || b }; genders = { a: pa.gender || "", b: pb.gender || "" }; } catch (e) {}
    base({ mode: "live", key: "live:" + a + "|" + b + ":" + Date.now(), pa: a, pb: b, names, genders, focus, presenting: sentence(name || "the same problem"), fid: null, from: opts.from || null });
    // the shared concept's name for the header and pill; the first call for a new pair can take a while, so it never blocks the page
    const me = F;
    X().api(`/api/ext/fc/reveal/${a}/${b}` + (focus ? "?focus=" + encodeURIComponent(focus) : "")).then(rv => {
      if (F === me && rv && rv.name && !opts.presenting) { me.presenting = sentence(rv.name); header(); draw(false); } }).catch(() => {});
    X().apiT("/api/live/warm", {}, 10000).then(st => X().LiveStatus.set(st)).catch(() => {});
    if (!statusHooked) { statusHooked = true; X().LiveStatus.on(onStatus); }
    const cur = X().LiveStatus.current(); if (cur) onStatus(cur);
    // start the two patient sessions now (loads the model while the trainee reads), empty trajectory, input focused
    F.starting = X().api("/api/ext/fc/live/start", { patient_a: a, patient_b: b }).catch(e => { F.starting = null; throw e; });
    F.starting.catch(() => {});
    liveUI("idle"); fcErr(null);
    $("#fc-progress").classList.add("hidden");
    const q = $("#fc-q"); q.value = probe || ""; q.disabled = false; setTimeout(() => q.focus({ preventScroll: true }), 50);
    if (opts.replay && opts.replay.length) replayInterview(opts.replay);
  }
  function install() {
    $("#fc-ask").addEventListener("click", ask);
    $("#fc-stop").addEventListener("click", stopAsk);
    const h = $("#fc-hist");   // a manual scroll releases the pin; any resize re-pins the latest turn while pinned
    ["wheel", "touchmove", "keydown"].forEach(e => h.addEventListener(e, () => { if (F) F.stick = false; }, { passive: true }));
    if (window.ResizeObserver) { const ro = new ResizeObserver(() => { if (F && F.stick) showLatest(false); }); ro.observe(h); ro.observe($("#fc-live")); ro.observe($("#fc-recbar")); }
    $("#fc-try2").addEventListener("click", tryIt);   // the live footer grows with its status line
    $("#fc-q").addEventListener("keydown", ev => { if (ev.key === "Enter") { ev.preventDefault(); ask(); } });
    window.addEventListener("resize", () => { if (F && $("#screen-fc").classList.contains("active")) { fitHeight(); draw(false); } });
  }
  window.addEventListener("DOMContentLoaded", install);
  return { openRecorded, openLive, has: () => !!F, state: () => F, select, step, gaps, ask, stop: stopAsk };
})();
