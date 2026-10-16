# Zone-Scoped Actions Plan

**Goal:** Make actions fully per-zone (no shared global catalog), zone-scope the sound and hydra `actions` tables, and fix flow-editor zone deletion so the editor state updates immediately.

## Confirmed Design Decisions

1. **Actions are unique per zone.** The zones-tab "include / exclude" master catalog is removed. Each zone owns its own `actions` dict (already the case in `zones.json`); the UI edits them independently.
2. **Sound + hydra `actions` tables become a two-level map `zone → action → msg`.** The core matches by `(zone, action)` from the `Snapshot` (which already carries `zone` + `action`). Existing flat global entries are migrated one-time: each is copied into every zone that has that action; orphaned entries are dropped.
3. **Flow-editor zone deletion** updates the shared zone-editor state (`selectedZone`) immediately, instead of leaving the deleted zone visible until refresh.

## Current State (verified)

**Data files**
- `realtimecontrol/rtr/zones.json` — `zones.<name>.actions` is already per-zone: `{enabled, pos:[6-num poses], speed:[per-pose]}`.
- `realtimecontrol/rtr/sound.json` — `actions` is a **flat global** map `{action: {start, poses}}` (currently `breathe`, `look`, `sleep`).
- `realtimecontrol/rtr/patches.json` — `actions` is a **flat global** map `{action: code}` (currently `breathe`, `look`).

**Zone→action inventory** (`zones.json`): `init` {}, `rest` {breathe, look, yawn}, `wakeup` {breathe, look, roar}, `stretch` {look}, `wander` {breathe, look}, `wildwander` {breathe, look}.

**Core**
- `rtr/state/machine.py` — motion layer already plays actions within `current_zone` (`step_action`, `play_action` read `zone.actions()`); `Snapshot` carries `zone` + `action`. No motion change needed.
- `rtr/sound/sound.py` — `_on_snapshot` does a **global** lookup `actions.get(snap.action)` (start + per-pose step). Must become zone-scoped.
- `rtr/patches/patches.py` — `match_patch` does a **global** lookup `data["actions"].get(action)`. Must become zone-scoped (it already receives `zone`).
- `rtr/io/httpserver.py` — `validate_sound_data` / `validate_patches_data` (imported from the sound/patches modules) gate POSTs; must validate the new two-level shape.

**Editor** (`realtimecontrol/rtr/editor.html`, 1774 lines)
- Zones tab: `catalogActions()` (ordered union of all action names), `buildActionToggles()` (include/exclude checkboxes), `seedAction()` (seed a zone's action from another zone), `buildActionList()` (edit the zone's own actions), "+ add action" button.
- Sound tab: `loadSound()` seeds `SOUND.actions` from the global union; `buildSoundForm()` renders a flat action list; `addSoundAction()`; `validateSound()`; `saveSound()`.
- Patches tab: `loadPatches()` seeds `PATCHES.actions` from `catalogActions()`; `buildPatchesForm()` renders flat `actions.<name>` slots; `getPatchCode`/`setPatchCode`/`removePatchSlot` parse `scope.name` keys; `addPatchAction()`.
- Flow tab: `delZone()` (line 1689) deletes the zone; in `flowView` it clears `FLOW.sel` but does **not** update `selectedZone` or rebuild the zones tab. `buildFlowDetail()` renders the zone form for the selected node.

---

## Implementation Steps (ordered)

### Step 1 — Migrate the data files (one-time)

Rewrite the `actions` table in both files to the two-level `zone → action` shape, copying each existing global entry into every zone that has that action and dropping orphans.

`realtimecontrol/rtr/sound.json` — replace the top-level `actions` object with:
```json
"actions": {
  "rest":       { "breathe": {<old breathe>}, "look": {<old look>}, "yawn":    {"start":{"note":60,"velocity":60},"poses":[]} },
  "wakeup":     { "breathe": {<old breathe>}, "look": {<old look>}, "roar":    {"start":{"note":60,"velocity":60},"poses":[]} },
  "stretch":    { "look":    {<old look>} },
  "wander":     { "breathe": {<old breathe>}, "look": {<old look>} },
  "wildwander": { "breathe": {<old breathe>}, "look": {<old look>} },
  "init": {}
}
```
(`<old breathe>` / `<old look>` are the current `actions.breathe` / `actions.ack` objects verbatim. `sleep` is dropped — no zone has it. `yawn`/`roar` get the default `{start:{note:60,velocity:60},poses:[]}`.)

`realtimecontrol/rtr/patches.json` — replace the top-level `actions` object with:
```json
"actions": {
  "rest":       { "breathe": "<old breathe>", "look": "<old look>" },
  "wakeup":     { "breathe": "<old breathe>", "look": "<old look>" },
  "stretch":    { "look":    "<old look>" },
  "wander":     { "breathe": "<old breathe>", "look": "<old look>" },
  "wildwander": { "breathe": "<old breathe>", "look": "<old look>" },
  "init": {}
}
```
(`<old breathe>` / `<old look>` are the current `actions.breathe` / `actions.look` code strings verbatim.)

The `zones` / `modes` / `default` / `channel` tables are unchanged.

### Step 2 — Core: sound adapter (`rtr/sound/sound.py`)

- `_on_snapshot`: change the action lookup from `actions.get(snap.action)` to `actions.get(snap.zone, {}).get(snap.action)` for **both** the action-start branch and the per-pose-step branch.
- `validate_sound_data`: validate the new shape — `actions` maps a zone name to an object mapping an action name to `{start?, poses?}` (validate each inner message as today).
- `builtin_sound_data`: return `{"channel", "zones", "modes", "actions": {}}` (actions now zone-keyed; empty by default).
- `reload` log line: count zones of actions (optional).

### Step 3 — Core: patches adapter (`rtr/patches/patches.py`)

- `match_patch`: resolve the action entry zone-scoped — `data["actions"][zone][action]` — keeping precedence **action > mode > zone > default**. `match_patch` already receives `zone`.
- `validate_patches_data`: validate `actions` as `zone → action → code` (each inner value a non-empty string).
- `builtin_patches_data`: `{"default", "zones":{}, "modes":{}, "actions":{}}`.
- `reload` log line: count zones of actions (optional).

### Step 4 — Core: HTTP validation (`rtr/io/httpserver.py`)

No routing change. The POST handlers already call `validate_sound_data` / `validate_patches_data`, which now enforce the two-level shape. Confirm the import paths are unchanged.

### Step 5 — Editor: zones tab (remove the global catalog)

- Delete `catalogActions()`, `seedAction()`, and `buildActionToggles()`.
- Replace the `"actions (include / exclude)"` heading with `"actions"`; keep `buildActionList()` (edit the zone's own actions) and the "+ add action" button (which already writes into `z.actions`).
- `selectZone` already selects the first action of the zone; keep as-is.

### Step 6 — Editor: sound tab (zone-scoped)

- `loadSound()`: seed `SOUND.actions` **per zone** — for each zone in `TABLE.zones`, ensure `SOUND.actions[zone]` exists and seed each of that zone's actions with `{start:{note:60,velocity:60},poses:[]}` (only filling missing entries). Drop the global-union seeding.
- `buildSoundForm()`: render the `actions` section grouped by zone — for each zone, a sub-group of its actions (start message + per-pose rows), reusing `msgEditor`.
- `validateSound()`: validate the two-level shape (iterate zones → actions → start/poses).
- `addSoundAction()`: prompt for the zone (default `selectedZone`) + action, add under `SOUND.actions[zone]`.
- `saveSound()`: unchanged (POSTs the whole `SOUND`).

### Step 7 — Editor: patches tab (zone-scoped)

- `loadPatches()`: seed `PATCHES.actions` **per zone** — for each zone, ensure `PATCHES.actions[zone]` exists and seed each of that zone's actions with `PATCHES.default` (only filling missing entries). Drop the `catalogActions()` seeding.
- `buildPatchesForm()`: render the `actions` group as nested slots keyed `actions.<zone>.<action>` (one per zone per action), instead of flat `actions.<name>`.
- `getPatchCode` / `setPatchCode` / `removePatchSlot`: parse the three-part `actions.<zone>.<action>` key (special-case the `actions` scope; `zones`/`modes` keep the two-part `scope.name` form).
- `addPatchAction()`: prompt for the zone (default `selectedZone`) + action, add under `PATCHES.actions[zone]`.
- `savePatches()`: unchanged.

### Step 8 — Editor: flow-editor zone deletion fix

In `delZone()` (line 1689), move the zone-editor reassignment **before** the view-specific branch so it always runs:
```js
const names = Object.keys(TABLE.zones);
selectedZone = names[0];
buildZoneList();
selectZone(selectedZone);
```
Then keep the existing `flowView` branch (clear `FLOW.pos[nm]`, clear `FLOW.sel`, re-render flow + detail). Result: deleting a zone — from either view — re-points the shared zone editor to a live zone immediately, and the flow detail panel shows "click a node to edit its zone".

---

## Files to Change

| File | Change |
|------|--------|
| `realtimecontrol/rtr/sound.json` | `actions` → two-level `zone → action` (migrate globals, drop `sleep`) |
| `realtimecontrol/rtr/patches.json` | `actions` → two-level `zone → action` (migrate globals) |
| `realtimecontrol/rtr/rtr/sound/sound.py` | zone-scoped lookup, validation, built-in |
| `realtimecontrol/rtr/rtr/patches/patches.py` | zone-scoped `match_patch`, validation, built-in |
| `realtimecontrol/rtr/rtr/io/httpserver.py` | (verify only) POST validation via updated validators |
| `realtimecontrol/rtr/editor.html` | zones tab (drop catalog), sound + patches tabs (zone-scoped), `delZone` fix |

## Validation Plan

- **Unit (core):** run the existing tests in `realtimecontrol/rtr/tests/` (and add a `match_patch` / sound-dispatch case asserting a zone-scoped action resolves to the right zone's entry and falls through to mode/zone/default when absent).
- **Editor (manual):** open `editor.html` (served over the HTTP server):
  - Zones tab: no include/exclude row; each zone's actions edit independently; "+ add action" adds to the current zone.
  - Sound tab: actions grouped by zone; editing a zone's action start/pose updates that zone only.
  - Patches tab: `actions.<zone>.<action>` slots; preview renders the selected slot; remove works on the three-part key.
  - Flow tab: delete a node → the shared zone editor re-points to a live zone immediately; the detail panel prompts to click a node; clicking another node edits that zone.
- **Save/reload:** save sound + patches, restart the core, confirm hot-reload logs the new tables and the data files persist the two-level shape.
- **No regression:** confirm action playback, zone-entry notes, and mode CCs are unchanged.

## Risks / Caveats

- **Data migration is one-time and lossy by design:** orphaned global entries (`sleep`) are dropped. Keep a backup of `sound.json`/`patches.json` before editing.
- **Validation gate:** the new `validate_sound_data`/`validate_patches_data` reject the old flat shape, so the old files must be migrated *before* the new core code runs (otherwise the core degrades to the built-in fallback). Migrate the files and deploy the code together.
- **`getPatchCode`/`setPatchCode` key parsing:** the three-part `actions.<zone>.<action>` key must be handled distinctly from the two-part `zones.<name>` / `modes.<name>` keys; zone/action names must not contain `.` (they don't today).
- **Sound trigger on zone change:** zone commits clear the action (`_commit_zone` → `clear_action`), so `snap.action` goes to `None` between zone-scoped actions and the start message re-fires correctly; no extra tracking needed.
