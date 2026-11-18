// The study page: form, what to measure, the practice plate with its guide, the note before the scored
// plates, four plates, the review of all four, sending the results.
// Colony Detection's decisions run in Python (quantacolony/study/web.py, through bridge.js). This file
// shows screens, turns mouse gestures into the same tool calls the desktop tools make
// (quantacolony/gui/tools.py), and sends the results file.

import { Engine } from "./bridge.js";
import { PlateView, isTyping } from "./canvas.js";

const CONFIG = window.STUDY_CONFIG || {};
const $ = id => document.getElementById(id);
const SETTLE_MS = 300;           // settings: detect once the control has been still this long
const POINTER_MS = 2000;
const ADVANCE_MS = 1200;         // a finished step stays this long, so its checkmarks are seen, then moves on
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
  state.plateView.ring = null;                                   // the step's part of the plate: four corner dots
  state.plateView.dots = !t.done && t.dots ? t.dots : [];
  state.plateView.dotsCapture = !t.done && !!t.dots_capture;
  state.plateView.arrow = !t.done && t.arrow ? t.arrow : [];
  state.plateView.draw();
  const p = state.plate;
  $("plate-label").textContent = p.practice ? "Practice plate"
    : `Plate ${p.number} of ${p.total}${p.reviewing ? " · changing after the review" : ""}`;
  for (const b of document.querySelectorAll(".mode")) b.classList.toggle("on", b.dataset.mode === v.mode);
  for (const [mode, panel] of Object.entries(PANELS)) $(panel).classList.toggle("hidden", mode !== v.mode);
  if (state.lastMode !== v.mode) {                               // the tool's group open, the other closed
    state.lastMode = v.mode;
    const auto = v.mode === "ModifyGlobalParameters";
    if (state.plate && !t.hide_outlines && ($("grp-auto").open || $("grp-manual").open)) {
      $("grp-auto").open = auto;
      $("grp-manual").open = !auto;
    }
  }
  placeSettings(v.mode === "AddManyColonies");
  $("count").textContent = t.hide_outlines ? "–" : v.count;
  $("chip").textContent = t.hide_outlines ? "The practice plate" : CHIPS[v.mode];
  setControls(v.settings);
  $("ctl-contour").checked = v.contour;
  if (!t.hide_outlines) $("count").textContent = v.count;
  $("count-detail").textContent = t.hide_outlines ? "" : `${v.count - v.added} automatic · ${v.added} added`;
  $("status").textContent = t.hide_outlines ? "" : v.status;
  $("btn-add").disabled = !!t.block_add;                        // the Snap step: right-click first
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
    if (d.open && d.classList.contains("group")) {              // Step 1 and Step 2: one open at a time
      for (const other of document.querySelectorAll("details.group[open]")) if (other !== d) other.open = false;
    }
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
                               color: state.view.mode === "DeleteManyColonies" ? "#FF6B5E" : "#7BF501" };   // delete red, add green
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
    if (e.key === "Enter" && mode.startsWith("Add")) {
      e.preventDefault();
      if (!(mode === "AddIndividualColonies" && state.guide && state.guide.block_add)) busy(() => act("add_pending"));
    }
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
    guide.innerHTML = `<div class="step"></div><h2>Measure this plate</h2><ul class="tasks"></ul>` +
      `<details class="context"><summary>More context</summary><p></p><p></p></details>`;
    guide.querySelector(".step").textContent = p.practice ? "" : `Plate ${p.number} of ${p.total}`;
    const tasks = [...t.tasks];
    if (p.reviewing) tasks[tasks.length - 1] = "Press Finish image to return to the review";
    for (const text of tasks) {
      const li = document.createElement("li");
      li.textContent = text;
      guide.querySelector("ul").appendChild(li);
    }
    guide.querySelectorAll(".context p")[0].textContent = t.goal;
    guide.querySelectorAll(".context p")[1].textContent = t.colors;
    return;
  }
  const keepOpen = state.contextOpen === t.key;               // the context toggle stays as the participant left it
  guide.innerHTML = `<div class="step"></div><h2></h2><ul class="checks"></ul><div class="buttons"></div>` +
    `<details class="context"><summary>More context</summary><p class="text"></p></details>`;
  guide.querySelector(".step").textContent = `Practice · step ${t.number} of ${t.total}`;
  guide.querySelector("h2").textContent = t.title;
  guide.querySelector(".text").textContent = t.text;
  const context = guide.querySelector("details.context");
  context.open = keepOpen;
  context.addEventListener("toggle", e => { e.stopPropagation(); state.contextOpen = context.open ? t.key : null; });
  const list = guide.querySelector("ul");
  const first = t.bullets.findIndex(b => !b.done);
  t.bullets.forEach((b, i) => {
    const li = document.createElement("li");
    li.className = b.info ? "info" : (b.done ? "done" : i === first ? "next" : "") + (b.restore ? " restore" : "");
    li.innerHTML = b.info ? `<span class="dot">•</span><span></span>` : `<span class="box">${b.done ? "✓" : ""}</span><span></span>`;
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
  // A step with tasks moves on by itself once every task is checked; a step without tasks has Next.
  const tracked = t.bullets.some(b => !b.info);
  if (!tracked) button("Next", "primary", () => act("pass_step", openSections()));
  if (tracked && t.met && t.key !== "finish") {
    const note = document.createElement("span");
    note.className = "moving-on";
    note.textContent = "Done. Moving on…";
    buttons.appendChild(note);
    if (state.advancing !== t.key) {
      state.advancing = t.key;
      setTimeout(() => {
        state.advancing = null;
        if (state.guide && state.guide.key === t.key) {
          act("pass_step", openSections());
          if (state.guide.key !== t.key) (state.passedKeys = state.passedKeys || []).push(t.key);
        }
      }, ADVANCE_MS);
    }
  }
  point(t.anchor);
  if (t.ring && t.ring.length && state.zoomedTo !== t.key) {   // bring the step's ringed part into view once
    state.zoomedTo = t.key;
    const view = state.plateView, side = Math.min(view.canvas.clientWidth, view.canvas.clientHeight) || 600;
    view.centerOn(t.ring[0], t.ring[1], Math.min(4 * view.fitScale, side / (3 * t.ring[2])));
  }
  if (t.key === "finish" && state.check && !state.check.passed) renderCheck(guide);
}

// The training check's result under the finish step: the two shares against the cutoffs, what was
// wrong, and a button that steps through the problems on the plate.
function renderCheck(guide) {
  const c = state.check;
  const pct = v => `${Math.round(100 * v)}%`;
  const box = document.createElement("div");
  box.className = "check";
  box.innerHTML = `<h3></h3><ul class="shares"><li></li><li></li></ul>
    <p class="small">Here are a few places to look again; the rest of the plate is up to you. Correct it, then press
    Finish image again.</p><button class="next-problem"></button>`;
  box.querySelector("h3").textContent = `Not passed yet (check ${c.attempt})`;
  const [a, b] = box.querySelectorAll(".shares li");
  a.textContent = `Colonies found: ${pct(c.found_share)}; needed ${pct(c.found_min)}`;
  b.textContent = `Outlines correct: ${pct(c.correct_share)}; needed ${pct(c.correct_min)}`;
  a.className = c.found_share >= c.found_min ? "ok" : "short";
  b.className = c.correct_share >= c.correct_min ? "ok" : "short";
  const next = box.querySelector(".next-problem");
  const problems = c.problems || [];
  next.textContent = problems.length ? `Show a place to look again (1 of ${problems.length})` : "No places to show";
  next.disabled = !problems.length;
  next.onclick = () => {                                     // moves the view there; nothing is drawn on the plate
    const k = state.problem = ((state.problem ?? -1) + 1) % problems.length;
    const [x, y, r, label] = problems[k];
    const view = state.plateView;
    view.centerOn(x, y, Math.max(3 * view.fitScale, Math.min(8 * view.fitScale, 60 / Math.max(r, 3))));
    next.textContent = `Show a place to look again (${(k + 1) % problems.length + 1} of ${problems.length})`;
    $("status").textContent = `Place ${k + 1} of ${problems.length}, at the center of the view: ${label}.`;
  };
  guide.appendChild(box);
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
  if (result.training) { state.check = result.training; state.problem = -1; }
  if (practice && result.training && !result.training.passed) { refresh(); return; }
  if (result.done) { await openReview(); return; }
  await openPlate();
  if (practice) showStartNote(result.training);
}

// ===== the note before the scored plates (study/model.py START_NOTE) ======================
function showStartNote(check) {
  $("note-passed").textContent = check
    ? `You passed the training check: ${check.found} of ${check.required} colonies found, ${check.correct} of `
      + `${check.outlines} outlines correct.` : "";
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
  show("screen-end");                                       // state.active stays on: leaving the page asks first
  $("end-title").textContent = "Sending your results…";
  $("end-text").textContent = "";
  $("send-progress").classList.remove("hidden");
  sendProgress(0.05, "Packing your measurements…");
  await new Promise(r => setTimeout(r, 30));                // let the page paint before Python runs
  const result = state.engine.call("results", complete);
  state.result = result;
  sendProgress(0.15, "Uploading…");
  const sent = await upload(result, f => sendProgress(0.15 + 0.85 * f, `Uploading… ${Math.round(100 * f)}%`))
    .catch(() => false);
  state.active = false;
  $("send-progress").classList.add("hidden");
  $("end-title").textContent = sent ? "Thank you!" : "One more step";
  $("end-text").textContent = sent
    ? (complete ? "Your measurements arrived safely. Thank you for the time and care you gave these plates: "
                  + "your results help show how consistently QuantaColony measures colonies from one person to the "
                  + "next. You can close this tab now."
                : "Thank you for your time. The plates you finished arrived safely, and they still help the study. "
                  + "You can close this tab now.")
    : "Your results could not be sent automatically. Download the file below and email it to "
      + (CONFIG.contact || "the person who gave you the study") + ".";
  $("btn-download").classList.toggle("hidden", sent);
}

function sendProgress(fraction, text) {
  $("send-bar-fill").style.width = `${Math.round(100 * Math.min(1, fraction))}%`;
  $("send-step").textContent = text;
}

// The upload with progress (fetch reports none): onProgress gets the share of bytes sent.
function upload(result, onProgress) {
  return new Promise(resolve => {
    if (!CONFIG.uploadUrl) { resolve(false); return; }
    const xhr = new XMLHttpRequest();
    xhr.open("POST", CONFIG.uploadUrl);
    xhr.setRequestHeader("Content-Type", "application/zip");
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(result.name));
    xhr.upload.onprogress = e => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => { onProgress(1); resolve(xhr.status >= 200 && xhr.status < 300); };
    xhr.onerror = xhr.ontimeout = () => resolve(false);
    xhr.timeout = 180000;
    xhr.send(new Blob([result.bytes], { type: "application/zip" }));
  });
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
