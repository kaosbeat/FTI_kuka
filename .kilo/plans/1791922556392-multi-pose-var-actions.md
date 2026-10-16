# Multi-pose var actions — unified `poses` form

## Objective

Extend variable-axis ("var") actions to multi-pose by unifying **all** actions into a
single `poses` form. Today an action is one of two shapes: a *variable-axis* form
(`base_pose` + `variable_axes` + `behavior`) that wiggles variable axes on top of one
fixed base pose, or a *legacy fixed-pose* form (`pos` list + per-pose `speed` list)
that steps through curated poses with no wiggle. The unified form is a superset: an
ordered list of anchor poses the robot steps through on the machine's existing cadence,
with an action-level wiggle (variable axes + behavior) applied on top of the current
anchor every tick. Both existing forms are expressible as special cases.

## Resolved design decisions

1. **Anchor sequence.** `poses` is an ordered list of full 6-axis anchor poses. The
   robot steps through them; the wiggle drives the variable axes on top of the current
   anchor; non-variable axes are pinned to the current anchor. Legacy var form =
   `poses: [base_pose]` + `variable_axes` + `behavior`. Legacy pos form = `poses:
   [pos...]` + `variable_axes: []` + `behavior: "calm"`.
2. **Action-level wiggle.** One `variable_axes` + one `behavior` per action (not per
   pose). Every anchor shares the same wiggle.
3. **Reuse cadence.** Advance the anchor index on the machine's existing cadence
   (`fixed` = every N ticks, `arrival` = on reaching the current target), exactly like
   legacy `pos`. The wiggle on variable axes runs continuously every tick (including
   transit), preserving the `spontaneous` nuance (re-target on cadence, hold on
   non-cadence ticks; all other behaviors re-target every tick).
4. **Unify all actions.** Replace both `base_pose` and `pos` with one `poses` field for
   every action. Drop the per-pose `speed` list (vestigial — see below).

## Unified schema

Every action is:

```json
{
  "enabled": true,
  "poses": [[n,n,n,n,n,n], [n,n,n,n,n,n]],
  "variable_axes": [0, 2],
  "behavior": "restless",
  "speed": 20,
  "loop": true,
  "next": { "zone": "...", "action": "..." }
}
```

- `poses`: non-empty list of 6-number anchors. The robot steps through them.
- `variable_axes`: list of axis indices (0-5). Default `[]` (no wiggle).
- `behavior`: one of `BEHAVIORS`. Default `"calm"`. `calm`/`bridge` are no-op
  policies (drive no variable axes).
- `speed`: single number (the action's move speed). Absent → zone speed. The old
  per-pose `speed` list is dropped (see *Why the per-pose speed list is dropped*).
- `loop`: bool, default `true`. `false` = run once, then hand off via `next`.
- `next`: unchanged (single `{zone, action}` dict or ordered list).
- `enabled`: unchanged (bool, default true).

`poses` ≥ 1 anchor. `variable_axes` default `[]`. `behavior` default `"calm"`.

## Unified runtime model

Merges `behavior.build_target` + `machine.step_action` into one per-tick path in
`machine.step`. The anchor index advances read-then-advance (the target is set to the
current anchor, then the index increments for the next tick) — identical to legacy
`step_action`. The wiggle is applied to the current anchor every tick (identical to
legacy var `build_target`), with the `spontaneous` hold nuance preserved.

Per tick, when an action is active:

```
zone    = zones.get(current_zone)
action  = zone._action(current_action)
poses   = action["poses"]
behavior = zone.action_behavior(current_action)

if behavior == "spontaneous" and not _cadence_due(curjpos):
    return self.target                    # hold the last target (spontaneous nuance)

pose = list(poses[action_index])          # read the current anchor
_apply_behavior(machine, pose, variable_axes, behavior, curjpos, action)  # wiggle (no-op if var_axes empty / calm / bridge)

if _cadence_due(curjpos):                 # advance the anchor index (read-then-advance)
    action_index += 1
    if action_index >= len(poses):
        if action_loops(current_action):  action_index = 0
        else:                             _advance_after_action(current_action)

self.target = clamp(machine, pose)
self.target_kind = "joint"
```

Entry pose = `poses[0]`; end pose = `poses[-1]` (was `base_pose` for var,
`pos[0]`/`pos[-1]` for legacy).

`build_target` is refactored to read `poses[action_index]` (the current anchor) instead
of `action_base_pose`, and apply the behavior — so it remains usable by tests and is the
single place the wiggle is computed. `step_action` is removed (its logic is folded into
`step` + the `_advance_anchor` helper). `_current_speed` drops the `"pos" in action`
branch (all actions are one form now) and uses `action_speed` (number or None → zone
speed).

## Why the per-pose `speed` list is dropped

The per-pose `speed` list is **vestigial**: it is validated (length match) in
`zones.py` and edited/swapped in the editor, but the runtime
(`machine._current_speed`, `engine.py`) and the editor's validate scan
(`editor.html` val-scan uses zone speed) both use the zone/action-level speed, never the
list. Dropping it is behavior-neutral and removes the editor var/pose form switcher
(`actionForm`, `convertActionForm`).

## Staged task list

Each stage is a safe resume point: after each, the system is in a consistent state and
the handoff log is updated. Run the baseline test suite before stage 1 to record the
starting point.

### Stage 0 — Baseline

- Run `pytest` (from `realtimecontrol/rtr`) and record the current pass/fail state.
- Run `node validate_zones.js` and record the current collision/limits report.

### Stage 1 — Schema + validation (`zones.py`)

- `_validate_action`: replace the `base_pose`/`pos` branch with a single `poses`
  branch. Require `poses` to be a non-empty list of 6-number lists. Validate
  `variable_axes` (list of 0-5 ints, default `[]`), `behavior` (in `BEHAVIORS`, default
  `"calm"`), `speed` (number, optional), `loop` (bool), `next` (unchanged). Keep the
  `bridge` no-op constraint (a `bridge` behavior must not drive variable axes).
- Migrate the built-in `ZONES` dict in place to the unified form (var actions:
  `base_pose` → `poses: [base_pose]`; pos actions: `pos` → `poses`, add
  `variable_axes: []` + `behavior: "calm"`, drop the per-pose `speed` list).
- Accessors: `action_base_pose` → return `poses[0]` (or remove and update callers);
  `action_entry_pose` → `poses[0]`; `action_end_pose` → `poses[-1]`;
  `action_variable_axes` / `action_behavior` / `action_loops` unchanged (they already
  read the unified fields). `action_speed` returns the number or None.
- Update the module docstring (the two-form description) to the unified form.

### Stage 2 — Runtime (`machine.py`, `behavior.py`)

- `behavior.build_target`: read `poses[action_index]` (the current anchor) instead of
  `action_base_pose`; apply the behavior to the variable axes. Remove the
  `if "pos" in action` early-return (no legacy form anymore).
- `machine.step`: replace the `if "pos" in action` / `else` branch with the unified
  per-tick logic above (the spontaneous hold, `build_target`, the `_advance_anchor`
  cadence advance). Add the `_advance_anchor(zone, action)` helper (read-then-advance,
  wrap on loop, else `_advance_after_action`).
- Remove `machine.step_action` (folded into `step`).
- `machine._current_speed`: drop the `"pos" in action` branch; use `action_speed`
  (number or None → zone speed).
- Update docstrings that reference `base_pose` / `pos` / the two forms.

### Stage 3 — Data migration (`zones.json`)

- Migrate every action in `zones.json` in place to the unified form:
  - var actions: `base_pose` → `poses: [base_pose]`, keep `variable_axes` /
    `behavior` / `speed` (number) / `loop` / `next`.
  - pos actions: `pos` → `poses`, add `variable_axes: []` + `behavior: "calm"`, drop
    the per-pose `speed` list (the runtime already used the zone speed for these).
  - Keep `enabled` / `loop` / `next` unchanged.
- Re-run `node validate_zones.js` to confirm the migrated data still passes
  collision/limits (the anchor poses are unchanged, so the report should be identical).

### Stage 4 — Sound fix (`commands.py`, `engine.py`, `sound.py`)

- `commands.py` `Snapshot`: add `action_index: int = 0` (additive optional field,
  present in `to_dict`).
- `engine.py`: set `action_index=self.machine.action_index` when building the Snapshot.
- `sound.py` `_on_snapshot`: key the per-pose MIDI on `snap.action_index` instead of
  the target-pose change. On action start, seed `_pose_idx` from `action_index`; on an
  `action_index` change, advance `_pose_idx` and send `poses[_pose_idx % len(poses)]`.
  This aligns the per-pose sound with the anchor index for wiggle actions (where the
  target changes every tick but the anchor advances on cadence).

### Stage 5 — Editor (`editor.html`)

- Remove the var/pose form switcher: `actionForm` / `convertActionForm` and the
  `#aeForm` select. One form now.
- `buildActionEditor`: replace the `if (form === "var") / else` split with a single
  section: a `poses` list (one row per anchor: 6 values + ▲ ▼ × + add), the
  `variable_axes` checkboxes, the `behavior` select, and the single `speed` number.
  Remove the per-pose `speed` inputs (one action-level speed).
- `actionStartPose` / `actionEndPose`: read `poses[0]` / `poses[-1]`.
- `aeSwapPoses` / `pastePose` / `copyPose`: operate on `poses` (drop the per-pose
  `speed` swap/paste; keep the sound `poses` swap).
- The new-action default (`z.actions[nm] = {...}`) uses the unified form:
  `{enabled, poses: [[0,0,0,0,0,0]], variable_axes: [], behavior: "calm", speed: 20,
  loop: true}`.
- The pose-continuity hint (var `base_pose` vs next) reads `poses[0]`.
- The val-scan already uses zone speed (no per-pose speed) — no change there.

### Stage 6 — 3D + collision (`rtr3d.js`, `validate_zones.js`)

- `validate_zones.js` `actionPoses`: replace the `base_pose` / `pos` branch with a
  single `poses` branch. For each anchor, check the anchor plus the 2^n corners of the
  variable-axis safezone box (the max-reach extremes). Return the poses + kind.
- `rtr3d.js`: the ghost arm is a single target pose (`setTarget`); the editor steps it
  locally. Confirm the editor's ghost stepping (the validate-tab pose sweep) iterates
  `poses` (it already does, via the action's pose list) — update the field name
  `pos` → `poses` where the editor drives the ghost. Per-anchor collision preview is
  already handled by the validate scan (each pose is checked) — no new `collide.js`
  change needed (the scratch-pose `checkWithMatrices` is unchanged).

### Stage 7 — Tests

- Update the test fixtures to the unified form:
  - `test_bridge_behavior.py`: `base_pose` → `poses: [base_pose]`; the legacy-form
    assertion (`"pos" in act and "base_pose" not in act`) → the unified form.
  - `test_state_machine.py`: the `step_action()` calls → the unified `step` path
    (drive ticks and assert the anchor index / target). The loop/advance scenarios
    (index 0 → 1 → 2, loop False → advance) map to the `_advance_anchor` logic.
  - `test_midi_brain_trigger.py` / `test_brain_autonomy_dwell.py`: `base_pose` →
    `poses: [base_pose]`.
  - `test_kinematics.py`: `action_base_pose("track")` → `poses[0]` (or the repurposed
    accessor); `build_target` reads the current anchor.
- Add a new test for the multi-pose wiggle: an action with `poses: [A, B]` +
  `variable_axes: [4]` + `behavior: "restless"` — assert the anchor index advances on
  cadence (target snaps to A then B) and the variable axis wiggles every tick on top
  of the current anchor.
- Add a test that the per-pose sound keys on `action_index` (the Snapshot carries it).

### Stage 8 — Full validation

- `pytest` (from `realtimecontrol/rtr`): all tests pass.
- `node validate_zones.js`: the collision/limits report is unchanged from the Stage 0
  baseline (the anchor poses are identical).
- Manually confirm (or add a smoke test) that a migrated legacy pos action steps
  through its `poses` on cadence with no wiggle, and a migrated var action wiggles its
  variable axes on top of `poses[0]`.

## Risks and mitigations

- **Sound misalignment (the one real integration bug).** The per-pose MIDI keys on
  target-pose change, which aligns with legacy pose stepping but misaligns for wiggle
  actions (target changes every tick; anchor advances on cadence). Mitigated in Stage 4
  by keying on `action_index` (exposed in the Snapshot).
- **Per-pose speed drop.** Behavior-neutral (the runtime already used zone/action
  speed), but the editor UI changes. Mitigated by dropping the switcher and the
  per-pose inputs in Stage 5.
- **Read-then-advance ordering.** The anchor must be read before the index increments
  (the target is the current anchor, the index advances for the next tick) — matching
  legacy `step_action`. A wrong order (advance-then-read) would skip `poses[0]`.
  Mitigated by the explicit per-tick logic in Stage 2 and the new multi-pose test.
- **Collision scope.** Collision is an editor/3D-preview concern
  (`collide.js` triangle-SAT, `validate_zones.js` offline check). Check each anchor
  (Stage 6); it is not a Python schema concern.

## Validation plan

- `pytest` (from `realtimecontrol/rtr`) after Stage 2, 7, and 8.
- `node validate_zones.js` after Stage 3 and 8 (report unchanged from baseline).
- The new multi-pose wiggle test (Stage 7) is the key behavioral check.
- The sound `action_index` test (Stage 7) is the key integration check.

## Open questions

None. The four design forks (anchor sequence, action-level wiggle, reuse cadence,
unify all actions) are resolved; the per-pose speed drop and the sound `action_index`
fix are determined by the code (vestigial field; target-change misalignment).
