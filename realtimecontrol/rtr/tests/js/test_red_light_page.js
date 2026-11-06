// Node DOM-stub test for the red-light overlay in the render page (render.html).
//
// Loads render.html's main <script> into a vm sandbox backed by a mini-DOM, then
// verifies:
//   (1) normalizeRedLightClient merges a partial config over the defaults + clamps.
//   (2) a state frame carrying red_light updates the overlay config.
//   (3) a state frame carrying perform.beat/bpm resyncs the beat clock.
//   (4) rlFrame extrapolates the beat between frames (free-running at bpm).
//
// Run: node tests/js/test_red_light_page.js
"use strict";
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const RDIR = path.join(__dirname, "..", "..");

const html = fs.readFileSync(path.join(RDIR, "render.html"), "utf8");
const scripts = html.match(/<script[^>]*>([\s\S]*?)<\/script>/g) || [];
let SRC = null;
for (const s of scripts) {
  const inner = s.replace(/^<script[^>]*>/, "").replace(/<\/script>$/, "");
  if (inner.includes("makeHydra")) { SRC = inner; break; }
}
if (!SRC) { console.error("could not find the main script"); process.exit(2); }

// ---- Mini-DOM (the surface render.html touches) ------------------------------
// A 2d context stub (no-op methods, settable props) so drawCamOverlay and
// drawRedLight do not throw; getContext("2d") returns the stub so
// setupRedLight succeeds.
const makeCtxStub = () => new Proxy(function(){}, {
  get: (t, prop) => (typeof prop === "string" ? makeCtxStub() : undefined),
  set: () => true,
  apply: () => makeCtxStub(),
});
const ctx2d = makeCtxStub();
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.props = {}; this.listeners = {}; this.style = {}; this.dataset = {}; this._text = ""; this.className = ""; }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text; }
  set value(v) { this.props.value = v; }
  get value() { return this.props.value !== undefined ? this.props.value : (this.attrs.value != null ? this.attrs.value : ""); }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  appendChild(c) { this.children.push(c); return c; }
  getContext(type) { return type === "webgl" ? undefined : ctx2d; }
}
const IDX = {};
function makeEl(tag, id) { const e = new El(tag); e.attrs.id = id; IDX[id] = e; return e; }
const IDS = ["stage", "hud", "dot", "status", "zone", "mode", "action", "patchSlot", "midiStatus", "codeOverlay", "camOverlay", "redLight"];
for (const id of IDS) makeEl("div", id);

const document = {
  readyState: "complete",
  getElementById: (id) => IDX[id] || null,
  createElement: (t) => new El(t),
  body: new El("body"),
  addEventListener() {},
  removeEventListener() {},
};

// ---- Capturing WebSocket stub ------------------------------------------------
const wsInstances = [];
class FakeWS {
  constructor(url) { this.url = url; this.readyState = 1; this.onopen = null; this.onclose = null; this.onmessage = null; wsInstances.push(this); }
  send(data) {}
  close() {}
}
FakeWS.OPEN = 1;

// ---- Controllable fetch mock -------------------------------------------------
let fetchTable = {};
function fakeFetch(url) {
  const entry = fetchTable[String(url)];
  if (entry && entry.ok) return Promise.resolve({ ok: true, status: entry.status || 200, json: () => Promise.resolve(entry.data) });
  return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
}

// ---- Controllable clock ------------------------------------------------------
let perfNow = 0;

const sandbox = {
  console,
  document,
  WebSocket: FakeWS,
  fetch: (u) => fakeFetch(u),
  location: { hostname: "localhost" },
  setTimeout, clearTimeout,
  performance: { now: () => perfNow },
  requestAnimationFrame: () => { /* no-op: the test drives rlFrame manually */ },
  cancelAnimationFrame: () => {},
  navigator: { userAgent: "test" },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.addEventListener = () => {};
sandbox.removeEventListener = () => {};
sandbox.innerWidth = 1200;
sandbox.innerHeight = 800;

const ctx = vm.createContext(sandbox);
try {
  vm.runInContext(SRC, ctx, { filename: "render.js" });
} catch (e) {
  console.error("LOAD ERROR:", e.message);
  process.exit(3);
}
vm.runInContext("globalThis.__T = { normalizeRedLightClient, rlFrame };", ctx);
const T = sandbox.__T;
const getVar = (n) => vm.runInContext(n, ctx);
const setVar = (n, v) => { vm.runInContext(n + " = " + JSON.stringify(v), ctx); };

let failures = 0;
function assert(cond, msg) { if (!cond) { failures++; console.log("FAIL:", msg); } else { console.log("ok:", msg); } }

(async () => {
  // --- normalizeRedLightClient: merge + clamp ---
  const n0 = T.normalizeRedLightClient(null);
  assert(n0.enabled === false && n0.x === 0.5 && n0.y === 0.5, "normalize(null) -> defaults");
  assert(n0.bar_w === 1 && n0.bar_h === 160 && n0.radius === 80 && n0.morph === true && n0.per_beat === 1, "normalize(null) -> default sizes");
  const np = T.normalizeRedLightClient({ enabled: true, x: 0.25 });
  assert(np.enabled === true && np.x === 0.25, "normalize partial keeps enabled + x");
  assert(np.y === 0.5 && np.bar_w === 1, "normalize partial fills missing from defaults");
  const nc = T.normalizeRedLightClient({ x: 5, y: -2, bar_w: -3, bar_h: 40, radius: 12, per_beat: 0.5 });
  assert(nc.x === 1 && nc.y === 0, "normalize clamps x->1 y->0");
  assert(nc.bar_w === 1, "normalize clamps bar_w->1");
  assert(nc.bar_h === 40 && nc.radius === 12 && nc.per_beat === 0.5, "normalize keeps in-range values");

  // --- state frame carrying red_light updates the overlay config ---
  const ws = wsInstances[0];
  assert(!!ws, "connect() created a WebSocket");
  ws.onmessage({ data: JSON.stringify({
    type: "state", zone: "rest", mode: null, action: null,
    red_light: { enabled: true, x: 0.3, y: 0.7, bar_w: 2, bar_h: 120, radius: 60, morph: true, per_beat: 2 },
  }) });
  let rl = getVar("RED_LIGHT");
  assert(rl.enabled === true && rl.x === 0.3 && rl.y === 0.7, "state red_light updates RED_LIGHT (enabled/x/y)");
  assert(rl.bar_w === 2 && rl.bar_h === 120 && rl.radius === 60 && rl.per_beat === 2, "state red_light updates RED_LIGHT (sizes/per_beat)");

  // --- a state frame carrying perform.beat/bpm resyncs the beat clock ---
  ws.onmessage({ data: JSON.stringify({
    type: "state", zone: "rest", mode: null, action: null,
    perform: { beat: 10.5, bpm: 130, clock_source: "internal" },
  }) });
  let clk = getVar("rlClock");
  assert(clk.beat === 10.5, "state perform resyncs rlClock.beat to 10.5");
  assert(clk.bpm === 130, "state perform sets rlClock.bpm to 130");

  // --- rlFrame extrapolates the beat between frames (free-running at bpm) ---
  setVar("rlClock", { beat: 0, bpm: 120, lastT: 0 });
  perfNow = 0;
  T.rlFrame();   // dt = (0 - 0)/1000 = 0; beat unchanged
  let b0 = getVar("rlClock").beat;
  perfNow = 1000;   // advance 1 second at 120 bpm -> +2 beats
  T.rlFrame();
  let b1 = getVar("rlClock").beat;
  assert(Math.abs(b0 - 0) < 1e-9, "rlFrame with dt=0 does not advance the beat");
  assert(Math.abs(b1 - 2) < 1e-9, "rlFrame advances beat by 2 over 1s at 120 bpm: " + b1);

  // --- a state frame WITHOUT red_light leaves the previous config in effect ---
  ws.onmessage({ data: JSON.stringify({ type: "state", zone: "rest", mode: null, action: null }) });
  rl = getVar("RED_LIGHT");
  assert(rl.enabled === true && rl.x === 0.3, "absent red_light keeps the previous config");

  console.log(failures === 0 ? "ALL_TESTS_PASSED" : "FAILURES=" + failures);
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.error("TEST ERROR:", e); process.exit(1); });
