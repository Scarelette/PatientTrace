/* Flow layout for patient networks (CBT order, left to right):
     Stimulus -> Cognition -> Emotion -> Physiological Sensation -> Behavior
   Flow.model(g)  : once per patient. Orders every node inside its type column with barycenter sweeps,
                    keeping each theme's members together, and fixes where each theme sits when collapsed.
   Flow.place(...) : positions for the nodes currently shown. A collapsed theme sits at its anchor; an
                    expanded theme's members are stacked around that same anchor, and overlapping neighbours
                    in the same column are pushed apart just enough (other columns never move).
   g = { nodes:[{name,type}], edges:[{from,to}], groups:[{label,type,members:[name]}] } (names are keys). */
window.Flow = (function () {
  const COLS = ["Stimulus", "Cognition", "Emotion", "Physiological Sensation", "Behavior"];
  const colOf = t => { const i = COLS.indexOf(t); return i < 0 ? 2 : i; };

  function model(g) {
    const groups = g.groups || [], typeOf = {}, gOf = {}, nbr = {};
    (g.nodes || []).forEach(n => { typeOf[n.name] = n.type; nbr[n.name] = []; });
    groups.forEach((grp, i) => grp.members.forEach(m => { if (m in typeOf && gOf[m] === undefined) gOf[m] = i; }));
    let extra = groups.length;                                   // nodes outside every theme act as their own
    Object.keys(typeOf).forEach(m => { if (gOf[m] === undefined) gOf[m] = extra++; });
    (g.edges || []).forEach(e => { if (nbr[e.from] && nbr[e.to] && e.from !== e.to) { nbr[e.from].push(e.to); nbr[e.to].push(e.from); } });

    // blocks = (theme, column) pairs; a column is an ordered list of blocks, a block an ordered list of names
    const cols = COLS.map(() => []), blockOf = {};
    Object.keys(typeOf).forEach(m => {
      const c = colOf(typeOf[m]), k = gOf[m] + "|" + c;
      if (!blockOf[k]) { blockOf[k] = { key: k, theme: gOf[m], col: c, names: [] }; cols[c].push(blockOf[k]); }
      blockOf[k].names.push(m);
    });
    cols.forEach(col => { col.sort((a, b) => a.theme - b.theme); col.forEach(b => b.names.sort((p, q) => nbr[q].length - nbr[p].length || p.localeCompare(q))); });
    const pos = {};
    const index = () => cols.forEach(col => { let y = 0; col.forEach(b => b.names.forEach(m => { pos[m] = y++; })); });
    index();
    const norm = {};                                             // position as a share of its column's height
    const normalize = () => cols.forEach(col => { const n = col.reduce((s, b) => s + b.names.length, 0); col.forEach(b => b.names.forEach(m => { norm[m] = n > 1 ? pos[m] / (n - 1) : 0.5; })); });
    const bary = m => { const ns = nbr[m].filter(x => colOf(typeOf[x]) !== colOf(typeOf[m])); return ns.length ? ns.reduce((s, x) => s + norm[x], 0) / ns.length : null; };
    for (let sweep = 0; sweep < 8; sweep++) {
      normalize();
      const order = sweep % 2 ? [4, 3, 2, 1, 0] : [0, 1, 2, 3, 4];
      order.forEach(c => {
        const col = cols[c];
        col.forEach(b => {
          const bs = b.names.map(m => [m, bary(m)]);
          const vals = bs.map(x => x[1]).filter(v => v != null);
          b.bc = vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : (b.names.reduce((s, m) => s + norm[m], 0) / b.names.length);
          b.names = bs.sort((p, q) => (p[1] ?? b.bc) - (q[1] ?? b.bc)).map(x => x[0]);
        });
        col.sort((a, b) => a.bc - b.bc);
        let y = 0; col.forEach(b => b.names.forEach(m => { pos[m] = y++; }));
        normalize();
      });
    }
    // collapsed anchors: each theme sits in its own type's column, in the order its members ended up in
    const themeCol = i => groups[i] ? colOf(groups[i].type) : colOf(typeOf[Object.keys(gOf).find(m => gOf[m] === i)]);
    const rank = {};
    const themesIn = COLS.map(() => []);
    for (let i = 0; i < extra; i++) {
      const c = themeCol(i), own = blockOf[i + "|" + c];
      const names = own ? own.names : Object.keys(gOf).filter(m => gOf[m] === i);
      if (!names.length) continue;
      rank[i] = names.reduce((s, m) => s + norm[m], 0) / names.length;
      themesIn[c].push(i);
    }
    themesIn.forEach(list => list.sort((a, b) => rank[a] - rank[b]));
    const slot = {};                                             // collapsed slot index inside its column
    themesIn.forEach(list => list.forEach((i, k) => { slot[i] = { col: themeCol(i), k, n: list.length }; }));
    const nameOrder = {}; cols.forEach(col => { let y = 0; col.forEach(b => b.names.forEach(m => { nameOrder[m] = y++; })); });
    return { COLS, colOf, typeOf, gOf, slot, nameOrder, themesIn };
  }

  // nodes: view nodes {key, name, type, group, gidx, member}; size(n) -> vertical room the node needs
  function place(nodes, M, W, H, size) {
    const colW = W / COLS.length, gap = 8, maxN = Math.max(1, ...M.themesIn.map(l => l.length));
    const pitch = Math.max(58, Math.min(110, (H - 60) / maxN));
    const anchorY = i => { const s = M.slot[i]; return s ? H / 2 + (s.k - (s.n - 1) / 2) * pitch : H / 2; };
    const items = COLS.map(() => ({}));
    nodes.forEach(n => {
      const c = n.group ? colOf2(M, n.gidx, n.type) : M.colOf(n.type);
      const th = n.group ? n.gidx : (n.member && n.gidx != null ? n.gidx : M.gOf[n.name]);
      const k = (n.group ? "g" : "m") + th;
      const it = items[c][k] || (items[c][k] = { theme: th, anchor: anchorY(th), nodes: [] });
      it.nodes.push(n);
    });
    const out = {};
    items.forEach((byKey, c) => {
      const list = Object.values(byKey);
      list.forEach(it => {
        it.nodes.sort((a, b) => (M.nameOrder[a.name] ?? 0) - (M.nameOrder[b.name] ?? 0));
        it.h = it.nodes.reduce((s, n) => s + size(n), 0) + gap * (it.nodes.length - 1);
      });
      list.sort((a, b) => a.anchor - b.anchor || (M.nameOrder[a.nodes[0].name] ?? 0) - (M.nameOrder[b.nodes[0].name] ?? 0));
      // overlap removal around anchors (pool adjacent violators): blocks stay centred on their anchors
      // unless they collide, and then only the colliding neighbours share the shift
      const cl = [];
      list.forEach(it => {
        let cur = { items: [it], h: it.h, want: it.anchor - it.h / 2 };
        cur.top = cur.want;
        while (cl.length) {
          const prev = cl[cl.length - 1];
          if (prev.top + prev.h + gap * 2 <= cur.top) break;
          cl.pop();
          const items2 = prev.items.concat(cur.items);
          let off = 0, sum = 0;
          items2.forEach(x => { sum += (x.anchor - x.h / 2) - off; off += x.h + gap * 2; });
          const h = off - gap * 2;
          cur = { items: items2, h, top: sum / items2.length };
        }
        cl.push(cur);
      });
      const x = colW * (c + 0.5);
      cl.forEach(k => { let y = k.top; k.items.forEach(it => { it.nodes.forEach(n => { const s = size(n); out[n.key] = { x, y: y + s / 2, col: c }; y += s + gap; }); y += gap; }); });
    });
    return out;
  }
  function colOf2(M, gi, type) { const s = M.slot[gi]; return s ? s.col : M.colOf(type); }

  // Edge geometry. Forward edges (to a later column) are smooth S-curves; feedback edges (to an earlier
  // column or the same column) are arcs routed above or below everything, in lanes so they don't overlap.
  function routes(edges, bounds) {
    const lanes = { up: 0, down: 0, side: {} }, mid = (bounds.y0 + bounds.y1) / 2;
    edges.slice().sort((a, b) => Math.abs(a.s.x - a.t.x) - Math.abs(b.s.x - b.t.x)).forEach(e => {
      if (e.t.x > e.s.x + 1) { e.route = { kind: "fwd" }; return; }
      if (Math.abs(e.t.x - e.s.x) <= 1) {
        const k = Math.round(e.s.x); lanes.side[k] = (lanes.side[k] || 0) + 1;
        e.route = { kind: "side", bulge: 46 + 9 * (lanes.side[k] % 6) }; return;
      }
      const up = (e.s.y + e.t.y) / 2 <= mid;
      const lane = up ? lanes.up++ : lanes.down++;
      e.route = { kind: "fb", y: up ? bounds.y0 - 18 - 5 * (lane % 8) : bounds.y1 + 18 + 5 * (lane % 8) };
    });
  }
  function path(e, rs, rt) {
    const s = e.s, t = e.t, r = e.route || { kind: "fwd" };
    let c1, c2;
    if (r.kind === "fwd") { const dx = (t.x - s.x) * 0.5; c1 = { x: s.x + dx, y: s.y }; c2 = { x: t.x - dx, y: t.y }; }
    else if (r.kind === "side") { c1 = { x: s.x + r.bulge, y: s.y }; c2 = { x: t.x + r.bulge, y: t.y }; }
    else { c1 = { x: s.x, y: r.y }; c2 = { x: t.x, y: r.y }; }
    const trim = (p, toward, d) => { const dx = toward.x - p.x, dy = toward.y - p.y, L = Math.hypot(dx, dy) || 1; return { x: p.x + dx / L * d, y: p.y + dy / L * d }; };
    const a = trim(s, c1, rs), b = trim(t, c2, rt + 6);
    return "M" + a.x.toFixed(1) + "," + a.y.toFixed(1) + " C" + c1.x.toFixed(1) + "," + c1.y.toFixed(1) + " " + c2.x.toFixed(1) + "," + c2.y.toFixed(1) + " " + b.x.toFixed(1) + "," + b.y.toFixed(1);
  }
  // ---- graph analysis on the full network (names are node keys) ----
  function adjacency(g) {
    const out = {}, deg = {};
    (g.nodes || []).forEach(n => { out[n.name] = []; deg[n.name] = 0; });
    (g.edges || []).forEach(e => { if (out[e.from] && out[e.to] && e.from !== e.to) { out[e.from].push(e.to); deg[e.from]++; deg[e.to]++; } });
    return { out, deg };
  }
  // Directed cycles of length 2..maxLen (each found once: it starts at its lowest-index node), ranked
  // shortest first, then by how central their nodes are (mean in+out degree).
  function cycles(g, maxLen = 5, cap = 4000) {
    const { out, deg } = adjacency(g), names = Object.keys(out), idx = {}; names.forEach((n, i) => { idx[n] = i; });
    const found = [];
    names.forEach((s, si) => {
      const path = [s], on = new Set([s]);
      (function dfs(v) {
        if (found.length >= cap) return;
        for (const w of out[v]) {
          if (w === s && path.length >= 2) { found.push(path.slice()); continue; }
          if (idx[w] <= si || on.has(w) || path.length >= maxLen) continue;
          path.push(w); on.add(w); dfs(w); path.pop(); on.delete(w);
        }
      })(s);
    });
    const score = c => c.reduce((t, n) => t + deg[n], 0) / c.length;
    return found.map(c => ({ nodes: c, edges: c.map((n, i) => [n, c[(i + 1) % c.length]]), score: score(c) }))
      .sort((a, b) => a.nodes.length - b.nodes.length || b.score - a.score);
  }
  function central(g, k = 5) {
    const { deg } = adjacency(g);
    return Object.keys(deg).filter(n => deg[n] > 0).sort((a, b) => deg[b] - deg[a] || a.localeCompare(b)).slice(0, k).map(n => ({ name: n, degree: deg[n] }));
  }
  // Breadth-first reach from a node: steps[0] = [start], steps[i] = nodes first reached in i hops,
  // with the edges that reached them.
  function reach(g, start) {
    const { out } = adjacency(g), seen = new Set([start]), steps = [{ nodes: [start], edges: [] }];
    let front = [start];
    while (front.length) {
      const next = [], edges = [];
      front.forEach(v => out[v].forEach(w => { if (!seen.has(w)) { seen.add(w); next.push(w); edges.push([v, w]); } else if (next.includes(w)) edges.push([v, w]); }));
      if (!next.length) break;
      steps.push({ nodes: next, edges }); front = next;
    }
    return steps;
  }
  return { COLS, model, place, routes, path, cycles, central, reach };
})();
