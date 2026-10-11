// validate_zones.js — deterministic collision + limits validation for the 12-zone
// animal network in zones.json.
//
// For every zone x action it checks the robot at the action's commanded poses:
//   - variable-axis form: the base_pose plus the 2^n corners of the variable-axis
//     safezone box (the maximum-reach extremes where collision is most likely)
//   - legacy fixed-pose form: every pose in the `pos` sequence
//
// At each pose it reports:
//   - self-collision (link vs link, excluding adjacent joint-housing pairs)
//   - environment collision (link vs the environment_collision.glb meshes)
//   - joint limits: HARDWARE_LIMITS (physical floor), SOFTWARE_LIMITS (clamp),
//     and the zone safezone (data consistency)
//
// Reuses the FK + STL + GLB + SAT pipeline from test_env_collide2.js.
const Collide = require("./collide.js");
const fs = require("fs");
const path = require("path");

const ASSETS = path.join(__dirname, "assets", "kr60ha", "collision");
const ZONES_PATH = path.join(__dirname, "zones.json");

// ------------------------------------------------------------------
// Mesh parsing (copied from test_env_collide2.js).
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
        idx = new Uint16Array(buf.buffer, buf.byteOffset + ibase, ia.count);
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

// ------------------------------------------------------------------
// FK (mirrors rtr3d.js JOINTS, copied from test_env_collide2.js).
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
// Limits (mirrors rtr/state/zones.py).
// ------------------------------------------------------------------
const SOFTWARE_LIMITS = [[-95,123],[-135,35],[-120,158],[-350,350],[-119,119],[-358,358]];
const HARDWARE_LIMITS = [[-185,185],[-135,35],[-120,158],[-350,350],[-119,119],[-350,350]];

const LINKS = ["base_link", "link_1", "link_2", "link_3", "link_4", "link_5", "link_6"];
const SKIP_PAIRS = [
  ["base", "link_1"], ["link_1", "link_2"], ["link_2", "link_3"],
  ["link_3", "link_4"], ["link_4", "link_5"], ["link_5", "link_6"],
];

// ------------------------------------------------------------------
// Build the world (bodies + env) once.
// ------------------------------------------------------------------
const stl = {};
LINKS.forEach((n) => { stl[n] = parseSTL(path.join(ASSETS, n + ".stl")); });
const envMeshes = parseEnvGLB(path.join(__dirname, "assets", "environment_collision.glb"));

const w = Collide.createWorld();
w.addBody("base", stl.base_link, () => ident());
for (let j = 0; j < 6; j++) w.addBody("link_" + (j + 1), stl["link_" + (j + 1)], () => null);
w.setSkipPairs(SKIP_PAIRS);
w.setSkipEnv(["base"]); // base pedestal is permanently seated on the floor cube (env:Cube); the browser sim skips the same pair (rtr3d.js).
w.setEnv(envMeshes.map((m) => ({ name: m.name, tris: m.tris })));

function checkPose(poseDeg) {
  const mats = fkMatrices(poseDeg);
  const matMap = { base: ident() };
  for (let j = 0; j < 6; j++) matMap["link_" + (j + 1)] = mats[j];
  const r = w.checkWithMatrices(matMap);
  const selfHits = r.collisions.filter((c) => c.type === "self");
  const envHits = r.collisions.filter((c) => c.type === "env");
  return { selfHits, envHits };
}

// Joint-limits violation for a single pose. Returns list of human strings.
function limitViolations(poseDeg, safezone) {
  const out = [];
  for (let i = 0; i < 6; i++) {
    const v = poseDeg[i];
    if (v < HARDWARE_LIMITS[i][0] || v > HARDWARE_LIMITS[i][1])
      out.push(`A${i + 1}=${v} outside HARDWARE [${HARDWARE_LIMITS[i][0]},${HARDWARE_LIMITS[i][1]}]`);
    if (v < SOFTWARE_LIMITS[i][0] || v > SOFTWARE_LIMITS[i][1])
      out.push(`A${i + 1}=${v} outside SOFTWARE [${SOFTWARE_LIMITS[i][0]},${SOFTWARE_LIMITS[i][1]}]`);
    if (v < safezone[i][0] || v > safezone[i][1])
      out.push(`A${i + 1}=${v} outside ZONE safezone [${safezone[i][0]},${safezone[i][1]}]`);
  }
  return out;
}

// Effective per-axis range: intersection of the zone safezone and HARDWARE_LIMITS,
// mirroring zones.py `effective_limits`. The runtime clamps variable-axis motion to
// this range before commanding, so these are the actual max-reach extremes to test.
function effectiveLimits(sz) {
  return sz.map((pair, i) => [
    Math.max(pair[0], HARDWARE_LIMITS[i][0]),
    Math.min(pair[1], HARDWARE_LIMITS[i][1]),
  ]);
}

// Poses to validate for one action.
function actionPoses(zone, action) {
  if (action.base_pose !== undefined) {
    const bp = action.base_pose;
    const va = action.variable_axes || [];
    const eff = effectiveLimits(zone.safezone);
    const poses = [bp.slice()];
    const n = va.length;
    if (n > 0) {
      for (let mask = 0; mask < (1 << n); mask++) {
        const p = bp.slice();
        for (let k = 0; k < n; k++) {
          const axis = va[k];
          p[axis] = (mask & (1 << k)) ? eff[axis][1] : eff[axis][0];
        }
        poses.push(p);
      }
    }
    return { poses, kind: "variable" };
  }
  return { poses: action.pos.map((p) => p.slice()), kind: "pos" };
}

const fmt = (p) => "[" + p.map((x) => +x.toFixed(1)).join(",") + "]";

// ------------------------------------------------------------------
// Run validation over zones.json.
// ------------------------------------------------------------------
const zonesData = JSON.parse(fs.readFileSync(ZONES_PATH, "utf8")).zones;
let totalPoses = 0;
const zoneResults = [];
const allFails = [];

for (const [zname, zone] of Object.entries(zonesData)) {
  if (zone.enabled === false) { zoneResults.push({ name: zname, skip: true }); continue; }
  const zoneFails = [];
  const sz = zone.safezone;

  for (const [aname, action] of Object.entries(zone.actions || {})) {
    if (action.enabled === false) continue;
    const { poses, kind } = actionPoses(zone, action);
    const actionFails = [];
    for (const pose of poses) {
      totalPoses++;
      const { selfHits, envHits } = checkPose(pose);
      const lim = limitViolations(pose, sz);
      if (selfHits.length || envHits.length || lim.length) {
        const bits = [];
        if (selfHits.length) bits.push("SELF " + JSON.stringify(selfHits));
        if (envHits.length) bits.push("ENV " + JSON.stringify(envHits));
        bits.push(...lim.map((s) => "LIMIT " + s));
        actionFails.push({ pose, bits });
      }
    }
    if (actionFails.length) {
      zoneFails.push({ action: aname, kind, fails: actionFails });
    }
  }

  const ok = zoneFails.length === 0;
  zoneResults.push({ name: zname, ok, fails: zoneFails });
  if (!ok) allFails.push(zname);
}

// ------------------------------------------------------------------
// Report.
// ------------------------------------------------------------------
console.log("=== Zone Collision + Limits Validation ===");
console.log("env meshes:", envMeshes.length, "  total poses checked:", totalPoses);
for (const zr of zoneResults) {
  if (zr.skip) { console.log("[" + zr.name + "] SKIPPED (disabled)"); continue; }
  if (zr.ok) { console.log("[" + zr.name + "] PASS"); continue; }
  console.log("[" + zr.name + "] FAIL");
  for (const af of zr.fails) {
    console.log("  " + af.action + " (" + af.kind + "):");
    for (const f of af.fails) {
      console.log("    " + fmt(f.pose) + "  " + f.bits.join(" ; "));
    }
  }
}
console.log("\n=== Summary ===");
console.log("zones: " + zoneResults.length + "   pass: " + (zoneResults.length - allFails.length) + "   fail: " + allFails.length);
if (allFails.length) console.log("failing zones: " + allFails.join(", "));
else console.log("all zones clear");
