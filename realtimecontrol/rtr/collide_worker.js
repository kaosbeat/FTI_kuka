// Persistent collision worker. Loads the KR60 collision STL world once, then
// answers JSON queries on stdin with a self-collision result on stdout.
//
//   in  (stdin, one JSON per line):   { "id": N, "pose": [a1..a6] }
//   out (stdout, one JSON per line):  { "id": N, "collision": bool, "collisions": [...] }
//                                     { "id": N, "collision": true, "error": "..." } on failure
//
// Self-collision only: base + link_1..link_6, adjacent joint-housing pairs skipped.
// Reuses the SAT world from collide.js and the FK chain from test_collide.js.
"use strict";
const fs = require("fs");
const path = require("path");
const Collide = require("./collide.js");

const ASSETS = path.join(__dirname, "assets", "kr60ha", "collision");
const LINKS = ["base_link", "link_1", "link_2", "link_3", "link_4", "link_5", "link_6"];

// ---- binary STL parser (Blender format: 80-byte header + uint32 count + 50 bytes/tri) ----
function parseSTL(file) {
  const buf = fs.readFileSync(file);
  const n = buf.readUInt32LE(80);
  const tris = new Float32Array(n * 9);
  let off = 84;
  for (let t = 0; t < n; t++) {
    off += 12; // normal
    for (let v = 0; v < 3; v++) {
      tris[t * 9 + v * 3 + 0] = buf.readFloatLE(off);
      tris[t * 9 + v * 3 + 1] = buf.readFloatLE(off + 4);
      tris[t * 9 + v * 3 + 2] = buf.readFloatLE(off + 8);
      off += 12;
    }
    off += 2; // attribute
  }
  return tris;
}

// ---- column-major mat4 (matches three.js Matrix4.elements) ----
function ident() { return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]; }
function mul(a, b) {
  const o = new Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
    o[c * 4 + r] = s;
  }
  return o;
}
function T(x, y, z) { return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]; }
function R(axis, ang) {
  const i = axis[0], j = axis[1], k = axis[2];
  const c = Math.cos(ang), s = Math.sin(ang), t = 1 - c;
  return [c + i * i * t, j * i * t + k * s, k * i * t - j * s, 0,
          i * j * t - k * s, c + j * j * t, j * k * t + i * s, 0,
          i * k * t + j * s, j * k * t - i * s, c + k * k * t, 0,
          0, 0, 0, 1];
}
// kr60ha chain (mirrors rtr3d.js JOINTS): world[j] = rotor j world matrix = link_{j+1} frame.
const JOINTS = [
  { xyz: [0, 0, 0],           axis: [0, 0, -1] },
  { xyz: [0.35, 0, 0.815],    axis: [0, 1, 0] },
  { xyz: [0.85, 0, 0],        axis: [0, 1, 0] },
  { xyz: [0.465, 0, 0.145],   axis: [-1, 0, 0] },
  { xyz: [0.355, 0, 0],       axis: [0, 1, 0] },
  { xyz: [0.17, 0, 0],        axis: [-1, 0, 0] },
];
function fkMatrices(deg) {
  const rad = deg.map((d) => d * Math.PI / 180);
  const world = [ident()];
  for (let j = 0; j < 6; j++) {
    const jt = JOINTS[j];
    const parent = j === 0 ? ident() : world[j - 1];
    world[j] = mul(mul(parent, T(jt.xyz[0], jt.xyz[1], jt.xyz[2])), R(jt.axis, rad[j]));
  }
  return world;
}

// Adjacent link pairs (joint housing overlap — always excluded from self-collision).
const SKIP_PAIRS = [
  ["base", "link_1"],
  ["link_1", "link_2"],
  ["link_2", "link_3"],
  ["link_3", "link_4"],
  ["link_4", "link_5"],
  ["link_5", "link_6"],
];

// ---- build the persistent world once ----
const tris = {};
LINKS.forEach((n) => { tris[n] = parseSTL(path.join(ASSETS, n + ".stl")); });
const world = Collide.createWorld();
world.addBody("base", tris.base_link, () => ident());
for (let j = 0; j < 6; j++) world.addBody("link_" + (j + 1), tris["link_" + (j + 1)], () => null);
world.setSkipPairs(SKIP_PAIRS);

// ---- stdin query loop ----
let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buf += chunk;
  let nl;
  while ((nl = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, nl).trim();
    buf = buf.slice(nl + 1);
    if (!line) continue;
    handle(line);
  }
});

function handle(line) {
  let q;
  try { q = JSON.parse(line); } catch (e) {
    return; // malformed line; ignore
  }
  const id = q.id !== undefined ? q.id : null;
  const pose = q.pose;
  if (!Array.isArray(pose) || pose.length !== 6) {
    return emit({ id: id, collision: true, error: "pose must be an array of 6 joint angles" });
  }
  try {
    const mats = fkMatrices(pose);
    const matMap = { base: ident() };
    for (let j = 0; j < 6; j++) matMap["link_" + (j + 1)] = mats[j];
    const res = world.checkWithMatrices(matMap);
    emit({ id: id, collision: res.bodies.size > 0, collisions: res.collisions });
  } catch (e) {
    emit({ id: id, collision: true, error: String(e && e.message ? e.message : e) });
  }
}

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}
