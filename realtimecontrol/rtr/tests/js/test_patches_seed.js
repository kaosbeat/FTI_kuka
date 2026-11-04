// Node DOM-stub test for the patches seeding + validation in editor.html.
//
// Regression test: patches.json ships with "default": "" (allowed empty). loadPatches()
// seeds every missing zone/mode/action slot with that default; an empty seed value then
// fails validatePatches() ("must be a non-empty string") and blocks the flow > actions
// save. Seeding must fall back to PATCHES_FALLBACK.default when PATCHES.default is empty,
// exactly like addPatchAction() does.
//
// Run: node tests/js/test_patches_seed.js
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
  if (inner.includes("buildGroupsForm")) { SRC = inner; break; }
}
if (!SRC) { console.error("could not find the main script"); process.exit(2); }

// ---- Mini-DOM (same hand-rolled stub as test_groups_tab.js) -----------------------
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
        if (name === "type") el.attrs.type = val;
        if (name === "value") el.attrs.value = val;
        if (name === "checked") el.props.checked = true;
      } else if (am[6] !== undefined) {
        const name = am[6];
        el.attrs[name] = "";
        if (name === "checked") el.props.checked = true;
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
  set checked(v) { this.props.checked = v; }
  get checked() { return !!this.props.checked; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { if (this._text) return this._text; return this.children.map((c) => c.textContent).join(""); }
  set innerHTML(v) { this._html = String(v); this.children = []; if (String(v).length) { for (const c of parseHtml(String(v))) { c.parent = this; this.children.push(c); } } }
  get innerHTML() { return this._html; }
  get type() { return this.attrs.type || this.props.type || ""; }
  set type(v) { this.attrs.type = v; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); if (c.parent === this) c.parent = null; return c; }
  insertBefore(c, ref) { const i = this.children.indexOf(ref); if (i < 0) return this.appendChild(c); c.parent = this; this.children.splice(i, 0, c); return c; }
  remove() { if (this.parent) this.parent.removeChild(this); }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  removeEventListener() {}
  setAttribute(k, v) { this.attrs[k] = v; if (k === "id") registerId(this); }
  getAttribute(k) { return this.attrs[k]; }
  setAttributeNode() {}
  focus() {} blur() {}
  getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 }; }
  get nextElementSibling() { if (!this.parent) return null; const s = this.parent.children; const i = s.indexOf(this); return i >= 0 && i + 1 < s.length ? s[i + 1] : null; }
  get firstChild() { return this.children[0] || null; }
  hasChildNodes() { return this.children.length > 0; }
  get firstElementChild() { return this.children[0] || null; }
  get parentNode() { return this.parent; }
  get parentElement() { return this.parent; }
  get childNodes() { return this.children; }
  matchesClass(cls) { return (" " + (this.className || "") + " ").split(" ").includes(cls); }
  get classList() {
    const self = this;
    const classes = () => (self.className || "").split(" ").filter((x) => x.length);
    return {
      add: (c) => { const s = new Set(classes()); s.add(c); self.className = [...s].join(" "); },
      remove: (c) => { const s = classes().filter((x) => x !== c); self.className = s.join(" "); },
      toggle: (c) => { const s = new Set(classes()); if (s.has(c)) s.delete(c); else s.add(c); self.className = [...s].join(" "); return s.has(c); },
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
  // Auto-vivify unknown ids: the editor wires top-level onclick/text on static ids;
  // missing ones must not crash script eval (keeps this test robust to new wiring).
  getElementById: (id) => {
    if (!IDX[id]) { const e = new El("div"); e.attrs.id = id; ROOT.appendChild(e); registerId(e); }
    return IDX[id] || null;
  },
  querySelectorAll: (sel) => ROOT.querySelectorAll(sel),
  querySelector: (sel) => ROOT.querySelector(sel),
  body: ROOT,
  documentElement: ROOT,
  addEventListener() {},
  removeEventListener() {},
  createTextNode: (t) => { const e = new El("#text"); e._text = t; return e; },
};
const STATIC_IDS = ["host","dot","status","liveState","safetyPopup","spTitle","spTarget","spIssues","midiStatus","saveMsg","hwLimits","sndOutPort","sndOutPortInfo","sndChannel","sndZoneSel","sndZoneDetail","midiStopLearn","midiZones","midiNav","patchCode","brainCards","brainInfo","groupList","brainAutEnabled","brainEvMidi","brainEvCamera","brainEvWs","brainGrpHuntEnabled","brainHuntDetZone","brainHuntDetAct","brainHuntScanZone","brainHuntScanAct","brainGrpFacefocusEnabled","brainLive","patchStatus","patchPreview","patchList","patchTitle","patchInfo","aePatchStatus","aePreview","aeBody","actionEditor","aeTitle","aePatchCode","zoneMain","patchesMain","midiMain","flowMain","brainMain","groupsMain","validateMain","tabZones","tabPatches","tabMidi","tabFlow","tabGroups","tabBrain","tabValidate","midiOutView","midiInView","midiTabOut","midiTabIn","valForm","valResult","valProgress","valPoseGrid","zoneList","zoneForm","zoneTitle","bars","ghostInfo","aeSaveStatus","flowSvg","flowEdgeLayer","flowInnerEdgeLayer","flowGroupLayer","flowDragArrow","flowDetail","flowDetailTitle","robot3d","valRobot3dMount","ghostToggle","valMeshEnvReal","valMeshEnvColl","valMeshRobReal","valMeshRobColl","valMeshToolColl","connect","save","addZone","renameZone","delZone","addGroup","renameGroup","removeGroup","valPoseGo","valCopyCollision","sndRefreshPorts","flowAddZone","flowRenameZone","flowDelZone","flowAutoLayout","flowFit","patchAdd","patchRemove","aeClose","spConfirm","spCancel","aeSave","ghostPrev","ghostNext","ghostCollide","groupsInfo","aePanel"];
for (const id of STATIC_IDS) { const e = new El("div"); e.attrs.id = id; ROOT.appendChild(e); registerId(e); }

// ---- Globals / sandbox -------------------------------------------------------------
let patchesData = null, zonesData = null;
try { patchesData = JSON.parse(fs.readFileSync(path.join(RDIR, "patches.json"), "utf8")); } catch (e) {}
try { zonesData = JSON.parse(fs.readFileSync(path.join(RDIR, "zones.json"), "utf8")); } catch (e) {}
if (!patchesData || !zonesData) { console.error("need real patches.json + zones.json"); process.exit(2); }
function fakeFetch(url) {
  const table = { "/api/patches": null, "/patches.json": patchesData, "/api/zones": null, "/zones.json": zonesData };
  const body = table[url];
  if (body) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
  return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
}
const noop = () => {};
const sandbox = {
  fmt: (v) => v == null ? "—" : (Math.round(v * 10) / 10).toFixed(1),
  console,
  document,
  fetch: (u) => fakeFetch(u),
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
vm.runInContext("globalThis.__T = { loadTable, loadPatches, validatePatches, PATCHES_FALLBACK };", ctx);
const T = sandbox.__T;
const getVar = (n) => vm.runInContext(n, ctx);
const tick = (ms) => new Promise((r) => setTimeout(r, ms == null ? 0 : ms));
let failures = 0;
function assert(cond, msg) { if (!cond) { failures++; console.log("FAIL:", msg); } else { console.log("ok:", msg); } }

(async () => {
  // The real patches.json ships with an EMPTY default (allowed), while zones.json
  // defines zones + actions not present in patches.json (testing,
  // resting_testing_bridge, clawing/lock_target, ...).
  assert(patchesData.default === "", "precondition: patches.json default is empty string");

  await T.loadTable();
  await T.loadPatches();
  const P = getVar("PATCHES");

  // Empty default stays as-is (allowed), but seeded slots must be non-empty.
  assert(P.default === "", "empty PATCHES.default preserved (allowed empty)");
  assert(P.zones.testing !== undefined, "zone 'testing' seeded");
  assert(P.zones.resting_testing_bridge !== undefined, "zone 'resting_testing_bridge' seeded");
  const seededZones = ["testing", "resting_testing_bridge"];
  for (const z of seededZones) assert(typeof P.zones[z] === "string" && P.zones[z].length, `zones[${z}] is a non-empty string: ${JSON.stringify(P.zones[z])}`);

  const seededActions = [
    ["clawing", "lock_target"],
    ["testing", "locked"], ["testing", "intent"], ["testing", "curious"], ["testing", "gentle"],
    ["resting_testing_bridge", "bridge"],
  ];
  for (const [z, a] of seededActions) {
    const v = P.actions && P.actions[z] && P.actions[z][a];
    assert(typeof v === "string" && v.length, `actions[${z}][${a}] is a non-empty string: ${JSON.stringify(v)}`);
  }

  // The regression: validatePatches() must pass on the freshly seeded table.
  const errs = T.validatePatches();
  assert(errs.length === 0, "validatePatches() has no errors after seeding (was: must be a non-empty string): " + JSON.stringify(errs));

  // Non-empty default must still win over the fallback (|| semantics, not always-fallback).
  patchesData = { default: "MYDEFAULT_CODE", zones: {}, modes: {}, actions: {} };
  await T.loadPatches();
  const P2 = getVar("PATCHES");
  assert(P2.default === "MYDEFAULT_CODE", "non-empty default preserved");
  assert(P2.zones.testing === "MYDEFAULT_CODE", "seed uses the non-empty default when present");
  const errs2 = T.validatePatches();
  assert(errs2.length === 0, "validatePatches() passes with non-empty default");

  console.log(failures === 0 ? "ALL_TESTS_PASSED" : "FAILURES=" + failures);
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.error("TEST ERROR:", e); process.exit(4); });
