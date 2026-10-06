// Test env collision with the NEW low-res model (environment_collision.glb).
// Reports each env mesh's WORLD AABB (after +90deg X) and probes poses that should
// overlap the cubes, to confirm the env-collision path fires with the low-res model.
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

// GLB parse with node TRS + +90deg X (Y-up -> Z-up). Returns {name, tris(world)}.
function parseEnvGLB(file) {
  const buf = fs.readFileSync(file);
  const jsonLen = buf.readUInt32LE(12);
  const json = JSON.parse(buf.subarray(20, 20 + jsonLen).toString("utf8"));
  const binStart = 20 + jsonLen + 8;
  const out = [];
  for (const node of json.nodes || []) {
    if (node.mesh == null) continue; // mesh index 0 is valid; only skip null/undefined
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
      for (let i = 0; i < ntri; i++) {
        for (let v = 0; v < 3; v++) {
          const vi = idx ? idx[i * 3 + v] : i * 3 + v;
          const vx = buf.readFloatLE(base + vi * 12);
          const vy = buf.readFloatLE(base + vi * 12 + 4);
          const vz = buf.readFloatLE(base + vi * 12 + 8);
          let wx = vx * s[0], wy = vy * s[1], wz = vz * s[2];
          const rx = wx * (1 - 2 * (qy * qy + qz * qz)) + wy * (2 * (qx * qy - qz * qw)) + wz * (2 * (qx * qz + qy * qw));
          const ry = wx * (2 * (qx * qy + qz * qw)) + wy * (1 - 2 * (qx * qx + qz * qz)) + wz * (2 * (qx * qz - qy * qw));
          const rz = wx * (2 * (qx * qz - qy * qw)) + wy * (2 * (qy * qz + qx * qw)) + wz * (1 - 2 * (qx * qx + qy * qy));
          wx = rx + t[0]; wy = ry + t[1]; wz = rz + t[2];
          tris[i * 9 + v * 3 + 0] = wx; tris[i * 9 + v * 3 + 1] = -wz; tris[i * 9 + v * 3 + 2] = wy;
        }
      }
      out.push({ name: node.name || "env", tris });
    }
  }
  return out;
}

const envMeshes = parseEnvGLB(path.join(__dirname, "assets", "environment_collision.glb"));
console.log("env meshes:", envMeshes.length);
for (const m of envMeshes) {
  const n = m.tris.length / 9;
  let a = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
  for (let i = 0; i < n; i++) for (let k = 0; k < 3; k++) {
    const v = m.tris[i * 9 + k];
    if (v < a[k]) a[k] = v; if (v > a[k + 3]) a[k + 3] = v;
  }
  console.log("  " + m.name + " world AABB: X[" + a[0].toFixed(2) + "," + a[3].toFixed(2) + "] Y[" + a[1].toFixed(2) + "," + a[4].toFixed(2) + "] Z[" + a[2].toFixed(2) + "," + a[5].toFixed(2) + "]");
}

// FK (mirrors rtr3d.js JOINTS).
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

const w = Collide.createWorld();
w.addBody("base", stl.base_link, () => ident());
for (let j = 0; j < 6; j++) w.addBody("link_" + (j + 1), stl["link_" + (j + 1)], () => null);
w.setSkipPairs(SKIP_PAIRS);
w.setEnv(envMeshes.map((m) => ({ name: m.name, tris: m.tris })));
console.log("env mesh count in world:", w.envMeshCount());

function liveCheck(poseDeg) {
  const mats = fkMatrices(poseDeg);
  const matMap = { base: ident() };
  for (let j = 0; j < 6; j++) matMap["link_" + (j + 1)] = mats[j];
  const r = w.checkWithMatrices(matMap);
  // report link world positions for context
  const pos = mats.map((m) => [m[12], m[13], m[14]]);
  return { r, pos };
}

// Probe poses. J1 (yaw about Z) swings the arm in XY; J2/J3 reach.
const poses = {
  HOME: [0, -90, 90, 0, 90, 0],
  "yaw_+90": [90, -90, 90, 0, 90, 0],
  "yaw_-90": [-90, -90, 90, 0, 90, 0],
  yaw_45: [45, -90, 90, 0, 90, 0],
  "yaw_-45": [-45, -90, 90, 0, 90, 0],
  reachY: [90, -60, 60, 0, 90, 0],
};
for (const [name, p] of Object.entries(poses)) {
  const { r, pos } = liveCheck(p);
  const envHits = r.collisions.filter((c) => c.type === "env");
  const linkPos = pos.map((v) => v.map((x) => +x.toFixed(2)).join(",")).join(" | ");
  console.log(name + ":", envHits.length ? "ENV COLLISION " + JSON.stringify(envHits) : (r.collisions.length ? "self-only " + JSON.stringify(r.collisions) : "clear"));
  console.log("   links:", linkPos);
}

// World-space AABB of a body: transform its local STL verts by an FK matrix.
function worldAABB(localTris, mat) {
  const a = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
  const n = localTris.length / 9;
  for (let i = 0; i < n; i++) for (let k = 0; k < 3; k++) {
    const lx = localTris[i * 9 + k], ly = localTris[i * 9 + k + 1], lz = localTris[i * 9 + k + 2];
    const wx = mat[0] * lx + mat[4] * ly + mat[8] * lz + mat[12];
    const wy = mat[1] * lx + mat[5] * ly + mat[9] * lz + mat[13];
    const wz = mat[2] * lx + mat[6] * ly + mat[10] * lz + mat[14];
    if (wx < a[0]) a[0] = wx; if (wx > a[3]) a[3] = wx;
    if (wy < a[1]) a[1] = wy; if (wy > a[4]) a[4] = wy;
    if (wz < a[2]) a[2] = wz; if (wz > a[5]) a[5] = wz;
  }
  return a;
}
const HOME_M = [0, -90, 90, 0, 90, 0];
const hm = fkMatrices(HOME_M);
console.log("\nHOME link world AABBs:");
LINKS.forEach((n, j) => {
  const a = worldAABB(stl[n], j === 0 ? ident() : hm[j - 1]);
  console.log("  " + n + ": X[" + a[0].toFixed(2) + "," + a[3].toFixed(2) + "] Y[" + a[1].toFixed(2) + "," + a[4].toFixed(2) + "] Z[" + a[2].toFixed(2) + "," + a[5].toFixed(2) + "]");
});

// PROVE the engine + new-model pipeline works: place a synthetic cube that overlaps
// link_6's *world* AABB at HOME, and confirm env collision fires.
function box(x0, y0, z0, x1, y1, z1) {
  const c = [[x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0],[x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1]];
  // 12 triangles, each 3 corner indices (two per face).
  const f = [
    [0,1,2],[0,2,3], // bottom
    [4,5,6],[4,6,7], // top
    [0,1,5],[0,5,4], // front (y0)
    [2,3,7],[2,7,6], // back (y1)
    [0,3,7],[0,7,4], // left (x0)
    [1,2,6],[1,6,5], // right (x1)
  ];
  const t = new Float32Array(12 * 9);
  for (let i = 0; i < 12; i++) for (let v = 0; v < 3; v++) {
    const p = c[f[i][v]];
    t[i * 9 + v * 3 + 0] = p[0]; t[i * 9 + v * 3 + 1] = p[1]; t[i * 9 + v * 3 + 2] = p[2];
  }
  return t;
}
// Rebuild world with a synthetic cube that STRADDLES link_6's world AABB (sticks out
// on the X/Z faces), so its surface crosses link_6's surface -> exact intersection.
const l6a = worldAABB(stl.link_6, hm[5]);
console.log("\nlink_6 world AABB at HOME:", l6a.map((v) => +v.toFixed(2)).join(", "));
const cx = (l6a[0] + l6a[3]) / 2, cy = (l6a[1] + l6a[4]) / 2, cz = (l6a[2] + l6a[5]) / 2;
const hx = (l6a[3] - l6a[0]) / 2, hy = (l6a[4] - l6a[1]) / 2, hz = (l6a[5] - l6a[2]) / 2;
const w2 = Collide.createWorld();
w2.addBody("base", stl.base_link, () => ident());
for (let j = 0; j < 6; j++) w2.addBody("link_" + (j + 1), stl["link_" + (j + 1)], () => null);
w2.setSkipPairs(SKIP_PAIRS);
w2.setEnv([
  { name: "Cube.001", tris: envMeshes[0].tris },
  { name: "Cube.002", tris: envMeshes[1].tris },
  { name: "synth_straddle_link6", tris: box(cx - 1.3 * hx, cy - 1.3 * hy, cz - 1.3 * hz, cx + 1.3 * hx, cy + 1.3 * hy, cz + 1.3 * hz) },
]);
const mm = { base: ident() };
for (let j = 0; j < 6; j++) mm["link_" + (j + 1)] = hm[j];
const synthRes = w2.checkWithMatrices(mm);
const synthEnv = synthRes.collisions.filter((c) => c.type === "env");
console.log("[synth box STRADDLING link_6] env collisions:", synthEnv.length ? JSON.stringify(synthEnv) : "NONE");

// ------------------------------------------------------------------
// DEFINITIVE base-vs-floor test using the CORRECT floor transform (this file's
// parseEnvGLB, which matches the browser GLTFLoader: T*R*S in Y-up, then +90deg X).
// At HOME the base is at identity. Confirm whether it actually penetrates the floor.
// ------------------------------------------------------------------
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
const floorTris = envMeshes[0].tris; // "Cube" (floor), correct world transform
const baseTris = stl.base_link;
// base bottom Z and floor top Z
let baseMinZ = Infinity, floorTopZ = -Infinity;
for (let i = 0; i < baseTris.length; i += 3) if (baseTris[i + 2] < baseMinZ) baseMinZ = baseTris[i + 2];
for (let i = 0; i < floorTris.length; i += 3) if (floorTris[i + 2] > floorTopZ) floorTopZ = floorTris[i + 2];
console.log("\n[base-vs-floor @ HOME] base bottom Z=" + baseMinZ.toFixed(4) + "  floor top Z=" + floorTopZ.toFixed(4));
const bfHit = Collide._collideTriArrays(baseTris, aabbs(baseTris), floorTris, aabbs(floorTris));
console.log("[base-vs-floor @ HOME] exact test:", bfHit >= 0 ? "COLLISION (pair " + bfHit + ")" : "no collision (base rests on floor)");

// Now sweep the arm so a link actually enters a cube, proving the env path fires.
// Cube.001 world AABB: X[0.20,0.66] Y[-0.82,-0.35] Z[-0.11,1.01]
// Cube.002 world AABB: X[0.01,0.47] Y[0.52,0.98] Z[-0.19,1.46]
// Sweep J1 (yaw) + J2/J3 to bring link_2/link_3 through Cube.001 and Cube.002.
console.log("\n[pose sweep into cubes]");
const sweep = [];
for (let yaw = -180; yaw <= 180; yaw += 15) {
  for (const reach of [[-90, 90], [-60, 60], [-30, 30], [0, 0], [30, 30], [60, 60]]) {
    const pose = [yaw, reach[0], reach[1], 0, 90, 0];
    const { r, pos } = liveCheck(pose);
    const envHits = r.collisions.filter((c) => c.type === "env");
    if (envHits.length) sweep.push({ pose, envHits });
  }
}
if (sweep.length) {
  console.log("poses with ENV collision:", sweep.length);
  sweep.slice(0, 5).forEach((s) => console.log("  pose [" + s.pose.join(",") + "] -> " + JSON.stringify(s.envHits)));
} else {
  console.log("no env collision found in sweep (arm never enters the cubes at these poses)");
}
