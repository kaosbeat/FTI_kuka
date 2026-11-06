// Node DOM-stub test for the red-light card in the editor's robotconfig tab.
//
// Loads the editor's main <script> into a vm sandbox backed by the same mini-DOM as
// test_perform_tab.js (permissive getElementById: unknown ids auto-created), then
// exercises the red-light logic end to end:
//   - RED_LIGHT_FALLBACK has the documented defaults,
//   - normalizeRedLight() merges a partial config over the fallback + clamps ranges,
//   - normalizeRobotConfig() carries a normalized red_light field,
//   - buildRobotConfigForm() renders the red-light checkboxes + number fields,
//   - saveRobotConfig() POSTs a payload that includes the red_light field.
//
// Run: node tests/js/test_red_light_tab.js
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

// ---- Mini-DOM (same as test_perform_tab.js) ----------------------------------
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
      toggle: (c, on) => { const s = classes(); if (on === undefined) { if (s.has(c)) s.delete(c); else s.add(c); } else { if (on) s.add(c); else s.delete(c); } self.className = [...s].join(" "); return s.has(c); },
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
vm.runInContext("globalThis.__T = { RED_LIGHT_FALLBACK, normalizeRedLight, normalizeRobotConfig, buildRobotConfigForm, buildRedLightForm, saveRobotConfig };", ctx);
const T = sandbox.__T;
const tick = (ms) => new Promise((r) => setTimeout(r, ms == null ? 0 : ms));
let failures = 0;
function assert(cond, msg) { if (!cond) { failures++; console.log("FAIL:", msg); } else { console.log("ok:", msg); } }

(async () => {
  const F = T.RED_LIGHT_FALLBACK;
  assert(F.enabled === false, "fallback enabled = false");
  assert(F.x === 0.5 && F.y === 0.5, "fallback x/y = 0.5");
  assert(F.bar_w === 1 && F.bar_h === 160 && F.radius === 80, "fallback bar_w=1 bar_h=160 radius=80");
  assert(F.morph === true && F.per_beat === 1, "fallback morph=true per_beat=1");

  // normalize: null/garbage -> full fallback.
  const n0 = T.normalizeRedLight(null);
  assert(JSON.stringify(n0) === JSON.stringify(F), "normalize(null) == fallback");
  const nGarbage = T.normalizeRedLight("garbage");
  assert(JSON.stringify(nGarbage) === JSON.stringify(F), "normalize(garbage) == fallback");

  // normalize: partial -> merges over fallback.
  const np = T.normalizeRedLight({ enabled: true, x: 0.25 });
  assert(np.enabled === true && np.x === 0.25, "normalize partial keeps enabled=true x=0.25");
  assert(np.y === 0.5 && np.bar_w === 1, "normalize partial fills missing fields from fallback");

  // normalize: clamps out-of-range values.
  const nc = T.normalizeRedLight({ x: 5, y: -2, bar_w: -3, bar_h: 40, radius: 12, per_beat: 0.5 });
  assert(nc.x === 1 && nc.y === 0, "normalize clamps x->1 y->0");
  assert(nc.bar_w === 1, "normalize clamps bar_w->1 (floor)");
  assert(nc.bar_h === 40 && nc.radius === 12 && nc.per_beat === 0.5, "normalize keeps in-range bar_h/radius/per_beat");

  // normalizeRobotConfig carries a normalized red_light.
  const rc = T.normalizeRobotConfig({ joint_offsets_deg: [0, 0, 0, 0, 0, 90], red_light: { enabled: true, x: 0.7 } });
  assert(rc.red_light && rc.red_light.enabled === true && rc.red_light.x === 0.7, "normalizeRobotConfig normalizes red_light");
  assert(rc.red_light.y === 0.5 && rc.red_light.bar_w === 1, "normalizeRobotConfig red_light fills missing from fallback");

  // buildRobotConfigForm renders the red-light card (2 checkboxes + 6 number fields).
  vm.runInContext("ROBOT_CONFIG = " + JSON.stringify(T.normalizeRobotConfig(null)) + ";", ctx);
  T.buildRobotConfigForm();
  const rlForm = document.getElementById("redLightForm");
  const rlInputs = rlForm.querySelectorAll("input");
  assert(rlInputs.length === 8, "red-light form has 8 inputs (2 checkbox + 6 number)");
  const byKey = {};
  for (const inp of rlInputs) byKey[inp.dataset.rl] = inp;
  assert(!!byKey.enabled && !!byKey.morph, "checkboxes carry their data-rl keys");
  assert(!!byKey.x && !!byKey.y && !!byKey.bar_w && !!byKey.bar_h && !!byKey.radius && !!byKey.per_beat, "number inputs carry their data-rl keys");
  assert(byKey.enabled.checked === false, "enabled checkbox seeded from fallback (false)");
  assert(parseFloat(byKey.x.value) === 0.5, "x input seeded from config (0.5)");

  // save: reads the red-light form and POSTs a payload that includes red_light.
  fetchTable = { "/api/robotconfig": { ok: true, data: {} } };
  byKey.enabled.checked = true;
  byKey.x.value = "0.25";
  byKey.per_beat.value = "2";
  await T.saveRobotConfig();
  await tick(20);
  const lastPost = fetchLog.find((e) => e.opts && e.opts.method === "POST" && e.url === "/api/robotconfig");
  assert(!!lastPost, "saveRobotConfig POSTs to /api/robotconfig");
  const body = JSON.parse(lastPost.opts.body);
  assert(body.red_light && body.red_light.enabled === true, "save payload includes red_light.enabled=true");
  assert(body.red_light.x === 0.25 && body.red_light.per_beat === 2, "save payload picks up edited x=0.25 per_beat=2");
  assert(body.red_light.y === 0.5 && body.red_light.bar_w === 1, "save payload fills unedited red-light fields from config");
  assert(Array.isArray(body.joint_offsets_deg) && body.joint_offsets_deg.length === 6, "save payload still carries the 6 joint offsets");

  console.log(failures === 0 ? "ALL_TESTS_PASSED" : "FAILURES=" + failures);
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.error("TEST ERROR:", e); process.exit(1); });
