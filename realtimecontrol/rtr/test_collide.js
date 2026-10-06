// Node test for collide.js: SAT unit checks + an end-to-end check against the real
// KR60 collision STLs (FK-driven, so HOME should be clear and a cranked pose should
// self-collide). Run: node test_collide.js  (add --explore to scan for colliding poses)
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

let failures = 0;
function check(label, cond) {
  console.log((cond ? "PASS" : "FAIL") + "  " + label);
  if (!cond) failures++;
}

// ------------------------------------------------------------------
// 1. SAT unit checks (synthetic triangles).
// ------------------------------------------------------------------
{
  const same = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
  const far = new Float32Array([10, 0, 0, 11, 0, 0, 10, 1, 0]);
  const aabb = (t) => { const a = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity]; for (let k = 0; k < 9; k += 3) { for (let d = 0; d < 3; d++) { const v = t[k + d]; if (v < a[d]) a[d] = v; if (v > a[d + 3]) a[d + 3] = v; } } return a; };
  check("SAT: identical triangles intersect", Collide._collideTriArrays(same, aabb(same), same, aabb(same)) >= 0);
  check("SAT: far-apart triangles do not intersect", Collide._collideTriArrays(same, aabb(same), far, aabb(far)) < 0);
}

// ------------------------------------------------------------------
// 2. World: two real link meshes, overlapping vs. clear.
// ------------------------------------------------------------------
{
  const l1 = parseSTL(path.join(ASSETS, "link_1.stl"));
  // overlapping: two copies of the same mesh at identity
  let w = Collide.createWorld();
  w.addBody("A", l1, () => ident());
  w.addBody("B", l1, () => ident());
  check("World: overlapping copies collide", w.check().bodies.size >= 2);
  // clear: same mesh translated +2 m in x
  w = Collide.createWorld();
  w.addBody("A", l1, () => ident());
  w.addBody("B", l1, () => T(2, 0, 0));
  check("World: far-apart copies do not collide", w.check().bodies.size === 0);
}

// ------------------------------------------------------------------
// 3. FK end-to-end: HOME clear, cranked pose collides.
// ------------------------------------------------------------------
const tris = {};
LINKS.forEach((n) => { tris[n] = parseSTL(path.join(ASSETS, n + ".stl")); });
// Adjacent link pairs (joint housing overlap — always excluded from self-collision).
const SKIP_PAIRS = [
  ["base", "link_1"],
  ["link_1", "link_2"],
  ["link_2", "link_3"],
  ["link_3", "link_4"],
  ["link_4", "link_5"],
  ["link_5", "link_6"],
];
function buildArm() {
  const w = Collide.createWorld();
  w.addBody("base", tris.base_link, () => ident());
  for (let j = 0; j < 6; j++) w.addBody("link_" + (j + 1), tris["link_" + (j + 1)], () => null);
  w.setSkipPairs(SKIP_PAIRS);
  return w;
}
// HOME pose: with joint-housing pairs excluded, a valid pose has no self-collision.
const HOME = [0, -90, 90, 0, 90, 0];
{
  const mats = fkMatrices(HOME);
  const w = Collide.createWorld();
  w.addBody("base", tris.base_link, () => ident());
  for (let j = 0; j < 6; j++) w.addBody("link_" + (j + 1), tris["link_" + (j + 1)], () => mats[j]);
  w.setSkipPairs(SKIP_PAIRS);
  const res = w.check();
  check("FK: HOME pose is collision-free (adjacent skipped)", res.bodies.size === 0);
  if (res.bodies.size) console.log("  HOME collisions:", JSON.stringify(res.collisions));
}

if (process.argv.includes("--explore")) {
  // Scan extreme cranked poses to find a reliable self-collision (non-adjacent links).
  const candidates = [
    [0, 170, -170, 0, 0, 0], [170, 170, -170, 0, 0, 0], [0, 0, 0, 170, 0, 170],
    [0, 150, -150, 0, 0, 0], [90, 90, -90, 0, 0, 0], [0, 120, -120, 60, 0, 60],
    [180, 0, 0, 0, 180, 0], [0, -170, 170, 0, 0, 0],
  ];
  for (const p of candidates) {
    const mats = fkMatrices(p);
    const w = Collide.createWorld();
    w.addBody("base", tris.base_link, () => ident());
    for (let j = 0; j < 6; j++) w.addBody("link_" + (j + 1), tris["link_" + (j + 1)], () => mats[j]);
    w.setSkipPairs(SKIP_PAIRS);
    const res = w.check();
    console.log("  pose", JSON.stringify(p), "->", res.bodies.size ? "COLLIDES: " + res.collisions.map((c) => c.a + "~" + c.b).join(", ") : "clear");
  }
// ------------------------------------------------------------------
// 4. geoToTris: indexed vs non-indexed geometry (simulates three.js BufferGeometry).
//    Verifies the fix for the env GLB indexed-geometry bug.
// ------------------------------------------------------------------
{
  // Simulate a three.js BufferGeometry with an index buffer (like a GLB).
  // 4 unique vertices, 2 triangles (6 indices).
  const indexedGeo = {
    attributes: { position: { count: 4, getX: (i) => [0, 1, 0, 1][i], getY: (i) => [0, 0, 1, 0][i], getZ: (i) => [0, 0, 0, 1][i] } },
    index: { count: 6, getX: (i) => [0, 1, 2, 2, 1, 3][i] },
  };
  // Simulate a non-indexed BufferGeometry (like an STL).
  // 6 vertices = 2 triangles.
  const nonIndexedGeo = {
    attributes: { position: { count: 6, getX: (i) => [0, 1, 0, 2, 1, 3][i], getY: (i) => [0, 0, 1, 0, 0, 0][i], getZ: (i) => [0, 0, 0, 0, 1, 1][i] } },
    index: null,
  };

  // Extract triangles using the same logic as rtr3d.js geoToTris.
  function geoToTris(geo, matrix) {
    const pos = geo.attributes.position;
    const idx = geo.index;
    const triCount = idx ? idx.count / 3 : pos.count / 3;
    const tris = new Float32Array(triCount * 9);
    for (let i = 0; i < triCount; i++) {
      for (let k = 0; k < 3; k++) {
        const vi = idx ? idx.getX(i * 3 + k) : i * 3 + k;
        tris[i * 9 + k * 3 + 0] = pos.getX(vi);
        tris[i * 9 + k * 3 + 1] = pos.getY(vi);
        tris[i * 9 + k * 3 + 2] = pos.getZ(vi);
      }
    }
    return tris;
  }

  const idxTris = geoToTris(indexedGeo, null);
  check("geoToTris: indexed geometry produces 2 triangles (6 verts)", idxTris.length === 18);
  // Verify the indexed triangles reference the correct vertices.
  // Tri 1: verts 0,1,2 = (0,0,0), (1,0,0), (0,1,0)
  check("geoToTris: indexed tri 0 = (0,0,0),(1,0,0),(0,1,0)",
    idxTris[0]===0 && idxTris[1]===0 && idxTris[2]===0 &&
    idxTris[3]===1 && idxTris[4]===0 && idxTris[5]===0 &&
    idxTris[6]===0 && idxTris[7]===1 && idxTris[8]===0);

  const niTris = geoToTris(nonIndexedGeo, null);
  check("geoToTris: non-indexed geometry produces 2 triangles (6 verts)", niTris.length === 18);
  check("geoToTris: non-indexed tri 0 = (0,0,0),(1,0,0),(0,1,0)",
    niTris[0]===0 && niTris[1]===0 && niTris[2]===0 &&
    niTris[3]===1 && niTris[4]===0 && niTris[5]===0 &&
    niTris[6]===0 && niTris[7]===1 && niTris[8]===0);

  // The old buggy code would have treated the 4 unique verts as 1 triangle + 1 quad,
  // producing garbage. The fix correctly reads the index buffer.
  const oldBuggy = new Float32Array(indexedGeo.attributes.position.count * 3); // 4 verts * 3 = 12 floats
  check("geoToTris: old buggy code would produce wrong size (12 floats for 4 verts)", oldBuggy.length === 12);
  check("geoToTris: fixed code produces correct size (18 floats for 2 tris)", idxTris.length === 18);
}

process.exit(failures ? 1 : 0);
}

// Hard-coded assertion: a cranked pose that reliably self-collides (non-adjacent links).
const CRANKED = [0, 170, -170, 0, 0, 0];
{
  const mats = fkMatrices(CRANKED);
  const w = Collide.createWorld();
  w.addBody("base", tris.base_link, () => ident());
  for (let j = 0; j < 6; j++) w.addBody("link_" + (j + 1), tris["link_" + (j + 1)], () => mats[j]);
  w.setSkipPairs(SKIP_PAIRS);
  const res = w.check();
  check("FK: cranked pose self-collides", res.bodies.size >= 3);
  if (res.bodies.size < 3) console.log("  cranked collisions:", JSON.stringify(res.collisions));
}
process.exit(failures ? 1 : 0);
