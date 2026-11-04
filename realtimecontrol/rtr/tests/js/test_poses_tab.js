// Node DOM-stub test for the poses tab in editor.html: every pose of a selected
// action is loadable (not just poses[0]), plus the pose-list controls (prev/next,
// + pose, per-row copy/reorder/delete) and the save-slot picker (replace vs append).
//
// Loads the editor's main <script> into a vm sandbox backed by a small hand-rolled
// DOM (mirrors test_flow_tab.js), seeds a TABLE with a multi-pose action, then
// verifies:
//   (1) buildPosesList renders one row per pose (all poses, not just poses[0]).
//   (2) posesLoad loads the ACTIVE pose (a non-first pose is reachable).
//   (3) posesPrev / posesNext step the active index and load.
//   (4) posesAddPose honours the slot picker (replace keeps length; append grows).
//   (5) posesReorder swaps adjacent poses (+ the parallel pose_imgs / pose_videos).
//   (6) posesDelete removes a pose (+ the parallel arrays).
//   (7) posesListNew appends the scratch as a new pose and selects it.
//   (8) posesCopyAt / posesCopy set POSE_CLIP.
//   (9) posesAddPose keeps the "save to" / load-from selections (no reset) and
//       keeps the pose list rendered (no collapse).
//   (10) posesLoad renders the pose list (visible as soon as an action is loaded).
//
// Run: node tests/js/test_poses_tab.js
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

// ---- Mini-DOM (mirrors test_flow_tab.js) --------------------------------------
const IDX = {};
function registerId(el) { const id = el.attrs.id; if (id) IDX[id] = el; }
function matchOne(el, part) {
  const toks = [];
  const r = /([a-zA-Z][a-zA-Z0-9_-]*)|\.([a-zA-Z][a-zA-Z0-9_-]*)|#([a-zA-Z][a-zA-Z0-9]+)|\[([a-zA-Z_][a-zA-Z0-9_-]*)(?:=([^\]]*))?\]/g;
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
  getElementById: (id) => IDX[id] || null,
  querySelectorAll: (sel) => ROOT.querySelectorAll(sel),
  querySelector: (sel) => ROOT.querySelector(sel),
  body: ROOT,
  documentElement: ROOT,
  addEventListener() {},
  removeEventListener() {},
  createTextNode: (t) => { const e = new El("#text"); e._text = t; return e; },
};
// Collect every element id the main script references via $("...") / getElementById(...),
// and give the mini-DOM a node for each (auto-derived, so new ids are picked up without
// hand-editing the list).
const idSet = new Set();
const idRe = /(?:\$|getElementById)\(\s*["']([a-zA-Z0-9_]+)["']\s*\)/g;
let idm;
while ((idm = idRe.exec(SRC)) !== null) idSet.add(idm[1]);
const STATIC_IDS = [...idSet].sort();
for (const id of STATIC_IDS) { const e = new El("div"); e.attrs.id = id; ROOT.appendChild(e); registerId(e); }

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

// Seed TABLE: zone "z" with action "a" (3 poses + parallel arrays) and "b" (no poses).
const P0 = [10, 20, 30, 40, 50, 60];
const P1 = [1, 2, 3, 4, 5, 6];
const P2 = [7, 8, 9, 10, 11, 12];
const zones = {
  z: {
    safezone: [[-180, 180], [-180, 180], [-180, 180], [-180, 180], [-180, 180], [-180, 180]],
    actions: {
      a: {
        poses: [P0, P1, P2],
        pose_imgs: ["p0.jpg", null, "p2.jpg"],
        pose_videos: [null, "v1.mp4", null],
      },
      b: { poses: [] },
    },
  },
};
vm.runInContext("TABLE = { zones: " + JSON.stringify(zones) + " };", ctx);

// Stub the whole-table save so the pose mutations are testable in-memory (the real
// saveZones() POSTs the table + runs validate(); we only care about the in-memory
// mutations here). Record calls so we can assert a save was triggered.
vm.runInContext("window.__saveCount = 0; saveZones = function() { window.__saveCount++; };", ctx);

const getVar = (n) => vm.runInContext(n, ctx);
let failures = 0;
function assert(cond, msg) { if (!cond) { failures++; console.log("FAIL:", msg); } else { console.log("ok:", msg); } }

// Build the pickers + grid (mirrors posesEnter() minus the 3D mount).
vm.runInContext("buildPosesLoadPicker(); buildPosesSavePicker(); buildPosesSlotPicker(); buildPosesGrid();", ctx);

// --- Test 1: the pose list renders every pose (not just poses[0]) --------------
vm.runInContext('document.getElementById("posesLoadSel").value = "z/a"; posesCurIndex = 0; buildPosesList();', ctx);
assert(getVar('document.getElementById("posesList").children.length') === 3, "pose list renders all 3 poses, not just poses[0]");
assert(getVar('document.getElementById("posesListInfo").textContent').includes("pose 1 of 3"), "list info shows the active pose position");

// --- Test 2: posesLoad loads the ACTIVE pose (a non-first pose is reachable) ---
vm.runInContext('document.getElementById("posesLoadSel").value = "z/a"; posesCurIndex = 1; posesLoad();', ctx);
assert(JSON.stringify(getVar("scratchPose")) === JSON.stringify(P1), "posesLoad loads the active (2nd) pose, not poses[0]");
vm.runInContext('posesCurIndex = 2; posesLoad();', ctx);
assert(JSON.stringify(getVar("scratchPose")) === JSON.stringify(P2), "posesLoad loads the 3rd pose");

// --- Test 3: posesPrev / posesNext step the active index and load -------------
vm.runInContext('document.getElementById("posesLoadSel").value = "z/a"; posesCurIndex = 1; posesNext();', ctx);
assert(getVar("posesCurIndex") === 2, "posesNext steps forward to index 2");
assert(JSON.stringify(getVar("scratchPose")) === JSON.stringify(P2), "posesNext loads pose 3");
vm.runInContext('posesPrev();', ctx);
assert(getVar("posesCurIndex") === 1, "posesPrev steps back to index 1");
assert(JSON.stringify(getVar("scratchPose")) === JSON.stringify(P1), "posesPrev loads pose 2");
// Clamp at the ends: prev at index 0 stays at 0.
vm.runInContext('posesPrev(); posesPrev();', ctx);
assert(getVar("posesCurIndex") === 0, "posesPrev clamps at index 0");

// --- Test 4: posesAddPose honours the slot picker (replace vs append) ---------
// Replace slot 2 (index 1): keeps the pose count, swaps the pose, keeps the image.
vm.runInContext('document.getElementById("posesSaveSel").value = "z/a"; document.getElementById("posesSlotSel").value = "2"; scratchPose = [99,99,99,99,99,99]; posesAddPose();', ctx);
let a = getVar("TABLE.zones.z.actions.a");
assert(a.poses.length === 3, "slot replace keeps the pose count");
assert(JSON.stringify(a.poses[1]) === JSON.stringify([99, 99, 99, 99, 99, 99]), "slot replace writes the scratch into slot 2");
assert(a.pose_imgs[1] === null, "slot replace keeps the slot's image (untouched)");
// Append: the pose list grows.
vm.runInContext('document.getElementById("posesSlotSel").value = "append"; scratchPose = [5,5,5,5,5,5]; posesAddPose();', ctx);
a = getVar("TABLE.zones.z.actions.a");
assert(a.poses.length === 4, "append grows the pose list");
assert(JSON.stringify(a.poses[3]) === JSON.stringify([5, 5, 5, 5, 5, 5]), "append adds the scratch as the new last pose");

// --- Test 5: posesReorder swaps adjacent poses (+ the parallel arrays) --------
vm.runInContext('TABLE.zones.z.actions.a.poses = [[' + P0 + '],[' + P1 + '],[' + P2 + ']]; TABLE.zones.z.actions.a.pose_imgs = ["p0.jpg", null, "p2.jpg"]; TABLE.zones.z.actions.a.pose_videos = [null, "v1.mp4", null]; document.getElementById("posesLoadSel").value = "z/a"; posesCurIndex = 0;', ctx);
vm.runInContext("posesReorder(0, 1);", ctx);  // move pose 0 down to index 1
a = getVar("TABLE.zones.z.actions.a");
assert(JSON.stringify(a.poses[0]) === JSON.stringify(P1), "reorder: pose 1 moves up to index 0");
assert(JSON.stringify(a.poses[1]) === JSON.stringify(P0), "reorder: pose 0 moves down to index 1");
assert(a.pose_imgs[0] === null && a.pose_imgs[1] === "p0.jpg", "reorder swaps the parallel pose_imgs");
assert(a.pose_videos[0] === "v1.mp4" && a.pose_videos[1] === null, "reorder swaps the parallel pose_videos");

// --- Test 6: posesDelete removes a pose (+ the parallel arrays) ---------------
vm.runInContext('TABLE.zones.z.actions.a.poses = [[' + P0 + '],[' + P1 + '],[' + P2 + ']]; TABLE.zones.z.actions.a.pose_imgs = ["p0.jpg", null, "p2.jpg"]; TABLE.zones.z.actions.a.pose_videos = [null, "v1.mp4", null]; document.getElementById("posesLoadSel").value = "z/a"; posesCurIndex = 1;', ctx);
vm.runInContext("posesDelete(1);", ctx);  // delete the middle pose
a = getVar("TABLE.zones.z.actions.a");
assert(a.poses.length === 2, "delete shrinks the pose list");
assert(JSON.stringify(a.poses[0]) === JSON.stringify(P0) && JSON.stringify(a.poses[1]) === JSON.stringify(P2), "delete removes the middle pose");
assert(a.pose_imgs.length === 2 && a.pose_imgs[0] === "p0.jpg" && a.pose_imgs[1] === "p2.jpg", "delete removes the parallel pose_imgs entry");
assert(a.pose_videos.length === 2 && a.pose_videos[0] === null && a.pose_videos[1] === null, "delete removes the parallel pose_videos entry");

// --- Test 7: posesListNew appends the scratch as a new pose and selects it ----
vm.runInContext('scratchPose = [42,42,42,42,42,42]; posesListNew();', ctx);
a = getVar("TABLE.zones.z.actions.a");
assert(a.poses.length === 3, "new pose grows the working action's list");
assert(JSON.stringify(a.poses[2]) === JSON.stringify([42, 42, 42, 42, 42, 42]), "new pose appends the scratch");
assert(getVar("posesCurIndex") === 2, "new pose selects the appended pose");

// --- Test 8: posesCopyAt / posesCopy set POSE_CLIP ----------------------------
vm.runInContext('document.getElementById("posesLoadSel").value = "z/a"; posesCopyAt(0);', ctx);
assert(JSON.stringify(getVar("POSE_CLIP")) === JSON.stringify(P0), "posesCopyAt copies the chosen pose to POSE_CLIP");
vm.runInContext('scratchPose = [3,3,3,3,3,3]; posesCopy();', ctx);
assert(JSON.stringify(getVar("POSE_CLIP")) === JSON.stringify([3, 3, 3, 3, 3, 3]), "posesCopy copies the scratch to POSE_CLIP");

// --- Test 9: saving keeps the "save to" selection + the pose list visible ----
// (regression: the picker rebuilds inside posesAddPose used to reset both pickers
// to the placeholder, which collapsed the pose list on every save.)
vm.runInContext('document.getElementById("posesLoadSel").value = "z/a"; document.getElementById("posesSaveSel").value = "z/a"; scratchPose = [4,4,4,4,4,4]; posesAddPose();', ctx);
assert(getVar('document.getElementById("posesSaveSel").value') === "z/a", "save keeps the 'save to' selection");
assert(getVar('document.getElementById("posesLoadSel").value') === "z/a", "save keeps the working action (load picker)");
a = getVar("TABLE.zones.z.actions.a");
assert(getVar('document.getElementById("posesList").children.length') === a.poses.length, "save keeps the pose list rendered (no collapse)");

// --- Test 10: pressing load makes the pose list visible -----------------------
vm.runInContext('document.getElementById("posesList").innerHTML = ""; document.getElementById("posesLoadSel").value = "z/a"; posesCurIndex = 1; posesLoad();', ctx);
assert(getVar('document.getElementById("posesList").children.length') > 0, "posesLoad renders the pose list (visible on load)");

// --- Sanity: the mutating operations each triggered a save --------------------
assert(getVar("window.__saveCount") >= 5, "pose mutations triggered saveZones (count=" + getVar("window.__saveCount") + ")");

console.log(failures === 0 ? "ALL_TESTS_PASSED" : "FAILURES=" + failures);
