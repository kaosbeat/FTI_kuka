// Debug: replicate the world env path for the base at HOME, step by step, to find why
// the world check misses the base-floor collision that the direct test finds.
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

// Exact values.
let baseMinZ = Infinity, floorMaxZ = -Infinity;
for (let i = 0; i < baseTris.length; i += 3) if (baseTris[i + 2] < baseMinZ) baseMinZ = baseTris[i + 2];
for (let i = 0; i < floorTris.length; i += 3) if (floorTris[i + 2] > floorMaxZ) floorMaxZ = floorTris[i + 2];
console.log("base minZ =", baseMinZ, " floor maxZ =", floorMaxZ);
console.log("overlap in Z:", baseMinZ < floorMaxZ ? "YES (" + (floorMaxZ - baseMinZ) + " m)" : "NO");

// Direct exact test (raw triangles, world frame).
console.log("\n[direct] base vs floor:", Collide._collideTriArrays(baseTris, aabbs(baseTris), floorTris, aabbs(floorTris)) >= 0 ? "COLLISION" : "no collision");

// Now replicate the world env path exactly.
const w = Collide.createWorld();
w.addBody("base", baseTris, () => [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]);
w.setEnv(envMeshes.map((m) => ({ name: m.name, tris: m.tris })));

// diag gives the world's view of the base local AABB and env AABB.
const d = w.diag();
console.log("\n[world diag] envAABB =", d.envAABB.map((v) => +v.toFixed(4)));
console.log("[world diag] base localAABB =", d.bodyAABBs.base.map((v) => +v.toFixed(4)));

// The world check at HOME (base identity).
const res = w.check();
console.log("[world check @ HOME] collisions:", JSON.stringify(res.collisions));

// Manually replicate the env path for the base using the world's cached world AABB.
// We can't access internals directly, so recompute the base world AABB (identity) and
// query the grid the same way collide.js does.
const baseWorldAABB = d.bodyAABBs.base; // identity matrix, so world == local
const envAABB = d.envAABB;
function aabbOverlap(a, b) {
  return !(a[3] < b[0] || a[0] > b[3] || a[4] < b[1] || a[1] > b[4] || a[5] < b[2] || a[2] > b[5]);
}
console.log("\n[manual] base worldAABB vs env AABB overlap:", aabbOverlap(baseWorldAABB, envAABB));

// Grid query replication (cellSize 0.25).
const cs = 0.25;
const ix0 = Math.floor(baseWorldAABB[0] / cs), ix1 = Math.floor(baseWorldAABB[3] / cs);
const iy0 = Math.floor(baseWorldAABB[1] / cs), iy1 = Math.floor(baseWorldAABB[4] / cs);
const iz0 = Math.floor(baseWorldAABB[2] / cs), iz1 = Math.floor(baseWorldAABB[5] / cs);
console.log("[manual] base query cells: ix[" + ix0 + "," + ix1 + "] iy[" + iy0 + "," + iy1 + "] iz[" + iz0 + "," + iz1 + "]");

// Floor binning.
const fAABB = aabbs(floorTris);
const fMin = [fAABB[0], fAABB[1], fAABB[2]], fMax = [fAABB[3], fAABB[4], fAABB[5]];
const fix0 = Math.floor(fMin[0] / cs), fix1 = Math.floor(fMax[0] / cs);
const fiy0 = Math.floor(fMin[1] / cs), fiy1 = Math.floor(fMax[1] / cs);
const fiz0 = Math.floor(fMin[2] / cs), fiz1 = Math.floor(fMax[2] / cs);
console.log("[manual] floor AABB:", fMin.map((v) => +v.toFixed(4)), fMax.map((v) => +v.toFixed(4)));
console.log("[manual] floor bin cells: ix[" + fix0 + "," + fix1 + "] iy[" + fiy0 + "," + fiy1 + "] iz[" + fiz0 + "," + fiz1 + "]");

// Check if any base query cell coincides with a floor bin cell.
let shared = false;
for (let ix = ix0; ix <= ix1; ix++)
  for (let iy = iy0; iy <= iy1; iy++)
    for (let iz = iz0; iz <= iz1; iz++)
      if (ix >= fix0 && ix <= fix1 && iy >= fiy0 && iy <= fiy1 && iz >= fiz0 && iz <= fiz1) shared = true;
console.log("[manual] base query overlaps floor bin cells:", shared);

// The per-mesh AABB overlap check inside the world loop.
console.log("[manual] base worldAABB vs floor AABB overlap:", aabbOverlap(baseWorldAABB, fAABB));
