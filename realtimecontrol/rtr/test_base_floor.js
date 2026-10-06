// Direct test: does base_link (identity) intersect the floor (Cube, +90deg X)?
// Prints the base's triangles that dip below Z=0 and runs the exact tri test base-vs-floor.
const Collide = require("./collide.js");
const fs = require("fs");
const path = require("path");

function parseSTL(file) {
  const buf = fs.readFileSync(file);
  const n = buf.readUInt32LE(80);
  const tris = new Float32Array(n * 9);
  let off = 84;
  for (let t = 0; t < n; t++) {
    off += 12;
    for (let v = 0; v < 3; v++) {
      tris[t * 9 + v * 3 + 0] = buf.readFloatLE(off);
      tris[t * 9 + v * 3 + 1] = buf.readFloatLE(off + 4);
      tris[t * 9 + v * 3 + 2] = buf.readFloatLE(off + 8);
      off += 12;
    }
    off += 2;
  }
  return tris;
}

// Parse just the floor (node "Cube", mesh 0) with +90deg X.
function parseFloor(file) {
  const buf = fs.readFileSync(file);
  const jsonLen = buf.readUInt32LE(12);
  const json = JSON.parse(buf.subarray(20, 20 + jsonLen).toString("utf8"));
  const binStart = 20 + jsonLen + 8;
  const node = json.nodes[0]; // "Cube" = floor
  const mesh = json.meshes[node.mesh];
  const prim = mesh.primitives[0];
  const acc = json.accessors[prim.attributes.POSITION];
  const bv = json.bufferViews[acc.bufferView];
  const base = binStart + (bv.byteOffset || 0) + (acc.byteOffset || 0);
  const nvert = acc.count;
  const ia = json.accessors[prim.indices];
  const iv = json.bufferViews[ia.bufferView];
  const ibase = binStart + (iv.byteOffset || 0) + (ia.byteOffset || 0);
  const idx = new Uint16Array(buf.buffer, ibase, ia.count);
  const t = node.translation || [0, 0, 0], s = node.scale || [1, 1, 1];
  const ntri = idx.length / 3;
  const tris = new Float32Array(ntri * 9);
  for (let i = 0; i < ntri; i++) for (let v = 0; v < 3; v++) {
    const vi = idx[i * 3 + v];
    const vx = buf.readFloatLE(base + vi * 12), vy = buf.readFloatLE(base + vi * 12 + 4), vz = buf.readFloatLE(base + vi * 12 + 8);
    const wx = vx * s[0], wy = vy * s[1], wz = vz * s[2];
    // +90deg X: (x,y,z) -> (x, -z, y); then translate
    tris[i * 9 + v * 3 + 0] = wx + t[0];
    tris[i * 9 + v * 3 + 1] = -wz + t[1];
    tris[i * 9 + v * 3 + 2] = wy + t[2];
  }
  return tris;
}

const baseTris = parseSTL(path.join(__dirname, "assets", "kr60ha", "collision", "base_link.stl"));
const floorTris = parseFloor(path.join(__dirname, "assets", "environment_collision.glb"));

// Base AABB
let ba = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
for (let i = 0; i < baseTris.length; i += 3) for (let k = 0; k < 3; k++) {
  const v = baseTris[i + k];
  if (v < ba[k]) ba[k] = v; if (v > ba[k + 3]) ba[k + 3] = v;
}
console.log("base_link AABB: X[" + ba[0].toFixed(3) + "," + ba[3].toFixed(3) + "] Y[" + ba[1].toFixed(3) + "," + ba[4].toFixed(3) + "] Z[" + ba[2].toFixed(3) + "," + ba[5].toFixed(3) + "]");

// Floor AABB
let fa = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
for (let i = 0; i < floorTris.length; i += 3) for (let k = 0; k < 3; k++) {
  const v = floorTris[i + k];
  if (v < fa[k]) fa[k] = v; if (v > fa[k + 3]) fa[k + 3] = v;
}
console.log("floor AABB:       X[" + fa[0].toFixed(3) + "," + fa[3].toFixed(3) + "] Y[" + fa[1].toFixed(3) + "," + fa[4].toFixed(3) + "] Z[" + fa[2].toFixed(3) + "," + fa[5].toFixed(3) + "]");

// Base triangles that dip below Z=0 (into the floor region).
const nBase = baseTris.length / 9;
let lowCount = 0, deepest = 0;
const lowTris = [];
for (let i = 0; i < nBase; i++) {
  let minZ = Infinity, maxX = -Infinity, minX = Infinity, maxY = -Infinity, minY = Infinity;
  for (let k = 0; k < 9; k += 3) {
    const x = baseTris[i * 9 + k], y = baseTris[i * 9 + k + 1], z = baseTris[i * 9 + k + 2];
    if (z < minZ) minZ = z;
    if (x < minX) minX = x; if (x > maxX) maxX = x;
    if (y < minY) minY = y; if (y > maxY) maxY = y;
  }
  if (minZ < 0) {
    lowCount++;
    if (minZ < deepest) deepest = minZ;
    if (lowTris.length < 8) lowTris.push({ i, minZ: +minZ.toFixed(3), x: [ +minX.toFixed(2), +maxX.toFixed(2) ], y: [ +minY.toFixed(2), +maxY.toFixed(2) ] });
  }
}
console.log("base tris dipping below Z=0:", lowCount, "of", nBase, "deepest Z:", +deepest.toFixed(3));
lowTris.forEach((t) => console.log("  tri[" + t.i + "] minZ=" + t.minZ + " X[" + t.x[0] + "," + t.x[1] + "] Y[" + t.y[0] + "," + t.y[1] + "]"));

// Direct exact test base vs floor (same frame, world).
function aabbs(tris) {
  const n = tris.length / 9, a = new Float32Array(n * 6);
  for (let i = 0; i < n; i++) {
    let mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
    for (let k = 0; k < 9; k += 3) {
      const x = tris[i * 9 + k], y = tris[i * 9 + k + 1], z = tris[i * 9 + k + 2];
      if (x < mn[0]) mn[0] = x; if (x > mx[0]) mx[0] = x;
      if (y < mn[1]) mn[1] = y; if (y > mx[1]) mx[1] = y;
      if (z < mn[2]) mn[2] = z; if (z > mx[2]) mx[2] = z;
    }
    a[i * 6 + 0] = mn[0]; a[i * 6 + 1] = mn[1]; a[i * 6 + 2] = mn[2];
    a[i * 6 + 3] = mx[0]; a[i * 6 + 4] = mx[1]; a[i * 6 + 5] = mx[2];
  }
  return a;
}
const baab = aabbs(baseTris), faab = aabbs(floorTris);
const hit = Collide._collideTriArrays(baseTris, baab, floorTris, faab);
console.log("\nbase vs floor exact test:", hit >= 0 ? "COLLISION at pair index " + hit : "no collision");
