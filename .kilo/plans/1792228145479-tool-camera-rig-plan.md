# Tool Camera Rig: 3D Visualization + FOV-Cone Overlay

## Goal
Add the tool-mounted camera (wide + close lenses) as a 3D object in `rtr3d.js`, parented to the tool node so it moves with the robot, and render each lens's field of view as a debug FOV-cone overlay with a direction arrow. This makes the camera's pose + gaze visible in the 3D view and foreshadows how the wide/close pair feeds the existing track/hunt servo.

## Current state (verified)
- **Server-side camera geometry already exists** and drives the servo: `rtr/camera/geometry.py` `CameraGeometry` computes `T_base_cam(j) = FK(j) · T_tool_cam` (`camera_pose`), `image_offset`, `estimate_target`, `image_jacobian`. Loaded in `main.py` (`machine.camera_geometry = load_camera_geometry()`). The servo `rtr/state/behavior.py` `_apply_camera` uses `estimate_target` + `image_jacobian` → `dq = -gain · pinv2(J) · (dx, dy)`.
- **`camera_mount.json`** (authoritative, PLACEHOLDER intrinsics to calibrate): `camera_frame` = {x: right, y: down, z: forward/optical axis}; `tool_cam` = {xyz: [0.0, 0.0, 0.07], rpy: [0.0, 0.0, 0.0]}; `fx`=500, `fy`=500, `z_cam_default`=500, `target_depth`=2.0.
- **`rtr3d.js`** (908 lines): `Robot3D.init(container, opts)` / `Robot3D.update({joints, target, ...})`. The tool node (A6 rotor child) = the KUKA **`$FLANGE`** frame (TOOL0 = {xyz:[0,0,0], rpy:[0, 1.5708, 0]}). `tool.glb` is authored Y-up and rotated +90° about X on load.
- **`client.html`** (913 lines): 3D view page. `Robot3D.init($("robot3d"), {toolEl, log, environment, onCollision})`; `renderState` → `Robot3D.update(s)`. **`editor.html`** also loads `rtr3d.js` and calls `Robot3D.update` (the rig will appear there too — no change needed).
- **Lens GLBs exist and are single cylinders** (parsed from the glTF JSON chunks):
  - `assets/CLOSELENS.glb` (14.9 KB): node `rotation` = 180° about X (quat `[-1,0,0,~0]`), `scale` = `[0.01405, 0.008765, 0.01405]`, `translation` = `[0, -0.00465, 0]`. Mesh local bbox X/Z ±1.388, Y −7.705..0 → scene cylinder **diameter ≈ 39.0 mm, length ≈ 67.5 mm** (matches near-lens φ39.00 / optical 67.53 mm).
  - `assets/WIDELENS.glb` (14.9 KB): same rotation/scale, no translation. Mesh local bbox X/Z ±1.640, Y −6.739..0 → scene cylinder **diameter ≈ 46.1 mm, length ≈ 59.1 mm** (matches wide-lens Φ44.5 × 58.5 mm).
  - Both: after the node transform the cylinder's long axis lies along the GLB **scene Y** axis; the front (local Y=0) lands at scene Y≈0 and the back at scene Y≈+0.06, so the "out"/optical direction points toward scene **−Y**.

## Resolved decisions
1. **Lens models**: reuse the existing `CLOSELENS.glb` + `WIDELENS.glb` (no new geometry generated).
2. **Mount**: the camera rig is a child of the tool node (the `$FLANGE` frame), offset by `tool_cam` = +70 mm along tool z, rpy [0,0,0]. Base-frame pose then equals `FK(j) · T_tool_cam`, matching Python `camera_pose()` exactly.
3. **Lens layout**: side-by-side along the camera **x (right)** axis. Baseline (center-to-center) = sum of radii = 19.5 + 23.0 = **42.5 mm** → wide at x = −21.25 mm, close at x = +21.25 mm (swap signs if the physical housing is mirrored).
4. **Orientation**: each lens's "out" end (currently GLB scene −Y) is rotated to the camera-frame **+z (forward/optical axis)**. A −90° rotation about X maps scene −Y → +Z; **verify visually** after load (if the cones point backward, add a 180° Z flip). This is the main uncertainty.
5. **FOV cones** (debug overlay, one per lens), circular cone using the H-FOV half-angle:
   - Wide (C20280M12, 2.8–12 mm): **125° H-FOV** (spec, 2.8 mm widest) → half-angle 62.5°. (Computed IMX477 H-FOV at 2.8 mm ≈ 99°; spec 125° used as the rated value. Configurable.)
   - Close (1" 16 mm, F1.4–16): **21.8° H-FOV** (1/2" field angle) → half-angle 10.9°.
   - Cone = wireframe outline (`THREE.ConeGeometry` wireframe / `LineSegments`) + a forward **direction arrow** (`THREE.ArrowHelper`) along +z. Cone length default **0.5 m** (adjustable; `target_depth`=2.0 m is too long for the view).
6. **Toggle**: a debug button in `client.html` shows/hides the FOV cones (and optionally the whole rig).

## Implementation steps
1. **`camera_mount.json`** — add a `lenses` block (single source of truth for the rig):
   ```json
   "lenses": {
     "wide":  { "glb": "assets/WIDELENS.glb",  "fov_h_deg": 125.0, "cone_len_m": 0.5, "offset_x_m": -0.02125, "offset_y_m": 0.0 },
     "close": { "glb": "assets/CLOSELENS.glb", "fov_h_deg": 21.8,  "cone_len_m": 0.5, "offset_x_m":  0.02125, "offset_y_m": 0.0 }
   }
   ```
2. **`rtr3d.js`** — add a `CameraRig` (or extend `Robot3D`):
   - Load `WIDELENS.glb` + `CLOSELENS.glb` via GLTFLoader (same pattern as `tool.glb`).
   - Build a `THREE.Group` `cameraRig`:
     - Per lens: a sub-Group holding the loaded cylinder, rotated so its "out" end points +z (verify; likely −90° X, possibly +180° Z), translated to (offset_x, offset_y, 0) in the camera frame.
     - (Optional) a body box behind the lenses.
     - Per lens: a FOV cone (wireframe) + a forward arrow, parented to the lens group so it inherits orientation.
   - Attach `cameraRig` to the tool node at `tool_cam` (xyz [0,0,0.07], rpy [0,0,0]) — mirror the existing tool-node transform so the base pose matches Python.
   - Expose `Robot3D.setCameraFovVisible(bool)` (and optionally `setCameraRigVisible(bool)`).
   - Keep the rig **out of the collision set** (visualization only; `collide.js` must not include it).
   - include instruction how to modify the rig, so it matches the real situation, and so the parameters are updated as the rig is updated (l;enses moved)
3. **`client.html`** — add a debug button (e.g. "FOV") that toggles `Robot3D.setCameraFovVisible`, wired to the existing button-bar pattern.
4. **`editor.html`** — no change required (reuses `rtr3d.js`); the rig appears automatically. Optionally add the same toggle.

## Data flow / integration
- The rig's pose is purely kinematic (child of the tool node); it needs no per-frame data beyond the joints already sent by `Robot3D.update`.
- The `camera`/`cam_telem`/`hunt`/`servo` fields already in the state frame (`display.py` `_frame`) can later drive a "which lens is active" highlight (wide/close/both) — out of scope now, but the rig exposes per-lens groups to make that trivial.

## Validation
1. Load `client.html`; confirm both lens cylinders appear at the tool tip and move with the robot (drive the joints; the rig follows).
2. Confirm the FOV cones point forward (+z) and toggle on/off with the button.
3. Cross-check the base-frame camera pose against Python `CameraGeometry.camera_pose(j)` at a few joint angles (rig base transform should equal `FK(j) · T_tool_cam`).
4. Confirm the rig does not affect collision detection (excluded from the collision mesh set).

## Risks / open questions
- **Orientation sign** (main uncertainty): the front end after the −90°-X rotation must be confirmed visually (GLB front is at scene Y≈0). If cones point backward, add a 180° Z flip.
- **Wide V-FOV**: the spec lists "125°(H)–43°(H), 120°(H)–38°(H)" which is ambiguous (the second pair's "(H)" label is suspect). The cone uses H-FOV only; a rectangular frustum would need the exact V-FOV.
- **Lens spacing**: sum-of-radii baseline (42.5 mm) is an estimate; the real housing baseline may differ. Adjustable in `camera_mount.json`.
- **Collision**: the rig is visualization-only and must be excluded from `collide.js`'s collision set.
