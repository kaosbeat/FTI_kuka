// Reproduce the browser collision pipeline in Node: base + 6 links + env GLB.
// Verifies whether the robot (at HOME / test poses) actually intersects the env,
// and whether the env collision path (grid + SAT) fires.
const Collide = require("./collide.js");
const fs = require("fs");
const path = require("path");

const ASSETS = path.join(__dirname, "assets", "kr60ha", "collision");

// ------------------------------------------------------------------
// STL parse (binary, non-indexed) — same as the main test.
// ------------------------------------------------------------------
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

// ------------------------------------------------------------------
// GLB env parse: flat scene, per-node TRS, +90° X rotation (Y-up -> Z-up).
// ------------------------------------------------------------------
function parseEnvGLB(file) {
  const buf = fs.readFileSync(file);
  const jsonLen = buf.readUInt32LE(12);
  const json = JSON.parse(buf.subarray(20, 20 + jsonLen).toString("utf8"));
  const binHeaderOff = 20 + jsonLen;
  const binStart = binHeaderOff + 8;
  const out = [];
  for (const node of json.nodes || []) {
    if (!node.mesh) continue;
    const mesh = json.meshes[node.mesh];
    for (const prim of mesh.primitives) {
      const pi = prim.attributes && prim.attributes.POSITION;
      if (pi === undefined) continue;
      const acc = json.accessors[pi];
      const bv = json.bufferViews[acc.bufferView];
      const base = binStart + (bv.byteOffset || 0) + (acc.byteOffset || 0);
      const nvert = acc.count;
      // Indexed primitives: read the index accessor (componentType 5123 = Uint16).
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
      for (let i = 0; i < ntri; i++) {
        for (let v = 0; v < 3; v++) {
          const vi = idx ? idx[i * 3 + v] : i * 3 + v;
          const vx = buf.readFloatLE(base + vi * 12);
          const vy = buf.readFloatLE(base + vi * 12 + 4);
          const vz = buf.readFloatLE(base + vi * 12 + 8);
          let wx = vx * s[0], wy = vy * s[1], wz = vz * s[2];
          const rx = wx * (1 - 2 * (qy * qy + qz * qz)) + wy * (2 * (qx * qy - qz * qw)) + wz * (2 * (qx * qz + qy * qw));
          const ry = wx * (2 * (qx * qy + qz * qw)) + wy * (1 - 2 * (qx * qx + qz * qz)) + wz * (2 * (qy * qz - qx * qw));
          const rz = wx * (2 * (qx * qz - qy * qw)) + wy * (2 * (qy * qz + qx * qw)) + wz * (1 - 2 * (qx * qx + qy * qy));
          wx = rx + t[0]; wy = ry + t[1]; wz = rz + t[2];
          // env rotation Rx(+90): (x, y, z) -> (x, -z, y)
          tris[i * 9 + v * 3 + 0] = wx; tris[i * 9 + v * 3 + 1] = -wz; tris[i * 9 + v * 3 + 2] = wy;
        }
      }
      out.push(tris);
    }
  }
  return out;
}

// ------------------------------------------------------------------
// FK (mirrors rtr3d.js JOINTS).
// ------------------------------------------------------------------
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

// ------------------------------------------------------------------
// Build the world exactly like the browser.
// ------------------------------------------------------------------
const LINKS = ["base_link", "link_1", "link_2", "link_3", "link_4", "link_5", "link_6"];
const SKIP_PAIRS = [
  ["base", "link_1"], ["link_1", "link_2"], ["link_2", "link_3"],
  ["link_3", "link_4"], ["link_4", "link_5"], ["link_5", "link_6"],
];

const stl = {};
LINKS.forEach((n) => { stl[n] = parseSTL(path.join(ASSETS, n + ".stl")); });
const envTris = parseEnvGLB(path.join(__dirname, "assets", "environment.glb"));
console.log("env meshes:", envTris.length, "total env tris:", envTris.reduce((a, t) => a + t.length / 9, 0));

const w = Collide.createWorld();
w.addBody("base", stl.base_link, () => ident());
for (let j = 0; j < 6; j++) w.addBody("link_" + (j + 1), stl["link_" + (j + 1)], () => null);
w.setSkipPairs(SKIP_PAIRS);
w.setEnv(envTris.map((t, i) => ({ name: "env" + i, tris: t })));
console.log("env mesh count in world:", w.envMeshCount());

// For a live check, the bodies' getters must return the FK matrices.
function liveCheck(poseDeg) {
  const mats = fkMatrices(poseDeg);
  // Rebuild with live getters (the world's addBody getters are static; for a live
  // check we use checkWithMatrices with the FK matrices, which is what runCheck does).
  const matMap = { base: ident() };
  for (let j = 0; j < 6; j++) matMap["link_" + (j + 1)] = mats[j];
  return w.checkWithMatrices(matMap);
}

const HOME = [0, -90, 90, 0, 90, 0];
const home = liveCheck(HOME);
console.log("HOME:", home.bodies.size ? "COLLISION " + JSON.stringify(home.collisions) : "clear");

// Tool position at HOME (FK chain end + tool0).
const TOOL0 = { xyz: [0.15, 0, 0], rpy: [0, 0, 0] }; // placeholder; the real tool0 is in rtr3d.js
// Just report the link_6 flange position at HOME.
const m6 = fkMatrices(HOME)[5];
console.log("link_6 flange at HOME:", [m6[12], m6[13], m6[14]].map((v) => +v.toFixed(3)));

// Try a few poses that reach out / down, to see if any hit the env.
const poses = {
  HOME: HOME,
  reach_out: [0, -45, 45, 0, 90, 0],
  reach_down: [0, -90, 45, 0, 90, 0],
  stow: [0, 0, 0, 0, 0, 0],
};
for (const [name, p] of Object.entries(poses)) {
  const r = liveCheck(p);
  console.log(name + ":", r.bodies.size ? "COLLISION " + JSON.stringify(r.collisions) : "clear");
}
