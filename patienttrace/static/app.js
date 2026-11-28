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

  function savedLayout() { return "flow"; }        // Network 2D view = Flow (the Free force layout stays for Interview/Compare)
  function savedView() { try { return localStorage.getItem("pt-view") === "2d" ? "2d" : "3d"; } catch (e) { return "3d"; } }
  const State = { screen: null, history: [], scrollY: {}, config: null, profile: null,
    existing: [], facets: {}, selectedIdx: null, job: null, poll: null,
    graphs: { short: null, long: null }, activeTab: "short", longProfile: null,
    stage: null, expanded: { short: new Set(), long: new Set() },
    member: { short: {}, long: {} }, expanding: false, pos: { short: {}, long: {} }, view: {}, layout: savedLayout(), view3d: savedView(), n3: null, chat: null,
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

  // NDJSON stream (live patient replies): calls onEvent(obj) per line; resolves when the stream ends.
  // `signal` (AbortController) cancels the read; the caller also tells the server to stop (/api/ext/live/stop).
  async function stream(path, body, onEvent, signal) {
    const r = await fetch(BASE + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
    if (!r.ok) { let d; try { d = await r.json(); } catch (e) {} throw new Error((d && d.detail) || ("HTTP " + r.status)); }
    const rd = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await rd.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf("\n")) >= 0) { const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1); if (line) onEvent(JSON.parse(line)); }
    }
    if (buf.trim()) onEvent(JSON.parse(buf));
  }
  const newStreamId = () => Math.random().toString(36).slice(2) + Date.now().toString(36);
  // JSON POST/GET with a hard client-side time limit: a slow model step never holds a browser connection forever
  // (browsers allow only ~6 per site over plain HTTP; hung requests would queue everything else, even page loads)
  async function apiT(path, body, ms) {
    const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), ms);
    try {
      const r = await fetch(BASE + path, { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined, signal: ctl.signal });
      if (!r.ok) { let d; try { d = await r.json(); } catch (e) {} throw new Error((d && d.detail) || ("HTTP " + r.status)); }
      return r.json();
    } catch (e) { if (ctl.signal.aborted) throw new Error(`no answer within ${Math.round(ms / 1000)} s`); throw e; }
    finally { clearTimeout(t); }
  }
  // live status of the patient model (up | starting | down): polled every 15 s, every 5 s while it is starting
  const LiveStatus = (() => {
    let cur = null, timer = null, started = false; const subs = [];
    const norm = st => (st && st.patient_model === "cold" ? Object.assign({}, st, { patient_model: "starting" }) : st);
    async function poll() {
      clearTimeout(timer);
      if (!document.hidden) {
        try { cur = norm(await apiT("/api/live-status", null, 8000)); }
        catch (e) { cur = { patient_model: "down", phase: "unreachable", detail: "The server did not answer the status check." }; }
        subs.forEach(f => { try { f(cur); } catch (e) {} });
      }
      timer = setTimeout(poll, cur && cur.patient_model === "starting" ? 5000 : 15000);
      return cur;
    }
    function set(st) { cur = norm(st); subs.forEach(f => { try { f(cur); } catch (e) {} }); clearTimeout(timer); timer = setTimeout(poll, cur.patient_model === "starting" ? 5000 : 15000); }
    function start() { if (started) return; started = true; poll(); document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); }); }
    // resolves once the patient model is up; rejects if it is down (and nothing is starting) or after maxMs
    function waitUp(maxMs, signal) {
      return new Promise((resolve, reject) => {
        const t0 = Date.now();
        if (signal) signal.addEventListener("abort", () => reject(new Error("stopped")), { once: true });
        const check = async () => {
          if (signal && signal.aborted) return reject(new Error("stopped"));
          const st = await poll();
          if (st.patient_model === "up") return resolve(st);
          if (st.patient_model === "down") return reject(new Error("unavailable: " + (st.detail || "the patient model is not running")));
          if (Date.now() - t0 > maxMs) return reject(new Error("the patient model did not start within " + Math.round(maxMs / 60000) + " minutes"));
          setTimeout(check, 5000);
        };
        check();
      });
    }
    return { start, poll, set, waitUp, current: () => cur, on: f => { subs.push(f); if (cur) f(cur); } };
  })();
  function liveBadge(el, st) {               // up "Live · ready" / starting "Warming up the patient model…" / down "Live interview unavailable right now"
    if (!el) return;
    if (!st) { el.className = "live-badge hidden"; return; }
    const m = st.patient_model === "cold" ? "starting" : st.patient_model;
    const txt = m === "up" ? "Live · ready" : m === "starting" ? (st.phase === "queued" ? "Starting the patient model…" : "Warming up the patient model…") : "Live interview unavailable right now";
    el.className = "live-badge " + (m === "starting" ? "cold" : m); el.textContent = txt;
    el.title = (st.detail || "") + (st.last_latency_ms ? ` Last reply ${(st.last_latency_ms / 1000).toFixed(1)} s.` : "");
  }

  const PROGRESS = { choose: "", create: "Create profile", existing: "Choose profile", generate: "Network model", chat: "Chat with patient", interview: "Interview", fc: "Formulation Contrast",
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
  function liveOffMsg() { const m = liveMissing(); return m.length ? "Live mode is off on this server: " + m.join("; ") + ". The recorded examples work without it." : LIVE_OFF_MSG; }
  // Network page: a status dot beside the live entry buttons (gray = down, amber = starting, none = up),
  // with its own tooltip and aria-label; the button carries the same text
  // opening a library patient's Network page warms the patient model (or, autostart on, starts a worker) so the
  // first Interview / Formulation Contrast question is not a cold start; at most once a minute
  let lastWarm = 0;
  function prewarm() {
    if (!liveOn() || Date.now() - lastWarm < 60000) return;
    lastWarm = Date.now();
    apiT("/api/live/warm", {}, 10000).then(st => LiveStatus.set(st)).catch(() => {});
  }
  function liveDots(st) {
    const on = liveOn() && !!State.patientId, m = st && (st.patient_model === "cold" ? "starting" : st.patient_model);
    const state = on && (m === "down" || m === "starting") ? m : "";
    const msg = state === "down" ? "Live interview unavailable right now" : state === "starting" ? "Starting the patient model…" : "";
    const nb = document.getElementById("net-livebadge");     // the starting phase also gets a visible label in the header
    if (nb) { if (state === "starting") liveBadge(nb, st); else nb.className = "live-badge hidden"; }
    [["chat-btn", "iv-dot", "Interview this simulated patient live"], ["contrast-btn", "fc-dot", "Pick a look-alike patient and interview both live"]].forEach(([id, dot, ok]) => {
      const b = document.getElementById(id), d = document.getElementById(dot); if (!b || !d) return;
      d.className = "live-dot" + (state ? " " + state : " hidden") + (b.classList.contains("hidden") ? " hidden" : "");
      d.title = msg; if (msg) d.setAttribute("aria-label", msg); else d.removeAttribute("aria-label");
      b.title = msg || ok; if (msg) b.setAttribute("aria-label", `${b.textContent.trim()} (${msg})`); else b.removeAttribute("aria-label");
    });
  }
  LiveStatus.on(liveDots);
  async function watchExample() {           // Network page: "Watch a recorded example" (Interview)
    if (!(window.Interview && State.patientId && State.network)) return;
    await Interview.open(); Interview.enterExample();
  }
  function liveMissing() { const x = State.config && State.config.ext; return (x && x.live_missing) || []; }
  function customOn() { return extFlags().custom !== false; }

  async function init() {
    try { State.config = await api("/api/config"); }
    catch (e) { toast("Failed to load config: " + e.message); return; }
    if (State.config.ext) LiveStatus.start();
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
    await loadRecorded();
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
  // patients with a recorded interview (the Interview page works for them without live mode)
  async function loadRecorded() {
    if (State.recorded) return State.recorded;
    try { State.recorded = (await api("/api/ext/interview/sessions")).recorded || []; } catch (e) { State.recorded = []; }
    return State.recorded;
  }
  const hasRecording = idx => (State.recorded || []).some(r => r.index === idx);
  function renderPicker(list) {
    const picker = $("#profile-picker"); picker.innerHTML = "";
    $("#picker-count").textContent = list.length + " of " + State.existing.length + " profiles";
    list.slice(0, 200).forEach((p, i) => {
      const card = el("div", { class: "pick-card" }); card.style.animationDelay = (Math.min(i, 16) * 20) + "ms";
      card.appendChild(el("div", { class: "pick-head" }, [
        el("span", { class: "pick-name" }, [p.name || "(unnamed)"]),
        el("span", { class: "pick-meta" }, [[p.gender, p.age ? p.age + "y" : p.age_bucket, p.culture !== "Unspecified" ? p.culture : ""].filter(Boolean).join(" · ")])]));
      if (hasRecording(p.index)) card.appendChild(el("span", { class: "pick-rec", title: "Has a recorded interview: the Interview page works without live mode" }, ["Recorded interview"]));
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
    await loadRecorded();
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
    State.expanding = false; State.pos = { short: {}, long: {} }; State.view = {}; State.mode = null; State.markSet = null; State.sel = null; drop3d();
    State.patientId = d.patient_id; State.network = d.network; State.patientTags = d.tags;
    if (window.Ext) { try { Ext.onShow(State.screen); } catch (e) { /* step bar refresh only */ } }   // Interview step becomes a link
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
    const idx = State.patientId ? parseInt(State.patientId.slice(1), 10) : null;
    const rec = document.getElementById("iv-rec-link");
    if (rec) rec.classList.toggle("hidden", !(window.Interview && idx != null && hasRecording(idx)));
    if (window.Interview && State.patientId && liveOn()) {     // library patient: the Interview page, live by default
      b.disabled = false; b.textContent = "Interview"; b.title = "Interview this simulated patient live"; liveDots(LiveStatus.current()); prewarm(); return; }
    if (!liveOn()) {
      if (window.Interview && idx != null && hasRecording(idx)) { b.disabled = false; b.textContent = "Interview"; b.title = "Live interviews are off on this server; the page offers the recorded example"; return; }
      if (window.Interview && State.patientId) { b.disabled = false; b.textContent = "💬 Interview (no recording)";
        b.title = "Live chat is off and this patient has no recorded interview; the page offers the patients that have one: " + (State.recorded || []).map(r => r.name).join(", "); return; }
      b.disabled = true; b.textContent = "💬 Chat (live mode off)"; b.title = LIVE_OFF_MSG; return; }
    b.disabled = mode === "waiting";
    b.textContent = { waiting: "💬 Chat (expanding profile…)", ready: "💬 Chat with patient",
                      fallback: "💬 Chat (authored profile)" }[mode];
    b.title = mode === "fallback" ? "Stage 1 failed, so the patient uses the profile as written" : "";
  }

  async function startJob(profile) {
    State.profile = profile; State.graphs = { short: null, long: null }; State.activeTab = "short"; State.longProfile = null;
    State.expanded = { short: new Set(), long: new Set() };
    State.member = { short: {}, long: {} }; State.expanding = false;
    State.pos = { short: {}, long: {} }; State.view = {}; State.mode = null; State.markSet = null; State.sel = null; drop3d();
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
  function posKey(tab) { const k = tab + ":" + State.layout; if (!State.pos[k]) State.pos[k] = {}; return k; }
  function layered(g) { return !!(g && g.groups && g.groups.length && g.edges && g.nodes.length > g.groups.length); }
  // themes with a single member are never collapsed (pointless wrapper)
  function realGroups(g) { return g.groups.map((grp, gi) => ({ grp, gi })).filter(x => x.grp.members.length > 1); }

  function drop3d() { if (State.n3) { State.n3.destroy(); State.n3 = null; State.fg = null; } }
  const can3d = g => State.view3d === "3d" && window.Net3D && Net3D.supported() && layered(g);
  window.addEventListener("net3d-ready", () => { const g = State.graphs[State.activeTab]; if (g && can3d(g) && !State.n3 && $("#graph-slot svg.graph-svg")) renderActive(); });
  function fitStage() {             // the Network graph fills the window below the toolbar (no scrolling to see it)
    const st = $("#graph-stage"); if (!st || !st.offsetParent) return;
    const top = st.getBoundingClientRect().top + window.scrollY;
    st.style.minHeight = "440px"; st.style.height = Math.max(440, Math.round(window.innerHeight - top - 16)) + "px";
  }
  window.addEventListener("resize", () => fitStage());
  function renderActive() {
    const g = State.graphs[State.activeTab]; if (!g) return;
    drop3d();
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
    document.body.classList.toggle("view-3d", can3d(g));
    if (can3d(g)) {               // ---- 3D layered view (default) ----
      State.n3 = Net3D.mount(slot, g, { showIso: !!State.showIso, tip: $("#g-tip"),
        onNodeClick: n => openNode(n), onBackgroundClick: closePanel, onChange: () => renderLayerCtl() });
      State.fg = State.n3;
      renderLayerCtl();
      if (State.mode) applyMode(true);
      renderInsights(); fitStage();
      if (State.sel && State.sel.kind === "node" && !State.n3.select(State.sel.name)) closePanel();
      return;
    }
    renderInsights(); fitStage();
    const pk = posKey(State.activeTab);
    if (!layered(g)) view = { nodes: view.nodes, edges: view.edges.map(e => Object.assign({ members: [e], id: e.from + "|" + e.to }, e)) };
    const flowModel = State.layout === "flow" && window.Flow ? (g._flow || (g._flow = Flow.model({ nodes: g.nodes, edges: g.edges || [], groups: g.groups || [] }))) : null;
    const fg = new ForceGraph(slot, view, Object.assign({ onExpand, onCollapse, positions: State.pos[pk],
      layout: State.layout, flowModel, view: State.view[pk] || (State.view[pk] = {}),
      onNodeClick: n => openNode(n), onEdgeClick: e => openEdge(e), onBackgroundClick: closePanel }, extOpts));
    fg.render(); State.fg = fg;
    if (State.mode) applyMode(false);
    renderInsights();
    // keep the selection across expand/collapse when it is still on screen
    if (State.sel && State.sel.kind === "node" && !fg.select("n:" + State.sel.name) && !fg.select(State.sel.name)) closePanel();
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
    const pos = State.pos[posKey(kind)] || {};
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
      if (!emap[k]) emap[k] = { id: k, from: a, to: b, fromName: e.from, toName: e.to, fromType: e.from_type, toType: e.to_type, evidence: e.evidence || "", weight: 0, members: [] };
      emap[k].weight++; emap[k].members.push(e);
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

  function layoutToggle() {
    const box = el("span", { class: "seg", role: "group", "aria-label": "View" });
    [["3d", "3D"], ["2d", "2D"]].forEach(([k, t]) => {
      const on = State.view3d === k;
      const b = el("button", { type: "button", class: "seg-btn" + (on ? " on" : ""), "aria-pressed": String(on),
        title: k === "3d" ? "Themes on top; click one to open the layer of its nodes beneath it" : "Columns in CBT order: stimulus → cognition → emotion → body → behaviour" }, [t]);
      if (k === "3d" && !(window.Net3D && Net3D.supported())) { b.disabled = true; b.title = "3D needs WebGL, which this browser does not offer"; }
      b.addEventListener("click", () => { if (State.view3d === k) return; State.view3d = k;
        try { localStorage.setItem("pt-view", k); } catch (e) { /* storage off */ }
        closePanel(); renderActive(); });
      box.appendChild(b);
    });
    return box;
  }
  // ================= DETAILS PANEL (node / edge) =================
  const STOP = new Set("the a an and or of to in on for with that this is are was were be been being as at by from it its their his her they he she we you i not no but if when while about into over than then so very more most can could would should may might will just also".split(" "));
  const stem = w => w.replace(/(ing|ed|es|s)$/, "");
  const words = t => String(t || "").toLowerCase().replace(/[^a-z0-9' ]+/g, " ").split(/\s+/).filter(w => w.length > 2 && !STOP.has(w)).map(stem);
  function bestPassage(text) {          // long-profile item sharing the most words with this text (simple fuzzy match)
    const want = new Set(words(text)); if (!want.size) return null;
    let best = null;
    document.querySelectorAll("#long-profile-view li, #long-profile-view p").forEach((elx, i) => {
      const ws = words(elx.textContent), hit = ws.filter(w => want.has(w));
      const score = new Set(hit).size / Math.sqrt(Math.max(4, ws.length));
      if (!best || score > best.score) best = { el: elx, i, score, hits: new Set(hit) };
    });
    return best && best.hits.size >= 2 ? best : null;
  }
  function markWords(text, hits) {
    return String(text).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])).replace(/[A-Za-z0-9']+/g, w => hits.has(stem(w.toLowerCase())) && w.length > 2 && !STOP.has(w.toLowerCase()) ? "<mark>" + w + "</mark>" : w);
  }
  function showInProfile(i) {
    const items = document.querySelectorAll("#long-profile-view li, #long-profile-view p"), it = items[i]; if (!it) return;
    items.forEach(x => x.classList.remove("lp-hit"));
    $("#lp-overlay").classList.remove("hidden"); it.classList.add("lp-hit");
    it.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }
  function themeOf(g, name) { const gi = (g.groups || []).findIndex(x => x.members.includes(name)); return gi < 0 ? null : { gi, label: g.groups[gi].label, size: g.groups[gi].members.length }; }
  function panelShell(kids) {
    const p = $("#net-panel"); p.innerHTML = "";
    const x = el("button", { class: "assist-close", type: "button", "aria-label": "Close details", title: "Close (Esc)" }, ["×"]);
    x.addEventListener("click", closePanel); p.appendChild(x);
    kids.forEach(k => k && p.appendChild(k)); p.classList.remove("hidden"); panelInset(); return p;
  }
  function panelInset() {               // 3D: fit the camera to the part of the canvas the panel leaves free
    if (!State.n3) return;
    const p = $("#net-panel"), c = $("#graph-slot");
    if (!p || p.classList.contains("hidden") || !c) { State.n3.setInset(0); return; }
    const pr = p.getBoundingClientRect(), cr = c.getBoundingClientRect();
    State.n3.setInset(pr.width > 0 && pr.left > cr.left ? cr.right - pr.left + 16 : 0);
  }
  function edgeRow(g, e, dir) {         // one link in a node's list; click = open that edge
    const other = dir === "out" ? e.to : e.from;
    const t = (g.nodes.find(n => n.name === other) || {}).type;
    const row = el("li", { class: "np-edge", tabindex: "0" }, [
      el("div", { class: "np-edge-h" }, [el("span", { class: "sw", style: "background:" + colorFor(t) }), (dir === "out" ? "→ " : "← ") + sentenceLabel(other)]),
      e.evidence ? el("div", { class: "np-ev" }, [e.evidence]) : null].filter(Boolean));
    const go = () => openEdge({ members: [e], fromName: e.from, toName: e.to });
    row.addEventListener("click", go); row.addEventListener("keydown", ev => { if (ev.key === "Enter") go(); });
    return row;
  }
  function openNode(n) {
    if (n.group) return;
    const g = State.graphs[State.activeTab], name = n.name;
    const node = g.nodes.find(x => x.name === name) || { name, type: n.type };
    const out = (g.edges || []).filter(e => e.from === name), inn = (g.edges || []).filter(e => e.to === name);
    const th = themeOf(g, name);
    State.sel = { kind: "node", name };
    if (State.fg) State.fg.select(n.key);
    const kids = [
      el("div", { class: "np-type" }, [el("span", { class: "sw", style: "background:" + colorFor(node.type) }), node.type + (th && th.size > 1 ? " · theme: " + th.label : "")]),
      el("h3", { class: "np-title" }, [sentenceLabel(name)]),
      el("div", { class: "np-counts" }, ["causes " + out.length + " · caused by " + inn.length]),
    ];
    if (th && th.size > 1 && (State.n3 ? State.n3.isOpenFor(name) : State.expanded[State.activeTab].has(th.gi))) {
      const b = el("button", { class: "ghost small", type: "button" }, ["Collapse theme “" + th.label + "”"]);
      b.addEventListener("click", () => { if (State.n3) { closePanel(); State.n3.closeThemeOf(name); return; }
        State.expanded[State.activeTab].delete(th.gi); closePanel(); renderActive(); });
      kids.push(el("div", { class: "np-actions" }, [b]));
    }
    const sec = (title, list, dir) => { if (!list.length) return null;
      const ul = el("ul", { class: "np-list" }, list.map(e => edgeRow(g, e, dir))); return el("div", {}, [el("h4", {}, [title + " (" + list.length + ")"]), ul]); };
    kids.push(sec("Causes", out, "out"), sec("Caused by", inn, "in"));
    if (!out.length && !inn.length) kids.push(el("p", { class: "muted small" }, ["No links to other nodes in this network."]));
    panelShell(kids);
  }
  function openEdge(e) {
    const g = State.graphs[State.activeTab], list = e.members && e.members.length ? e.members : [{ from: e.fromName, to: e.toName, evidence: e.evidence }];
    State.sel = { kind: "edge" };
    if (State.fg && e.s) State.fg.selectEdge(e.s.key, e.t.key);
    else if (State.n3 && list.length === 1) State.n3.selectEdge(list[0].from, list[0].to);
    const kids = [el("div", { class: "np-type" }, [list.length > 1 ? list.length + " links between these themes" : "Link"])];
    list.forEach(x => {
      const ft = (g.nodes.find(n => n.name === x.from) || {}).type, tt = (g.nodes.find(n => n.name === x.to) || {}).type;
      const box = el("div", { class: "np-link" }, [
        el("div", { class: "np-edge-h" }, [el("span", { class: "sw", style: "background:" + colorFor(ft) }), sentenceLabel(x.from)]),
        el("div", { class: "np-arrow", "aria-hidden": "true" }, ["↓ causes"]),
        el("div", { class: "np-edge-h" }, [el("span", { class: "sw", style: "background:" + colorFor(tt) }), sentenceLabel(x.to)]),
        el("h4", {}, ["Evidence"]), el("p", { class: "np-ev" }, [x.evidence || "No evidence text recorded."])]);
      const m = State.longProfile ? bestPassage(x.evidence) : null;
      if (m) {
        const q = el("blockquote", { class: "np-quote" }); q.innerHTML = markWords(m.el.textContent, m.hits);
        const b = el("button", { class: "ghost small", type: "button" }, ["Show in long profile"]);
        b.addEventListener("click", () => showInProfile(m.i));
        box.appendChild(el("h4", {}, ["Closest passage in the long profile"])); box.appendChild(q); box.appendChild(b);
      }
      kids.push(box);
    });
    panelShell(kids);
  }
  function closePanel() {
    State.sel = null; const p = $("#net-panel"); if (p) { p.classList.add("hidden"); p.innerHTML = ""; }
    if (State.fg) State.fg.unselect();
    panelInset();
  }
  document.addEventListener("keydown", ev => {
    if (ev.key !== "Escape") return;
    if (!$("#lp-overlay").classList.contains("hidden")) { $("#lp-overlay").classList.add("hidden"); return; }
    if ($("#net-panel") && !$("#net-panel").classList.contains("hidden")) { closePanel(); return; }
    if (State.mode) stopMode();
  });

  // ================= LOOPS · MOST CENTRAL · TRACE FROM TRIGGER =================
  // Analysis runs on the full network; the view expands the themes it needs and marks the result.
  const REDUCED = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  function keyOf(g, name) { return State.n3 ? name : layered(g) ? "n:" + name : name; }
  function edgeKey(g, a, b) { return keyOf(g, a) + "|" + keyOf(g, b); }
  function analysis(g) {
    if (!g._an) {
      const all = Flow.cycles(g), groups = [];
      // a loop sharing >= 2/3 of its (smaller) node set with an earlier, better-ranked loop joins that loop's group
      all.forEach(c => {
        const set = new Set(c.nodes);
        const host = groups.find(r => r.nodes.filter(n => set.has(n)).length / Math.min(r.nodes.length, c.nodes.length) >= 0.67);
        if (host) host.similar++; else groups.push(Object.assign({ similar: 0 }, c));
      });
      g._an = { loops: all, groups, top: groups.slice(0, 8), central: Flow.central(g) };
    }
    return g._an;
  }
  function needThemes(g, names) {        // expand every theme holding one of these nodes; true if a 2D re-render is needed
    if (State.n3) { State.n3.reveal(names, { noCamera: true }); return false; }
    if (!layered(g)) return false;
    const set = State.expanded[State.activeTab]; let ch = false;
    (g.groups || []).forEach((grp, gi) => { if (grp.members.length > 1 && !set.has(gi) && grp.members.some(m => names.includes(m))) { set.add(gi); ch = true; } });
    return ch;
  }
  function showSet(names, pairs) {        // mark these nodes/edges (expanding themes first)
    const g = State.graphs[State.activeTab];
    State.markSet = { names, pairs };
    if (needThemes(g, names)) { renderActive(); return; }
    applyMark();
  }
  function applyMark() {
    const g = State.graphs[State.activeTab], m = State.markSet; if (!State.fg) return;
    if (!m) { State.fg.clearMark(); return; }
    State.fg.mark(m.names.map(n => keyOf(g, n)), m.pairs.map(([a, b]) => edgeKey(g, a, b)));
    State.fg.focus(m.names.map(n => keyOf(g, n)));
  }
  function applyMode(fresh) {             // re-apply the active mode after a re-render
    if (State.mode === "loops") showLoop(fresh); else if (State.mode === "trace") showTrace(fresh);
    applyMark();
  }
  function bar(kids) {
    const b = $("#net-bar"); b.innerHTML = ""; kids.filter(Boolean).forEach(k => b.appendChild(typeof k === "string" ? document.createTextNode(k) : k));
    const x = el("button", { class: "icon-btn nb-close", type: "button", "aria-label": "Stop", title: "Stop (Esc)" }, ["×"]);
    x.addEventListener("click", stopMode); b.appendChild(x); b.classList.remove("hidden");
  }
  function stopMode() {
    clearInterval(State.traceTimer); State.traceTimer = null;
    const wasLoops = State.mode === "loops";
    State.mode = null; State.markSet = null; $("#net-bar").classList.add("hidden"); $("#net-bar").innerHTML = "";
    if (wasLoops && State.loopBase) {          // close the themes that only the loops opened
      const g = State.graphs[State.activeTab];
      if (State.n3) { State.n3.openOnly([], State.loopBase); applyMark(); }
      else { State.expanded[State.activeTab] = new Set(State.loopBase); renderActive(); }
      State.loopBase = null;
    } else applyMark();
    renderInsights();
  }
  const loopList = g => State.loopAll ? analysis(g).loops : analysis(g).top;
  function startLoops() {
    const g = State.graphs[State.activeTab]; if (!analysis(g).loops.length) return;
    closePanel(); clearInterval(State.traceTimer);
    // themes the user had open before stepping through loops; each loop opens only what it needs on top of these
    State.loopBase = State.n3 ? State.n3.openSet() : [...State.expanded[State.activeTab]];
    State.mode = "loops"; State.loopIdx = 0; State.loopAll = false; showLoop(true); renderInsights();
  }
  function openForLoop(g, names) {
    if (State.n3) { State.n3.openOnly(names, State.loopBase || []); return false; }
    const need = new Set(State.loopBase || []);
    (g.groups || []).forEach((grp, gi) => { if (grp.members.length > 1 && grp.members.some(m => names.includes(m))) need.add(gi); });
    const cur = State.expanded[State.activeTab], same = need.size === cur.size && [...need].every(x => cur.has(x));
    State.expanded[State.activeTab] = need; return !same;
  }
  function showLoop(fresh) {
    const g = State.graphs[State.activeTab], L = loopList(g), c = L[State.loopIdx];
    const prev = el("button", { class: "ghost small", type: "button", "aria-label": "Previous loop" }, ["‹"]);
    const next = el("button", { class: "ghost small", type: "button", "aria-label": "Next loop" }, ["›"]);
    prev.disabled = State.loopIdx === 0; next.disabled = State.loopIdx === L.length - 1;
    prev.addEventListener("click", () => { State.loopIdx--; showLoop(true); });
    next.addEventListener("click", () => { State.loopIdx++; showLoop(true); });
    const cap = c.nodes.concat([c.nodes[0]]).map(n => sentenceLabel(n).toLowerCase()).join(" → ");
    const A = analysis(g), more = el("button", { class: "ghost small", type: "button", title: State.loopAll ? "Back to the distinct loops" : "Every loop, including near-duplicates" },
      [State.loopAll ? "Top " + A.top.length : "Show all " + A.loops.length]);
    more.addEventListener("click", () => { State.loopAll = !State.loopAll; State.loopIdx = 0; showLoop(true); renderInsights(); });
    bar([el("b", {}, ["Loop " + (State.loopIdx + 1) + " of " + L.length]), prev, next,
      el("span", { class: "nb-cap", title: cap }, [cap, !State.loopAll && c.similar ? el("span", { class: "muted" }, ["  (+" + c.similar + " similar)"]) : null].filter(Boolean)),
      A.loops.length > A.top.length ? more : null]);
    if (fresh) { State.markSet = { names: c.nodes, pairs: c.edges }; if (openForLoop(g, c.nodes)) { renderActive(); return; } applyMark(); }
  }
  function startTrace(name) {
    closePanel(); clearInterval(State.traceTimer);
    const g = State.graphs[State.activeTab];
    State.mode = "trace"; State.trace = { start: name, steps: Flow.reach(g, name), k: 0 };
    const all = State.trace.steps.flatMap(s => s.nodes);
    needThemes(g, all) ? (renderActive()) : null;       // expand everything it will reach once, up front
    State.trace.k = REDUCED() ? State.trace.steps.length - 1 : 0;
    showTrace(true);
    if (!REDUCED() && State.trace.steps.length > 1)
      State.traceTimer = setInterval(() => { if (!State.trace || State.trace.k >= State.trace.steps.length - 1) { clearInterval(State.traceTimer); State.traceTimer = null; showTrace(false); return; }
        State.trace.k++; showTrace(true); }, 1100);
    renderInsights();
  }
  function showTrace(fresh) {
    const T = State.trace; if (!T) return;
    const upto = T.steps.slice(0, T.k + 1), names = upto.flatMap(s => s.nodes), pairs = upto.flatMap(s => s.edges);
    const last = T.k === T.steps.length - 1;
    const replay = el("button", { class: "ghost small", type: "button" }, ["Replay"]);
    replay.addEventListener("click", () => startTrace(T.start));
    bar([el("b", {}, ["From “" + sentenceLabel(T.start) + "”"]),
      el("span", { class: "nb-cap" }, [T.steps.length < 2 ? "this trigger has no outgoing links" :
        "step " + T.k + " of " + (T.steps.length - 1) + " · reaches " + (names.length - 1) + " node" + (names.length === 2 ? "" : "s") + (last ? "" : "…")]),
      last && T.steps.length > 1 && !REDUCED() ? replay : null]);
    if (fresh) { State.markSet = { names, pairs }; applyMark();
      if (State.n3 && T.k > 0) State.n3.pulse(T.steps[T.k].nodes); }
  }
  function renderInsights() {
    const g = State.graphs[State.activeTab], box = $("#insight-ctl");
    if (!g || !(g.nodes || []).length || !window.Flow) { box.classList.add("hidden"); return; }
    const A = analysis(g); box.innerHTML = ""; box.classList.remove("hidden");
    const lb = el("button", { class: "ghost small" + (State.mode === "loops" ? " on" : ""), type: "button",
      title: "Directed cycles of 2–5 nodes; near-duplicates grouped; shortest and most central first" },
      [A.loops.length ? "Feedback loops (" + A.top.length + " of " + A.loops.length + ")" : "Feedback loops (0)"]);
    lb.addEventListener("click", () => State.mode === "loops" ? stopMode() : startLoops());
    // a network without any directed cycle says so instead of showing a disabled "(0)" button
    box.appendChild(A.loops.length ? lb : el("span", { class: "muted small no-loops", title: "No chain of links in this network leads back to where it started" }, ["No feedback loops"]));
    const stim = g.nodes.filter(n => n.type === "Stimulus").sort((a, b) => a.name.localeCompare(b.name));
    if (stim.length) {
      const sel = el("select", { class: "small-select", id: "trace-select", "aria-label": "Trace from trigger" },
        [el("option", { value: "" }, ["Trace from trigger…"])].concat(stim.map(n => el("option", { value: n.name }, [sentenceLabel(n.name)]))));
      if (State.mode === "trace" && State.trace) sel.value = State.trace.start;
      sel.addEventListener("change", () => sel.value ? startTrace(sel.value) : stopMode());
      box.appendChild(sel);
    }
    if (A.central.length) {          // compact: one dropdown instead of a row of chips
      const cs = el("select", { class: "small-select", id: "central-select", "aria-label": "Most central nodes", title: "Nodes with the most links" },
        [el("option", { value: "" }, ["Most central…"])].concat(A.central.map(c => el("option", { value: c.name }, [sentenceLabel(c.name) + " · " + c.degree + " links"]))));
      cs.addEventListener("change", () => { const v = cs.value; cs.value = ""; if (v) selectByName(v); });
      box.appendChild(cs);
    }
  }
  function selectByName(name) {           // expand its theme if needed, then open it in the panel
    const g = State.graphs[State.activeTab];
    if (State.mode) stopMode();
    State.sel = { kind: "node", name };
    if (needThemes(g, [name])) renderActive();
    const key = keyOf(g, name), n = { key, name, type: (g.nodes.find(x => x.name === name) || {}).type };
    openNode(n); if (State.fg) State.fg.focus([key]);
  }

  function setHint(t) { const h = $("#net-hint"); if (h) { h.textContent = t || ""; h.classList.toggle("hidden", !t); } }
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
      setHint("");
      const b = isoBtn();
      ctl.classList.remove("hidden"); ctl.innerHTML = ""; ctl.appendChild(layoutToggle()); if (b) ctl.appendChild(b); return;
    }
    ctl.classList.remove("hidden"); ctl.innerHTML = "";
    if (State.n3) {                 // 3D toolbar
      const n = State.n3.openCount();
      setHint(n ? n + " of " + State.n3.themeCount() + " themes open · click a node for details · drag to rotate"
        : "Click a theme to open the layer of its nodes beneath it");
      const ov = el("button", { class: "ghost small", type: "button", title: "Close every layer and return to the flat view" }, ["Overview"]);
      ov.disabled = !n; ov.addEventListener("click", () => { closePanel(); State.n3.collapseAll(); });
      const all = el("button", { class: "ghost small", type: "button" }, ["Expand all"]);
      all.addEventListener("click", () => State.n3.expandAll());
      ctl.appendChild(layoutToggle()); ctl.appendChild(ov); ctl.appendChild(all);
      const ib = isoBtn(); if (ib) ctl.appendChild(ib);
      return;
    }
    const kind = State.activeTab, set = State.expanded[kind];
    setHint(set.size ? "Click a theme to expand · a node or link for details" : "Click a theme to expand it into its nodes");
    const overview = el("button", { class: "ghost small", type: "button" }, ["Overview"]);
    overview.addEventListener("click", () => { set.clear(); renderActive(); });
    const all = el("button", { class: "ghost small", type: "button" }, ["Expand all"]);
    all.addEventListener("click", () => { realGroups(g).forEach(x => set.add(x.gi)); renderActive(); });
    ctl.appendChild(layoutToggle()); ctl.appendChild(overview); ctl.appendChild(all);
    const ib = isoBtn(); if (ib) ctl.appendChild(ib);
  }

  function renderLegend() {
    const lg = $("#legend"); lg.innerHTML = "";
    TYPES.forEach(t => lg.appendChild(el("span", { class: "lg" }, [el("span", { class: "sw", style: "background:" + colorFor(t) }), t])));
    const d = el("span", { class: "lg lg-dir lg-2d", title: "2D view: when you hover a node" });
    d.innerHTML = '<svg width="22" height="8" aria-hidden="true"><line x1="1" y1="4" x2="21" y2="4" stroke="currentColor" stroke-width="2"/></svg>causes' +
      '<svg width="22" height="8" aria-hidden="true"><line x1="1" y1="4" x2="21" y2="4" stroke="currentColor" stroke-width="2" stroke-dasharray="4 3"/></svg>caused by' +
      '<svg width="26" height="10" aria-hidden="true"><path d="M2,9 C2,1 24,1 24,9" fill="none" stroke="currentColor" stroke-width="1.6" opacity=".6"/></svg>backward link';
    lg.appendChild(d);
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
    if (window.Interview && State.patientId && State.network) { Interview.open(); return; }   // Interview page
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
        weight: e.weight || 1, members: e.members || null });
    });
    const pset = {}; edges.forEach(e => pset[e.s.key + ">" + e.t.key] = true);
    edges.forEach(e => { e.recip = !!pset[e.t.key + ">" + e.s.key]; });

    const rad = n => n.group ? 16 + Math.min(24, (n.count || 1) * 1.2) : 9 + Math.min(14, n.deg * 2);
    const W = Math.max(320, container.clientWidth || 900);
    const H = Math.max(320, container.clientHeight || 560);
    const cx = W / 2, cy = H / 2;
    const K = Math.max(90, 0.80 * Math.sqrt((W * H) / Math.max(1, nodes.length)));
    const P = opts.positions || {};   // persistent id -> {x,y}
    let known = 0;
    nodes.forEach((n, i) => {
      if (P[n.key]) { n.x = P[n.key].x; n.y = P[n.key].y; n.pinned = true; known++; }  // already placed -> stays still
      else if (n.spawn) { const j = opts.layout === "flow" ? 0 : 1; n.x = n.spawn.x + j * (Math.random() * 40 - 20); n.y = n.spawn.y + j * (Math.random() * 40 - 20); }
      else { const a = 2 * Math.PI * i / nodes.length; n.x = cx + Math.cos(a) * Math.min(W, H) * 0.4; n.y = cy + Math.sin(a) * Math.min(W, H) * 0.4; }
      n.vx = 0; n.vy = 0;
    });
    // gentle settle when most nodes are already placed (avoids a big shake on expand)
    const startAlpha = (known >= 2 && known / nodes.length > 0.5) ? 0.32 : 1;
    // Flow layout: positions come from Flow.place (no physics); nodes glide from where they were
    // (or from their theme) to their slot. Free layout keeps the force simulation below.
    const FLOW = opts.layout === "flow" && opts.flowModel && window.Flow;
    const FW = Math.max(W, 900);            // flow columns need room for their labels; narrow screens zoom out
    const FH = 640;                         // fixed layout height: positions must not depend on how tall the panel is today
    const FLOW_MS = matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 560;
    if (FLOW) {
      const size = n => 2 * rad(n) + 17;
      const tgt = Flow.place(nodes, opts.flowModel, FW, FH, size);
      nodes.forEach(n => {
        const t = tgt[n.key] || { x: n.x, y: n.y };
        const from = P[n.key] || n.spawn || t;
        n.x0 = from.x; n.y0 = from.y; n.tx = t.x; n.ty = t.y; n.col = t.col; n.x = n.x0; n.y = n.y0; n.pinned = true;
      });
    }

    let svg, gE, gN, gH2, selected = null, tip = $("#g-tip"), raf = null, alpha = 1;
    let vb = { x: 0, y: 0, w: W, h: H };   // viewBox (zoom/pan)
    if (opts.view && opts.view.vb) vb = Object.assign({}, opts.view.vb);   // keep the camera across re-renders
    function setVB() { if (opts.view) opts.view.vb = Object.assign({}, vb); svg.setAttribute("viewBox", vb.x.toFixed(1) + " " + vb.y.toFixed(1) + " " + vb.w.toFixed(1) + " " + vb.h.toFixed(1)); }
    function svgPoint(ev) { const r = svg.getBoundingClientRect(); return { x: vb.x + (ev.clientX - r.left) / r.width * vb.w, y: vb.y + (ev.clientY - r.top) / r.height * vb.h }; }
    function zoomAt(p, f) { const nw = Math.min(W * 1.8, Math.max(W * 0.22, vb.w * f)); const s = nw / vb.w; vb.w = nw; vb.h = vb.h * s; vb.x = p.x - (p.x - vb.x) * s; vb.y = p.y - (p.y - vb.y) * s; setVB(); }
    function resetView() { vb = { x: 0, y: 0, w: W, h: H }; setVB(); }
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
    let t0 = 0, flowB = null;
    function tween(now) {
      if (!t0) t0 = now;
      const k = FLOW_MS ? Math.min(1, (now - t0) / FLOW_MS) : 1, e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      nodes.forEach(n => { n.x = n.x0 + (n.tx - n.x0) * e; n.y = n.y0 + (n.ty - n.y0) * e; });
      draw();
      if (k < 1) raf = requestAnimationFrame(tween); else { alpha = 0; settled(); }
    }
    function flowRoutes() {          // lanes for feedback arcs, from the final positions
      let y0 = Infinity, y1 = -Infinity;
      nodes.forEach(n => { y0 = Math.min(y0, n.ty - rad(n)); y1 = Math.max(y1, n.ty + rad(n) + 18); });
      const proxy = edges.map(e => ({ e, s: { x: e.s.tx, y: e.s.ty }, t: { x: e.t.tx, y: e.t.ty } }));
      Flow.routes(proxy, { y0, y1 }); proxy.forEach(p => { p.e.route = p.route; });
      let top = y0, bot = y1; proxy.forEach(p => { if (p.route.kind === "fb") { top = Math.min(top, p.route.y); bot = Math.max(bot, p.route.y); } });
      flowB = { top: top - 34, bot: bot + 8 };
      return { top, y0, y1 };
    }
    function geom(e) {
      let dx = e.t.x - e.s.x, dy = e.t.y - e.s.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      const ux = dx / d, uy = dy / d;
      // straight; reciprocal pairs get a tiny offset so both are visible
      const off = 0;   // two-way pairs are drawn as two bowed curves instead
      const nx = -uy * off, ny = ux * off;
      const sx = e.s.x + ux * rad(e.s) + nx, sy = e.s.y + uy * rad(e.s) + ny;
      const ex = e.t.x - ux * (rad(e.t) + 6) + nx, ey = e.t.y - uy * (rad(e.t) + 6) + ny;
      return [sx, sy, ex, ey];
    }
    function draw() {
      edges.forEach(e => {
        if (FLOW) { const d = Flow.path(e, rad(e.s), rad(e.t)); e._l.setAttribute("d", d); e._h.setAttribute("d", d); return; }
        const [sx, sy, ex, ey] = geom(e);
        if (e.recip) {   // A -> B and B -> A: two curves bowing to opposite sides
          const mx = (sx + ex) / 2, my = (sy + ey) / 2, dx = ex - sx, dy = ey - sy, L = Math.hypot(dx, dy) || 1, b = Math.min(28, L * 0.22);
          e._l.setAttribute("d", "M" + sx.toFixed(1) + "," + sy.toFixed(1) + " Q" + (mx - dy / L * b).toFixed(1) + "," + (my + dx / L * b).toFixed(1) + " " + ex.toFixed(1) + "," + ey.toFixed(1));
        } else e._l.setAttribute("d", "M" + sx.toFixed(1) + "," + sy.toFixed(1) + " L" + ex.toFixed(1) + "," + ey.toFixed(1));
        e._h.setAttribute("d", e._l.getAttribute("d")); });
      nodes.forEach(n => { n._g.setAttribute("transform", "translate(" + n.x.toFixed(1) + "," + n.y.toFixed(1) + ")"); P[n.key] = { x: n.x, y: n.y }; });
    }
    function render() {
      svg = document.createElementNS(NS, "svg"); svg.setAttribute("class", "graph-svg"); svg.setAttribute("viewBox", vb.x + " " + vb.y + " " + vb.w + " " + vb.h);
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
      gE = document.createElementNS(NS, "g"); gN = document.createElementNS(NS, "g");
      if (FLOW) {
        const b = flowRoutes(), gH = document.createElementNS(NS, "g"); gH.setAttribute("class", "flow-cols");
        const colW = FW / Flow.COLS.length, used = new Set(nodes.map(n => n.col));
        Flow.COLS.forEach((c, i) => {
          if (!used.has(i)) return;
          const tx = document.createElementNS(NS, "text"); tx.setAttribute("x", colW * (i + 0.5)); tx.setAttribute("y", b.top - 16);
          tx.setAttribute("text-anchor", "middle"); tx.textContent = (i ? "→ " : "") + (c === "Physiological Sensation" ? "Body" : c);
          tx.style.fill = colorFor(c); gH.appendChild(tx);
        });
        svg.appendChild(gH);
      }
      gH2 = document.createElementNS(NS, "g");
      svg.appendChild(gE); svg.appendChild(gH2); svg.appendChild(gN);

      edges.forEach(e => {
        const sl = slugOf(e.s.type);
        const l = document.createElementNS(NS, "path");
        l.setAttribute("class", "g-edge t-" + sl + (FLOW && e.route && e.route.kind !== "fwd" ? " fb" : "")); l.setAttribute("marker-end", "url(#arw-" + sl + ")");
        const h = document.createElementNS(NS, "path"); h.setAttribute("class", "g-hit");    // wide invisible target
        h.addEventListener("mouseenter", ev => { edgeTip(ev, e); if (!selected) hiEdge(e); }); h.addEventListener("mousemove", moveTip);
        h.addEventListener("mouseleave", () => { hideTip(); if (!selected) clearHi(); });
        h.addEventListener("pointerdown", ev => ev.stopPropagation());
        h.addEventListener("click", ev => { ev.stopPropagation(); hideTip(); if (opts.onEdgeClick) opts.onEdgeClick(e); });
        e._l = l; e._h = h; gE.appendChild(l); gH2.appendChild(h);
      });
      nodes.forEach((n, i) => {
        const g = document.createElementNS(NS, "g"); g.setAttribute("class", "g-node" + (n.group ? " grp" : ""));
        g.setAttribute("data-key", n.key);
        const c = document.createElementNS(NS, "circle"); c.setAttribute("r", rad(n)); c.setAttribute("fill", colorFor(n.type));
        const nm = opts.sentence ? sentenceLabel(n.name) : n.name;
        const full = n.group ? (nm + " (" + n.count + ")") : nm;
        const LM = FLOW ? Math.min(opts.shortLabel || 34, Math.floor((FW / Flow.COLS.length - 16) / 7.1)) : (opts.shortLabel || opts.labelMax || 34);
        let label = full;
        if (label.length > LM) label = label.slice(0, LM - 1) + "…";
        n.full = full; n.short = label;
        const tx = document.createElementNS(NS, "text"); tx.setAttribute("text-anchor", "middle"); tx.setAttribute("dy", rad(n) + 15); tx.textContent = label;
        if (n.group) tx.setAttribute("font-weight", "700");
        g.appendChild(c); g.appendChild(tx); n._t = tx;
        g.addEventListener("mouseenter", ev => { edges.forEach(e => e._l.classList.remove("sel")); highlight(n); nodeTip(ev, n); });
        g.addEventListener("mousemove", moveTip);
        g.addEventListener("mouseleave", () => { hideTip(); clearHi(); });   // returns to the selection, if any
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
      svg.addEventListener("pointerup", () => { if (panning && pmoved < 4 && opts.onBackgroundClick) opts.onBackgroundClick(); panning = false; });
      svg.addEventListener("pointerleave", () => { panning = false; });

      // zoom controls
      const zc = el("div", { class: "zoom-ctl" }, []);
      const zbtn = (t, fn) => { const b = el("button", { class: "zoom-btn", type: "button", title: t }, [t === "reset" ? "⤢" : t]); b.addEventListener("pointerdown", e => e.stopPropagation()); b.addEventListener("click", e => { e.stopPropagation(); fn(); }); return b; };
      zc.appendChild(zbtn("＋", () => { userView = true; zoomAt({ x: vb.x + vb.w / 2, y: vb.y + vb.h / 2 }, 0.8); }));
      zc.appendChild(zbtn("－", () => { userView = true; zoomAt({ x: vb.x + vb.w / 2, y: vb.y + vb.h / 2 }, 1.25); }));
      zc.appendChild(zbtn("reset", () => { userView = false; if (opts.fit) fitKeys(null, false); else resetView(); }));

      container.appendChild(svg); container.appendChild(zc); draw();
      if (FLOW) { alpha = 1; raf = requestAnimationFrame(tween); } else { alpha = startAlpha; raf = requestAnimationFrame(tick); }
      if (opts.fit && window.ResizeObserver) { let lw = container.clientWidth, lh = container.clientHeight;
        new ResizeObserver(() => { const w = container.clientWidth, h = container.clientHeight; if (Math.abs(w - lw) + Math.abs(h - lh) < 4) return; lw = w; lh = h;
          if (!userView && !alpha) { if (focusSet) fitKeys(focusSet, true); else fitKeys(null, false); } }).observe(container); }
    }
    function wrapLabel(m, on) {      // full text, wrapped onto <= 3 lines of ~28 characters
      const t = m._t; if (!t) return;
      if (!on) { t.textContent = m.short; return; }
      const words = m.full.split(/\s+/), lines = []; let cur = "";
      words.forEach(w => { if ((cur + " " + w).trim().length > 28 && cur) { lines.push(cur); cur = w; } else cur = (cur + " " + w).trim(); });
      if (cur) lines.push(cur);
      if (lines.length > 3) { lines.length = 3; lines[2] = lines[2].replace(/.{0,2}$/, "") + "…"; }
      t.textContent = "";
      const x = t.getAttribute("dx") || 0;
      lines.forEach((l, i) => { const sp = document.createElementNS(NS, "tspan"); sp.setAttribute("x", x); if (i) sp.setAttribute("dy", "1.15em"); sp.textContent = l; t.appendChild(sp); });
    }
    function highlight(n) {
      const conn = new Set([n]);
      edges.forEach(e => {
        const out = e.s === n, inn = e.t === n, on = out || inn;
        e._l.classList.toggle("hi", on); e._l.classList.toggle("dim", !on);
        e._l.classList.toggle("e-out", out); e._l.classList.toggle("e-in", inn && !out);
        if (on) { conn.add(e.s); conn.add(e.t); }
      });
      nodes.forEach(m => m._g.classList.toggle("dim", !conn.has(m)));
      if (opts.shortLabel) conn.forEach(m => { wrapLabel(m, true); m._g.classList.remove("lbl-off"); m._g.classList.toggle("lbl-full", m === n); });
      conn.forEach(m => { if (m !== n) gN.appendChild(m._g); }); gN.appendChild(n._g);   // labels of the focus on top
    }
    function clearHi() {
      edges.forEach(e => e._l.classList.remove("hi", "dim", "e-out", "e-in", "sel")); nodes.forEach(m => m._g.classList.remove("dim", "sel"));
      if (marked) applyMark(); else setLabels();
      if (selected) { if (selected.node) { highlight(selected.node); selected.node._g.classList.add("sel"); } else hiEdge(selected.edge); }
    }
    function hiEdge(e) {               // an edge and its two ends; everything else fades
      edges.forEach(x => { x._l.classList.toggle("dim", x !== e); x._l.classList.toggle("hi", x === e); if (x === e) x._l.classList.add("sel"); });
      nodes.forEach(m => m._g.classList.toggle("dim", m !== e.s && m !== e.t));
      if (opts.shortLabel) [e.s, e.t].forEach(m => { wrapLabel(m, true); m._g.classList.remove("lbl-off"); gN.appendChild(m._g); });
    }
    this.select = key => { const n = nodes.find(m => m.key === key); selected = n ? { node: n } : null; if (svg) clearHi(); return !!n; };
    this.selectEdge = (fromKey, toKey) => { const e = edges.find(x => x.s.key === fromKey && x.t.key === toKey); selected = e ? { edge: e } : null; if (svg) clearHi(); return !!e; };
    this.unselect = () => { selected = null; if (svg) clearHi(); };
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
      if (FLOW && flowB && !keys) { y0 = Math.min(y0, flowB.top); y1 = Math.max(y1, flowB.bot); }
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
      setLabels(); if (opts.flipLabels && !FLOW) flipLabels();
      if (focusSet) fitKeys(focusSet, true);
      else if (opts.fit && firstSettle && !userView) {
        // a kept camera stays put unless something new landed outside it (then zoom out to fit)
        const kept = opts.view && opts.view.kept;
        const inside = n => n.x - rad(n) >= vb.x && n.x + rad(n) <= vb.x + vb.w && n.y - rad(n) >= vb.y && n.y + rad(n) + 18 <= vb.y + vb.h;
        if (!kept || !nodes.every(inside)) fitKeys(null, false);
        if (opts.view) opts.view.kept = true;
      }
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
          "<div class='tt-ev'>click for details</div>";
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
        if (!FLOW && alpha < 0.06) { alpha = 0.25; raf = requestAnimationFrame(tick); } draw();
      });
      const up = () => {
        if (!on) return;
        on = false; n.fixed = false;
        if (moved < 5) {                                   // a click, not a drag
          const act = () => {
            if (n.group) { if (opts.onExpand) opts.onExpand(n); return; }        // theme -> expand
            hideTip(); if (opts.onNodeClick) opts.onNodeClick(n);   // Network: details panel · Interview: aligned replies
          };
          // extension views: a click during the layout animation is applied once the layout settles
          if (opts.fit && alpha > 0.05) { pendingAct = act; return; }
          act();
          return;   // single click on a member: no-op (hover shows the bright/dim highlight)
        }
        if (!FLOW) alpha = Math.max(alpha, 0.2);
      };
      g.addEventListener("pointerup", up); g.addEventListener("pointercancel", up);
    }
    this.render = render;
  }

  window.addEventListener("DOMContentLoaded", init);
  // Internals shared with static/ext.js (ANGEL demo extension; inert when no flag is on).
  const _ext = { api, apiT, stream, newStreamId, LiveStatus, liveBadge, el, $, $$, toast, show, State, ForceGraph, colorFor, TYPES, BASE, sentenceLabel };
  return { _ext, back, toggleTheme, goCreate, goExisting, reviewProfile, generateFromForm, generateFromExisting,
    restart, applyFilters, showGraph, toggleLongProfile, goChat, sendChat, toggleVoice, toggleMic, liveOn, liveMissing, liveOffMsg, watchExample, LIVE_OFF_MSG };
})();
