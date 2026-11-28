/* ANGEL demo extension (SPEC §7): F1 alignment in chat, F2 contrast-patient cards, F3 dual view.
   Inert unless the server exposes /api/config.ext (a DEMO_FEAT_* flag is on). Features apply to
   the 330 library patients (precomputed networks); user-created profiles get a short note.

   Shared components (used by several pages):
     metaEl(tags, maxTags)    patient metadata line: sex · age group · culture + disorder tags
     AlignmentChips(rec, o)   node/edge chips for one aligned message (hover/click/retry events)
     ThemeGraph(slot, …)      one patient's network, theme-clustered (Overview / Expand all),
                              fit-to-view, short labels, focus, explored shading, unconnected toggle
     ContrastGraph(slot, …)   three horizontal bands: A's causes (top), shared symptoms (middle),
                              B's causes (bottom); patient colours A blue / B purple */
const Ext = (function () {
  "use strict";
  const X = App._ext;
  const { api, el, $, $$, toast, show, State, ForceGraph, colorFor, TYPES } = X;

  const ext = () => (State.config && State.config.ext) || null;
  const flags = () => (ext() && ext().flags) || {};
  const lib = () => !!State.patientId;
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const SIDES = ["a", "b"];

  function download(name, obj) {
    const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }
  // Title Case concept names -> sentence case ("Avoiding Social Gatherings" -> "Avoiding social gatherings").
  // Only labels that are mostly Title Case are changed, so proper nouns inside normal labels survive.
  const sentenceCase = label => X.sentenceLabel(label);
  // line icons instead of emoji (extension only; applied by a MutationObserver so app.js stays unchanged)
  const ICON_PATHS = {
    moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    pencil: '<path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M14 6l4 4"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    chat: '<path d="M4 5h16v11H9l-5 4z"/>',
    speaker: '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 0 1 0 6"/><path d="M18.5 6.5a8 8 0 0 1 0 11"/>',
    mute: '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M17 9l5 6M22 9l-5 6"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  };
  const EMOJI = [["\u2615", null], ["\u263E", "moon"], ["\u270D\uFE0F", "pencil"], ["\u270D", "pencil"], ["\uD83D\uDCC1", "folder"], ["\u2728", "sparkle"],
    ["\uD83D\uDCAC", "chat"], ["\uD83D\uDD07", "mute"], ["\uD83D\uDD0A", "speaker"], ["\uD83C\uDF99\uFE0F", "mic"], ["\uD83C\uDF99", "mic"],
    ["\u23F9\uFE0F", "stop"], ["\u23F9", "stop"], ["\uFF0B", "plus"], ["\uFF0D", "minus"], ["\u2922", "fit"]].filter(x => x[1]);
  function iconSvg(name) {
    const sv = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    sv.setAttribute("viewBox", "0 0 24 24"); sv.setAttribute("class", "ic ic-" + name); sv.setAttribute("aria-hidden", "true");
    sv.innerHTML = ICON_PATHS[name]; return sv;
  }
  function iconizeText(node) {
    const t = node.nodeValue; if (!t) return;
    const hit = EMOJI.find(([ch]) => t.includes(ch)); if (!hit) return;
    const parent = node.parentNode; if (!parent || parent.closest && parent.closest("textarea, input, .btext, .cc-emoji")) return;
    const frag = document.createDocumentFragment(); let rest = t;
    for (;;) {
      let first = null;
      EMOJI.forEach(([ch, nm]) => { const i = rest.indexOf(ch); if (i >= 0 && (!first || i < first.i || (i === first.i && ch.length > first.ch.length))) first = { i, ch, nm }; });
      if (!first) { if (rest) frag.appendChild(document.createTextNode(rest)); break; }
      if (first.i) frag.appendChild(document.createTextNode(rest.slice(0, first.i)));
      frag.appendChild(iconSvg(first.nm));
      rest = rest.slice(first.i + first.ch.length).replace(/^\uFE0F/, "");
    }
    parent.replaceChild(frag, node);
  }
  function iconizeTree(root) {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); const list = [];
    while (w.nextNode()) list.push(w.currentNode);
    list.forEach(iconizeText);
  }
  let iconObs = null;
  function startIcons() {
    if (iconObs) return;
    iconizeTree(document.body);
    iconObs = new MutationObserver(ms => ms.forEach(m => {
      if (m.type === "characterData") iconizeText(m.target);
      m.addedNodes && m.addedNodes.forEach(n => { if (n.nodeType === 3) iconizeText(n); else if (n.nodeType === 1) iconizeTree(n); });
    }));
    iconObs.observe(document.body, { childList: true, subtree: true, characterData: true });
  }
  // label as it reads inside a sentence ("Fear of abandonment" -> "fear of abandonment"; keeps "I'm", acronyms)
  function inSentence(label) {
    const s = sentenceCase(String(label || "").trim());
    if (!s) return s;
    if (/^I\b/.test(s) || /^[A-Z]{2,}/.test(s)) return s;
    return s.charAt(0).toLowerCase() + s.slice(1);
  }
  const STOP = new Set(["what", "when", "would", "could", "about", "your", "yours", "that", "this", "there", "their", "with",
    "have", "having", "feel", "feeling", "feels", "were", "into", "from", "they", "them", "then", "than", "else", "someone",
    "like", "just", "does", "doesn", "really", "mean", "means", "thing", "things", "most", "more", "much", "very", "tell"]);
  const words = s => new Set(String(s || "").toLowerCase().split(/[^a-z]+/).filter(w => w.length >= 4 && !STOP.has(w)).map(w => w.slice(0, 5)));

  // ======================================================================= shared: metadata
  const TYPE_LETTER = { "Stimulus": "S", "Emotion": "E", "Cognition": "C", "Behavior": "B", "Physiological Sensation": "P" };
  function typeBadge(type) {
    return el("span", { class: "tbadge", style: "background:" + colorFor(type), title: type }, [TYPE_LETTER[type] || "?"]);
  }
  function legendInto(box, compact) {
    box.innerHTML = "";
    TYPES.forEach(t => box.appendChild(el("span", { class: "lg", title: t }, compact ? [typeBadge(t)] : [typeBadge(t), t])));
  }
  const missing = v => v == null || /^\s*(unknown|unspecified|n\/a|none|other)?\s*$/i.test(String(v));
  const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
  function metaParts(t) {
    if (!t) return [];
    return [t.gender, t.age_bucket, t.culture].filter(v => !missing(v)).map(v => cap(String(v).trim()));
  }
  // Same line everywhere; a missing field is left out. maxTags: show at most N disorder tags, then "+N".
  function metaEl(t, maxTags) {
    const box = el("span", { class: "pmeta" });
    const parts = metaParts(t);
    if (parts.length) box.appendChild(el("span", { class: "pick-meta" }, [parts.join(" · ")]));
    const tags = ((t && t.disorders) || []).filter(d => !missing(d));
    const k = maxTags == null ? tags.length : maxTags;
    tags.slice(0, k).forEach(d => box.appendChild(el("span", { class: "tag tag-dz" }, [d])));
    if (tags.length > k) {
      const more = el("button", { type: "button", class: "tag tag-more", title: tags.slice(k).join(", "), "aria-label": "Show " + (tags.length - k) + " more" }, ["+" + (tags.length - k)]);
      more.addEventListener("click", ev => { ev.stopPropagation(); tags.slice(k).forEach(d => box.insertBefore(el("span", { class: "tag tag-dz" }, [d]), more)); more.remove(); });
      box.appendChild(more);
    }
    return box;
  }
  const nameOf = (t, fallback) => (t && t.name) || fallback;
  // an answer cut off at the patient model's token limit ends mid-sentence: show it up to the last complete
  // sentence + " …" (display only; exports keep the full text)
  function shownAnswer(text) {
    const t = String(text || "").trim();
    if (!t || /[.!?…]["'”’)\]]*$/.test(t)) return t;
    let end = -1; const rx = /[.!?…]["'”’)\]]*(?=\s)/g; let m;
    while ((m = rx.exec(t))) end = m.index + m[0].length;
    return (end > 0 ? t.slice(0, end) : t.replace(/[\s,;:—–-]+$/, "")) + " …";
  }

  // ======================================================================= shared: evidence + citations
  function markSpans(text, nodes, edges) {
    const flag = new Array(text.length).fill(0);
    const put = (ev, bit) => { if (!ev) return; const i = text.indexOf(ev); if (i < 0) return; for (let k = i; k < i + ev.length; k++) flag[k] |= bit; };
    (nodes || []).forEach(n => put(n.evidence, 1)); (edges || []).forEach(e => put(e.evidence, 2));
    let out = "", i = 0;
    while (i < text.length) {
      let j = i; while (j < text.length && flag[j] === flag[i]) j++;
      const seg = esc(text.slice(i, j));
      out += flag[i] ? "<mark class='ev" + (flag[i] & 2 ? " ev-edge" : "") + (flag[i] & 1 ? " ev-node" : "") + "'>" + seg + "</mark>" : seg;
      i = j;
    }
    return out;
  }
  const spansOf = items => ({ nodes: items.filter(x => x.kind === "node"), edges: items.filter(x => x.kind === "edge") });
  // Tags may cite edges (e7) and, in the demo diff_summary (f3-diff-v3-demo), nodes (n3).
  // labelOf(side, id) -> string | {label, type}
  const CITE_RE = /\[\s*([QCABqcab])\s*:\s*([en]\d+(?:\s*,\s*[en]\d+)*)\s*\]/g;
  function citeHTML(text, sideName, labelOf, o) {
    o = o || {};
    let out = "", last = 0, m;
    CITE_RE.lastIndex = 0;
    while ((m = CITE_RE.exec(text || ""))) {
      out += esc(text.slice(last, m.index));
      const side = sideName(m[1].toUpperCase());
      m[2].split(/\s*,\s*/).forEach(id => {
        const info = labelOf(side, id);
        const lab = ((info && typeof info === "object" ? info.label : info) || id).split(" → ").map(sentenceCase).join(" → ");
        const isNode = id[0] === "n";
        const badge = isNode
          ? "<span class='tbadge' style='background:" + colorFor(info && info.type) + "'>" + (TYPE_LETTER[info && info.type] || "?") + "</span>"
          : "<span class='ebadge'>→</span>";
        out += "<button type='button' class='cite cite-" + side + (isNode ? " cite-node" : "") + "' data-side='" + side + "' " +
          (isNode ? "data-node='" : "data-edge='") + id + "' title='" + esc((isNode && info && info.type ? info.type + ": " : "") + lab) + "'>" +
          (o.noSide ? "" : "<b class='cite-side'>" + m[1].toUpperCase() + "</b>" + badge) + "<span class='cite-l'>" + esc(lab) + "</span></button>";
      });
      last = m.index + m[0].length;
    }
    return out + esc((text || "").slice(last));
  }

  // ======================================================================= shared: AlignmentChips
  function itemsOf(rec) {
    if (!rec) return [];
    return (rec.nodes || []).map(n => ({ kind: "node", id: n.node_id, label: sentenceCase(n.label || n.node_id), type: n.type, evidence: n.evidence }))
      .concat((rec.edges || []).map(e => ({ kind: "edge", id: e.edge_id, label: e.label || e.edge_id, source: e.source, target: e.target, evidence: e.evidence })));
  }
  function AlignmentChips(rec, o) {
    o = o || {};
    if (!rec) return el("div", { class: "al-chips pending", "aria-live": "polite" }, [el("span", { class: "shimmer" }, ["aligning…"])]);
    if (rec.unavailable || rec.error || rec.status === "unavailable") {
      const b = el("button", { class: "al-retry", type: "button", title: "Alignment unavailable — retry", "aria-label": "Alignment unavailable, retry" }, ["↻"]);
      b.addEventListener("click", ev => { ev.stopPropagation(); if (o.onRetry) o.onRetry(); });
      return el("div", { class: "al-chips unavailable" }, [b]);
    }
    const items = itemsOf(rec);
    if (rec.unmatched || !items.length) return el("div", { class: "al-chips none" }, [el("span", { class: "nochip" }, ["no network content"])]);
    const box = el("div", { class: "al-chips" });
    // edges first; a node that is an endpoint of a shown edge is not repeated; at most 3 chips, then "+N"
    const ends = new Set(); items.filter(it => it.kind === "edge").forEach(it => { ends.add(it.source); ends.add(it.target); });
    const shownItems = items.filter(it => it.kind === "edge").concat(items.filter(it => it.kind === "node" && !ends.has(it.id)));
    const MAXCH = 3;
    shownItems.forEach((it, idx) => {
      const kids = it.kind === "node" ? [typeBadge(it.type), el("span", { class: "chip-l" }, [it.label])]
        : [el("span", { class: "ebadge", title: "cause → effect edge" }, ["→"]), el("span", { class: "chip-l" }, [it.label])];
      const c = el("button", { type: "button", class: "achip " + it.kind, "data-kind": it.kind, "data-id": it.id,
        title: (it.kind === "node" ? it.type + ": " : "Edge: ") + it.label + (it.evidence ? "\n“" + it.evidence + "”" : "") }, kids);
      c.addEventListener("mouseenter", () => o.onFocus && o.onFocus(it));
      c.addEventListener("focus", () => o.onFocus && o.onFocus(it));
      c.addEventListener("mouseleave", () => o.onLeave && o.onLeave());
      c.addEventListener("blur", () => o.onLeave && o.onLeave());
      c.addEventListener("click", ev => { ev.stopPropagation(); if (o.onClick) o.onClick(it); });
      if (idx >= MAXCH) c.classList.add("over");
      box.appendChild(c);
    });
    if (shownItems.length > MAXCH) {
      const more = el("button", { type: "button", class: "achip more", title: shownItems.slice(MAXCH).map(x => x.label).join("\n") }, ["+" + (shownItems.length - MAXCH)]);
      more.addEventListener("click", ev => { ev.stopPropagation(); box.classList.add("all"); more.remove(); });
      box.appendChild(more);
    }
    return box;
  }

  // ======================================================================= shared: suggestion chips
  function chipsInto(box, label, questions, onPick) {
    box.innerHTML = "";
    if (!questions.length) { box.classList.add("hidden"); return; }
    box.classList.remove("hidden");
    box.appendChild(el("span", { class: "starters-label" }, [label]));
    questions.forEach(q => {
      const b = el("button", { type: "button", class: "qchip", title: "Use this question" }, [q]);
      b.addEventListener("click", () => onPick(q));
      box.appendChild(b);
    });
  }

  // ======================================================================= shared: ThemeGraph
  // One patient's SPEC network with theme clustering (collapsed by default). Keys of real nodes are
  // SPEC node ids; a collapsed theme is "g:<i>"; edges into collapsed themes are aggregated.
  function ThemeGraph(slot, net, groups, opts) {
    opts = opts || {};
    const nodes = net.nodes || [], byId = {}; nodes.forEach(n => { byId[n.id] = n; });
    const G = (groups || []).filter(g => (g.members || []).length > 1);
    const gOf = {}; G.forEach((g, gi) => g.members.forEach(m => { gOf[m] = gi; }));
    const expanded = new Set(), pos = {};
    let fg = null, marked = { n: [], e: [] }, explored = null, focusOn = false, vEdge = {}, showIso = false, isoN = 0;
    const vis = id => (gOf[id] === undefined || expanded.has(gOf[id])) ? id : "g:" + gOf[id];

    function view() {
      const vn = [], seen = new Set();
      G.forEach((g, gi) => {
        if (expanded.has(gi)) g.members.forEach(m => { if (byId[m] && !seen.has(m)) { seen.add(m); vn.push({ id: m, name: byId[m].label, type: byId[m].type, member: true, gidx: gi }); } });
        else vn.push({ id: "g:" + gi, name: sentenceCase(g.label || "theme"), type: g.type || byId[g.members[0]].type, group: true, gidx: gi, count: g.members.length });
      });
      nodes.forEach(n => { if (gOf[n.id] === undefined && !seen.has(n.id)) { seen.add(n.id); vn.push({ id: n.id, name: n.label, type: n.type }); } });
      vn.forEach(n => {
        if (pos[n.id]) return;
        if (n.member && pos["g:" + n.gidx]) n.spawn = pos["g:" + n.gidx];
        else if (n.group) { const pts = G[n.gidx].members.map(m => pos[m]).filter(Boolean);
          if (pts.length) n.spawn = { x: pts.reduce((s, p) => s + p.x, 0) / pts.length, y: pts.reduce((s, p) => s + p.y, 0) / pts.length }; }
      });
      const agg = {}; vEdge = {};
      (net.edges || []).forEach(e => {
        if (!byId[e.source] || !byId[e.target]) return;
        const a = vis(e.source), b = vis(e.target); if (a === b) return;
        const real = a === e.source && b === e.target;
        const id = real ? e.id : "agg:" + a + ">" + b;
        vEdge[e.id] = id;
        if (!agg[id]) agg[id] = { id, from: a, to: b, evidence: real ? (e.evidence || "") : "", weight: 0,
          fromName: byId[e.source].label, toName: byId[e.target].label, fromType: byId[e.source].type, toType: byId[e.target].type };
        agg[id].weight++;
      });
      const edgesV = Object.values(agg);
      // unconnected = real nodes without any edge in the network (count stable whatever is expanded)
      const deg = new Set(); (net.edges || []).forEach(e => { deg.add(e.source); deg.add(e.target); });
      const isoIds = new Set(nodes.filter(n => !deg.has(n.id)).map(n => n.id)); isoN = isoIds.size;
      const hide = n => n.group ? G[n.gidx].members.every(m => isoIds.has(m)) : isoIds.has(n.id);
      return { nodes: showIso ? vn : vn.filter(n => !hide(n)), edges: edgesV };
    }
    function apply() {
      if (!fg) return;
      const mk = new Set(marked.n.map(vis)), me = new Set(marked.e.map(e => vEdge[e]).filter(Boolean));
      if (mk.size || me.size) fg.mark([...mk], [...me]); else fg.clearMark();
      if (explored && explored.n.size) {     // before anything is discussed, everything keeps its full colour
        fg.setExplored([...explored.n].map(vis), [...explored.e].map(e => vEdge[e]).filter(Boolean));
      } else fg.setExplored(null, null);
      if (focusOn && !opts.noFocus && (mk.size || me.size)) fg.focus([...mk]);
    }
    function render() {
      slot.innerHTML = "";
      const v = view();
      fg = new ForceGraph(slot, v, {
        positions: pos, fit: true, shortLabel: opts.shortLabel || 18, flipLabels: true, labelMax: 60, sentence: true,
        onExpand: n => { expanded.add(n.gidx); render(); },
        onCollapse: n => { if (n.gidx != null) { expanded.delete(n.gidx); render(); } },
        onNodeClick: n => { if (!n.group && opts.onNodeClick) opts.onNodeClick(n.key); },
      });
      fg.render(); apply(); ctl();
    }
    function ctl() {
      const box = opts.ctl; if (!box) return;
      box.innerHTML = "";
      if (G.length) {
        const ov = el("button", { class: "ghost small", type: "button" }, ["Overview"]);
        ov.addEventListener("click", () => { expanded.clear(); render(); });
        const all = el("button", { class: "ghost small", type: "button" }, ["Expand all"]);
        all.addEventListener("click", () => { G.forEach((g, gi) => expanded.add(gi)); render(); });
        box.appendChild(el("span", { class: "muted small" }, [expanded.size ? "Click a theme to expand · double-click a node to collapse" : "Click a theme to expand it"]));
        box.appendChild(ov); box.appendChild(all);
      }
      if (isoN) {
        const b = el("button", { class: "ghost small", type: "button" }, [(showIso ? "Hide" : "Show") + " unconnected nodes (" + isoN + ")"]);
        b.addEventListener("click", () => { showIso = !showIso; render(); });
        box.appendChild(b);
      }
    }
    this.mark = (nodeIds, edgeIds, focus) => {
      marked = { n: (nodeIds || []).slice(), e: (edgeIds || []).slice() };
      (edgeIds || []).forEach(eid => { const e = (net.edges || []).find(x => x.id === eid); if (e) marked.n.push(e.source, e.target); });
      focusOn = !!focus;
      if (!showIso && marked.n.some(id => byId[id] && !(net.edges || []).some(e => e.source === id || e.target === id))) { showIso = true; render(); return; }
      apply();
    };
    this.clear = () => { marked = { n: [], e: [] }; focusOn = false; apply(); if (fg) fg.focus(null); };
    this.expandFor = nodeIds => {
      let ch = false; (nodeIds || []).forEach(id => { const gi = gOf[id]; if (gi !== undefined && !expanded.has(gi)) { expanded.add(gi); ch = true; } });
      if (ch) render();
    };
    this.setExplored = (nset, eset) => { explored = nset ? { n: new Set(nset), e: new Set(eset || []) } : null; apply(); };
    this.pulse = (nodeIds, edgeIds) => { if (fg) fg.pulse((nodeIds || []).map(vis), (edgeIds || []).map(e => vEdge[e]).filter(Boolean)); };
    this.refit = () => { if (fg) fg.fit(); };
    this.overview = () => { expanded.clear(); render(); };
    this.setLabelLen = n => { opts.shortLabel = n; render(); };      // Paper view: longer node labels
    this.render = render;
    render();
  }

  // ======================================================================= shared: ContrastGraph (left / centre / right)
  // cg: /api/ext/contrast-graph payload; nets: {a,b} SPEC networks (optional; labels fall back to cg);
  // names {a,b}; opts: {probe, pillsBox, mini, initial (concept id), cited {a:Set(edge ids), b:Set}}.
  // HTML grid (A's causes | shared symptom(s) | B's causes) + SVG overlay for the curved edges.
  // Focus mode (default): one shared symptom (the probe-targeted one), <= 6 causes per side.
  // State changes (discussed / highlighted) only toggle classes: order and labels never change.
  function ContrastGraph(slot, cg, nets, names, opts) {
    opts = opts || {};
    const MAXC = opts.mini ? 3 : 6, PILLS = 5, MAXALSO = opts.mini ? 2 : 3;
    const NS = "http://www.w3.org/2000/svg";
    let state = { a: { n: new Set(), e: new Set() }, b: { n: new Set(), e: new Set() } }, fresh = null, hi = null, pinned = null;
    let mode = "focus", moreCauses = { a: false, b: false }, morePills = false, moreAlso = { a: false, b: false }, expanded = false;
    const mentioned = { a: new Map(), b: new Map() };     // aligned nodes, in order of first appearance
    let root = null, svg = null, itemEls = {}, pillEls = {}, edgeEls = [], ro = null, model = null;
    const pw = words(opts.probe);
    const score = {};
    cg.concepts.forEach(c => {
      const cw = words(c.name + " " + SIDES.map(s => c.nodes[s].map(n => n.label).join(" ")).join(" "));
      let hit = 0; cw.forEach(w => { if (pw.has(w)) hit++; });
      const dist = SIDES.reduce((a, s) => a + cg.sides[s].edges.filter(e => e.target_concept === c.concept_id && e.distinctive).length, 0);
      const all = SIDES.reduce((a, s) => a + cg.sides[s].edges.filter(e => e.target_concept === c.concept_id).length, 0);
      score[c.concept_id] = hit * 100 + dist * 2 + all * 0.1;
    });
    cg.concepts.forEach(c => { c.name = sentenceCase(c.name); });
    const ranked = cg.concepts.slice().sort((p, q) => score[q.concept_id] - score[p.concept_id]);
    const cById = {}; cg.concepts.forEach(c => { cById[c.concept_id] = c; });
    let selected = (opts.initial && cById[opts.initial]) ? opts.initial : (ranked.length ? ranked[0].concept_id : null);
    // node labels/types: full network if given, else the contrast-graph payload itself
    const info = { a: {}, b: {} };
    SIDES.forEach(s => {
      ((nets && nets[s] && nets[s].nodes) || []).forEach(n => { info[s][n.id] = n; });
      cg.sides[s].causes.forEach(n => { if (!info[s][n.id]) info[s][n.id] = n; });
      cg.concepts.forEach(c => c.nodes[s].forEach(n => { if (!info[s][n.id]) info[s][n.id] = n; }));
    });
    const edgeById = { a: {}, b: {} };
    SIDES.forEach(s => {
      ((nets && nets[s] && nets[s].edges) || []).forEach(e => { edgeById[s][e.id] = e; });
      cg.sides[s].edges.forEach(e => { if (!edgeById[s][e.id]) edgeById[s][e.id] = e; });
    });
    const visible = () => mode === "all" ? ranked : ranked.filter(c => c.concept_id === selected);

    function build() {
      const vc = visible(), vset = new Set(vc.map(c => c.concept_id));
      const side = {};
      SIDES.forEach(s => {
        const items = {}, edges = [];
        cg.sides[s].edges.forEach(e => {
          if (!vset.has(e.target_concept)) return;
          if (e.source_concept && vset.has(e.source_concept)) { if (mode !== "all") edges.push({ s, e, from: "c:" + e.source_concept, inner: true }); return; }
          const key = s + ":" + e.source, n = info[s][e.source] || {};
          if (!items[key]) items[key] = { key, side: s, id: e.source, label: sentenceCase(n.label || e.source), type: n.type, distinctive: false, edges: [] };
          items[key].distinctive = items[key].distinctive || e.distinctive;
          items[key].edges.push(e.id);
          edges.push({ s, e, from: key });
        });
        // stable order: distinctive first, then alphabetical (never re-sorted by dialogue state)
        const isCited = it => !!(opts.cited && opts.cited[s] && it.edges.some(x => opts.cited[s].has(x)));
        let list = Object.values(items).sort((p, q) => (opts.mini ? isCited(q) - isCited(p) : 0) || (q.distinctive - p.distinctive) || p.label.localeCompare(q.label));
        const full = list.slice();
        const hidden = mode === "focus" && !moreCauses[s] && !expanded ? Math.max(0, list.length - MAXC) : 0;
        if (hidden) list = list.slice(0, MAXC);
        // causes the dialogue mentioned that this view does not draw: add them ("mentioned", dashed connector)
        const centre = new Set(vc.flatMap(c => c.nodes[s].map(n => n.id)));
        const have = new Set(full.map(it => it.id));
        const shownIds = new Set(list.map(it => it.id));
        mentioned[s].forEach((m, id) => {
          if (centre.has(id) || shownIds.has(id) || !vc.length) return;
          if (have.has(id)) { const it = full.find(x => x.id === id); list.push(it); shownIds.add(id); return; }   // behind "+N more"
          // a real edge from this node into one of the drawn symptoms' nodes on this side?
          const real = ((nets && nets[s] && nets[s].edges) || []).find(e => e.source === id && vc.some(c => c.nodes[s].some(n => n.id === e.target)));
          const it = { key: s + ":m:" + id, side: s, id, label: sentenceCase(m.label), type: m.type, distinctive: true, edges: real ? [real.id] : [], mentioned: true, also: !real };
          list.push(it); shownIds.add(id);
          if (real) {
            const tc = vc.find(c => c.nodes[s].some(n => n.id === real.target));
            edges.push({ s, e: { id: real.id, source: id, target: real.target, target_concept: tc.concept_id, distinctive: true }, from: it.key, mentioned: true });
          }
        });
        // "also mentioned" (no link to the drawn symptom): at most MAXALSO per side, the most recent ones,
        // in order of first mention; the rest behind "+N more". Core causes are never displaced.
        const alsoIt = list.filter(it => it.also);
        let alsoHidden = 0;
        if (alsoIt.length > MAXALSO && !moreAlso[s] && !expanded) {
          const keep = new Set(alsoIt.slice(-MAXALSO).map(it => it.key));
          alsoHidden = alsoIt.length - MAXALSO;
          list = list.filter(it => !it.also || keep.has(it.key));
        }
        const shown = new Set(list.map(it => it.key));
        side[s] = { list, full, hidden, alsoHidden, alsoTotal: alsoIt.length, edges: edges.filter(x => x.inner || shown.has(x.from)) };
      });
      // 4: a mentioned cause whose cause group (concept vocabulary) matches a cause shown on the other side:
      //    both are drawn grey as a shared kind of cause
      const kinds = cg.node_kinds || { a: {}, b: {} };
      SIDES.forEach(s => side[s].list.forEach(it => { it.sharedKind = false; }));
      SIDES.forEach(s => {
        const o = s === "a" ? "b" : "a";
        side[s].list.filter(it => it.mentioned).forEach(it => {
          const k = kinds[s][it.id]; if (!k) return;
          side[o].list.forEach(ot => { if (kinds[o][ot.id] === k) { it.sharedKind = true; ot.sharedKind = true; } });
        });
      });
      return { vc, side };
    }

    function pills() {
      const box = opts.pillsBox; if (!box || opts.mini) return;
      box.innerHTML = ""; pillEls = {};
      const mk = (key, kids, title) => {
        const b = el("button", { type: "button", class: "cg-pill", role: "tab", title }, kids);
        b.addEventListener("click", () => { if (key === "all") mode = "all"; else { mode = "focus"; selected = key; } moreCauses = { a: false, b: false }; render(); });
        pillEls[key] = b; box.appendChild(b); return b;
      };
      const show = morePills ? ranked.slice() : ranked.slice(0, PILLS);
      if (selected && !show.some(c => c.concept_id === selected)) show.push(cById[selected]);
      show.forEach(c => mk(c.concept_id, [typeBadge(c.type), el("span", {}, [c.name]), el("i", { class: "pill-dot", "aria-hidden": "true" })],
        c.name + (score[c.concept_id] >= 100 ? " (named in the probe question)" : "")));
      if (ranked.length > show.length) {
        const more = el("button", { type: "button", class: "cg-pill more" }, ["+" + (ranked.length - show.length)]);
        more.addEventListener("click", () => { morePills = true; pills(); paint(false); });
        box.appendChild(more);
      }
      mk("all", ["All (" + ranked.length + ")"], "Show every shared symptom");
    }

    function render() {
      slot.innerHTML = ""; itemEls = {}; edgeEls = [];
      model = build();
      const { vc, side } = model;
      pills();
      root = el("div", { class: "cg2" + (mode === "all" ? " all" : "") + (opts.mini ? " mini" : "") + (opts.plain ? " plain" : "") });
      if (opts.onFocus) opts.onFocus(mode === "all" ? null : cById[selected], mode);
      if (opts.panel) opts.panel.classList.toggle("all", mode === "all");
      const mark = (it) => el("i", { class: "dmark" + (it.distinctive ? "" : " grey"), "aria-label": it.distinctive ? "distinctive cause" : "shared kind of cause" }, [it.distinctive ? "◆" : "◇"]);
      const col = (s) => {
        const c = el("div", { class: "cg2-col side-" + s });
        c.appendChild(el("div", { class: "cg2-head" }, [names[s] + "'s causes"]));
        if (!side[s].list.length) c.appendChild(el("div", { class: "cg2-none muted small" }, ["No recorded cause"]));
        let alsoHead = false;
        const moreBtn = () => {
          if (!(side[s].hidden || (mode === "focus" && moreCauses[s] && !expanded))) return;
          const b = el("button", { type: "button", class: "cg2-more" }, [moreCauses[s] ? "Show fewer" : "+" + side[s].hidden + " more"]);
          b.addEventListener("click", () => { moreCauses[s] = !moreCauses[s]; render(); });
          c.appendChild(b);
        };
        side[s].list.forEach(it => {
          if (it.also && !alsoHead) { alsoHead = true; moreBtn(); c.appendChild(el("div", { class: "cg2-also" }, ["also mentioned"])); }
          const kids = [el("span", { class: "cg2-lbl" }, [it.label])];
          if (it.mentioned && !it.also) kids.push(el("span", { class: "cg2-tag" }, ["mentioned"]));
          if (!opts.plain) { kids.unshift(typeBadge(it.type)); if (s === "a") kids.push(mark(it)); else kids.unshift(mark(it)); }
          const dist = it.distinctive && !it.sharedKind;
          const e = el("button", { type: "button", class: "cg2-item side-" + s + (dist ? " dist" : " both") + (it.mentioned ? " mentioned" : "") + (it.also ? " also" : ""),
            title: it.label + "\n" + (it.sharedKind ? "Shared kind of cause: the other patient has a cause of the same kind" :
              it.mentioned ? "Mentioned in " + names[s] + "'s answers" + (it.also ? "; not linked to this symptom in the network" : "") :
              it.distinctive ? "Distinctive: " + names[s === "a" ? "b" : "a"] + " has no such cause" : "Both patients have this kind of cause") }, kids);
          e.addEventListener("mouseenter", () => { hi = { side: s, n: new Set([it.id]), e: new Set(it.edges) }; paint(false); });
          e.addEventListener("mouseleave", () => { hi = pinned; paint(false); });
          e._it = it; itemEls[it.key] = e; c.appendChild(e);
        });
        if (!alsoHead) moreBtn();
        if (!expanded && (side[s].alsoHidden || (moreAlso[s] && side[s].alsoTotal > MAXALSO))) {
          const b = el("button", { type: "button", class: "cg2-more also-more" }, [moreAlso[s] ? "Show fewer" : "+" + side[s].alsoHidden + " more"]);
          b.addEventListener("click", () => { moreAlso[s] = !moreAlso[s]; render(); });
          c.appendChild(b);
        }
        return c;
      };
      const mid = el("div", { class: "cg2-mid" });
      vc.forEach(c => {
        const p = el("div", { class: "cg2-sym", title: SIDES.map(s => names[s] + ": " + c.nodes[s].map(x => x.label).join("; ")).join("\n") },
          (opts.plain ? [] : [typeBadge(c.type)]).concat([el("span", { class: "cg2-sym-lbl" }, [c.name])]));
        p._c = c; itemEls["c:" + c.concept_id] = p; mid.appendChild(p);
      });
      const ca = col("a"), cb = col("b");
      [ca, cb].forEach(c => c.addEventListener("scroll", () => requestAnimationFrame(drawEdges), { passive: true }));
      root.appendChild(ca); root.appendChild(mid); root.appendChild(cb);
      svg = document.createElementNS(NS, "svg"); svg.setAttribute("class", "cg2-svg"); svg.setAttribute("aria-hidden", "true");
      root.appendChild(svg);
      slot.appendChild(root);
      requestAnimationFrame(drawEdges);
      if (ro) ro.disconnect();
      if (window.ResizeObserver) { ro = new ResizeObserver(() => drawEdges()); ro.observe(root); }
      paint(false);
    }
    function drawEdges() {
      if (!root || !svg || !model) return;
      const R = root.getBoundingClientRect();
      if (!R.width) return;
      svg.setAttribute("width", root.scrollWidth); svg.setAttribute("height", root.scrollHeight);
      svg.setAttribute("viewBox", "0 0 " + root.scrollWidth + " " + root.scrollHeight);
      svg.innerHTML = ""; edgeEls = [];
      // anchor = middle of the item, clamped to the visible part of its (scrollable) column
      const at = (elx, edge) => {
        const r = elx.getBoundingClientRect(), col = elx.closest(".cg2-col"), cr = col ? col.getBoundingClientRect() : r;
        const y = Math.max(cr.top + 4, Math.min(cr.bottom - 4, r.top + r.height / 2));
        return { x: (edge === "l" ? r.left : r.right) - R.left, y: y - R.top, hidden: !!col && (r.bottom < cr.top || r.top > cr.bottom) };
      };
      SIDES.forEach(s => model.side[s].edges.forEach(x => {
        const src = itemEls[x.from], dst = itemEls["c:" + x.e.target_concept];
        if (!src || !dst) return;
        let d;
        if (x.inner) {
          const a = at(src, s === "a" ? "l" : "r"), b = at(dst, s === "a" ? "l" : "r"), off = s === "a" ? -40 : 40;
          d = "M" + a.x + "," + a.y + " C" + (a.x + off) + "," + a.y + " " + (b.x + off) + "," + b.y + " " + b.x + "," + b.y;
        } else {
          const a = at(src, s === "a" ? "r" : "l"), b = at(dst, s === "a" ? "l" : "r"), mx = (a.x + b.x) / 2;
          d = "M" + a.x + "," + a.y + " C" + mx + "," + a.y + " " + mx + "," + b.y + " " + b.x + "," + b.y;
        }
        const p = document.createElementNS(NS, "path");
        p.setAttribute("d", d);
        p.setAttribute("class", "cg2-edge side-" + s + (x.e.distinctive ? " dist" : " both") + (x.mentioned ? " mentioned" : "") + (at(src, "l").hidden ? " offscreen" : ""));
        p._x = x; edgeEls.push(p); svg.appendChild(p);
      }));
      paint(false);
    }
    const anyDisc = () => SIDES.some(s => state[s].n.size || state[s].e.size);
    const itemDisc = it => state[it.side].n.has(it.id) || it.edges.some(x => state[it.side].e.has(x));
    function conceptDisc(c) {
      return SIDES.filter(s => c.nodes[s].some(x => state[s].n.has(x.id)) ||
        cg.sides[s].edges.some(e => e.target_concept === c.concept_id && state[s].e.has(e.id)));
    }
    // classes only: colour / outline / glow / opacity. Never text, order or size.
    function paint(doPulse) {
      const started = anyDisc();
      const cited = opts.cited || null;
      Object.values(itemEls).forEach(e => {
        let ds, isHi = false, isFresh = false, isCited = false;
        if (e._c) {
          ds = conceptDisc(e._c);
          isHi = !!hi && e._c.nodes[hi.side].some(x => hi.n.has(x.id));
          isFresh = !!fresh && SIDES.some(s => e._c.nodes[s].some(x => fresh[s].n.has(x.id)) ||
            cg.sides[s].edges.some(x => x.target_concept === e._c.concept_id && fresh[s].e.has(x.id)));
        } else {
          const it = e._it, s = it.side;
          ds = itemDisc(it) ? [s] : [];
          isHi = !!hi && hi.side === s && (hi.n.has(it.id) || it.edges.some(x => hi.e.has(x)));
          isFresh = !!fresh && (fresh[s].n.has(it.id) || it.edges.some(x => fresh[s].e.has(x)));
          isCited = !!cited && it.edges.some(x => cited[s] && cited[s].has(x));
        }
        e.classList.toggle("und", started && !ds.length);
        e.classList.toggle("disc-a", ds.includes("a")); e.classList.toggle("disc-b", ds.includes("b"));
        e.classList.toggle("mk", isHi); e.classList.toggle("dim", !!hi && !isHi && !e._c && !ds.length);   // discussed items keep their glow
        e.classList.toggle("cited", isCited);
        if (doPulse && isFresh) { e.classList.remove("x-pulse"); void e.offsetWidth; e.classList.add("x-pulse"); setTimeout(() => e.classList.remove("x-pulse"), 1700); }
      });
      edgeEls.forEach(p => {
        const x = p._x, s = x.s, on = x.mentioned || state[s].e.has(x.e.id) || (state[s].n.has(x.e.source) && state[s].n.has(x.e.target));
        p.classList.toggle("und", started && !on); p.classList.toggle("disc", on);
        const h = !!hi && hi.side === s && (hi.e.has(x.e.id) || hi.n.has(x.e.source));
        p.classList.toggle("mk", h); p.classList.toggle("dim", !!hi && !h && !on);
        p.classList.toggle("cited", !!cited && cited[s] && cited[s].has(x.e.id));
      });
      Object.entries(pillEls).forEach(([k, b]) => {
        if (k === "all") { b.classList.toggle("on", mode === "all"); b.setAttribute("aria-selected", String(mode === "all")); return; }
        const sel = mode === "focus" && k === selected;
        b.classList.toggle("on", sel); b.setAttribute("aria-selected", String(sel));
        b.classList.toggle("has-dot", !sel && conceptDisc(cById[k]).length > 0);
      });
    }
    function touched(side, nset, eset) {
      const out = new Set();
      cg.concepts.forEach(c => { if (c.nodes[side].some(x => nset.has(x.id))) out.add(c.concept_id); });
      cg.sides[side].edges.forEach(e => { if (eset.has(e.id) || nset.has(e.source)) out.add(e.target_concept); });
      return out;
    }
    // a discussed cause hidden behind "+N more" un-hides its side; otherwise only classes change
    this.setState = (st, nw) => {
      state = st; fresh = nw || null;
      let re = false;
      SIDES.forEach(s => st[s].n.forEach(id => {
        if (!mentioned[s].has(id)) { const n = info[s][id] || {}; mentioned[s].set(id, { label: n.label || id, type: n.type }); re = true; }
      }));
      if (model && mode === "focus") SIDES.forEach(s => {
        const shown = new Set(model.side[s].list.map(it => it.key));
        if (!moreCauses[s] && model.side[s].full.some(it => !shown.has(it.key) && itemDisc(it))) { moreCauses[s] = true; re = true; }
      });
      if (re) render();
      paint(!!nw);
    };
    this.highlight = (side, nodeIds, edgeIds, persist) => {
      const n = new Set(nodeIds || []), e = new Set(edgeIds || []);
      (edgeIds || []).forEach(eid => { const ed = edgeById[side][eid]; if (ed) { n.add(ed.source); n.add(ed.target); } });
      const t = touched(side, n, e);
      if (persist && mode === "focus" && t.size && !t.has(selected)) { selected = ranked.find(c => t.has(c.concept_id)).concept_id; render(); }
      hi = { side, n, e }; if (persist) pinned = hi; paint(false);
      const found = Object.values(itemEls).some(x => x.classList.contains("mk")) || edgeEls.some(p => p.classList.contains("mk"));
      if (!found && t.size) return "elsewhere";
      return found;
    };
    this.clear = () => { hi = null; pinned = null; paint(false); };
    // paper view: every cause listed (no "+N more"), no scrolling inside the box
    this.setExpanded = on => { expanded = !!on; render(); };
    // focus symptom: distinctive causes per side and how many the dialogue has reached so far
    this.focusStats = () => {
      if (mode === "all" || !model || !selected) return null;
      const out = { id: selected, name: cById[selected].name };
      SIDES.forEach(s => {
        const d = model.side[s].full.filter(it => it.distinctive && !it.mentioned);
        out[s] = { total: d.length, reached: d.filter(itemDisc).length };
      });
      return out;
    };
    this.select = cid => { if (cById[cid]) { mode = "focus"; selected = cid; moreCauses = { a: false, b: false }; moreAlso = { a: false, b: false }; render(); } };
    this.symptoms = () => ranked.map(c => ({ id: c.concept_id, name: c.name, probe: score[c.concept_id] >= 100, discussed: conceptDisc(c) }));
    this.selectedId = () => (mode === "all" ? null : selected);
    this.setAll = on => { mode = on ? "all" : "focus"; render(); };
    this.isAll = () => mode === "all";
    // the shared symptom discussed by both patients (most relevant first), if any
    this.bothDiscussed = () => (ranked.find(c => SIDES.every(s => conceptDisc(c).includes(s) ||
      cg.sides[s].edges.some(e => e.target_concept === c.concept_id && state[s].n.has(e.source)))) || {}).concept_id || null;
    this.undiscussedConcepts = () => ranked.filter(c => conceptDisc(c).length === 0 &&
      SIDES.every(s => !cg.sides[s].edges.some(e => e.target_concept === c.concept_id && state[s].n.has(e.source))));
    this.render = render;
    render();
  }

  // ======================================================================= page hooks
  let prewarmed = false;    // the patient model is requested as soon as the demo opens (queue + load overlap browsing)
  const STEP_OF = { choose: "profile", create: "profile", existing: "profile", generate: "network", chat: "interview", interview: "interview", fc: "compare", contrast: "compare", dual: "compare" };
  const STEP_ORDER = ["profile", "network", "interview", "compare"];
  function fillDisclaimers() {
    const d = ext() ? ext().disclaimer : "";
    $$("[data-ext-footnote]").forEach(n => { n.textContent = d; n.classList.toggle("hidden", !d); });
    const ex = document.getElementById("landing-example"); if (ex) ex.classList.toggle("hidden", !(ext() && flags().dual));
  }
  function onShow(name) {
    if (!ext()) return;
    document.body.classList.add("ext-on");
    startIcons(); installOnce();
    closeAllMenus();
    if (PAPER.on && PAPER.screen !== name) setPaper(false);
    const tb = document.querySelector(".topbar");          // sticky panels sit just below the header
    if (tb) document.documentElement.style.setProperty("--topbar-h", tb.offsetHeight + "px");
    fillDisclaimers();
    const cur = STEP_OF[name] || "profile", ci = STEP_ORDER.indexOf(cur);
    $("#steps").classList.remove("hidden");
    $$("#steps [data-step]").forEach(s => { const i = STEP_ORDER.indexOf(s.dataset.step);
      s.classList.toggle("active", i === ci); s.classList.toggle("done", i < ci || (i !== ci && stepHasState(s.dataset.step)));
      const link = i !== ci && stepHasState(s.dataset.step);
      s.classList.toggle("link", link); s.setAttribute("role", link ? "link" : "text"); s.tabIndex = link ? 0 : -1;
      if (i === ci) s.setAttribute("aria-current", "step"); else s.removeAttribute("aria-current"); });
    const wide = name === "contrast" || name === "dual" || name === "interview" || name === "fc" || (name === "chat" && $("#chat-split").classList.contains("on"));
    document.body.classList.toggle("ext-wide", wide);
    // Interview and Compare are one-viewport app layouts (CSS: body.app-chat / body.app-dual)
    document.body.classList.toggle("app-chat", name === "chat" && $("#chat-split").classList.contains("on"));
    document.body.classList.toggle("app-dual", name === "dual");
    if (name !== "interview") document.documentElement.classList.remove("iv-lock");
    if (name === "generate") { $("#net-label").classList.remove("hidden"); }
    if (!prewarmed && (flags().align || flags().dual)) { prewarmed = true; api("/api/gpu/ensure", {}).catch(() => {}); }
    try { $("#g-tip").classList.remove("show"); } catch (e) {}
  }
  // a step is a link whenever it has something to return to (round 10)
  function stepHasState(k) {
    if (k === "profile") return true;
    if (k === "network") return !!(State.graphs && (State.graphs.long || State.graphs.short));
    if (k === "interview") return !!State.chat || !!(State.profile && State.patientId);
    if (k === "compare") return !!D || !!R || (window.FC && FC.has()) || canContrast();
    return false;
  }
  function goStep(k) {
    if (k === "profile") App.restart();
    else if (k === "network" && stepHasState("network")) show("generate");
    else if (k === "interview") { if (window.Interview && State.patientId) Interview.open(); else if (State.chat) show("chat"); else if (State.profile) App.goChat(); }
    else if (k === "compare") { if (canContrast()) pickContrast(); else if (window.FC && FC.has()) show("fc"); else if (D) { show("dual"); fitDual(); } else if (R) show("contrast"); }
  }
  function curTheme() {
    const t = document.documentElement.getAttribute("data-theme");
    if (t === "dark" || t === "light") return t;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function syncThemeIcon() {
    const b = document.getElementById("theme-toggle"); if (!b) return;
    const dark = (PAPER.on ? PAPER.theme || curTheme() : curTheme()) === "dark";
    b.innerHTML = ""; b.appendChild(iconSvg(dark ? "sun" : "moon"));
    b.title = dark ? "Switch to light theme" : "Switch to dark theme"; b.setAttribute("aria-label", b.title);
  }
  function closeAllMenus() {
    $$("details.more-menu[open]").forEach(d => { d.open = false; });
    const fm = document.getElementById("focus-menu"); if (fm) fm.classList.add("hidden");
  }
  // in-page confirmation (replaces window.confirm): resolves true / false; Escape = cancel, Enter = confirm
  function confirmDialog(message, okLabel) {
    return new Promise(resolve => {
      const prev = document.activeElement;
      const ok = el("button", { type: "button", class: "primary" }, [okLabel || "Continue"]);
      const cancel = el("button", { type: "button", class: "ghost" }, ["Cancel"]);
      const box = el("div", { class: "x-dialog", role: "alertdialog", "aria-modal": "true", "aria-label": message }, [
        el("p", {}, [message]), el("div", { class: "x-dialog-actions" }, [cancel, ok])]);
      const back = el("div", { class: "x-backdrop" }, [box]);
      const done = v => { document.removeEventListener("keydown", key, true); back.remove(); try { prev && prev.focus(); } catch (e) {} resolve(v); };
      const key = ev => {
        if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); done(false); }
        else if (ev.key === "Enter") { ev.preventDefault(); done(true); }
        else if (ev.key === "Tab") { ev.preventDefault(); (document.activeElement === ok ? cancel : ok).focus(); }
      };
      ok.addEventListener("click", () => done(true)); cancel.addEventListener("click", () => done(false));
      back.addEventListener("click", ev => { if (ev.target === back) done(false); });
      document.addEventListener("keydown", key, true);
      document.body.appendChild(back); ok.focus();
    });
  }
  let installed = false;
  function installOnce() {
    if (installed) return; installed = true;
    syncThemeIcon();
    // every ⋯ menu: close on outside click and on Escape
    document.addEventListener("click", ev => { $$("details.more-menu[open]").forEach(d => { if (!d.contains(ev.target)) d.open = false; }); }, true);
    document.addEventListener("keydown", ev => {
      if (ev.key !== "Escape") return;
      closeAllMenus();
      const lp = document.getElementById("lp-overlay"); if (lp && !lp.classList.contains("hidden")) lp.classList.add("hidden");
    });
    // long-profile modal: backdrop click closes
    const lp = document.getElementById("lp-overlay");
    if (lp) lp.addEventListener("click", ev => { if (ev.target === lp) lp.classList.add("hidden"); });
    // "Start over": confirm when a conversation is under way
    const orig = App.restart;
    App.restart = async () => {
      const chatting = document.querySelectorAll("#chat-log .bubble").length > 0 || (D && Object.keys(D.turns || {}).length > 0 && !D.replay);
      if (chatting && !(await confirmDialog("Start over? The current conversation will be closed.", "Start over"))) return;
      orig();
    };
    // completed steps are links back
    $$("#steps [data-step]").forEach(sp => {
      sp.addEventListener("click", () => { if (sp.classList.contains("link")) goStep(sp.dataset.step); });
      sp.addEventListener("keydown", ev => { if ((ev.key === "Enter" || ev.key === " ") && sp.classList.contains("link")) { ev.preventDefault(); goStep(sp.dataset.step); } });
    });
    // theme toggle (round 10: light and dark both supported again; the choice is saved by App.toggleTheme)
    const tt = document.getElementById("theme-toggle");
    if (tt) tt.addEventListener("click", () => setTimeout(syncThemeIcon, 0));
  }
  function onPatientLoaded() {
    if (!ext()) return;
    fillDisclaimers();
    $("#contrast-btn").classList.toggle("hidden", !(flags().contrast && lib()));
    const fr = document.getElementById("fc-rec-link"); if (fr) fr.classList.toggle("hidden", !(flags().contrast && lib() && window.FC));
    if (App._ext.LiveStatus && App._ext.LiveStatus.current()) App._ext.LiveStatus.set(App._ext.LiveStatus.current());   // refresh the dots
  }

  // ======================================================================= F1: Interview + alignment
  let C = null;
  // a user-created profile's raw graph (names) as a SPEC-like network for ThemeGraph
  function rawAsSpec(g) {
    if (!g || !g.nodes) return { net: { nodes: [], edges: [] }, groups: [] };
    const nodes = g.nodes.map(n => ({ id: n.name, label: n.name, type: n.type }));
    const edges = (g.edges || []).map((e, i) => ({ id: "e" + i, source: e.from, target: e.to, evidence: e.evidence || "" }));
    const groups = (g.groups || []).map(x => ({ label: x.label, type: x.type, members: x.members || [] }));
    return { net: { nodes, edges }, groups };
  }
  async function onChatStart(r) {
    const active = !!(ext() && flags().align);
    const userProfile = active && !lib();
    $("#chat-split").classList.toggle("on", active);
    $("#chat-net-panel").classList.toggle("hidden", !active);
    $("#chat-export").classList.toggle("hidden", !active);
    $("#chat-paper").classList.toggle("hidden", !active);
    $("#chat-sub-short").classList.toggle("hidden", !active);
    $("#chat-sub").classList.toggle("hidden", active);
    $("#chat-starters").classList.toggle("hidden", !active);
    const note = $("#chat-ext-note");
    const anyFeat = ext() && (flags().align || flags().contrast || flags().dual);
    note.classList.toggle("hidden", !(anyFeat && !lib() && !active));
    note.textContent = "Utterance–network alignment, contrast patients and the dual-patient view are available for the 330 library patients (precomputed networks). They are hidden for user-created profiles.";
    document.body.classList.toggle("ext-wide", active);
    document.body.classList.toggle("app-chat", active);
    C = null;
    if (!active) return;
    const name = r.patient_name || State.patientId || "the patient";
    C = { sessionId: r.session_id, pid: State.patientId, name, tg: null, net: State.network, turns: {}, n: 0, selected: null, follow: true,
      exp: { n: new Set(), e: new Set() }, noAlign: userProfile };
    $("#chat-title").textContent = "Interview · " + name;
    $("#chat-contrast-btn").classList.toggle("hidden", !flags().contrast || userProfile);
    $("#chat-explored").classList.toggle("hidden", userProfile);
    $("#chat-export").classList.toggle("hidden", userProfile);
    const sub = $("#chat-sub-short");
    $$(".lib-note", sub).forEach(x => x.remove());
    if (userProfile) sub.appendChild(el("span", { class: "lib-note" }, [" Alignment and contrast patients are available for library patients."]));
    $("#chat-net-title").textContent = "Network";
    setNetCollapsed(false);
    legendInto($("#chat-legend"), true);
    $("#chat-overview").onclick = () => { if (C && C.tg) C.tg.overview(); };
    $("#chat-fit").onclick = () => { if (C && C.tg) C.tg.refit(); };
    detailHint(); updateExplored(); renderStarters();
    patientCard();
    const my = C;
    warmStatus(my);
    let g = { network: State.network, groups: [] };
    if (userProfile) { const raw = State.graphs && (State.graphs.long && State.graphs.long.nodes && State.graphs.long.nodes.length ? State.graphs.long : State.graphs.short);
      const x = rawAsSpec(raw); g = { network: x.net, groups: x.groups }; }
    else { try { g = await api("/api/ext/network/" + State.patientId); } catch (e) { /* unclustered fallback */ } }
    if (C !== my) return;
    C.net = g.network; updateExplored(); renderStarters();
    requestAnimationFrame(() => {
      if (C !== my) return;
      C.tg = new ThemeGraph($("#chat-graph"), C.net, g.groups, { ctl: $("#chat-graph-ctl"), onNodeClick: id => nodeClicked(id), noFocus: true });
    });
  }
  // empty chat: who the patient is (name, age, 3 presenting concerns) until the first message
  function patientCard() {
    const p = State.profile || {}, id = p.identity || {}, t = State.patientTags || {};
    const probs = (p.presenting_problems || []).slice(0, 3);
    const log = $("#chat-log"); const old = $(".chat-empty", log); if (old) old.remove();
    const card = el("div", { class: "chat-empty patient-card" }, [
      el("div", { class: "pc-head" }, [el("b", {}, [id.name || C.name]), id.age ? el("span", { class: "muted" }, [" · " + id.age]) : "", metaEl(t, 2)].filter(Boolean)),
      probs.length ? el("ul", { class: "pc-probs" }, probs.map(x => el("li", {}, [String(x)]))) : "",
      el("div", { class: "muted small" }, ["Start the conversation below, or pick a suggested question."])].filter(Boolean));
    log.appendChild(card);
  }
  // "Starting the patient model…" line in the chat while the GPU worker is queued / loading
  async function warmStatus(my) {
    const t0 = Date.now();
    let line = null;
    for (;;) {
      if (C !== my) { if (line) line.remove(); return; }
      let st = null; try { st = await api("/api/gpu/status"); } catch (e) { /* keep trying */ }
      if (st && st.ready) { if (line) { line.textContent = "Patient model ready."; setTimeout(() => line && line.remove(), 2500); } return; }
      if (!line) { line = el("div", { class: "warm-line", role: "status" }); $("#chat-log").prepend(line); }
      const sec = Math.round((Date.now() - t0) / 1000);
      const what = st && st.state === "PENDING" ? "waiting for a GPU" : "loading the patient model";
      line.textContent = "Starting the patient model (" + what + ", " + sec + " s) — usually about a minute; you can type your first message now.";
      await new Promise(r => setTimeout(r, 3000));
    }
  }
  function setNetCollapsed(c) {
    $("#chat-net-panel").classList.toggle("collapsed", c);
    $("#chat-split").classList.toggle("net-collapsed", c);
    $("#chat-net-toggle").setAttribute("aria-expanded", String(!c));
    $("#chat-net-restore").classList.toggle("hidden", !c);
    if (!c && C && C.tg) requestAnimationFrame(() => C.tg.refit());
  }
  function toggleNet() { setNetCollapsed(!$("#chat-net-panel").classList.contains("collapsed")); }
  // three suggested therapist questions, from the patient's own network (emotions / body sensations not yet discussed)
  function renderStarters() {
    if (!C) return;
    const net = C.net || { nodes: [], edges: [] };
    const deg = {}; (net.edges || []).forEach(e => { deg[e.source] = (deg[e.source] || 0) + 1; deg[e.target] = (deg[e.target] || 0) + 1; });
    const pick = type => (net.nodes || []).filter(n => n.type === type && !C.exp.n.has(n.id) && n.label.length <= 40)
      .sort((p, q) => (deg[q.id] || 0) - (deg[p.id] || 0));
    const emo = pick("Emotion"), phy = pick("Physiological Sensation");
    const qs = [C.n === 0 ? "How have things been for you lately?" : "What was going through your mind just before that?"];
    if (emo[0]) qs.push("When do you notice yourself feeling " + inSentence(emo[0].label) + "?");
    if (phy[0]) qs.push("When does " + inSentence(phy[0].label) + " tend to show up for you?");
    else if (emo[1]) qs.push("What usually happens just before you feel " + inSentence(emo[1].label) + "?");
    chipsInto($("#chat-starters"), "Try asking:", qs.slice(0, 3), q => { const t = $("#chat-text"); t.value = q; t.focus(); });
  }
  function detailHint() {          // nothing selected: the detail strip is hidden (hint lives in the ⓘ tooltip)
    const d = $("#chat-detail"); d.innerHTML = ""; d.classList.add("empty");
  }
  function updateExplored() {
    if (!C) return;
    const total = (C.net && C.net.nodes || []).length;
    $("#chat-explored").textContent = total ? "explored " + C.exp.n.size + " / " + total + " nodes" : "";
  }
  function onPatientReply(bubble, text, sid) {
    if (!C || sid !== C.sessionId || C.noAlign) return;
    const turn = ++C.n;
    const shown = shownAnswer(text);
    if (shown !== String(text || "").trim()) { const bt = bubble.querySelector(".btext"); if (bt) { bt.textContent = shown; bt.title = "Cut off at the patient model's length limit (full text in the JSON export)"; } }
    const T = { turn, bubble, text: shown, full: text, rec: null };
    C.turns[turn] = T;
    bubble.classList.add("al-bubble"); bubble.dataset.turn = turn;
    T.chips = AlignmentChips(null); bubble.appendChild(T.chips);
    bubble.addEventListener("click", ev => { if (ev.target.closest(".bubble-play, .achip, .al-retry")) return; selectTurn(turn, true); });
    runAlign(T);
  }
  function runAlign(T) {
    const my = C, t0 = performance.now();
    T.rec = null; renderChips(T);
    api("/api/ext/align", { session_id: C.sessionId, patient_id: C.pid, turn: T.turn }).then(rec => {
      if (C !== my || C.turns[T.turn] !== T) return;
      T.rec = rec.status === "unavailable" ? { unavailable: true } : rec; T.ms = Math.round(performance.now() - t0);
      renderChips(T);
      if (!T.rec.unavailable && !T.rec.unmatched) {
        const nn = [], ee = [];
        itemsOf(T.rec).forEach(it => { if (it.kind === "node") nn.push(it.id); else { ee.push(it.id); nn.push(it.source, it.target); } });
        nn.forEach(x => C.exp.n.add(x)); ee.forEach(x => C.exp.e.add(x));
        if (C.tg) { C.tg.setExplored(C.exp.n, C.exp.e); C.tg.pulse(nn, ee); }
        updateExplored();
      }
      renderStarters();
      if (C.follow || C.selected === T.turn) selectTurn(T.turn, false);
    }).catch(() => { if (C === my && C.turns[T.turn] === T) { T.rec = { unavailable: true }; renderChips(T); } });
  }
  function renderChips(T) {
    const nu = AlignmentChips(T.rec, {
      onFocus: it => { setSpans(T, [it]); if (C.tg) C.tg.mark(it.kind === "node" ? [it.id] : [], it.kind === "edge" ? [it.id] : [], true); },
      onLeave: () => restoreSelection(),
      onClick: it => {
        selectTurn(T.turn, false, it);
        if (C.tg && it.kind === "node") C.tg.expandFor([it.id]);
        if (C.tg) C.tg.mark(it.kind === "node" ? [it.id] : [], it.kind === "edge" ? [it.id] : [], true);
      },
      onRetry: () => runAlign(T),
    });
    T.chips.replaceWith(nu); T.chips = nu;
  }
  function setSpans(T, items) {
    const bt = T.bubble.querySelector(".btext"); if (!bt) return;
    const sp = spansOf(items || []);
    bt.innerHTML = items && items.length ? markSpans(T.text, sp.nodes, sp.edges) : esc(T.text);
  }
  function restoreSelection() {
    if (!C) return;
    Object.values(C.turns).forEach(o => setSpans(o, o.turn === C.selected && o.rec ? (o.only ? [o.only] : itemsOf(o.rec)) : []));
    const T = C.turns[C.selected];
    if (C.tg) {
      if (T && T.rec && !T.rec.unmatched && !T.rec.unavailable) {
        const its = T.only ? [T.only] : itemsOf(T.rec);
        C.tg.mark(its.filter(i => i.kind === "node").map(i => i.id), its.filter(i => i.kind === "edge").map(i => i.id), true);
      } else if (C.nodeSel) C.tg.mark([C.nodeSel], [], true);
      else C.tg.clear();
    }
  }
  function selectTurn(turn, user, only) {
    if (!C) return;
    const T = C.turns[turn]; if (!T) return;
    if (user) {
      if (C.selected === turn && !only) { C.selected = null; C.follow = false; Object.values(C.turns).forEach(o => o.bubble.classList.remove("al-selected")); restoreSelection(); detailHint(); return; }
      C.follow = (turn === C.n);
    }
    C.selected = turn; C.nodeSel = null;
    Object.values(C.turns).forEach(o => { o.only = null; o.bubble.classList.toggle("al-selected", o === T); });
    T.only = only || null;
    Object.values(C.turns).forEach(o => $$(".achip", o.bubble).forEach(c => c.classList.toggle("on", !!only && o === T && c.dataset.id === only.id && c.dataset.kind === only.kind)));
    restoreSelection();
    const box = $("#chat-detail"); box.innerHTML = ""; box.classList.remove("empty");
    box.appendChild(el("div", { class: "det-head" }, ["Patient reply " + turn]));
    if (!T.rec) { box.appendChild(el("div", { class: "muted small" }, ["Aligning…"])); return; }
    if (T.rec.unavailable) { box.appendChild(el("div", { class: "muted small" }, ["Alignment unavailable — use ↻ under the reply to retry."])); return; }
    if (T.rec.unmatched) { box.appendChild(el("div", { class: "det-none muted small" }, ["No network content in this reply."])); return; }
    const list = el("div", { class: "det-list" });
    itemsOf(T.rec).forEach(it => {
      const row = el("div", { class: "det-row" + (it.kind === "node" ? " clickable" : " edge") }, [
        it.kind === "node" ? typeBadge(it.type) : el("span", { class: "ebadge" }, ["→"]),
        el("span", { class: "det-lab" }, [it.label]), el("span", { class: "det-ev" }, ["“" + it.evidence + "”"])]);
      if (it.kind === "node") row.addEventListener("click", () => nodeClicked(it.id));
      list.appendChild(row);
    });
    box.appendChild(list);
    if (T.ms) box.appendChild(el("div", { class: "muted tiny-note" }, ["aligned " + (T.ms / 1000).toFixed(1) + " s after the reply"]));
  }
  function nodeClicked(nid) {
    if (!C) return;
    const node = ((C.net && C.net.nodes) || []).find(n => n.id === nid); if (!node) return;
    C.selected = null; C.follow = false; C.nodeSel = nid;
    Object.values(C.turns).forEach(o => { o.only = null; o.bubble.classList.remove("al-selected", "al-hit"); });
    const hits = [];
    Object.values(C.turns).forEach(T => {
      if (!T.rec || T.rec.unavailable) return;
      const direct = (T.rec.nodes || []).filter(n => n.node_id === nid);
      const via = (T.rec.edges || []).filter(e => e.source === nid || e.target === nid);
      if (direct.length || via.length) hits.push({ T, ev: (direct[0] || via[0]).evidence, via: !direct.length, item: direct.length ? { kind: "node", id: nid, evidence: direct[0].evidence } : { kind: "edge", id: via[0].edge_id, evidence: via[0].evidence } });
    });
    Object.values(C.turns).forEach(o => setSpans(o, []));
    hits.forEach(h => { h.T.bubble.classList.add("al-hit"); setSpans(h.T, [h.item]); });
    if (C.tg) C.tg.mark([nid], [], true);
    const box = $("#chat-detail"); box.innerHTML = ""; box.classList.remove("empty");
    box.appendChild(el("div", { class: "det-head" }, [typeBadge(node.type), " " + node.label]));
    if (!hits.length) { box.appendChild(el("div", { class: "muted small" }, ["Not mentioned yet."])); return; }
    box.appendChild(el("div", { class: "muted small" }, ["Mentioned in " + (hits.length === 1 ? "1 reply" : hits.length + " replies") + ":"]));
    const list = el("div", { class: "det-list" });
    hits.forEach(h => {
      const row = el("div", { class: "det-row clickable" }, [el("span", { class: "det-turn" }, ["Reply " + h.T.turn]),
        el("span", { class: "det-ev" }, ["“" + h.ev + "”" + (h.via ? "  (via an edge)" : "")])]);
      row.addEventListener("click", () => h.T.bubble.scrollIntoView({ behavior: "smooth", block: "center" }));
      list.appendChild(row);
    });
    box.appendChild(list);
    hits[hits.length - 1].T.bubble.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  async function exportAlignment() {
    if (!C) { toast("No alignment session."); return; }
    try { download("interview_" + C.pid + "_" + C.sessionId + ".json", await api("/api/ext/align/export?session_id=" + encodeURIComponent(C.sessionId))); }
    catch (e) { toast("Export failed: " + e.message); }
  }

  // ======================================================================= F2: contrast cards
  let R = null;
  async function findContrast() {
    if (!flags().contrast) return;
    if (!lib()) { toast("Contrast patients are available for library patients only."); return; }
    show("contrast");
    const box = $("#contrast-cards"); box.innerHTML = "";
    box.appendChild(el("div", { class: "muted" }, [el("span", { class: "spinner tiny" }), " Finding contrast patients…"]));
    const name = (State.profile && State.profile.identity && State.profile.identity.name) || State.patientId;
    $("#contrast-title").textContent = "Contrast patients for " + name;
    const qm = $("#contrast-query-meta"); qm.innerHTML = "";
    $("#contrast-sub").textContent = "Patients with similar symptoms but different causes.";
    try { R = await api("/api/ext/recommend/" + State.patientId); }
    catch (e) { box.innerHTML = ""; box.appendChild(el("p", { class: "muted" }, ["Recommendation failed: " + e.message])); return; }
    qm.appendChild(el("span", { class: "pick-name" }, [nameOf(R.query, name)])); qm.appendChild(metaEl(R.query, 2));
    box.innerHTML = "";
    if (!R.cards || !R.cards.length) { box.appendChild(el("p", { class: "muted" }, ["No contrast patient found."])); return; }
    R.cards.forEach(card => box.appendChild(renderCard(card, nameOf(R.query, name))));
    requestAnimationFrame(() => R.cards.forEach(card => drawEgo(card)));
  }
  // ---- contrast picker (Interview header button, Formulation Contrast step): 2-3 look-alike patients, no mechanisms ----
  const PICK = {};                          // pid -> recommendation response
  function canContrast() { return !!(ext() && flags().contrast && lib()); }
  const pickName = p => (State.profile && State.profile.identity && State.profile.identity.name) || p;
  function closePicker() { const m = document.getElementById("pk-back"); if (m) m.remove(); document.removeEventListener("keydown", pickKey); }
  function pickKey(ev) { if (ev.key === "Escape") closePicker(); }
  function pickShell(title) {
    closePicker();
    const back = el("div", { class: "pk-back", id: "pk-back" }), box = el("div", { class: "pk", role: "dialog", "aria-modal": "true", "aria-label": title });
    box.innerHTML = `<div class="pk-h"><h3>${esc(title)}</h3><button type="button" class="ghost small pk-x" aria-label="Close">✕</button></div><div class="pk-body"></div>`;
    back.appendChild(box); document.body.appendChild(back);
    back.addEventListener("click", ev => { if (ev.target === back) closePicker(); });
    box.querySelector(".pk-x").addEventListener("click", closePicker);
    document.addEventListener("keydown", pickKey);
    return box.querySelector(".pk-body");
  }
  function sharedOf(card) {                 // the shared presenting problem (focus concept) + other shared surface symptoms
    const sh = (card.shared_surface || []).map(x => typeof x === "string" ? { name: x } : x);
    const f = card.target_concept_id || card.default_focus;
    const main = sh.find(x => x.concept_id && x.concept_id === f) || sh[0] || { name: "" };
    return { main: main.name || "", more: sh.filter(x => x !== main).map(x => x.name).filter(Boolean).slice(0, 2) };
  }
  const PICKTXT = {};                       // pid -> {candidate: {chips, why}} (model-written once, cached server-side)
  function pickMeta(c) {                    // "Female · Adult (26–40)": omit unknown fields, never a bare number
    const t = c.patient || {}, e = (State.existing || []).find(p => "p" + String(p.index).padStart(3, "0") === c.patient_id) || {};
    const g = (t.gender || e.gender || "").trim(), band = (t.age_bucket || e.age_bucket || "").replace(/-/g, "–").trim();
    return [g && !/^unknown|unspecified$/i.test(g) ? g.charAt(0).toUpperCase() + g.slice(1).toLowerCase() : "",
            band && !/^(unknown|unspecified)$/i.test(band) ? band : ""].filter(Boolean).join(" · ");
  }
  // the shared symptom the card is about first, then the rarer (more telling) ones; common ones last
  function chipOrder(c, chips) {
    const sh = (c.shared_surface || []).map((x, k) => ({ k, name: typeof x === "string" ? x : x.name, id: typeof x === "string" ? null : x.concept_id, prev: typeof x === "string" ? 50 : (x.prevalence ?? 50) }));
    const labels = chips && chips.length === sh.length ? chips : sh.map(x => String(x.name || "").toLowerCase());
    const f = c.target_concept_id || c.default_focus;
    return sh.slice().sort((a, b) => ((b.id && b.id === f) - (a.id && a.id === f)) || (a.prev - b.prev) || (a.k - b.k)).map(x => labels[x.k]).filter(Boolean);
  }
  const pron = c => ({ female: "her", male: "him" })[(((c.patient || {}).gender) || "").toLowerCase()] || "them";
  async function pickContrast() {
    if (!canContrast()) { toast("Contrast patients are available for library patients only."); return; }
    const pid = State.patientId, name = pickName(pid);
    const body = pickShell(`Contrast ${name} with a look-alike patient`);
    body.innerHTML = `<p class="muted"><span class="spinner tiny"></span> Finding patients who look like ${esc(name)}…</p>`;
    let rec, txt;
    try {
      [rec, txt] = await Promise.all([PICK[pid] || api("/api/ext/recommend/" + pid),
        PICKTXT[pid] || api("/api/ext/recommend/" + pid + "/picker").then(r => Object.fromEntries((r.cards || []).map(c => [c.patient_id, c]))).catch(() => ({}))]);
      PICK[pid] = rec; PICKTXT[pid] = txt;
    } catch (e) { body.innerHTML = `<p class="muted">Recommendation failed: ${esc(e.message)}</p>`; return; }
    if (!document.getElementById("pk-back")) return;          // closed while loading
    const cards = (rec.cards || []).slice(0, 3);
    const round = window.FC && FC.has() && FC.state().mode === "live" && FC.state().pa === pid ? FC.state() : null;
    body.innerHTML = `<p class="pk-lead">Same presenting problem, different reasons behind it. Pick one, then ask both the same questions.</p>
      <ol class="pk-list">${cards.map((c, i) => { const t = c.patient || {}, x = txt[c.patient_id] || {}, chips = chipOrder(c, x.chips), meta = pickMeta(c);
        return `<li class="pk-card" data-i="${i}">
          <div class="pk-top"><span class="pk-rank">#${c.rank || i + 1}</span><span class="pk-name">${esc(t.name || c.patient_id)}</span>${meta ? `<span class="pk-meta">${esc(meta)}</span>` : ""}
            <button type="button" class="primary small pk-go">Contrast →</button></div>
          <div class="pk-both"><span class="pk-lab">Both:</span>${chips.map((ch, k) => `<span class="pk-chip${k >= 2 ? " more hidden" : ""}">${esc(ch)}</span>`).join("")}${chips.length > 2 ? `<button type="button" class="pk-plus" aria-expanded="false">+${chips.length - 2}</button>` : ""}</div>
          ${x.why ? `<p class="pk-why"><span class="pk-whylab">Why ${pron(c)}</span> ${esc(x.why)}</p>` : ""}
          ${c.probe_question ? `<button type="button" class="pk-qbtn" aria-expanded="false">▸ Show a question to start with</button><div class="pk-q hidden">“${esc(c.probe_question)}”</div>` : ""}
        </li>`; }).join("")}</ol>
      <div class="pk-foot"><span class="pk-note">Each patient's causes stay hidden until you finish the round.</span>
        ${round ? `<button type="button" class="ghost small" id="pk-return">Return to your round with ${esc(round.names.b)}</button>` : ""}
        <button type="button" class="ghost small" id="pk-all">Browse all contrast patients</button></div>`;
    body.querySelectorAll(".pk-card").forEach(li => {
      const c = cards[+li.dataset.i];
      li.querySelector(".pk-go").addEventListener("click", () => pickStart(c));
      const plus = li.querySelector(".pk-plus");
      if (plus) plus.addEventListener("click", () => { li.querySelectorAll(".pk-chip.more").forEach(x => x.classList.remove("hidden")); plus.remove(); });
      const qb = li.querySelector(".pk-qbtn");
      if (qb) qb.addEventListener("click", () => { const q = li.querySelector(".pk-q"), open = q.classList.toggle("hidden") === false;
        qb.textContent = open ? "▾ Hide question" : "▸ Show a question to start with"; qb.setAttribute("aria-expanded", open); li.classList.toggle("open", open); });
    });
    body.querySelector("#pk-all").addEventListener("click", () => { closePicker(); findContrast(); });
    const rt = body.querySelector("#pk-return"); if (rt) rt.addEventListener("click", () => { closePicker(); show("fc"); });
  }
  function pickStart(card) {
    const pid = State.patientId, name = pickName(pid), bname = (card.patient && card.patient.name) || card.patient_id;
    const qa = window.Interview && Interview.liveQA ? Interview.liveQA(pid) : [], live = App.liveOn();
    const body = pickShell(`${name} and ${bname}`);
    body.innerHTML = `<p class="pk-lead">How do you want to start?</p>
      <div class="pk-opts">
        <button type="button" class="pk-opt" id="pk-fresh"${live ? "" : " disabled"}><b>Start fresh</b><span>An empty trajectory: ask both patients new questions.</span></button>
        <button type="button" class="pk-opt" id="pk-replay"${live && qa.length ? "" : " disabled"}><b>Ask ${esc(bname)} the questions you already asked ${esc(name)}</b>
          <span>${qa.length ? `${qa.length} question${qa.length === 1 ? "" : "s"} from your interview. ${esc(name)}'s answers come from that interview; ${esc(bname)} answers them now.` : `You have not asked ${esc(name)} anything in a live interview yet.`}</span></button>
      </div>
      ${live ? "" : `<p class="pk-off">${esc(App.liveOffMsg())}</p><button type="button" class="ghost small" id="pk-rec">See a recorded Formulation Contrast example</button>`}
      <div class="pk-foot"><button type="button" class="ghost small" id="pk-backlist">← Other patients</button></div>`;
    const focus = card.target_concept_id || card.default_focus, from = { pid, name };
    const presenting = sharedOf(card).main;
    body.querySelector("#pk-fresh").addEventListener("click", () => { closePicker(); FC.openLive(pid, card.patient_id, card.probe_question, focus, { from, presenting }); });
    body.querySelector("#pk-replay").addEventListener("click", () => reviewQuestions(card, qa, { from, presenting, focus }));
    body.querySelector("#pk-backlist").addEventListener("click", pickContrast);
    const r = body.querySelector("#pk-rec"); if (r) r.addEventListener("click", () => { closePicker(); FC.openRecorded(); });
  }

  // Before replaying the trainee's Interview questions to the contrast patient: names swapped, and the questions that
  // depend on the first patient's own details flagged; the trainee can skip or edit each one.
  async function reviewQuestions(card, qa, o) {
    const pid = State.patientId, name = pickName(pid), bname = (card.patient && card.patient.name) || card.patient_id;
    const body = pickShell(`Ask ${bname} your questions to ${name}`);
    body.innerHTML = `<p class="muted"><span class="spinner tiny"></span> Adapting your ${qa.length} questions for ${esc(bname)}…</p>`;
    let items;
    try { items = (await App._ext.apiT("/api/ext/fc/adapt", { patient_a: pid, patient_b: card.patient_id, questions: qa.map(x => x.q) }, 60000)).items; }
    catch (e) { items = qa.map(x => ({ original: x.q, adapted: x.q.replace(new RegExp("\\b" + name + "\\b", "g"), bname), flag: false })); }
    if (!document.getElementById("pk-back")) return;
    const nflag = items.filter(x => x.flag).length;
    body.innerHTML = `<p class="pk-lead">${esc(name)}'s answers come from your interview; ${esc(bname)} answers these now. ${nflag ? `<b>${nflag}</b> question${nflag === 1 ? " depends" : "s depend"} on details only ${esc(name)} mentioned: they start unticked with a neutral wording; tick to include, or edit.` : "Edit any question before replaying."}</p>
      <ol class="rv-list">${items.map((x, i) => `<li class="rv-q${x.flag ? " flagged" : ""}">
          <label class="rv-inc"><input type="checkbox" data-i="${i}"${x.flag ? "" : " checked"}><span>Q${i + 1}</span></label>
          <div class="rv-body"><input type="text" class="rv-text" data-i="${i}" value="${esc(x.flag && x.neutral ? x.neutral : x.adapted)}" aria-label="Question ${i + 1} for ${esc(bname)}" title="Asked to ${esc(name)}: ${esc(x.original)}">
            ${x.flag ? `<div class="rv-flag">Depends on ${esc(name)}'s details${x.reason ? ": " + esc(x.reason) : ""}</div>` : ""}</div></li>`).join("")}</ol>
      <div class="pk-foot"><button type="button" class="ghost small" id="rv-back">← Back</button><button type="button" class="primary small" id="rv-go"></button></div>`;
    const go = body.querySelector("#rv-go"), boxes = [...body.querySelectorAll(".rv-inc input")], texts = [...body.querySelectorAll(".rv-text")];
    const count = () => { const n = boxes.filter((b, i) => b.checked && texts[i].value.trim()).length; go.textContent = `Replay ${n} question${n === 1 ? "" : "s"}`; go.disabled = !n; };
    boxes.forEach(b => b.addEventListener("change", count)); texts.forEach(t => t.addEventListener("input", count)); count();
    body.querySelector("#rv-back").addEventListener("click", () => pickStart(card));
    go.addEventListener("click", () => {
      const list = qa.map((x, i) => ({ q: texts[i].value.trim(), a: x.a, asked: x.q })).filter((x, i) => boxes[i].checked && x.q);
      closePicker(); FC.openLive(pid, card.patient_id, null, o.focus, { from: o.from, presenting: o.presenting, replay: list });
    });
  }

  // "<name> <symptom> because: <causes>" from the card's valid citations (the symptom most causes point into)
  function becauseOf(card, side) {
    const cites = (card.citations || []).filter(x => x.valid && x.side === side);
    if (!cites.length) return null;
    const by = {}; cites.forEach(x => { (by[x.target] = by[x.target] || []).push(x); });
    const target = Object.keys(by).sort((p, q) => by[q].length - by[p].length)[0];
    const types = {}; ((card.ego_graphs[side] || {}).nodes || []).forEach(n => { types[n.label] = n.type; });
    return { target, causes: by[target].map(x => ({ label: x.source, type: types[x.source], edge: x.edge_id })), more: cites.length - by[target].length };
  }
  function renderCard(card, qname) {
    const t = card.patient || {};
    const cname = nameOf(t, card.patient_id);
    const c = el("div", { class: "ccard" }); card._el = c;
    const fmt = v => (v == null ? "–" : Number(v).toFixed(2));
    c.appendChild(el("div", { class: "ccard-head" }, [
      el("div", { class: "ccard-who" }, [el("span", { class: "ccard-rank" }, ["#" + card.rank]), el("span", { class: "pick-name" }, [cname]), metaEl(t, 2)]),
      el("span", { class: "sd-badge", tabindex: "0",
        title: "Surface similarity S = " + fmt(card.surface_score) + " (overlap of emotions, behaviours, body sensations)\nMechanism difference D = " + fmt(card.mechanism_score) + " (how differently the shared symptoms are caused)" },
        ["Similar symptoms · Different causes"])]));
    if (card.teaching_point) c.appendChild(el("div", { class: "ccard-teach" }, [card.teaching_point]));
    // shared symptoms = the selector of this card's graph: selected chip first ("shown below"), top 3 + "+N more"
    const shared = card.shared_surface || [];
    const counts = card.distinctive_counts || {};
    const sh = el("div", { class: "ccard-shared", role: "radiogroup", "aria-label": "Shared symptom shown in the graph" });
    card._focus = card.target_concept_id || card.default_focus || null;   // round 10: the stored target concept first
    let expanded = false;
    const chip = s => {
      const on = s.concept_id === card._focus, n = counts[s.concept_id];
      const b = el("button", { type: "button", role: "radio", "aria-checked": String(on), class: "chip chip-sel" + (on ? " on" : "") + (n && Math.min(n[0], n[1]) === 0 ? " weak" : ""),
        title: qname + ": " + s.query_nodes.join("; ") + "\n" + cname + ": " + s.candidate_nodes.join("; ") + (on ? "" : "\nClick to show this symptom below") },
        [sentenceCase(s.name)].concat(on ? [el("span", { class: "chip-hint" }, ["shown below"])] : []));
      b.addEventListener("click", () => { if (!card._cg || s.concept_id === card._focus) return; card._cg.select(s.concept_id); });
      return b;
    };
    card._chips = () => {
      sh.innerHTML = "";
      const sel = shared.filter(s => s.concept_id === card._focus), rest = shared.filter(s => s.concept_id !== card._focus);
      const order = sel.concat(rest), k = expanded ? order.length : 3;
      order.slice(0, k).forEach(s => sh.appendChild(chip(s)));
      if (order.length > k) {
        const more = el("button", { type: "button", class: "chip chip-more", title: order.slice(k).map(s => sentenceCase(s.name)).join(", ") }, ["+" + (order.length - k) + " more"]);
        more.addEventListener("click", () => { expanded = true; card._chips(); });
        sh.appendChild(more);
      }
    };
    card._chips();
    c.appendChild(sh);
    const mini = el("div", { class: "ccard-mini" }, [el("div", { class: "muted small" }, [el("span", { class: "spinner tiny" }), " loading…"])]);
    card._mini = mini; c.appendChild(mini);
    const labels = {}; (card.citations || []).forEach(x => { labels[x.side + ":" + x.edge_id] = x.valid ? x.source + " → " + x.target : "(invalid citation)"; });
    const why = el("details", { class: "ccard-why" }, [el("summary", {}, ["Why?"]),
      el("div", { class: "ccard-rationale", html: citeHTML(card.rationale, k => k === "Q" ? "query" : "candidate", (s, e) => labels[s + ":" + e], { noSide: true }) })]);
    c.appendChild(why);
    c.addEventListener("click", ev => {
      const b = ev.target.closest("[data-edge]"); if (!b || !card._cg) return;
      const side = b.dataset.side === "query" ? "a" : "b", eid = b.dataset.edge;
      $$("[data-edge]", c).forEach(x => x.classList.toggle("on", x === b));
      card._cg.highlight(side, [], [eid], true);
    });
    const act = el("div", { class: "ccard-probe" }, [el("span", { class: "ccard-label" }, ["Probe question"]), el("div", { class: "probe-q" }, ["“" + card.probe_question + "”"])]);
    if (flags().dual) {
      const go = el("button", { class: "primary small", type: "button" }, ["Compare side by side →"]);
      go.addEventListener("click", () => {
        const f = card._focus, s = shared.find(x => x.concept_id === f);
        const probe = (!f || f === card.target_concept_id || !card.target_concept_id && f === card.default_focus) ? card.probe_question : probeFor(s ? s.name : "");
        openDual(State.patientId, card.patient_id, probe, { a: State.network }, false, f);
      });
      if (!App.liveOn()) {   // browse mode: no live sessions; offer the recorded examples instead
        go.disabled = true; go.title = App.liveOffMsg();
        const ex = el("button", { class: "ghost small", type: "button" }, ["See a recorded example"]);
        ex.addEventListener("click", () => openReplay());
        act.appendChild(el("div", { class: "actions" }, [ex, go]));
      } else act.appendChild(el("div", { class: "actions" }, [go]));
    }
    c.appendChild(act);
    return c;
  }
  // a neutral probe for a symptom the recommendation's own probe was not written for (same wording for both patients,
  // open, presupposes no cause)
  function probeFor(name) {
    if (!name) return "";
    const n = sentenceCase(name), t = n.charAt(0).toLowerCase() + n.slice(1);
    return "Think of the last time you noticed " + t + ". What was happening just before, and what was going through your mind?";
  }
  // mini contrast graph for a card: opens on the card's default focus symptom (backend: the probe's target symptom,
  // unless either patient has no distinctive cause into it)
  async function drawEgo(card) {
    const box = card._mini; if (!box) return;
    let cg;
    try { cg = await api("/api/ext/contrast-graph/" + R.query_id + "/" + card.patient_id); }
    catch (e) { box.innerHTML = ""; box.appendChild(el("div", { class: "muted small" }, ["Contrast graph unavailable: " + e.message])); return; }
    const cited = { a: new Set(), b: new Set() };
    (card.citations || []).filter(x => x.valid).forEach(x => cited[x.side === "query" ? "a" : "b"].add(x.edge_id));
    box.innerHTML = "";
    card._cg = new ContrastGraph(box, cg, null, { a: nameOf(R.query, "A"), b: nameOf(card.patient, card.patient_id) },
      { mini: true, plain: true, initial: card.target_concept_id || card.default_focus, cited, probe: card.probe_question,
        onFocus: c => { card._focus = c ? c.concept_id : null; if (card._chips) card._chips(); } });
  }
  function exportRecommendation() {
    if (!R) { toast("No recommendation yet."); return; }
    download("recommendation_" + R.query_id + ".json", JSON.parse(JSON.stringify(R, (k, v) => (k.startsWith("_") ? undefined : v))));
  }

  // ======================================================================= F3: dual view (Compare)
  // Simplified page: "<A> vs <B> · <focus symptom> ▾" + ⋯ menu; contrast graph of the focus symptom;
  // turns (question / two answers / one difference sentence); one input with [Both | A | B].
  let D = null;
  const SESS = {};          // "<a>|<b>" -> D : going Back and reopening the same pair restores the conversation
  const emptyState = () => ({ a: { n: new Set(), e: new Set() }, b: { n: new Set(), e: new Set() } });
  async function openDual(a, b, probe, nets, fresh, focus) {
    if (window.FC) { if (!App.liveOn()) { toast(App.liveOffMsg()); return; } return FC.openLive(a, b, probe, focus); }   // Formulation Contrast
    if (!App.liveOn()) { toast(App.LIVE_OFF_MSG); return; }
    if (!flags().dual) return;
    show("dual");
    const key = a + "|" + b;
    if (!fresh && SESS[key] && SESS[key].sid) {
      restoreDual(SESS[key]);
      if (focus && D.cg && !D.replay) { D.cg.select(focus); D.focus = focus; if (!Object.keys(D.turns).length && probe) { D.probe = probe; setSuggestion(probe); } }
      return;
    }
    if (D && D.paper) setPaper(false);
    D = { sid: null, pids: { a, b }, nets: Object.assign({}, nets || {}), groups: {}, names: { a, b }, tags: {}, cg: null, full: {}, turns: {}, rows: {},
      exp: emptyState(), busy: false, probe: probe || "", pin: null, tab: "a", target: "both", suggestion: probe || "", key, focus: focus || null,
      starting: true, queued: null };
    SESS[key] = D;
    const my = D;
    setReplayUI(false);
    ["#dual-turns", "#dual-tabs", "#dual-full-graphs", "#focus-menu", "#dual-traj"].forEach(s => { $(s).innerHTML = ""; });
    setFull(false); closeMenus();
    $("#focus-name").textContent = "…";
    $("#dual-cg").innerHTML = "<div class='muted small'><span class='spinner tiny'></span> Building the contrast graph…</div>";
    $("#dual-shared-text").value = "";
    setTarget("both");
    setDualBusy(true, "");
    try {
      const got = await Promise.all(SIDES.map(s => api("/api/ext/network/" + D.pids[s])));
      if (D !== my) return;
      SIDES.forEach((s, i) => { D.nets[s] = got[i].network; D.groups[s] = got[i].groups; D.tags[s] = got[i].tags; D.names[s] = nameOf(got[i].tags, D.pids[s]); });
    } catch (e) { D.starting = false; setDualBusy(false, "Failed to load networks: " + e.message); return; }
    renderDualHead(); renderEmpty(); renderSeg(); setSuggestion(D.probe);
    try {
      const cg = await api("/api/ext/contrast-graph/" + a + "/" + b);
      if (D !== my) return;
      $("#dual-cg").innerHTML = "";
      D.cgData = cg;
      D.cg = new ContrastGraph($("#dual-cg"), cg, D.nets, D.names, { probe: D.probe, plain: true, onFocus: onFocusChange, panel: $("#cg-panel"), initial: D.focus });
    } catch (e) { $("#dual-cg").innerHTML = ""; $("#dual-cg").appendChild(el("div", { class: "muted small" }, ["Contrast graph unavailable: " + e.message])); }
    setDualBusy(true, "Starting both patient sessions (the patient model may need a minute to warm up)…");
    let r;
    try { r = await api("/api/ext/f3/start", { patient_a: a, patient_b: b, probe_question: probe || null }); }
    catch (e) { if (D === my) { D.starting = false; D.queued = null; setDualBusy(false, "Could not start the dual session: " + e.message); } return; }
    if (D !== my) return;
    D.sid = r.session_id; D.starting = false;
    if (!D.suggestion && r.suggested_question) setSuggestion(r.suggested_question);
    setDualBusy(false, r.same_patient ? "Both columns are the same patient (two independent sessions)." : "");
    if (D.queued) { const qd = D.queued; D.queued = null; $("#dual-shared-text").value = qd.q; setTarget(qd.target); sendDual(); }
    renderTraj(); fitDual();
  }
  // reopen a kept session: rebuild the view from its turns (no new patient sessions)
  function restoreDual(saved) {
    D = saved;
    setReplayUI(!!D.replay);
    ["#dual-turns", "#dual-tabs", "#dual-full-graphs", "#focus-menu"].forEach(s => { $(s).innerHTML = ""; });
    D.full = {}; setFull(false); closeMenus();
    renderDualHead(); renderSeg();
    const turns = Object.values(D.turns).sort((x, y) => x.turn_no - y.turn_no);
    D.turns = {}; D.rows = {}; D.exp = emptyState(); D.pin = null;
    if (!turns.length) renderEmpty();
    $("#dual-cg").innerHTML = "";
    if (D.cgData) D.cg = new ContrastGraph($("#dual-cg"), D.cgData, D.nets, D.names, { probe: D.probe, plain: true, onFocus: onFocusChange, panel: $("#cg-panel"), initial: D.focus });
    turns.forEach(v => { upsertTurn(v); if (v.status !== "done" && v.status !== "error" && !D.replay) pollTurn(v.turn_no); });
    if (D.cg) D.cg.setState(D.exp, null);
    setSuggestion(D.suggestion);
    setDualBusy(false, "");
    setPaper(false); renderTraj(); fitDual();
  }
  async function newSession() {
    if (!D || D.replay) return;
    if (Object.keys(D.turns).length && !(await confirmDialog("Start a new session with these two patients? The current conversation will be cleared.", "New session"))) return;
    const old = D;
    if (old.sid) api("/api/ext/f3/close", { session_id: old.sid }).catch(() => {});
    delete SESS[old.key];
    openDual(old.pids.a, old.pids.b, old.probe, { a: old.nets.a, b: old.nets.b }, true);
  }
  // ---- replay mode: a frozen case (data/replay), step / play, no LLM or GPU calls
  let RP = null;
  async function openReplay(caseId) {
    if (!flags().dual) return;
    if (window.FC) return FC.openRecorded(caseId);                 // Formulation Contrast
    let list = [], idx = {};
    try { idx = await api("/api/ext/replay"); list = idx.cases || []; } catch (e) { toast("Examples unavailable: " + e.message); return; }
    if (!list.length) { toast("No examples available."); return; }
    const id = caseId || idx.default || list[0].case_id;
    let c;
    try { c = await api("/api/ext/replay/" + encodeURIComponent(id)); } catch (e) { toast("Example unavailable: " + e.message); return; }
    // what "Exit example" returns to: a live comparison that was open, else the previous screen
    const prev = RP ? RP.prev : (D && !D.replay ? D : null), from = RP ? RP.from : State.screen;
    if (RP && RP.timer) clearInterval(RP.timer);
    show("dual");
    D = { sid: null, replay: true, pids: { a: c.patient_a, b: c.patient_b }, nets: {}, groups: {}, names: c.names, tags: c.tags, cg: null, full: {}, turns: {}, rows: {},
      exp: emptyState(), busy: false, probe: c.probe, pin: null, tab: "a", target: "both", suggestion: "", key: "replay:" + id, focus: c.focus || null, paper: false };
    RP = { c, list, step: 0, timer: null, prev, from };
    setPaper(false);
    ["#dual-turns", "#dual-tabs", "#dual-full-graphs", "#focus-menu"].forEach(s => { $(s).innerHTML = ""; });
    $("#dual-traj").innerHTML = "";
    setFull(false); closeMenus(); setReplayUI(true);
    renderDualHead(); renderEmpty();
    $("#dual-empty").textContent = "Example conversation — press Next or Play.";
    try {
      const got = await Promise.all(["a", "b"].map(s => api("/api/ext/network/" + D.pids[s])));
      ["a", "b"].forEach((s, i) => { D.nets[s] = got[i].network; D.groups[s] = got[i].groups; });
      D.cgData = await api("/api/ext/contrast-graph/" + D.pids.a + "/" + D.pids.b);
      $("#dual-cg").innerHTML = "";
      D.cg = new ContrastGraph($("#dual-cg"), D.cgData, D.nets, D.names, { probe: D.probe, plain: true, onFocus: onFocusChange, panel: $("#cg-panel"), initial: D.focus });
    } catch (e) { $("#dual-cg").textContent = "Contrast graph unavailable: " + e.message; }
    renderReplayBar(); renderTraj(); fitDual();
  }
  // a frozen turn record -> the live turn view shape (labels / edge endpoints from the networks)
  function replayView(i) {
    const t = RP.c.turns[i];
    const al = (s, a) => {
      const edges = {}; ((D.nets[s] && D.nets[s].edges) || []).forEach(e => { edges[e.id] = e; });
      return { nodes: (a.nodes || []).map(n => ({ node_id: n.node_id, evidence: n.evidence })),
               edges: (a.edges || []).map(e => ({ edge_id: e.edge_id, evidence: e.evidence, source: (edges[e.edge_id] || {}).source, target: (edges[e.edge_id] || {}).target })),
               no_match: !!a.no_match, unmatched: !!a.no_match };
    };
    return { turn_no: i + 1, kind: "shared", side: "both", question: t.question, status: "done",
      a: { response: t.a.response, alignment: al("a", t.a.alignment || {}) }, b: { response: t.b.response, alignment: al("b", t.b.alignment || {}) },
      diff: { diff_summary: t.diff_summary || "" } };
  }
  function replayStep(dir) {
    if (!RP || !D || !D.replay) return;
    const n = RP.c.turns.length;
    const target = Math.max(0, Math.min(n, RP.step + dir));
    if (target < RP.step) {           // step back: rebuild from scratch up to target
      D.turns = {}; D.rows = {}; D.exp = emptyState(); D.pin = null; $("#dual-turns").innerHTML = "";
      if (D.cgData) { $("#dual-cg").innerHTML = ""; D.cg = new ContrastGraph($("#dual-cg"), D.cgData, D.nets, D.names, { probe: D.probe, plain: true, onFocus: onFocusChange, panel: $("#cg-panel"), initial: D.focus }); if (D.paper) D.cg.setExpanded(true); }
      for (let i = 0; i < target; i++) upsertTurn(replayView(i));
      if (!target) { renderEmpty(); $("#dual-empty").textContent = "Example conversation — press Next or Play."; }
    } else for (let i = RP.step; i < target; i++) upsertTurn(replayView(i));
    RP.step = target;
    renderReplayBar(); renderTraj(); fitDual();
    if (PAPER.on) { applyPaperHide(); renderPaperBar(); }
  }
  function replayPlay() {
    if (!RP) return;
    if (RP.timer) { clearInterval(RP.timer); RP.timer = null; renderReplayBar(); return; }
    if (RP.step >= RP.c.turns.length) replayStep(-RP.step);
    replayStep(1);
    RP.timer = setInterval(() => { if (!RP || RP.step >= RP.c.turns.length) { clearInterval(RP.timer); if (RP) RP.timer = null; renderReplayBar(); return; } replayStep(1); }, 3500);
    renderReplayBar();
  }
  function renderReplayBar() {
    const bar = $("#replay-bar"); bar.innerHTML = "";
    if (!RP) return;
    const n = RP.c.turns.length;
    const btn = (label, fn, dis, cls) => { const b = el("button", { type: "button", class: "ghost small" + (cls ? " " + cls : "") }, [label]); b.disabled = !!dis; b.addEventListener("click", fn); return b; };
    bar.appendChild(el("span", { class: "replay-badge" }, ["Example"]));
    bar.appendChild(btn("◀ Back", () => replayStep(-1), RP.step === 0));
    bar.appendChild(btn(RP.timer ? "Pause" : "Play", replayPlay, false, "primary-ish"));
    bar.appendChild(btn("Next ▶", () => replayStep(1), RP.step >= n));
    bar.appendChild(el("span", { class: "muted small" }, ["Turn " + RP.step + " of " + n]));
    const sel = el("select", { "aria-label": "Example" });
    RP.list.forEach(x => { const o = el("option", { value: x.case_id }, [x.title]); if (x.case_id === RP.c.case_id) o.selected = true; sel.appendChild(o); });
    sel.addEventListener("change", () => { if (RP.timer) clearInterval(RP.timer); openReplay(sel.value); });
    bar.appendChild(sel);
    bar.appendChild(el("span", { class: "muted small replay-src" }, ["Recorded run — no model calls"]));
    bar.appendChild(btn("Paper view", () => setPaper(true)));
    bar.appendChild(btn("Exit example", exitReplay, false, "exit-ex"));
  }
  // leave the example: back to the live comparison that was open, else to the screen it was opened from
  function exitReplay() {
    if (!RP) return;
    const prev = RP.prev, from = RP.from;
    setPaper(false); setReplayUI(false);
    if (prev) { show("dual", true); restoreDual(prev); return; }
    D = null;
    if (from && from !== "dual") show(from, true, true);
    else if (State.history.length) App.back();
    else show("choose", true);
    const h = State.history; if (h.length && h[h.length - 1] === State.screen) h.pop();
  }
  // ---- Paper view: one static, readable composition (graph with every cause, trajectory, all turns)
  function dualPaper(on) {
    if (!D) on = false;
    const was = !!(D && D.paper);
    if (D) D.paper = !!on;
    if (on && !was && RP && D.replay) {
      if (RP.timer) { clearInterval(RP.timer); RP.timer = null; }
      RP.paperFrom = RP.step;
      if (RP.step < RP.c.turns.length) replayStep(RP.c.turns.length - RP.step);
    }
    if (D && D.cg && on !== was) D.cg.setExpanded(!!on);
    if (!on && was && RP && D && D.replay && RP.paperFrom != null) { const back = RP.paperFrom; RP.paperFrom = null; replayStep(back - RP.step); }
    if (!on && D) { D.paperHide = null; applyPaperHide(); }
    if (on && D) SIDES.forEach(s => { if (D.full[s]) D.full[s].expandFor([...D.exp[s].n]); });
    renderTraj(); fitDual();
  }
  // Paper view for figures (round 10): light theme whatever the user's setting (not saved), content 1600 px wide,
  // no input bar / suggestions / ⋯ menus / step header; Compare: "Show turns" T1…Tn; Interview: every theme that
  // contains a traced node is expanded so the real node labels show.
  const PAPER = { on: false, screen: null, theme: null };
  const PAPER_SCREENS = ["chat", "contrast", "dual"];
  function setPaper(on) {
    on = !!on;
    const scr = State.screen;
    if (on && !PAPER_SCREENS.includes(scr)) on = false;
    if (on === PAPER.on && (!on || PAPER.screen === scr)) { if (!on && scr === "dual" && D && D.paper) dualPaper(false); return; }
    const was = PAPER.screen, root = document.documentElement;
    if (on) { PAPER.theme = root.getAttribute("data-theme"); root.setAttribute("data-theme", "light"); }
    else if (PAPER.on) { if (PAPER.theme) root.setAttribute("data-theme", PAPER.theme); else root.removeAttribute("data-theme"); }
    PAPER.on = on; PAPER.screen = on ? scr : null;
    document.body.classList.toggle("paper-view", on);
    PAPER_SCREENS.forEach(x => document.body.classList.toggle("paper-" + x, on && scr === x));
    const which = on ? scr : was;
    if (which === "dual") dualPaper(on);
    if (which === "chat") chatPaper(on);
    renderPaperBar();
    $$(".mi-paper").forEach(x => { x.textContent = on ? "Exit paper view" : "Paper view"; });
    syncThemeIcon();
    if (on) window.scrollTo(0, 0);
  }
  function chatPaper(on) {
    if (!C || !C.tg) return;
    if (on) C.tg.expandFor([...C.exp.n]);
    requestAnimationFrame(() => requestAnimationFrame(() => { if (C && C.tg) { C.tg.setLabelLen(on ? 44 : 18); C.tg.refit(); } }));
  }
  function applyPaperHide() {
    if (!D) return;
    Object.keys(D.rows).forEach(k => D.rows[k].classList.toggle("paper-hide", !!(D.paperHide && D.paperHide.has(Number(k)))));
  }
  function renderPaperBar() {
    const bar = $("#paper-bar"); bar.innerHTML = "";
    bar.classList.toggle("hidden", !PAPER.on);
    if (!PAPER.on) return;
    bar.appendChild(el("span", { class: "paper-badge" }, ["Paper view"]));
    if (PAPER.screen === "dual" && D) {
      const turns = Object.keys(D.rows).map(Number).sort((x, y) => x - y);
      if (turns.length) {
        const box = el("span", { class: "paper-turns", role: "group", "aria-label": "Show turns" }, [el("span", { class: "muted small" }, ["Show turns:"])]);
        turns.forEach(i => {
          const cb = el("input", { type: "checkbox", "data-turn": String(i) }); cb.checked = !(D.paperHide && D.paperHide.has(i));
          cb.addEventListener("change", () => { D.paperHide = D.paperHide || new Set(); if (cb.checked) D.paperHide.delete(i); else D.paperHide.add(i); applyPaperHide(); });
          box.appendChild(el("label", { class: "paper-turn" }, [cb, "T" + i]));
        });
        bar.appendChild(box);
      }
    }
    bar.appendChild(el("span", { class: "muted small paper-hint" }, ["Light theme · 1600 px wide · take screenshots at 2× (browser zoom 200 % or a 2× device scale)."]));
    const x = el("button", { type: "button", class: "ghost small" }, ["Exit paper view"]);
    x.addEventListener("click", () => setPaper(false));
    bar.appendChild(x);
  }
  function togglePaper() { setPaper(!PAPER.on); }
  function setReplayUI(on) {
    if (!on && D && D.paper) setPaper(false);
    if (!on && RP && RP.timer) clearInterval(RP.timer);
    if (!on) RP = null;
    $("#screen-dual .dual-page").classList.toggle("replay", on);
    $("#replay-bar").classList.toggle("hidden", !on);
    $("#mi-new").classList.toggle("hidden", on);
    if (!on) $("#replay-bar").innerHTML = "";
  }
  function setDualBusy(b, msg) {
    if (!D) return; D.busy = b;
    const warm = !!D.starting && !D.sid;
    const send = $("#dual-shared-send");
    send.disabled = !warm && (b || !D.sid);
    send.textContent = warm ? (D.queued ? "Queued…" : "Warming up…") : "Send";
    send.classList.toggle("warming", warm);
    const st = $("#dual-status");
    st.innerHTML = msg ? (b ? "<span class='spinner tiny'></span> " : "") + esc(msg) : "";
    st.classList.toggle("hidden", !msg);
  }
  function metaText(t) {
    const parts = metaParts(t), tags = ((t && t.disorders) || []).filter(d => !missing(d));
    return parts.concat(tags.length ? [tags.join(", ")] : []).join(" · ");
  }
  function renderDualHead() {
    SIDES.forEach(s => { const n = $("#dual-name-" + s); n.textContent = D.names[s]; n.title = metaText(D.tags[s]); });
    const tabs = $("#dual-tabs"); tabs.innerHTML = "";          // narrow screens only (CSS)
    SIDES.forEach(s => {
      const b = el("button", { type: "button", class: "dual-tab side-" + s, role: "tab", "data-side": s }, [D.names[s]]);
      b.addEventListener("click", () => setTab(s));
      tabs.appendChild(b);
    });
    setTab(D.tab);
  }
  function setTab(s) {
    if (!D) return;
    D.tab = s;
    $("#screen-dual .dual-page").removeAttribute("data-tab");   // narrow screens stack both patients (no tab hides one)
    $$("#dual-tabs .dual-tab").forEach(b => { const on = b.dataset.side === s; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); });
  }
  // ---- title: focus symptom dropdown + ⋯ menu
  function backToContrast() {
    const h = State.history;
    if (h.length && h[h.length - 1] === "contrast") { App.back(); return; }
    show("contrast", false, true);
  }
  function onFocusChange(c, mode) {
    if (D && c && mode !== "all") D.focus = c.concept_id;
    const bc = document.getElementById("dual-back-contrast");
    if (bc) bc.classList.toggle("hidden", !(D && !D.replay && R && R.query_id === D.pids.a && (R.cards || []).some(x => x.patient_id === D.pids.b)));
    $("#focus-name").textContent = mode === "all" ? "All shared symptoms" : (c ? c.name : "–");
    $("#mi-all").textContent = mode === "all" ? "Show one symptom" : "Show all symptoms";
    if (D) { renderTraj(); fitDual(); }
  }
  function toggleFocusMenu() {
    const m = $("#focus-menu"), open = m.classList.contains("hidden");
    closeMenus();
    if (!open || !D || !D.cg) return;
    m.innerHTML = "";
    const cur = D.cg.selectedId();
    D.cg.symptoms().forEach(c => {
      const b = el("button", { type: "button", role: "option", class: "focus-opt" + (c.id === cur ? " on" : ""), "aria-selected": String(c.id === cur) },
        [el("span", {}, [c.name]), c.probe ? el("span", { class: "muted small" }, [" · probe"]) : "",
         c.discussed.length ? el("i", { class: "opt-dot", title: "came up in the conversation" }) : ""].filter(Boolean));
      b.addEventListener("click", () => { D.cg.select(c.id); closeMenus(); });
      m.appendChild(b);
    });
    m.classList.remove("hidden"); $("#focus-btn").setAttribute("aria-expanded", "true");
  }
  function closeMenus() {
    $("#focus-menu").classList.add("hidden"); $("#focus-btn").setAttribute("aria-expanded", "false");
    $("#dual-more").open = false;
  }
  function toggleAllSymptoms() { if (D && D.cg) D.cg.setAll(!D.cg.isAll()); }
  document.addEventListener("click", ev => {
    if (!ev.target.closest(".focus-wrap")) { const m = document.getElementById("focus-menu"); if (m) m.classList.add("hidden"); }
    if (!ev.target.closest("#dual-more")) { const d = document.getElementById("dual-more"); if (d) d.open = false; }
  });
  document.addEventListener("keydown", ev => { if (ev.key === "Escape" && D) closeMenus(); });
  function setFull(on) {
    $("#dual-full-graphs").classList.toggle("hidden", !on);
    $("#dual-cg").classList.toggle("hidden", on);
    $("#mi-full").textContent = on ? "Show contrast graph" : "Show full networks";
    $("#cg-body").classList.toggle("full", on);
    if (on) drawFull();
  }
  function toggleFull() { setFull($("#dual-full-graphs").classList.contains("hidden")); }
  // ---- input: one box, [Both | A | B], the suggestion as placeholder (Send on an empty box asks it)
  function renderSeg() {
    const seg = $("#dual-seg"); seg.innerHTML = "";
    [["both", "Both"], ["a", D.names.a], ["b", D.names.b]].forEach(([k, label]) => {
      const b = el("button", { type: "button", role: "radio", class: "seg-btn seg-" + k, "data-k": k }, [label]);
      b.addEventListener("click", () => setTarget(k));
      seg.appendChild(b);
    });
    setTarget(D.target);
    const ta = $("#dual-shared-text");
    ta.onkeydown = ev => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendDual(); } };
  }
  function setTarget(k) {
    if (!D) return;
    D.target = k;
    $$("#dual-seg .seg-btn").forEach(b => { const on = b.dataset.k === k; b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); });
    setSuggestion(D.suggestion);
  }
  function setSuggestion(q) {
    if (!D) return;
    D.suggestion = q || "";
    const to = D.target === "both" ? "both patients" : D.names[D.target];
    $("#dual-shared-text").placeholder = "Ask a question…";
    const box = $("#dual-sugg"); box.innerHTML = "";
    if (D.suggestion) {
      const b = el("button", { type: "button", class: "qchip", title: "Put this question in the box" }, [D.suggestion]);
      b.addEventListener("click", () => { const t = $("#dual-shared-text"); t.value = D.suggestion; t.focus(); });
      box.appendChild(el("span", { class: "starters-label" }, ["Suggested:"])); box.appendChild(b);
    }
    $("#dual-shared-send").title = D.suggestion ? "Send (an empty box asks the suggested question)" : "Send";
  }
  function nextSuggestion() {
    if (!D || !D.cg) return;
    const c = D.cg.undiscussedConcepts()[0];
    setSuggestion(c ? "What usually leads up to " + inSentence(c.name) + " for you?" : "");
  }
  function sendDual() {
    if (D && !D.sid && D.starting && !D.replay) {          // both sessions still starting: queue the question
      const ta = $("#dual-shared-text");
      const q = (ta.value || "").trim() || D.suggestion;
      if (!q) { toast("Type a question first."); return; }
      D.queued = { q, target: D.target };
      setDualBusy(D.busy, "Warming up both patients — your question will be sent as soon as they are ready.");
      return;
    }
    if (!D || !D.sid || D.busy) return;
    const ta = $("#dual-shared-text");
    if (!(ta.value || "").trim() && D.suggestion) ta.value = D.suggestion;
    if (D.target === "both") askShared(); else askFollow(D.target);
  }
  async function askShared() {
    if (!D || !D.sid || D.busy) return;
    const ta = $("#dual-shared-text"); const q = (ta.value || "").trim(); if (!q) return;
    ta.value = "";
    setDualBusy(true, "Both patients are answering…");
    try { const v = await api("/api/ext/f3/shared", { session_id: D.sid, question: q }); upsertTurn(v); pollTurn(v.turn_no); setDualBusy(false, ""); }
    catch (e) { ta.value = q; setDualBusy(false, "Question failed: " + e.message); }
  }
  async function askFollow(side) {
    if (!D || !D.sid || D.busy) return;
    const ta = $("#dual-shared-text"); const q = (ta.value || "").trim(); if (!q) return;
    ta.value = "";
    setDualBusy(true, D.names[side] + " is answering…");
    try { const v = await api("/api/ext/f3/follow_up", { session_id: D.sid, side, question: q }); upsertTurn(v); pollTurn(v.turn_no); setDualBusy(false, ""); }
    catch (e) { ta.value = q; setDualBusy(false, "Question failed: " + e.message); }
  }
  function pollTurn(no) {
    const sid = D.sid;
    const tick = async () => {
      if (!D || D.sid !== sid) return;
      let v; try { v = await api("/api/ext/f3/turn?session_id=" + encodeURIComponent(sid) + "&turn_no=" + no); } catch (e) { setTimeout(tick, 2500); return; }
      upsertTurn(v);
      if (v.status !== "done" && v.status !== "error") setTimeout(tick, 1200);
      else nextSuggestion();
    };
    setTimeout(tick, 900);
  }
  async function retrySide(v, side) {
    if (!D || !D.sid || D.busy) return;
    setDualBusy(true, D.names[side] + " is answering…");
    try { const nv = await api("/api/ext/f3/follow_up", { session_id: D.sid, side, question: v.question }); upsertTurn(nv); pollTurn(nv.turn_no); setDualBusy(false, ""); }
    catch (e) { setDualBusy(false, "Retry failed: " + e.message); }
  }
  // hover an answer = highlight its aligned causes (patient colour); click = pin (may switch the focus symptom)
  function showInGraphs(side, nodeIds, edgeIds, persist) {
    if (persist) D.pin = { side, nodeIds, edgeIds };
    const any = nodeIds.length || edgeIds.length;
    const r = D.cg ? D.cg.highlight(side, nodeIds, edgeIds, persist) : false;
    if (D.full[side]) D.full[side].mark(nodeIds, edgeIds, true);
    const note = $("#dual-cg-note");
    if (!any || r === true) { note.textContent = ""; return; }
    if (r === "elsewhere") { note.textContent = "This answer is about another shared symptom — click the answer to switch to it."; return; }
    note.textContent = "This answer touches no shared symptom" + (persist ? " — shown in " + D.names[side] + "'s full network." : ".");
    if (persist) { setFull(true); requestAnimationFrame(() => { if (D.full[side]) { D.full[side].expandFor(nodeIds); D.full[side].mark(nodeIds, edgeIds, true); } }); }
  }
  function clearGraphs() { if (D.cg) D.cg.clear(); SIDES.forEach(s => D.full[s] && D.full[s].clear()); $("#dual-cg-note").textContent = ""; }
  function restorePin() { if (D.pin) showInGraphs(D.pin.side, D.pin.nodeIds, D.pin.edgeIds, false); else clearGraphs(); }
  function renderEmpty() {
    const t = $("#dual-turns"); t.innerHTML = "";
    const ex = el("button", { type: "button", class: "linkish" }, ["see an example"]);
    ex.addEventListener("click", () => openReplay());
    t.appendChild(el("div", { class: "dual-empty-hint", id: "dual-empty" }, ["Ask both to start, or ", ex, "."]));
  }
  const STATUS_TXT = { replying: "waiting for replies…", replied: "aligning…", aligning: "aligning…", summarizing: "comparing the answers…", done: "", error: "error" };
  function upsertTurn(v) {
    const prev = D.turns[v.turn_no];
    D.turns[v.turn_no] = v;
    const emp = $("#dual-empty"); if (emp) emp.remove();
    let row = D.rows[v.turn_no];
    const fresh = !row;
    if (fresh) { row = el("div", { class: "dual-turn" }); D.rows[v.turn_no] = row; $("#dual-turns").appendChild(row); }
    row.innerHTML = ""; row.dataset.turn = v.turn_no;
    const to = v.kind === "shared" ? "" : "to " + D.names[v.side] + ": ";
    const qline = el("div", { class: "dual-q" }, [el("span", { class: "turn-no", "aria-label": "Turn " + v.turn_no }, ["T" + v.turn_no]),
      el("span", { class: "dual-q-text" }, [to ? el("span", { class: "q-to side-" + v.side }, [to]) : "", v.question].filter(Boolean))]);
    row.appendChild(qline);
    const cols = el("div", { class: "dual-cols" });
    const newly = emptyState(); let anyNew = false;
    SIDES.forEach(s => {
      const ans = v[s];
      const cell = el("div", { class: "dual-ans side-" + s });
      if (!ans) {
        cell.classList.add("empty");
        if (v.kind === "shared" && v.status === "error") {
          const rb = el("button", { class: "ghost small", type: "button" }, ["↻ Ask " + D.names[s] + " again"]);
          rb.addEventListener("click", () => retrySide(v, s));
          cell.appendChild(el("div", { class: "muted small" }, ["No answer. "])); cell.appendChild(rb);
        } else if (v.kind === "shared") cell.appendChild(el("span", { class: "dots" }, [el("i"), el("i"), el("i")]));
        else cell.classList.add("absent");          // one-sided turn: nothing to show for the other patient
        cols.appendChild(cell); return;
      }
      const al = ans.alignment;
      const ok = al && !al.error && al.status !== "unavailable";
      cell.appendChild(el("div", { class: "ans-who" }, [D.names[s]]));
      const shown = shownAnswer(ans.response);
      cell.appendChild(el("div", { class: "btext", title: shown !== String(ans.response || "").trim() ? "Cut off at the patient model's length limit (full text in the JSON export)" : null }, [shown]));
      if (!al) cell.appendChild(el("div", { class: "ans-state" }, [el("span", { class: "spinner tiny" }), " aligning…"]));
      if (ok) {
        const nn = (al.nodes || []).map(n => n.node_id), ee = (al.edges || []).map(e => e.edge_id);
        if (nn.length || ee.length) {
          cell.classList.add("has-al"); cell.tabIndex = 0;
          cell.title = "Hover to see this answer's causes in the graph; click to pin";
          cell.addEventListener("mouseenter", () => showInGraphs(s, nn, ee, false));
          cell.addEventListener("focus", () => showInGraphs(s, nn, ee, false));
          cell.addEventListener("mouseleave", () => restorePin());
          cell.addEventListener("blur", () => restorePin());
          cell.addEventListener("click", () => { const same = D.pin && D.pin.side === s && D.pin.edgeIds.join() === ee.join() && D.pin.nodeIds.join() === nn.join();
            $$(".dual-ans.pinned").forEach(x => x.classList.remove("pinned"));
            if (same) { D.pin = null; clearGraphs(); return; }
            cell.classList.add("pinned"); setTab(s); showInGraphs(s, nn, ee, true); });
        }
        if (!(prev && prev[s] && prev[s].alignment)) {
          itemsOf(al).forEach(it => { if (it.kind === "node") { newly[s].n.add(it.id); D.exp[s].n.add(it.id); } else { newly[s].e.add(it.id); D.exp[s].e.add(it.id); newly[s].n.add(it.source); newly[s].n.add(it.target); D.exp[s].n.add(it.source); D.exp[s].n.add(it.target); } });
          anyNew = true;
        }
      }
      cols.appendChild(cell);
    });
    row.appendChild(cols);
    if (anyNew) { if (D.cg) D.cg.setState(D.exp, newly); SIDES.forEach(s => D.full[s] && D.full[s].setExplored(D.exp[s].n, D.exp[s].e)); }
    if (v.kind === "shared") row.appendChild(diffRow(v));
    if (D.paperHide) row.classList.toggle("paper-hide", D.paperHide.has(v.turn_no));
    renderTraj();
    if (fresh || (prev && prev.status !== v.status)) fitDual(true);
  }
  // ---- per-turn tag + trajectory (round 10). The TAG compares everything each answer traced in this turn
  //      (all causes, any symptom): kinds of cause via the concept vocabulary (cg.node_kinds), shared symptoms
  //      themselves never count as a cause. The DOTS say whether an answer revealed a distinctive cause
  //      (a cause the other patient's network lacks, into any shared symptom).
  //   dot: dist = distinctive cause revealed; trace = traced, but no distinctive cause; none = nothing traced;
  //        wait = not aligned yet; absent = not asked (follow-up to the other patient)
  function traceOf(s, v) {
    const ans = v[s];
    if (!ans) return { st: v.kind === "shared" ? "wait" : "absent" };
    const al = ans.alignment;
    if (!al) return { st: "wait" };
    if (al.error || al.status === "unavailable") return { st: "none", ids: new Set(), causes: [] };
    const cg = D.cgData, centre = new Set(), srcs = new Set(), ids = new Set();
    if (cg) cg.concepts.forEach(c => c.nodes[s].forEach(n => centre.add(n.id)));
    itemsOf(al).forEach(it => { if (it.kind === "node") ids.add(it.id); else { ids.add(it.source); ids.add(it.target); srcs.add(it.source); } });
    // a shared-symptom node still counts as a cause when the network draws it as a cause of another symptom
    const causeInNet = new Set(cg ? cg.sides[s].edges.filter(e => e.source_concept !== e.target_concept).map(e => e.source) : []);
    const causes = [...ids].filter(id => srcs.has(id) || !centre.has(id) || causeInNet.has(id));
    return { st: ids.size ? "ok" : "none", ids, causes };
  }
  function sideCat(s, v) {
    const t = traceOf(s, v);
    if (t.st !== "ok") return t.st;
    const cg = D.cgData;
    if (cg && cg.sides[s].edges.some(e => e.distinctive && e.source_concept !== e.target_concept && t.ids.has(e.source))) return "dist";
    return "trace";
  }
  const CAT_TXT = { dist: "revealed a distinctive cause", trace: "traced, but no distinctive cause", none: "nothing traced in the network",
    wait: "…", absent: "not asked" };
  const TAG_HELP = {
    div: "Different causes: each answer traced at least one kind of cause the other answer did not.",
    part: "Partly shared causes: both answers traced a cause of the same kind, and {X} also traced another kind.",
    same: "Same kind of cause: both answers traced causes of the same kind only.",
    one: "Only {X}'s cause: only {X}'s answer traced a cause; the other answer traced none (at most the shared symptom itself).",
    none: "No cause contrast: neither answer traced a cause (at most the shared symptom itself, or nothing in the network).",
    follow: "Follow-up to one patient." };
  function turnInfo(v) {
    const a = sideCat("a", v), b = sideCat("b", v);
    const kindsOf = s => {
      const t = traceOf(s, v), nk = (D.cgData && D.cgData.node_kinds && D.cgData.node_kinds[s]) || {};
      const lab = id => { const n = ((D.nets[s] && D.nets[s].nodes) || []).find(x => x.id === id); return n ? n.label : id; };
      const m = new Map(); (t.causes || []).forEach(id => { const k = nk[id] || "n:" + id; if (!m.has(k)) m.set(k, lab(id)); });
      return m;
    };
    const ka = kindsOf("a"), kb = kindsOf("b");
    const list = (m) => m.size ? [...m.values()].slice(0, 4).map(x => sentenceCase(x)).join("; ") + (m.size > 4 ? "; …" : "") : "no cause traced";
    const who = s => D.names[s] + " (" + CAT_TXT[s === "a" ? a : b] + "): " + list(s === "a" ? ka : kb);
    if (v.kind !== "shared") {            // follow-up to one patient: describe that answer only
      const s = v.side, c = s === "a" ? a : b, m = s === "a" ? ka : kb;
      return { a, b, verdict: "follow", badge: m.size ? (c === "dist" ? "Distinctive cause" : "Cause traced") : "",
        title: TAG_HELP.follow + "\n" + who(s) };
    }
    if (a === "wait" || b === "wait") return { a, b, verdict: "wait", badge: "", title: "" };
    const onlyA = [...ka.keys()].filter(k => !kb.has(k)), onlyB = [...kb.keys()].filter(k => !ka.has(k));
    const both = [...ka.keys()].filter(k => kb.has(k));
    let verdict, badge, X = "";
    if (ka.size && kb.size) {
      if (onlyA.length && onlyB.length) { verdict = "div"; badge = "Different causes"; }
      else if (!onlyA.length && !onlyB.length) { verdict = "same"; badge = "Same kind of cause"; }
      else { verdict = "part"; badge = "Partly shared causes"; X = D.names[onlyA.length ? "a" : "b"]; }
    } else if (ka.size || kb.size) { verdict = "one"; X = D.names[ka.size ? "a" : "b"]; badge = "Only " + X + "'s cause"; }
    else { verdict = "none"; badge = "No cause contrast"; }
    const sharedTxt = both.length ? "\nShared kind: " + both.map(k => sentenceCase(ka.get(k)) + " ~ " + sentenceCase(kb.get(k))).join("; ") : "";
    return { a, b, verdict, badge, title: TAG_HELP[verdict].split("{X}").join(X) + "\n" + who("a") + "\n" + who("b") + sharedTxt };
  }
  function renderTraj() {
    const box = document.getElementById("dual-traj"); if (!box) return;
    box.innerHTML = "";
    if (!D) { box.classList.add("hidden"); return; }
    const turns = Object.values(D.turns).sort((x, y) => x.turn_no - y.turn_no);
    turns.forEach(v => {                   // badges follow the graph's symptom
      const q = D.rows[v.turn_no] && D.rows[v.turn_no].querySelector(".dual-q"); if (!q) return;
      const old = q.querySelector(".turn-badge"); if (old) old.remove();
      const info = turnInfo(v);
      if (info.badge) q.appendChild(el("span", { class: "turn-badge v-" + info.verdict, title: info.title }, [info.badge]));
    });
    const total = D.replay && RP ? RP.c.turns.length : turns.length;
    if (!total) { box.classList.add("hidden"); return; }
    box.classList.remove("hidden");
    const lbl = el("div", { class: "traj-lbls", "aria-hidden": "true" }, [el("span", { class: "traj-h" }, ["Trajectory"]),
      el("span", { class: "nm-a" }, [D.names.a]), el("span", { class: "nm-b" }, [D.names.b])]);
    const cols = el("div", { class: "traj-cols", role: "list", "aria-label": "Turns" });
    const cur = turns.length ? turns[turns.length - 1].turn_no : 0;
    for (let i = 1; i <= total; i++) {
      const v = D.turns[i];
      const info = v ? turnInfo(v) : null;
      const kids = SIDES.map(s => el("i", { class: "tdot side-" + s + " cat-" + (info ? info[s] : "future") }));
      kids.push(el("span", { class: "tno" }, [String(i)]));
      const b = el("button", { type: "button", role: "listitem", class: "traj-col" + (v ? "" : " future") + (i === cur ? " cur" : "") + (info ? " v-" + info.verdict : ""),
        title: v ? "Turn " + i + (info.badge ? " · " + info.badge : "") + "\n" + info.title : "Turn " + i + " (not played yet)" }, kids);
      if (v) b.addEventListener("click", () => jumpTurn(i)); else b.disabled = true;
      cols.appendChild(b);
    }
    const st = D.cg && D.cg.focusStats ? D.cg.focusStats() : null;
    const sum = el("div", { class: "traj-sum" });
    if (st) {
      sum.appendChild(el("span", { class: "traj-sum-h" }, ["Distinctive causes of " + inSentence(st.name) + " reached"]));
      SIDES.forEach(s => sum.appendChild(el("span", { class: "traj-n nm-" + s, title: D.names[s] + ": " + st[s].reached + " of " + st[s].total + " distinctive causes of this symptom have come up" },
        [D.names[s] + " ", el("b", {}, [st[s].reached + "/" + st[s].total])])));
    }
    const key = el("div", { class: "traj-key muted" }, [el("i", { class: "tdot cat-dist side-a", "aria-hidden": "true" }), " distinctive cause revealed",
      el("span", { class: "k-sep" }, ["·"]), el("i", { class: "tdot cat-trace side-a", "aria-hidden": "true" }), " answer traced, no distinctive cause",
      el("span", { class: "k-sep" }, ["·"]), el("i", { class: "tdot cat-none side-a", "aria-hidden": "true" }), " nothing traced"]);
    box.appendChild(lbl); box.appendChild(cols); box.appendChild(sum); box.appendChild(key);
  }
  function jumpTurn(i) {
    const row = D && D.rows[i]; if (!row) return;
    if (PAPER.on) row.scrollIntoView({ block: "start", behavior: "smooth" });
    else { const sc = $("#dual-turns"); sc.scrollTo({ top: row.offsetTop - sc.offsetTop - 4, behavior: "smooth" }); }
    row.classList.remove("flash"); void row.offsetWidth; row.classList.add("flash");
    setTimeout(() => row.classList.remove("flash"), 1600);
  }
  // ---- desktop: the graph box is capped at 40 % of the window and shrinks further when needed so the
  //      conversation always shows the current turn and the previous difference sentence in full
  let fitRaf = 0;
  function fitDual(scroll) {
    cancelAnimationFrame(fitRaf);
    fitRaf = requestAnimationFrame(() => {
      const page = $("#screen-dual .dual-page"), body = $("#cg-body"), sc = $("#dual-turns");
      if (!D || !page || !body || !sc) return;
      const desk = window.matchMedia("(min-width: 900px)").matches && !document.body.classList.contains("paper-view");
      if (!desk) { page.style.removeProperty("--cg-cap"); return; }
      const cap40 = Math.round(window.innerHeight * 0.4), MIN = window.innerHeight < 780 ? 130 : 150;
      const rows = Object.keys(D.rows).map(Number).sort((x, y) => x - y).map(k => D.rows[k]);
      const last = rows[rows.length - 1], prevRow = rows[rows.length - 2];
      let need = 0;
      if (last) {
        need = last.offsetHeight + 10;
        const pd = prevRow && prevRow.querySelector(".dual-diff");
        if (pd) need += (prevRow.getBoundingClientRect().bottom - pd.getBoundingClientRect().top) + 14;
      }
      const cur = body.getBoundingClientRect().height;
      let cap = cap40;
      if (need) cap = Math.max(MIN, Math.min(cap40, Math.floor(cur + (sc.clientHeight - need))));
      page.style.setProperty("--cg-cap", cap + "px");
      if (last && (scroll || D.replay)) requestAnimationFrame(() => {
        const pd = prevRow && prevRow.querySelector(".dual-diff");
        const top = (pd || last).getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop - 6;
        sc.scrollTop = (last.getBoundingClientRect().bottom - sc.getBoundingClientRect().top + sc.scrollTop) - top <= sc.clientHeight ? top : sc.scrollHeight;
      });
    });
  }
  window.addEventListener("resize", () => { if (D && State.screen === "dual") fitDual(); });
  // one plain-text difference sentence (citation tags removed; "Patient A"/"A said" shown as names)
  function diffRow(v) {
    const dv = el("div", { class: "dual-diff" });
    if (v.diff) {
      let t = String(v.diff.diff_summary || "").replace(CITE_RE, "").replace(/\s+([,.;])/g, "$1").replace(/\s{2,}/g, " ").trim();
      const VERB = "(?=\\s+(?:said|says|described|describes|feels|felt|was|is|blamed|blames|attributed|attributes|focused|focuses|mentioned|stayed|would|expected|expects|named|recalled|framed|linked|links|tied|ties|reported|reports|added|adds|spoke|talked)\\b)";
      t = t.replace(/\b[Pp]atient A\b/g, D.names.a).replace(/\b[Pp]atient B\b/g, D.names.b)
        .replace(new RegExp("\\bA\\b" + VERB, "g"), D.names.a).replace(new RegExp("\\bB\\b" + VERB, "g"), D.names.b)
        // any other mid-sentence "A"/"B" used as a patient label ("but A hides it")
        .replace(/([^.!?]\s)A\b(?=\s+[a-z'’])/g, "$1" + D.names.a).replace(/\bB\b(?=\s+[a-z'’])/g, D.names.b)
        .replace(/\bA's\b/g, D.names.a + "'s").replace(/\bB's\b/g, D.names.b + "'s");
      dv.appendChild(el("span", { class: "diff-label" }, ["Difference"]));
      dv.appendChild(el("span", { class: "diff-t" }, [" " + t]));
    } else if (v.status === "error") {
      dv.appendChild(el("span", { class: "muted small" }, ["Could not compare this turn" + (v.error ? ": " + v.error : "") + "."]));
    } else {
      dv.appendChild(el("span", { class: "muted small" }, [el("span", { class: "spinner tiny" }), " " + (STATUS_TXT[v.status] || v.status)]));
    }
    return dv;
  }
  function drawFull() {
    if (!D) return;
    const box = $("#dual-full-graphs");
    if (box.childNodes.length) return;
    SIDES.forEach(s => {
      const ctl = el("div", { class: "layer-ctl" });
      const slot = el("div", { class: "graph-stage ext-graph dual-g" });
      box.appendChild(el("div", { class: "dual-graph side-" + s }, [el("div", { class: "ego-title" }, [el("span", { class: "pname nm-" + s }, [D.names[s]])]), ctl, slot]));
      requestAnimationFrame(() => { D.full[s] = new ThemeGraph(slot, D.nets[s], D.groups[s], { ctl }); D.full[s].setExplored(D.exp[s].n, D.exp[s].e); });
    });
  }
  async function exportComparison() {
    if (!D || !D.sid) { toast("No comparison session."); return; }
    try { download("comparison_" + D.sid + ".json", await api("/api/ext/f3/export?session_id=" + encodeURIComponent(D.sid))); }
    catch (e) { toast("Export failed: " + e.message); }
  }

  return { onShow, onPatientLoaded, onChatStart, onPatientReply, findContrast, pickContrast, canContrast, exportAlignment, exportRecommendation,
    openDual, backToContrast, askShared, askFollow, sendDual, exportComparison, toggleNet, toggleFull, toggleAllSymptoms, toggleFocusMenu, closeMenus,
    openReplay, newSession, exitReplay, setPaper, togglePaper, shownAnswer,
    _state: () => ({ C, R, D }) };
})();
window.Ext = Ext;   // app.js hooks check window.Ext
