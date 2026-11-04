// Node DOM-stub test for the live perform page (perform.html).
//
// Loads perform.html's main <script> into a vm sandbox backed by a mini-DOM,
// then verifies:
//   (1) renderState maps the `perform` snapshot fields to the DOM (beat clock,
//       velocity, multiplier, deadline/time-to-arrive, late flag, live move).
//   (2) the CC-A velocity slider sends a NORMALIZED perform_velocity command (0-1).
//   (3) the CC-B multiplier buttons send a perform_multiplier command with the
//       chosen multiplier.
//   (4) an absent `perform` field renders the idle state.
//
// Run: node tests/js/test_perform_page.js
"use strict";
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const RDIR = path.join(__dirname, "..", "..");

const html = fs.readFileSync(path.join(RDIR, "perform.html"), "utf8");
const scripts = html.match(/<script[^>]*>([\s\S]*?)<\/script>/g) || [];
let SRC = null;
for (const s of scripts) {
  const inner = s.replace(/^<script[^>]*>/, "").replace(/<\/script>$/, "");
  if (inner.includes("renderState")) { SRC = inner; break; }
}
if (!SRC) { console.error("could not find the main script"); process.exit(2); }

// ---- Mini-DOM (only the surface perform.html touches) -------------------------
class El {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attrs = {}; this.props = {};
    this.listeners = {}; this.style = {}; this.dataset = {}; this._text = ""; this.className = "";
  }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text; }
  set value(v) { this.props.value = v; }
  get value() { return this.props.value !== undefined ? this.props.value : (this.attrs.value != null ? this.attrs.value : ""); }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  appendChild(c) { this.children.push(c); return c; }
}
const IDX = {};
function makeEl(tag, id) { const e = new El(tag); e.attrs.id = id; IDX[id] = e; return e; }

// Every id perform.html references via $("...").
const IDS = ["dot", "status", "host", "reconnect", "clockSource", "bpm", "beat",
  "beatPhase", "beatFill", "beatDuration", "velocityVal", "velocity", "velocityDeg",
  "multChips", "multVal", "baseBeats", "deadline", "lateBox", "tta", "lateHint",
  "zone", "action", "moving"];
for (const id of IDS) makeEl("div", id);

// The multiplier buttons: 9 children of multChips, each with a data-m.
const multChipsEl = IDX["multChips"];
const MULTS = [0.0625, 0.125, 0.25, 0.5, 1, 2, 4, 8, 16];
for (const m of MULTS) { const c = new El("button"); c.dataset.m = String(m); multChipsEl.appendChild(c); }

const document = {
  getElementById: (id) => IDX[id] || null,
  createElement: (t) => new El(t),
  body: new El("body"),
};

// ---- Capturing WebSocket stub ------------------------------------------------
const sentCmds = [];
class FakeWS {
  constructor(url) { this.url = url; this.readyState = 1; }
  send(data) { sentCmds.push(JSON.parse(data)); }
  close() {}
}
FakeWS.OPEN = 1;

const sandbox = {
  console,
  document,
  WebSocket: FakeWS,
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ velocity_max_deg_s: 30.0 }) }),
  location: { hostname: "localhost" },
  setTimeout, clearTimeout,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const ctx = vm.createContext(sandbox);
try {
  vm.runInContext(SRC, ctx, { filename: "perform.js" });
} catch (e) {
  console.error("LOAD ERROR:", e.message);
  process.exit(3);
}

let failures = 0;
function assert(cond, msg) { if (!cond) { failures++; console.log("FAIL:", msg); } else { console.log("ok:", msg); } }

// --- Test 1: renderState maps the perform snapshot to the DOM ----------------
vm.runInContext(`renderState({
  type: "state",
  zone: "resting", action: "breathe", moving: true,
  perform: {
    velocity: 0.5, multiplier: 4, base_beats: 4, deadline: 2.4,
    time_to_arrive: 1.2, late: false, beat: 12.25, beat_index: 12,
    beat_phase: 0.25, bpm: 130, beat_duration: 0.4615, clock_source: "internal"
  }
});`, ctx);
assert(IDX["zone"].textContent === "resting", "zone shown: " + IDX["zone"].textContent);
assert(IDX["action"].textContent === "breathe", "action shown: " + IDX["action"].textContent);
assert(IDX["moving"].textContent === "yes", "moving shown: " + IDX["moving"].textContent);
assert(IDX["clockSource"].textContent === "internal", "clock source: " + IDX["clockSource"].textContent);
assert(IDX["bpm"].textContent === "130.0", "bpm: " + IDX["bpm"].textContent);
assert(IDX["beat"].textContent === "12.25", "beat: " + IDX["beat"].textContent);
assert(IDX["beatPhase"].textContent === "0.250", "beat phase: " + IDX["beatPhase"].textContent);
assert(IDX["beatFill"].style.width === "25%", "beat fill width: " + IDX["beatFill"].style.width);
assert(IDX["velocityVal"].textContent === "0.50", "velocity value: " + IDX["velocityVal"].textContent);
assert(IDX["velocityDeg"].textContent.startsWith("15.0"), "velocity deg/s (0.5 x 30): " + IDX["velocityDeg"].textContent);
assert(IDX["multVal"].textContent === "4x", "multiplier value: " + IDX["multVal"].textContent);
assert(multChipsEl.children[6].className === "active", "4x chip highlighted active");
assert(multChipsEl.children[4].className === "", "1x chip not active");
assert(IDX["baseBeats"].textContent === "4", "base beats: " + IDX["baseBeats"].textContent);
assert(IDX["deadline"].textContent === "2.400 s", "deadline: " + IDX["deadline"].textContent);
assert(IDX["tta"].textContent === "1.200 s", "time-to-arrive: " + IDX["tta"].textContent);
assert(IDX["lateBox"].className === "lateBox ok", "on-time box class: " + IDX["lateBox"].className);
assert(IDX["lateHint"].textContent === "on time", "on-time hint: " + IDX["lateHint"].textContent);

// --- Test 2: late state flags the box + hint ----------------------------------
vm.runInContext(`renderState({
  type: "state",
  zone: "resting", action: "reach", moving: true,
  perform: {
    velocity: 0.1, multiplier: 1, base_beats: 4, deadline: 1.0,
    time_to_arrive: 2.5, late: true, beat: 0, beat_index: 0,
    beat_phase: 0, bpm: 130, beat_duration: 0.4615, clock_source: "midi"
  }
});`, ctx);
assert(IDX["lateBox"].className === "lateBox late", "late box class: " + IDX["lateBox"].className);
assert(IDX["lateHint"].textContent.includes("LATE"), "late hint: " + IDX["lateHint"].textContent);
assert(IDX["tta"].textContent === "2.500 s", "late time-to-arrive: " + IDX["tta"].textContent);
assert(IDX["clockSource"].textContent === "midi", "midi clock source: " + IDX["clockSource"].textContent);

// --- Test 3: an absent perform field renders the idle state -------------------
vm.runInContext(`renderState({ type: "state", zone: "resting", action: "idle", moving: false });`, ctx);
assert(IDX["lateHint"].textContent === "not performing", "idle hint: " + IDX["lateHint"].textContent);
assert(IDX["lateBox"].className === "lateBox ok", "idle box class: " + IDX["lateBox"].className);

// --- Test 4: velocity slider sends a NORMALIZED perform_velocity --------------
IDX["velocity"].value = "75";
IDX["velocity"].listeners["input"][0]();
const velCmd = sentCmds[sentCmds.length - 1];
assert(velCmd.cmd === "perform_velocity", "velocity cmd name: " + velCmd.cmd);
assert(velCmd.value === 0.75, "velocity normalized to 0-1: " + velCmd.value);

// --- Test 5: multiplier button sends perform_multiplier -----------------------
multChipsEl.children[7].listeners["click"][0](); // the 8x button
const multCmd = sentCmds[sentCmds.length - 1];
assert(multCmd.cmd === "perform_multiplier", "multiplier cmd name: " + multCmd.cmd);
assert(multCmd.multiplier === 8, "multiplier value: " + multCmd.multiplier);

// --- Test 6: fractional multiplier button sends the parsed value -------------
multChipsEl.children[3].listeners["click"][0](); // the 1/2 (0.5) button
const fracCmd = sentCmds[sentCmds.length - 1];
assert(fracCmd.cmd === "perform_multiplier", "fractional multiplier cmd name: " + fracCmd.cmd);
assert(fracCmd.multiplier === 0.5, "fractional multiplier value: " + fracCmd.multiplier);

console.log(failures === 0 ? "ALL_TESTS_PASSED" : "FAILURES=" + failures);
if (failures > 0) process.exit(1);
