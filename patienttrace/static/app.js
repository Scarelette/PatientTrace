/* Patient_demo frontend: profile create/select -> long profile + network model. */
const App = (function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  function el(tag, attrs, kids) {
    const n = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      if (k === "class") n.className = attrs[k];
      else if (k === "html") n.innerHTML = attrs[k];
      else n.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(c => n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return n;
  }
  function toast(msg) {
    const t = el("div", { class: "toast" }, [msg]);
    document.body.appendChild(t); setTimeout(() => t.remove(), 2600);
  }

  const TYPES = ["Stimulus", "Emotion", "Cognition", "Behavior", "Physiological Sensation"];
  const TYPE_VAR = { "Stimulus": "--t-stimulus", "Emotion": "--t-emotion", "Cognition": "--t-cognition",
    "Behavior": "--t-behavior", "Physiological Sensation": "--t-physio" };
  const TYPE_SLUG = { "Stimulus": "stimulus", "Emotion": "emotion", "Cognition": "cognition",
    "Behavior": "behavior", "Physiological Sensation": "physio" };
  const slugOf = t => TYPE_SLUG[t] || "physio";
  function colorFor(type) {
    const v = TYPE_VAR[type] || "--muted";
    return getComputedStyle(document.documentElement).getPropertyValue(v).trim() || "#888";
  }

  const State = { screen: null, history: [], scrollY: {}, config: null, profile: null,
    existing: [], facets: {}, selectedIdx: null, job: null, poll: null,
    graphs: { short: null, long: null }, activeTab: "short", longProfile: null,
    stage: null, expanded: { short: new Set(), long: new Set() },
    member: { short: {}, long: {} }, expanding: false, pos: { short: {}, long: {} }, chat: null,
    patientId: null, network: null, patientTags: null };   // patientId set only for precomputed library patients (extension)

  // URL prefix when the app is served under a subpath (e.g. "/demo"); "" at root.
  const BASE = (window.DEMO_BASE || "");

  async function api(path, body, method) {
    const opt = { method: method || (body ? "POST" : "GET"), headers: { "Content-Type": "application/json" } };
    if (body) opt.body = JSON.stringify(body);
    const r = await fetch(BASE + path, opt);
    if (!r.ok) { let d; try { d = await r.json(); } catch (e) {} throw new Error((d && d.detail) || ("HTTP " + r.status)); }
    return r.json();
  }

  const PROGRESS = { choose: "", create: "Create profile", existing: "Choose profile", generate: "Network model", chat: "Chat with patient",
    contrast: "Contrast patients", dual: "Dual-patient view" };
  function show(name, noPush, keepScroll) {
    try { $("#g-tip").classList.remove("show"); } catch (e) {}
    if (State.screen) State.scrollY[State.screen] = window.scrollY;   // never leave a graph tooltip on screen
    if (name !== "generate") { try { WaitAnim.stop(); } catch (e) {} }
    if (!noPush && State.screen && State.screen !== name) {
      State.history.push(State.screen);
      if (State.history.length > 50) State.history.shift();
    }
    State.screen = name;
    if (window.Ext) { try { Ext.onShow(name); } catch (e) {} }
    $$(".screen").forEach(s => s.classList.remove("active"));
    const scr = $("#screen-" + name); if (scr) scr.classList.add("active");
    $("#progress").textContent = PROGRESS[name] || "";
    $("#back-btn").classList.toggle("hidden", State.history.length === 0);
    const y = keepScroll ? (State.scrollY[name] || 0) : 0;
    window.scrollTo({ top: y });
    if (y) requestAnimationFrame(() => window.scrollTo({ top: y }));   // after the page's layout settles
  }
  // Back = the page the user actually came from (history stack), at the scroll position they left it
  function back() { const p = State.history.pop(); if (p) show(p, true, true); $("#back-btn").classList.toggle("hidden", State.history.length === 0); }
  function toggleTheme() {
    let cur = document.documentElement.getAttribute("data-theme");
    if (cur !== "dark" && cur !== "light")      // "auto": follow what the user currently sees
      cur = (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("demoTheme", next); } catch (e) {}
  }
  function restart() { stopPoll(); State.history = []; show("choose", true); }

  // Packaged demo: flags.live is false when no GPU worker / API key is configured (browse mode);
  // flags.custom is false unless DEMO_FEAT_CUSTOM=1 (creating new profiles needs extra models).
  const LIVE_OFF_MSG = "Live mode is off: start the app with a GPU worker and an API key (see README). The recorded examples work without them.";
  function extFlags() { const x = State.config && State.config.ext; return (x && x.flags) || {}; }
  function liveOn() { return extFlags().live !== false; }
  function customOn() { return extFlags().custom !== false; }

  async function init() {
    try { State.config = await api("/api/config"); }
    catch (e) { toast("Failed to load config: " + e.message); return; }
    if (!customOn()) {
      const cc = document.querySelector('.choose-card[onclick="App.goCreate()"]'); if (cc) { cc.parentElement.classList.add("single"); cc.remove(); }
    }
    if (!liveOn()) {
      const n = document.getElementById("browse-note"); if (n) { n.textContent = "Browse mode: networks, contrast patients and recorded examples. Live interviews need a GPU worker and an API key (see README)."; n.classList.remove("hidden"); }
    }
    show("choose", true);
  }

  // ================= CREATE FLOW =================
  function ensureGpu() { api("/api/gpu/ensure", {}).catch(() => {}); }  // pre-warm a GPU

  function goCreate() {
    State.profile = State.config.empty_profile ? JSON.parse(JSON.stringify(State.config.empty_profile)) : {};
    renderForm(); $("#profile-review").classList.add("hidden"); show("create"); ensureGpu();
  }
  function renderForm() {
    const form = $("#profile-form"); form.innerHTML = "";
    State.config.profile_fields.forEach(field => {
      const row = el("div", { class: "form-row", id: "prof-field-" + field.key });
      if (field.type === "object") {
        const group = el("div", { class: "object-group" });
        group.appendChild(el("div", { class: "group-title" }, [field.label]));
        if (field.instruction) group.appendChild(el("div", { class: "field-instruction" }, [field.instruction]));
        (field.subfields || []).forEach(sf => {
          const sfw = el("div", { class: "subfield" });
          sfw.appendChild(el("label", {}, [sf.label]));
          const cur = (State.profile[field.key] || {})[sf.key] || "";
          if (sf.instruction) sfw.appendChild(el("div", { class: "field-instruction" }, [sf.instruction]));
          sfw.appendChild(el("input", { type: "text", id: "f-" + field.key + "-" + sf.key, value: cur }));
          group.appendChild(sfw);
        });
        row.appendChild(group);
      } else if (field.type === "list") {
        const labelRow = el("div", { class: "label-row" });
        labelRow.appendChild(el("label", {}, [field.label]));
        const aBtn = el("button", { class: "ghost assist-btn", type: "button" }, ["✨ Assist"]);
        aBtn.addEventListener("click", () => assistField(field, aBtn));
        labelRow.appendChild(aBtn); row.appendChild(labelRow);
        if (field.instruction) row.appendChild(el("div", { class: "field-instruction" }, [field.instruction]));
        const list = el("div", { class: "list-items", id: "list-" + field.key });
        const vals = State.profile[field.key] || [];
        (vals.length ? vals : [""]).forEach(v => list.appendChild(listItem(v)));
        row.appendChild(list);
        const add = el("button", { class: "ghost add-item", type: "button" }, ["+ Add"]);
        add.addEventListener("click", () => list.appendChild(listItem(""))); row.appendChild(add);
      } else {
        row.appendChild(el("label", {}, [field.label]));
        if (field.instruction) row.appendChild(el("div", { class: "field-instruction" }, [field.instruction]));
        row.appendChild(el("input", { type: "text", id: "f-" + field.key, value: State.profile[field.key] || "" }));
      }
      form.appendChild(row);
    });
  }
  function listItem(val) {
    const item = el("div", { class: "list-item" });
    const inp = el("input", { type: "text", value: val || "" });
    const rm = el("button", { class: "ghost remove", type: "button" }, ["×"]);
    rm.addEventListener("click", () => item.remove());
    item.appendChild(inp); item.appendChild(rm); return item;
  }
  function readForm() {
    const p = {};
    State.config.profile_fields.forEach(field => {
      if (field.type === "object") {
        const o = {}; (field.subfields || []).forEach(sf => { o[sf.key] = ($("#f-" + field.key + "-" + sf.key).value || "").trim(); }); p[field.key] = o;
      } else if (field.type === "list") {
        p[field.key] = $$("#list-" + field.key + " input").map(i => i.value.trim()).filter(Boolean);
      } else { p[field.key] = ($("#f-" + field.key) ? $("#f-" + field.key).value : "").trim(); }
    });
    return p;
  }
  async function assistField(field, btn) {
    const row = $("#prof-field-" + field.key);
    const old = row.querySelector(".assist-pop"); if (old) old.remove();
    const cur = $$("#list-" + field.key + " input").map(i => i.value.trim()).filter(Boolean);
    btn.disabled = true; btn.textContent = "…thinking";
    let res;
    try {
      res = (await api("/api/profile/assist", { field_key: field.key, field_label: field.label, field_type: field.type,
        instruction: field.instruction || "", current_value: cur, profile: readForm() })).assist;
    } catch (e) { res = { ok: false, error: e.message }; }
    btn.disabled = false; btn.textContent = "✨ Assist";
    renderAssistPop(row, field, res);
  }
  function renderAssistPop(row, field, res) {
    const pop = el("div", { class: "assist-pop" });
    const close = el("button", { class: "assist-close", type: "button" }, ["×"]);
    close.addEventListener("click", () => pop.remove()); pop.appendChild(close);
    if (!res || !res.ok) { pop.appendChild(el("div", { class: "assist-note" }, [(res && /unavailable right now/.test(res.error || "")) ? res.error : "Assist unavailable: " + ((res && res.error) || "unknown")])); row.appendChild(pop); return; }
    if (res.note) pop.appendChild(el("div", { class: "assist-note" }, [res.note]));
    if (res.mode === "prompts" && res.prompts && res.prompts.length) {
      const ul = el("ul", { class: "assist-prompts" }); res.prompts.forEach(p => ul.appendChild(el("li", {}, [p]))); pop.appendChild(ul);
    } else if (res.items && res.items.length) {
      const ul = el("ul", { class: "assist-items" }); res.items.forEach(it => ul.appendChild(el("li", {}, [it]))); pop.appendChild(ul);
      const apply = el("button", { class: "primary small", type: "button" }, ["Use these"]);
      apply.addEventListener("click", () => { const list = $("#list-" + field.key); list.innerHTML = ""; res.items.forEach(it => list.appendChild(listItem(it))); pop.remove(); });
      pop.appendChild(apply);
    } else if (res.text) { pop.appendChild(el("div", { class: "assist-text" }, [res.text])); }
    row.appendChild(pop);
  }
  async function reviewProfile() {
    const panel = $("#profile-review"), status = $("#profile-review-status"), body = $("#profile-review-body");
    $$(".field-suggestion").forEach(n => n.remove());
    panel.classList.remove("hidden"); status.textContent = "· reviewing…"; body.innerHTML = "";
    let review;
    try { review = (await api("/api/profile/review", { profile: readForm() })).review; }
    catch (e) { review = { ok: false, error: e.message }; }
    status.textContent = ""; renderReview(review);
  }
  function renderReview(review) {
    const body = $("#profile-review-body");
    if (!review || !review.ok) { body.appendChild(el("p", { class: "muted" }, [(review && /unavailable right now/.test(review.error || "")) ? review.error : "Couldn't run the review" + (review && review.error ? " (" + review.error + ")" : "") + "."])); return; }
    if (review.overall) body.appendChild(el("p", {}, [review.overall]));
    if (review.top_suggestions && review.top_suggestions.length) {
      body.appendChild(el("h4", {}, ["Top suggestions"]));
      const ul = el("ul", {}); review.top_suggestions.forEach(s => ul.appendChild(el("li", {}, [s]))); body.appendChild(ul);
    }
    (review.fields || []).forEach(f => {
      if (!f.suggestion) return;
      const row = $("#prof-field-" + f.field); if (!row) return;
      row.appendChild(el("div", { class: "field-suggestion q-" + (f.quality || "na") }, [
        el("span", { class: "review-badge q-" + (f.quality || "na") }, [f.quality || "note"]),
        el("span", { class: "fs-text" }, [f.suggestion])]));
    });
    $("#profile-review").scrollIntoView({ behavior: "smooth", block: "center" });
  }
  function generateFromForm() {
    const p = readForm();
    if (!p.identity || !p.identity.name) { toast("Please add at least an identity name."); return; }
    startJob(p);
  }

  // ================= EXISTING FLOW (with filters) =================
  async function goExisting() {
    show("existing"); ensureGpu();
    const picker = $("#profile-picker"); picker.innerHTML = "<div class='muted'>Loading…</div>";
    try { const r = await api("/api/existing_profiles"); State.existing = r.profiles; State.facets = r.facets || {}; }
    catch (e) { picker.innerHTML = ""; picker.appendChild(el("p", { class: "muted" }, ["Failed to load: " + e.message])); return; }
    fillSelect("#filter-gender", "All genders", State.facets.gender);
    fillSelect("#filter-age", "All ages", State.facets.age_bucket);
    fillSelect("#filter-culture", "All cultures", State.facets.culture);
    fillSelect("#filter-disorder", "All disorders", State.facets.disorder);
    State.selectedIdx = null; $("#existing-generate").disabled = true; $("#picker-hint").textContent = "No profile selected.";
    applyFilters();
  }
  function fillSelect(sel, allLabel, values) {
    const s = $(sel); s.innerHTML = "";
    s.appendChild(el("option", { value: "" }, [allLabel]));
    (values || []).forEach(v => s.appendChild(el("option", { value: v }, [String(v)])));
  }
  function applyFilters() {
    const q = ($("#filter-search").value || "").toLowerCase().trim();
    const g = $("#filter-gender").value, a = $("#filter-age").value, c = $("#filter-culture").value, d = $("#filter-disorder").value;
    const list = State.existing.filter(p => {
      if (g && p.gender !== g) return false;
      if (a && p.age_bucket !== a) return false;
      if (c && p.culture !== c) return false;
      if (d && !(p.disorders || []).includes(d)) return false;
      if (q) {
        const hay = (p.name + " " + p.title + " " + p.summary + " " + (p.diagnosis || []).join(" ")).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    renderPicker(list);
  }
  function renderPicker(list) {
    const picker = $("#profile-picker"); picker.innerHTML = "";
    $("#picker-count").textContent = list.length + " of " + State.existing.length + " profiles";
    list.slice(0, 200).forEach((p, i) => {
      const card = el("div", { class: "pick-card" }); card.style.animationDelay = (Math.min(i, 16) * 20) + "ms";
      card.appendChild(el("div", { class: "pick-head" }, [
        el("span", { class: "pick-name" }, [p.name || "(unnamed)"]),
        el("span", { class: "pick-meta" }, [[p.gender, p.age ? p.age + "y" : p.age_bucket, p.culture !== "Unspecified" ? p.culture : ""].filter(Boolean).join(" · ")])]));
      if (p.title) card.appendChild(el("div", { class: "pick-title" }, [p.title]));
      if (p.summary) card.appendChild(el("div", { class: "pick-summary" }, [p.summary]));
      const tags = el("div", { class: "pick-tags" });
      (p.disorders || []).forEach(dz => tags.appendChild(el("span", { class: "tag tag-dz" }, [dz])));
      card.appendChild(tags);
      card.addEventListener("click", () => {
        $$(".pick-card").forEach(c => c.classList.remove("selected")); card.classList.add("selected");
        State.selectedIdx = p.index; $("#existing-generate").disabled = false;
        $("#picker-hint").textContent = "Selected: " + (p.name || ("#" + p.index));
      });
      picker.appendChild(card);
    });
    if (!list.length) picker.appendChild(el("p", { class: "muted" }, ["No profiles match these filters."]));
  }
  // Extension flag: library patients load the precomputed long profile + long network.
  function usePrecomputed() { const x = State.config && State.config.ext; return !!(x && x.flags && x.flags.precomputed); }
  async function loadPrecomputed(idx) {
    stopPoll(); State.job = null;
    show("generate"); renderLegend();
    $("#graph-slot").innerHTML = ""; $("#lp-overlay").classList.add("hidden");
    $("#gen-spinner").style.display = ""; $("#gen-msg").textContent = "Loading precomputed profile…";
    let d;
    try { d = await api("/api/ext/patient/" + idx); }
    catch (e) { $("#gen-msg").textContent = "Failed to load: " + e.message; $("#gen-spinner").style.display = "none"; return; }
    State.profile = d.profile; State.longProfile = d.long_profile;
    State.graphs = { short: null, long: d.long_graph }; State.activeTab = "long";
    State.expanded = { short: new Set(), long: new Set() }; State.member = { short: {}, long: {} };
    State.expanding = false; State.pos = { short: {}, long: {} };
    State.patientId = d.patient_id; State.network = d.network; State.patientTags = d.tags;
    $("#tab-short").disabled = true; $("#tab-short").title = "Not precomputed for library patients";
    $("#tab-long").disabled = false; $("#tab-long-badge").textContent = "● precomputed";
    setStep("short", ""); setStep("long-profile", "done"); setStep("long", "done");
    renderLongProfile(d.long_profile); $("#lp-btn").classList.remove("hidden"); setChatBtn("ready");
    $("#gen-spinner").style.display = "none";
    $("#gen-msg").textContent = "Precomputed long profile + network";
    showGraph("long");
    if (window.Ext) { try { Ext.onPatientLoaded(); } catch (e) {} }
  }
  async function generateFromExisting() {
    if (State.selectedIdx == null) return;
    if (usePrecomputed()) return loadPrecomputed(State.selectedIdx);
    // jump to page 4 immediately, then fetch the profile
    show("generate"); renderLegend();
    $("#graph-slot").innerHTML = ""; $("#lp-overlay").classList.add("hidden");
    $("#graph-overlay").classList.remove("hidden");
    $("#overlay-title").textContent = "Loading profile…"; $("#overlay-text").innerHTML = "";
    $("#gen-spinner").style.display = ""; $("#gen-msg").textContent = "Loading…";
    let full;
    try { full = (await api("/api/existing_profiles/" + State.selectedIdx)).profile; }
    catch (e) { $("#gen-msg").textContent = "Failed to load profile: " + e.message; $("#gen-spinner").style.display = "none"; return; }
    startJob(full);
  }

  // ================= GENERATE + RESULTS (page 4) =================
  function setStep(id, s) { const e = $("#step-" + id); if (!e) return; e.classList.remove("active", "done"); if (s) e.classList.add(s); }
  function stopPoll() { if (State.poll) { clearInterval(State.poll); State.poll = null; } }
  // The actor role-plays stage-1's expanded profile, so chat waits for it. "fallback"
  // = stage 1 failed: chat stays possible, on the profile as authored.
  function setChatBtn(mode) {
    const b = $("#chat-btn"); if (!b) return;
    if (!liveOn()) { b.disabled = true; b.textContent = "💬 Chat (live mode off)"; b.title = LIVE_OFF_MSG; return; }
    b.disabled = mode === "waiting";
    b.textContent = { waiting: "💬 Chat (expanding profile…)", ready: "💬 Chat with patient",
                      fallback: "💬 Chat (authored profile)" }[mode];
    b.title = mode === "fallback" ? "Stage 1 failed, so the patient uses the profile as written" : "";
  }

  async function startJob(profile) {
    State.profile = profile; State.graphs = { short: null, long: null }; State.activeTab = "short"; State.longProfile = null;
    State.expanded = { short: new Set(), long: new Set() };
    State.member = { short: {}, long: {} }; State.expanding = false;
    State.pos = { short: {}, long: {} };
    State.patientId = null; State.network = null; State.patientTags = null;
    $("#tab-short").disabled = false; $("#tab-short").title = "";
    show("generate");
    if (window.Ext) { try { Ext.onPatientLoaded(); } catch (e) {} }
    renderLegend();
    $("#graph-slot").innerHTML = "";
    $("#gen-spinner").style.display = ""; $("#gen-msg").textContent = "Starting…";
    $("#tab-short").classList.add("active"); $("#tab-long").classList.remove("active");
    $("#tab-long").disabled = true; $("#tab-long-badge").textContent = ""; $("#lp-btn").classList.add("hidden");
    $("#lp-overlay").classList.add("hidden");
    setStep("short", "active"); setStep("long-profile", "active"); setStep("long", "");
    setChatBtn("waiting");
    updateOverlay();
    let job;
    try { job = await api("/api/generate", { profile }); }
    catch (e) { $("#gen-msg").textContent = "Failed to start: " + e.message; $("#gen-spinner").style.display = "none"; setChatBtn("fallback"); return; }
    State.job = job.job_id; stopPoll(); State.poll = setInterval(pollJob, 2200); pollJob();
  }

  const OVERLAY = {
    short: "Reading the profile and extracting <b>nodes</b> — symptoms, emotions, thoughts, behaviours and triggers — then drawing the <b>cause → effect</b> links between them.",
    gpu: "Allocating a <b>GPU</b> on the cluster for this session (a 2-hour job; the first request can take a few minutes to schedule + load the model). It’s reused for later profiles and auto-released after 20 min idle.",
    stage1: "Expanding the short profile into a richer <b>long profile</b> on the GPU (symptom detail, cognitions, coping, risk factors).",
    long: "Building the fuller <b>long-profile network</b> from the expanded profile with a stronger model, then verifying each causal link.",
  };
  // ---- fun "network forming" animation + rotating tips while waiting ----
  const TIPS = [
    "Reading the profile for symptoms, emotions, thoughts, behaviours & triggers…",
    "Each node is one clinical construct — coloured by its type.",
    "Drawing the cause → effect links the profile actually supports…",
    "Every edge is verified before it's kept.",
    "Related nodes get grouped into themes you can click to expand.",
    "Tip: hover a node or edge for details; drag to rearrange.",
    "Mutual-reinforcement loops (A ⇄ B) are kept on purpose.",
  ];
  const WaitAnim = (function () {
    let canvas, ctx, raf, nodes, running = false, W, H, tipTimer, tipI = 0;
    function start() {
      if (running) return;
      canvas = $("#wait-canvas"); if (!canvas) return;
      const r = canvas.getBoundingClientRect();
      W = canvas.width = Math.max(2, r.width); H = canvas.height = Math.max(2, r.height);
      ctx = canvas.getContext("2d");
      const cols = TYPES.map(colorFor);
      nodes = [];
      for (let i = 0; i < 14; i++) nodes.push({ x: Math.random() * W, y: Math.random() * H, vx: (Math.random() - .5) * .6, vy: (Math.random() - .5) * .6, c: cols[i % cols.length], r: 5 + Math.random() * 6 });
      running = true; loop();
      const tip = $("#wait-tip");
      if (tip) { tipI = 0; tip.textContent = TIPS[0]; tip.style.opacity = 1;
        tipTimer = setInterval(() => { tipI = (tipI + 1) % TIPS.length; tip.style.opacity = 0; setTimeout(() => { tip.textContent = TIPS[tipI]; tip.style.opacity = 1; }, 260); }, 3500); }
    }
    function loop() {
      if (!running || !ctx) return;
      ctx.clearRect(0, 0, W, H);
      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j], dx = a.x - b.x, dy = a.y - b.y, d = Math.hypot(dx, dy);
        if (d < 150) { ctx.globalAlpha = (1 - d / 150) * .45; ctx.strokeStyle = a.c; ctx.lineWidth = 1.3; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
      }
      ctx.globalAlpha = 1;
      for (const n of nodes) {
        n.x += n.vx; n.y += n.vy;
        if (n.x < n.r || n.x > W - n.r) n.vx *= -1; if (n.y < n.r || n.y > H - n.r) n.vy *= -1;
        ctx.fillStyle = n.c; ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2); ctx.fill();
      }
      raf = requestAnimationFrame(loop);
    }
    function stop() { running = false; if (raf) cancelAnimationFrame(raf); if (tipTimer) clearInterval(tipTimer); if (ctx && canvas) ctx.clearRect(0, 0, canvas.width, canvas.height); }
    return { start, stop };
  })();

  function updateOverlay() {
    const ov = $("#graph-overlay"); const tab = State.activeTab;
    const ready = State.graphs[tab];
    if (ready) { ov.classList.add("hidden"); WaitAnim.stop(); return; }
    ov.classList.remove("hidden"); WaitAnim.start();
    if (tab === "short") {
      $("#overlay-title").textContent = "Building the short-profile network…";
      $("#overlay-text").innerHTML = OVERLAY.short;
    } else if (State.longProfile) {
      $("#overlay-title").textContent = "Building the long-profile network…";
      $("#overlay-text").innerHTML = OVERLAY.long;
    } else if (State.stage === "allocating_gpu") {
      $("#overlay-title").textContent = "Allocating a GPU…";
      $("#overlay-text").innerHTML = OVERLAY.gpu;
    } else {
      $("#overlay-title").textContent = "Expanding the profile…";
      $("#overlay-text").innerHTML = OVERLAY.stage1;
    }
  }

  async function pollJob() {
    if (!State.job) return;
    let j; try { j = await api("/api/job/" + State.job); } catch (e) { return; }

    if (j.short_graph && !State.graphs.short) {
      State.graphs.short = j.short_graph; setStep("short", "done");
      if (State.activeTab === "short") renderActive();
    }
    if (j.long_profile && !State.longProfile) {
      State.longProfile = j.long_profile; setStep("long-profile", "done"); setStep("long", "active");
      renderLongProfile(j.long_profile); $("#lp-btn").classList.remove("hidden"); setChatBtn("ready");
      if (State.activeTab === "long") updateOverlay();
    }
    if (j.long_graph && !State.graphs.long) {
      State.graphs.long = j.long_graph; setStep("long", "done");
      $("#tab-long").disabled = false; $("#tab-long-badge").textContent = "● ready";
      if (State.activeTab === "long") renderActive();
    }

    // long-graph tab shows a live "generating…" hint until it's ready
    if (!State.graphs.long && j.status === "running") $("#tab-long-badge").textContent = "· generating…";

    State.stage = j.stage;
    if ((j.status === "done" || j.status === "error") && !State.longProfile) setChatBtn("fallback");
    const t = j.timings || {};
    if (j.status === "done") {
      stopPoll(); $("#gen-spinner").style.display = "none";
      $("#gen-msg").textContent = "Done in " + (t.total != null ? t.total + "s" : "—");
    } else if (j.status === "error") {
      stopPoll(); $("#gen-spinner").style.display = "none"; $("#gen-msg").textContent = j.error || "Error";
    } else {
      const m = { starting: "Starting…", allocating_gpu: "Allocating a GPU…", stage1: "Expanding long profile (GPU)…", long_graph: "Building long-profile network…", done: "Finishing…" };
      $("#gen-msg").textContent = m[j.stage] || "Working…";
    }
    updateOverlay();
  }

  function showGraph(tab) {
    State.activeTab = tab;
    $("#tab-short").classList.toggle("active", tab === "short");
    $("#tab-long").classList.toggle("active", tab === "long");
    if (State.graphs[tab]) renderActive(); else { $("#graph-slot").innerHTML = ""; }
    updateOverlay();
  }
  function layered(g) { return !!(g && g.groups && g.groups.length && g.edges && g.nodes.length > g.groups.length); }
  // themes with a single member are never collapsed (pointless wrapper)
  function realGroups(g) { return g.groups.map((grp, gi) => ({ grp, gi })).filter(x => x.grp.members.length > 1); }

  function renderActive() {
    const g = State.graphs[State.activeTab]; if (!g) return;
    const slot = $("#graph-slot"); slot.innerHTML = "";
    $("#graph-overlay").classList.add("hidden");
    const isExt = !!(State.config && State.config.ext);
    if (isExt && State.activeTab === "short" && State.graphs.long && (State.graphs.long.nodes || []).length && !State.graphs.long.error) {
      State.activeTab = "long"; return renderActive();   // the long-profile network is the one the simulator and the Interview use
    }
    State.isoCount = 0;
    if (!isExt) renderLayerCtl();
    const empty = !(g.nodes && g.nodes.length);
    if (g.error || empty) {
      const msg = (g.error === "no nodes" || empty)
        ? "No network could be built — this profile has no symptoms, emotions, thoughts, behaviours or triggers to connect. Add some detail and try again."
        : "Graph failed: " + g.error;
      slot.appendChild(el("div", { class: "graph-overlay" }, [
        el("div", { class: "overlay-title" }, ["Nothing to show"]),
        el("div", { class: "overlay-text" }, [msg])]));
      return;
    }
    let view = layered(g) ? buildView(g, State.activeTab) : g;
    let extOpts = {};
    if (isExt) {   // extension only: unconnected nodes hidden behind "Show unconnected nodes"
      // count = real nodes with no edge in the whole network (stable whatever is expanded)
      const deg = new Set(); (g.edges || []).forEach(e => { deg.add(e.from); deg.add(e.to); });
      const isoNames = new Set((g.nodes || []).filter(n => !deg.has(n.name)).map(n => n.name));
      State.isoCount = isoNames.size;
      if (isoNames.size && !State.showIso) {
        const members = gi => ((g.groups || [])[gi] || {}).members || [];
        const hide = n => n.group ? members(n.gidx).every(m => isoNames.has(m)) : isoNames.has(n.name);
        view = { nodes: view.nodes.filter(n => !hide(n)), edges: view.edges };
      }
      extOpts = { fit: true, shortLabel: 22, flipLabels: true, sentence: true };
      renderLayerCtl();
    }
    new ForceGraph(slot, view, Object.assign({ onExpand, onCollapse, positions: State.pos[State.activeTab] }, extOpts)).render();
  }

  // ---- coherent client-side layered view: themes aggregate the ONE edge set ----
  function buildView(g, kind) {
    const groups = g.groups, expanded = State.expanded[kind];
    const name2type = {}; g.nodes.forEach(n => { name2type[n.name] = n.type; });
    const name2gi = {}; groups.forEach((grp, gi) => grp.members.forEach(nm => { name2gi[nm] = gi; }));
    const single = gi => groups[gi].members.length <= 1;           // (1) singletons stay individual
    const open = gi => expanded.has(gi) || single(gi);
    const vid = nm => { const gi = name2gi[nm]; if (gi === undefined) return "n:" + nm; return open(gi) ? "n:" + nm : "g:" + gi; };

    const nodes = [], seen = new Set();
    groups.forEach((grp, gi) => {
      if (open(gi)) {
        grp.members.forEach(nm => { const id = "n:" + nm; if (!seen.has(id)) { seen.add(id); nodes.push({ id, name: nm, type: name2type[nm] || grp.type, gidx: single(gi) ? undefined : gi, member: !single(gi) }); } });
      } else {
        nodes.push({ id: "g:" + gi, name: grp.label, type: grp.type, group: true, gidx: gi, count: grp.members.length });
      }
    });
    g.nodes.forEach(n => { if (name2gi[n.name] === undefined) { const id = "n:" + n.name; if (!seen.has(id)) { seen.add(id); nodes.push({ id, name: n.name, type: n.type }); } } });

    // spawn hints so expand/collapse stays local
    const pos = State.pos[kind] || {};
    nodes.forEach(n => {
      if (pos[n.id]) return;
      if (n.member && pos["g:" + n.gidx]) { n.spawn = pos["g:" + n.gidx]; return; }
      if (n.group) { const pts = groups[n.gidx].members.map(m => pos["n:" + m]).filter(Boolean);
        if (pts.length) n.spawn = { x: pts.reduce((s, p) => s + p.x, 0) / pts.length, y: pts.reduce((s, p) => s + p.y, 0) / pts.length }; }
    });

    // (3) coherence: map EVERY real edge to its visible endpoints and aggregate.
    // An edge into/out of a collapsed theme stays an edge into/out of that member
    // once expanded — same underlying edges, always consistent.
    const emap = {};
    g.edges.forEach(e => {
      const a = vid(e.from), b = vid(e.to); if (a === b) return;
      const k = a + "|" + b;
      if (!emap[k]) emap[k] = { from: a, to: b, fromName: e.from, toName: e.to, fromType: e.from_type, toType: e.to_type, evidence: e.evidence || "", weight: 0 };
      emap[k].weight++;
    });
    return { nodes, edges: Object.values(emap) };
  }

  function onExpand(node) {       // click a theme -> expand it
    if (!node.group) return;
    State.expanded[State.activeTab].add(node.gidx); renderActive();
  }
  function onCollapse(node) {     // double-click a member -> collapse its theme
    if (node.member && node.gidx != null) { State.expanded[State.activeTab].delete(node.gidx); renderActive(); }
  }

  function renderLayerCtl() {
    const g = State.graphs[State.activeTab];
    const ctl = $("#layer-ctl");
    const isoBtn = () => {          // extension only (State.isoCount is 0 otherwise)
      if (!State.isoCount) return null;
      const b = el("button", { class: "ghost small", type: "button" }, [(State.showIso ? "Hide" : "Show") + " unconnected nodes (" + State.isoCount + ")"]);
      b.addEventListener("click", () => { State.showIso = !State.showIso; renderActive(); });
      return b;
    };
    if (!layered(g)) {
      const b = isoBtn();
      if (!b) { ctl.classList.add("hidden"); ctl.innerHTML = ""; return; }
      ctl.classList.remove("hidden"); ctl.innerHTML = ""; ctl.appendChild(b); return;
    }
    ctl.classList.remove("hidden"); ctl.innerHTML = "";
    const kind = State.activeTab, set = State.expanded[kind];
    const hint = el("span", { class: "muted small" }, [
      set.size ? "Click a theme to expand · a node to collapse it" : "Click a theme to expand it into its nodes"]);
    const overview = el("button", { class: "ghost small", type: "button" }, ["Overview"]);
    overview.addEventListener("click", () => { set.clear(); renderActive(); });
    const all = el("button", { class: "ghost small", type: "button" }, ["Expand all"]);
    all.addEventListener("click", () => { realGroups(g).forEach(x => set.add(x.gi)); renderActive(); });
    ctl.appendChild(hint); ctl.appendChild(overview); ctl.appendChild(all);
    const ib = isoBtn(); if (ib) ctl.appendChild(ib);
  }

  function renderLegend() {
    const lg = $("#legend"); lg.innerHTML = "";
    TYPES.forEach(t => lg.appendChild(el("span", { class: "lg" }, [el("span", { class: "sw", style: "background:" + colorFor(t) }), t])));
  }
  function toggleLongProfile() { $("#lp-overlay").classList.toggle("hidden"); }
  function renderLongProfile(lp) {
    const box = $("#long-profile-view"); box.innerHTML = "";
    const order = ["presenting_problems", "symptom_details", "emotional_profile", "cognitive_patterns",
      "behavior_patterns", "triggers", "risk_and_protective_factors", "background", "brief_summary"];
    const addList = (title, arr) => { if (!arr || !arr.length) return; box.appendChild(el("h4", {}, [title])); const ul = el("ul", {}); arr.forEach(x => ul.appendChild(el("li", {}, [String(x)]))); box.appendChild(ul); };
    order.forEach(k => {
      const v = lp[k];
      if (Array.isArray(v)) addList(k.replace(/_/g, " "), v);
      else if (v && typeof v === "object") Object.keys(v).forEach(sk => { if (Array.isArray(v[sk]) && v[sk].length) addList((k + " · " + sk).replace(/_/g, " "), v[sk]); });
      else if (typeof v === "string" && v.trim()) { box.appendChild(el("h4", {}, [k.replace(/_/g, " ")])); box.appendChild(el("p", {}, [v])); }
    });
  }

  // ================= CHAT (angel stage-2 roleplay) =================
  async function goChat() {
    if (!liveOn()) { toast(LIVE_OFF_MSG); return; }
    if (!State.profile) { toast("Generate or pick a profile first."); return; }
    show("chat");
    $("#chat-log").innerHTML = ""; $("#chat-text").value = "";
    $("#chat-title").textContent = "Chat with the patient";
    let r;
    // Send stage-1's expanded profile so the actor role-plays the observer's output.
    // The button stays disabled until it arrives; it is null only if stage 1 failed,
    // and the server then falls back to State.profile.
    try { r = await api("/api/chat/start", { profile: State.profile, long_profile: State.longProfile }); }
    catch (e) { toast("Chat start failed: " + e.message); return; }
    State.chat = { sessionId: r.session_id, sending: false, systemPrompt: r.system_prompt || "",
      voice: State.chat && State.chat.voice || false, recording: false };
    $("#chat-title").textContent = "Chat with " + (r.patient_name || "the patient") +
      (r.profile_source === "long" ? " · expanded profile" : " · authored profile");
    $("#chat-log").appendChild(el("div", { class: "chat-empty" }, ["Start the conversation — type your first message below."]));
    if (window.Ext) { try { Ext.onChatStart(r); } catch (e) { console.error(e); } }
    // Audio controls only if the server has speech configured.
    const hasAudio = !!(State.config && State.config.audio);
    $("#chat-mic").classList.toggle("hidden", !hasAudio);
    $("#voice-toggle").classList.toggle("hidden", !hasAudio);
    updateVoiceBtn();
    const t = $("#chat-text");
    t.onkeydown = ev => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendChat(); } };
    t.focus();
  }
  function addBubble(role, text) {
    const log = $("#chat-log");
    const kids = [el("span", { class: "who" }, [role === "therapist" ? "You (therapist)" : "Patient"]), el("span", { class: "btext" }, [text])];
    if (role === "patient" && State.config && State.config.audio) {
      const play = el("button", { class: "bubble-play", title: "Play voice", type: "button" }, ["🔊"]);
      play.onclick = () => toggleSpeak(text, play);
      kids.push(play);
    }
    const b = el("div", { class: "bubble " + role + " bubble-enter" }, kids);
    log.appendChild(b); log.scrollTop = log.scrollHeight; return b;
  }
  function addTyping() {
    const log = $("#chat-log");
    const b = el("div", { class: "bubble patient typing" }, [
      el("span", { class: "who" }, ["Patient"]),
      el("span", { class: "dots" }, [el("i", {}, []), el("i", {}, []), el("i", {}, [])])]);
    log.appendChild(b); log.scrollTop = log.scrollHeight; return b;
  }

  async function sendChat() {
    const t = $("#chat-text"); const msg = (t.value || "").trim();
    if (!msg || !State.chat || State.chat.sending) return;
    const hint = $("#chat-log .chat-empty"); if (hint) hint.remove();
    t.value = ""; addBubble("therapist", msg);
    State.chat.sending = true; $("#chat-send").disabled = true;
    const typing = addTyping();
    try {
      const r = await api("/api/chat/send", { session_id: State.chat.sessionId, message: msg });
      typing.remove();
      const reply = r.reply || "…";
      const bubble = addBubble("patient", reply);
      if (window.Ext && r.reply) { try { Ext.onPatientReply(bubble, reply, State.chat.sessionId); } catch (e) { console.error(e); } }
      if (State.chat.voice) speak(reply, bubble.querySelector(".bubble-play"));
    } catch (e) {
      typing.remove(); addBubble("patient", "(the patient model is warming up or unavailable — try again in a moment: " + e.message + ")");
    }
    State.chat.sending = false; $("#chat-send").disabled = false; t.focus();
  }

  // ================= AUDIO (mic input + spoken replies) =================
  function updateVoiceBtn() {
    const b = $("#voice-toggle"); if (!b || !State.chat) return;
    b.textContent = State.chat.voice ? "🔊 Voice on" : "🔇 Voice off";
    b.title = State.chat.voice
      ? "Hands-free: the patient speaks replies aloud, and your mic auto-sends"
      : "Speak the patient's replies aloud";
    b.classList.toggle("on", State.chat.voice);
    const mic = $("#chat-mic");
    if (mic) mic.title = State.chat.voice
      ? "Talk to the patient — click, speak, click to send automatically"
      : "Click to record; the text goes in the box for you to review, then Send";
  }
  function toggleVoice() {
    if (!State.chat) return;
    State.chat.voice = !State.chat.voice;
    updateVoiceBtn();
    if (!State.chat.voice && State.audio) { State.audio.pause(); }
  }

  function setPlayIcon(btn, playing) {
    if (!btn) return;
    btn.textContent = playing ? "⏹️" : "🔊";
    btn.title = playing ? "Stop voice" : "Play voice";
  }

  // Toggle a patient bubble's voice: first click plays it aloud, a second click
  // (while it's loading or playing) stops it. Clicking a different bubble stops
  // the current one and plays the new one.
  function toggleSpeak(text, btn) {
    const active = State.audioBtn === btn && State.audio &&
      (!State.audio.paused ||
       (btn && (btn.classList.contains("loading") || btn.classList.contains("playing"))));
    if (active) {
      try { State.audio.pause(); } catch (e) {}
      if (btn) { btn.classList.remove("loading", "playing"); setPlayIcon(btn, false); }
      return;
    }
    speak(text, btn);
  }

  function speak(text, btn) {
    if (!text || !State.chat) return;
    if (State.audio) { try { State.audio.pause(); } catch (e) {} }
    const clear = () => { if (btn) { btn.classList.remove("loading", "playing"); setPlayIcon(btn, false); } };
    if (btn) btn.classList.remove("blocked");
    // GET so the <audio> element streams and starts playing as bytes arrive.
    const url = BASE + "/api/chat/tts?session_id=" + encodeURIComponent(State.chat.sessionId) +
                "&text=" + encodeURIComponent(text);
    const a = new Audio();
    State.audio = a;
    State.audioBtn = btn || null;
    if (btn) btn.classList.add("loading");
    a.onplaying = () => { if (btn) { btn.classList.remove("loading"); btn.classList.add("playing"); setPlayIcon(btn, true); } };
    a.onwaiting = () => { if (btn) btn.classList.add("loading"); };
    a.onended = clear;
    a.onpause = () => { if (btn) { btn.classList.remove("playing"); setPlayIcon(btn, false); } };
    a.onerror = () => { clear(); toast("Could not play the patient's voice."); };
    a.src = url;
    a.play().catch(() => { clear(); if (btn) btn.classList.add("blocked"); });
  }

  // Pick a recording format the browser supports AND the API can read; keep the
  // matching file extension so the server/OpenAI parses it correctly.
  function pickRecorderType() {
    const cands = [
      ["audio/webm;codecs=opus", "webm"], ["audio/webm", "webm"],
      ["audio/ogg;codecs=opus", "ogg"], ["audio/ogg", "ogg"],
      ["audio/mp4", "mp4"], ["audio/mpeg", "mp3"],
    ];
    const sup = window.MediaRecorder && MediaRecorder.isTypeSupported;
    for (const [mime, ext] of cands) {
      if (!sup || MediaRecorder.isTypeSupported(mime)) return { mime: sup ? mime : "", ext };
    }
    return { mime: "", ext: "webm" };
  }

  async function toggleMic() {
    if (!State.chat) return;
    if (State.chat.recording) { stopMic(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      toast("Microphone not supported in this browser."); return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e) { toast("Microphone access denied — allow the mic for this site."); return; }

    const fmt = pickRecorderType();
    let rec;
    try { rec = fmt.mime ? new MediaRecorder(stream, { mimeType: fmt.mime }) : new MediaRecorder(stream); }
    catch (e) { rec = new MediaRecorder(stream); }
    const chunks = [];
    const startedAt = Date.now();
    rec.ondataavailable = ev => { if (ev.data && ev.data.size) chunks.push(ev.data); };
    rec.onstop = async () => {
      stream.getTracks().forEach(tr => tr.stop());
      const mic = $("#chat-mic"); mic.classList.remove("recording"); mic.textContent = "🎙️";
      State.chat.recording = false;
      const dur = Date.now() - startedAt;
      const blob = new Blob(chunks, { type: (rec.mimeType || fmt.mime || "audio/webm").split(";")[0] });
      if (!blob.size || dur < 400) { toast("Recording too short — hold the mic a bit longer and speak clearly."); return; }
      const t = $("#chat-text"); const prev = t.value;
      t.value = "Transcribing…"; t.disabled = true;
      try {
        const fd = new FormData();
        fd.append("audio", blob, "speech." + fmt.ext);
        const r = await fetch(BASE + "/api/chat/transcribe", { method: "POST", body: fd });
        let d = {}; try { d = await r.json(); } catch (e) {}
        t.disabled = false;
        if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
        const text = (d.transcript || "").trim();
        if (!text) { t.value = prev; toast("Didn't catch that — speak a bit louder / closer to the mic."); t.focus(); return; }
        t.value = (prev ? prev + " " : "") + text;
        t.focus();
        if (State.chat.voice) sendChat();   // hands-free: voice in -> patient voice out
      } catch (e) {
        t.disabled = false; t.value = prev; toast("Transcription failed: " + e.message);
      }
    };
    State.chat.rec = rec; State.chat.recording = true;
    const mic = $("#chat-mic"); mic.classList.add("recording"); mic.textContent = "⏹";
    rec.start(200);   // timeslice so data flushes even on quick stops
  }
  function stopMic() {
    if (State.chat && State.chat.rec && State.chat.recording) State.chat.rec.stop();
  }

  // sentence case for display ("headaches" -> "Headaches", "Muscle Tension" -> "Muscle tension"); extension only
  function sentenceLabel(label) {
    let s = String(label || "").trim(); if (!s) return s;
    const w = s.split(" ");
    if (w.length > 1) {
      const rest = w.slice(1), caps = rest.filter(x => /^[A-Z][a-z]/.test(x)).length;
      if (caps / rest.length >= 0.5) s = w.map((x, i) => (i > 0 && /^[A-Z][a-z'’-]*[,.;:)]?$/.test(x) && !/^I(['’][a-z]+)?$/.test(x)) ? x.toLowerCase() : x).join(" ");
    }
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  // ================= FORCE GRAPH (simple: nodes + straight directed edges) =================
  function ForceGraph(container, graph, opts) {
    opts = opts || {};
    const NS = "http://www.w3.org/2000/svg";
    const nodesIn = graph.nodes || [], edgesIn = graph.edges || [];
    const byKey = {};
    const nodes = nodesIn.map((n) => {
      const o = { key: (n.id != null ? n.id : n.name), name: n.name, type: n.type, deg: 0,
        group: !!n.group, gidx: n.gidx, member: !!n.member, count: n.count || 0 };
      byKey[o.key] = o; return o;
    });
    const edges = [];
    edgesIn.forEach(e => {
      const a = byKey[e.from], b = byKey[e.to]; if (!a || !b) return;
      a.deg++; b.deg++;
      edges.push({ s: a, t: b, id: e.id, evidence: e.evidence || "",
        fromName: e.fromName || a.name, toName: e.toName || b.name,
        fromType: e.fromType || e.from_type || a.type, toType: e.toType || e.to_type || b.type,
        weight: e.weight || 1 });
    });
    const pset = {}; edges.forEach(e => pset[e.s.key + ">" + e.t.key] = true);
    edges.forEach(e => { e.recip = !!pset[e.t.key + ">" + e.s.key]; });

    const W = Math.max(320, container.clientWidth || 900);
    const H = Math.max(320, container.clientHeight || 560);
    const cx = W / 2, cy = H / 2;
    const K = Math.max(90, 0.80 * Math.sqrt((W * H) / Math.max(1, nodes.length)));
    const P = opts.positions || {};   // persistent id -> {x,y}
    let known = 0;
    nodes.forEach((n, i) => {
      if (P[n.key]) { n.x = P[n.key].x; n.y = P[n.key].y; n.pinned = true; known++; }  // already placed -> stays still
      else if (n.spawn) { n.x = n.spawn.x + (Math.random() * 40 - 20); n.y = n.spawn.y + (Math.random() * 40 - 20); }
      else { const a = 2 * Math.PI * i / nodes.length; n.x = cx + Math.cos(a) * Math.min(W, H) * 0.4; n.y = cy + Math.sin(a) * Math.min(W, H) * 0.4; }
      n.vx = 0; n.vy = 0;
    });
    // gentle settle when most nodes are already placed (avoids a big shake on expand)
    const startAlpha = (known >= 2 && known / nodes.length > 0.5) ? 0.32 : 1;

    let svg, gE, gN, tip = $("#g-tip"), raf = null, alpha = 1;
    let vb = { x: 0, y: 0, w: W, h: H };   // viewBox (zoom/pan)
    function setVB() { svg.setAttribute("viewBox", vb.x.toFixed(1) + " " + vb.y.toFixed(1) + " " + vb.w.toFixed(1) + " " + vb.h.toFixed(1)); }
    function svgPoint(ev) { const r = svg.getBoundingClientRect(); return { x: vb.x + (ev.clientX - r.left) / r.width * vb.w, y: vb.y + (ev.clientY - r.top) / r.height * vb.h }; }
    function zoomAt(p, f) { const nw = Math.min(W * 1.8, Math.max(W * 0.22, vb.w * f)); const s = nw / vb.w; vb.w = nw; vb.h = vb.h * s; vb.x = p.x - (p.x - vb.x) * s; vb.y = p.y - (p.y - vb.y) * s; setVB(); }
    function resetView() { vb = { x: 0, y: 0, w: W, h: H }; setVB(); }
    const rad = n => n.group ? 16 + Math.min(24, (n.count || 1) * 1.2) : 9 + Math.min(14, n.deg * 2);
    const esc = s => String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

    function tick() {
      const k = alpha;
      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j]; let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy || 0.01;
        const rep = (K * K) / d2, d = Math.sqrt(d2), fx = dx / d * rep, fy = dy / d * rep;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      }
      edges.forEach(e => { let dx = e.t.x - e.s.x, dy = e.t.y - e.s.y, d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const f = (d - K) * 0.03, fx = dx / d * f, fy = dy / d * f; e.s.vx += fx; e.s.vy += fy; e.t.vx -= fx; e.t.vy -= fy; });
      nodes.forEach(n => {
        if (n.pinned && !n.fixed) { n.vx = 0; n.vy = 0; return; }   // placed nodes stay put on expand/collapse
        n.vx += (cx - n.x) * 0.005; n.vy += (cy - n.y) * 0.005;
        if (!n.fixed) { n.x += n.vx * k; n.y += n.vy * k; } n.vx *= 0.86; n.vy *= 0.86;
        n.x = Math.max(rad(n) + 4, Math.min(W - rad(n) - 4, n.x)); n.y = Math.max(rad(n) + 4, Math.min(H - rad(n) - 4, n.y));
      });
      draw(); alpha *= 0.985; if (alpha > 0.05) raf = requestAnimationFrame(tick); else { alpha = 0; settled(); }
    }
    function geom(e) {
      let dx = e.t.x - e.s.x, dy = e.t.y - e.s.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      const ux = dx / d, uy = dy / d;
      // straight; reciprocal pairs get a tiny offset so both are visible
      const off = e.recip ? 7 * (e.s.key < e.t.key ? 1 : -1) : 0;
      const nx = -uy * off, ny = ux * off;
      const sx = e.s.x + ux * rad(e.s) + nx, sy = e.s.y + uy * rad(e.s) + ny;
      const ex = e.t.x - ux * (rad(e.t) + 6) + nx, ey = e.t.y - uy * (rad(e.t) + 6) + ny;
      return [sx, sy, ex, ey];
    }
    function draw() {
      edges.forEach(e => { const [sx, sy, ex, ey] = geom(e); e._l.setAttribute("x1", sx.toFixed(1)); e._l.setAttribute("y1", sy.toFixed(1)); e._l.setAttribute("x2", ex.toFixed(1)); e._l.setAttribute("y2", ey.toFixed(1)); });
      nodes.forEach(n => { n._g.setAttribute("transform", "translate(" + n.x.toFixed(1) + "," + n.y.toFixed(1) + ")"); P[n.key] = { x: n.x, y: n.y }; });
    }
    function render() {
      svg = document.createElementNS(NS, "svg"); svg.setAttribute("class", "graph-svg"); svg.setAttribute("viewBox", "0 0 " + W + " " + H);
      const defs = document.createElementNS(NS, "defs");
      // one arrowhead marker per node type, colored to match the edge (its cause)
      TYPES.forEach(t => {
        const sl = slugOf(t), col = colorFor(t);
        const mk = document.createElementNS(NS, "marker");
        mk.setAttribute("id", "arw-" + sl); mk.setAttribute("viewBox", "0 0 10 10");
        mk.setAttribute("refX", "8.5"); mk.setAttribute("refY", "5");
        mk.setAttribute("markerWidth", "6"); mk.setAttribute("markerHeight", "6"); mk.setAttribute("orient", "auto-start-reverse");
        const ap = document.createElementNS(NS, "path"); ap.setAttribute("d", "M0,1.5 L9,5 L0,8.5 Z"); ap.setAttribute("fill", col);
        mk.appendChild(ap); defs.appendChild(mk);
      });
      svg.appendChild(defs);
      gE = document.createElementNS(NS, "g"); gN = document.createElementNS(NS, "g"); svg.appendChild(gE); svg.appendChild(gN);

      edges.forEach(e => {
        const sl = slugOf(e.s.type);
        const l = document.createElementNS(NS, "line");
        l.setAttribute("class", "g-edge t-" + sl); l.setAttribute("marker-end", "url(#arw-" + sl + ")");
        l.addEventListener("mouseenter", ev => edgeTip(ev, e)); l.addEventListener("mousemove", moveTip); l.addEventListener("mouseleave", hideTip);
        e._l = l; gE.appendChild(l);
      });
      nodes.forEach((n, i) => {
        const g = document.createElementNS(NS, "g"); g.setAttribute("class", "g-node" + (n.group ? " grp" : ""));
        g.setAttribute("data-key", n.key);
        const c = document.createElementNS(NS, "circle"); c.setAttribute("r", rad(n)); c.setAttribute("fill", colorFor(n.type));
        const nm = opts.sentence ? sentenceLabel(n.name) : n.name;
        const full = n.group ? (nm + " (" + n.count + ")") : nm;
        const LM = opts.shortLabel || opts.labelMax || 34;
        let label = full;
        if (label.length > LM) label = label.slice(0, LM - 1) + "…";
        n.full = full; n.short = label;
        const tx = document.createElementNS(NS, "text"); tx.setAttribute("text-anchor", "middle"); tx.setAttribute("dy", rad(n) + 15); tx.textContent = label;
        if (n.group) tx.setAttribute("font-weight", "700");
        g.appendChild(c); g.appendChild(tx); n._t = tx;
        g.addEventListener("mouseenter", ev => { highlight(n); nodeTip(ev, n); });
        g.addEventListener("mousemove", moveTip);
        g.addEventListener("mouseleave", () => { hideTip(); clearHi(); });
        drag(g, n); n._g = g; gN.appendChild(g);
        if (!n.pinned) setTimeout(() => g.classList.add("flash"), Math.min(i, 20) * 30);  // only NEW nodes flash
      });
      // zoom (wheel), pan (drag empty space), click empty space = clear focus
      let panning = false, plx = 0, ply = 0, pmoved = 0;
      svg.addEventListener("wheel", ev => { ev.preventDefault(); userView = true; zoomAt(svgPoint(ev), ev.deltaY > 0 ? 1.12 : 0.89); }, { passive: false });
      svg.addEventListener("pointerdown", ev => { panning = true; plx = ev.clientX; ply = ev.clientY; pmoved = 0; });
      svg.addEventListener("pointermove", ev => {
        if (!panning) return;
        const r = svg.getBoundingClientRect(), cdx = ev.clientX - plx, cdy = ev.clientY - ply;
        pmoved += Math.abs(cdx) + Math.abs(cdy); if (pmoved > 3) userView = true;
        vb.x -= cdx / r.width * vb.w; vb.y -= cdy / r.height * vb.h; plx = ev.clientX; ply = ev.clientY; setVB();
      });
      svg.addEventListener("pointerup", () => { panning = false; });
      svg.addEventListener("pointerleave", () => { panning = false; });

      // zoom controls
      const zc = el("div", { class: "zoom-ctl" }, []);
      const zbtn = (t, fn) => { const b = el("button", { class: "zoom-btn", type: "button", title: t }, [t === "reset" ? "⤢" : t]); b.addEventListener("pointerdown", e => e.stopPropagation()); b.addEventListener("click", e => { e.stopPropagation(); fn(); }); return b; };
      zc.appendChild(zbtn("＋", () => { userView = true; zoomAt({ x: vb.x + vb.w / 2, y: vb.y + vb.h / 2 }, 0.8); }));
      zc.appendChild(zbtn("－", () => { userView = true; zoomAt({ x: vb.x + vb.w / 2, y: vb.y + vb.h / 2 }, 1.25); }));
      zc.appendChild(zbtn("reset", () => { userView = false; if (opts.fit) fitKeys(null, false); else resetView(); }));

      container.appendChild(svg); container.appendChild(zc); draw(); alpha = startAlpha; raf = requestAnimationFrame(tick);
      if (opts.fit && window.ResizeObserver) { let lw = container.clientWidth, lh = container.clientHeight;
        new ResizeObserver(() => { const w = container.clientWidth, h = container.clientHeight; if (Math.abs(w - lw) + Math.abs(h - lh) < 4) return; lw = w; lh = h;
          if (!userView && !alpha) { if (focusSet) fitKeys(focusSet, true); else fitKeys(null, false); } }).observe(container); }
    }
    function highlight(n) {
      const conn = new Set();
      edges.forEach(e => { const on = e.s === n || e.t === n; e._l.classList.toggle("hi", on); e._l.classList.toggle("dim", !on); if (on) { conn.add(e.s); conn.add(e.t); } });
      nodes.forEach(m => m._g.classList.toggle("dim", m !== n && !conn.has(m)));
      if (opts.shortLabel && n._t) { n._t.textContent = n.full; n._g.classList.remove("lbl-off"); n._g.classList.add("lbl-full"); }
    }
    function clearHi() { edges.forEach(e => e._l.classList.remove("hi", "dim")); nodes.forEach(m => m._g.classList.remove("dim")); if (marked) applyMark(); else setLabels(); }
    // Persistent highlight set by the caller (extension: alignment / citations).
    let marked = null;
    function applyMark() {
      edges.forEach(e => { const on = !!marked && marked.e.has(e.id); e._l.classList.toggle("mk", on); e._l.classList.toggle("dim", !!marked && !on); });
      nodes.forEach(n => { const on = !!marked && marked.n.has(n.key); n._g.classList.toggle("mk", on); n._g.classList.toggle("dim", !!marked && !on); });
      setLabels();
    }
    // ---- opt-in extras (extension views): fit-to-view, short labels + declutter, focus, explored shading, pulse
    let userView = false, focusSet = null, firstSettle = true, pendingAct = null;
    const lblW = n => (n._t ? n._t.textContent.length : 0) * 7.1;
    function setLabels() {
      if (!opts.shortLabel) return;
      nodes.forEach(n => { if (!n._t) return; const full = !!marked && marked.n.has(n.key); n._t.textContent = full ? n.full : n.short; n._g.classList.toggle("lbl-full", full); n._g.classList.remove("lbl-off"); });
      if (!opts.declutter) return;
      const order = nodes.slice().sort((a, b) => ((marked && marked.n.has(b.key)) - (marked && marked.n.has(a.key))) || (b.group - a.group) || (b.deg - a.deg));
      const placed = [];
      order.forEach(n => {
        const w = lblW(n), r = { x0: n.x - w / 2, x1: n.x + w / 2, y0: n.y + rad(n) + 3, y1: n.y + rad(n) + 18 };
        const hit = placed.some(q => r.x0 < q.x1 && r.x1 > q.x0 && r.y0 < q.y1 && r.y1 > q.y0);
        if (hit && !(marked && marked.n.has(n.key))) n._g.classList.add("lbl-off"); else placed.push(r);
      });
    }
    function flipLabels() {            // every node keeps a label: below, else above / right / left, whichever is free
      const placed = nodes.map(m => ({ x0: m.x - rad(m), x1: m.x + rad(m), y0: m.y - rad(m), y1: m.y + rad(m) }));
      const hit = r => placed.some(q => r.x0 < q.x1 && r.x1 > q.x0 && r.y0 < q.y1 && r.y1 > q.y0);
      nodes.slice().sort((a, b) => b.deg - a.deg).forEach(n => {
        if (!n._t) return;
        const w = lblW(n), r = rad(n);
        const opts4 = [
          { box: { x0: n.x - w / 2, x1: n.x + w / 2, y0: n.y + r + 3, y1: n.y + r + 18 }, a: "middle", dx: 0, dy: r + 15 },
          { box: { x0: n.x - w / 2, x1: n.x + w / 2, y0: n.y - r - 18, y1: n.y - r - 3 }, a: "middle", dx: 0, dy: -(r + 7) },
          { box: { x0: n.x + r + 4, x1: n.x + r + 4 + w, y0: n.y - 8, y1: n.y + 8 }, a: "start", dx: r + 4, dy: 4 },
          { box: { x0: n.x - r - 4 - w, x1: n.x - r - 4, y0: n.y - 8, y1: n.y + 8 }, a: "end", dx: -(r + 4), dy: 4 }];
        const pick = opts4.find(o => !hit(o.box)) || opts4[0];
        n._t.setAttribute("text-anchor", pick.a); n._t.setAttribute("dx", pick.dx); n._t.setAttribute("dy", pick.dy);
        placed.push(pick.box);
      });
    }
    function fitKeys(keys, isFocus) {
      if (!svg) return;
      const set = keys ? nodes.filter(n => keys.has(n.key)) : nodes;
      if (!set.length) return;
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      set.forEach(n => { const r = rad(n), hw = Math.max(r, (isFocus ? n.full.length * 7.1 : lblW(n)) / 2);
        x0 = Math.min(x0, n.x - hw); x1 = Math.max(x1, n.x + hw); y0 = Math.min(y0, n.y - r); y1 = Math.max(y1, n.y + r + 20); });
      const pad = 18; x0 -= pad; y0 -= pad; x1 += pad; y1 += pad;
      let w = x1 - x0, h = y1 - y0;
      if (isFocus) {                       // focus = pan (and zoom OUT if needed); never zoom in beyond the current view
        const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, ww = Math.max(vb.w, w, h * vb.w / vb.h), hh = ww * vb.h / vb.w;
        const inside = x0 >= vb.x && x1 <= vb.x + vb.w && y0 >= vb.y && y1 <= vb.y + vb.h;
        if (!inside) { vb = { x: cx - ww / 2, y: cy - hh / 2, w: ww, h: hh }; setVB(); }
        return;
      }
      const br = svg.getBoundingClientRect(), A = br.width && br.height ? br.width / br.height : W / H;   // current panel shape
      if (w / h > A) { const nh = w / A; y0 -= (nh - h) / 2; h = nh; } else { const nw = h * A; x0 -= (nw - w) / 2; w = nw; }
      vb = { x: x0, y: y0, w, h }; setVB();
    }
    function settled() {
      setLabels(); if (opts.flipLabels) flipLabels();
      if (focusSet) fitKeys(focusSet, true);
      else if (opts.fit && firstSettle && !userView) fitKeys(null, false);
      firstSettle = false;
      if (pendingAct) { const f = pendingAct; pendingAct = null; f(); }
    }
    this.fit = () => { userView = false; focusSet = null; fitKeys(null, false); };
    this.focus = keys => {                        // pan/zoom so these nodes (with full labels) are visible
      focusSet = keys && keys.length ? new Set(keys) : null;
      if (marked) marked.n.forEach(k => { if (focusSet) focusSet.add(k); });
      if (!focusSet) { if (opts.fit) fitKeys(null, false); return; }
      if (!alpha) fitKeys(focusSet, true);        // otherwise applied when the layout settles
    };
    this.setExplored = (nodeKeys, edgeIds) => {
      const ns = nodeKeys ? new Set(nodeKeys) : null, es = edgeIds ? new Set(edgeIds) : null;
      nodes.forEach(n => { if (!n._g) return; n._g.classList.toggle("unexp", !!ns && !ns.has(n.key)); n._g.classList.toggle("disc", !!ns && ns.has(n.key)); });
      edges.forEach(e => { if (!e._l) return; e._l.classList.toggle("unexp", !!es && !es.has(e.id)); e._l.classList.toggle("disc", !!es && es.has(e.id)); });
    };
    this.pulse = (nodeKeys, edgeIds) => {
      const ns = new Set(nodeKeys || []), es = new Set(edgeIds || []);
      nodes.forEach(n => { if (n._g && ns.has(n.key)) { n._g.classList.remove("pulse"); void n._g.getBBox; n._g.classList.add("pulse"); setTimeout(() => n._g.classList.remove("pulse"), 1700); } });
      edges.forEach(e => { if (e._l && es.has(e.id)) { e._l.classList.add("pulse"); setTimeout(() => e._l.classList.remove("pulse"), 1700); } });
    };
    this.mark = (nodeKeys, edgeIds) => {
      marked = { n: new Set(nodeKeys || []), e: new Set(edgeIds || []) };
      edges.forEach(e => { if (marked.e.has(e.id)) { marked.n.add(e.s.key); marked.n.add(e.t.key); } });
      if (svg) applyMark();
    };
    this.clearMark = () => { marked = null; if (svg) applyMark(); };
    function nodeTip(ev, n) {
      if (n.group) {
        tip.innerHTML = "<div class='tt-type'>Theme · " + esc(n.type) + "</div><b>" + esc(n.name) + "</b>" +
          "<div class='tt-ev'>" + n.count + " nodes — click to expand</div>";
      } else {
        const ins = edges.filter(e => e.t === n).length, outs = edges.filter(e => e.s === n).length;
        tip.innerHTML = "<div class='tt-type'>" + esc(n.type) + "</div><b>" + esc(n.name) + "</b>" +
          "<div class='tt-type'>causes " + outs + " · caused by " + ins + "</div>" +
          (n.member ? "<div class='tt-ev'>double-click to collapse its theme</div>" : "");
      }
      tip.classList.add("show"); moveTip(ev);
    }
    function edgeTip(ev, e) {
      const w = e.weight > 1 ? "  ·  ×" + e.weight : "";
      tip.innerHTML = "<div class='tt-type'>" + esc(e.fromType) + " → " + esc(e.toType) + (e.recip ? " · mutual" : "") + w + "</div>" +
        "<b>" + esc(e.fromName) + "</b> → <b>" + esc(e.toName) + "</b>" + (e.evidence ? "<div class='tt-ev'>" + esc(e.evidence) + "</div>" : "");
      tip.classList.add("show"); moveTip(ev);
    }
    function moveTip(ev) { tip.style.left = Math.min(window.innerWidth - 320, ev.clientX + 14) + "px"; tip.style.top = (ev.clientY + 14) + "px"; }
    function hideTip() { tip.classList.remove("show"); }
    function drag(g, n) {
      let on = false, moved = 0;
      g.addEventListener("pointerdown", ev => { on = true; moved = 0; n.fixed = true; try { g.setPointerCapture(ev.pointerId); } catch (e) { /* synthetic event */ } ev.preventDefault(); ev.stopPropagation(); });
      g.addEventListener("pointermove", ev => {
        if (!on) return;
        moved += Math.abs(ev.movementX || 0) + Math.abs(ev.movementY || 0);
        const p = svgPoint(ev); n.x = p.x; n.y = p.y;
        if (alpha < 0.06) { alpha = 0.25; raf = requestAnimationFrame(tick); } draw();
      });
      const up = () => {
        if (!on) return;
        on = false; n.fixed = false;
        if (moved < 5) {                                   // a click, not a drag
          const now = Date.now();
          const act = () => {
            if (n.group) { if (opts.onExpand) opts.onExpand(n); return; }        // theme -> expand
            if (n.member && n._lc && now - n._lc < 320) { if (opts.onCollapse) opts.onCollapse(n); return; }  // double-click -> collapse
            n._lc = now;
            if (opts.onNodeClick) opts.onNodeClick(n);   // extension: node -> aligned utterances
          };
          // extension views: a click during the layout animation is applied once the layout settles
          if (opts.fit && alpha > 0.05) { pendingAct = act; return; }
          act();
          return;   // single click on a member: no-op (hover shows the bright/dim highlight)
        }
        alpha = Math.max(alpha, 0.2);
      };
      g.addEventListener("pointerup", up); g.addEventListener("pointercancel", up);
    }
    this.render = render;
  }

  window.addEventListener("DOMContentLoaded", init);
  // Internals shared with static/ext.js (ANGEL demo extension; inert when no flag is on).
  const _ext = { api, el, $, $$, toast, show, State, ForceGraph, colorFor, TYPES, BASE, sentenceLabel };
  return { _ext, back, toggleTheme, goCreate, goExisting, reviewProfile, generateFromForm, generateFromExisting,
    restart, applyFilters, showGraph, toggleLongProfile, goChat, sendChat, toggleVoice, toggleMic, liveOn, LIVE_OFF_MSG };
})();
