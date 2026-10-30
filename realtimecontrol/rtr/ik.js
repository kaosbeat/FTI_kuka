// ik.js — KR60 inverse kinematics (pure JS: no THREE, no DOM).
//
// Shared by the editor (drag-the-end-effector pose authoring) and testable in
// Node. Mirrors rtr/robot/kinematics.py exactly:
//
//   - FK takes joint angles in DEGREES (the KUKA A1..A6 convention).
//   - The geometric Jacobian is PER-RADIAN, so an IK joint delta is a radian
//     quantity and must be converted to degrees (deg = rad * 180/pi) before it is
//     applied to the degree-based FK. (Treating the radian delta as degrees makes
//     every step ~57x too small — the earlier, broken IK.)
//
// The chain is the kr60ha xacro (identical to rtr3d.js JOINTS): base at the origin,
// A1 about -Z, six revolute joints + a fixed tool0 ($FLANGE) frame. The flange
// position is the link_6 matrix translation (tool0 xyz is [0,0,0]).
//
// Usage:
//   const IK = KR60IK;
//   IK.fkPos([0,-90,90,0,90,0]);            // -> [1.17, 0, 1.64]
//   IK.ikSolve([0,-90,90,0,90,0], [1.2,0,1.68], { limits, lam, maxIter, tol });

"use strict";

const KR60IK = (() => {
  // Revolute joints from kr60ha_macro.xacro (meters, Z-up) — identical to rtr3d.js.
  const JOINTS = [
    { xyz: [0, 0, 0],           axis: [0, 0, -1] },  // A1
    { xyz: [0.35, 0, 0.815],    axis: [0, 1, 0]  },  // A2
    { xyz: [0.85, 0, 0],        axis: [0, 1, 0]  },  // A3
    { xyz: [0.465, 0, 0.145],   axis: [-1, 0, 0] },  // A4
    { xyz: [0.355, 0, 0],       axis: [0, 1, 0]  },  // A5
    { xyz: [0.17, 0, 0],        axis: [-1, 0, 0] },  // A6
  ];
  const HOME = [0, -90, 90, 0, 90, 0];
  // The xacro joint limits (deg) — the hard floor IK clamps to (same as editor HW_LIMITS_FALLBACK).
  const DEFAULT_LIMITS = [
    [-185, 185],   // A1
    [-135, 35],    // A2
    [-95, 158],    // A3
    [-180, 180],   // A4
    [-55, 100],    // A5
    [-150, 150],   // A6
  ];
  const IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  const RAD2DEG = 180 / Math.PI;
  const DEG2RAD = Math.PI / 180;

  // Forward kinematics walk. Returns { mats, pos }:
  //   mats[j] = the 16-element column-major matrix of link_(j+1) (the rotor frame
  //             after applying joint j) — identical construction to rtr3d.js checkPoseCollision.
  //   pos     = the flange (tool0) position = mats[5] translation (tool0 xyz is [0,0,0]).
  function fkChain(jointsDeg) {
    const rad = jointsDeg.map((d) => d * DEG2RAD);
    const mats = [];
    for (let j = 0; j < 6; j++) {
      const jt = JOINTS[j];
      const parent = j === 0 ? IDENT : mats[j - 1];
      const tx = jt.xyz[0], ty = jt.xyz[1], tz = jt.xyz[2];
      // Tm = parent * T(xyz)
      const Tm = parent.slice();
      Tm[12] = parent[0]*tx + parent[4]*ty + parent[8]*tz + parent[12];
      Tm[13] = parent[1]*tx + parent[5]*ty + parent[9]*tz + parent[13];
      Tm[14] = parent[2]*tx + parent[6]*ty + parent[10]*tz + parent[14];
      // Rm = rotation about the joint axis by rad[j] (Rodrigues)
      const ax = jt.axis[0], ay = jt.axis[1], az = jt.axis[2];
      const c = Math.cos(rad[j]), s = Math.sin(rad[j]), t = 1 - c;
      const Rm = [
        c + ax*ax*t, ay*ax*t + az*s, az*ax*t - ay*s, 0,
        ax*ay*t - az*s, c + ay*ay*t, ay*az*t + ax*s, 0,
        ax*az*t + ay*s, ay*az*t - ax*s, c + az*az*t, 0,
        0, 0, 0, 1
      ];
      // res = Tm * Rm
      const res = new Array(16);
      for (let col = 0; col < 4; col++)
        for (let row = 0; row < 4; row++) {
          let sum = 0;
          for (let k = 0; k < 4; k++) sum += Tm[k * 4 + row] * Rm[col * 4 + k];
          res[col * 4 + row] = sum;
        }
      mats.push(res);
    }
    const pos = [mats[5][12], mats[5][13], mats[5][14]];
    return { mats, pos };
  }

  // The flange position (metres) for a concrete 6-value pose (deg).
  function fkPos(jointsDeg) {
    return fkChain(jointsDeg).pos;
  }

  // Geometric linear Jacobian J_v (3x6), PER-RADIAN. Column j is
  //   z_j x (pos - p_j)
  // where z_j is joint j's axis in the base frame and p_j is joint j's origin in the
  // base frame. (p_j = parent * T(xyz) translation; z_j = parent rotation * local axis,
  // since a rotation about its own axis leaves the axis unchanged.)
  function jacobian(jointsDeg) {
    const { mats, pos } = fkChain(jointsDeg);
    const Jv = [[0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0]];
    for (let j = 0; j < 6; j++) {
      const parent = j === 0 ? IDENT : mats[j - 1];
      const jt = JOINTS[j];
      const tx = jt.xyz[0], ty = jt.xyz[1], tz = jt.xyz[2];
      const px = parent[0]*tx + parent[4]*ty + parent[8]*tz + parent[12];
      const py = parent[1]*tx + parent[5]*ty + parent[9]*tz + parent[13];
      const pz = parent[2]*tx + parent[6]*ty + parent[10]*tz + parent[14];
      const z0 = parent[0]*jt.axis[0] + parent[4]*jt.axis[1] + parent[8]*jt.axis[2];
      const z1 = parent[1]*jt.axis[0] + parent[5]*jt.axis[1] + parent[9]*jt.axis[2];
      const z2 = parent[2]*jt.axis[0] + parent[6]*jt.axis[1] + parent[10]*jt.axis[2];
      const dx = pos[0] - px, dy = pos[1] - py, dz = pos[2] - pz;
      Jv[0][j] = z1*dz - z2*dy;
      Jv[1][j] = z2*dx - z0*dz;
      Jv[2][j] = z0*dy - z1*dx;
    }
    return Jv;
  }

  // Inverse of a 3x3 matrix (null when singular).
  function inv3(m) {
    const a = m[0][0], b = m[0][1], c = m[0][2];
    const d = m[1][0], e = m[1][1], f = m[1][2];
    const g = m[2][0], h = m[2][1], i = m[2][2];
    const det = a*(e*i - f*h) - b*(d*i - f*g) + c*(d*h - e*g);
    if (Math.abs(det) < 1e-12) return null;
    const inv = 1 / det;
    return [
      [(e*i - f*h)*inv, (c*h - b*i)*inv, (b*f - c*e)*inv],
      [(f*g - d*i)*inv, (a*i - c*g)*inv, (c*d - a*f)*inv],
      [(d*h - e*g)*inv, (b*g - a*h)*inv, (a*e - b*d)*inv],
    ];
  }

  // Damped minimum-norm pseudo-inverse of a 3 x 6 matrix -> a 6 x 3 matrix P such
  // that dq = P @ d maps a base-frame displacement (metres) to joint deltas (RADIANS).
  function pinv3(m, lam) {
    const n = 6;
    // JJT = m @ m^T (3x3), damped on the diagonal.
    const JJT = [];
    for (let r = 0; r < 3; r++) {
      JJT.push([]);
      for (let r2 = 0; r2 < 3; r2++) {
        let s = 0;
        for (let k = 0; k < n; k++) s += m[r][k] * m[r2][k];
        JJT[r].push(s + (r === r2 ? lam * lam : 0));
      }
    }
    const JJT_inv = inv3(JJT);
    if (!JJT_inv) return Array.from({ length: n }, () => [0, 0, 0]);
    // P = m^T @ JJT_inv (6x3).
    const P = [];
    for (let i = 0; i < n; i++) {
      P.push([]);
      for (let c = 0; c < 3; c++) {
        let s = 0;
        for (let r = 0; r < 3; r++) s += m[r][i] * JJT_inv[r][c];
        P[i].push(s);
      }
    }
    return P;
  }

  function dist(a, b) {
    return Math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2);
  }

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  // DLS IK with backtracking + hardware clamp.
  //   startDeg : concrete 6-value pose (deg) to seed from (keeps the arm continuous).
  //   target   : 3-value flange target (metres).
  //   opts     : { limits, lam, maxIter, tol } (all optional; sensible defaults).
  // Returns { pose, err, iters, converged } where pose is a concrete 6-value (deg).
  function ikSolve(startDeg, target, opts) {
    opts = opts || {};
    const limits = opts.limits || DEFAULT_LIMITS;
    const lam = opts.lam !== undefined ? opts.lam : 0.1;
    const maxIter = opts.maxIter !== undefined ? opts.maxIter : 60;
    const tol = opts.tol !== undefined ? opts.tol : 0.0004;
    let j = startDeg.slice();
    let cur = fkPos(j);
    let err = dist(cur, target);
    let it = 0;
    while (it < maxIter && err >= tol) {
      it++;
      const Jv = jacobian(j);
      const P = pinv3(Jv, lam);
      const d = [target[0]-cur[0], target[1]-cur[1], target[2]-cur[2]];
      // dq in RADIANS; convert to degrees before applying to the degree-based FK.
      const dq = [0, 0, 0, 0, 0, 0];
      for (let i = 0; i < 6; i++) dq[i] = (P[i][0]*d[0] + P[i][1]*d[1] + P[i][2]*d[2]) * RAD2DEG;
      let accepted = false;
      let alpha = 1.0;
      for (let bt = 0; bt < 10; bt++) {
        const trial = j.map((a, i) => clamp(a + alpha * dq[i], limits[i][0], limits[i][1]));
        const tp = fkPos(trial);
        const terr = dist(tp, target);
        if (terr < err * 0.9) {
          j = trial; err = terr; cur = tp; accepted = true; break;
        }
        alpha *= 0.5;
      }
      if (!accepted) break;
    }
    return { pose: j, err, iters: it, converged: err < tol };
  }

  return { JOINTS, HOME, DEFAULT_LIMITS, fkChain, fkPos, jacobian, pinv3, ikSolve };
})();

if (typeof module !== "undefined" && module.exports) module.exports = KR60IK;
