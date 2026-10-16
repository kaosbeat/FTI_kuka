# Zone-Centric MIDI Redesign (In/Out)

## Context

The prior per-zone In/Out task is complete and is the base for this work. This task
redesigns the MIDI In/Out editors in `editor.html` to be **zone-centric** and refactors
the backing modules:

- **Out**: lists zones; selecting one shows its actions with attributes (kind/loop/
  target/enabled) and lets the operator attach a MIDI command to each **action playback**
  and to **zone entry**. The now-redundant `modes` block is converted into actions on
  the `rest` zone.
- **In**: provides **relative navigation** controls (next action / next zone / back /
  retrigger) that mimic the `tui.py` flow-map navigation, so a MIDI sequencer can drive
  the robot, including re-triggering the same action for rhythmic motion.

Root: `/home/kaos/Documents/kaotec/FTI_kuka/realtimecontrol/rtr`.

## Resolved Decisions

1. **MIDI-in navigation = virtual cursor over `tui.build_items`.** `MidiInput` maintains
   a cursor over the current zone's `build_items()` rows (actions grouped by kind, then
   zones = declared exits + reverse exits). Commands:
   - **next_action** → advance the cursor to the next *action* row (wrapping) and
     activate it: `PLAY_ACTION` + `SET_MODE action` (mirrors `tui.activation_commands`).
   - **next_zone** → advance the cursor to the next *zone/exit* row (wrapping) and
     activate it: `GOTO_ZONE`.
   - **back** → activate the first *reverse-exit* row (`back=True`, the zone the robot
     could have come from): `GOTO_ZONE`.
   - **retrigger** → re-activate the currently-playing action row: `PLAY_ACTION` +
     `SET_MODE action`. (`machine.play_action` resets `action_index=0`, so the motion
     restarts — verified `machine.py:192-206`.)
2. **`modes` → `rest` actions; drop the mode-change trigger.** The `sound.json` `modes`
   block is removed; the 5 mode CCs are migrated into `sound.json` `actions.rest[mode]`
   (each `{start: <cc msg>, poses: []}`). The `Sound` adapter's mode-change branch is
   removed — mode CCs are only sent when the operator explicitly plays the corresponding
   `rest`-zone action. The new model triggers MIDI only on **zone-entry** and
   **action-playback** (+ per-pose steps).

## Layering Constraint

The pure nav helpers (`build_items`, `reverse_exits`, `_zone_exits`, `ACTION_KINDS`,
`_SECTION_ORDER`, `action_kind/loops/target/enabled`) live in `tui.py` (`rtr.remote`),
but `midi.py` is in `rtr.io`. Importing `remote` from `io` inverts the layering. **Move
the pure helpers to a new shared module `rtr/flow.py`** (sibling of `core`/`state`/`io`/
`remote`); both `tui.py` and `midi.py` import from it. `item_label` and
`activation_commands` may stay in `tui.py` (display/command shaping) or move too.

## Task List (each ends with a `.kilo/task-handoff.md` entry)

### 1. Analyse / research — DONE (this plan)

### 2. Create `rtr/flow.py` (shared pure navigation)
- Move from `tui.py`: `_zone_exits`, `reverse_exits`, `ACTION_KINDS`, `_SECTION_ORDER`,
  `action_kind`, `action_loops`, `action_target`, `action_enabled`, `build_items`.
  (`SECTION_LABELS`, `item_label`, `activation_commands` optional to move.)
- Update `tui.py` to import these from `..flow` (remove the local defs).
- No behavior change to the TUI. Run existing TUI tests; ruff-clean.

### 3. `sound.py` (MIDI-out): drop mode-change, migrate `modes`
- Remove the mode-change branch (step 2) from `_on_snapshot`. Keep zone-entry,
  action-start, and per-pose-step branches.
- In `validate_sound_data`: migrate a legacy `modes` block **in place** — for each
  `modes[m]`, add `actions["rest"][m] = {"start": <cc msg>, "poses": []}` if not
  already present, then drop the `modes` key. (Idempotent; mirrors the existing legacy
  bare-zone-entry normalization.)
- Update `builtin_sound_data()` to the new shape (no `modes`; the 5 mode CCs as
  `actions.rest` entries).
- Ruff-clean.

### 4. Migrate `sound.json`
- Remove the `modes` block; add the 5 mode CCs to `actions.rest`
  (`wander/random/action/track/hold`), each `{"start": <cc>, "poses": []}`.
  (The loader migration in task 3 also covers this; do the file edit so the editor
  saves the clean shape.)

### 5. `midi.py` (MIDI-in): nav cursor + dispatch
- Track `_current_action` from each snapshot (alongside `_current_zone`).
- Maintain a virtual cursor over `build_items(zones, current_zone)` (import from
  `rtr.flow`). Rebuild on zone change; reset/seed the cursor to the current action's
  row (or 0).
- Add **nav dispatch** in `_on_message` (after per-zone dispatch, before legacy
  fallback): match the `nav` keys (global, any port) and submit the cursor commands.
- Extend `start_learn(zone=None, action=None, nav=None)`; include `nav` in the
  `MIDI_LEARN` payload.
- Extend `validate_midi_data` to accept an optional top-level `nav` object
  (`{next_action, next_zone, back, retrigger}` → MIDI keys); validate each key.
- Ruff-clean.

### 6. Migrate `midi.json`
- Add a `nav` section (4 commands, initially `null` until learned).

### 7. `editor.html`: zone-centric Out + nav In
- **Out view** (zone-centric): a zone selector; for the selected zone show
  `out_port` + `command` (zone-entry MIDI) and the zone's **actions** (seeded from the
  `zones.json` table) each with read-only attributes (kind/loop/target/enabled) and
  editable `start`/`poses` (from `sound.json`). Drop the separate `modes` section.
- **In view**: add a **global nav** section (4 commands, each with a key editor + learn
  button) and keep the existing per-zone section (`in_port`, `command`, `actions`).
- Update `SOUND_FALLBACK` (remove `modes`; mode CCs under `actions.rest`).
- Update the merged save (no `modes`); wire nav learn (reuse `/api/midi/learn` with a
  `nav` target). Validate JS with `node --check`.

### 8. `httpserver.py`: learn nav target
- Extend `_post_midi_learn` to parse a `nav` field and pass it to `midi.start_learn`
  (nav learns write to `MIDI.nav[nav]`; zone learns unchanged). Ruff-clean.

### 9. `main.py`: verify wiring
- Confirm `MidiInput` still receives `zones_provider=lambda: machine.zones` (needed for
  the nav cursor) and the `Sound` wiring is unchanged. No edits expected (the 4
  pre-existing ruff errors here are unrelated).

### 10. Tests
- `tests/test_sound_out_ports.py`: extend for the `modes`→`rest` migration (no `modes`
  block; mode CCs present in `actions.rest`; no mode-change send).
- `tests/test_midi_in.py`: extend for the nav cursor (next_action / next_zone / back /
  retrigger), nav-key dispatch, and the `nav` validation.
- Run the full suite; ruff on changed files; `node --check` the JS.

### 11. Final handoff + validation

## Validation
- `pytest tests/` (all green).
- `ruff check` on `sound.py`, `midi.py`, `httpserver.py`, `flow.py` (clean).
- `node --check` on the `editor.html` JS block.
- Manual: load the editor, confirm the zone-centric Out and the nav In render; learn a
  nav key; confirm the cursor navigation drives `GOTO_ZONE`/`PLAY_ACTION`.

## Risks / Caveats
- **Retrigger sound** is best-effort: re-triggering the same action does not change
  `snap.action`, so the `start` message is not re-sent by the action-start branch; the
  per-pose branch re-fires on the target-pose reset. The robot motion restarts
  (verified); the sound follows via per-pose.
- The **virtual cursor** is stateful: it resets/reseeds on a zone change; `back` is
  deterministic (first reverse-exit row).
- The **`modes` migration** is in-place and idempotent (old files load; the editor saves
  the clean shape on first save).
- `editor.html` is large (~2598 lines); use the established temp-file + script
  workaround for large block replacements.

## Open Questions
- None — both key decisions (nav semantics, modes→rest trigger) are resolved.
