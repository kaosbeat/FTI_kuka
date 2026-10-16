# Autonomous Mode: Zone Groups as Autonomy Behaviors

## Goal

Add an autonomous mode to the robot core where the brain navigates zones/actions via existing flow `next` links only. Zone groups enable *behaviors*; the "autonomy level" is the free combination of enabled groups. Hunt and facefocus become **zone groups** (not separate special modes) — they are examples of the same pattern: a group of zones in which certain behaviors/actions are enabled when the group is on.

## Hard Constraint

The brain can ONLY follow existing action `next` links from the flow editor. No manual override, no arbitrary zone jumps.

## The Model (resolved)

- **Zone** — a positional decision region (`safezone` + `speed`). A place where the robot can easily decide on possible positions.
- **Zone group** — a named set of zones + the **behaviors** enabled in them. Groups **can overlap** (a zone may belong to several groups).
- **Behavior** — an existing per-action behavior field (`look`, `scan`, `face`, `track`, `focus`, `wander`, `random`, `hold`). A group enables a set of these.
- **Autonomy level** — the current **free combination** of enabled groups (each group carries its own `enabled` bool; there is no fixed level set). Navigation runs across the **union** of all enabled groups.

Hunt and facefocus are zone groups:

| Group | Zones | Behaviors | Driver | Notes |
|---|---|---|---|---|
| `hunt` | `wakeup` | `look`, `scan` | camera (person detect / lost) | fast / aggressive; detect→`look`, lost→`scan` |
| `facefocus` | `stretch` | `face` | camera (face detect) | `wink`/`inspect`/`call` cycled |

The camera/timer logic that drives hunt (lost detection, rescan) and facefocus (face cycling) **stays**, but it is **gated by the group's `enabled` bool**. When the group is off, its camera logic is inert and its zones are excluded from autonomous navigation.

### What changed vs. the previous plan draft

- The old separate `hunt` / `facefocus` top-level sections and the `performance` section are **folded into `zone_groups`** (the `hunt` and `facefocus` groups, with a `params` sub-object for their timing/trigger data).
- `zone_groups` is no longer `{groups, active}` (a single active group). It is a **dict of named groups**, each with `{zones, behaviors, enabled, [params]}`.
- Navigation is constrained to the **union of enabled groups** (not one active group).
- "Performance mode" is no longer a special section — it is simply a combination (the `perform` group on, `hunt`/`facefocus` off).

## Config Schema (brain.json)

```json
{
  "autonomy": {
    "enabled": true,
    "dwell_s": 30.0,
    "events": { "midi": true, "camera": true, "websocket": true }
  },

  "zone_groups": {
    "home": {
      "zones": ["init", "rest", "init_rest_bridge"],
      "behaviors": ["hold"],
      "enabled": true
    },
    "hunt": {
      "zones": ["wakeup"],
      "behaviors": ["look", "scan"],
      "enabled": true,
      "params": {
        "lost_s": 2.0,
        "attention_guard": ["track", "focus", "look", "face"],
        "detect": { "zone": "wakeup", "action": "look" },
        "scan":   { "zone": "wakeup", "action": "scan" }
      }
    },
    "facefocus": {
      "zones": ["stretch"],
      "behaviors": ["face"],
      "enabled": true,
      "params": {
        "actions": ["wink", "inspect", "call"],
        "action_s": 3.0,
        "face_lost_s": 2.5
      }
    },
    "perform": {
      "zones": ["watch", "perform", "fume"],
      "behaviors": ["track", "focus"],
      "enabled": false
    }
  }
}
```

- `autonomy.enabled` — master switch for autonomous navigation.
- `autonomy.dwell_s` — global default dwell time (seconds) before following a `next` link.
- `autonomy.events` — which external events can trigger a next link (MIDI note-on, camera detect/hunt lock, WebSocket `proceed`).
- `zone_groups.<name>` — each group:
  - `zones` — list of zone names (must exist in zones.json).
  - `behaviors` — list of behavior fields enabled in those zones.
  - `enabled` — whether this group is part of the current autonomy level.
  - `params` — **optional**; group-specific decision data (only `hunt` and `facefocus` use it today).
- Per-action `dwell_s` override remains in zones.json (optional, on any looping action) — see Task 4.

## Implementation Plan

### Task 1: Config schema + validation

**Files:** `rtr/brain/config.py`, `brain.json`

- Replace the top-level `hunt` / `facefocus` / `performance` sections in `DEFAULT_BRAIN_CONFIG` with `autonomy` + `zone_groups`.
- `zone_groups` is a dict of `{name: {zones, behaviors, enabled, [params]}}`.
- `validate_brain_config`:
  - Validate `autonomy` (`enabled` bool, `dwell_s` number, `events.{midi,camera,websocket}` bools).
  - Validate each group: `zones` is a list of non-empty strings, `behaviors` is a list of non-empty strings, `enabled` is a bool.
  - Validate `params` per group (optional): for `hunt` — `lost_s` number, `attention_guard` str-list, `detect`/`scan` zone-action objects; for `facefocus` — `actions` str-list, `action_s`/`face_lost_s` numbers. Only validate the keys present for that group's known param shape.
  - Fill defaults for missing keys from `DEFAULT_BRAIN_CONFIG`.
- `builtin_brain_config()` returns the new defaults (the schema above).
- Update `brain.json` to the new structure (the `hunt` and `facefocus` groups carry the current hunt/facefocus values as `params`).

### Task 2: Autonomy decision logic in brain.py

**Files:** `rtr/brain/brain.py`

Add to `Brain`:

1. **State tracking:**
   - `self._autonomy_action_start_ts: float` — timestamp when the current action started.
   - `self._autonomy_triggered: bool` — set when an event trigger fires (MIDI/camera/websocket); cleared after the next link is followed.
   - `self._enabled_groups: set[str]` — the set of group names with `enabled` true (recomputed on config reload).
   - `self._enabled_zones: set[str]` — the union of `zones` across enabled groups (the navigation constraint).

2. **`_zone_group(zone, action)` helper** — returns the group(s) that contain `zone` and enable the action's `behavior`; used to decide whether a zone/action is currently "active".

3. **`_autonomy_tick()` called from `tick()` after the hunt bookkeeping:**
   - If `autonomy.enabled` is false, return.
   - If the machine is transitioning, return.
   - If the current action is not a looping action, return (single-run actions already follow `next` via `_advance_after_action`).
   - Read dwell time: per-action `dwell_s` from zones.json if present, else global `autonomy.dwell_s`.
   - If `time.time() - self._autonomy_action_start_ts > dwell_s` or `self._autonomy_triggered` is true, call `_autonomy_follow_next()`.

4. **`_autonomy_follow_next()`:**
   - Read the current action's `next` via `zone.action_next(action)`.
   - If no `next`, return.
   - **Constraint:** the target zone must be in `self._enabled_zones` (the union of enabled groups). If not, walk the graph via `reverse_targets` to the nearest zone in `self._enabled_zones`; if none reachable, stay.
   - Call `machine.trigger_action(target_zone, target_action)`; reset the action timestamp and clear `_autonomy_triggered`.

5. **`_autonomy_reset_action_ts()`** — called when the current action changes (detected in `tick()` by comparing `machine.current_action` to the previous tick's value); sets `_autonomy_action_start_ts = time.time()`.

6. **Gate the existing hunt / facefocus camera logic by group `enabled`:**
   - `_handle_cam_candidates` (hunt detect) and the hunt lost/rescan bookkeeping in `tick()` run **only if the `hunt` group is enabled**.
   - `_handle_cam_face` (facefocus) and `_sync_camera` run **only if the `facefocus` group is enabled**.
   - The hunt/facefocus params are now read from the group's `params` sub-object (not the old top-level sections). When a group is disabled, its camera logic is inert and its zones are excluded from `self._enabled_zones`.

7. **Event trigger handlers:**
   - **MIDI:** `_handle_midi_trigger(note)` — sets `_autonomy_triggered = True` if `autonomy.events.midi` is true.
   - **Camera:** in `_handle_cam_candidates`, when a new lock is established, set `_autonomy_triggered = True` if `autonomy.events.camera` is true (only while the `hunt` group is enabled).
   - **WebSocket:** `Cmd.PROCEED` handler in `on_command` — sets `_autonomy_triggered = True` if `autonomy.events.websocket` is true.

### Task 3: New command + WebSocket handling

**Files:** `rtr/core/commands.py`, `rtr/io/websocket.py`

- Add `Cmd.PROCEED` to the `Cmd` enum (or a string constant if the enum is closed).
- Add `"proceed"` to `_BROWSER_CMDS` in `websocket.py`.
- Payload: `{}` (no payload; the brain decides what to do).
- The brain's `on_command` handles `Cmd.PROCEED` by setting `_autonomy_triggered = True`.

### Task 4: Per-action dwell_s in zones.json

**Files:** `rtr/state/zones.py`

- Add `action_dwell_s(action_name)` to the `Zone` accessor: returns the action's `dwell_s` field or `None`.
- The brain reads it via `self.machine.zones.get(zone).action_dwell_s(action)`.
- No `machine.py` changes (dwell logic lives in the brain).

### Task 5: MIDI note-on trigger

**Files:** `rtr/io/midi.py`

- On a note-on matching the configured trigger key, call `brain._handle_midi_trigger(note)`.
- Trigger key configured in the MIDI config (per-zone or global; see Open Question 1).

### Task 6: Editor UI — Brain tab (autonomy + zone groups)

**Files:** `editor.html`

In the brain tab form:

1. **Autonomy section:** `autonomy.enabled` checkbox, `autonomy.dwell_s` number input, `autonomy.events.{midi,camera,websocket}` checkboxes.
2. **Zone groups section:** for each group — name (read-only), `zones` (editable list), `behaviors` (editable list), `enabled` checkbox, and a `params` sub-form for `hunt`/`facefocus` (the timing/trigger fields). Add/remove group buttons.
3. **Update `loadBrain()` / `saveBrain()`** to parse/post the new `autonomy` + `zone_groups` structure (drop the old `hunt`/`facefocus`/`performance` forms).
4. **Update `renderBrainLive()`** to show the current autonomy state (enabled groups, dwell remaining, triggered).

### Task 7: Editor UI — Flow tab (zone group visualization)

**Files:** `editor.html`

- Draw a semi-transparent rounded rectangle around each group's zones (from node min/max x/y + padding); label with the group name.
- Highlight groups that are currently `enabled` (brighter/thicker); dim disabled groups.
- Because groups **overlap**, draw all enabled outlines; a zone node shows its group memberships in the detail panel (multi-select to add/remove).
- Update `flowRender()` and `applyFlowHighlight()`.

### Task 8: Engine wiring + state frame

**Files:** `rtr/core/engine.py`, `rtr/core/commands.py`

- Include in the snapshot: `autonomy_enabled` (bool), `autonomy_enabled_groups` (list of group names), `performance_mode` (bool, derived: `perform` enabled and `hunt`/`facefocus` disabled), `autonomy_dwell_remaining` (float).
- Add these to the `Snapshot` class; the display adapter and editor live view read them.

### Task 9: HTTP API endpoints

**Files:** `rtr/io/httpserver.py`

- The existing `/api/brain` GET/POST handles the new config (no new endpoint for config).
- No dedicated performance-mode endpoint (it is a combination of group `enabled` flags); the operator toggles groups via the brain tab.

## Data Flow

```
Engine tick (20 Hz)
  ├── drain commands (MIDI, WebSocket)
  │     ├── Cmd.PROCEED        → brain._autonomy_triggered = True
  │     ├── Cmd.CAM_CANDIDATES → brain._handle_cam_candidates (hunt group only; sets _autonomy_triggered on new lock)
  │     ├── Cmd.CAM_FACE       → brain._handle_cam_face (facefocus group only)
  │     └── MIDI note-on       → brain._handle_midi_trigger (sets _autonomy_triggered)
  ├── brain.tick()
  │     ├── hunt bookkeeping (lost/face-lost) — only while the hunt/facefocus group is enabled
  │     ├── _autonomy_tick()
  │     │     ├── dwell timer expired?  /  _autonomy_triggered?
  │     │     └── if either: _autonomy_follow_next()
  │     │           ├── read action's next field
  │     │           ├── constrain target to union of enabled groups
  │     │           ├── if blocked: nearest in-union zone via reverse_targets
  │     │           └── machine.trigger_action(zone, action)
  │     └── _autonomy_reset_action_ts() (if action changed)
  ├── machine.step()
  └── publish snapshot (includes autonomy state)
```

## Failure Modes & Edge Cases

1. **All zones in the enabled union are unreachable:** the robot stays in its current looping action. Logged.
2. **No group enabled:** the union is empty; the robot stays in its current action (no autonomous navigation). Logged.
3. **`hunt` group disabled while a person is locked:** the hunt camera logic is inert; the lock is not cleared by the hunt timer (it is held until the group is re-enabled or a manual command). The zone `wakeup` is excluded from the navigation union.
4. **`facefocus` group disabled while a face is visible:** facefocus is inert; the `face` behavior still runs if the robot is already in `stretch` on a `face` action, but no new facefocus entry is triggered.
5. **Dwell timer expires during a transition:** the brain skips the autonomy check (the machine is already transitioning).
6. **Action with no `next` field:** the robot stays in the looping action. No error.
7. **Per-action `dwell_s` negative or zero:** treated as 0 (immediate next on the next tick after the action starts).
8. **Group `zones` reference an unknown zone:** validation rejects the config at load (logged; the core keeps the last good config).

## Validation Plan

1. **Unit tests:**
   - `config.py`: `validate_brain_config` with the new `autonomy` + `zone_groups` (defaults, type errors, missing keys, unknown zone in a group, unknown behavior).
   - `brain.py`: `_autonomy_tick` with mocked time (dwell fires, event trigger fires, both).
   - `brain.py`: `_autonomy_follow_next` with the union constraint (in-union, out-of-union redirect, no reachable in-union).
   - `brain.py`: hunt group disabled (no camera redirects); facefocus group disabled (no facefocus entry).
2. **Integration test:**
   - Start the core with a test brain.json (autonomy on, dwell_s=1, events on, hunt+facefocus+home enabled).
   - Trigger a looping action; wait for the dwell timer; verify the robot follows the `next` link.
   - Disable the `hunt` group; verify the hunt camera logic goes inert and `wakeup` leaves the union.
   - Send a `proceed` WebSocket command; verify the robot follows the `next` link immediately.
3. **Editor test:**
   - Load the brain tab; verify the autonomy + zone-group forms render and save.
   - Toggle a group's `enabled`; verify the flow tab outline highlights/dims and the state frame updates.

## Open Questions

1. **MIDI trigger key granularity:** a single global note, or per-zone/per-action (the existing MIDI config has per-zone/per-action mappings)? Recommendation: per-zone with a global fallback.
2. **Zone group visualization style:** rounded rectangles vs. convex hulls vs. dashed outlines. Recommendation: semi-transparent rounded rectangles with the group name as a label, matching the existing SVG style.
3. **`params` shape generality:** should every group allow an arbitrary `params` object, or only `hunt`/`facefocus` have a known param shape? Recommendation: only `hunt` and `facefocus` have validated `params`; other groups may carry an unvalidated `params` passthrough for future use.
4. **Derived "performance mode":** the state frame's `performance_mode` is derived (`perform` on, `hunt`/`facefocus` off). Confirm this derived flag is enough, or whether an explicit operator "performance" preset is wanted.
