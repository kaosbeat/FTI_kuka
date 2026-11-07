// Node DOM-stub test for the roaming indicator on the brain tab's behavior cards.
//
// Loads the editor's main <script> into a vm sandbox backed by a small hand-rolled
// DOM (the same mini-DOM as test_groups_tab.js), switches to the brain view (which
// loads brain.json and builds the behavior cards), then verifies the roaming badge:
//   - one .roamBadge in the top-left of every one of the 15 behavior cards,
//   - the no-roam behaviors (calm / bridge, the no-op policies in behavior.py) get a
//     "fixed" badge (📌); every other behavior gets a "roam" badge (🚶).
//
// Run: node tests/js/test_roam_badge.js
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

// ---- Mini-DOM (same as test_groups_tab.js) ----------------------------------
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
      toggle: (c) => { const s = new Set(classes()); if (s.has(c)) s.delete(c); else s.add(c); self.className = [...s].join(" "); return s.has(c); },
      contains: (c) => classes().includes(c),
    };
  }
  * _walk() { for (const c of this.children) { yield c; yield* c._walk(); } }
  querySelectorAll(sel) { return [...this._walk()].filter((e) => matchSel(e, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
const ROOT = new El("root");
// Permissive getElementById: auto-create any element the editor's top-level init
// references (the fixed STATIC_IDS list is stale — the editor gained tabRobotConfig /
// liveToggle / ui*Toggle / crop* ids after the groups test was written). Pre-created
// STATIC_IDS still take priority; anything else is created on first access.
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
const STATIC_IDS = ["host","dot","status","liveState","safetyPopup","spTitle","spTarget","spIssues","midiStatus","saveMsg","hwLimits","sndOutPort","sndOutPortInfo","sndChannel","sndZoneSel","sndZoneDetail","midiStopLearn","midiZones","midiNav","midiCommands","patchCode","brainCards","brainInfo","groupList","brainAutEnabled","brainEvMidi","brainEvCamera","brainEvWs","brainGrpHuntEnabled","brainHuntDetZone","brainHuntDetAct","brainHuntScanZone","brainHuntScanAct","brainGrpFacefocusEnabled","brainLive","patchStatus","patchPreview","patchList","patchTitle","patchInfo","aePatchStatus","aePreview","aeBody","actionEditor","aeTitle","aePatchCode","zoneMain","patchesMain","midiMain","flowMain","brainMain","groupsMain","validateMain","tabZones","tabPatches","tabMidi","tabFlow","tabGroups","tabBrain","tabValidate","midiOutView","midiInView","midiTabOut","midiTabIn","valForm","valResult","valProgress","valPoseGrid","zoneList","zoneForm","zoneTitle","bars","ghostInfo","aeSaveStatus","flowSvg","flowEdgeLayer","flowInnerEdgeLayer","flowGroupLayer","flowDragArrow","flowDetail","flowDetailTitle","robot3d","valRobot3dMount","ghostToggle","valMeshEnvReal","valMeshEnvColl","valMeshRobReal","valMeshRobColl","valMeshToolColl","connect","save","addZone","renameZone","delZone","addGroup","renameGroup","removeGroup","valPoseGo","valCopyCollision","sndRefreshPorts","flowAddZone","flowRenameZone","flowDelZone","flowAutoLayout","flowFit","patchAdd","patchRemove","aeClose","spConfirm","spCancel","aeSave","ghostPrev","ghostNext","ghostCollide","groupsInfo","aePanel","camBrainCards"];
for (const id of STATIC_IDS) { const e = new El("div"); e.attrs.id = id; ROOT.appendChild(e); registerId(e); }

// ---- Globals / sandbox -------------------------------------------------------
let brainData = {};
try { brainData = JSON.parse(fs.readFileSync(path.join(RDIR, "brain.json"), "utf8")); } catch (e) {}
function fakeFetch(url) {
  const table = { "/brain.json": brainData };
  if (table[url]) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(table[url]) });
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
vm.runInContext("globalThis.__T = { BRAIN, buildBrainForm, loadBrain, BEHAVIORS, NO_ROAM_BEHAVIORS };", ctx);
const T = sandbox.__T;
const tick = (ms) => new Promise((r) => setTimeout(r, ms == null ? 0 : ms));
const setVar = (n, v) => { vm.runInContext(n + " = " + JSON.stringify(v), ctx); };
let failures = 0;
function assert(cond, msg) { if (!cond) { failures++; console.log("FAIL:", msg); } else { console.log("ok:", msg); } }

(async () => {
  // Set brainView and run loadBrain (async fetch of /brain.json) which normalizes
  // BRAIN and calls buildBrainForm (since brainView is set). This avoids switchView's
  // 3D-unmount side effects, which the mini-DOM stub does not model.
  setVar("brainView", true);
  await T.loadBrain();
  await tick(20);

  const B = vm.runInContext("BRAIN", ctx);
  assert(B && B.behaviors, "BRAIN loaded with behaviors");

  const cards = document.getElementById("brainCards");
  assert(cards.children.length > 0, "brain cards rendered");

  // Every behavior card carries exactly one roaming badge.
  const badges = cards.querySelectorAll(".roamBadge");
  assert(badges.length === T.BEHAVIORS.length, badges.length + " badges for " + T.BEHAVIORS.length + " behaviors");

  // Map each badge to its card's behavior name (the card's h2).
  const byName = {};
  for (const b of badges) {
    const card = b.parent;
    const h2 = card.children.find((c) => c.tag === "h2");
    byName[h2.textContent] = b;
  }
  assert(Object.keys(byName).length === T.BEHAVIORS.length, "badge per behavior card: " + Object.keys(byName).length);

  // The no-roam (strict-pose) behaviors get a "fixed" badge (📌); the rest get "roam" (🚶).
  for (const name of T.BEHAVIORS) {
    const badge = byName[name];
    const isNoRoam = T.NO_ROAM_BEHAVIORS.has(name);
    const cls = (badge.className || "").split(" ").filter(Boolean);
    assert(cls.includes(isNoRoam ? "fixed" : "roam"), name + " badge class = " + (isNoRoam ? "fixed" : "roam") + " (got " + badge.className + ")");
    assert(badge.textContent === (isNoRoam ? "\uD83D\uDCCD" : "\uD83D\uDEB6"), name + " badge glyph = " + (isNoRoam ? "📌" : "🚶"));
    assert(badge.title.length > 0, name + " badge has a tooltip");
  }
  // Sanity: exactly calm + bridge are fixed; the other 13 roam.
  const fixedNames = T.BEHAVIORS.filter((n) => T.NO_ROAM_BEHAVIORS.has(n)).sort();
  assert(JSON.stringify(fixedNames) === JSON.stringify(["bridge", "calm"]), "fixed set = calm, bridge (" + fixedNames.join(", ") + ")");
  const roamCount = T.BEHAVIORS.length - fixedNames.length;
  assert(roamCount === 13, "13 roaming behaviors (got " + roamCount + ")");

  console.log(failures === 0 ? "ALL_TESTS_PASSED" : "FAILURES=" + failures);
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.error("TEST ERROR:", e); process.exit(4); });
