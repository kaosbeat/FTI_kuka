# Zone Variable-Axis Rework

## Goal

Rework the zone system in `realtimecontrol/rtr/` so that:
- Zones are **pure safe-boundaries** (safezone + speed + actions only).
- Zones are linked **only** via exit-action → entry-action (`next` field).
- Actions gain a **variable axis** so camera-driven behaviors (track/focus/scan/look) can drive motion.
- `startpos`, `exitpos`, `exits`, `mode`, `find_path`, `request_zone` are removed.

## Confirmed Decisions

| Decision | Resolution |
|---|---|
| Variable-axis schema | `base_pose` + `variable_axes` + `behavior` (user confirmed) |
| Exit→entry link | Action declares `next: {zone, action}`; the action IS the transition (user confirmed) |
| Zone graph | No `exits` field; graph derived by walking all actions' `next` fields (user confirmed) |
| Navigation API | `trigger_action(zone, action)` replaces `request_zone`; no direct "go to zone X" |
| Pose continuity | Exit action's `base_pose` must equal entry action's `base_pose`; zones must overlap (user stated) |

## New Schema (zones.json)

### Zone (simplified)

```json
{
  "enabled": true,
  "safezone": [[lo, hi], [lo, hi], [lo, hi], [lo, hi], [lo, hi], [lo, hi]],
  "speed": 20,
  "actions": { ... }
}
```

**Dropped:** `startpos`, `exitpos`, `exits`, `mode`, `exitposes`.

### Action (new variable-axis form)

```json
{
  "enabled": true,
  "base_pose": [a1, a2, a3, a4, a5, a6],
  "variable_axes": [0, 4],
  "behavior": "track",
  "speed": 20,
  "loop": true,
  "next": {
    "zone": "watch",
    "action": "focus"
  }
}
```

- `base_pose`: the target pose for all **non-variable** axes. Variable axes are driven by the behavior.
- `variable_axes`: indices of axes driven by the behavior (0=A1, 1=A2, …, 5=A6). Empty list = fixed pose. Variable axis still have to remain in the safe zone.
- `behavior`: name of the behavior policy that drives the variable axes.
- `speed`: move speed for the non-variable axes (how fast the robot reaches `base_pose`).
- `loop`: whether the action loops (for fixed-pose sequences) or runs once.
- `next`: optional. When the action completes (single-run), the machine transitions to `next.zone` and plays `next.action`. The `base_pose` of this action must equal the `base_pose` of `next.action` (pose continuity; zones must overlap). Thi s equality and limits have to be checked in the action creator/editors

### Action (legacy fixed-pose form, kept for backward compatibility)

```json
{
  "enabled": true,
  "pos": [[a1,a2,a3,a4,a5,a6], ...],
  "speed": [s1, s2, ...],
  "loop": true,
  "next": { "zone": "...", "action": "..." }
}
```

### Modes

the legacy wander, random, action, track, hold  were called modes. I think we want to remove these in favour of the behavior keyword in the action json. 

Existing actions (breathe, yawn, roar, snarl, trick, bow) keep the `pos`/`speed` form during migration. Both forms are valid; an action has either `pos` or `base_pose` (not both). 

### Behavior names

| Behavior | Drives | Description |
|---|---|---|
| `track` | A1 (or `variable_axes`) | A1 follows the tracked person (camera dx). |
| `focus` | A5 (or `variable_axes`) | A5 follows the face (camera dy), keeping the face in frame. |
| `scan` | A1 (or `variable_axes`) | A1 sweeps back and forth. |
| `look` | A1 (or `variable_axes`) | A1 follows the tracked person (alias for `track` with different gain/dead-zone). |
| `wander` | A1–A3 | Gentle continuous drift (existing `wander` policy). |
| `random` | A1–A3 | Jump to random poses (existing `random_pose` policy). |
| `hold` | none | Keep the last commanded pose (no variable axes). |

## What Goes Away (code)

### `zones.py`
- `Zone.startpos`, `Zone.exitpos`, `Zone.exits`, `Zone.exitpose_for` properties.
- `Zones.find_path`, `Zones.can_exit_to` methods.
- `ACTION_KINDS` constant (replaced by the `next` field).
- Validation for `startpos`, `exitpos`, `exits`, `exitposes`, `mode` in `_validate_zone`.
- modes, there is no need for them now that we hgave behaviour

### `machine.py`
- `request_zone`, `request_exit` methods.
- `_commit_zone` (simplified: no entry_action param, the `next` field handles it).
- `update` method (transition logic simplified).
- The `self._transition` dict structure (simplified).

### `behavior.py`
- `wander`, `random_pose`, `track` free functions (replaced by variable-axis behavior policies).
- The `machine.mode` dispatch in `step()` (replaced by action-based dispatch).

### `brain.py`
- `_handle_cam_track` (zone-gated nudge) — replaced by camera state provider.
- `TRACK_ZONES`, `LOOK_ZONES`, `STILL_ZONES` constants — replaced by action-level behavior.

## What Stays

### `zones.py`
- `safezone`, `speed`, `actions` properties.
- `effective_limits`, `HARDWARE_LIMITS`, `SOFTWARE_LIMITS`, `LIMITS`.
- `MODES` constant (retained for backward compat but no longer used by zones).

### `behavior.py`
- `clamp`, `hardware_clamp` functions.
- `effective`, `safezone` helper functions.

## What's New

### `zones.py`
- Validation for `base_pose`, `variable_axes`, `behavior`, `next` in action schemas.
- `Zone.action_base_pose(name)`, `Zone.action_variable_axes(name)`, `Zone.action_behavior(name)`, `Zone.action_next(name)` accessors.
- `Zones.graph()` method: returns the zone graph derived from all actions' `next` fields (for display/editor).

### `machine.py`
- `trigger_action(zone_name, action_name)`: begins a transition to `zone_name` and plays `action_name` on arrival. Validates that the action exists and is enabled.
- Simplified `step()`: dispatches to the current action's behavior (which reads camera state from the machine).
- `camera_state` attribute: stores the latest camera telemetry (set by the brain each tick).

### `behavior.py`
- New behavior policy functions that take `(machine, curjpos, camera_state)` and return the target pose.
- Each behavior modifies only the `variable_axes`; non-variable axes are set to `base_pose`.
- `build_target(machine, curjpos)`: dispatches to the current action's behavior.

### `brain.py`
- Camera state provider: reads camera telemetry and stores it in `machine.camera_state` each tick.
- No longer calls `machine.set_target()` or `machine.set_mode()` directly for camera tracking.

## Impact by Module

| Module | Change level | Notes |
|---|---|---|
| `machine.py` | **Major** | Rewrite transition logic; add `trigger_action`, `camera_state`; simplify `step()` |
| `zones.py` | **Major** | Remove `startpos`/`exitpos`/`exits`; add new action accessors; add `graph()` |
| `behavior.py` | **Major** | Replace free-function policies with variable-axis behavior policies |
| `brain.py` | **Medium** | Replace `_handle_cam_track` with camera state provider |
| `zones.json` | **Major** | Migrate all 8 zones to new schema; drop `startpos`/`exitpos`/`exits`/`mode` |
| `display.py` | **Medium** | Render zone graph from `next` fields; show variable-axis actions |
| `editor.html` | **Medium** | Edit `base_pose`/`variable_axes`/`behavior`/`next` on actions; remove zone-level pose editing |
| `httpserver.py` | **Medium** | Remove `POST /api/zone`; add `POST /api/action` (trigger_action) |
| `main.py` | **Minor** | Wiring changes: brain provides camera state; machine dispatches to action behavior |
| `camera.py` | **None** | Unchanged (CameraController/Intent still work the same) |
| `pipeline.py` | **None** | Unchanged |
| `camera_remote.py` | **None** | Unchanged |

## Concrete Cases (from user)

### Case 1: Rest → Track (A1 follows person, audience walks 360°)

```json
"rest": {
  "safezone": [[-5,5], [-134,-130], [155,157], [-3,3], [-10,10], [-357,357]],
  "speed": 20,
  "actions": {
    "track_person": {
      "base_pose": [-3, -133, 156, -2, 0, 0],
      "variable_axes": [0],
      "behavior": "track",
      "speed": 20,
      "loop": true,
      "next": { "zone": "watch", "action": "focus_face" }
    }
  }
}
```

The robot stays at the rest pose on A2–A6 (base_pose) while A1 is driven by the camera track behavior. The safezone clamps A1 to [-5, 5] (the rest zone's A1 range). When the user wants to move closer, the `next` field transitions to the `watch` zone's `focus_face` action.

### Case 2: Watch → Focus (A5 rotates to hold face, multiple axes move)

```json
"watch": {
  "safezone": [[-20,20], [-134,-130], [155,157], [-3,3], [0,60], [-357,357]],
  "speed": 20,
  "actions": {
    "focus_face": {
      "base_pose": [-3, -133, 156, -2, 0, 0],
      "variable_axes": [4],
      "behavior": "focus",
      "speed": 20,
      "loop": true
    }
  }
}
```

The `base_pose` is the same as the `rest.track_person` action's `base_pose` (pose continuity). The safezone overlaps with the rest zone at this pose. A5 is driven by the camera focus behavior (keeps the face in frame). The non-variable axes (A1–A4, A6) are held at `base_pose`.

**Note:** The user mentioned "multiple axes move" during the transition. The transition from `rest` to `watch` involves the robot moving from the rest pose to the watch pose. If the base_poses are the same (as above), the transition is seamless — the robot doesn't move on non-variable axes, only the safezone changes (allowing A5 to move from [-10, 10] to [0, 60]). If the base_poses differ, the robot moves on the non-variable axes during the transition.

## Migration Path (zones.json)

**Phase 1 — Additive (backward compatible):**
- Add `base_pose`, `variable_axes`, `behavior`, `next` fields to actions alongside existing `pos`/`speed`.
- Validation accepts both forms.
- `machine.py` supports both: if an action has `base_pose`, use the new path; if it has `pos`, use the old path.
- `zones.json` still has `startpos`/`exitpos`/`exits`/`mode` (ignored by the new code).

**Phase 2 — Migrate zones.json:**
- Rewrite all 8 zones to the new schema.
- Drop `startpos`/`exitpos`/`exits`/`mode` from zones.
- Convert fixed-pose actions to the new schema where practical:
  - `breathe`: `base_pose` = center of breathe cycle, `variable_axes` = [0, 1, 2, 4], `behavior` = "breathe" (new oscillation behavior) or keep `pos`/`speed` form.
  - `yawn`: `base_pose` = rest pose, `variable_axes` = [], `behavior` = "hold".
  - `roar`, `snarl`, `trick`, `bow`: keep `pos`/`speed` form (they're fixed sequences).
- Add `next` fields to define the zone graph.

**Phase 3 — Remove old schema:**
- Remove `pos`/`speed` support from `machine.py` and `zones.py` validation.
- Remove `startpos`/`exitpos`/`exits`/`mode` from `zones.py` validation.
- Remove `find_path`, `request_zone`, `request_exit` from `machine.py`.

## Open Questions

1. **`wander`/`random`/`hold` modes**: Currently zone-level modes. Should they become variable-axis actions (e.g., `wander` action with `variable_axes: [0, 1, 2]`, `behavior: "wander"`)? Or stay as zone-level modes? **Recommendation:** Make them actions. The zone's `mode` field goes away; the mode is implicit in the action being played.

2. **Camera state access**: The behavior policies need camera state (dx, dy, lock, etc.). Should the machine store `camera_state` as an attribute (set by the brain each tick), or should the behavior read it from the CameraController directly? **Recommendation:** Machine stores it (cleaner separation; the brain is the sole camera-state provider).

3. **Behavior gain/dead-zone parameters**: The `track`/`focus` behaviors need gain and dead-zone parameters (currently `_CAM_GAIN` in brain.py). Should these be per-action in zones.json (e.g., `"gain": 0.5, "deadzone": 10`)? Or per-behavior in a separate config? **Recommendation:** Per-action in zones.json (the action knows how it wants to respond).

4. **`speed` semantics**: For variable-axis actions, `speed` applies to the non-variable axes (how fast the robot reaches `base_pose`). The variable axes move at the rate determined by the behavior. Is this correct? **Recommendation:** Yes.

## Validation Plan

1. **Unit tests**: Schema validation for new action fields (`base_pose`, `variable_axes`, `behavior`, `next`).
2. **Unit tests**: `trigger_action` — valid/invalid transitions, pose continuity check.
3. **Unit tests**: Behavior policies — verify variable axes are driven, non-variable axes are held at `base_pose`.
4. **Integration test**: `camera_mock` → brain → machine → action behavior → target pose (full round trip).
5. **Display test**: Zone graph renders correctly from `next` fields.
6. **End-to-end**: Run with `--sim` and verify the robot transitions between zones via actions.

## Files to Modify

- `realtimecontrol/rtr/rtr/state/machine.py`
- `realtimecontrol/rtr/rtr/state/zones.py`
- `realtimecontrol/rtr/rtr/state/behavior.py`
- `realtimecontrol/rtr/rtr/brain/brain.py`
- `realtimecontrol/rtr/zones.json`
- `realtimecontrol/rtr/rtr/display/display.py`
- `realtimecontrol/rtr/rtr/display/editor.html`
- `realtimecontrol/rtr/rtr/display/client.html`
- `realtimecontrol/rtr/rtr/httpserver.py`
- `realtimecontrol/rtr/main.py`
