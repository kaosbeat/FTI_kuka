# Zone-Centric MIDI Redesign — Granular TODOs

Base plan: `.kilo/plans/1790605758485-zone-centric-midi-redesign.md`.
Root: `/home/kaos/Documents/kaotec/FTI_kuka/realtimecontrol/rtr`.

This plan re-splits the redesign into fine-grained, resumable TODOs. Every task
ends with a `.kilo/task-handoff.md` entry (newest at top). Tasks 2–9 are already
implemented and verified; they are listed as DONE for the resume map. The granular
work is in **10** (tests) and **11** (final validation + handoff).

## Environment / tooling notes (verified)
- `pytest` is not installed in the `kukart` pyenv → run test files standalone:
  `python tests/<file>.py` (each ends with `ALL TESTS PASSED`).
- Ruff binary: `~/.pyenv/versions/a3dtmsh/bin/ruff` (not `python -m ruff`).
- Test-file `E402` findings are pre-existing across the suite (the `sys.path.insert`
  bootstrap pattern) — do not treat them as regressions.
- `node --check` validates the extracted JS block of `editor.html`.

## Done (tasks 2–9 — resume map only)
- **T2** `rtr/flow.py`: pure nav helpers moved out of `tui.py`; `tui.py` imports from `..flow`. DONE.
- **T3** `rtr/sound/sound.py`: mode-change branch removed; `validate_sound_data` migrates legacy `modes` → `actions["rest"]` in place (idempotent), then drops `modes`; `builtin_sound_data()` has no `modes`. DONE (`sound.py:186-198`).
- **T4** `sound.json`: `modes` block removed; 5 mode CCs under `actions.rest`. DONE.
- **T5** `rtr/io/midi.py`: nav cursor (`_rebuild_nav`/`_ensure_nav`/`_nav_*`), global nav dispatch (`_dispatch_nav`, any port), `start_learn(nav=...)`, `validate_midi_data` nav validation. DONE (`midi.py:592-600`, `404`, `201-208`).
- **T6** `midi.json`: `nav` section added (4 keys, initially `null`). DONE.
- **T7** `editor.html`: zone-centric Out (zone selector + per-zone actions), global nav In section, `SOUND_FALLBACK` no `modes`, nav learn wired. DONE.
- **T8** `rtr/io/httpserver.py`: `_post_midi_learn` passes `nav` to `start_learn`. DONE.
- **T9** `main.py`: verified `MidiInput(zones_provider=...)` + `Sound` + `HttpServer` wiring; no edits. DONE.

## Task 10 — Tests (granular)

### 10.1 Sound tests — `tests/test_sound_out_ports.py`
- **10.1a** Add `test_validate_migrates_legacy_modes_into_rest`:
  - Feed a table with a non-empty `modes` block (e.g. `{"rest": {...cc...}, "wander": {...cc...}}`) plus an existing `actions["rest"]` entry.
  - Assert: `modes` key is dropped; `actions["rest"]` gains each mode as `{"start": <msg>, "poses": []}`; pre-existing `actions["rest"]` entries are preserved; migration is idempotent (call `validate_sound_data` twice → same result).
  - (Mirrors `sound.py:186-198`.)
- **10.1b** Add `test_mode_change_sends_nothing`:
  - A snapshot that changes only the mode (zone/action/pose unchanged) sends **no** CC — verifies the mode-change branch is gone.
- **10.1c** (Already present, keep) `test_mode_cc_sent_as_rest_action` — mode CC sent as a `rest` action on playback.
- **10.1d** Run `python tests/test_sound_out_ports.py` → `ALL TESTS PASSED`.

### 10.2 MIDI nav validation — `tests/test_midi_in.py`
- **10.2a** `test_validate_midi_data_accepts_nav`: a `nav` object with valid keys (`next_action`/`next_zone`/`back`/`retrigger` → MIDI key or `null`) is accepted.
- **10.2b** `test_validate_midi_data_rejects_unknown_nav_name`: a `nav` with an unknown command name → `ValueError`.
- **10.2c** `test_validate_midi_data_rejects_bad_nav_key`: a `nav` value that is not a valid MIDI key → `ValueError`.

### 10.3 MIDI nav cursor — `tests/test_midi_in.py`
Reuse the existing harness (disabled `MidiInput`, feed messages, read commands off `StateBus`).
- **10.3a** `test_nav_next_action_wraps_and_activates`: zone with ≥2 actions; fire `next_action`; assert `PLAY_ACTION` + `SET_MODE action` for the next action row, and that the cursor wraps after the last row.
- **10.3b** `test_nav_next_zone_gotos_zone`: fire `next_zone`; assert `GOTO_ZONE` for the next zone/exit row.
- **10.3c** `test_nav_back_gotos_first_reverse_exit`: fire `back`; assert `GOTO_ZONE` to the first reverse-exit (`back=True`) row.
- **10.3d** `test_nav_retrigger_replays_current_action`: set the current action, fire `retrigger`; assert `PLAY_ACTION` + `SET_MODE action` re-sent for the **same** action row.

### 10.4 MIDI nav dispatch — `tests/test_midi_in.py`
- **10.4a** `test_nav_dispatch_fires_on_any_port`: a nav key fired on a **non-default** port still dispatches (nav is global, not port-scoped) — contrast with per-zone dispatch.

### 10.5 MIDI nav learn — `tests/test_midi_in.py`
- **10.5a** `test_nav_learn_captured_on_default_port`: `start_learn(nav="next_action")`; capture a message on the default port; assert the key is stored in `nav` and the `MIDI_LEARN` payload carries `nav`.
- **10.5b** `test_nav_learn_not_dispatched`: while a nav learn is armed, the captured message is not dispatched as a zone command (learn consumes it).

### 10.6 Run + lint
- **10.6a** Run `python tests/test_midi_in.py` and `python tests/test_sound_out_ports.py` → both `ALL TESTS PASSED`.
- **10.6b** Ruff on the two test files (ignore pre-existing `E402`).
- **10.6c** Handoff entry for task 10.

## Task 11 — Final validation + handoff (granular)
- **11.1** Run the **full** suite: every file in `tests/` standalone → all `ALL TESTS PASSED`.
- **11.2** Ruff on the changed production files: `rtr/flow.py`, `rtr/sound/sound.py`, `rtr/io/midi.py`, `rtr/io/httpserver.py` → clean.
- **11.3** `node --check` on the `editor.html` JS block → OK.
- **11.4** Import smoke: `python -c "import main"` → OK (wires `MidiInput`/`Sound`/`HttpServer`).
- **11.5** Handoff entry for task 11.
- **11.6** Full handoff: summarize the whole redesign (flow.py move, sound modes→rest, midi nav cursor/dispatch/learn, editor, httpserver) and the final schema (`sound.json` no `modes`; `midi.json` `nav` section).

## Validation (final gate)
- All test files green (standalone run).
- Ruff clean on `flow.py`, `sound.py`, `midi.py`, `httpserver.py`.
- `node --check` OK on `editor.html`.
- `python -c "import main"` OK.

## Risks / caveats
- **Retrigger sound** is best-effort: re-triggering the same action does not change
  `snap.action`, so the `start` message is not re-sent by the action-start branch; the
  per-pose branch re-fires on the target-pose reset. Robot motion restarts (verified
  `machine.py:192-206`); sound follows via per-pose.
- **Virtual cursor** is stateful: reseeds on a zone change; `back` is deterministic
  (first reverse-exit row).
- **`modes` migration** is in-place and idempotent (old files load; the editor saves the
  clean shape on first save).
- `editor.html` is large (~2682 lines); use the established temp-file + script
  workaround for large block replacements.

## Open questions
- None — nav semantics and modes→rest trigger are resolved; only test coverage and
  final validation remain.
