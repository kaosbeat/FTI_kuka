# Camera-Orientation-Aware Servoing (toolhead-mounted camera)

## Progress

- [x] **P1** — Kinematic foundation. `rtr/robot/kinematics.py` (FK + geometric Jacobian, pure stdlib), `kr60_chain.json` (chain from the URDF), `rtr/robot/test_kinematics.py` (self-test). Verified: FK reach physically plausible (home=2.015 m, stretch=2.672 m); analytic Jacobian matches finite-difference to ~5e-7; damped pseudo-inverse maps a base-frame displacement to the correct joint deltas.
- [x] **P2** — Camera geometry (the orientation term). `rtr/camera/geometry.py` (`CameraGeometry`: `camera_pose(j) = FK(j)·T_tool_cam`, `offset_to_base_direction(dx,dy,z_cam,j) = R_cam·(dx,dy,z_cam)`), `camera_mount.json` (tool→camera mount, placeholder). Verified: `camera_pose == FK·T_tool_cam` and `offset_to_base_direction == R_cam·v` (exact); the same image offset yields different base directions at different arm poses (A1=0 vs A1=120).
- [ ] **P3** — Wire into the loop.
- [ ] **P4** — Calibration tool.
- [ ] **P5** — Validation.

## Problem (confirmed in code)

The camera offset is **orientation-blind**. The current chain:

- `rtr/camera/pipeline.py:195` — RPI sends `dx = cx - W/2`, `dy = cy - H/2` (pixels, **camera image frame**).
- `rtr/brain/brain.py:139` — stores `machine.camera_state = {dx, dy, w, h, ...}` raw.
- `rtr/state/behavior.py:124` — `_apply_camera` does `pose[axis] += offset * gain` (track→A1, focus→A5), a **fixed axis-aligned joint gain**.

There is **no FK, no camera-orientation term, no Jacobian**. So it only works if the camera always points in a fixed base-frame direction. On a toolhead-mounted camera that rotates with the arm, the image-x/image-y directions (and their sign) change with every arm configuration — so as the arm moves, the robot pushes the *wrong* direction and tracking drifts/breaks. **The current algorithm cannot take this into account; it has no model of where the camera is pointing.**

The camera is mounted on the toolhead (the two lenses are the cylinders in `assets/tool.glb`), so the camera pose is a function of the 6 joint angles.

## The fix

Rotate the camera-frame offset into the base frame using the camera's **current** pose (from FK), then map that base-frame direction to joint motion with the **kinematic Jacobian**:

```
T_base_cam(j) = FK(j) · T_tool_cam          # camera pose in base frame
d_base        = R(T_base_cam) · (dx, dy, a) # offset → base-frame direction
dq            = J_v⁺(j) · d_base            # damped pseudo-inverse → joint deltas
target        = base_pose + dq               # restricted to the action's variable_axes
```

- `(dx, dy)` = centering (track). `a` = approach term from bbox size `w, h` (follow/approach).
- `R(T_base_cam)` is the **orientation term** — this is the actual fix.
- `J_v⁺` is configuration-aware, so the same pixel offset produces the correct joint move in any arm pose.
- `variable_axes` already encodes the track/follow split: `[0,4]` = head-only (return-to-base); a full set = full-arm follow.

## Frame conventions

- **Base frame** = KUKA `$ROBROOT` (URDF `base_link`, ROS Z-up). The world the robot moves in.
- **tool0** = KUKA `$FLANGE` (URDF `tool0` link, = `link_6` rotated +90° about Y).
- **Camera frame** = tool0 transformed by `T_tool_cam` (the tool→lens mount, from `tool.glb`). Image x→right, y→down, z→optical axis (forward), by convention.
- **Joint angles** = KUKA A1..A6 (degrees), mapped 1:1 to the URDF `joint_a1..a6` angles (the standard KUKA-ROS assumption).

## Kinematic ground truth

The KR60 chain is defined by the URDF at
`kr60/models/kuka_experimental/kuka_kr60_support/urdf/kr60ha_macro.xacro`:

| Joint | origin xyz (m) | axis | limit (deg) |
|-------|----------------|------|-------------|
| A1 | (0, 0, 0) | (0,0,-1) | ±185 |
| A2 | (0.35, 0, 0.815) | (0,1,0) | −135..35 |
| A3 | (0.85, 0, 0) | (0,1,0) | −120..158 |
| A4 | (0.465, 0, 0.145) | (−1,0,0) | ±350 |
| A5 | (0.355, 0, 0) | (0,1,0) | ±119 |
| A6 | (0.17, 0, 0) | (−1,0,0) | ±350 |
| tool0 (fixed) | (0, 0, 0) rpy=(0, 90°, 0) | — | — |

FK walk (each joint `T = Trans(xyz)·Rot(rpy)·Rot(axis, θ)`):
`R_child = R_parent·Rot(rpy)·Rot(axis,θ)`, `t_child = t_parent + R_parent·xyz`.
Geometric Jacobian: `J_ω_i = R_child_i·axis_i`, `J_v_i = z_i × (p_ee − p_i)`.
Position servo uses the **linear** part `J_v` (3×6): `dq = J_v⁺ d_base`.

## Decisions (made independently)

- **Foundation = a KR60 kinematic model** built from the URDF chain. Pure-Python (stdlib `math`) to keep the core dependency-free (the core has no numpy today; numpy/scipy are only listed for the RPI pipeline). FK + a 6×6 Jacobian are trivially cheap at 20 Hz.
- **Real robot:** the controller already reports the TCP pose (`kuka.get_curpos()`), so `T_base_tool` can come from the controller (exact) when available; FK is the sim fallback and the Jacobian source either way.
- **`T_tool_cam`** (tool→camera mount) comes from the `tool.glb` lens positions, refined by calibration.
- **Two servo modes** (reusing `variable_axes`): `track/focus/look` = return-to-base (robot holds base_pose, head servos); `follow` = integrating `curjpos + dq` (the arm approaches the person).

## Step-by-step phases (each independently testable)

### P1 — Kinematic foundation
- New `rtr/robot/kinematics.py`: load the chain (origins/axes/rpy) from a data file; `fk(j) -> (pos, R)` (tool0 in base); `jacobian(j) -> (J_ω, J_v)`; `linear_jacobian(j)`; damped pseudo-inverse `pinv`.
- New `kr60_chain.json` (rtr root, next to `zones.json`): the chain data from the URDF table above.
- Self-test `test_kinematics.py` (stdlib, like `sim_demo.py`): FK at known poses (home, rest, stretch) gives a sensible reach; analytic `J_v` matches a finite-difference Jacobian.
- **Exit:** FK is self-consistent (numerical == analytic Jacobian) and the reach is physically plausible.

### P2 — Camera geometry (the orientation term)
- New `rtr/camera/geometry.py`: `T_TOOL_CAM` (tool→camera), `camera_pose(j)` (camera frame in base), `offset_to_base_direction(dx, dy, a, j)` = `R_cam_base · (dx, dy, a)`.
- New `camera_mount.json` (rtr root): the tool→camera transform (from `tool.glb` lens layout; placeholder until the final tool is finalized).
- **Exit:** for several known arm poses, the computed base direction points the correct way (debug overlay / printed).

### P3 — Wire into the loop
- `direction_to_dq(d_base, j, var_axes)`: `dq = J_v(var_axes)⁺ · d_base` (reduced Jacobian over the action's variable axes).
- Modify `behavior.py::_apply_camera` to use the geometry (replace the fixed `offset * gain`) — keep `gain` as a scale on `d_base` and a dead-zone.
- `brain.py::_handle_cam_track`: store `w, h` on `camera_state` (approach term); keep `dx/dy`.
- Wire the kinematics + geometry into the machine (`main.py`), and give `machine` access to the current joint pose for FK (it already receives `curjpos` each tick).
- **Exit:** sim + `camera_mock` — the robot centers the target across full-arm reconfigurations (not just at one pose).

### P4 — Calibration tool
- CLI + display: command known robot motion → read the resulting camera offset → derive `T_TOOL_CAM` and per-axis/per-zone gains. Overlay the camera orientation + computed `d_base` for live debug.
- **Exit:** a calibration run produces a usable `camera_mount.json` + gains, stored back to disk.

### P5 — Validation
- Sim + `camera_mock`: tracking holds across full-arm reconfigurations.
- Field test: track **and** follow a person; the camera stays centered as the arm moves.

## Open items (late-stage, non-blocking)

- Exact `T_TOOL_CAM` (needs the final `tool.glb` lens layout / a physical cal run).
- Per-zone gain + approach `a` constant (field-calibrated, as the earlier plan already noted).
- Whether `follow` is a distinct behavior name vs a `variable_axes` + `servo` flag.
- Confirm KUKA joint-angle ↔ URDF angle sign/direction against the real controller (P1 assumes 1:1).
