# Task Handoff Log

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
