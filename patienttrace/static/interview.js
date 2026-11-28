/* Interview page (port of docs/interview-page-reference.html, redesign 2, onto real data).
   Left: the patient's network at theme level (themes = the Network page's groups, as circles in the five
   CBT columns) and the explanation of one reply. Right: the transcript.
   Hovering a reply previews it; clicking pins it. The themes holding the nodes the reply expressed
   (active, eased, path ends) open into tinted cards listing ONLY those nodes; nodes slide out of the theme
   circle, the column re-flows, untouched themes fade; the reply's path draws link by link in CBT order.
   A ring around each theme shows how much of it has surfaced so far in the session.
   Per turn: spans / active / path / trigger / eased from the F1 alignment (enriched by
   /api/ext/interview/turn) and AI-generated guidance. Sources: a recorded session or live chat. */
window.Interview = (function () {
  const X = () => App._ext;
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const REDUCED = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const TYPES = [
    { t: "Stimulus", v: "--t-stimulus", lab: "Stimulus" }, { t: "Cognition", v: "--t-cognition", lab: "Cognition" },
    { t: "Emotion", v: "--t-emotion", lab: "Emotion" }, { t: "Physiological Sensation", v: "--t-physio", lab: "Body" },
    { t: "Behavior", v: "--t-behavior", lab: "Behavior" }];
  const COL = Object.fromEntries(TYPES.map((x, i) => [x.t, i]));
  const NS = "http://www.w3.org/2000/svg";
  const CX = TYPES.map((_, i) => 64 + i * 123), CW = 114, TOP = 34, ROW = 34, VW = 620;

  let S = null;          // session state

  // ---------------- labels ----------------
  function shortLabel(s) {
    s = String(s || "").replace(/^["“']|["”']$/g, "").trim();
    if (s.length <= 18) return s;
    const w = s.split(/\s+/); let out = "";
    for (const x of w) { if ((out + " " + x).trim().length > 16) break; out = (out + " " + x).trim(); }
    return (out || s.slice(0, 15)) + "…";
  }
  const lab = id => S.net.nodes[id] ? S.net.nodes[id].label : id;
  const shortOf = id => (S && S.short && S.short[id]) || shortLabel(lab(id));
  function wrap(s, max, lines = 2) {
    const out = []; let cur = "";
    String(s).split(/\s+/).forEach(x => { const t = cur ? cur + " " + x : x; if (t.length > max && cur) { out.push(cur); cur = x; } else cur = t; });
    if (cur) out.push(cur);
    if (out.length > lines) { out.length = lines; out[lines - 1] = out[lines - 1].replace(/[,.;:]?$/, "") + "…"; }
    return out;
  }

  // ---------------- network model: nodes, links, themes ----------------
  function buildNet(net, groupsByLabel) {
    const nodes = {}, ev = {}, deg = {};
    net.nodes.forEach(n => { nodes[n.id] = n; deg[n.id] = 0; });
    const edges = net.edges.filter(e => nodes[e.source] && nodes[e.target] && e.source !== e.target);
    edges.forEach(e => { ev[e.source + ">" + e.target] = e.evidence || e.label || ""; deg[e.source]++; deg[e.target]++; });
    // themes = the Network page's groups (members are labels there), mapped to node ids; ungrouped nodes are their own theme
    const idOf = {}; net.nodes.forEach(n => { idOf[n.label] = n.id; });
    const groups = [], themeOf = {};
    (groupsByLabel || []).forEach(g => { const m = (g.members || []).map(x => idOf[x]).filter(x => x && themeOf[x] == null);
      if (!m.length) return; m.forEach(x => { themeOf[x] = groups.length; }); groups.push({ label: g.label, type: g.type || nodes[m[0]].type, members: m }); });
    net.nodes.forEach(n => { if (themeOf[n.id] == null) { themeOf[n.id] = groups.length; groups.push({ label: shortLabel(n.label), type: n.type, members: [n.id] }); } });
    const gcol = groups.map(g => COL[g.type] ?? 2);
    const tAdj = groups.map(() => new Map()), tEdges = new Set(), tW = {};
    edges.forEach(e => { const a = themeOf[e.source], b = themeOf[e.target]; if (a === b) return;
      tEdges.add(a + "|" + b); tW[a + "|" + b] = (tW[a + "|" + b] || 0) + 1;
      tAdj[a].set(b, (tAdj[a].get(b) || 0) + 1); tAdj[b].set(a, (tAdj[b].get(a) || 0) + 1); });
    // within each column, order themes to keep linked themes near each other (barycenter sweeps)
    const colThemes = TYPES.map((_, c) => groups.map((_, i) => i).filter(i => gcol[i] === c));
    const rank = {}, rerank = () => colThemes.forEach(ts => ts.forEach((t, k) => { rank[t] = k / Math.max(1, ts.length - 1); }));
    rerank();
    for (let it = 0; it < 6; it++) { colThemes.forEach(ts => ts.sort((a, b) => {
      const m = t => { let s = 0, w = 0; tAdj[t].forEach((c, u) => { s += rank[u] * c; w += c; }); return w ? s / w : rank[t]; };
      return m(a) - m(b); })); rerank(); }
    return { nodes, ev, deg, edges, groups, themeOf, gcol, tEdges, tW, colThemes };
  }
  const tR = i => 7 + Math.sqrt(S.net.groups[i].members.length) * 1.8;
  const cvar = id => (TYPES[COL[S.net.nodes[id]?.type]] || TYPES[2]).v;
  const byFlow = (a, b) => (COL[S.net.nodes[a]?.type] ?? 9) - (COL[S.net.nodes[b]?.type] ?? 9);
  function loopEdges(path) {                   // edges of the path that lie on a cycle within the path
    const out = new Set();
    path.forEach(([a, b]) => { const seen = new Set([b]), st = [b];
      while (st.length) { const x = st.pop(); if (x === a) { out.add(a + ">" + b); break; }
        path.forEach(([p, q]) => { if (p === x && !seen.has(q)) { seen.add(q); st.push(q); } }); } });
    return out;
  }
  function layout(shown) {                     // targets for every theme (open card or circle) and every shown node
    const T = {}, P = {}; let maxY = 0;
    S.net.colThemes.forEach((ts, c) => { let y = TOP; const x = CX[c];
      ts.forEach(i => { const ns = shown.get(i), L = S.net.groups[i].label;
        if (ns && !ns.length && S.themeOnly && S.themeOnly.has(i)) {
          const head = wrap(L, 15).length > 1 ? 40 : 28;
          T[i] = { x, y, h: head + ROW, o: 1, tm: true };
        } else if (ns && ns.length) {
          const head = wrap(L, 15).length > 1 ? 40 : 28;
          T[i] = { x, y, h: head + ns.length * ROW + 4, o: 1 };
          ns.forEach((n, k) => { P[n] = { x: x - CW / 2 + 14, y: y + head + 8 + k * ROW }; });
        } else T[i] = { x, y, h: 34 + 12.5 * wrap(L, 17).length, o: 0 };
        y += T[i].h + 8; });
      maxY = Math.max(maxY, y); });
    return { T, P, H: Math.max(290, maxY + 6) };
  }
  const circlePos = (s, i) => ({ x: s.x + (-CW / 2 + 14) * s.o, y: s.y + 14, r: tR(i) + (6.5 - tR(i)) * s.o });
  function curve(p, q) {
    if (q.x > p.x + 5) { const d = (q.x - p.x) / 2; return `M${p.x},${p.y} C${p.x + d},${p.y} ${q.x - d},${q.y} ${q.x},${q.y}`; }
    if (Math.abs(q.x - p.x) <= 5) { const k = 28 + Math.abs(q.y - p.y) * 0.2; return `M${p.x},${p.y} C${p.x - k},${p.y} ${q.x - k},${q.y} ${q.x},${q.y}`; }
    // feedback links arc over the cards: control points chosen so the peak clears the higher card top
    const peak = Math.min(p.top ?? p.y - 20, q.top ?? q.y - 20) - 10, lift = Math.max(-10, (peak - (p.y + q.y) / 8) / 0.75);
    return `M${p.x},${p.y} C${p.x + 26},${lift} ${q.x - 26},${lift} ${q.x},${q.y}`;
  }
  const sv = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent && parent.appendChild(e); return e; };

  // ---------------- drawing (built once per patient; frame() eases everything) ----------------
  function drawNet() {
    const svg = $("#iv-net"); svg.innerHTML = "";
    svg.setAttribute("aria-label", `${S.name}'s symptom network by theme; themes touched by the selected reply open to show the nodes expressed`);
    const defs = sv("defs", {}, svg);
    TYPES.map((x, i) => ({ id: "iv-ar-" + i, v: x.v })).concat([{ id: "iv-ar-acc", v: "--accent" }]).forEach(m => {
      const mk = sv("marker", { id: m.id, viewBox: "0 0 8 8", markerWidth: 7, markerHeight: 7, refX: 13, refY: 4, orient: "auto", markerUnits: "userSpaceOnUse" }, defs);
      sv("path", { d: "M0,0 L8,4 L0,8 z", style: `fill:var(${m.v})` }, mk); });
    TYPES.forEach((x, i) => { sv("text", { x: CX[i], y: 16, class: "iv-colh", style: `fill:var(${x.v})` }, svg).textContent = x.lab + (i < 4 ? " →" : ""); });
    const gTE = sv("g", {}, svg), gCards = sv("g", {}, svg); S.gFx = sv("g", {}, svg); S.gTop = sv("g", {}, svg);
    S.TH = S.net.groups.map((g, i) => {
      const col = `var(${TYPES[S.net.gcol[i]].v})`;
      const card = sv("rect", { class: "iv-th-card", rx: 10, width: CW, style: `fill:color-mix(in srgb, ${col} 11%, transparent);stroke:color-mix(in srgb, ${col} 45%, transparent)` }, gCards);
      const grp = sv("g", { class: "iv-th", "data-theme": i }, S.gTop);
      const ringBg = sv("circle", { class: "ring-bg" }, grp), ring = sv("circle", { class: "ring", pathLength: 1, style: `stroke:${col}`, "stroke-dasharray": "0 1" }, grp);
      const dot = sv("circle", { style: `fill:${col}` }, grp);
      sv("title", {}, dot).textContent = `${g.label}: ${g.members.length} node${g.members.length === 1 ? "" : "s"}`;
      const labC = sv("text", { class: "iv-th-lab c" }, grp), labO = sv("text", { class: "iv-th-lab" }, grp);
      const tmT = sv("text", { class: "iv-th-tm" }, grp); tmT.textContent = "theme-level match";
      grp.addEventListener("mouseenter", () => { S.hoverTheme = i; }); grp.addEventListener("mouseleave", () => { if (S.hoverTheme === i) S.hoverTheme = null; });
      wrap(g.label + (g.members.length > 1 ? ` (${g.members.length})` : ""), 17).forEach((l, k) => { sv("tspan", { x: 0, dy: k ? 12.5 : 0 }, labC).textContent = l; });
      wrap(g.label, 15).forEach((l, k) => { sv("tspan", { x: 0, dy: k ? 12 : 0 }, labO).textContent = l; });
      return { card, grp, ring, ringBg, dot, labC, labO, tmT, cur: null, tgt: null, dim: 1, dimT: 1, seen: 0 };
    });
    S.TE = [...S.net.tEdges].map(k => { const [a, b] = k.split("|").map(Number), w = S.net.tW[k] || 1;
      return { a, b, w, p: sv("path", { class: "iv-te", "stroke-width": (0.8 + 0.45 * Math.min(w, 6)).toFixed(2) }, gTE) }; });
    S.NODES = {}; S.FX = []; S.H = 300; S.Ht = 300; S.current = null;
    $("#iv-legend").innerHTML = TYPES.map(x => `<span><i style="--c:var(${x.v})"></i>${x.lab}</span>`).join("")
      + `<span><i class="ring-k"></i>Share of theme surfaced</span><span><i class="ez-k"></i>Eased</span>`;
    apply(new Map(), new Set(), true);
    cancelAnimationFrame(S.raf); S.raf = requestAnimationFrame(frame);
  }
  function nodeEl(n) {
    const col = `var(${cvar(n)})`, g = sv("g", { class: "iv-nd", "data-n": n, style: `--c:${col}` }, S.gTop);
    sv("title", {}, g).textContent = lab(n);
    sv("circle", { class: "halo", r: 7, style: `stroke:${col}` }, g);
    sv("circle", { class: "dot", r: 5.5, style: `fill:${col}` }, g);
    const t = sv("text", { class: "lb", x: 12, y: 4 }, g);
    // fits the card (about 14 characters a line): long single words are hyphenated
    const words = shortOf(n).split(/\s+/).flatMap(w => w.length > 14 ? (w.includes("-") ? w.replace("-", "-\u0000").split("\u0000") : [w.slice(0, 12) + "-", w.slice(12)]) : [w]);
    wrap(words.join(" ").replace(/- /g, "-\u00a0"), 14).map(l => l.replace(/-\u00a0/g, "-")).forEach((l, k) => { sv("tspan", { x: 12, dy: k ? 12 : 0 }, t).textContent = l; });
    return g;
  }
  const lerp = (a, b, k) => a + (b - a) * k, easeIO = t => t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
  function apply(shown, seenSet, snap) {
    const L = layout(shown), anyOpen = shown.size > 0; S.Ht = L.H;
    S.TH.forEach((T, i) => { T.tgt = L.T[i]; if (!T.cur || snap) T.cur = { ...L.T[i] };
      T.dimT = anyOpen && !shown.has(i) ? 0.5 : 1;
      const m = S.net.groups[i].members; T.seen = m.filter(x => seenSet.has(x)).length / m.length;
      T.ring.setAttribute("stroke-dasharray", `${T.seen} 1`); });
    Object.entries(S.NODES).forEach(([n, N]) => { if (!L.P[n]) { N.dead = true; const i = S.net.themeOf[n], c = circlePos(S.TH[i].tgt, i); N.tgt = { x: c.x, y: c.y, a: 0 }; } });
    Object.entries(L.P).forEach(([n, p]) => {
      let N = S.NODES[n];
      if (!N || N.dead) { if (N) N.g.remove(); const i = S.net.themeOf[n], c = circlePos(S.TH[i].cur, i);
        N = S.NODES[n] = { g: nodeEl(n), cur: { x: c.x, y: c.y, a: 0 } }; }      // slides out of its theme circle
      N.dead = false; N.tgt = { ...p, a: 1 }; if (snap) N.cur = { ...N.tgt };
    });
    if (snap) S.H = S.Ht;
  }
  // links leave a node from the right edge of its card (never across its label) and enter at the target dot
  function endPos(n, src) {
    const i = S.net.themeOf[n], N = S.NODES[n], c = S.TH[i].cur;
    if (N && !N.dead) return { x: src ? c.x + CW / 2 - 4 : N.cur.x, y: N.cur.y, top: c.y };
    const p = circlePos(c, i); return { x: p.x, y: p.y, top: p.y - p.r };
  }
  function frame(now) {                        // one loop eases themes, nodes, height and redraws every link
    if (!S || !S.TH) return;
    S.raf = requestAnimationFrame(frame);
    if (!$("#screen-interview").classList.contains("active")) return;
    const k = REDUCED() ? 1 : 0.16, svg = $("#iv-net");
    if (Math.abs(S.H - S.Ht) > 0.05 || !svg.getAttribute("viewBox")) { S.H = lerp(S.H, S.Ht, k); svg.setAttribute("viewBox", `0 0 ${VW} ${S.H.toFixed(1)}`); }
    S.TH.forEach((T, i) => { const c = T.cur, t = T.tgt; ["x", "y", "h", "o"].forEach(p => { c[p] = lerp(c[p], t[p], k); }); T.dim = lerp(T.dim, T.dimT, k);
      T.card.setAttribute("x", c.x - CW / 2); T.card.setAttribute("y", c.y); T.card.setAttribute("height", Math.max(0, c.h - 2));
      const p = circlePos(c, i);
      [T.dot, T.ring, T.ringBg].forEach((e, j) => { e.setAttribute("cx", p.x); e.setAttribute("cy", p.y); e.setAttribute("r", j ? p.r + 3.5 : p.r); });
      T.ring.setAttribute("transform", `rotate(-90 ${p.x} ${p.y})`);
      T.labC.setAttribute("transform", `translate(${c.x},${c.y + 14 + tR(i) + 15})`); T.labC.style.opacity = 1 - c.o;
      T.labO.setAttribute("transform", `translate(${p.x + 12},${c.y + 18})`); T.labO.style.opacity = c.o;
      T.grp.style.opacity = T.dim; T.card.style.opacity = c.o * T.dim;
      T.card.classList.toggle("tm", !!t.tm); T.tmT.setAttribute("transform", `translate(${c.x - CW / 2 + 14},${c.y + (t.tm ? c.h - 14 : 0)})`); T.tmT.style.opacity = t.tm ? c.o : 0; });
    Object.entries(S.NODES).forEach(([n, N]) => { const c = N.cur, t = N.tgt; c.x = lerp(c.x, t.x, k); c.y = lerp(c.y, t.y, k); c.a = lerp(c.a, t.a, REDUCED() ? 1 : 0.2);
      if (N.dead && c.a < 0.03) { N.g.remove(); delete S.NODES[n]; return; }
      N.g.setAttribute("transform", `translate(${c.x},${c.y})`); N.g.style.opacity = c.a;
      if (N.onAt != null && now >= N.onAt) { N.g.classList.add("on"); N.onAt = null; } });
    const anyOpen = S.TH.some(T => T.tgt.o > 0);
    const ht = S.hoverTheme;
    S.TE.forEach(E => { const a = circlePos(S.TH[E.a].cur, E.a), b = circlePos(S.TH[E.b].cur, E.b);
      E.p.setAttribute("d", curve(a, b));
      const hot = ht != null && (E.a === ht || E.b === ht);          // resting ~0.2; the hovered theme's links light up
      E.p.style.opacity = hot ? 0.85 : (anyOpen ? 0.12 : 0.2) * Math.min(S.TH[E.a].dim, S.TH[E.b].dim);
      E.p.classList.toggle("hot", hot); });
    S.FX.forEach(F => { F.p.setAttribute("d", curve(endPos(F.a, true), endPos(F.b, false)));
      if (F.done) return;
      const pr = Math.max(0, Math.min(1, (now - F.t0) / 450));
      F.p.style.strokeDashoffset = 1 - easeIO(pr); F.p.style.opacity = pr > 0 ? 1 : 0;
      if (pr >= 1) { F.done = true; F.p.style.strokeDasharray = ""; F.p.style.strokeDashoffset = ""; if (F.loop) F.p.classList.add("flow"); } });
  }

  // ---------------- one reply on the network ----------------
  const pIdx = () => S.turns.map((t, i) => t.who === "p" ? i : -1).filter(i => i >= 0);
  function seenUpTo(idx) { const seen = new Set(); pIdx().filter(i => i <= idx).forEach(i => (S.turns[i].active || []).forEach(n => seen.add(n))); return seen; }
  function showTurn(idx, opts = {}) {
    const t = S.turns[idx], now = performance.now(), draw = opts.draw && !REDUCED();
    const seen = seenUpTo(idx), active = (t.active || []).filter(n => S.net.nodes[n]), eased = (t.eased || []).filter(n => S.net.nodes[n]);
    const themeHits = themeMatches(t);
    const trigN = t.trigger && S.net.nodes[t.trigger.node] ? t.trigger.node : null;
    $("#iv-net").classList.remove("stale");
    if (!active.length && !themeHits.length && !eased.length && !trigN) {   // nothing matched: show nothing, never the previous reply
      showEmpty(); S.current = idx; coverage(seen);
      $("#iv-netcap").innerHTML = `Reply ${pIdx().indexOf(idx) + 1} · No network node matched this reply <button type="button" class="ghost small iv-suggest">Suggest nodes</button>`;
      $("#iv-netcap .iv-suggest").onclick = () => suggest(idx);
      return;
    }
    S.current = idx;                          // (a reply with only a trigger shows that trigger's theme and node)
    const path = (t.path || []).filter(([a, b]) => S.net.nodes[a] && S.net.nodes[b]).slice().sort((a, b) => byFlow(a[0], b[0]) || byFlow(a[1], b[1]));
    const order = []; path.forEach(([a, b]) => [a, b].forEach(n => { if (!order.includes(n)) order.push(n); }));
    const vis = [...new Set([...(trigN ? [trigN] : []), ...order, ...active, ...eased])];
    const shown = new Map(); vis.forEach(n => { const i = S.net.themeOf[n]; if (i == null) return; if (!shown.has(i)) shown.set(i, []); shown.get(i).push(n); });
    S.themeOnly = new Set(themeHits.filter(i => !shown.has(i))); S.themeOnly.forEach(i => shown.set(i, []));
    apply(shown, seen, !!opts.snap);
    const STEP = 380, START = draw ? 420 : 0, appear = {};
    path.forEach(([a, b], i) => { appear[a] = Math.min(appear[a] ?? Infinity, START + i * STEP); appear[b] = Math.min(appear[b] ?? Infinity, START + i * STEP + STEP * 0.8); });
    const kinds = t.kinds || {};
    Object.entries(S.NODES).forEach(([n, N]) => { N.g.classList.remove("on", "ez", "hl", "im", "tg"); N.onAt = null;
      if (kinds[n] === "implied") N.g.classList.add("im");                // implied: hollow
      if (n === trigN && !active.includes(n)) N.g.classList.add("tg");
      if (active.includes(n)) N.onAt = now + (draw ? (appear[n] ?? START) : 0);         // pulses when its first link arrives
      if (eased.includes(n)) N.g.classList.add("ez"); });
    S.gFx.innerHTML = ""; const loops = loopEdges(path);
    S.FX = path.map(([a, b], i) => { const key = a + ">" + b, loop = loops.has(key);
      const p = sv("path", { class: "iv-fx", pathLength: 1, style: `stroke:var(${loop ? "--accent" : cvar(a)});stroke-dasharray:1 1;stroke-dashoffset:1;opacity:0`,
        "marker-end": `url(#iv-ar-${loop ? "acc" : COL[S.net.nodes[a].type]})` }, S.gFx);
      sv("title", {}, p).textContent = `${lab(a)} → ${lab(b)}\n${S.net.ev[key] || ""}`;
      return { a, b, p, loop, t0: draw ? now + START + i * STEP : -1e9, done: false }; });
    const TL = t.trigger_link;                                          // trigger with no network edge: dotted link
    if (TL && S.net.nodes[TL.from] && S.net.nodes[TL.to]) {
      const p = sv("path", { class: "iv-fx iv-trig-link", pathLength: 1, style: `stroke:var(${cvar(TL.from)})` , "marker-end": `url(#iv-ar-${COL[S.net.nodes[TL.from].type]})` }, S.gFx);
      sv("title", {}, p).textContent = `Trigger: ${lab(TL.from)} → ${lab(TL.to)} (not a link in the network)`;
      S.FX.push({ a: TL.from, b: TL.to, p, loop: false, t0: -1e9, done: true });
    }
    $("#iv-netcap").innerHTML = `Reply ${pIdx().indexOf(idx) + 1} · ${shown.size} theme${shown.size === 1 ? "" : "s"} open${S.themeOnly.size ? " · theme-level match" : ""}${loops.size ? " · <b>maintaining loop</b>" : ""}${eased.length ? " · <b>easing</b>" : ""}`;
    coverage(seen);
  }
  function themeMatches(t) {                  // backend theme matches (by members) -> our theme indices
    return (t.themes || []).map(m => { const ms = (m.members || []).filter(x => S.net.nodes[x]); return ms.length ? S.net.themeOf[ms[0]] : null; }).filter(i => i != null);
  }
  async function suggest(idx) {                // second, more inclusive aligner pass for a reply with no match
    const t = S.turns[idx];
    let s2 = t.suggested && !t.suggested.empty ? t.suggested : null;
    if (!s2 && t.suggested && t.suggested.empty) { X().toast("No plausible node or theme for this reply, even with a lower threshold."); return; }
    if (!s2) {
      try { const k = idx, trainee = S.turns[k - 1] && S.turns[k - 1].who === "t" ? S.turns[k - 1].text : "";
        s2 = await X().api("/api/ext/interview/turn", { patient_id: S.pid, trainee, reply: t.text, level: "low",
          history: S.turns.slice(Math.max(0, k - 5), k - 1).map(x => ({ who: x.who, text: x.text })), earlier: [...seenUpTo(k - 1)] }); }
      catch (e) { X().toast("Suggest nodes needs the language model (live mode): " + (e.message || e)); return; }
      if (!s2.active.length && !s2.themes.length) { t.suggested = { empty: true }; X().toast("No plausible node or theme for this reply, even with a lower threshold."); return; }
    }
    S.turns[idx] = Object.assign({ who: "p", text: t.text, suggestedApplied: true }, s2);
    renderMsg(idx, document.querySelector(`#iv-log [data-i="${idx}"]`));
    pin(idx, { force: true });
  }
  function showEmpty() { if (!S.TH) return; S.current = null; S.gFx.innerHTML = ""; S.FX = []; apply(new Map(), new Set(), false);
    $("#iv-netcap").textContent = ""; coverage(new Set()); }
  function coverage(seen) {
    const all = Object.keys(S.net.nodes), un = all.filter(n => !seen.has(n) && S.net.deg[n] > 0).sort((a, b) => S.net.deg[b] - S.net.deg[a]).slice(0, 4);
    $("#iv-cov").textContent = `${seen.size} of ${all.length} network nodes surfaced so far`;
    $("#iv-unexp").innerHTML = un.length ? `Not explored yet: <b>${un.map(n => `<span title="${esc(lab(n))}">${esc(shortOf(n))}</span>`).join(", ")}</b>` : "";
  }

  // ---------------- transcript ----------------
  function markText(t) {
    const spans = (t.spans || []).slice().sort((a, b) => a.start - b.start), ez = new Set(t.eased || []);
    let out = "", p0 = 0;
    spans.forEach(s => { if (s.start < p0 || !S.net.nodes[s.node]) return;
      out += esc(t.text.slice(p0, s.start)) + `<mark class="iv-sp${ez.has(s.node) ? " ez" : ""}" style="--c:var(${ez.has(s.node) ? "--muted" : cvar(s.node)})" data-n="${esc(s.node)}">${esc(t.text.slice(s.start, s.end))}</mark>`;
      p0 = s.end; });
    return out + esc(t.text.slice(p0));
  }
  function trainerHTML(i) {                    // the phrase that set off the next reply is dotted
    const t = S.turns[i], nxt = S.turns[i + 1], trig = nxt && nxt.who === "p" && nxt.trigger;
    if (!trig || !trig.phrase) return esc(t.text);
    const k = t.text.toLowerCase().indexOf(trig.phrase.toLowerCase());
    if (k < 0) return esc(t.text);
    return esc(t.text.slice(0, k)) + `<span class="iv-trig-word" title="This phrase set off: ${esc(lab(trig.node))}">${esc(t.text.slice(k, k + trig.phrase.length))}</span>` + esc(t.text.slice(k + trig.phrase.length));
  }
  const titleOf = t => (t.guidance && t.guidance.title) || "Reply";
  function renderMsg(i, replace) {
    const t = S.turns[i], li = document.createElement("li");
    li.className = "iv-msg " + t.who; li.dataset.i = i;
    if (t.who === "t") li.innerHTML = `<div class="who-l">Trainee</div><div class="bubble">${trainerHTML(i)}</div>`;
    else {
      const types = [...new Set((t.active || []).map(n => S.net.nodes[n]?.type).filter(Boolean))].sort((a, b) => COL[a] - COL[b]);
      const loop = loopEdges(t.path || []).size > 0, pending = t.pending;
      li.innerHTML = `<div class="who-l">${esc(S.name)}</div><div class="bubble" tabindex="0" role="button" aria-label="Show what's happening in this reply">${t.spans ? markText(t) : esc(t.text)}</div>
        <div class="dots">${types.map(x => `<i style="--c:var(${TYPES[COL[x]].v})" title="${esc(TYPES[COL[x]].lab)}"></i>`).join("")}
        <span class="tag${loop ? " loop" : ""}">${t.streaming ? "" : pending ? "analysing…" : t.analysisError ? "not analysed" : esc(titleOf(t)) + (loop ? " · loop" : "") + ((t.eased || []).length ? " · easing" : "")}</span>${!pending && t.analysisError ? `<button type="button" class="iv-retry-an" title="${esc(t.analysisError)}">Retry analysis</button>` : ""}</div>`;
      const b = li.querySelector(".bubble");
      const ra = li.querySelector(".iv-retry-an");
      if (ra) ra.addEventListener("click", () => { const q = (S.turns[i - 1] && S.turns[i - 1].who === "t") ? S.turns[i - 1].text : (t.trainee || "");
        analyze(i, q); renderMsg(i, document.querySelector(`#iv-log [data-i="${i}"]`)); });
      if (!pending && !t.analysisError) {
        b.addEventListener("mouseenter", () => preview(i));
        b.addEventListener("mouseleave", unpreview);
        b.addEventListener("click", () => pin(i));
        b.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pin(i); } });
        li.querySelectorAll(".iv-sp").forEach(m => { m.addEventListener("mouseenter", () => S.NODES[m.dataset.n]?.g.classList.add("hl"));
          m.addEventListener("mouseleave", () => S.NODES[m.dataset.n]?.g.classList.remove("hl")); });
      }
    }
    if (replace) { li.classList.add("no-rise"); if (replace.classList.contains("pinned")) li.classList.add("pinned"); replace.replaceWith(li); }   // in-place update: no second rise-in
    else $("#iv-log").appendChild(li);
    return li;
  }

  // ---------------- explanation panel: previews on hover, stays when pinned ----------------
  const pron = () => S.gender === "male" ? "He" : S.gender === "female" ? "She" : "They";
  const chip = (n, ez, im) => `<span class="iv-chip${ez ? " ez" : ""}${im ? " im" : ""}" style="--c:var(${cvar(n)})" title="${esc(lab(n))}"><i></i>${esc(shortOf(n))}</span>`;
  const fullChip = n => `<span class="iv-chip wrap" style="--c:var(${cvar(n)})" title="${esc(lab(n))}"><i></i>${esc((S.short && S.short[n]) || lab(n))}</span>`;
  const AI = g => `<div class="iv-ai" title="Written by a language model from the profile, the network and this reply; check it against your own judgement">AI-generated guidance${g && g.model ? " · " + esc(g.model) : ""}</div>`;
  function chainHTML(t) {
    const quote = n => ((t.spans || []).find(s => s.node === n) || {}).text;
    const K = t.kinds || {}, C = t.confidence || {};
    const tm = themeMatches(t).map(i => `<li><span class="iv-chip tmc" style="--c:var(${TYPES[S.net.gcol[i]].v})"><i></i>${esc(S.net.groups[i].label)}</span><span class="q">theme-level match: no single node fitted</span></li>`).join("");
    return `<ol class="iv-chain">${tm}${(t.active || []).filter(n => S.net.nodes[n]).slice().sort(byFlow).map(n => `<li>${chip(n, false, K[n] === "implied")}<span class="q">${quote(n) ? `“<em>${esc(quote(n))}</em>”`
        : K[n] === "implied" ? `<span class="likely">likely</span>, implied${C[n] != null ? ` (${Math.round(C[n] * 100)}%)` : ""}`
        : t.trigger && n === t.trigger.node ? "set off by your question" : S.net.nodes[n]?.type === "Stimulus" ? "background" : "implied, not said"}</span></li>`).join("")}
      ${(t.eased || []).filter(n => S.net.nodes[n]).map(n => `<li>${chip(n, true)}<span class="q">eased${quote(n) ? `: “<em>${esc(quote(n))}</em>”` : ""}</span></li>`).join("")}</ol>`;
  }
  function renderDetail(i, isPreview) {
    const t = S.turns[i], g = t.guidance || {}, path = t.path || [], loops = loopEdges(path), P = pIdx(), k = P.indexOf(i), d = $("#iv-detail");
    d.classList.remove("swap"); void d.offsetWidth; d.classList.add("swap"); d.classList.toggle("preview", !!isPreview);
    const any = (t.active || []).length || (t.eased || []).length || themeMatches(t).length;
    d.innerHTML = `<div><div class="eyebrow">Reply ${k + 1} of ${P.length} · ${isPreview ? "<b>previewing, click to pin</b>" : "pinned"}</div><h3>${esc(titleOf(t))}</h3></div>
      ${t.trigger && S.net.nodes[t.trigger.node] ? `<div class="iv-trig">Triggered by <q>${esc(t.trigger.phrase)}</q> → ${fullChip(t.trigger.node)}${t.trigger_link ? `<span class="muted small">(no direct link in the network: dotted)</span>` : ""}</div>` : ""}
      <div><h4>What ${pron().toLowerCase()} expresses</h4>${any ? chainHTML(t) : `<p class="muted">No network node matched this reply. <button type="button" class="ghost small iv-suggest2">Suggest nodes</button></p>`}
        ${t.suggestedApplied ? `<p class="muted small">Suggested with a lower threshold: treat as tentative.</p>` : ""}</div>
      ${loops.size ? `<div class="iv-loopnote">Maintaining loop (moving blue links): these reactions feed each other, so the cycle keeps itself going.</div>` : ""}
      ${g.surface || g.under ? `<div class="iv-two"><div><h4>${pron()} shows</h4><p>${esc(g.surface)}</p></div><div><h4>Underneath</h4><p>${esc(g.under)}</p></div></div>` : ""}
      ${g.tip || g.instead ? `<div class="iv-tip"><h4>For the trainee</h4>${g.tip ? `<p>${esc(g.tip)}</p>` : ""}${g.instead ? `<p class="try"><b>You could try:</b> “${esc(g.instead)}”</p>` : ""}${AI(g)}</div>`
        : g.error ? `<p class="muted">Guidance unavailable (${esc(g.error)}).</p>` : ""}
      ${path.length ? `<details class="iv-ev-w"><summary>Why these links (${path.length}, from ${pron() === "They" ? "their" : pron() === "He" ? "his" : "her"} profile)</summary><ul class="iv-ev">${path.map(([a, b]) => `<li><span class="arr">${esc(shortOf(a))} → ${esc(shortOf(b))}</span>: ${esc(S.net.ev[a + ">" + b] || "")}</li>`).join("")}</ul></details>` : ""}
      ${isPreview ? "" : `<div class="iv-nav"><button type="button" class="ghost small" id="iv-prev">← Previous reply</button><button type="button" class="ghost small" id="iv-next">Next reply →</button></div>`}`;
    const sg = d.querySelector(".iv-suggest2"); if (sg) sg.onclick = () => suggest(i);
    if (isPreview) return;
    $("#iv-prev").disabled = k <= 0;
    $("#iv-next").disabled = k >= P.length - 1 || !document.querySelector(`#iv-log [data-i="${P[k + 1]}"]`) || S.turns[P[k + 1]].pending;
    $("#iv-prev").onclick = () => { pin(P[k - 1]); scrollToMsg(P[k - 1]); };
    $("#iv-next").onclick = () => { pin(P[k + 1]); scrollToMsg(P[k + 1]); };
  }
  let hT = null, lT = null, previewing = null;
  const canHover = () => matchMedia("(hover: hover)").matches;
  // scroll the transcript only (scrollIntoView would also scroll the page and push the header off screen)
  function logScrollTo(el, smooth) {
    const log = $("#iv-log"); if (!el || !log) return;
    const top = el.offsetParent === log ? el.offsetTop : el.offsetTop - log.offsetTop, bottom = top + el.offsetHeight;
    if (bottom > log.scrollTop + log.clientHeight) log.scrollTo({ top: bottom - log.clientHeight + 8, behavior: smooth && !REDUCED() ? "smooth" : "auto" });
    else if (top < log.scrollTop) log.scrollTo({ top: Math.max(0, top - 8), behavior: smooth && !REDUCED() ? "smooth" : "auto" });
  }
  const scrollToMsg = i => logScrollTo(document.querySelector(`#iv-log [data-i="${i}"]`), true);
  function pin(i, opts = {}) {
    clearTimeout(hT); clearTimeout(lT);
    S.pinned = i; previewing = null;
    document.querySelectorAll("#iv-log .iv-msg.p").forEach(m => m.classList.toggle("pinned", +m.dataset.i === i));
    renderDetail(i, false);
    if (S.current !== i || opts.snap || opts.force) showTurn(i, { draw: !opts.snap, snap: opts.snap });
  }
  function preview(i) {
    if (!canHover() || S.playing) return;
    clearTimeout(lT); clearTimeout(hT);
    hT = setTimeout(() => { if (previewing === i) return; previewing = i;
      renderDetail(i, i !== S.pinned); if (S.current !== i) showTurn(i, { draw: true }); }, 150);
  }
  function unpreview() {
    clearTimeout(hT); clearTimeout(lT);
    lT = setTimeout(() => { if (previewing != null && S.pinned != null && previewing !== S.pinned) { renderDetail(S.pinned, false); showTurn(S.pinned, { draw: false }); }
      previewing = null; }, 260);
  }

  // ---------------- replay ----------------
  let runId = 0;
  const sleep = ms => new Promise(r => setTimeout(r, REDUCED() ? 0 : ms));
  function typing() { const ty = document.createElement("li"); ty.className = "iv-msg p iv-typing";
    ty.innerHTML = `<div class="who-l">${esc(S.name)}</div><div class="bubble"><i></i><i></i><i></i></div>`; $("#iv-log").appendChild(ty); logScrollTo(ty, false); return ty; }
  async function replay() {
    if (!S.recorded || !S.example) return;
    const id = ++runId; S.playing = true; setPlay();
    S.turns = JSON.parse(JSON.stringify(S.recorded.turns));
    $("#iv-log").innerHTML = ""; S.pinned = null; showEmpty();
    $("#iv-detail").classList.remove("preview");
    $("#iv-detail").innerHTML = `<div class="eyebrow">Replaying</div><p>Each reply opens the themes it touches and lights the nodes ${esc(S.name)} expressed.</p>`;
    for (let i = 0; i < S.turns.length; i++) {
      if (id !== runId) return;
      if (S.turns[i].who === "p") {
        const ty = typing(); await sleep(900 + Math.min(1600, S.turns[i].text.length * 6)); if (id !== runId) return; ty.remove();
        renderMsg(i); scrollToMsg(i); pin(i);
        await sleep(1800 + (S.turns[i].path || []).length * 380);
      } else { renderMsg(i); scrollToMsg(i); await sleep(700); }
    }
    S.playing = false; setPlay();
  }
  function stopReplay() { runId++; S.playing = false; showAll(); setPlay(); }
  // ---- the recorded example is a separate mode: the trainee's own live session is kept and restored ----
  function enterExample() {
    if (!S.recorded || S.example) return;
    if (S.stream) { X().toast("Wait for the current reply, or press Stop."); return; }
    S.saved = { turns: S.turns, pinned: S.pinned }; S.example = true; S.pinned = null;
    S.turns = JSON.parse(JSON.stringify(S.recorded.turns));
    header(); replay();
  }
  function exitExample() {
    if (!S.example) return;
    runId++; S.playing = false; S.example = false;
    S.turns = (S.saved && S.saved.turns) || []; S.pinned = null; S.saved = null;
    header(); showAll(); if (S.live) $("#iv-draft").focus();
  }
  function setPlay() {
    const b = $("#iv-play"); if (!b) return; b.hidden = !S.recorded;
    b.querySelector("span").textContent = S.example ? "Exit example" : "Replay recorded example";
    b.classList.toggle("exit", !!S.example);
    $("#iv-exbadge").classList.toggle("hidden", !S.example);
  }
  function header() {
    const r = S.recorded;
    $("#iv-sub").textContent = S.example ? "Recorded example: scripted questions, replies from the patient model. Not a real person."
      : S.live ? "Live session with the patient model. Not a real person." : "Live chat is off on this server. Not a real person.";
    setPlay(); hint(); fitPage();
  }
  function hint() {                         // one line above the input while the trainee's own session is empty
    const h = $("#iv-hint"), show = S.live && S.model !== "down" && !S.example && !S.turns.length;
    h.classList.toggle("hidden", !show);
    h.textContent = show ? `Start the interview: greet ${S.name} and ask what brings them here.` : "";
  }
  // ---- the trainee's live session, per patient: in memory while the page is open, and in this browser ----
  const LSK = pid => "iv-live:" + pid;
  function saveLocal(me) {
    me = me || S; if (!me) return;
    const turns = (me.example ? (me.saved && me.saved.turns) : me.turns) || [];
    try { localStorage.setItem(LSK(me.pid), JSON.stringify({ sid: me.sid, turns: turns.filter(t => !t.pending && !t.streaming) })); } catch (e) {}
  }
  function liveQA(pid) {                    // [{q, a}] the trainee asked this patient live (for Formulation Contrast)
    let turns = null; const me = SESS[pid];
    if (me) turns = me.example ? (me.saved && me.saved.turns) : me.turns;
    if (!turns) { try { turns = (JSON.parse(localStorage.getItem(LSK(pid)) || "null") || {}).turns; } catch (e) {} }
    const out = []; (turns || []).forEach((t, i) => { const n = turns[i + 1]; if (t.who === "t" && n && n.who === "p" && !n.pending && !n.streaming && n.text) out.push({ q: t.text, a: n.text }); });
    return out;
  }
  function showAll() {
    $("#iv-log").innerHTML = ""; S.turns.forEach((_, i) => renderMsg(i));
    const P = pIdx();
    if (P.length) {                         // the example opens on its second reply; the trainee's own session on its latest
      const i = S.example ? P[Math.min(1, P.length - 1)] : P[P.length - 1];
      pin(i, { snap: true }); if (!S.example) scrollToMsg(S.turns.length - 1);
    }
    else { showEmpty(); $("#iv-detail").classList.remove("preview");
      $("#iv-detail").innerHTML = `<div class="eyebrow">Explanation</div><p>Hover or click a reply to see what's happening.</p>`;
      if (!S.live && !S.recorded) emptyHelp(); }
    hint();
  }

  async function emptyHelp() {               // no live model and no recording: say so and offer the recorded patients
    const rec = (X().State.recorded || []).filter(r => r.index !== S.idx);
    $("#iv-detail").innerHTML = `<div class="eyebrow">No interview to show</div>
      <p>Live chat is off on this server (no patient model / API key), and ${esc(S.name)} has no recorded interview.
      The network above still shows ${esc(S.name)}'s themes.</p>
      ${rec.length ? `<p>Recorded interviews exist for:</p><div class="iv-nav">${rec.map(r => `<button type="button" class="ghost small iv-open-rec" data-i="${r.index}">Open ${esc(r.name)}'s interview</button>`).join("")}</div>` : ""}`;
    document.querySelectorAll(".iv-open-rec").forEach(b => b.addEventListener("click", async () => {
      X().State.selectedIdx = +b.dataset.i; await App.generateFromExisting(); App.goChat(); }));
  }

  // ---------------- live: streamed reply -> alignment -> enrichment -> the same animation as the replay ----------------
  // The composer is free again as soon as the reply has finished streaming; the analysis of that reply runs
  // in the background ("Analyzing…" in the left panel) and pins the reply when it lands. A failed or stopped
  // turn puts the question back into the input, with an inline error and Retry.
  const DOWN_MSG = "Live interview unavailable right now";
  function composer(state) {             // "idle" | "running"; disabled while the patient model is down
    const run = state === "running", down = !S.live || S.model === "down";
    $("#iv-send").classList.toggle("hidden", run); $("#iv-stop").classList.toggle("hidden", !run);
    $("#iv-send").disabled = run || down; $("#iv-draft").disabled = down;
    const why = down ? (S.live ? `${DOWN_MSG}: ${S.modelWhy || ""}` : "Live chat is off on this server.") : "";
    $("#iv-draft").title = why; $("#iv-send").title = why;
  }
  function showErr(msg, question) {
    const e = $("#iv-err"); e.classList.remove("hidden");
    e.querySelector("span").textContent = msg === "stopped" ? "Stopped. Your question is back in the box."
      : /unavailable|warming up/.test(msg) ? `Not sent: ${/warming up/.test(msg) ? "the patient model is still warming up" : "the patient model is unavailable"}. Your question is kept; press Retry when it is back.`
      : `The patient model did not reply: ${msg}`;
    const inp = $("#iv-draft"); if (!inp.value.trim()) inp.value = question;
    e.querySelector("button").onclick = () => { e.classList.add("hidden"); send(); };
  }
  function analyzingUI() {
    const n = S.analyzing || 0, el = $("#iv-analyzing");
    el.classList.toggle("hidden", !n); el.textContent = n ? (n > 1 ? `Analyzing ${n} replies…` : "Analyzing the reply…") : "";
    if (n && S.pinned == null) { $("#iv-detail").classList.remove("preview"); $("#iv-detail").innerHTML = `<div class="eyebrow">Analyzing…</div><p>Matching the reply to ${esc(S.name)}'s network, then writing the guidance.</p>`; }
  }
  // Analyses run one at a time, in turn order (a queue per session): the next question can go out while earlier
  // replies are still being analysed, and the page never holds more than one analysis connection open.
  // Each one gives up after ANALYSIS_LIMIT (the server allows 45 s + one retry), then shows "Retry analysis".
  const ANALYSIS_LIMIT = 100000;
  function analyze(i, msg) {
    const me = S;
    me.turns[i].pending = true; delete me.turns[i].analysisError;
    me.analyzing = (me.analyzing || 0) + 1; analyzingUI();
    me.anChain = (me.anChain || Promise.resolve()).then(() => analyzeOne(me, i, msg)).catch(() => {});
    return me.anChain;
  }
  async function analyzeOne(me, i, msg) {
    const reply = me.turns[i].text, turn = me.turns.slice(0, i + 1).filter(t => t.who === "p").length;
    const earlier = [...(S === me ? seenUpTo(i - 1) : new Set())];
    try {
      const en = await X().apiT("/api/ext/interview/turn", { patient_id: me.pid, trainee: msg, reply, earlier, turn,
        history: me.turns.slice(Math.max(0, i - 5), i - 1).map(x => ({ who: x.who, text: x.text })) }, ANALYSIS_LIMIT);
      me.turns[i] = Object.assign({ who: "p", text: reply }, en);
    } catch (e) { me.turns[i] = { who: "p", text: reply, analysisError: String(e.message || e), trainee: msg }; }
    finally { me.analyzing--; }
    if (S !== me) return;
    renderMsg(i, document.querySelector(`#iv-log [data-i="${i}"]`));
    const prevT = document.querySelector(`#iv-log [data-i="${i - 1}"]`); if (prevT) renderMsg(i - 1, prevT);   // trigger underline
    analyzingUI();
    const later = pIdx().some(j => j > i && !me.turns[j].pending && !me.turns[j].analysisError);
    if (!me.turns[i].analysisError && !later) pin(i);     // never jump back over a newer, already shown reply
    saveSession(); saveLocal(me);
  }
  function saveSession() {
    if (!S.sid) return;
    const done = S.turns.filter(t => !t.pending && !t.streaming);
    X().api("/api/ext/interview/save", { patient_id: S.pid, name: S.name, session_id: S.sid, turns: done }).catch(() => {});
  }
  async function stop() {
    if (!S.stream) return;
    try { await X().api("/api/ext/live/stop", { stream_id: S.stream.id }); } catch (e) {}
    S.stream.ctl.abort();
  }
  async function send() {
    const inp = $("#iv-draft"), msg = inp.value.trim(); if (!msg || S.stream || !S.live) return;
    if (S.playing) stopReplay();
    $("#iv-err").classList.add("hidden");
    if (S.example) exitExample();
    const me = S, ctl = new AbortController(); me.stream = { id: X().newStreamId(), ctl }; composer("running");
    inp.value = "";
    const t = me.turns.length; me.turns.push({ who: "t", text: msg }); renderMsg(t); scrollToMsg(t); hint();
    const ty = typing(); let i = null, li = null, final = null, err = null;
    const undo = () => {                                    // the question goes back to the box; the transcript drops it
      if (i != null) { me.turns.splice(i, 1); if (li) li.remove(); }
      me.turns.splice(t, 1); const tl = document.querySelector(`#iv-log [data-i="${t}"]`); if (tl) tl.remove();
    };
    try {
      const start = async () => {           // new server session, seeded with this patient's conversation so far
        const convo = []; me.turns.slice(0, t).forEach(x => { if (!x.pending && !x.streaming) convo.push({ role: x.who === "t" ? "therapist" : "patient", content: x.text }); });
        me.sid = (await X().api("/api/chat/start", { profile: X().State.profile, long_profile: X().State.longProfile, conversation: convo })).session_id;
      };
      if (me.model !== "up") {                 // starting: hold the question until the patient model is ready
        ty.querySelector(".bubble").innerHTML = `<span class="iv-wait">Waiting for the patient model to start…</span><i></i><i></i><i></i>`;
        await X().LiveStatus.waitUp(10 * 60 * 1000, ctl.signal);
        ty.querySelector(".bubble").innerHTML = "<i></i><i></i><i></i>";
      }
      if (!me.sid) await start();
      let idle = null; const kick = () => { clearTimeout(idle); idle = setTimeout(() => { err = "no answer from the patient model within 75 s"; ctl.abort(); }, 75000); };
      kick();
      const go = () => X().stream("/api/chat/stream", { session_id: me.sid, message: msg, stream_id: me.stream.id, patient_id: me.pid }, ev => {
        kick();
        if (ev.retry) { ty.querySelector(".bubble").title = "retrying…"; return; }
        if (ev.text != null || ev.reset) {
          if (ty.isConnected) ty.remove();
          if (i == null) { i = me.turns.length; me.turns.push({ who: "p", text: "", pending: true, streaming: true }); li = renderMsg(i); li.classList.add("streaming"); }
          me.turns[i].text = ev.reset ? "" : ev.text;
          li.querySelector(".bubble").textContent = me.turns[i].text; logScrollTo(li, false);
        }
        if (ev.done) final = ev;
        if (ev.error) err = ev.error;
        if (ev.stopped) err = "stopped";
      }, ctl.signal);
      try { await go(); }
      catch (e) { if (!/unknown or expired/.test(e.message || "")) throw e; await start(); await go(); }   // server restarted: resume
      clearTimeout(idle);
    } catch (e) { err = err || (ctl.signal.aborted ? "stopped" : (e.message || String(e))); }
    finally { if (ty.isConnected) ty.remove(); me.stream = null; }
    if (err && /unavailable|warming up/.test(err)) X().LiveStatus.poll();
    if (S !== me) return;
    if (!final) { undo(); composer("idle"); hint(); showErr(err || "no reply", msg); return; }
    if (i == null) { i = me.turns.length; me.turns.push({ who: "p", text: "" }); li = renderMsg(i); }
    me.turns[i] = { who: "p", text: final.reply, pending: true }; li = renderMsg(i, li); scrollToMsg(i);
    composer("idle"); inp.focus(); saveLocal(me);
    analyze(i, msg);                                        // not awaited: the next question can go out now
  }

  // ---------------- entry ----------------
  // live status (polled by App): header badge; a worker that comes up turns live mode on; when the model is down
  // the recorded example is offered as the fallback
  function onStatus(st) {
    if (!S) return;
    S.model = st.patient_model === "cold" ? "starting" : st.patient_model; S.modelWhy = st.detail || "";
    if (!$("#screen-interview").classList.contains("active")) return;
    X().liveBadge($("#iv-livebadge"), S.live ? st : null);
    if (!S.stream) composer("idle");
    const off = $("#iv-off"), down = S.live && S.model === "down";
    if (S.live) {
      off.classList.toggle("hidden", !down);
      off.innerHTML = down ? `<b>${esc(DOWN_MSG)}.</b> ${esc(S.modelWhy)}`
        + (S.recorded ? ` <button type="button" class="ghost small" id="iv-off-ex">Watch a recorded example</button>` : "") : "";
      const ex = $("#iv-off-ex"); if (ex) ex.addEventListener("click", enterExample);
    }
    $("#iv-play").classList.toggle("suggest", down && !!S.recorded && !S.example);
    hint();
  }
  let statusHooked = false;
  const SESS = {};                            // pid -> that patient's interview state (live session kept across visits)
  async function open() {
    const St = X().State;
    if (!St.network || !St.patientId) { X().toast("Pick a library profile first."); return; }
    const pid = St.patientId, idx = parseInt(pid.slice(1), 10), ident = (St.profile && St.profile.identity) || {};
    const live = typeof App.liveOn === "function" && App.liveOn();
    if (!S || S.pid !== pid) {
      runId++; if (S) { cancelAnimationFrame(S.raf); S.playing = false; }
      if (SESS[pid]) { S = SESS[pid]; S.TH = null; }          // back to this patient: redraw the network, keep the session
      else {
        const groups = (St.graphs && St.graphs.long && St.graphs.long.groups) || [];
        S = SESS[pid] = { pid, idx, name: ident.name || "The patient", gender: (ident.gender || "").toLowerCase(), net: buildNet(St.network, groups),
              turns: [], pinned: null, recorded: null, live, sid: null, short: {}, example: false };
        try { const v = JSON.parse(localStorage.getItem(LSK(pid)) || "null"); if (v && Array.isArray(v.turns)) { S.turns = v.turns; S.sid = v.sid || null; } } catch (e) {}
        try { S.recorded = await X().api("/api/ext/interview/session/" + idx); } catch (e) { S.recorded = null; }
        const lp = X().api("/api/ext/interview/labels/" + idx).catch(() => ({}));       // never hold the page up for labels
        S.short = await Promise.race([lp, new Promise(r => setTimeout(() => r({}), 1500))]);
        if (!Object.keys(S.short || {}).length) { const me = S; lp.then(v => { if (S === me && v && Object.keys(v).length) me.short = v; }); }
      }
    }
    S.live = live;
    X().show("interview");
    $("#iv-avatar").textContent = (S.name || "?").charAt(0);
    $("#iv-name").innerHTML = `${esc(S.name)} <span class="meta">${esc(ident.age || "")}${ident.age ? " · " : ""}simulated patient</span>`;
    const inp = $("#iv-draft"), miss = (App.liveMissing ? App.liveMissing() : []);
    if (!S.stream) composer("idle");
    inp.placeholder = live ? "Type your question…" : "Live chat is off on this server.";
    const off = $("#iv-off"); off.classList.toggle("hidden", live);
    off.innerHTML = live ? "" : esc("Live chat is off on this server: " + (miss.length ? miss.join("; ") : "no patient model or API key") + ".")
      + (S.recorded ? ` <button type="button" class="ghost small" id="iv-off-ex">Watch a recorded example</button>` : "");
    const ex = $("#iv-off-ex"); if (ex) ex.addEventListener("click", enterExample);
    $("#iv-contrast").classList.toggle("hidden", !(window.Ext && Ext.canContrast && Ext.canContrast()));
    if (!S.TH) drawNet();
    header();
    showAll();
    fitPage();
    if (live && !S.example) setTimeout(() => inp.focus({ preventScroll: true }), 60);
    if (!statusHooked) { statusHooked = true; X().LiveStatus.on(st => onStatus(st)); }
    const cur = X().LiveStatus.current(); if (cur) onStatus(cur); else X().LiveStatus.poll();
    // warm the model before the first question; if none runs (and autostart is on) the server starts one
    if (live) X().apiT("/api/live/warm", {}, 10000).then(st => X().LiveStatus.set(st)).catch(() => {});
  }
  function fitPage() {
    const a = $(".iv-app"); if (!a || !$("#screen-interview").classList.contains("active")) return;
    if (innerWidth <= 900) { a.style.height = ""; document.documentElement.classList.remove("iv-lock"); return; }
    document.documentElement.classList.add("iv-lock"); window.scrollTo(0, 0);
    a.style.height = Math.max(480, Math.round(innerHeight - a.getBoundingClientRect().top - 12)) + "px";
  }
  window.addEventListener("resize", () => fitPage());
  function install() {
    $("#iv-play").addEventListener("click", () => S && (S.example ? exitExample() : enterExample()));
    $("#iv-contrast").addEventListener("click", () => window.Ext && Ext.pickContrast());
    $("#iv-send").addEventListener("click", send);
    $("#iv-stop").addEventListener("click", stop);
    $("#iv-draft").addEventListener("keydown", ev => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); send(); } });
  }
  window.addEventListener("DOMContentLoaded", install);
  function rest() {                           // resting view: nothing pinned, every theme closed
    if (!S) return; S.pinned = null; document.querySelectorAll("#iv-log .iv-msg.p").forEach(m => m.classList.remove("pinned"));
    $("#iv-net").classList.remove("stale"); showEmpty(); coverage(seenUpTo(S.turns.length - 1));
    $("#iv-detail").classList.remove("preview");
    $("#iv-detail").innerHTML = `<div class="eyebrow">Overview</div><p>Hover a reply to preview it, click to pin. Hover a theme to light up its links.</p>`;
  }
  return { open, replay, stopReplay, state: () => S, pin, rest, send, stop, enterExample, exitExample, liveQA, saveLocal };
})();
