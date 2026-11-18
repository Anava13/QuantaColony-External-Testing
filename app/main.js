// The study page: form, what to measure, the practice plate with its guide, the note before the scored
// plates, six plates, the review of all six, sending the results.
// Colony Detection's decisions run in Python (quantacolony/study/web.py, through bridge.js). This file
// shows screens, turns mouse gestures into the same tool calls the desktop tools make
// (quantacolony/gui/tools.py), and sends the results file.

import { Engine } from "./bridge.js";
import { PlateView, isTyping } from "./canvas.js";

const CONFIG = window.STUDY_CONFIG || {};
const $ = id => document.getElementById(id);
const SETTLE_MS = 300;           // settings: detect once the control has been still this long
const POINTER_MS = 2000;
const EDGE_PX = 6;               // screen pixels around a drawn circle's edge that count as the edge
const PANELS = { ModifyGlobalParameters: "panel-settings", DeleteIndividualColonies: "panel-delete",
                 DeleteManyColonies: "panel-delete-region", AddIndividualColonies: "panel-add",
                 AddManyColonies: "panel-region" };
const CHIPS = { ModifyGlobalParameters: "Detect automatically · adjust settings on the left",
                DeleteIndividualColonies: "Delete colonies · click outlines to select them",
                DeleteManyColonies: "Delete a region · drag a box over outlines",
                AddIndividualColonies: "Add colonies · drag across a colony from edge to edge",
                AddManyColonies: "Detect in a region · drag a box over missed colonies" };

const state = { study: null, engine: new Engine(), ready: false, name: "", answers: {}, plate: null, view: null,
                active: false, settleTimer: null, lastPointer: 0, gesture: null, result: null };

// ===== screens ==========================================================================
function show(id) {
  for (const s of ["screen-form", "screen-concepts", "screen-detect", "screen-review", "screen-end"]) $(s).classList.toggle("hidden", s !== id);
  const detecting = id === "screen-detect";
  for (const b of ["btn-undo", "btn-redo", "btn-finish"]) $(b).classList.toggle("hidden", !detecting);
  if (!detecting) $("plate-label").textContent = "";
}

async function busy(work) {
  $("busy").classList.remove("hidden");
  await new Promise(r => setTimeout(r, 20));          // let the page paint before Python runs
  try { return await work(); } finally { $("busy").classList.add("hidden"); }
}

// ===== form =============================================================================
function renderForm() {
  const holder = $("questions");
  holder.innerHTML = "";
  for (const [key, question, choices] of state.study.questions) {
    const label = document.createElement("div");
    label.className = "field";
    label.textContent = question;
    const seg = document.createElement("div");
    seg.className = "seg-light";
    for (const choice of choices) {
      const b = document.createElement("button");
      b.textContent = choice;
      b.classList.toggle("on", state.answers[key] === choice);
      b.onclick = () => { state.answers[key] = choice; renderForm(); };
      seg.appendChild(b);
    }
    holder.append(label, seg);
  }
  const missing = state.study.questions.filter(([key]) => !state.answers[key]).map(([, q]) => q);
  const problems = [];
  if (!state.name.trim()) problems.push("Enter a name.");
  if (missing.length) problems.push("Still to answer: " + missing.join(", ") + ".");
  $("form-problems").textContent = problems.join(" ");
  $("btn-start").disabled = problems.length > 0 || !state.ready;
}

function progress(fraction, text) {
  $("load-note").textContent = fraction < 1 ? `${text} (${Math.round(fraction * 100)}%)` : "";
  $("loading-bar").style.width = `${Math.round(fraction * 100)}%`;
  $("loading-text").textContent = text;
}

async function start() {
  const seed = crypto.getRandomValues(new Uint32Array(1))[0];
  const result = await busyPage("Preparing the plates…", () =>
    state.engine.call("start", state.name, state.answers, seed));
  if (!result.ok) { $("form-problems").textContent = result.problems.join(" "); return; }
  state.active = true;
  show("screen-concepts");
}

async function busyPage(text, work) {
  $("loading-text").textContent = text;
  $("loading-bar").style.width = "100%";
  $("loading").classList.remove("hidden");
  await new Promise(r => setTimeout(r, 30));
  try { return await work(); } finally { $("loading").classList.add("hidden"); }
}

// ===== what to measure ==================================================================
function renderConcepts() {
  $("goal").textContent = state.study.goal;
  const holder = $("concepts");
  holder.innerHTML = "";
  for (const c of state.study.concepts) {
    const box = document.createElement("div");
    box.className = "concept";
    box.innerHTML = `<h3></h3><img alt=""><div class="heads"><span>The photo</span><span>Wrong (red)</span>` +
      `<span>Right (green)</span></div><p class="lead"></p>`;
    box.querySelector("h3").textContent = c.title;
    box.querySelector("img").src = c.file;
    box.querySelector("p").textContent = c.caption;
    holder.appendChild(box);
  }
}

async function startPractice() {
  state.engine.call("mark", "concepts_read");
  show("screen-detect");
  state.engine.call("open_next");
  await openPlate();
}

// ===== a plate ==========================================================================
async function openPlate() {
  state.plate = state.engine.call("plate");
  await state.plateView.setImage(state.engine.image(state.plate.width, state.plate.height));
  refresh();
}

function openSections() {
  return [...document.querySelectorAll("details[open]")].map(d => d.id);
}

function refresh() {
  const v = state.view = state.engine.call("view");
  const t = state.guide = state.engine.call("tutorial", openSections());
  state.plateView.setView(t.hide_outlines ? { ...v, colonies: [], pending: [], candidates: [] } : v);
  const ring = !t.done && t.key === "mass";                      // the ringed mass: its own step only
  if (ring !== !!state.plateView.ring) state.plateView.setRing(ring ? state.engine.call("target_ring") : null);
  const p = state.plate;
  $("plate-label").textContent = p.practice ? "Practice plate"
    : `Plate ${p.number} of ${p.total}${p.reviewing ? " · changing after the review" : ""}`;
  for (const b of document.querySelectorAll(".mode")) b.classList.toggle("on", b.dataset.mode === v.mode);
  for (const [mode, panel] of Object.entries(PANELS)) $(panel).classList.toggle("hidden", mode !== v.mode);
  placeSettings(v.mode === "AddManyColonies");
  $("count").textContent = t.hide_outlines ? "–" : v.count;
  $("chip").textContent = t.hide_outlines ? "The practice plate" : CHIPS[v.mode];
  setControls(v.settings);
  $("ctl-contour").checked = v.contour;
  if (!t.hide_outlines) $("count").textContent = v.count;
  $("count-detail").textContent = t.hide_outlines ? "" : `${v.count - v.added} automatic · ${v.added} added`;
  $("status").textContent = t.hide_outlines ? "" : v.status;
  $("btn-undo").disabled = v.undo === 0;
  $("btn-redo").disabled = v.redo === 0;
  renderGuide();
}

// While Detect in a region is on, the settings sit under it (they apply inside its box); otherwise under
// Step 1. Estimate settings works on the whole plate, so it stays out of the region's panel.
function placeSettings(inRegion) {
  const settings = $("panel-settings");
  const home = inRegion ? $("region-settings") : $("grp-auto");
  if (settings.parentElement !== home) home.appendChild(settings);
  if (inRegion) settings.classList.remove("hidden");
  $("btn-estimate").classList.toggle("hidden", inRegion);
  $("settings-moved").classList.toggle("hidden", !inRegion);
}

// ===== settings controls ===============================================================
function setControls(s) {
  for (const input of document.querySelectorAll("#panel-settings [data-field]")) {
    if (document.activeElement === input && input.type === "number") continue;
    const value = s[input.dataset.field];
    if (input.type === "checkbox") input.checked = !!value;
    else input.value = value;
    const out = input.parentElement.querySelector("output");
    if (out) out.textContent = Number(value).toFixed(input.dataset.field === "sense" ? 3 : 2);
  }
  for (const b of $("ctl-shape").children) b.classList.toggle("on", b.dataset.v === s.shape);
  for (const b of $("ctl-color").children) b.classList.toggle("on", b.dataset.v === s.color);
  $("split-rows").classList.toggle("hidden", !s.split);
  $("circular-only").classList.toggle("hidden", s.shape !== "Circular");     // Largest object: Circular only
}

function readControls() {
  const s = { ...state.view.settings };
  for (const input of document.querySelectorAll("#panel-settings [data-field]")) {
    s[input.dataset.field] = input.type === "checkbox" ? input.checked : Number(input.value);
  }
  return s;
}

function settingChanged(field, settle = true, trigger = "settings", override = {}) {
  state.engine.call("setting_changing", field);
  const out = document.querySelector(`[data-field="${field}"]`)?.parentElement.querySelector("output");
  const input = document.querySelector(`[data-field="${field}"]`);
  if (out && input) out.textContent = Number(input.value).toFixed(field === "sense" ? 3 : 2);
  clearTimeout(state.settleTimer);
  const apply = () => busy(() => {
    const result = state.engine.call("apply_settings", { ...readControls(), ...override }, trigger);
    refresh();
    if (!result.ok) $("status").textContent = result.error;
  });
  if (settle) state.settleTimer = setTimeout(apply, SETTLE_MS); else apply();
}

function wireControls() {
  for (const input of document.querySelectorAll("#panel-settings [data-field]")) {
    input.addEventListener("input", () => settingChanged(input.dataset.field, input.type !== "checkbox"));
  }
  for (const b of $("ctl-shape").children) b.onclick = () => settingChanged("detection_type", false, "shape", { shape: b.dataset.v });
  for (const b of $("ctl-color").children) b.onclick = () => settingChanged("level", false, "settings", { color: b.dataset.v });
  $("btn-estimate").onclick = () => busy(() => { state.engine.call("estimate"); refresh(); });
  for (const b of document.querySelectorAll(".mode")) b.onclick = () => setMode(b.dataset.mode);
  $("btn-delete").onclick = $("btn-delete-region").onclick = () => act("delete_selected");
  $("btn-add").onclick = () => busy(() => act("add_pending"));
  $("btn-add-region").onclick = () => act("add_pending");
  for (const b of document.querySelectorAll(".btn-clear")) b.onclick = () => act("clear");
  $("ctl-contour").onchange = e => busy(() => act("set_contour", e.target.checked));
  $("btn-undo").onclick = () => act("undo");
  $("btn-redo").onclick = () => act("redo");
  $("btn-finish").onclick = finishPlate;
  $("btn-zoom-in").onclick = () => state.plateView.zoomBy(1.4);
  $("btn-zoom-out").onclick = () => state.plateView.zoomBy(1 / 1.4);
  $("btn-fit").onclick = () => state.plateView.fit();
  for (const d of document.querySelectorAll("details")) d.addEventListener("toggle", () => {
    if (d.open && d.classList.contains("sec")) {                // one settings section open at a time
      for (const other of document.querySelectorAll("details.sec[open]")) if (other !== d) other.open = false;
    }
    if (state.plate) refresh();
  });
  $("btn-stop").onclick = () => { if (confirm("Stop the study? The plates you finished are sent; the rest are left out.")) endStudy(false); };
}

function act(name, ...args) { state.engine.call(name, ...args); refresh(); }
async function setMode(mode) {
  if (mode === state.view.mode) return;
  if (await settleUnadded("mode")) act("set_mode", mode);
}

// Drawn circles and region outlines count only once added. Before Finish image or a tool change drops
// them, ask: add them, discard them, or go back. Resolves true when the action can go ahead.
async function settleUnadded(before) {
  const u = state.engine.call("unadded");
  const n = u.drawn + u.region;
  if (!n) return true;
  const what = u.drawn ? (u.drawn === 1 ? "1 drawn circle" : `${u.drawn} drawn circles`)
                       : (u.region === 1 ? "1 outline found in the region" : `${u.region} outlines found in the region`);
  $("unadded-text").textContent = `${what[0].toUpperCase() + what.slice(1)} ${n === 1 ? "is" : "are"} not added to the `
    + `colonies yet, so ${n === 1 ? "it does" : "they do"} not count. Add ${n === 1 ? "it" : "them"} to the accepted `
    + `detections before you ${before === "finish" ? "finish this plate" : "change tools"}?`;
  const dialog = $("unadded");
  const choice = await new Promise(resolve => {
    for (const b of dialog.querySelectorAll("button")) b.onclick = () => { dialog.close(); resolve(b.value); };
    dialog.oncancel = () => resolve("back");
    dialog.showModal();
  });
  if (choice === "back") return false;
  await busy(() => act("resolve_unadded", choice, before));
  return true;
}

// ===== tools (quantacolony/gui/tools.py) ================================================
const tools = {
  press(x, y, button) {
    const mode = state.view.mode;
    if (mode === "DeleteIndividualColonies" && button === "left") { act("delete_click", x, y); return; }
    if (mode === "DeleteManyColonies" || mode === "AddManyColonies") {
      state.gesture = { kind: "box", x0: x, y0: y };
      return;
    }
    if (mode === "AddIndividualColonies") {
      const tolerance = state.plateView.screenToImage(EDGE_PX);
      const [index, part] = state.engine.call("hit_circle", x, y, tolerance);
      if (button === "right") { if (index >= 0) act("remove_circle", index, "right"); return; }
      if (index >= 0) {
        const c = state.view.pending[index];
        state.gesture = { kind: part === "edge" ? "resize" : "move", index, x0: x, y0: y, cx: c.x, cy: c.y, r: c.r };
      } else {
        state.gesture = { kind: "draw", index: state.engine.call("add_circle", x, y, 0.5), x0: x, y0: y };
      }
      refresh();
    }
  },
  move(x, y, pressed) {
    const now = performance.now();
    if (now - state.lastPointer > POINTER_MS) { state.lastPointer = now; state.engine.call("pointer"); }
    const g = state.gesture;
    if (!pressed || !g) return;
    if (g.kind === "box") {
      state.plateView.drag = { x0: g.x0, y0: g.y0, x1: x, y1: y,
                               color: state.view.mode === "DeleteManyColonies" ? "#FF6B5E" : "#ECEEE6" };
      state.plateView.draw();
      return;
    }
    const [cx, cy, r] = circleGeometry(g, x, y);
    state.engine.call("update_circle", g.index, cx, cy, r, false);
    state.view = state.engine.call("view");
    state.plateView.setView(state.view);
  },
  release(x, y, button) {
    const g = state.gesture;
    state.gesture = null;
    if (!g || button !== "left") return;
    if (g.kind === "box") {
      state.plateView.drag = null;
      if (Math.abs(x - g.x0) < 1 || Math.abs(y - g.y0) < 1) { state.plateView.draw(); return; }
      if (state.view.mode === "DeleteManyColonies") act("select_rect", g.x0, g.y0, x, y);
      else busy(() => act("start_region", g.x0, g.y0, x, y));
      return;
    }
    const moved = Math.hypot(x - g.x0, y - g.y0);
    if (g.kind === "draw" && moved < state.plateView.screenToImage(2)) { act("remove_circle", g.index, "click"); return; }
    const [cx, cy, r] = circleGeometry(g, x, y);
    busy(() => act("update_circle", g.index, cx, cy, r, true));
  },
};

function circleGeometry(g, x, y) {
  if (g.kind === "draw") return [(g.x0 + x) / 2, (g.y0 + y) / 2, Math.max(Math.hypot(x - g.x0, y - g.y0) / 2, 0.5)];
  if (g.kind === "move") return [g.cx + x - g.x0, g.cy + y - g.y0, g.r];
  return [g.cx, g.cy, Math.max(Math.hypot(x - g.cx, y - g.cy), 0.5)];
}

function wireKeys() {
  window.addEventListener("keydown", e => {
    if (!state.view || $("screen-detect").classList.contains("hidden") || isTyping(e)) return;
    const mode = state.view.mode;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); act(e.shiftKey ? "redo" : "undo"); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") { e.preventDefault(); act("redo"); return; }
    if (e.key >= "1" && e.key <= "5" && !e.ctrlKey && !e.metaKey) { setMode(Object.keys(PANELS)[Number(e.key) - 1]); return; }
    if (!$("start-note").classList.contains("hidden")) return;
    if (e.key === "Escape") { act("clear"); return; }
    if ((e.key === "Delete" || e.key === "Backspace") && mode.startsWith("Delete")) { e.preventDefault(); act("delete_selected"); return; }
    if (e.key === "Enter" && mode.startsWith("Add")) { e.preventDefault(); busy(() => act("add_pending")); }
  });
  document.addEventListener("visibilitychange", () => { if (state.plate && state.active) state.engine.call("focus", !document.hidden); });
  window.addEventListener("beforeunload", e => { if (state.active) { e.preventDefault(); e.returnValue = ""; } });
}

// ===== the guide (study/guide.py STEPS) ================================================
function renderGuide() {
  const t = state.guide;
  const guide = $("guide");
  if (t.done) {
    point("");
    const p = state.plate;
    guide.innerHTML = `<div class="step"></div><h2>Measure every colony</h2><p></p><p></p>` +
      `<p></p>`;
    guide.querySelector(".step").textContent = p.practice ? "" : `Plate ${p.number} of ${p.total}`;
    guide.querySelectorAll("p")[0].textContent = t.goal;
    guide.querySelectorAll("p")[1].textContent = t.colors;
    guide.querySelectorAll("p")[2].textContent = p.reviewing
      ? "Press Finish image when the outlines match the colonies to return to the review."
      : "Press Finish image when the outlines match the colonies. The next plate opens on its own.";
    return;
  }
  guide.innerHTML = `<div class="step"></div><h2></h2><p class="text"></p><ul class="checks"></ul><div class="buttons"></div>`;
  guide.querySelector(".step").textContent = `Practice · step ${t.number} of ${t.total}`;
  guide.querySelector("h2").textContent = t.title;
  guide.querySelector(".text").textContent = t.text;
  const list = guide.querySelector("ul");
  const first = t.bullets.findIndex(b => !b.done);
  t.bullets.forEach((b, i) => {
    const li = document.createElement("li");
    li.className = b.done ? "done" : i === first ? "next" : "";
    li.innerHTML = `<span class="box">${b.done ? "✓" : ""}</span><span></span>`;
    li.lastChild.textContent = b.text;
    list.appendChild(li);
  });
  const buttons = guide.querySelector(".buttons");
  const button = (text, cls, handler, disabled = false) => {
    const el = document.createElement("button");
    el.textContent = text;
    if (cls) el.className = cls;
    el.disabled = disabled;
    el.onclick = handler;
    buttons.appendChild(el);
  };
  if (t.key !== "finish") button("Next", "primary", () => act("pass_step", openSections()), !t.met);
  if (!t.met && t.key !== "finish" && t.bullets.length) button("Skip this step", "link", () => act("skip_step"));
  point(t.anchor);
  if (t.key === "mass" && state.zoomedTo !== "mass") {          // bring the ringed mass into view once
    state.zoomedTo = "mass";
    const ring = state.engine.call("target_ring");
    if (ring.length) state.plateView.centerOn(ring[0], ring[1], 4 * state.plateView.fitScale);
  }
}

// The control the next unchecked bullet uses stays outlined; inside a collapsed group or section, the
// outermost collapsed one's title is outlined, so the participant opens it first.
function point(anchor) {
  const target = anchorElement(anchor);
  if (state.pointed === target) return;
  if (state.pointed) state.pointed.classList.remove("spot");
  state.pointed = target;
  if (target) {
    target.classList.add("spot");
    target.scrollIntoView({ block: "nearest" });
  }
}

function anchorElement(anchor) {
  if (!anchor || anchor === "plate") return null;
  let el = $(anchor);
  if (!el) return null;
  if (el.tagName === "DETAILS") el = el.querySelector("summary");
  let closed = null;
  for (let d = el.parentElement; d; d = d.parentElement) {
    if (d.tagName === "DETAILS" && !d.open) closed = d;
  }
  return closed ? closed.querySelector("summary") : el;
}

// ===== finishing ========================================================================
async function finishPlate() {
  if (!(await settleUnadded("finish"))) return;
  const practice = state.plate.practice;
  const result = await busy(() => state.engine.call("finish_image"));
  if (result.done) { await openReview(); return; }
  await openPlate();
  if (practice) showStartNote();
}

// ===== the note before the scored plates (study/model.py START_NOTE) ======================
function showStartNote() {
  const list = $("note-points");
  list.innerHTML = "";
  for (const text of state.engine.call("start_note")) {
    const li = document.createElement("li");
    li.innerHTML = `<label><input type="checkbox"><span></span></label>`;
    li.querySelector("span").textContent = text;
    list.appendChild(li);
  }
  const boxes = [...list.querySelectorAll("input")];
  const update = () => { $("btn-note").disabled = !boxes.every(b => b.checked); };
  for (const b of boxes) b.onchange = update;
  update();
  $("btn-note").onclick = () => {
    state.engine.call("note_accepted");
    $("start-note").classList.add("hidden");
  };
  $("start-note").classList.remove("hidden");
}

// ===== the review before sending ===========================================================
async function openReview() {
  show("screen-review");
  const plates = await busyPage("Preparing your plates…", () => state.engine.call("review"));
  const grid = $("review-grid");
  for (const old of grid.querySelectorAll("img")) URL.revokeObjectURL(old.src);
  grid.innerHTML = "";
  for (const p of plates) {
    const card = document.createElement("div");
    card.className = "review-card";
    card.innerHTML = `<img alt=""><div class="row"><span></span><button>Change this plate</button></div>`;
    const url = URL.createObjectURL(new Blob([p.image], { type: "image/jpeg" }));
    const img = card.querySelector("img");
    img.src = url;
    img.alt = `Plate ${p.number}`;
    img.onclick = () => { const big = $("plate-large"); big.querySelector("img").src = url; big.showModal(); };
    card.querySelector("span").textContent = `Plate ${p.number} · ${p.count} colonies`;
    card.querySelector("button").onclick = () => reopenPlate(p.value);
    grid.appendChild(card);
  }
  $("review-ok").checked = false;
  $("btn-send").disabled = true;
}

async function reopenPlate(value) {
  show("screen-detect");
  await busy(() => state.engine.call("reopen", value));
  await openPlate();
}

async function endStudy(complete) {
  state.active = false;
  show("screen-end");
  $("end-title").textContent = "Sending your results…";
  $("end-text").textContent = "";
  const result = await busyPage("Saving your results…", () => state.engine.call("results", complete));
  state.result = result;
  const sent = await upload(result).catch(() => false);
  $("end-title").textContent = sent ? "Thank you" : "One more step";
  $("end-text").textContent = sent
    ? (complete ? "Every plate is measured, and your results were sent. You can close this tab."
                : "The plates you finished were sent. You can close this tab.")
    : "Your results could not be sent automatically. Download the file below and email it to "
      + (CONFIG.contact || "the person who gave you the study") + ".";
  $("btn-download").classList.toggle("hidden", sent);
}

async function upload(result) {
  if (!CONFIG.uploadUrl) return false;
  const response = await fetch(CONFIG.uploadUrl, {
    method: "POST", body: new Blob([result.bytes], { type: "application/zip" }),
    headers: { "Content-Type": "application/zip", "X-File-Name": encodeURIComponent(result.name) },
  });
  return response.ok;
}

function download() {
  const r = state.result;
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([r.bytes], { type: "application/zip" }));
  link.download = r.name;
  link.click();
}

// ===== start ============================================================================
async function main() {
  state.study = await (await fetch("study.json", { cache: "no-cache" })).json();
  state.plateView = new PlateView($("plate"), tools);
  $("name").addEventListener("input", e => { state.name = e.target.value; renderForm(); });
  $("btn-start").onclick = start;
  $("btn-practice").onclick = startPractice;
  $("btn-download").onclick = download;
  $("review-ok").onchange = e => { $("btn-send").disabled = !e.target.checked; };
  $("btn-send").onclick = () => { state.engine.call("confirm_review"); endStudy(true); };
  $("btn-info").onclick = () => { $("info").showModal(); if (state.active) state.engine.call("info_opened"); };
  wireControls();
  wireKeys();
  renderConcepts();
  show("screen-form");
  renderForm();
  try {
    await state.engine.load(progress);
    state.ready = true;
  } catch (error) {
    $("load-note").textContent = "The study could not load in this browser: " + error.message;
  }
  renderForm();
}

main();
window.__study = state;            // for automated checks
