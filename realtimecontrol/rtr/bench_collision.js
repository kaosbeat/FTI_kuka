// Benchmark the collision engine cost after the worldTris per-tri-AABB fix.
// Builds the full robot (base + 6 links) against the low-res 3-cube env and times
// checkWithMatrices over many iterations at a HOME (collision-free) pose.
const Collide = require("./collide.js");
const fs = require("fs");
const path = require("path");

const ASSETS = path.join(__dirname, "assets", "kr60ha", "collision");

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

const JOINTS = [
  { xyz: [0, 0, 0], axis: [0, 0, -1] },
  { xyz: [0.35, 0, 0.815], axis: [0, 1, 0] },
  { xyz: [0.85, 0, 0], axis: [0, 1, 0] },
  { xyz: [0.465, 0, 0.145], axis: [-1, 0, 0] },
  { xyz: [0.355, 0, 0], axis: [0, 1, 0] },
  { xyz: [0.17, 0, 0], axis: [-1, 0, 0] },
];
function ident() { return [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]; }
function fkMatrices(deg) {
  const rad = deg.map((d) => d * Math.PI / 180);
  const world = [ident()];
  for (let j = 0; j < 6; j++) {
    const jt = JOINTS[j];
    const parent = j === 0 ? ident() : world[j - 1];
    const c = Math.cos(rad[j]), s = Math.sin(rad[j]), t = 1 - c;
    const ax = jt.axis[0], ay = jt.axis[1], az = jt.axis[2];
    const Rm = [
      c + ax*ax*t, ay*ax*t + az*s, az*ax*t - ay*s, 0,
      ax*ay*t - az*s, c + ay*ay*t, ay*az*t + ax*s, 0,
      ax*az*t + ay*s, ay*az*t - ax*s, c + az*az*t, 0,
      0, 0, 0, 1
    ];
    const Tm = parent.slice();
    Tm[12] = parent[0]*jt.xyz[0] + parent[4]*jt.xyz[1] + parent[8]*jt.xyz[2] + parent[12];
    Tm[13] = parent[1]*jt.xyz[0] + parent[5]*jt.xyz[1] + parent[9]*jt.xyz[2] + parent[13];
    Tm[14] = parent[2]*jt.xyz[0] + parent[6]*jt.xyz[1] + parent[10]*jt.xyz[2] + parent[14];
    const res = new Array(16);
    for (let col = 0; col < 4; col++)
      for (let row = 0; row < 4; row++) {
        let sum = 0;
        for (let k = 0; k < 4; k++) sum += Tm[k * 4 + row] * Rm[col * 4 + k];
        res[col * 4 + row] = sum;
      }
    world[j] = res;
  }
  return world;
}

const LINKS = ["base_link", "link_1", "link_2", "link_3", "link_4", "link_5", "link_6"];
const SKIP_PAIRS = [
  ["base", "link_1"], ["link_1", "link_2"], ["link_2", "link_3"],
  ["link_3", "link_4"], ["link_4", "link_5"], ["link_5", "link_6"],
];
const stl = {};
LINKS.forEach((n) => { stl[n] = parseSTL(path.join(ASSETS, n + ".stl")); });
const envMeshes = parseEnvGLB(path.join(__dirname, "assets", "environment_collision.glb"));

const w = Collide.createWorld();
w.addBody("base", stl.base_link, () => ident());
for (let j = 0; j < 6; j++) w.addBody("link_" + (j + 1), stl["link_" + (j + 1)], () => null);
w.setSkipPairs(SKIP_PAIRS);
w.setSkipEnv(["base"]);
w.setEnv(envMeshes.map((m) => ({ name: m.name, tris: m.tris })));

const HOME = [0, -90, 90, 0, 90, 0];
const hm = fkMatrices(HOME);
const mm = { base: ident() };
for (let j = 0; j < 6; j++) mm["link_" + (j + 1)] = hm[j];

// Warmup.
for (let i = 0; i < 20; i++) w.checkWithMatrices(mm);

const N = 500;
let total = 0, max = 0;
const t0 = process.hrtime.bigint();
for (let i = 0; i < N; i++) {
  const s = process.hrtime.bigint();
  w.checkWithMatrices(mm);
  const e = process.hrtime.bigint();
  const dt = Number(e - s) / 1e6; // ms
  total += dt;
  if (dt > max) max = dt;
}
const wall = Number(process.hrtime.bigint() - t0) / 1e6;
console.log("HOME pose (collision-free), full robot + 3-cube env:");
console.log("  N =", N);
console.log("  avg =", (total / N).toFixed(3), "ms");
console.log("  max =", max.toFixed(3), "ms");
console.log("  wall =", wall.toFixed(1), "ms total");
console.log("  collisions:", JSON.stringify(w.checkWithMatrices(mm).collisions));
