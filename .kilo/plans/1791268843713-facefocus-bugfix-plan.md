# Facefocus / hunt bugfix plan

Five reported bugs, ordered fix plan. Project root: `/home/kaos/Documents/kaotec/FTI_kuka/realtimecontrol/rtr` (package `rtr/`), venv `/tmp/kilo/rtrvenv`.

## Handoff verification (2026-10-08)

All six tasks are **implemented and committed** (not in `git status` modified list; folded into commit `2873149 autonomy work`). The codebase has since evolved beyond this plan (autonomy navigation, feral state, group configs, remote `sdlapp.py`/`view.py` with a feral RED warning).

| Task | Status | Verified at |
|------|--------|-------------|
| 1 — state frame exposes `hunt` + `servo` | DONE | `machine.py:56` (`servo_state`), `behavior.py:200-234` (`_servo` records every call incl. zero-offset; `build_target` resets to `{}` on None at :81/:87/:92), `brain.py:138` (`hunt_info`), `commands.py:97-98,115-118` (`Snapshot.hunt/servo`), `engine.py:179-180` (`_publish`), `display.py:125-130` (`_frame`) |
| 2 — camera follows face actions (manual/brain) | DONE | `brain.py:406-424` (`_sync_camera`), called at top of `tick()` (`brain.py:439`); uses `machine.effective_behavior` (`machine.py:343-352`, covers transition targets); `camera.py:57,73` (`clear_lock`); person-lost uses `clear_lock=True` (`brain.py:451`) |
| 3 — red lock box on debug page | DONE | `camera_debug.html:229,234` (locked `#e05d5d`), legend :68, CSS :47; lock-id fallback `t.track?.id ?? lastIntent?.lock_id ?? null` at :221 (canvas) and :322 (list) |
| 4 — motion/servo panel on debug page | DONE | `camera_debug.html:359-381` (`renderMotion`: behavior, error dx/dy, per-axis `dq_deg` with A-labels, target, joints, moving); section at :101; hunt state in status overlay :262,:270 |
| 5 — brain page shows facefocus + live "check out" | DONE | `editor.html:596,1444-1466` (BRAIN + facefocus), form :1508-1536, `saveBrain` round-trip :1546-1562, `renderBrainLive` :1581-1593 (shows "check out" for `facefocus`) called from state handler at :638 |
| 6 — validation harnesses | DONE (run NOT re-confirmed) | `hunt_test.py:120-144` (manual-face: intent close/analyze with no lock, `servo_state` behavior/axes/dq), `facefocus_test.py:117-131` (snapshot `hunt.state` + `servo`) |

**Only unverified item:** the test harnesses have not been re-run in this session — Python execution is blocked in plan mode. To confirm green, run in an implementation-capable session:
`/tmp/kilo/rtrvenv/bin/python /tmp/kilo/{hunt_test.py,facefocus_test.py,brain_config_test.py,brain_http_test.py}` and `hunt_integ.py` (expect EXIT 0).

## Root causes (verified against code)

1. **Brain page missing "check out" mode + stretch actions** — `editor.html` brain tab renders only `hunt.*` fields (`buildBrainForm`, editor.html:1420). `facefocus` config exists in `brain.json` and `/api/brain` already round-trips it (httpserver.py:194/213, config.py `validate_brain_config`), but the form never shows it. Live hunt state is not in the state frame (`display.py:_frame`) and not shown anywhere.
2. **Manual stretch does not switch camera wide→close** — camera intent is only changed by brain CAM handlers (`_handle_cam_candidates`, `_handle_cam_face`) and the hunt lost-checks (`brain.tick`). A manually triggered `stretch/wink|inspect|call` (behavior `"face"`) changes no camera intent → camera stays `wide/idle` → no `CAM_FACE` → `machine.face_state` stays empty → the `face` behaviour reads dx=dy=0 → no motion. This is also the root cause of bug 4's "it does not move".
3. **Lock box orange, not red** — `camera_debug.html` highlights the locked candidate only when `cam_telem.track.id` matches (`drawFrame` line 205, `renderCandidates` line 304). When the track payload is absent/stale (camera on wide, or the close tracker reassigned ids), no box is marked locked and all candidates draw orange `#ff9f40`. Locked color is green `#4fc08d`; user wants red.
4. **Direction vector not displayed; robot does not move** — the servo vector (per-axis joint deltas from the image-Jacobian math in `behavior.py:_servo`) is never exposed; the state frame has no servo data. Motion failure is the downstream of bug 2 (no face telemetry → zero error → no motion).
5. **Dynamic axis mode / brain commands in doubt** — same display surface as bug 4: need the locked id and the resulting vector on the camera debug page to prove the variable-axis behaviour runs.

Secondary defect found: `CameraController.set_intent` never applies `lock_id=None` (`camera.py:71` `if lock_id is not None`), so the brain's person-lost `set_intent(..., lock_id=None)` leaves a stale lock in the intent (and the debug page's intent panel shows a ghost lock).

## Settled design decisions

- **Bug 2 trigger**: whenever the robot's current *or transitioning* action has `behavior == "face"` **and** `facefocus.enabled` is true, the brain forces the camera to `close/analyze` (lock = current hunt lock id). Covers manual and brain-driven paths. Leaving a face action does not force wide — hunt logic keeps ownership (tracking→wide/track on face-lost, wide/idle on person-lost).
- **"Direction vector"**: the per-axis joint deltas (degrees) the behaviour computed this tick (`dq` from the image Jacobian, or the legacy fallback), plus the image error they were derived from (dx, dy) and the variable axes. Resulting target pose is already in the state frame (`target`).
- **Lock box red**: `#e05d5d` (existing red in the page). Candidates stay `#ff9f40`. Lock highlight id = `cam_telem.track.id` **falling back to** the camera intent `lock_id` (brain's current lock), so the box is marked even when the track payload lags.
- State-frame additions are additive optional fields; old clients ignore them.

## Ordered tasks

### Task 1 — Expose brain + servo state in the state frame (foundation for bugs 1/4/5)

- `rtr/state/machine.py`: add `self.servo_state: dict = {}` (next to `face_state`).
- `rtr/state/behavior.py`:
  - `_servo` writes `machine.servo_state = {"behavior": <name>, "dx": <scaled>, "dy": <scaled>, "dq_deg": [per var axis], "axes": list(var_axes)}` on every call (both the Jacobian path and the legacy fallback branch). Pass the behaviour name in from `_apply_camera` / `_apply_face`.
  - `build_target` sets `machine.servo_state = {}` whenever it returns None (no action / legacy action), so the field always reflects the last tick.
- `rtr/brain/brain.py`: add `hunt_info()` → `{"state": self._hunt_state, "lock_id": self._hunt_lock_id}`.
- `rtr/core/commands.py` `Snapshot`: add optional fields `hunt: Optional[dict] = None`, `servo: Optional[dict] = None`; include in `to_dict()` when present.
- `rtr/core/engine.py` `_publish`: populate `hunt=brain.hunt_info()`, `servo=dict(machine.servo_state)`.
- `rtr/display/display.py` `_frame`: add `hunt` / `servo` to the frame when present.

### Task 2 — Bug 2: camera follows face actions (manual or brain-driven)

- `rtr/brain/brain.py`: add `_sync_camera()`, called at the top of `tick()` (before the hunt-state early return, so it works while hunt is idle/scanning):
  - resolve the effective behaviour: current action's behaviour, or the transition target's `zone.action_behavior(...)` while `machine.transitioning`;
  - if behaviour is `"face"` and `facefocus.enabled`: `camera.set_intent(active="close", mode="analyze", lock_id=self._hunt_lock_id)` (only when active/mode differ, to avoid spamming CAM_CONTROL).
  - No other case forces the camera (hunt handlers keep ownership).
- `rtr/camera/camera.py` `set_intent`: add `clear_lock: bool = False`; when true, set `intent.lock_id = None` (fixes the stale-lock quirk).
- `rtr/brain/brain.py` person-lost path: `set_intent(active="wide", mode="idle", clear_lock=True)`.

### Task 3 — Bug 3: red lock square on the camera debug page

- `rtr/camera_debug.html`:
  - locked candidate box + label color → `#e05d5d` (canvas `drawFrame` + legend swatch); candidates stay `#ff9f40`.
  - lock id resolution: `lockedId = t.track?.id ?? lastIntent?.lock_id` (both `drawFrame` and `renderCandidates`), so the brain's lock highlights the box even when the track payload is momentarily absent.
  - track offset arrow stays green (it is the offset vector, distinct from the lock box).

### Task 4 — Bugs 4+5: motion/servo panel on the camera debug page

- `rtr/camera_debug.html`: new section "motion (servo)":
  - from state frame `servo`: behaviour name (proves the variable-axis mode is plugged in), image error dx/dy, resulting per-axis `dq_deg` vector with axis labels, `axes`;
  - from state frame: `target` vs `joints` (resulting target pose) and `moving`;
  - locked id row (track id / intent lock id) — the page already shows it in the intent + track panels; the canvas/legend from Task 3 makes it visible on the frame itself.
  - face panel: add `dx` / `dy` rows (payload already carries them; panel currently omits them).
  - state frame also carries `hunt` (state + lock id) for the editor (Task 5) — debug page may show `hunt.state` in the status overlay line.

### Task 5 — Bug 1: brain page shows facefocus config + live "check out" state

- `rtr/editor.html`:
  - `BRAIN` object + `BRAIN_FALLBACK`: add `facefocus` (`enabled`, `zone`, `actions`, `action_s`, `face_lost_s`); also fix the stale fallback `attention_guard` to `["track", "focus", "look", "face"]` (mirror config.py).
  - `loadBrain` / `buildBrainForm` / `saveBrain`: round-trip the facefocus fields (checkbox for `enabled`, text for `zone`, comma-separated text for `actions`, numbers for `action_s` / `face_lost_s`) — same pattern as the hunt fields.
  - Live state: on each `type === "state"` frame, render a live row in the brain tab (`brainInfo` area or a new `brainLive` div): `hunt.state` (show "check out" for `facefocus`), `hunt.lock_id`, and the camera intent (active/mode). Reuse the existing state-frame handler at editor.html:593.

### Task 6 — Validation

- Update/extend harnesses (in `/tmp/kilo/`):
  - `hunt_test.py`: add a manual-face case — rig with hunt idle, `machine.trigger_action("stretch", "wink")` (transition commits since ENTRY check is only for wakeup; run from a pose at the stretch entry pose `[85, -97, 17, -2, 0.3, 0]` or step until commit), then `brain.tick` → assert `camera.intent` is `close/analyze` with no candidates ever sent. Also assert `machine.servo_state` shape after a `CAM_FACE` with non-zero dx/dy while in `stretch/wink`.
  - `facefocus_test.py`: keep the 24 checks; add: `hunt` field on the snapshot (state frame) shows `facefocus` + lock id; `servo_state` non-empty while the face action runs with non-zero face offset.
  - `brain_config_test.py`, `brain_http_test.py`: unchanged (config shape unchanged) — must still pass.
- Run with `/tmp/kilo/rtrvenv/bin/python`: `brain_config_test.py` (21/21), `hunt_test.py` (PASS), `facefocus_test.py` (all checks), `brain_http_test.py` (11/11), `hunt_integ.py` (EXIT 0).
- `py_compile` every touched Python file.
- Extract inline JS of `camera_debug.html` and `editor.html` and `node --check` (existing pattern: `/tmp/kilo/camdbg.js`, `/tmp/kilo/editor_inline.js`).
- Manual sim check: core `--sim` + mock camera; open `camera_debug.html` → red lock box around the locked candidate, servo panel shows `behavior=face` + vector while stretch/wink runs; trigger `stretch/wink` from the editor with no one locked → intent panel flips to `close/analyze`.

## Risks / notes

- `_sync_camera` runs every tick; guard with an "only on change" check so `CAM_CONTROL` events don't spam.
- The close camera's tracker may assign ids different from the wide camera's lock (real hardware); the track payload can be null for 1–2 frames after a switch — the intent-lock fallback (Task 3) covers the highlight; the existing face-lost timer covers recovery.
- `Snapshot` gains optional fields — `to_dict` stays JSON-flat; P5live/client.html ignore unknown fields.
- Mock random-walk mode only sends `cam_face` when `mode == "analyze"` **and** a lock is set; after Task 2 the lock still comes from `_handle_cam_candidates`, so the flow is unchanged in sim.

## Out of scope

- No new face-detection model (the `FaceDetector` stub stays).
- No changes to the hunt heuristics, lock scoring, or the `brain.json` schema (facefocus fields already exist).
- No changes to `client.html`.
