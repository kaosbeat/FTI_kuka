# Action Editor + Action-Flow Plan

**Goal:** Give every zone action a *kind* (`internal` / `entry` / `exit`) and a `loop` / single-run flag, model a per-action `target` (the next zone/action it hands off to, with a hand-off pose), and provide a dedicated **action editor** (reached from the zones tab or the flow tab) that edits an action's poses (bounded by the zone safezone), their order, its kind/loop/target, its hydra patch, and its per-pose MIDI sound. The **flow tab** renders actions *inside* the zone block: entry actions left-aligned (connector dot on the left edge), exit actions right-aligned (connector dot on the right edge), internal actions centered, and a loop marker on looping actions; exit→entry connections are drawn between zones. The state machine **executes** these semantics (loop vs single, exit transitions on completion, entry actions on arrival).

## Confirmed Design Decisions (from the user)

1. **Patch & sound storage stay separate.** `sound.json` and `patches.json` are unchanged on disk (hot-reload + independent validation intact). The new action editor is simply *where* you edit an action's patch + per-pose sound; it writes to those files. Zone/mode-level sound + patch keep their own sections/tabs.
2. **Single exclusive kind.** Each action has exactly one `kind`: `internal` | `entry` | `exit`, **plus** a separate `loop` boolean. (So a "looping exit" or "single-run internal" is possible.)
3. **Full execution now.** The state machine runs loop-vs-single, fires an exit action's transition on completion, and triggers entry actions on arrival — not just authoring + visualization.

## Action-Kind Semantics (interpretation — flagged for review)

The flow is a chain of actions where each *hand-off* goes **exit → entry**:

- **`exit`** action: the way *out* of a zone. Its `target` names another zone + the **entry** action there + a hand-off `pose` (the exit position). On a **single-run** completion the machine transitions to the target zone (leave at `target.pose`, arrive at the target zone's `startpos`) and activates the target entry action. If **loop**, it repeats in place until stopped.
- **`entry`** action: the way *in* to a zone. It is triggered on zone arrival (by an exit action's target, or a `goto_zone` that names it). It plays its poses; on a single-run completion it advances to its `target.action` (the next action, usually in the same zone) or the zone's default mode.
- **`internal`** action: "listen" — no external connectors. Triggered on demand (`play_action` command / MIDI). Loop or single. No automatic hand-off.

An action's `target` always means *"what comes next"*: an exit points at another zone's entry; an entry points at the next action in its own zone.

### Data model (per action, in `zones.json`)

```jsonc
"<action>": {
  "enabled": true,
  "kind": "internal",          // "internal" | "entry" | "exit"  (default "internal")
  "loop": true,                // true = wrap around (today's behaviour); false = single-run
  "pos": [[6], ...],           // as today
  "speed": [N],                // as today
  "target": {                  // optional; the next step (single-run hand-off)
    "zone": "wakeup",          // required for exit; defaults to the current zone for entry/internal
    "action": "walkin",        // the action to activate next (entry action, or next action)
    "pose": [a1,a2,a3,a4,a5,a6] // the hand-off pose (exit position / entry position)
  }
}
```

The fields are **additive and optional**; existing zones.json files are valid unchanged (every action defaults to `kind:"internal"`, `loop:true`, no `target`), so no data migration is required.

## Current State (verified)

**Data** — `zones.json` zones carry `enabled/mode/startpos/exitpos/safezone/exits/exitposes/speed` and `actions.{name: {enabled, pos, speed}}`. No kind/loop/target fields yet.

**Core**
- `rtr/state/zones.py` — `_validate_zone` validates per-action `pos`/`speed`/`enabled`; `Zone` exposes `actions()`, `action_enabled()`; `Zones` has `can_exit_to`, `find_path`, `exitpose_for`.
- `rtr/state/machine.py` — `step_action()` wraps around (always loops); `play_action`/`clear_action`; `set_mode("action")` starts the first enabled action; `request_zone` walks the `exits` graph; `_commit_zone` adopts speed + default mode and **clears** the action.
- `rtr/state/behavior.py` — `clamp`/`hardware_clamp`/`wander`/`random_pose`/`track`.
- `rtr/sound/sound.py` — `_on_snapshot` is already zone-scoped: `actions.get(snap.zone, {}).get(snap.action)` for start + per-pose steps.
- `rtr/patches/patches.py` — `match_patch` is already zone-scoped, precedence **action > mode > zone > default**.
- `rtr/io/httpserver.py` — `POST /api/zones|sound|patches` validate + atomic write + submit a `RELOAD_*` command.

**Editor** (`editor.html`, 1761 lines) — tabs `zones / sound / patches / flow`. Zones tab edits a zone's own actions inline (`buildActionList`, pose rows, "+ add action"). Flow tab renders zones as SVG nodes (`NODE_W=140, NODE_H=70`), exits as arrows; node positions are session-only. Sound + patches tabs are the separate submodules the user wants to fold *into* the action editor (as editing surfaces, not as data).

## Implementation Steps (ordered)

### Step 1 — Data model (`rtr/state/zones.py`)
- `_validate_zone`: accept optional per-action `kind` (one of internal/entry/exit), `loop` (bool), `target` (object with optional `zone` (must be an existing zone), `action` (string), `pose` (6 numbers)).
- `Zone` accessors: `action_kind(name)`, `action_loops(name)`, `action_target(name)`.
- Keep everything backward compatible (defaults: `kind="internal"`, `loop=True`, no target).

### Step 2 — State machine execution (`rtr/state/machine.py`)
- `step_action()`: honor `action_loops` — wrap when looping; on the last pose of a **single-run** action, do **not** wrap and instead call a new `_advance_after_action(action)`.
- `_advance_after_action(action)`: read `action_target`. If `target.zone` differs from the current zone → begin an **exit transition** (leave at `target.pose`, arrive at the target zone's `startpos`, then activate `target.action` on arrival — reuse/extend `request_zone` + `_commit_zone`). If same zone → activate `target.action` (entry/internal advance) or fall back to the zone's default mode.
- `_commit_zone(name, entry_action=None)`: when arriving with a named `entry_action`, play it instead of clearing (still adopt speed/default mode).
- New `Cmd`-free helper `request_exit(target)` that builds a transition from the current zone to `target.zone` carrying the entry action to play.
- Guard: an exit target that is not a declared `exits` path still uses `find_path` routing; if unreachable, log + no-op (matches `request_zone`).

### Step 3 — Editor: action editor (`editor.html`)
- New **action editor** reached by clicking an action (zones tab) or an action row (flow tab). It edits one action at a time:
  - **Poses**: add/edit/delete rows (6-joint), reorder (up/down), each bounded by the zone safezone (reuse the existing safezone warning in `saveZones`).
  - **Kind**: internal / entry / exit selector.
  - **Loop**: checkbox (loop vs single-run).
  - **Target**: zone + action + hand-off pose (shown/required by kind).
  - **Hydra patch**: the action's `patches.json` code (`actions.<zone>.<action>`), with the live preview.
  - **Sound**: the action's `sound.json` start + per-pose messages (reuse `msgEditor`).
- Save writes `zones.json` (kind/loop/target/poses) **and** `sound.json` + `patches.json` (the action's sound/patch) — three POSTs, same as the existing tabs.

### Step 4 — Flow tab: actions inside zone blocks
- Grow the zone node to contain its actions as rows: **entry** actions left-aligned with a connector dot on the node's left edge; **exit** actions right-aligned with a connector dot on the right edge; **internal** actions centered, no connectors; a **loop marker** on looping actions.
- Draw **exit→entry** edges between zones from the action `target` data (in addition to / replacing the zone-level `exits` arrows for the action-level flow).
- Clicking an action row opens the action editor (Step 3).

### Step 5 — Validation + tests
- Extend `tests/test_state_machine.py` for loop-vs-single + exit transition + entry-on-arrival.
- Extend `tests/test_zone_scoped_actions.py` for the new action schema (kind/loop/target round-trip through `validate_state_data`).
- Run the full suite (`python3 -m tests.test_*`) to confirm no regression.
- Update `realtimecontrol/rtr/README.md` (actions section: kinds, loop/single, target; flow-tab rendering).

## Files to Change

| File | Change |
|------|--------|
| `realtimecontrol/rtr/rtr/state/zones.py` | validate + accessors for `kind`/`loop`/`target` |
| `realtimecontrol/rtr/rtr/state/machine.py` | loop/single in `step_action`, `_advance_after_action`, exit transition, entry-on-arrival |
| `realtimecontrol/rtr/editor.html` | action editor (new), flow-tab action rendering + connectors |
| `realtimecontrol/rtr/zones.json` | (no forced change; optionally annotate an example action with kind/loop/target) |
| `realtimecontrol/rtr/README.md` | document action kinds/loop/target + flow rendering |
| `realtimecontrol/rtr/tests/test_state_machine.py` | loop/single + exit + entry tests |
| `realtimecontrol/rtr/tests/test_zone_scoped_actions.py` | new-schema validation tests |

## Validation Plan

- **Unit (core):** run all `tests/test_*.py` with `python3 -m` (pytest is not in the kukart venv). Add the new cases listed in Step 5.
- **Editor (manual):** open `editor.html` over HTTP; click an action in the zones tab and in the flow tab; edit poses (bounded), reorder, kind, loop, target, patch, sound; save; confirm `zones.json` + `sound.json` + `patches.json` persist and hot-reload.
- **Flow (manual):** confirm entry/exit/internal alignment, connector dots, loop markers, and exit→entry arrows.
- **No regression:** confirm wander/random/track/hold modes and the existing zone-level `exits` transitions are unchanged.

## Risks / Caveats

- **Kind semantics are an interpretation** (see above). If the intended entry/exit target direction differs, only Steps 2–4 need adjusting; the data model (Step 1) stays.
- **Exit routing:** an exit `target.zone` that is not on the `exits` graph is unreachable; `request_exit` reuses `find_path` and no-ops with a log (consistent with `request_zone`).
- **`sound.json`/`patches.json` unchanged on disk:** the action editor writes them via the existing POSTs; no adapter/validation change is required for this feature.
- **Flow node growth:** adding action rows changes node height; `NODE_H` and the auto-layout constants must account for the action count to avoid overlap.
