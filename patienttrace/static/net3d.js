// Layered 3D network view (port of docs/layered-network-3d-reference.html).
// Starts as a flat top-down graph of themes. Clicking a theme tilts the camera into 3D and grows a layer
// under that theme holding its detailed nodes; closing the last theme glides back to the flat view.
//
//   const v = Net3D.mount(container, graph, opts)   graph = { nodes:[{name,type}], edges:[{from,to,evidence}],
//                                                             groups:[{label,type,members:[name]}] }
//   opts: showIso (show unconnected nodes), tip (tooltip element), onNodeClick({key,name,type}),
//         onBackgroundClick(), onChange(openCount, themeCount)
//   v.destroy() frees the renderer, controls, observers, listeners and every GPU resource.
// The instance also answers the calls app.js makes on the 2D graph: select / selectEdge / unselect /
// mark / clearMark / focus / pulse. Node keys are node names.
import * as THREE from "./vendor/three-0.170.0/three.module.min.js";
import { OrbitControls } from "./vendor/three-0.170.0/OrbitControls.js";

const TYPE_VAR = { "Stimulus": "--t-stimulus", "Emotion": "--t-emotion", "Cognition": "--t-cognition",
  "Behavior": "--t-behavior", "Physiological Sensation": "--t-physio" };
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const colorFor = t => css(TYPE_VAR[t] || "--muted") || "#888";
const DEPTH = 85;                                     // how far below a theme its layer sits
const clamp01 = x => Math.max(0, Math.min(1, x));
const ease = t => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;   // ease-in-out cubic
const easeOutBack = t => { const c = 1.4; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); };
const escH = s => String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
const isDark = () => document.documentElement.dataset.theme === "dark" ||
  (document.documentElement.dataset.theme !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);

// ---------------- data: unconnected nodes hidden unless asked for; every node in exactly one theme ----------------
function prepare(gIn, showIso) {
  const linked = new Set(); (gIn.edges || []).forEach(e => { if (e.from !== e.to) { linked.add(e.from); linked.add(e.to); } });
  const nodes = (gIn.nodes || []).filter(n => showIso || linked.has(n.name));
  const names = new Set(nodes.map(n => n.name)), seen = new Set();
  const groups = [];
  (gIn.groups || []).forEach(grp => {
    const members = (grp.members || []).filter(m => names.has(m) && !seen.has(m));
    members.forEach(m => seen.add(m));
    if (members.length) groups.push({ label: grp.label, type: grp.type, members });
  });
  nodes.forEach(n => { if (!seen.has(n.name)) groups.push({ label: n.name, type: n.type, members: [n.name] }); });
  const edges = (gIn.edges || []).filter(e => names.has(e.from) && names.has(e.to) && e.from !== e.to);
  return { nodes, edges, groups };
}

// ---------------- layout: themes as a 2D graph, each with room for its layer ----------------
function layout(g, aspect = 1) {
  const gi = {}; g.groups.forEach((grp, i) => grp.members.forEach(m => { gi[m] = i; }));
  const discR = g.groups.map(grp => grp.members.length <= 1 ? 10 : 14 + 9 * Math.sqrt(grp.members.length));
  const n = g.groups.length;
  const th = g.groups.map((_, i) => { const a = 2 * Math.PI * i / Math.max(1, n); return { x: Math.cos(a) * 60, z: Math.sin(a) * 60, vx: 0, vz: 0 }; });
  const agg = {};
  g.edges.forEach(e => { const a = gi[e.from], b = gi[e.to]; if (a == null || b == null || a === b) return;
    const k = Math.min(a, b) + "|" + Math.max(a, b); agg[k] = (agg[k] || 0) + 1; });
  const links = Object.entries(agg).map(([k, w]) => { const [s, t] = k.split("|").map(Number); return { s, t, w }; });
  for (let it = 0; it < 500; it++) {                 // springs + repulsion + gravity
    const cool = 1 - it / 500;
    th.forEach((a, i) => { a.vx = -a.x * 0.012; a.vz = -a.z * 0.012;
      th.forEach((b, j) => { if (i === j) return; const dx = a.x - b.x, dz = a.z - b.z, d2 = dx * dx + dz * dz + 0.01, d = Math.sqrt(d2);
        const f = 2600 / d2; a.vx += f * dx / d; a.vz += f * dz / d; }); });
    links.forEach(({ s, t, w }) => { const a = th[s], b = th[t], dx = b.x - a.x, dz = b.z - a.z, d = Math.hypot(dx, dz) + 0.01;
      const f = (d - (discR[s] + discR[t] + 20)) * 0.012 * Math.min(3, w); a.vx += f * dx / d; a.vz += f * dz / d; b.vx -= f * dx / d; b.vz -= f * dz / d; });
    th.forEach(p => { const h = Math.hypot(p.vx, p.vz) + 1e-9, m = Math.min(5, h) * cool; p.x += p.vx / h * m; p.z += p.vz / h * m; });
  }
  for (let it = 0; it < 300; it++)                  // no two layers may overlap once opened
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      const a = th[i], b = th[j], dx = b.x - a.x, dz = b.z - a.z, d = Math.hypot(dx, dz) + 1e-6, min = discR[i] + discR[j] + 10;
      if (d < min) { const push = (min - d) / 2; a.x -= dx / d * push; a.z -= dz / d * push; b.x += dx / d * push; b.z += dz / d * push; }
    }
  const cx = th.reduce((s, p) => s + p.x, 0) / Math.max(1, n), cz = th.reduce((s, p) => s + p.z, 0) / Math.max(1, n);
  th.forEach(p => { p.x -= cx; p.z -= cz; });
  // a wide canvas gets a wide layout: stretch x (only spreads things apart, so no new overlaps)
  if (n > 2 && aspect > 1.05) {
    let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
    th.forEach(p => { x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); z0 = Math.min(z0, p.z); z1 = Math.max(z1, p.z); });
    const f = Math.max(1, Math.min(3, aspect * 0.95 / ((x1 - x0) / Math.max(1, z1 - z0))));
    th.forEach(p => { p.x *= f; });
  }
  const mem = {};
  g.groups.forEach((grp, i) => {
    const k = grp.members.length, r = k <= 1 ? 0 : discR[i] * 0.66;
    grp.members.forEach((m, j) => { const a = 2 * Math.PI * j / Math.max(1, k) - Math.PI / 2; mem[m] = { dx: Math.cos(a) * r, dz: Math.sin(a) * r }; });
  });
  return { gi, th, discR, mem };
}

// ---------------- labels: drawn on a canvas, wrapped to 2 lines with an ellipsis ----------------
function drawLabel(sprite, text, { size = 26, weight = 500, color, bg, maxW = 300, maxLines = 2 }) {
  const c = document.createElement("canvas"), x = c.getContext("2d"), dpr = 2;
  const font = `${weight} ${size}px -apple-system, "Segoe UI", Roboto, sans-serif`;
  x.font = font; const words = String(text).split(/\s+/), lines = []; let cur = "";
  words.forEach(w => { const t = cur ? cur + " " + w : w; if (x.measureText(t).width > maxW && cur) { lines.push(cur); cur = w; } else cur = t; });
  if (cur) lines.push(cur);
  if (lines.length > maxLines) {                      // full text is in the tooltip
    lines.length = maxLines; let l = lines[maxLines - 1];
    while (x.measureText(l + "…").width > maxW && l.includes(" ")) l = l.slice(0, l.lastIndexOf(" "));
    lines[maxLines - 1] = l.replace(/[,;:.]$/, "") + "…";
  }
  lines.forEach((l, i) => { while (x.measureText(l).width > maxW && l.length > 4) l = l.slice(0, -2) + "…"; lines[i] = l; });
  const w = Math.ceil(Math.max(...lines.map(l => x.measureText(l).width))) + 18, lh = size * 1.18, h = Math.ceil(lines.length * lh) + 10;
  c.width = w * dpr; c.height = h * dpr; x.scale(dpr, dpr); x.font = font;
  if (bg) { x.fillStyle = bg; x.beginPath(); x.roundRect(0, 0, w, h, 9); x.fill(); }
  x.fillStyle = color; x.textBaseline = "top"; x.textAlign = "center";
  lines.forEach((l, i) => x.fillText(l, w / 2, 5 + i * lh));
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4;
  if (sprite.material.map) sprite.material.map.dispose();
  sprite.material.map = tex; sprite.material.needsUpdate = true;
  Object.assign(sprite.userData, { pw: w, ph: h, size });
}
function makeLabel(text, style) {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ depthWrite: false, depthTest: false, transparent: true }));
  s.center.set(0.5, 1); s.renderOrder = 10; s.userData.a = 1; s.userData.style = style; s.userData.text = text;
  return s;
}

let SUPPORTED = null;                                 // probed once: every probe would hold a WebGL context
export function supported() {
  if (SUPPORTED !== null) return SUPPORTED;
  try {
    const c = document.createElement("canvas"), gl = window.WebGLRenderingContext && (c.getContext("webgl2") || c.getContext("webgl"));
    SUPPORTED = !!gl; if (gl) { const ext = gl.getExtension("WEBGL_lose_context"); if (ext) ext.loseContext(); }
  } catch (e) { SUPPORTED = false; }
  return SUPPORTED;
}

// ---------------- the view ----------------
export function mount(container, gIn, opts = {}) {
  const g = prepare(gIn, !!opts.showIso);
  const L = layout(g, Math.max(1, container.clientWidth) / Math.max(1, container.clientHeight));
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const OPEN_MS = REDUCED ? 1 : 1100, CLOSE_MS = REDUCED ? 1 : 750, CAM_MS = REDUCED ? 1 : 1300;
  let dark = isDark(), ink, muted, accent, labelBg;
  const readColors = () => { dark = isDark(); ink = css("--ink") || "#1d1d1f"; muted = css("--muted") || "#6e6e73";
    accent = css("--accent") || "#0071e3"; labelBg = dark ? "rgba(28,28,30,0.78)" : "rgba(255,255,255,0.82)"; };
  readColors();

  const W = () => Math.max(1, container.clientWidth), H = () => Math.max(1, container.clientHeight);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: !!opts.preserve });
  renderer.setPixelRatio(Math.min(2, devicePixelRatio)); renderer.setSize(W(), H());
  renderer.domElement.classList.add("net3d-canvas");
  container.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(24, W() / H(), 1, 6000);   // narrow lens: less perspective skew between layers
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.maxPolarAngle = Math.PI * 0.47; controls.minDistance = 80; controls.maxDistance = 2400;
  scene.add(new THREE.AmbientLight(0xffffff, 1.7));
  const sun = new THREE.DirectionalLight(0xffffff, 1.5); sun.position.set(60, 220, 120); scene.add(sun);

  // shared geometries (scaled per object) -- nothing below allocates per frame
  const GEO = { sphere: new THREE.SphereGeometry(1, 32, 16), cone: new THREE.ConeGeometry(1, 3.1, 10) };

  // ---- camera: every pose is fitted so the visible points fill ~86% of the view ----
  const V = (x, y, z) => new THREE.Vector3(x, y, z), vf = new THREE.Vector3();
  const themeR = g.groups.map(grp => 3.6 + Math.min(5, Math.sqrt(grp.members.length) * 1.9));
  function framePoints(openIdx) {
    const pts = [];
    themes.forEach((T, i) => { const p = T.to, r = themeR[i] + 4;
      pts.push(V(p.x + r + 10, 0, p.z), V(p.x - r - 10, 0, p.z), V(p.x, 0, p.z - r), V(p.x, 0, p.z + r + 10)); });
    openIdx.forEach(i => { const p = themes[i].to, R = L.discR[i], d = themes[i].depthTo;
      for (let k = 0; k < 12; k++) { const a = k / 12 * Math.PI * 2; pts.push(V(p.x + Math.cos(a) * R, -d, p.z + Math.sin(a) * R)); }
      pts.push(V(p.x, -d, p.z + R + 24)); });      // room for the 2-line labels under the nearest nodes
    return pts;
  }
  const probe = new THREE.PerspectiveCamera();
  let insetR = 0;                                   // px of the canvas covered on the right (details panel)
  function fitPose(phi, theta, pts, fill = 0.9, rMin = 80) {
    const target = new THREE.Box3().setFromPoints(pts).getCenter(V(0, 0, 0));
    const visW = Math.max(160, W() - insetR);
    probe.fov = camera.fov; probe.aspect = visW / H(); probe.near = 1; probe.far = 6000; probe.updateProjectionMatrix();
    let r = 900; const right = V(0, 0, 0), up = V(0, 0, 0), sph = new THREE.Spherical();
    for (let it = 0; it < 8; it++) {               // re-center and re-scale until the projected bounds fill the view
      sph.set(r, phi, theta); probe.position.setFromSpherical(sph).add(target); probe.lookAt(target); probe.updateMatrixWorld();
      let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
      pts.forEach(p => { vf.copy(p).project(probe); x0 = Math.min(x0, vf.x); x1 = Math.max(x1, vf.x); y0 = Math.min(y0, vf.y); y1 = Math.max(y1, vf.y); });
      const halfH = r * Math.tan(THREE.MathUtils.degToRad(probe.fov / 2)), halfW = halfH * probe.aspect;
      right.setFromMatrixColumn(probe.matrixWorld, 0); up.setFromMatrixColumn(probe.matrixWorld, 1);
      target.addScaledVector(right, (x0 + x1) / 2 * halfW).addScaledVector(up, (y0 + y1) / 2 * halfH);
      r = Math.min(2400, Math.max(rMin, r * Math.max((x1 - x0) / 2, (y1 - y0) / 2, 0.05) / fill));
    }
    if (insetR) {                                  // centre the fitted view in the uncovered part of the canvas
      sph.set(r, phi, theta); probe.position.setFromSpherical(sph).add(target); probe.lookAt(target); probe.updateMatrixWorld();
      right.setFromMatrixColumn(probe.matrixWorld, 0);
      const worldPerPx = 2 * r * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) / H();
      target.addScaledVector(right, insetR / 2 * worldPerPx);
    }
    return { target, r, phi, theta };
  }
  let openList = [], refDist = 300;
  const TILT = THREE.MathUtils.degToRad(58);
  // a slight lean toward the theme just opened (capped: the layout is stretched to the canvas width,
  // so rotating the view much would waste that width)
  const leanTo = i => { const p = themes[i].base; return Math.abs(p.x) + Math.abs(p.z) > 1 ? Math.max(-0.26, Math.min(0.26, Math.atan2(p.x, p.z) * 0.35)) : 0; };
  const POSE = {
    flat: () => { relayout(0, 0); return fitPose(0.0001, 0, framePoints([])); },
    focus: i => { const th = leanTo(i); relayout(TILT, th); return fitPose(TILT, th, framePoints(openList)); },   // lean toward the theme just opened
  };
  const sphTmp = new THREE.Spherical();
  const placeCamera = p => { sphTmp.set(p.r, p.phi, p.theta);
    camera.position.setFromSpherical(sphTmp).add(p.target); controls.target.copy(p.target); camera.lookAt(p.target); };
  let themes = [];
  let camTween = null, mode = "flat";
  function glideTo(to, nextMode) {
    if (nextMode) mode = nextMode;
    const off = camera.position.clone().sub(controls.target), s = new THREE.Spherical().setFromVector3(off);
    camTween = { t0: performance.now(), from: { target: controls.target.clone(), r: s.radius, phi: s.phi, theta: s.theta }, to };
    controls.enabled = false; controls.enableRotate = mode !== "flat";
  }
  const goPose = (name, arg) => glideTo(POSE[name](arg), name === "flat" ? "flat" : "3d");

  const pickables = [];
  // ---- themes on the top plane ----
  themes = g.groups.map((grp, i) => {
    const p = L.th[i], col = new THREE.Color(colorFor(grp.type)), r = themeR[i];
    const mesh = new THREE.Mesh(GEO.sphere, new THREE.MeshStandardMaterial({ color: col, roughness: 0.45, transparent: true, opacity: 1 }));
    mesh.scale.setScalar(r); mesh.position.set(p.x, 0, p.z); scene.add(mesh);
    const halo = new THREE.Mesh(new THREE.TorusGeometry(r + 2, 0.45, 8, 56), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0 }));
    halo.rotation.x = Math.PI / 2; halo.position.copy(mesh.position); scene.add(halo);
    const lab = makeLabel(grp.members.length > 1 ? `${grp.label} (${grp.members.length})` : grp.label, { size: 28, weight: 650, maxW: 300 });
    lab.position.copy(mesh.position); Object.assign(lab.userData, { r, px: 13, ti: i }); scene.add(lab);
    const layer = new THREE.Group(); layer.position.set(p.x, -DEPTH, p.z); layer.scale.setScalar(0.001); layer.visible = false; scene.add(layer);
    const disc = new THREE.Mesh(new THREE.CircleGeometry(L.discR[i], 64), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false }));
    disc.rotation.x = -Math.PI / 2; layer.add(disc);
    const rim = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(Array.from({ length: 80 }, (_, k) => { const a = k / 80 * Math.PI * 2; return V(Math.cos(a) * L.discR[i], 0, Math.sin(a) * L.discR[i]); })),
      new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0 })); layer.add(rim);
    const funnel = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.9, L.discR[i], DEPTH, 48, 1, true),
      new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false }));
    funnel.position.set(p.x, -DEPTH / 2, p.z); funnel.visible = false; scene.add(funnel);
    mesh.userData = { kind: "theme", i }; pickables.push(mesh);
    return { grp, mesh, halo, lab, layer, disc, rim, funnel, r, open: false, t: 0, tStart: 0, tFrom: 0, dim: 1,
      base: { x: p.x, z: p.z }, from: { x: p.x, z: p.z }, to: { x: p.x, z: p.z }, depth: DEPTH, depthFrom: DEPTH, depthTo: DEPTH };
  });

  // ---- keeping open layers apart on screen ----
  // Seen from a camera tilted by phi, a layer at depth d appears shifted toward the viewer by d*tan(phi)
  // (in ground-plane units), i.e. on top of whatever theme sits in front of it. Work in that screen-aligned
  // plane: each theme has a sphere footprint, an open theme also a layer footprint. Pick each open layer's
  // depth (DEPTH or 1.6*DEPTH, whichever overlaps less), then push themes apart until no footprints of
  // different themes overlap, with a weak spring back to the original spot. All closed -> original layout.
  const DEPTH2 = DEPTH * 1.55, MOVE_MS = REDUCED ? 1 : 750;
  let moveT0 = 0;
  function relayout(phi, theta) {
    const tanp = Math.tan(Math.min(phi, 1.25)), st = Math.sin(theta), ct = Math.cos(theta);
    if (!openList.length || phi < 0.05) themes.forEach(T => { T.to = { x: T.base.x, z: T.base.z }; T.depthTo = DEPTH; });
    else {
      const P = themes.map(T => { const a = T.base.x * ct - T.base.z * st, v = T.base.x * st + T.base.z * ct; return { a, v, a0: a, v0: v, open: T.open, d: DEPTH }; });
      const sph = new THREE.Spherical(), pv = V(0, 0, 0);
      const rs = i => themeR[i] + 14, rd = i => L.discR[i] + 12;
      const ov = (a1, v1, r1, a2, v2, r2) => Math.max(0, r1 + r2 - Math.hypot(a2 - a1, v2 - v1));
      const placed = [];
      openList.forEach(i => {
        let best = DEPTH, bo = Infinity;
        [DEPTH, DEPTH2].forEach(d => {
          const va = P[i].v + d * tanp; let o = 0;
          P.forEach((q, j) => { if (j === i) return; o += ov(P[i].a, va, rd(i), q.a, q.v, rs(j));
            if (placed.includes(j)) o += ov(P[i].a, va, rd(i), q.a, q.v + q.d * tanp, rd(j)); });
          if (o < bo - 1e-6) { bo = o; best = d; }
        });
        P[i].d = best; placed.push(i);
      });
      // wide canvas: separate sideways more than front-to-back (keeps the tilted view inside the height)
      const asp = Math.max(1, Math.min(3, camera.aspect)), HW = Math.sqrt(asp) * 0.9, VW = 1 / Math.sqrt(asp) * 0.9 + 0.1;
      const foot = (p, i) => p.open ? [[p.a, p.v, rs(i)], [p.a, p.v + p.d * tanp, rd(i)]] : [[p.a, p.v, rs(i)]];
      for (let it = 0; it < 400; it++) {
        for (let i = 0; i < P.length; i++) for (let j = i + 1; j < P.length; j++) {
          const fi = foot(P[i], i), fj = foot(P[j], j);
          for (const A of fi) for (const B of fj) {
            let da = B[0] - A[0], dv = B[1] - A[1], d = Math.hypot(da, dv); const need = A[2] + B[2];
            if (d >= need) continue;
            if (d < 1e-3) { da = 1; dv = 0; d = 1; }
            const push = (need - d) * 0.25, ua = da / d * HW, uv = dv / d * VW;
            P[i].a -= ua * push; P[i].v -= uv * push; P[j].a += ua * push; P[j].v += uv * push;
          }
        }
        P.forEach(p => { p.a += (p.a0 - p.a) * 0.01; p.v += (p.v0 - p.v) * 0.01; });
      }
      themes.forEach((T, i) => { const p = P[i]; T.to = { x: p.a * ct + p.v * st, z: -p.a * st + p.v * ct }; T.depthTo = p.d; });
      // second pass with the real (perspective) camera: frame the planned layout, project every footprint,
      // and push apart the themes whose screen ellipses still overlap
      const toW = (a, v) => ({ x: a * ct + v * st, z: -a * st + v * ct });
      const q1 = V(0, 0, 0);
      for (let it = 0; it < 40; it++) {
        themes.forEach((T, i) => { T.to = toW(P[i].a, P[i].v); });
        const pose = fitPose(phi, theta, framePoints(openList));
        sph.set(pose.r, phi, theta); probe.position.setFromSpherical(sph).add(pose.target); probe.lookAt(pose.target); probe.updateMatrixWorld();
        const asp = probe.aspect, scr = (x, y, z) => { pv.set(x, y, z).project(probe); return [pv.x * asp, pv.y]; };
        // screen units per ground unit along a (right) and v (toward the viewer), measured at the centre
        const c0 = scr(pose.target.x, 0, pose.target.z), ca = scr(pose.target.x + ct, 0, pose.target.z - st), cv = scr(pose.target.x + st, 0, pose.target.z + ct);
        const ka = Math.hypot(ca[0] - c0[0], ca[1] - c0[1]) || 1e-3, kv = Math.hypot(cv[0] - c0[0], cv[1] - c0[1]) || 1e-3;
        const F = [];
        themes.forEach((T, i) => {
          const p = T.to, c = scr(p.x, 0, p.z), e = scr(p.x + rs(i) * ct, 0, p.z - rs(i) * st);
          const rr = Math.hypot(e[0] - c[0], e[1] - c[1]); F.push({ i, s: true, x: c[0], y: c[1], rx: rr, ry: rr });
          if (T.open) { const d = P[i].d, R = rd(i), cc = scr(p.x, -d, p.z), ex = scr(p.x + R * ct, -d, p.z - R * st), ez = scr(p.x + R * st, -d, p.z + R * ct);
            F.push({ i, s: false, x: cc[0], y: cc[1], rx: Math.hypot(ex[0] - cc[0], ex[1] - cc[1]), ry: Math.hypot(ez[0] - cc[0], ez[1] - cc[1]) }); }
        });
        let moved = false;
        for (let x = 0; x < F.length; x++) for (let y = x + 1; y < F.length; y++) {
          const A = F[x], B = F[y]; if (A.i === B.i || (A.s && B.s)) continue;
          let dx = B.x - A.x, dy = B.y - A.y; const nx = dx / (A.rx + B.rx), ny = dy / (A.ry + B.ry), d = Math.hypot(nx, ny);
          if (d >= 1.04) continue;
          if (d < 1e-4) { dx = 1; dy = 0; }
          const L2 = Math.hypot(dx, dy), gap = (1.04 - d) * Math.hypot(A.rx + B.rx, A.ry + B.ry) * 0.5 * 0.6;
          const ua = dx / L2 * gap / ka * HW, uv = -dy / L2 * gap / kv * VW;          // screen up = away from the viewer
          P[A.i].a -= ua / 2; P[A.i].v -= uv / 2; P[B.i].a += ua / 2; P[B.i].v += uv / 2; moved = true;
        }
        if (!moved) break;
      }
      themes.forEach((T, i) => { T.to = toW(P[i].a, P[i].v); });
      // still colliding? stagger depths further (1, 1.55, 2.1 x DEPTH): of two overlapping layers, the one on
      // the far side (smaller v) goes deeper, so near layers do not hide far ones
      const LEVELS = [DEPTH, DEPTH * 1.55, DEPTH * 2.1];
      for (let round = 0; round < 6; round++) {
        const pose = fitPose(phi, theta, framePoints(openList));
        sph.set(pose.r, phi, theta); probe.position.setFromSpherical(sph).add(pose.target); probe.lookAt(pose.target); probe.updateMatrixWorld();
        const asp = probe.aspect, disc = i => { const p = themes[i].to, d = P[i].d, R = rd(i);
          pv.set(p.x, -d, p.z).project(probe); const cx = pv.x * asp, cy = pv.y;
          pv.set(p.x + R * ct, -d, p.z - R * st).project(probe); const rx = Math.hypot(pv.x * asp - cx, pv.y - cy);
          pv.set(p.x + R * st, -d, p.z + R * ct).project(probe); const ry = Math.hypot(pv.x * asp - cx, pv.y - cy);
          return { cx, cy, rx, ry }; };
        let changed = false;
        for (let x = 0; x < openList.length; x++) for (let y = x + 1; y < openList.length; y++) {
          const i = openList[x], j = openList[y], A = disc(i), B = disc(j);
          const d = Math.hypot((A.cx - B.cx) / (A.rx + B.rx), (A.cy - B.cy) / (A.ry + B.ry));
          if (d >= 1) continue;
          const far = P[i].v < P[j].v ? i : j, lv = LEVELS.indexOf(P[far].d), k = lv < 0 ? 1 : lv + 1;
          if (k < LEVELS.length) { P[far].d = LEVELS[k]; changed = true; }
        }
        if (!changed) break;
        themes.forEach((T, i) => { T.depthTo = P[i].d; });
      }
    }
    themes.forEach(T => { T.from = { x: T.mesh.position.x, z: T.mesh.position.z }; T.depthFrom = T.depth; });
    moveT0 = performance.now();
  }
  { const p0 = POSE.flat(); refDist = p0.r; placeCamera(p0); } controls.enableRotate = false;   // flat: pan and zoom only
  // ---- detailed nodes, waiting inside their theme ----
  const members = {};
  const stemGeo = () => { const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3)); return geo; };
  g.nodes.forEach(n => {
    const i = L.gi[n.name], T = themes[i], col = new THREE.Color(colorFor(n.type)), off = L.mem[n.name] || { dx: 0, dz: 0 };
    const mesh = new THREE.Mesh(GEO.sphere, new THREE.MeshStandardMaterial({ color: col, roughness: 0.5, transparent: true, opacity: 0 }));
    mesh.scale.setScalar(2.4);
    const lab = makeLabel(n.name, { size: 26, maxW: 330 }); Object.assign(lab.userData, { r: 2.4, px: 12, name: n.name, base: 0 });
    const stem = new THREE.Line(stemGeo(), new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0 }));
    [mesh, lab, stem].forEach(o => { o.visible = false; scene.add(o); });
    mesh.userData = { kind: "node", name: n.name }; pickables.push(mesh);
    const home = V(T.mesh.position.x + off.dx, -DEPTH, T.mesh.position.z + off.dz);
    members[n.name] = { n, i, T, mesh, lab, stem, home, off, emph: 1, pulse0: 0, size: 1 };
  });
  const allLabels = () => themes.map(T => T.lab).concat(Object.values(members).map(m => m.lab));
  function paintLabels() { allLabels().forEach(l => drawLabel(l, l.userData.text, Object.assign({ color: ink, bg: labelBg }, l.userData.style))); }
  paintLabels();

  // ---- state shared by edges, labels and dimming ----
  let selected = null, hoverNode = null, selPair = null, markSet = null;
  const emph = () => selPair || markSet;               // {nodes:Set, pairs:Set("a|b")} or null

  // ---- labels: constant screen size, hung below their sphere; overlapping labels fade (less important first) ----
  const lblItems = [];
  function layoutLabels() {
    const Hh = H(), Ww = W(), tanH = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)), anyOpen = openList.length > 0;
    const focus = selected || hoverNode, near = new Set(), E = emph();
    if (focus) g.edges.forEach(e => { if (e.from === focus) near.add(e.to); if (e.to === focus) near.add(e.from); });
    lblItems.length = 0;
    allLabels().forEach(l => {
      if (!l.visible) return;
      const u = l.userData, dist = camera.position.distanceTo(l.position), ppw = Hh / (2 * dist * tanH);
      const k = u.px / u.size * Math.min(1.35, Math.max(0.85, Math.sqrt(refDist / dist)));
      const pw = u.pw * k, ph = u.ph * k, gap = u.r * ppw * (u.name != null ? members[u.name].size : 1) + 4;
      l.scale.set(pw / ppw, ph / ppw, 1); l.center.set(0.5, 1 + gap / ph);
      vf.copy(l.position).project(camera);
      const sx = (vf.x + 1) / 2 * Ww, sy = (1 - vf.y) / 2 * Hh + gap, cy = sy - gap;
      const pr = u.name != null ? (u.name === focus ? 100 : near.has(u.name) ? 50 : (E && E.nodes.has(u.name)) ? 60 : 10)
        : (themes[u.ti].open ? 20 : 5 + g.groups[u.ti].members.length * 0.1 - (anyOpen ? 3 : 0));
      lblItems.push({ l, pr, x0: sx - pw / 2, y0: sy, x1: sx + pw / 2, y1: sy + ph, off: vf.z > 1, cy, gap, ph, own: u.name != null ? members[u.name].mesh : themes[u.ti].mesh });
    });
    lblItems.sort((a, b) => b.pr - a.pr);
    // spheres are obstacles too (projected radius + 4 px), except the label's own sphere
    const balls = [];
    themes.forEach(T => { if (T.mesh.material.opacity > 0.2) balls.push(ball(T.mesh, T.r)); });
    Object.values(members).forEach(m => { if (m.mesh.visible && m.mesh.material.opacity > 0.2) balls.push(ball(m.mesh, m.mesh.scale.x)); });
    function ball(mesh, r) { vf.copy(mesh.position).project(camera); const x = (vf.x + 1) / 2 * Ww, y = (1 - vf.y) / 2 * Hh;
      const rp = r * Hh / (2 * camera.position.distanceTo(mesh.position) * tanH) + 4; return { mesh, x0: x - rp, x1: x + rp, y0: y - rp, y1: y + rp }; }
    const kept = [], hits = (it, y0, y1) => kept.some(r => !(it.x1 < r.x0 || it.x0 > r.x1 || y1 < r.y0 || y0 > r.y1))
      || balls.some(b => b.mesh !== it.own && !(it.x1 < b.x0 || it.x0 > b.x1 || y1 < b.y0 || y0 > b.y1));
    lblItems.forEach(it => {
      const u = it.l.userData, base = u.base ?? 1;
      let hit = it.off || hits(it, it.y0, it.y1);
      if (hit && !it.off) {                           // try above the sphere instead
        const y1 = it.cy - it.gap, y0 = y1 - it.ph;
        if (!hits(it, y0, y1)) { hit = false; it.y0 = y0; it.y1 = y1; it.l.center.y = -it.gap / it.ph; }
      }
      if (!hit && base > 0.3) kept.push(it);
      u.a += ((hit ? 0 : 1) - u.a) * (REDUCED ? 1 : 0.2);
      it.l.material.opacity = u.a * base * (u.dim ?? 1);
    });
  }

  // ---- edges: rebuilt when what is shown changes (old ones disposed), positions updated in place ----
  // Links between nodes (and between closed themes) are tubes along a gentle arc, so they read as the
  // content and stay visible; node -> closed-theme links are thin dashed lines (hover/selection only).
  const edgeGroup = new THREE.Group(); scene.add(edgeGroup);
  let edgeList = [];
  const TS = 14, TR = 6;                                // tube segments along / around
  const RC = Float32Array.from({ length: TR }, (_, r) => Math.cos(r / TR * Math.PI * 2)), RS = Float32Array.from({ length: TR }, (_, r) => Math.sin(r / TR * Math.PI * 2));
  function tubeGeo() {
    const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array((TS + 1) * TR * 3), 3));
    const idx = [];
    for (let a = 0; a < TS; a++) for (let r = 0; r < TR; r++) {
      const p = a * TR + r, q = a * TR + (r + 1) % TR, u = (a + 1) * TR + r, w = (a + 1) * TR + (r + 1) % TR;
      idx.push(p, u, q, q, u, w);
    }
    geo.setIndex(idx); return geo;
  }
  const endOf = name => { const m = members[name]; if (!m) return null; return m.T.open || m.T.t > 0 ? { obj: m.mesh, node: name } : { obj: m.T.mesh, theme: m.i }; };
  const UP = V(0, 1, 0), XAX = V(1, 0, 0);
  function rebuildEdges() {
    edgeGroup.children.forEach(o => { if (o.geometry !== GEO.cone) o.geometry.dispose(); o.material.dispose(); });
    edgeGroup.clear(); edgeList = [];
    const agg = {}, focus = selected || hoverNode, E = emph();
    g.edges.forEach(e => {
      const a = endOf(e.from), b = endOf(e.to); if (!a || !b) return;
      if (a.theme != null && a.theme === b.theme) return;
      const k = (a.node || "t" + a.theme) + "|" + (b.node || "t" + b.theme);
      (agg[k] = agg[k] || { k, a, b, w: 0, e: [] }).w++; agg[k].e.push(e);
    });
    Object.values(agg).forEach(({ k, a, b, w, e }) => {
      const both = !!(a.node && b.node), cross = !!a.node !== !!b.node;
      const focusHit = !!focus && e.some(x => x.from === focus || x.to === focus);
      const markHit = !!E && e.some(x => E.pairs.has(x.from + "|" + x.to));
      const hit = focusHit || markHit, dim = (!!focus || !!E) && !hit;
      if (cross && !focusHit) return;                // node -> closed-theme links only for the hovered/selected node
      const col = hit ? accent : colorFor(a.node ? members[a.node].n.type : g.groups[a.theme].type);   // source type
      const rB = () => b.theme != null ? themes[b.theme].r : 2.4 * members[b.node].size, rA = () => a.theme != null ? themes[a.theme].r : 2.4 * members[a.node].size;
      if (cross) {
        const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
        const line = new THREE.Line(geo, new THREE.LineDashedMaterial({ color: col, transparent: true, opacity: 0.9, dashSize: 2.5, gapSize: 2.5 }));
        line.frustumCulled = false; edgeGroup.add(line);
        edgeList.push({ kind: "line", a: a.obj, b: b.obj, line, baseOp: 0.9 }); return;
      }
      const ww = Math.min(w, 6);
      const op = dim ? 0.06 : hit ? 1 : both ? 0.85 : Math.min(0.8, 0.35 + 0.1 * ww);
      const radius = (both ? 0.42 : 0.3 + 0.1 * ww) * (hit ? 1.35 : 1);
      const mat = new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: op, depthWrite: !dim });
      const tube = new THREE.Mesh(tubeGeo(), mat); tube.frustumCulled = false;
      const cone = new THREE.Mesh(GEO.cone, new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: op }));
      cone.scale.setScalar(both ? (hit ? 1.75 : 1.45) : 1.1 + 0.12 * ww); cone.frustumCulled = false;
      edgeGroup.add(tube); edgeGroup.add(cone);
      const back = k.split("|").reverse().join("|");
      edgeList.push({ kind: "tube", a: a.obj, b: b.obj, tube, cone, baseOp: op, radius, rA, rB, side: agg[back] ? 1 : 0 });
    });
    updateEdges();
  }
  const C = V(0, 0, 0), Q = V(0, 0, 0), Tg = V(0, 0, 0), N = V(0, 0, 0), Bn = V(0, 0, 0), D = V(0, 0, 0), S = V(0, 0, 0);
  function bez(t, p0, c, p2, out) { const u = 1 - t; return out.set(u * u * p0.x + 2 * u * t * c.x + t * t * p2.x, u * u * p0.y + 2 * u * t * c.y + t * t * p2.y, u * u * p0.z + 2 * u * t * c.z + t * t * p2.z); }
  function bezT(t, p0, c, p2, out) { return out.set(2 * (1 - t) * (c.x - p0.x) + 2 * t * (p2.x - c.x), 2 * (1 - t) * (c.y - p0.y) + 2 * t * (p2.y - c.y), 2 * (1 - t) * (c.z - p0.z) + 2 * t * (p2.z - c.z)).normalize(); }
  function updateEdges() {
    for (const E of edgeList) {
      const pa = E.a.position, pb = E.b.position;
      const vis = E.a.visible && E.b.visible, f = Math.min(E.a.material.opacity ?? 1, E.b.material.opacity ?? 1);
      if (E.kind === "line") {
        const pos = E.line.geometry.attributes.position; pos.setXYZ(0, pa.x, pa.y, pa.z); pos.setXYZ(1, pb.x, pb.y, pb.z); pos.needsUpdate = true;
        E.line.computeLineDistances(); E.line.visible = vis; E.line.material.opacity = E.baseOp * f; continue;
      }
      E.tube.visible = E.cone.visible = vis; if (!vis) continue;
      E.tube.material.opacity = E.cone.material.opacity = E.baseOp * f;
      // reshape only when an end moved or grew (nothing to do while the view is at rest)
      const ra = E.rA(), rb = E.rB(), K = E.key || (E.key = new Float32Array(8));
      if (K[0] === pa.x && K[1] === pa.y && K[2] === pa.z && K[3] === pb.x && K[4] === pb.y && K[5] === pb.z && K[6] === ra && K[7] === rb) continue;
      K[0] = pa.x; K[1] = pa.y; K[2] = pa.z; K[3] = pb.x; K[4] = pb.y; K[5] = pb.z; K[6] = ra; K[7] = rb;
      D.copy(pb).sub(pa); const len = D.length(); if (len < 1e-3) { E.tube.visible = E.cone.visible = false; continue; }
      // control point: lifted by ~20% of the length (an arc between layers); two-way pairs bow to opposite sides
      C.copy(pa).add(pb).multiplyScalar(0.5); C.y += 0.2 * len;
      if (E.side) { S.copy(D).cross(UP); if (S.lengthSq() > 1e-6) C.addScaledVector(S.normalize(), 0.14 * len); }
      const arrowH = 3.1 * E.cone.scale.x;
      const t0 = Math.min(0.4, (ra + 0.6) / len), t1 = Math.max(0.6, 1 - (rb + arrowH + 0.4) / len);
      const pos = E.tube.geometry.attributes.position;
      for (let s2 = 0; s2 <= TS; s2++) {
        const t = t0 + (t1 - t0) * s2 / TS; bez(t, pa, C, pb, Q); bezT(t, pa, C, pb, Tg);
        N.copy(Tg).cross(UP); if (N.lengthSq() < 1e-6) N.copy(Tg).cross(XAX); N.normalize(); Bn.copy(N).cross(Tg).normalize();
        for (let r = 0; r < TR; r++) {
          const cs = RC[r] * E.radius, sn = RS[r] * E.radius;
          pos.setXYZ(s2 * TR + r, Q.x + N.x * cs + Bn.x * sn, Q.y + N.y * cs + Bn.y * sn, Q.z + N.z * cs + Bn.z * sn);
        }
      }
      pos.needsUpdate = true;
      bez(t1, pa, C, pb, Q); bezT(t1, pa, C, pb, Tg);
      E.cone.position.copy(Q).addScaledVector(Tg, arrowH / 2); E.cone.quaternion.setFromUnitVectors(UP, Tg);
    }
  }

  // ---- opening and closing ----
  const openCount = () => openList.length;
  function setOpen(i, open, opt = {}) {
    const T = themes[i]; if (!T || T.open === open) return false;
    T.open = open; T.tStart = performance.now(); T.tFrom = T.t;
    if (!open && selected && T.grp.members.includes(selected)) { selected = null; refreshSizes(); }
    openList = themes.map((t, j) => t.open ? j : -1).filter(j => j >= 0);
    if (!opt.noCamera) {
      if (open) goPose("focus", i);
      else if (!openList.length) goPose("flat");
      else goPose("focus", openList[openList.length - 1]);
    }
    if (!opt.batch) { rebuildEdges(); opts.onChange && opts.onChange(openCount(), themes.length); }
    return true;
  }
  function refreshSizes() { Object.values(members).forEach(m => { m.size = m.n.name === selected ? 1.7 : 1; }); }
  function reveal(names, opt = {}) {           // open every closed theme holding one of these nodes
    const want = new Set((names || []).map(n => members[n] && members[n].i).filter(i => i != null && !themes[i].open));
    if (!want.size) return false;
    const list = [...want];
    list.forEach(i => setOpen(i, true, { noCamera: true, batch: true })); rebuildEdges(); opts.onChange && opts.onChange(openCount(), themes.length);
    if (!opt.noCamera) goPose("focus", list[list.length - 1]);
    else relayout(TILT, leanTo(list[list.length - 1]));       // the caller moves the camera (focus) itself
    return true;
  }

  // ---- picking ----
  const tip = opts.tip;
  const hideTip = () => { if (tip) tip.classList.remove("show"); };
  const ray = new THREE.Raycaster(), ptr = new THREE.Vector2(); let down = null, hover = null;
  const el = renderer.domElement;
  const pick = ev => { const r = el.getBoundingClientRect();
    ptr.set((ev.clientX - r.left) / r.width * 2 - 1, -(ev.clientY - r.top) / r.height * 2 + 1); ray.setFromCamera(ptr, camera);
    const hit = ray.intersectObjects(pickables.filter(o => o.visible && (o.material.opacity ?? 1) > 0.1))[0]; return hit && hit.object; };
  const onDown = ev => { down = [ev.clientX, ev.clientY]; };
  const onUp = ev => {
    if (!down || Math.hypot(ev.clientX - down[0], ev.clientY - down[1]) > 5) { down = null; return; } down = null;
    hideTip();
    const o = pick(ev);
    if (!o) { if (opts.onBackgroundClick) opts.onBackgroundClick(); return; }
    if (o.userData.kind === "theme") setOpen(o.userData.i, !themes[o.userData.i].open);
    else if (opts.onNodeClick) { const m = members[o.userData.name]; opts.onNodeClick({ key: m.n.name, name: m.n.name, type: m.n.type }); }
  };
  const onMove = ev => {
    const o = pick(ev); el.style.cursor = o ? "pointer" : (mode === "flat" ? "default" : "grab");
    if (hover && hover !== o) hover.material.emissive?.setHex(0); hover = o; if (o) o.material.emissive?.setHex(dark ? 0x444444 : 0x222222);
    const hn = o && o.userData.kind === "node" ? o.userData.name : null;
    if (hn !== hoverNode) { hoverNode = hn; rebuildEdges(); }
    if (!tip) return;
    if (!o) { hideTip(); return; }
    const d = o.userData;
    if (d.kind === "theme") { const grp = g.groups[d.i];
      tip.innerHTML = `<div class='tt-type'>Theme · ${escH(grp.type)}</div><b>${escH(grp.label)}</b><div class='tt-ev'>${grp.members.length} node${grp.members.length === 1 ? "" : "s"} · click to ${themes[d.i].open ? "close" : "open"}</div>`;
    } else { const m = members[d.name], outs = g.edges.filter(e => e.from === d.name).length, ins = g.edges.filter(e => e.to === d.name).length;
      tip.innerHTML = `<div class='tt-type'>${escH(m.n.type)}</div><b>${escH(d.name)}</b><div class='tt-type'>causes ${outs} · caused by ${ins}</div><div class='tt-ev'>click for details</div>`; }
    tip.classList.add("show");
    tip.style.left = Math.min(window.innerWidth - 320, ev.clientX + 14) + "px"; tip.style.top = (ev.clientY + 14) + "px";
  };
  const onLeave = () => { hideTip(); if (hover) { hover.material.emissive?.setHex(0); hover = null; } if (hoverNode) { hoverNode = null; rebuildEdges(); } };
  el.addEventListener("pointerdown", onDown); el.addEventListener("pointerup", onUp);
  el.addEventListener("pointermove", onMove); el.addEventListener("pointerleave", onLeave);

  // ---- light/dark: recolour materials and redraw only the label textures ----
  function recolor() {
    readColors();
    themes.forEach(T => { const c = new THREE.Color(colorFor(T.grp.type)); [T.mesh, T.halo, T.disc, T.rim, T.funnel].forEach(o => o.material.color.copy(c)); });
    Object.values(members).forEach(m => { const c = new THREE.Color(colorFor(m.n.type)); m.mesh.material.color.copy(c); m.stem.material.color.copy(c); });
    paintLabels(); rebuildEdges();
  }
  const mo = new MutationObserver(recolor); mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const mq = matchMedia("(prefers-color-scheme: dark)"); mq.addEventListener("change", recolor);

  // ---- resize: refit the flat view ----
  const ro = new ResizeObserver(() => { camera.aspect = W() / H(); camera.updateProjectionMatrix(); renderer.setSize(W(), H());
    if (mode === "flat" && !camTween) { const p = POSE.flat(); refDist = p.r; placeCamera(p); } });
  ro.observe(container);

  // ---- animation loop ----
  let raf = 0, dead = false;
  const ft = new Float32Array(240), fr = new Float32Array(240); let fti = 0, ftn = 0;   // per-frame JS / render time (ms)
  function frame(now) {
    if (dead) return;
    raf = requestAnimationFrame(frame);
    if (!container.isConnected) { destroy(); return; }
    if (container.offsetParent === null) return;          // hidden (another step is on screen): skip work
    const t0 = performance.now();
    if (camTween) {
      const k = ease(clamp01((now - camTween.t0) / CAM_MS)), A = camTween.from, B = camTween.to;
      vf.copy(A.target).lerp(B.target, k);
      placeCamera({ target: vf, r: A.r + (B.r - A.r) * k, phi: A.phi + (B.phi - A.phi) * k, theta: A.theta + (((B.theta - A.theta + Math.PI * 3) % (Math.PI * 2)) - Math.PI) * k });
      if (k >= 1) { camTween = null; controls.enabled = true; }
    }
    let structural = false; const anyOpen = openList.length > 0, E = emph();
    const mk = ease(clamp01((now - moveT0) / MOVE_MS));
    themes.forEach(T => {
      T.mesh.position.set(T.from.x + (T.to.x - T.from.x) * mk, 0, T.from.z + (T.to.z - T.from.z) * mk);
      T.depth = T.depthFrom + (T.depthTo - T.depthFrom) * mk;
      T.halo.position.copy(T.mesh.position); T.lab.position.copy(T.mesh.position);
      T.layer.position.set(T.mesh.position.x, -T.depth, T.mesh.position.z);
      const dur = T.open ? OPEN_MS : CLOSE_MS, goal = T.open ? 1 : 0;
      if (T.t !== goal) {
        const p = clamp01((now - T.tStart) / dur); T.t = T.tFrom + (goal - T.tFrom) * p;
        if (p >= 1) { T.t = goal; if (!T.open) structural = true; }
      }
      const t = T.t, layerK = ease(clamp01(t / 0.55)), dropK = clamp01((t - 0.2) / 0.8);
      T.layer.visible = T.funnel.visible = t > 0;
      T.layer.scale.setScalar(Math.max(0.001, easeOutBack(layerK)));
      const focusT = (selected || hoverNode) && members[selected || hoverNode] && members[selected || hoverNode].T === T;
      const crowd = focusT ? 1 : 1 / Math.sqrt(Math.max(1, openList.length));
      T.disc.material.opacity = 0.13 * layerK * crowd; T.rim.material.opacity = 0.55 * layerK * Math.max(0.5, crowd);
      T.funnel.material.opacity = 0.06 * layerK * crowd; T.funnel.scale.set(1, Math.max(0.001, layerK * T.depth / DEPTH), 1);
      T.funnel.position.set(T.mesh.position.x, -T.depth * layerK / 2, T.mesh.position.z);
      T.halo.material.opacity = layerK;
      const dimGoal = anyOpen && !T.open && T.t === 0 ? 0.35 : 1; T.dim += (dimGoal - T.dim) * (REDUCED ? 1 : 0.12);
      T.mesh.material.opacity = T.dim; T.lab.userData.dim = T.dim;
      T.grp.members.forEach(nm => {
        const m = members[nm];
        const k = ease(dropK), vis = t > 0;
        m.mesh.visible = m.lab.visible = m.stem.visible = vis;
        if (!vis) return;
        m.home.set(T.mesh.position.x + m.off.dx, -T.depth, T.mesh.position.z + m.off.dz);
        m.mesh.position.lerpVectors(T.mesh.position, m.home, k);
        const eGoal = E && !E.nodes.has(nm) ? 0.2 : 1; m.emph += (eGoal - m.emph) * (REDUCED ? 1 : 0.15);
        m.mesh.material.opacity = clamp01(dropK * 3) * m.emph;
        let s = m.size;
        if (m.pulse0) { const pt = (now - m.pulse0) / 1200; if (pt >= 1) m.pulse0 = 0; else s *= 1 + 0.7 * Math.sin(pt * Math.PI) * (1 - pt); }
        m.mesh.scale.setScalar(2.4 * s);
        m.lab.position.copy(m.mesh.position);
        m.lab.userData.base = clamp01((dropK - 0.6) / 0.4); m.lab.userData.dim = m.emph;
        const sp = m.stem.geometry.attributes.position, a = T.mesh.position, b = m.mesh.position;
        sp.setXYZ(0, a.x, a.y, a.z); sp.setXYZ(1, b.x, b.y, b.z); sp.needsUpdate = true; m.stem.material.opacity = 0.16 * k * m.emph;   // stems stay faint: links are the content
      });
    });
    if (structural) rebuildEdges(); else updateEdges();
    layoutLabels();
    controls.update();
    const t1 = performance.now(); renderer.render(scene, camera);
    ft[fti] = t1 - t0; fr[fti] = performance.now() - t1; fti = (fti + 1) % ft.length; ftn = Math.min(ft.length, ftn + 1);
  }
  raf = requestAnimationFrame(frame);
  rebuildEdges();

  function destroy() {
    if (dead) return; dead = true;
    cancelAnimationFrame(raf); ro.disconnect(); mo.disconnect(); mq.removeEventListener("change", recolor);
    el.removeEventListener("pointerdown", onDown); el.removeEventListener("pointerup", onUp);
    el.removeEventListener("pointermove", onMove); el.removeEventListener("pointerleave", onLeave);
    hideTip(); controls.dispose();
    const mats = new Set(), geos = new Set();
    scene.traverse(o => { if (o.geometry) geos.add(o.geometry); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => mats.add(m)); });
    geos.forEach(x => x.dispose()); mats.forEach(m => { if (m.map) m.map.dispose(); m.dispose(); });
    Object.values(GEO).forEach(x => x.dispose());
    renderer.dispose(); renderer.forceContextLoss();
    if (el.parentNode) el.parentNode.removeChild(el);
  }

  const nodesOf = keys => (keys || []).filter(k => members[k]);
  // Any camera move we start frames every open layer (never leaves one off-screen); the nodes asked for
  // are inside that frame. The user can still zoom in by hand afterwards.
  function refit() {
    if (!openList.length) { glideTo(POSE.flat(), "flat"); return; }
    const s = new THREE.Spherical().setFromVector3(camera.position.clone().sub(controls.target));
    const phi = mode === "flat" ? TILT : s.phi, theta = mode === "flat" ? 0 : s.theta;
    relayout(phi, theta);                           // re-plan for this view (and the space the panel leaves)
    glideTo(fitPose(phi, theta, framePoints(openList)), "3d");
  }
  const focusOn = names => { if (nodesOf(names).length || openList.length) refit(); };
  return {
    expandAll() { themes.forEach((T, i) => setOpen(i, true, { noCamera: true, batch: true })); rebuildEdges(); opts.onChange && opts.onChange(openCount(), themes.length);
      if (openList.length) { relayout(TILT, 0); glideTo(fitPose(TILT, 0, framePoints(openList)), "3d"); } },
    openOnly(names, keepIdx) {                   // open exactly the themes holding these nodes (plus keepIdx); close the rest
      const need = new Set((names || []).map(n => members[n] && members[n].i).filter(i => i != null));
      (keepIdx || []).forEach(i => need.add(i));
      let last = null;
      themes.forEach((T, i) => { const want = need.has(i); if (T.open !== want) { setOpen(i, want, { noCamera: true, batch: true }); if (want) last = i; } });
      rebuildEdges(); opts.onChange && opts.onChange(openCount(), themes.length);
      if (openList.length) relayout(TILT, last != null ? leanTo(last) : 0); else goPose("flat");
    },
    openSet: () => openList.slice(),
    setInset(px) { px = Math.max(0, Math.round(px || 0)); if (Math.abs(px - insetR) < 2) return; insetR = px; refit(); },
    collapseAll() { themes.forEach((_, i) => setOpen(i, false, { noCamera: true, batch: true })); rebuildEdges(); opts.onChange && opts.onChange(openCount(), themes.length); goPose("flat"); },
    toggle(i) { setOpen(i, !themes[i].open); },
    mode: () => mode, openCount, themeCount: () => themes.length,
    reveal, isOpenFor: name => !!members[name] && members[name].T.open,
    themeOf: name => members[name] ? g.groups[members[name].i] : null,
    closeThemeOf(name) { if (members[name]) setOpen(members[name].i, false); },
    select(key) { if (!members[key]) return false; selected = key; selPair = null; reveal([key]); refreshSizes(); rebuildEdges(); return true; },
    selectEdge(a, b) { if (!members[a] || !members[b]) return false; selected = null; refreshSizes();
      selPair = { nodes: new Set([a, b]), pairs: new Set([a + "|" + b]) }; reveal([a, b]); rebuildEdges(); return true; },
    unselect() { selected = null; selPair = null; refreshSizes(); rebuildEdges(); },
    mark(keys, pairIds) { markSet = { nodes: new Set(nodesOf(keys)), pairs: new Set(pairIds || []) }; reveal(keys, { noCamera: true }); rebuildEdges(); },
    clearMark() { markSet = null; rebuildEdges(); },
    focus: focusOn,
    pulse(keys) { const t = performance.now(); nodesOf(keys).forEach(k => { members[k].pulse0 = REDUCED ? 0 : t; }); },
    screenPos(kind, key) {                       // page coordinates of a theme (index) or node (name), for tests
      const o = kind === "theme" ? themes[key].mesh : members[key].mesh; const v = o.position.clone().project(camera);
      const r = el.getBoundingClientRect(); return { x: r.left + (v.x + 1) / 2 * r.width, y: r.top + (1 - v.y) / 2 * r.height };
    },
    themeIndexOf: label => themes.findIndex(T => T.grp.label === label),
    overlap() {                                  // screen-space overlap between layers / theme spheres (px), for tests
      const box = (cx, cy, rx, ry) => ({ cx, cy, rx, ry }), Hh = H(), Ww = W(), out = [];
      const proj = p => { vf.copy(p).project(camera); return { x: (vf.x + 1) / 2 * Ww, y: (1 - vf.y) / 2 * Hh }; };
      themes.forEach((T, i) => {
        const c = proj(T.mesh.position), e = proj(V(T.mesh.position.x + T.r, 0, T.mesh.position.z));
        out.push({ i, kind: "sphere", ...box(c.x, c.y, Math.abs(e.x - c.x) || T.r, Math.abs(e.x - c.x) || T.r) });
        if (T.open && T.t >= 1) {
          const R = L.discR[i], q = T.layer.position, c2 = proj(q);
          const px = proj(V(q.x + R, q.y, q.z)), pz = proj(V(q.x, q.y, q.z + R));
          out.push({ i, kind: "layer", ...box(c2.x, c2.y, Math.hypot(px.x - c2.x, px.y - c2.y), Math.hypot(pz.x - c2.x, pz.y - c2.y)) });
        }
      });
      let worst = 0, pairs = 0;
      for (let a = 0; a < out.length; a++) for (let b = a + 1; b < out.length; b++) {
        const A = out[a], B = out[b]; if (A.i === B.i || (A.kind === "sphere" && B.kind === "sphere")) continue;
        const dx = (A.cx - B.cx) / (A.rx + B.rx), dy = (A.cy - B.cy) / (A.ry + B.ry), d = Math.hypot(dx, dy);
        if (d < 1) { pairs++; worst = Math.max(worst, 1 - d); }
      }
      return { pairs, worst: Math.round(worst * 100) / 100 };
    },
    depths: () => themes.filter(T => T.open).map(T => Math.round(T.depthTo)),
    extent() {                                   // share of the canvas the framed graph covers now (%), for tests
      let x0 = 1, x1 = -1, y0 = 1, y1 = -1;
      framePoints(openList).forEach(p => { vf.copy(p).project(camera); x0 = Math.min(x0, vf.x); x1 = Math.max(x1, vf.x); y0 = Math.min(y0, vf.y); y1 = Math.max(y1, vf.y); });
      return { w: Math.round((x1 - x0) / 2 * 100), h: Math.round((y1 - y0) / 2 * 100) };
    },
    debugLayout: () => { const sp = new THREE.Spherical().setFromVector3(camera.position.clone().sub(controls.target));
      return { phi: sp.phi, theta: sp.theta, themes: themes.map((T, i) => ({ label: T.grp.label, open: T.open, x: T.mesh.position.x, z: T.mesh.position.z,
        bx: T.base.x, bz: T.base.z, depth: T.depth, rs: themeR[i] + 10, rd: L.discR[i] + 9 })) }; },
    state: () => ({ open: openList.length, selected, hover: hoverNode, marked: markSet ? markSet.nodes.size : 0, selPair: !!selPair,
      pulsing: Object.values(members).filter(m => m.pulse0).length, labelsShown: lblItems.filter(i => i.l.material.opacity > 0.5).length,
      edges: edgeList.length, mode }),
    stats() {
      const q = arr => { const a = Array.from(arr.slice(0, ftn)).sort((x, y) => x - y);
        return { avg: a.reduce((s, x) => s + x, 0) / Math.max(1, a.length), p95: a[Math.floor(a.length * 0.95)] || 0, max: a[a.length - 1] || 0 }; };
      return { frames: ftn, js: q(ft), render: q(fr), objects: scene.children.length + edgeGroup.children.length };
    },
    resetStats() { fti = 0; ftn = 0; },
    destroy,
  };
}

window.Net3D = { mount, supported };
window.dispatchEvent(new Event("net3d-ready"));
