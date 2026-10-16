# State machine consolidation + flowchart ("moods") editor

## Context

The RTR core (one asyncio process) is: `Engine` (tick loop) → `Brain` (mode policy +
target) → `StateMachine` (zone, transition, action) → `Zones` data (`zones.json`).
Adapters (MIDI, WebSocket, HTTP, sound, patches, display, camera) talk to the core only
through `Command`/`Event` on the `StateBus`.

Today the **state is split**: the machine owns (zone, transition, action), the Brain owns
(mode, target, flags, cadence). The user wants the state machine to be the **single owner
of the robot's state** so it can be worked on in isolation, and wants the zone graph
("moods") editable in `editor.html` as a **flowchart**: draw arrows to link zones, edit
zone exit positions in that tool.

## Resolved design decisions

1. **Full consolidation.** `StateMachine` owns the composite state
   **(zone, mode, action)** + in-progress transition + current target + speed + flags.
   `Brain` becomes a thin **command interpreter** (maps `Command` → machine operations);
   it holds no state and remains the plug-in seam for the LLM/chatbot.
2. **Per-zone default mode.** Optional `mode` field per zone in `zones.json`. On
   committing a zone transition the machine sets `mode` to that zone's default (and
   clears any playing action). `set_mode` commands still override at any time. Zones
   without a `mode` field keep the current mode.
3. **Per-edge exit pose + zone default.** Keep `exitpos` (the zone's default hand-off
   pose) and add an optional per-zone map `exitposes: {"<target-zone>": [6 numbers]}`.
   A transition hop toward `B` uses `exitposes[B]` if present, else `exitpos`. Fully
   backward compatible — existing `zones.json` keeps working unchanged.
4. **Flowchart = new 4th editor tab "flow".** Full-width 2D SVG canvas (nodes =
   zones/moods, arrows = exits) + right-hand detail panel. The zones/sound/patches tabs
   stay as-is.
5. **Isolation: in place, staged.** The machine stays at `rtr/state/`. Stage 1 builds
   the new `StateMachine` as a standalone deterministic module with its own test suite
   (drivable without engine/robot); stage 2 rewires `Brain` + `Engine` onto it.

## README staleness found (fixed in this plan)

- README points to `state/zones.py` "for the graph and exact limits", but `zones.json`
  is the source of truth, and the built-in `ZONES` fallback in `zones.py` has **drifted**
  from the file (e.g. `wildwander` exits `["stretch","wander"]` in py vs `["wander"]` in
  json; `wakeup`/`stretch` exit lists differ). Re-sync the fallback.
- The graph sketch omits back-edges that exist in `zones.json`
  (`rest→init`, `stretch→wakeup`, `wakeup→rest`).
- README claims the bus publishes `zone_changed`/`mode_changed`/`action_changed`/
  `pose_updated` — they are defined and subscribed (camera, display) but **never
  published**. The engine will publish them on change, making the docs true.

## Target architecture

### `rtr/state/machine.py` — the consolidated StateMachine

State (all previously split across machine + brain):

```
zone, mode, action, action_index, target, target_kind ("joint"|"linear"),
speed, flags, limitadjust, track_speed, tick_hz, random_every, action_every, tick
```

Methods:

- `request_zone(name, curjpos)` — as today, but each path hop is
  `[exitpose(edge), startpos(next)]` where `exitpose(edge)` =
  `zones.get(from).exitposes.get(to) or zones.get(from).exitpos`.
- `commit` (inside `update` on arrival): `zone = target zone`;
  `mode = zone.default_mode` **if declared**, else keep current mode;
  `clear_action()`; `speed = zone.speed`.
- `set_mode(mode)` — validates against `MODES`; if `mode == "action"` and no current
  action, start the first *enabled* action of the current zone (moved from
  `brain.set_mode`).
- `play_action` / `clear_action` / `step_action` — moved unchanged from the current
  machine.
- `set_target(pose, kind)` — for `set_joint_pose` / `set_linear_pose`.
- `random_wrist()` — randomises A4/A5 of the current target (hardware-clamped), then
  `set_mode("hold")` (moved from the brain's `RANDOM_WRIST` handler).
- `step(curjpos) -> Optional[List[float]]` — the per-tick driver (moved from
  `brain.step`): transition takes priority (steps floored to **hardware** limits only —
  the safezone floor must not apply to exit/start hand-off poses, guarded by
  `tests/test_transition.py`); otherwise dispatch the current mode's policy
  (`behavior.wander` / `random_pose` / `step_action` / `track` / hold); final result
  floored to the effective floor (safezone ∩ hardware); returns the target.
- `reset(curjpos)` — as today, plus `target = curjpos`, `target_kind = "joint"`.

### `rtr/state/behavior.py` — new module, pure behaviour policies

The motion math is currently private methods on `Brain` (`brain.py`), each coupled to
`self.machine` (for `current_zone`/`zones` → safezone) and to `self` (for
`limitadjust`, `track_speed`, `tick_hz`): `_wander(curjpos)`, `_random_pose()`,
`_track(curjpos)`, plus the clamp helpers `_clamp` / `_hardware_clamp` / `_effective`
/ `_safezone` over `fitlimits`. Move these into `behavior.py` as free functions that
take the `StateMachine` (or the resolved `limits`) explicitly, e.g.
`wander(machine, curjpos)`, `random_pose(machine)`, `track(machine, curjpos)`,
`clamp(machine, pose)`, `hardware_clamp(pose)`. The machine's `step()` dispatches to
these; the motion math is unchanged, only its home and access to shared state change.

### `rtr/brain/brain.py` — thin command interpreter

`Brain(machine)` — no state. `on_command(cmd, curjpos)` maps:
`GOTO_ZONE→request_zone`, `SET_MODE→set_mode`, `PLAY_ACTION→play_action`,
`CLEAR_ACTION→clear_action`, `SET_JOINT_POSE→set_target+set_mode("hold")`,
`SET_LINEAR_POSE→set_target("linear")`, `ADJUST_LIMIT→limitadjust[i]`,
`RANDOM_WRIST→random_wrist()`, `SET_FLAG→flags[name]`. Subclassing/overriding
`on_command` remains the seam where the LLM brain plugs in.

### `rtr/core/engine.py`

- `target = self.machine.step(curjpos)` (was `brain.step`); `target_kind` from the
  machine.
- Startup: `machine.reset(curjpos)` seeds the target (the `brain.target = ...` lines go).
- `_publish`: snapshot fields (zone/mode/action/speed/flags/target) read from the
  machine, not the brain.
- After each tick, compare (zone, mode, action) to the previous tick and publish
  `ZONE_CHANGED` / `MODE_CHANGED` / `ACTION_CHANGED` — makes the documented events real
  (camera + display already subscribe).
- `reload_zones`: unchanged logic, reading `machine` state.

### `rtr/state/zones.py` — data additions

- `Zone.default_mode` property → `data.get("mode")` (None when absent).
- `Zone.exitpose_for(target)` → `data.get("exitposes", {}).get(target)` or
  `data["exitpos"]`.
- `validate_state_data`: accept optional `mode` (must be in `MODES`) and optional
  `exitposes` (dict of zone-name → list of 6 numbers).
- **Re-sync** the built-in `ZONES` fallback to `zones.json` (exits lists, exitpos,
  speeds) so the offline fallback matches the file.

### `zones.json` — schema additions (backward compatible)

```json
"rest": {
  "enabled": true,
  "mode": "hold",                  // NEW, optional: default mode on entry
  "exitpos": [ ... 6 ... ],        // default hand-off pose (all edges)
  "exitposes": { "wakeup": [ ... ] }, // NEW, optional: per-edge override
  "exits": ["wakeup", "init"],
  ...
}
```

Seed default modes (editable later in the editor): `init: hold`, `rest: hold`,
`wakeup: action`, `stretch: action`, `wander: wander`, `wildwander: wander`.
No `exitposes` entries are seeded — they appear as the user draws/edits arrows.

### `editor.html` — new "flow" tab

Header: add a `flow` tab button (4th tab). Layout: `main#flowMain` = SVG canvas
(left, full height) + detail panel (right, ~380px, same width as the 3D panel).

- **Nodes** = zones: rounded rect, label = zone name (the "mood"), small text =
  default mode + action count. Disabled zones dimmed. The **live** zone (from the WS
  `state` frame the editor already receives) gets an accent ring + a `mode/action`
  label. Node positions: deterministic auto-layout on load (columns by BFS depth from
  `init`), draggable, **not persisted**.
- **Arrows** = exit edges: one per `(zone, exits[i])`, arrowhead, label "custom" when
  the edge has an `exitposes` override (else "shared").
- **Draw arrow:** mousedown on a node, drag, mouseup on another node → appends the
  target to the source zone's `exits` (no duplicates). A temporary arrow follows the
  pointer while dragging. Click (no drag) = select.
- **Select node** → detail panel shows the zone form: enabled, speed, **default mode
  select** (new), startpos, **exitpos** (the existing 6-axis grid), safezone, actions.
  This reuses the existing zone-form builders — refactor `buildZoneForm` to take a
  target container (currently hard-coded to `#zoneForm`), so both tabs share one
  implementation.
- **Select arrow** → detail panel shows the edge: `from → to`, a **per-edge exitpose**
  editor (6-axis grid + "use zone default" clear button), and **remove link**
  (deletes the exit + any `exitposes` entry).
- **Add / remove zone** buttons reuse `addZone` / `delZone`.
- **Save:** the flow tab edits mutate `TABLE` in place, so the existing `save()` →
  `POST /api/zones` path works as-is; extend client-side `validate()` with: `mode`
  (if present) ∈ `MODES`; `exitposes` (if present) is a dict of 6-number lists whose
  keys are existing zones.

### `README.md` updates

- State machine section: rewrite for the consolidated composite state
  (zone × mode × action), per-zone default mode, per-edge exitposes, `behavior.py`,
  Brain as thin interpreter.
- Zones section: the real graph from `zones.json` (incl. back-edges), point to
  `zones.json` as the source of truth.
- Events: document that the engine now publishes `zone_changed` / `mode_changed` /
  `action_changed` on change.
- Editor section: describe the flow tab (flowchart, arrow drawing, exitpose editing).
- Module table: add `state/behavior.py`.
- `zones.json` schema: document `mode` + `exitposes`.

## Ordered tasks (each ends with a `.kilo/task-handoff.md` entry per AGENTS.md)

1. **Analyse (done in this session):** architecture mapped, README staleness listed.
2. **Implement** `rtr/state/zones.py`: `Zone.default_mode`, `Zone.exitpose_for`,
   `validate_state_data` for `mode`/`exitposes`, re-sync `ZONES` fallback to
   `zones.json`.
3. **Implement** `rtr/state/behavior.py`: lift the policies + clamps from `brain.py`.
4. **Implement** `rtr/state/machine.py`: the consolidated `StateMachine`
   (composite state, `step`, commit-with-default-mode, `request_zone` with per-edge
   exitposes, `set_target`, `random_wrist`, flags).
5. **Test** (stage 1 gate): new `tests/test_state_machine.py` — mode set/override,
   play/clear action, request_zone path routing incl. per-edge exitpose selection,
   commit sets default mode + clears action, transition steps exact, hold keeps target,
   wander/random/track stay inside the effective floor, transition targets hardware-
   clamped only. Port `tests/test_transition.py` to `machine.step`. Run until green.
6. **Implement** stage 2 rewire: `brain.py` thin interpreter; `engine.py`
   (`machine.step`, snapshot from machine, publish change events, seed target in
   `reset`); `main.py` wiring (`Brain(machine)`).
7. **Test:** full suite (`pytest tests/` + direct runs) and a `python main.py --sim`
   smoke: boot, `goto_zone rest`, `set_mode wander`, `play_action breathe` over the WS.
8. **Implement** `zones.json`: add the `mode` defaults (content decision above).
9. **Implement** `editor.html`: the flow tab (SVG canvas, node/edge rendering, arrow
   drawing, detail panel reusing the zone form, edge form, live highlight, validation).
10. **Implement** `README.md`: the updates listed above.
11. **Test:** full suite + manual editor pass (serve over HTTP port 8766: draw an
    arrow, edit a per-edge exitpose, edit the zone's exitpos, save → verify
    `zones.json` + core hot-reload + flow redraws from the re-fetched table).
12. **Handoff:** final full handoff entry.

## Risks / edge cases

- **Transition clamping** is the one historical bug here (safezone-clamped exit pose
  → stalled transition). The hardware-only clamp for transition steps must survive the
  move into `machine.step`; `test_transition.py` (ported) guards it.
- **Backward compatibility:** missing `mode` / `exitposes` must behave exactly as today
  (keep current mode; use the single `exitpos`). Validation stays permissive.
- **Flow tab form reuse:** `buildZoneForm` currently hard-codes `#zoneForm` and global
  `selectedZone`; refactor to a container parameter before the flow tab reuses it, or
  the two tabs will fight over the same DOM.
- **Live state frames:** the editor already parses `state` frames (zone/mode/action);
  the flow highlight must update in place, not rebuild the SVG, or arrow-drag state
  is lost.
- **Dead events:** publishing `ZONE_CHANGED`/`MODE_CHANGED` makes the camera/display
  handlers fire for the first time — their existing handlers are already in place and
  benign (display sends a small `zone`/`mode` frame; clients ignore unknown types).

## Validation

- `pytest tests/` (and `python tests/test_transition.py`, `python tests/test_state_machine.py` directly — the suite runs without pytest).
- `python main.py --sim` smoke: boot + WS commands (`goto_zone`, `set_mode`,
  `play_action`) reach the expected state; snapshot shows zone/mode/action/target.
- Editor manual pass over `http://localhost:8766/editor.html` (flow tab interactions,
  save → `zones.json` diff → hot-reload).

## Open questions

None blocking. The seeded `mode` defaults in `zones.json` are content, not
architecture — they are editable in the new flow tab and can be tuned without code.
