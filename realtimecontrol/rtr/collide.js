// collide.js — self-contained collision engine for the RTR 3D sim.
//
// Tests the moving robot bodies (the six links + base + tool, each an STL mesh at its
// link frame) against each other (self-collision) and against the static environment
// (the Blender GLB). It is written with no three.js dependency so it can be exercised
// directly under Node (see test_collide.js); in the browser it is loaded as a plain
// script and exposes `window.Collide`.
//
// Approach (exact, not bounding-box): each body keeps its triangles + per-triangle
// AABBs in its LOCAL frame. On a check, a body's world AABB (its local AABB corners
// through the body's world matrix) rejects distant pairs cheaply; only overlapping
// pairs run an exact triangle-vs-triangle test (11-axis SAT). The environment is a
// static set of world-space meshes binned into a spatial grid, so a body only tests
// the few env meshes near it.
//
// Matrices are 16-element column-major arrays (the same layout three.js uses for
// Matrix4.elements), so a live body can hand over `mesh.matrixWorld.elements` directly.

(function (global, factory) {
  if (typeof module !== "undefined" && typeof module.exports === "object") {
    module.exports = factory();
  } else {
    global.Collide = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ------------------------------------------------------------------
  // Minimal column-major 4x4 + vector helpers (no three.js).
  // ------------------------------------------------------------------
  // Transform a point [x,y,z] by a column-major 4x4 matrix m. Writes into out[0..2].
  function xformPoint(m, x, y, z, out) {
    out[0] = m[0] * x + m[4] * y + m[8] * z + m[12];
    out[1] = m[1] * x + m[5] * y + m[9] * z + m[13];
    out[2] = m[2] * x + m[6] * y + m[10] * z + m[14];
    return out;
  }

  // World AABB of a local AABB [minX..maxZ] under matrix m (transform 8 corners).
  function aabbToWorld(m, aabb, out) {
    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    const c = [0, 0, 0];
    for (let i = 0; i < 8; i++) {
      c[0] = (i & 1) ? aabb[3] : aabb[0];
      c[1] = (i & 2) ? aabb[4] : aabb[1];
      c[2] = (i & 4) ? aabb[5] : aabb[2];
      xformPoint(m, c[0], c[1], c[2], c);
      if (c[0] < minX) minX = c[0]; if (c[0] > maxX) maxX = c[0];
      if (c[1] < minY) minY = c[1]; if (c[1] > maxY) maxY = c[1];
      if (c[2] < minZ) minZ = c[2]; if (c[2] > maxZ) maxZ = c[2];
    }
    out[0] = minX; out[1] = minY; out[2] = minZ;
    out[3] = maxX; out[4] = maxY; out[5] = maxZ;
    return out;
  }

  // AABB overlap, a/b = [minX,minY,minZ,maxX,maxY,maxZ].
  function aabbOverlap(a, b) {
    return !(a[3] < b[0] || a[0] > b[3] ||
             a[4] < b[1] || a[1] > b[4] ||
             a[5] < b[2] || a[2] > b[5]);
  }

  // ------------------------------------------------------------------
  // Triangle geometry.
  // ------------------------------------------------------------------
  // Per-triangle AABB for triangle i of a flat tris array (9 numbers/triangle).
  function triAABB(tris, i, out) {
    const o = i * 9;
    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    for (let k = 0; k < 9; k += 3) {
      const x = tris[o + k], y = tris[o + k + 1], z = tris[o + k + 2];
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
      if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
    }
    out[0] = minX; out[1] = minY; out[2] = minZ;
    out[3] = maxX; out[4] = maxY; out[5] = maxZ;
    return out;
  }

  // Cross product into out.
  function cross(ax, ay, az, bx, by, bz, out) {
    out[0] = ay * bz - az * by;
    out[1] = az * bx - ax * bz;
    out[2] = ax * by - ay * bx;
    return out;
  }

  // Projects triangle (tris at offset o) onto axis n; returns false on separation.
  function separates(n, tris, oA, oB, other) {
    let minA = Infinity, maxA = -Infinity;
    for (let k = 0; k < 9; k += 3) {
      const p = n[0] * tris[oA + k] + n[1] * tris[oA + k + 1] + n[2] * tris[oA + k + 2];
      if (p < minA) minA = p; if (p > maxA) maxA = p;
    }
    let minB = Infinity, maxB = -Infinity;
    for (let k = 0; k < 9; k += 3) {
      const p = n[0] * other[oB + k] + n[1] * other[oB + k + 1] + n[2] * other[oB + k + 2];
      if (p < minB) minB = p; if (p > maxB) maxB = p;
    }
    return !(maxA < minB || maxB < minA);
  }

  // True if triangle ia of trisA intersects triangle ib of trisB (same frame).
  // 17-axis separating-axis test: 2 face normals, 9 edge-pair crosses,
  // 3 (nA x eB) crosses, 3 (nB x eA) crosses.
  function trisIntersectAt(trisA, ia, trisB, ib) {
    const oA = ia * 9, oB = ib * 9;
    const nA = [0, 0, 0], nB = [0, 0, 0], ax = [0, 0, 0];
    // face normals
    cross(trisA[oA + 3] - trisA[oA], trisA[oA + 4] - trisA[oA + 1], trisA[oA + 5] - trisA[oA + 2],
          trisA[oA + 6] - trisA[oA], trisA[oA + 7] - trisA[oA + 1], trisA[oA + 8] - trisA[oA + 2], nA);
    if (!separates(nA, trisA, oA, oB, trisB)) return false;
    cross(trisB[oB + 3] - trisB[oB], trisB[oB + 4] - trisB[oB + 1], trisB[oB + 5] - trisB[oB + 2],
          trisB[oB + 6] - trisB[oB], trisB[oB + 7] - trisB[oB + 1], trisB[oB + 8] - trisB[oB + 2], nB);
    if (!separates(nB, trisA, oA, oB, trisB)) return false;
    // edges of A (3) and B (3)
    const eA = [
      [trisA[oA + 3] - trisA[oA], trisA[oA + 4] - trisA[oA + 1], trisA[oA + 5] - trisA[oA + 2]],
      [trisA[oA + 6] - trisA[oA + 3], trisA[oA + 7] - trisA[oA + 4], trisA[oA + 8] - trisA[oA + 5]],
      [trisA[oA] - trisA[oA + 6], trisA[oA + 1] - trisA[oA + 7], trisA[oA + 2] - trisA[oA + 8]],
    ];
    const eB = [
      [trisB[oB + 3] - trisB[oB], trisB[oB + 4] - trisB[oB + 1], trisB[oB + 5] - trisB[oB + 2]],
      [trisB[oB + 6] - trisB[oB + 3], trisB[oB + 7] - trisB[oB + 4], trisB[oB + 8] - trisB[oB + 5]],
      [trisB[oB] - trisB[oB + 6], trisB[oB + 1] - trisB[oB + 7], trisB[oB + 2] - trisB[oB + 8]],
    ];
    // 9 edge-edge crosses
    for (let i = 0; i < 3; i++) {
      for (let j = 0; j < 3; j++) {
        cross(eA[i][0], eA[i][1], eA[i][2], eB[j][0], eB[j][1], eB[j][2], ax);
        if (ax[0] * ax[0] + ax[1] * ax[1] + ax[2] * ax[2] < 1e-12) continue;
        if (!separates(ax, trisA, oA, oB, trisB)) return false;
      }
    }
    // 3: nA x eB[j]
    for (let j = 0; j < 3; j++) {
      cross(nA[0], nA[1], nA[2], eB[j][0], eB[j][1], eB[j][2], ax);
      if (ax[0] * ax[0] + ax[1] * ax[1] + ax[2] * ax[2] < 1e-12) continue;
      if (!separates(ax, trisA, oA, oB, trisB)) return false;
    }
    // 3: nB x eA[i]
    for (let i = 0; i < 3; i++) {
      cross(nB[0], nB[1], nB[2], eA[i][0], eA[i][1], eA[i][2], ax);
      if (ax[0] * ax[0] + ax[1] * ax[1] + ax[2] * ax[2] < 1e-12) continue;
      if (!separates(ax, trisA, oA, oB, trisB)) return false;
    }
    return true;
  }

  // Exact test between two triangle sets in the SAME frame. `trisA`/`trisB` are flat
  // (9/triangle); `aabbA`/`aabbB` are per-triangle AABBs (6/triangle). Returns the
  // index of the first intersecting pair, or -1.
  function collideTriArrays(trisA, aabbA, trisB, aabbB) {
    const nA = aabbA.length / 6, nB = aabbB.length / 6;
    for (let i = 0; i < nA; i++) {
      for (let j = 0; j < nB; j++) {
        // per-triangle AABB reject
        if (aabbA[i * 6 + 3] < aabbB[j * 6 + 0] || aabbA[i * 6 + 0] > aabbB[j * 6 + 3]) continue;
        if (aabbA[i * 6 + 4] < aabbB[j * 6 + 1] || aabbA[i * 6 + 1] > aabbB[j * 6 + 4]) continue;
        if (aabbA[i * 6 + 5] < aabbB[j * 6 + 2] || aabbA[i * 6 + 2] > aabbB[j * 6 + 5]) continue;
        if (trisIntersectAt(trisA, i, trisB, j)) return i * nB + j;
      }
    }
    return -1;
  }

  // ------------------------------------------------------------------
  // World: the set of moving bodies + the static environment.
  // ------------------------------------------------------------------
  function createWorld() {
    const bodies = []; // {name, tris, triAABB, aabb, getMatrix, _wt, _wtriAABB, _wtGen, _wAABB}
    let env = null;    // {meshes:[{name,tris,triAABB,aabb}], grid:Map, aabb:[6], cellSize}
    let gen = 0;
    const skipPairs = new Set(); // "nameA|nameB" pairs excluded from self-collision (joint housing)

    function worldTris(b, m) {
      // Transform the body's local triangles to world (cached for the current check).
      // `m` is the body's world matrix for THIS check (resolved by runCheck's getMat),
      // so live and scratch checks both transform triangles with the same matrix they
      // use for the AABB test — scratch checks must not fall back to the live getter.
      if (b._wtGen === gen) return b._wt;
      const n = b.tris.length / 9;
      const wt = new Float32Array(n * 9);
      const wtri = new Float32Array(n * 6);
      const p = [0, 0, 0];
      for (let i = 0; i < n; i++) {
        for (let k = 0; k < 9; k += 3) {
          xformPoint(m, b.tris[i * 9 + k], b.tris[i * 9 + k + 1], b.tris[i * 9 + k + 2], p);
          wt[i * 9 + k] = p[0]; wt[i * 9 + k + 1] = p[1]; wt[i * 9 + k + 2] = p[2];
        }
        triAABB(wt, i, wtri);
      }
      b._wt = wt; b._wtriAABB = wtri; b._wtGen = gen;
      return wt;
    }

    // The core collision pass. `getMat` resolves a body's world matrix for this check.
    function runCheck(getMat) {
      gen++;
      const collisions = [];
      const hot = new Set(); // colliding body names

      // Compute world AABBs for every body under this check's matrices.
      for (const b of bodies) {
        aabbToWorld(getMat(b.name), b.aabb, b._wAABB);
      }

      // --- self-collision (body vs body) ---
      for (let i = 0; i < bodies.length; i++) {
        for (let j = i + 1; j < bodies.length; j++) {
          const a = bodies[i], c = bodies[j];
          if (skipPairs.has(a.name + "|" + c.name) || skipPairs.has(c.name + "|" + a.name)) continue;
          if (!aabbOverlap(a._wAABB, c._wAABB)) continue;
          const wa = worldTris(a, getMat(a.name)), wc = worldTris(c, getMat(c.name));
          if (collideTriArrays(wa, a._wtriAABB, wc, c._wtriAABB) >= 0) {
            collisions.push({ a: a.name, b: c.name, type: "self" });
            hot.add(a.name); hot.add(c.name);
          }
        }
      }

      // --- environment collision (body vs static env meshes) ---
      if (env) {
        for (const b of bodies) {
          if (!aabbOverlap(b._wAABB, env.aabb)) continue;
          const cands = queryEnv(b._wAABB);
          const wb = worldTris(b, getMat(b.name));
          for (const mi of cands) {
            const em = env.meshes[mi];
            if (!aabbOverlap(b._wAABB, em.aabb)) continue;
            if (collideTriArrays(wb, b._wtriAABB, em.tris, em.triAABB) >= 0) {
              collisions.push({ a: b.name, b: "env:" + em.name, type: "env" });
              hot.add(b.name);
              break; // one env hit is enough to flag this body
            }
          }
        }
      }

      return { collisions: collisions, bodies: hot };
    }

    return {
      // Register a moving body. `tris` = flat Float32Array (9/triangle) in the body's
      // LOCAL frame; `getMatrix` = () => 16-element column-major world matrix.
      addBody: function (name, tris, getMatrix) {
        const n = tris.length / 9;
        const perTriAABB = new Float32Array(n * 6);
        const aabb = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
        const t = [0, 0, 0, 0, 0, 0];
        for (let i = 0; i < n; i++) {
          triAABB(tris, i, t);
          for (let k = 0; k < 6; k++) perTriAABB[i * 6 + k] = t[k];
          if (t[0] < aabb[0]) aabb[0] = t[0]; if (t[3] > aabb[3]) aabb[3] = t[3];
          if (t[1] < aabb[1]) aabb[1] = t[1]; if (t[4] > aabb[4]) aabb[4] = t[4];
          if (t[2] < aabb[2]) aabb[2] = t[2]; if (t[5] > aabb[5]) aabb[5] = t[5];
        }
        bodies.push({ name: name, tris: tris, triAABB: perTriAABB, aabb: aabb, getMatrix: getMatrix,
                      _wAABB: [0, 0, 0, 0, 0, 0], _wt: null, _wtriAABB: null, _wtGen: -1 });
      },

      // Set the static environment. `meshes` = [{name, tris (flat, WORLD frame)}].
      // Builds a spatial grid so bodies only test nearby env meshes.
      setEnv: function (meshes, cellSize) {
        cellSize = cellSize || 0.25;
        const grid = new Map();
        const aabb = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
        const envMeshes = meshes.map(function (m, mi) {
          const n = m.tris.length / 9;
          const perTriAABB = new Float32Array(n * 6);
          const ma = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
          const t = [0, 0, 0, 0, 0, 0];
          for (let i = 0; i < n; i++) {
            triAABB(m.tris, i, t);
            for (let k = 0; k < 6; k++) perTriAABB[i * 6 + k] = t[k];
            if (t[0] < ma[0]) ma[0] = t[0]; if (t[3] > ma[3]) ma[3] = t[3];
            if (t[1] < ma[1]) ma[1] = t[1]; if (t[4] > ma[4]) ma[4] = t[4];
            if (t[2] < ma[2]) ma[2] = t[2]; if (t[5] > ma[5]) ma[5] = t[5];
          }
          for (let k = 0; k < 6; k++) {
            if (ma[k] < aabb[k]) aabb[k] = ma[k];
            if (ma[k] > aabb[k + 3]) aabb[k + 3] = ma[k];
          }
          // bin the mesh's AABB into the grid
          const ix0 = Math.floor(ma[0] / cellSize), ix1 = Math.floor(ma[3] / cellSize);
          const iy0 = Math.floor(ma[1] / cellSize), iy1 = Math.floor(ma[4] / cellSize);
          const iz0 = Math.floor(ma[2] / cellSize), iz1 = Math.floor(ma[5] / cellSize);
          for (let ix = ix0; ix <= ix1; ix++)
            for (let iy = iy0; iy <= iy1; iy++)
              for (let iz = iz0; iz <= iz1; iz++) {
                const key = ix + "," + iy + "," + iz;
                let cell = grid.get(key);
                if (!cell) { cell = []; grid.set(key, cell); }
                cell.push(mi);
              }
          return { name: m.name, tris: m.tris, triAABB: perTriAABB, aabb: ma };
        });
        env = { meshes: envMeshes, grid: grid, aabb: aabb, cellSize: cellSize };
      },

      // Live check (each body's getMatrix).
      check: function () {
        const matByName = {};
        for (const b of bodies) matByName[b.name] = b.getMatrix();
        return runCheck(function (name) { return matByName[name]; });
      },

      // Check under an explicit set of world matrices (scratch pose, e.g. a zone's pose).
      checkWithMatrices: function (matMap) {
        return runCheck(function (name) { return matMap[name]; });
      },

      // Exclude body pairs from self-collision (joint housing overlap).
      setSkipPairs: function (pairs) {
        skipPairs.clear();
        for (let i = 0; i < pairs.length; i++) {
          skipPairs.add(pairs[i][0] + "|" + pairs[i][1]);
          skipPairs.add(pairs[i][1] + "|" + pairs[i][0]);
        }
      },

      bodyNames: function () { return bodies.map(function (b) { return b.name; }); },
      envMeshCount: function () { return env ? env.meshes.length : 0; },
    };

    // Query the env spatial grid for the meshes whose cells overlap `aabb`.
    function queryEnv(aabb) {
      const cs = env.cellSize;
      const ix0 = Math.floor(aabb[0] / cs), ix1 = Math.floor(aabb[3] / cs);
      const iy0 = Math.floor(aabb[1] / cs), iy1 = Math.floor(aabb[4] / cs);
      const iz0 = Math.floor(aabb[2] / cs), iz1 = Math.floor(aabb[5] / cs);
      const seen = new Set();
      const out = [];
      for (let ix = ix0; ix <= ix1; ix++)
        for (let iy = iy0; iy <= iy1; iy++)
          for (let iz = iz0; iz <= iz1; iz++) {
            const cell = env.grid.get(ix + "," + iy + "," + iz);
            if (!cell) continue;
            for (let k = 0; k < cell.length; k++) {
              const mi = cell[k];
              if (!seen.has(mi)) { seen.add(mi); out.push(mi); }
            }
          }
      return out;
    }
  }

  // ------------------------------------------------------------------
  // Helpers for building bodies from a loaded mesh geometry.
  // ------------------------------------------------------------------
  // Read a three.js BufferGeometry's position attribute into a flat triangle array.
  // (The geometry is expected non-indexed; three's STLLoader produces non-indexed.)
  function geometryToTris(geometry) {
    const pos = geometry.attributes.position;
    const n = pos.count;
    const tris = new Float32Array(n * 3); // n vertices -> n/3 triangles -> n*3 floats
    for (let i = 0; i < n; i++) {
      tris[i * 3 + 0] = pos.getX(i);
      tris[i * 3 + 1] = pos.getY(i);
      tris[i * 3 + 2] = pos.getZ(i);
    }
    return tris;
  }

  return {
    createWorld: createWorld,
    geometryToTris: geometryToTris,
    // Exposed for the Node test (and unit checks):
    _xformPoint: xformPoint,
    _aabbToWorld: aabbToWorld,
    _aabbOverlap: aabbOverlap,
    _collideTriArrays: collideTriArrays,
  };
});
