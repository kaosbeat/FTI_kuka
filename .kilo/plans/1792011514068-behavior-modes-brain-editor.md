# Plan: Behavior Modes — Per-Behavior Cards, Global Card, Candidate Settings, TUI Update

## Context

The RTR brain's 15 behavior modes are the motion policies in `rtr/state/behavior.py`
(`calm`, `bridge`, `locked`, `curious`, `intent`, `gentle`, `vigilant`, `restless`,
`spontaneous`, `soothing`, `agitated`, `aggressive`, `exhilarated`, `impatient`,
`drowsy`). Their tunable parameters are **hardcoded module constants** in `behavior.py`
and invisible to the operator. The brain tab in `editor.html` is a single flat card
(autonomy + zone groups + hunt + facefocus) with no per-behavior tuning and no link
to `camera_debug.html`.

**Goal:** make every behavior's parameters visible and tunable in the editor brain tab
(one card per behavior + a global card + a camera card), add the candidate-decision
settings (how the brain picks a lock target), link to `camera_debug.html`, and update
the remote TUI to show the current behavior and its parameters.

## Verified Current State (from source)

- `behavior.py` constants: `_DEFAULT_IMAGE_GAIN = 0.3`, `_LOOK_FACTOR = 0.5`
  (curious), `_FACE_FACTOR = 0.5` (gentle), `_DEFAULT_GAIN = 0.05` (legacy fallback
  only — unchanged). Per-behavior values in `_apply_behavior`:
  - `soothing` freq=0.5 amp_scale=0.1 · `agitated` 1.0/0.3 · `aggressive` 1.0/0.3 ·
    `exhilarated` 2.0/0.5 · `impatient` 1.0/0.3 · `drowsy` 0.3/0.2
  - `vigilant`: `_apply_scan` — `t = machine.tick * 0.1`, sweeps the **full safezone**
    (no amp_scale).
  - `restless`: `_apply_wander` — `adj = machine.limitadjust[axis]` with hardcoded
    fallback `5.0`.
  - `spontaneous`: random jumps, no params. `calm`/`bridge`: no-op.
- `brain.py:373`: candidate score `score = conf - dist * 0.1` — the `0.1`
  centrality weight is hardcoded; `min_conf` is already configurable.
- `config.py`: `DEFAULT_BRAIN_CONFIG` = autonomy / zone_groups / hunt / facefocus /
  cam_control; `validate_brain_config()` fills defaults, type-checks.
- `display.py:_frame()` has `self._machine`; `zones.py:action_behavior(name)`
  resolves the current action's behavior.
- `view.py`: page 2 `_brain_content(state)` shows zone/mode/action/moving/speed/heading;
  `RemoteState.apply_state` parses the state frame (state dict holds
  zone/mode/action/moving/speed only).
- `editor.html`: `#brainMain` → single `#brainForm`; `BRAIN_FALLBACK`,
  `normalizeBrain()`, `buildBrainForm()`, `saveBrain()`, `renderBrainLive()` exist.
  Save = POST `/api/brain` → hot-reload via `Brain.reload()`.

## Architecture Decisions

**D1 — Config location.** New top-level `behaviors` section in `brain.json` (same
pattern as `hunt`/`facefocus`). One entry per parameterized behavior:

```json
"behaviors": {
  "locked":      { "gain": 0.3 },
  "curious":     { "gain": 0.3, "look_factor": 0.5 },
  "intent":      { "gain": 0.3 },
  "gentle":      { "gain": 0.3, "face_factor": 0.5 },
  "vigilant":    { "freq": 1.0 },
  "restless":    { "wander_adj": 5.0 },
  "spontaneous": {},
  "soothing":    { "freq": 0.5, "amp_scale": 0.1 },
  "agitated":    { "freq": 1.0, "amp_scale": 0.3 },
  "aggressive":  { "freq": 1.0, "amp_scale": 0.3 },
  "exhilarated": { "freq": 2.0, "amp_scale": 0.5 },
  "impatient":   { "freq": 1.0, "amp_scale": 0.3 },
  "drowsy":      { "freq": 0.3, "amp_scale": 0.2 }
}
```

Values = current hardcoded defaults, so a missing section is a no-op (backward
compatible). `freq` is a multiplier on the base rate `machine.tick * 0.1`
(vigilant's current rate = 1.0). `wander_adj` is the fallback for `machine.limitadjust`
when no per-axis entry exists.

**D2 — Runtime path.** `StateMachine` gets `self.behavior_params: dict` (set by
`Brain.reload()` from `cfg["behaviors"]`). `behavior.py` reads via a helper
`_bp(machine, behavior, key, default)`. Priority: per-action `gain` in zones.json
(existing) > `behaviors.<name>.gain` > built-in default. `behavior.py` stays pure
(no config loading); it only reads a plain dict off the machine.

**D3 — Editor layout.** `#brainMain` becomes a CSS grid of cards
(`repeat(auto-fit, minmax(280px, 1fr))`, same pattern as `camera_debug.html`):
- **Global card**: autonomy (enabled/dwell_s/events), zone groups (per-group
  enabled/zones/params), cam_control (per-zone active/mode/lock).
- **13 behavior cards**: one per behavior with number inputs for its params
  (`gain`/`look_factor`/`face_factor`/`freq`/`amp_scale`/`wander_adj`); `calm`,
  `bridge`, `spontaneous` render as info-only cards (no params).
- **Candidates card**: how the brain decides on camera candidates — `hunt.params`
  `min_conf`, new `centrality_weight` (the `0.1` in brain.py:373),
  `attention_guard`, `detect`/`scan` targets.
- **Camera card**: link to `camera_debug.html` (same host), camera inject buttons,
  autonomy trigger buttons (moved from the old flat form).

**D4 — TUI scope (read-only).** The TUI's BRAIN page (page 2) gains the current
behavior name + its key params as state rows. **Tuning is out of scope for the TUI** —
the editor owns config editing + hot-reload; the handheld remote stays a viewer.
This matches the existing split (TUI sends commands; editor edits config).

## TODOs

### Task 1 — Config schema: `behaviors` + `centrality_weight`
Files: `rtr/brain/config.py`, `brain.json`
1.1. `DEFAULT_BEHAVIORS` dict in `config.py` (values per D1).
1.2. `_validate_behaviors_section(data)`: per-behavior numeric type-checks
    (gain/look_factor/face_factor/freq/amp_scale/wander_adj); unknown behavior
    names ignored; missing keys filled from defaults.
1.3. Add `centrality_weight` to `_HUNT_PARAMS` (default 0.1) and to
    `_validate_hunt_params()`.
1.4. `validate_brain_config()` returns `"behaviors"` key.
1.5. Update `brain.json` on disk (`behaviors` + `hunt.params.centrality_weight`).
Validate: `python -c "from rtr.brain.config import load_brain_config; c=load_brain_config('brain.json'); assert 'behaviors' in c and c['hunt']['params']['centrality_weight']==0.1"`

### Task 2 — Wire `behavior_params` through the machine
Files: `rtr/state/machine.py`, `rtr/brain/brain.py`
2.1. `self.behavior_params: dict = {}` in `StateMachine.__init__`.
2.2. `Brain.reload()` writes `self.machine.behavior_params = cfg.get("behaviors", {})`
    after a successful load.
Validate: sim start logs the params dict; missing section → empty dict (defaults).

### Task 3 — `behavior.py` reads params from the machine
Files: `rtr/state/behavior.py`
3.1. Helper `_bp(machine, behavior, key, default)`.
3.2. `_apply_behavior`: `image_gain = _bp(machine, behavior, "gain", 0.3)` (action
    `gain` override still wins, as today).
3.3. `_apply_camera`: curious factor → `_bp(machine, "curious", "look_factor", 0.5)`.
3.4. `_apply_face`: → `_bp(machine, "gentle", "face_factor", 0.5)`.
3.5. `_apply_behavior` oscillation calls: pass
    `_bp(machine, behavior, "freq", d)` / `_bp(machine, behavior, "amp_scale", d)`
    where `d` is the current per-behavior literal (no signature change to
    `_apply_oscillation` — it already receives `machine`).
3.6. `_apply_scan` (vigilant): `t = machine.tick * 0.1 * _bp(machine, "vigilant", "freq", 1.0)`.
3.7. `_apply_wander` (restless): fallback `5.0` →
    `_bp(machine, "restless", "wander_adj", 5.0)`.
3.8. Keep `_DEFAULT_GAIN` (0.05) untouched (legacy kinematics-fallback path).
Validate: sim run with defaults moves identically to before (values match old literals).

### Task 4 — Candidate scoring uses `centrality_weight`
Files: `rtr/brain/brain.py`
4.1. `_handle_cam_candidates`: `score = conf - dist * hw` where
    `hw = hunt.params.centrality_weight` (default 0.1).
Validate: unit check the scoring with a test config (score changes with the weight).

### Task 5 — State frame carries behavior + params
Files: `rtr/display/display.py`
5.1. `_frame()`: when `self._machine` has an active action, add
    `frame["behavior"] = zone.action_behavior(action)` and, if present in
    `machine.behavior_params`, `frame["behavior_params"] = bp[behavior]`.
Validate: WS client sees `behavior` / `behavior_params` in the state frame.

### Task 6 — Editor brain tab: per-behavior + global + candidates + camera cards
Files: `editor.html`
6.1. `#brainMain` markup: grid container `#brainCards` (keep `#brainLive`/`#brainInfo`
    below the grid).
6.2. CSS: `.brainGrid` (auto-fit grid) + `.brainCard` (panel/border/radius/padding,
    h2 = small-caps label) — match existing editor panel styling.
6.3. `BRAIN_FALLBACK` gains the `behaviors` section + `hunt.params.centrality_weight`
    (mirror `config.py`).
6.4. `normalizeBrain()` fills `behaviors` per D1 (keep custom behaviors; fill known
    defaults per behavior).
6.5. Rewrite `buildBrainForm()` → build one card per: Global (autonomy + zone groups
    + cam_control), Candidates (min_conf, centrality_weight, attention_guard,
    detect/scan), 13 behavior cards (number inputs; info-only for calm/bridge/
    spontaneous), Camera (link to `camera_debug.html` with current host, camera inject
    + autonomy trigger buttons — moved from the old flat form).
6.6. `saveBrain()` includes `behaviors` (read from per-behavior inputs) and
    `hunt.params.centrality_weight` in the POST payload.
6.7. `renderBrainLive(m)` shows the current behavior + its params from the state
    frame (D5 field).
Validate: browser — all cards render; edit a param → save → POST ok → brain
hot-reloads; camera_debug link opens.

### Task 7 — TUI BRAIN page shows behavior (read-only)
Files: `rtr/remote/view.py`, `rtr/remote/tui.py`
7.1. `RemoteState.apply_state`: capture `behavior` / `behavior_params` from the frame
    into the state dict (and `state_key`).
7.2. `_brain_content(state)`: add rows `BEHAVIOR: <name>` and `PARAMS: gain=0.3 ...`
    (compact; `—` when none).
7.3. `tui.py`: no renderer change needed (rows are shaped by view.py) — verify the
    page renders the new rows; SDL app inherits the same change via `build_rows`.
Validate: TUI BRAIN page shows the current behavior + params; HUNT/CAMERA pages
unchanged.

### Task 8 — Handoff log
Files: `.kilo/task-handoff.md`
8.1. Entry covering all of the above (status/changes/validation/next task).

## Risks and Caveats

1. **Behavior parity:** Defaults equal the current hardcoded values, so a
   `brain.json` without `behaviors` is a no-op. Verify with a sim diff before/after.
2. **`vigilant` has no amp_scale** (it sweeps the full safezone by design) — do not
   add one silently; if amplitude tuning is wanted later, that's a separate change.
3. **`centrality_weight`** is a new config key; default 0.1 = current behavior.
4. **Frame size:** `behavior_params` is a tiny dict (≤3 numbers); no perf concern.
5. **TUI tuning is out of scope** (D4) — the editor remains the only config editor.
6. **`spontaneous` card** is info-only (random jumps have no tunable params); keep it
   visible so the operator knows the mode exists.

## Validation Plan

1. `python -c` config load assertion (Task 1).
2. `python main.py --sim --no-midi --no-sound` — identical motion with defaults.
3. Browser: brain tab cards render, edit → save → hot-reload; camera_debug link works.
4. TUI: BRAIN page shows behavior + params.
5. WS: state frame includes `behavior` / `behavior_params`.

## Open Questions

- Per-behavior `gain` vs. per-action `gain` (zones.json): resolved by priority
  (action > behavior > default). No action.
- TUI tuning UI: out of scope by decision D4. If the user later wants tuning on the
  handheld, that's a separate plan (would add set-behavior-param commands + a new
  page or a BRAIN-page submenu).
