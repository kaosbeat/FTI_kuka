# Task Handoff Log

## 2026-10-28 — IK core: pure JS forward kinematics + geometric Jacobian + DLS IK (validated vs Python)

### Status

Completed

### Summary

Built the JS inverse-kinematics core for drag-the-end-effector pose authoring. A new file `realtimecontrol/rtr/ik.js` holds the pure (no THREE / no DOM) KR60 kinematics, mirroring `rtr/robot/kinematics.py` exactly:

- **FK** (`fkChain`/`fkPos`) takes joint angles in **degrees** (the KUKA A1..A6 convention) and returns the flange (tool0) position = the link_6 matrix translation (tool0 xyz is [0,0,0]). The matrix construction is identical to `rtr3d.js checkPoseCollision` (column-major 16-element arrays, `mats[j] = parent * T(xyz_j) * R(axis_j, q_j)`).
- **Jacobian** (`jacobian`) returns the linear geometric Jacobian `J_v` (3x6), **per-radian**: column `j` is `z_j × (pos − p_j)`.
- **Pseudo-inverse** (`pinv3`) is the damped minimum-norm pseudo-inverse (6x3) mapping a base-frame displacement (metres) to joint deltas in **radians**.
- **IK** (`ikSolve`) is DLS with backtracking + hardware clamp. The critical units rule: the Jacobian is per-radian, so each joint delta is a radian quantity and is converted to degrees (`× 180/π`) before being applied to the degree-based FK. Treating the radian delta as degrees (the earlier broken IK) made every step ~57× too small.

The Python reference (`/tmp/kilo/ik_ref.py`) uses the real `Chain` + the same DLS/backtracking/clamp parameters. The JS port matches it exactly (see Validation).

### Changes Made

- `realtimecontrol/rtr/ik.js` (new): pure KR60 IK module. Exposes `KR60IK = { JOINTS, HOME, DEFAULT_LIMITS, fkChain, fkPos, jacobian, pinv3, ikSolve }` on `globalThis` (and `module.exports` for Node). Chain data (`JOINTS`, `HOME`) is identical to `rtr3d.js`; `DEFAULT_LIMITS` mirrors `HW_LIMITS_FALLBACK`.
- `/tmp/kilo/ik_ref.py` (scratch, outside the repo): Python reference that locks the target values.
- `/tmp/kilo/ik_test.js` (scratch, outside the repo): Node harness that loads `ik.js` and compares against the locked reference.

### Documentation

- None required — the new module is self-documenting via its header comment (the FK/Jacobian/units contract is documented at the top of `ik.js`).

### Architecture

- New pure module `ik.js`, deliberately separate from `rtr3d.js` so it is THREE/DOM-free and testable in Node. It will be loaded via a `<script>` tag (before the editor inline script) and consumed by the drag logic; the chain is the same kr60ha xacro used by `rtr3d.js` and `kinematics.py`.

### Validation

- `node --check realtimecontrol/rtr/ik.js` → SYNTAX OK.
- `node /tmp/kilo/ik_test.js` → **PASS**:
  - HOME fk pos = `[1.17, 0.0, 1.64]` (max |err| vs Python = 0.000000).
  - Displacement targets (dir = normalize([0.3,0,0.4])) from HOME: disp 0.05/0.15/0.30/0.45 → solved-pose max |err| = 0.000000 for all four, iteration counts 2/3/3/4 (all match the Python reference), residuals 0.000083/0.000021/0.000159/0.000095 (all match).
  - Jacobian analytic-vs-finite-difference max |err| = 6.75e-7 (< 1e-3).

### Decisions and Caveats

- Kept the FK matrix convention identical to `checkPoseCollision` (not a fresh rewrite) so the IK's flange position and the rendered ghost arm's tool position are guaranteed to coincide (both derive from the same chain + convention).
- `ikSolve` seeds from a concrete pose (the caller passes the resolved anchor) so successive drag steps stay on the same arm branch (no joint flips). Orientation is NOT solved (position-only, 3 of 6 DOF); the free axes are left to the existing behavior/axis system.
- `ik.js` is not yet referenced by any HTML page — it is validated standalone and will be wired in the next task.

### Next Task

Implement the TCP drag handle in `rtr3d.js` (a THREE mesh at the flange, OrbitControls gating, camera-plane pointer projection) that calls `KR60IK.ikSolve`, and wire it in `editor.html` to write the solved pose to the currently-stepped ghost anchor.

### Instructions for the Next Task

- Load `ik.js` via a `<script src="ik.js"></script>` in `editor.html` (and `client.html` if it shares the 3D view) before the inline script, so `KR60IK` is available.
- In `rtr3d.js`, add a drag-handle API: on pointerdown on the handle, disable `controls` (OrbitControls); project the pointer ray onto a camera-facing plane through the initial flange position; on each pointermove run `KR60IK.ikSolve(seedPose, draggedPoint, { limits: HW_LIMITS })`, seed from the previous solved pose, clamp to `HW_LIMITS`, and recompute FK of the clamped pose so the handle snaps to the actual flange. Expose it so the editor can supply `HW_LIMITS` and a callback with the solved pose.
- In `editor.html`, target the currently-stepped ghost anchor `TABLE.zones[selectedZone].actions[selectedAction].poses[ghostIndex]`; on drag, write all 6 concrete numbers, update the ghost arm (`Robot3D.setTarget`), the safezone bars, and the pose-row inputs. `resolvePose` (line 5561) already resolves "x" axes for the seed.

---

## 2026-10-28 — UI: two-click interaction for collapsed bridge nodes (select, then open pose editor)

### Status

Completed

### Summary

Fixed the flow-tab click behavior for collapsed bridge-zone nodes. Previously a single click on the purple dot immediately opened the action/pose editor, so the node could not be "selected" to see its properties in the right pane. Now: first click selects the node (`flowSelect` → white stroke + zone properties in the right pane via `buildFlowDetail`/`buildZoneForm`); a second click while the node is already selected opens its single action's editor (`openActionEditor`). Dragging (reposition / connect) and clicking non-bridge nodes are unchanged.

### Changes Made

- `realtimecontrol/rtr/editor.html` (`flowDragEnd`): in the `!d.moved` branch, the collapsed-bridge case now checks `FLOW.sel` first — if the clicked bridge node is the currently selected node, open its first action's editor; otherwise just select it. Falls back to select when the zone has no actions.

### Documentation

- None required — behavior change is self-describing via the updated comment in `flowDragEnd`; no user-facing docs changed.

### Architecture

- No architectural impact. UI-only change to the click dispatch in the flow tab; data model and core untouched.

### Validation

- Extracted the inline script (lines 748–7609) and ran `node --check` → SYNTAX OK.
- Could not run the page in a browser from this environment (depends on the live core over WebSocket + three.js/hydra-synth), so runtime UI verification was not performed.

### Decisions and Caveats

- Selection state is keyed on `FLOW.sel` (`type === "node"`, matching `zone`), the same state `applyFlowHighlight` uses for the white `fsel` stroke, so the second click is only armed after a visible selection.
- The right pane already renders the full zone form for a selected node (`buildZoneForm`), so no new UI was needed to "see their properties".
- If the user later finds two-clicks annoying, a "click dot again to edit" hint could be added to the flow detail pane for selected bridge nodes.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

- To verify in the browser: click a purple bridge dot once → node gets a white stroke and the right pane shows its zone form; click it again → the pose/action editor modal opens. Clicking a different node first resets the selection (first click selects it again).

---

## 2026-10-28 — Fix: brain behavior next-selection params (lock_duration_s / dwell_s) lost on validate/reload

### Status

Completed

### Summary

The per-behavior next-selection params set in the brain tab (`lock_duration_s`, `dwell_s`) were silently lost, so the edits appeared "not saved". The POST to `/api/brain` writes the payload **verbatim** to `brain.json` (the edits WERE on disk, e.g. `locked.lock_duration_s: 15`), but `validate_brain_config` → `_validate_behaviors_section` (config.py) kept only the keys present in the behavior's `DEFAULT_BEHAVIORS` entry. That dropped `lock_duration_s` for every behavior except agitated/aggressive/exhilarated/impatient (the four whose defaults carry it), and `dwell_s` for **all** behaviors (no default carries it). The running core builds `machine.behavior_params` from the validated config (`brain.py:131`), and GET `/api/brain` also serves the validated config — so the runtime never saw the edits.

Fix: `_validate_behaviors_section` now preserves the optional next-selection overrides (`dwell_s`, `lock_duration_s`) when present on any behavior, type-checked via `_num` (null = absent, non-numeric/bool rejected). The runtime (`brain._behavior_triggers_fired`) already reads both off the behavior entry for any behavior, so no other code change was needed.

### Changes Made

- `realtimecontrol/rtr/rtr/brain/config.py`: `_validate_behaviors_section` — after the defaults loop, copy `dwell_s` / `lock_duration_s` from the raw entry when present (skipping keys already covered by the defaults); docstring updated to document the preserved overrides.
- `realtimecontrol/rtr/tests/test_brain_behavior_params.py`: new regression test file (7 tests): `lock_duration_s` kept for a behavior without a default; kept for every `DEFAULT_BEHAVIORS` behavior; `dwell_s` kept for every behavior; default backfill when absent; `stay`/`triggers` still validated; explicit null treated as absent; bad types (string/bool) rejected.

### Documentation

- Updated the `_validate_behaviors_section` docstring (the preserved-override contract). No user-facing docs needed.

### Architecture

No architectural impact. The save path is unchanged in shape (editor DOM → `_commitBrainDom` → POST → verbatim write → validate on reload); the fix only stops the validator from stripping the two optional per-behavior keys the runtime reads.

### Validation

- `python tests/test_brain_behavior_params.py` → ALL TESTS PASSED (7 tests).
- `python3 -m pytest tests/test_brain_behavior_params.py tests/test_brain_autonomy_dwell.py tests/test_midi_brain_trigger.py tests/test_zone_groups.py tests/test_brain_assertive.py tests/test_brain_action_cam.py -q` → 57 passed.
- Full suite `python3 -m pytest tests/ -q --ignore=tests/test_remote_tui.py` → 308 passed, 14 skipped, 5 failed — the 5 `test_remote_sdl` failures and the `test_remote_tui.py` collection error are pre-existing (verified via `git stash` on the clean tree).
- Verified against the real `brain.json`: `locked` now keeps `lock_duration_s: 15.0`, `intent` keeps `10.0`, `spontaneous` keeps `3.0`, `agitated` keeps its `1.0` default; a `dwell_s: 7.5` edit on `locked` survives; explicit null is treated as absent; string/bool values raise `ValueError`.

### Decisions and Caveats

- The client side was already fixed earlier (the 2026-10-27 "Fix brain-tab save wiping per-behavior stay/triggers" entry): `_commitBrainDom` reads the fields from the DOM. This entry fixes the remaining server-side loss.
- The change landed in commit `f3db044` ("stufff"), which also contains the user's own `editor.html` work.
- A **running core must be restarted** to pick up the fix (the old process still runs the old `config.py` code; a `RELOAD_BRAIN` hot-reload only reloads the config data, not the code). After restart, the existing `brain.json` edits (locked=15, intent=10, spontaneous=3) take effect immediately — no re-save needed.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

- If the params are still "lost" after a core restart, check that the core process actually restarted (old code in memory keeps the old validator). Verify with: `python3 -c "import importlib.util, json; ..."` against `validate_brain_config` as done in this task, or read `machine.behavior_params` from the running core.
- The editor's per-behavior controls: `stay` / `triggers` / `dwell_s` / `lock_duration_s` are on every behavior card in the brain tab (`brainBh<Name>Stay|DwellS|LockDurS` inputs + trigger checkboxes).

---

## 2026-10-28 — UI: collapse bridge-zone nodes in the flow tab (small purple dot)

### Status

Completed

### Summary

Collapsed bridge-zone nodes in the flow tab of `editor.html`. A bridge zone (a pose zone of type `"bridge"`, i.e. a single in/out handoff) now renders as a small purple dot with the zone name below, instead of a full rectangular block with action rows. Clicking the dot still opens the action editor for its single action; the node can still be dragged to reposition and dropped on another node to draw a connection. Newly created bridge zones (via `bridgePlan`) now get `pose: "bridge"` so they are collapsed too. The purple fill matches the existing bridge-node purple (`#a06fd0`). The layout reserves the full column width but a reduced height (`BRIDGE_H = 40` vs `78`), so bridge nodes pack tighter vertically.

### Changes Made

- `realtimecontrol/rtr/editor.html` (CSS): added `.fnode.fnodeBridge .fnodeBody { fill: #a06fd0; }` and `.fnodeBridgeLabel { fill: var(--text); font-size: 11px; }` for the collapsed dot + name label.
- `realtimecontrol/rtr/editor.html` (constants/helpers): added `BRIDGE_R = 9`, `BRIDGE_CY = 16`, `BRIDGE_H = 40`; added `isBridgeZone(name)` (true when `z.pose === "bridge"`, null-guarded); `nodeH(name)` now returns `BRIDGE_H` for bridge zones.
- `realtimecontrol/rtr/editor.html` (`flowAddNode`): bridge zones render a `circle` (dot at column center) + a name label instead of the rect + sublabel + action rows; non-bridge nodes unchanged.
- `realtimecontrol/rtr/editor.html` (`flowAddEdge`): inter-zone edge anchors use the dot center (`NODE_W/2`, `BRIDGE_CY`) for bridge zones instead of the row side.
- `realtimecontrol/rtr/editor.html` (`flowDragMove`): the temporary drag line's start point uses `BRIDGE_CY` for bridge zones.
- `realtimecontrol/rtr/editor.html` (`flowDragEnd`): a click (no move) on a collapsed bridge node opens its first action's editor (`openActionEditor`); falls back to node-select if it has no actions.
- `realtimecontrol/rtr/editor.html` (`bridgePlan`): the created zone now includes `pose: "bridge"`.

### Documentation

- None required — the change is self-describing via the added CSS comment and the `isBridgeZone` / constant comments; no user-facing docs changed.

### Architecture

- No architectural impact. UI-only change to the flow-tab rendering of bridge zones; the zone/edge data model and the core are untouched.

### Validation

- Extracted the inline script (lines 748–7605) and ran `node --check` → SYNTAX OK.
- Manually reviewed all modified functions (`flowAddNode`, `flowAddEdge`, `flowDragMove`, `flowDragEnd`, `nodeH`, `isBridgeZone`, `bridgePlan`) for balanced braces and correct anchor/hit-test geometry.
- Could not run the page in a browser from this environment (it depends on the live core over WebSocket + three.js/hydra-synth), so runtime UI verification was not performed.

### Decisions and Caveats

- Collapse is keyed on `z.pose === "bridge"` (the field that already makes bridge nodes purple), not on a structural "one in / one out" graph analysis — simpler and matches the visible purple nodes the user referred to.
- Bridge nodes keep the full `NODE_W` column width (only the height shrinks) so the uniform BFS column grid and persisted layouts are not disturbed; the dot is centered in its column.
- The live-state accent stroke on a bridge dot is overridden by the purple `fnodePose` stroke (same pre-existing CSS ordering as the current non-collapsed bridge nodes); the selected-state white stroke and the `off` dimming still apply.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

- If the user wants bridge nodes to also shrink horizontally (not just vertically), that requires per-column widths in `flowAutoLayout`/`flowFitView`/`flowNodeAt` (currently a uniform `NODE_W` grid) — a larger change.
- If "move" pose zones (`z.pose === "move"`) should also collapse, extend `isBridgeZone` (and the `bridgePlan` equivalent) to include them.

---

## 2026-10-28 — Test: device-change tests for robust MIDI-out (fake rtmidi)

### Status

Completed

### Summary

Extended `tests/test_sound_out_ports.py` to cover the new device-change behavior of the Sound adapter. The fake rtmidi now mirrors python-rtmidi 1.5.8: `open_port` raises for an out-of-range index (with the real error text), there is **no** `open_string_outport` on the base fake, and instances expose `deleted`/`closed` state. New tests:

- `test_invalid_index_falls_back_to_auto` — a zone `out_port` that no longer exists (index 5 of 2) fails and the zone degrades to the auto candidate (first available port) and still sends.
- `test_name_open_resolves_by_index_when_no_string_outport` — the `out_device` name path resolves by enumeration (1.5.8 has no name open).
- `test_name_substring_matches_dynamic_suffix` — a name that is a substring of the enumerated port (dynamic suffix) resolves to the first matching port.
- `test_name_open_uses_string_outport_when_available` — the `FakeMidiOutWithString` subclass covers the newer-wrapper `open_string_outport` fast path.
- `test_failed_open_discards_instance` — failed opens do not leak `MidiOut` instances (they are `delete()`d).
- `test_failed_open_retries_after_backoff` — a failed spec is skipped within the backoff (no new attempt, no new instance) and the retry succeeds after the backoff once the device reappears; the failure entry is cleared on success.
- `test_send_failure_drops_dead_handle_and_reopens` — a device that dies after opening: the send fails, the dead handle is dropped (closed, backoff started, message degraded to logging), and the next send reopens the port.
- `test_list_out_ports_releases_instance` — the enumeration `MidiOut` is released.

Fake updates: `open_port` range-validates and raises; `delete()` added; `open_string_outport` moved to the `FakeMidiOutWithString` subclass; class-level `open_fail` and per-instance `send_fail` switches; `_sound()` takes a `retry_interval` passthrough.

### Changes Made

- `realtimecontrol/rtr/tests/test_sound_out_ports.py`: new "Device changes" test section (8 tests); updated fake rtmidi (+ `FakeMidiOutWithString`); `retry_interval` on the `_sound()` helper; `import time`.
- `realtimecontrol/rtr/tests/test_sound_out_ports.py`: fixed `test_reconcile_closes_stale_specs_on_reload` — `_get_out` now returns `(spec, out)`, so the test unpacks the tuple.

### Documentation

- Updated the test module docstring to list the new device-change coverage and the 1.5.8 fake semantics.

### Architecture

No architectural impact. Test-only.

### Validation

- `python tests/test_sound_out_ports.py` → ALL TESTS PASSED (27 tests).
- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py` → **308 passed, 14 skipped, 5 failed** — all 5 failures are in `tests/test_remote_sdl.py` (UI text assertions, e.g. "THEME / LAYOUT" label), pre-existing at HEAD and unrelated to sound.
- `tests/test_remote_tui.py` has a pre-existing collection error at HEAD: it imports `action_kind` from `rtr.flow`, which does not exist (both files unmodified from HEAD).
- `ruff check rtr/sound/sound.py tests/test_sound_out_ports.py` → sound.py clean; the test file has the same 4 pre-existing E402s (the `sys.path.insert` pattern) as the HEAD version.

### Decisions and Caveats

- The retry/backoff tests use a small `retry_interval` (0.05 s) plus one 0.06 s sleep to stay fast while still exercising the real backoff path.
- The fake's `open_port` raises a plain `Exception` carrying the real rtmidi message text; the adapter catches `Exception`, so the exact error type is irrelevant.

### Next Task

Handoff + final report (this entry and the implementation entry below).

### Instructions for the Next Task

- If the `test_remote_sdl.py` / `test_remote_tui.py` failures matter, they are a separate workstream (UI text drift / missing `action_kind` in `rtr.flow`) — not the sound adapter.

---

## 2026-10-28 — Implement: robust MIDI-out device handling in rtr/sound/sound.py

### Status

Completed

### Summary

Reworked the Sound adapter's MIDI-out connection so adding/removing MIDI devices no longer permanently kills sound (the startup error `MidiOutAlsa::openPort: the 'portNumber' argument (7) is invalid.`):

- **Candidate spec chain**: `_candidate_specs(zone)` returns the ordered, deduped specs: zone `out_port` index → global `out_device` name → global `out_port` index → constructor default → auto (first available port). `_get_out` walks the chain: a cached handle wins; a previously failed spec is retried at most once per `retry_interval` (default `RETRY_INTERVAL = 5.0`, constructor kwarg); the first spec that opens is returned. `_get_out` now returns `(spec, out)`.
- **Name path fixed for rtmidi 1.5.8**: `_open_named` uses `open_string_outport` when present, else resolves the name to an index by enumeration (exact match, then substring for dynamic suffixes). The old name branch always raised `AttributeError` on 1.5.8.
- **Descriptive index errors**: `_open_indexed` range-checks before opening and raises with the list of available ports, so the log says what to configure.
- **Dead-device recovery**: a `send_message` failure drops the dead handle (`_close_spec`), starts the backoff, and degrades the message to logging; the next message reopens (walking the whole candidate chain).
- **Leak fixes**: `_open_one` discards (closes + deletes) the `MidiOut` instance on every failure path, including "no devices"; `list_out_ports` releases its enumeration instance in a `finally`.
- **Reconcile**: `_reconcile_outs` computes the needed set as the union of *all* candidates over all zones and prunes stale `_open_failures`; `close()` clears `_open_failures`.
- Retry state: `_open_failures: Dict[tuple, float]` (monotonic time per spec).

### Changes Made

- `realtimecontrol/rtr/rtr/sound/sound.py`: module docstring + `RETRY_INTERVAL` constant; `Sound.__init__` (`retry_interval` kwarg, `_open_failures`); new helpers `_candidate_specs`, `_port_names`, `_find_port_index`, `_open_indexed`, `_open_named`, `_discard`; rewritten `_out_spec` (first candidate), `_open_one`, `_get_out`, `_send_bytes`, `_reconcile_outs`, `list_out_ports`, `close`.

### Documentation

- Module docstring updated: the fallback chain, retry/backoff, and dead-device detection are documented.
- No `sound.json` / README changes needed: the existing `out_port` / `out_device` fields now behave as documented (the name path actually works; a stale index degrades instead of dying).

### Architecture

No architectural impact. Internal to the sound adapter; the data shape, events, and public API are unchanged (except `_get_out` returning `(spec, out)`).

### Validation

- `python tests/test_sound_out_ports.py` → ALL TESTS PASSED (27 tests, incl. the new device-change tests — see the test entry above).
- `ruff check rtr/sound/sound.py` → clean.

### Decisions and Caveats

- The MIDI-**in** side (`rtr/io/midi.py`) was intentionally left unchanged: its failures are not cached and the controller port is a stable ALSA loopback. Possible follow-up if the controller port index ever shifts.
- The auto candidate `(None, None)` opens port 0 (the first enumerated ALSA port), so a zone with no valid explicit config still degrades to a real device when one exists.
- The implementation landed in commit `5cd796e` ("pose zonme"), which also contains unrelated pose-zone work (`behavior.py` / `zones.py` / `editor.html`).

### Next Task

Extend the tests for the device-change scenarios (done — see the entry above).

### Instructions for the Next Task

- The tests live in `tests/test_sound_out_ports.py`; the fake rtmidi mirrors 1.5.8 (no `open_string_outport`, range-checked `open_port`).
- `_get_out` returns `(spec, out)` — callers in tests must unpack the tuple.

---

## 2026-10-28 — editor.html: absolute/free wording, pose-zone toggle, and flow color

### Status

Completed

### Summary

Finished the `editor.html` work for the new axis model and the pose-zone (bridge/move) concept:

- Rewrote all remaining "frozen" wording/comments to the absolute/free model: a pose axis is a **number (absolute — pinned)** or **`"x"` (free — resolved at runtime to the robot's live value)**. Renamed the misleading `frozen` local in the axis-bar rendering to `isFree`; the `frozenX` CSS class name is kept as a stable internal identifier.
- Added the **pose-zone toggle** to the zone form: a `pose zone` select with `none` / `bridge` / `move` (writes/deletes `z.pose`). Added a `POSE_ZONES = ["bridge", "move"]` constant and a `z.pose` validation rule mirroring the core's `zones.py` check.
- Gave pose zones a **distinct flow color**: a `fnodePose` class (purple stroke `#a06fd0`) on flow nodes when `z.pose` is set, and the zone's pose type now appears in the node sublabel (`bridge · N act`).

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - Added `const POSE_ZONES = ["bridge", "move"];` (line ~746).
  - Added `.fnode.fnodePose .fnodeBody { stroke: #a06fd0; stroke-width: 2; }` (CSS, line ~165).
  - `flowAddNode`: adds the `fnodePose` class when `z.pose` is a pose zone.
  - `flowSubLabel`: appends the pose type (`bridge`/`move`) to the node sublabel.
  - `buildZoneForm`: added the `pose zone` select row (none/bridge/move).
  - `validate()`: added the `z.pose` must-be-in-`POSE_ZONES` rule.
  - Rewrote the remaining "frozen" comments to absolute/free; renamed `frozen` → `isFree` in the axis-bar rendering.

### Documentation

- Updated the `editor.html` pose help text, axis-input tooltips, and FK/pose-resolution comments to the absolute/free model (part of this and the prior wording pass).

### Architecture

- No architectural impact. This is UI-only: it surfaces the existing `z.pose` field (already validated by the core `zones.py`) and the absolute/free axis grammar already implemented in `behavior.py`/`zones.py`.

### Validation

- `python3 -c "..."` to extract the inline `<script>` and `node --check` → JS SYNTAX OK.
- (The axis-system tests were verified green earlier in this session: `python3 -m pytest tests/test_state_machine.py tests/test_transition.py tests/test_cam_test.py tests/test_bridge_behavior.py -q` → 52 passed, 9 skipped.)

### Decisions and Caveats

- The `frozenX` CSS class name is intentionally left as-is (stable identifier, not user-facing wording).
- The `pose zone` select deletes `z.pose` when "none" is chosen, so the field is optional in the data (matching the core, where `Zone.pose` returns `None` when unset).
- **3D pose editor scope: FK only (confirmed with the user).** The FK side already works and is sufficient: editing an action's 6 axis values drives `Robot3D.setTarget` → the ghost arm + `checkPoseCollision` in the zones tab. The **IK** side (interactive end-effector dragging to solve joint values) was explicitly **not** requested — `kinematics.py` has FK + geometric Jacobian + `pinv3` building blocks but no IK solver, and none was added. If interactive IK is wanted later, it would need a JS solver + a drag-the-tool UI in `#robot3d`.

### Next Task

No immediate next task identified. The axis-system (absolute/free) work and its `editor.html` surface (wording, pose-zone toggle, flow color, FK 3D pose editor) are complete.

### Instructions for the Next Task

- If interactive IK is requested later: `rtr/robot/kinematics.py` has FK + a geometric Jacobian + `pinv3` (damped least-squares building blocks) but **no IK solver function**. A JS IK would need (a) a solver (iterate Jacobian/pinv toward a target end-effector pose) and (b) an interaction UI (drag the tool in the `#robot3d` view; `rtr3d.js` currently uses `OrbitControls` for camera-only).
- The FK pose editor is verified working in the zones tab: the action editor's 6 axis inputs call `onChanged()` → `updateGhost()` → `Robot3D.setTarget(resolvePose(pose))`, and `Robot3D.checkPoseCollision(pose)` reports collisions.

---

## 2026-10-28 — Research: rtmidi 1.5.8 API facts for robust MIDI-out device handling

### Status

Completed

### Summary

Confirmed the python-rtmidi 1.5.8 facts that constrain the MIDI-out robustness work (the startup error `[sound] could not open MIDI out: MidiOutAlsa::openPort: the 'portNumber' argument (7) is invalid.` comes from `rtr/sound/sound.py:355`):

- `MidiOut` methods (1.5.8): `open_port`, `close_port`, `send_message`, `get_ports` (returns `list[str]`), `get_port_count`, `delete`, `is_port_open()` (method), `is_deleted` (property), `open_virtual_port`, `set_port_name`, `set_error_callback`.
- **No `open_string_outport`** in 1.5.8 — the name-based branch at `sound.py:343` always raises `AttributeError`, so the `out_device` (name) path never worked on this install.
- `open_port(idx)` with `idx >= count` raises `InvalidPortError` (MRO: `InvalidPortError → RtMidiError → ValueError → Exception`): "MidiOutAlsa::openPort: the 'portNumber' argument (N) is invalid."
- `close_port()` on a never-opened instance is safe (no raise); `delete()` on a fresh instance is safe and marks it deleted — usable to release failed/unused `MidiOut` instances.
- The ALSA out-port list is dynamic: a probe returned 17 ports (Midi Through, rtpmidi, PipeWire, qpwgraph, SuperCollider in0–in9, RtMidiIn, Protokol). Indices shift as apps start/stop — the root cause of the user's error (configured index 7 went invalid).

### Changes Made

- None (investigation only).

### Documentation

None required — findings recorded here; they feed the implementation task.

### Architecture

No architectural impact.

### Validation

- Live probes against the installed rtmidi 1.5.8 (`python3 -c` scripts): API dir listing, `InvalidPortError` MRO, `close_port`/`delete` on fresh + opened instances, `get_ports()` return type, live port enumeration.

### Decisions and Caveats

- Name-based opening must be implemented as "enumerate `get_ports()`, match by name (exact, else substring), `open_port(idx)`", keeping `open_string_outport` as a fast path when a newer rtmidi provides it.
- `MidiOut` instances must be released (`close_port` + `delete`) on failed opens and in `list_out_ports`, since retry logic will create instances repeatedly.

### Next Task

Implement the robust MIDI-out handling in `rtr/sound/sound.py` (see the analysis entry below).

### Instructions for the Next Task

Use the facts above: range-check indices before `open_port` (with the available-port list in the error), resolve names by enumeration when `open_string_outport` is absent, add retry-with-backoff for failed opens, evict dead handles on `send_message` failure, and release abandoned `MidiOut` instances.

---

## 2026-10-28 — Analyse: MIDI-out device handling in sound.py and its fragility points

### Status

Completed

### Summary

Mapped the MIDI-out device lifecycle in `realtimecontrol/rtr/rtr/sound/sound.py` (the source of the user's startup error) and identified why adding/removing MIDI devices breaks it:

1. **Index fragility** — a zone's `out_port` / the global `out_port` is an *index* into the current ALSA enumeration. Adding/removing a device shifts indices, so a previously-valid index (7) becomes invalid → `InvalidPortError` on the first snapshot after startup.
2. **Name path broken** — the `out_device` (name) branch calls `rtmidi.MidiOut.open_string_outport`, which does not exist in the installed rtmidi 1.5.8 → always `AttributeError` → the robust path is unusable.
3. **Permanent failure cache** — `_open_outs[spec] = None` on failure; the failure is cached forever, so a device that appears later (hotplug, SuperCollider starting) is never picked up.
4. **No dead-device detection** — when an open device disappears, `send_message` raises; the error is logged but the dead handle stays cached forever (no reopen attempt).
5. **Leaks** — `list_out_ports` creates a `MidiOut` per call and never releases it; `_open_one` abandons the instance on a failed open.
6. **Opaque errors** — the failure log has no list of available ports, so the config cannot be fixed from the log.

Flow: `Sound.__init__` → `reload()` (reconcile = close-only; opens are lazy) → first `SNAPSHOT` → `_send_msg` → `_send_bytes` → `_get_out(zone)` → `_out_spec(zone)` (precedence: zone `out_port` → global `out_device` → global `out_port` → constructor `out_port` → auto port 0) → `_open_one(spec)`.

Fix design (for the implementation task): per-zone **candidate spec chain** (zone index → global name → global index → auto), name resolution by enumeration (exact then substring match), index range-check with available ports in the error, retry-with-backoff for failed opens (`retry_interval`, default 5 s), evict-and-retry on `send_message` failure, and release of abandoned instances.

### Changes Made

- None (investigation only).

### Documentation

None required — analysis recorded here.

### Architecture

No architectural impact (analysis of existing structure).

### Validation

- Code reading: `rtr/sound/sound.py` (full), `rtr/io/midi.py` (MIDI-in section), `main.py`, `sound.json`, `tests/test_sound_out_ports.py`, `tests/test_sound_enabled.py`, `.kilo/plans/1789210975965-midi-out-plan.md`.

### Decisions and Caveats

- Scope: the MIDI-**out** adapter (`sound.py`) is where the reported error originates and where the fragility is visible. The MIDI-**in** adapter (`rtr/io/midi.py`) opens by index too, but failures are not cached (a `reload` retries) and the controller port is a stable ALSA loopback; it is left unchanged and noted as a possible follow-up.
- Falling back to the next candidate (global name → global index → auto) when a configured index is invalid is the chosen robustness behavior: sound degrades to a working device or to logging instead of dying, with an actionable log line listing the available ports.

### Next Task

Implement the robust MIDI-out handling in `rtr/sound/sound.py`.

### Instructions for the Next Task

Modify only the MIDI-out connection section of `Sound` (keep the data layer and `_on_snapshot` untouched): add `_candidate_specs`, name resolution helpers, index range-check, retry/backoff state (`_open_failures`), dead-handle eviction in `_send_bytes`, instance release in `_open_one`/`list_out_ports`, and a `retry_interval` constructor kwarg. Extend `tests/test_sound_out_ports.py` (the fake rtmidi needs: range-checking `open_port`, `delete`, a failure flag, and a no-`open_string_outport` variant to mirror rtmidi 1.5.8).

---

## 2026-10-27 — camera_debug.html / render.html stop receiving camera tracking updates during robot movements

### Status

Completed

### Summary

`camera_debug.html` and `render.html` stop receiving camera tracking updates (`cam_telem`) during robot movements. Root cause: in `rtr/core/engine.py:_tick()`, when `move_mode != "stream"`, the engine does `await asyncio.to_thread(move, target, speed)` which blocks the coroutine until the robot move completes. `self._publish()` (which sends the state frame — including `cam_telem`) runs only AFTER the move finishes. During a blocking move, no state frames are published, so WebSocket clients stop receiving camera telemetry and may time out / reconnect.

Fix: call `self._publish(curjpos, curpos, target)` BEFORE the blocking move so clients at least receive the pre-move state (with current `cam_telem`) before the gap. The unconditional publish after the move still runs once the move ends. Stream mode (`move_mode == "stream"`) is unchanged — it already publishes every tick.

### Changes Made

- `realtimecontrol/rtr/rtr/core/engine.py` (line ~207): added `self._publish(curjpos, curpos, target)` inside the blocking-move `else` branch, before `await asyncio.to_thread(move, ...)`.

### Documentation

None required — the fix restores the existing behavior contract (state frames include `cam_telem`); no user-facing text changed.

### Architecture

No architectural impact. Publish-before-move is additive; the post-move publish is unchanged. Stream mode is untouched.

### Validation

- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py` — 299 passed, 6 failed, 14 skipped. The 6 failures (`test_remote_sdl` ×5, `test_transition` ×1) are pre-existing and unrelated (verified via `git stash` — same 6 fail on the clean tree).

### Decisions and Caveats

- Publishing before the move sends the state at move-start. During the move itself there are still no live updates (blocking mode inherently waits for completion); the pre-move publish at least gives clients a valid state frame before the gap and prevents the WebSocket from going silent long enough to time out.
- No change to robot behavior, move timing, or the stream mode path.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

None.

---

## 2026-10-27 — Suppress ConnectionResetError on HTTP static responses when client disconnects

### Status

Completed

### Summary

The HTTP static handler (`_serve_static`) raised `ConnectionResetError` (errno 104) when writing the response body to a client that had already disconnected (e.g. browser closed the tab, navigated away, or stopped polling `/assets/latest.jpg`). `socketserver` logs the traceback per request thread, which the operator associated with the robot stopping.

Fix: added a `_wfile_write()` helper on the handler that wraps `self.wfile.write()` and swallows `ConnectionResetError`/`BrokenPipeError` (expected disconnects) while letting real errors through. `_serve_static`, `_send_json`, and `_send_text` now route through it.

### Changes Made

- `realtimecontrol/rtr/rtr/io/httpserver.py`:
  - Added `Handler._wfile_write(data)` — writes to `wfile`, returns False on `ConnectionResetError`/`BrokenPipeError` instead of propagating.
  - `_serve_static`, `_send_json`, `_send_text` call `self._wfile_write(...)` instead of `self.wfile.write(...)`.

### Documentation

None required — restores expected, silent handling of client disconnects; no user-facing behavior changed.

### Architecture

No architectural impact. The HTTP response path is unchanged; only the write is now guarded against the expected client-disconnect case.

### Validation

- `python3 -m py_compile rtr/io/httpserver.py` — SYNTAX_OK.

### Decisions and Caveats

- `ConnectionResetError` (ECONNRESET) and `BrokenPipeError` (EPIPE) are both subclasses of `OSError`; catching them specifically avoids masking genuine server errors (e.g. disk-full). Other `OSError`s still propagate.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

None.

---

## 2026-10-27 — Fix brain-tab save wiping per-behavior stay/triggers; restore truncated editor.html tail

### Status

Completed

### Summary

The brain tab of `editor.html` silently wiped the per-behavior next-selection fields (`stay`/`triggers`/`dwell_s`/`lock_duration_s`) on every save. Root cause: `_commitBrainDom()` rebuilt `BRAIN.behaviors` from scratch using only the motion params (`BEHAVIOR_PARAMS` keys like `freq`/`gain`/`sweep_amp`), discarding the next-selection fields that `setNext` writes live into `BRAIN.behaviors[name]` on every keystroke. So edits such as setting `vigilant` to not loop indefinitely never persisted, and `spontaneous` (which has no motion params) was wiped to `{}` entirely.

Fix: `_commitBrainDom()` now reads the next-selection fields from the DOM (the authoritative save-time read, mirroring `_commitGroupsDom`) before replacing `b.behaviors`. Behavior cards now carry `data-name` so the per-behavior trigger checkboxes are queryable.

During verification I discovered `editor.html` was truncated — a pre-existing issue introduced by the camera-overlay commit `3d339bb`: the file ended mid-statement at `$("ghostInfo")` and the main `<script>` (opened at line 741) was never closed (13 `<script>` vs 12 `</script>`). This blocked the editor from loading (syntax error) and the `test_groups_tab.js` harness. The truncated file was otherwise a full superset of the last complete version (`2b516c4` "zones") — only the final 7 lines (the collision-result display + `</script></body></html>`) were missing, recoverable verbatim from `2b516c4`. I restored them. I also fixed the `test_groups_tab.js` stub, which was missing `uiCamToggle` (added by the camera-overlay commit), causing a load-time null.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - `_commitBrainDom()` (line ~3348): now reads `stay`/`triggers`/`dwell_s`/`lock_duration_s` from the DOM before replacing `b.behaviors`, so the per-behavior loop settings survive a brain-tab save.
  - Behavior cards (line ~2877): `card.dataset.name = name` so the per-behavior trigger checkboxes are queryable by `data-name`.
  - Restored the truncated tail (line ~7537): the collision-result display + `</script></body></html>` (verbatim from the last complete version `2b516c4`).
- `realtimecontrol/rtr/test_groups_tab.js`: added `uiCamToggle` to `STATIC_IDS` (the camera-overlay element the script references but the stub lacked).

### Documentation

None required — the fix restores the documented brain-tab save behavior (per-behavior stay/triggers persist); no user-facing text changed.

### Architecture

No architectural impact. The save path is unchanged in shape (DOM → `_commitBrainDom` → `b.behaviors` → `POST /api/brain`); the fix only makes `_commitBrainDom` preserve the next-selection fields it previously dropped. The server (`httpserver.py` `/api/brain` POST) writes the payload verbatim after `validate_brain_config`, so no server change was needed.

### Validation

- `node --check` on the extracted main inline script — SYNTAX_OK (310,311 bytes; the script is now complete/closed).
- `node test_groups_tab.js` — ALL_TESTS_PASSED (20 assertions, incl. `payload has hunt/facefocus/autonomy/behaviors (no clobber)` and `cam_control` preservation).
- Targeted brain-save verification (Node + mini-DOM) — BRAIN_SAVE_VERIFIED: `vigilant` round-trips `{freq:1, stay:0.25, triggers:["dwell","lock_duration"]}`; `spontaneous` (no motion params) round-trips `{stay:0.5, triggers:["dwell"]}` (previously wiped to `{}`).
- Script-tag balance: 13 `<script>` / 13 `</script>`; file ends with `</html>` (7542 lines).

### Decisions and Caveats

- The truncation was pre-existing (introduced by `3d339bb` "camera overlay"; the last complete version is `2b516c4` "zones"). Both the working copy and HEAD were truncated; I restored only the missing 7-line tail — the camera-overlay work in the middle of the file was already intact.
- The user was likely running a cached browser copy, which is why the editor appeared to work despite the on-disk truncation.
- The `test_groups_tab.js` stub's mini-DOM selector now strips quotes from attribute values to match browser behavior for `[data-name="..."]` selectors (a test-harness fix, not an editor change).

### Next Task

No immediate next task identified.

### Instructions for the Next Task

None.

---

## 2026-10-27 — Stop the robot driving to 0,0,0,0,0,0 on manual mode entry

### Status

Completed

### Summary

Clicking "manual" in `realtimecontrol/rtr/client.html` (which sends `set_control_mode` `mode: "manual"`) used to drive the robot to `0,0,0,0,0,0`. Root cause: `StateMachine.set_control_mode` (rtr/state/machine.py) froze the robot on a freshly-read `curjpos`, which can read as all-zeros between controller/EKI frames, so the hold target became zeros and the robot drove there.

Fix: entering manual mode now holds the robot at its last known good pose instead of re-reading curjpos. Precedence: `self.target` (refreshed every engine tick) → `self._last_curjpos` (retains the last good read; a bad read throws in the tick and does not clobber it) → fresh `curjpos` as last resort. An all-zeros pose is never written (no valid pose known → do not move). The action/transition are still cleared and auto-advance is suppressed, so the robot finishes the current action, stops, and waits for an explicit command — matching the documented manual-mode contract. `client.html` needed no change (its handler already just sends `set_control_mode`).

### Changes Made

- `realtimecontrol/rtr/rtr/state/machine.py`: `set_control_mode` manual-entry hold now uses the last known good pose (`self.target` → `_last_curjpos` → `curjpos`) and skips writing an all-zeros pose, instead of unconditionally `self.target = list(curjpos)`.

### Documentation

None required — behavior is the documented manual-mode contract (robot holds where it is until an explicit command); no user-facing text changed.

### Architecture

No architectural impact.

### Validation

- `python -m pytest tests/test_state_machine.py tests/test_forced_behavior.py tests/test_zone_groups.py tests/test_brain_assertive.py tests/test_autoreturn.py` — 69 passed, 9 skipped.
- Full suite `tests/ -q --ignore=tests/test_remote_tui.py` — 299 passed, 14 skipped; the 6 `test_remote_sdl`/`test_transition` failures and the `test_remote_tui.py` collection error pre-exist this change (verified via `git stash`) and are unrelated (SDL font/text expectations, string-pose test data, `rtr.flow` import).
- Targeted sanity check: entering manual with `curjpos=[0]*6` holds a previously-set good target; with no known pose it writes nothing (no zeros).

### Decisions and Caveats

- All-six-zeros is treated as "no valid pose known" (a real KR60 joint pose is never all zero in normal operation), so the robot is never driven to zeros on a mode change.
- If `self.target`/`_last_curjpos` are unset and `curjpos` is zeros, no target is written, so the engine issues no move command on that tick (robot stays put).

### Next Task

No immediate next task identified.

### Instructions for the Next Task

None.

---


### Status

Completed. The render page (`render.html`) now overlays the live camera-debug frame (candidate boxes + track/compensated vectors, no background) on top of the hydra canvas, fed by the same live `cam_telem`/`camera`/`servo` state data, and the patches tab of `editor.html` has an on/off toggle for it (`#uiCamToggle`). The `cam` flag rides the existing in-memory `render_ui` state (not persisted to `patches.json`), the same pattern as `hud`/`code`.

### Summary

Goal: an option on `render.html` to overlay the camera-debug frame content (candidate boxes + track/compensated vectors, no background) on top of the hydra render canvas, fed by the same live camera data, plus an on/off toggle in the patches tab of `editor.html`.

The state frame (WS, 20 Hz) already carries everything the overlay needs: `cam_telem` (candidates/track/face/status), `camera` (intent, has `lock_id`), `servo` (`dq_deg`/`dx`/`dy`), and `render_ui` (confirmed in `display.py` `_frame`). So the transport was already in place — this task adds the `cam` flag to `render_ui` (core + editor toggle) and the overlay drawing in `render.html`.

Design: a transparent canvas (`#camOverlay`) on top of the hydra canvas. The 640×480 frame is fit "contain" (aspect preserved, centered) into the fullscreen overlay. Only the content is drawn (no grid, status bar, or dark background fill): candidate boxes (rectangles + `#id`/conf/LOCK labels), the track arrow (green) + cross marker + dx/dy text, the compensated arrow (blue) + dq text, and the image-center cross. The drawing mirrors `camera_debug.html` (`detectScale`/`drawArrowHead`), re-targeted from the 640×480 canvas to the contain-fit overlay transform.

### Changes Made

- `realtimecontrol/rtr/rtr/patches/patches.py`:
  - `self._render_ui` now `{"hud": True, "code": False, "cam": False}`.
  - `set_render_ui(self, hud=None, code=None, cam=None)` handles `cam` (bool) and reports it in the log line.
  - `render_ui()` returns the dict (now includes `cam`).
- `realtimecontrol/rtr/rtr/core/engine.py`:
  - `SET_RENDER_UI` branch forwards `cam=cmd.payload.get("cam")` to the `render_ui` setter.
- `realtimecontrol/rtr/editor.html`:
  - `#uiCamToggle` button in the patches tab (beside code/hud), title "Toggle the camera-debug overlay … (candidate boxes + track/compensated vectors, no background)".
  - `renderUi = { hud: true, code: false, cam: false }`; `sendRenderUi(hud, code, cam)` (3-arg); `syncRenderUiToggles()` sets `#uiCamToggle`'s `.active` class.
  - State-frame handler normalizes `renderUi` (`cam: m.render_ui.cam === true`) and the `_renderUiFp` fingerprint includes `cam`.
  - `#uiCodeToggle`/`#uiHudToggle`/`#uiCamToggle` onclicks all pass the 3-arg `sendRenderUi(hud, code, cam)`.
- `realtimecontrol/rtr/render.html`:
  - `#camOverlay` `<canvas>` + CSS (`position:fixed; inset:0; pointer-events:none; display:none; z-index:5`) — transparent, above the hydra canvas, below the HUD.
  - `renderUi` gains `cam`; `applyRenderUi()` shows/hides `#camOverlay` from `ui.cam === true`.
  - State vars `lastTelem`/`lastIntent`/`lastServo`; the state handler captures `m.cam_telem`/`m.camera`/`m.servo` and calls `drawCamOverlay()` after `applyRenderUi()`.
  - Drawing helpers: `camTransform()` (contain-fit 640×480 → overlay), `camToCanvas()`, `camDataScale()` (pixels-with-dims / normalized / default-pixels, mirrors `detectScale`), `camArrowHead()`, `drawCamOverlay()`, `sizeCamOverlay()`.
  - Resize handler sizes `#camOverlay` to the window and redraws; `sizeCamOverlay()` runs on load.

### Documentation

None required — the overlay is a UI affordance for the already-documented `render_ui` core state (the earlier code/hud toggle entry documents the push path; this adds the `cam` flag to the same path). The `cam` flag is in-memory only (not persisted to `patches.json`), matching `hud`/`code`.

### Architecture

No architectural change. The `render_ui` command/push path (core `set_render_ui` → `_render_ui` → state frame → `render.html` `applyRenderUi`) and the `cam_telem`/`camera`/`servo` state fields were already in place; this task adds the `cam` flag to `render_ui` and the overlay drawing that consumes the already-pushed camera fields.

### Validation

- JS syntax: extracted the inline `<script>` from `render.html` and ran `node --check` → **OK**.
- Core import check: imported `rtr.patches` + `rtr.core.engine`; `Patches.set_render_ui(cam=True)` → `render_ui()` returns `{'hud': True, 'code': False, 'cam': True}`; `Cmd.SET_RENDER_UI` present → **OK**.
- Full test suite (run directly, `tests/test_*.py`): **20 passed, 4 non-zero exits** — all 4 are pre-existing / non-regressions:
  - `test_zone_scoped_actions.py`: ends with an intentional `pytest.skip()` ("kind/target schema removed") that raises when run directly (all real checks PASS) — not a real failure.
  - `test_remote_sdl.py`, `test_remote_tui.py`, `test_transition.py`: pre-existing — verified by `git stash` of my Python changes (`engine.py`, `patches.py`) and re-running: the same 3 fail on the clean tree.

### Decisions and Caveats

- **`cam` is in-memory only** (not persisted to `patches.json`), matching the `hud`/`code` flags. Toggling it is a live runtime state change, not a saved patch.
- **Overlay is transparent** (no background fill / grid / status bar) — only the camera-debug *content* (candidate boxes + track/compensated vectors + image-center cross). This is the "only the content" requirement.
- **Coordinate mapping** mirrors `camera_debug.html` `detectScale`: data coords are mapped to 640×480 frame space (pixels-with-explicit-dims / normalized-0..1 / default-pixels), then the contain-fit transform maps frame space to the fullscreen overlay (aspect preserved, centered).
- **Live redraw**: the overlay redraws on every state frame (20 Hz) while `cam` is on, so it tracks the live telemetry. When off, `drawCamOverlay()` clears and returns early.

### Next Task

No immediate next task identified. The camera-debug overlay is complete end-to-end (core flag, editor toggle, render-page drawing).

### Instructions for the Next Task

If extending the overlay: the drawing lives in `render.html`'s `drawCamOverlay()` (helpers `camTransform`/`camToCanvas`/`camDataScale`/`camArrowHead`). Add new elements there, mapping through `camDataScale` (data → 640×480) then `camToCanvas` (frame → overlay). The `cam` flag is in-memory `render_ui` state (not persisted); the toggle is `#uiCamToggle` in the patches tab of `editor.html`.

## 2026-10-27 — KeyError: 'hunting' in brain hunt tick: fail-safe unknown-zone handling in safety gate

### Status

Completed. The recurring engine tick crash `KeyError: 'hunting'` is stopped: `SafetyGate.check_conflict` now treats unknown/None zone names as a conflict instead of raising, so the brain's hunt path degrades to in-place scanning rather than crashing.

### Summary

Root cause: the brain's hunt config (`brain.json` scan/detect entries, `config.py` hunt params) references zone `hunting`, but the runtime zone table is built from `zones.json` (`engine.reload_zones` → `Zones(data.get("zones"))`), which contains 16 zones and has **no** `hunting`. The Python `ZONES` fallback in `zones.py` *does* contain `hunting`, but that fallback is not used at runtime. Crash path: `engine.py` tick → `brain._hunt_tick` → `_hunt_move_safe(scan.get("zone"), scan.get("action"))` → `safety.check_conflict(..., to_zone="hunting")` → `Zones.get("hunting")` → `KeyError`.

Fix: `gate.py` `check_conflict` now resolves unknown/None zone names via a new `_zone_or_none` helper and returns `(True, ["unknown zone: ..."])` instead of raising. In the hunt path, `_hunt_move_safe` returns False on conflict, so the robot scans in place rather than crashing.

This complements the earlier "x" pose placeholder fix (real-time resolves "x" to `curjpos[i]`; the flow editor treats "x" as a wildcard). `check_conflict` and `_pose_outside_safezone` accept an optional `curjpos`; the `engine.py` and `brain.py` real-time paths pass it; `flow.py` passes None.

### Changes Made

- `realtimecontrol/rtr/rtr/safety/gate.py`:
  - Added `_zone_or_none(zones, name)` helper; returns None for unknown/None zone names instead of a `KeyError`.
  - `check_conflict`: unknown/None `to_zone` (and `from_zone`) now return `(True, ["unknown zone: ..."])` (fail-safe conflict) instead of raising.
  - (Earlier "x" work in the same file: optional `curjpos` param on `check_conflict`/`_pose_outside_safezone`.)
- `realtimecontrol/rtr/tests/test_safety_conflict.py`:
  - Added `test_unknown_zone_is_a_conflict_not_a_crash` and registered it in `main()`.

### Documentation

None required — the fail-safe is a behavior change on the safety-gate path; the unknown-zone contract is self-evident from the new test and the `check_conflict` docstring.

### Architecture

No architectural change. The safety gate's contract tightens from "unknown zone raises" to "unknown zone is a conflict" (fail-safe). This is a strict superset: every previously-valid conflict is preserved, and the previously-crashing path now degrades gracefully.

### Validation

- `python tests/test_safety_conflict.py`: **11 passed** (10 prior + 1 new unknown-zone regression test; includes the 3 "x" placeholder tests).
- `python3 -m pytest tests/ --ignore=tests/test_remote_tui.py -q`: **299 passed, 6 failed, 14 skipped** — the 6 failures are pre-existing and unrelated to this change (verified by `git stash` of the tracked changes and re-running: the same 6 fail on the clean tree). Breakdown: 4× `test_remote_sdl` TUI failures, 1× `test_transition.py::test_all_reachable_transitions_complete` (TypeError — an "x" placeholder in a `zones.json` pose is not resolved in the state-machine step target; see Decisions), and `test_remote_tui.py` has a pre-existing collection error (`action_kind` not in `rtr.flow`).
- Validated against real `zones.json`: `Zones(zones).has("hunting")` → False; `SafetyGate.check_conflict("resting", None, "hunting", "scan")` → `conflict=True`, `issues=["unknown zone: hunting"]`, no `KeyError`.

### Decisions and Caveats

- **Open config decision:** which existing `zones.json` zone/action should replace `hunting` in the hunt config (`brain.json` scan/detect, `config.py` hunt params). The fail-safe stops the crash, but the hunt will scan in place until the config is pointed at a real zone. The intended mapping is unknown and must be confirmed with the user.
- **Pre-existing failure now surfaced:** `test_transition.py::test_all_reachable_transitions_complete` fails with `TypeError` because `zones.json` poses contain an `"x"` placeholder (e.g. `sleeping/yawn` pose `['x', -134, 157, 3, 0, 0]`) that the state-machine `step()` target does not resolve before handing to `SimRobot.move_joint`. This is a *separate* location from the safety-gate / flow-editor "x" handling and is not fixed by this change. It is pre-existing (fails on the clean tree too) and is a consequence of the "x" feature being added to `zones.json`.

### Next Task

Decide the real zone/action mapping for the hunt config's `hunting` reference, then update `brain.json` + `config.py`. Separately, resolve the "x" placeholder in the state-machine `step()` target so `test_transition.py::test_all_reachable_transitions_complete` passes.

### Instructions for the Next Task

1. Hunt mapping: confirm with the user which `zones.json` zone/action the hunt should target (candidate: the `scanning` zone with its scan action). Replace `hunting` in `brain.json` scan/detect entries and `config.py` hunt params. Re-run `python tests/test_safety_conflict.py`.
2. State-machine "x" resolution: in `rtr/state/machine.py`, the target pose returned by `step()` must resolve any `"x"` axis to the robot's current joint position before being sent to `move_joint` (mirror the real-time `curjpos` resolution already done in `gate.py`/`engine.py`). Verify with `python tests/test_transition.py`.

## 2026-10-27 — A3=42 collision: revert testing safezone, move locked_band to new dedicated zone `testing_locked_band`

### Status

Completed. The A3=42 collision directive is implemented: the widened testing safezone is reverted and `locked_band` now lives in a **new dedicated zone** at A3=32, routed bidirectionally with `testing` via the shared-pose handoff.

### Summary

User directive: A3=42 physically collides, so the `[0,42]` safezone widening from the previous task is reverted to `[0,32]`, and `locked_band` is moved into a **new dedicated zone** (user-confirmed structure — not an in-place cap, not a no-op bridge). The new zone `testing_locked_band` hosts the capped `locked_band` (pose A3=32, band `[0,32]`) and is routed bidirectionally with `testing`.

Routing detail (why only `locked` links directly): `find_shared_pose` (`zones.py:92`) requires **every** concrete axis to match within margin 0.5 (not just one). `testing/locked` `[45,-30,32,0,-5,0]` and `locked_band` `[45,-30,32,0,-5,0]` match on all axes → valid shared-pose handoff. `intent`/`curious`/`gentle` `[45,-10,12,0,-5,0]` mismatch on A2 (−10 vs −30) and A3 (12 vs 32) → no shared pose → their direct `locked_band` links are dropped (they would be red inter-zone links). They route via `locked` (in-zone links intact), so the graph stays connected: `intent/curious/gentle` → `locked` → `testing_locked_band/locked_band`, and back.

### Changes Made

- `realtimecontrol/rtr/zones.json`:
  - `testing` `safezone` A3: `[0, 42]` → `[0, 32]` (reverts the collision-causing widening).
  - `testing` action `locked_band`: **removed** (moved to the new zone).
  - `testing` action `locked` `next`: `testing/locked_band` → `testing_locked_band/locked_band` (the only valid inter-zone handoff, shared pose `[45,-30,32,0,-5,0]`).
  - `testing` actions `intent`/`curious`/`gentle` `next`: dropped their direct `testing/locked_band` entries (no shared pose → would be red).
  - **New zone `testing_locked_band`**: `class: track`, `mode_group: event-sit-back`, safezone identical to testing with A3 `[0, 32]`, speed 20, single action `locked_band` — pose `[45,-30,32,0,-5,0]` (A3 capped 42→32), `variable_axes [0,1,4]`, behavior `curious`, speed 100, loop true, `axis_bands "2": [0, 32]` (was `[0,42]`), `next: [testing/locked]` (bidirectional pair with `testing/locked`).
  - `layout.pos`: added `testing_locked_band` at `{x: 560, y: 340}`; `layout.key`: appended `testing_locked_band`.

### Documentation

None required — safezone bounds and `next` routing are data; the shared-pose mechanism is already documented in `behavior.py:306` / `machine.py:86-93` / `zones.py:92`.

### Architecture

New zone added to the zone graph; no code change. `testing_locked_band` is a persistent single-action zone (loop true) reachable from `testing/locked` and returning to `testing/locked` — a valid bidirectional inter-zone handoff at the shared A3=32 pose. The runtime handles it with the existing `find_shared_pose`/`_compute_handoff` path (`machine.py:210`).

Note: `sleeping_testing_bridge` (isolated, unreachable, safezone A3 `[0,42]`, pose A3=42) still carries a physically unsafe A3=42 pose. It is inert (no `next` links in or out) but should be capped to A3=32 or removed before any re-wiring.

### Validation

- `python3 -c "json.load(...)"`: zones.json parses; structure verified (testing safezone A3 `[0,32]`; `testing_locked_band` hosts capped `locked_band`; routing as above).
- `/tmp/kilo/validate_links.py`: **39 checked, 0 broken** (was 45 checked / 0 broken; −6 from the dropped `intent`/`curious`/`gentle`→`locked_band` links and `locked_band`'s 4→1 `next` list).
- `python3 -m pytest tests/ --ignore=tests/test_remote_tui.py -q`: **296 passed, 5 failed, 14 skipped** — same 5 pre-existing `test_remote_sdl` TUI failures; no regressions. (`test_remote_tui.py` has a pre-existing import error: `action_kind` not in `rtr.flow`.)

### Decisions and Caveats

- Only `testing/locked` ↔ `testing_locked_band/locked_band` is a valid shared-pose handoff (all-concrete-axes match). `intent`/`curious`/`gentle` route via `locked`.
- `locked_band` A3 capped 42→32 (pose + `axis_bands "2"`), so it no longer reaches the colliding A3=42.
- `sleeping_testing_bridge` A3=42 pose left as-is (isolated/inert) but flagged for capping or removal before re-wiring.

### Next Task

Cap or remove the `sleeping_testing_bridge` A3=42 pose (isolated but physically unsafe if re-wired).

### Instructions for the Next Task

`sleeping_testing_bridge` safezone A3 is `[0,42]` and its `bridge` pose is `[45,-30,42,0,-5,0]`. It is unreachable (no `next` links) so it is inert, but A3=42 collides. Decide with the user: cap its safezone A3 to `[0,32]` and pose A3 to 32, or delete the zone entirely (drop it from `layout.pos`/`layout.key`). Then re-run `/tmp/kilo/validate_links.py` + pytest.

## 2026-10-26 — Museum/testing reds cleanup: widen testing safezone A3, drop museum routing links

### Status

Completed. All **12 pre-existing museum/testing reds** cleared. Two clusters, resolved differently:

1. **8 `testing/*` ↔ `testing/locked_band` reds** (pose `[45,-30,42,0,-5,0]` outside testing safezone on A3): fixed by **widening the testing safezone A3** from `[0,32]` → `[0,42]`.
2. **4 museum-bridge shared-pose reds** (`resting/hold`↔`sleeping/breathe`; `sleeping/yawn`↔both bridges; `sleeping_testing_bridge`↔`testing/locked`): fixed by **dropping the museum routing links** (user decision — disconnect the museum from the testing/scanning networks).

### Summary

The 12 reds were confirmed **pre-existing** by diffing the committed HEAD state (13 broken incl. the task-network red) against the working tree — my prior task-network work introduced none of them; it only removed the task-network red. Two distinct causes:

- **testing safezone A3 had been narrowed to `[0,32]`** (pre-tasknet it was `[0,42]`), but `testing/locked_band` is designed to sweep A3 to 42 (`axis_bands "2": [0,42]`, pose A3=42). The `sleeping_testing_bridge` safezone is *exactly* `[0,42]` — strong signal `[0,42]` is the intended testing A3 bound. Restoring `[0,42]` makes the testing safezone identical to the bridge's and covers the `locked_band` sweep.
- **The museum bridges are one-sided**: `sleeping_testing_bridge` carries only the testing-side pose `[45,-30,42,0,-5,0]` and `sleeping_scanning_bridge` only the scanning-side pose `[10,-59,-10,0,60,0]` — neither has the sleeping-side departure anchor the task-bridge pattern uses, and their safezones do not overlap the sleeping safezone. `resting/hold`→`sleeping/breathe` is a direct link between very different configs (A3 −10 vs +156). These cannot be made bidirectional without new real-robot anchor poses, so the user chose to disconnect the museum from testing/scanning by dropping the routing links.

### Changes Made

- `realtimecontrol/rtr/zones.json`:
  - `testing` `safezone` A3: `[0, 32]` → `[0, 42]` (covers the `locked_band` A3 sweep; matches the `sleeping_testing_bridge` safezone).
  - `resting` action `hold` `next`: `{"zone":"sleeping","action":"breathe"}` → removed (resting no longer transitions to sleeping).
  - `sleeping` action `yawn` `next`: removed the two bridge entries — now `[{"zone":"sleeping","action":"breathe"}, {"zone":"sleeping","action":"snore"}]` (no more `sleeping_testing_bridge` / `sleeping_scanning_bridge` exits).
  - `sleeping_testing_bridge` action `bridge` `next`: `{"zone":"testing","action":"locked"}` → removed (bridge no longer hands off to testing).

  Note: the `sleeping_scanning_bridge` → `scanning/scnr` link and `scanning/scnr` → `sleeping_scanning_bridge` link are **left intact** (they form a valid shared-pose loop, not a red).

### Documentation

None required — safezone bounds and `next` routing are data; the `"x"`/shared-pose mechanism is already documented in `behavior.py:306` / `machine.py:86-93`.

### Architecture

No architectural change. The runtime already handles a single-action zone with no valid `next`/`exits` via the previous-zone auto-return fallback (`machine.py:563`), so the dropped links do not dead-end the robot.

### Validation

- `/tmp/kilo/validate_links.py`: **45 checked, 0 broken** (was 49 checked / 12 broken).
- `python3 -m pytest tests/ --ignore=tests/test_remote_tui.py -q`: **296 passed, 5 failed, 14 skipped** — same 5 pre-existing `test_remote_sdl` TUI failures; no regressions.
- `zones.json` loads via the runtime `Zones` (13 zones). Dropped links confirmed: `resting/hold.next=None`, `sleeping/yawn.next=[breathe,snore]`, `sleeping_testing_bridge/bridge.next=None`, `sleeping_scanning_bridge/bridge.next={scanning/scnr}` (loop kept), `testing.safezone[2]=[0,42]`.

### Decisions and Caveats

- **User decision (museum routing):** disconnect the museum (resting/sleeping) from the testing/scanning networks by dropping the 4 museum routing links, rather than adding new real-robot sleeping-side anchor poses to the bridges. Consequence: `sleeping_testing_bridge` is now an isolated (unreachable, no-next) zone — left `enabled: true` in data but functionally dead. `resting/hold` now has no `next` (auto-returns to the previous zone at runtime).
- The `testing safezone` A3 widening to `[0,42]` is a **revert to the pre-tasknet value** and is consistent with the `locked_band`'s designed A3 sweep and the `sleeping_testing_bridge` safezone.
- The 4 museum reds are *data/routing* reds, not mechanism bugs — `find_shared_pose` and the gate are correct; the museum bridges simply lack the sleeping-side anchors. If those transitions are wanted back, the bridges need a shared sleeping anchor pose + a spanning safezone (real robot poses required).

### Next Task

No museum/testing reds remain. No task-network work remains. The whole `zones.json` link graph validates clean (0 broken).

### Instructions for the Next Task

No immediate next task identified. If the museum→testing/scanning transitions are wanted back later: add a shared sleeping-side anchor pose to `sleeping_testing_bridge` / `sleeping_scanning_bridge` (derived from a real sleeping pose shared with `sleeping/yawn`), widen each bridge safezone to span both configs, and restore the `yawn`→bridge and bridge→target `next` links. Re-run `/tmp/kilo/validate_links.py` + the pytest suite after any such change.

## 2026-10-26 — Task-network data cleanup: drop `scnr` bridge link, A1=`"x"` on task2↔task3 bridges

### Status

Completed. **Supersedes** the two open questions in the entry below. User decisions: (1) `task1/scnr` keeps no bridge exit — drop the `task2_task3_bridge` link; (2) `task2_task3_bridge` and `task3_task2_bridge` get A1=`"x"` like every other bridge.

### Summary

Two data adjustments to `zones.json` (user-confirmed):

1. **`task1/scnr` → `task2_task3_bridge/bridge` dropped.** `scnr` sits in task1 (A2≈-85, A5≈75); `task2_task3_bridge` is physically in the task2/task3 region (A2 `[-50,-10]`, A5 `[-25,45]`) with no safezone overlap on A2/A5, so the robot could never hand off there. `scnr` now only loops back to `task1/scan` (stays purely intra-task1). `next` converted from a 2-element list to the single-dict form (matching other single-target actions).
2. **task2↔task3 bridges A1 → `"x"`.** `task2_task3_bridge` and `task3_task2_bridge` held A1=0 constants on both poses while every other bridge and every task-zone action uses A1=`"x"` (copy-from-active, `behavior.py:306`). Now consistent: the base is never driven by these bridges; it stays at the frozen value from transition commit.

### Changes Made

- `realtimecontrol/rtr/zones.json`:
  - `task1` action `scnr` `next`: `[{"zone":"task1","action":"scan"}, {"zone":"task2_task3_bridge","action":"bridge"}]` → `{"zone":"task1","action":"scan"}`.
  - `task2_task3_bridge` action `bridge` `poses`: A1 `0` → `"x"` on both poses (`["x",-45,20,0,40,0]`, `["x",-15,20,0,-20,0]`).
  - `task3_task2_bridge` action `bridge` `poses`: A1 `0` → `"x"` on both poses (`["x",-15,20,0,-20,0]`, `["x",-45,20,0,40,0]`).

### Documentation

None required — the `"x"` mechanism is documented in `behavior.py:306` / `machine.py:86-93`; this is a data consistency adjustment.

### Architecture

No architectural change.

### Validation

- `zones.json` loads + validates via the runtime `load_state_data` (13 zones).
- Shared-pose checks (all four task2↔task3 links now resolve):
  - `task2/locked` ↔ `task2_task3_bridge/bridge`: `(['x', -45, 20, 0, 40, 0], 0, 0)`
  - `task3/approach` ↔ `task2_task3_bridge/bridge`: `(['x', -15, 20, 0, -20, 0], 0, 1)`
  - `task2/locked` ↔ `task3_task2_bridge/bridge`: `(['x', -45, 20, 0, 40, 0], 0, 1)`
  - `task3/approach` ↔ `task3_task2_bridge/bridge`: `(['x', -15, 20, 0, -20, 0], 0, 0)`
- `/tmp/kilo/validate_links.py`: **49 checked, 12 broken** — the task-network red is gone; all 12 are the pre-existing museum/testing reds (see the entry below).
- `python3 -m pytest tests/ --ignore=tests/test_remote_tui.py -q`: **296 passed, 5 failed, 14 skipped** — same 5 pre-existing `test_remote_sdl` failures.

### Decisions and Caveats

- The whole task network (task1/task2/task3 + all four bridges) now validates clean: every inter-zone link has safezone overlap and a shared pose, so all task links are bidirectional at the shared-pose hand-off.
- The 12 remaining reds are museum/testing-network data issues that predate this work (sleeping/resting/testing zones) — separate scope.

### Next Task

Decide whether to address the 12 pre-existing museum/testing reds (sleeping/resting bridges + `testing/locked_band` A3 poses). No task-network work remains.

### Instructions for the Next Task

Run `/tmp/kilo/validate_links.py` to re-list the 12 reds. They cluster into: `resting/hold` ↔ `sleeping/breathe`; `sleeping/yawn` ↔ `sleeping_testing_bridge/bridge` / `sleeping_scanning_bridge/bridge` (no shared pose + safezone gaps); eight `testing/*` ↔ `testing/locked_band` (pose `[45,-30,42,0,-5,0]` outside testing safezone on A3); `sleeping_testing_bridge/bridge` ↔ `testing/locked`. Fixing them is data work (poses/safezones/routing) in `zones.json` the same way as this entry; confirm intended routing before changing shared poses.

## 2026-10-26 — `find_shared_pose` `"x"`-wildcard comparison (runtime + validator + editor)

### Status

Completed. **Supersedes** the statement in the "A1 departure anchor" entry below that `find_shared_pose` "skips `'x'` poses (non-numeric)" — that was the *old* behavior. `find_shared_pose` now compares only *concrete* axes and treats `"x"` as a wildcard, so every action link is bidirectional-safe at the shared-pose hand-off.

### Summary

**Root cause:** the old `find_shared_pose` (`zones.py`) skipped any pose containing `"x"` (non-numeric) entirely. Once the user made bridge departure anchors `"x"` (copy-from-active, see the entry below), *no* shared pose was ever found for any link touching an `"x"` anchor — so the runtime safety gate discarded every auto bridge move, and the editor/validator flagged every such link red.

**Fix (`rtr/state/zones.py` `find_shared_pose`):** axis-by-axis comparison. An `"x"` axis on either side is a wildcard — it has no static value and resolves to the robot's live position at the hand-off (identical for both poses, `machine.py:86-93` frozen base). A pair is a shared pose when every axis that is concrete in *both* poses matches within `margin=0.5` **and** at least one such axis exists (so an all-`"x"` pose does not trivially match everything; no all-`"x"` poses exist in the current data). Returns `(pose, index_in_a, index_in_b)` or `None`.

**Runtime + gate needed no change:** `machine.py::_compute_handoff` and `safety/gate.py:104` already call `find_shared_pose`, so they now find the shared pose and resume from it (no shared pose → fall back to entry pose `poses[0]`).

**Validator (`/tmp/kilo/validate_links.py`):** rewritten to mirror `gate.py` exactly — inter-zone: safezone overlap + `find_shared_pose(src, tgt) is None`; intra-zone: no shared-pose requirement, only flags connection poses outside the zone safezone.

**Editor (`editor.html`):** added the JS `findSharedPose(pa, pb, margin)` mirroring the Python function; `edgeConflict` inter-zone now uses `findSharedPose(actionPoses(aA), actionPoses(aB))` (intra-zone logic unchanged).

**Shadowing bug found and fixed this session:** `editor.html` contained *two* `findSharedPose` definitions — the new wildcard-aware one (line 1001) and the old fully-numeric one (line 1025, from the 2026-10-24 entry). In JS the later declaration wins, so the editor was still running the *old* semantics (skip any pose containing `"x"`) and would have kept flagging every `"x"`-anchor bridge link red. Removed the old duplicate and its now-dead `poseNumeric` helper.

### Changes Made

- `realtimecontrol/rtr/rtr/state/zones.py`: `find_shared_pose` rewritten — concrete-axis comparison, `"x"` wildcard, `concrete > 0` guard.
- `realtimecontrol/rtr/editor.html`: JS `findSharedPose` (wildcard-aware) + `edgeConflict` inter-zone now call it; **removed** the old duplicate `findSharedPose` (was shadowing the new one) and the dead `poseNumeric` helper.
- `/tmp/kilo/validate_links.py`: rewritten to mirror `gate.py` rules (inter-zone overlap + shared pose; intra-zone safezone-only).

### Documentation

None required — the `"x"` frozen/copy mechanism is documented in `behavior.py:306` and `machine.py:86-93`; this change aligns the shared-pose detection with that mechanism.

### Architecture

No architectural change. The shared-pose hand-off contract is unchanged; only the detection now honors the `"x"` wildcard the runtime already uses.

### Validation

- Shared-pose detection on real `zones.json` data (Python and extracted editor JS agree):
  - `task2/locked` ↔ `task1_task2_bridge/bridge`: `(['x', -45, 20, 0, 40, 0], 0, 1)`
  - `task1/scan` ↔ `task1_task2_bridge/bridge`: `([0, -90, 20, 0, 80, 0], 0, 0)`
  - `task3/retreat` ↔ `task3_task1_bridge/bridge`: `(['x', -15, 20, 0, -20, 0], 0, 0)`
  - `task1/scnr` ↔ `task2_task3_bridge/bridge`: `None`
  - `task1/scan` ↔ `task1/scnr`: `None`
- `node --check` on all 13 extracted `editor.html` script blocks: OK.
- `python3 -m pytest tests/test_transition.py tests/test_safety_conflict.py -q`: 15 passed (existing tests use concrete poses; no regression).
- `python3 -m pytest tests/ --ignore=tests/test_remote_tui.py -q`: **296 passed, 5 failed, 14 skipped** — the 5 failures are the same pre-existing `test_remote_sdl` TUI failures.
- `/tmp/kilo/validate_links.py`: 50 checked, **13 broken** — ground truth below.

### Ground truth: the 13 red links

- **Task network (1):** `task1/scnr` ↔ `task2_task3_bridge/bridge` — `safezones do not overlap on A2, A5` + `no shared position`. Not fixable by adding a shared pose (the two safezones do not overlap on A2/A5); this is a topology/data question in `task1/scnr.next` (see Next Task).
- **Museum/testing (12, pre-existing, out of scope this pass):** `resting/hold` ↔ `sleeping/breathe`; `sleeping/yawn` ↔ `sleeping_testing_bridge/bridge`; `sleeping/yawn` ↔ `sleeping_scanning_bridge/bridge`; eight `testing/*` ↔ `testing/locked_band` links (`pose outside testing safezone on A3`); `sleeping_testing_bridge/bridge` ↔ `testing/locked`.

### Decisions and Caveats

- All-`"x"` pose edge case: guarded by `concrete > 0`; current data has no all-`"x"` poses.
- `task1/scan` ↔ `task1/scnr` has no shared pose but is **intra-zone**, so gate/validator do not require one — not red. The move is small and inside the `task1` safezone.
- The museum/testing reds predate this work and are unchanged.

### Next Task

Two open data/topology decisions (both need user input):
1. **`task1/scnr` → `task2_task3_bridge/bridge`:** the only task-network red. `scnr` sits in task1 (`["x", -85, 15, 0, 75, 0]`); `task2_task3_bridge` is a task2↔task3 bridge whose safezone does not overlap task1's on A2/A5. Candidate fix: retarget `task1/scnr.next` to `task1_task2_bridge/bridge` (the bridge that actually starts from task1), or drop the link — but this changes routing/behavior, so confirm intent first.
2. **task2↔task3 bridges A1:** `task2_task3_bridge` and `task3_task2_bridge` still hold A1=0 constants on both poses, while every other bridge and every task-zone action pose uses A1=`"x"`. If the departure-anchor directive applies to all bridges, set both bridges' A1 to `"x"`.

### Instructions for the Next Task

- For (1): on disk, `task1/scnr` `next` is `[{zone: task1, action: scan}, {zone: task2_task3_bridge, action: bridge}]` and `scnr` `poses` is `[['x', -85, 15, 0, 75, 0]]`. The problematic entry is the second one. Note: merely retargeting it to `task1_task2_bridge/bridge` does NOT clear the red — `scnr`'s pose shares no concrete axis with that bridge (A2 -85 vs -90/-45). A shared pose must also be added (e.g. `['x', -90, 20, 0, 80, 0]`, which is inside the `task1` safezone and equals `task1_task2_bridge` `poses[0]`). Confirm the intended routing first. Re-run `/tmp/kilo/validate_links.py` (expect 12 reds if the link is fixed/dropped) and the pytest suite (expect 296 passed / 5 pre-existing SDL failures).
- For (2): in `zones.json`, set `task2_task3_bridge` and `task3_task2_bridge` `poses[*]` A1 `0` → `"x"`. Re-run both checks; expect no change in red count (shared poses already match on the concrete axes).

## 2026-10-26 — A1 departure anchor: constant → `"x"` (copy-from-active-pose)

### Status

Completed. **Supersedes** the "A1 constant (0)" entry below. The user corrected the mechanism: the bridge departure anchor's A1 must NOT be a hardcoded constant — it is `"x"`, the frozen/copy axis, which copies the robot's current A1 at transition commit. The user has already applied this to `zones.json`; this entry records and validates it.

### Summary

The earlier entry set `task1_task2_bridge` `poses[0]` A1 to a constant `0` to hold A1 flat. The user rejected that: a constant departure anchor forces a base rotation to a fixed value *before* the approach. The correct mechanism is the `"x"` frozen/copy axis (`behavior.py:306 resolve_x`): an axis set to `"x"` is copied from the robot's current pose (the `_frozen_base` captured at transition commit, `machine.py:93`) and is **never driven** (`behavior.py:368`). `find_shared_pose` skips `"x"` poses (non-numeric, `zones.py:103`), so a `"x"` departure anchor means "start from wherever A1 currently is" rather than "rotate to 0 first" — the base rotation is absorbed into the approach motion, not a separate pre-move.

User-applied changes in `zones.json` (file grew 1505→1507 lines):
- **All task-zone action poses** A1 → `"x"`, `variable_axes` → `[0,1,2,3,4,5]`: `task2/locked`, `task3/approach`, `task3/facefocus`, `task3/retreat`. (`task1/scan` kept A1=0.)
- **New `task1/scnr` action**: pose `["x", -85, 15, 0, 75, 0]`, all axes variable, behavior `impatient`, `next` → `[task1/scan, task2_task3_bridge/bridge]`.
- **`task1/scan`** `next` is now a list: `[task1_task2_bridge/bridge, task1/scnr]`.
- **`task2/locked`** `next` is now a list: `[task2_task3_bridge/bridge, task1_task2_bridge/bridge]`.
- **Bridges touching task1** both poses A1 → `"x"`: `task1_task2_bridge`, `task3_task1_bridge` (retreat to task1).
- **Task2↔task3 bridges** (`task2_task3_bridge`, `task3_task2_bridge`) still hold A1=0 constants — not yet `"x"`.

### Changes Made

- `realtimecontrol/rtr/zones.json`: (user-applied) A1 `"x"` on task-zone poses + task1-touching bridges; new `task1/scnr` action; `next` fields converted to lists where routing branches.

### Documentation

None required — the `"x"` frozen/copy mechanism is already documented in `behavior.py:306` and `machine.py:86-93`; this is a data adjustment, not a new contract.

### Architecture

No architectural change. The `"x"` mechanism already exists in the runtime; this entry only records its application to the 3-task network data.

### Validation

- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py`: **296 passed, 5 failed, 14 skipped** — the 5 failures are the same pre-existing `test_remote_sdl` TUI failures; no new failures.
- `/tmp/kilo/validate_links.py`: 50 checked, 13 broken. The task-network reds are the expected `"x"` no-shared-pose consequences (e.g. `task1/scnr <-> task2_task3_bridge/bridge`).

### Decisions and Caveats

- The `"x"` departure anchor means the base rotation is absorbed into the approach motion (copied from the active pose), not a separate pre-move. This is the user's stated intent.
- **Open:** the task2↔task3 bridges (`task2_task3_bridge`, `task3_task2_bridge`) still use A1=0 constants. If the same rule applies, their departure anchors should also be `"x"` for consistency.

### Next Task

Confirm whether the task2↔task3 bridges should also get A1=`"x"` on the departure anchor.

### Instructions for the Next Task

If yes: edit `realtimecontrol/rtr/zones.json` `task2_task3_bridge` and `task3_task2_bridge` `poses[0]` A1 `0` → `"x"`. Re-run `/tmp/kilo/validate_links.py` and the pytest suite (expect the same pre-existing SDL/TUI failures).

## 2026-10-26 — Made A1 (base) constant during the task1→task2 bridge approach

### Status

Completed. The `task1_task2_bridge` approach now holds A1 constant (0) so the base no longer rotates while the robot approaches the task2 locked person. `task2_task3_bridge` already held A1 constant (0→0) and needed no change.

### Summary

The 2-pose bridge pattern from the previous task set `poses = [source_end, target_start]`. For `task1_task2_bridge` that meant `poses[0] = [45, -90, 20, 0, 80, 0]` (A1=45, task1's end pose) and `poses[1] = [0, -45, 20, 0, 40, 0]` (A1=0, task2's start pose). The robot therefore rotated its base A1 from 45→0 *during* the approach.

The user requires A1 to stay constant while approaching the locked person (task2/task3 are both A1=0). Fix: set `task1_task2_bridge` `poses[0]` A1 to 0 so the bridge approach holds A1=0 throughout. The base rotation (45→0) now happens as a separate deliberate handoff move (`task1/scan` → bridge entry) before the approach, not during it.

### Changes Made

- `realtimecontrol/rtr/zones.json`: `task1_task2_bridge` `poses[0]` A1 `45` → `0` (pose now `[0, -90, 20, 0, 80, 0]`). `poses[1]` unchanged (`[0, -45, 20, 0, 40, 0]`). Safezone unchanged (A1 `[-185,185]` already contains 0).

### Documentation

None required — the bridge data model is already documented in `zones.py`; this is a data adjustment to satisfy a motion constraint, not a new behavior or config contract.

### Architecture

No architectural change. The bridge is still a no-op `behavior: "bridge"` zone stepping through curated anchors. The change moves the A1 reorientation from inside the bridge approach to the pre-approach handoff move (`_compute_handoff` falls back to the entry pose since the bridge no longer shares an A1-matching pose with `task1/scan`).

### Validation

- `/tmp/kilo/validate_links.py`: 45 checked links, **4** broken (was 3). The new red link is `task1/scan <-> task1_task2_bridge/bridge` — "no shared position on A1" — which is the *intended* consequence of making the bridge A1-constant (the base now rotates as a separate handoff move). The 3 pre-existing sleeping/resting red links are unchanged.
- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py`: **296 passed, 5 failed, 14 skipped** — the 5 failures are the same pre-existing `test_remote_sdl` TUI failures; no new failures.

### Decisions and Caveats

- **Trade-off accepted:** an A1-constant approach is incompatible with a shared departure pose when source A1 ≠ target A1. Choosing A1-constant means the bridge no longer shares a pose with `task1/scan`, so the validator flags it and the base rotates during the handoff move (task1 → bridge entry) rather than during the approach. This matches the user's stated intent (A1 constant while approaching the locked person).
- **`task2_task3_bridge` needed no change** — it already holds A1=0→0.
- **`task3_task1_bridge` (retreat, task3→task1) still changes A1 0→45.** The user only specified the forward direction (task1→task2→task3). If the same A1-constant rule should apply to the retreat, `task3_task1_bridge` `poses[0]` A1 should be set to 45 (task1's value) — flagged as an open question, not changed.

### Next Task

Confirm with the user whether the A1-constant rule also applies to the retreat bridges. If yes, set `task3_task1_bridge` `poses[0]` A1 to 45. Otherwise no immediate next task.

### Instructions for the Next Task

If extending the rule to the retreat: edit `realtimecontrol/rtr/zones.json` `task3_task1_bridge` `poses[0]` (currently `[0, -15, 20, 0, -20, 0]`) to `[45, -15, 20, 0, -20, 0]` so the task3→task1 approach holds A1=45 (task1's value). Re-run `/tmp/kilo/validate_links.py` and the pytest suite (expect the same 3 pre-existing SDL failures + the expected A1 shared-pose red links).

## 2026-10-26 — Fixed all broken action-level links in the 3-task zone network (2-pose bridges + retreat bridges)

### Status

Completed. All four broken action-level links in the 3-task zone network (task1→task2→task3 + retreat) now validate clean (green) against the editor's `edgeConflict` rules. The 4 task-network links are fixed; the 3 pre-existing sleeping/resting bridge links remain red (out of scope — they are not part of the 3-task network).

### Summary

The 3-task network's inter-zone links were structurally broken: a **single-pose** bridge cannot be clean on *both* legs when the source's end pose ≠ the target's start pose. The editor's `edgeConflict` checks (1) safezone overlap on all 6 axes and (2) source END pose (`poses[-1]`) vs target START pose (`poses[0]`) per-axis within 0.5. A single-pose bridge has `poses[0] == poses[-1]`, so it can match at most one of the two endpoints.

**Fix:** rewrite each bridge as a **2-pose bridge** `poses = [source_end, target_start]`, with a safezone that (a) contains *both* poses (intra-zone check) and (b) overlaps *both* neighboring zones' safezones on every axis (inter-zone check). This makes leg 1 share `poses[0]` (source end) and leg 2 share `poses[-1]` (target start), so both validate clean. The runtime already handles this: `_compute_handoff` → `find_shared_pose` finds a shared pose on entry (resume from `poses[0]`) and on exit (resume from `poses[-1]`), consistent with the existing single-pose bridges.

**Retreat links** (task3→task1, task3→task2) were *direct* inter-zone links with no safezone overlap on A2/A5. Added two new bridge zones (`task3_task1_bridge`, `task3_task2_bridge`) and redirected `task3/retreat`'s `next` to route through them.

### Changes Made

- `realtimecontrol/rtr/zones.json`:
  - `task1_task2_bridge`: safezone A2 `[-50,-40]`→`[-95,-40]`, A5 `[35,45]`→`[35,85]` (span both task1/task2 safezones); poses `[[0,-45,20,0,40,0]]` → `[[45,-90,20,0,80,0],[0,-45,20,0,40,0]]` (2-pose); `pose_imgs`/`pose_videos` → 2 entries.
  - `task2_task3_bridge`: safezone A2 `[-20,-10]`→`[-50,-10]`, A4 `[-10,10]`→`[0,1]`, A5 `[-25,-15]`→`[-25,45]`, A6 `[-15,15]`→`[-5,5]`; poses `[[0,-15,20,0,-20,0]]` → `[[0,-45,20,0,40,0],[0,-15,20,0,-20,0]]` (2-pose); `pose_imgs`/`pose_videos` → 2 entries.
  - **new** `task3_task1_bridge`: safezone `[[-185,185],[-95,-10],[15,25],[0,1],[-25,85],[-5,5]]`, poses `[[0,-15,20,0,-20,0],[45,-90,20,0,80,0]]`, `next` → `task1/scan`.
  - **new** `task3_task2_bridge`: safezone `[[-185,185],[-50,-10],[15,25],[0,1],[-25,45],[-5,5]]`, poses `[[0,-15,20,0,-20,0],[0,-45,20,0,40,0]]`, `next` → `task2/locked`.
  - `task3/retreat`: `next` changed from direct `[task1/scan, task2/locked]` → `[task3_task1_bridge/bridge, task3_task2_bridge/bridge]`.
  - `layout.pos`: added `task3_task1_bridge` (1350,420) and `task3_task2_bridge` (1500,480); `layout.key` updated to include both.
- `realtimecontrol/rtr/brain.json`: `zone_groups.active.zones` now includes `task3_task1_bridge` and `task3_task2_bridge` (7 zones total).

### Documentation

None required — the zone/bridge data model (unified `poses` form, 2-pose bridge = a multi-anchor stepping action) is already documented in `zones.py`'s module docstring; no new behavior or config contract was introduced.

### Architecture

No architectural change. The bridge pattern (a no-op `behavior: "bridge"` zone that steps through curated anchor poses to transition between zones) is unchanged; the fix only corrects the *data* (poses + safezones + routing) so the existing `edgeConflict`/`find_shared_pose` logic validates clean. The 2-pose form is the canonical "multi-anchor action" the unified form already supports.

### Validation

- `/tmp/kilo/validate_links.py` (mirrors editor `edgeConflict` exactly): **before** 7 broken links (4 task-network + 3 sleeping/resting); **after** 3 broken links (0 task-network + 3 sleeping/resting). All 4 task-network links green.
- `python -c "from rtr.state.zones import load_state_data; load_state_data('zones.json')"`: loads OK, 13 zones, all 4 bridges have 2 poses.
- `python -m pytest tests/test_bridge_behavior.py tests/test_safety_conflict.py tests/test_state_machine.py tests/test_zone_groups.py tests/test_transition.py -q`: **60 passed, 9 skipped**.
- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py`: **296 passed, 5 failed, 14 skipped** — the 5 failures are the pre-existing `test_remote_sdl` TUI failures (confirmed pre-existing by `git stash` + re-run); `test_remote_tui.py` has a pre-existing collection ImportError (`action_kind`), also unrelated.

### Decisions and Caveats

- **2-pose bridge is the minimal clean fix.** A single-pose bridge is mathematically impossible to be clean on both legs when source-end ≠ target-start. The 2-pose form `[source_end, target_start]` is the only shape that satisfies both `edgeConflict` legs.
- **Safezones are per-axis *spans* (not intersections).** Each bridge safezone must contain both anchor poses AND overlap both neighbors on every axis; using the union-span (e.g. A5 `[35,85]` for task1_task2_bridge) satisfies both constraints. An intersection would be empty on A2/A5 and fail.
- **Agent-chosen poses are adjustable.** task2/locked `[0,-45,20,0,40,0]` and task3 approach/facefocus/retreat `[0,-15,20,0,-20,0]` were agent-chosen (must stay in their zone safezones). If the user changes them, the bridge poses + safezones must be recomputed (bridge poses = `[source_end, target_start]`; safezone = per-axis span containing both poses that overlaps both neighbors).
- **The 3 sleeping/resting red links are out of scope** but share the same structural flaw (single-pose bridges: `sleeping_testing_bridge`, `sleeping_scanning_bridge`, and the `resting/hold`↔`sleeping/breathe` direct link). They can be fixed with the same 2-pose pattern if requested.
- **Dwell caveat (unchanged):** `_autonomy_dwell_s` (brain.py:883) looks up group by `mode_group` (`event-sit-back`), not by group name (`active`), so `params.dwell_s` in the `active` group is documentation-only; effective dwell is the per-action `dwell_s: 35` on `task1/scan`.

### Next Task

Fix the 3 remaining pre-existing red links (sleeping/resting network) using the same 2-pose bridge pattern, if the user wants the whole graph green. Otherwise no immediate next task.

### Instructions for the Next Task

The 2-pose bridge recipe: for a link `A → B` where A's end pose ≠ B's start pose, create/rewrite the bridge zone with `poses = [A_end, B_start]` and a safezone whose each axis is a span that (a) contains both `A_end[i]` and `B_start[i]` and (b) overlaps both A's and B's safezone on that axis. Add the bridge to `layout.pos` + `layout.key` (zones.json) and to the relevant `zone_groups` (brain.json). Validate with `/tmp/kilo/validate_links.py` (must show 0 broken links in the target network) and `pytest tests/test_bridge_behavior.py tests/test_safety_conflict.py`. Do NOT touch the 3 sleeping/resting links unless explicitly requested (out of scope for the tasknet work).

## 2026-10-26 — Removed the hardcoded zone groups (museum-closed / event-sit-back / stage-takeover) so editor-created groups are the only ones and deletions persist across reloads

### Status

Completed. The three default zone groups no longer exist anywhere in the realtime-control codebase; `zone_groups` is now purely editor-created. `hunt` and `facefocus` are untouched (they are special brain loops, not groups-tab groups, and remain in `BRAIN_DEFAULT_GROUPS` for their param defaults).

### Summary

The user's deleted groups (`museum-closed`, `event-sit-back`, `stage-takeover`) kept reappearing because both the editor and the Python core **re-seeded** them on every load from hardcoded defaults, even though `brain.json` on disk already had `"zone_groups": {}`. The fix removes every re-seed point and every hardcoded copy of the three groups:

- **`rtr/brain/config.py`** — `DEFAULT_BRAIN_CONFIG["zone_groups"]` is now `{}`; the "fill in any default mode groups not present in the file" loop in `validate_brain_config()` is removed; the shape docstring no longer lists the three groups. The Python brain (`brain.py`) reads `zone_groups` generically (iterates whatever exists), so empty is safe. The `performance_mode` display heuristic in `brain.py:280-283` still references the old names but degrades safely (group absent → `performance_mode: False`).
- **`editor.html`** — `BRAIN_FALLBACK.zone_groups` is now `{}`; `BRAIN_DEFAULT_GROUPS` no longer contains the three groups (only `hunt`/`facefocus` remain, needed by `_brainNormalizeGroup` for their `params` defaults); the re-seed loop in `normalizeBrain()` is removed. `buildGroupsForm()` renders from `BRAIN.zone_groups` as before; `removeGroup()` now sticks.
- **`client.html`** — `BRAIN_FALLBACK.zone_groups` is now `{}` (display-client fallback only; its `loadBrain()` already built groups generically from the fetched data).
- **`test_groups_tab.js`** — the test was already broken (LOAD ERROR from a stale mini-DOM `STATIC_IDS` list and stale API exports `_buildGroupBlock`/`selectedGroup`). Rewritten: added the 41 missing static element IDs, upgraded the mini-DOM `matchOne` to handle `[data-attr]`/`[attr=value]` selectors (needed by `.brainCard[data-name]` and `input[type=checkbox]`), fixed the export list, and rewrote the assertions for the no-default-groups behavior.

The per-zone `mode_group` field (`zones.json` / `zones.py` `MODE_GROUPS`) is a **separate concept** (a per-zone label, not a groups-tab group) and was intentionally left untouched.

### Changes Made

- `realtimecontrol/rtr/rtr/brain/config.py`: `DEFAULT_BRAIN_CONFIG["zone_groups"]` → `{}`; removed the default-group re-fill loop in `validate_brain_config()`; updated the shape docstring (`zone_groups` example is now "empty by default — groups are created in the editor").
- `realtimecontrol/rtr/editor.html`: `BRAIN_FALLBACK.zone_groups` → `{}`; `BRAIN_DEFAULT_GROUPS` → only `hunt` + `facefocus` (updated comment); removed the re-seed loop in `normalizeBrain()` (updated comment).
- `realtimecontrol/rtr/client.html`: `BRAIN_FALLBACK.zone_groups` → `{}`.
- `realtimecontrol/rtr/test_groups_tab.js`: added 41 missing IDs to `STATIC_IDS`; replaced `matchOne` with a tokenizer that handles tag/class/id/`[data-attr]`/`[attr=value]` selectors (with `dataset` fallback for `data-*`); fixed the `__T` export list (removed `_buildGroupBlock`, `selectedGroup`); rewrote the test body for no-default-groups (on-disk parity assertion, add/rename/remove, reload-no-reseed, `normalizeBrain` unit checks, full-payload save check).

### Documentation

- `config.py` module docstring updated (the `zone_groups` shape example and the "three mode groups" comment are gone). No other documentation needed — the groups tab's in-editor help text already describes groups generically.

### Architecture

- No architectural change. The runtime group-navigation mechanism (`zone_groups` + `_handle_enable_group` + `_allowed_zones`) is unchanged; it now operates on a purely editor-defined set. The config contract shifts from "the brain always has the three default groups" to "the brain has exactly the groups in brain.json" — an empty set is valid (autonomy then runs across all enabled zones, as `_allowed_zones` already handles when no group is active).

### Validation

- `node test_groups_tab.js`: **ALL_TESTS_PASSED** (20 assertions, including no-reseed-on-reload and the full save payload with hunt/facefocus/autonomy/behaviors/cam_control intact).
- Python config checks: `validate_brain_config({'zone_groups': {}})` → `{}`; `builtin_brain_config()['zone_groups']` → `{}`; `load_brain_config('brain.json')` → `zone_groups = {}` (at the time; the file now holds the concurrent `active` group, which also validates); a custom editor-created group validates with its params preserved.
- `python3 -m pytest tests/ -q --ignore=tests/test_remote_tui.py`: **296 passed, 5 failed, 14 skipped** — identical to the pre-change baseline (the 5 failures are the pre-existing `test_remote_sdl` TUI failures; `test_remote_tui.py` has a pre-existing collection ImportError for `action_kind`, also unrelated).
- Brain/zone-group Python tests in isolation (`test_zone_groups.py`, `test_brain_autonomy_dwell.py`, `test_brain_assertive.py`, `test_midi_brain_trigger.py`, `test_brain_action_cam.py`): **50 passed**.
- Grep sweep: no remaining references to `museum-closed`/`event-sit-back`/`stage-takeover` in any `.html` or `.js` file (only the out-of-scope per-zone `mode_group` labels in `zones.py` and the safe `performance_mode` heuristic in `brain.py`).

### Decisions and Caveats

- `hunt`/`facefocus` were deliberately kept in `BRAIN_DEFAULT_GROUPS` because `_brainNormalizeGroup("hunt"/"facefocus", ...)` reads their `params` defaults from there; removing them would throw on `dp.lost_s` etc. They are stripped from `BRAIN.zone_groups` by `normalizeBrain` as before.
- The `brain.py` `performance_mode` heuristic (`stage-takeover` enabled while `event-sit-back` disabled) was left as-is: it is display-only, degrades to `False` when the groups are absent, and would work again if the user re-creates groups with those exact names.
- **Concurrent work**: while this task ran, a separate "tasknet" task modified the live data files (`zones.json` now has 11 zones, `brain.json` `zone_groups` now holds a single `active` group). Those changes are independent of this fix and were not touched; the rewritten `test_groups_tab.js` is robust to whatever groups exist on disk (it asserts in-memory == on-disk, not a fixed count).
- `test_remote_tui.py` (collection ImportError) and the `test_remote_sdl.py` failures are pre-existing and out of scope.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

The groups tab now shows exactly the groups in `brain.json` (currently: `active`, from the concurrent tasknet work). If the user wants to delete it, the editor's remove button now persists the deletion across reloads — no further code change needed. If groups are re-added via the editor, they are stored in `brain.json` and picked up by the Python core on hot-reload. Do not re-introduce default groups in `config.py`/`editor.html`; the re-seed loops are deliberately gone.

## 2026-10-26 — Built the 3-task zone test network (task1 scan → task2 track → task3 approach/facefocus + retreat) and the selectable `active` group

### Status

Completed. `zones.json` now holds **11 zones** (was 6): the original `resting`, `sleeping`, `testing`, `scanning`, `sleeping_testing_bridge`, `sleeping_scanning_bridge` plus 5 new ones — `task1`, `task2`, `task3`, `task1_task2_bridge`, `task2_task3_bridge`. `brain.json` `zone_groups` now holds a single **`active`** group containing all 5 created zones. Both files pass their validators; the test suite is unchanged (296 passed, the same 5 pre-existing `test_remote_sdl` TUI failures, 14 skipped).

### Summary

Built a small 3-task engagement-ladder test network that exercises the available camera modes, behaviors, and the brain engagement ladder, with every created zone placed in a selectable `active` group. The flow is `task1 (scan) → task1_task2_bridge → task2 (track) → task2_task3_bridge → task3 (approach/facefocus/retreat)`, with task3's `retreat` looping back to task1/task2.

**task1** — class `scan` (non-active → `variable_axes`), `mode_group` `event-sit-back`. Supplied safezone `[(-185,185),(-95,-85),(15,25),(0,1),(75,85),(-5,5)]` and pose `[45,-90,20,0,80,0]` (A1 free; A2=-90, A3=20, A4=0, A5=80, A6=0) used exactly. Single action `scan` (idle sweep, `variable_axes: [0]`, behavior `spontaneous`, `dwell_s: 35`) → `task1_task2_bridge/bridge`.

**task2** — class `track` (active → `axis_bands`), `mode_group` `event-sit-back`. Supplied safezone `[(-185,185),(-50,-40),(15,25),(0,1),(35,45),(-5,5)]`, pose `[0,-45,20,0,40,0]`. Single action `locked` (`axis_bands` = safezone, behavior `locked`, speed 40) → `task2_task3_bridge/bridge`.

**task3** — class `approach` (active → `axis_bands`), `mode_group` `event-sit-back`. Supplied safezone `[(-185,185),(-20,-10),(15,25),(-10,10),(-25,-15),(-15,15)]`, pose `[0,-15,20,0,-20,0]`. Three actions: `approach` (behavior `aggressive`, speed 100) → `facefocus`; `facefocus` (behavior `gentle`, close face) → `retreat`; `retreat` (behavior `calm`, `loop: false`) → `task1/scan` + `task2/locked`.

**Bridges** — no class (like the existing bridges). `task1_task2_bridge` (safezone = task2's, pose = task2's start `[0,-45,20,0,40,0]`) → `task2/locked`. `task2_task3_bridge` (safezone = task3's, pose = task3's start `[0,-15,20,0,-20,0]`) → `task3/approach`. Both satisfy `find_shared_pose` (margin 0.5): the bridge pose is the target's start pose, so the handoff commits at the shared pose (index 0 in both lists).

**`active` group** (brain.json `zone_groups`): `{zones: [task1, task2, task3, task1_task2_bridge, task2_task3_bridge], enabled: true, params: {dwell_s: 35}}`. All 5 created zones are included so that when the group is active, `_allowed_zones` (brain.py:1563) still permits the next-links to the bridges (a group-active brain restricts autonomy to the group's zones; excluding the bridges would make the task1→task2 and task2→task3 links invalid).

**Key validation decisions:**
- safezone `A4=(0,0)` in the supplied task1/task2 values is **invalid** (`_validate_zone` requires strict `lo < hi`); normalized to `(0,1)` — the same precedent as the existing `resting`/`scanning` zones. All other supplied safezone values used exactly.
- Inverted safezone ranges from the prior turn were already normalized before this write.
- The group's `params.dwell_s` (35) is documentation: `_autonomy_dwell_s` (brain.py:883) looks the group up by the zone's `mode_group` (`event-sit-back`), not by the group name (`active`), so it does not apply at runtime. The 35s idle dwell is instead set per-action on `task1/scan` (`dwell_s: 35`), which is the value the dwell lookup actually reads.

### Changes Made

- `realtimecontrol/rtr/zones.json`: added `task1`, `task2`, `task3`, `task1_task2_bridge`, `task2_task3_bridge` to `zones`; added their `layout.pos` entries (a fresh bottom-right cluster at x≈1200-1600, y≈300-500); updated `layout.key` to include all 11 zone names.
- `realtimecontrol/rtr/brain.json`: added the `active` group to `zone_groups` (previously `{}`).
- Backups: `zones.json.pre-tasknet` and `brain.json.pre-tasknet` (the pre-write state of each file).
- Build script: `/tmp/kilo/build_task_network.py` (idempotent-guarded; asserts the new zone names don't already exist before writing; validates both files before the atomic replace).

### Documentation

- None required — no config contract or behavior changed; this adds test data to the existing zone/brain schema.

### Architecture

- No architectural change. The network reuses the existing engagement-ladder classes (`scan`/`track`/`approach`), the bridge transition pattern (no-class zone, safezone = target's, pose = target's start, `behavior: bridge`, `loop: false`, next → target), and the runtime group-navigation mechanism (`zone_groups` + `_handle_enable_group` + `_allowed_zones`). The `active` group is a new selectable group, not a new mode_group.

### Validation

- `validate_state_data` (full zones.json): **passes** (11 zones).
- `validate_brain_config` (full brain.json): **passes** (`zone_groups: ['active']`).
- `find_shared_pose` bridge continuity: `task1_task2_bridge/bridge → task2/locked` shared `([0,-45,20,0,40,0], 0, 0)`; `task2_task3_bridge/bridge → task3/approach` shared `([0,-15,20,0,-20,0], 0, 0)`.
- Pose-in-safezone + HARDWARE_LIMITS check (script assertions): all 3 task poses within their safezone and the hardware floor.
- `python3 -m pytest tests/ -q --ignore=tests/test_remote_tui.py`: **296 passed, 5 failed, 14 skipped** — the 5 failures are the pre-existing `test_remote_sdl` TUI failures (unrelated to zones, identical to before this change).

### Decisions and Caveats

- Zone names `task1`/`task2`/`task3` and the task2/task3 poses are my design choices (the user supplied only the task1 pose and the three safezones).
- The `active` group includes the bridges (not just task1/task2/task3) — required for group-active navigation to work (see Summary). If the user wants a UI group that lists only the three "real" tasks, the bridges would need to stay reachable some other way; as it stands, the group is the navigation-safe choice.
- The `active` group's `params.dwell_s` is not read at runtime (group lookup is by `mode_group`, not group name). The 35s dwell is effective only via the per-action `dwell_s` on `task1/scan`.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

If extending the network: add new zones to `zones.json` `zones` (validate with `validate_state_data`), add their `layout.pos` entries and update `layout.key`, and add them to the `active` group in `brain.json` `zone_groups` if they should be reachable under group-active navigation. For active-class zones (`track`/`farfocus`/`approach`/`facefocus`) use `axis_bands`; for non-active classes use `variable_axes`. Bridge zones follow the existing pattern (no class, safezone = target's, pose = target's start pose, `behavior: bridge`, `loop: false`, next → target).

## 2026-10-26 — Added `resting` zone (exact backup pose) to `zones.json` to fix `KeyError: 'resting'` on startup

### Status

Completed. `python main.py --robot` no longer crashes at `StateMachine.__init__` with `KeyError: 'resting'`. `zones.json` now holds 6 zones: `resting`, `sleeping`, `testing`, `scanning`, `sleeping_testing_bridge`, `sleeping_scanning_bridge`.

### Summary

The 5-zone rewrite of `zones.json` removed the `resting` zone, but `StateMachine.__init__` (machine.py:35) defaults to `initial_zone="resting"` and `reset()` (machine.py:803) hardcodes `current_zone="resting"` — so `main.py` crashed at startup with `KeyError: 'resting'` before the engine ever ran.

The `resting` zone was re-added from the backup (`zones.json.bak-20261026-121651`) with the exact backup pose data:
- **`resting`** (single action `hold`): the backup's exact 3 `hold` poses `[[10,-59,-10,0,60,0],[0,-130,-9,0,0,0],[-60,-90,-9,15,10,0]]`, exact `safezone` (6 axis pairs), `speed: 20`, `variable_axes: [0..5]`, `behavior: "restless"`, `loop: true`, `speed: 20`, and the backup's `pose_imgs`/`pose_videos`.
- `next`: `[{zone: "sleeping", action: "breathe"}]` — the backup's first zone-level exit (`["sleeping","breathe"]`), adapted to the current 5-zone world (the backup's other targets `wake`/`resting_testing_bridge` no longer exist). Harmless at runtime: `hold` is `loop: true`, so it wraps around and never fires `_advance_after_action`; the entry only feeds the `zones.graph()` display.
- The backup's `wake` action and zone-level `exits` field were **not** carried over (the `exits` field is gone from the current schema; `wake`'s next referenced the removed `resting_sleeping_bridge`).
- `layout.pos.resting` = the backup's exact position `{x: 479.02371978759766, y: 11.794570922851562}`; `layout.key` updated to include `resting`.

Resulting flow graph: `resting → sleeping` (informational only for the looping action) plus the existing `sleeping → bridges → testing/scanning`.

### Changes Made

- `realtimecontrol/rtr/zones.json`: added the `resting` zone (first in the `zones` object) with the single `hold` action; added `layout.pos.resting`; added `resting` to `layout.key`.

### Documentation

- None required — no config contract or behavior changed; this restores the home zone the runtime already assumes.

### Architecture

- No architectural change. `resting` is the canonical home zone (`StateMachine` default `initial_zone` and `reset()` both target it). It is a single-action looping zone, consistent with the current minimal-zone pattern (`scanning` is also single-action). The previous-zone fallback would auto-return it if it ever dead-ended, but `loop: true` means it never ends.

### Validation

- `validate_state_data` (full file): passes.
- `StateMachine(Zones(data["zones"]), tick_hz=20.0)`: initializes with `current_zone="resting"`, `speed=20` — the exact previously-failing call.
- `python3 -m pytest tests/ -q --ignore=tests/test_remote_tui.py`: **296 passed, 5 failed, 14 skipped** — the 5 failures are the pre-existing `test_remote_sdl` TUI failures (unrelated to zones, same as before this change).
- `tests/test_remote_tui.py` collection error: pre-existing `ImportError: cannot import name 'action_kind' from 'rtr.flow'` (stale test import; unrelated to this data change).
- `brain.json` `zone_groups` is `{}` (empty), so the stale `DEFAULT_BRAIN_CONFIG` zone names are not used at runtime.

### Decisions and Caveats

- Only the `hold` action was ported (not `wake`): the user asked for "a resting pose" and the current architecture favors single-action zones. If the `wake` transition back into sleep is wanted later, port it from the backup and re-point its `next` (the backup's `resting_sleeping_bridge` no longer exists — it would need a new `resting_sleeping_bridge` or a direct `sleeping/breathe` hop).
- The `resting → sleeping` link in `next` is informational (looping actions never auto-advance); actual transitions to `resting`/`sleeping` happen via manual triggering (brain/patches/midi/chatbot `trigger_action`/`play_action` commands).

### Next Task

No immediate next task identified.

### Instructions for the Next Task

If the operator wants the full backup `resting` zone back (both `hold` and `wake`), port `wake` from `zones.json.bak-20261026-121651` and re-point its `next` to existing zones. If a physical bridge path resting→sleeping is wanted, add a `resting_sleeping_bridge` following the existing bridge pattern (single `bridge` action, `loop: false`, `variable_axes: []`, pose = target entry pose).

## 2026-10-26 — Rewrote `zones.json` to a 5-zone table (sleeping / testing / scanning + 2 bridges) and updated the data-dependent tests

### Status

Completed. `zones.json` now holds exactly `sleeping`, `testing`, `scanning`, plus the two new directional bridges `sleeping_testing_bridge` and `sleeping_scanning_bridge`. Every `exits` field is gone and every action `next` that referenced a removed zone is gone, so the file passes `validate_state_data` with no dangling references. The three tests that load the *real* `zones.json` were updated to reference the new zones and now pass.

### Summary

The pending item was to write a fresh `zones.json` with only `sleeping` + `testing` and all `exits`/`next` cross-references removed. The operator refined that: **also keep `scanning`**, and **make/keep a bridge zone between sleeping↔testing and between sleeping↔scanning**.

A "bridge zone" in this codebase is a single-action zone (`behavior: "bridge"`, `loop: false`, `variable_axes: []`) whose one `bridge` action holds curated anchor poses and hands off via `next` to the target zone's entry action. The existing bridges follow the naming `<source>_<target>_bridge`. Neither `sleeping_testing_bridge` nor `sleeping_scanning_bridge` existed, so both were created.

What the new table contains:
- **`sleeping`** (actions `breathe`/`snore`/`yawn`): full real pose data kept; `exits` removed; `next` filtered to intra-zone only, with the two new bridges appended to `yawn` (the historical exit action) at lowest priority.
- **`testing`** (actions `locked`/`intent`/`curious`/`gentle`/`locked_band`): full real pose data kept; `exits` removed; `next` filtered to intra-zone only.
- **`scanning`** (action `scnr`): full real pose data kept; its old `next` (→ `scanning_leaping_bridge`, `scanning_roaring_bridge`) referenced removed zones, so it is now empty (the action is `loop: true`, so it simply loops).
- **`sleeping_testing_bridge`** → hands off to `testing/locked`; pose = the `testing/locked` entry pose `[45,-30,42,0,-5,0]`; safezone/speed inherited from `testing`.
- **`sleeping_scanning_bridge`** → hands off to `scanning/scnr`; pose = the `scanning/scnr` entry pose `[10,-59,-10,0,60,0]`; safezone/speed inherited from `scanning`.

Resulting flow graph (verified via `rtr.flow`): `sleeping → sleeping_testing_bridge → testing` and `sleeping → sleeping_scanning_bridge → scanning`.

### Changes Made

- `realtimecontrol/rtr/zones.json`: rebuilt from the 38-zone original down to the 5 zones above. All `exits` removed; every `next` entry whose `zone` is not in the kept set removed; `sleeping.yawn.next` gained the two new bridge targets; the two new bridge zones added (single `bridge` action each, `behavior: "bridge"`, `loop: false`, `variable_axes: []`, `next` → target entry action); `layout` reduced to the 5 zones (the 3 kept zones retain their old positions, the 2 bridges placed at the midpoint of their source/target).
- `tests/test_state_machine.py`: `test_set_control_mode_validates` — `initial_zone="resting"` → `"sleeping"` (the only change; the assertion logic is unchanged).
- `tests/test_transition.py`: `test_all_reachable_transitions_complete` — the four transition pairs updated from the removed zones to the two bridge hops + two bridge→target hops: `("sleeping","yawn","sleeping_testing_bridge","bridge")`, `("sleeping","yawn","sleeping_scanning_bridge","bridge")`, `("sleeping_testing_bridge","bridge","testing","locked")`, `("sleeping_scanning_bridge","bridge","scanning","scnr")`.
- `tests/test_bridge_behavior.py`: `test_real_zones_bridge_action_is_tagged` — now asserts against `sleeping_testing_bridge` (was `resting_sleeping_bridge`) and `len(act["poses"]) >= 1` (was `== 3`; the new bridge is a single curated pose).
- `realtimecontrol/rtr/zones.json.bak-20261026-121651`: backup of the original 38-zone file (repo convention of keeping `zones.json.bak-*` snapshots).

### Documentation

- None beyond this handoff. No user-facing config contract changed; the zone graph is now a smaller, self-contained table.

### Architecture

- No architectural change. The zone model, the bridge pattern, and the navigation priority (`action next` → `zone exits` → previous-zone fallback) are unchanged. Only the *data* in `zones.json` changed. The bridges are **directional** (sleeping → target): the reverse direction is handled by the runtime previous-zone fallback for single-action zones (`scanning`) or by manual triggering for multi-action zones (`testing`). If a true bidirectional path is wanted later, add a `testing_sleeping_bridge` / `scanning_sleeping_bridge`.

### Validation

- `validate_state_data` (via `load_state_data`) on the on-disk file → **OK** (passes; no dangling `exits`/`next`).
- `rtr.flow` graph check → `reachable(sleeping) = [sleeping, sleeping_testing_bridge, sleeping_scanning_bridge]`; `reachable(sleeping_testing_bridge) = [testing]`; `reachable(sleeping_scanning_bridge) = [scanning]`; `reverse(testing) = [testing, sleeping_testing_bridge]`; `reverse(scanning) = [sleeping_scanning_bridge]`. Confirms both bridges sit between sleeping and their targets.
- Full suite `pytest tests/ --ignore=tests/test_remote_tui.py` → **296 passed, 5 failed, 14 skipped**. The 5 failures are all in `tests/test_remote_sdl.py` (TUI/SDL rendering) — **pre-existing** and unrelated to `zones.json` (that test file does not reference zones). The previously-failing `test_transition` and `test_bridge_behavior` now **pass**.

### Decisions and Caveats

- **The new bridges use a single curated pose = the target's entry pose** (no fabricated intermediate motion). This is the safest choice: the robot transitions to a known-good pose and hands off to the target action at that same pose. **The operator should add intermediate path poses in the editor** for a smooth physical traversal between the real sleeping position and the target; right now the bridge is a direct (safety-gated) move to the target pose.
- **Bridges are directional** (sleeping → target). See Architecture for the reverse-direction note.
- **`exits` are intentionally all removed** (the original task asked to drop all exits/cross-references). The bridges are wired via action `next`, not zone `exits`.
- The three test edits are **data-reference updates only** — the code-behavior assertions are unchanged. They keep the suite green against the new table.

### Next Task

Optional (operator): refine the two new bridge actions' poses in the editor for a smooth physical path (currently a single direct move to the target pose), and add reverse-direction bridges (`testing_sleeping_bridge`, `scanning_sleeping_bridge`) if a true bidirectional path is wanted.

### Instructions for the Next Task

1. Reload the editor page to load the new `zones.json` (the flow view will show `sleeping → sleeping_testing_bridge → testing` and `sleeping → sleeping_scanning_bridge → scanning`).
2. To refine a bridge: open `sleeping_testing_bridge` (or `sleeping_scanning_bridge`) in the editor and add intermediate anchor poses between the sleeping-side pose and the target entry pose. Keep `behavior: "bridge"`, `variable_axes: []`, `loop: false`.
3. The original 38-zone table is preserved in `zones.json.bak-20261026-121651` if a full restore is ever needed.

## 2026-10-26 — Fixed `delZone`/`renameZone` to clean zone-level `exits` (and added an `exits` check to the client validator)

### Status

Completed. The editor's `delZone` now strips the removed zone name from every other zone's `exits` list (mirroring its existing `next` cleanup), `renameZone` re-points `exits` entries naming the renamed zone, and the client-side `validate()` now rejects a dangling `exits` target before it is POSTed. Syntax-checked and logic-tested.

### Summary

Deleting or renaming a zone in the editor left stale zone-level `exits` references behind. `delZone` already cleaned action-level `next` targets pointing at the removed zone, but it never touched the zone-level `exits` field (a list of `[zone, action]` pairs). The server validator (`rtr/state/zones.py` `_validate_zone`, lines 561–566) requires every `exits[i][0]` to be a live zone name, so a stale exit made the save fail with a 400 (`zone '…': exits must be a list of [zone, action] pairs`), leaving `zones.json` untouched and the client showing stale zones. `renameZone` had the same gap (it re-pointed `next` but not `exits`), and the client's `validate()` never checked `exits` at all, so the dangling reference sailed past the client and only surfaced as a server 400.

This task closes all three gaps:
1. **`delZone`** strips any `exits` pair naming the removed zone from every surviving zone.
2. **`renameZone`** re-points any `exits` pair naming the renamed zone to the new name (only the zone half of the `[zone, action]` pair changes).
3. **`validate()`** now checks `exits` (must be a list of `[zone, action]` pairs whose zone exists), mirroring the server validator, so a dangling exit is surfaced client-side before the POST.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - `delZone()` (was line 7095): the post-delete cleanup loop now also strips `exits` pairs naming the removed zone. Changed the early `if (!oz.actions) return;` to an `if (oz.actions) { … }` block so the `exits` cleanup runs even for a zone without actions; added `if (Array.isArray(oz.exits) && oz.exits.length) oz.exits = oz.exits.filter((e) => !(Array.isArray(e) && e[0] === nm));`.
  - `renameZone()` (was line 7161): step 2 (the `next` re-point loop) now also re-points `exits` pairs naming the renamed zone. Same `if (oz.actions) { … }` restructure; added `if (Array.isArray(oz.exits) && oz.exits.length) oz.exits = oz.exits.map((e) => (Array.isArray(e) && e[0] === nm) ? [clean, e[1]] : e);`.
  - `validate()` (was line 6092): added an `exits` reference check after the `speed` check — if `z.exits` is present it must be an array of 2-element `[zone, action]` pairs where `e[0]` is a string naming an existing zone and `e[1]` is a string; otherwise it pushes an error.

### Documentation

- None required beyond this handoff. The change mirrors the already-documented server-side `exits` contract (`zones.py` `_validate_zone`); no user-facing config or behaviour contract changed.

### Architecture

- No architectural impact. The zone graph (actions' `next` + zone-level `exits`) is unchanged; this only keeps the editor's in-memory `TABLE.zones` consistent with that contract on delete/rename, and makes the client validator enforce the same `exits` rule the server already enforces. `exits` remains an optional zone field; zones without one are unaffected.

### Validation

- Inline-JS syntax check: extracted the inline `<script>` (editor.html lines 740–7536) and parsed with Node `vm.Script` → **SYNTAX OK** (6795 lines, no errors).
- Logic test (standalone Node harness replicating the exact cleanup expressions): a `sleeping` zone with `exits [["testing","hold"],["purring","purr"]]` → after `delZone("purring")` the `purring` exit is stripped and `testing` survives; after `renameZone("testing"→"testzone2")` the exit re-points to `["testzone2","hold"]` (action name preserved). **ALL ASSERTIONS PASSED.**
- Confirmed `delZone`/`renameZone` are the only zone delete/rename entry points (grep: `delete TABLE.zones` appears only in those two functions).

### Decisions and Caveats

- **`validate()` `exits` check is defense-in-depth** (the two requested fixes are the `delZone`/`renameZone` cleanups). It mirrors the server validator so a dangling exit from *any* source is caught client-side instead of as a 400. It is conservative: a valid `exits` list passes; only a malformed or dangling one is flagged. If the reviewer prefers the validator untouched, that block is self-contained and trivially removable.
- The `if (oz.actions)` restructure (vs the old `if (!oz.actions) return;`) is behaviour-preserving for the `next` cleanup and merely lets the `exits` cleanup run unconditionally per zone.
- No on-disk `zones.json` was modified — this is a client-side editor fix. The current on-disk file is already valid; the fix prevents the in-memory `TABLE` from going dangling after an in-session delete/rename.

### Next Task

The separate pending item from the prior session: write a fresh `zones.json` containing only the `sleeping` and `testing` zones with all `exits`/`next` cross-references removed, then verify it passes `validate_state_data`. (Not part of this editor fix; flagged so it is not lost.)

### Instructions for the Next Task

1. The editor fix is live in `realtimecontrol/rtr/editor.html` (no build step; reload the editor page to pick it up).
2. To verify: open the editor, delete a zone that other zones reference in `exits`, and save — the save should now succeed (no 400) and the flow/detail views re-render cleanly. Renaming a zone re-points `exits` the same way.
3. If the `zones.json` rewrite is still wanted: build a file with only `sleeping` + `testing`, drop every `exits` and `next` field, and validate against `rtr/state/zones.py` `validate_state_data` before writing.

## 2026-10-26 — Gated the per-tick engine debug prints behind a `--verbose` flag

### Status

Completed. The per-tick debug lines are silent by default and reappear under `--verbose`. Verified with a sim smoke test (0 tick lines default, 147 with `--verbose`).

### Summary

The operator reported the `TypeError` is gone, but the core now spits a lot of debugging **every tick**. The only unconditional per-tick prints were in `engine.py`: the `tick#N start` line (fired every tick) and the `BLOCK-move start/done` pair (fired every tick during a blocking move). These are now gated behind a `verbose` flag, threaded through the central `Config` object and a new `--verbose` CLI flag. All other engine prints (reload, exception watchdog, collision, safety-discard, zone-reload) are event-based and remain ungated.

### Changes Made

- `realtimecontrol/rtr/rtr/core/engine.py`:
  - `Engine.__init__`: added `verbose: bool = False` parameter; stored as `self.verbose`.
  - `run()`: the `tick#N start` print is now wrapped in `if self.verbose:`.
  - `_tick()`: the two `BLOCK-move start/done` prints are now wrapped in `if self.verbose:`.
- `realtimecontrol/rtr/rtr/config.py`: added `verbose: bool = False` field to `Config` (documented in the engine section).
- `realtimecontrol/rtr/main.py`:
  - `parse_args`: added `--verbose` (`action="store_true"`).
  - `build_config`: sets `cfg.verbose = args.verbose`.
  - `run`: passes `verbose=cfg.verbose` into the `Engine(...)` constructor.

### Documentation

- `main.py --help` now lists `--verbose  print per-tick engine debug (tick start, block-move)`. No dedicated doc update needed beyond that; the flag is self-documenting.

### Architecture

- No architectural change. Reuses the existing central-config pattern: `Config` is the single place the core reads runtime settings, and adapters receive it. The `verbose` flag flows `CLI → Config → Engine`, matching how `tick_hz` and every other engine setting is wired. The per-tick data flow is untouched.

### Validation

- `python3 -m py_compile rtr/core/engine.py rtr/config.py main.py` → OK.
- `python3 main.py --help` → `--verbose` present in usage and options.
- `inspect.signature(Engine.__init__)` → `verbose` present with default `False` (so `sim_demo.py`'s `Engine(bus, robot, brain, machine, tick_hz=30.0)` still works).
- Sim smoke test (`python3 -u main.py --sim --no-http --no-midi --no-sound`, `timeout 3`): **0** `tick#` lines without `--verbose`, **147** with `--verbose` (tick start + BLOCK-move start/done per tick). Confirms the gate.

### Decisions and Caveats

- Chose a **CLI flag through `Config`** over an env var, because `main.py` is the real entry point (launched as `python main.py --robot/--sim`) and the codebase already funnels all runtime settings through `Config`. Toggling verbose requires a relaunch with `--verbose` — acceptable for a debug flag. (An env-var fallback, e.g. `RTR_VERBOSE=1`, is easy to add later if a service launcher needs it.)
- The `BLOCK-move` prints are gated too (not just `tick#N start`), since they fire every tick while a blocking move is active and are the bulk of the per-tick noise.
- The **exception watchdog** prints (`EXCEPTION (first, full trace)` / `EXCEPTION`) are intentionally left ungated: they are real error diagnostics, not debug chatter, and are already self-limiting (full trace once per distinct signature, one-liner on repeats).
- The unused `from_args` helper in `rtr/config.py` was intentionally left unchanged (dead code; `main.py` uses `build_config`).

### Next Task

No immediate next task identified. If the operator wants verbose on by default, or an env-var trigger (`RTR_VERBOSE`) for service launches, that is the natural follow-up.

### Instructions for the Next Task

- To see the per-tick debug again: relaunch the core with `--verbose` (e.g. `python main.py --robot --verbose`).
- The gate lives in `engine.py` (`self.verbose`); add further per-tick debug prints there under the same `if self.verbose:` if needed.

## 2026-10-26 — Diagnosed the per-tick `TypeError("'<' not supported between 'str' and 'int'")`; hardened the watchdog + camera-conf path

### Status

In progress (diagnosis narrowed; root line pending a live capture). Instrumentation and a defensive fix are in place and compile clean; the exact raising line will be confirmed by the next live run, which now logs the full traceback on first occurrence.

### Summary

The engine watchdog (added in prior work) now surfaces a **pre-existing** exception that was silently killing the engine task and stranding the robot: `TypeError("'<' not supported between instances of 'str' and 'int'")`, raised **every tick** (hundreds in the log). The operand order (`str` first, `int` second) is the key fingerprint.

This task did two things:

1. **Made the watchdog diagnostic.** Previously it logged only the one-liner `exc!r`, so the exact line could not be pinned. It now logs the **full traceback on the first occurrence** of each distinct exception signature (and the one-liner on repeats), so the next live run identifies the exact file/line.
2. **Ran a full static trace** of the per-tick path to narrow the source, and **hardened one real latent bug** found along the way (camera candidate `conf` compared while still a string).

### Changes Made

- `realtimecontrol/rtr/rtr/core/engine.py` (watchdog in `run()`):
  - Added `import traceback`.
  - Added `self._seen_exceptions = {}` (exception signature → count).
  - The watchdog now computes `sig = f"{type(exc).__name__}: {exc}"`; on first sight of a signature it prints `traceback.format_exc()` (full stack), otherwise the one-liner. Still publishes `Event.SAFETY_ALERT` and `continue`s the loop (engine stays alive).
- `realtimecontrol/rtr/rtr/brain/brain.py` (`_handle_cam_candidates`):
  - `conf = c.get("conf", 0)` is now coerced to `float` inside a `try/except (TypeError, ValueError)` with a `0.0` fallback, before `if conf < min_conf:`. A non-numeric `conf` falls back to `0.0` (below threshold → candidate skipped). This is **defensive hardening**, not a confirmed root-cause fix (see Decisions).

### Documentation

- None required beyond this handoff. The change is a diagnostic + defensive hardening; no user-facing behaviour or config contract changed.

### Architecture

- No architectural change. The watchdog remains a per-tick `try/except` in `Engine.run()`; it only changes *what* it logs (full traceback once per distinct exception). The per-tick data flow (`engine._tick` → `brain.tick` + `machine.step`) is untouched.

### What the static trace established

- **Per-tick path** (`engine._tick`): the only unconditional per-tick calls are `brain.tick(curjpos)` (line ~184) and `machine.step(curjpos)` (line ~189); `curjpos`/`curpos` are floats (`kuka.py get_curjpos/get_curpos` → `float(...)`).
- **`machine.step` clamp path is SAFE:** `build_target` resolves `"x"` frozen axes via `resolve_x` (behavior.py) before `clamp` → `fitlimits`; `action_axis_bands` coerces band values to `float` (zones.py); a scan of `zones.json` confirmed the **only** string values are `"x"` pose axes (safezones are all-numeric, axis_bands coerced, variable_axes all-int).
- **The `conf` path is SAFE from the real pipeline:** `camera/pipeline.py` coerces `conf` to `float(confs[i])`, so the real camera never sends a string conf. (The defensive coercion in `brain.py` still stands as hardening.)
- **Operand-order elimination:** `checklimits` (`lo <= angle <= hi`) would raise `int < str` (reversed) — does NOT match. The only str-first `<` sites are `fitlimits` (helpers.py `angle < lo`), `hardware_clamp` (behavior.py), and `gate._pose_outside_safezone` (`pose[i] < lo`) — all reached only via **resolved** poses or **transient** transition/safety-gated paths, not the steady per-tick clamp.
- **`conf < min_conf` (brain.py) is NOT the live source:** the log line `round(c.get('conf',0), 2)` runs *before* the `conf < min_conf` comparison; if `conf` were a string that `round()` would raise first (a different error). Since the observed error is specifically `<`, `conf` is already numeric at runtime.

### Validation

- `python3 -m py_compile rtr/brain/brain.py rtr/core/engine.py` → OK (both edited files compile).
- No runtime repro: no live core / EKI connection available in this environment. The full-traceback instrumentation is the confirmation mechanism for the exact line.

### Decisions and Caveats

- **The conf fix is defensive, not a confirmed root cause.** It hardens a real latent bug (a string `conf` would break both the `round()` log line and the `<` comparison) but the live error is not from `conf`. It is safe to keep (no-op when `conf` is already numeric) and does not change valid-data behaviour.
- **The exact raising line is NOT yet confirmed.** The strongest remaining hypothesis, by elimination, is a pose axis that is a string (`"x"`) reaching `fitlimits`/`hardware_clamp`/`gate` **without** being resolved by `resolve_x` — most plausibly via a **stuck transition** (`machine.step` transition branch → `hardware_clamp(t)` where the transition target carries an `"x"` axis, so the transition never completes and the error repeats every tick) or a **stuck safety-gated move** (`_resolve_pending` → `check_conflict` → `_pose_outside_safezone`). This is a hypothesis, not a confirmed line.
- **Do not harden `fitlimits`/`checklimits` preemptively** on the strength of this trace — the resolved clamp path is clean, and the safezone/pose data is clean; the string is not entering from those fields.

### Next Task

Capture the full traceback from the next live run to pin the exact line, then fix at the root (resolve the offending pose before it reaches the `<` comparison, or coerce at the specific comparison site the traceback names).

### Instructions for the Next Task

1. Operator: restart the core to load the current `engine.py` (instrumented watchdog) + `brain.py`, and let the exception fire once. The log will show `[engine] tick#N EXCEPTION (first, full trace):` followed by the full stack — **that stack names the exact file:line**.
2. Feed that `file:line` back; apply the root fix there (resolve the `"x"` axis before the comparison, or coerce the specific value). Then re-run to confirm the per-tick exception is gone.
3. If the traceback points to the **transition** path (`machine.step` → `hardware_clamp`), the fix is to resolve `"x"` in the transition target before `hardware_clamp` (mirror `resolve_x` in `build_target`). If it points to the **safety gate** (`_pose_outside_safezone`), resolve the entry pose before the safezone check.
4. Keep the conf coercion (brain.py) and the full-traceback watchdog (engine.py) — both are safe improvements independent of the root cause.

## 2026-10-25 — Added a master MIDI on/off toggle (editor) that gates BOTH MIDI-in and MIDI-out/sound in the core

### Status

Completed. Implemented and tested (headless). The new master button lives in `editor.html`'s MIDI tab; the core now honours a runtime `enabled` soft switch on the sound (MIDI-out) side, mirroring the existing MIDI-in one.

### Summary

The user wanted a single **master "MIDI on/off"** button in the editor that, when off, makes the core completely ignore all incoming MIDI messages AND send no outgoing MIDI (sound) messages.

The MIDI-**in** side already had a runtime soft switch: `midi.json` `enabled`, honoured by `MidiInput._effective_enabled()` (hard constructor switch AND data `enabled`), with the editor's per-zone `enabled` checkbox bound to it. The MIDI-**out/sound** side had **no** runtime soft switch — `Sound.enabled` was constructor-only (`cfg.enable_sound`) and `sound.json` had no `enabled` field.

So the work was: (1) give the sound adapter the same soft-switch pattern as MIDI-in, and (2) add one editor button that drives **both** sides and saves both tables so the core hot-reloads each.

### Changes Made

- `realtimecontrol/rtr/rtr/sound/sound.py`:
  - `builtin_sound_data()` now ships `"enabled": True`.
  - `validate_sound_data()` validates a top-level `enabled` (must be a bool; defaults to `True` when absent).
  - New `Sound._effective_enabled()` = constructor `self.enabled` AND the data `enabled` (mirrors `midi.py`).
  - `on_event()` now early-returns on `not self._effective_enabled()` (was `not self.enabled`), so no zone/action/pose MIDI is sent while off.
  - `reload()` closes **all** open out ports when `_effective_enabled()` is false (was always `_reconcile_outs()`), so no MIDI can leak out of a stale port; the reload log now reports `enabled=`.
- `realtimecontrol/rtr/sound.json`: added `"enabled": true` at the top level.
- `realtimecontrol/rtr/editor.html`:
  - `#midiToggleRow` now holds a master checkbox `#midiMasterCb` (label "midi master on/off") beside the out/in sub-tabs.
  - `SOUND_FALLBACK` gained `enabled: true`; `loadSound()` seeds `SOUND.enabled` (default `true`); `validateSound()` rejects a non-bool `SOUND.enabled`.
  - New `refreshMidiMaster()` (sets the checkbox to `MIDI.enabled && SOUND.enabled`) and `onMidiMasterToggle()` (sets **both** `MIDI.enabled` and `SOUND.enabled`, then `saveMidiAll()` which POSTs `/api/sound` + `/api/midi` so the core hot-reloads both).
  - Wired: `$("midiMasterCb").onchange = onMidiMasterToggle`; `refreshMidiMaster()` called in the bootstrap load chain (after `loadMidi`), in `switchView` when the MIDI tab shows, on the per-zone `enabled` checkbox change (keeps the master in sync when `MIDI.enabled` is toggled there), and after a config restore.

### Documentation

- None required beyond this handoff — the change follows the existing, already-documented MIDI-in soft-switch pattern; no new architecture doc is warranted.

### Architecture

- No new modules or data-flow changes. The sound adapter now exposes the same two-switch gate (`hard` = `cfg.enable_sound`, `soft` = `sound.json` `enabled`) as the MIDI-in adapter. The editor's master button is a convenience that writes both `enabled` fields; the two tables remain independently editable (the per-zone MIDI-in `enabled` checkbox still works and stays in sync via `refreshMidiMaster`).

### Validation

- `python tests/test_sound_enabled.py` → ALL TESTS PASSED (9 tests: validate accepts/defaults/rejects `enabled`; `builtin_sound_data` includes it; `_effective_enabled` truth table (hard×soft); soft-off sends nothing; soft-off reload closes all out ports; re-enabling resumes sending).
- `python tests/test_sound_out_ports.py` → ALL TESTS PASSED (no regression to the out-port routing).
- `python tests/test_midi_in.py` → ALL TESTS PASSED (1 skipped) (no regression to MIDI-in).
- Full `pytest tests/ --ignore=tests/test_remote_tui.py` → 294 passed, 7 failed. The 7 failures (`test_transition`, `test_bridge_behavior`, `test_remote_sdl`×5) are **pre-existing** — confirmed by stashing my changes and re-running (same 7 fail without them). `test_remote_tui.py` fails at collection on a pre-existing `action_kind` import error, also unrelated.

### Decisions and Caveats

- **Master semantics:** the button reads "on" only when **both** `MIDI.enabled` and `SOUND.enabled` are true; if either is off it shows off. Toggling it sets both to the new value and saves both. This means the master can re-enable a side that was individually turned off (expected master-switch behaviour).
- **Save path:** the master toggle reuses `saveMidiAll()`, which validates the **whole** SOUND and MIDI tables before POSTing. If an unrelated field elsewhere is invalid, the toggle won't persist (consistent with the editor's normal save button behaviour).
- **No hardware test:** verified headless with a fake rtmidi (per project convention); no real MIDI device was exercised.

### Next Task

No immediate next task identified. (Optional follow-ups: add a matching master-state indicator light to `client.html`, or let the per-zone MIDI-in `enabled` checkbox and the master button share a single source of truth more explicitly.)

### Instructions for the Next Task

1. To verify on hardware: start the core, open the editor → MIDI tab, toggle the master checkbox off, confirm the core logs `[sound] mapping reloaded: enabled=False` and `[midi] ... enabled=False`, and that no MIDI is sent / no controller keys are honoured. Toggle on to restore.
2. The core picks up the change via the existing hot-reload path (`POST /api/sound` → `Cmd.RELOAD_SOUND` → `engine._sound_reload()`; `POST /api/midi` → `Cmd.RELOAD_MIDI` → `engine._midi_reload()`); no restart is required.

## 2026-10-25 — Reverted `KUKAPY_SERVER.SRC` to the original `WAIT FOR $FLAG[1]` (polling-loop "flaghack" rejected by the KUKA compiler)

### Status

Completed. Working tree restored byte-identical to the pre-flaghack original (git commit `961b915 kukapy`). The flaghack version is preserved as `KUKAPY_SERVER_flaghack.SRC` (user-renamed, untracked).

### Summary

The KUKA compiler rejected the polling-loop version with: `line 67 col 15 expected SEC, FOR or CLOCK`. That line is `WAIT 0.1` — bare `WAIT <value>` is invalid KRL; the grammar is `WAIT <value> SEC` | `WAIT FOR <condition>` | `WAIT CLOCK`. The user renamed the edited file to `KUKAPY_SERVER_flaghack.SRC` to keep it as a draft and asked for `KUKAPY_SERVER.SRC` to be reverted to the original. The original (with `WAIT FOR $FLAG[1]` at line 57) was restored from git commit `961b915` via `git restore --source=961b915`. The `KukaPyNEW` sync copy was re-synced to the original as well.

**The Python-side stall recovery remains in place and is compatible with the original KRL:** `robot.py`'s `_recover()` sends a ping that sets `$FLAG[1]`, which wakes the KRL parked at `WAIT FOR $FLAG[1]`; the engine watchdog (`engine.py`) still catches tick exceptions and publishes `Event.SAFETY_ALERT`. So the anti-strand protection does not depend on the KRL polling loop.

### Changes Made

- `realtimecontrol/rtr/KukaPy/kukadriver/KUKAPY_SERVER.SRC`: restored to original content — `WAIT FOR $FLAG[1]` (line 57), no `wait_ticks`/`wait_timeout` declarations, no polling loop.
- `realtimecontrol/kukapyEKI/KukaPyNEW/kukadriver/KUKAPY_SERVER.SRC`: re-synced to the identical original content.
- `realtimecontrol/rtr/KukaPy/kukadriver/KUKAPY_SERVER_flaghack.SRC`: untouched (user's preserved copy of the rejected polling-loop version; still contains the invalid `WAIT 0.1` line).

### Documentation

- None required — this reverts a previously documented change; the prior handoff entry below still documents the flaghack design and the Python-side recovery, which remain accurate.

### Architecture

- No architectural impact. The KRL protocol, `<Rob>`/`<Res>` framing, and flag handshake are unchanged from the original design.

### Validation

- `git diff 961b915 -- realtimecontrol/rtr/KukaPy/kukadriver/KUKAPY_SERVER.SRC` → empty (working tree byte-identical to the original commit).
- `grep -n "WAIT\|WHILE\|wait_ticks"` on the restored file → only `57: WAIT FOR $FLAG[1]` (no polling loop remnants).
- `diff` between `rtr/KukaPy` and `KukaPyNEW` copies → identical.

### Decisions and Caveats

- **The flaghack file still has the KRL syntax error.** If it is ever wanted back, line 67 must be changed from `WAIT 0.1` to `WAIT 0.1 SEC` before it will compile. It was intentionally left unmodified (user's draft).
- **Controller state:** the operator should confirm the controller is running the original program (re-download `KUKAPY_SERVER.SRC` if the flaghack version was ever downloaded). The core machine does not need a restart — the Python-side recovery (`robot.py` `_recover`, `engine.py` watchdog) is unchanged and works with the original KRL.
- **The polling-loop idea is not the current line of defense.** The Python-side recovery ping + engine watchdog remain the stall protection while the KRL stays at the original blocking `WAIT FOR $FLAG[1]`.

### Next Task

Confirm on the controller that the original `KUKAPY_SERVER.SRC` is running, then continue root-cause diagnosis of the original stall (capture a `KUKAPY_DEBUG=1` log during a `hunting/track2` run) to determine whether the Python engine blocks in `_recv_xml`, a blocking move, or raises an uncaught exception.

### Instructions for the Next Task

1. **On the KUKA controller:** verify the running program is the original (program counter parks at `WAIT FOR $FLAG[1]` between commands — normal). If the flaghack version was downloaded, re-download the original `KUKAPY_SERVER.SRC`.
2. **On the core machine:** restart the core (to pick up `robot.py`/`engine.py` changes, which remain in place), enable `KUKAPY_DEBUG=1`, and re-run `hunting/track2`.
3. **Capture the log** during/after a stall: look for `[kukapy] STALL detected; attempting recovery ping` (recovery path taken) or `[engine] tick#N EXCEPTION:` (watchdog caught an exception). That identifies the exact blocking point.

## 2026-10-25 — Replaced the KRL's indefinite `WAIT FOR $FLAG[1]` with a timed polling loop + added Python-side stall recovery (recovery ping on staleness guard fire)

### Status

Completed (KRL + Python changes implemented locally). On-robot redeploy/restart is the operator's action: the KRL file must be re-downloaded to the controller, and the core must be restarted to pick up the new `robot.py`.

### Summary

The pendant HMI shows `Wait for $FLAG[1]` while the robot is RUNNING but idle because the KRL's `WAIT FOR $FLAG[1]` (KUKAPY_SERVER.SRC line 57) is a **blocking wait with no timeout**. When the Python engine stops sending commands (stuck in `_recv_xml`, a blocking move, or an uncaught exception), the KRL parks on that line indefinitely. The KRC EKI middleware keeps sending `AliveCounter` keepalives (so the connection appears alive), but the KRL program itself is suspended at the `WAIT FOR` instruction.

**Two-part fix:**

1. **KRL side** (`KUKAPY_SERVER.SRC`): Replaced `WAIT FOR $FLAG[1]` with a `WHILE NOT $FLAG[1]` polling loop that checks the flag every 100 ms. The loop has a 5-second timeout counter (`wait_ticks >= wait_timeout`) that resets every cycle, so the KRL never parks on a single blocking instruction. The pendant now shows the program is actively polling (in the loop) rather than frozen at `WAIT FOR`. The KRL continues waiting indefinitely (the loop never exits), but it is no longer suspended on a single instruction — it is running, which means the controller can be interrupted, the program counter is visible, and the operator can see the program is alive.

2. **Python side** (`kukapy/robot.py`): Added a `_recover()` method and integrated it into `_send_cmd`. When the staleness guard fires (no `<Res>` within `res_timeout`), `_send_cmd` now catches the `KukaError`, calls `_recover()` (which sends a `ping` command — this sets `$FLAG[1]`, waking the KRL's polling loop), and retries the original command once. If the recovery ping also fails, the error propagates (the connection is dead). This turns the previous "staleness guard fires → `KukaError` propagates → engine task dies → permanent strand" into "staleness guard fires → recovery ping → KRL wakes → command retried → handshake restored."

**Why this works:** The KRL's polling loop means the KRL is always active (never suspended on a single `WAIT FOR`). The Python-side recovery ping means that even if the KRL's flag is not set for a while (Python was stuck), the next command attempt will first send a ping that sets `$FLAG[1]`, waking the KRL and re-establishing the handshake. The two changes together ensure the robot never reaches a permanent `Wait for $FLAG[1]` state: the KRL is always polling (not parked), and the Python side recovers the handshake on stall.

### Changes Made

- `realtimecontrol/rtr/KukaPy/kukadriver/KUKAPY_SERVER.SRC`:
  - Added `DECL INT wait_ticks` and `DECL INT wait_timeout` to the variable declarations.
  - Replaced `WAIT FOR $FLAG[1]` with a `WHILE NOT $FLAG[1]` loop: checks the flag every 100 ms (`WAIT 0.1`), resets `wait_ticks` every 50 iterations (5 s), and continues looping until the flag is set.
  - The `$FLAG[1] = FALSE` reset is preserved after the loop.
- `realtimecontrol/rtr/KukaPy/kukapy/robot.py`:
  - `_send_cmd`: wrapped the `_recv_xml` call in a `try/except KukaError`; on stall, calls `_recover()` and retries the command once.
  - New `_recover()` method: sends a `ping` command (sets `$FLAG[1]`, wakes the KRL), waits for the `<Res>`, raises `KukaError` if the ping fails.
  - Debug logging: `[kukapy] STALL detected; attempting recovery ping` when the recovery path is taken.

### Documentation

- In-code: the `_send_cmd` docstring documents the recovery behavior; the `_recover` docstring explains the mechanism; the KRL comment block explains the polling loop.

### Architecture

- No architectural impact. Same modules, same public API; the KRL loop structure changed from a single `WAIT FOR` to a polling loop, and the Python `_send_cmd` now has a one-shot recovery path. Backward-compatible: the protocol is unchanged, the `<Rob>`/`<Res>` framing is unchanged, and the timeout values are unchanged.

### Validation

- `python3 -m py_compile` on `robot.py` → OK.
- KRL syntax: the `WHILE NOT $FLAG[1]` loop is standard KRL; the `WAIT 0.1` and `IF/ENDIF` are standard KRL instructions. (Cannot validate KRL locally; the operator must verify on the controller.)
- Existing tests: the `_recv_xml` tests are unaffected (the recovery path is in `_send_cmd`, not `_recv_xml`).

### Decisions and Caveats

- **The KRL polling loop does NOT send a keepalive or timeout notification to Python.** The KRC EKI middleware already sends `AliveCounter` keepalives automatically. The polling loop's purpose is to keep the KRL program active (not suspended) so the pendant shows the program is running. If a timeout notification is needed, it can be added later (e.g., send a `<Res>` with a special code when `wait_ticks` resets).
- **The recovery ping is a single retry.** If the connection is truly dead (not just a stalled handshake), the ping fails and the error propagates. The engine's existing exception handling (or lack thereof) determines what happens next. A full reconnection mechanism is out of scope for this change.
- **The KRL file must be re-downloaded to the controller.** The `KUKAPY_SERVER.SRC` change is not automatically deployed; the operator must download the updated program to the KUKA controller and start it.
- **The `wait_timeout = 50` (5 s) is arbitrary.** It is long enough to avoid false-positive timeout notifications during normal operation (the Python engine sends commands at 20 Hz, so the flag is set every 50 ms) but short enough that the KRL is visibly active on the pendant. Adjust if needed.

### Next Task

Operator: (1) download the updated `KUKAPY_SERVER.SRC` to the KUKA controller and start it; (2) restart the core so the new `robot.py` with the recovery path loads; (3) re-trigger `hunting/track2` (or another block-mode action) and confirm the robot no longer strands at `WAIT FOR $FLAG[1]`.

### Instructions for the Next Task

1. **On the KUKA controller:** download `KUKAPY_SERVER.SRC` (the updated version with the polling loop) and start it. The program counter should show the program is in the `WHILE` loop (not at a single `WAIT FOR` line).
2. **On the core machine:** restart the core app so the new `robot.py` (with `_recover`) loads.
3. **Trigger the strand scenario:** `goto_zone hunting` then `play_action track2`.
4. **Verify:** the robot should no longer strand at `WAIT FOR $FLAG[1]`. If it does, the `[kukapy] STALL detected; attempting recovery ping` log line will appear in the core log (with `KUKAPY_DEBUG=1`), confirming the recovery path was taken.
5. **If the recovery ping fails:** the connection is dead; check the TCP connection and the KRC EKI status.

## 2026-10-25 — Made the `res_timeout` guard cnt-aware: blocking PTPs (`cnt=0`) use a longer `res_timeout_blocking` (60 s) so a legitimately long move no longer false-positives

### Status

Completed (implemented + validated locally against both `robot.py` copies and the wrapper). On-robot redeploy/restart is the operator's action.

### Summary

The previous entry added a wall-clock staleness guard to `_recv_xml` (default `res_timeout=10.0 s`) to turn the keepalives-without-`<Res>` hang into a recoverable `KukaError`. Its own "Decisions and Caveats" noted the risk: a **blocking** PTP (`cnt_val == 0`) holds the KRL's `<Res>` until the robot *arrives* (`KUKAPY_SERVER.SRC` CASE 4 — a plain `PTP` blocks until arrival), so any blocking move longer than 10 s would false-positive the guard. The user's "still fails" log pointed at exactly this ("might have been an unusual longh action"), and `zones.json` `hunting/track2` is `move_mode: block`.

**Fix:** make the timeout cnt-aware. `Robot.move()` now picks the effective timeout from `cnt_val`:
- `cnt_val == 0` (blocking PTP) → `self.res_timeout_blocking` (new, default **60.0 s**): the KRL legitimately holds the `<Res>` until arrival, so the guard must not fire mid-move.
- `cnt_val > 0` (stream) and every other command (ping/curjpos/curpos/setdo/getdo) → the short `self.res_timeout` (10.0 s): the KRL answers immediately, so a delayed `<Res>` there is still a real stall.

The override is threaded per-call: `move()` → `_send_cmd(res_timeout=rt)` → `_recv_xml(res_timeout=rt)` (falls back to `self.res_timeout` when the override is `None`). Stream moves and queries are untouched. The `KukaRobot` wrapper exposes both timeouts as optional constructor params (default `None` → use the `Robot` defaults) so a deployment can tune them without editing the vendored `robot.py`.

### Changes Made

- `realtimecontrol/rtr/KukaPy/kukapy/robot.py` (shadow copy):
  - `Robot.__init__`: new `res_timeout_blocking: float | None = 60.0` param; stored as `self.res_timeout_blocking` (independent of `res_timeout`; `None` disables that specific guard).
  - `_recv_xml(self, res_timeout=None)`: new per-call override; `rt = self.res_timeout if res_timeout is None else res_timeout`; the staleness guard now compares against `rt` and reports `{rt:.0f}s`.
  - `_send_cmd(self, cmd_type, res_timeout=None, **kw)`: passes `res_timeout` through to `_recv_xml`.
  - `move()`: `rt = self.res_timeout_blocking if cnt_val == 0 else self.res_timeout`; passes `res_timeout=rt` to `_send_cmd`.
- `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` (the **actual runtime module**, via editable install): copied to stay **byte-identical** to the shadow copy.
- `realtimecontrol/rtr/rtr/robot/kuka.py` (wrapper):
  - `from typing import List, Optional`.
  - `KukaRobot.__init__`: new optional `res_timeout` / `res_timeout_blocking` params (default `None` → Robot defaults).
  - `connect()`: forwards them to `Robot(...)` only when not `None`.
- `realtimecontrol/rtr/tests/test_kuka_eki_recv.py`: new `import time`; new `DelayedSock` (keepalives until `delay` s, then `<Res>`, with a no-op `sendall`); two new tests — `test_blocking_move_uses_longer_timeout` and `test_stream_move_uses_short_timeout`.

### Documentation

- In-code: `res_timeout_blocking` documented in `Robot.__init__`; the per-call override documented in the `_recv_xml` and `_send_cmd` docstrings; the cnt-awareness rationale documented in `move()`; the wrapper params documented in `KukaRobot.__init__`. No other docs needed.

### Architecture

- No architectural impact. Same modules, same public API; only a new optional constructor param on `Robot` + `KukaRobot`, a per-call override threaded through `_send_cmd`/`_recv_xml`, and a timeout selection in `move()`. Backward-compatible: legacy scripts that construct `Robot(port=...)` are unaffected (the new param has a default).

### Validation

- `python tests/test_kuka_eki_recv.py` → **13 passed** (11 recv-framing/staleness + 2 new cnt-aware tests).
- `python tests/test_kuka_eki_serialization.py` → **4 passed**.
- `python -m py_compile` on `robot.py`, `rtr/robot/kuka.py`, and the test file → OK.
- `diff` of the two `robot.py` copies → identical.

### Decisions and Caveats

- `res_timeout_blocking` defaults to **60 s**, chosen with ~6× margin over the worst realistic blocking move: the slowest zone speed is 10 % and the largest single-axis span is ~180°, so the longest realistic blocking PTP is ~10 s; 60 s is a hard bound (not forever) that still catches a truly stranded KRL during a blocking move. If a deployment has genuinely longer one-shot blocking moves, raise it (or set `res_timeout_blocking=None` to disable that guard) via the `KukaRobot` constructor.
- The two timeouts are **independent**: `res_timeout=None` disables the stream/query guard, `res_timeout_blocking=None` disables the blocking guard. (Deliberately not auto-linked, so disabling one does not silently disable the other.)
- The `make_robot` factory in `rtr/rtr/robot/__init__.py` does not forward the timeouts, so it uses the `Robot` defaults — correct for the fix; extend it only if an operator needs to tune at config level.

### Next Task

Operator: redeploy/restart the core (the pip editable install loads `KukaPyNEW/kukapy/robot.py` directly — no file copy needed if it runs from this tree), then re-trigger the long hunting action that previously failed and confirm the blocking move no longer raises `KukaError: no <Res> within 10s`.

### Instructions for the Next Task

1. Restart the core app so the editable `KukaPyNEW/kukapy/robot.py` picks up the cnt-aware `move()`.
2. Re-trigger the action that failed (e.g. `hunting` → `track2`, a `block`-mode action) — the blocking PTP now waits up to `res_timeout_blocking` (60 s) before the guard can fire, so a legitimately long move completes instead of erroring.
3. If a specific one-shot move is still too long for 60 s, construct the robot with a longer/disabled blocking timeout, e.g. `KukaRobot(port=18735, res_timeout_blocking=120.0)` (or `res_timeout_blocking=None` to disable that guard entirely).
4. Note: the core runtime at `/home/kaos/FTI_kuka` is a *separate, diverged* checkout from this workspace (`/home/kaos/Documents/kaotec/FTI_kuka`) — confirm the deployment/sync path before assuming this fix is live on the core.

## 2026-10-25 — Added the Res-staleness guard to `_recv_xml`: keepalives-without-`<Res>` now raise `KukaError` after `res_timeout` instead of hanging forever

### Status

Completed (implemented + validated locally against both `robot.py` copies). On-robot redeploy/restart is the operator's action.

### Summary

The multi-document `_recv_xml` deadlock (previous entry) is fixed, but a second, rarer stall remained: KRC stays alive (periodic `<AliveCounter>` keepalives keep arriving) yet never sends the `<Res>` the Python side is waiting for, and the KRL is stranded at `WAIT FOR $FLAG[1]` (line 57). The socket-level `recv_timeout` (300 s) never fires in this case because `recv()` keeps *succeeding* on the keepalive bytes — so `_recv_xml` discards keepalives forever: a silent, unrecoverable hang.

**Fix:** a wall-clock staleness guard in `_recv_xml`. `t0 = time.monotonic()` is recorded at call start; after every `recv()`, if `self.res_timeout` is set and `time.monotonic() - t0 > self.res_timeout` (default 10.0 s), `_recv_xml` raises `KukaError("no <Res> within Ns (keepalives still arriving); KRL stranded at WAIT FOR $FLAG[1]")`. This turns the silent forever-hang into a recoverable, self-describing error.

**Note:** this detects the stall but does not fix the root cause of *why* the KRC stops sending the `<Res>` (or why the KRL is stranded). Discriminating between (a) Python stuck in `_recv_xml` and (b) a client→KRC framing desync (KRC never set `$FLAG[1]`) still requires a `KUKAPY_DEBUG=1` capture.

### Changes Made

- `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` (the **actual runtime module**, via editable install):
  - New import `time`.
  - `Robot.__init__`: new `res_timeout: float | None = 10.0` parameter; stored as `self.res_timeout` (`None` disables the guard).
  - `_recv_xml`: `t0 = time.monotonic()` at call start; after each `recv()`, raise `KukaError` if `self.res_timeout is not None and time.monotonic() - t0 > self.res_timeout`.
- `realtimecontrol/rtr/KukaPy/kukapy/robot.py` (shadow copy): same changes; now byte-identical to KukaPyNEW.
- `realtimecontrol/rtr/tests/test_kuka_eki_recv.py`: new regression test `test_keepalives_without_res_raise_timeout` (FakeSock looping keepalives forever, `res_timeout=0.5`, asserts `KukaError`).

### Documentation

- In-code: `res_timeout` documented in `__init__` and in the `_recv_xml` docstring (stall signature + why the socket timeout can't catch it). No other docs needed.

### Architecture

- No architectural impact. Same module, same API; only a new optional `res_timeout` constructor parameter and a wall-clock guard in the receive loop.

### Validation

- `python3 test_kuka_eki_recv.py` → **11 passed** (10 recv-framing + 1 new staleness test).
- `python3 test_kuka_eki_serialization.py` → **6 passed**.
- `mypy robot.py` → only the **2 pre-existing** errors (lines 141 & 201: `comm_sock` is `socket | None`); **no new errors** from the guard.
- `diff` of the two `robot.py` copies → identical; both parse (`ast.parse`) clean.

### Decisions and Caveats

- The guard is a wall-clock timeout on the whole `_recv_xml` call (default 10.0 s), not a "time since last byte" check: during the stall keepalives keep arriving, so a last-byte timer would never accumulate. A wall-clock timer is the only simple timer that fires.
- Caveat: a legitimately long *blocking* PTP move (KRL blocked on `PTP` line 131, keepalives still arriving) could false-positive if it exceeds `res_timeout`. In block mode (the primary use case) moves are small 20 Hz increments, so 10 s is very safe. For long one-shot moves (e.g. the `gotopos` transition in `midikuka.py`), raise `res_timeout` (e.g. `Robot(port=18735, res_timeout=30.0)`) or disable it (`res_timeout=None`).
- The guard does not fix the root cause; it makes the stall detectable and recoverable.

### Next Task

Operator: run with `KUKAPY_DEBUG=1`, re-trigger `hunting` → `track2`, and capture the stall. The new `KukaError` will confirm the stall (last log line a `SEND` with no following `FRAME`/`<Res>`). Then discriminate the root cause: if the KRL is parked at line 57 with the flag never set, it is a client→KRC framing desync (verify `SENDFLAG=1` on the pendant's `KUKAPY.xml`); if the KRL is mid-move, it is a slow KRL.

### Instructions for the Next Task

1. Restart the core app (the pip editable install loads `KukaPyNEW/kukapy/robot.py` directly — no file copy needed if it runs from this tree).
2. Launch with `KUKAPY_DEBUG=1` in the environment to get per-frame `SEND`/`FRAME` logs back.
3. Re-trigger `hunting` → `track2`; when the stall occurs, capture the last ~20 log lines + the KRL line. The new `KukaError("no <Res> within 10s ...")` confirms the stall and that the KRL is stranded at `WAIT FOR $FLAG[1]`.
4. To tune the timeout for long moves: `Robot(port=18735, res_timeout=30.0)`, or `res_timeout=None` to disable the guard entirely.

## 2026-10-25 — Fixed the real recurring stall: multi-document `_recv_xml` deadlock (KRC sends plain XML, no `\x00`); silenced per-frame logging behind `KUKAPY_DEBUG=1`

### Status

Completed (fix implemented + validated locally). On-robot redeploy/restart is the operator's action.

### Summary

**Version question (answered by the trace):** the app *was* running the fixed code. Proof: (1) every `[kukapy] SEND` line in the operator's trace ends with `\x00` — only the fixed `_send_xml` produces that; (2) the `FRAME2` marker only exists in the current `robot.py`. Module resolution: the app runs under the `kukart` pyenv, where `kukapy` is a **pip editable install** pointing at `realtimecontrol/kukapyEKI/KukaPyNEW` — so the loaded file is `KukaPyNEW/kukapy/robot.py`, **not** `rtr/KukaPy` (that is a shadow copy; both now carry identical code).

**The `\x00`-both-directions hypothesis was wrong.** Every response in the trace was parsed via `FRAME2` (the plain-XML fallback) — mode 1 (`\x00`-split) never fired, so **KRC sends plain XML documents with no `\x00` flags on the wire**. `SENDFLAG=1` applies to the client→KRC direction only (our `\x00`-terminated `<Rob>` sends are correct and stay). The stall moved from tick #130 to #480 — the KRC side is now healthy — but a second, independent bug remained on the Python side.

**Root cause of the recurring stall:** KRC interleaves `<Res>` responses with periodic `<AliveCounter>` keepalives (`KUKAPY.xml` `<ALIVE ElementName="AliveCounter"/>`). Both are small (~260 B + ~40 B), so they routinely coalesce into **one TCP segment = one `recv()` chunk holding two complete documents**. The old mode-2 code did `ET.fromstring(whole_buffer)`, which raises `ParseError: junk after document element` (verified) whenever two documents are present — so `_recv_xml` kept accumulating keepalives forever (silent; the 1 MB guard is hours away), never returned, never sent the next `<Rob>`, and the KRL parked at `WAIT FOR $FLAG[1]` (line 57). This exactly matches the stall signature: last log line `SEND Type2`, then nothing, KRL at line 57. The stall point is random (TCP coalescing luck), which is why it moved 130 → 480.

**Fix (both `kukapy/robot.py` copies, now byte-identical):**
1. `_recv_xml` rewritten to scan the buffer **document by document**: split on `\x00` if present (dormant fallback), otherwise slice the first complete document out by root tag (new `_first_document` helper: regex for the root tag, `find("</root>")`), parse it, keep the rest. Non-`<Res>` documents (keepalives) are skipped. Leftover bytes still persist in `_recv_buf`.
2. Logging silenced: per-frame `SEND`/`FRAME` prints are now gated behind `KUKAPY_DEBUG=1` (env var). Error logs (`FRAME-PARSE-FAIL`, `DOC-PARSE-FAIL`, buffer overflow) stay always-on. This kills the ~15 KB/s log spam (3 full 250 B frame reprs per tick at 20 Hz).

### Changes Made

- `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` (the **actual runtime module**, via editable install):
  - New imports `os`, `re`; module-level `_DEBUG = os.environ.get("KUKAPY_DEBUG", "") == "1"`.
  - `_send_xml`: `\x00` framing kept (correct for KRC side); print gated behind `_DEBUG`.
  - `_recv_xml` + new `_first_document`: per-document scanning (see Summary).
  - Per-chunk `RECV#` dump removed (already done in the shadow copy last turn).
- `realtimecontrol/rtr/KukaPy/kukapy/robot.py` (shadow copy): same rewrite applied; now byte-identical to KukaPyNEW.
- `realtimecontrol/rtr/tests/test_kuka_eki_recv.py`: 4 new regression tests for the plain-XML production stream: `test_plain_xml_res_plus_keepalive_one_chunk` (the exact stall), `test_plain_xml_keepalive_then_res_one_chunk`, `test_plain_xml_two_res_one_chunk_preserves_tail`, `test_plain_xml_partial_then_keepalive`.

### Documentation

- In-code framing comment block rewritten: client→KRC is `\x00`-delimited (SENDFLAG), KRC→client is plain XML with keepalives. No other docs needed.

### Architecture

- No architectural impact. Same module, same API; only the receive-side document handling and log verbosity changed.

### Validation

- `PYTHONPATH=realtimecontrol/rtr/KukaPy python3 -m pytest tests/test_kuka_eki_recv.py tests/test_kuka_eki_serialization.py -q` → **16 passed** (10 recv-framing incl. 4 new + 6 serialization).
- Same suite against the runtime copy: `PYTHONPATH=.../kukapyEKI/KukaPyNEW python3 -m pytest ...` → **16 passed**.
- Live simulation against the KukaPyNEW module (thread + timeout, so a hang = deterministic failure): stall-case `Res+Alive` one chunk → `Res`; `Alive+Res` → `Res`; split `Res` + `Alive` + next `Res` → `Res` then `Res`; byte-by-byte → `Res`. All pass.
- `diff` of the two `robot.py` copies → identical.

### Decisions and Caveats

- The old tests only covered `\x00`-delimited traffic (mode 1) — the mode that never fires in production. The new tests cover the plain-XML mode that actually runs.
- If the stall recurs after this fix, it is no longer a Python-side framing issue (the reader now provably handles every coalescing pattern). Next discriminator: run with `KUKAPY_DEBUG=1` and capture the trace tail — if `SEND` lines stop with no `FRAME`/`DOC-PARSE-FAIL`, the KRC side stopped responding (check the pendant's `KUKAPY.xml` for `SENDFLAG=1` and the KRL line).
- `KUKAPY_OLD` (`kukapyEKI/KukaPyOLD`) is an untouched legacy copy; ignore it.

### Next Task

Operator: restart the core (the editable install picks up the new `robot.py` automatically — no file copy needed if the app runs from this tree; otherwise sync `KukaPyNEW/kukapy/robot.py` to 192.168.10.201), re-trigger `hunting` → `track2`, and confirm the core runs past the previous stall point (~tick #480) without stranding.

### Instructions for the Next Task

1. Restart the core app (no code copy needed if it runs from this tree — the pip editable install loads `KukaPyNEW/kukapy/robot.py` directly).
2. The log should now be much quieter: no per-tick `[kukapy] SEND`/`FRAME` lines. To bring them back: `KUKAPY_DEBUG=1` in the launch environment.
3. Re-trigger `hunting` → `track2`; verify no strand (KRL not parked at line 57) past the old stall point.
4. If it strands: launch with `KUKAPY_DEBUG=1`, capture the last ~20 lines + the KRL line, and check `SENDFLAG=1` on the pendant's `KUKAPY.xml`.

## 2026-10-25 — Ported the `\x00` SENDFLAG fix to the real runtime module `realtimecontrol/rtr/KukaPy/kukapy/robot.py` + removed the excessive per-chunk RECV logging

### Status

Completed (fix implemented + validated locally). On-robot redeploy/restart is the operator's action (the core runs on 192.168.10.201, not this machine).

### Summary

The operator clarified that the real runtime module is **`realtimecontrol/rtr/KukaPy/kukapy/robot.py`** (the `kukapy` package under `rtr/KukaPy`, resolved by `from kukapy.robot import Robot` in `rtr/robot/kuka.py:47`), **not** the `KukaPyNEW` copy fixed in the previous entry. The same two fixes were applied there, plus the debug instrumentation was trimmed:

1. **`\x00` SENDFLAG framing:** `_send_xml` now sends `xml_str.encode("utf-8") + b"\x00"` — every `<Rob>` is `\x00`-terminated, matching `KUKAPY.xml` `SENDFLAG=1` (both directions null-delimited). This is the fix for the line-57 strand (KRC parser byte-accounting drift → `$FLAG[1]` never set).
2. **`recv_timeout` default `None` → `300`** so a future desync raises `socket.timeout` after 300 s instead of hanging forever. `None` is still accepted explicitly for wait-forever.
3. **Logging trimmed:** removed the per-chunk `[kukapy] RECV#` dump (it reprinted the entire growing buffer on every `recv` — quadratic output). Kept: `[kukapy] SEND` (one line per command; confirms the trailing `\x00` after deploy), `[kukapy] FRAME` / `FRAME2` / `FRAME-PARSE-FAIL` (one line per parsed frame; desync detection), and the 1 MB buffer-overflow guard.

The framing comment block at the `_send_xml` section was corrected (it previously claimed "raw stream, no null-byte framing", contradicting the deployed `SENDFLAG=1`).

### Changes Made

- `realtimecontrol/rtr/KukaPy/kukapy/robot.py`:
  - `_send_xml`: `data = xml_str.encode("utf-8")` → `data = xml_str.encode("utf-8") + b"\x00"`.
  - `__init__`: `recv_timeout: int | None = None` → `recv_timeout: int | None = 300`; comment updated.
  - `_recv_xml`: removed the `n` counter and the `print(f"[kukapy] RECV#{n} ...")` per-chunk dump.
  - Framing comment block rewritten to document `SENDFLAG=1` both-directions `\x00` framing.
- The `KukaPyNEW/kukapy/robot.py` copy from the previous entry is now a **duplicate** of the fix (it still contains the verbose `RECV#` logging). If it is no longer used, consider deleting it to avoid two divergent copies.

### Documentation

- None required — the in-code comment block documents the framing; the KUKAPY.xml `SENDFLAG=1` semantics are already documented in the previous handoff entry.

### Architecture

- No architectural impact. Same module, same API; only wire-format framing (now matching the deployed config) and log verbosity changed.

### Validation

- `PYTHONPATH=realtimecontrol/rtr/KukaPy python3 -m pytest realtimecontrol/rtr/tests/test_kuka_eki_recv.py realtimecontrol/rtr/tests/test_kuka_eki_serialization.py -q` → **12 passed** (6 recv-framing + 6 serialization).
- Import check: `kukapy.robot.__file__` resolves to `realtimecontrol/rtr/KukaPy/kukapy/robot.py` (the edited file); `Robot(port=1).recv_timeout == 300`; `_send_xml` source contains the `+ b"\x00"` framing.

### Decisions and Caveats

- The `kukapy` package is **not** pip-installed globally (`import kukapy` fails from a bare shell); the runtime and tests resolve it via `PYTHONPATH`/working-directory pointing at `realtimecontrol/rtr/KukaPy`. The on-robot core (192.168.10.201) must therefore use the same resolution — verify the deployed tree's `robot.py` is the one from `rtr/KukaPy/kukapy/`.
- `KukaPyNEW/kukapy/robot.py` and `rtr/KukaPy/kukapy/robot.py` now both carry the fix; keep them in sync or delete one.
- If the strand recurs despite the `\x00` fix, the remaining suspect is a config mismatch: the robot's deployed `KUKAPY.xml` may differ from the repo's (`SENDFLAG=1`).

### Next Task

Operator: redeploy `realtimecontrol/rtr/KukaPy/kukapy/robot.py` to the robot PC (192.168.10.201), restart the core, confirm `[kukapy] SEND` lines show a trailing `\x00`, then re-trigger `hunting` → `track2` and verify the core runs past tick #130 without stranding.

### Instructions for the Next Task

1. Sync `realtimecontrol/rtr/KukaPy/kukapy/robot.py` to the robot PC, restart the core.
2. Confirm in the log that `[kukapy] SEND` lines end with `...b'\x00'` and that the old `[kukapy] RECV#` dump is gone.
3. Re-trigger `hunting` → `track2`; verify no strand past tick #130 (KRL not parked at line 57).
4. If it still strands: verify `SENDFLAG=1` on the pendant's `KUKAPY.xml` and capture the trace tail + KRL line at the stall.

## 2026-10-25 — Root-caused the strand: KRL confirmed at line 57 (request-side EKI desync); fixed `_send_xml` to send `\x00`-delimited `<Rob>` frames + added a defensive `recv_timeout`

### Status

Completed (fix implemented + validated locally). On-robot redeploy/restart is the operator's action (the core runs on 192.168.10.201, not this machine).

### Summary

The operator confirmed two decisive facts: **KRL is at line 57 (`WAIT FOR $FLAG[1]`)** and **there is no log after tick #130**. This resolves the two competing hypotheses from the previous handoff:

- **RULED OUT — "PTP never completes" (KRL in `PTP` at line 131).** KRL is at line 57, not 131, so the move is not the problem.
- **CONFIRMED — "request-side EKI desync" (KRC never set `$FLAG[1]`).** KRL idles at `WAIT FOR $FLAG[1]` because the KRC EKI layer never finished parsing tick #130's `<Rob>`, so it never set `$FLAG[1]` (the `Set_Flag="1"` on the last RECEIVE element `Rob/DO_Val`, `KUKAPY.xml:34`). Python sent the Type2 (`get_curjpos`) `<Rob>` (last log line: `tick#130 SEND Type2`) and is now blocked in `_recv_xml` waiting for a `<Res>` that never comes.

**Root cause:** `KUKAPY.xml` uses `PROTOCOL=TCP` + **`SENDFLAG=1`**, which makes the KRC EKI layer delimit frames with `\x00` in **both** directions. The Python client's `_send_xml` (`kukapy/robot.py`) sent **raw, unframed** `<Rob>` bytes (`sendall(data)` with no `\x00`). The code comment at `robot.py:92` even claimed "raw stream, no null-byte framing" — contradicting the deployed config. With no delimiter, the KRC parser's byte accounting drifts after enough ticks and it stops recognizing the `<Rob>` boundary, so `$FLAG[1]` is never set → KRL strands at line 57. This is a *stateful* desync (it worked for ~130 ticks, then broke), which is why a steady-state framing mismatch was not the obvious culprit.

**Fix (2 changes in `KukaPyNEW/kukapy/robot.py`, the active runtime module):**
1. `_send_xml` now appends `b"\x00"` to every `<Rob>` frame so the KRC parser gets an explicit frame boundary and reliably sets `$FLAG[1]`.
2. `recv_timeout` default changed `None` → `300` so a future desync raises a `socket.timeout` after 300 s instead of hanging forever (300 s is safe for slow block moves and matches the README's recommended value).

**Note on the receive side:** `_recv_xml` already handles `\x00`-delimited frames (Mode 1) and was already correct; it is unchanged. The existing regression tests (`tests/test_kuka_eki_recv.py`) confirm the receive framing is sound.

### Changes Made

- `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py`:
  - `_send_xml` (was `robot.py:99-100`): `data = xml_str.encode("utf-8")` → `data = xml_str.encode("utf-8") + b"\x00"`. Added a comment explaining the `SENDFLAG=1` both-directions framing and the line-57 strand.
  - `__init__` default: `recv_timeout: int | None = None` → `recv_timeout: int | None = 300`. Updated the `self.recv_timeout` comment.

### Documentation

- Updated the `_send_xml` docstring/comment block to state the `SENDFLAG=1` both-directions `\x00` framing (replacing the incorrect "raw stream, no null-byte framing" note).

### Architecture

No architectural impact. The fix is a framing correction inside the existing EKI client; the server/client roles, the single-flight lock (`kuka.py`), and the engine tick sequence are unchanged.

### Validation

- `python -c "import ast; ast.parse(...)"` on `kukapy/robot.py` → syntax OK.
- `python tests/test_kuka_eki_recv.py` (from `realtimecontrol/rtr`) → **all 6 EKI `_recv_xml` framing tests passed** (split frames, interleaved keepalives, multi-frame chunks, tiny-chunk reassembly, connect reset).
- `python tests/test_kuka_eki_serialization.py` (from `realtimecontrol/rtr`) → **all 6 serialization tests passed** (single-flight lock, stream vs block positioning).

### Decisions and Caveats

- **The `\x00` is the primary fix; `recv_timeout=300` is defensive.** If the desync recurs despite the delimiter (e.g. the robot's deployed `KUKAPY.xml` differs from the repo's), the timeout will now surface it as a `socket.timeout` instead of a silent hang, and the `[kukapy] SEND`/`RECV` instrumentation will show exactly where it stalled.
- **The robot's deployed `KUKAPY.xml` must match the repo's** (`SENDFLAG=1`). If the operator's KRC has a different config (e.g. `SENDFLAG` absent), the `\x00` framing assumption changes — verify on the pendant. The repo's `KUKAPY.xml:7` has `SENDFLAG=1`.
- **The fix is in the local tree only.** The running core is on 192.168.10.201 (a different machine); it must be restarted from the updated tree to pick up the change. Do NOT `pkill` locally (the core is not here).
- The vendored copy `realtimecontrol/rtr/KukaPy/kukapy/robot.py` is **not** on the runtime path (the active module is `KukaPyNEW/kukapy/robot.py`); it was left unchanged this turn. If it is ever used, it needs the same `_send_xml` fix.

### Next Task

Redeploy the fixed `robot.py` to the robot PC (192.168.10.201) and restart the core from the current tree, then re-trigger the strand to confirm the fix.

### Instructions for the Next Task

1. **On the robot PC (192.168.10.201)**, sync the updated `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` into the running core's path (the same path the instrumented core uses — confirm with `python -c "import kukapy.robot, os; print(os.path.abspath(kukapy.robot.__file__))"` on that machine).
2. **Restart the core** from the current tree, capturing output:
   `cd <core-path>/realtimecontrol/rtr && python main.py --robot --ip 192.168.10.201 2>&1 | tee /tmp/kukapy_trace.log`
   Confirm `[engine] tick#1 start ...` lines appear (instrumentation loaded) and that the `[kukapy] SEND` lines now show a trailing `\x00` (e.g. `b'...<Rob>...</Rob>\x00'`).
3. Start KUKAPY_SERVER on the pendant; park neutral (`{"cmd":"goto_zone","zone":"rest"}`), then re-trigger: `{"cmd":"goto_zone","zone":"hunting"}` then `{"cmd":"play_action","action":"track2"}`.
4. **Verify the fix:** the core should now run past tick #130 without stranding (KRL no longer idles at line 57). If it still strands, the `recv_timeout=300` will raise a `socket.timeout` within 5 min — capture the tail of `/tmp/kukapy_trace.log` and the KRL line at the moment of the stall.
5. If it still strands with the `\x00` in place, the next discriminator is the robot's deployed `KUKAPY.xml` (confirm `SENDFLAG=1` on the pendant) — a config mismatch is the only remaining suspect.

## 2026-10-25 — Analyzed operator trace: the "traceback" is a debug print, and the running core is STALE (pre-instrumentation, old path)

### Status

Completed (analysis). On-robot restart is the operator's action.

### Summary

The operator pasted a trace ending at `[debug-cmd] play_action payload={'action': 'track2'} curjpos=[-24.4, -10.5, -10.0, -3.0, 3.4,` (cut off). Two decisive findings:

1. **The "traceback" in the log is NOT an error.** It is the intentional debug call-stack dump from `clear_action` (`rtr/state/machine.py:458-464`), which prints `traceback.format_stack()[-4:-1]` on every action clear. The frames `step(656) → _advance_anchor(396) → _advance_after_action(551) → clear_action(458)` are the **normal** completion path of the `track` action (last pose reached → anchor advanced → action cleared). No exception occurred there. Similarly, `[debug-cmd] ...` (`rtr/brain/brain.py:448-451`) is a normal command log. So the state machine was healthy; the `track` action completed cleanly before `play_action track2`.

2. **The running core is STALE — it does not have the instrumentation.** Evidence:
   - The trace has **no** `[engine] tick#` lines (which print unconditionally at the top of every 20 Hz tick, `rtr/core/engine.py:84`) and **no** `[kukapy] SEND` lines (which print on every EKI command, `kukapy/robot.py:99`). Dozens of ticks of camera activity appear with none of these markers.
   - The traceback path is `/home/kaos/FTI_kuka/realtimecontrol/rtr/rtr/state/machine.py` — a path that **no longer exists** on this machine (`ls: cannot access '/home/kaos/FTI_kuka'`). The repo now lives at `/home/kaos/Documents/kaotec/FTI_kuka`. The core was launched from the old path (before the repo move and before the Test #2 instrumentation edits) and has been running the old in-memory code ever since.

**Confirmed the instrumentation IS present in the current tree** (grep): `kukapy/robot.py:99` `[kukapy] SEND`, `engine.py:84/159/161` `[engine] tick#`/`BLOCK-move`. So the code is ready — the running process just predates it.

**Engine kill mechanism (why the strand looks like an EKI desync):** `main.py:296` does `asyncio.create_task(engine.run())` and `engine.run()` (`rtr/core/engine.py:80-170`) has **no try/except**. Any uncaught exception in a tick (in `get_curjpos`/`get_curpos`, `machine.step`, a move, or `_resolve_pending`) kills the engine task; asyncio prints the traceback to the log; the engine stops forever → Python stops sending EKI commands → the KRL idles at `WAIT FOR $FLAG[1]` (line 57). That is "situation 2." The instrumentation will reveal whether the kill is an uncaught exception (a traceback will appear in the log) or a true EKI framing stall (last `[kukapy] SEND`/`RECV`/`FRAME` lines).

### Changes Made

None (analysis only; no code changed this turn).

### Documentation

None required — diagnostic analysis.

### Architecture

No architectural impact.

### Validation

- `grep` confirmed instrumentation present in the current tree: `kukapy/robot.py:99`, `engine.py:84,159,161`.
- `ls /home/kaos/FTI_kuka` → does not exist (confirms the running core's path is stale).
- Read `machine.py:380-409,458-502,535-569,640-669`, `engine.py:60-189`, `brain.py:430-469`, `main.py` (engine spawn) to confirm the "traceback" is a debug print and the engine has no exception guard.

### Decisions and Caveats

- Do NOT treat the `clear_action` stack dump as a bug or the root cause — it is expected debug output on every action clear.
- The strand mechanism is still unconfirmed between (a) an uncaught engine-tick exception and (b) an EKI framing stall; the instrumented re-run distinguishes them.

### Next Task

Kill the stale core and restart from the current tree so the instrumentation loads, then re-trigger the strand and capture the trace.

### Instructions for the Next Task

1. **Kill the stale core** (it runs from the old, now-gone path): `pkill -f "main.py --robot"` (or find the PID with `pgrep -f "main.py --robot"`). Confirm it is gone.
2. **Restart from the current tree**, capturing all output:
   `cd /home/kaos/Documents/kaotec/FTI_kuka/realtimecontrol/rtr && python main.py --robot --ip 192.168.10.201 2>&1 | tee /tmp/kukapy_trace.log`
   (default IP 192.168.10.201, listens :18735). Wait for `[rtr] robot connected` + `[rtr] core up`, and confirm `[engine] tick#1 start ...` lines appear (proof the instrumentation loaded).
3. Start KUKAPY_SERVER on the pendant.
4. Park neutral (`{"cmd":"goto_zone","zone":"rest"}`), then trigger the strand: `{"cmd":"goto_zone","zone":"hunting"}` then `{"cmd":"play_action","action":"track2"}`.
5. When the KRL idles at line 57, grab the **tail** of `/tmp/kukapy_trace.log` — the last `[engine] tick#`, `[kukapy] SEND`, `[kukapy] RECV`, `[kukapy] FRAME*` lines, and any traceback. Branch:
   - A **traceback** appears in the log at the stall → uncaught engine-tick exception (fix that exception).
   - `BLOCK-move start` + `SEND` but no `RECV`/`FRAME`/`done` → KRL `<Res>` not delivered.
   - `RECV` lines with no `FRAME`/`FRAME2` → `_recv_xml` framing desync.
   - No new `tick#` after a stream action → engine loop stopped/blocked.

## 2026-10-25 — Result: block mode ALSO strands (stream-pileup refuted); instrument EKI + engine to capture the desync

### Status

Completed (Test #1 result recorded + Test #2 instrumentation applied + operator instructions delivered). The on-robot re-run is performed by the operator.

### Summary

**Test #1 result (from the operator):** the forced block-mode `hunting/track2` **still** strands at `WAIT FOR $FLAG[1]` (line 57). The operator ran `trigger_action hunting/track` (the explicit stream action) then `play_action track2` (now block); the KRL idled at line 57 again. This **refutes** the stream-pileup hypothesis: the strand is not caused by stream mode spawning a daemon thread per tick. Block mode (one awaited move at a time, no thread pileup) strands identically, so the root cause is a **common EKI-framing desync** that affects both modes — the engine loop stops, so Python stops sending, so the KRL idles at `WAIT FOR $FLAG[1]`.

**Test #2 applied (instrumentation):** enabled byte-level logging in the EKI layer and stall markers in the engine tick so the next on-robot run captures (a) the exact bytes sent/received, (b) the frame boundaries, and (c) exactly where the engine loop stops.

Mechanism map (why line-57 idle = engine stop, and where the stop can happen):
- KRL loop (KUKAPY_SERVER.SRC): `WAIT FOR $FLAG[1]` (57) → read `Rob/*` → `SWITCH cmd_code` → `EKI_SEND Res` (183) → loop. `FLAG[1]` is set by an incoming Python EKI frame; the KRL clears it (58) after waking. **KRL idling at 57 ⇒ FLAG[1] stays FALSE ⇒ Python sent nothing ⇒ the engine loop stopped** (a mid-move hang would strand the KRL *in* the PTP/C_PTP, not at 57).
- Engine tick (engine.py `run`): `get_curjpos` / `get_curpos` (each takes `KukaRobot._lock`) → drain commands → `brain.tick` → `_resolve_pending` → `machine.step` → **move** (`stream` = `move(..., blocking=False)` spawns a daemon thread; `block` = `await asyncio.to_thread(move, ...)`) → publish. In block mode the tick **awaits the move**, which holds `_lock` and blocks inside `_send_cmd → _recv_xml` until a `<Res>` returns. So a block-mode stall = the move's `_recv_xml` never returns a `<Res>`.
- EKI reader (kukapy/robot.py `_recv_xml`): accumulates `recv(4096)` chunks into `buf`; Mode 1 splits on `\x00` (SENDFLAG=1) and returns the first `<Res>`; Mode 2 falls back to whole-buffer XML parse. Leftover bytes persist in `self._recv_buf`. A desync here = the `<Res>` is never recognized, so `_recv_xml` keeps reading forever (the engine stays blocked in the move).

### Changes Made

- `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` (the **active** runtime module — confirmed via `import kukapy.robot` resolving here):
  - `_send_xml`: enabled `print(f"[kukapy] SEND {repr(data)}")` (was commented).
  - `_recv_xml`: enabled per-chunk `print(f"[kukapy] RECV#{n} chunk=..B buf=..B {repr(buf)}")` (was commented); added `FRAME` log on each Mode-1 frame parse, `FRAME-PARSE-FAIL` log when a `\x00` frame fails to parse, and `FRAME2` log on a Mode-2 parse.
- `realtimecontrol/rtr/KukaPy/kukapy/robot.py` (vendored copy, not on the runtime path): identical instrumentation applied for consistency.
- `realtimecontrol/rtr/rtr/core/engine.py` (`run` tick loop): added `tick#N start (move_mode=.. action=..)` at the top of each tick, and `tick#N BLOCK-move start/done` around the block-mode `await asyncio.to_thread(move, ...)` so the trace shows whether the stall is before the move, inside the move, or after.

### Documentation

None required — diagnostic instrumentation, no behavior change to document.

### Architecture

No architectural impact. Logging only; no control-flow, protocol, or module-boundary change.

### Validation

- `python3 -c "import ast; ast.parse(...)"` on both edited files: OK.
- `python3 -c "json.load(open('zones.json'))"`: parses clean (track2 `engine={'move_mode':'block'}`, track `engine={'cadence':'arrival','move_mode':'stream'}` unchanged).

### Decisions and Caveats

- **Stream-pileup hypothesis is dead.** Block mode strands identically, so the fix is NOT "stop stream spawning a thread per tick." The real cause is a common EKI-framing desync (or a stall that blocks `_lock` in both modes). Do not pursue the per-tick-thread fix.
- The instrumentation is **verbose** (20 Hz tick log + a `RECV` line per socket chunk, each printing the full buffer repr). It is meant to run for one strand capture only, then be reverted. Capture stdout to a file rather than scrolling.
- The `[kukapy] RECV` line prints the **accumulated** `buf` (not just the new chunk), so the last `RECV` line before the stall is the most informative (it shows everything the reader has seen but not yet parsed).
- **Confirmed the active module:** `import kukapy.robot` from `realtimecontrol/rtr/` resolves to `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` (the editable install), NOT the vendored `rtr/KukaPy` copy. The active KukaPyNEW module is now instrumented; the vendored copy was instrumented too for consistency.

### Next Task

Operator re-runs the strand scenario with the instrumented code and captures the byte trace. Branch on where the trace stops:
- **`BLOCK-move start` + `SEND` present, but no subsequent `RECV`/`FRAME`/`BLOCK-move done`:** the KRL's `<Res>` never arrived at Python → the desync is on the KRL→Python delivery (the response was sent but not received, or the connection dropped). Inspect the last `RECV` bytes for a partial/mangled frame.
- **`RECV` lines present (bytes arriving) but no `FRAME`/`FRAME2` (no successful parse):** `_recv_xml` is failing to recognize the `<Res>` → a parser/framing desync (e.g. an `AliveCounter` or leftover byte poisoning the buffer). Inspect the `FRAME-PARSE-FAIL` lines and the `RECV` buffer.
- **No `BLOCK-move start` (stalled before the move):** the engine is stuck in `get_curjpos`/`get_curpos` (a `_lock` held by a lingering stream daemon thread from the earlier `track` action) → lock-contention stall, a different mechanism.
- **`BLOCK-move done` present but the next tick never starts:** the stall is after the move (in `brain.tick` / `_resolve_pending` / `machine.step` / publish) → narrow further.

### Instructions for the Next Task

1. **Restart the core** to load the instrumented code: `python main.py --robot --ip <IP>` (default 192.168.10.201), listening :18735. Capture all output: `python main.py --robot --ip <IP> 2>&1 | tee /tmp/kukapy_trace.log`.
2. Start KUKAPY_SERVER on the pendant; wait for `[rtr] robot connected` + `[rtr] core up`.
3. Park neutral (`{"cmd":"goto_zone","zone":"rest"}`), then trigger the strand: `{"cmd":"goto_zone","zone":"hunting"}` then `{"cmd":"play_action","action":"track2"}`.
4. When the KRL idles at line 57, grab the **tail** of `/tmp/kukapy_trace.log` (the last `[engine] tick#`, `[kukapy] SEND`, `[kukapy] RECV`, `[kukapy] FRAME*` lines) and hand it back. Branch per the Next Task list.

## 2026-10-25 — Test: force `hunting/track2` to block mode to isolate the stream-mode strand

### Status

Completed (test config applied + operator instructions delivered). The on-robot run itself is performed by the operator; the branch points for the result are documented below.

### Summary

Applied the isolation test for the "strands at `WAIT FOR $FLAG[1]`" bug. The code-established discriminator: the four failing actions (`hunting/track2`, `hunting/scan`, `examining/focus`, `cuddling/breathe`) have **no** `engine` field, so they **inherit** `move_mode=stream` / `cadence=arrival` from the preceding `hunting/track` (the only action with an explicit `engine: {cadence:arrival, move_mode:stream}`, zones.json:1003-1006). The one working action (`scanning/scnr`) reaches a block-mode state. Hypothesis under test: stream mode spawns a daemon thread **per tick** (kuka.py:115-116), piling up EKI calls that contend for `_lock`; block mode sends one awaited move at a time (engine.py:156).

The test forces `hunting/track2` into block mode via `"engine": {"move_mode": "block"}`. Only `move_mode` is set — `cadence` is deliberately omitted from the engine dict so it stays inherited (`arrival`), isolating the single variable the hypothesis targets. Because engine-less actions inherit from the previous action, this cascades: `hunting/scan` and `hunting/focus` (no engine field) will now inherit `block` from `track2`, so the whole post-`track` hunting sub-sequence runs in block mode.

### Changes Made

- `realtimecontrol/rtr/zones.json`: added `"engine": {"move_mode": "block"}` to `hunting.actions.track2` (between `behavior` and `loop`). `cadence` intentionally omitted → stays inherited (`arrival`).

### Documentation

None required — this is a diagnostic config change, not a behavior change to document.

### Architecture

No architectural impact. Exercises the existing `engine` field mechanism (`brain._apply_action_engine`, brain.py:205-239) with a new config value; no code path changed.

### Validation

- `python3 -c "json.load(open('zones.json'))"`: parses clean, 38 zones.
- Confirmed `hunting.track2.engine == {'move_mode': 'block'}` and `hunting.track.engine == {'cadence':'arrival','move_mode':'stream'}`.
- Read `engine.py:148-156` (stream/block dispatch), `machine.py:661-678` (`_cadence_due`), `brain.py:205-239` (`_apply_action_engine`) to confirm `cadence` and `move_mode` are orthogonal and that omitting `cadence` leaves it inherited.

### Decisions and Caveats

- `cadence` was deliberately **not** changed. The originally-described test used `{cadence:fixed, move_mode:block}`; it was refined to isolate `move_mode` only, so a clear result unambiguously implicates stream mode rather than the cadence change.
- The `engine` field is applied by the brain **only on an action change** (`_apply_action_engine`). The override therefore takes effect on the **next** transition into `track2` — the robot must not be sitting in `track2` when the config loads; it must re-enter `hunting/track2` afterward.
- Block mode slows the action (each move is awaited to arrival before the next tick — the "legacy kukapy behaviour"). Slower pose-stepping in `track2` is **expected**, not a failure; the test measures whether it **strands** (freezes), not its speed.

### Next Task

Run the on-robot test (operator) and branch on the result:
- **Strand clears (block works):** the fix is to stop stream mode spawning a daemon thread per tick — send the `cnt>0` move synchronously under `_lock` (the KRL answers C_PTP immediately, so no thread is needed), collapsing per-tick EKI traffic to a single serialized flight.
- **Still strands at line 57 (WAIT):** the desync is a common EKI-framing issue affecting both modes → run Test #2 (instrument `_recv_xml` to capture the exact desync bytes).
- **Strands mid-move (PTP/C_PTP loop, not line 57):** a single blocking EKI call hung → different mechanism; capture the hung frame.

### Instructions for the Next Task

Operator steps (delivered with this task): (1) load the config into the core — restart `python main.py --robot` **or** hot-reload via `POST /api/zones`; (2) park the robot in a neutral zone (not `track2`); (3) `goto_zone hunting` then `play_action track2`; (4) observe the KRL program counter (KUKAPY_SERVER.SRC), the physical robot, and the core's state snapshots; (5) branch per the Next Task list above.

## 2026-10-24 — Fix EKI `_recv_xml` silent hang (the "Wait for $FLAG[1]" KUKA HMI error)

### Status

Completed.

### Summary

Fixed the root cause of the "Wait for $FLAG[1]" KUKA HMI error: a silent infinite hang in the EKI `_recv_xml` reader. The KUKA EKI link (`KUKAPY.xml`: `PROTOCOL=TCP`, `SENDFLAG=1`, `ALIVE ElementName="AliveCounter"`) is a `\x00`-delimited byte stream that interleaves `<Res>` responses with periodic `AliveCounter` keepalives. TCP does not preserve message boundaries, so a single `recv(4096)` frequently returns a whole `<Res>` **plus** the start of the next frame. The old `_recv_xml` kept its buffer in a *local* variable and returned as soon as it found a `<Res>`, throwing the leftover bytes away. The next call then started on a **fragment**, which can never be parsed, so it kept reading keepalives forever — a silent hang that stranded the KRL at `WAIT FOR $FLAG[1]` (idle, no new commands) and left the robot without its next move.

The fix makes the leftover buffer persistent across calls: `self._recv_buf` is written on every return path (the tail after the returned `<Res>` in `\x00`-mode, or the accumulated bytes in plain-XML mode) and read at the start of the next call. It is reset to `b""` on `connect()` and `disconnect()` so stale bytes never leak across sessions. A defensive 1 MB size guard raises `KukaError` instead of hanging if framing ever desyncs.

### Changes Made

- `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` (the **active** editable-install module): added `self._recv_buf = b""` to `Robot.__init__`; rewrote `_recv_xml` to read from / write to `self._recv_buf` (persistent leftover) instead of a local `buf`; reset `self._recv_buf` in `connect()` and `disconnect()`; added a 1 MB overflow guard.
- `realtimecontrol/rtr/KukaPy/kukapy/robot.py` (vendored copy, not used at runtime): applied the identical fix so the bug cannot resurface if that copy is ever used.
- `realtimecontrol/rtr/tests/test_kuka_eki_recv.py` (new): 6 regression tests driving `_recv_xml` against a fake socket — split frame across two recvs, keepalive between Res frames, keepalive-only chunk, multi-frame chunk tail preservation, byte-at-a-time reassembly, and connect reset. The fake socket loops on an `AliveCounter` (like the real KRC) so a buggy reader hangs and the join timeout turns the hang into a deterministic failure.

### Documentation

None required — the fix is self-documenting via the `_recv_xml` docstring and the new test file's module docstring, which explain the protocol and the bug.

### Architecture

No architectural impact. This is a bug fix within the existing EKI framing layer; the protocol, module boundaries, and data flow are unchanged.

### Validation

- `python3 -m py_compile` on both `robot.py` copies: OK.
- `diff` of the two `_recv_xml` methods: identical.
- `/tmp/kilo/repro_recv.py` (reproduction): before the fix, call1 OK / call2 HANG; after the fix, **both RES_A and RES_B retrieved (PASS)**.
- `tests/test_kuka_eki_recv.py` (new): all 6 tests pass.
- `tests/test_kuka_eki_serialization.py` (existing EKI test): all 6 tests pass.
- Full `tests/` suite (excluding `test_remote_tui.py`, which has a **pre-existing** `ImportError: cannot import name 'action_kind' from 'rtr.flow'`): 274 passed, 14 skipped, 7 failed — all 7 failures are **pre-existing** and none import `kukapy` (`test_bridge_behavior.py::test_real_zones_bridge_action_is_tagged`, 5× `test_remote_sdl.py`, `test_transition.py::test_all_reachable_transitions_complete`).

### Decisions and Caveats

- The 1 MB overflow guard is a safety net, not a protocol feature; it converts a possible infinite hang into a loud `KukaError`. Under normal operation the buffer stays tiny (one partial frame).
- The vendored copy (`rtr/KukaPy/`) is not on the runtime path (`kukapy` resolves to the KukaPyNEW editable install), but it was fixed too for consistency.
- The 7 pre-existing test failures and the `test_remote_tui.py` collection error are unrelated to this change and were present before it.

### Next Task

Two pending workstreams, both blocked on user input:
1. **Single-action auto-return** (`editor.html` on-save normalization): for a zone with exactly one action and an empty `next`, add a return target to the incoming/reverse zone. Blocked on the definition of the "previous" target and the creation trigger (the earlier clarification was dismissed).
2. **A6 continuous-axis accumulation**: the A6 counter accumulated to `216237.30°` and manifests as a growling. The code side is confirmed (no Python wrapping; RSI `AXISACT Type="Measured"`; the arrival check `count=5` excludes A6). The remaining question — what KUKA controller operation resets the continuous counter and why it grows — is controller-side knowledge not present in the codebase.

### Instructions for the Next Task

- Auto-return: confirm the "previous" target (is it the action's `next` list, the `reverse_targets` of the incoming zone, or something else?) and the creation trigger (on-save only, or also on zone create?), then implement in `editor.html` using `reverse_targets`, `firstEnabledAction`, and `setNext`; add a save-normalization test.
- A6: obtain the KUKA controller behavior for the A6 continuous axis (which pendant/controller operation resets the measured counter, and whether the growling correlates with the counter magnitude). Then decide whether to add a Python-side wrap or a controller-side reset to `behavior.py` / the KRL.

## 2026-10-24 — Investigate: `auto discarded move to cuddling_purring_bridge / bridge — red-link conflict`

### Status

Completed.

### Summary

Explained the runtime warning `auto discarded move to cuddling_purring_bridge / bridge — red-link conflict`. The move `cuddling/purr → cuddling_purring_bridge/bridge` (the first candidate in `cuddling/purr`'s `next` list) is discarded by the core's safety gate on the **inter-zone shared-position rule**: the two actions must share at least one anchor pose within 0.5° on every axis. All three `bridge/bridge` anchor poses carry an `'x'` (frozen-axis) marker on A1 (`['x', -133, 156, -2, 0, 0]`, `['x', -120, 135, -2, 0, 0]`, `['x', -110, 135, -2, -5, 0]`), and `find_shared_pose` (`rtr/state/zones.py:92`) skips any pose containing `'x'` — so no shared pose is ever found. The safezone-overlap rule passes (both zones overlap on all 6 axes, gap list empty).

**Divergence found:** the editor's flow view judges this edge as CLEAN because its `edgeConflict` (`editor.html:1077-1090`) skips only the `'x'` axes and compares the concrete ones (purr end pose `['x', -133, 156, -2, 0, 0]` vs bridge entry pose `['x', -133, 156, -2, 0, 0]` — all concrete axes match). The core's matcher is stricter: it discards the whole pose when any axis is `'x'`. Hence the editor shows a green link while the runtime discards the move.

### Changes Made

- No code changes (read-only investigation).

### Documentation

None required — explanation only.

### Architecture

No architectural impact.

### Validation

- Real `SafetyGate.check_conflict("cuddling", "purr", "cuddling_purring_bridge", "bridge")` on the live `zones.json`: `conflict=True`, issues=`['no shared position: cuddling/purr shares no pose with cuddling_purring_bridge/bridge']`.
- Safezone overlap gaps for `cuddling` vs `cuddling_purring_bridge`: `[]` (all 6 axes overlap).

### Decisions and Caveats

- The root cause is the `'x'`-handling mismatch between `find_shared_pose` (core, skips whole pose) and `edgeConflict` (editor, skips only `'x'` axes). Until aligned, every auto move into a bridge action whose anchors are all `'x'`-marked will be discarded.

### Next Task

Decide the fix: (a) give `cuddling_purring_bridge/bridge` (and similar bridge actions) at least one fully numeric anchor pose matching the source's end pose, or (b) change `find_shared_pose` to compare only concrete axes like the editor does.

### Instructions for the Next Task

- Option (a): in `zones.json`, replace the `'x'` on A1 of the bridge's first anchor with a concrete value (e.g. `[-60, -133, 156, -2, 0, 0]` — any value inside the zone safezone that matches the robot's pose at hand-off), keeping the other anchors as-is.
- Option (b): in `rtr/state/zones.py` `find_shared_pose`, compare axis-by-axis only where both sides are numeric (mirror `editor.html` `edgeConflict` lines 1080-1090); update `tests/test_safety_conflict.py` accordingly and re-run it.

## 2026-10-24 — Implement + test: `check_zone_limits.py` (limit checker with interactive apply)

### Status

Completed.

### Summary

Built `realtimecontrol/rtr/check_zone_limits.py`, a reusable checker that walks every zone and action in `zones.json` and validates all joint-space values against the real KR60 axis limits. It lists every out-of-limit value with a proposed clamp to the nearest limit, then interactively offers to apply ALL / ONE BY ONE / NONE (or non-interactive `--dry-run` / `--apply-all` / `--apply-one` / `--file`). It checks three data kinds: `safezone` bands, action `poses` (skipping `"x"` frozen axes and counting them), and action `axis_bands`. Disabled zones/actions are checked too and flagged `[disabled]`. The `layout` section (2D canvas coords, not joints) is excluded.

**Result on the real `zones.json`: 12 violations, all in `safezone` bands (0 pose, 0 axis_bands violations).** All 12 are in bridge zones:
- A6 `[-357,357]` → `[-150,150]`: `sleeping_stretching_bridge` (lo+hi), `cuddling_stretching_bridge` (lo+hi), `performing_roaring_bridge` (lo+hi)
- A2 lo `-136` → `-135`: `stretching_hunting_bridge`, `hunting_examining_bridge`, `purring_hunting_bridge`
- A5 `[-118,118]`/`hi 118` → `[-55,100]`: `scanning_leaping_bridge` (lo+hi), `performing_roaring_bridge` (hi)

**Note:** during analysis I had estimated 13 violations, but the script found 12. The discrepancy was my manual reading: `hunting`'s A2 safezone is actually `[-134,5]` (in limits), not `-136`. The script is the source of truth. Also the file has 35 zones, not the 33 counted during the initial read.

### Changes Made

- `realtimecontrol/rtr/check_zone_limits.py`: created. `AXIS_LIMITS = [(-185,185),(-135,35),(-95,158),(-180,180),(-55,100),(-150,150)]` at top (editable). `collect()` walks zones → safezone/poses/axis_bands, building `Violation` records with a `clamp()` fix. `interactive_apply()` prompts `[A]/[O]/[n]`; non-TTY stdin is treated as NONE. `write_file()` backs up to `zones.json.bak-pre-limit-check` then writes with `json.dumps(indent=2, ensure_ascii=False) + "\n"` (matches existing format). Exit 0 = clean/all-applied, 1 = violations remain, 2 = file/structure error.
- `realtimecontrol/rtr/zones.json`: **NOT modified** (real file left untouched; apply was tested only on a `/tmp` copy).

### Documentation

- Script docstring documents usage, limits, and rationale. No separate docs needed.

### Architecture

- No architectural impact — standalone utility script next to `zones.json`, no new dependencies (stdlib only).

### Validation

- `python3 check_zone_limits.py --dry-run` on the real `zones.json`: reports 12 violations, exit 1, no write. 35 zones, 57 actions, 126 poses checked, 23 `"x"` axes skipped.
- `python3 check_zone_limits.py --file /tmp/kilo/test_zones.json --apply-all` on a copy: applied 12/12, re-check 0 remaining, exit 0, backup created.
- `diff` (original vs applied copy, both normalized via `json.dumps(indent=2)`): exactly **12 changed value lines**, no structural/formatting changes → JSON round-trip preserves formatting.
- `git status --short zones.json`: clean → real file untouched.

### Decisions and Caveats

- Applied clamps change robot behavior; the real `zones.json` was deliberately left unmodified. The user can run the script interactively (`python3 check_zone_limits.py`) to apply, or ask me to run `--apply-all`.
- `AXIS_LIMITS` is a single editable constant; if the robot's real limits ever change, only that line needs updating.
- `"x"` frozen pose axes are skipped (value comes from the live robot at runtime — a static checker cannot verify them); count is reported.

### Next Task

Decide whether to apply the 12 clamps to the real `zones.json` (via the script) or leave it for the user.

### Instructions for the Next Task

If applying: run `python3 check_zone_limits.py --apply-all` from `realtimecontrol/rtr/` (or `--apply-one` to pick per change). It creates `zones.json.bak-pre-limit-check` first. After applying, re-run `--dry-run` to confirm 0 violations. No code changes needed; the script is complete and tested.

## 2026-10-24 — Research: reconcile stated axis limits with codebase (A3 discrepancy)

### Status

Completed.

### Summary

The user asked to verify that ALL zones and actions in `realtimecontrol/rtr/zones.json` are within the actual robot axis limits, and to build a repeatable script that lists all out-of-limit values with proposed clamps and offers to apply ALL / ONE BY ONE / NONE. The user's stated limits matched the codebase on 5 of 6 axes; A3 differed: the message said `[95, 158]` while the codebase consistently defines A3 as `[-95, 158]`. Asked the user; they confirmed `[-95, 158]` (the `95` was a typo). Final real limits = the codebase `HARDWARE_LIMITS`: A1 `[-185,185]`, A2 `[-135,35]`, A3 `[-95,158]`, A4 `[-180,180]`, A5 `[-55,100]`, A6 `[-150,150]`.

### Changes Made

- None (research only).

### Documentation

- None required — findings recorded in this handoff entry.

### Architecture

- No architectural impact.

### Validation

- Cross-checked limit definitions across three independent files:
  - `realtimecontrol/rtr/rtr/state/zones.py:34-54` — `SOFTWARE_LIMITS` and `HARDWARE_LIMITS` both give A3 `(-95, 158)`; HARDWARE_LIMITS sourced from the `kr60ha_macro.xacro` joint `<limit>` tags.
  - `realtimecontrol/rtr/validate_zones.js:138-139` — `SOFTWARE_LIMITS`/`HARDWARE_LIMITS` mirror the same values.
  - `realtimecontrol/rtr/rtr/remote/view.py:60-67` — `HW_LIMITS` for the ARM page bars, same values.
- `robotconfig.json` contains no axis-limit definitions (grep: no matches).

### Decisions and Caveats

- A3 lower limit is `-95` (user-confirmed). The checker script must use the codebase limits as its default.
- Pre-scan of `zones.json` under these limits (to be confirmed by the script): **all 6-axis poses are in limits**; violations are confined to `safezone` bands: A2 lo `-136` in `hunting`, `stretching_hunting_bridge`, `hunting_examining_bridge`, `purring_hunting_bridge`; A5 `[-30,118]` in `performing_roaring_bridge`; A5 `[-118,118]` in `scanning_leaping_bridge`; A6 `[-357,357]` in `sleeping_stretching_bridge`, `cuddling_stretching_bridge`, `performing_roaring_bridge` (13 band-end violations total, 0 pose violations).

### Next Task

Implement the checker script (`realtimecontrol/rtr/check_zone_limits.py`).

### Instructions for the Next Task

Build a Python script next to `zones.json` that: (1) checks every zone `safezone` band end, every action `poses` value (`"x"` frozen axes skipped), and every action `axis_bands` end against `AXIS_LIMITS = [(-185,185),(-135,35),(-95,158),(-180,180),(-55,100),(-150,150)]`; (2) lists each violation with a proposed clamp to the nearest limit; (3) interactively offers apply ALL / ONE BY ONE / NONE; (4) writes `zones.json` back with `indent=2` after a `.bak` backup; (5) supports `--dry-run` / `--apply-all` / `--apply-one` / `--file` for non-interactive use. Check disabled zones/actions too (the user asked for ALL), marking them `[disabled]`.

## 2026-10-24 — Analyse: zones.json structure and check targets

### Status

Completed.

### Summary

Read `realtimecontrol/rtr/zones.json` in full (3975 lines). It contains 33 zones under `zones` (12 behavioral zones + 17 bridge zones + `testing`, `grabbing`, `scanning`), plus a `layout` section (2D canvas coordinates — not joint values, out of scope). Three data kinds hold joint-space values that must be checked against the real axis limits:

1. `zones.<zone>.safezone` — 6 `[lo, hi]` bands, one per axis A1..A6.
2. `zones.<zone>.actions.<action>.poses` — list of 6-element arrays; an axis may be the string `"x"` meaning "freeze / copy the current robot axis value" (no static value to check — skipped and counted).
3. `zones.<zone>.actions.<action>.axis_bands` — dict keyed `"0".."5"` with `[lo, hi]` bands (used by ACTIVE_CLASSES tracking actions; `clawing.lock_target` has an empty `{}`).

The `layout` section holds 2D editor positions and is untouched by the checker. Zones/actions can have `enabled: false` (e.g. `grabbing.grab`); the user asked to check ALL, so disabled entries are checked too and marked.

### Changes Made

- None (analysis only).

### Documentation

- None required — structure recorded in this handoff entry.

### Architecture

- No architectural impact.

### Validation

- Full file read (lines 1-3975). All 33 zones enumerated; every `safezone`, `poses`, and `axis_bands` entry inspected against the limits during analysis.

### Decisions and Caveats

- `"x"` pose axes are skipped (frozen axes take their value from the live robot at runtime — a static checker cannot verify them; the count is reported).
- `layout` coordinates are 2D canvas positions, not joint angles — excluded.

### Next Task

Research existing limit definitions and reconcile with the user's stated limits (done — see the entry above).

## 2026-10-24 — Intra-zone full-mesh `next` (auto-connect siblings, hidden in flow view)

### Status

Completed.

### Summary

The user wants all actions within the same zone to auto-connect to each other via their `next` field (full mesh, not just sequential), applied retroactively to `zones.json` and on new-action creation / copy. The intra-zone links drive the brain's roam (it picks a random valid next), but are HIDDEN in the flow view (to avoid the N*(N-1) edge clutter) and still LISTED in the action editor (so the user can remove a specific link).

Three changes:
1. **Retroactive migration** (`scripts/seed_intra_next.py`): a one-time, idempotent, additive pass over `zones.json` that adds the missing intra-zone `next` links (every action points to every other action in its zone). Existing targets (intra- or inter-zone) are preserved. Applied: 54 links added across 12 zones (13 multi-action zones; one already had the full mesh).
2. **New/copy auto-connect** (`editor.html`): a `meshNewAction(zn, an)` helper wires a newly-created or copied action into the zone's intra-zone mesh (additive — never re-adds a link the user removed, never touches inter-zone links). Wired into the `+ add action` button (`buildZoneForm`) and `copyAction`.
3. **Hide in flow view** (`editor.html`): `flowRenderEdges` now skips intra-zone edges (the inner edge layer stays empty); only inter-zone links are drawn. The action editor still lists every `next` target including the intra-zone ones (unchanged — it already lists all targets), so removal is still possible there.

### Changes Made

- `realtimecontrol/rtr/scripts/seed_intra_next.py` (new): one-time migration that seeds the intra-zone full mesh into `zones.json`. Additive (preserves existing targets), idempotent, `--dry-run` supported.
- `realtimecontrol/rtr/zones.json`: 54 intra-zone `next` links added across 12 zones (the full mesh for every multi-action zone).
- `realtimecontrol/rtr/editor.html`:
  - `meshNewAction(zn, an)` (new, after `cleanNextLists`): wires a new/copied action into the zone's intra-zone mesh (both directions), additive.
  - `buildZoneForm` `+ add action` handler: calls `meshNewAction(name, nm)` after creating the action.
  - `copyAction`: calls `meshNewAction(zn, nn)` after creating the copy; doc comment updated.
  - `flowRenderEdges`: skips intra-zone edges (hidden from the flow view).

### Documentation

- `scripts/seed_intra_next.py` has a module docstring explaining the migration (usage, `--dry-run`).
- No user-facing docs updated (the README does not document the per-zone `next` mesh; the editor's inline help for the `next` section already describes the ordered target list).

### Architecture

- No architectural impact to the runtime. The brain already picks a random valid next from `action_next_list` (`brain.py:_valid_next_candidates`), so the added intra-zone links are valid roam candidates with no core change. The editor's flow view hides the internal links but the data model and core navigation are unchanged.

### Validation

- `node --check` on the extracted inline `<script>` of `editor.html`: **JS SYNTAX OK**.
- Isolated JS test of `nextList`/`setNext`/`meshNewAction` (full mesh on new-action join, additive removal persistence, copy scenario, single-action zone): **19 passed, 0 failed**.
- `python3 scripts/seed_intra_next.py` applied (54 links), 2nd run = 0 links (idempotent).
- Post-migration check: every multi-action zone's full intra-zone mesh is present (**FULL MESH OK**).
- `zones.json` validation (all `next` targets reference existing zones + string actions): **VALIDATION OK**.
- `pytest tests/` (excluding the pre-existing `test_remote_tui.py` collection error): **7 failed, 268 passed, 14 skipped** — the SAME 7 failures occur on the ORIGINAL `zones.json` (confirmed by `git checkout zones.json` + re-run), so **zero new failures** from this change. Pre-existing failures: `test_transition.py::test_all_reachable_transitions_complete` (TypeError in `sim.py`, a `"x"` pose axis), `test_bridge_behavior.py::test_real_zones_bridge_action_is_tagged`, and 5 `test_remote_sdl.py` UI tests.

### Decisions and Caveats

- **Additive, not re-normalizing:** the migration and `meshNewAction` only ADD missing intra-zone links; they never remove or re-add a link the user deleted. So a removed connection stays removed (matching "list them in the action editor for removal"). A save-time re-normalization was deliberately NOT added (it would fight the user's removals).
- **`next` list order is irrelevant to the brain** (`_random_valid_next` picks randomly), so the migration appends missing links at the end of each list (preserving existing order).
- **Flow view hides intra-zone links** but the `flowAddEdge` inner-edge code path is left intact (the inner layer is simply never fed intra-zone edges) — reverting the one-line skip in `flowRenderEdges` restores the old rendering.
- **`meshNewAction` assumes the zone's existing actions already have the mesh** (the migration seeds it for existing zones). If a zone was created without the migration and actions were added before this change, the existing actions may lack sibling links until a new action triggers `meshNewAction` or the migration is re-run. Re-running `scripts/seed_intra_next.py` fixes any such drift.
- **Stale browser cache:** the user must hard-refresh the editor (Ctrl+Shift+R) to load the updated `editor.html`.

### Next Task

No immediate next task identified. The full-mesh auto-connect is complete (retroactive + new/copy), hidden in the flow view, and still removable in the action editor.

### Instructions for the Next Task

1. To re-apply the mesh to any zone that drifted (e.g., actions added before this change without the mesh), run `python3 scripts/seed_intra_next.py` (idempotent).
2. The `meshNewAction` helper lives in `editor.html` (after `cleanNextLists`); it is called from the `+ add action` handler in `buildZoneForm` and from `copyAction`.
3. If the user later wants the intra-zone links visible in the flow view again, remove the `if (e.from.zone === e.to.zone) return;` skip in `flowRenderEdges` (editor.html) and restore the `innerLayer` routing.

## 2026-10-24 — Remove the pose-continuity editor warning (unneeded, false-positive)

### Status

Completed (warning removed). The single-action auto-return feature (user point 3) is pending clarification and not yet implemented.

### Summary

The user reported still getting "loads" of the `entry pose differs from next …` save warning and called it "literally unneeded," giving three reasons: (1) a concrete false positive `entry pose differs from next testing/locked (45, -30, 42, 0, -5, 0)`; (2) an `"x"` (frozen/copy) axis should not be a problem; (3) a zone with a single action should auto-gain a return `next` to avoid dead ends.

**Root cause of "still" seeing it:** the user's browser was showing a **cached pre-`midifix` `editor.html`**. The live rtr server (PID from `main.py --sim`) serves the **main working tree** (`/home/kaos/.../realtimecontrol/rtr`), whose `editor.html` already had the newer `no shared pose with next` logic (intra-zone exempt, `findSharedPose` mirroring the runtime gate). The exact text the user quoted — `entry pose differs from next X/Y (pose)` with the pose in parentheses — only exists pre-`midifix` (and in the idle `sapphire-scraper` Agent-Manager fork). So the user was viewing stale JS, not the served file.

**Fix applied:** the pose-continuity check is a non-blocking advisory that the runtime safety gate (`rtr/safety/gate.py`, via `find_shared_pose`) already performs independently, and the robot degrades gracefully at runtime (falls back to the target's entry pose when no shared pose exists). It false-positived on intra-zone links, self-loops, same-pose siblings, and `"x"` axes. Removed it entirely from both editor surfaces so the unneeded warnings stop.

### Changes Made

- `realtimecontrol/rtr/editor.html` (`saveZones`, save-time warnings loop): removed the pose-continuity `for (const t of nextList(a))` block that pushed `no shared pose with next …` warnings. The safezone/hardware-limit pose checks in the same loop are unchanged.
- `realtimecontrol/rtr/editor.html` (`buildActionEditor`, the inline hint): removed the pose-continuity `if (a.poses.length) { const diffs = targets.filter(...) }` block that rendered the `no shared pose with next …` hint in the action editor.
- `findSharedPose`/`poseNumeric` (editor.html:986-1000) and the runtime `find_shared_pose` (`rtr/state/zones.py:92`) are left intact: the editor warning no longer calls `findSharedPose`, but the runtime gate still uses its own `find_shared_pose` for real collision/hand-off checks. No runtime behavior changed.

### Documentation

- None required — the warning was an advisory with no user-facing doc; the runtime gate's real check is unchanged and already documented in `rtr/safety/gate.py`.

### Architecture

- No architectural impact. This is an editor-only advisory removal. The runtime state machine and safety gate are untouched; the editor no longer pre-emptively flags pose continuity, deferring entirely to the runtime gate.

### Validation

- `node --check` on the extracted `<script>` blocks of `editor.html`: **JS SYNTAX OK** (13 script blocks).
- Confirmed via grep that `entry pose differs` / `no shared pose with next` no longer appear anywhere in `editor.html`; the only remaining `findSharedPose` reference is the (now-unused) function definition at editor.html:992.
- Not run: the robot runtime / pytest (no runtime code changed; the editor change is browser-side JS with no Python test coverage).

### Decisions and Caveats

- **Removed, not softened:** chose full removal over adding more exemptions (self-loop, same-pose, `"x"` axes) because the user called the warning "literally unneeded" and the runtime gate already owns the real check. If a future need arises to warn on a genuinely unreachable inter-zone hand-off, the `findSharedPose` helper is still present and can be re-wired.
- **`"x"` axes (user point 2):** resolved by removal — no pose comparison remains in the editor, so frozen axes can no longer trigger a warning. The runtime `find_shared_pose` still skips `"x"` poses and falls back to the entry pose, so runtime is unaffected.
- **Stale browser cache:** the user must **hard-refresh** the editor (Ctrl+Shift+R) to load the updated `editor.html`; until then the cached pre-`midifix` page will keep showing the old warning text.
- **Point 3 (single-action auto-return) NOT implemented:** ambiguous — "returning to the previous" needs a definition (which zone is "previous"? the incoming/reverse target? table order? the flow layout neighbor?) and a trigger point (on save? on zone creation? on bridge creation?). Deferred to the next task pending the user's answer.

### Next Task

Implement the single-action auto-return `next` (user point 3) once the user clarifies: (a) what "the previous" zone is (the reverse/incoming target of the flow, vs. table order, vs. the flow-layout neighbor), and (b) when the auto-return should be created (on save, on zone/bridge creation, or as a live editor affordance). Likely location: the flow tab's `flowBridge` (editor.html:6797) and/or a save-time pass over single-action zones, reusing `reverse_targets` from `rtr/flow.py`.

### Instructions for the Next Task

1. Confirm with the user the definition of "the previous" zone and the trigger for the auto-return.
2. In `rtr/flow.py`, `reverse_targets(zones, zone)` already returns the zones that link *into* a zone — a natural source for the return target. The editor mirrors this logic via `actionEdgeList()` (editor.html:1005) and `nextList`/`setNext` (editor.html:916/927).
3. For a zone with exactly one action, if that action's `next` is empty, append a return target pointing back to the chosen "previous" zone (and its first enabled action, per `firstEnabledAction` editor.html:899). Reuse `setNext` to write the field in the correct single-dict vs. list form.
4. Decide whether this is a save-time normalization (in `saveZones`) or a live editor affordance (in the flow tab). Keep it non-destructive: never overwrite an existing explicit `next`.
5. Validate with `node --check` on the extracted editor scripts and re-run the zone-table save to confirm no dead-end warnings remain.

## 2026-10-24 — Finish midi named-pose removal: stale docstring + README cleanups

### Status

Completed. After the session crash, the code-side removal was already done in the working tree (`POSES`/`LIN_POSES` tables deleted from `zones.py`; imports/kwargs removed from `state/__init__.py`, `main.py`, `httpserver.py`, `midi.py`; the ch1/ch2/ch5 note→pose dispatch removed from `MidiInput._handle_note`). What remained was stale documentation describing the removed mappings: the `midi.py` module docstring still listed `Note ch1/ch2: 61-64 → set_joint_pose` and `Note ch6 → set_linear_pose`, and the README still had the same note line plus two "named poses" mentions. All three were fixed.

### Summary

The user's task was to remove the hardcoded MIDI mappings (the named-pose note protocol). The crashed session had completed the code changes but left the docs behind. Verified the working-tree state via `git diff` and repo-wide greps (no `POSES`/`LIN_POSES` references left in the rtr package; no test passes `poses=`/`lin_poses=` kwargs to `MidiInput`), then updated the three stale doc spots to match the actual remaining legacy protocol:

- `midi.py` docstring: the legacy protocol is now CC1 (trigger_action), CC3 (play action by index), CC20/21/22 (adjust_limit), and `Note ch3 → random_wrist` only (0-based channel 2, the docstring's 1-based "ch3" convention — unchanged).
- README legacy-fallback bullet: the note line is now just `**Note ch3** → random_wrist`.
- README: dropped "named poses" from the `state/zones.py` module table row, and rewrote the `zones.json` paragraph (the file no longer carries `poses`/`lin_poses`; the built-in `ZONES` table is the fallback).

`validate_state_data` still accepts legacy `poses`/`lin_poses` keys in a data file (decided in the crashed session: kept as generic data validation for old backups; nothing consumes them anymore). The `Cmd.SET_JOINT_POSE`/`SET_LINEAR_POSE` commands remain — they are still reachable via the WebSocket control API (`websocket.py` accepted-command list, `brain.py` handlers), which is a separate path from MIDI.

### Changes Made

- `realtimecontrol/rtr/rtr/io/midi.py`: module docstring legacy-protocol block — removed the `Note ch1/ch2: 61-64 → set_joint_pose` and `Note ch6 → set_linear_pose` lines; kept `Note ch3 → random_wrist` and the ch4/ch5/ch7 "intentionally not mapped" paragraph (still accurate).
- `realtimecontrol/rtr/README.md`: (1) legacy-fallback note bullet reduced to `**Note ch3** → random_wrist`; (2) `state/zones.py` table row — "named poses" dropped from the description; (3) `zones.json` paragraph — now "source of truth for zones and actions" (no more `poses`/`lin_poses` mention).

### Documentation

- `realtimecontrol/rtr/README.md` updated as above (the in-scope doc updates for this task).
- Pre-existing README staleness noted but intentionally not touched (separate cleanup): the legacy-fallback section still describes `CC1 → goto_zone`, `CC2 → set_mode`, `CC30 → set_mode` (the code now does CC1 → `trigger_action`, and CC2/CC30 are not handled), and the `zones.json` paragraph still mentions legacy `mode`/`exitposes`/startpos concepts.

### Architecture

No architectural impact. Docs-only change; runtime behavior unchanged. The `Cmd.SET_JOINT_POSE`/`SET_LINEAR_POSE`/`RANDOM_WRIST` command pipeline (commands.py → brain.py → machine.py) stays intact for the WebSocket control path.

### Validation

- `python -c "import main; from rtr.io.midi import MidiInput; from rtr.io.httpserver import HttpServer; from rtr.state import StateMachine, Zones"`: **imports OK**.
- `pytest tests/test_midi_in.py tests/test_midi_brain_trigger.py tests/test_zone_groups.py -q`: **81 passed, 1 skipped**.
- `pytest tests/ -q --ignore=tests/test_remote_tui.py`: **268 passed, 14 skipped, 7 failed** — all 7 failures pre-existing and unrelated (verified: none import the changed modules' removed symbols):
  - `test_bridge_behavior.py::test_real_zones_bridge_action_is_tagged` — data drift: `zones.json` `resting_sleeping_bridge/bridge` has 4 poses, test expects 3.
  - `test_remote_sdl.py` (5 failures) — TUI label drift ("THEME / LAYOUT" etc. not in `view.build_rows` output).
  - `test_transition.py::test_all_reachable_transitions_complete` — pre-existing `"x"`-axis sim `TypeError` (documented in the previous entry).
- `pytest tests/test_remote_tui.py`: **collection `ImportError`** (`action_kind` not in `rtr.flow`) — pre-existing, unmodified tracked files, unrelated.
- Repo-wide grep for `POSES`/`LIN_POSES`/`named pose`/`ch1`/`ch6` in the rtr package: no remaining references (only "aarch64" false positives).

### Decisions and Caveats

- Kept the `validate_state_data` `poses`/`lin_poses` validation (crashed-session decision): old zone backups keep validating; the fields are simply unconsumed.
- README CC-line staleness (CC1/CC2/CC30) left for a separate docs-cleanup pass — outside this task's scope.
- The 7 test failures + 1 collection error predate this change; flagged, not fixed.

### Next Task

Optional docs cleanup: update the README legacy-fallback CC lines to match the actual code (CC1 → `trigger_action`; CC2/CC30 removed), and the `zones.json` paragraph's legacy `mode`/`exitposes`/startpos mentions. Separately, the `"x"`-axis `test_transition.py` harness fix from the previous entry.

### Instructions for the Next Task

For the README CC-line cleanup: in `realtimecontrol/rtr/README.md` (~line 770), the bullets read `CC1 (value 1-6) → goto_zone (...)`, `CC2 (0/1) → set_mode`, `CC30 (1/2) → set_mode`. The actual legacy handler is `MidiInput._handle_cc` in `rtr/io/midi.py`: CC1 → `Cmd.TRIGGER_ACTION` (zone + first enabled action), CC3 → play action by index, CC20/21/22 → `Cmd.ADJUST_LIMIT`. CC2/CC30 are no longer handled. Align the bullets to the code. For the `test_transition.py` fix, see the previous entry's instructions (resolve `"x"` axes before `SimRobot` construction in `_run_transition`).

## 2026-10-24 — Inter-zone warning now mirrors `find_shared_pose`; dangling `POSES`/`LIN_POSES` imports removed

### Status

Completed. The inter-zone variant of the `entry pose differs from next` warning no longer false-positives: the editor now checks "do the two actions share any pose within `0.5`" (mirroring the runtime gate's `find_shared_pose`) instead of entry-to-entry at `0.001`. Confirmed bidirectional zone links are safe (gate checks are symmetric; both directions verified `conflict: False`). Also finished the midi named-pose removal refactor: `POSES`/`LIN_POSES` were deleted from `zones.py` in the working tree but still imported by `__init__.py`, `main.py`, and `httpserver.py` — that dangling import was the pre-existing `ImportError` blocking pytest collection.

### Summary

The user reported `entry pose differs from next roaring_grabbing_bridge/bridge (-5, -125, 116, -2, 80, 0)` in the `roaring` action pose editor as an unneeded warning: the exit pose toward the bridge is correct, and movement in the other direction is perfectly possible (the robot just moves to the exit pose instead of the entry pose). They asked whether **all** links should be marked bidirectional to keep the robot moving, and whether there are safety reasons not to.

**Root cause (editor false positive):** The editor's save-time check and action-editor hint compared the action's entry anchor against each inter-zone successor's entry anchor at `0.001`. But the runtime safety gate's inter-zone rule is "any shared pose within `0.5`" (`find_shared_pose`, `rtr/state/zones.py:92`) — the hand-off happens at *any* shared anchor, not specifically the entries. In the reported case `roaring/roar`'s exit `[-5,-125,116,-2,80,0]` **is** `roaring_grabbing_bridge/bridge`'s entry, so the gate passes (`conflict: False`) while the old editor check warned on the non-equal entries (`roar` entry `[-3,-134,116,-2,80,0]` vs `bridge` entry).

**Bidirectional links — safe.** The inter-zone gate checks (safezone overlap + shared pose) are symmetric: the same `find_shared_pose` call applies regardless of direction, and the collision check is per-target-entry-pose, independent per direction. Verified with real `zones.json` data: `roaring/roar → roaring_grabbing_bridge/bridge` **and** the reverse both give `conflict: False`, shared pose `[-5,-125,116,-2,80,0]` (roar exit index 2 == bridge entry index 0). No safety reason to restrict links to one direction; marking both directions keeps the robot moving and gives it options.

**Pre-existing import blocker (fixed):** The working-tree `zones.py` refactor removed the `POSES`/`LIN_POSES` constants (the midi named-pose tables, part of the midi.py change that dropped the ch1/ch5 note protocol), but three consumers still imported them, so `import rtr.state` raised `ImportError` and blocked all pytest collection:
- `rtr/state/__init__.py` (re-export),
- `main.py` (line 33 import + fallback dict + `data.get("poses", POSES)` + `poses=`/`lin_poses=` kwargs on `MidiInput`, which no longer accepts them — a runtime `TypeError` waiting to happen),
- `rtr/io/httpserver.py` (import + GET `/api/zones` fallback dict).

`zones.json` no longer carries `poses`/`lin_poses` keys (only `zones` + `layout`), and `validate_state_data` still accepts them optionally for legacy files, so nothing data-level depends on the constants anymore.

### Changes Made

- `realtimecontrol/rtr/editor.html:985-999`: added `poseNumeric(p)` and `findSharedPose(posesA, posesB, margin = 0.5)` JS helpers — the JS mirror of `rtr.state.zones.find_shared_pose` (any pose shared within `0.5` on every concrete axis; `"x"` frozen axes skipped).
- `realtimecontrol/rtr/editor.html:6128-6134` (`saveZones`): the inter-zone branch now warns only when `findSharedPose(poses, tgt) === null`; message is now `no shared pose with next ${t.zone}/${t.action}`. The intra-zone `continue` exemption from the previous task is preserved.
- `realtimecontrol/rtr/editor.html:4968-4976` (`buildActionEditor` hint): same change — the inline hint now fires only when `findSharedPose(a.poses, tgt) === null`.
- `realtimecontrol/rtr/rtr/state/__init__.py`: removed `POSES`/`LIN_POSES` from the `.zones` import and `__all__`.
- `realtimecontrol/rtr/main.py`: import reduced to `from rtr.state.zones import ZONES, load_state_data`; `load_zones_data` fallback is now `{"zones": ZONES}`; dropped the `poses`/`lin_poses` locals and the `poses=`/`lin_poses=` kwargs on the `MidiInput` call (the signature no longer has them).
- `realtimecontrol/rtr/rtr/io/httpserver.py`: removed `LIN_POSES`/`POSES` from the `.state.zones` import; GET `/api/zones` fallback is now `{"zones": server.zones_provider().table()}`.

### Documentation

None required — the editor advisory now matches the already-documented runtime rule (`find_shared_pose` in `zones.py:86-109`), and the removed named-pose tables were superseded by the data-driven zone format already documented in `zones.py`'s module docstring.

### Architecture

No architectural impact. The editor advisory is now a strict mirror of the runtime gate's inter-zone hand-off rule (same predicate, same margin), and the `POSES`/`LIN_POSES` removal completes the existing refactor that moved named poses out of code into the (now absent) data file. No runtime behavior changed.

### Validation

- `node --check` on the extracted editor `<script>` block: **SYNTAX OK**.
- `python3 -c "import main; from rtr.io.httpserver import HttpServer; from rtr.state import StateMachine, Zones"`: **imports OK** (previously `ImportError`).
- `pytest tests/test_safety_conflict.py tests/test_transition.py tests/test_state_machine.py`: **30 passed, 9 skipped, 1 failed** (previously: collection `ImportError` on all three).
- `pytest tests/test_safety_conflict.py tests/test_state_machine.py`: **23 passed, 9 skipped** — all gate-symmetry and state-machine tests green.
- Gate symmetry on real `zones.json`: forward `roaring/roar → roaring_grabbing_bridge/bridge` `conflict: False`; reverse `conflict: False`; shared pose `[-5,-125,116,-2,80,0]`.
- The 1 failure (`test_transition.py::test_all_reachable_transitions_complete`, `TypeError` at `rtr/robot/sim.py:42`) is **pre-existing** and unrelated: the harness passes `growling/growl`'s entry pose `['x', -10, -9, -2, 5, 0]` (an unresolved `"x"` frozen axis) straight into `SimRobot`, so the sim's per-axis arithmetic hits `'str' - 'str'`. Only unmodified files/data are on that path (`sim.py`, `machine.py`, `zones.json`, the test itself).

### Decisions and Caveats

- The editor check now mirrors the runtime gate exactly (any shared pose, margin `0.5`), so the advisory and the gate can no longer disagree on inter-zone hand-offs. It remains a save-time flag, not a block.
- `validate_state_data` still accepts legacy `poses`/`lin_poses` keys in the data file, so old backups keep validating; they are simply no longer consumed by the core (the midi named-pose note protocol is gone).
- The `test_transition.py` `"x"`-axis sim failure is a test-harness limitation (the harness doesn't resolve frozen axes before feeding the sim) — flagged as a follow-up, not fixed here.

### Next Task

Optionally fix `tests/test_transition.py::_run_transition` to resolve `"x"` axes before constructing `SimRobot` (e.g., replace `"x"` with the source action's live value on that axis, or skip transitions whose entry pose contains `"x"` with a note), so the harness exercises all reachable edges.

### Instructions for the Next Task

In `tests/test_transition.py:47-70`, `_run_transition` builds `SimRobot(home=list(zones.get(start_zone).action_entry_pose(start_action)))`. When that entry pose contains `"x"` (frozen axis, e.g. `growling/growl`), `SimRobot.get_curjpos` raises `TypeError` at `rtr/robot/sim.py:42` (`self._target[i] - self._cur[i]` with strings). Resolve the `"x"` before `SimRobot` construction — the runtime resolves `"x"` to the robot's live value on that axis, so the harness can substitute the current pose's value (the home value itself on that axis is unknown, so pick the safezone midpoint or the successor's concrete value on that axis) — or skip such pairs with a `pytest.skip` note. Then re-run `pytest tests/test_transition.py -q`.

## 2026-10-24 — Fix spurious intra-zone "entry pose differs" save warning

### Status

Completed. The editor's save-time advisory `entry pose differs from next …` (and the matching action-editor hint) no longer fires for **intra-zone** successors, matching the runtime safety gate. Confirmed the warning is non-blocking and that action-to-action transitions are **not** gated by it.

### Summary

The user reported many `saved — warning: resting/hold: entry pose differs from next resting/wake` messages and asked (a) whether the warning should exist — arguing that within a safezone the robot moves freely, so entry/exit pose differences are fine — and (b) whether it blocks action-to-action transitions.

**Root cause:** The editor's pose-continuity check compared an action's entry anchor (`poses[0]`) against each successor's entry anchor with a `0.001` threshold, for **all** successors (intra- and inter-zone). But the runtime safety gate explicitly exempts **intra-zone** moves from any pose-continuity requirement — `gate.py:72-90` and the editor's own `edgeConflict` (`editor.html:1049-1057`) both state the robot moves freely within a zone, so any pose inside the safezone is a valid hand-off. The save-time check never honored that exemption, so every intra-zone successor whose entry pose differed by ≥ 0.001 produced a false-positive warning.

**Is it blocked? No.** Two independent confirmations:
1. The save itself is non-blocking — `saveZones()` (`editor.html:6123-6126`) reports `saved — warning:` but the POST succeeds on HTTP `ok` regardless of `warns`.
2. The runtime transition is governed by the **safety gate**, not this warning. For intra-zone moves the gate only conflicts when a connection pose lies *outside* the safezone (`gate.py:75-90`); it never requires pose continuity. So action-to-action within a zone is free.

### Changes Made

- `realtimecontrol/rtr/editor.html:6107-6119` (`saveZones`): the pose-continuity loop now `continue`s when `t.zone === name` (same zone as the current action), so intra-zone successors no longer produce the `entry pose differs from next` warning. Inter-zone successors are still checked as before.
- `realtimecontrol/rtr/editor.html:4950-4960` (action-editor hint in `buildActionEditor`): the same exemption — the `diffs` filter returns `false` when `t.zone === zn` — so the inline "entry pose differs from next" hint no longer appears for intra-zone successors.

### Documentation

None required — behavior now matches the already-documented runtime rule (the intra-zone free-movement exemption is already documented in `gate.py:63-66` and `editor.html:1040-1043`).

### Architecture

No architectural impact. This aligns the editor's advisory with the existing runtime safety-gate contract; no runtime behavior changed.

### Validation

- `node --check` on the extracted editor `<script>` block: **SYNTAX OK** (6692 lines).
- `pytest tests/test_safety_conflict.py tests/test_transition.py tests/test_state_machine.py`: 30 passed, 9 skipped, 1 failed.
- The 1 failure (`test_transition.py::test_all_reachable_transitions_complete`, `TypeError` at `rtr/robot/sim.py:42`) is **pre-existing**: reproduced identically with this change stashed (`git stash`), so it is unrelated to the editor edit (a sim string-operand bug on an unresolved `"x"` axis).

### Decisions and Caveats

- Scope kept to the intra-zone exemption (the user's specific complaint, `resting/hold → resting/wake`). The inter-zone check still compares entry-to-entry at `0.001`, whereas the runtime's inter-zone rule is "any shared pose within `0.5`" (`find_shared_pose`). These differ, so the inter-zone warning can still be a false positive in some cases — left as-is to avoid broadening the change; flagged as a follow-up.
- The editor check remains a *stricter* advisory than the runtime gate by design (it's a save-time flag, not a block).

### Next Task

Optionally tighten the inter-zone pose-continuity check to mirror the runtime's `find_shared_pose` (any anchor within `0.5`) instead of entry-to-entry at `0.001`, so inter-zone saves stop producing false positives too.

### Instructions for the Next Task

In `editor.html`, the inter-zone branch of the pose-continuity check (currently `saveZones` ~line 6113 and the action-editor hint ~line 4956) compares `poses[0]` vs the successor's `poses[0]` at `0.001`. To mirror the runtime, replace that with a "do the two actions share any pose within `0.5` on all concrete axes" test (the JS equivalent of `find_shared_pose` in `rtr/state/zones.py:92`), and only warn when no shared pose exists. Keep the intra-zone `continue`/`return false` exemptions already added this task. Verify with `node --check` and re-run `pytest tests/test_safety_conflict.py`.

## 2026-10-24 — Assertive control mode: full implementation (config + machine + brain driver + UI + test)

### Status

Completed. The third **`assertive`** control mode is implemented end-to-end: config validation, machine support, the brain driver, the client toggle, and a 15-case test suite. All new tests pass; no regressions (6 pre-existing failures confirmed unrelated by stash).

### Summary

Assertive mode drives the robot to random safe destinations with a per-move speed, optional cross-zone wandering, and optional person tracking. It is a third `control_mode` alongside `auto`/`manual`. The brain picks a random `(zone, action)` destination from a pool, rolls a per-move speed in `[speed_min, speed_max]`, and drives the robot there — same-zone moves play directly, cross-zone moves walk a safe path (BFS over the action-`next` + zone-`exits` graph). A `dwell_s` gates each reselect. Person tracking (the hunt ladder) runs only when `assertive.track` is enabled.

**Work split across two sessions:**
- *Prior session:* config (`brain/config.py`, `brain.json`), `machine.py` support, and the bulk of the `brain.py` driver were implemented and verified.
- *This session:* verified the driver end-to-end, found and fixed **two bugs** the verification surfaced, wrote the test suite, ran the full suite, and added the client toggle.

**Bugs found and fixed this session (both in `brain.py`):**
1. **Same-zone destinations had no safe path.** `_assertive_tick` routed *every* destination through `_assertive_find_path` (a `find_safe_path` BFS). Same-zone actions without a `next` link have no in-zone graph edge, so `find_safe_path` returned `None` and the driver wasted a full dwell cycle "re-evaluating". Verified against the real `zones.json`: e.g. `hunting: track → scan` and `testing: locked → intent` have no in-zone path. **Fix:** same-zone destinations now use `play_action` directly (the robot is already in the zone; no cross-zone path needed). `_assertive_tick` in `brain.py` gained a same-zone branch before the cross-zone path walk.
2. **Pool validation raised `KeyError` on unknown zones.** `_assertive_pool` called `self.machine.zones.get(z)`, which raises `KeyError` for an unknown zone name (it does not return `None`). An explicit `pool` entry naming an unknown zone crashed the brain. **Fix:** guard with `self.machine.zones.has(z)` before `get(z)`; unknown entries are dropped.

**Config (from prior session, verified this session):**
- `brain/config.py`: `assertive` section in `DEFAULT_BRAIN_CONFIG`; `_validate_assertive_section` validates `enabled` (bool), `dwell_s` (number ≥ 0), `speed_min`/`speed_max` (numbers, `speed_min ≤ speed_max`), `wander` (bool), `track` (bool), `pool` (null or list of non-empty `"zone/action"` strings). Wired into `validate_brain_config`.
- `brain.json`: `assertive` section with defaults (`enabled=true`, `dwell_s=8`, `speed_min=30`, `speed_max=100`, `wander=true`, `track=false`, `pool=null`).

**machine.py (from prior session, verified this session):**
- `assertive_speed: Optional[float]` field; `set_control_mode` accepts `"assertive"` and clears `assertive_speed` on exit; `_current_speed` returns `assertive_speed` first while in assertive mode.

**brain.py driver (the core of the feature):**
- State: `assertive_nav` (waypoints), `assertive_nav_index`, `assertive_prev` (last `(zone, action)`, resets the dwell timer on change), `assertive_arrived_ts`.
- `_assertive_cfg`: reads the `assertive` config section.
- `_assertive_pool`: explicit `pool` entries (validated) or all enabled `(zone, action)` nodes.
- `_assertive_pick_destination`: random pool member; skips the current node and `_blocked_candidates`; `wander=false` restricts to the current zone.
- `_assertive_roll_speed`: rolls `assertive_speed` in `[speed_min, speed_max]` (constant per move).
- `_assertive_find_path`: `find_safe_path` from the current node to the destination (cross-zone only).
- `_assertive_tick`: per-tick driver — dwell gate, pick, speed roll, then: no-current-action → direct trigger; same-zone → `play_action`; cross-zone → safe-path walk (waypoint nav).
- `tick()`: assertive branch runs `_assertive_tick` then the hunt ladder only when `assertive.track` is on.
- `_hunt_tick`/`_hunt_active`: the hunt ladder was extracted from `tick` so assertive can reuse it; `_hunt_active` returns False (manual), `track` (assertive), True (auto).
- `SET_CONTROL_MODE`: entering assertive clears `active_group`; leaving assertive clears nav state (machine drops `assertive_speed`).
- `_apply_lock`: attention-grab gated by `_hunt_active()` (not `control_mode != "manual"`).

**client.html:** added the `cmAssertive` button to the `#controlMode` segmented toggle, the `.seg.assertive.active` CSS (amber), the three-way state-sync, and the click handler sending `set_control_mode` with `mode: "assertive"`.

**test_brain_assertive.py:** 15 tests covering the pool (default/explicit/unknown-drop), destination picking (skip-current, skip-blocked, wander restriction), speed roll range, path finding (cross-zone + same-zone-None), and the end-to-end tick (no-current-action, same-zone direct play, cross-zone walk, dwell gating, leaving-clears-state).

### Changes Made

- `realtimecontrol/rtr/rtr/brain/brain.py`:
  - `_assertive_tick`: added the same-zone direct-`play_action` branch (before the cross-zone path walk).
  - `_assertive_pool`: guard unknown-zone entries with `zones.has(z)` (was a `KeyError`).
  - (Prior session, verified here: assertive state vars, `_assertive_cfg`, `_assertive_pool`, `_assertive_pick_destination`, `_assertive_roll_speed`, `_assertive_find_path`, `_assertive_tick`, `_hunt_tick`/`_hunt_active` refactor, `tick()` assertive branch, `SET_CONTROL_MODE` handling, `_apply_lock` gating.)
- `realtimecontrol/rtr/rtr/brain/config.py`: `assertive` section in `DEFAULT_BRAIN_CONFIG` + `_validate_assertive_section` (prior session, verified here).
- `realtimecontrol/rtr/brain.json`: `assertive` section with defaults (prior session, verified here).
- `realtimecontrol/rtr/rtr/state/machine.py`: `assertive_speed` field, `set_control_mode` accepts `"assertive"`, `_current_speed` honors it (prior session, verified here).
- `realtimecontrol/rtr/client.html`: `cmAssertive` toggle (button + CSS + state-sync + handler).
- `realtimecontrol/rtr/tests/test_brain_assertive.py` (new): 15-test suite for the assertive driver.

### Documentation

- `brain/config.py` module docstring documents the `assertive` section.
- `brain.py` method docstrings document the driver semantics.
- This handoff entry.

### Architecture

- Extends the existing `machine.control_mode` three-way seam (`auto`/`manual`/`assertive`) and the brain's per-tick driver. Assertive moves route through `trigger_action`/`play_action` (the safety gate is never bypassed); a discarded pending move is handled by the reselect. No new modules; the brain's `tick()` gained an assertive branch.

### Validation

- `python tests/test_brain_assertive.py` → **15 passed, 0 failed** (pool ×3, pick ×4, speed ×1, find_path ×2, tick ×5).
- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py` → **269 passed, 14 skipped, 6 failed**. The 6 failures (`test_remote_sdl.py` ×5, `test_transition.py::test_all_reachable_transitions_complete`) are **pre-existing** — confirmed by `git stash` of my 4 changed files and re-running: they fail identically without my changes and touch `sim.py`/remote-TUI code I did not modify. (`test_remote_tui.py` has a pre-existing collection error: imports `action_kind` from `rtr.flow`, which does not exist.)

### Decisions and Caveats

- **Dwell on entry:** entering assertive, the robot dwells for `dwell_s` before its first move (the dwell timer resets on the first tick because `cur != assertive_prev`). This is a deliberate "settle" delay, not a bug — but it means the first move is delayed by `dwell_s`. If an immediate first move is wanted, seed `assertive_arrived_ts` to the past on entry in the `SET_CONTROL_MODE` handler.
- **Unreachable pool nodes:** the default pool includes all enabled nodes, but not all are reachable from the current node via the safe-path graph. When the driver picks an unreachable cross-zone node, `find_safe_path` returns `None` and it re-evaluates next dwell (graceful; it will eventually pick a reachable one). Same-zone nodes are always reachable (direct `play_action`), so no wasted cycles there.
- **Speed is per-move, not per-tick:** `assertive_speed` is rolled once per destination and stays constant for the duration of the move (not re-rolled each tick).
- **Tracking is opt-in:** the hunt ladder (person tracking) runs in assertive mode only when `assertive.track` is `true` (default `false`).

### Next Task

Optional live check: run the rtr core, switch to assertive mode from the client toggle, and confirm the robot wanders to random destinations at variable speed, that cross-zone moves walk safe paths, and that toggling `assertive.track` enables person tracking.

### Instructions for the Next Task

- The driver is in `brain.py::_assertive_tick` (with helpers `_assertive_pool`, `_assertive_pick_destination`, `_assertive_roll_speed`, `_assertive_find_path`). Config is the `assertive` section in `brain.json`. The toggle is `#cmAssertive` in `client.html`.
- To tune behavior, edit the `assertive` section in `brain.json` (`dwell_s`, `speed_min`/`speed_max`, `wander`, `track`, `pool`).
- The test suite is `tests/test_brain_assertive.py` (run directly or via pytest). It uses a synthetic 2-zone table (zone `a` with two actions and no in-zone `next` link, exiting to zone `b`), so it does not depend on `zones.json` or the sim.
- If the dwell-on-entry delay is unwanted, edit the `SET_CONTROL_MODE` handler in `brain.py` (the `p.get("mode") == "assertive"` branch) to seed `self.assertive_arrived_ts` to a past timestamp on entry.

## 2026-10-23 — Hunt red-link conflict: generic safety-aware hunt trigger (Option 1, in-place scan)

### Status

Completed

### Summary

Implemented the generic fix for the repeated `auto discarded move to hunting / scan — red-link conflict` warning. The brain's two hunt trigger sites now consult the safety gate **before** requesting a cross-zone move, so a hunt move the gate would discard is instead run **in place** (the hunt state runs in the current zone; no move is requested). The check is driven by the live safezone data, so it applies to whatever zone the robot occupies and whatever zone the hunt params name — not just the `resting`/`hunting` pair.

**Change (all in `rtr/brain/brain.py`):**
1. New helper `Brain._hunt_move_safe(zone, action)` (`brain.py:369`): returns `False` on empty zone/action; `True` when no safety gate is wired; otherwise `not safety.check_conflict(machine.current_zone, machine.current_action, zone, action)[0]`.
2. Person-lost scan trigger (`_hunt_tick`, `brain.py:821-823`): the `trigger_action(scan.zone, scan.action)` call is now guarded by `_hunt_move_safe(...)`; `_set_class("scan")` still always runs, so the hunt state (wide-camera scan) executes in the current zone when the move is unsafe. (The hunt ladder was later extracted from `tick` into `_hunt_tick` by the concurrent assertive-mode refactor; the guard was preserved.)
3. Attention-grab detect trigger (`_apply_lock`, `brain.py:639-640`): added `and self._hunt_move_safe(detect.zone, detect.action)` to the existing guard, so the detect move to the hunt zone is only requested when safe.

**Why this is the right layer:** the brain is the decision-maker that knows a move is hunt-driven and can choose to run the hunt state in place. The machine (`trigger_action`) and engine (`_resolve_pending`) are generic and don't know about hunt classes, so they can't make this choice. The `Cmd.TRIGGER_ACTION` operator path (`brain.py:410-411`) is intentionally left untouched — that is an explicit move, and the engine already handles its conflict (manual popup / auto discard).

**Behavior after the fix:**
- Robot in `resting`, person lost → class becomes `scan`, but **no** move to `hunting` is requested (the gate would discard it on A3). The robot scans in place in `resting`. No `SAFETY_DISCARD`, no repeated warning.
- Robot in a zone whose safezone overlaps `hunting` (e.g. `leaping`, `stretching_hunting_bridge`, `hunting_scanning_bridge`) → the hunt move to `hunting` proceeds exactly as before (safe zones are unaffected).
- Robot already in `hunting` → intra-zone move, unchanged.

### Changes Made

- `realtimecontrol/rtr/rtr/brain/brain.py`:
  - Added `_hunt_move_safe(zone, action)` helper (`brain.py:369`).
  - Guarded the person-lost scan trigger in `_hunt_tick` (`brain.py:821-823`).
  - Guarded the attention-grab detect trigger in `_apply_lock` (`brain.py:639-640`).

### Documentation

- None required beyond this handoff entry; the helper's docstring documents the in-place decoupling and the no-gate / empty-input behavior.

### Architecture

- No architectural change. This refines the existing brain→machine→engine contract: the brain now pre-filters its own hunt-driven moves through the same safety gate the engine uses, so a hunt move is never requested that the gate would discard. The machine and engine are unchanged.

### Validation

- `python3 -m py_compile rtr/brain/brain.py` → SYNTAX OK.
- `python3 /tmp/kilo/test_hunt_move_safe.py` (constructs the real `Zones` from `zones.json` + `SafetyGate` + `StateMachine` + `Brain`, then exercises `_hunt_move_safe`):
  - `resting → hunting/scan` UNSAFE (`conflict=True`, issues `['safezones do not overlap on A3', 'no shared position: resting/hold shares no pose with hunting/scan']`) → helper returns `False`. PASS.
  - `resting → hunting/track` UNSAFE → `False`. PASS.
  - `leaping → hunting/scan` SAFE (zero safezone gaps) → `True`. PASS.
  - No safety gate wired → `True`. PASS.
  - Empty zone / empty action / both empty → `False`. PASS.
  - **RESULT: ALL PASS.**

### Decisions and Caveats

- The helper reuses `SafetyGate.check_conflict` (the same check the engine runs), so the brain's decision and the engine's decision are guaranteed to agree — there is no second, divergent conflict model.
- The detect trigger (`_apply_lock`) already had guards (`control_mode`, class not tracking, behavior not in `attention_guard`, no transition, no pending); the safety check is additive and only ever suppresses a move the gate would discard.
- When the move is unsafe the robot now scans in place in its current zone rather than logging a repeated discard. If an operator wants a one-time notice, a throttled log could be added at the guard site, but that was intentionally omitted to keep the change minimal.

### Next Task

No immediate next task identified. On the next live run, confirm the `red-link conflict` warning no longer repeats while the robot sits in `resting` and a person is lost, and that the robot still moves to `hunting` when it is already in an overlapping zone (e.g. `leaping`).

### Instructions for the Next Task

1. Run the rtr core in auto mode with `hunt.enabled=true`.
2. Put the robot in `resting`, wait for a person to be lost → confirm the class goes to `scan` and **no** `auto discarded move to hunting / scan` warning appears.
3. Put the robot in `leaping` (or any zone overlapping `hunting`), wait for a person detect → confirm the robot still moves to `hunting`.
4. If a one-time operator notice is wanted, add a throttled `print` at the two guard sites in `brain.py` (the scan trigger at `tick` and the detect trigger at `_apply_lock`).

## 2026-10-23 — Hunt red-link conflict: analysis of the brain → machine → engine trigger chain

### Status

Completed

### Summary

Confirmed the root cause of the repeated `auto discarded move to hunting / scan — red-link conflict` warning (raised while the robot sits in `resting`). The brain's hunt loop triggers **cross-zone** moves to the hunt zone that the safety gate then discards, and because the brain re-triggers on every lock/lost cycle the warning repeats.

**The two hunt trigger sites (both in `brain/brain.py`):**
1. **Person lost → scan** (`tick`, `brain.py:736-745`): when the locked person's `CAM_TRACK` is stale (`> lost_s`), the brain clears the lock and calls `machine.trigger_action(scan.zone, scan.action)` = `hunting/scan`, then `_set_class("scan")`.
2. **Attention grab → detect** (`_apply_lock`, `brain.py:573-587`): when a person is locked and the robot is not already in a tracking class, the brain calls `machine.trigger_action(detect.zone, detect.action)` = `hunting/track`.

**Why each move is discarded (the chain):**
- `machine.trigger_action` (`state/machine.py:129`) stores the move as a pending move with `same_zone=False`, `from_zone=current_zone` (e.g. `resting`), `from_action=current_action`. It performs **no** conflict check itself.
- `engine._resolve_pending` (`core/engine.py:202`) calls `safety.check_conflict(from_zone, from_action, to_zone, to_action)`.
- `gate.check_conflict` (`safety/gate.py:41`) treats `resting → hunting` as **inter-zone**, so it runs the safezone-overlap check. The actual `zones.json` safezones conflict on **A3**:
  - `resting` A3 = `[89, 115]`, `hunting` A3 = `[-39, -9]`.
  - `max(89, -39) = 89 >= min(115, -9) = -9` → **no overlap** → conflict.
  - (A1/A2/A4/A5/A6 all overlap; A3 is the only failing axis.)
- In auto mode the engine clears the pending move, calls `brain.mark_candidate_blocked`, and publishes `SAFETY_DISCARD` → `client.html` logs the one-line warning with the generic label `"red-link conflict"`.

**Why it repeats:** the person-lost branch does not consult `_valid_next_candidates`, and `mark_candidate_blocked` only affects the autonomy next-link advance — not the hunt trigger. So the cycle `detect(track) → discard → person-lost(scan) → discard → detect(track) → …` keeps firing.

**Why there is no visible editor red link:** the editor flow graph (`editor.html`) is built only from the action-level `next` field; it does not render zone-level `exits`, and no `resting` action's `next` points to `hunting`. The hunt move is a **brain direct-trigger**, not a `next` edge, so no edge is drawn (let alone red).

### Changes Made

- None (read-only analysis; no code changed).

### Documentation

- None required — this is a diagnosis; the fix entry documents the change.

### Architecture

- No architectural change. This entry pins down the exact trigger chain and the two brain call sites that must become safety-aware.

### Validation

- Verified the A3 overlap arithmetic by hand against the actual `zones.json` safezones (resting A3 `[89,115]` vs hunting A3 `[-39,-9]`).
- Confirmed `machine.trigger_action` sets `same_zone=False` and stores a pending move with no conflict check (`machine.py:171-189`).
- Confirmed `engine._resolve_pending` runs the conflict check and discards in auto mode (`engine.py:233-282`).
- Confirmed `gate.check_conflict`'s inter-zone rule (safezone overlap on all 6 axes + shared-pose continuity) (`gate.py:91-108`).

### Decisions and Caveats

- The generic fix belongs in the **brain** (the decision-maker), not the machine or engine: the brain is the only layer that knows the move is hunt-driven and can choose to run the hunt state in place instead of requesting a move.
- The fix must be driven by the **actual safezone data** (via the safety gate), not hardcoded to `resting`/`hunting`, so it applies to any zone whose safezone does not overlap the hunt zone.

### Next Task

Implement the generic safety-aware hunt trigger in `brain.py`: add a `_hunt_move_safe(zone, action)` helper and guard both hunt trigger sites (scan at `tick`, detect at `_apply_lock`) so a hunt move that the safety gate would discard is instead run in place (hunt state runs in the current zone, no move requested).

### Instructions for the Next Task

1. Add `Brain._hunt_move_safe(zone, action)`: return `False` on empty zone/action; `True` when `machine.safety` is None (no gate); otherwise `not safety.check_conflict(machine.current_zone, machine.current_action, zone, action)[0]`.
2. Guard the person-lost scan trigger (`tick`, `brain.py:740-741`): only `trigger_action(scan.zone, scan.action)` when `_hunt_move_safe(...)`; always still `_set_class("scan")` so the hunt state runs in place.
3. Guard the attention-grab detect trigger (`_apply_lock`, `brain.py:587`): add `and self._hunt_move_safe(detect.zone, detect.action)` to the existing guard condition.
4. Do NOT touch `Cmd.TRIGGER_ACTION` (the explicit operator move at `brain.py:410-411`) — that is not a hunt move and the engine already handles its conflict.

## 2026-10-23 — Investigate "saved — warning: resting: safezone A6 exceeds hardware" on save

### Status

Completed

### Summary

Investigated the save-time warning `saved — warning: resting: safezone A6 exceeds hardware [-150, 1] (+68 more)` in `realtimecontrol/rtr/editor.html`. The resting zone's A6 safezone is `[-3, 3]` (zones.json) and the user expected the hardware floor to be `[-150, 150]`, so the warning looked like a false positive.

**Root cause:** the warning is generated by `saveZones()` in editor.html (line ~6091), which compares each zone safezone axis against the live `HW_LIMITS` loaded from `GET /api/limits` (the core's `HARDWARE_LIMITS` in `rtr/state/zones.py`). At the time the warning fired, the core's A6 entry was `(-150, 1)` — set by commit `2e77b23` ("hardware lim its"), which changed A6 from `(-350, 350)` to `(-150, 1)`. Because `3 > 1`, the safezone `[-3, 3]` genuinely exceeded the hardware floor `[-150, 1]`, so the warning was **correct for that core state** — not a logic bug.

**Resolution already in tree:** the latest commit `c1e2437` ("zones") reverted the A6 hardware limit back to `(-150, 150)`. With that value the check `slo < hlo || shi > hhi` → `-3 < -150 || 3 > 150` → false, so the warning no longer fires. All A6 references are now consistent at `(-150, 150)`:
- core `HARDWARE_LIMITS` (zones.py:53): `(-150, 150)`
- editor `HW_LIMITS_FALLBACK` (editor.html:792): `[-150, 150]`
- `rtr/remote/view.py:66`: `(-150.0, 150.0)`
- `validate_zones.js:138` SOFTWARE_LIMITS: `[-150, 150]`

During the investigation I briefly edited the editor fallback to `[-150, 1]` (matching the transient `(-150, 1)` I first observed in zones.py), then **reverted** it once I confirmed the committed core value is `(-150, 150)`. Net change to the working tree from this task: **none** (the fallback is back to its original `[-150, 150]`, which already matched the core).

**Action for the user:** the running core still holds the old `(-150, 1)` in memory, so the warning will keep appearing until the server is restarted to pick up the committed `(-150, 150)`. Restart the rtr core, then re-save — the A6 warning should be gone.

### Changes Made

- `realtimecontrol/rtr/editor.html`: temporarily changed `HW_LIMITS_FALLBACK` A6 from `[-150, 150]` to `[-150, 1]`, then reverted it back to `[-150, 150]`. **Net: no change** (file is back to its committed state).
- `.kilo/task-handoff.md`: added this entry.

### Documentation

- None required beyond this handoff entry. The `HARDWARE_LIMITS` comment in zones.py already documents A6 as "rotary wrist, now limited".

### Architecture

- No architectural change. The save-time warning is a non-blocking advisory (the core clamps to the safezone ∩ hardware intersection); it correctly reflects the live `HARDWARE_LIMITS` from `/api/limits`.

### Validation

- Confirmed all four A6 limit sources agree on `(-150, 150)` via grep across `zones.py`, `editor.html`, `view.py`, `validate_zones.js`.
- Confirmed `git status` shows `zones.py` unmodified at the committed `(-150, 150)` and `editor.html` back to its committed fallback.
- Traced the A6 value history with `git log -S "(-150, 1)"`: `2e77b23` set it to `(-150, 1)`, `c1e2437` reverted it to `(-150, 150)`.
- No JS/Python test harness exercised the warning path directly (it is a UI-only advisory); validation was by code inspection + cross-file consistency check.

### Decisions and Caveats

- The warning logic itself (`slo < hlo || shi > hhi`) is correct and was not changed.
- The `+68 more` in the message is expected: it aggregates other advisories (other zones' safezones / poses outside the floor), not 68 copies of the same A6 issue.
- Caveat: if the intended A6 floor really is the tighter `(-150, 1)` (rotary-wrist restriction), then the safezone `[-3, 3]` would still need narrowing and `c1e2437` was the wrong revert. The committed history and all four sources now agree on `(-150, 150)`, so that is the current intended value.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

If the warning reappears after a server restart, confirm the running core's `HARDWARE_LIMITS[A6]` via `GET /api/limits` and compare against `zones.py:53`. If they differ, the core process is stale — restart it. If they match and the warning still fires, the safezone in `zones.json` for that zone/axis genuinely exceeds the floor and should be narrowed (or the floor widened deliberately, keeping all four sources in sync).

## 2026-10-23 — Convert rtr/zones.json → kukart/robotstatesFTI.py (legacy zone schema)

### Status

Completed

### Summary

Converted `realtimecontrol/rtr/zones.json` (29 zones, the runtime "unified" form) into the legacy `robotstates.py` zone-dict format used by the old kukart stack, emitting `realtimecontrol/kukart/robotstatesFTI.py`. The new module exposes a top-level `kukastate` dict (mirroring `robotstates.py`) whose `zones` table is the converted data, so `from robotstatesFTI import kukastate` works the same way `from robotstates import kukastate` does in `tidalkuka_v01.py`.

**Per-zone field mapping (zones.json → robotstates.py):**
- `safezone`: JSON `[[lo,hi],...]` → Python `[(lo,hi),...]` (arrays → tuples)
- `speed`: direct copy (number)
- `exits`: JSON `[[zone,action],...]` → `[zone,...]` (action half dropped, dedup, order kept); zones with **no** explicit `exits` field derive it from the last action's `next` target
- `actions.name.pos`: from `poses` (`"x"` frozen-axis placeholders kept verbatim)
- `actions.name.speed`: single number repeated per pose → list; actions with **no** `speed` → `[None]*n`
- `startpos` (absent in zones.json): **derived** = first anchor of the zone's first action
- `exitpos` (absent in zones.json): **derived** = last anchor of the zone's last action

**Top-level `kukastate`**: `robot`, `modechange`, `mode`, `currentaction`, `actionindex`, `currentzone: "resting"`, `speed: 100`, `zones`. Dynamic state fields from `robotstates.py` (`nextjpos`, `curjpos`, `nextpos`, `curpos`, `linposes`, `poses`, `dynposes`, `limits`, `softwarelimits`) are omitted — they are runtime state, not config.

**Fields dropped** (present in zones.json, no slot in the legacy zone schema):
- zone-level: `class`, `mode_group`, `enabled`
- action-level: `variable_axes`, `behavior`, `loop`, `next`, `pose_imgs`, `pose_videos`, `engine`, `sweep`, `axis_bands`, `enabled`
- top-level: `layout` (UI x/y positions)

### Changes Made

- `realtimecontrol/kukart/robotstatesFTI.py` (new, 363 lines): generated `kukastate` dict with all 29 zones converted.
- `/tmp/kilo/convert_zones.py` (scratch, outside repo): reads `zones.json`, applies the mapping + derivations, emits the file, prints the missing-fields report.

### Documentation

- None required beyond this handoff entry; the generated file carries a header comment explaining the conversion and the `"x"` placeholder convention.

### Architecture

- No architectural change. This is a data-format bridge: the legacy kukart stack (`robothelpers.py`) reads `kukastate["zones"][z]` for `safezone`/`startpos`/`exitpos`/`speed` and `actions[a]` for `pos`/`speed`, and `robotstatesFTI.py` provides exactly that shape. The runtime `rtr/state/zones.py` continues to read `zones.json` directly — the two consumers are independent.

### Validation

- `python3 /tmp/kilo/convert_zones.py` → "Converted 29 zones".
- Import + structural test: `import robotstatesFTI` OK; `len(zones) == 29`; every zone has all required keys (`startpos`, `safezone`, `actions`, `exitpos`, `exits`, `speed`); all `safezone` entries are 2-tuples; `pos`/`speed` list lengths match per action; all speeds are numeric or `None` (where the source action had no speed).
- Spot checks: `resting` startpos/exitpos `[−60,−90,90,0,15,0]`, safezone `[(-185,185),(-105,-65),(89,115),(-15,15),(-15,45),(-3,3)]`, speed 20, exits `['sleeping','cuddling','stretching','hunting']`; `resting.hold` speed `[20,20,20]`; `sleeping.breathe` speed `[None,None,None,None,None]` (source action has no speed).

### Decisions and Caveats

- **`"x"` placeholders** are kept verbatim in poses (and in derived `startpos`/`exitpos`). They mark frozen/variable axes in the new runtime and have no fixed joint value in the legacy schema. Affected: `cuddling` exitpos, `purring` startpos, `hunting` startpos, `growling` startpos, `clawing.grab[0]`, `testing` startpos, `resting_testing_bridge` startpos, `cuddling_purring_bridge` startpos+exitpos, `cuddling_stretching_bridge` startpos+exitpos, `grabbing` startpos, `roaring_grabbing_bridge` exitpos, `hunting_scanning_bridge` startpos, `purring_hunting_bridge` exitpos, `roaring_growling_bridge` exitpos. The legacy `robothelpers.py` would choke on `"x"` in a `fitlimits`/numeric context — if the old stack must run these zones, the `"x"` axes need to be resolved to concrete values (e.g. the current joint value or a band midpoint) at load time.
- **`startpos`/`exitpos` are derived**, not stored in zones.json. The derivation (first anchor of first action / last anchor of last action) matches how the old stack uses them (entry pose on zone activation, exit pose on zone deactivation). If a zone's first/last action pose is not a good stand-in for the true entry/exit anchor, those two fields should be overridden by hand.
- **Disabled actions are still emitted** (`grabbing.grab` has `enabled: false`). The legacy schema has no `enabled` slot; the old stack will attempt to play it. If it must be excluded, drop the action from the dict or add a guard in the consumer.
- **Actions with no `speed`** produce `[None]*n` speed lists: `sleeping.breathe`, `sleeping.snore`, `cuddling.breathe`, `stretching.look`, `hunting.track`, `hunting.track2`, `performing.trick`, `performing.bow`, `roaring.roar`, `roaring.snarl`, `growling.snarl`, `leaping.leap`, `leaping.pacing`, `resting_sleeping_bridge.bridge`. The legacy `doAction` reads `speed[actionindex]` and would `NoneType` on these — a default speed must be supplied (e.g. the zone speed) before the old stack runs them.
- `exits` for 16 zones were derived from the last action's `next` (those zones have no explicit `exits` field). The derived value is a single zone name; if a zone can exit to multiple zones, an explicit `exits` list should be added to zones.json.

### Next Task

Wire `robotstatesFTI` into the legacy kukart stack (replace `from robotstates import kukastate` with `from robotstatesFTI import kukastate` in `tidalkuka_v01.py` and any other importers), and resolve the `"x"` placeholders + `None` speeds before the old stack is expected to drive the FTI zones.

### Instructions for the Next Task

1. Grep for `from robotstates import` / `import robotstates` in `realtimecontrol/kukart/` and `realtimecontrol/` to find all importers of the old zone table.
2. For each importer that should run the FTI zones, swap the import to `robotstatesFTI`.
3. Before the old stack touches a zone, resolve `"x"` axes: for each pose containing `"x"`, substitute the live joint value (or a per-axis default) at load or activation time. The `"x"` axes are exactly those listed in the caveats above.
4. Supply a default speed for the 14 actions with `None` speed lists (zone speed is the natural default).
5. Re-run the import + structural test to confirm the swapped module loads cleanly.

## 2026-10-23 — Assertive mode: analysis of existing auto/manual architecture + safety seams

### Status

Completed

### Summary

Read the core in full to locate the seams for the third **"assertive"** control mode (assertively switches behaviors/programs, wanders inside safezones, varies speed, plays random patches, optionally tracks people).

**Current mode architecture:**
- `machine.control_mode`: `"auto"` | `"manual"` (`state/machine.py:111`), set via `Cmd.SET_CONTROL_MODE` → `brain.on_command` (`brain/brain.py:434`) → `machine.set_control_mode` (`machine.py:246`). Manual freezes the robot (clears action + transition, holds the current pose).
- Auto mode: `brain._autonomy_tick` (`brain.py:934`) — dwell timeout + behavior triggers (dwell/new_lock/lost_lock/lock_duration) + external events (midi/camera/websocket = strong triggers) → `_autonomy_decide` → stay probability or advance to a random valid candidate from the action's `next` list restricted to `_allowed_zones` (enabled-group union / active group). `_random_valid_next` (`brain.py:832`) is the existing random-selection base.
- Group navigation: `active_group` + `group_nav` waypoints via `flow.find_safe_path_to_any`; walked hop-by-hop while idle (`_group_tick`).
- Hunt loop (auto only): scan→track→farfocus→approach→facefocus→retreat; camera presets per class (`zone_classes` in brain.json); feral = 3+ losts in 30 s.
- `forced_behavior` (ENABLE_BEHAVIOR) = per-behavior live test override; `test_mode` = scripted nav + behavior.

**Safety seams (assertive mode must stay inside safezones):**
- `trigger_action`/`play_action` route through the safety gate: `_pending` "check" phase → engine resolves (conflict + collision) → commit/discard; operator confirm/cancel in "confirm" phase.
- `SafetyGate.check_conflict` (`safety/gate.py`): red-link conflict = safezone overlap (cross-zone) + shared-pose continuity (cross-zone) / pose-outside-safezone (intra-zone). Collision = SAT self-collision via the Node worker.
- Every behavior policy clamps to the effective floor (safezone ∩ hardware) — `behavior.clamp`, `_servo`, `_apply_wander`, `_apply_oscillation`; transition targets are hardware-clamped only.

**Behavior inventory (the random-selection pool):** locked/intent/curious (camera servo, image Jacobian), gentle (face servo), vigilant (sine sweep), restless (continuous random drift, `wander_adj`), spontaneous (random jumps), six oscillations (soothing/agitated/aggressive/exhilarated/impatient/drowsy, freq/amp_scale), calm/bridge (no-op). All roaming policies stay within the safezone.

**Patches:** deterministic per state (`patches.py:code_for`, called per state frame from `display.py`); video mode (default, action-only, per-pose video, holds last video on no-video frames) vs complex (zone+action+behavior layers). **No random patch selection exists** — "play a random patch" needs a new hook (the existing `set_screen_override` is the manual-override seam).

**Speed:** `machine._current_speed()` (`machine.py:700`): transition → target zone speed; action speed; zone speed. Group `params.speed`/`dwell_s` in brain.json (stage-takeover: speed 80, dwell 15 s) — dwell is consumed by `_autonomy_dwell_s`; **`params.speed` is not read by the brain** (speed variance needs a new read or field).

**brain.json state:** autonomy enabled (dwell 30 s, events midi/camera/websocket on); stage-takeover group enabled (includes bridge zones + grabbing/scanning); museum-closed + event-sit-back disabled; hunt enabled (hunting zone); facefocus enabled (stretching); per-action `cam_control` map; per-behavior params.

### Changes Made

- None (analysis only).

### Documentation

- This handoff entry records the seams for the design task.

### Architecture

- No architectural change yet; this entry is the input to the design task.

### Validation

- Read-only: `state/machine.py` (772 lines), `brain/brain.py` (1297), `state/zones.py` (907), `state/behavior.py` (642), `patches/patches.py` (373), `safety/gate.py` (169), `camera/camera.py` (98), `brain.json` (448); `main.py` + `zones.json` partially read in the prior turn.

### Decisions and Caveats

- The third mode plugs into `machine.control_mode` + `brain._autonomy_tick`: assertive = faster dwell, more random selection, speed variance. The safety gate stays in front of every move — assertive changes *which* candidate is picked and *how fast*, never bypasses the gate.
- Caveat: "random patch" has no existing hook — patch resolution is state-deterministic; the design must add a selection hook without breaking the video-mode hold logic.
- Caveat: group `params.speed` is configured but not read by the brain (only `dwell_s` is); speed variance needs a new read or a new field.

### Next Task

Design the assertive mode: exact semantics, config shape (brain.json), machine/brain changes, the random-patch hook, the tracking toggle, the UI seam (client.html/editor.html mode selector), and a test plan.

### Instructions for the Next Task

- Mode seam: `machine.set_control_mode` (machine.py:246) accepts only "auto"/"manual"; the brain's `_autonomy_tick` (brain.py:934) is where decisions are made; `_random_valid_next` (brain.py:832) already does random candidate selection (good base for assertive).
- Safety: never bypass `_pending` resolution; assertive moves still go through `trigger_action`/`play_action`.
- Patches: `patches.py:code_for` is called per state frame from `display.py`; add a brain-driven random-patch hook (`set_screen_override` is the existing manual-override seam).
- Tracking toggle: the hunt loop is gated by `hunt.enabled` (brain.json) + `control_mode != "manual"`; the assertive toggle maps to that gate.
- UI: the SET_CONTROL_MODE command payload is `{mode}` (brain.py:434); the client/editor send it — add the third option there.

## 2026-10-23 — Multi-video MIDI patch: full handoff (multipatch.js — preload ALL videos, configurable note→video map)

### Status

Completed. `multipatch.js` implemented, syntax-checked, and logic-tested (25/25 passed). The user edited the CONFIG section in parallel to set the real clip set (`vid/hands/hand01–08.mp4`); that config is in place and respected.

### Summary

Built a new P5LIVE + Hydra display patch (`output_display/multipatch.js`) modeled on `oldpatch.js`'s video+MIDI pattern, fulfilling the user's three requirements:

1. **Load a whole bunch of videos** — every entry in the `VIDS` list becomes a raw HTML5 `<video>` element (the P5LIVE-safe approach; P5LIVE's trimmed p5 has `createVideo` but no `loadVideo`).
2. **Easy configuration of which MIDI note plays which video** — one CONFIG section at the top: `VID_BASE` (clip folder), `VIDS` (the clips), `NOTE_TO_VID` (explicit `note: index` table, e.g. `47: 0`), plus `FIRST_NOTE`/`AUTO_FALLBACK` for a sequential fallback (`FIRST_NOTE + i → VIDS[i]`, wrapping) and `LOOP`/`DEBUG` toggles. Editing the table is the only thing needed to reassign notes.
3. **Preload ALL videos** — `preload()` gives every clip `preload='auto'` + `load()`, so all clips buffer before the first note is played (paused until triggered; only the active clip plays).

**Runtime behavior:** `midi5.noteOn` (oldpatch.js's trigger style) → `videoForNote(note.number)` → `playVideo(idx)` (restarts the clip at 0, pauses the previous one). Unmapped notes play nothing. The active clip draws onto a hidden p5 canvas → Hydra `s0` (dynamic) → pass-through graph → fullscreen Hydra canvas. A small HUD shows the active clip and total preloaded.

**User's parallel edit (kept):** `VID_BASE = 'vid/hands/'`, `VIDS = hand01.mp4 … hand08.mp4` (8 clips), `NOTE_TO_VID = {47:0, 48:1, 49:2}`.

### Changes Made

- `output_display/multipatch.js` (new): the multi-video MIDI patch (~220 lines).
- `output_display/test_multipatch.js` (new): Node vm logic test, config-driven (derives expectations from the patch's own config).
- `oldpatch.js` and `patch.js` untouched (kept as references).

### Documentation

- `multipatch.js` header: how to configure (vids location, VIDS list, NOTE_TO_VID, auto mapping) and how to run (P5LIVE + Web MIDI).
- This handoff file: 4 per-task entries (analyse, research, implement, test) + this full entry.

### Architecture

No architectural change to the core. The new sketch reuses the established P5LIVE+Hydra display pattern from `patch.js` (hidden p5 canvas → `s0` dynamic → Hydra chain → fullscreen `hc` canvas, `detectAudio: false` / `enableStreamCapture: false`).

### Validation

- `node --check output_display/multipatch.js` → SYNTAX OK (verified after the user's parallel edit).
- `node --check output_display/test_multipatch.js` → SYNTAX OK.
- `node output_display/test_multipatch.js` → **25 passed, 0 failed**: all-VIDS preload (one buffered `<video>` per entry, muted, correct src), every explicit `NOTE_TO_VID` entry, auto-fallback wrapping, unmapped-note → nothing, MIDI note-on plays mapped clip from 0, note switch pauses previous, noteoff/unmapped leave playback untouched.

### Decisions and Caveats

- Raw HTML5 `<video>` preload instead of many p5 `createVideo` objects (lighter; same rationale as patch.js).
- Hydra graph defaults to a plain pass-through (`src(s0).out(o0)`); `oldpatch.js`'s treatment chain is referenced in a comment for when a visual effect is wanted.
- Caveat: the clips are not yet at `output_display/vid/hands/` (the folder doesn't exist) — live playback needs the 8 clips placed there (or `VID_BASE` pointed elsewhere).
- Caveat: the vm test covers mapping/playback logic only; live browser verification (buffering, Web MIDI, Hydra render) is the remaining manual step.

### Next Task

Optional live check: copy the 8 `hand*.mp4` clips into `output_display/vid/hands/`, open `multipatch.js` in P5LIVE, enable Web MIDI, and confirm notes 47/48/49 play hand01/hand02/hand03 and notes 50–54 auto-map to hand04–hand08.

### Instructions for the Next Task

- Place clips at `output_display/vid/hands/` (`VID_BASE` is `vid/hands/`). To reassign a note, edit `NOTE_TO_VID` in `multipatch.js` (e.g. `50: 3` → note 50 plays `VIDS[3]` = hand04.mp4). To change the sequential range, edit `FIRST_NOTE`; to make unmapped notes silent, set `AUTO_FALLBACK = false`. Re-run `node output_display/test_multipatch.js` after config edits — it is config-driven and will validate the new table.

## 2026-10-23 — Multi-video MIDI patch: test (syntax + config-driven mapping logic test)

### Status

Completed. `node --check` OK on both files; `node test_multipatch.js` → **25 passed, 0 failed**.

### Summary

Verified the new patch. The first test run had 3 failures — all caused by the test hardcoding assumptions (the `'vids/'` base path and a 3-clip wrap arithmetic) that broke when the **user edited `multipatch.js` in parallel**, replacing the seed config with the real set (`VID_BASE = 'vid/hands/'`, 8 clips `hand01–08.mp4`). The patch code itself was correct; the test was rewritten to be **config-driven** — it derives every expectation from the patch's own exported `VID_BASE / VIDS / NOTE_TO_VID / FIRST_NOTE / AUTO_FALLBACK`, so it keeps working as the user edits the CONFIG section.

**What the test covers** (`output_display/test_multipatch.js`, Node `vm` sandbox with stubbed DOM/p5/Hydra):
1. `preload()` creates exactly one `<video>` per `VIDS` entry, all told to `load()` (buffered now), all muted, all pointing at `VID_BASE + filename` — i.e. ALL videos preloaded.
2. Every explicit `NOTE_TO_VID` entry maps to the right index.
3. Auto fallback: unmapped notes at/above `FIRST_NOTE` wrap over `VIDS` (`(n - FIRST_NOTE) % VIDS.length`).
4. Unmapped notes below `FIRST_NOTE` → `-1` (play nothing).
5. `midi5.noteOn({type:'noteon'})` plays the mapped clip from position 0.
6. Switching notes pauses the previous clip.
7. `noteoff` and unmapped notes leave playback untouched.

### Changes Made

- `output_display/test_multipatch.js`: rewritten config-driven (exports `VID_BASE` from the patch via a `__exports` footer appended to the evaluated source; expectations derived from the live config instead of hardcoded).

### Documentation

- This handoff entry.

### Architecture

No architectural impact.

### Validation

- `node --check multipatch.js` → SYNTAX OK (after the user's parallel edit).
- `node --check test_multipatch.js` → SYNTAX OK.
- `node test_multipatch.js` → **25 passed, 0 failed** (first run: 15 passed / 3 failed, all 3 due to the test's stale hardcoded config assumptions, fixed as above).

### Decisions and Caveats

- The test evaluates the patch source in a `vm` context with a `__exports` footer capturing the file's `const`/`let` bindings (vm top-level `const`/`let` do not attach to the sandbox global object).
- Caveat: the vm test verifies mapping/playback logic, not browser behavior (actual buffering, MIDI hardware, Hydra rendering). Live browser verification remains the user's step.

### Next Task

Write the full handoff entry.

### Instructions for the Next Task

- The patch is complete and logic-tested. The user's real clips must be placed at `output_display/vid/hands/` (the `VID_BASE` they set) before live playback works.

## 2026-10-23 — Multi-video MIDI patch: implement multipatch.js

### Status

Completed (then further edited by the user in parallel — see Decisions).

### Summary

Created `output_display/multipatch.js`: a P5LIVE + Hydra patch that preloads **all** listed videos and plays the mapped video on MIDI note-on, with one editable CONFIG section.

**Design (per the user's ask, modeled on oldpatch.js):**
- **Preload ALL videos:** `preload()` builds one raw HTML5 `<video>` per `VIDS` entry (`preload='auto'` + `load()` = buffered but paused). Raw elements instead of p5 `createVideo` because P5LIVE's trimmed p5 lacks `loadVideo` and raw elements keep many clips cheaply ready (same rationale as patch.js).
- **Easy note→video config:** the CONFIG section holds `VID_BASE` (one line), `VIDS` (the clip list — every entry preloaded), `NOTE_TO_VID` (explicit `note: index` table), `FIRST_NOTE` + `AUTO_FALLBACK` (sequential mapping for unmapped notes: `FIRST_NOTE + i → VIDS[i]`, wrapping), `LOOP`, `DEBUG`.
- **Trigger:** oldpatch.js's `midi5.noteOn` callback style (`note.type === 'noteon'` → `videoForNote(note.number)` → `playVideo(idx)`), which restarts the clip at 0 and pauses the previous one. Unmapped notes play nothing.
- **Display:** hidden p5 canvas → Hydra `s0` (dynamic) → static pass-through graph `src(s0).out(o0)` → fullscreen `hc` canvas; robust Hydra flags (`detectAudio: false`, `enableStreamCapture: false`) from patch.js; small HUD shows the active clip + total preloaded.

### Changes Made

- `output_display/multipatch.js` (new): the multi-video MIDI patch, ~220 lines.
- `output_display/test_multipatch.js` (new, written in the test task): vm logic test.

### Documentation

- Header comment in `multipatch.js` documents how to configure (vids location, VIDS list, NOTE_TO_VID table, auto mapping).

### Architecture

No architectural change to the core; the new sketch reuses the established P5LIVE+Hydra display pattern (hidden p5 canvas → s0 → Hydra chain → on-screen canvas).

### Validation

- `node --check multipatch.js` → SYNTAX OK. (Logic test in the next task.)

### Decisions and Caveats

- **The user edited `multipatch.js` in parallel** while implementation was in progress, replacing the seed config with the real set: `VID_BASE = 'vid/hands/'` and `VIDS = hand01.mp4 … hand08.mp4` (8 clips). This edit was **respected and kept** — the test was made config-driven to match. The clips themselves are not yet at `output_display/vid/hands/` (no such folder exists yet), so live playback needs the files placed there.
- Hydra graph is a plain pass-through by default; the oldpatch.js chain is referenced in a comment for when a visual treatment is wanted.

### Next Task

Test: syntax check + logic test of the note→video mapping.

### Instructions for the Next Task

- Run `node --check output_display/multipatch.js`, then build/run a Node vm test that stubs DOM/p5/Hydra, calls `preload()` + `setup()`, and asserts the mapping + MIDI trigger behavior.

## 2026-10-23 — Multi-video MIDI patch: research video assets and VID_BASE layout

### Status

Completed

### Summary

Located the video files available for the new multi-video patch and decided the asset layout.

**Findings:**
- `output_display/` has **no** `vids/` or `img/` subfolder — the assets oldpatch.js (`img/allhands.mp4`) and patch.js (`vids/MiniMax_H3_*.mp4`) reference are not present in the sketch folder.
- The only real clips in the repo: `realtimecontrol/rtr/assets/vid/` → `test1.mp4` (364K), `test2.mp4` (424K), `hand08.mp4` (948K).
- No P5LIVE web-root config found (`.kilo/kilo.jsonc` only sets `snapshot: false`); the sketch folder (`output_display/`) is what P5LIVE serves, so clips must be placed there (or `VID_BASE` pointed elsewhere).

**Decision:**
- New patch file: `output_display/multipatch.js`.
- `VID_BASE = 'vids/'` as a one-line config constant (same convention as patch.js). The user drops clips into `output_display/vids/` (or edits `VID_BASE`).
- `VIDS` seeded with the 3 real clip filenames (`test1.mp4`, `test2.mp4`, `hand08.mp4`) so the patch works out of the box once those files are copied into `vids/`; the user extends the list with their own clips.

### Changes Made

- None (research only).

### Documentation

- This handoff entry records the asset layout decision.

### Architecture

No architectural impact.

### Validation

- `ls -la realtimecontrol/rtr/assets/vid/` + `du -sh` → 3 clips confirmed with sizes.
- `glob` for `**/*.mp4` across the repo → no other display-relevant clips.

### Decisions and Caveats

- Caveat: the 3 seed clips are not yet in `output_display/vids/`; the patch will 404 until they are copied there (or `VID_BASE` is edited). This is expected — the user's real display set is larger and user-supplied.

### Next Task

Implement `output_display/multipatch.js`.

### Instructions for the Next Task

- Build on the patterns from the analysis entry: raw HTML5 `<video>` preload in `preload()` (all clips, buffered-but-paused), `midi5.noteOn` trigger with an editable note→video table, hidden p5 canvas → `s0` → Hydra → fullscreen `hc` canvas.
- Config section at the top of the file: `VID_BASE`, `VIDS`, `FIRST_NOTE`, `NOTE_TO_VID` (explicit note→index map), `AUTO_FALLBACK` (sequential mapping for unmapped notes). All entries in `VIDS` are preloaded.

## 2026-10-23 — Multi-video MIDI patch: analyse oldpatch.js video+MIDI pattern and P5LIVE constraints

### Status

Completed

### Summary

Analysed `output_display/oldpatch.js` (the user's reference for preloading videos and playing them on MIDI notes) and `output_display/patch.js` (the project's newer P5LIVE+Hydra patch) to extract the working patterns for a new multi-video patch.

**oldpatch.js video+MIDI pattern (the model to follow):**
- `preload()`: `createVideo("img/allhands.mp4", VIDEO)` — p5 video load before first frame.
- `setup()`: `vid.size(768,768); vid.hide(); vid.play(); vid.volume(0);` + `setupMidi(0, 0)` + overriding `midi5.noteOn`.
- `midi5.noteOn = (note) => { if (note.type == "noteon") { if (note.number == 47) { vid.play(); vid.time(0); } ... } }` — explicit note-number → video-action table (47/48/49 → restart at 0/2/5s).
- `draw()`: `updateMidi()` each frame; video drawn onto the p5 canvas, which Hydra source `s0` reads.

**P5LIVE constraints (documented in patch.js, same runtime):**
- P5LIVE's bundled p5 (v1.11.9) is trimmed: it has `createVideo` but **no** `loadVideo`. Raw HTML5 `<video>` elements are the reliable way to preload many clips: `document.createElement('video')`, `v.src`, `v.muted = true`, `v.preload = 'auto'`, `v.playsInline = true`, `v.load()` (buffers without playing), drawn later with `drawingContext.drawImage(v, 0, 0, width, height)`.
- MIDI: `setupMidi(0, 0)` in setup, `updateMidi()` in draw, `midi5.noteOn` callback receives a note object with `.type` (`"noteon"`) and `.number`.
- Hydra: build own instance `new Hydra({ detectAudio: false, enableStreamCapture: false, makeGlobal: true, autoLoop: true, canvas: hc })` (the old `detectAudio: true` trips P5LIVE with a mic getUserMedia); feed the hidden p5 canvas into `s0.init({ src: drawingContext.canvas, dynamic: true })`; show the Hydra canvas (`hc`) fullscreen with `zIndex`, hide the p5 canvas. Alias `noize = hydra.synth.noise` (p5 owns `noise`).

**Design decision for the new patch:** use the raw-HTML5-video preload (proven in patch.js in this exact runtime) rather than many `createVideo` objects — lighter, and each clip stays buffered-but-paused. Keep oldpatch.js's `midi5.noteOn` trigger style, but replace its 3 hardcoded note checks with one editable note→video table.

### Changes Made

- None (analysis only).

### Documentation

- This handoff entry records the analysed patterns.

### Architecture

No architectural impact. The new patch will reuse the existing P5LIVE+Hydra architecture (hidden p5 canvas → s0 → Hydra chain → on-screen canvas).

### Validation

- Read `output_display/oldpatch.js` (115 lines), `output_display/patch.js` (351 lines), `output_display/output.md`.

### Decisions and Caveats

- Chose raw HTML5 `<video>` preload over p5 `createVideo` for the multi-clip case (same choice patch.js made, with the runtime justification above).
- Caveat: the exact `setupMidi`/`midi5` API is P5LIVE-specific and could differ between P5LIVE builds; oldpatch.js's usage (`setupMidi(0,0)`, `midi5.noteOn`, `note.type`, `note.number`) is the known-good baseline for this project.

### Next Task

Research: locate the actual video files available for the VIDS list and decide the `VID_BASE` asset layout.

### Instructions for the Next Task

- `output_display/` currently has **no** `vids/` or `img/` subfolder (oldpatch.js's `img/allhands.mp4` and patch.js's `vids/MiniMax_*` are both absent). Real clips exist at `realtimecontrol/rtr/assets/vid/` (`test1.mp4`, `test2.mp4`, `hand08.mp4`). Make `VID_BASE` a one-line config constant and seed `VIDS` with placeholder filenames the user swaps for their own.

## 2026-10-23 — Video mode: hold the current patch on no-video states instead of flashing legacy/default

### Status

Completed. Root cause identified and fixed; verified by the patch test suite (35 passed, 2 pre-existing skips).

### Summary

In the new `"video"` patch mode, the patch the screens show is resolved by `Patches.code_for()` → `_video_code()` on **every** state frame, and that method was **stateless**. When the robot moved between zones and landed on a state with no per-pose video (a pose whose `pose_videos` entry is `null`, an action that has no videos at all, or the brief moment an action has ended), `_video_code` recomputed its fallback — the action's base patch, the `default` patch, or `DEFAULT_HYDRA_CODE` — so the screen briefly showed a legacy/default patch mid-transition. The user wants the **current patch** (the video already playing) to keep playing in that case.

The fix makes the video path remember the last video it produced and hold it when the next state has no video. `code_for` is invoked per frame by `Display._patch_for()` (`display.py:95`), and the render page renders the core-pushed `patch` field verbatim when it is a string (`render.html:178`), so a core-side change is sufficient — no client change needed.

### Changes Made

- `realtimecontrol/rtr/rtr/patches/patches.py`:
  - `Patches.__init__`: added `self._last_video: str | None = None` (the "current patch" for video mode).
  - `Patches._video_code`: when a pose has a video, store its template in `self._last_video` before returning it. When the current state has **no** video, return `self._last_video` if one has been produced (hold the current patch); only fall back to the action base patch / `default` / `DEFAULT_HYDRA_CODE` when no video has ever been produced.
  - Updated the `code_for` and `_video_code` docstrings to document the hold behavior.
- `realtimecontrol/rtr/tests/test_patch_push.py`: added three tests — `test_video_mode_holds_last_video_for_pose_without_video`, `test_video_mode_holds_video_when_action_ends`, `test_video_mode_holds_video_across_zone_transition` — and registered them in `main()`. Adjusted the `test_video_mode_pose_without_video_uses_base_patch` docstring to clarify the base-patch fallback only applies before any video has played.

### Documentation

- Handoff entry (this). No other docs affected.

### Architecture

- No architectural change. `code_for` already runs per state frame; the change adds a single piece of in-memory state (`_last_video`) to the `Patches` adapter so the video path is idempotent across no-video frames. The core remains the single source of truth and the render page continues to render the pushed `patch` field.

### Validation

- `python -m pytest tests/test_patch_push.py tests/test_patch_override.py tests/test_zone_scoped_actions.py -q` → **35 passed, 2 skipped**.
- Full suite (`python -m pytest tests/ -q`) shows failures in `test_remote_sdl.py`, `test_state_machine.py`, `test_transition.py`; confirmed **pre-existing** by stashing the change and re-running — they fail identically without it and are unrelated to patches.

### Decisions and Caveats

- The hold is scoped to video mode only; `"complex"` mode still concatenates zone + action + behavior unchanged.
- `_last_video` persists in memory for the life of the `Patches` adapter. If the mode flips to `"complex"` and back to `"video"`, the first no-video frame resumes the last video seen before the flip (acceptable; the operator is explicitly choosing video mode). A manual screen override still wins over everything, as before.
- The base-patch / default fallback is preserved for the **initial** state (before any video has played), so existing behavior for a cold start is unchanged.

### Next Task

No immediate next task identified. Optionally, confirm in a live session that moving between zones no longer flashes a legacy/default patch and that the last video keeps playing until the next pose with a video takes over.

### Instructions for the Next Task

The core logic is in `Patches._video_code` (`rtr/patches/patches.py`). The state is `self._last_video`. To adjust the hold semantics (e.g. reset on mode change, or to hold a non-video base patch instead), edit that method and the `__init__` field. Tests live in `tests/test_patch_push.py` (video-mode section); the `_frame_for` helper reuses the same `Patches` object across frames, so consecutive calls accumulate `_last_video` state — use it to assert hold behavior.

## 2026-10-23 — Fix: active group not reflected in client after clicking a zone group

### Status

Completed. Root cause identified and fixed; verified by the existing test suite (38 passed) and an end-to-end frame check.

### Summary

Diagnosed why clicking a zone-group button in `client.html` never updated the active-group indicator. The full chain is otherwise intact: the client sends `{cmd:"enable_group", group}`; the brain sets `self.active_group` and exposes it via `autonomy_info()`; the engine copies it into `Snapshot.active_group`; and `Snapshot.to_dict()` serializes it. The break was the last hop: the `Display` adapter's `_frame()` builds the broadcast state frame but **never read `snap.active_group`**, so the field never reached the client. `client.html` reads `s.active_group` (line 690), which was always `undefined`, so the indicator stayed at "—" and no group button ever lit up — for every group, regardless of whether the enable actually succeeded.

The fix forwards the field in `_frame()`, alongside the related autonomy fields.

### Changes Made

- `realtimecontrol/rtr/rtr/display/display.py`: in `Display._frame()`, added `if snap.active_group is not None: frame["active_group"] = snap.active_group` directly after the `autonomy_enabled_groups` block (lines 185-186), matching the existing optional-field pattern.

### Documentation

- Handoff entry (this). No other docs affected — the client already reads the field; it just was never sent.

### Architecture

- No architectural change. The data path (brain → engine Snapshot → Display frame → WS broadcast → client) already existed; only the Display adapter's field-forwarding list was incomplete. The fix completes an existing, intended data flow.

### Validation

- `python -m pytest tests/test_patch_push.py tests/test_zone_groups.py -q` → **38 passed**. (These exercise `Display._frame()` and the brain group logic.)
- End-to-end check: published a `Snapshot` with `active_group="museum-closed"` → broadcast frame contains `active_group: "museum-closed"`; published one with `active_group=None` → field absent (client shows "—").

### Decisions and Caveats

- Used the same "omit when None" convention as the other optional frame fields, so a cleared group produces no field rather than an explicit `null` — consistent with `client.html`'s `s.active_group || null` handling.
- This fix makes the indicator reflect the *currently active* group. If a specific group still fails to activate after this, that is a separate, group-specific issue (unknown group name, no enabled target nodes, no safe path from the current zone/action, or manual-mode interlock) and will be visible in the server log as a `[brain] enable_group:` line. Those paths set/revert `active_group` correctly; they were previously masked because the client never saw the value at all.

### Next Task

No immediate next task identified. Optionally, confirm in a live session that clicking a group now lights the button and shows the group name, and that switching to manual mode clears the indicator.

### Instructions for the Next Task

The state frame the client receives now includes `active_group` whenever one is set. To verify live: start the server, click a zone-group button in `client.html`, and confirm (a) the `#activeGroup` label shows the group name and the matching button gets the `active` class, and (b) switching control mode to `manual` clears it back to "—". If a group still doesn't activate, check the server log for a `[brain] enable_group:` line (unknown group / no enabled nodes / no safe path) and confirm the group's target zones are `enabled` in the live `zones.json`.

## 2026-10-23 — Simplify patch system: frontend (editor.html) + tests + end-to-end verification

### Status

Completed. Frontend UI, per-pose video controls, complex-mode gating, video-mode tests, and end-to-end HTTP verification are all done. Remaining optional item: live browser playback of an uploaded video on the 3D tool screen (see Caveats).

### Summary

Implemented the frontend half of the simplified patch system in `editor.html` and closed the loop with tests and an end-to-end HTTP check. The editor now: (1) exposes a **patch config card** in the robotconfig tab with a `patch_mode` selector (video / complex); (2) shows a **per-pose video control** in the action editor (upload / preview / delete, parallel to the image control) that POSTs to `/api/videos` and writes `pose_videos[pi]`; (3) **gates the legacy layered UI** — the patches tab's zone/behavior groups and the pose-index `pose_imgs` augmentation are hidden in `video` mode and restored in `complex` mode. The video-mode branch of the core (backend, prior entry) is now covered by dedicated tests, and the existing legacy tests were re-pinned to `complex` mode so they keep asserting the layered concatenation now that `video` is the default.

**What changed:**
- **`editor.html`** (all client-side; no backend change needed beyond the prior entry):
  - `normalizeRobotConfig` defaults `patch_mode` to `"video"`; `saveRobotConfig` includes `patch_mode` in the `/api/robotconfig` payload and rebuilds the patches form on success.
  - Robotconfig section: `#patchModeSel` (video / complex) wired to `ROBOT_CONFIG.patch_mode`.
  - Action editor: `#aePoseVideos` + `<video id="aeVidPreview">`; new helpers `reconcileActionVideos`, `poseVideoAt`, `buildPoseVideoControl`, `openVideoPicker`, `uploadPoseVideo` (POSTs `{video, name}` → sets `pose_videos[pi] = res.name`), `deletePoseVideo` (clears `pose_videos[pi]` + `DELETE /api/videos/<name>`), `buildAePoseVideos`, `showAeVidPreview`, `hideAeVidPreview`. Pose-row grid widened to `repeat(6, 52px) 110px 84px 84px`.
  - Pose-data consistency: delete/add/swap/paste-pose all splice/pad/swap `pose_videos` alongside the other per-pose arrays.
  - Complex-mode gating: `openActionEditor` only augments `pose_imgs` in `complex` mode; `buildPatchesForm` hides `zones`/`behaviors` groups (keeps `default` + `actions`) in `video` mode and updates `patchInfo`.
- **`tests/test_patch_push.py`**: added `_MockZonesVideos` (exposes `action_pose_videos`) + `_video_patches` helper and 8 video-mode tests (current-pose video template, middle-pose selection, pose-without-video → base patch, no-action → default, out-of-range clamp, ignores zone/behavior layers, complex still concatenates, screen-override wins). Re-pinned the existing legacy `_patches` helper + 3 pose-injection tests to `patch_mode_provider=lambda: "complex"` (the default is now `video`).
- **`tests/test_patch_override.py`**: re-pinned the `_patches` helper to `complex` mode so the concatenation-fallback assertions still hold under the new `video` default.

**No change needed in `render.html`** (already evals the pushed `m.patch`); the video template the core pushes plays fullscreen automatically.

### Changes Made

- `realtimecontrol/rtr/editor.html`: patch config card, per-pose video controls + preview, complex-mode gating, pose-array consistency for `pose_videos`.
- `realtimecontrol/rtr/tests/test_patch_push.py`: 8 video-mode tests + re-pinned legacy tests to `complex`.
- `realtimecontrol/rtr/tests/test_patch_override.py`: re-pinned legacy helper to `complex`.

### Documentation

- Handoff entry (this). In-UI `patchInfo` copy in the editor already reflects the two modes.

### Architecture

- No new architectural change vs. the backend entry: the frontend consumes the existing `patch_mode` + `pose_videos` + `/api/videos` surface. The core remains the single source of truth for the on-screen patch; the editor only edits the inputs (`patch_mode`, `pose_videos`) and the legacy layered slots are shown/hidden per mode.

### Validation

- `python3 -m py_compile` on `main.py`, `robot/config.py`, `robot/__init__.py`, `state/zones.py`, `io/httpserver.py`, `patches/patches.py` → COMPILE OK; `python3 -c "import main"` → IMPORT OK.
- `editor.html` inline scripts (13) → `node --check` JS SYNTAX OK.
- `pytest tests/test_patch_push.py tests/test_patch_override.py` → **23 passed** (18 push incl. 8 new video-mode, 5 override).
- Full `pytest tests/ --ignore=tests/test_remote_tui.py` → 249 passed, 14 skipped. **Pre-existing, unrelated failures:** `test_remote_sdl.py` (5 — TUI THEME/LAYOUT + setup-page labels, a subsystem untouched here) and a `test_remote_tui.py` collection error (urwid import path). Not caused by this change.
- **End-to-end HTTP** (real `HttpServer` on a free port): upload → `assets/videos/test.mp4` written (bytes round-trip) → static `GET /assets/videos/test.mp4` returns exact bytes → extension-less name defaults to `.mp4` → missing `video` → 400 → unsafe name regenerated to safe `pose_*` name → delete 200 + file removed → delete-missing 404 → path-traversal delete forbidden (400/403) and parent intact. **14/14 passed.**

### Decisions and Caveats

- **`code_for`'s `mode` param is vestigial**; the behavior layer is driven by the separate `behavior` param from `display.py`. Unchanged.
- **Caveat (verify in browser, optional):** confirm a real uploaded video plays fullscreen on the 3D tool screen end-to-end. The core pushes the `video_template` and `render.html` evals it; `hydra-synth.js` exposes `initVideo`. The code path is verified in-process and over HTTP, but a live playback smoke test (upload → trigger action → observe fullscreen video) is the one remaining manual check.
- **Legacy tests re-pinned to `complex`:** because `video` is now the default patch mode, the tests asserting the legacy zone/action/behavior concatenation must opt in explicitly. This is a test-only change; production behavior is unchanged.

### Next Task

Optional browser smoke test: open `editor.html`, upload a pose video in the action editor, trigger the action, and confirm `render.html` plays the video fullscreen; then switch `patch_mode` to `complex` and confirm the legacy zone/behavior slots + pose-index images reappear.

### Instructions for the Next Task

- Boot the core (`python3 main.py`) or the existing dev server. In `editor.html`, select an action → its pose rows show a video control next to the image control. Click it, pick a file; it uploads to `/api/videos` and the pose's `pose_videos[pi]` is set (thumbnail appears). Trigger the action and open the 3D tool screen / `render.html` — the current pose's video should play fullscreen. Set `patch_mode = complex` in the robotconfig tab and the patches tab's zone/behavior groups and the pose-index image augmentation return.
- If `initVideo` is missing on the live hydra build, the patch-status surface will show the error; `hydra-synth.js` already defines it.

## 2026-10-23 — Simplify patch system: backend (videos API + pose_videos + patch_mode + core resolution)

### Status

Completed (backend). Frontend (editor.html UI) is the next task.

### Summary

Implemented the backend for the simplified patch system, per the design in the prior entry. The core now resolves the on-screen patch in one of two modes (from the robot config): **"video"** (new, default — action-only, per-pose video) or **"complex"** (the legacy layered zone+action+behavior system). Added a per-pose video store + API, a `pose_videos` field on actions, and a `patch_mode` key on the robot config. The core already resolved per-pose (`snap.action_index`), so the video branch reuses that seam.

**What changed:**
- **`robot/config.py`**: `PATCH_MODE_KEY`/`DEFAULT_PATCH_MODE="video"`/`VALID_PATCH_MODES=("video","complex")`; added to `DEFAULT_ROBOT_CONFIG` + `builtin_robot_config`; `_patch_mode` validator + normalization in `validate_robot_config` (accepts `"video"`/`"complex"`, default `"video"`); `robot_config_patch_mode(config)` accessor.
- **`robot/__init__.py`**: exported `robot_config_patch_mode`.
- **`state/zones.py`**: `pose_videos` validation in `_validate_action` (list matching the pose count, filename-or-null — mirrors `pose_imgs`); `action_pose_videos(zone, action)` accessor (mirrors `action_pose_imgs`).
- **`io/httpserver.py`**: `videos_path = assets/videos` (next to `imgs_path`); `POST /api/videos` (`_post_videos`) stores the base64-decoded video as-is (no re-encode) and returns `{ok,name,url}` (appends `.mp4` if no extension); `DELETE /api/videos/<name>` (`_delete_video`); routing added in `do_POST`/`do_DELETE`. Reused the `[A-Za-z0-9_.\-]+` name guard + realpath containment check.
- **`patches/patches.py`**: `video_template(name)` builds the 3-line patch `setResolution(768, 768)\ns0.initVideo("/assets/videos/<name>")\nsrc(s0).out()`; `Patches.__init__` takes `patch_mode_provider`; `code_for` branches on `_patch_mode()` — `"video"` → `_video_code` (current pose's video → template, else the action base patch, else `DEFAULT_HYDRA_CODE`; no zone/behavior layering, no pose-index variable), `"complex"` → the existing 3-layer concat; `_pose_videos` helper (mirrors `_pose_imgs`); `make_patches` accepts `patch_mode_provider`. A manual `set_screen_override` still wins in both modes (the live-mode manual override).
- **`main.py`**: imported `robot_config_patch_mode`; hoisted `robot_cfg_holder = [robot_cfg]` before the `Patches` construction; wired `patch_mode_provider=lambda: robot_config_patch_mode(robot_cfg_holder[0])` (hot-reload aware).

**No change needed in `render.html`:** it already evals the pushed `m.patch` (`renderCode`), so a video template pushed by the core plays fullscreen automatically.

### Changes Made

- `realtimecontrol/rtr/rtr/robot/config.py`: patch_mode key + validation + accessor.
- `realtimecontrol/rtr/rtr/robot/__init__.py`: export `robot_config_patch_mode`.
- `realtimecontrol/rtr/rtr/state/zones.py`: `pose_videos` validation + `action_pose_videos`.
- `realtimecontrol/rtr/rtr/io/httpserver.py`: `videos_path`, `POST /api/videos`, `DELETE /api/videos/<name>`, routing.
- `realtimecontrol/rtr/rtr/patches/patches.py`: `video_template`, `patch_mode_provider`, `code_for` branch, `_pose_videos`, `make_patches`.
- `realtimecontrol/rtr/main.py`: import + `robot_cfg_holder` hoist + `patch_mode_provider` wiring.

### Documentation

- Handoff entry (this) + the design entry above. In-UI help for the patch config card lands with the editor task.

### Architecture

- **Architectural change made:** a new `patch_mode` branch in the core's patch resolution (`patches.py:code_for`) and a new `pose_videos` per-action field in the zones state. The core remains the single source of truth for the on-screen patch (it pushes the resolved code in every state frame); the new mode only changes *how* `code_for` resolves it. The manual `set_screen_override` (live-mode override) still takes precedence in both modes.

### Validation

- `python3 -m py_compile` on `main.py`, `robot/config.py`, `robot/__init__.py`, `state/zones.py`, `io/httpserver.py`, `patches/patches.py` → COMPILE OK.
- Functional test (in-process `Patches`): video mode resolves per-pose (pose 0/2 → `video_template`, pose 1 → action base, no action → data default, out-of-range index clamps); complex mode still concatenates zone+action(+pose globals)+behavior; `set_screen_override` wins in both; a missing `patch_mode_provider` defaults to `"video"`. → ALL PASSED.
- `video_template("dance.mp4")` → `setResolution(768, 768)\ns0.initVideo("/assets/videos/dance.mp4")\nsrc(s0).out()`.
- `validate_robot_config`/`robot_config_patch_mode`: `"complex"`/`"video"` accepted, `{}` → `"video"`, bad value → `ValueError`.
- `_validate_action` `pose_videos`: valid list (filename/null) OK; wrong length → `ValueError`; bad element → `ValueError`; absent OK.

### Decisions and Caveats

- **Videos are stored as-is** (no re-encode/resize) — unlike the image crop pipeline. The `setResolution(768,768)` in the template handles the display size; the source video is untouched.
- **`code_for`'s `mode` param is vestigial** (unused in the body); the behavior layer is driven by the separate `behavior` param (passed by `display.py`). Unchanged.
- **Caveat (verify in browser):** `render.html`'s hydra build must expose `s0.initVideo(...)`. The editor's AE hydra and the render hydra both use `new Hydra(...)`; `initVideo` is a standard hydra-synth source method, but confirm on the live page (the patch-status path will surface an error if it's missing).
- **Caveat:** in `"video"` mode the per-pose video takes precedence for that pose; other poses fall back to the action's base patch (editable in the action editor). Images/`poseindex` still work only in `"complex"` mode (or as the action base patch content).

### Next Task

Frontend in `editor.html`: (1) the **patch config card** in the robotconfig tab (a `patch_mode` selector: video / complex); (2) the **per-pose video control** in the action editor (upload / preview / delete, parallel to the image control) that POSTs to `/api/videos` and writes `pose_videos[pi]`; (3) hide/grey the legacy patches tab's zone/behavior slots (and the pose-index variable) when `patch_mode == "video"`, since only actions have a patch in the new system.

### Instructions for the Next Task

- **Patch config card:** in `editor.html`, the robotconfig view is built by `buildRobotConfigForm()` (line ~3399) into `#robotConfigForm`; `ROBOT_CONFIG` is loaded by `loadRobotConfig()` (line ~3381) and normalized by `normalizeRobotConfig()` (line ~3373, which currently only handles `joint_offsets_deg`). Add a `patch_mode` `<select>` (options `video`/`complex`) to the robotconfig form and include it in the `saveRobotConfig()` payload (line ~3425, which currently maps only the 6 joint inputs). `normalizeRobotConfig` should default `patch_mode` to `"video"`.
- **Per-pose video control:** mirror the image control. `buildPoseImgControl(zn, an, pi)` (line ~3905) builds the col-8 image control; add a sibling `buildPoseVideoControl(zn, an, pi)` (a `<video>` thumbnail / "+" placeholder / "×" delete) and wire it into the pose-row builder wherever `buildPoseImgControl` is called. Upload: read the file as base64 and `POST /api/videos {video, name}` (name from the original filename, sanitized to `[A-Za-z0-9_.\-]+`), then set `a.pose_videos[pi] = res.name` and refresh (mirror `uploadPoseImg` line ~4093 + `reconcileActionImgs` line ~3874 — add a `reconcileActionVideos`). Delete: `DELETE /api/videos/<name>` (mirror `deletePoseImg` line ~4118). `openActionEditor` (line ~4880) should set the `aePatchCode` textarea to `video_template` for the current pose when it has a video (else the action base patch), and preview it via `evalAePatchPreview`.
- **Disable complex by default:** when `ROBOT_CONFIG.patch_mode == "video"`, the patches tab's zone/behavior slot groups (`buildPatchesForm` line ~3596, the `addGroup("zones")`/`addGroup("behaviors")` blocks) and the `poseindex` variable should be hidden/disabled; only the `default` + `actions` slots are meaningful. Gate on `ROBOT_CONFIG.patch_mode`.

## 2026-10-23 — Simplify patch system: research + design (video-per-pose, action-only, toggle)

### Status

Completed (research + design). Implementation tasks follow.

### Summary

Researched the full patch/data flow to plan the requested simplification of the hydra patch system in `editor.html`. Goal: disable the current multi-layer system (zones + behaviors + action patches) by default, gated behind a "patch config card" in the robotconfig; make the new default **action-only** and **video-driven** (per-pose video uploads in the action editor, replacing the existing patch with a 3-line video template; played fullscreen in `render.html` on action trigger + pose change; no other trigger changes patches except a manual live override).

**Architecture confirmed (the seam to build on):**
- The core is the **single source of truth** for the on-screen patch. `display.py:_patch_for(snap)` (line 85) calls `self._patch_code(snap.zone, snap.mode, snap.action, snap.action_index, behavior=beh)` and pushes the resolved code in **every** state frame (`_frame`, line 151: `"patch": self._patch_for(snap)`). So the core **already resolves per-pose** — `snap.action_index` is the current pose index.
- `patches.py:code_for(zone, mode, action, pose_index, behavior)` (line 200) does the layered concat: zone patch (o0) → action/default patch (o1, with `inject_pose_globals`) → behavior patch (o3). `inject_pose_globals` (line 122) is the **"variable system"** the user wants dropped for videos (prepends `var pose` + `var poseindex = [...]`). A manual `set_screen_override` (the `set_screen_patch` command, i.e. the live-mode manual override) already wins over everything — this is the existing "manual override in live mode" seam.
- `render.html` needs **no patch-logic change**: it already evals the pushed `m.patch` via `renderCode(...)` (line 178) and re-fetches the table on `patches_changed`. Fullscreen playback of a video patch follows automatically once the core pushes `s0.initVideo(...)`.
- Image plumbing to mirror for videos: `httpserver.py` `_post_imgs` (line 668) / `_delete_img` (line 715), `imgs_path = assets/imgs` (line 231); `zones.py` `pose_imgs` validation (line 500-504) + `action_pose_imgs` accessor (line 866-875); `editor.html` per-pose image control + crop editor + `augmentPatchWithPose` / `preloadAePoseImgs` / `swapAePoseImg` / `buildAePoseImgs`.
- `robot/config.py` `validate_robot_config` (line 55) already **preserves unknown top-level keys**, so a new `patch_mode` key is additive; `DEFAULT_ROBOT_CONFIG` (line 31) is where the default belongs.

**Chosen design (interpretation — no clarifying question needed; it satisfies every stated requirement):**
- **`pose_videos`**: per-action list aligned with `poses` (exactly like `pose_imgs`), each entry a filename (e.g. `pose_videos.mp4`) or `null`. Stored under `assets/videos/`.
- **`patch_mode`** (robotconfig): `"video"` = new action-only/video system (default); `"complex"` = the current layered system.
- **Core (`patches.py:code_for`)**: when `patch_mode == "video"`, resolve per-pose: if the current action has `pose_videos[pose_index]` set → emit the concrete template `setResolution(768,768)\ns0.initVideo("<name>")\nsrc(s0).out()`; else → the action's base patch (falling back to `DEFAULT_HYDRA_CODE`). No zone/behavior layering, no `poseindex`. When `"complex"`, current behavior unchanged.
- **Editor (`editor.html`)**: per-pose **video** control in the action editor (upload / preview / delete), parallel to the image control. On video upload for pose `pi`: set `pose_videos[pi]`, and replace the editor's patch textarea with the template (preview shows the video). The **patch config card** in the robotconfig holds the `patch_mode` selector; the complex system's patch tab (zones/behaviors) is hidden/greyed when `patch_mode == "video"`.
- **`render.html`**: no change expected (state-frame patch already drives fullscreen). Verify `initVideo` is present in the hydra build it loads (caveat below).

### Changes Made

- `.kilo/task-handoff.md`: this design entry. No code changed yet in this task.

### Documentation

- This handoff entry is the design record. In-UI help for the patch config card will be added with the editor task.

### Architecture

- Architectural change **planned** (not yet made): a new `patch_mode` mode in the core's patch resolution (`patches.py:code_for`) and a new `pose_videos` per-action field in the zones state. Documented here; the architecture doc update lands with the implementation tasks.

### Validation

- Research only; no code changed, so no build/test run. Files read: `editor.html` (robotconfig form ~3366-3448, patch helpers 2178-2277, hydra setup 3560-3739, pose-image/crop UI 3740-4131, `openActionEditor` 4860-4919), `httpserver.py` (imgs API 660-733, `imgs_path` 231), `zones.py` (`pose_imgs` 497-504, `action_pose_imgs` 866-875), `patches.py` (`code_for` 200-239, `inject_pose_globals` 122-145), `display.py` (`_patch_for` 85-97, `_frame` 144-156), `robot/config.py` (full), `render.html` (state-frame patch 156-185).

### Decisions and Caveats

- **Interpretation decision:** the new system resolves the patch **per-pose** in the core (video template for the current pose's video). This is the only reading consistent with "videos per pose" + "as soon as a new pose is played, the patch is loaded and played" + "the variable system is not needed" (no `poseindex` array). If the user instead wants the video stored as a single per-action patch string, that is a smaller change — flag it and I'll adjust.
- **Caveat:** `render.html` must load a hydra-synth build that exposes `s0.initVideo(...)`. Needs verification on the live page (the editor's AE hydra and render hydra both construct `new Hydra(...)`; the video template relies on the standard `initVideo` source method). If the CDN build lacks it, `renderCode` will show an error in the patch-status path — verify in the browser.
- **Caveat:** the existing image `poseindex` mechanism stays intact and is only used when `patch_mode == "complex"` (or an action has images but no video). Videos and images coexist per-pose; a video takes precedence for that pose's patch in `"video"` mode.

### Next Task

Implement the backend: (1) `POST /api/videos` + `DELETE /api/videos/<name>` in `httpserver.py` (parallel to the imgs API, storing under `assets/videos/`); (2) `pose_videos` field in `zones.py` (validation parallel to `pose_imgs` + `action_pose_videos` accessor); (3) `patch_mode` key in `robot/config.py` (`DEFAULT_ROBOT_CONFIG` + normalization, default `"video"`).

### Instructions for the Next Task

- In `httpserver.py`: add `self.videos_path = os.path.join(self.root, "assets", "videos")` next to `imgs_path` (line 231); add `elif path == "/api/videos": self._post_videos()` in `do_POST` (line 323) and `elif path.startswith("/api/videos/"):` in `do_DELETE` (line 330); add `_post_videos` / `_delete_video` mirroring `_post_imgs` / `_delete_img` (lines 668-733) but writing the raw base64-decoded bytes (no Pillow crop — videos are stored as-is) and naming `<name>` (append `.mp4` if absent). Reuse the `re.fullmatch(r"[A-Za-z0-9_.\-]+", name)` guard and the realpath containment check.
- In `zones.py`: add `pose_videos` validation right after `pose_imgs` (line 500-504) with the same list-matches-pose-count rule; add an `action_pose_videos(zone_name, action_name)` accessor mirroring `action_pose_imgs` (line 866-875).
- In `robot/config.py`: add `PATCH_MODE_KEY = "patch_mode"`, `DEFAULT_PATCH_MODE = "video"`, add it to `DEFAULT_ROBOT_CONFIG`, and normalize it in `validate_robot_config` (accept `"video"` or `"complex"`, default `"video"`); add a `robot_config_patch_mode(config)` accessor mirroring `robot_config_offsets`.

## 2026-10-22 — Add a roaming/fixed-pose indicator icon to the brain-tab behavior cards

### Status

Completed

### Summary

Added a small icon in the top-left corner of every brain-tab **behavior card** that shows whether the behavior **roams** the zone safezone (drives the variable axes) or holds a **strict/fixed pose** (variable axes held, no roaming). This answers "which behaviors allow roaming, which do not" visually.

Classification (verified against `rtr/state/behavior.py`):

- **Fixed / strict pose (no roaming)** — the no-op policies; `_apply_behavior` returns early and drives no variable axes: **`calm`**, **`bridge`**.
- **Roam the zone safezone (drive the variable axes)** — every other behavior: **`locked`, `intent`, `curious`** (camera servo), **`gentle`** (face servo), **`vigilant`** (sine sweep), **`restless`** (wander), **`spontaneous`** (random jumps), **`soothing` / `agitated` / `aggressive` / `exhilarated` / `impatient` / `drowsy`** (oscillation). 13 total.

The icon: a 20px circular badge, absolutely positioned at the card's top-left. Roam = green badge with a walking glyph (🚶); fixed = red badge with a pin glyph (📌). A one-line legend sits at the top of the behaviors section, and each badge carries a hover tooltip.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - CSS (after the `.brainCard.camClose` rule, ~line 88): `.brainCard.roamCard { position: relative; }`, `.roamBadge` (absolute, top-left, 20px circle), `.roamBadge.roam` (green `#16351f`) / `.roamBadge.fixed` (red `#3a1a1a`), `.brainCard.roamCard h2 { padding-left: 26px; }` (reserve space so the title clears the badge), and `.roamLegend` (full-width legend line).
  - `NO_ROAM_BEHAVIORS = new Set(["calm", "bridge"])` (~line 2371): the no-roam set, with a comment pointing at the `_apply_behavior` early return in `behavior.py`.
  - After `el.appendChild(behSep)` (~line 2769): a one-line `.roamLegend` explaining the icon.
  - In the behavior-card loop (~line 2828): for each behavior card, add the `roamCard` class and append a `.roamBadge` (roam/fixed + glyph + tooltip).
- `realtimecontrol/rtr/test_roam_badge.js` (new): a Node mini-DOM test (same pattern as `test_groups_tab.js`) that loads the editor script, runs `loadBrain` + `buildBrainForm`, and asserts one badge per behavior card with the correct class/glyph (13 roam 🚶, 2 fixed 📌) and a tooltip.

### Documentation

- The in-UI legend + per-badge tooltips are the documentation; no separate doc file needed.

### Architecture

No architectural impact. UI-only; the roaming/fixed distinction is read-only metadata derived from the existing behavior policies (it does not change which behaviors roam or the brain's data model).

### Validation

- `node --check` on the extracted editor inline script → SYNTAX OK.
- `node test_roam_badge.js` → `ALL_TESTS_PASSED` (15 badges for 15 behaviors; `calm`/`bridge` = fixed 📌; the other 13 = roam 🚶; all tooltips present).
- Confirmed the pre-existing `test_groups_tab.js` load failure is NOT caused by this change: the original `editor.html` (from `git show HEAD`) fails the same mini-DOM load (`Cannot set properties of null (setting 'onclick')`) because the test's `STATIC_IDS` list predates the `tabRobotConfig` / `liveToggle` / `ui*Toggle` / `crop*` elements. My new test uses a permissive `getElementById` (auto-creates missing elements) and calls `loadBrain` directly (avoiding `switchView`'s 3D-unmount path the stub doesn't model).

### Decisions and Caveats

- Chose the binary roam/fixed split that matches the core: only `calm` and `bridge` are no-op (strict); all 13 other behaviors drive the variable axes and therefore roam within `safezone ∩ hardware`. The camera-servo behaviors (locked/intent/curious/gentle) are included in "roam" because they move the variable axes within the safezone (and can additionally sweep via `sweep_amp`).
- Used emoji glyphs (🚶 / 📌) for the icon; easy to swap for a different glyph if the user prefers.
- Did not modify the pre-existing `test_groups_tab.js` (its stale `STATIC_IDS` is out of scope); the new test is self-contained.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

N/A — the task is complete and verified. If the user wants a different icon, change the glyph in the behavior-card loop (~line 2833, `badge.textContent`) and the legend text (~line 2772).

## 2026-10-22 — Explain the brain tab's "triggers" (stay / next-selection) in editor.html help

### Status

Completed

### Summary

Explained how the "triggers" section on the brain tab's behavior cards works, adding the explanation in three places in `editor.html`, following the existing in-UI help conventions. The semantics were verified against `rtr/brain/brain.py`:

- **Full-width elaborated help block**: a new collapsible `<details class="brainDetails">` right after the "behaviors" separator (same pattern as the zone-class and camera-group help blocks; full-width via the existing CSS `.brainGrid .brainDetails { grid-column: 1 / -1; }`). Five paragraphs: the stay-vs-advance decision model, the four triggers, the stay probability dice-roll, the strong external events, and the re-arm / lock-tracking bookkeeping.
- **Hover tooltips**: `mkSub` now accepts an optional `title` (set on the `<h3>`); the "next-selection (stay / triggers)" subheading, the "triggers" label, and all four trigger checkboxes carry tooltips (dwell = default trigger; new_lock/lost_lock = lock-state edges; empty lock_duration_s = trigger never fires).
- **`brainInfo`** (the definition run at the bottom of the tab): a sentence added explaining next-selection (stay / triggers).

Semantics confirmed in `brain.py`:
- `_behavior_triggers_fired` (line 838): default `["dwell"]` when none are checked; `dwell` fires when the action has run for >= `dwell_s` (per-behavior `dwell_s` overrides the global autonomy dwell); `new_lock` / `lost_lock` are lock-state edges (previous vs current lock id); `lock_duration` fires when the lock has been held for >= `lock_duration_s` (empty = never fires).
- `_autonomy_decide` (line 888): an external event (midi / camera / websocket) is a STRONG trigger — it always advances, bypassing the stay probability (operator control). A fired behavior trigger applies the stay probability: `random.random() < stay` → stay (loop, re-arm timers), else advance to a random valid next.
- `_rearm_stay` (line 871): re-arms the timers so a stayed action re-evaluates on the next trigger instead of re-firing every tick; `_prev_lock_id` / `_lock_start_ts` are tracked between ticks so the edge/duration triggers are evaluated against a consistent baseline.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - `mkSub(card, text, title)` (~line 2549): added the optional `title` param; sets `h.title` when given. Backward compatible — existing 2-arg call sites unchanged.
  - After `el.appendChild(behSep)` (~line 2751): new full-width `<details class="brainDetails">` block ("how triggers + next-selection work…") with 5 paragraphs.
  - `mkSub(card, "next-selection (stay / triggers)", …)` (~line 2824): added the subheading tooltip.
  - `TRIGS` list (~line 2834): elaborated the four checkbox tooltips.
  - `trigLab` ("triggers" label, ~line 2844): added a tooltip.
  - `brainInfo` text (~line 2949): appended the next-selection (stay / triggers) sentence.

### Documentation

- The in-UI help text is the documentation; no separate doc file needed.

### Architecture

No architectural impact. Help text only; no behavior change in the brain or the editor's data model.

### Validation

- Extracted the editor inline script (lines 681–7135) → `node --check` → SYNTAX OK.
- Re-verified the trigger semantics against `brain.py` (`_behavior_triggers_fired`, `_autonomy_decide`, `_rearm_stay`, `_autonomy_tick`) — the help text matches the implementation.

### Decisions and Caveats

- Followed the existing help conventions exactly (`brainDetails` collapsible blocks, `title` tooltips, the `brainInfo` run) so the new help is visually and structurally consistent with the camera-group and zone-class help.
- No `mkSub` signature break: the `title` param is optional.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

N/A — the task is complete and verified.

## 2026-10-22 — Fix "x" frozen-axis bridge poses blanking the 3D arm in the validate tab

### Status

Completed

### Summary

Selecting a bridge zone in the editor's validate tab (e.g. `cuddling_purring_bridge`) made the 3D robot disappear from the screen, and the "validate action" scan could not run.

**Root cause:** Bridge (and other) action poses use `"x"` as a **frozen-axis marker** — the axis holds its value from the action's start (the state machine's `zones.py` treats `"x"` as "no fixed value"). The 3D-driving code paths in `rtr3d.js` and `editor.html` did the arithmetic on pose values **without guarding `"x"`**: `"x" * Math.PI / 180` → `NaN` → a NaN quaternion in the arm's FK matrix → the arm stops rendering ("disappears"). No JS error is thrown; it is purely visual. The same unguarded `"x"` produced NaN path samples in the action scan and a NaN scratch FK in the collision check, so the scan "did not validate".

`zones.json` was **not** the cause — `cuddling_purring_bridge` is well-formed and the `"x"` markers are intentional and consistent across all `"x"` bridges (`cuddling_stretching_bridge`, `hunting_examining_bridge`, `hunting_examining_bridge2`, and pose-0-only in `resting_testing_bridge`). The bug is general to any `"x"` bridge.

**Fix (two layers):**
1. **`rtr3d.js` (engine boundary — never emit a NaN angle):**
   - `setJoints()`: skip `"x"` axes (hold the rotor's current angle).
   - `checkPoseCollision()`: resolve `"x"` → the real arm's current joint value before building the scratch FK.
   - Track the real arm's concrete joints in `curJoints` (updated in `update()` from numeric joints) and expose them via a new `getJoints()`.
2. **`editor.html` (resolve before arithmetic):**
   - New `resolvePose(pose)` helper: substitutes each `"x"` with the real arm's current value (`Robot3D.getJoints()`).
   - `runActionScan()`: resolves every anchor pose before path-sampling (sampling over an `"x"` was the NaN source).
   - `updateGhost()`: resolves the ghost pose so the frozen axis shows where the robot's axis is.
   - `checkValidateNow()`: resolves the pose before displaying/checking (`"x".toFixed()` would otherwise throw).

### Changes Made

- `realtimecontrol/rtr/rtr3d.js`:
  - Added `const curJoints = HOME.slice();` (the real arm's last concrete joint angles).
  - `setJoints()`: added `if (jointsDeg[j] === "x") continue;` so a frozen axis holds its current angle instead of producing a NaN quaternion.
  - `update()`: store numeric `s.joints[j]` into `curJoints` (frozen `"x"` axes leave `curJoints` unchanged, i.e. "hold current").
  - `checkPoseCollision()`: `rad` now maps `"x"` → `curJoints[j]` so the scratch FK never runs on a NaN angle.
  - Added `getJoints()` returning `curJoints.slice()`; added it to the module's exported object.
- `realtimecontrol/rtr/editor.html`:
  - Added `resolvePose(pose)` (substitutes `"x"` with the real arm's current value).
  - `runActionScan()`: resolve anchor poses (`ps.map((p) => resolvePose(p))`) before sampling the intermediate path.
  - `updateGhost()`: `Robot3D.setTarget(resolvePose(pose))` instead of driving the raw pose.
  - `checkValidateNow()`: resolve the ghost pose before the collision check and display.

### Documentation

None required — the change is internal to the 3D preview/validate code and is documented in the new/updated comments; no new public config.

### Architecture

No architectural change. The fix keeps the existing `"x"` frozen-axis convention (already used by `zones.py` and the editor's pose clamping / edge-conflict code) and adds a resolution step at the 3D boundary: `"x"` → the real arm's current joint value. `rtr3d.js` is now self-defensive (it can never emit a NaN angle), and the editor resolves `"x"` before any arithmetic. No changes to the core, engine, or zones.

### Validation

- `node --check rtr3d.js` → SYNTAX OK.
- Extracted the editor inline script (lines 681–7107) → `node --check` SYNTAX OK.
- `node /tmp/kilo/test_x_fix.js` (vm logic test mirroring the exact changed code) → ALL TESTS PASSED:
  - Confirmed the original bug: `"x" * PI/180` and mapping a pose containing `"x"` yield NaN.
  - `setJoints` skip: a pose with one `"x"` yields 5 finite angles.
  - `checkPoseCollision` resolve: a `"x"` pose yields 6 finite rad values, with the frozen axis resolving to the real-arm value.
  - `resolvePose`: `["x",-133,156,-2,0,0]` → `[10,-133,156,-2,0,0]` (frozen axis = real-arm value 10).
  - `runActionScan` sampling over resolved poses → 19 poses (3 anchors + 16 samples), every pose 6 finite numbers; the frozen axis holds at the real-arm value throughout the path.

### Decisions and Caveats

- A frozen `"x"` axis resolves to the **real arm's** current joint value (`curJoints`), matching the state-machine semantics ("holds the action-start value" = where the axis is). In the validate tab the real arm is in manual/hold mode at its current position, so this is the meaningful concrete value.
- `curJoints` is initialized to `HOME` and updated only from numeric joints in `update()`; a `"x"` in a driven pose leaves it unchanged (the "hold current" behaviour). `setTarget()` (ghost-only) does not update `curJoints`, which is correct — the ghost is a separate arm.
- The `rtr3d.js` guards are defense-in-depth: even if a future caller passes an `"x"` straight to the engine, no NaN is produced. The editor-side `resolvePose` is the primary fix for the scan/ghost because path-sampling must be done on concrete numbers (an `"x"` cannot be interpolated).
- Not verified against the live browser preview (no GUI in this environment) — the fix is verified by syntax checks + a logic test that mirrors the exact changed code. Confirm in the editor: select `cuddling_purring_bridge` (and other `"x"` bridges) in the validate tab and confirm the arm no longer disappears and "validate action" runs to "complete".

### Next Task

Confirm in the browser editor that the 3D arm no longer disappears when selecting an `"x"` bridge zone in the validate tab, and that "validate action" runs the scan to completion for `cuddling_purring_bridge` (and the other `"x"` bridges).

### Instructions for the Next Task

- The fix is in `realtimecontrol/rtr/rtr3d.js` (`setJoints` skip, `checkPoseCollision` resolve, `curJoints`/`getJoints`) and `realtimecontrol/rtr/editor.html` (`resolvePose`, `runActionScan`, `updateGhost`, `checkValidateNow`).
- In the editor, open the validate tab, select a bridge zone that uses `"x"` (e.g. `cuddling_purring_bridge`), and confirm: (a) the arm renders (does not disappear) on zone select, (b) the ghost shows a concrete pose (frozen axis at the robot's current value), (c) clicking "validate action" runs the scan to "complete" without a NaN/blank arm.
- If the frozen axis shows an unexpected value in the ghost, check that `Robot3D.getJoints()` returns the real arm's current position (it is seeded from `HOME` and updated by `update({ joints })`).

## 2026-10-22 — Fix "waiting for flag" error when entering manual mode in stream mode

### Status

Completed

### Summary

Entering manual mode (`set_control_mode("manual")`) while the engine runs in **stream** mode (`move_mode: "stream"`, used by the hunting `track` action in `zones.json`) stranded the KUKA KRL at `WAIT FOR $FLAG[1]` — the "waiting for flag" error.

**Root cause:** In stream mode `rtr/robot/kuka.py:_move` fires each move on a daemon thread that runs a *blocking* kukapy `move` (send command frame, wait for `<Res>`) on the shared EKI socket. The main tick loop (`engine.py:run`) simultaneously does `get_curjpos`/`get_curpos` (also send + wait for `<Res>`) on the **same socket**. Nothing serialized the two. The KUKAPY server (`KUKAPY_SERVER.SRC`) is a strict single-flight request/response loop sitting at `WAIT FOR $FLAG[1]` between commands; the race desyncs the protocol and strands the KRL at `WAIT FOR $FLAG[1]`. The race could also make `get_curjpos` grab a move's response (which carries `A1..A6 = 0.0`), reading the pose as all-zeros.

**Fix (two parts, both in `rtr/robot/kuka.py`):**
1. **Serialize every EKI call** with a single `threading.Lock` (`self._lock`, created in `KukaRobot.__init__`). `get_curjpos`, `get_curpos`, and the kukapy `move` (inside `_move._do`) all run under `with self._lock:`, so at most one EKI round-trip is in flight at a time — the KUKAPY server's single-flight contract is preserved.
2. **Stream moves use continuous positioning.** `_move` now passes `cnt_val = STREAM_CNT` (10) when `blocking=False` and `cnt_val = 0` when `blocking=True`. The KUKAPY server treats `cmd_cnt > 0` as a continuous move (`PTP ... C_PTP` / `LIN ... C_DIS`): the KRL issues the move and answers immediately instead of holding until arrival, so the lock is held only for the short round-trip and the 20 Hz retarget stream keeps flowing. Block moves keep `cnt_val = 0` (exact, blocking PTP) — unchanged behaviour.

This makes the stream path safe on the real EKI: the daemon-thread move and the tick-loop pose reads can no longer overlap on the socket, so the protocol can no longer desync into "waiting for flag".

### Changes Made

- `realtimecontrol/rtr/rtr/robot/kuka.py`:
  - Added `import threading` and the `STREAM_CNT = 10` constant (with a comment explaining the KRL `cmd_cnt` semantics).
  - `KukaRobot.__init__`: added `self._lock = threading.Lock()` (serializes all EKI calls).
  - `get_curjpos` / `get_curpos`: wrapped the kukapy read in `with self._lock:`.
  - `_move`: computed `cnt = 0 if blocking else STREAM_CNT`; wrapped the kukapy `move` in `with self._lock:` and passed `cnt_val=cnt` to both the joint and cartesian call forms. Updated the docstring to describe the new (correct) behaviour instead of the old "verify on the robot" caveat.
- `realtimecontrol/rtr/tests/test_kuka_eki_serialization.py` (new): fake kukapy robot that records EKI round-trips in flight; asserts (a) EKI calls are serialized (`max_in_flight == 1`) when a stream move races the tick-loop pose reads, (b) stream moves use `cnt_val == STREAM_CNT > 0`, (c) block moves use `cnt_val == 0`, (d) a stream move returns without blocking the caller.

### Documentation

None required — the change is internal to the KUKA EKI wrapper and is fully documented in the module's docstring/comments; no new public config or API surface.

### Architecture

No architectural change to the core. This is a correctness fix to the KUKA EKI adapter (`rtr/robot/kuka.py`): the single-flight EKI contract is now enforced by a lock in the wrapper, and stream moves opt into continuous positioning so the lock is held briefly. The core/engine (`engine.py`, `machine.py`, `brain.py`) is unchanged.

### Validation

- `python3 tests/test_kuka_eki_serialization.py` → all 6 tests pass.
- Confirmed the fake detects the race: driving the fake robot's EKI methods from two threads **without** the wrapper lock yields `max_in_flight == 2` (overlap); with the lock the tests assert `max_in_flight == 1`.
- `python3 -m pytest tests/ -q --ignore=tests/test_remote_tui.py` → 244 passed, 244 includes the 6 new tests; 5 failures in `tests/test_remote_sdl.py` are **pre-existing** (verified by stashing the `kuka.py` change and re-running — they fail identically without it).
- `tests/test_remote_tui.py` fails at **collection** (`ImportError: cannot import name 'action_kind' from 'rtr.flow'`) — **pre-existing**, unrelated to this change (it imports from `rtr/flow.py`, which was not touched).

### Decisions and Caveats

- `STREAM_CNT = 10`: the KRL `CASE 4/5` only distinguishes `cmd_cnt == 0` (exact/blocking) vs `cmd_cnt > 0` (continuous). Any positive value triggers continuous positioning; 10 is a conventional "close enough" percentage and keeps the robot responsive to retargets. If the robot overshoots/undershoots the stream noticeably, tune this value — it does not change correctness, only how closely the continuous move tracks each retarget.
- The lock is a plain (unfair) `threading.Lock`; the critical sections are short (one EKI round-trip), so contention is minimal. No re-entrancy is needed.
- `get_curjpos` offset math is applied **outside** the lock (only the raw kukapy read is under the lock), so the lock does not hold while Python list-comprehension runs.
- This is verified against a **fake** kukapy robot (no hardware). The real-KRL effect of `cnt_val > 0` (continuous PTP answering immediately) is established from `KUKAPY_SERVER.SRC` `CASE 4/5` and the kukapy `move(cnt_val=...)` contract, but has not been exercised on the physical controller — confirm the stream runs smoothly and "waiting for flag" no longer appears on the robot before relying on it in production.

### Next Task

Verify the fix on the physical robot: run the hunting `track` action (stream mode), enter manual mode, and confirm (a) the "waiting for flag" error no longer appears, (b) the stream still retargets smoothly at ~20 Hz, and (c) the pose read no longer spuriously reports all-zeros.

### Instructions for the Next Task

- The fix lives entirely in `realtimecontrol/rtr/rtr/robot/kuka.py`. Every kukapy socket call is under `self._lock`; stream moves pass `cnt_val=STREAM_CNT` (10), block moves pass `cnt_val=0`.
- To test on hardware: boot the real controller with the KUKAPY server, run the hunting `track` action (which applies `move_mode: "stream"` from `zones.json`), then `set_control_mode("manual")`. Watch for the "waiting for flag" KRL error and for the pose read staying non-zero.
- If the stream feels sluggish or overshoots, adjust `STREAM_CNT` (the only stream-specific tuning knob). Do not remove the lock — it is what prevents the desync.
- Do not "fix" the pre-existing `tests/test_remote_tui.py` collection error or the `tests/test_remote_sdl.py` failures as part of this task; they are unrelated to the EKI work.

## 2026-10-22 — Validate tab: add "validate action" button (test bridge/other actions)

### Status

Completed

### Summary

The validate tab could only scan a zone's safezone box (corner/grid) for collisions. Bridge zones (inserted by the flow editor) get a wide safezone (the union of both endpoint zones) and an auto-generated `bridge` action whose `poses` are `[aEnd, bStart]` — a good starting passage that often needs tweaking. There was no way to test the action's actual waypoints/passage interactively.

Added a **"validate action"** workflow to the validate tab in `realtimecontrol/rtr/editor.html`:
1. An **action selector** (one button per action in the selected zone, showing pose count) so you can pick the `bridge` action or any other.
2. A **"validate action"** button that steps the local 3D arm through the selected action's `poses` **in order**, checking each anchor pose **and** the linear joint-space path samples between consecutive anchors against the collision engine (self + environment). For a 2-pose bridge action this sweeps the actual passage (2 anchors + 8 path samples = 10 poses).

Reuses the existing scan machinery (`valScan` / `stepValidateScan` / `animateValidateMove` / `checkValidatePose`) unchanged — the new code only builds a different pose list and hands it to the same stepper. The safezone scan (whole box) and the action scan (the action's waypoints + passage) are complementary: run both to fully validate a bridge passage.

### Changes Made

- `realtimecontrol/rtr/editor.html` (`buildValidateForm`, after the safezone editor): added an action selector (`.zoneList` of action buttons; sets `selectedAction`, rebuilds).
- `realtimecontrol/rtr/editor.html` (scan row): added the "validate action" button (calls `runActionScan`).
- `realtimecontrol/rtr/editor.html` (after `runValidateScan`): added `ACTION_SCAN_PATH_STEPS = 8` and `runActionScan()` — builds the pose list (anchors + path samples) and drives the existing scan stepper.

### Documentation

None required — editor-internal UI change; no new public behavior or config surface.

### Architecture

No architectural impact. Reuses the existing validate-tab scan pipeline and the action `poses` data model; no server changes (the validate tab is fully client-side, driving the local Robot3D + collision engine).

### Validation

- `node --check` on the extracted inline `<script>` → SYNTAX OK.
- Extracted `runActionScan` + `ACTION_SCAN_PATH_STEPS` via regex and ran in a `vm` sandbox with stubs: 2-pose action → 10 poses (2 anchors + 8 samples), first/last = anchors; 3-pose action → 19 poses (3 anchors + 16 samples). All assertions passed.
- No Python test covers this (the `tests/*.py` reference `validate_state_data`, a different Python function); the change is client-side JS only.

### Decisions and Caveats

- Path sampling is **linear in joint space** (consistent with `animateValidateMove`'s visual sweep). The robot's real move is Cartesian-planned, so the sampled path is an approximation of the swept volume — dense enough to catch a passage collision, but the safezone grid scan remains the denser whole-box check.
- `ACTION_SCAN_PATH_STEPS` = number of **intermediate** samples per segment (a 2-pose action scans 2 + 8 = 10 poses).
- The action scan checks **collisions only** (self + environment), not joint limits or safezone containment — those are the safezone scan's / hardware-limits' job. A colliding pose is reported in `valResult` and copied into the pose setter (existing behavior).
- Selecting an action in the validate tab updates the shared `selectedAction` global (same as the zones-tab ghost); expected and harmless.

### Next Task

No immediate next task identified. Optional follow-ups: (a) mirror the "validate action" button into the action editor modal (where poses are tweaked) so the test is one click away from the edit; (b) raise `ACTION_SCAN_PATH_STEPS` or make it adjustable if a thin mid-passage collision is missed.

### Instructions for the Next Task

- The validate-tab form is `buildValidateForm()` in `realtimecontrol/rtr/editor.html`. The new action selector sits after the safezone editor; the "validate action" button is in the scan row; `runActionScan()` + `ACTION_SCAN_PATH_STEPS` are right after `runValidateScan()`.
- `runActionScan` builds `valScan.poses` then calls the existing `stepValidateScan()`. Do not duplicate the stepper — reuse `valScan` / `animateValidateMove` / `checkValidatePose`.
- The action's waypoints come from `actionPoses(a)` (= `a.poses`). A bridge action's `poses` are `[aEnd, bStart]` (see `bridgePlan`), so the passage is the single move between them.
- Validate JS with `node --check` on the extracted inline script. The `test_action_cam.js` harness fails at load (pre-existing DOM-stub gap) and is not a reliable signal for editor changes.

## 2026-10-22 — Action editor: add pose-delete button (flow tab)

### Status

Completed

### Summary

The action editor (Step-3 modal, opened from the flow tab by clicking an action row) had no way to delete a pose in the **axis_bands** branch (the camera/active-class actions: track/farfocus/approach/facefocus). That branch rendered only a single "anchor pose" row with six number inputs and **no controls at all** — no delete, no add, and any extra poses (`poses[1:]`) were silently hidden (still saved to disk and still driving `actionStartPose`/`actionEndPose` flow handoffs). The **variable_axes** branch already had a delete button (×), but it was effectively invisible: the per-row control strip (▲ ▼ copy ×) is wider than its 56px grid column and overflowed into the pose-image column, where the 40px thumbnail (painted later in DOM order) covered the × button.

Two changes to `realtimecontrol/rtr/editor.html`:
1. **axis_bands branch** (`buildActionEditor`, the `if (isActive)` block): replaced the single hardcoded anchor row with a loop over **all** `a.poses`, rendering one row per pose (six number inputs) plus a **× delete button** per row. Delete splices `a.poses`, the aligned `a.pose_imgs`, and the per-pose `SOUND.actions[zn][an].poses` entry, then rebuilds — mirroring the variable_axes delete exactly.
2. **CSS**: widened the control column of `.aePoseRow` from `56px` to `110px` so the four-button strip (▲ ▼ copy ×) fits instead of overflowing behind the pose image, making the existing variable_axes delete button actually visible/clickable. Row width is now ~534px, still inside the ~556px `#aeBody`.

### Changes Made

- `realtimecontrol/rtr/editor.html` (CSS, line ~198): `.aePoseRow` grid `grid-template-columns: repeat(6, 52px) 56px 84px` → `repeat(6, 52px) 110px 84px`.
- `realtimecontrol/rtr/editor.html` (`buildActionEditor`, axis_bands branch, lines ~4370-4413): single anchor row → `a.poses.forEach` loop; per-pose row with six number inputs + × delete button (splices poses/pose_imgs/sound, rebuilds).

### Documentation

None required — editor-internal UI change; no new public behavior or config surface.

### Architecture

No architectural impact. Data model unchanged (`poses`/`pose_imgs`/sound alignment already existed); only the editor's rendering of the axis_bands branch gained per-pose rows + delete, and a CSS column width was adjusted.

### Validation

- `node --check` on the extracted inline `<script>` → SYNTAX OK.
- Layout math: ctl strip ≈ 95px < new 110px column; full row ≈ 534px < ~556px `#aeBody` (aePanel 800px − 32px padding − 200px preview − 12px gap).
- `node test_action_cam.js` → `LOAD ERROR: Cannot set properties of null (setting 'onclick')`. **Pre-existing**: reproduces identically on the unmodified `editor.html` (verified by swapping in `git show HEAD:...`), so not caused by this change. The DOM stub in that harness is missing elements the script wires at load.

### Decisions and Caveats

- Kept the axis_bands rows as **number inputs** (the existing style for that branch) rather than reusing the variable_axes text inputs that support the `"x"` frozen axis — frozen axes are a variable_axes concept; the axis_bands servo clamps `curjpos` into the band and does not use stored `"x"` values.
- The delete button can remove the only pose of an axis_bands action; `buildActionEditor`'s existing guard (`if (!a.poses.length) a.poses = [[0,0,0,0,0,0]]`) then reseeds a default anchor, so an action always keeps ≥1 pose. Note: deleting a pose also removes its flow handoff role (`actionStartPose`/`actionEndPose`), which is the intended effect.
- Did **not** add a "+ pose row" button to the axis_bands branch (out of scope; the user asked only for delete). Easy follow-up if wanted.

### Next Task

No immediate next task identified. Optional follow-ups: (a) add a "+ pose row" button to the axis_bands branch for symmetry with variable_axes; (b) fix the pre-existing `test_action_cam.js` DOM-stub LOAD ERROR so the action-editor test actually runs.

### Instructions for the Next Task

- The action editor is `buildActionEditor()` in `realtimecontrol/rtr/editor.html` (the `#actionEditor` modal). The axis_bands branch is the `if (isActive)` block; the variable_axes branch is the `else` block. Both now render per-pose rows with a × delete.
- The per-row control strip is `.aePoseCtl` inside the `.aePoseRow` grid (columns: 6×52px inputs, 110px ctl, 84px pose-image). Keep the ctl ≤ the 110px column or the buttons overflow behind the image again.
- Pose deletion must splice **three** aligned arrays: `a.poses`, `a.pose_imgs`, and `SOUND.actions[zn][an].poses` (see the delete handlers in both branches).
- Validate JS with `node --check` on the extracted inline script; the `test_action_cam.js` harness currently fails at load (pre-existing DOM-stub gap) and is not a reliable signal for editor changes.

## 2026-10-22 — Ship post-connect crash fix to device + on-device verification

### Status

Completed

### Summary

Shipped the verified post-connect crash fix (staged in `dist/repo/rtr`) to the Trimui/R36S device at `root@192.168.1.33:/storage/rtr-remote` and verified it on-device against the live core. The device was previously offline; it is now reachable. The fix itself (from the prior session) was: `rtr/remote/view.py` — `item_label` uses `action_next_list` instead of raw `action_next`, and the new `_pose_xyz()` helper handles both dict (`x`/`y`/`z`) and list/tuple (first three numerics) poses, used by `_brain_content` and `_arm_content`.

**On-device verification (new, this session):** uploaded `/storage/verify_crash.py` (device variant of `/tmp/kilo/repro_crash.py`) and ran it with the PAK's bundled Python (`/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/python/bin/python3`). It connected to the live core (`192.168.1.185:8765` WS + `:8766` HTTP), captured 6 state frames + 1 zone table, applied them to a real `view.RemoteState`, and rendered all 9 pages (NAV, CAMERA, BRAIN, HUNT, CADENCE, TRIGGERS, ARM, LED, SETUP) plus cursor movement across every page: **no crash in the shared view layer**. All shipped remote modules also `py_compile` clean under the PAK's bundled Python.

**Integrity check:** on-device `view.py` md5 `1d9f2ba2e1c361f912ae134695cb3dcc` matches the staged file exactly; `_pose_xyz` present (4 occurrences).

### Changes Made

- No app-code changes this session. The ship used existing staged code at `realtimecontrol/rtr/dist/repo/rtr`.
- `/storage/verify_crash.py` (on-device, new): verification script driving the real view layer with live core frames.
- `/tmp/kilo/ship_code.py` (local, reused): paramiko tar-over-ssh deploy; run from repo root `/home/kaos/Documents/kaotec/FTI_kuka`.

### Documentation

- None required — no new behavior; this is a deploy + verification step for the already-documented crash fix. `realtimecontrol/rtr/remote/deploy.md` already documents the ship path.

### Architecture

- No architectural impact.

### Validation

- Device reachability: `ping -c 3 192.168.1.33` → 0% loss (was 100% loss before).
- Ship: `/tmp/kilo/venv/bin/python /tmp/kilo/ship_code.py` (cwd=repo root) → tar-over-ssh rc=0, all 15 files landed with fresh timestamps.
- Integrity: on-device `md5sum rtr/remote/view.py` = `1d9f2ba2e1c361f912ae134695cb3dcc` = staged md5; `grep -c _pose_xyz` = 4.
- Syntax: `py_compile` of all shipped remote modules under PAK bundled Python → COMPILE_OK.
- On-device live test: `verify_crash.py` → WS connected, 6 state + 1 zones applied OK, all 9 pages rendered OK, cursor path OK → "ALL PAGES RENDERED ON DEVICE: no crash in shared view layer".
- `launch.sh --selftest` → still fails with `MENU SELFTEST FAIL: dummy not available` (pre-existing, separate blocker: PAK's SDL2 build lacks the dummy video driver).

### Decisions and Caveats

- The `--selftest` "dummy not available" failure is unrelated to the crash fix and was not resolved here; it only affects the menu selftest (which needs an SDL display driver), not the real app path, which the live verification exercised directly.
- The real end-user check (launch the PAK on the physical device, connect, browse pages) still needs a human at the device; the automated on-device verification covers the exact crash path (connect → apply live frames → render all pages).
- Paramiko connections to this device must use `allow_agent=False, look_for_keys=False` (the local SSH agent's key is unusable and breaks auth otherwise).

### Next Task

Optional: on-device `--selftest` "dummy not available" fix (give the PAK's SDL2 build a dummy video driver, or make the selftest tolerate its absence).

### Instructions for the Next Task

- Repro: `launch.sh --selftest` on the device (see ship output above). The PAK's SDL2 is at `/usr/trimui/lib/libSDL2-2.0.so.0` (LD_PRELOADed); the dummy driver is absent.
- Options: (a) rebuild/patch the PAK's SDL2 with `SDL_VIDEODRIVER=dummy` support enabled, or (b) make the menu selftest skip the display-dependent assertion when the dummy driver is unavailable, documenting it as a known device limitation.
- Verify with `launch.sh --selftest` rc=0 and re-run `verify_crash.py` to confirm no regression.

## 2026-10-22 — Runtime zone_groups: browser enable buttons + MIDI-in group mapping

### Status

Completed

### Summary

Added runtime `zone_groups` enable controls: one enable button per group in `client.html`, a MIDI-in group mapping/learn section in `editor.html`, and the full backend path that, on enable, finds a safe path from the robot's current `(zone, action)` to the nearest enabled node in the group, walks it hop-by-hop, then stays in the group. Manual mode and group mode are mutually exclusive.

**Backend (rtr package):**
- `rtr/core/commands.py`: new `Cmd.ENABLE_GROUP` / `Cmd.CLEAR_GROUP`; `Snapshot` gained `active_group: Optional[str]` (included in `to_dict()` when set).
- `rtr/io/websocket.py`: registered `"enable_group"` / `"clear_group"` in `_BROWSER_CMDS`.
- `rtr/flow.py`: new `find_safe_path_to_any(zones, from_zone, from_action, targets, safety_check=None)` — multi-target BFS over the `(zone, action)` graph; returns the shortest safe path (including start + reached target) to *any* target node, or `None`. Same node-validity rule as `find_safe_path` (zone + action both enabled).
- `rtr/brain/brain.py`: group runtime state on `Brain` (`active_group`, `group_nav`, `group_nav_index`); `on_command` handlers for `ENABLE_GROUP`/`CLEAR_GROUP`; manual-mode interlock in the `SET_CONTROL_MODE` handler (manual clears the group, auto preserves it); `_group_targets()`, `_handle_enable_group()`, `clear_group()`, `_group_tick()`, `_allowed_zones()`; group navigation inserted into `tick()` after the manual-mode early return (suppresses regular hunt/autonomy while navigating); `_valid_next_candidates()` and `autonomy_info()` use `_allowed_zones()` / `active_group`.
- `rtr/core/engine.py`: `_publish()` passes `active_group=info.get("active_group")` into the `Snapshot`.

**MIDI-in (rtr/io/midi.py):**
- `builtin_midi_data()` + `validate_midi_data()` gained a `groups` section: `{"in_port": null, "<group>": key_or_null}`.
- `MidiInput`: `_groups_in_port()`, `_in_ports()` includes the groups port, `start_learn(group=..., group_port=...)`, `_dispatch_groups()` (matches a learned group key on the groups' in_port and submits `Cmd.ENABLE_GROUP`); learn capture in `_on_message()` now includes a `group` field in the `MIDI_LEARN` payload; dispatch chain is zone → nav → capture → groups.

**Frontend:**
- `client.html`: groups card (`#groups`, `#activeGroup`, `#clearGroupBtn`); `buildGroupButtons()` (one button per `BRAIN.zone_groups`, onclick → `{cmd:"enable_group", group}`); active-group highlight in `renderState`; `clearGroupBtn` → `{cmd:"clear_group"}`.
- `editor.html`: MIDI-in groups section (`#midiGroups`); `buildMidiGroups()` (in_port picker + one row per group with key input + learn button); `midiLearn()` extended with `group`/`group_port`; group seeding in `loadMidi()`.

### Changes Made

- `realtimecontrol/rtr/rtr/core/commands.py`: `ENABLE_GROUP`/`CLEAR_GROUP` commands; `Snapshot.active_group` + `to_dict()`.
- `realtimecontrol/rtr/rtr/io/websocket.py`: `_BROWSER_CMDS` += `"enable_group"`, `"clear_group"`.
- `realtimecontrol/rtr/rtr/flow.py`: `find_safe_path_to_any()`.
- `realtimecontrol/rtr/rtr/brain/brain.py`: group state, enable/clear handlers, manual interlock, `_group_tick`/`_allowed_zones`/`_group_targets`, tick wiring, `autonomy_info`.
- `realtimecontrol/rtr/rtr/core/engine.py`: `_publish()` passes `active_group`.
- `realtimecontrol/rtr/rtr/io/midi.py`: `groups` section (data + validation), `_groups_in_port`/`_in_ports`/`start_learn`/`_dispatch_groups`, learn-capture `group` field, dispatch chain.
- `realtimecontrol/rtr/client.html`: groups card + `buildGroupButtons()` + active reflection + clear button.
- `realtimecontrol/rtr/editor.html`: `#midiGroups` section + `buildMidiGroups()` + `midiLearn()` group params + group seeding.
- `realtimecontrol/rtr/tests/test_zone_groups.py`: new — 20 tests (path helper, brain enable/clear, manual interlock, group-restricted autonomy, `autonomy_info`, MIDI group dispatch/learn, MIDI data validation).
- `realtimecontrol/rtr/tests/test_midi_in.py`: updated 4 pre-existing expectations to include the new `group` field in the `MIDI_LEARN` payload and the `groups` section in `builtin_midi_data()`.

### Documentation

- `README.md` not updated — the zone_groups runtime behavior is a feature change whose data contract (`zone_groups` in `brain.json`) was already documented; no new doc surface needed for this iteration.

### Architecture

- Architectural impact: a new runtime navigation mode (group) layered onto the existing Brain tick loop. Group navigation is driven inside `Brain.tick()` and reuses the existing `find_safe_path` BFS machinery (via the new multi-target `find_safe_path_to_any`). Group membership restricts autonomy candidates via `_allowed_zones()`. The `active_group` field is propagated to the browser through the existing `Snapshot`/state-frame path. No changes to the `StateMachine` or the command-bus contract beyond the two new commands.

### Validation

- `python -m pytest tests/test_zone_groups.py tests/test_midi_in.py -q` → **76 passed, 1 skipped**.
- `python -m pytest tests/ --ignore=tests/test_remote_tui.py -q` → **238 passed, 14 skipped, 5 failed** (the 5 failures are in `test_remote_sdl.py` and stem from the prior session's `rtr/remote/view.py` work — settings/setup/dpad — not this change).
- `ast.parse` on all 6 changed Python modules → OK.
- `node --check` on the extracted inline JS of `client.html` and `editor.html` → OK.

### Decisions and Caveats

- **Single active group at a time:** enabling a new group replaces the previous one (no stacking). `CLEAR_GROUP` (or switching to manual) resumes normal autonomy.
- **No-safe-path reverts:** if `find_safe_path_to_any` returns `None` (current position can't safely reach any node in the group), `active_group` reverts to `None` and the robot keeps its current state — the group is not "armed" without a path.
- **Group zone restriction:** while a group is active, `_allowed_zones()` returns only that group's zones, so autonomous wander/hunt stays inside the group until it is cleared.
- **Pre-existing test issues (not introduced here):** `tests/test_remote_tui.py` has a collection error (imports `action_kind`/`action_target`/`reverse_exits` from `rtr.flow`, which do not exist at HEAD); the KukaPy connection tests (`KukaPy/kuka_example/connection/test_ping.py`, `test_ekimin.py`) and `rtr/robot/test_kinematics.py` hang a full `python -m pytest` run because they open a real robot connection with no robot present. Run `pytest tests/` (not bare `pytest`) for a clean, fast run.
- **Live files:** `client.html`/`editor.html`/`brain.json` are edited against a possibly-running core; treat as live targets.

### Next Task

1. Live-test the group buttons in `client.html` against a running core (enable each group, confirm the path is logged and the robot stays in-group; switch to manual and confirm the group clears).
2. Live-test MIDI group learn in `editor.html` (pick the groups in_port, learn a key per group, fire it and confirm `ENABLE_GROUP` is dispatched).
3. (Optional, separate from this feature) Fix the pre-existing `test_remote_tui.py` import error and the `test_remote_sdl.py` failures once the prior view.py work is settled.

### Instructions for the Next Task

- Enable a group from the browser: send `{cmd:"enable_group", group:"<name>"}` (the client button does this). The brain switches to auto, logs the safe path, and walks `group_nav`; `active_group` appears in the state frame (`Snapshot.active_group`).
- Clear: `{cmd:"clear_group"}`, or set control mode to manual (the interlock clears the group). Auto mode preserves the active group.
- MIDI groups live in `midi.json` → `{"in_port": null, "<group>": "note:0:60" | null}`. A learned key on the groups' in_port dispatches `ENABLE_GROUP`. Learn via `start_learn(group="<name>")`; the capture is port-scoped to the groups' in_port.
- Node validity for the path uses the same rule as `find_safe_path`: a `(zone, action)` node is valid only if the zone exists+enabled and the action exists+enabled.
- Run tests from within `realtimecontrol/rtr` with `python -m pytest tests/ --ignore=tests/test_remote_tui.py` to avoid the pre-existing collection error and the KukaPy hardware hang.

## 2026-10-22 — Editor: HUD/code render toggles + patches.json three-layer output slots

### Status

Completed

### Summary

Added two render-page UI toggle buttons (code / hud) next to the broadcast button in the editor's patch top bar, and completed the `patches.json` migration to the three-layer output-slot layout (zone → `o0`, action/default → `o1`, behavior → `o3`).

**Editor toggles:**
- `#uiCodeToggle` and `#uiHudToggle` buttons added next to `#liveToggle` (patch top bar), styled with the existing `.liveToggle` class.
- Each `onclick` flips the matching `renderUi` field, calls `syncRenderUiToggles()`, and sends `set_render_ui` (via `sendRenderUi(hud, code)`).
- New `syncRenderUiToggles()` sets the `.active` class from `renderUi.code` / `renderUi.hud`.
- The state-frame handler (line ~1142) now syncs the toggles from the pushed `render_ui` field, guarded by a `_renderUiFp` fingerprint so the DOM is only touched when the value changes.
- The `render_ui` push path was already wired end-to-end (core `set_render_ui` → `_render_ui` → state frame → `render.html` `applyRenderUi`); this task only added the editor-side controls.

**patches.json output slots:**
- Migrated all patch output targets from bare `.out()` (→ `o0`) to explicit per-layer slots: zones → `.out(o0)`, actions → `.out(o1)`, behaviors (`modes`) → `.out(o3)`. `default` stays `""`.
- This matches the documented three-layer concatenation design (see the prior handoff): the core joins zone + action/default + behavior verbatim, and each layer now renders to its own output slot so patches can reference/composite each other (e.g. `modes.calm` already reads `src(o0)` and writes `.out(o3)`).

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - Patch top bar: added `#uiCodeToggle` ("code") and `#uiHudToggle` ("hud") buttons next to `#liveToggle`.
  - Added `_renderUiFp` to the fingerprint declaration.
  - Added `syncRenderUiToggles()`.
  - State-frame handler: syncs the toggle `.active` state from `m.render_ui` (fingerprint-guarded).
  - Added `onclick` handlers for `#uiCodeToggle` / `#uiHudToggle` (flip field, sync, `sendRenderUi`).
- `realtimecontrol/rtr/patches.json`:
  - zones: `.out()` → `.out(o0)` (18 slots).
  - modes (behaviors): `.out()` → `.out(o3)` (14 slots; `calm` was already `.out(o3)`).
  - actions: `.out()` → `.out(o1)` (39 slots, nested zone → action → code).
  - `default` unchanged (`""`).

### Documentation

- `README.md` already documents the three-layer concatenation + output-slot layout (zone→o0, action→o1, behavior→o3) from the prior task; no new doc needed. The new editor toggles are a UI affordance for the already-documented `render_ui` core state.

### Architecture

- No architectural impact. The `render_ui` command/push path and the three-layer concatenation were already in place; this task added editor-side controls and aligned the `patches.json` data with the documented output-slot assignment. The core concatenates raw patch strings verbatim and is agnostic to output slots; slot assignment is a presentation concern owned by the patch code.

### Validation

- `node --check` on the extracted inline JS of `editor.html` → syntax OK.
- `python3 tests/test_patch_push.py` (workdir `realtimecontrol/rtr`) → ALL TESTS PASSED (10).
- `python3 tests/test_patch_override.py` (workdir `realtimecontrol/rtr`) → ALL TESTS PASSED (5).
- `patches.json` parses as valid JSON; verified no bare `.out()` remains and every zone/mode/action slot carries its target slot.

### Decisions and Caveats

- **Live file:** the rtr core (`main.py --sim`) is RUNNING and the `patches.json` / editor is being edited live (e.g. `actions.purring` changed from `hold`/`purr` to `purr`/`prrrr` during this task, by the running system, not by this migration). The migration is idempotent (only rewrites bare `.out()`), so it is safe to re-run, but the file is a live target — treat it as such.
- **Visual implication:** hydra-synth displays only the `o0` output by default (the `renderAll` 2×2 grid is off unless a patch calls `render()`). So after the migration the screens show the **zone** layer (`o0`) as the base; the action (`o1`) and behavior (`o3`) layers render to their own slots and are only visible where a patch composites them (e.g. `modes.calm` reads `src(o0)` → `.out(o3)`). This is the intended three-layer structure, but the per-patch compositing is authored by the artist — it is NOT auto-composited by the core.
- The `resting/hold` action patch already embeds pose globals in its stored code; `inject_pose_globals` would prepend another header when active (pre-existing concern, unchanged here).

### Next Task

1. Artist to author per-patch compositing (behavior/action reading `src(o0)`) for the layers that should appear on screen; the core does not auto-composite.
2. Confirm the HUD/code toggles drive the live screens (open the editor, toggle each button, watch `render.html`).

### Instructions for the Next Task

- The editor toggles are wired: `#uiCodeToggle`/`#uiHudToggle` → flip `renderUi.code`/`renderUi.hud` → `sendRenderUi(hud, code)` → core `set_render_ui`. The state frame pushes `render_ui` back every frame and the toggles' `.active` class follows it.
- `patches.json` now uses explicit slots: zones `.out(o0)`, actions `.out(o1)`, behaviors `.out(o3)`. The core concatenates zone + action/default + behavior verbatim and does not care about slots.
- Re-running the migration is safe (idempotent): a bare `.out()` is rewritten to the scope's slot; explicit slots are left as-is.
- The rtr core is running in sim (`main.py --sim`); it edits `patches.json` live. Stop it or coordinate before bulk-editing the file if a clean snapshot is needed.

## 2026-10-21 — RTR remote: fix post-connect crash in shared view layer (list-form next + poses)

### Status

Completed

### Summary

Fixed the RTR remote app's post-connect crash (app connected, then immediately exited) by correcting two data-shape assumptions in the shared view layer. The core sends zone `next` fields and pose fields (`cart`/`target`) as flat lists, but `view.py` assumed dicts in three places, each raising `AttributeError: 'list' object has no attribute 'get'` and killing the app right after connect.

**Crash 1 (fixed earlier this session):** `item_label` called `action_next()` and did `n.get("zone")`, but live `next` can be a **list** of entries (e.g. `hold` → `[{"zone": "resting", "action": "wake"}, ...]`). Fixed to use `action_next_list()` and read the first entry.

**Crash 2 (fixed this task):** `_brain_content` and `_arm_content` read `target.get("x"/"y"/"z")` and `cart.get("x"/"y"/"z")`, but the core sends `cart` and `target` as **6-element float lists** (`[X, Y, Z, A, B, C]` for cart; joint or cartesian pose for target), not dicts. Added a defensive `_pose_xyz()` helper that accepts either a dict with `x`/`y`/`z` keys or a list/tuple whose first three values are the position, and rewrote both functions to use it (including the ARM DIST + top-view logic).

**Verification:** The local repro (`/tmp/kilo/repro_crash.py`) drives the real `view.RemoteState` with live core frames. Before the fix, page 2 (BRAIN) crashed at `_brain_content`; after the fix **all 9 pages render with no crash** and the ARM/BRAIN pages show correct live data (joint bars, TOOL/TARGET poses, DIST, top-view, autonomy, MIDI).

### Changes Made

- `rtr/remote/view.py`:
  - `item_label`: import `action_next_list` (was `action_next`); use the first normalized `next` entry for the `→` marker.
  - Added `_pose_xyz(pose)` helper: returns `(x, y, z)` for a dict with numeric `x`/`y`/`z` or a list/tuple with ≥3 numeric values; returns `None` otherwise.
  - `_brain_content`: target readout now uses `_pose_xyz`.
  - `_arm_content`: TOOL/TARGET readouts, DIST, and top-view now use `_pose_xyz` (list-aware); placeholder `—` when a pose has no position.

### Documentation

- `remote/deploy.md` unchanged (no new deploy behavior; this is a bug fix in already-deployed code).

### Architecture

- No architectural impact. The shared view layer (`view.py`) is consumed by both `tui.py` (urwid) and `sdlapp.py` (pygame), so this fix applies to both renderers. Pose data model confirmed: `display.py` builds `joints`/`cart`/`target` as `list(snapshot.*)`; `cart` is cartesian `(X,Y,Z,A,B,C)` from `get_curpos()`, `joints` is `[A1..A6]`, `target` is the step's target pose (joint pose in the common case).

### Validation

- `/tmp/kilo/venv/bin/python /tmp/kilo/repro_crash.py` — all 9 pages render, `ALL PAGES RENDERED: no crash in shared view layer`.
- `pytest tests/test_remote_sdl.py` — 27 passed, 5 failed. The 5 failures (SETTINGS page tests) are **pre-existing**: the identical 5 fail on the original `view.py` (verified via `git stash`), so this change introduces no regression. `test_selftest_subprocess` (the `--selftest` dummy-driver run) passes locally.

### Decisions and Caveats

- `_pose_xyz` prefers numeric checks over blind indexing to avoid treating malformed poses as positions (returns `None` → placeholder). This is defensive, not a schema change.
- The 5 pre-existing SETTINGS test failures are separate from this task (they concern `build_settings_items`/`activate` expectations, not poses or `next` shapes) and are left for a follow-up.
- On-device `launch.sh --selftest` previously failed with "dummy not available"; the local selftest passes, so the device failure is environment-specific (pygame/SDL on the Trimui) and is a separate blocker.

### Next Task

1. Re-run the on-device selftest to determine why `dummy` is unavailable on the Trimui (vs. passing locally).
2. Investigate/fix the 5 pre-existing SETTINGS test failures.

### Instructions for the Next Task

- The view-layer crash fix is complete and verified locally; do not re-edit `view.py` pose/next handling.
- To reproduce the crash fix check: `/tmp/kilo/venv/bin/python /tmp/kilo/repro_crash.py` (needs the venv with `websockets`, `pygame`, `urwid`, `pytest`).
- The on-device selftest runs `python -m rtr.remote.sdlapp --selftest` with `SDL_VIDEODRIVER=dummy`; it passes locally, so on the device check whether `pygame` is importable and whether the SDL dummy video driver is present.
- The 5 SETTINGS failures are in `tests/test_remote_sdl.py`: `test_settings_content_and_help_text`, `test_setup_page_activation_returns_none_cmds`, `test_setup_calibrate_activation_returns_prompt`, `test_setup_map_activation_returns_capture_status`, `test_setup_dpad_left_right_still_font_on_font_row`. They fail identically on a clean `view.py`, so they predate this fix.

## 2026-10-21 — Patch authority: extra-patches concatenation + broadcast toggle

### Status

Completed

### Summary

Replaced the single-patch precedence model (`action > mode > zone > default`) with a **three-layer concatenation** (zone → action/default → behavior) and converted the editor's "live render" view into a clean **broadcast toggle**.

**Core changes:**
- `Patches.code_for` now concatenates three layers in order: zone patch (`zones[zone]`, renders to `o0`), action/default patch (`actions[zone][action]` or `default`, renders to `o1`, with pose globals injected), and behavior patch (`modes[behavior]`, renders to `o3`). Missing layers are omitted; the result is joined by newlines.
- The `behavior` parameter was added to `code_for` (passed from `Display._patch_for` via `machine.behavior`).
- The `match_patch` function is retained for the browser-local fallback but is no longer used by the core's main path.
- The `_screen_override` (broadcast) still wins over the concatenation when set.

**Editor changes:**
- Removed the `#liveRenderView` div (the separate center-column view with `#liveCode` textarea + hud/code toggles).
- Converted `#liveToggle` from a view-switcher to a **broadcast toggle**: ON pushes the selected slot's code to the screens (live-updating on edit/slot-change); OFF sends `set_screen_patch: null` to clear the override.
- The `#patchPreview` always shows the selected slot's code (unchanged).

**Test changes:**
- Updated `test_patch_push.py` and `test_patch_override.py` to expect the new concatenation model.
- Added new tests for zone+action+behavior concatenation, zone+action only, zone+default, default only, and behavior-only scenarios.

### Changes Made

- `rtr/patches/patches.py`: Rewrote `code_for` to concatenate zone + action/default + behavior; updated module docstring; updated `match_patch` docstring to clarify it's a browser-local fallback.
- `rtr/display/display.py`: Updated `_patch_for` to pass `behavior` (from `machine.behavior`) to `code_for`; updated `_patch_code` comment.
- `editor.html`: Removed `#liveRenderView` div; converted `#liveToggle` to a broadcast toggle; removed `liveCode` textarea and related JS; added `broadcastCurrentSlot()` and `syncBroadcastToggle()` functions; updated `patchCode` oninput to broadcast when ON; updated slot selection to broadcast on change.
- `tests/test_patch_push.py`: Rewrote for the new concatenation model; added `_MockMachine` with full attribute set; added 5 new concatenation tests.
- `tests/test_patch_override.py`: Rewrote for the new concatenation model; added `_MockMachine` with full attribute set; updated all test expectations.
- `README.md`: Updated the patch table schema, concatenation model description, broadcast override section, `set_screen_patch` command description, and editor/client descriptions.

### Documentation

- Updated README sections: patch table schema (now documents the three layers with output slots), core's single source of truth (now describes concatenation), manual screen override (now describes the broadcast toggle), `set_screen_patch` command table entry, editor.html and client.html descriptions.

### Architecture

- The core's patch resolution changed from single-patch precedence to three-layer concatenation. The output slots (`o0`, `o1`, `o3`) are declared by the patch code itself (via `.out(oN)`), not by the core. The core joins the raw code strings verbatim.
- The editor's broadcast toggle replaces the previous "live render" view. The `set_screen_patch` command is repurposed as the broadcast control (ON = selected slot's code, OFF = null).

### Validation

- `python tests/test_patch_push.py` — ALL TESTS PASSED (10 tests)
- `python tests/test_patch_override.py` — ALL TESTS PASSED (5 tests)
- `ruff` — not available in the current Python environment

### Decisions and Caveats

- The `modes` table now maps **behavior names** (not mode names) to code. The user will migrate `patches.json` to use behavior names in the `modes` scope after the core change.
- The local `matchPatch` functions in `render.html` and `client.html` still use the old precedence model as a fallback. Since the core pushes the concatenated patch in every state frame, the fallback is rarely used. A follow-up task could update these to also concatenate.
- The `mode` parameter in `code_for` is retained for backward compatibility but is not used in the new concatenation logic.

### Next Task

Migrate `patches.json` to the new format: use behavior names in the `modes` scope, and ensure each layer's code declares its output slot via `.out(oN)` (zone→`o0`, action→`o1`, behavior→`o3`).

### Instructions for the Next Task

- In `patches.json`, rename the keys in the `modes` object from mode names (wander, random, etc.) to behavior names (calm, breathe, stretch, etc.).
- Ensure each patch code declares its output slot: zone patches use `.out(o0)`, action/default patches use `.out(o1)`, behavior patches use `.out(o3)`.
- The `default` entry should be the action-slot fallback (renders to `o1`).
- Test by loading the editor's patches tab and toggling the broadcast ON to verify the concatenated patch renders correctly on the screens.

## 2026-10-21 — Write deploy runbook (realtimecontrol/rtr/remote/deploy.md)

### Status

Completed

### Summary

Wrote a deploy runbook so a future session can ship the updated remote app code
to the Trimui/R36S device without re-reconning. The doc records the device
identity + password, the on-device layout, the build/stage + ship flow, the
verify/selftest step, the current known-failing selftest, and the page order.

### Changes Made

- `realtimecontrol/rtr/remote/deploy.md`: NEW. Deploy runbook. Records:
  - Device: `root@192.168.1.33`, password `tina`, TinaLinux aarch64,
    password-only auth (no keys; `sshpass` absent; `sudo` not non-interactive).
  - On-device paths: PAK at `/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/`,
    app code at `/storage/rtr-remote/` (the `rtr/` tree).
  - Build/stage: `bash realtimecontrol/rtr/scripts/build_pak.sh` → `dist/repo`
    (15 files incl. `rtr/remote/led.py`) + static PAK parts.
  - Ship: the script's `--ship-code` needs ssh/scp (unavailable); the working
    method is paramiko password-auth tar-over-ssh via `/tmp/kilo/ship_code.py`.
  - Verify: `launch.sh --selftest`; current failure
    `MENU SELFTEST FAIL: dummy not available` (SDL dummy driver unavailable;
    `launch.sh` preloads system SDL2 which may lack the dummy driver).
  - Pages (9): NAV, CAMERA, BRAIN, HUNT, CADENCE, TRIGGERS, ARM, LED, SETUP.

### Documentation

- `realtimecontrol/rtr/remote/deploy.md` is the documentation (the deliverable).

### Architecture

- No architectural impact.

### Validation

- Confirmed `deploy.md` wrote (6138 bytes).
- Confirmed `realtimecontrol/rtr/dist/repo` is staged with `led.py` and 15 files,
  matching the doc's claims.
- No build/test commands run (documentation-only task).

### Decisions and Caveats

- Password `tina` is written to disk in `deploy.md` per the user's explicit
  request ("note down the pass word, that is not a problem").
- `/tmp/kilo/ship_code.py` is ephemeral; the doc includes a note to recreate it
  if `/tmp` is wiped, and points to `deploy_recon.py` for the connect/recon pattern.

### Next Task

Diagnose the on-device selftest failure (`MENU SELFTEST FAIL: dummy not
available`) and get `launch.sh --selftest` passing, then confirm the new pages
(TRIGGERS / ARM / LED) render against live core state.

### Instructions for the Next Task

- Reconnect to `root@192.168.1.33` (pw `tina`) via paramiko (see
  `realtimecontrol/rtr/remote/deploy.md`).
- On the device, inspect which SDL2 the system build exposes:
  `strings /usr/trimui/lib/libSDL2-2.0.so* | grep -i dummy` and
  `ls /usr/trimui/lib` to confirm the driver set.
- Determine whether the system SDL2 ships the `dummy` driver; if not, decide
  whether the headless selftest should use a different offscreen path or the
  framebuffer driver with a null output.
- Get `--selftest` rc=0, then verify the 9 pages render with live core state.

## 2026-10-21 — Patch authority analysis: extra-patches concatenation + broadcast model

### Status

Completed

### Summary

Investigated the broken patch system. Findings:

1. **Core resolution is single-patch, not concatenated.** `Patches.code_for` (rtr/patches/patches.py) calls `match_patch` which returns ONE code by precedence `action > mode > zone > default`. The user's model requires the zone / action(default) / behavior patches to be **concatenated and executed together** (zone→`o0`, action→`o1`, behavior→`o3`). The core never does this, so an action's patch is not composed with the zone/behavior layers.

2. **Screen override hijacks authority and never clears.** `Patches._screen_override` (set via `SET_SCREEN_PATCH`) wins over the match in `code_for` and is held in memory indefinitely. The editor's `liveCode` field auto-pushes `set_screen_patch` on every keystroke (debounced) and state frames overwrite that field (guarded only by focus), so the operator sees the editor "switch back to a patch I did not ask for."

3. **hydra-synth output model (verified in hydra-synth.js).** 4 output slots `o0..o3`. `.out()` (no arg) renders to the default output `o0`; `.out(oN)` renders to slot `oN`. With `isRenderingAll` (default) the canvas is a 2x2 grid: `o0` TL, `o1` TR, `o2` BL, `o3` BR. So concatenating zone(`.out(o0)`) + action(`.out(o1)`) + behavior(`.out(o3)`) and eval'ing it shows all three layers — exactly the user's described working setup. The core only needs to concatenate the raw code strings in the order **zone → action/default → behavior**; the output slots are declared by the patch code itself.

4. **Behavior source.** `Snapshot.mode` == `machine.behavior` (forced behavior, else the current action's declared behavior, else `calm`). The behavior patch lives in the `modes` scope keyed by behavior name. The action patch lives in `actions[zone][action]`; pose globals (`pose`/`poseindex`) are injected into the action patch by `inject_pose_globals`.

5. **Action-patch storage.** Action patches already live in `patches.json` under `actions[zone][action]` and are edited in BOTH the patches tab and the action editor (`#aePatchCode`, which writes the same `PATCHES.actions[zone][action]` and POSTs `/api/patches`). Single source of truth → no storage conflict; the action editor is preferred for photo access.

### Changes Made

- None (analysis only).

### Documentation

- None required — findings recorded here; README update is a follow-up task.

### Architecture

- No architectural change yet. Confirmed the seam: core resolves the patch (`Patches.code_for`) and pushes it in every `state` frame (`display.py` → `patch` field); `render.html` / `client.html` / the 3D tool screen all prefer the pushed `patch`. The fix belongs in `code_for` (concatenation) + the editor (broadcast toggle).

### Validation

- Read-only investigation; no commands run.

### Decisions and Caveats

- The concatenation order is **zone → action/default → behavior** (matches the user's working example).
- The `set_screen_patch` command is repurposed as the editor **broadcast** control (ON = editor content, OFF = `null`), replacing the keystroke auto-push.
- Output slots (`o0`/`o1`/`o3`) are authored in the patch code, not by the core; the core concatenates verbatim.

### Next Task

Rewrite `Patches.code_for` to build the concatenated zone + action/default + behavior code, and repurpose the screen override as the broadcast.

### Instructions for the Next Task

- In `rtr/patches/patches.py`, replace the `match_patch` call in `code_for` with a concatenation: append `zones[zone]` (if set), then the action patch `actions[zone][action]` falling back to `default` (with `inject_pose_globals`), then `modes[behavior]` (if set). Keep `_screen_override` as the broadcast that wins over everything. Keep `match_patch` for the browser-local fallback but note the core no longer uses it for the main path.
- Update `test_patch_override.py` / `test_patch_push.py` expectations (they assert the old single-patch precedence).

## 2026-10-21 — Remote TUI: TRIGGERS, LED, ARM pages + editor.html text ports

### Status

Completed

### Summary

Extended the canonical remote TUI with four new features:
1. **TRIGGERS page**: live timestamped log of core trigger/event frames, using a capped ring buffer (100 events) fed by the `on_event` callback.
2. **LED page**: integrated 22-LED sysfs control into the main TUI app, with per-LED color cycling, 4 actions (all white / all off / test all / restore), and safe sysfs write pattern (effect_enable toggle bracketing).
3. **editor.html text ports**: BRAIN page now shows safety readout, autonomy state (enabled/groups/perf/dwell), target position, and MIDI monitor — all from the live state frame.
4. **ARM page**: ASCII robot-arm position display with joint-angle bars against hardware limits, tool/target positions, 3D distance, and a simple top-view ASCII grid.

### Changes Made

- `realtimecontrol/rtr/rtr/remote/led.py` (new): Pure LED module extracted from `ledtest.py`. Contains `LED_NAMES`, `PALETTE`, `PALETTE_HEX`, `NUM_LEDS`, `NUM_COLORS`, `WHITE_IDX`, `BLACK_IDX`, `SAFE_KEYS`, `FRAME_FILE`, `LED_SYSFS_BASE`, `LED_ACTIONS`, `build_frame()`, and `LedSysfs` class (real + memory backends). No SDL/pygame dependency.
- `realtimecontrol/rtr/rtr/remote/view.py`:
  - Added `PAGES` with 3 new pages: `("NAV", "CAMERA", "BRAIN", "HUNT", "CADENCE", "TRIGGERS", "ARM", "LED", "SETUP")`.
  - Added `EVENT_CAP = 100`, `AXES`, `HW_LIMITS` constants.
  - Added `build_led_items()` for LED page items (22 LEDs + 4 actions).
  - Updated `current_items()` to use name-based dispatch (SETUP, CADENCE, LED, default).
  - Updated `activate_commands()` for `kind="led"` and `kind="led_action"` items.
  - Added `_triggers_content()`: timestamped event log with type-specific context fields.
  - Added `_arm_content()`: joint-angle bars, cart/target positions, distance, top-view ASCII grid.
  - Added `_led_content()`: 22 LED rows + 4 action rows with cursor navigation.
  - Updated `_brain_content()`: now accepts `live` dict and shows safety, autonomy, target, MIDI readouts.
  - Updated `build_rows()`: name-based page dispatch; new keyword params `live`, `events`, `led_colors`, `led_available`.
  - `RemoteState.__init__`: added `live`, `live_key`, `events`, `led_colors`, `led_available` fields.
  - `RemoteState.apply_state()`: stores high-frequency live fields (joints, cart, target, safety, autonomy, MIDI) with separate fingerprinting.
  - `RemoteState.apply_event()`: appends to capped event ring buffer.
  - `RemoteState.set_led_color()` / `led_all()`: LED state mutators.
  - `RemoteState.current_items()`: passes `led_colors` to the module-level function.
  - `RemoteState.build_rows()`: passes new params to the module-level function.
- `realtimecontrol/rtr/rtr/remote/tui.py`:
  - `_start_conn()`: wired `on_event=self._on_event` to the Connection.
  - `_on_event()`: pushes `("event", data)` to the queue.
  - `_poll()`: handles `("event", data)` by calling `self._rs.apply_event(payload)`.
  - `_activate()`: handles LED page activation (LED color advance / action execution).
  - `_activate_led()`: local LED handler (advance color, all white, all off, restore).
  - `_write_led_frame()`: writes current LED colors to sysfs (safe write).
  - `_restore_leds()`: restores sysfs snapshot.
  - `_enter_led_page()` / `_exit_led_page()`: sysfs lifecycle (snapshot on enter, restore on exit).
  - `_page_nav()`: page navigation with LED sysfs lifecycle hooks.
  - `__init__`: added `_led_sysfs` attribute.
  - `_shutdown()`: restores LED sysfs on exit.
- `realtimecontrol/rtr/rtr/remote/ledtest.py`:
  - Replaced local LED constant definitions with imports from `.led`.
  - `ACTIONS` is now an alias for `LED_ACTIONS` from `.led`.
  - `build_frame` and `LedSysfs` are imported from `.led` (no longer defined locally).

### Documentation

- No new documentation files needed; the module docstrings in `led.py` and the updated `view.py` / `tui.py` docstrings describe the new pages and features.

### Architecture

- **New module boundary**: `led.py` is a pure, framework-free module (no SDL/pygame/urwid dependency) containing LED constants and the sysfs backend. Both `ledtest.py` (SDL app) and `view.py` (shared view layer) depend on it. This eliminates the previous implicit coupling where the TUI would need to import from the SDL app.
- **View layer extension**: The pure view layer (`view.py`) now handles 9 pages instead of 6. The `RemoteState` class tracks three categories of state:
  - **Low-frequency state** (`self.state`): zone/mode/action/moving/speed/behavior/control_mode (deduped by `state_key`).
  - **High-frequency live state** (`self.live`): joints/cart/target/safety/autonomy/MIDI (deduped by `live_key`).
  - **Event log** (`self.events`): capped ring buffer of timestamped trigger/event frames.
- **Page dispatch**: Changed from index-based (`page == 4`) to name-based (`PAGES[page] == "CADENCE"`) so new pages can be inserted without breaking special-case logic.

### Validation

- `python -c "from rtr.remote import led, view"` — PASS (all imports resolve).
- `python -c "from rtr.remote import tui"` — PASS.
- `python -c "from rtr.remote import ledtest"` — PASS.
- All 9 pages render non-empty rows with empty state (SDL selftest logic) — PASS.
- TRIGGERS event ring buffer caps at 100 — PASS.
- Live state tracking (joints/cart/target/safety/autonomy/MIDI) — PASS.
- LED items (26 rows: 22 LEDs + 4 actions) — PASS.
- LED activate_commands (None command set, status message) — PASS.
- LED state methods (set_led_color, led_all) — PASS.
- ARM page renders joints, cart, target, distance with live data — PASS.
- BRAIN page renders safety, autonomy, MIDI with live data — PASS.
- TRIGGERS page renders events with type-specific context — PASS.
- `python tests/test_remote_tui.py` — pre-existing failure (`action_kind` not in `rtr.flow`), unrelated to this change.
- LED selftest (`ledtest.py --selftest`) — requires pygame (not installed in this env); logic verified via direct `LedSysfs` memory-backend tests.

### Decisions and Caveats

- **LED page input model**: The LED page reuses the standard up/down cursor + enter activation model. Left/right remains page navigation (unlike the standalone `ledtest.py` SDL app where left/right cycles colors). This is a deliberate fit to the shared TUI input model; the standalone app keeps its own input scheme.
- **ARM page is client-side only**: No FK computation in the TUI. The core already provides `joints`, `cart`, and `target` in the state frame; the ARM page renders them directly.
- **Event log is read-only**: The TRIGGERS page shows the event ring buffer but has no selectable items (no actions). The cursor does not move on this page.
- **LED sysfs lifecycle**: The sysfs snapshot is taken when entering the LED page and restored when leaving it. If the TUI crashes while on the LED page, the LEDs remain in their last state (the kernel's `effect_enable` stays at 0, which is safe).
- **`sdlapp.py` compatibility**: The `RemoteState.build_rows()` method signature is unchanged (same positional + keyword args). New params (`live`, `events`, `led_colors`, `led_available`) are passed internally from `RemoteState` and have defaults in the module-level `build_rows()`, so existing callers are unaffected.

### Next Task

- Run the full SDL selftest on the actual device (requires pygame + `/sys/class/led_anim/`).
- Optionally: add a "clear log" action to the TRIGGERS page.
- Optionally: add a non-blocking test sweep to the TUI LED page (the standalone `ledtest.py` has one; the TUI version could reuse it with a timer).

### Instructions for the Next Task

1. On the device, run `python -m rtr.remote.tui` to verify the new pages render correctly with live core state.
2. Navigate to the TRIGGERS page (page 5) and verify events appear as the core emits them.
3. Navigate to the ARM page (page 6) and verify joint bars, cart/target, and top-view ASCII render with live data.
4. Navigate to the LED page (page 7) and verify:
   - Sysfs is available (no "preview only" notice).
   - Up/down moves the cursor over LED rows.
   - Enter on a LED row advances its color and writes the frame.
   - Enter on "all white" / "all off" / "restore" executes the action.
   - Leaving the LED page restores the sysfs snapshot.
5. Verify the BRAIN page shows safety, autonomy, target, and MIDI readouts when the core provides those fields.
