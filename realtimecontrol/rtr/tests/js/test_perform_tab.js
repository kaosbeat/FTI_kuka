// Node DOM-stub test for the perform config tab in editor.html.
//
// Loads the editor's main <script> into a vm sandbox backed by the same mini-DOM as
// test_roam_badge.js (permissive getElementById: unknown ids auto-created), then
// exercises the perform-tab logic end to end:
//   - PERFORM_FALLBACK has the documented global defaults,
//   - normalizePerformConfig() merges a partial config over the fallback,
//   - loadPerformConfig() prefers /api/perform, falls back to /perform.json, then defaults,
//   - buildPerformForm() renders the 4 number fields + the multipliers text field,
//   - savePerformConfig() POSTs the parsed payload to /api/perform and flags saveMsg.
//
// Run: node tests/js/test_perform_tab.js
"use strict";
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const RDIR = path.join(__dirname, "..", "..");

const html = fs.readFileSync(path.join(RDIR, "editor.html"), "utf8");
const scripts = html.match(/<script[^>]*>([\s\S]*?)<\/script>/g) || [];
let SRC = null;
for (const s of scripts) {
  const inner = s.replace(/^<script[^>]*>/, "").replace(/<\/script>$/, "");
  if (inner.includes("buildBrainForm")) { SRC = inner; break; }
}
if (!SRC) { console.error("could not find the main script"); process.exit(2); }

// ---- Mini-DOM (same as test_roam_badge.js) ----------------------------------
const IDX = {};
function registerId(el) { const id = el.attrs.id; if (id) IDX[id] = el; }
function matchOne(el, part) {
  if (part.startsWith("#")) return el.attrs.id === part.slice(1);
  if (part.startsWith(".")) return el.matchesClass(part.slice(1));
  if (part.includes(".")) {
    const [t, c] = part.split(".", 2);
    return el.tag === t && el.matchesClass(c);
  }
  return el.tag === part;
}
function matchSel(el, sel) {
  const parts = sel.trim().split(/\s+/);
  if (!matchOne(el, parts[parts.length - 1])) return false;
  for (let i = 0; i < parts.length - 1; i++) {
    let anc = el.parent, found = false;
    while (anc) { if (matchOne(anc, parts[i])) { found = true; break; } anc = anc.parent; }
    if (!found) return false;
  }
  return true;
}
const VOID_TAGS = { input: 1, br: 1, img: 1, hr: 1, meta: 1, link: 1 };
function parseHtml(str) {
  const out = [];
  const stack = [];
  const re = /<\s*\/?\s*([a-zA-Z][a-zA-Z0-9]*)([^>]*)>?|([^<]+)/g;
  let m;
  while ((m = re.exec(str)) !== null) {
    if (m[3] !== undefined) {
      const txt = m[3];
      if (txt.length) {
        const t = new El("#text");
        t._text = txt;
        if (stack.length) stack[stack.length - 1].appendChild(t);
        else out.push(t);
      }
      continue;
    }
    const tag = m[1].toLowerCase();
    const isClose = m[0].startsWith("</");
    if (isClose) {
      while (stack.length && stack[stack.length - 1].tag !== tag) stack.pop();
      if (stack.length) stack.pop();
      continue;
    }
    const el = new El(tag);
    const are = /([a-zA-Z_:]+)\s*=\s*("([^"]*)"|'([^']*)'|([^\s"'>]+))|([a-zA-Z_:]+)(?=\s|$)/g;
    let am;
    const attrStr = m[2] || "";
    while ((am = are.exec(attrStr)) !== null) {
      if (am[1] !== undefined) {
        const name = am[1];
        const val = am[3] !== undefined ? am[3] : am[4] !== undefined ? am[4] : am[5] !== undefined ? am[5] : "";
        el.attrs[name] = val;
        if (name === "id") registerId(el);
        if (name === "class") el.className = val;
      }
    }
    if (VOID_TAGS[tag] || m[0].endsWith("/>")) {
      if (stack.length) stack[stack.length - 1].appendChild(el);
      else out.push(el);
    } else {
      if (stack.length) stack[stack.length - 1].appendChild(el);
      else out.push(el);
      stack.push(el);
    }
  }
  return out;
}
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.parent = null; this.attrs = {}; this.props = {}; this.listeners = {}; this.style = {}; this.dataset = {}; this._text = ""; this._html = ""; this.className = ""; }
  set id(v) { this.attrs.id = v; registerId(this); }
  get id() { return this.attrs.id || ""; }
  set value(v) { this.props.value = v; }
  get value() { return this.props.value !== undefined ? this.props.value : (this.attrs.value != null ? this.attrs.value : ""); }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { if (this._text) return this._text; return this.children.map((c) => c.textContent).join(""); }
  set innerHTML(v) { this._html = String(v); this.children = []; if (String(v).length) { for (const c of parseHtml(String(v))) { c.parent = this; this.children.push(c); } } }
  get innerHTML() { return this._html; }
  get type() { return this.attrs.type || this.props.type || ""; }
  set type(v) { this.attrs.type = v; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); if (c.parent === this) c.parent = null; return c; }
  remove() { if (this.parent) this.parent.removeChild(this); }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  removeEventListener() {}
  setAttribute(k, v) { this.attrs[k] = v; if (k === "id") registerId(this); }
  getAttribute(k) { return this.attrs[k]; }
  setAttributeNode() {}
  focus() {} blur() {}
  getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 }; }
  get firstChild() { return this.children[0] || null; }
  hasChildNodes() { return this.children.length > 0; }
  get childNodes() { return this.children; }
  matchesClass(cls) { return (" " + (this.className || "") + " ").split(" ").includes(cls); }
  get classList() {
    const self = this;
    const classes = () => (self.className || "").split(" ").filter((x) => x.length);
    return {
      add: (c) => { const s = new Set(classes()); s.add(c); self.className = [...s].join(" "); },
      remove: (c) => { const s = classes().filter((x) => x !== c); self.className = s.join(" "); },
      toggle: (c, on) => { const s = new Set(classes()); if (on === undefined) { if (s.has(c)) s.delete(c); else s.add(c); } else { if (on) s.add(c); else s.delete(c); } self.className = [...s].join(" "); return s.has(c); },
      contains: (c) => classes().includes(c),
    };
  }
  * _walk() { for (const c of this.children) { yield c; yield* c._walk(); } }
  querySelectorAll(sel) { return [...this._walk()].filter((e) => matchSel(e, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
const ROOT = new El("root");
const document = {
  _root: ROOT,
  createElement: (t) => new El(t),
  getElementById: (id) => { if (!IDX[id]) { const e = new El("div"); e.attrs.id = id; ROOT.appendChild(e); IDX[id] = e; } return IDX[id]; },
  querySelectorAll: (sel) => ROOT.querySelectorAll(sel),
  querySelector: (sel) => ROOT.querySelector(sel),
  body: ROOT,
  documentElement: ROOT,
  addEventListener() {},
  removeEventListener() {},
  createTextNode: (t) => { const e = new El("#text"); e._text = t; return e; },
};

// ---- Controllable fetch mock -------------------------------------------------
let fetchTable = {};
const fetchLog = [];
function fakeFetch(url, opts) {
  fetchLog.push({ url: String(url), opts: opts || null });
  const entry = fetchTable[String(url)];
  if (entry && entry.ok) {
    return Promise.resolve({ ok: true, status: entry.status || 200, json: () => Promise.resolve(entry.data) });
  }
  return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
}
const noop = () => {};
const sandbox = {
  fmt: (v) => v == null ? "—" : (Math.round(v * 10) / 10).toFixed(1),
  console,
  document,
  fetch: (u, o) => fakeFetch(u, o),
  WebSocket: class { constructor() {} close() {} send() {} },
  Robot3D: new Proxy({}, { get: (_t, prop) => (prop === "checkPoseCollision" ? (() => null) : noop) }),
  prompt: () => null, alert: noop, confirm: () => false,
  setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (f) => setTimeout(f, 0), cancelAnimationFrame: noop,
  performance: { now: () => Date.now() },
  navigator: { userAgent: "test" },
  localStorage: { getItem: () => null, setItem: noop },
  Image: class { set src(v) {} },
  Event: class { constructor(t) { this.type = t; } },
  AudioContext: class { createGain(){return{gain:{value:0},connect(){},disconnect(){}};} createOscillator(){return{connect(){},start(){},stop(){},frequency:{value:0}};} get currentTime(){return 0;} resume(){return Promise.resolve();} },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.addEventListener = noop; sandbox.removeEventListener = noop;
sandbox.dispatchEvent = () => true;
sandbox.location = { href: "http://localhost/", origin: "http://localhost" };
sandbox.innerWidth = 1200; sandbox.innerHeight = 800;
sandbox.HTMLElement = El; sandbox.Element = El; sandbox.SVGElement = El;

const ctx = vm.createContext(sandbox);
try {
  vm.runInContext(SRC, ctx, { filename: "editor.js" });
} catch (e) {
  console.error("LOAD ERROR:", e.message);
  process.exit(3);
}
vm.runInContext("globalThis.__T = { PERFORM_FALLBACK, normalizePerformConfig, loadPerformConfig, buildPerformForm, savePerformConfig, _midiKeyFor, buildPerformMidiList, updatePerformMidiLights };", ctx);
const T = sandbox.__T;
const tick = (ms) => new Promise((r) => setTimeout(r, ms == null ? 0 : ms));
let failures = 0;
function assert(cond, msg) { if (!cond) { failures++; console.log("FAIL:", msg); } else { console.log("ok:", msg); } }

(async () => {
  const F = T.PERFORM_FALLBACK;
  assert(F.default_base_beats === 4, "fallback default_base_beats = 4");
  assert(F.internal_bpm === 130.0, "fallback internal_bpm = 130.0");
  assert(F.clock_timeout === 1.0, "fallback clock_timeout = 1.0");
  assert(F.velocity_max_deg_s === 30.0, "fallback velocity_max_deg_s = 30.0");
  assert(JSON.stringify(F.multipliers) === JSON.stringify([0.0625, 0.125, 0.25, 0.5, 1, 2, 4, 8, 16]), "fallback multipliers = 9 steps (1/16 … 16)");

  // normalize: null -> full fallback.
  const n0 = T.normalizePerformConfig(null);
  assert(JSON.stringify(n0) === JSON.stringify(F), "normalize(null) == fallback");

  // normalize: partial -> merges over fallback.
  const np = T.normalizePerformConfig({ internal_bpm: 120, multipliers: [2, 4] });
  assert(np.internal_bpm === 120, "normalize partial keeps internal_bpm=120");
  assert(JSON.stringify(np.multipliers) === JSON.stringify([2, 4]), "normalize partial keeps multipliers=[2,4]");
  assert(np.default_base_beats === 4 && np.velocity_max_deg_s === 30.0, "normalize partial fills missing fields from fallback");

  // load: /api/perform 200 wins.
  fetchTable = { "/api/perform": { ok: true, data: { default_base_beats: 8, internal_bpm: 90, clock_timeout: 0.5, velocity_max_deg_s: 180, multipliers: [1, 4] } } };
  const loaded = await T.loadPerformConfig();
  assert(loaded.internal_bpm === 90 && loaded.default_base_beats === 8, "load uses /api/perform when ok");
  assert(JSON.stringify(loaded.multipliers) === JSON.stringify([1, 4]), "load multipliers from /api/perform");

  // load: /api/perform 404 -> falls back to /perform.json.
  fetchTable = { "/perform.json": { ok: true, data: { internal_bpm: 60 } } };
  const loaded2 = await T.loadPerformConfig();
  assert(loaded2.internal_bpm === 60, "load falls back to /perform.json when api 404");
  assert(loaded2.velocity_max_deg_s === 30.0, "load fills missing fields from fallback when using /perform.json");

  // load: everything 404 -> defaults.
  fetchTable = {};
  const loaded3 = await T.loadPerformConfig();
  assert(JSON.stringify(loaded3) === JSON.stringify(F), "load falls back to PERFORM_FALLBACK when nothing is available");

  // buildPerformForm: renders 4 number fields + the multipliers text field.
  fetchTable = { "/api/perform": { ok: true, data: F } };
  await T.loadPerformConfig();
  T.buildPerformForm();
  const form = document.getElementById("performForm");
  const inputs = form.querySelectorAll("input");
  assert(inputs.length === 5, "perform form has 5 inputs (4 number + multipliers)");
  const byKey = {};
  for (const inp of inputs) byKey[inp.dataset.key] = inp;
  assert(!!byKey.default_base_beats && !!byKey.internal_bpm && !!byKey.clock_timeout && !!byKey.velocity_max_deg_s, "number inputs carry their dataset keys");
  assert(byKey.multipliers && byKey.multipliers.value === "0.0625, 0.125, 0.25, 0.5, 1, 2, 4, 8, 16", "multipliers input shows comma-separated list (9 steps)");
  assert(byKey.internal_bpm.value === 130.0, "internal_bpm input seeded from config");

  // save: posts the parsed payload and flags saveMsg "saved".
  fetchTable = { "/api/perform": { ok: true, data: {} } };
  byKey.velocity_max_deg_s.value = "500";
  byKey.multipliers.value = "1, 2, 4, 8";
  await T.savePerformConfig();
  await tick(20);
  const lastPost = fetchLog.find((e) => e.opts && e.opts.method === "POST" && e.url === "/api/perform");
  assert(!!lastPost, "savePerformConfig POSTs to /api/perform");
  const body = JSON.parse(lastPost.opts.body);
  assert(body.velocity_max_deg_s === 500, "save payload picks up edited velocity_max_deg_s=500");
  assert(JSON.stringify(body.multipliers) === JSON.stringify([1, 2, 4, 8]), "save payload parses multipliers text to [1,2,4,8]");
  assert(document.getElementById("saveMsg").textContent === "saved", "saveMsg reads 'saved' after a good POST");

  // save: invalid number falls back to the documented default for that field.
  byKey.velocity_max_deg_s.value = "abc";
  fetchTable = { "/api/perform": { ok: true, data: {} } };
  await T.savePerformConfig();
  await tick(20);
  const body2 = JSON.parse(fetchLog.filter((e) => e.opts && e.opts.method === "POST").pop().opts.body);
  assert(body2.velocity_max_deg_s === 30.0, "invalid number falls back to fallback velocity_max_deg_s=30.0");

  // save: bad multipliers text falls back to the default multipliers.
  byKey.multipliers.value = "junk";
  fetchTable = { "/api/perform": { ok: true, data: {} } };
  await T.savePerformConfig();
  await tick(20);
  const body3 = JSON.parse(fetchLog.filter((e) => e.opts && e.opts.method === "POST").pop().opts.body);
  assert(JSON.stringify(body3.multipliers) === JSON.stringify([0.0625, 0.125, 0.25, 0.5, 1, 2, 4, 8, 16]), "junk multipliers fall back to default multipliers");

  // --- _midiKeyFor: the canonical key for a mappable message (JS port of _key_for) ---
  assert(T._midiKeyFor([0xB0, 70, 64]) === "cc:70:64", "_midiKeyFor cc");
  assert(T._midiKeyFor([0x90, 60, 100]) === "note:0:60", "_midiKeyFor note on");
  assert(T._midiKeyFor([0x90, 60, 0]) === "noteoff:0:60", "_midiKeyFor note-on vel 0 -> noteoff");
  assert(T._midiKeyFor([0x80, 60, 64]) === "noteoff:0:60", "_midiKeyFor note off");
  assert(T._midiKeyFor([0xC0, 5]) === "program:0:5", "_midiKeyFor program");
  assert(T._midiKeyFor([0xD0, 1, 2]) === null, "_midiKeyFor nrp -> null");

  // --- buildPerformMidiList: lists every registered MIDI mapping ---
  const midiTest = {
    zones: { stretch: { command: "cc:1:5", actions: { reach: "note:0:60" } } },
    nav: { next_action: "cc:2:5" },
    capture: { key: "program:0:1" },
    groups: { in_port: null, g1: "note:0:61" },
    perform: { cc_a: 70, cc_b: 71 },
  };
  vm.runInContext("MIDI = " + JSON.stringify(midiTest) + ";", ctx);
  T.buildPerformMidiList();
  const list = document.getElementById("performMidiList");
  const rows = list.querySelectorAll(".pmRow");
  assert(rows.length === 7, "perform midi list has 7 rows (zone cmd + zone action + nav + capture + group + 2 perform CCs)");

  // --- updatePerformMidiLights: lights the matching row for an incoming message ---
  T.updatePerformMidiLights([{ dir: "in", seq: 1, msg: [0xB0, 1, 5] }]);
  let litCount = 0, litTexts = [];
  rows.forEach((r) => { if (r.classList.contains("pmLit")) { litCount++; litTexts.push(r.textContent); } });
  assert(litCount === 1, "one row lights for the matching zone command");
  assert(litTexts.join("|").includes("zone"), "the lit row is the zone command");

  // Perform CCs light on any value of that controller (CC-A = 70).
  T.updatePerformMidiLights([{ dir: "in", seq: 2, msg: [0xB0, 70, 42] }]);
  litCount = 0; litTexts = [];
  rows.forEach((r) => { if (r.classList.contains("pmLit")) { litCount++; litTexts.push(r.textContent); } });
  assert(litCount === 1, "perform CC-A lights on any value of CC 70");
  assert(litTexts.join("|").includes("CC-A"), "the lit row is perform CC-A");

  // A non-matching message clears the lights.
  T.updatePerformMidiLights([{ dir: "in", seq: 3, msg: [0xB0, 99, 10] }]);
  litCount = 0;
  rows.forEach((r) => { if (r.classList.contains("pmLit")) litCount++; });
  assert(litCount === 0, "no row lights for a non-matching message");

  console.log(failures === 0 ? "ALL_TESTS_PASSED" : "FAILURES=" + failures);
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.error("TEST ERROR:", e); process.exit(4); });
