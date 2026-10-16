# Plan — Real KR60 3D model + hardware angle limits (RTR core)

## Goal
Replace the hand-built cylinder/sphere placeholder in the RTR 3D view with the real
KR60 mesh model from `kr60/models/kuka_experimental/kuka_kr60_support/`
(`kr60ha_macro.xacro` + `meshes/kr60ha/visual/*.stl`), and integrate the xacro's
per-joint hardware angle limits as a **hard safety floor** in the server and make them
**visible** in the pose editor.

## Decisions (agreed with user)
- **Hardware limits = hard safety floor.** Every joint pose sent to the robot is clamped
  to the intersection of the current zone's `safezone` AND the xacro hardware limits,
  per axis: `eff[i] = (max(sz_lo, hw_lo), min(sz_hi, hw_hw))`. This also extends the
  floor to **A6**, currently treated as free.
- **Kinematic model = use the xacro chain as-is.** Drive the real meshes with the
  `kr60ha_macro.xacro` chain (base at origin, A1 about −Z). Accept that the computed
  flange position differs slightly from the old `kuka_kr60_abs.urdf` schematic — the 3D
  view is a visualization; the real robot is driven by the KUKA controller, not the model.

## Current state (verified)
- Placeholder 3D is in `realtimecontrol/rtr/rtr3d.js` (shared by `client.html` +
  `editor.html`). `Robot3D` builds a schematic from a hardcoded `JOINTS` array that
  mirrors `pathcalc/.../kuka_kr60_abs.urdf` — **not** the kr60ha chain. `setJoints`
  applies each KUKA angle (deg→rad) about the URDF joint axis (correct FK convention).
- The real model chain is in `kr60ha_macro.xacro` (6 revolute joints + fixed tool0),
  meshes in `meshes/kr60ha/visual/{base_link,link_1..6}.stl` (binary STL, meters, Z-up).
- HTTP server `rtr/io/httpserver.py` serves static files from the `rtr/` dir with a
  `realpath` traversal check → STLs must be **copied** under it (a symlink resolves
  outside the root → 403). `_is_static` already allows any `/assets/...` path.
- `.stl` is missing from `_CTYPES` (needs adding).
- `state/zones.py` defines `SOFTWARE_LIMITS`/`LIMITS` but they are **unused**; the brain
  only clamps to the current zone `safezone`. Enforcement choke point is
  `core/engine.py:70-76` (`target = brain.step(...)` → `move_joint`), and the engine
  sends the brain's returned target straight to the robot — so clamping in `brain.step()`
  output floors **every** pose (wander/random/action/track/hold/transition) and keeps the
  published `target_pose` snapshot honest.
- xacro hardware limits (deg): A1 ±185, A2 −135..35, A3 −120..158, A4 ±350, A5 ±119,
  A6 ±350. (From `kr60ha_macro.xacro` joint `<limit>` tags.)
- No test suite in `rtr/`. Sim is headless. three.js (r128) loads from CDN.

## Subtasks

### A. Ship the real meshes to the static server
1. Copy the visual STLs into the served tree (create the dir):
   `realtimecontrol/rtr/assets/kr60ha/visual/{base_link,link_1,link_2,link_3,link_4,link_5,link_6}.stl`
   from `kr60/models/kuka_experimental/kuka_kr60_support/meshes/kr60ha/visual/`.
   (Copy, not symlink — the `realpath` check requires the file to live under the root.)
2. `rtr/io/httpserver.py`: add `".stl": "model/stl"` to `_CTYPES`.
   (No `_is_static` change needed — `/assets/` is already allowed.)

### B. Replace the schematic with the real mesh model in `rtr3d.js`
Keep the public API (`Robot3D.init/update/setTarget`) unchanged so `client.html` and
`editor.html` keep working.
1. Add the STLLoader script tag to **both** `client.html` and `editor.html`, next to the
   existing GLTFLoader tag:
   `<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/STLLoader.js"></script>`
2. Replace the `JOINTS`/`TOOL` data with the kr60ha chain (from `kr60ha_macro.xacro`):
   - 6 revolute joints: A1 `xyz[0,0,0] rpy[0,0,0] axis[0,0,-1]`; A2 `xyz[0.35,0,0.815] axis[0,1,0]`;
     A3 `xyz[0.85,0,0] axis[0,1,0]`; A4 `xyz[0.465,0,0.145] axis[-1,0,0]`;
     A5 `xyz[0.355,0,0] axis[0,1,0]`; A6 `xyz[0.17,0,0] axis[-1,0,0]` (all `rpy[0,0,0]`).
   - tool0 fixed: `xyz[0,0,0] rpy[0,1.5708,0]` (the $FLANGE frame).
   - Mesh list: `base_link`, `link_1..link_6`.
3. Rewrite `buildArm` to load STLs instead of cylinders/spheres:
   - Load each STL **once** via `THREE.STLLoader` (share the `BufferGeometry` between the
     current and target arms to halve memory/network).
   - Frame chain: `base_link` mesh → scene (fixed); for j=0..5, a **rotor** group child of
     (scene if j==0 else rotor[j−1]) placed/oriented by joint j's `xyz`/`rpy`; `link_{j+1}`
     mesh as a child of rotor[j] at origin; tool0 marker as a child of rotor[5] at tool0.
   - `setJoints` unchanged (axis-angle about `axis` per rotor).
   - Materials: current arm = real KUKA colors — base_link & link_6 black
     (`0x0e0e10`, RAL 9005), link_1–5 orange (`0xf67828`, RAL 2003),
     `MeshStandardMaterial`; target (ghost) arm = same geometry, green-transparent
     (`0x4fc08d`, opacity ~0.22) to preserve the "current/target" legend.
   - Keep a small tool0 flange marker (box) so `toolNode.getWorldPosition` still drives
     the "tool (x,y,z)" readout in `client.html`.
4. In `init`: **remove** the hand-built base plate + pedestal cylinder (the real
   `base_link` mesh is the pedestal). Keep the `GridHelper`, lights, and the environment
   GLB. Re-frame the camera for the real model (start `target ≈ (0.3, 0, 0.9)`,
   `position ≈ (2.2, -2.0, 1.6)`; verify visually and adjust).
5. STL load failure: log via `logFn` and continue (same degrade pattern as the
   environment GLB), so `file://` / no-asset usage still runs.

### C. Hardware-limit data + helper (server)
`rtr/state/zones.py`:
1. Add `HARDWARE_LIMITS` (deg, from the xacro):
   `[(-185,185), (-135,35), (-120,158), (-350,350), (-119,119), (-350,350)]`.
2. Add `effective_limits(zone_safezone)` returning the per-axis intersection
   `[(max(sz[0],hw[0]), min(sz[1],hw[1])) for ...]`.
3. Export both from `rtr/state/__init__.py` (alongside `LIMITS`/`SOFTWARE_LIMITS`).

### D. Enforce the hard safety floor in the brain
`rtr/brain/brain.py`:
1. Add `_effective()` → `effective_limits(self._safezone())`.
2. Change `_clamp(pose)` to clamp **all 6** axes through `_effective()` (the existing
   `range(len(pose))` loop already covers A6 once the limits list has 6 entries).
3. Point `_wander`, `_random_pose`, `_track` at `_effective()` instead of `_safezone()`
   so generated poses respect the floor (A6 stays 0 in random; the step clamp covers it).
4. In `step()`, floor the final result: clamp the transition target `t`, and add
   `self.target = self._clamp(self.target)` (guard `None`) before the final `return`.
   This guarantees every pose reaching `engine.py → move_joint` is within the floor.

### E. Expose the limits over HTTP
`rtr/io/httpserver.py`:
1. Add `GET /api/limits` → `{"axes": ["A1".."A6"], "hardware_limits": HARDWARE_LIMITS}`.
   (Static constant; no POST.) Import `HARDWARE_LIMITS` from `..state.zones`.

### F. Make limits visible in the pose editor (`editor.html`)
1. On load, fetch `/api/limits` (fallback to a JS constant mirroring the xacro, same
   pattern as the zones/sound fallbacks). Store `HW_LIMITS`.
2. Show the per-axis hardware limits as a reference row near the joint-pose inputs and the
   safezone bars (e.g. `A1 [-185, 185]`).
3. In `saveZones()` validation, add a **non-blocking warning** (like the existing
   safezone warning) when any action pose value is outside `HW_LIMITS` for its axis —
   the server will clamp it, but the editor should flag it. (Optional secondary: also warn
   when a zone's `safezone` range exceeds the hardware range.)

### G. Docs + handoff
1. `realtimecontrol/rtr/README.md`: note the real-mesh model (kr60ha), the `/api/limits`
   endpoint, and the hardware-limit hard floor (incl. A6).
2. `.kilo/task-handoff.md`: add the required entry (per AGENTS.md).

## Validation (no test suite; manual)
- **Server floor:** run `python main.py --sim` and, over the WebSocket, send
  `set_joint_pose` with a value outside hardware (e.g. A6 = 357, A2 = 40) and confirm the
  resulting `target_pose` snapshot is clamped to the floor (A6 ≤ 350, A2 ≤ 35). Confirm
  wander/random never emit a pose outside the floor. Confirm `GET /api/limits` returns the
  xacro limits.
- **Editor:** open `editor.html` over HTTP; confirm the hardware limits render and that
  saving a pose outside the hardware range shows the warning (and the core still clamps).
- **3D model:** open `client.html` + `editor.html` over HTTP; confirm the real KR60 meshes
  load, sit on the floor/base, and track the live joints (current orange/black + green
  ghost). Confirm the tool readout moves. Confirm `file://` still degrades gracefully
  (logs "…not loaded", keeps running).
- **Regression:** confirm zone transitions still complete (machine arrival logic untouched)
  and the environment GLB still loads.

## Risks / caveats / open questions
- **Flange offset (accepted):** the kr60ha chain's flange differs slightly from the old
  abs schematic for the same A1–A6 (decision: use model as-is). Cosmetic to the 3D view.
- **Environment pedestal overlap:** `assets/environment.glb` has its own pedestal cylinder
  at the origin that now overlaps the real `base_link` mesh. Not auto-changed (it is
  user-editable Blender content); the user may remove it in Blender.
- **Zone safezone vs hardware:** if a zone's safezone is configured wider than the
  hardware limit, the effective range is the intersection (safe). If it is *entirely*
  outside hardware (misconfiguration), the floor clamps to the hardware boundary and a
  transition to that pose may stall at the limit (safe failure). The editor warning in
  (F) surfaces this.
- **STL size:** ~1.8 MB of visual STLs (~36k tris) — fine for WebGL; shared geometry keeps
  the target arm from doubling it.
- **A6 now limited:** previously free; the floor now clamps it to ±350°. Intended.

## Out of scope
- No change to zone/pose data values, sound mapping, MIDI, or the KUKA EKI robot layer.
- No calibration/alignment offset between the kr60ha chain and the real $FLANGE
  (deferred per decision 2).
