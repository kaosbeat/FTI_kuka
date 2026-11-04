// Node DOM-stub test for the flow tab (autofit + bridge layout) in editor.html.
//
// Loads the editor's main <script> into a vm sandbox backed by a small hand-rolled
// DOM, seeds a TABLE with a chain plus a bridge zone, then verifies the two layout
// behaviours added for the flow tab:
//   (1) flowAutoLayout gives a bridge zone a FRACTIONAL column depth (the mean of its
//       endpoints' BFS depths) so it lands in the column between them, and centres it
//       vertically at the midpoint of the two nodes it connects.
//   (2) flowFitView matches the canvas's aspect ratio (no letterbox bands), clamps the
//       zoom, and centres the view on the content.
//
// Run: node tests/js/test_flow_tab.js
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
  if (inner.includes("flowAutoLayout")) { SRC = inner; break; }
}
if (!SRC) { console.error("could not find the main script"); process.exit(2); }

// ---- Mini-DOM (mirrors test_groups_tab.js) --------------------------------------
const IDX = {};
function registerId(el) { const id = el.attrs.id; if (id) IDX[id] = el; }
function matchOne(el, part) {
  const toks = [];
  const r = /([a-zA-Z][a-zA-Z0-9_-]*)|\.([a-zA-Z][a-zA-Z0-9_-]*)|#([a-zA-Z][a-zA-Z0-9_-]+)|\[([a-zA-Z_][a-zA-Z0-9_-]*)(?:=([^\]]*))?\]/g;
  let mt;
  while ((mt = r.exec(part)) !== null) {
    if (mt[1] !== undefined) toks.push({ t: "tag", v: mt[1] });
    else if (mt[2] !== undefined) toks.push({ t: "class", v: mt[2] });
    else if (mt[3] !== undefined) toks.push({ t: "id", v: mt[3] });
    else if (mt[4] !== undefined) toks.push({ t: "attr", name: mt[4], val: mt[5] });
  }
  for (const tk of toks) {
    if (tk.t === "tag" && el.tag !== tk.v) return false;
    if (tk.t === "class" && !el.matchesClass(tk.v)) return false;
    if (tk.t === "id" && el.attrs.id !== tk.v) return false;
    if (tk.t === "attr") {
      const ds = tk.name.replace(/^data-/, "");
      const has = (tk.name in el.attrs) || (ds in el.dataset);
      if (!has) return false;
      if (tk.val !== undefined) {
        const v = tk.name in el.attrs ? el.attrs[tk.name] : el.dataset[ds];
        if (String(v) !== tk.val) return false;
      }
    }
  }
  return true;
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
      } else if (am[6] !== undefined) {
        const name = am[6];
        el.attrs[name] = "";
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
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); if (c.parent === this) c.parent = null; return c; }
  remove() { if (this.parent) this.parent.removeChild(this); }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  removeEventListener() {}
  setAttribute(k, v) { this.attrs[k] = v; if (k === "id") registerId(this); }
  getAttribute(k) { return this.attrs[k]; }
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
      toggle: (c) => { const s = classes().filter((x) => x !== c); if (self.classList.contains(c)) { s.add(c); self.className = [...s].join(" "); } else { self.className = [...s].join(" "); } return s.has(c); },
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
  getElementById: (id) => IDX[id] || null,
  querySelectorAll: (sel) => ROOT.querySelectorAll(sel),
  querySelector: (sel) => ROOT.querySelector(sel),
  body: ROOT,
  documentElement: ROOT,
  addEventListener() {},
  removeEventListener() {},
  createTextNode: (t) => { const e = new El("#text"); e._text = t; return e; },
};
// Every element id the main script references via $("...") / getElementById("...").
// Regenerate if the editor adds new ids: grep -oE '\$\("([a-zA-Z0-9_]+)"\)' editor.html.
const STATIC_IDS = ["actionEditor","addZone","aeBody","aeClose","aePatchCode","aePatchStatus","aePoseImgs","aePoseVideos","aePreview","aePreviewCol","aeSave","aeSaveStatus","aeTitle","aeVidPreview","backup","bars","brainAutEnabled","brainCards","brainEvCamera","brainEvMidi","brainEvWs","brainGrpFacefocusEnabled","brainGrpHuntEnabled","brainHuntDetAct","brainHuntDetZone","brainHuntScanAct","brainHuntScanZone","brainInfo","brainLive","brainMain","camBrainCards","camLensSel","camMain","camSnapBtn","camSnapUrl","camSnapshot","camTestAction","camTestBehavior","camTestStart","camTestStatus","camTestStop","camTestZone","camView3d","connect","cropCancel","cropCanvas","cropConfirm","cropEditor","cropFile","cropReset","delZone","dot","flowAddZone","flowAutoLayout","flowDelZone","flowDetail","flowDetailTitle","flowDragArrow","flowEdgeLayer","flowFit","flowGroupLayer","flowInnerEdgeLayer","flowMain","flowProblems","flowRenameZone","flowSvg","flowValidate","ghostCollide","ghostInfo","ghostNext","ghostPrev","ghostToggle","groupList","groupsMain","grpNewName","host","hwLimits","limitsForm","liveState","liveToggle","midiAllList","midiCapture","midiGroups","midiInCb","midiInView","midiMain","midiMonitor","midiNav","midiOutCb","midiOutView","midiStatus","midiStopLearn","midiTabIn","midiTabOut","midiZones","patchAdd","patchCode","patchInfo","patchList","patchModeSel","patchPreview","patchRemove","patchStatus","patchTitle","patchesMain","poses3dSection","posesAddPose","posesClamp","posesCollide","posesCopy","posesGrid","posesInfo","posesLoad","posesLoadSel","posesMain","posesRobot3dMount","posesSaveMsg","posesSaveSel","renameZone","restore","restoreFile","robot3d","robotConfigForm","robotConfigReset","robotconfigMain","safetyPopup","save","saveMsg","sndChannel","sndOutPort","sndOutPortInfo","sndRefreshPorts","sndZoneDetail","sndZoneSel","spCancel","spConfirm","spIssues","spTarget","spTitle","status","statusBar","tabBrain","tabCam","tabFlow","tabGroups","tabMidi","tabPatches","tabPoses","tabRobotConfig","tabValidate","tabZones","uiCamToggle","uiCodeToggle","uiHudToggle","valCopyCollision","valForm","valMeshEnvColl","valMeshEnvReal","valMeshRobColl","valMeshRobReal","valMeshToolColl","valPoseGo","valPoseGrid","valProgress","valResult","valRobot3dMount","validateMain","zoneForm","zoneList","zoneMain","zoneTitle","performForm","performMain","performReset","tabPerform"];
for (const id of STATIC_IDS) { const e = new El("div"); e.attrs.id = id; ROOT.appendChild(e); registerId(e); }

// The flow canvas: report a fixed 800x400 rect (aspect 2:1) so the aspect-ratio-fit
// math is deterministic.
const flowSvgEl = document.getElementById("flowSvg");
flowSvgEl.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 400, right: 800, bottom: 400 });

// ---- Globals / sandbox ---------------------------------------------------------
const noop = () => {};
const sandbox = {
  fmt: (v) => v == null ? "—" : (Math.round(v * 10) / 10).toFixed(1),
  console,
  document,
  fetch: () => Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) }),
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

// Seed TABLE with a chain plus a bridge.
//   init(0) -> b(1) -> c(2)
//   b also -> bridge, and bridge -> c  (bridge is a shortcut between b and c)
//   bridge has pose "bridge".
// BFS depths: init=0, b=1, c=2, bridge=2. The fractional override then sets bridge
// depth = mean(neighbours' BFS depths) = mean(c=2, b=1) = 1.5.
const zones = {
  init:   { actions: { a: { next: { zone: "b", action: "a" } } } },
  b:      { actions: { a: { next: [ { zone: "c", action: "a" }, { zone: "bridge", action: "a" } ] } } },
  c:      { actions: { a: {} } },
  bridge: { pose: "bridge", actions: { a: { next: { zone: "c", action: "a" } } } },
};
vm.runInContext("TABLE = { zones: " + JSON.stringify(zones) + " };", ctx);

const getVar = (n) => vm.runInContext(n, ctx);
let failures = 0;
function assert(cond, msg) { if (!cond) { failures++; console.log("FAIL:", msg); } else { console.log("ok:", msg); } }

// --- Test 1: flowAutoLayout bridge placement -------------------------------------
vm.runInContext("flowAutoLayout();", ctx);
const pos = getVar("FLOW.pos");
// Columns (x = 40 + d*(NODE_W+GX), GX=70, NODE_W=140 -> step 210):
//   init depth 0 -> x=40; b depth 1 -> x=250; bridge depth 1.5 -> x=355; c depth 2 -> x=460.
assert(pos.init.x === 40, "init at column 0: x=" + pos.init.x);
assert(pos.b.x === 250, "b at column 1: x=" + pos.b.x);
assert(pos.c.x === 460, "c at column 2: x=" + pos.c.x);
assert(pos.bridge.x === 355, "bridge at fractional column 1.5 (between b=250 and c=460): x=" + pos.bridge.x);
// Vertical centring: b and c each have 1 action -> nodeH=78, centres at y=40+39=79.
// The bridge centring pass sets y = midpoint(79,79) - BRIDGE_CY(16) = 63.
assert(pos.bridge.y === 63, "bridge centred vertically at the midpoint of its endpoints: y=" + pos.bridge.y);
// The bridge's dot centre (y + BRIDGE_CY) must equal the midpoint of the endpoints' centres.
assert(pos.bridge.y + 16 === 79, "bridge dot lands at the endpoints' vertical midpoint");

// --- Test 2: flowFitView aspect + clamp + centring --------------------------------
vm.runInContext("flowFitView();", ctx);
const v = getVar("FLOW.view");
// Content bounds: x in [40, 600], y in [40, 118] -> centre (320, 79). PAD=40.
// cw=640, ch=158; canvas aspect 800/400=2 > 640/158, so vh=cw/2=320, vw=640.
// s=640/800=0.8 (within [0.35, 6.0]) -> kept. view = {x:0, y:-81, w:640, h:320}.
assert(v.w / v.h === 2, "fit view aspect matches the canvas (2:1): " + v.w + "x" + v.h);
assert(v.x + v.w / 2 === 320, "fit view centred on content x: " + (v.x + v.w / 2));
assert(v.y + v.h / 2 === 79, "fit view centred on content y: " + (v.y + v.h / 2));

// --- Test 3: flowFitView clamps the zoom (huge content must not over-shrink) ----
// Reset positions to a wide spread so the natural fit scale drops below the MIN clamp.
vm.runInContext("FLOW.pos = { a: {x:0,y:0}, b: {x:0,y:4000}, c: {x:0,y:8000} }; TABLE.zones = { a:{actions:{}}, b:{actions:{}}, c:{actions:{}} }; flowFitView();", ctx);
const v2 = getVar("FLOW.view");
const s2 = v2.w / 800;
assert(s2 >= 0.35, "fit scale clamped to at least MIN (0.35): s=" + s2.toFixed(3));

console.log(failures === 0 ? "ALL_TESTS_PASSED" : "FAILURES=" + failures);
process.exit(failures === 0 ? 0 : 1);
