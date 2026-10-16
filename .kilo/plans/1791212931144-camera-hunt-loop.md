# Camera Hunt Loop: wire detection → robot motion

## Goal
The robot "hunts" for people: **scan → detect → lock → move toward target → loop**.
Reuse the existing `look`/`track` actions + the image-Jacobian servo (already implemented);
add only the brain's **detect→trigger** and **lost→rescan** logic. No new behavior policy.

## Resolved decisions
1. **behavior vs action** — `behavior` is a per-action *attribute* (it drives the variable
   axes); it is **derived** state, not an independent axis. `machine.behavior`
   (machine.py:307-312) = the current action's `behavior` field, or `"hold"`. The
   state-frame `mode` field carries that derived value (for display/patches).
   **State = `(zone, action)`; do NOT add a separate "behavior" state axis.**
2. **Hunt structure** — brain-driven, reusing existing actions (user-confirmed). The hunt
   is a sequence of existing actions the brain triggers:
   - detect → `wakeup>look` (attention grab; the `look` servo moves A1 toward the target)
   - lost   → `wakeup>scan` (rescan; sine sweep of A1)
   - detect again → `wakeup>look` → loop
   - In this hunt structure events should trigger configurasble midi actions

## Current state (verified against code)
**Implemented**
- State machine owns `(zone, action)`; `behavior` derived (machine.py:307-312).
- Camera telemetry: RPI → `CAM_CANDIDATES`/`CAM_TRACK` → brain → `machine.camera_state`
  (brain.py:129-147). `CAM_TRACK` is sent **only** while the locked id is visible
  (pipeline.py:409).
- Visual servoing: `_apply_camera` (behavior.py:119) drives the variable axes from the
  offset via the image Jacobian (`dq = -gain · pinv2(J_img_var) · (dx,dy)`). This already
  gives "a different direction when the camera rotates" (the Jacobian is pose-derived).
- Lock: brain picks the best candidate (conf + centrality) → `camera.set_intent(lock_id)`
  (brain.py:94-127).

**Missing (the hunt loop)**
1. **Attention grab** — the brain sets the lock but never triggers a robot action. If the
   robot sits in a legacy zone (e.g. `rest>breathe`), the offset is ignored; nothing moves.
2. **Target-lost** — `CAM_TRACK` stops when the locked id is invisible, so
   `machine.camera_state` holds *stale* dx/dy forever. No "lost for N s → clear lock".
3. **Rescan** — no `scan` action is ever triggered.

## Design
### Hunt state machine (brain-owned)
States: `idle` → `tracking` → `scanning` → `tracking` …
- **`idle` → `tracking`**: `CAM_CANDIDATES` with a person → pick best candidate →
  `camera.set_intent(lock_id, mode="track")` → if `machine.behavior` not in
  (`track`/`focus`/`look`) and not `machine.transitioning` →
  `machine.trigger_action("wakeup", "look")`. Set hunt state = `tracking`.
- **`tracking` → `scanning`**: no `CAM_TRACK` for the locked id for `LOST_S` (default
  2.0 s) → `camera.set_intent(lock_id=None, mode="idle")` →
  `machine.trigger_action("wakeup", "scan")`. Set hunt state = `scanning`.
- **`scanning` → `tracking`**: `CAM_CANDIDATES` with a person → pick best → set lock →
  `machine.trigger_action("wakeup", "look")`. Set hunt state = `tracking`.

### Where the state lives
- The **brain** holds the hunt state (it is the decision-maker / command interpreter):
  `self._hunt_state` (`"idle"|"tracking"|"scanning"`), `self._hunt_lock_id`,
  `self._hunt_last_track_ts`, `self._hunt_lost_s = 2.0`.
- The brain gets a **per-tick hook** `tick(curjpos)` for the time-driven lost check.
  The engine calls `brain.tick(curjpos)` **after** the command-drain loop and **before**
  `machine.step` (engine.py:73-96), so a triggered transition is driven the same tick.

### zones.json
Add `wakeup>scan` (sibling of the existing `wakeup>look`, zones.json:249-266):
- `base_pose` = `[-85, -130, 137, -2, 0, 0]` (same as `wakeup>look`, so the
  look→scan handoff is a no-op transition),
- `variable_axes` = `[0]`, `behavior` = `"scan"`, `speed` = `20`, `loop` = `true`,
  `next` = `{}`.

## Tasks (ordered)
1. **`rtr/brain/brain.py`**
   - `__init__`: add `self._hunt_state = "idle"`, `self._hunt_lock_id = None`,
     `self._hunt_last_track_ts = 0.0`, `self._hunt_lost_s = 2.0`.
   - `_handle_cam_candidates`: after picking `best` and setting the lock, set
     `mode="track"` on the intent, record `_hunt_lock_id`, and do the attention grab:
     if `self.machine.behavior` not in `("track","focus","look")` and not
     `self.machine.transitioning` → `self.machine.trigger_action("wakeup","look")`;
     set `_hunt_state = "tracking"`.
   - `_handle_cam_track`: set `self._hunt_last_track_ts = time.time()` (keep the existing
     `machine.camera_state` stash).
   - Add `tick(self, curjpos)`: if `_hunt_state == "tracking"` and
     `time.time() - self._hunt_last_track_ts > self._hunt_lost_s` → lost:
     `camera.set_intent(lock_id=None, mode="idle")`,
     `machine.trigger_action("wakeup","scan")`, `_hunt_state = "scanning"`.
2. **`rtr/core/engine.py`** — in `run()`, call `self.brain.tick(curjpos)` after the
   `for cmd in self.bus.drain_commands():` loop and before `target = self.machine.step(...)`.
3. **`zones.json`** — add the `wakeup>scan` action (see Design).
4. **`rtr/camera/camera.py`** — no change needed (`set_intent` already takes `mode` +
   `lock_id`).

## Validation
- Run core `python main.py --sim --camera` and the mock
  `python -m rtr.camera.camera_mock` (random-walk candidates + track; venv `/tmp/kilo/rtrvenv`).
- Confirm in the state frame (`zone`/`action`/`mode`) and the `target` pose:
  1. On detection the robot transitions to `wakeup/look` (`mode=look`) and the A1 target
     moves in the sign of the camera offset (visual servo).
  2. When the mock stops sending `cam_track` for the locked id, the robot transitions to
     `wakeup/scan` (`mode=scan`) after ~2 s and A1 sweeps (sine).
  3. When the mock resumes the track, the robot transitions back to `wakeup/look`.
- Edge: a transition already in progress makes `trigger_action` return False (no-op);
  the hunt state is only set to `tracking` on a successful trigger.

## Risks / open questions
- The rest/wander → `wakeup` transition is a large move; during it the look servo is
  inactive (the transition takes priority in `machine.step`). Acceptable for now.
- `LOST_S` default 2.0 s — tune after live testing.
- `wakeup>scan` sweeps A1 across the full safezone `(-94, 122)`; if too aggressive,
  narrow the safezone or add a per-action scan-amplitude field (follow-up).
- No explicit `scanning → idle` (a robot with no person keeps scanning). Add an
  idle-after-N-s-of-scan if desired (follow-up).
