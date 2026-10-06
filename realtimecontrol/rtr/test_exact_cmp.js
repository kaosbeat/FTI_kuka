// Compare the direct exact test vs a manual replica of the world's exact test,
// to find why the world path misses the base-floor collision the direct test finds.
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
function parseEnvGLB(file) {
  const buf = fs.readFileSync(file);
  const jsonLen = buf.readUInt32LE(12);
  const json = JSON.parse(buf.subarray(20, 20 + jsonLen).toString("utf8"));
  const binStart = 20 + jsonLen + 8;
  const out = [];
  for (const node of json.nodes || []) {
    if (node.mesh == null) continue;
    const mesh = json.meshes[node.mesh];
    for (const prim of mesh.primitives) {
      const pi = prim.attributes && prim.attributes.POSITION;
      if (pi === undefined) continue;
      const acc = json.accessors[pi];
      const bv = json.bufferViews[acc.bufferView];
      const base = binStart + (bv.byteOffset || 0) + (acc.byteOffset || 0);
      const nvert = acc.count;
      let idx = null;
      if (prim.indices !== undefined) {
        const ia = json.accessors[prim.indices];
        const iv = json.bufferViews[ia.bufferView];
        const ibase = binStart + (iv.byteOffset || 0) + (ia.byteOffset || 0);
        idx = new Uint16Array(buf.buffer, ibase, ia.count);
      }
      const t = node.translation || [0, 0, 0];
      const s = node.scale || [1, 1, 1];
      const q = node.rotation || [0, 0, 0, 1];
      const [qx, qy, qz, qw] = q;
      const ntri = idx ? idx.length / 3 : nvert / 3;
      const tris = new Float32Array(ntri * 9);
      for (let i = 0; i < ntri; i++) for (let v = 0; v < 3; v++) {
        const vi = idx ? idx[i * 3 + v] : i * 3 + v;
        const vx = buf.readFloatLE(base + vi * 12), vy = buf.readFloatLE(base + vi * 12 + 4), vz = buf.readFloatLE(base + vi * 12 + 8);
        let wx = vx * s[0], wy = vy * s[1], wz = vz * s[2];
        const rx = wx * (1 - 2 * (qy * qy + qz * qz)) + wy * (2 * (qx * qy - qz * qw)) + wz * (2 * (qx * qz + qy * qw));
        const ry = wx * (2 * (qx * qy + qz * qw)) + wy * (1 - 2 * (qx * qx + qz * qz)) + wz * (2 * (qx * qz - qy * qw));
        const rz = wx * (2 * (qx * qz - qy * qw)) + wy * (2 * (qy * qz + qx * qw)) + wz * (1 - 2 * (qx * qx + qy * qy));
        wx = rx + t[0]; wy = ry + t[1]; wz = rz + t[2];
        tris[i * 9 + v * 3 + 0] = wx; tris[i * 9 + v * 3 + 1] = -wz; tris[i * 9 + v * 3 + 2] = wy;
      }
      out.push({ name: node.name || "env", tris });
    }
  }
  return out;
}
// Per-triangle AABB, same layout as collide.js triAABB.
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

const envMeshes = parseEnvGLB(path.join(__dirname, "assets", "environment_collision.glb"));
const floorTris = envMeshes[0].tris;
const baseTris = parseSTL(path.join(__dirname, "assets", "kr60ha", "collision", "base_link.stl"));

const ID = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1];

// (1) Direct test.
const baseAABB = aabbs(baseTris), floorAABB = aabbs(floorTris);
const direct = Collide._collideTriArrays(baseTris, baseAABB, floorTris, floorAABB);
console.log("[direct] pair =", direct, direct >= 0 ? "COLLISION" : "clear");

// (2) Replicate the world's exact test: transform base by identity (world tris),
// compute per-tri AABBs the same way, then run the SAME collideTriArrays.
const n = baseTris.length / 9;
const wt = new Float32Array(n * 9);
const wtri = new Float32Array(n * 6);
const p = [0, 0, 0];
for (let i = 0; i < n; i++) {
  for (let k = 0; k < 9; k += 3) {
    Collide._xformPoint(ID, baseTris[i * 9 + k], baseTris[i * 9 + k + 1], baseTris[i * 9 + k + 2], p);
    wt[i * 9 + k] = p[0]; wt[i * 9 + k + 1] = p[1]; wt[i * 9 + k + 2] = p[2];
  }
  // replicate triAABB
  let mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
  for (let k = 0; k < 9; k += 3) {
    const x = wt[i * 9 + k], y = wt[i * 9 + k + 1], z = wt[i * 9 + k + 2];
    if (x < mn[0]) mn[0] = x; if (x > mx[0]) mx[0] = x;
    if (y < mn[1]) mn[1] = y; if (y > mx[1]) mx[1] = y;
    if (z < mn[2]) mn[2] = z; if (z > mx[2]) mx[2] = z;
  }
  wtri[i * 6 + 0] = mn[0]; wtri[i * 6 + 1] = mn[1]; wtri[i * 6 + 2] = mn[2];
  wtri[i * 6 + 3] = mx[0]; wtri[i * 6 + 4] = mx[1]; wtri[i * 6 + 5] = mx[2];
}
console.log("[replica] wt === baseTris:", wt.every((v, i) => v === baseTris[i]));
console.log("[replica] wtri === baseAABB:", wtri.every((v, i) => v === baseAABB[i]));
const replica = Collide._collideTriArrays(wt, wtri, floorTris, floorAABB);
console.log("[replica] pair =", replica, replica >= 0 ? "COLLISION" : "clear");
// Print the colliding pair's AABBs and verts to compare with the world path.
const ai = 8 * 6, bi = 10 * 6;
console.log("[replica] base tri8 AABB=" + Array.prototype.slice.call(wtri, ai, ai + 6).map((v) => +v.toFixed(5)).join(",") + "  floor tri10 AABB=" + Array.prototype.slice.call(floorAABB, bi, bi + 6).map((v) => +v.toFixed(5)).join(",") + "  base tri8 verts=" + Array.prototype.slice.call(wt, 8 * 9, 8 * 9 + 9).map((v) => +v.toFixed(5)).join(",") + "  floor tri10 verts=" + Array.prototype.slice.call(floorTris, 10 * 9, 10 * 9 + 9).map((v) => +v.toFixed(5)).join(","));

// (3) Run the real world check.
const w = Collide.createWorld();
w.addBody("base", baseTris, () => ID);
w.setEnv(envMeshes.map((m) => ({ name: m.name, tris: m.tris })));
const res = w.check();
console.log("[world] collisions:", JSON.stringify(res.collisions));
