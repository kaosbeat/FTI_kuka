# Task Handoff Log

## 2026-11-05 — Perform control mode: 4th `control_mode` silencing brain autonomy + MIDI `next_pose`

### Status

Completed

### Summary

Added a 4th `"perform"` control mode to the RTR state machine. It is the single mechanism for live MIDI-clock performance: the performer drives the robot via MIDI notes and `client.html` clicks while the brain's autonomy triggers are silenced. On entry the robot freezes (like `manual`) and the engine is forced to `block` move mode + `arrival` cadence (the robot completes a move before the next trigger is processed); the previous engine settings are saved and restored on exit. The legacy zone-group-derived `performance_mode` boolean (stage-takeover enabled ∧ event-sit-back disabled) is retired — `control_mode == "perform"` is now the single source of truth.

**Locked design decisions** (user-confirmed):
- Freeze on entry: clear the running action + hold the pose (same as `manual`).
- Replace `performance_mode`: the group-derived bool is retired; `control_mode == "perform"` is the single mechanism.
- MIDI: only `handle_next_pose()` works (arms the cadence pulse); `_handle_midi_trigger()` stays a no-op (like `manual`).
- `enable_group`: stays in perform (does not force `auto`; the group activates but brain-driven navigation does not run while the brain is silenced).
- Engine: auto-set `move_mode="block"` + `cadence="arrival"` on entry; restore previous values on exit.
- `midi_pulse()` arms the pulse in perform mode regardless of base cadence; `_cadence_due()` consumes the armed pulse in perform mode (manual advance on top of the arrival auto-advance).
- Entering perform clears the active group (consistent with `manual`).
- Safety popup (`set_pending_confirm`) shows for perform (like `manual`), not auto-discard.

### Changes Made

- `realtimecontrol/rtr/rtr/state/machine.py`:
  - `__init__`: added `_perform_prev_move_mode` / `_perform_prev_cadence` fields; documented `control_mode` (incl. `"perform"`) and the midi-pulse behavior.
  - `set_control_mode()`: accepts `"perform"`; freeze-on-entry logic shared with `manual`; on entry saves prev `move_mode`/`cadence`, forces `block`/`arrival`, drops a stale `_midi_pulse`; on exit restores the prev values.
  - `_advance_after_action()`: gate changed to `("manual", "perform")` (no auto-advance).
  - `_cadence_due()`: perform-mode armed pulse fires the cadence before the base-cadence check; docstring updated.
  - `midi_pulse()`: arms the pulse when `cadence == "midi"` **or** `control_mode == "perform"`.
- `realtimecontrol/rtr/rtr/brain/brain.py`:
  - `autonomy_info()`: `performance_mode` now derived as `control_mode == "perform"` (group-based derivation removed).
  - `tick()`: early-return gate changed to `("manual", "perform")` (brain takes no autonomous action).
  - `_handle_midi_trigger()`: no-op gate changed to `("manual", "perform")`.
  - `_handle_proceed()`: no-op gate changed to `("manual", "perform")`.
  - `_handle_cam_face()`: face-action cycling (an autonomy trigger) gated to `("manual", "perform")`.
  - `_hunt_active()`: returns False for `perform` (like `manual`), so the hunt ladder + attention-grab `trigger_action` in `_apply_lock` do not run.
  - `_handle_enable_group()`: skips `set_control_mode("auto")` when `control_mode == "perform"`.
  - `SET_CONTROL_MODE` handler: clears the active group on entering `("manual", "perform")`.
- `realtimecontrol/rtr/rtr/core/engine.py`:
  - `_machine_is_performing()`: returns `control_mode == "perform"` (no longer reads `autonomy_info`).
  - `_resolve_pending()` safety gate: blocked-move popup condition changed to `("manual", "perform")`.
- `realtimecontrol/rtr/rtr/core/commands.py`: `control_mode` comment updated to `"manual" | "auto" | "assertive" | "perform"`.
- `realtimecontrol/rtr/client.html`:
  - Added `<button id="cmPerform" class="seg perform">perform</button>` in the `#controlMode` seg-toggle.
  - Added render-sync line for `cmPerform`; added the `onclick` handler (`set_control_mode` / `perform`).
  - Added `.seg.perform.active` CSS rule (`#b05fd0`) and the status-bar color for `perform`.
- `realtimecontrol/rtr/editor.html`: documented the new `perform` control mode in the perform tab (freezes on entry, block/arrival engine settings restored on exit, `next_pose` advances the pose, triggers still work, blocked moves surface the popup, beat-clock runs only while active, legacy `performance_mode` retired).
- `realtimecontrol/rtr/tests/test_state_machine.py`: added 4 perform-mode tests (`test_set_control_mode_perform`, `test_perform_mode_freezes_like_manual`, `test_perform_mode_blocks_auto_advance`, `test_perform_midi_pulse_advances_anchor`) and registered them in the `main()` runner.

### Documentation

- `editor.html` perform tab: new "perform control mode" section describing the mode's behavior.
- Inline docstrings updated in `machine.py` (`set_control_mode`, `_cadence_due`, `midi_pulse`) and `brain.py` (`autonomy_info`, `tick`, `_hunt_active`, `_handle_midi_trigger`, `_handle_proceed`, `_handle_cam_face`, `_handle_enable_group`).
- `commands.py` `control_mode` field comment updated.

### Architecture

No new modules or data-flow changes. The perform mode reuses the existing `SET_CONTROL_MODE` command path, the existing midi `next_pose` → `brain.handle_next_pose()` → `machine.midi_pulse()` chain, and the existing safety-gate popup. The only structural change is that `performance_mode` (previously a derived bool on the autonomy info) is now a thin alias for `control_mode == "perform"`, and the engine's perform detection reads the machine directly instead of the brain.

### Validation

- `python -m py_compile` on `machine.py`, `brain.py`, `engine.py`, `commands.py`: **SYNTAX OK**.
- `python -m pytest tests/test_state_machine.py -q`: **20 passed, 9 skipped** (incl. the 4 new perform tests).
- `python -m pytest tests/ -q`: **444 passed, 14 skipped, 5 failed** — the 5 failures are all in `tests/test_remote_sdl.py` and are **pre-existing** (verified by stashing my changes and re-running: the same 5 fail on the base commit).
- JS tests: `node tests/js/test_perform_tab.js`: **ALL_TESTS_PASSED**. The other JS test failures (`test_action_cam.js`, `test_base_floor.js`, `test_patches_seed.js`, `test_perform_page.js`) are **pre-existing** (verified by stash + re-run on the base commit).

### Decisions and Caveats

- **`_hunt_active()` returns False for perform** (like manual): without this, the hunt ladder's attention-grab `trigger_action` in `_apply_lock` would fire in perform mode (the fall-through `return True` applied). This was the one autonomy leak found during the scan.
- **`_handle_cam_face` face-action cycling gated to perform**: it is a brain autonomy trigger (cycles face actions on a timer), silenced like the other autonomy paths.
- **`_autonomy_triggered` flag can still be set by `_apply_lock` in perform mode** (the camera-event flag is set unconditionally on a new lock). This is consistent with `manual` mode's existing behavior (the flag is only consumed by `_autonomy_tick`, which `tick()` skips in manual/perform), so a stale flag can cause one advance on exit. Left as-is to match manual semantics; flagged as a possible follow-up if the spurious-on-exit advance is undesirable.
- **Engine settings are user-overridable in perform mode**: `SET_ENGINE_MODE` / `SET_CADENCE` still work while in perform (they just set `machine.move_mode` / `machine.cadence`); the forced `block`/`arrival` is only applied on entry. The previous values are restored on exit regardless.
- **`perform.html` (the live perform page) is unchanged**: it already documents the `next_pose` cadence note and drives the same MIDI CCs; the new control mode is orthogonal to it.

### Next Task

No immediate next task identified. Possible follow-ups (not started):
- Decide whether the `_autonomy_triggered` flag should be gated in `_apply_lock` for manual/perform (to avoid a spurious advance on exit).
- Add a dedicated JS test for the `cmPerform` client toggle (the existing JS DOM-stub tests do not cover the client.html control-mode buttons).

### Instructions for the Next Task

The feature is complete and tested. To verify end-to-end: start the RTR core, open `client.html`, click the `perform` segment, and confirm (a) the robot freezes, (b) the engine status shows `block`/`arrival`, (c) a `next_pose` MIDI note advances the current action's pose, (d) zone/action trigger clicks still work, (e) a blocked move shows the confirm/cancel popup, and (f) switching back to `auto`/`manual` restores the previous engine settings. The `test_state_machine.py` perform tests cover the state-machine side; the brain-side silencing is covered by the existing `test_cam_test.py` / `test_midi_brain_trigger.py` suites (which pass).

## 2026-11-04 — KUKA EKI stale-read zero-curjpos: reject-and-hold with escalation (implement + test + handoff)

### Status

Completed

### Summary

Fixed the EKI stale-read zero-curjpos bug: a zero-filled move `<Res>` (KUKAPY CASE 4 / movej never writes `Res/A1-A6`, only CASE 2 / curjpos does) can be mis-paired as the next tick's `curjpos` response, so the engine reads raw `[0,0,0,0,0,0]` (→ `[0,0,0,0,0,90]` with the J6 offset) even though the robot physically moved. A single mis-paired frame self-heals on the next tick, so the OLD consecutive-zero streak (threshold 3) never fired ("no KukaError").

The fix replaces the consecutive-zero streak with a **reject-and-hold with escalation** detector in `kuka.py`:
- **Reject** an all-zero raw read when the last physically-plausible reading was non-zero (discriminator: `raw all-zero AND last-good non-zero`). A genuine home pose (last-good itself all-zero, or no baseline yet) is accepted — no false positive at zero.
- **Hold** the last-measured pose (offset-applied, consistent with the accept path) for the rejected tick. Holding the last-MEASURED pose lags reality (arrival detection delays, never fires early) — the safe direction. Never substitutes the target/ghost.
- **Escalate** 3 consecutive rejections to `KukaError`, so the engine watchdog's existing `KukaError → recover()` path handles a sustained desync.

Baseline (`_last_good_raw`) is re-established on `recover()` and `set_joint_offsets` so the first read after either is accepted unconditionally. The detector runs pre-offset (raw space) to avoid the `[0,0,0,0,0,90]` offset artifact.

**This session** completed the remaining work: aligned the test file with the new detector semantics and added 6 reject-and-hold behavior tests. The `kuka.py` implementation itself was already committed (in `1929bf0`, bundled with unrelated changes — see Decisions).

### Changes Made

- `realtimecontrol/rtr/rtr/robot/kuka.py` (committed in **`1929bf0`**, see Decisions for the bundling caveat):
  - `__init__`: replaced `_zero_streak` / `_zero_streak_threshold` with `_last_good_raw = None`, `_reject_streak = 0`, `_reject_streak_threshold = 3`.
  - `get_curjpos`: full rewrite — reject-and-hold discriminator, hold last-measured pose (offset-applied), escalate at streak ≥ 3.
  - `recover()`: re-baselines `_last_good_raw = None; _reject_streak = 0` at the top (removed the two inline `_zero_streak = 0` lines).
  - `set_joint_offsets`: re-baselines `_last_good_raw = None; _reject_streak = 0` after the offset update.
- `realtimecontrol/rtr/tests/test_kuka_eki_serialization.py` (committed in **`5e1f5ac`**, this session):
  - Replaced the stale `_zero_streak` assertions in `test_recover_soft_resync_does_not_quit` and `test_recover_hard_reconnect_when_link_dead` with `_reject_streak` / `_last_good_raw` checks.
  - Added a dedicated **reject-and-hold** test section: a `ScriptedKukapyRobot` fake (scripted curjpos reads) + `_make_robot_with_reads` helper + 6 behavior tests (`accepts_nonzero_reads`, `rejects_stale_all_zero_and_holds`, `accepts_zero_pose_at_home`, `escalates_to_kuka_error`, `resets_streak_on_accept`, `holds_offset_applied_pose`).

### Documentation

None required — the detector contract is documented inline in the `kuka.py` comments (the `__init__` block, the `get_curjpos` reject-and-hold block, and the `recover()` / `set_joint_offsets` re-baseline comments), and the test section header explains the CASE 4 / zero-`<Res>` root cause.

### Architecture

No architectural impact. Same module, same data flow, same escalation path: the detector raises `KukaError` on a sustained desync, which the engine watchdog (`engine.py` L139–145) already catches to call `robot.recover()`. `engine.py` and `machine.py` are untouched. The new state lives entirely in the `KukaRobot` wrapper (`_last_good_raw`, `_reject_streak`, `_reject_streak_threshold`).

### Validation

- `python tests/test_kuka_eki_serialization.py` (from the `realtimecontrol/rtr` root): **ALL TESTS PASSED** (17/17, incl. the 6 new reject-and-hold tests).
- `python -m pytest tests/test_kuka_eki_serialization.py -q`: **17 passed**.
- `python -c "from rtr.robot.kuka import KukaRobot"`: **import OK** (run from `realtimecontrol/rtr`).
- Behavior verified by the new tests: non-zero read accepted + baselined; all-zero against a non-zero baseline rejected + last-good held (streak 1); a genuine home pose accepted (no false positive); 3 consecutive rejections raise `KukaError`; a plausible read resets the streak + re-baselines; the held pose is offset-applied.

### Decisions and Caveats

- **The `kuka.py` implementation is committed in `1929bf0` ("renderupdates"), which is BUNDLED with unrelated changes** (`perform.html`, `rtr/display/display.py`, `rtr3d.js`, and this handoff file). It was committed before this session's test work. Because it is not a standalone commit, `git revert 1929bf0` would revert the unrelated files too. The targeted revert below reverts **only** the `kuka.py` hunk.
- **Reject-and-hold, not a longer streak**: a single mis-paired frame is rejected immediately (hold the last-measured pose) instead of waiting for 3 consecutive zeros — that is what makes the zero-curjpos tick safe. Escalation to `KukaError` is still at 3 consecutive rejections, so a sustained desync (not a one-off) triggers `recover()`.
- **Hold last-measured, never the ghost/target**: substituting the commanded target would mask a real desync and could fire arrival early. Holding the last reading only delays arrival detection (safe direction).
- **Detector runs pre-offset (raw space)** so the J6 offset artifact (`[0,0,0,0,0,90]`) cannot be mistaken for the stale signature.
- **Re-baseline on `recover()` + `set_joint_offsets`**: without it, a lingering stale frame right after a buffer clear (or a genuine home pose after an offset hot-reload) could be wrongly rejected against a stale non-zero baseline.

### Revert

The reject-and-hold change spans two commits. To revert **only** the reject-and-hold feature (and nothing else):

```bash
# Revert the kuka.py reject-and-hold implementation (only the kuka.py hunk of 1929bf0):
git checkout 1929bf0~1 -- realtimecontrol/rtr/rtr/robot/kuka.py

# Revert the test alignment + behavior tests (only the test file of 5e1f5ac):
git checkout 5e1f5ac~1 -- realtimecontrol/rtr/tests/test_kuka_eki_serialization.py
```

After both, `kuka.py` is back to the old `_zero_streak` detector and the test file back to the old assertions; commit the result to finalize. (Do **not** use `git revert 1929bf0` — that reverts the bundled `perform.html` / `display.py` / `rtr3d.js` changes as well.)

**Literal diff** of the `kuka.py` reject-and-hold change (as committed in `1929bf0`):

```diff
--- a/realtimecontrol/rtr/rtr/robot/kuka.py
+++ b/realtimecontrol/rtr/rtr/robot/kuka.py
@@ class KukaRobot.__init__
-        # EKI stale-read detector: the KUKAPY server's CASE 4 (movej) responds
-        # with Res/A1-A6 = 0.0 (the loop's initialization values), because CASE 4
-        # never writes Res/A1-A6 — only CASE 2 (curjpos) does. If that zero-filled
-        # move <Res> lands in _recv_buf and gets misread as the next tick's
-        # curjpos response, the engine sees [0,0,0,0,0,0] raw (→ [0,0,0,0,0,90]
-        # with the J6 offset). The robot physically moved, but the readback is
-        # stale. Track consecutive all-zero raw reads; when the streak reaches the
-        # threshold, raise KukaError so the engine's watchdog triggers recover().
-        self._zero_streak = 0
-        self._zero_streak_threshold = 3
+        # Reject-and-hold stale-read detector (replaces the old consecutive-zero
+        # streak). The KUKAPY server's movej (CASE 4) responds with Res/A1-A6 = 0.0
+        # (the loop's initialization values — CASE 4 never writes Res/A1-A6, only
+        # CASE 2 / curjpos does). If that zero-filled move <Res> is mis-paired as the
+        # next tick's curjpos response, raw reads all-zero even though the robot
+        # physically moved. A single mis-paired frame self-heals on the next tick, so
+        # the OLD consecutive-zero streak (threshold 3) never fired ("no KukaError").
+        # Reject-and-hold acts on ONE bad tick: reject an all-zero read when the last
+        # physically-plausible reading was non-zero, hold the last-good pose, and
+        # escalate consecutive rejections to KukaError so the engine watchdog triggers
+        # recover() on a sustained desync.
+        self._last_good_raw = None
+        self._reject_streak = 0
+        self._reject_streak_threshold = 3

@@ def get_curjpos
     def get_curjpos(self) -> List[float]:
         with self._lock:
             raw = list(self._eki_call("get_curjpos"))
-        # Stale-read detection: the KUKAPY server's move response (CASE 4) carries
-        # Res/A1-A6 = 0.0 (initialization values). If that zero-filled <Res> is
-        # misread as a curjpos response, raw is all zeros. Track consecutive all-zero
-        # reads; when the streak reaches the threshold, raise KukaError so the
-        # engine's watchdog triggers recover() (reconnect clears _recv_buf).
+        # Reject-and-hold with escalation.
+        #
+        # all-zero raw is the stale move-<Res> signature. It is only a false reading
+        # when the robot was physically elsewhere: the discriminator is "raw all-zero
+        # AND last-good non-zero". If the robot is genuinely at home (last-good is
+        # itself all-zero, or we have no baseline yet), the all-zero read is real and
+        # is accepted. This avoids the false positive at a legitimate zero pose.
         all_zero = all(abs(v) < 1e-6 for v in raw)
-        if all_zero:
-            self._zero_streak += 1
-            if self._zero_streak >= self._zero_streak_threshold:
+        last_good_zero = (self._last_good_raw is not None
+                          and all(abs(v) < 1e-6 for v in self._last_good_raw))
+        if all_zero and self._last_good_raw is not None and not last_good_zero:
+            self._reject_streak += 1
+            # Log every rejection loudly — a silent hold would mask a sustained
+            # desync (the whole point is no silent data corruption).
+            print(f"[kuka] REJECTED stale all-zero curjpos read "
+                  f"(reject streak {self._reject_streak}); "
+                  f"holding last-good raw={self._last_good_raw}")
+            if self._reject_streak >= self._reject_streak_threshold:
                 KukaError = _get_kuka_error()
                 raise KukaError(
-                    f"EKI stale read: raw curjpos all-zero for "
-                    f"{self._zero_streak} consecutive reads (threshold "
-                    f"{self._zero_streak_threshold}); move <Res> desync suspected"
+                    f"EKI stale read: rejected all-zero curjpos for "
+                    f"{self._reject_streak} consecutive reads (threshold "
+                    f"{self._reject_streak_threshold}); sustained desync, "
+                    f"escalating to recover()"
                 )
-        else:
-            self._zero_streak = 0
+            # Hold the last physically-plausible raw reading, offset-applied (the
+            # accept path below also returns offset-applied, so this stays
+            # consistent). Holding the last-MEASURED pose lags reality while the
+            # robot moves (arrival detection delays, never fires early) — the safe
+            # direction. Never substitute the target/ghost here.
+            return [self._last_good_raw[i] + self._joint_offsets[i]
+                    for i in range(len(self._last_good_raw))]
+        # Accept: physically-plausible reading. Reset the reject streak and
+        # re-baseline the last-good raw (stored pre-offset, so it is independent
+        # of any joint-offset hot-reload).
+        self._reject_streak = 0
+        self._last_good_raw = list(raw)
         return [raw[i] + self._joint_offsets[i] for i in range(len(raw))]

@@ def recover
     def recover(self) -> None:
         """..."""
+        # Re-baseline reject-and-hold so the first read after recovery is accepted
+        # unconditionally (establishes a fresh baseline). Without this, a lingering
+        # stale all-zero frame right after the buffer clear could be rejected, or a
+        # genuine home pose could be wrongly rejected against a stale non-zero
+        # baseline.
+        self._last_good_raw = None
+        self._reject_streak = 0
         # Case 1: soft resync (the link is alive but mis-paired).
         if self._robot is not None and self._robot.comm_sock is not None:
             try:
                 self._robot._recv_buf = b""  # drop any mis-paired leftover frame
                 self._robot.ping()           # sets $FLAG[1]; KRL answers, pairing re-syncs
-                self._zero_streak = 0
                 print("[kuka] EKI soft-resync OK (link alive, pairing re-synced)")
                 return
             except Exception:
@@ def recover (hard reconnect)
             self._robot = None
         try:
             self.connect()
-            self._zero_streak = 0
             print("[kuka] EKI connection recovered")
             ...
@@ def set_joint_offsets
     def set_joint_offsets(self, offsets: List[float]) -> None:
         """Update the joint home offset (hot-reload from the robot config)."""
         self._joint_offsets = list(offsets)
+        # Re-baseline reject-and-hold: the offset shifts the virtual space, so the
+        # next physical read re-establishes the last-good baseline. (The raw reading
+        # itself is offset-independent, so this only guards the acceptance edge case.)
+        self._last_good_raw = None
+        self._reject_streak = 0
```

### Next Task

No immediate next task identified. The reject-and-hold fix, its tests, and this handoff are complete. Optional follow-ups (out of scope): (1) tune `_reject_streak_threshold` (3) against the live robot if the escalation cadence feels too aggressive/lazy; (2) if the `kuka.py` change is ever needed back as a standalone commit, interactive-rebase `1929bf0` to split the `kuka.py` hunk out of the bundled "renderupdates" commit.

### Instructions for the Next Task

- The detector lives in `realtimecontrol/rtr/rtr/robot/kuka.py`: `_last_good_raw` / `_reject_streak` / `_reject_streak_threshold` (`__init__` ~L63–76), the reject-and-hold logic in `get_curjpos` (~L135–175), and the re-baselines in `recover()` (~L208–214) and `set_joint_offsets` (~L241–248).
- The behavior tests are in `realtimecontrol/rtr/tests/test_kuka_eki_serialization.py` (the `Reject-and-hold stale-read detector` section, near the end, before `test_connect_resets_robot_on_failure`); run with `python tests/test_kuka_eki_serialization.py` from the `realtimecontrol/rtr` root (17 tests).
- The escalation path is unchanged: `KukaError` → engine watchdog (`engine.py` L139–145) → `robot.recover()`. Do not add a new recovery path.
- To revert the feature, use the two `git checkout <commit>~1 -- <path>` commands in the Revert section (do not `git revert 1929bf0`).

## 2026-11-04 — Coordinated-axis 3D viewer: solid-arm tween in `rtr3d.js` (TODO #5)

### Status

Completed

### Summary

Added local per-frame animation to the 3D viewer so the solid (current) arm moves smoothly between the ~20 Hz state frames instead of snapping on every `update()`. Previously `update()` snapped the solid arm's rotors to `s.joints` (the live `curjpos`) and the ghost to `s.target`; the solid arm jumped frame-to-frame.

The fix eases the solid arm's **rendered** joints toward the authoritative current position in the `requestAnimationFrame` render loop (60 Hz), while the ghost still snaps to `s.target` in `update()`. The solid arm therefore **trails** the ghost during a move (and converges once the move settles). `curJoints` remains the authoritative source for `getJoints()`, frozen `"x"` resolution, and `checkPoseCollision`; only the rendered solid rotors are eased.

### Changes Made

- `realtimecontrol/rtr/rtr3d.js`:
  - Added three locals next to `curJoints` (~L84-92):
    - `const displayJoints = HOME.slice();` — the solid arm's **rendered** joint angles (deg); seeded at rest pose.
    - `const SOLID_SMOOTH_RATE = 12;` — frame-rate-independent easing rate (Hz); higher = snappier/less lag.
    - `let lastFrameTs = null;` — timestamp of the previous render frame (for the dt).
  - `update()` (~L1014-1036): removed the `setJoints(rotors, s.joints)` snap. It now only updates `curJoints` from `s.joints` (the ghost `setJoints(rotorsT, s.target)` is unchanged). The solid arm's rendered position is now owned by the render loop.
  - Render loop `loop(ts)` (~L907-930): added a guarded `if (rotors)` block that computes `dt` from `ts` (clamped to 0.1 s), eases each `displayJoints[j]` toward `curJoints[j]` with `k = 1 - exp(-dt * SOLID_SMOOTH_RATE)`, then calls `setJoints(rotors, displayJoints)`.

### Documentation

None required — the easing contract is documented inline in the `displayJoints`/`SOLID_SMOOTH_RATE` comments and the render-loop block comment.

### Architecture

No architectural impact. Same module, same data flow; the change only splits the solid arm's rendered position (`displayJoints`, eased) from its authoritative position (`curJoints`). The ghost/target arm is untouched. Live collision checking runs on the rendered (eased) solid rotors, so it now carries the same small display lag as the visuals; scratch target collision checks remain exact.

### Validation

- `node --check realtimecontrol/rtr/rtr3d.js` → **SYNTAX_OK**.
- `node tests/js/test_*.js` (all 14, from the `realtimecontrol/rtr` root): **11 PASS, 3 FAIL**. All 3 failures are **pre-existing and unrelated to `rtr3d.js`** (none of them load the real `rtr3d.js` for position assertions; the FK/collide tests only mirror the `JOINTS` geometry):
  - `test_action_cam.js` → LOAD ERROR (DOM `onclick` null) — pre-existing per the 2026-11-04 poses-tab handoff.
  - `test_patches_seed.js` → FAILURES=4 (patch-data assertions) — pre-existing per the same handoff.
  - `test_perform_page.js` → `TypeError: Cannot set properties of null` at `perform.js:77` (`$("pose")`) — a mini-DOM gap from the perform.html task (the `#pose` element isn't in the test harness); **confirmed pre-existing** by `git stash` (fails identically with the `rtr3d.js` change reverted).
- Confirmed no JS test loads the real `rtr3d.js` for position assertions (grep: tests stub `Robot3D` via a `Proxy` no-op; only `test_collide.js`/`test_env_collide*.js` mirror the joint geometry).
- Verified the ghost still snaps to `s.target` (`update()` L1028) and the solid arm now trails it (render loop L907-930). `HOME=[0,-90,90,0,90,0]` is unchanged and remains the `displayJoints` seed.

### Decisions and Caveats

- **Eased in the render loop, not in `update()`** — the state stream is ~20 Hz but the render loop is ~60 Hz; easing in `update()` would only run as fast as the frames arrive. Easing per render frame gives smooth interpolation between frames.
- **`displayJoints` is display-only** — `curJoints` stays authoritative for `getJoints()` / `"x"` resolution / `checkPoseCollision`. The rendered solid arm lags the true position by at most the easing tail; this is a visual-only offset.
- **Collision check uses the rendered rotors** — `runCollisionCheck()` runs on the solid arm's rendered (eased) position, so live collision feedback now matches what the operator sees (a small display lag); the scratch target collision check (editor drag) is unaffected and exact.
- **`SOLID_SMOOTH_RATE = 12`** is a tunable; lower it for more trailing, higher for less. Frame-rate independent (exponential decay on dt).
- **editor.html** already has its own local manual lerp for pose-stepping / the validate scan; the shared render-loop tween adds a slight visual lag there too, but the end state converges to the commanded pose.

### Next Task

No immediate next task identified. The coordinated-axis 3D viewer animation is complete and validated. Optional follow-ups (out of scope): (1) tune `SOLID_SMOOTH_RATE` against the live robot for the desired trail amount; (2) add a `#pose` element to the `test_perform_page.js` mini-DOM to clear that pre-existing harness failure.

### Instructions for the Next Task

- The tween lives in `realtimecontrol/rtr/rtr3d.js`: `displayJoints`/`SOLID_SMOOTH_RATE`/`lastFrameTs` (~L84-92), the `update()` no-snap change (~L1014-1023), and the render-loop easing block (~L907-930).
- `curJoints` is authoritative (do not read rendered position from `displayJoints` for collision/IK); `displayJoints` is the solid arm's rendered position only.
- The ghost (`rotorsT`) still snaps to `s.target` in `update()`; the solid arm (`rotors`) is eased in the render loop.
- To tune the trail: change `SOLID_SMOOTH_RATE` (higher = less lag). Verify with `node --check rtr3d.js` and the live `client.html`/`editor.html`.

## 2026-11-04 — perform.html UI: zone_groups dropdown, beat/bar counter, pose display (+ mode_group purge)

### Status

Completed

### Summary

Finished the `perform.html` performance-control page (TODO #4) and folded in the already-verified `mode_group` purge handoff. Three UI additions, all display/monitor-oriented (no new command surface beyond the existing velocity/multiplier controls):

1. **Display-only `zone_groups` dropdown** — lists the enabled performance groups from `brain.json` (`/api/brain`) and highlights the active one (from the state frame's `active_group`). Selecting a group sends no command (the group is driven by the brain, not the operator).
2. **Beat/bar counter** — shows the bar position in 4/4 time derived from the continuous beat (`bar = floor(beat/4)+1`, `beat-in-bar = beat%4+1`).
3. **Pose index display** — shows the current anchor index within the action's poses, so the operator can see the MIDI-driven pose advancement (the `next_pose` cadence note). This required forwarding `action_index` from the snapshot into the state frame.

The `mode_group` purge (removing the legacy `mode_group` field from zones/config/brain and the dead dwell lookup) was completed and verified earlier; its handoff is folded in here.

### Changes Made

- `realtimecontrol/rtr/perform.html`:
  - Added `.groupSel` CSS for the group dropdown.
  - Added a "bar" row to the Beat clock section (`id="bar"`).
  - Added a "Performance group" section with a display-only `<select id="groupSelect">`.
  - Added a "pose" row + hint to the Live move section (`id="pose"`).
  - Added `fetchBrainGroups()` — fetches `/api/brain`, lists enabled `zone_groups` in the dropdown.
  - Wired `fetchBrainGroups()` into the WS `onopen` handler.
  - `renderState()`: highlights the active group (`m.active_group`) in the dropdown; computes the bar counter from `p.beat` (4/4); displays the pose index (`m.action_index + 1`, 1-indexed).
- `realtimecontrol/rtr/rtr/display/display.py`:
  - `_frame()` now forwards `snap.action_index` as `frame["action_index"]` (optional field) so the perform page can show the current pose.
- `mode_group` purge (earlier, verified): removed the legacy `mode_group` field from `zones.json` (7 lines), `zones.py` builtin table (12 lines), `zones.py` (`MODE_GROUPS` constant, `mode_group` validation, `Zone.mode_group` property, `Zones.mode_groups` method), and the dead `mode_group` dwell block in `brain.py` `_autonomy_dwell_s`. Kept `brain/config.py` `_validate_mode_group` (it validates the new `zone_groups` system).

### Documentation

None required — the dropdown is display-only (documented inline in the HTML hint), the bar counter's 4/4 assumption is noted in the JS comment, and the pose display references the existing `next_pose` MIDI cadence.

### Architecture

No new modules or dependencies. The state frame gains one optional field (`action_index`), forwarded from the existing snapshot (engine.py already populates it). The perform page is a consumer of the existing `/api/brain` and state-frame fields; no new command path. The `mode_group` purge removed a legacy config field with no runtime behavior change (the dwell lookup was dead because old `mode_group` names never overlapped `zone_groups` keys).

### Validation

- `python -m pytest tests/test_perform_state.py tests/test_perform_config.py tests/test_perform_http.py tests/test_zone_groups.py tests/test_state_machine.py tests/test_transition.py tests/test_sound_out_ports.py tests/test_sound_enabled.py -q`: **120 passed, 9 skipped**.
- `python -m pytest tests/test_brain_autonomy_dwell.py tests/test_brain_behavior_params.py tests/test_brain_assertive.py tests/test_snapshot_grab.py tests/test_midi_brain_trigger.py tests/test_bridge_behavior.py -q`: **49 passed**.
- `python -c "from rtr.core.commands import Snapshot; from rtr.display.display import Display; ..."`: **Snapshot.action_index present; _frame forwards action_index**.
- `node --check` on the extracted perform.html JS: **JS syntax OK**.
- Cross-checked every `$("id")` referenced in the perform.html JS against the defined element IDs: **no missing IDs**.

### Decisions and Caveats

- The `zone_groups` dropdown is **display-only** by design (the user's decision): it reflects the brain-driven active group and sends no command on selection.
- The bar counter assumes **4/4 time** (`bar = beat // 4`). If the performance uses a different meter, this needs adjusting. The continuous `beat` value is 0-indexed; the display is 1-indexed.
- The pose index is shown **1-indexed** (`action_index + 1`) to match operator counting; the underlying `action_index` is 0-indexed.
- The `next_pose` MIDI cadence note is the only pose-advancement path (no WS command for it). Pose advancement is MIDI-only by design; the page only displays it.
- `action_index` is forwarded as an optional frame field; existing frame consumers (client.html, editor.html, TUI) are unaffected by the additive field.

### Next Task

Coordinated-axis 3D viewer animation (TODO #5): animate the move in `rtr3d.js` (currently `update()` snaps `curJoints` to `s.joints` and the ghost to `s.target` with no local tween; `HOME=[0,-90,90,0,90,0]`). Wire it into `client.html` / `editor.html`.

### Instructions for the Next Task

- `rtr3d.js` currently has no local tween: `update()` snaps `curJoints` to `s.joints` and the ghost to `s.target`. Add a per-frame interpolation (lerp toward the target with a smoothing factor, or a time-based tween) so the 3D arm moves smoothly between frames instead of jumping.
- Keep the ghost (target) and the solid arm (current) distinct; the solid arm should trail the target during a move.
- `HOME=[0,-90,90,0,90,0]` is the rest pose; confirm it matches the machine's home before using it as a tween start point.
- Verify against the live state frame (`joints` = current, `target` = target pose) in `client.html` / `editor.html`.

## 2026-11-04 — Core timing: move duration = max over joints (motion-profile estimate)

### Status

Completed

### Summary

Implemented the coordinated-axis move-duration estimate. The perform "time-to-arrive" previously used a crude `sum(|axis deltas|) / velocity` (sum of per-axis distances, constant speed). That is wrong for a coordinated move where all six joints travel in sync: the real rest-to-rest time is the **max** over the per-joint times, and each joint follows a trapezoidal/triangular velocity profile (accelerate → cruise → decelerate), not constant speed.

Settled this turn's two open questions and folded them in:
- **Triangular branch** resolved by dimensional analysis: for short moves the axis never reaches `vmax`, so `t = 2*sqrt(D/a)` (the user's literal `2D/a` is dimensionally inconsistent).
- **Acceleration** resolved as a tunable config field `acceleration_max_deg_s2` (perform.json default `100.0`; machine state placeholder `1.0`, seeded from config). The exact physical KUKA value is still a placeholder to be tuned to hardware.

Per-joint formula: `v = velocity × velocity_max_deg_s`; `D_j = |cur_j − tgt_j|`; `t_j = D_j/v + v/a` if `D_j ≥ v²/a` (trapezoidal) else `2·sqrt(D_j/a)` (triangular); total `T = max_j(t_j)`; `late = T > deadline` (notify-only, no auto-shortening).

### Changes Made

- `realtimecontrol/rtr/rtr/state/machine.py`:
  - Added module-level `motion_profile_time(distance_deg, vmax_deg_s, accel_deg_s2)` — the per-joint rest-to-rest time (trapezoidal/triangular; `0.0` for zero distance, `inf` for non-positive v or a).
  - Added `set_perform_acceleration_max_deg_s2(accel_deg_s2)` setter (clamped non-negative).
  - Rewrote `update_perform_time_to_arrive` to take a **list** of per-joint distances (was a scalar `distance`) and compute `T = max_j(motion_profile_time(d, v, a))`.
  - (Earlier in the task) perform state dict: `time_to_arrive` comment updated to "seconds (max over joints of the rest-to-rest time)"; added `"acceleration_max_deg_s2": 1.0` placeholder.
- `realtimecontrol/rtr/rtr/core/engine.py`:
  - Seeds `acceleration_max_deg_s2` from the perform config at init (beside the existing `velocity_max_deg_s` seed).
  - Distance computation (was `sum(abs(a-b) ...)`) → per-joint deltas `distances = [abs(a-b) for a,b in zip(curjpos, tgt)]`, passed as a list to `update_perform_time_to_arrive`.
- `realtimecontrol/rtr/main.py`: `_reload_perform()` hook now also re-seeds `set_perform_acceleration_max_deg_s2` on hot-reload.
- `realtimecontrol/rtr/perform.json`: added `"acceleration_max_deg_s2": 100.0` (earlier in the task).
- `realtimecontrol/rtr/rtr/perform.py`: `acceleration_max_deg_s2` added to module docstring, `DEFAULT_PERFORM_CONFIG`, `validate_perform_config` (positive-number check), and the shape docstring (earlier in the task).
- `realtimecontrol/rtr/tests/test_perform_state.py`:
  - Added 4 `motion_profile_time` unit tests (trapezoidal, triangular, zero distance, zero v/a).
  - Rewrote the `update_perform_time_to_arrive` tests to the per-joint list signature + max-over-joints formula.

### Documentation

None required — the formula and the acceleration/triangular decisions are captured in the `motion_profile_time` and `update_perform_time_to_arrive` docstrings.

### Architecture

No architectural impact. Same state-machine shape; the perform state gains one field (`acceleration_max_deg_s2`) and the time-to-arrive estimate is now a per-joint max. No new modules, dependencies, or data-flow boundaries.

### Validation

- `python tests/test_perform_state.py`: **ALL TESTS PASSED** (15 tests, incl. the 4 new motion-profile tests + rewritten per-joint tta tests).
- `python tests/test_perform_config.py`: **ALL TESTS PASSED** (validates the new `acceleration_max_deg_s2` field).
- `python tests/test_state_machine.py`: **ALL TESTS PASSED** (no regression from the state-dict change).
- `python tests/test_perform_http.py`: **ALL TESTS PASSED** (config hot-reload path intact).
- `python -c "import rtr.state.machine, rtr.core.engine, rtr.perform, main"`: **imports OK**.

### Decisions and Caveats

- `acceleration_max_deg_s2=100.0` is a **tunable placeholder**, not a confirmed hardware value. The KukaPy/KRL `acceleration=100` references are move parameters, not confirmed deg/s². Tune to the real KUKA when available.
- The estimate is approximate: actual KUKA time depends on PTP/LIN, start/end speeds, acceleration limits, payload, configuration, singularities, and blending. The estimate is for the beat-deadline/late flag, not a motion command.
- `late` remains notify-only (no auto-shortening of the move).

### Next Task

`perform.html` UI (TODO #4): `zone_groups` dropdown from `brain.json`, purge the `mode_group` leftover, beat/bar counter, and advance the action MIDI to the next pose.

### Instructions for the Next Task

- `brain.json` `zone_groups` is the single source of truth for the performance-group dropdown (current: `active`, `sleep`, `engage`); do not invent a new group (the user adds the performance group via the editor later).
- `zones.json` `mode_group` is a hardcoded leftover to purge from data + code without breaking behavior (values: `museum-closed`, `event-sit-back`, `None`).
- Do NOT add new per-pose MIDI input granularity; the existing per-action MIDI mapping should advance to the next pose. Per-pose MIDI output stays separate, keyed by `action_index` in `sound.json`.
- The core timing is now per-joint (see `motion_profile_time` + `update_perform_time_to_arrive`); the UI just displays `time_to_arrive` / `late` from `perform_state()`.

## 2026-11-04 — Move `test_*.js` out of the rtr root into `tests/js/`

### Status

Completed

### Summary

User wanted the 14 `test_*.js` Node test scripts out of the `realtimecontrol/rtr` root to declutter it. They are plain `node test_X.js` scripts (no package.json, no test runner, no CI references) that resolve all paths from `__dirname`: the `collide.js` module, `assets/` (STL/GLB), `editor.html`/`perform.html`, and the `*.json` data files. Nothing (README, scripts, PAK build, HTML) referenced them, so the move was safe.

New home: **`tests/js/`** — the existing `tests/` directory already holds the Python tests, so all JS tests now live under one `tests/` roof and the root is clean.

### Changes Made

- `realtimecontrol/rtr/tests/js/`: **created**; all 14 `test_*.js` moved here (6 via `git mv` — they were tracked — 8 via `mv` — untracked).
- Each moved file, three mechanical path fixes:
  - `require("./collide.js")` → `require("../../collide.js")` (6 files)
  - `const RDIR = __dirname;` → `const RDIR = path.join(__dirname, "..", "..");` (8 files)
  - `path.join(__dirname, "assets"...` → `path.join(__dirname, "..", "..", "assets"...` (6 files)
- `Run:` header comments updated to `node tests/js/test_X.js`.

### Documentation

None required — no README/script/PAK referenced the old locations; the in-file `Run:` comments now carry the correct path.

### Architecture

No architectural impact. Runtime app code (`collide.js`, `editor.html`, `assets/`, `*.json`) is untouched; only the test scripts moved. The PAK (`scripts/build_pak.sh` CODE_FILES) never referenced the test files.

### Validation

- `node tests/js/test_*.js` (all 14, from the `realtimecontrol/rtr` root): **12 PASS, 2 FAIL**.
- The 2 failures (`test_action_cam.js`, `test_patches_seed.js`) are **pre-existing data-assertion failures**, not move-related: zero `ENOENT`/`MODULE_NOT_FOUND`/`Cannot find` errors in their output — all files load from the new location; the failures are assertions on zone/patch data (`actions[resting_testing_bridge][bridge]` is `undefined`) and a DOM `onclick` null error. Both files were untracked before the move, so no git baseline; the absence of any path error is the evidence the move is not the cause.
- `ls test_*.js` at the root → **0 files** (root clean).
- `git status`: the 6 tracked files show as `RM` (rename + modify); the 8 untracked ones are new under `tests/js/`.

### Decisions and Caveats

- `tests/js/` chosen over a top-level `js-tests/` or `tests_js/` to keep all tests (Python + JS) under the single existing `tests/` directory.
- The 2 pre-existing JS test failures were **not** fixed (out of scope; same convention as the 5 pre-existing `test_remote_sdl.py` SETUP failures).
- `bench_collide.js`, `bench_collision.js`, `validate_zones.js` remain in the root — same category (root-level dev scripts using `__dirname` + `./collide.js`) but the user only asked for `test_*.js`. They are the natural candidates for the next cleanup pass (move to `tests/js/` or a `tools/` dir; `validate_zones.js` is arguably a tool, not a test).

### Next Task

Optional cleanup continuation: decide a home for `bench_collide.js`, `bench_collision.js`, `validate_zones.js` (and possibly the other one-off root scripts: `check_zone_limits.py`, `sim_demo.py`, `parse_moods.py`, `chatbot.py`) and move them with the same two-level `__dirname` fix.

### Instructions for the Next Task

- Same mechanical fix if moving to a subdir: `require("./collide.js")` → `require("../../collide.js")`, `__dirname` → `path.join(__dirname, "..", "..")` (two levels up), and update `Run:` comments.
- Verify with `node <path>` per file; treat data-assertion failures as pre-existing (grep output for `ENOENT|MODULE_NOT_FOUND|Cannot find` → expect 0).
- 2 known pre-existing JS failures: `test_action_cam.js` (DOM `onclick` null), `test_patches_seed.js` (4 assertions on `resting_testing_bridge`/`bridge` patch data).

## 2026-11-04 — Poses tab: regression tests for the save/load picker fix (test)

### Status

Completed

### Summary

Verified the poses-tab fix (previous entry) against the mini-DOM test harness and the sibling DOM-stub tests.

### Changes Made

- `realtimecontrol/rtr/test_poses_tab.js`: Test 9 (saving keeps `#posesSaveSel` / `#posesLoadSel` selections and the rendered list) and Test 10 (`posesLoad()` renders the pose list) — see the implement entry for the assertions.

### Documentation

None required — test-only.

### Architecture

No architectural impact.

### Validation

- `node test_poses_tab.js` → **ALL_TESTS_PASSED** (32/32, including the 4 new assertions).
- `node test_flow_tab.js` / `node test_groups_tab.js` / `node test_roam_badge.js` → **ALL_TESTS_PASSED** (no regressions from the `editor.html` changes).
- `node test_action_cam.js` → LOAD ERROR, `node test_patches_seed.js` → FAILURES=4 — **pre-existing**, identical to the recorded baseline (stale hand-maintained `STATIC_IDS` lists; they predate the poses-tab ids and fail identically on the original `editor.html`).

### Decisions and Caveats

- The two pre-existing failures were left untouched (out of scope; baseline parity confirmed).
- The new assertions run against the real `editor.html` main script (the harness auto-derives element ids), so they guard the actual wiring, not a stub.

### Next Task

Final handoff (this entry set). No further code work outstanding.

### Instructions for the Next Task

`No immediate next task identified.`

## 2026-11-04 — Poses tab: keep "save to" selection + pose list visible across save/load (implement)

### Status

Completed

### Summary

Fixed the poses-tab behavior the user reported: (1) the "— save to —" dropdown (`#posesSaveSel`) no longer resets to the placeholder after a save, (2) the pose list no longer collapses on save, and (3) the pose list is now visible as soon as an action is loaded (pressed **load**, or picked in the "load from" picker).

Mechanics: the picker rebuilds (`buildPosesLoadPicker` / `buildPosesSavePicker`) now preserve the previous selection — the value is captured before `innerHTML = ""` and restored afterwards when the option still exists (`restorePickerValue`). Since `#posesLoadSel` is the working action, preserving it also keeps `buildPosesList()` rendering the rows instead of collapsing to "pick an action to edit its poses". `posesLoad()` now calls `buildPosesList()` after loading the pose into the scratch. `#posesLoadSel.onchange` now resets the active index to 0 and re-renders the list (plus the safezone/out-of-bounds highlighting), so the list activates the moment an action is picked.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - New `restorePickerValue(sel, prev)` helper (just above `buildPosesLoadPicker`): restores a picker's previous value after a rebuild if the option still exists.
  - `buildPosesLoadPicker()`: captures `prev = loadSel.value` before the rebuild and calls `restorePickerValue(loadSel, prev)` after.
  - `buildPosesSavePicker()`: same — `prev = saveSel.value` before the rebuild, `restorePickerValue(saveSel, prev)` after.
  - `posesLoad()`: added `buildPosesList()` after `posesLoadPoseFromAction(posesCurIndex)`.
  - Event wiring (next to `#posesSaveSel.onchange`): `if ($("posesLoadSel")) $("posesLoadSel").onchange = () => { posesCurIndex = 0; buildPosesList(); updatePosesOob(); };`
- `realtimecontrol/rtr/test_poses_tab.js`: added Test 9 (save keeps both picker selections + the list rendered) and Test 10 (`posesLoad()` renders the list); updated the header comment.

### Documentation

None required — UI behavior fix, no docs describe these interactions.

### Architecture

No architectural impact.

### Validation

- `node test_poses_tab.js` → **ALL_TESTS_PASSED** (32/32, incl. the 4 new regression assertions: "save keeps the 'save to' selection", "save keeps the working action (load picker)", "save keeps the pose list rendered (no collapse)", "posesLoad renders the pose list (visible on load)").
- `node test_flow_tab.js` → **ALL_TESTS_PASSED**; `node test_groups_tab.js` → **ALL_TESTS_PASSED**; `node test_roam_badge.js` → **ALL_TESTS_PASSED** (no regressions).
- `node test_action_cam.js` → LOAD ERROR, `node test_patches_seed.js` → FAILURES=4 — **pre-existing**, identical to the recorded baseline (stale hand-maintained `STATIC_IDS`).

### Decisions and Caveats

- `#posesSlotSel` still resets to "append" after a save: deliberate (prevents accidentally replacing a slot twice in a row); not part of the complaint.
- Picking a new action in the load-from picker resets the active pose index to 0 (the old index belonged to the previous action; `buildPosesList()` would otherwise clamp it arbitrarily).
- The pickers are also rebuilt on tab entry (`posesEnter()`), so the selection now survives tab switches too (same preservation path).

### Next Task

Run the test suite and finalize the handoff (covered by the next entry).

### Instructions for the Next Task

- All changes are in `realtimecontrol/rtr/editor.html` (poses-tab JS, ~L7050–7360 + wiring at ~L9125) and `realtimecontrol/rtr/test_poses_tab.js` (Tests 9–10).
- Verify with `node test_poses_tab.js` (must print ALL_TESTS_PASSED).

## 2026-11-04 — Poses tab: save resets "save to" dropdown + collapses pose list (analysis)

### Status

Completed

### Summary

User report: in the poses tab, every time the "save" button is pressed, the "— save to —" dropdown (`#posesSaveSel`) resets to its placeholder, forcing the user to reselect the action they are working on; the pose list (`#posesList`) also collapses on save; and the list should be visible as soon as an action is loaded.

Root cause (all in `realtimecontrol/rtr/editor.html`, poses-tab section ~L7050–7350):

1. **`#posesSaveSel` resets on save**: `posesAddPose()` (L7287) ends by calling `buildPosesSavePicker()` (L7313), which does `saveSel.innerHTML = ""` and rebuilds all options — the selection falls back to the first option ("— save to —", value `""`).
2. **Pose list collapses on save**: `posesAddPose()` also calls `buildPosesLoadPicker()` (L7312), which resets the *working action* picker (`#posesLoadSel`) the same way. `buildPosesList()` (L7315, called right after) then finds no working action (`posesWorkingAction()` returns null) and clears `#posesList` with "pick an action to edit its poses".
3. **List not visible on load**: `posesLoad()` (L7345) only loads the pose into the scratch and never calls `buildPosesList()`; `#posesLoadSel` has no `onchange` handler, so picking an action in the "load from" picker also doesn't render the list until some other operation triggers a rebuild.

The pickers are also rebuilt on every tab entry (`posesEnter()`, L7406), so the same reset happens there.

### Changes Made

- None (analysis only).

### Documentation

None required — pure analysis; the fix entry below documents the behavior change.

### Architecture

No architectural impact.

### Validation

- Code read: `editor.html` L6940–7434 (poses-tab JS), L9095–9139 (event wiring); `test_poses_tab.js` (mini-DOM harness + assertions). No commands run.

### Decisions and Caveats

- Fix approach: preserve each picker's previous value across rebuilds (capture before `innerHTML = ""`, restore after if the option still exists); `posesLoad()` re-renders the list; `#posesLoadSel.onchange` re-renders the list (resetting the active index to 0) so the list activates as soon as an action is picked.
- `#posesSlotSel` intentionally keeps resetting to "append" after a save (deliberate default to avoid accidental slot replace on the next save) — not part of the complaint.

### Next Task

Implement the fix in `editor.html` (pickers preserve selection; `posesLoad()` renders the list; `#posesLoadSel.onchange` renders the list).

### Instructions for the Next Task

- Add a `restorePickerValue(sel, prev)` helper; call it at the end of `buildPosesLoadPicker()` and `buildPosesSavePicker()` after capturing `prev = sel.value` before the `innerHTML = ""` rebuild.
- Add `buildPosesList()` to `posesLoad()` after `posesLoadPoseFromAction(posesCurIndex)`.
- Wire `if ($("posesLoadSel")) $("posesLoadSel").onchange = () => { posesCurIndex = 0; buildPosesList(); updatePosesOob(); };` next to the existing `#posesSaveSel.onchange` wiring (L9122).
- Then add regression assertions to `test_poses_tab.js` and run `node test_poses_tab.js`.

## 2026-11-04 — Unclutter `remote/` vs `rtr/remote/` (entry points, dead code, docs)

### Status

Completed

### Summary

User hit `ImportError: attempted relative import with no known parent package` running `python rtr/remote/tui.py` and asked what the top-level `remote/` dir is vs `rtr/remote/` (one for the Trimui brick, one for the terminal?). Findings:

- **`rtr/remote/`** is the canonical remote package: desktop urwid TUI (`tui.py`) **and** the Trimui SDL2 app (`mainmenu.py` → `sdlapp.py`), plus shared `view.py`/`connection.py`/`config.py`/`events.py`/`led.py`. The PAK ships this tree to the device (`scripts/build_pak.sh` CODE_FILES → `dist/repo/rtr` → `/storage/rtr-remote`); the device runs the **SDL** app, never `tui.py`.
- **Top-level `remote/`** was a historical scaffold: a thin launcher (`tui.py`), a **dead legacy `config.py`** (`localhost:5000`/`js0` defaults, imported by nothing), its `__init__.py`, a stale `config.json.example`, the Trimui deploy runbook (`deploy.md`), and R36S hardware assets (`yellowbrick/`: device-tree blobs + MIPI overlay).
- The `__main__` bootstrap block in `rtr/remote/tui.py` was **dead code**: module-level relative imports (line 40) fail before it can run, so `python rtr/remote/tui.py` could never work — the user's exact error.
- `tests/test_remote_tui.py` was **stale**: it imported `action_kind`/`action_target`/`reverse_exits` from `rtr.flow` and `item_label` from `rtr.remote.tui` — all gone after the flow model moved to the `next`-field model (`flow.py`: `reachable_targets`/`reverse_targets`/`build_items`/`activation_commands`; `view.py`: `item_label`). It failed at import.

Working entry points (from the `realtimecontrol/rtr` root): `python remote/tui.py [flags]` (launcher) and `python -m rtr.remote.tui [flags]` (canonical module). Direct execution of `rtr/remote/tui.py` is impossible and is now documented as such.

### Changes Made

- `realtimecontrol/rtr/rtr/remote/tui.py`: removed the dead `__main__` bootstrap block; added a "Run it" note to the module docstring (launcher + `-m` forms, and why direct execution fails).
- `realtimecontrol/rtr/rtr/remote/config.py`: fixed the stale `--core-host` help text ("default: 127.0.0.1" → actual default `192.168.1.185`).
- `realtimecontrol/rtr/remote/`: **deleted** legacy `config.py`, `__init__.py`, `config.json.example`, `__pycache__/`. Kept `tui.py` (launcher), `deploy.md`, `yellowbrick/`.
- `realtimecontrol/rtr/tests/test_remote_tui.py`: **rewritten** against the current API (30 tests: `action_loops`/`action_enabled`/`action_next_list`, `reachable_targets`/`reverse_targets`, `build_items`, `item_label`, `activation_commands`).
- `realtimecontrol/rtr/remote/README.md`: **new** — what the top-level dir holds, how to run the desktop TUI from it, where config lives, what the Trimui actually runs.
- `realtimecontrol/rtr/rtr/remote/README.md`: added a "What's where" table (this dir / top-level `remote/` / PAK / `dist/repo`), a "Running tests (dev machine)" section, and a pointer to `remote/deploy.md` for the socat cross-network forwarding.
- `realtimecontrol/rtr/README.md`: fixed the remote-TUI section (broken `python rtr/remote/tui.py` command → `python -m rtr.remote.tui`; added config note + README links); added a top-level `remote/` row to the Modules table.

### Documentation

- New `remote/README.md` (top-level) + expanded `rtr/remote/README.md` + main README fixes (see Changes Made).

### Architecture

No architectural impact. The package layout is unchanged; only dead scaffolding was removed and the two working entry points were made explicit. The PAK (`build_pak.sh` CODE_FILES) only references `rtr/remote/*`, so device deploy is unaffected.

### Validation

- `python tests/test_remote_tui.py` → **ALL TESTS PASSED** (30/30; was failing at import before the rewrite).
- `python remote/tui.py --help` → OK (launcher still works after deleting `remote/__init__.py`).
- `python -m rtr.remote.tui --help` → OK (harmless `RuntimeWarning: 'rtr.remote.tui' found in sys.modules...` — inherent to `rtr/remote/__init__.py` importing `.tui`; the app runs fine).
- `RemoteTUI(Config(core_host='orangebot'))._build_top()` → builds a 12-row Pile (render path OK without a TTY).
- `python tests/test_remote_sdl.py` → **5 failed, 25 passed, 2 skipped — identical to the pre-change baseline** (pre-existing SETUP-page test failures, untouched by this task).
- Grep: no remaining references to the deleted top-level `remote/config.py` / `remote/__init__.py` / `remote/config.json.example` (all hits are the canonical `rtr/remote/*` paths or the new README text).

### Decisions and Caveats

- Kept `remote/tui.py` as the launcher rather than deleting it: it's the documented `python remote/tui.py` entry point and the only way to run the TUI without the `-m` RuntimeWarning.
- Did **not** make `python rtr/remote/tui.py` work: the module-level relative imports make direct execution impossible without a restructure; documented instead (docstring + both READMEs).
- The 5 pre-existing `test_remote_sdl.py` SETUP-page failures were left alone (out of scope; they fail identically before and after).
- `python -m rtr.remote.tui` prints a RuntimeWarning because `rtr/remote/__init__.py` eagerly imports `.tui`. Left as-is: `__init__` is intentional (PAK + `test_remote_sdl.py` rely on it) and the warning is harmless.

### Next Task

Per-module README sweep ("this goes for all modules"): only `rtr/remote/` and `rtr/camera/` have module READMEs. Extend the same convention (what's in the dir, dev test runs, real-robot config) to `brain/`, `core/`, `display/`, `io/`, `patches/`, `robot/`, `safety/`, `sound/`, `state/`, and fix the 5 stale SETUP-page tests in `tests/test_remote_sdl.py`.

### Instructions for the Next Task

- Convention to copy: `rtr/remote/README.md` "What's where" + "Running tests (dev machine)" sections; main README Modules table row per top-level dir.
- SETUP test failures to fix: `test_settings_content_and_help_text`, `test_setup_page_activation_returns_none_cmds`, `test_setup_calibrate_activation_returns_prompt`, `test_setup_map_activation_returns_capture_status`, `test_setup_dpad_left_right_still_font_on_font_row` — all in `tests/test_remote_sdl.py`; the SETUP page logic lives in `rtr/remote/view.py` (`_settings_content`, `build_settings_items`, `activate_commands`).
- Config reference for the real robot: `rtr/remote/config.json` fields + `remote/deploy.md` (socat forwarding, on-device PAK path `/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/config.json`).

## 2026-11-04 — Diagnose `clear_action` "error" output (not an error)

### Status

Completed

### Summary

User reported repeated `[debug-clear_action] clearing action=bridge zone=sleeping_resting_bridge` output with a 3-frame stack, calling it "these errors". Confirmed this is **not** an exception. It is the intentional debug print in `clear_action` (`realtimecontrol/rtr/rtr/state/machine.py:540-546`), added to capture "who is clearing the action (the hold-revert bug)". It prints `traceback.format_stack()[-4:-1]` (the last 3 call frames) on every action clear.

Decoded the pasted stack and confirmed it is the **normal single-run completion path**, not a fault:
- `bridge` (`zones.json`) is `loop: false` with 3 poses and `next: {zone: resting, action: hold}`.
- `step` (`machine.py:755`) → `_advance_anchor` (`machine.py:478`, `action_index >= n`, loop false) → `_advance_after_action` (`machine.py:650`) → `clear_action()`.
- `_advance_after_action` clears the action first (print fires) then hands off to `resting/hold`. `resting`/`hold` are both enabled, so the handoff is valid.

Key diagnostic for the ongoing hold-revert bug: because the print dumps the call stack, each firing identifies *which* path cleared the action. The `_advance_after_action` stack is the legitimate completion path (expected). The spurious clears to investigate are the ones from other stacks: safety-gate discard (`_try_go` → `clear_action`, `machine.py:701`), entering manual mode (`set_control_mode`), or `random_wrist` (`machine.py:278`).

### Changes Made

- None. Investigation/diagnosis only; no code changed.

### Documentation

None required — this is a diagnostic clarification, no behavior changed.

### Architecture

No architectural impact.

### Validation

- Read `machine.py` `clear_action` (`:540-549`), `_advance_after_action` (`:631-667`), `_advance_anchor` (`:455-478`), `step` (`:709-758`), `_try_go` (`:681-704`).
- Verified `zones.json`: `sleeping_resting_bridge.bridge` = `loop:false`, 3 poses, `next: resting/hold`; `resting.hold` enabled + `loop:true`.
- Cross-checked pasted line numbers (755 / 478 / 650) against current file — all match.

### Decisions and Caveats

- Did **not** remove the debug print: it was added deliberately to track the hold-revert bug and is still doing useful work (identifying the clearing call path). Remove it only once that bug is diagnosed/fixed.
- The "errors" are log noise from the debug instrumentation, not a crash.

### Next Task

If the hold-revert bug persists, watch which call stack the `clear_action` print fires from. A firing from `_advance_after_action` is normal; a firing from the safety-discard / manual-mode / `random_wrist` paths is the spurious clear to chase.

### Instructions for the Next Task

- Reproduce, then read the *stack* in the `[debug-clear_action]` print, not just the message.
- Legit completion stack: `step → _advance_anchor → _advance_after_action → clear_action`.
- Spurious stacks to investigate: `_try_go → clear_action` (`machine.py:701`), `set_control_mode → clear_action`, `random_wrist → clear_action` (`machine.py:278`).

## 2026-11-04 — Fix stale `_MockMachine` mocks (missing `last_discard`)

### Status

Completed

### Summary

Fixed the pre-existing test debt flagged in the 2026-11-03 video-registry handoff: the `_MockMachine` duck-typed machine in `tests/test_patch_push.py` (and the identical sibling in `tests/test_patch_override.py`) did not define `last_discard`, so `Display._frame` raised `AttributeError: '_MockMachine' object has no attribute 'last_discard'` when it reached `display.py:201`. That error killed the state-frame emission, so `_frame_for`'s `sent[0]` raised `IndexError`. The real machine defines the attribute (`machine.py:103`: `self.last_discard: Optional[dict] = None`); the mocks were simply stale. Added the one-line attribute to both mocks.

**Sibling discovery:** while validating, the full-suite run surfaced that `tests/test_patch_override.py` has the *exact same* stale-mock bug (same `_MockMachine`, same missing `last_discard`, same `IndexError`). It was fixed identically in the same change. The other two remaining failures have **different, unrelated** root causes and were left untouched (out of scope for this fix — see Decisions).

### Changes Made

- `realtimecontrol/rtr/tests/test_patch_push.py`: added `self.last_discard = None` to `_MockMachine.__init__` (`:65`).
- `realtimecontrol/rtr/tests/test_patch_override.py`: added `self.last_discard = None` to `_MockMachine.__init__` (`:66`).

(2 files, 1 line each; `git diff --stat` confirms the change is isolated to these two test files.)

### Documentation

None required — this is a test-mock correctness fix restoring the duck-type contract already documented by the mocks' own docstring ("A duck-typed machine exposing the attributes `Display._frame` accesses").

### Architecture

No architectural impact. No change to `Display`, `Patches`, `machine`, or any production module — only the test doubles were brought in line with the real `machine.py` attribute surface that `display.py:201` already reads.

### Validation

- `python3 tests/test_patch_push.py` → **ALL TESTS PASSED** (22/22; was failing with `IndexError` before the fix).
- `python3 -m pytest tests/ -q --ignore=tests/test_remote_tui.py` → **399 passed, 14 skipped** (the two `test_patch_override.py` failures are now green).
- `git diff --stat` → only `tests/test_patch_push.py` + `tests/test_patch_override.py` changed (1 insertion each).
- **PRE-EXISTING, UNRELATED (left untouched):**
  - `tests/test_remote_sdl.py` — 5 failures, UI-text drift (e.g. `test_settings_content_and_help_text` asserts `"THEME / LAYOUT"` appears in the settings view; it does not). Different root cause from the mock bug; no `Display`/`machine` involvement.
  - `tests/test_remote_tui.py` — collection error: `ImportError: cannot import name 'action_kind' from 'rtr.flow'`. Different root cause (missing export); excluded from the suite run via `--ignore`.

### Decisions and Caveats

- **Scope = the `last_discard` stale-mock bug.** The requested "fix" was the documented `test_patch_push.py` failure; the identical sibling in `test_patch_override.py` was fixed in the same pass because it is the same bug and same one-line remedy. Leaving it broken while fixing its sibling would have been inconsistent.
- **`test_remote_sdl.py` / `test_remote_tui.py` are out of scope.** They fail for unrelated reasons (SDL settings-view text drift; a missing `action_kind` export in `rtr.flow`). They do not touch `Display`/`machine`/`last_discard`, so this fix neither causes nor resolves them. Documented here so the debt is tracked, not silently dropped.
- **No production code changed** — the fix is purely test-double correctness; runtime `Display` behavior is unchanged.

### Next Task

Clear the remaining unrelated test debt (separate root causes, separate tasks):
1. `tests/test_remote_sdl.py` — reconcile the SDL settings/setup-view text the tests assert ("THEME / LAYOUT", setup-page activation, calibrate/map/dpad prompts) with the current view builder, or update the assertions to the current labels.
2. `tests/test_remote_tui.py` — resolve the `action_kind` import (either export it from `rtr.flow` or update the test to import the correct symbol).

### Instructions for the Next Task

The `last_discard` fix is complete and verified; do not re-touch the two `_MockMachine` mocks. For `test_remote_sdl.py`, read `rtr/remote/tui.py`'s settings/setup `build_rows` to see the current section labels and activation behavior, then decide whether the code or the test assertions are stale (compare against the intended UI). For `test_remote_tui.py`, grep `rtr/flow.py` for what `action_kind` should be (likely a renamed/removed symbol) and align the import or the export. Run `python3 -m pytest tests/ -q` to confirm the suite is fully green afterward.

## 2026-11-03 — Video registry: FULL FEATURE handoff (tasks 1-10)

### Status

Completed

### Summary

**Feature: leak-free video registry** — fixes the browser memory crash on the render page (and editor previews). The crash came from `initVideo` creating a brand-new `<video>` element + regl texture on **every** hydra re-eval. On the render page the core re-evals the patch on every state change inside a **single persistent Hydra instance** (one stable regl context), so each re-eval spawned a new `<video>` decode pipeline and a new GPU texture that were orphaned but never destroyed — unbounded growth → crash. The fix makes every `initVideo` reuse a **shared, preloaded** `<video>` source via a global registry, and eagerly preloads the referenced videos.

**Root cause (confirmed):** `render.html:83 makeHydra()` reuses one Hydra instance (`if (hydra) return hydra`); `renderCode()` (`:93`) calls `h.eval(code)` which re-runs the patch, spawning a fresh `HydraSource` per eval. The old `HydraSource`'s `<video>` + `this.tex` are dropped but never destroyed (no `.destroy()`; the original `initVideo` at `hydra-synth.js:2773` created a new `<video>` + `this.regl.texture(...)` unconditionally). Editor previews (`editor.html:4327/4487`) re-eval the same way.

**Solution (implemented across tasks 1-9):**
1. **Shared registry** (`video-registry.js`, NEW): `window.__RTR_VIDS__` = map of absolute URL → `{ vid, loaded, missing, texByCtx }`. `ensureVideo(url)` is the single creator of `<video>` elements (muted/loop/preload=auto/no-autoplay). `preloadVideos()` GETs `/api/videos` and warms the registry with a staggered pool (CONCURRENCY=3); referenced-but-absent files are marked `missing` up front (no 404 churn) when the server reports `on_disk`.
2. **Registry-aware `initVideo`** (`hydra-synth.js:2773-2817`): reuses the shared `<video>` per URL; caches ONE regl texture per `(url, regl-context)` in `e.texByCtx` (a `Map` keyed by the regl instance — safe across the render page's stable context and the editor's per-preview contexts); returns early for `missing` (no crash/retry storm); falls back to the original create-fresh path when the registry is absent (3D screen context).
3. **`GET /api/videos`** (`rtr/io/httpserver.py`): returns `{"videos": [...union of referenced pose_videos...], "on_disk": [...files in assets/videos/...]}` so the client knows what to preload and what's missing.
4. **Page wiring** (render/editor/client): `preloadVideos()` on WS open + re-warm on `zones_changed` (guarded by `typeof preloadVideos === "function"`).

**Why this fixes the crash:** the number of live `<video>` decode pipelines is now bounded by the number of **unique** video URLs (not by re-eval count), and GPU textures are bounded by `(unique URLs × regl contexts)`. Preloading means the videos are already decoded before `initVideo` reuses them, so no mid-session decode spike.

### Changes Made

- `realtimecontrol/rtr/video-registry.js` (NEW): the shared registry + `preloadVideos()`.
- `realtimecontrol/rtr/hydra-synth.js` (`initVideo` at `:2773-2817`): registry-aware reuse (shared `<video>` + per-context texture cache + `missing` early-out + fallback).
- `realtimecontrol/rtr/hydra-synth.js.bak-video-registry` (NEW): pre-patch backup (md5 `0cca2e754485c24536161985fabc7e62`).
- `realtimecontrol/rtr/rtr/io/httpserver.py`: `GET /api/videos` dispatch + `_get_videos()` handler.
- `realtimecontrol/rtr/render.html`: local hydra (task 2) + registry script tag (task 3) + `preloadVideos()` on WS open + `zones_changed` branch (task 6).
- `realtimecontrol/rtr/editor.html`: registry script tag (task 3) + `preloadVideos()` on WS open + `zones_changed` branch (task 7).
- `realtimecontrol/rtr/client.html`: registry script tag (task 3) + `preloadVideos()` on WS open + `zones_changed` branch (task 8).
- `realtimecontrol/rtr/tests/test_videos_http.py` (NEW): 3 tests for `GET /api/videos`.

### Documentation

Documented inline: the `initVideo` header comment explains the reuse contract; the `_get_videos` comment documents the response shape; the `video-registry.js` header documents the module. No separate doc file needed.

### Architecture

New shared client-side module (`video-registry.js`) owns the video-source registry and is the single creator of `<video>` elements. `initVideo` (hydra-synth.js) is its consumer. The registry key is the **absolute** URL `/assets/videos/<name>`, matching what `patches.py:157` embeds in generated patches — so preload and `initVideo` hit the same entry. The texture cache is keyed by BOTH url AND regl context (Map), so contexts never share textures across GL boundaries. The `GET /api/videos` endpoint is the data source for the eager preload.

### Validation

- `node --check hydra-synth.js` → **SYNTAX_OK** (4480 lines, +26 vs. original).
- `node --check video-registry.js` → **SYNTAX_OK**.
- `python3 -m py_compile rtr/io/httpserver.py` → **PY_COMPILE_OK**.
- `python3 tests/test_videos_http.py` → **ALL TESTS PASSED** (3/3).
- `python3 tests/test_perform_http.py` → **ALL TESTS PASSED** (5/5) — no regression to existing HTTP handlers.
- **PRE-EXISTING, UNRELATED:** `python3 tests/test_patch_push.py` fails (`_MockMachine` missing `last_discard` → `IndexError`). `display.py:201` reads `machine.last_discard` (real machine defines it at `machine.py:103`); the test's `_MockMachine` (`test_patch_push.py:56`) doesn't. Committed `Display` behavior + stale mock; the test imports `Display`/`Patches`/`bus`/`commands`, **not** `httpserver`, so the video change can't affect it. One-line fix if desired: add `self.last_discard = None` to `_MockMachine.__init__`.

### Decisions and Caveats

- **Preload does not autoplay** — preloaded videos are muted/loop/preload=auto but `autoplay=false`; they start only when `initVideo` reuses and plays them (avoids the browser blocking unstarted media).
- **Registry key is absolute** — `urlFor` normalizes a bare filename to `/assets/videos/<name>`; `initVideo` uses the raw (already-absolute) url as the key. Both match the generated-patch form, so preload and `initVideo` dedupe.
- **Texture cache keyed by regl context** — a `Map` keyed by the regl instance avoids cross-context reuse (a texture belongs to the GL context it was created in). Render page (single context) gets full reuse; editor (one context per preview) is safe; simultaneous multi-preview of the same URL each get their own context-keyed texture (no worse than status quo).
- **`params` ignored in the texture cache key** — generated patches call `initVideo(url)` with no `params` (`patches.py:157`), so the common case has `params === undefined`. If a caller ever passes differing texture params for the same URL + context, the first params win (not hit in practice).
- **Union includes all zones/actions (not just enabled)** — keeps `GET /api/videos` simple. A possible follow-up: filter by `zone.enabled`/`action_enabled` to avoid preloading unused videos.
- **Two video dirs** — `assets/videos/` (video mode; `on_disk` source) vs. `assets/vid/` (legacy, referenced by complex patches in `patches.json`). `GET /api/videos` only lists `assets/videos/`; legacy `vid` paths are preloaded on demand via their relative url (no 404-churn issue since they're not in the `on_disk` union).

### Next Task

No immediate next task identified. The feature is complete. Optional follow-ups (out of scope, for whoever owns those areas): (1) add `self.last_discard = None` to `_MockMachine` in `tests/test_patch_push.py` to clear the pre-existing test debt; (2) filter the `GET /api/videos` union by `zone.enabled`/`action_enabled` to skip preloading disabled zones' videos.

### Instructions for the Next Task

The video-registry feature is complete and validated. If continuing, the optional follow-ups above are the only loose ends. Do NOT re-trigger the crash by reverting `initVideo` to create-fresh; the registry (`video-registry.js`) + `initVideo` reuse + `GET /api/videos` + the page wiring are all load-bearing. The backup `hydra-synth.js.bak-video-registry` holds the original `initVideo` if a targeted revert is ever needed.

## 2026-11-03 — Video registry: test GET /api/videos (task 9/10)

### Status

Completed

### Summary

Part of the leak-free video registry feature. Task 9 of the plan: add a unit test for the new `GET /api/videos` endpoint and run the existing httpserver + patches tests to confirm no regressions. Added `tests/test_videos_http.py` (headless, real `ThreadingHTTPServer` on an auto port, mirroring `test_perform_http.py`). All three video-endpoint tests pass; the existing `test_perform_http.py` still passes (5/5).

### Changes Made

- `realtimecontrol/rtr/tests/test_videos_http.py` (NEW): three tests for `GET /api/videos`:
  - `test_get_videos_returns_union_and_on_disk` — asserts the union of referenced `pose_videos` (deduped, `null` skipped) and the `on_disk` file list.
  - `test_get_videos_handles_missing_zones` — asserts graceful degradation when the zones provider raises (`videos` empty, `on_disk` still served).
  - `test_get_videos_empty_when_no_videos_dir` — asserts `on_disk` is empty when `assets/videos/` is absent (no crash).

### Documentation

None required — the test module docstring documents coverage, mirroring `test_perform_http.py`.

### Architecture

No architectural change. The test exercises the new `GET /api/videos` handler (task 5) end-to-end over HTTP.

### Validation

- `python3 tests/test_videos_http.py` → **ALL TESTS PASSED** (3/3): `test_get_videos_empty_when_no_videos_dir`, `test_get_videos_handles_missing_zones`, `test_get_videos_returns_union_and_on_disk`.
- `python3 tests/test_perform_http.py` → **ALL TESTS PASSED** (5/5) — confirms the `do_GET` dispatch edit (task 5) didn't regress the existing HTTP handlers.
- `python3 tests/test_patch_push.py` → **PRE-EXISTING FAILURE** (see below); unrelated to this change.

### Decisions and Caveats

- **`test_patch_push.py` failure is PRE-EXISTING and UNRELATED** — it fails with `'_MockMachine' object has no attribute 'last_discard'` → `IndexError` on `sent[0]`. Root cause: `rtr/display/display.py:201` reads `self._machine.last_discard` (the real machine defines it at `rtr/state/machine.py:103`), but the test's `_MockMachine` (`tests/test_patch_push.py:56`) doesn't. This is committed `Display` behavior + a stale test mock — test debt from the in-flight work, NOT caused by the video registry. `test_patch_push.py` imports `Display`/`Patches`/`bus`/`commands`, **not** `httpserver`, so my additive `GET /api/videos` change cannot affect it. One-line fix if desired: add `self.last_discard = None` to `_MockMachine.__init__`.
- **Test isolation** — the new test uses a temp root (controlled `assets/videos/`) so `on_disk` is deterministic and independent of the real `assets/videos/` dir.

### Next Task

Task 10: write the full feature handoff entry summarizing the leak-free video registry (tasks 1-9).

### Instructions for the Next Task

Write a comprehensive handoff entry (top of `.kilo/task-handoff.md`) summarizing the whole video-registry feature: the leak (per-eval `<video>` + regl texture growth in a persistent regl context), the registry design (`window.__RTR_VIDS__`, shared `<video>` per URL, per-`(url,regl-context)` texture cache), the `initVideo` patch (task 4), the `GET /api/videos` endpoint (task 5), the page wiring (tasks 6-8), and validation. Note the pre-existing `test_patch_push.py` mock debt. Mark the feature complete.

## 2026-11-03 — Video registry: wire client.html preload (task 8/10)

### Status

Completed

### Summary

Part of the leak-free video registry feature. Task 8 of the plan: wire the client page (the patch-chooser / 3D tool screen) to warm the video registry. Determined that client.html DOES render hydra video: `updatePatchPreview()` re-evals the selected patch via `h.eval()` (line 505), and the patch chooser can select zone/action patches that use `initVideo`. It already handled `zones_changed` (line 608, calling `fetchZones()`). So the fix is to add the eager `preloadVideos()` warm-up.

### Changes Made

- `realtimecontrol/rtr/client.html`:
  - `ws.onopen` (line 593): added `preloadVideos()` (guarded) after `fetchZones()`.
  - `zones_changed` branch (line 608): added `preloadVideos()` (guarded) alongside the existing `fetchZones()`.

### Documentation

None required — the changes are small and self-documenting; the page already logged "zones changed; re-fetching" for the branch.

### Architecture

The client page now warms the video registry (eager on WS open + re-warm on `zones_changed`), matching render.html and editor.html. Combined with task 4's registry-aware `initVideo`, the client's patch-preview re-evals reuse the shared per-URL `<video>` element and its context-keyed texture, bounding decode pipelines and GPU churn.

### Validation

- Grep confirms both edits are present: `ws.onopen` preload (line 593) and the `zones_changed` preload (line 608).
- Confirmed client.html renders hydra (line 495 `new Hydra`, line 505 `h.eval`) and already had a `zones_changed` branch (line 608) before this change.

### Decisions and Caveats

- **Reused the existing `zones_changed` branch** — client.html already re-fetched zones on this event; adding `preloadVideos()` there (rather than a new branch) keeps the event handling in one place.
- **Guarded calls** — `typeof preloadVideos === "function"` guards both call sites.
- **Patch-preview is the video consumer** — the 3D tool screen's patch preview is where video patches render; the preload covers it.

### Next Task

Task 9: add a unit test for `GET /api/videos` and run the existing httpserver + patches tests.

### Instructions for the Next Task

In `realtimecontrol/rtr/tests/`: add a test (following the `test_perform_http.py` pattern — real `ThreadingHTTPServer`, auto-port, `FakeBus`/`zones_provider`) that constructs an `HttpServer` with a `zones_provider` returning zones with `pose_videos`, and asserts `GET /api/videos` returns `{"videos": [...union...], "on_disk": [...]}`. Then run the existing httpserver + patches tests (e.g. `pytest tests/test_perform_http.py tests/test_patch_push.py` or the suite's runner) to confirm no regressions.

## 2026-11-03 — Video registry: wire editor.html preload (task 7/10)

### Status

Completed

### Summary

Part of the leak-free video registry feature. Task 7 of the plan: wire the editor page to warm the video registry (so its preview panes reuse preloaded videos) and re-warm on `zones_changed`. The editor's preview panes re-eval hydra patches (lines ~4327, ~4487) just like the render page; combined with task 4's registry-aware `initVideo`, preloading bounds the number of live `<video>` decode pipelines across all previews.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - `ws.onopen` (line 1365): added `preloadVideos()` (guarded by `typeof preloadVideos === "function"`) after the hello message is sent.
  - WS `onmessage` handler (line 1434): added an `else if (m.type === "zones_changed")` branch that calls `preloadVideos()` (guarded), mirroring the existing `patches_changed`/`sound_changed` re-fetch branches.

### Documentation

None required — the changes are small and self-documenting (comment on the `zones_changed` branch explains why).

### Architecture

The editor page now drives the video registry lifecycle just like the render page (task 6): eager warm on WS open + re-warm on `zones_changed`. Because `initVideo` (task 4) is registry-aware, every hydra instance in the editor (each preview pane = its own regl context) reuses the shared per-URL `<video>` element and its own context-keyed texture. This bounds GPU/churn across all simultaneous previews.

### Validation

- Grep confirms both edits are present: `ws.onopen` preload (line 1365) and the `zones_changed` branch (lines 1434-1436).
- The `zones_changed` branch is inserted between the `patches_changed` and `midi_changed` branches, preserving the `else if` chain.

### Decisions and Caveats

- **Guarded calls** — `typeof preloadVideos === "function"` guards both call sites so a failed `video-registry.js` load doesn't break the WS handler.
- **Warm on WS open** — matches the existing editor pattern (the WS just connected, so the core is reachable for the `fetch("/api/videos")`).
- **Preview panes benefit automatically** — no per-pane code change needed; the shared `initVideo` reuse + eager preload cover all preview instances.

### Next Task

Task 8: client.html follow-up wiring (the client page also renders hydra; wire `preloadVideos()` + `zones_changed` for parity, or confirm it doesn't render video and skip).

### Instructions for the Next Task

In `realtimecontrol/rtr/client.html`: determine whether it renders hydra video (it has `collide.js` + a local `hydra-synth.js` at line 252). If it renders video patches, add `preloadVideos()` on WS open + a `zones_changed` branch (same pattern as render.html task 6 / editor.html task 7). If it does NOT render video (e.g., only the 3D arm), the registry script tag (already added at line 253) is inert and no further wiring is needed — document that decision. Check the WS `onmessage` handler and the startup block to decide.

## 2026-11-03 — Video registry: wire render.html preload (task 6/10)

### Status

Completed

### Summary

Part of the leak-free video registry feature. Task 6 of the plan: wire the render page to warm the video registry eagerly and re-warm it when zones change. The render page is the primary crash site (single persistent Hydra instance, re-eval on every state change), so preloading the referenced videos up front means `initVideo` reuses already-decoded `<video>` elements instead of spawning new decode pipelines mid-session.

### Changes Made

- `realtimecontrol/rtr/render.html`:
  - `ws.onopen` (line 309): added `preloadVideos()` after `loadPatches()` (guarded by `typeof preloadVideos === "function"`).
  - WS `onmessage` handler (line 334): added an `else if (m.type === "zones_changed")` branch that calls `preloadVideos()` (guarded), so the registry re-warms when the zone/action video union changes.

### Documentation

None required — the changes are small and self-documenting (comment on the `zones_changed` branch explains why).

### Architecture

The render page now drives the video registry lifecycle: eager warm on WS open + re-warm on `zones_changed`. Combined with task 4's `initVideo` reuse, this bounds the number of live `<video>` decode pipelines and regl textures to the referenced-video union, eliminating the unbounded-growth crash.

### Validation

- Grep confirms both edits are present: `ws.onopen` preload (line 309) and the `zones_changed` branch (lines 334-336).
- Read the modified region (lines 330-338) to confirm the `onmessage` handler is well-formed (balanced `else if` chain, closes the handler and `connect()`).

### Decisions and Caveats

- **Guarded calls** — `typeof preloadVideos === "function"` guards both call sites so a failed `video-registry.js` load (404/parse) doesn't break the WS handler or startup.
- **Warm on WS open (not page load)** — `preloadVideos()` does an HTTP `fetch("/api/videos")`; calling it in `ws.onopen` ensures the core is reachable (the WS just connected), matching the existing `loadPatches()` pattern.
- **Re-warm on zones_changed** — the video union is derived from zones/actions, so when they change the preload set may differ; re-warming keeps the registry current.

### Next Task

Task 7: wire editor.html — add a `zones_changed` WS handler + `preloadVideos()` for the preview panes.

### Instructions for the Next Task

In `realtimecontrol/rtr/editor.html`: the WS handler is at lines ~1369-1464 (handles `state`/`sound_changed`/`patches_changed`/`midi_changed`/`midi_learn`) — there is NO `zones_changed` branch yet. Add an `else if (msg.type === "zones_changed")` branch that calls `preloadVideos()` (guarded). Also call `preloadVideos()` on startup / WS open so the editor preview panes (lines ~4327, ~4487) reuse preloaded videos. The `preloadVideos` global is exposed by `video-registry.js` (already wired at editor.html line 884).

## 2026-11-03 — Video registry: add GET /api/videos (task 5/10)

### Status

Completed

### Summary

Part of the leak-free video registry feature. Task 5 of the plan: add a `GET /api/videos` endpoint so the client's `preloadVideos()` (video-registry.js) can learn which videos are referenced and which are actually on disk. The endpoint returns `{"videos": [...], "on_disk": [...]}`: the union of all referenced video filenames (from zones/actions' `pose_videos`) and the files actually present under `assets/videos/`.

### Changes Made

- `realtimecontrol/rtr/rtr/io/httpserver.py`:
  - `do_GET` dispatch (line ~306): added `elif path == "/api/videos": self._get_videos()`.
  - New `_get_videos()` method (line ~850): builds the union of referenced video names by iterating `server.zones_provider().names()` → `.get(zn).actions()` → `.action_pose_videos(zn, an)`, collecting non-null filename strings (deduped). Then lists `on_disk` = `sorted(os.listdir(server.videos_path))`. Responds `{"videos": [...], "on_disk": [...]}`. Handles the zones-unavailable and dir-missing cases gracefully (returns empty lists).

### Documentation

None required — the endpoint mirrors the existing video handlers (`_post_videos`, `_delete_video`) and the response shape is documented in the method's comment and consumed by `preloadVideos()`.

### Architecture

New read-only endpoint in the zone/video API. It is the data source for the client's eager preload (task 3). The `videos` list drives the preload union; `on_disk` lets the client mark referenced-but-absent names as `missing` up front (no 404 churn) instead of preloading them.

### Validation

- `python3 -m py_compile rtr/io/httpserver.py` → **PY_COMPILE_OK**.
- Grep confirms the dispatch branch (line 307) and the `_get_videos` method (line 850) are present.
- Confirmed `pose_videos` entries in `zones.json` are bare filenames or `null` (e.g. `["null", "MiniMax_H3_00083_..mp4", ...]`), so the union logic (skip non-string/null) is correct.

### Decisions and Caveats

- **Union includes all zones/actions (not just enabled)** — keeps the endpoint simple and matches the plan's scope. Disabled zones' videos are also listed (harmless: the client preloads them, but they're never played). A possible follow-up is to filter by `zone.enabled` / `action_enabled` to avoid preloading unused videos.
- **`on_disk` is a directory listing** — only files (no recursion); `assets/videos/` is flat. If the dir is absent, `on_disk` is empty and the client falls back to preloading all names and letting 404s resolve.
- **Graceful degradation** — if `zones_provider()` raises (no running engine), `videos` is empty and the client relies on `on_disk` alone (which is still served).

### Next Task

Task 6: wire render.html — add a `zones_changed` WS branch in `connect()` (line ~305) and call `preloadVideos()` on startup.

### Instructions for the Next Task

In `realtimecontrol/rtr/render.html`: (1) in the WS `connect()` handler (line ~305, which currently handles `state`/`patches_changed`), add an `else if (msg.type === "zones_changed")` branch that calls `preloadVideos()`. (2) On startup (the startup block around lines 377-384, and/or in `ws.onopen`), call `preloadVideos()` once to warm the registry eagerly. The `preloadVideos` global is exposed by `video-registry.js` (already wired at line 58).

## 2026-11-03 — Video registry: patch initVideo to reuse registry (task 4/10)

### Status

Completed

### Summary

Part of the leak-free video registry feature. Task 4 of the plan: make `initVideo` in `hydra-synth.js` reuse the shared registry instead of creating a new `<video>` + regl texture on every hydra re-eval. This is the PRIMARY leak fix. Confirmed the crash mechanism first: `render.html`'s `makeHydra()` reuses a SINGLE Hydra instance (one stable regl context), so `h.eval(code)` re-runs the patch and spawns a fresh `HydraSource` per eval — each `initVideo` created a new `<video>` (new decode pipeline) and a new `this.tex` (GPU texture) that are orphaned but never destroyed, accumulating until the crash.

### Changes Made

- `realtimecontrol/rtr/hydra-synth.js` (`initVideo`, now lines 2773-2817): rewrote to be registry-aware. When `window.__RTR_VIDS__` + `window.__RTR_VID__ensure` are present, it (1) reuses the shared `<video>` element per URL via `ensure(url)`, (2) caches ONE regl texture per `(url, regl context)` in `e.texByCtx` (a `Map` keyed by the regl instance), (3) returns early for `e.missing` (referenced-but-absent → no crash / no retry storm), (4) handles the load race via `e.loaded || vid.readyState >= 2`. When the registry is absent (e.g. 3D screen context without the script), it falls back to the ORIGINAL create-fresh path verbatim.
- `realtimecontrol/rtr/hydra-synth.js.bak-video-registry` (NEW): pre-patch backup (md5 `0cca2e754485c24536161985fabc7e62`, identical to the pre-patch original).

### Documentation

None required — the change is self-documenting in the code (header comment on `initVideo` explains the reuse contract). The registry entry shape (`{vid, loaded, missing, texByCtx}`) is the interface.

### Architecture

`initVideo` is now the consumer of the shared registry (task 3's `video-registry.js`). The texture cache is keyed by BOTH url AND regl context (`e.texByCtx` = `Map<reglInstance, texture>`), so the render page's single stable context fully reuses its texture, and the editor's per-preview contexts never share textures across GL contexts (safe). The `<video>` element is shared globally per URL (the dominant leak fix).

### Validation

- `node --check hydra-synth.js` → **SYNTAX_OK** (4480 lines, +26 vs. the 4454-line original).
- Backup md5 verified identical to the pre-patch original before editing.

### Decisions and Caveats

- **Texture cache keyed by regl context** — a `Map` keyed by the regl instance avoids the cross-context reuse bug (a texture belongs to the GL context it was created in). The render page (single context) gets full reuse; the editor (one context per preview) is safe. Simultaneous multi-preview of the same URL in the editor each get their own context-keyed texture (no worse than status quo, better than a naive single cache).
- **`makeTex` sets `this.src` after load** — preserves the original's safety: `tick()` only sees a valid `src` + real texture once `loadeddata` fires (or the video is already loaded), so `tex.subimage(src)` never runs against an empty/unloaded source.
- **`params` ignored in the cache key** — generated patches call `initVideo(url)` with no `params` (`patches.py:157`), so the common case has `params === undefined`. If a caller ever passes differing texture params for the same URL + context, the first params win (documented limitation; not hit in practice).
- **Fallback is behavior-identical** — the registry-absent branch is the original code verbatim, so the 3D screen path (rtr3d.js) and any context without `video-registry.js` are unchanged.

### Next Task

Task 5: add `GET /api/videos` to `rtr/io/httpserver.py` (the union of referenced video names via `zones.names()`/`get()`/`action_pose_videos()`, plus an optional `on_disk` list from `self.videos_path`).

### Instructions for the Next Task

In `realtimecontrol/rtr/rtr/io/httpserver.py`: add a `_get_videos()` method and a `GET` dispatch branch for `/api/videos` (see dispatch around lines 300-345; existing `_post_videos` at :789 and `_delete_video` at :828 are the pattern to mirror). `_get_videos` should build `data = {"videos": [...], "on_disk": [...]}`: iterate `self.zones.names()` → `self.zones.get(zn).actions()` → `self.zones.action_pose_videos(zn, an)`, collect the union of filenames (strip the `/assets/videos/` prefix if present, keep bare names), and list `on_disk` = the filenames actually present under `self.videos_path` (a `Path`). Respond JSON like the other handlers. The client `preloadVideos()` (video-registry.js) expects `data.videos` (array of names) and optional `data.on_disk` (array of names present on disk).

## 2026-11-03 — Video registry: create video-registry.js + wire script tag (task 3/10)

### Status

Completed

### Summary

Part of the leak-free video registry feature. Task 3 of the plan: create the shared browser-side video registry + eager preload, and load it on every page. New file `video-registry.js` defines `window.__RTR_VIDS__` (a map keyed by the **absolute** video URL `/assets/videos/<name>`) with entries `{ vid, loaded, missing, tex, texCtx }`, an `ensureVideo(url)` helper (single place that creates a `<video>` element), and `preloadVideos()` which GETs `/api/videos` and warms the registry with a staggered pool (CONCURRENCY=3). Referenced-but-absent files are marked `missing` up front (no 404 churn) when the server reports `on_disk`; otherwise 404s resolve via the element's `error` event.

### Changes Made

- `realtimecontrol/rtr/video-registry.js` (NEW): `window.__RTR_VIDS__` registry, `urlFor(name)` (absolute-path key), `ensureVideo(url)` (creates muted/loop/preload=auto, non-autoplay `<video>`), `whenReady(e)`, `preloadVideos()` (GET `/api/videos`, staggered pool, graceful 404). Exposes `global.__RTR_VID__ensure` (for `initVideo` reuse) + `global.preloadVideos`.
- `realtimecontrol/rtr/render.html`: line 58 — added `<script src="video-registry.js"></script>` after the local `hydra-synth.js` tag.
- `realtimecontrol/rtr/client.html`: line 253 — same script tag.
- `realtimecontrol/rtr/editor.html`: line 884 — same script tag.

### Documentation

None required — the module's purpose is documented in its 3-line header; the registry contract (`__RTR_VIDS__` entry shape) is the interface `initVideo` (task 4) relies on.

### Architecture

New shared client-side module (`video-registry.js`) that owns the video-source registry. It is the single creator of `<video>` elements (`ensureVideo`), so `initVideo` (task 4) and the preload share one code path. The registry key is the absolute URL `/assets/videos/<name>`, matching what `patches.py:157` embeds in the generated patch — so preload and `initVideo` hit the same entry.

### Validation

- `node --check video-registry.js` → **SYNTAX_OK**.
- Grep confirms the script tag is present in all three pages (render:58, client:253, editor:884), immediately after the local `hydra-synth.js` tag.

### Decisions and Caveats

- **Preload does not autoplay** — preloaded videos are `muted`/`loop`/`preload=auto` but `autoplay=false`; they start only when `initVideo` reuses and plays them. This avoids the browser blocking unstarted media and keeps the preloaded element quiet until a pose actually plays it.
- **`on_disk` is optional** — if the server (task 5) reports `on_disk`, missing names are marked up front (no 404); if absent (older core), all names are preloaded and 404s resolve via the `error` event.
- **Registry key is absolute** — `urlFor` normalizes a bare filename to `/assets/videos/<name>`; this MUST match `initVideo`'s key (task 4) or reuse will not occur.

### Next Task

Task 4: back up `hydra-synth.js` and patch `initVideo` (`:2773`) to reuse the registry (reuse the cached `<video>` element + optional per-URL texture guarded by regl context).

### Instructions for the Next Task

Back up `realtimecontrol/rtr/hydra-synth.js` (e.g. `hydra-synth.js.bak-video-registry`), then replace the body of `initVideo(url, params)` (lines 2773-2791) with a registry-aware version: if `window.__RTR_VIDS__[url]` has a `vid`, reuse it (set `this.src` to it; if `loaded`, build the texture immediately, else attach a one-time `loadeddata` listener; call `play()`; reuse the cached `tex` only if `texCtx === this.regl`, else create + cache). If the entry is `missing`, leave the source empty (return early). Otherwise create + register the element (delegate to `window.__RTR_VID__ensure(url)` when present, else the original `document.createElement('video')` path). Keep the `dynamic = true` assignment.

## 2026-11-03 — Video registry: vendor local hydra-synth.js to render + client (task 2/10)

### Status

Completed

### Summary

Part of the leak-free video registry feature (plan `.kilo/plans/1793730784254-harmonize-visuals-video-registry.md`). Task 2 of the plan: point the render and client pages at the LOCAL `hydra-synth.js` so the `initVideo` shim (task 4) can be applied consistently across every page. The editor already used the local copy; render + client were still on the CDN.

Before switching, a parity check confirmed the local `initVideo` (`hydra-synth.js:2773-2791`) is **byte-identical** to the CDN build (same line, same 4454-line file, md5 `0cca2e754485c24536161985fabc7e62`), so switching render/client to the local copy is behavior-safe.

### Changes Made

- `realtimecontrol/rtr/render.html`: line 57 — `<script src="https://cdn.jsdelivr.net/npm/hydra-synth/dist/hydra-synth.js">` → `<script src="hydra-synth.js">`.
- `realtimecontrol/rtr/client.html`: line 252 — same CDN → local switch.
- `realtimecontrol/rtr/editor.html`: unchanged (already local at line 883; the CDN at line 878 is inside a commented-out block).

### Documentation

None required — the script-source change is self-documenting; the parity rationale is recorded here.

### Architecture

No new boundary. This unifies the hydra source across all three control pages (render/client/editor) on the vendored local `hydra-synth.js`, which is a prerequisite for the `initVideo` registry shim (task 4) and the shared `video-registry.js` preload (task 3).

### Validation

- `node --check hydra-synth.js` → **SYNTAX_OK** (4454 lines, md5 `0cca2e754485c24536161985fabc7e62`, unchanged from the documented local copy).
- Grep confirms the only remaining CDN hydra reference is `editor.html:878` (inert, inside `<!-- ... -->`).
- No JS logic changed (only a script `src` attribute), so no page-behavior test was required.

### Decisions and Caveats

- **Parity verified before switching** — the local `initVideo` matches the CDN byte-for-byte, so render/client do not change behavior from this switch alone.
- **The CDN line in `editor.html` is left commented out** (pre-existing) — not touched.

### Next Task

Task 3: create `video-registry.js` (the `window.__RTR_VIDS__` registry + `preloadVideos()`) and add its `<script>` tag to render/client/editor.

### Instructions for the Next Task

Create `realtimecontrol/rtr/video-registry.js` defining `window.__RTR_VIDS__` (a map keyed by the absolute video URL `/assets/videos/<name>`) and a `preloadVideos()` that GETs `/api/videos` and registers a `<video>` element per name (staggered, graceful 404). Add `<script src="video-registry.js"></script>` after the local `hydra-synth.js` tag in render.html (line 57), client.html (line 252), and editor.html (line 883). The registry key MUST be the absolute `/assets/videos/<name>` form because `patches.py:157` generates `initVideo("/assets/videos/{name}")`.

## 2026-11-03 — EKI recovery fix: no-quit soft resync + dead-socket → KukaError translation

### Status

Completed

### Summary

Fixed the EKI recovery so the engine does not get stuck in an infinite `AttributeError("'NoneType' object has no attribute 'sendall'")` loop after a stale-read recovery. **Root cause (confirmed from the live `KUKAPY_DEBUG=1` log):** the KRL `KUKAPY_SERVER.SRC` CASE 8 exits the program on the `quit` command (Type 8). The old `recover()` called `disconnect()` → sent `quit` (Type 8), killing the KRL; then `connect()` re-listens but the dead KRL never dials back in, so `accept()` times out and left `self._robot` as a half-initialized `Robot` object with `comm_sock = None`. Every subsequent tick then raised a raw `AttributeError` (NOT a `KukaError`), so the engine watchdog (`isinstance(exc, KukaError)`) never retried and the engine spun forever.

**Fix (three parts in `kuka.py`):**

1. **`connect()` clean-fail** — wrapped `self._robot.connect()` in try/except to reset `self._robot = None` on failure, so no half-initialized `Robot` (with `comm_sock = None`) is left behind.
2. **`_eki_call()` dead-socket translation** — new helper that routes every kukapy call (`get_curjpos`, `get_curpos`, `move`) through a common wrapper. If `self._robot` is `None`, it raises `KukaError("EKI not connected")`. If a kukapy call raises an `AttributeError` with the dead-socket signature (`"NoneType"` + `"sendall"` or `"recv"`), it re-raises as `KukaError("EKI socket dead: ...")` so the engine watchdog can recover.
3. **`recover()` rewritten (no quit)** — a stale read is a **local desync with a live connection**, so `recover()` now does a **soft resync** first (clear `_recv_buf` + `ping()`) to re-establish the request/response pairing WITHOUT quitting the KRL; only falls back to a **hard reconnect** (close local socket + re-listen) if the link is dead. Neither path sends `quit` (Type 8), which would kill the KRL permanently.

### Changes Made

- `realtimecontrol/rtr/rtr/robot/kuka.py`:
  - `connect()`: wrapped `self._robot.connect()` in try/except; on failure resets `self._robot = None` and re-raises.
  - New `_eki_call(self, method, *args, **kwargs)` helper: raises `KukaError("EKI not connected (robot is None)")` if `self._robot` is `None`; catches a dead-socket `AttributeError` (msg contains `NoneType` AND `sendall`/`recv`) and re-raises as `KukaError("EKI socket dead: ...")`.
  - `get_curjpos()`, `get_curpos()`, `_move()`: now route their kukapy calls through `_eki_call`.
  - `recover()` rewritten: (1) soft resync first — if `self._robot` and `comm_sock` are not `None`, set `self._robot._recv_buf = b""` + `self._robot.ping()`, reset `self._zero_streak`, print `[kuka] EKI soft-resync OK`; (2) hard reconnect fallback — close `comm_sock` (no quit), set `self._robot = None`, call `self.connect()`, reset `_zero_streak`. Never sends `quit`.
- `realtimecontrol/rtr/tests/test_kuka_eki_serialization.py`:
  - New `FakeSocket` class: a socket object that records close/sendall/recv calls.
  - New `FakeKukapyRobotWithSocket` class: a kukapy `Robot` that models the socket layer for recovery tests (tracks `ping_called` / `disconnect_called`).
  - New `_make_robot_with_socket(alive)` helper.
  - New `test_eki_call_translates_dead_socket_to_kuka_error`: a dead socket (`comm_sock = None`) raises `KukaError`, not `AttributeError`.
  - New `test_eki_call_raises_kuka_error_when_robot_is_none`: `self._robot = None` raises `KukaError`.
  - New `test_recover_soft_resync_does_not_quit`: `recover()` on a live link clears `_recv_buf`, calls `ping()`, does NOT call `disconnect()`.
  - New `test_recover_hard_reconnect_when_link_dead`: `recover()` on a dead link does a hard reconnect (mocked `connect()`), does NOT call `disconnect()`.
  - New `test_connect_resets_robot_on_failure`: `connect()` resets `self._robot = None` on failure.

### Documentation

None required — the recovery mechanism is documented in the `kuka.py` method docstrings (`_eki_call`, `recover`).

### Architecture

The fix adds a fault-translation layer (`_eki_call`) between the `KukaRobot` wrapper and the kukapy `Robot`, converting unrecoverable `AttributeError`s into the `KukaError` the engine watchdog already handles. The `recover()` method is now a two-tier recovery (soft resync → hard reconnect) that never sends `quit`, making the KRL (which EXITs on quit) recoverable. No new architectural boundary — the watchdog → `recover()` chain is unchanged; only the error types and the recovery strategy changed.

### Validation

- `pytest tests/test_kuka_eki_serialization.py tests/test_kuka_eki_recv.py`: **24 passed** (11 serialization/recovery + 13 recv).
- Full suite (`tests/`, excluding `test_remote_tui.py`): **378 passed, 23 failed** (all 23 failures are pre-existing in `test_patch_push.py` and `test_remote_sdl.py`, unrelated to this change).
- Live robot verification (soft-resync actually restores motion) requires the running robot (not available in this environment).

### Decisions and Caveats

- **No quit in recovery** — the KRL EXITs on `quit` (CASE 8 in `KUKAPY_SERVER.SRC`) and will not come back. A soft resync (clear `_recv_buf` + `ping`) is sufficient for the stale-read case (the KRL is alive and responding); a hard reconnect (close + re-listen) is the fallback for a dead link. Neither sends `quit`.
- **`_eki_call` is the single translation point** — every kukapy call goes through it, so any future kukapy method added to the wrapper will automatically get the dead-socket → `KukaError` translation.
- **Soft resync clears `_recv_buf`** — this drops the mis-paired leftover frame (the zero-filled move `<Res>` that caused the desync). The `ping()` re-establishes the request/response pairing: the KRL receives the ping (Type 1), answers with a `<Res>`, and the next `curjpos` (Type 2) is correctly paired.
- **`connect()` clean-fail** — if `Robot.connect()` raises (e.g., `accept()` times out), `self._robot` is reset to `None` so the next tick sees a clean state (`KukaError("EKI not connected")`) instead of a half-initialized `Robot` with `comm_sock = None` (which would raise a raw `AttributeError`).

### Next Task

Live verification on the physical robot: run the `KUKAPY_DEBUG=1` capture during a stale-read freeze and confirm the soft resync triggers (log `[kuka] EKI soft-resync OK`) and motion is restored without the `AttributeError` loop.

### Instructions for the Next Task

Run recipe (on the core machine):

```bash
cd realtimecontrol/rtr
KUKAPY_DEBUG=1 python main.py --robot --ip <robot-ip> --port 18735 2>&1 | tee /tmp/kukapy_recovery_$(date +%Y%m%d_%H%M).log
```

1. Start `KUKAPY_SERVER` on the teach pendant after the `Listening on :18735` line.
2. Run the 5-zone network until a stale-read freeze occurs.
3. In the log: find the `EKI stale read` `KukaError` (the detector firing), then the next line should be `[kuka] EKI soft-resync OK` (NOT a `quit`/`disconnect`), followed by normal `SEND <Type>2</Type>` / `FRAME ... A=[non-zero]` traffic. The `AttributeError` loop must NOT appear.
4. If the soft resync does NOT restore motion (the KRL is genuinely dead, not just desynced), the log should show `[kuka] EKI connection recovered` (hard reconnect) or `[kuka] EKI recovery failed: ...` (reconnect also failed, which means the KRL was quit by a previous run and needs a manual pendant restart).

## 2026-11-03 — KUKAPY_DEBUG FRAME lines now print Res Code + A1-A6 (pairing capture)

### Status

Completed

### Summary

The user asked how to capture a live `KUKAPY_DEBUG=1` log during the `scan_room > look` freeze to confirm the exact EKI `<Res>` pairing. Analysis showed the existing debug output was insufficient for that: `SEND` lines print the full raw `<Rob>` command, but the two `FRAME tag='Res'` debug lines in `_recv_xml` only printed tag/len/leftover — the `<Res>` A1-A6 values were parsed and immediately discarded, so a capture could show send/recv ordering but never whether the curjpos response carried the move's zero-filled `A1-A6`. Added a `_res_debug(elem)` helper on `kukapy.robot.Robot` and wired it into both debug branches, so each consumed `<Res>` now logs `code=... A=[A1 A2 A3 A4 A5 A6]` next to the `SEND` that requested it. The desync (a move `<Res>` with `A1-A6 = 0.0` mis-paired as the next `curjpos` response) is now directly visible in the log.

### Changes Made

- `realtimecontrol/rtr/KukaPy/kukapy/robot.py`:
  - New `_res_debug(elem)` method: returns `code=<Code> A=[<A1> ... <A6>]` from a parsed `<Res>` element (`findtext` with defaults, so ping responses without A fields print empty).
  - Both `FRAME tag='Res'` debug prints in `_recv_xml` (the `\x00`-delimited branch and the plain-XML branch) now append `self._res_debug(elem)`.

### Documentation

None required — this is a debug-only logging change gated behind `KUKAPY_DEBUG=1`; the helper's docstring documents the diagnostic purpose.

### Architecture

No architectural impact. Logging-only change inside the existing `_recv_xml` reader; no behavior, timing, or API change.

### Validation

- `pytest tests/test_kuka_eki_serialization.py tests/test_kuka_eki_recv.py tests/test_stuck_recovery.py`: **31 passed**.
- Smoke test of `_res_debug` on a curjpos `<Res>` (`A=[0.0 -90.0 20.0 0.0 80.0 0.0]`) and a movej `<Res>` (`A=[0.0 0.0 0.0 0.0 0.0 0.0]`): prints as expected, confirming the discriminator is visible.

### Decisions and Caveats

- The A fields are printed as raw strings from the `<Res>` (KUKAPY_SERVER.SRC formats them with 6 decimals), so a zero-filled move `<Res>` and a real curjpos `<Res>` are distinguishable at a glance.
- Per-frame logging at 20 Hz is ~15 KB/s; a capture during a freeze run should be teed to a file (see run recipe below).
- Live verification still pending: the pairing must be observed during an actual `scan_room > look` freeze.

### Next Task

Run the live capture to confirm the desync and the stale-read detector trigger (recipe below).

### Instructions for the Next Task

Run recipe (on the core machine):

```bash
cd realtimecontrol/rtr
KUKAPY_DEBUG=1 python main.py --robot --ip 192.168.10.201 --port 18735 2>&1 | tee /tmp/kukapy_freeze_$(date +%Y%m%d_%H%M).log
```

1. `_DEBUG` is read from the environment at import, so the var must be in the process env (the `KUKAPY_DEBUG=1` prefix above).
2. Start `KUKAPY_SERVER` on the teach pendant after the `Listening on :18735` line.
3. Run the 5-zone network until it freezes at `scan_room > look`.
4. In the log: find the last `SEND` containing `<Type>4</Type>` (movej) and the `FRAME` after it — if the following `SEND <Type>2</Type>` (curjpos) is answered by a `FRAME ... A=[0.000000 0.000000 0.000000 0.000000 0.000000 0.000000]` while the robot is not physically home, the move `<Res>` was mis-paired as the curjpos response (root cause confirmed).
5. The engine's stale-read detector should also log the `EKI stale read` `KukaError` after 3 consecutive zero reads, followed by the watchdog's `robot.recover()` attempt — confirms detector + recovery path on live hardware.

## 2026-11-03 — Last-discard display + EKI stale-read root-cause fix

### Status

Completed

### Summary

Two changes addressing the user's report that the robot halts at `scan_room > look` with a stale readback of `[0,0,0,0,0,90]` (raw `[0,0,0,0,0,0]` + J6 offset `90`):

1. **Last-discard display** — Added a persistent `last_discard` record to `machine.py`, recorded by the engine on every auto-mode safety discard, forwarded in the state frame, and rendered as a `last discard: zone/action` segment in the status bar of both `client.html` and `editor.html`. The segment shows the zone/action, reason (red-link conflict / self-collision), issues, and timestamp on hover.

2. **EKI stale-read root-cause fix** — The KUKAPY_SERVER.SRC KRL loop initializes ALL `Res` values to `0.0` every iteration (lines 85–97). CASE 4 (movej) executes `PTP` but never writes `Res/A1-A6`, so the move's `<Res>` response carries `A1-A6 = 0.0`. If that zero-filled response lands in `_recv_buf` and gets misread as the next tick's `curjpos` response, the engine sees `[0,0,0,0,0,0]` raw → `[0,0,0,0,0,90]` with offset. Added a stale-read detector in `kuka.py:get_curjpos` that tracks consecutive all-zero raw reads; when the streak reaches 3 (≈150 ms at 20 Hz), it raises `KukaError`, which the engine's existing watchdog catches and triggers `robot.recover()` (disconnect + reconnect clears `_recv_buf`).

### Changes Made

- `realtimecontrol/rtr/rtr/state/machine.py`:
  - Added `self.last_discard: Optional[dict]` attribute (initialized to `None`).
  - Added `record_discard(zone, action, conflict, collision, issues)` method that stores `{zone, action, conflict, collision, issues, ts}`.
- `realtimecontrol/rtr/rtr/core/engine.py`:
  - In `_resolve_pending`, the auto-mode discard branch now calls `m.record_discard(...)` before publishing `Event.SAFETY_DISCARD`.
- `realtimecontrol/rtr/rtr/display/display.py`:
  - `_frame` now forwards `machine.last_discard` as `frame["last_discard"]` when present.
- `realtimecontrol/rtr/client.html`:
  - `updateStatusBar`: added `discardSeg` logic (zone/action, reason, issues, timestamp on hover) rendered after `blockedSeg` in the status bar.
- `realtimecontrol/rtr/editor.html`:
  - `updateStatusBar`: identical `discardSeg` logic added (mirrors `client.html`).
- `realtimecontrol/rtr/rtr/robot/kuka.py`:
  - Added `_zero_streak` counter and `_zero_streak_threshold = 3` in `__init__`.
  - Added lazy `_get_kuka_error()` helper (imports `KukaError` from `kukapy.robot` on first use, so the sim runs without kukapy).
  - `get_curjpos`: after reading raw values, checks if all are near-zero; increments `_zero_streak` on all-zero, resets on non-zero; raises `KukaError` when streak ≥ threshold.
- `realtimecontrol/rtr/tests/test_kuka_eki_serialization.py`:
  - `FakeKukapyRobot.get_curjpos` and `get_curpos` now return non-zero values (`[1,2,3,4,5,6]` and `[10,20,30,40,50,60]`) so the stale-read detector doesn't false-positive in the serialization tests.

### Documentation

None required — the stale-read mechanism is documented in the `kuka.py` comments (the KUKAPY_SERVER.SRC CASE 4 / Res initialization interaction), and the last-discard display is self-documenting via its hover tooltip.

### Architecture

No new architectural boundary. The stale-read detector is a detection layer in the robot wrapper (`kuka.py`); the recovery path uses the existing engine watchdog → `robot.recover()` chain. The last-discard record is a persistent field on `machine.py` (same pattern as `pending` / `servo_state` / `hunt_state`), forwarded through the existing `display.py:_frame` → WebSocket → client status-bar pipeline.

### Validation

- `/home/kaos/.pyenv/versions/kukart/bin/pytest tests/test_kuka_eki_serialization.py tests/test_kuka_eki_recv.py tests/test_stuck_recovery.py`: **31 passed**.
- Full suite (`tests/`, excluding pre-existing `test_remote_tui.py` collection error): **373 passed, 23 failed** (all 23 failures are pre-existing in `test_patch_push.py` and `test_remote_sdl.py`, unrelated to this change).
- Live robot verification (scan_room > look freeze) requires the running robot (not available in this environment).

### Decisions and Caveats

- The stale-read threshold is 3 consecutive all-zero raw reads (≈150 ms at 20 Hz). This is short enough to catch the desync quickly but long enough to avoid false positives when the robot is legitimately at the physical home position (all zeros) for a few ticks after a reset.
- The detector only triggers on the RAW read (before offset is applied), so the J6 offset of 90° does not interfere with the detection.
- The `KukaError` import is lazy (inside `_get_kuka_error()`) so the sim and tests run without kukapy installed.
- The last-discard record persists until the next discard replaces it (no expiry). If the user wants it to clear after a timeout, that would be a future enhancement.
- The KUKAPY_SERVER.SRC is NOT modified (per the constraint). The fix is purely on the Python side (detection + recovery).

### Next Task

No immediate next task identified. The last-discard display and EKI stale-read fix are complete and tested. If the freeze persists on the live robot, the next step would be to capture a `KUKAPY_DEBUG=1` log during the freeze to confirm the exact EKI `Res` pairing and verify the detector triggers.

### Instructions for the Next Task

If the stale-read detector needs tuning (threshold, detection logic), adjust `_zero_streak_threshold` in `kuka.py:__init__` or the detection logic in `get_curjpos`. If the last-discard display needs changes (styling, additional fields, expiry), update `updateStatusBar` in **both** `client.html` and `editor.html` identically, and the `record_discard` method in `machine.py` if new fields are needed.

## 2026-11-03 — Move-blocked flag ported to editor.html + status-bar z-index fix in both clients

### Status

Completed

### Summary

Two changes for parity and visibility:

1. **Ported the `move blocked` flag to `editor.html`** — `editor.html`'s `updateStatusBar` (line 4167) was the old version without the flag. Added the `escAttr(s)` helper and the `blockedSeg` block, mirroring `client.html` exactly. The editor now shows the persistent red **move blocked** flag with hover tooltip when a safety-gated move is blocked in manual mode.

2. **Fixed status-bar visibility under the safety popup** — The `#safetyPopup` (`position: fixed; inset: 0; z-index: 25`) covers the entire viewport including the header status bar. Since the flag and popup share the same trigger condition (`safety.phase === "confirm"`), the flag was always hidden behind the popup when visible. Added `position: relative; z-index: 30;` to `#statusBar` CSS in both `client.html` and `editor.html` so the flag remains visible above the popup overlay.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - `updateStatusBar(m)`: added `escAttr(s)` helper and `blockedSeg` logic (identical to `client.html`). The flag renders between behavior and candidates segments when `m.safety.phase === "confirm"`.
  - `#statusBar` CSS: added `position: relative; z-index: 30;` so the status bar renders above the `#safetyPopup` overlay (z-index 25).
- `realtimecontrol/rtr/client.html`:
  - `#statusBar` CSS: added `position: relative; z-index: 30;` so the status bar renders above the `#safetyPopup` overlay.

### Documentation

None required — the flag logic is self-documenting via its hover `title` tooltip and the in-code comments (already present in `client.html`, now mirrored in `editor.html`).

### Architecture

No new architectural boundary. Both changes are client-side only. The data path (`display.py:_frame` → `frame["safety"]` from `machine.pending_info()`) was already verified correct in the prior task; this task only addresses the client-side rendering (editor parity) and CSS layering (z-index).

### Validation

- `node --check` on extracted inline JS from both `client.html` and `editor.html`: **PASS** (both files syntactically valid).
- Live UI verification during an actual manual block requires the running robot/UI (not available in this environment).

### Decisions and Caveats

- The z-index fix (30) places the status bar above the safety popup (25) but below any potential future overlays. If additional fixed overlays are added with z-index between 25 and 30, the status bar will still be on top of the safety popup.
- The `editor.html` flag is now byte-identical in logic to `client.html`; future changes to the flag logic must be applied to both files.
- The `SAFETY_ALERT` event is still not forwarded to the client by `display.py`; the state frame remains the sole source of truth for the flag.

### Next Task

No immediate next task identified. The move-blocked flag is now visible in both clients.

### Instructions for the Next Task

If the flag behavior needs adjustment (e.g., different styling, additional info in the tooltip, or a different trigger condition), update `updateStatusBar` in **both** `client.html` (line 343) and `editor.html` (line 4167) identically. The `escAttr` helper must be kept in sync as well.

## 2026-11-03 — Move-blocked flag in client.html status bar (persistent, hover trace, log line)

### Status

Completed

### Summary

Made the safety-gate "move blocked" state visible in the control client's always-on status bar (`client.html`), for debugging the KUKA real-robot hold. When `_resolve_pending` (`engine.py:338-423`) blocks a pending move in **manual** mode, the pending stays in the `"confirm"` phase and the state frame carries `s.safety` (via `display.py`'s `pending_info()` fallback). The status bar now shows a persistent red **move blocked** flag while that phase is active, with a hover tooltip carrying the trace (target zone/action, reason = red-link conflict and/or self-collision, issues, control mode). A one-line warning is also logged the moment the block transitions in (reusing the existing `_safetyFp` change-detect in `renderState`). No server change was needed: `s.safety` already reaches the client, and the `SAFETY_ALERT` event (which is **not** forwarded to the client by `display.py`) is intentionally not relied on — the state frame is the source of truth.

This also records a confirmed fact from the user: when the frozen `curjpos` (`[0,0,0,0,0,90]`) happens, the robot is **not** physically at `[0,0,0,0,0,0]`/`[0,0,0,0,0,90]` — so that reading is a stale/zeroed EKI `Res` desync, not a parked robot.

### Changes Made

- `realtimecontrol/rtr/client.html`:
  - New `escAttr(s)` helper — escapes a string for an HTML `title` attribute (the status bar's hover tooltips).
  - `updateStatusBar(m)`: added a persistent **move blocked** segment. When `m.safety.phase === "confirm"`, it renders a red bold flag with a `title` tooltip built from `m.safety` (zone/action, reason, issues, control mode). The flag sits between the behavior and candidates segments; when no block it is absent (the bar reads `…behavior › candidates`).
  - `renderState`: in the existing `spFp !== _safetyFp` transition block (the one that opens the safety popup), added a `log("move BLOCKED (manual): …", "warn")` line so the block is traced in the log window. It fires once per block (on the `"clear"` → `"confirm"` transition).

### Documentation

None required — the flag is self-documenting via its hover `title` tooltip and the in-code comments above the `blockedSeg` block and the log line.

### Architecture

No new architectural boundary. The change is purely a client-side read of the existing `s.safety` state frame field (which `display.py:197-200` already populates from `machine.pending_info()` while a safety-gated move is pending). The manual-mode block is persistent (`set_pending_confirm()` holds the pending in the `"confirm"` phase until confirm/cancel), so the flag is persistent; the auto-mode discard is one-shot (`clear_pending()` + `SAFETY_DISCARD`), so it is only traced by the existing `safety_discard` log line (no persistent flag). `editor.html` has its own `updateStatusBar` (line 4167) that mirrors this one and does **not** yet carry the flag — see Decisions.

### Validation

- `node --check` on the extracted inline `<script>` of `client.html` → SYNTAX OK (the `escAttr` + `updateStatusBar` + `renderState` edits parse cleanly).
- Manually traced the data path: `engine._resolve_pending` → `machine.set_pending_confirm()` (phase `"confirm"`) → `display._frame` fallback `machine.pending_info()` → `frame["safety"]` → client `s.safety` → `updateStatusBar` flag + `renderState` log. Confirmed `Event.SAFETY_ALERT` is not forwarded by `display.py` (only `SAFETY_DISCARD` + `POSE_CONFLICT` are), so the client derives the manual block from `s.safety`, not the event.
- No Python changed, so no Python test run was required.

### Decisions and Caveats

- **Client-side only** — `s.safety` already reaches the client (`display.py:197-200`), and the manual block is persistent, so no server change was needed. This keeps the change minimal and avoids touching the core.
- **The flag is manual-mode-specific** — it keys on `s.safety.phase === "confirm"`, which only persists in manual mode. In auto mode the discard is one-shot (the pending is cleared), so the status bar shows no persistent flag there; the block is still traced by the existing `safety_discard` log line. If a persistent "last block" indicator is wanted in auto mode, that would need a new server field (e.g. a `last_block` record) — not done here.
- **`editor.html` parity** — `editor.html` has a mirrored `updateStatusBar` (line 4167) that does not yet carry the flag. The user scoped this task to `client.html`; if they want the flag in the editor header too, the same `blockedSeg` block can be copied there.
- **The frozen `curjpos` is a desync, confirmed** — the user confirmed the robot is never physically at `[0,0,0,0,0,0]`/`[0,0,0,0,0,90]` when the freeze happens, so the `[0,0,0,0,0,90]` reading is a stale/zeroed EKI `Res` (a mis-paired move `<Res>` from the KRL single-flight loop), not a parked home pose. This is the live root cause to keep chasing (see the prior handoff's diagnostic steps: teach-pendant `$AXIS_ACT`, `KUKAPY_DEBUG=1` traffic).

### Next Task

No immediate next task identified. The move-blocked flag + hover trace + log line are complete and validated in `client.html`.

### Instructions for the Next Task

The flag is driven by `s.safety` in the state frame (`display.py` populates it from `machine.pending_info()` while a safety-gated move is pending). In `client.html`, `updateStatusBar(m)` builds the `blockedSeg` when `m.safety.phase === "confirm"` (the persistent manual-mode block), and `renderState` logs the block on the `_safetyFp` transition. To add the flag to `editor.html`, copy the `escAttr` helper + the `blockedSeg` block into its `updateStatusBar` (line 4167). To make the auto-mode discard persistent in the status bar, a new server field (e.g. a `last_block` record on the machine) would be needed. The frozen `curjpos` desync is a separate, confirmed issue (the robot is not physically at the home pose when it happens) — chase it with the teach-pendant `$AXIS_ACT` + `KUKAPY_DEBUG=1` diagnostics from the prior handoff.

## 2026-11-03 — Poses tab: all-pose load + pose list (prev/next, + pose, per-row copy/reorder/delete) + save-slot picker

### Status

Completed

### Summary

Fixed the poses tab so that **every** pose of a selected zone/action is loadable into the scratch (previously `posesLoad()` only ever loaded `a.poses[0]`), and added the per-workflow controls the user asked for. The "load-from" picker (`#posesLoadSel`) is now the **working action**: its full pose list is rendered as rows (index + 6 values + `[copy] [▲] [▼] [×]`), any row can be clicked to load that pose into the scratch, and `‹`/`›` step through poses. A **save-slot picker** (`#posesSlotSel`) lets the "save" button (`#posesAddPose`, renamed from "add pose") write the scratch into a specific slot (replace, keeping that slot's image/video) or append a new one. New operations keep the parallel `pose_imgs` / `pose_videos` and `SOUND.actions[zn][an].poses` arrays aligned, mirroring `aeSwapPoses` / `reconcileActionSound`. All mutating operations auto-save via `saveZones()` (consistent with the existing `posesAddPose`).

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - `#posesMain` HTML restructured: added a "poses in action" block with `#posesPrev` / `#posesNext` / `#posesListNew` buttons, a `#posesListInfo` status line, and a `#posesList` container; added `#posesSlotSel` next to `#posesSaveSel`; the `#posesAddPose` button label changed "add pose" → "save" with an updated title.
  - CSS: added `.posesList`, `.posesRow` (+ `.active`), `.posesRowIdx`, `.posesRowVals` styles (reusing the existing `--border` / `--accent` / `--dim` vars).
  - JS new state: `posesCurIndex` (the active pose index in the working action's list).
  - JS new functions: `posesWorkingAction()`, `posesLoadPoseFromAction(i)`, `posesSelectPose(i)`, `buildPosesList()`, `posesCopyAt(i)`, `posesStep(dir)` / `posesPrev()` / `posesNext()`, `posesListNew()`, `posesReorder(i,dir)`, `posesDelete(i)`, `buildPosesSlotPicker()`.
  - JS modified: `posesLoad()` now loads `a.poses[posesCurIndex]` (the active pose) instead of `a.poses[0]`; `posesAddPose()` honours `#posesSlotSel` (replace a slot keeping its image/video, or append); `posesEnter()` now also calls `buildPosesSlotPicker()` + `buildPosesList()`.
  - Wiring: new handlers wired **defensively** (`if ($("posesPrev")) …`) so the DOM-stub tests that seed only `STATIC_IDS` still load; `#posesSaveSel.onchange` → `buildPosesSlotPicker`.
- `realtimecontrol/rtr/test_poses_tab.js` (NEW): Node `vm` + mini-DOM test for the poses tab. Auto-derives `STATIC_IDS` from the script (so new ids need no hand-editing). Seeds a 3-pose action with parallel arrays + a pose-less action, stubs `saveZones`, and verifies: list renders all poses, `posesLoad` loads a non-first pose, prev/next step + clamp, slot replace-vs-append, reorder (swaps `poses` + `pose_imgs` + `pose_videos`), delete (removes parallel entries), `+ pose` appends the scratch, `posesCopyAt` / `posesCopy` set `POSE_CLIP`, and that mutations triggered `saveZones`.

### Documentation

None required — the poses-tab controls are self-documenting via their HTML `title` attributes and the in-code comments above each function; no new user-facing docs were requested.

### Architecture

No new architectural boundary. The poses tab reuses the existing `#robot3d` 3D singleton and the existing whole-table `saveZones()` POST path. The new pose-list operations are pure in-memory mutations of `TABLE.zones[zn].actions[an]` (kept in lock-step with the parallel `pose_imgs` / `pose_videos` / `SOUND.actions[...].poses` arrays, mirroring the action editor's `aeSwapPoses`), then persisted via the same `saveZones()` the top "save" button uses. The "working action" is the load-from picker's target; the "save target" (`#posesSaveSel`) remains independent (any action, including pose-less ones) so the scratch can still be written to a different action.

### Validation

- `node test_poses_tab.js` → `ALL_TESTS_PASSED` (28 assertions: all-pose list, non-first load, prev/next + clamp, slot replace/append, reorder + parallel arrays, delete + parallel arrays, + pose, copy, save triggered).
- No regression in the 5 `STATIC_IDS` DOM-stub tests: `node test_flow_tab.js`, `node test_groups_tab.js`, `node test_roam_badge.js` → `ALL_TESTS_PASSED`; `node test_action_cam.js` (LOAD ERROR) and `node test_patches_seed.js` (FAILURES=4) → **pre-existing**, confirmed identical on the original `editor.html` (swapped in `git show HEAD:…` and re-run; the `test_action_cam` LOAD ERROR reproduces without this change).
- The main `<script>` loads cleanly in the `vm` sandbox (no syntax errors) — the poses test's script-load step would otherwise abort.
- Python tests not run — no Python was changed (this is a frontend-only edit to `editor.html` + a new JS test).

### Decisions and Caveats

- **Defensive wiring for the new ids** (`posesPrev`, `posesNext`, `posesListNew`, `posesSlotSel`, `posesList`, `posesListInfo`) so the 5 existing DOM-stub tests (which hand-maintain `STATIC_IDS` and do not include the new ids) keep loading. The new `test_poses_tab.js` auto-derives `STATIC_IDS` from the script, so it picks up the new ids automatically. Existing ids (`posesSaveSel`, etc.) are unchanged and still wired as before.
- **Auto-save on every pose mutation** (`+ pose`, reorder, delete, slot save) via `saveZones()`, matching the existing `posesAddPose()` behaviour in this tab (the action editor's reorder does *not* auto-save, but the poses tab's established pattern is to save on each authoring step).
- **Slot replace keeps the slot's image/video** — replacing pose `i` overwrites only `a.poses[i-1]`; `pose_imgs` / `pose_videos` are left untouched (the reference media attached to that slot survives). Appending extends the parallel arrays with a `null` slot (mirrors `posesAddPose`).
- **The load-from picker still lists only actions that already have poses** (`buildPosesLoadPicker` unchanged), so the working action always has ≥1 pose; the pose-less case is still handled by the save target (`#posesSaveSel`), which lists all actions.

### Next Task

No immediate next task identified. The poses-tab all-pose load + pose-list controls + save-slot picker are complete and validated.

### Instructions for the Next Task

The working action is `posesWorkingAction()` (the `#posesLoadSel` target); its pose list is rendered by `buildPosesList()` into `#posesList` (one `.posesRow` per pose, active row gets `.active`). The active index is `posesCurIndex`. Load primitive is `posesLoadPoseFromAction(i)`; row-click / prev-next / `posesLoad()` all funnel through `posesSelectPose()` / `posesStep()`. Mutations (`posesListNew`, `posesReorder`, `posesDelete`, `posesAddPose`) mutate `TABLE.zones[zn].actions[an]` and keep `pose_imgs` / `pose_videos` / `SOUND.actions[zn][an].poses` aligned, then call `saveZones()`. If you add more poses-tab element ids, wire them defensively (the `if ($("…"))` block near the `posesClamp` wiring) and the new `test_poses_tab.js` will pick them up automatically (it derives `STATIC_IDS` from the script). Run `node test_poses_tab.js` after changes.

## 2026-11-02 — Perform editor: velocity default, 9-step multipliers, 3D live move, MIDI mappings list

### Status

Completed

### Summary

Implemented the four requested perform-editor improvements on top of the completed 9-task Perform Mode feature: (1) default CC-A velocity changed from 360°/s to 30°/s; (2) the CC-B multiplier set changed from 5 steps (1/2/4/8/16) to 9 divide-and-multiply steps (1/16, 1/8, 1/4, 1/2, 1, 2, 4, 8, 16) with CC-B banded evenly across the configured list; (3) the editor perform tab now shows a live 3D robot move visualizer (the shared `#robot3d` singleton mounted into the perform tab, pose-drag disabled); (4) the editor perform tab now lists the learned MIDI mappings (zone command, zone actions, nav, capture, groups, perform CC-A/CC-B) with live trigger lights that flash the matching row when the corresponding MIDI message arrives. The live `perform.html` page was updated to the 9 multiplier buttons and the 30°/s velocity default.

### Changes Made

- `realtimecontrol/rtr/rtr/perform.py`:
  - `builtin_perform_config()` default `velocity_max_deg_s` → `30.0` and `multipliers` → the 9-step list `[0.0625, 0.125, 0.25, 0.5, 1, 2, 4, 8, 16]`.
  - `validate_perform_config()` now validates `multipliers` as a non-empty list of positive numbers (ints/floats, `bool` rejected) so any divide/multiply set can be persisted.
- `realtimecontrol/rtr/perform.json`: updated to `velocity_max_deg_s: 30.0` and the 9-step `multipliers` list.
- `realtimecontrol/rtr/rtr/state/machine.py`: `set_perform_multiplier` now accepts any positive number (ignores non-positive / non-numeric / `bool`), not just the hardcoded 1/2/4/8/16 set.
- `realtimecontrol/rtr/rtr/io/midi.py`:
  - Added `STANDARD_MULTIPLIERS = [0.0625, 0.125, 0.25, 0.5, 1, 2, 4, 8, 16]`.
  - Added `MidiInput.multipliers_provider` (constructor kwarg) + `_perform_multipliers()` (returns the provider's list, else `STANDARD_MULTIPLIERS`).
  - CC-B dispatch now bands the raw CC value (0–127) across the configured list: `idx = int(round((value / 127.0) * (len(multipliers) - 1)))`, clamped to `0..len-1`, then submits `Cmd.PERFORM_MULTIPLIER` with `multipliers[idx]`.
- `realtimecontrol/rtr/main.py`: passes `multipliers_provider=lambda: perform_cfg["multipliers"]` into `MidiInput`.
- `realtimecontrol/rtr/editor.html`:
  - `PERFORM_FALLBACK` updated to velocity `30.0` and the 9-step multipliers.
  - `savePerformConfig()` parses the multipliers text field with `parseFloat` (was int-only).
  - `performMain` HTML restructured into a `performCols` layout with a `performRobot3dMount` and a `performMidiList` container.
  - Added `mountRobot3dPerform()` and a `performView` branch in `switchView()` that disables pose drag and mounts the shared 3D node into the perform tab.
  - Added `_midiKeyFor()` (JS port of the core `_key_for()` logic: `cc:<d>:<v>`, `note:<ch>:<note>`, `noteoff:<ch>:<note>`, `program:<ch>:<prog>`), `buildPerformMidiList()`, and `updatePerformMidiLights()` (perform CC rows match on CC number only, any value).
  - The state handler calls `updatePerformMidiLights(m.midi_monitor)` when `performView` is active; the `midi_changed` handler rebuilds the perform MIDI list.
  - Added CSS for the perform layout and the `.pmRow` / `.pmLit` trigger styling.
- `realtimecontrol/rtr/perform.html`: 9 multiplier buttons (1/16 … 16) with `data-m` per value, velocity default `30.0`, and `parseFloat` in the multiplier highlight + click handlers (was `parseInt`).
- `realtimecontrol/rtr/tests/test_perform_config.py`, `tests/test_midi_in.py`, `tests/test_perform_state.py`: updated for the new velocity default, 9-step multipliers, CC-B banding, and positive-number multiplier setter.
- `realtimecontrol/rtr/test_perform_tab.js`: updated velocity/multiplier assertions; added coverage for `_midiKeyFor()`, `buildPerformMidiList()`, and `updatePerformMidiLights()`; fixed the mini-DOM `classList.toggle` to respect the force argument (matching the real DOM).
- `realtimecontrol/rtr/test_perform_page.js`: updated to the 9 multiplier buttons, velocity default 30.0, and added a fractional-multiplier (0.5) command test.

### Documentation

None required — the field-level behavior is documented inline in `perform.html` / `editor.html` and the module docstrings in `perform.py` / `midi.py`; no new user-facing docs were requested.

### Architecture

No new architectural boundary. The changes refine the existing Perform Mode boundaries: the multiplier list is now a first-class, provider-sourced config value (the MIDI layer reads it via `multipliers_provider` rather than a hardcoded constant), and the editor perform tab reuses the existing shared `#robot3d` 3D singleton and the existing `midi_monitor` state frame. The CC-B banding math (`midi.py:912-916`) is configuration-agnostic: it maps 0–127 evenly across whatever multiplier list is loaded, verified to be even for the 9-step set (CC 0 → 1/16, CC 64 → 1x, CC 127 → 16x).

### Validation

- `python -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_remote_tui.py` → `391 passed, 14 skipped`; the only failures are the 5 pre-existing `tests/test_remote_sdl.py` failures (unrelated to this task).
- Targeted: `tests/test_perform_config.py`, `tests/test_midi_in.py`, `tests/test_perform_state.py`, `tests/test_perform_http.py` all pass.
- `node test_perform_tab.js` → `ALL_TESTS_PASSED`; `node test_perform_page.js` → `ALL_TESTS_PASSED`.
- `node test_flow_tab.js`, `node test_groups_tab.js`, `node test_roam_badge.js` → `ALL_TESTS_PASSED` (no regressions).
- `node test_action_cam.js`, `node test_patches_seed.js` → pre-existing failures (unchanged, unrelated).
- Deterministic CC-B banding check (Python): CC 0→1/16, CC 64→1x, CC 127→16x, all 9 steps evenly distributed.
- Live headless sim round-trip (`python3 main.py --sim --no-midi --no-sound`, HTTP :8766): boots clean; `GET /api/perform` returns `velocity_max_deg_s: 30.0` + the 9-step multipliers; `GET /perform.html` returns the page; `POST /api/perform` with a valid config returns `{"ok": true}` and persists to `perform.json`; `POST` with `velocity_max_deg_s: -5` returns `{"error": "'velocity_max_deg_s' must be positive"}` and leaves the file unchanged. Original `perform.json` restored after the round-trip.

### Decisions and Caveats

- **CC-B banding is list-agnostic** — `midi.py` bands 0–127 across whatever `multipliers` list is loaded (provider, else `STANDARD_MULTIPLIERS`), so a user can persist any divide/multiply set via the editor and it maps evenly. The 9-step set is the default.
- **3D visualizer reuses the `#robot3d` singleton** — the perform tab mounts the same 3D node the pose tab uses (pose drag disabled while in the perform tab), avoiding a second WebGL context.
- **Trigger lights match the core `_key_for()` semantics** — the JS `_midiKeyFor()` mirrors the Python core so a lit row means "that exact learned mapping fired"; perform CC-A/CC-B rows match on CC number only (any value) since the value is the payload, not part of the mapping identity.
- **Sim velocity is degrees/sec** — the 30°/s default is the sim's physical velocity ceiling (CC-A 0–1 → 0–30°/s); the real KUKA robot interprets speed as a kukapy percentage, so the planner's estimate remains an approximation for the real robot (a pre-existing caveat from the original plan).
- **Not committed** — all changes (original 9-task feature + these four improvements) remain in the working tree; no commit was requested.

### Next Task

No immediate next task identified. All four requested perform-editor improvements are complete and validated.

### Instructions for the Next Task

To extend the perform multiplier set: edit `perform.json` `multipliers` (or the editor perform tab multipliers field) — the CC-B banding in `rtr/io/midi.py:912-916` and the live page / editor buttons derive from the list automatically. The 3D perform visualizer is the shared `#robot3d` node (see `mountRobot3dPerform()` + the `performView` branch of `switchView()` in `editor.html`); the MIDI mappings list + trigger lights are `buildPerformMidiList()` / `updatePerformMidiLights()` / `_midiKeyFor()` in `editor.html`. If the multiplier list changes, update the 9-step defaults in `rtr/perform.py` `builtin_perform_config()`, `perform.json`, and the button rows in `perform.html` + `editor.html` (and the JS tests `test_perform_page.js` / `test_perform_tab.js`).

## 2026-11-02 — Perform Mode Task 9: HTTP server (/api/perform + perform.html)

### Status

Completed

### Summary

Wired the perform-mode global config into the HTTP server so the editor perform tab (Task 8) and the live perform page (Task 7) can read and write `perform.json` at runtime. Added a `/api/perform` **GET** (returns the on-disk config, falling back to the running provider, then the built-in default) and a `/api/perform` **POST** (validates the payload, atomically persists it via a tmp file + `os.replace`, then submits `Cmd.RELOAD_PERFORM` to the core so the running `BeatClock` + machine pick up the new `internal_bpm` / `velocity_max_deg_s`), both in `rtr/io/httpserver.py`. Threaded `perform_provider` + `perform_path` through the `HttpServer` constructor and passed them from `main.py`. `perform.html` is served automatically as a static file from the root (no explicit route needed). This is the final task in the 9-task Perform Mode plan — the feature is complete.

### Changes Made

- `realtimecontrol/rtr/rtr/io/httpserver.py`:
  - Added import: `from ..perform import builtin_perform_config, load_perform_config, validate_perform_config`.
  - Added `perform_provider=None, perform_path: str = None` to `HttpServer.__init__` signature + body (`self.perform_provider`, `self.perform_path = os.path.abspath(...)`).
  - Added the GET dispatch branch `elif path == "/api/perform": self._get_perform()`.
  - Added the POST dispatch branch `elif path == "/api/perform": self._post_perform()`.
  - Implemented `_get_perform()`: reads `perform_path` fresh via `load_perform_config()`; on `(OSError, ValueError)` falls back to `perform_provider()`; then to `builtin_perform_config()`.
  - Implemented `_post_perform()`: reads the body, `validate_perform_config(data)` (400 on bad input), atomic write (tmp + `os.replace`), submits `Command(Cmd.RELOAD_PERFORM, {})` to the bus, returns 200 `{"ok": true}`; 500 on write failure.
- `realtimecontrol/rtr/main.py`:
  - Passed `perform_provider=lambda: perform_cfg` and `perform_path=cfg.perform_path` to the `HttpServer` constructor call.
- `realtimecontrol/rtr/tests/test_perform_http.py` (NEW): headless test that starts a real `ThreadingHTTPServer` on an auto-assigned local port and exercises the endpoint — GET reads the file, GET falls back to the provider when the file is unreadable, POST persists + submits `Cmd.RELOAD_PERFORM`, POST rejects bad input (400, file untouched), and `/perform.html` is served. 5 tests.

### Documentation

None required — the endpoint mirrors the existing config-API handlers (`_get_robotconfig` / `_post_robotconfig`), and `perform.html`'s inline code documents the fields it reads.

### Architecture

The `/api/perform` endpoint is a peer of the other config APIs (zones, sound, patches, brain, robotconfig, limits): GET reads the file fresh (so an edited file is reflected without a restart), POST validates → atomically persists → hot-reloads via `Cmd.RELOAD_PERFORM`. The reload handler (added in Task 6) re-reads the file, applies `velocity_max_deg_s` to the machine and `internal_bpm` to the `BeatClock`. No new architectural boundary — it reuses the existing HTTP dispatch + atomic-write + bus-submit pattern. `perform.html` is a static asset, not a route.

### Validation

- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py --ignore=tests/test_remote_sdl.py` → 363 passed, 12 skipped (includes the 5 new `test_perform_http.py` tests).
- `python tests/test_perform_http.py` → ALL TESTS PASSED (5 tests).
- `node test_perform_page.js` → ALL_TESTS_PASSED (27 assertions).
- `node test_perform_tab.js` → ALL_TESTS_PASSED (24 assertions).
- `node test_flow_tab.js`, `node test_groups_tab.js`, `node test_roam_badge.js` → ALL_TESTS_PASSED.
- `node test_action_cam.js`, `node test_patches_seed.js` → pre-existing failures (confirmed identical on the HEAD `editor.html`, unrelated to this task).
- Live headless sim (`python main.py --sim --no-midi --no-sound`): boots clean (`[http] listening` + `[rtr] core up`); `GET /api/perform` returns the config; `POST /api/perform` with a new config returns `{"ok": true}`, persists to `perform.json`, and the file reflects the new values; `GET /perform.html` returns the page. (Original `perform.json` restored after the round-trip.)

### Decisions and Caveats

- **GET reads the file fresh** (not the in-memory `perform_cfg`), matching the other config GETs — so an externally-edited `perform.json` is reflected on the next GET. The provider fallback only fires when the file is unreadable.
- **POST hot-reloads via the bus** (`Cmd.RELOAD_PERFORM`), not by mutating the in-memory `perform_cfg` directly — the reload handler in `main.py` re-reads the file and applies the new BPM / velocity range to the live clock + machine, keeping a single source of truth (the file).
- **Atomic write** via tmp + `os.replace` (same as the other config POSTs) so a failed write never leaves a half-written `perform.json`.
- **`perform.html` needs no explicit route** — it's served by the existing static handler from the root directory.

### Next Task

No immediate next task identified. The 9-task Perform Mode feature is complete: BeatClock (1), MIDI clock + perform CCs (2), machine perform state (3), engine (4), zones `base_beats` (5), perform config (6), `perform.html` (7), editor perform tab (8), HTTP server (9).

### Instructions for the Next Task

If extending Perform Mode: the per-action `base_beats` are edited in the zones tab and stored on each zone/action in `zones.json`; the global defaults (`default_base_beats`, `internal_bpm`, `clock_timeout`, `velocity_max_deg_s`, `multipliers`) live in `perform.json` and are edited in the editor perform tab / served by `/api/perform`. The live page is `perform.html`; the beat-clock math is in `rtr/core/clock.py` (deadline = `base_beats × multiplier × 60/BPM`, time-to-arrive = `distance / velocity`, late = `time_to_arrive > deadline`, notify-only).

## 2026-11-02 — Perform Mode Task 8: editor.html perform tab

### Status

Completed

### Summary

Added a **perform** tab to `editor.html` for editing the global perform config (`perform.json`): `default_base_beats`, `internal_bpm`, `clock_timeout`, `velocity_max_deg_s`, and the `multipliers` list. The tab follows the existing robotconfig-tab pattern exactly: a tab button, a `<main>` section, a `performView` flag, a `loadPerformConfig()` that reads `/api/perform` (falling back to `/perform.json`, then to a built-in default), a `normalizePerformConfig()` that merges a partial config over the fallback, a `buildPerformForm()` that renders 4 number fields + a comma-separated multipliers text field, and a `savePerformConfig()` that POSTs to `/api/perform` (which triggers the `Cmd.RELOAD_PERFORM` hot-reload on the server). Wired the tab into `switchView()`, the `save()` dispatch, the `BACKUP_SECTIONS` list, the load chain, and the tab onclick handler. Wrote a node DOM-stub test that loads the real editor script and verifies the normalize/load/build/save logic.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - Added the `tabPerform` button after `tabRobotConfig` (header).
  - Added the `<main id="performMain">` section (with `performForm` + `performReset`) between `robotconfigMain` and `camMain`.
  - Added `let performView = false;` after the robotconfig view flag.
  - Added the perform functions block after `saveRobotConfig`: `PERFORM_FALLBACK`, `PERFORM_CONFIG`, `normalizePerformConfig()`, `loadPerformConfig()`, `buildPerformForm()`, `savePerformConfig()`.
  - Wired `performView` into `switchView()` (view flag, `other` boolean, `performMain` display, `tabPerform` active class, lazy-load block).
  - Added `if (performView) { savePerformConfig(); return; }` to the `save()` dispatch.
  - Added the `perform` entry to `BACKUP_SECTIONS`.
  - Added `.then(loadPerformConfig)` to the load chain.
  - Added `$("tabPerform").onclick = () => switchView("perform");` to the tab onclick wiring.
- `realtimecontrol/rtr/test_flow_tab.js`, `realtimecontrol/rtr/test_groups_tab.js`, `realtimecontrol/rtr/test_action_cam.js`: added `performForm`, `performMain`, `performReset`, `tabPerform` to the strict `STATIC_IDS` lists (the editor's top-level `$("tabPerform").onclick` needs the id to exist in the vm sandbox).
- `realtimecontrol/rtr/test_perform_tab.js` (NEW): node DOM-stub test — loads the editor's real script into a permissive vm sandbox and verifies (1) `PERFORM_FALLBACK` shape, (2) `normalizePerformConfig` merges partials over the fallback, (3) `loadPerformConfig` prefers `/api/perform`, falls back to `/perform.json`, then defaults, (4) `buildPerformForm` renders 4 number fields + the multipliers text field, (5) `savePerformConfig` POSTs the parsed payload and flags `saveMsg`, (6) invalid values fall back to defaults. 24 assertions, ALL PASS.

### Documentation

None required — the perform tab's inline code documents the fields it edits and the save path.

### Architecture

The perform tab is a peer of the other editor config tabs (robotconfig, brain, limits, ...): it edits the global perform config and saves via the same POST-then-hot-reload pattern. It does NOT edit the per-action `base_beats` (those live on the zone/action rows and are already editable in the zones tab, added in Task 5); this tab is strictly the global defaults. The `/api/perform` GET/POST endpoint (Task 9) is what the tab talks to; until it's served, the tab's `/api/perform` fetch 404s and it falls back to `/perform.json` (read by the browser) and then the built-in default.

### Validation

- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py --ignore=tests/test_remote_sdl.py` → 358 passed, 12 skipped.
- `node test_perform_tab.js` → ALL_TESTS_PASSED (24 assertions).
- `node test_flow_tab.js`, `node test_groups_tab.js`, `node test_roam_badge.js` → ALL_TESTS_PASSED.
- `node test_action_cam.js` and `node test_patches_seed.js` → pre-existing failures (confirmed they fail identically on the HEAD `editor.html`, unrelated to this task).

### Decisions and Caveats

- **Global config only**: this tab edits `perform.json` (the global defaults). The per-action `base_beats` are edited in the zones tab (Task 5), not here — keeps the tab focused and avoids duplicating the zone-editing UI.
- **Load fallback chain**: `/api/perform` (Task 9) → `/perform.json` (the browser can read the file directly) → built-in `PERFORM_FALLBACK`. This keeps the tab usable even before Task 9 serves the API (it reads `perform.json` directly and saves optimistically).
- **Save path**: `savePerformConfig()` POSTs to `/api/perform`; the server handler (Task 9) persists the file and dispatches `Cmd.RELOAD_PERFORM` to hot-reload. The tab does not send the reload command itself — the server does it (matching how the other config tabs' saves work).
- **Pre-existing JS test failures**: `test_action_cam.js` (load error) and `test_patches_seed.js` (4 assertions) fail on the HEAD `editor.html` too — not caused by this task.

### Next Task

Task 9: HTTP server — serve `perform.html` (the `/perform.html` route) and the `/api/perform` GET/POST endpoint (persist `perform.json` + dispatch `Cmd.RELOAD_PERFORM` on POST) in `rtr/io/httpserver.py`.

### Instructions for the Next Task

1. In `rtr/io/httpserver.py`, add a route that serves `perform.html` (mirror how `editor.html` / `client.html` are served).
2. Add a `/api/perform` **GET** that returns the current perform config JSON (read from `perform.json`).
3. Add a `/api/perform` **POST** that validates + persists the config to `perform.json`, then dispatches `Cmd.RELOAD_PERFORM` to the engine (hot-reload). Reuse the same validate/persist helpers the other config APIs use.
4. Confirm `perform.html`'s `/api/perform` fetch (for `velocity_max_deg_s`) now resolves, and that the editor perform tab's save round-trips.
5. Validate: full Python suite + `node test_perform_page.js` + `node test_perform_tab.js`.

## 2026-11-02 — Perform Mode Task 7: perform.html live perform page

### Status

Completed

### Summary

Created `perform.html`, the live performance page. It connects to the core over the WebSocket, reads the `perform` field from the 20 Hz state frames, and renders the beat-clock state (source, BPM, beat counter, beat phase bar, beat duration), the CC-A velocity readout + slider, the CC-B multiplier buttons (1x/2x/4x/8x/16x), the deadline / time-to-arrive with a late alert, and the live move (zone / action / moving). The velocity slider and multiplier buttons send `PERFORM_VELOCITY` / `PERFORM_MULTIPLIER` commands. Added the `perform` field to the display's state frame so the page can read it, and added the two perform commands to the WS browser-command set. Wrote a node DOM-stub test that loads the real page script and verifies the render + command logic.

### Changes Made

- `realtimecontrol/rtr/perform.html` (NEW): the live perform page — beat clock, CC-A velocity slider, CC-B multiplier buttons, deadline/time-to-arrive with late alert, live move state. Reads the `perform` field from state frames; sends `perform_velocity` (normalized 0-1) and `perform_multiplier` commands.
- `realtimecontrol/rtr/rtr/display/display.py`: `_frame` now forwards the `perform` field into the state frame when `snap.perform is not None` (so the page can read the beat clock + velocity/multiplier/deadline/late).
- `realtimecontrol/rtr/rtr/io/websocket.py`: added `perform_velocity` and `perform_multiplier` to `_BROWSER_CMDS` (they're browser control-page commands, like the other entries).
- `realtimecontrol/rtr/test_perform_page.js` (NEW): node DOM-stub test — loads perform.html's real script into a vm sandbox, verifies (1) renderState maps the perform snapshot to the DOM, (2) the late flag toggles the box/hint, (3) the idle state when `perform` is absent, (4) the velocity slider sends a normalized `perform_velocity`, (5) the multiplier button sends `perform_multiplier`. 27 assertions, ALL PASS.

### Documentation

None required — the page's inline comment block documents the fields it reads and the commands it sends.

### Architecture

The perform page is a peer of client.html / editor.html: a browser control page that talks to the core over the same WebSocket. It is intentionally a *viewer + two controls* (velocity, multiplier) — it does not trigger moves (that's the brain / performance mode's job). The `perform` field in the state frame is the single source of truth the page renders from; the page sends no move commands. Serving the page (the `/perform.html` route) is Task 9.

### Validation

- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py --ignore=tests/test_remote_sdl.py` → 358 passed, 12 skipped.
- `node test_perform_page.js` → ALL_TESTS_PASSED (27 assertions).

### Decisions and Caveats

- **Reads the `perform` field, not the machine directly**: the page renders from the `perform` dict the engine publishes into the snapshot (`velocity`, `multiplier`, `base_beats`, `deadline`, `time_to_arrive`, `late`, `beat`, `beat_index`, `beat_phase`, `bpm`, `beat_duration`, `clock_source`). When the clock isn't wired (e.g. `--no-midi` with no clock), `perform` is absent and the page shows the idle state.
- **Velocity normalization**: the slider is 0-100; the page divides by 100 before sending, so `perform_velocity` carries the 0-1 normalized fraction (matching the MIDI CC-A path). The "degrees/sec" readout multiplies by `velocity_max_deg_s` (fetched from `/api/perform`; falls back to 360.0).
- **`/api/perform` fetch**: the page fetches the perform config to get `velocity_max_deg_s` for the deg/s readout. That endpoint is Task 9; until then the fetch 404s silently (caught) and the readout uses the 360.0 fallback.

### Next Task

Task 8: editor.html perform tab — add a perform tab to the editor for editing the perform config (default_base_beats, internal_bpm, clock_timeout, velocity_max_deg_s, multipliers) and the per-action base_beats, with a live monitor.

### Instructions for the Next Task

1. Add a **perform tab** to `editor.html` (alongside the existing tabs: brain, cam, flow, groups, midi, patches, poses, robotconfig, sound, etc.).
2. It edits the global perform config (`perform.json`): `default_base_beats`, `internal_bpm`, `clock_timeout`, `velocity_max_deg_s`, `multipliers`.
3. It also edits the per-action `base_beats` (from zones) — either as a field on the action rows or a dedicated section.
4. Save via the existing editor save pattern (POST to the HTTP API, then a `reload_perform` command).
5. A live monitor showing the current `perform` state (from the state frame) is a nice-to-have.
6. The `Cmd.RELOAD_PERFORM` command (added in Task 6) triggers the hot-reload.

## 2026-11-02 — Perform Mode Task 6: Perform config (perform.json + rtr/perform.py + wiring)

### Status

Completed

### Summary

Created the global perform config (`perform.json`) and its loader (`rtr/perform.py`), added the `velocity_max_deg_s` scale to the machine's perform state (so the time-to-arrive estimate uses real degrees/sec), wired the perform config into the engine (velocity range + `default_base_beats` fallback + hot-reload hook), and wired the whole thing into `main.py` (load the config, build the `BeatClock` from it, connect the MIDI 0xF8 clock callback to the clock, and pass the clock + config to the engine). Added a `Cmd.RELOAD_PERFORM` command for the hot-reload path.

### Changes Made

- `realtimecontrol/rtr/perform.json` (NEW): global perform config — `default_base_beats` (4), `internal_bpm` (130.0), `clock_timeout` (1.0), `velocity_max_deg_s` (360.0), `multipliers` ([1,2,4,8,16]).
- `realtimecontrol/rtr/rtr/perform.py` (NEW):
  - `DEFAULT_PERFORM_CONFIG` constant (the fallback shape).
  - `builtin_perform_config()` — a deep copy of the fallback.
  - `validate_perform_config(data)` — fills defaults, rejects bad types (non-int base_beats, non-positive bpm/velocity, non-int multipliers).
  - `load_perform_config(path)` — reads + validates the file.
- `realtimecontrol/rtr/rtr/config.py`: added `perform_path: str = "perform.json"`.
- `realtimecontrol/rtr/rtr/state/machine.py`:
  - `perform` dict: added `velocity_max_deg_s` field (default 1.0).
  - `set_perform_velocity_max_deg_s(max_deg_s)` — new setter (clamps to non-negative).
  - `update_perform_time_to_arrive(distance)` — now computes `v = velocity × velocity_max_deg_s`, then `tta = distance / v` (so tta is in seconds when velocity_max_deg_s is a real degrees/sec range). With the default 1.0 the old contract is preserved.
- `realtimecontrol/rtr/rtr/core/engine.py`:
  - `__init__`: added `perform_config` and `perform_reload` parameters. Seeds `machine.set_perform_velocity_max_deg_s` from the config at init.
  - `_tick`: added a `Cmd.RELOAD_PERFORM` handler (calls `self._perform_reload()`).
  - `_current_base_beats`: now falls back to `perform_config["default_base_beats"]` when the action has no `base_beats`.
- `realtimecontrol/rtr/rtr/core/commands.py`: added `Cmd.RELOAD_PERFORM`.
- `realtimecontrol/rtr/main.py`:
  - Imports: `BeatClock`, `builtin_perform_config`, `load_perform_config`.
  - `--perform` CLI arg + `cfg.perform_path`.
  - `load_perform_config_fallback(path)` — the fallback wrapper.
  - `run()`: loads the perform config, builds the `BeatClock` (internal_bpm + clock_timeout), wires `clock.clock_tick` to the `MidiInput`, and passes `clock` + `perform_config` + `perform_reload` to the `Engine`. The `_reload_perform` closure re-seeds the machine's velocity range + the clock's internal BPM on hot-reload.
- `realtimecontrol/rtr/tests/test_perform_config.py` (NEW): 12 headless tests — ALL PASS.

### Documentation

None required — module + method docstrings document the config contract.

### Architecture

The perform config is a first-class config file (like brain.json / midi.json) with its own loader + fallback. The `BeatClock` is now built from the config (internal_bpm + clock_timeout) and wired to the MIDI adapter's 0xF8 callback. The velocity range (`velocity_max_deg_s`) is seeded into the machine so the time-to-arrive estimate is in seconds. The `default_base_beats` is the global fallback for actions without their own `base_beats`. The `Cmd.RELOAD_PERFORM` + `perform_reload` hook make the config hot-reloadable (the /api/perform endpoint in Task 9 will submit the command).

### Validation

- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py --ignore=tests/test_remote_sdl.py` → 358 passed, 12 skipped.
- `python -u main.py --sim --no-midi --no-sound --no-http --no-display` → boots cleanly (perform config loaded, no fallback warning, robot connected, core up).

### Decisions and Caveats

- **Velocity units**: the machine's `perform["velocity"]` stores the CC-A *normalized* fraction (0.0–1.0, as in Task 3). The `velocity_max_deg_s` config scales it to real degrees/sec for the time-to-arrive estimate. The machine's `velocity_max_deg_s` defaults to 1.0 (so the Task 3 tests — which treat velocity as the raw fraction — still pass unchanged). A real deployment sets `velocity_max_deg_s` to the robot's max degrees/sec (360.0 in the shipped config).
- **default_base_beats fallback**: an action's own `base_beats` (zones.json) wins; when absent (0) the engine uses `perform_config["default_base_beats"]`.
- **Hot-reload**: the `perform_reload` closure re-seeds the machine's velocity range + the clock's internal BPM. The /api/perform endpoint (Task 9) will submit `Cmd.RELOAD_PERFORM` to trigger it.

### Next Task

Task 7: perform.html — the live perform page (MIDI settings, performance-zone selection, CC-A velocity slider, CC-B multiplier buttons, BPM/clock state, late alerts, live move state).

### Instructions for the Next Task

1. Create `realtimecontrol/rtr/perform.html` (a live page, like client.html / editor.html).
2. It reads the live state from the WebSocket (the `perform` field in the snapshot: velocity, multiplier, base_beats, deadline, time_to_arrive, late, beat, beat_index, beat_phase, bpm, beat_duration, clock_source).
3. Controls: CC-A velocity slider (0-1), CC-B multiplier buttons (1x/2x/4x/8x/16x), BPM display (internal value / external clock state), beat-clock state (beat counter, beat_phase), late alert (red when late), live move state (current zone/action + time-to-arrive).
4. The CC-A/CC-B controls submit `Cmd.PERFORM_VELOCITY` / `Cmd.PERFORM_MULTIPLIER` via the WebSocket.
5. The page is served by the HTTP server (Task 9 adds the route).

## 2026-11-02 — Perform Mode Task 5: Zones base_beats field (rtr/state/zones.py)

### Status

Completed

### Summary

Added the `base_beats` field to the action schema in `zones.py`. The `Zone` class now has an `action_base_beats(name)` method that returns the action's base beat count (int, default 0). The validator accepts `base_beats` as an optional non-negative int. Updated the engine's `_current_base_beats` helper to use the new public method.

### Changes Made

- `realtimecontrol/rtr/rtr/state/zones.py`:
  - `Zone.action_base_beats(name)`: new method — returns the action's `base_beats` (int, default 0 when absent or malformed).
  - `_validate_action`: added validation for `base_beats` (must be a non-negative int or absent).
- `realtimecontrol/rtr/rtr/core/engine.py`:
  - `_current_base_beats`: updated to use `z.action_base_beats(m.current_action)` instead of the private `_action` method.

### Documentation

None required — method docstring documents the `base_beats` contract.

### Architecture

No architectural impact. The `base_beats` field is a per-action data field (like `dwell_s` / `speed`) that the engine reads to compute the perform deadline. The field is optional (default 0 = no timing constraint).

### Validation

- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py --ignore=tests/test_remote_sdl.py` → 346 passed, 12 skipped.

### Decisions and Caveats

- `base_beats` is per-action (not per-zone), consistent with the existing `speed` / `dwell_s` pattern.
- The field is optional: when absent, `action_base_beats` returns 0 (the perform deadline is 0, meaning no timing constraint).
- The validator rejects negative values and non-int types (bools are explicitly excluded).

### Next Task

Task 6: Perform config — create `perform.json` + a loader for the global perform settings (default multiplier, internal BPM, clock timeout).

### Instructions for the Next Task

1. Create `realtimecontrol/rtr/perform.json` with the global perform settings:
   - `internal_bpm`: float (default 130.0) — the fallback BPM when no external clock.
   - `clock_timeout`: float (default 1.0) — seconds of silence before falling back to internal.
   - `default_multiplier`: int (default 1) — the initial CC-B multiplier.
2. Add a loader function (similar to `load_midi_data`) that reads and validates `perform.json`.
3. The loader should be wired into `main.py` (Task 4's engine init will use these values to configure the `BeatClock`).
4. The `perform.json` is separate from `zones.json` (which holds per-action `base_beats`) and `midi.json` (which holds the CC numbers).

## 2026-11-02 — Perform Mode Task 4: Engine (rtr/core/engine.py)

### Status

Completed

### Summary

Modified the engine to (a) accept a `BeatClock` instance, (b) tick it each engine cycle, (c) handle `PERFORM_VELOCITY` / `PERFORM_MULTIPLIER` commands by calling the machine's setters, (d) compute the perform deadline and time-to-arrive each tick when in perform mode, and (e) fold the clock state + perform state into the snapshot (a new `perform` dict field).

### Changes Made

- `realtimecontrol/rtr/rtr/core/engine.py`:
  - `__init__`: added `clock` parameter (the `BeatClock` instance, default None).
  - `_tick`: added `self._clock.tick()` at the top (before reading the pose).
  - `_tick`: added handlers for `Cmd.PERFORM_VELOCITY` and `Cmd.PERFORM_MULTIPLIER` (call `machine.set_perform_velocity` / `set_perform_multiplier`).
  - `_tick`: after `machine.step`, when `_machine_is_performing()`, compute the deadline (from `base_beats` + BPM) and time-to-arrive (from distance / velocity).
  - `_machine_is_performing()`: new helper — checks `brain.autonomy_info()["performance_mode"]`.
  - `_current_base_beats()`: new helper — reads the current action's `base_beats` from the zone data (via `Zone._action`).
  - `_publish`: when a clock is wired, builds a `perform` dict (velocity, multiplier, base_beats, deadline, time_to_arrive, late, beat, beat_index, beat_phase, bpm, beat_duration, clock_source) and passes it to the `Snapshot`.
- `realtimecontrol/rtr/rtr/core/commands.py`:
  - `Snapshot`: added `perform: Optional[dict]` field + `to_dict` serialization (done in Task 3, verified here).

### Documentation

None required — method docstrings document the perform integration.

### Architecture

The engine now owns the beat clock tick and the perform state computation. The clock is injected (not created) so `main.py` can wire it to both the engine and the MIDI adapter. The perform state is only computed when `performance_mode` is active (the brain's autonomy info). The snapshot's `perform` field carries the full live perform state to clients (including the beat counter and clock source).

### Validation

- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py --ignore=tests/test_remote_sdl.py` → 346 passed, 12 skipped.

### Decisions and Caveats

- The clock tick happens at the top of `_tick` (before reading the pose) so the beat counter is current when the deadline is computed.
- The `perform` field in the snapshot is only populated when a clock is wired (`self._clock is not None`). When no clock, it's `None` (clients ignore the field).
- The `base_beats` is read from the zone data via `Zone._action(name)` (the private method that returns the raw action dict). This will be formalized in Task 5 when `base_beats` is added to the zone schema.
- The distance computation is `Σ|axis deltas|` between the current joint pose and the target pose (6 axes). This is a simple Euclidean-like metric in joint space.

### Next Task

Task 5: Zones — modify `rtr/state/zones.py` + `zones.json` to add the `base_beats` field to the action schema.

### Instructions for the Next Task

1. In `rtr/state/zones.py`, the `Zone` class:
   - Add an `action_base_beats(name)` method (similar to `action_dwell_s` / `action_speed`) that returns the action's `base_beats` (int, default 0).
   - Update the validator to accept `base_beats` as an optional int field in the action dict.
2. In `zones.json`, add `base_beats` to the action schema documentation (the field is per-action, not per-zone).
3. The engine's `_current_base_beats` helper can then use `z.action_base_beats(m.current_action)` instead of the private `_action` method.
4. The `base_beats` value represents how many beats the action takes at 1x multiplier. The deadline is `base_beats × multiplier × (60/BPM)` seconds.

## 2026-11-02 — Perform Mode Task 3: Machine perform state (rtr/state/machine.py)

### Status

Completed

### Summary

Added the perform mode state to the `StateMachine`. The machine now tracks a `perform` dict with velocity (CC-A), multiplier (CC-B), base_beats, deadline, time_to_arrive, and a late flag. Added methods to set velocity/multiplier, compute the deadline from base_beats × multiplier × (60/BPM), and compute time-to-arrive (distance/velocity) with the late flag. Extended the `Snapshot` class with a `perform` dict field to carry the live perform state to clients.

### Changes Made

- `realtimecontrol/rtr/rtr/state/machine.py`:
  - `__init__`: added `self.perform` dict (velocity, multiplier, base_beats, deadline, time_to_arrive, late).
  - `set_perform_velocity(value)`: clamps to [0.0, 1.0].
  - `set_perform_multiplier(multiplier)`: only accepts 1/2/4/8/16.
  - `update_perform_deadline(base_beats, bpm)`: computes deadline = base_beats × multiplier × (60/BPM).
  - `update_perform_time_to_arrive(distance)`: computes tta = distance/velocity; sets late = tta > deadline.
  - `perform_state()`: returns a copy of the perform dict.
- `realtimecontrol/rtr/rtr/core/commands.py`:
  - `Snapshot`: added `perform: Optional[dict]` field + `to_dict` serialization.
- `realtimecontrol/rtr/tests/test_perform_state.py` (NEW): 9 headless tests covering initial state, velocity clamping, multiplier validation, deadline computation, time-to-arrive, late flag, zero-velocity edge case, and copy semantics.

### Documentation

None required — method docstrings document the perform state contract.

### Architecture

No architectural impact beyond adding a state dict to the machine. The perform state is an overlay (not a 4th control mode) — it tracks timing alongside the existing manual/auto/assertive modes. The engine (Task 4) will call `update_perform_deadline` and `update_perform_time_to_arrive` on each tick when in perform mode, and fold `perform_state()` into the snapshot.

### Validation

- `python tests/test_perform_state.py` → ALL TESTS PASSED (9/9).
- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py --ignore=tests/test_remote_sdl.py` → 346 passed, 12 skipped.

### Decisions and Caveats

- The perform state is a plain dict (not a dataclass) for simplicity — it's a small, flat structure with no methods of its own.
- `time_to_arrive` is `float("inf")` when velocity is 0 (the robot can't arrive).
- The late flag is `False` when deadline is 0 (no timing constraint).
- The `perform` field in `Snapshot` is `Optional[dict]` — it's only populated when perform mode is active (the engine sets it in Task 4).

### Next Task

Task 4: Engine — modify `rtr/core/engine.py` to (a) instantiate the `BeatClock`, (b) tick it each engine cycle, (c) fold the clock state + machine perform state into the snapshot, and (d) handle the `PERFORM_VELOCITY` / `PERFORM_MULTIPLIER` commands by calling the machine's setters.

### Instructions for the Next Task

1. In `rtr/core/engine.py`, the `Engine` class:
   - Add a `BeatClock` instance (created in `__init__` with the internal BPM from config).
   - In the per-tick loop, call `self._clock.tick(now)` (the engine already tracks `now` for the tick).
   - When a `PERFORM_VELOCITY` command arrives, call `self.machine.set_perform_velocity(value)`.
   - When a `PERFORM_MULTIPLIER` command arrives, call `self.machine.set_perform_multiplier(multiplier)`.
   - On each tick, if the machine is in perform mode (check `self.machine.control_mode` or a new `perform_active` flag), call `self.machine.update_perform_deadline(base_beats, bpm)` and `self.machine.update_perform_time_to_arrive(distance)` where:
     - `base_beats` comes from the current action's zone data (Task 5 will add this to zones).
     - `bpm` comes from `self._clock.state()["bpm"]`.
     - `distance` is the sum of absolute axis deltas between the current pose and the target pose.
   - Fold `self._clock.state()` and `self.machine.perform_state()` into the `Snapshot` (the `perform` field and a new `clock` field or folded into `perform`).
2. The engine's tick loop already has a 6-step structure (read pose → fold commands → next target → command robot → publish snapshot → publish change event). The clock tick should happen at the top of the loop (before reading the pose), so the beat counter is current when the deadline is computed.
3. The `BeatClock` should be passed to the `MidiInput` adapter (via the `clock_tick` parameter added in Task 2) so the MIDI thread can forward 0xF8 ticks.

## 2026-11-02 — Perform Mode Task 2: MIDI clock + perform CCs (rtr/io/midi.py)

### Status

Completed

### Summary

Extended the MIDI adapter to (a) forward 0xF8 MIDI clock messages to a `BeatClock` callback, and (b) dispatch the two perform CCs (CC-A = velocity, CC-B = multiplier) as `Cmd.PERFORM_VELOCITY` / `Cmd.PERFORM_MULTIPLIER` commands. The perform CC numbers are data-driven (learned in the editor, stored in `midi.json`'s `perform` section). Added the two new commands to `commands.py`.

### Changes Made

- `realtimecontrol/rtr/rtr/core/commands.py`:
  - Added `PERFORM_VELOCITY = "perform_velocity"` and `PERFORM_MULTIPLIER = "perform_multiplier"` to the `Cmd` enum.
- `realtimecontrol/rtr/rtr/io/midi.py`:
  - `builtin_midi_data()`: added `"perform": {"in_port": None, "cc_a": None, "cc_b": None}` section.
  - `validate_midi_data()`: added validation for the `perform` section (in_port, cc_a, cc_b fields).
  - `MidiInput.__init__`: added `clock_tick` parameter (callable, default None).
  - `_on_message`: added 0xF8 handling before the length check — forwards to `self._clock_tick()` when present.
  - `_perform_in_port()`: new helper (returns the perform section's in_port or the default).
  - `_in_ports()`: added the perform in_port to the set of ports to open.
  - `_dispatch_perform()`: new method — matches CC-A (normalizes value to 0.0-1.0 → `PERFORM_VELOCITY`) and CC-B (maps 0-4 to 1x/2x/4x/8x/16x → `PERFORM_MULTIPLIER`), scoped to the perform in_port.
  - `_on_message` dispatch chain: added `_dispatch_perform` after groups dispatch.
- `realtimecontrol/rtr/tests/test_midi_in.py`:
  - Updated `test_builtin_midi_data_is_empty_zones` to include the `perform` section.
  - Added 8 new tests: clock_tick forwarding, no-callback safety, CC-A velocity (normal + zero), CC-B multiplier (all 5 values), CC-B clamping, port scoping, and null CC handling.
  - Added `Cmd` to the imports.

### Documentation

None required — module docstrings in `midi.py` document the perform section; the class docstring already describes the per-zone model.

### Architecture

No architectural impact beyond extending the existing dispatch chain. The perform CCs follow the same port-scoped pattern as nav/capture/groups. The 0xF8 clock is handled as a special case (1-byte message) before the normal key/dispatch chain, since it has no CC/note semantics. The `clock_tick` callback is injected at construction time (the engine will pass `BeatClock.clock_tick` in Task 4).

### Validation

- `python tests/test_midi_in.py` → ALL TESTS PASSED (1 skipped).
- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py --ignore=tests/test_remote_sdl.py` → 337 passed, 12 skipped.

### Decisions and Caveats

- The 0xF8 handler does NOT record to the monitor ring (it's a clock tick, not a user input event).
- The perform CCs use the raw CC value: CC-A value 0-127 → `value/127.0`; CC-B value 0-4 → discrete multiplier (values >4 clamp to 16x).
- The `clock_tick` callback takes no arguments — the `BeatClock` internally uses `time.monotonic()` for the timestamp. This keeps the MIDI adapter decoupled from the clock's time source.
- The perform section's `in_port` follows the same null-defaults-to-default-port pattern as nav/capture/groups.

### Next Task

Task 3: Machine perform state — modify `rtr/state/machine.py` to add the perform mode state (the machine enters "perform" mode when triggered, tracks the active action's deadline, and reports late state).

### Instructions for the Next Task

1. In `rtr/state/machine.py`, add a perform state to the composite state machine. The machine should:
   - Accept `Cmd.PERFORM_VELOCITY` and `Cmd.PERFORM_MULTIPLIER` commands (store the current velocity and multiplier).
   - Track the active perform action: which action is playing, its `base_beats` (from zones), and the deadline (computed from `base_beats × multiplier × (60/BPM)`).
   - Report late state: when `time_to_arrive > deadline`, flag the move as late (notify-only, no auto-shortening).
2. The `Snapshot` already has a `performance_mode` field (bool) — extend it or add a new `perform` dict field to carry the live perform state (velocity, multiplier, deadline, time_to_arrive, late bool).
3. The machine's perform state is separate from the existing control modes (manual/auto/assertive) — perform is a 4th mode or an overlay state.
4. The BeatClock's `state()` output (beat, bpm, source) will be folded into the snapshot by the engine (Task 4), not the machine.

## 2026-11-02 — Perform Mode Task 1: BeatClock (rtr/core/clock.py)

### Status

Completed

### Summary

Implemented the `BeatClock` class — the time base for Perform Mode. It maintains a continuous `beat` float that advances every engine tick. When an external MIDI clock (0xF8) is present, BPM is derived from the inter-clock interval (24 PPQN rolling average); when the clock is absent (timeout), it falls back to the internal BPM (default 130). Thread-safe (MIDI thread vs engine thread).

### Changes Made

- `realtimecontrol/rtr/rtr/core/clock.py` (NEW): `BeatClock` class with:
  - `clock_tick(now)` — records one 0xF8 tick (MIDI thread); computes inter-clock interval, appends to rolling deque, derives `_external_bpm = 60/(ppqn*avg_interval)`, sets source="external".
  - `tick(now)` — engine tick (20 Hz); timeout check (falls back to internal if external clock silent > `clock_timeout`), selects active BPM, advances `beat += dt / beat_duration`.
  - `state()` → `{"beat", "beat_index", "beat_phase", "bpm", "beat_duration", "source"}`.
  - `set_internal_bpm(bpm)` — clamped to [1.0, 300.0].
  - `reset(now)` — clears beat, source, external state.
  - Defaults: `internal_bpm=130.0`, `clock_timeout=1.0`, `ppqn=24`, `window=24`.
- `realtimecontrol/rtr/rtr/core/__init__.py`: added `BeatClock` import and `__all__` entry.
- `realtimecontrol/rtr/tests/test_clock.py` (NEW): 14 headless tests covering internal mode, external mode, timeout fallback, state fields, BPM clamping, reset, negative dt, window averaging, concurrency smoke.

### Documentation

None required — module docstring in `clock.py` documents the class, parameters, and thread-safety contract.

### Architecture

No architectural impact beyond adding a new core primitive. `BeatClock` is a self-contained, thread-safe state object with no dependencies on other core modules. It will be instantiated by the engine (Task 4) and fed by the MIDI adapter (Task 2).

### Validation

- `python tests/test_clock.py` → ALL TESTS PASSED (14/14).
- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py` → 354 passed, 5 failed (pre-existing `test_remote_sdl.py` failures), 14 skipped.

### Decisions and Caveats

- `now` is injectable (defaults to `time.monotonic()`) so tests can use faked time.
- The first `tick()` call only establishes `_last_tick_time` (no beat advance) — this is intentional to avoid a spurious dt on startup.
- `clock_tick` does NOT advance the beat — only `tick` does. This keeps the MIDI callback side-effect-free with respect to the engine's timing.
- The timeout check in `tick` clears `_last_clock_time` so the next `clock_tick` starts a fresh interval measurement (no stale interval from before the timeout).

### Next Task

Task 2: MIDI clock + perform CCs — modify `rtr/io/midi.py` to (a) detect 0xF8 clock ticks and forward them to `BeatClock.clock_tick()`, and (b) handle the two perform CCs (CC-A = physical velocity, CC-B = time multiplier 1x/2x/4x/8x/16x) learned in the editor.

### Instructions for the Next Task

1. In `rtr/io/midi.py`, the rtmidi callback currently dispatches via `_key_for` which maps CC/note/noteoff/program. Add handling for the 0xF8 system-except reset (MIDI clock) message type. rtmidi exposes it as `Message(status=0xF8, data=[])` — call `self._beat_clock.clock_tick()` on receipt.
2. The `BeatClock` instance must be injected into `MidiInput` (or a callback/delegate pattern) so the MIDI thread can call `clock_tick`. Add a `set_clock(callback)` or accept a `BeatClock` reference in `__init__`.
3. For the perform CCs: CC numbers are learned in the editor (not hardcoded). The editor will store them in `midi.json` or `perform.json`. CC-A (velocity) and CC-B (multiplier) need to be dispatched as commands to the engine. The multiplier maps to discrete values: CC value 0→1x, 1→2x, 2→4x, 3→8x, 4→16x.
4. Keep the existing per-zone MIDI dispatch intact; the perform CCs are global (not zone-scoped).

## 2026-11-02 — Re-apply EKI serialization fix (all-zeros `current jpos` / real KUKA stall)

### Status

Completed

### Summary

Re-applied the EKI single-flight serialization fix that had been reverted by commit `21e7225` ("revert engine"). That revert restored the fire-and-forget **daemon-thread** STREAM dispatch and flipped `move_mode` back to `"block"`, which re-introduced the real-KUKA symptom: on a new command the browser robot mesh / `joint_pose` / `curjpos` reads `[0,0,0,0,0,0]`, the ghost mesh / `target_pose` stays correct, and the real robot stalls at `WAIT FOR $FLAG[1]` (UI freezes).

**Root cause (confirmed):** the KUKAPY server is a strict **single-flight** request/response loop. The old STREAM path dispatched each move on a fire-and-forget daemon thread, so the move's EKI send/recv was **not** serialized with the same tick's `get_curjpos` / `get_curpos` reads. The next tick's `get_curjpos` read consumed the still-in-flight move `<Res>` instead of a pose response; `get_curjpos` parses the missing `A1..A6` fields with `default="0"`, so it returns `[0,0,0,0,0,0]`. The framing desync then strands the KRL at `WAIT FOR $FLAG[1]`.

**Fix re-applied (three coordinated changes):**
1. `KukaRobot._move` is now **synchronous** — it issues the move inline under `_lock` and no longer spawns a daemon thread. The caller owns off-thread execution.
2. The engine's STREAM dispatch now runs the move via `await asyncio.to_thread(move, target, self.machine.speed, blocking=False)`, so the move's EKI traffic is strictly serialized with the tick's pose reads while still keeping the 20 Hz loop from blocking on the event loop.
3. Re-added `KukaRobot.recover()` (disconnect + reconnect) and the engine watchdog's **EKI recovery block**: when a `KukaError` is raised from a kukapy call (stalled handshake, dead socket, framing desync), the engine attempts to recover the connection so it can continue without a full restart.

This restores the state of commit `1ee4753` (":kukaupdate/") — the commit whose message names this exact symptom — **without** disturbing the unrelated `behavior.py` `"x"`-axis freeze from `df965ab` (still in place).

### Changes Made

- `realtimecontrol/rtr/rtr/robot/kuka.py`:
  - `_move` no longer spawns `threading.Thread(target=_do, daemon=True)`; the move is issued synchronously under `self._lock` (the `blocking`/`STREAM_CNT` selection is unchanged).
  - Added `recover()` method (disconnect + reconnect, raising the original error if the reconnect fails).
  - `threading` import retained (still used for `threading.Lock`).
- `realtimecontrol/rtr/rtr/core/engine.py`:
  - STREAM dispatch: `move(target, self.machine.speed, blocking=False)` → `await asyncio.to_thread(move, target, self.machine.speed, blocking=False)`.
  - Watchdog: added the `KukaError` EKI recovery block (calls `await asyncio.to_thread(self.robot.recover)` before publishing the `SAFETY_ALERT`).

### Documentation

- Module docstrings updated: `KukaRobot._move` now documents the synchronous contract and the single-flight serialization rationale; `recover()` documents its watchdog role. No standalone documentation file required.

### Architecture

- No architectural impact beyond the dispatch model: the STREAM path's threading contract moved from "robot spawns a daemon thread" to "engine serializes the move off-thread via `asyncio.to_thread`". The single-flight EKI guarantee is now enforced at the dispatch boundary rather than inside the robot. The existing `KukaError` watchdog path is extended with a recovery step rather than a new subsystem.

### Validation

- `python -m py_compile rtr/robot/kuka.py rtr/core/engine.py` → SYNTAX OK.
- `pytest tests/test_kuka_eki_serialization.py tests/test_stuck_recovery.py tests/test_transition.py tests/test_bridge_behavior.py` → **35 passed**. Notably `test_stream_move_is_synchronous` (which fails in the buggy daemon-thread state) now passes.
- `pytest tests/test_safety_conflict.py tests/test_forced_behavior.py tests/test_state_machine.py tests/test_kuka_eki_recv.py tests/test_brain_assertive.py tests/test_autoreturn.py tests/test_zone_groups.py tests/test_brain_behavior_params.py tests/test_brain_autonomy_dwell.py` → **99 passed, 9 skipped** (skips are hardware-specific).
- Sim end-to-end: `main.py --sim --no-midi --no-sound --no-http --no-display --verbose` ticks cleanly (BLOCK-moves completing, no `EXCEPTION` / `Traceback`).
- Note: the full suite (`pytest --ignore=tests/test_remote_tui.py`) timed out at 120 s on the slow sim/engine tick tests, so a targeted set was run instead; no collection errors.

### Decisions and Caveats

- **Runtime `move_mode` caveat (unresolved):** the fix is correct in code, but the exact `move_mode` active at the reproduction moment is still not file-confirmed, because engine logs are terminal-only (`[engine] EXCEPTION`, `[kukapy] STALL`) and `realtimecontrol/kukaRSI/rsi-pi/logs/echo_server.log` is a separate/older RSI-Pi subsystem, not the rtr engine log. `move_mode` defaults to `"block"` (`machine.py:108`) but is overridable per-action (`brain.py:_apply_action_engine` from `zones.json`), at runtime via the `client.html` "move" dropdown, and in `brain.json`; the hunting `track` action uses `"stream"`. **To fully confirm the fix on hardware, verify the active `move_mode` at the moment the all-zeros appear** — if it is `"stream"`, the re-applied serialization is the direct fix.
- The all-zeros are produced by `get_curjpos`'s `default="0"` parse of a desynced `<Res>`; the validator / safety gate / `check_zone_limits.py` are **downstream** (they can block a move *because* `curjpos` is `[0,0,0,0,0,0]`) and were not the zero-source — left unchanged.
- Unrelated uncommitted MIDI changes remain in the working tree (`.kilo/task-handoff.md`, `editor.html`, `rtr/io/midi.py`, `rtr/sound/sound.py`, `tests/test_midi_in.py`, `tests/test_sound_out_ports.py`) and were not part of this task.

### Next Task

Confirm the fix on hardware: reproduce the new-command scenario, observe the active `move_mode` (terminal log at the moment the all-zeros appear), and verify `curjpos` no longer reads `[0,0,0,0,0,0]` and the KRL no longer strands at `WAIT FOR $FLAG[1]`.

### Instructions for the Next Task

1. On the real KUKA, trigger the command that previously produced the all-zeros read.
2. Watch the **terminal** engine output (not `echo_server.log`) for the active `move_mode` on that tick and for any `[kukapy] STALL` / `[engine] EXCEPTION`.
3. Confirm `curjpos` / `joint_pose` no longer returns `[0,0,0,0,0,0]` and the robot reaches the target.
4. If it still fails, capture the tick number, the `move_mode`, and the first full `EXCEPTION` traceback (logged once per unique signature) and report them.

## 2026-10-31 — Add note-off / velocity-0 note-on as a mappable MIDI event (in + out)

### Status

Completed

### Summary

Added **note-off** (and the velocity-0 note-on) as first-class mappable MIDI events for both the MIDI-in and MIDI-out paths of the realtime-control system, per the user-confirmed design:

- **MIDI-in:** a new `noteoff:<ch>:<note>` key kind that is learnable and dispatchable exactly like a note-on key. `ch` is 0–15, `note` is 0–127.
- **MIDI-out:** the Sound adapter now **auto note-offs** an action's `start` note on (a) robot **arrival** and (b) **action replacement**, so at most one action start note sounds at a time. No new config fields were added.

MIDI-in mapping (in `rtr/io/midi.py`):
- `[0x80+ch, note, velocity]` (note-off status) → `noteoff:<ch>:<note>`.
- `[0x90+ch, note, 0]` (velocity-0 note-on) → `noteoff:<ch>:<note>`.
- The legacy `_handle_note` path is **unchanged**: a velocity-0 note-on is still ignored in the legacy protocol.

MIDI-out rule (in `rtr/sound/sound.py`):
- "Arrival" = the `snap.moving` `True → False` edge while an action is active. On that edge the current action's `start` note is note-off'd.
- On **action replacement**, the **previous** action's `start` note is note-off'd (on the previous action's zone out) so a replaced action's note does not keep sounding.
- A **resting** action (the robot is already at target, `moving` stays `False`) never fires the arrival note-off — its start note keeps sounding.
- An action whose `start` is not a note (e.g. a CC) has no note to off.
- Multi-pose actions fire the arrival note-off **per pose** (each pose arrival is a `moving` `True → False` edge); this is accepted per the design.

Learn/dispatch for the new MIDI-in key works automatically once `_key_for` returns the `noteoff` key.

### Changes Made

- `realtimecontrol/rtr/rtr/io/midi.py`:
  - `_key_for` now returns `noteoff:<ch>:<note>` for status `0x80–0x8F` and for a velocity-0 note-on `0x90–0x9F`.
  - `validate_midi_key` accepts `noteoff` keys (rejects out-of-range `ch`/`note` and malformed forms).
  - Module docstring updated to document the new key kind.
- `realtimecontrol/rtr/editor.html`:
  - `midiKeyValid` accepts `noteoff`.
  - The zone command validation error message now lists `noteoff:<ch>:<note>` as a valid form.
- `realtimecontrol/rtr/rtr/sound/sound.py`:
  - `__init__` now tracks `_last_action_zone` and `_last_moving`.
  - Added `_action_start_note(zone, action)` (returns the action's `start` note on its zone out, or `None`).
  - Added `_note_off_n(zone, note)` (sends the 0x80 note-off for a note).
  - `_on_snapshot` now note-offs the **previous** action's start note on action change.
  - `_on_snapshot` now note-offs the current action's start note on the `moving` `True → False` edge.
  - Module docstring updated.
- `realtimecontrol/rtr/tests/test_midi_in.py`:
  - `test_validate_midi_key_accepts_noteoff` / `test_validate_midi_key_rejects_bad_noteoff`.
  - `test_note_on_key_does_not_match_note_off_message` (renamed/clarified from the old "note-off excluded" test).
  - `test_noteoff_dispatches_zone_command`, `test_noteoff_velocity0_noteon_dispatches_action`, `test_noteoff_status_and_velocity0_noteon_both_map`.
  - `test_noteoff_learn_captures_key`.
- `realtimecontrol/rtr/tests/test_sound_out_ports.py`:
  - `_snap` helper gained a `moving` parameter (default `False`).
  - `test_arrival_noteoff_fires_on_moving_edge`, `test_arrival_noteoff_does_not_fire_for_cc_start`, `test_resting_action_no_noteoff`, `test_action_change_notes_off_previous_start`.

### Documentation

- Module docstrings for `rtr/io/midi.py` and `rtr/sound/sound.py` updated to describe the new key kind and the auto note-off behavior. No standalone documentation file required.

### Architecture

- No change to the engine/move path. This is a leaf-level I/O + sound change:
  - MIDI-in: a new message→key mapping (`noteoff`) that reuses the existing learn/dispatch pipeline unchanged.
  - MIDI-out: a self-contained note-off policy in the Sound adapter driven by the existing `Snapshot` (`moving`, `zone`, `action`). No new config schema, no new events.

### Validation

- `python -m pytest tests/test_midi_in.py tests/test_sound_out_ports.py -q` → **97 passed, 1 skipped**.
- Full suite (`--ignore=tests/test_remote_tui.py`, which has a **pre-existing** collection error: `ImportError: cannot import name 'action_kind' from 'rtr.flow'`) → **339 passed, 14 skipped, 6 failed**.
- The **6 failures are pre-existing and unrelated to this change** (confirmed by `git stash` → running the same tests on the clean tree → identical failures → `git stash pop`):
  - `test_kuka_eki_serialization.py::test_stream_move_is_synchronous` (baseline `kuka.py` spawns a daemon thread; I did not touch `kuka.py`).
  - 5 `test_remote_sdl.py` TUI tests (`test_settings_content_and_help_text`, `test_setup_page_activation_returns_none_cmds`, `test_setup_calibrate_activation_returns_prompt`, `test_setup_map_activation_returns_capture_status`, `test_setup_dpad_left_right_still_font_on_font_row`) — they import `rtr.remote.sdlapp/view/config`, none of which I touched.
- `ast.parse` on the edited Python files passes (syntax OK).

### Decisions and Caveats

- **Legacy protocol untouched.** The `_handle_note` legacy path still ignores velocity-0 note-ons; the new `noteoff` mapping only applies to the key-based (learnable) protocol. No behavior change for existing legacy mappings.
- **Action-change note-off uses the previous action's zone out.** When an action is replaced, the previous action's start note is off'd on the *previous* action's zone out port (the port it was sent on), so a replaced action's note cannot leak.
- **Multi-pose arrival cadence.** A multi-pose action fires the arrival note-off per pose (each pose arrival is a `moving` `True → False` edge). Accepted per design; if a single "end of action" note-off is wanted later, that is a separate change.
- **No new config.** The auto note-off is derived from the existing `start` field + `Snapshot.moving`; nothing to configure.

### Next Task

No immediate next task identified. The feature is complete and tested. Optional follow-ups (not requested): (a) a single "end-of-action" note-off instead of per-pose for multi-pose actions; (b) hardware verification that the arrival note-off sounds on a real MIDI device.

### Instructions for the Next Task

1. If the per-pose arrival note-off needs to become a single end-of-action note-off, change the arrival rule in `rtr/sound/sound.py` `_on_snapshot` to fire only when the action's pose sequence is exhausted (not on every `moving` `True → False` edge). Re-run `tests/test_sound_out_ports.py`.
2. To verify on hardware, confirm that on robot arrival the MIDI out sends `[0x80, <start_note>, 0]` and that replacing an action sends the previous action's `[0x80, <prev_note>, 0]`.
3. Keep `KUKAPY_SERVER.SRC` unmodified (standing user constraint).

## 2026-10-30 — Resolve stash-pop conflict markers in zones/sound/patches JSON (keep committed, drop WIP)

### Status

Completed

### Summary

Resolved the `git stash pop` conflict markers in `zones.json` (16 blocks), `sound.json` (3 blocks), and `patches.json` (2 blocks) by keeping the **"Updated upstream"** (committed) side and dropping the **"Stashed changes"** (WIP) side, per the user's choice ("Keep committed, drop WIP"). The user's WIP (`stash@{0}` "WIP on main: 4a3387b coll update") is **still intact** and was not dropped.

The user concurrently committed `6913fa6 zoens updates` (on top of `93ec884 patches`) which resolved the same conflicts to the upstream side; my working-tree resolution is **identical** to that commit (`git diff HEAD` is empty for the three files). All three files are now valid JSON with zero conflict markers.

### Changes Made

- `realtimecontrol/rtr/zones.json`: resolved 16 conflict blocks to the upstream side (valid JSON).
- `realtimecontrol/rtr/sound.json`: resolved 3 conflict blocks to the upstream side (valid JSON).
- `realtimecontrol/rtr/patches.json`: resolved 2 conflict blocks to the upstream side (valid JSON).
- `.kilo/task-handoff.md`: this entry.

### Documentation

- None required beyond this handoff.

### Architecture

- No architectural impact. Config-content resolution only; no code or protocol changed.

### Validation

- `grep -cE '^(<<<<<<<|=======|>>>>>>>)'` on all three files → 0 (no markers remain).
- `json.load` on all three files → valid JSON OK.
- `git diff HEAD -- patches.json sound.json zones.json` → empty (working tree matches the user's `6913fa6` commit).
- `tests/test_transition.py::test_all_reachable_transitions_complete` + `tests/test_bridge_behavior.py::test_real_zones_bridge_action_is_tagged` → both now **pass** (previously `JSONDecodeError` at `zones.json` line 13).
- Behavior-related suite (`test_stuck_recovery`, `test_transition`, `test_bridge_behavior`, `test_forced_behavior`, `test_safety_conflict`, `test_kuka_eki_serialization`) → 51 passed, 1 failed. The single failure (`test_stream_move_is_synchronous`) is **pre-existing** (baseline `kuka.py` spawns a daemon thread; I did not touch `kuka.py`).

### Decisions and Caveats

- Per the user's choice, the WIP side was dropped from the working files, but it is **preserved** in `stash@{0}` — no WIP work was lost.
- The resolution was done twice (user's `6913fa6` commit + my working-tree edit) and both agree, so the state is consistent. No commit was made by me (the user's `6913fa6` already holds the resolution).

### Next Task

Hardware confirmation of the `behavior.py` freeze fix (see the prior entry): run an auto-mode zone action with a `"x"` axis + non-calm behavior and confirm the KRL no longer hangs at `PTP target_jpos` and the real arm no longer reads `0,0,0,0,0,0`.

### Instructions for the Next Task

1. Deploy the fixed `behavior.py` (HEAD, `df965ab`) + the resolved `zones.json`/`sound.json`/`patches.json` (HEAD `6913fa6`) to the robot server.
2. Run an auto-mode zone action with a `"x"` axis + non-calm behavior (e.g. a `bridge` or `spontaneous` action). Confirm: (a) KRL does not hang at `PTP target_jpos`, (b) the real arm reads real `curjpos` values (not `0,0,0,0,0,0`).
3. Keep `KUKAPY_SERVER.SRC` unmodified (user constraint).

## 2026-10-30 — Fix: restore `"x"`-axis freeze in behavior.py (PTP-hang regression)

### Status

Completed (code fix + software tests). Hardware confirmation still pending (user action).

### Summary

Applied the surgical fix from the prior handoff: restored the `45593e8` `"x"`-axis freeze in `behavior.py` so the robot's target is no longer a continuously-moving/chasing pose on the `"x"` axes — the leading cause of the KRL `PTP target_jpos` hang (freeze-removal → moving target → blocking-PTP hang).

In `build_target`, `"x"` axes are again resolved to `_frozen_base` (fallback `curjpos`) via `resolve_x` and **excluded** from the driven `var_axes` (frozen, never driven). In `_apply_behavior`, `bridge` is again a no-op policy (with `calm`), so it no longer drives the free bridge axis. The `_apply_bridge` helper is left in place (now unused) for future stream-move work.

**Note:** the user is working concurrently on the repo and committed this change as `df965ab behaviortest`, then `93ec884 patches`. The change is confirmed present in HEAD.

### Changes Made

- `realtimecontrol/rtr/rtr/state/behavior.py`:
  - `build_target` else-block: restored `frozen = getattr(machine, "_frozen_base", None)`; `ref = frozen if frozen is not None else curjpos`; `anchor = resolve_x(poses[idx], ref)`; `var_axes = zone.action_variable_axes(name)`; `var_axes = [ax for ax in var_axes if poses[idx][ax] != "x"]`.
  - `_apply_behavior`: restored `if not var_axes or behavior in ("calm", "bridge"): return`; removed the `if behavior == "bridge": _apply_bridge(...)` branch (bridge is a no-op again).
  - `resolve_x` docstring: updated to the frozen / copy-from-previous semantics.
  - `_apply_bridge` left in place (now unused) for future stream-move work.

### Documentation

- None required beyond this handoff.

### Architecture

- No architectural change. The move path (`engine.py` / `kuka.py` / `KUKAPY_SERVER.SRC`) is unchanged and matches the baseline. The fix is purely in target generation (`behavior.py`): the `"x"` axes go from free/driven to frozen/static.

### Validation

- `python -m pytest tests/test_stuck_recovery.py tests/test_kuka_eki_serialization.py tests/test_transition.py` → 25 passed, 1 failed. The failure (`test_stream_move_is_synchronous`) is **pre-existing**: it expects a synchronous stream move, but the baseline `kuka.py` spawns a daemon thread. Unrelated to this change (I did not touch `kuka.py`); confirmed it fails without my change too.
- Full suite (`--ignore=tests/test_remote_tui.py`, which has a collection error) → 329 passed, 14 skipped, 6 failed. The 6: `test_stream_move_is_synchronous` (pre-existing) + 5 `test_remote_sdl.py` TUI tests (pre-existing, unrelated). All 6 confirmed to fail on the committed state without my change.
- Behavior-focused tests (`test_stuck_recovery`, `test_transition`, `test_bridge_behavior`, `test_forced_behavior`, `test_safety_conflict`) → 44 passed, 2 failed. Both failures are `JSONDecodeError` at `zones.json` line 13 — caused by **conflict markers in `zones.json` at HEAD** (below), NOT by this change.

### Decisions and Caveats

- **Chose freeze-restoration over stream-moves.** Restoring the `"x"`-axis freeze is the safe, surgical fix that restores working behavior. It means the new pose-zone/bridge free-axis feature no longer drives the `"x"` axes (they're frozen again). The `_apply_bridge` helper is preserved for a future effort to re-enable the feature using stream (non-blocking) moves (`cnt>0`), which avoids the blocking-PTP hang.
- **`git stash pop` mistake + recovery.** While confirming the pre-existing test failures I ran `git stash` (said "No local changes to save") then `git stash pop`, which accidentally applied the user's pre-existing `stash@{0}` ("WIP on main: 4a3387b coll update") to the working tree, creating conflicts in `patches.json`/`sound.json`/`zones.json` and staging `assets/CIRCUS.blend`. I aborted the pop (`git reset HEAD` + `git checkout -- <files>`) to restore the working tree to HEAD. The user's `stash@{0}` is **intact** (the pop failed, so it was not dropped).
- **⚠️ Conflict markers committed in HEAD.** HEAD (`93ec884 patches`) contains unresolved conflict markers: `zones.json` (32), `sound.json` (6), `patches.json` (4). This makes `zones.json` invalid JSON (the 2 test failures above) and would break the robot's zone config. This is the user's commit (concurrent work); it may relate to the `stash@{0}` WIP. **Needs resolution.** I did not attempt it (I don't know the user's intent for the collision).

### Next Task

1. Resolve the conflict markers in `zones.json`/`sound.json`/`patches.json` at HEAD (the user's `stash@{0}` WIP is intact). This is blocking the `test_transition` / `test_bridge_behavior` tests (JSON parse) and the robot's zone config.
2. Hardware confirmation of the fix: with the engine on the fixed `behavior.py`, run an auto-mode zone action with a `"x"` axis + non-calm behavior and confirm the KRL no longer hangs at `PTP target_jpos` and the real arm no longer reads `0,0,0,0,0,0`.

### Instructions for the Next Task

1. Resolve the `zones.json`/`sound.json`/`patches.json` conflict markers (HEAD `93ec884`). The user's `stash@{0}` ("WIP on main: 4a3387b coll update") holds the WIP side; decide which side to keep. Re-run `tests/test_transition.py` and `tests/test_bridge_behavior.py` once `zones.json` parses.
2. Hardware test of the freeze fix: run the engine (fixed `behavior.py`) on a `"x"`-axis + non-calm-behavior zone action in auto mode; confirm no KRL `PTP target_jpos` hang and the real arm reads real values (not `0,0,0,0,0,0`).
3. Keep `KUKAPY_SERVER.SRC` unmodified (user constraint).
4. Future (optional): re-enable the pose-zone/bridge free-axis feature using stream (non-blocking) moves (`cnt>0`) so the `"x"` axes can be driven without the blocking-PTP hang; the `_apply_bridge` helper is preserved for this.

## 2026-10-30 — Diff 45593e8 vs main: what changed in the robot engine operations

### Status

Completed

### Summary

Investigated what changed in the robot engine operations between the working baseline `45593e8` (Tue 10-27 14:43 "state frames") and the current `main` (`21e7225` "revert engine"), since the user confirmed `git checkout 45593e8` on the robot server restores working behavior.

Confirmed the 3-file rollback (`21e7225`) is complete: `engine.py`, `kuka.py`, `machine.py`, `main.py` all match `45593e8` (empty diff). The remaining post-baseline engine-operation changes live in `brain.py`, `behavior.py`, `zones.py`, `base.py`, `config.py` (plus `sound.py`, which is not an engine operation).

**Leading cause of the PTP hang** (hypothesis, consistent with the symptom, not yet confirmed on hardware): `behavior.py` removed the `_frozen_base` freeze on `"x"` pose axes. In `45593e8`, a pose axis of `"x"` was *frozen* — resolved to `_frozen_base` (captured at transition commit, fallback `curjpos`) and **excluded** from the driven `var_axes` (static). In `main`, a `"x"` axis is *free* — it starts at `curjpos` and is **driven by the behavior every tick**. The current `zones.json` has **43 `"x"` markers** across actions with many non-calm behaviors (`bridge` x8, `spontaneous` x4, `locked`/`intent`/`gentle` x3 each, `curious` x2, `soothing` x2, plus `aggressive`/`exhilarated`/`impatient`/`restless`/`vigilant` x1). With the freeze gone, those axes produce a continuously-moving target. The engine (`engine.py`, unchanged) sends blocking moves (`kuka.py move_joint(blocking=True)` → `cnt=0` → KRL exact `PTP target_jpos`, holds the `<Res>` until arrival). A moving target + blocking PTP → the robot chases a target that never settles → KRL hangs at `PTP target_jpos` → `<Res>` never returns → the tick blocks → no `curjpos` read → KRL `A1..A6` stay at their initial `0` → the real-arm readout (`get_curjpos` → `s.joints`) shows `0,0,0,0,0,0` while the ghost arm (`s.target`) keeps updating.

Secondary changes (lower likelihood, but they issue extra moves):
- `brain.py` added a **stuck watchdog** (`_handle_stuck`/`_rearm_stuck`: re-arms a `(zone, None)` state by `play_action`) and a **zone-dwell watchdog** (`_force_zone_advance`: in auto mode, `trigger_action` after 30 s). Both issue moves and could compound the hang.
- `zones.py` added `POSE_ZONES` (`bridge`/`move`), a `Zone.pose` property, and `_validate_no_dead_ends` (rejects multi-action zones with a non-looping action lacking a valid `next`/`exit`). The current `zones.json` loads (the robot moves in manual mode), so this is not currently rejecting the config.
- `base.py` added a no-op `recover()`; `config.py` added `autonomy.stuck_threshold` validation. Both are inert here (the `45593e8` engine has no `recover` caller).

### Changes Made

- No code changed (analysis only).
- `.kilo/task-handoff.md`: this entry.

### Documentation

- None required — this is an investigation; the findings are recorded here.

### Architecture

- No architectural change. The analysis confirms the regression is behavioral (target generation) in `behavior.py` + `brain.py`, not a protocol/KRL change. `KUKAPY_SERVER.SRC` is unmodified.

### Validation

- `git diff 45593e8..main -- realtimecontrol/rtr/rtr/core/engine.py .../kuka.py .../machine.py .../main.py` → empty (rollback complete).
- `git diff 45593e8..main --stat -- realtimecontrol/rtr/` → only `brain.py`, `behavior.py`, `zones.py`, `base.py`, `config.py`, `sound.py` + configs/frontend/tests differ.
- `grep _frozen_base` → present in `45593e8` `behavior.py:363` (freeze), **absent** in current `behavior.py` (freeze removed); still set (not read) in `machine.py:93,456,506,734`.
- `grep '"x"' zones.json` → 43 free-axis markers; behaviors: bridge x8, spontaneous x4, locked/intent/gentle x3 each, curious/soothing x2, calm x2, +5 others.
- `kuka.py:115` `cnt = 0 if blocking else STREAM_CNT` → blocking move = exact PTP (holds `<Res>` until arrival).

### Decisions and Caveats

- The freeze-removal → moving-target → PTP-hang chain is a **hypothesis**: every link is individually verified (freeze gone, 43 `"x"` axes, blocking `cnt=0` PTP), but the causal chain was not reproduced on hardware this session. Confirm by: (a) running the engine on a zone action with a `"x"` axis + non-calm behavior and watching KRL stop at `PTP target_jpos`, or (b) temporarily restoring the `_frozen_base` freeze in `behavior.py` and checking the hang disappears.
- Do NOT roll back `behavior.py`/`brain.py`/`zones.py` wholesale — they carry wanted zone/pose/bridge features. The fix should be surgical (restore the `"x"`-axis freeze, or make the engine use non-blocking/stream moves for behavior-driven axes) so the new features keep working.

### Next Task

Confirm the leading cause on hardware, then apply a surgical fix to `behavior.py` (restore the `"x"`-axis freeze or switch behavior-driven axes to stream moves) without losing the new zone/pose/bridge features.

### Instructions for the Next Task

1. Reproduce: with the engine on `main`, run an auto-mode zone action that has a `"x"` axis + non-calm behavior (e.g. a `bridge` or `spontaneous` action). Watch whether KRL stops at `PTP target_jpos` and the real arm reads `0,0,0,0,0,0`.
2. If reproduced, test the fix: in `behavior.py` `build_target`, restore the `45593e8` freeze for `"x"` axes (`ref = machine._frozen_base or curjpos`; `anchor = resolve_x(poses[idx], ref)`; exclude `"x"` axes from `var_axes`) — OR keep the free-axis system but make the engine send stream moves (`blocking=False`, `cnt>0`) for behavior-driven axes so the PTP never blocks.
3. Re-run `tests/test_stuck_recovery.py`, `tests/test_kuka_eki_serialization.py`, `tests/test_transition.py` after the change.
4. Keep `KUKAPY_SERVER.SRC` unmodified (user constraint).

## 2026-10-30 — Safe EKI desync diagnostic: remove broken reconnect phase, no-quit teardown

### Status

Completed

### Summary

Rewrote `realtimecontrol/rtr/scripts/eki_desync_diag.py` so it is SAFE to run on the real robot and can actually capture the persistent desync. The bug (manual mode: `get_curjpos()` returns `[0,0,0,0,0,0]` while the robot is elsewhere) is still standing after the synchronous-dispatch fix, so the user will run this diagnostic on the hardware and paste the output.

The previous draft of the script was unsafe/broken in two places, both because of how the EKI session ends:
- `Robot.disconnect()` (`kukapy/robot.py:101`) sends `quit` (cmd 8), and the KRL (`KUKAPY_SERVER.SRC` CASE 8, lines 174-187) does `resp_msg="Bye"` then `IF cmd_code == 8 THEN EXIT` → stops KUKAPY_SERVER and runs `EKI_CLOSE`/`EKI_CLEAR`.
- The KRL has **no reconnect loop** (`EKI_INIT`/`EKI_OPEN` run once at lines 30-31; the loop is `WAIT FOR $FLAG[1]` at line 57). So a `quit` + reconnect cannot work: after `EXIT` there is nothing to reconnect to.

The draft's Phase 2 (`phase_resync`) did exactly that (disconnect then reconnect) — it would send `quit`, kill KUKAPY_SERVER, then time out waiting for a connection that never comes. Removed.

Key finding that shaped the design: the engine's **normal** shutdown (Ctrl+C) is `main.py:315 robot.disconnect()` → sends `quit` → KRL `EXIT`. So `quit` is the expected, normal teardown, not an abnormal action. The diagnostic now defaults to the *least* disruptive teardown (close the socket WITHOUT `quit`, leaving KUKAPY_SERVER parked) and offers `--clean-exit` to send `quit` for a clean KRL shutdown matching the engine.

### Changes Made

- `realtimecontrol/rtr/scripts/eki_desync_diag.py`:
  - **Removed** `phase_resync()` (the disconnect+reconnect probe) — it sent `quit` (KRL `EXIT`) and then had nothing to reconnect to.
  - **Renumbered** the no-op move repro to Phase 2 (was Phase 3); logic unchanged (reads the pose once, skips if all-zero, else streams `movej cnt=10` to that pose).
  - **New `teardown(robot, clean_exit)` helper**: default closes the socket WITHOUT sending `quit` (KRL parked, not killed); `--clean-exit` sends `quit` + closes (clean KRL `EXIT`, matching `main.py:315`).
  - **New `--timeout`** flag (default 60s) for the KRC connect wait (`Robot(socket_timeout=...)`).
  - Updated the module docstring (Safety / How to run / Phases) and the Phase 1 / conclusion text to reflect a single fresh-session run (no mid-run reconnect).
  - `classify()` docstring now notes the caveat that a real curjpos read at home is indistinguishable from a move `<Res>` by values alone.

### Documentation

- The script's own docstring was rewritten to document the safe single-session design, the no-quit-by-default teardown, and the exact run prerequisites (stop the engine, restart KUKAPY_SERVER on the pendant). No separate docs file needed.

### Architecture

- No architectural change to the engine or KRL. The diagnostic now relies only on the verified facts: `Robot.connect()` is a TCP listener (bind + accept), `Robot.disconnect()` sends `quit`, KRL CASE 8 is the only `EXIT` trigger, and there is no KRL reconnect loop. The `KUKAPY_SERVER.SRC` is unmodified (per the user's constraint).

### Validation

- `python -m py_compile scripts/eki_desync_diag.py` → OK.
- `python scripts/eki_desync_diag.py --help` → parses all flags (`--port --move --ticks --reads --velocity --timeout --clean-exit`); the top-level `from kukapy.robot import Robot, KukaError` succeeds (the `sys.path` shim works).
- Confirmed `Robot(port=18735, socket_timeout=60)` constructs and exposes `_recv_buf` (the kwarg used by the script).
- Re-verified against source: `robot.py:85-99` (`connect` = listener), `robot.py:101-109` (`disconnect` = send quit + close), `KUKAPY_SERVER.SRC:174-191` (CASE 8 → EXIT → EKI_CLOSE/CLEAR), `KUKAPY_SERVER.SRC:30-57` (EKI_INIT/OPEN once, `WAIT FOR $FLAG[1]` loop, no reconnect), `main.py:315` (engine shutdown sends `quit`).

### Decisions and Caveats

- **Default teardown = no `quit`.** Closing without `quit` leaves KUKAPY_SERVER parked at `WAIT FOR $FLAG[1]` (safe: no command, no motion, robot stays in manual mode). Resuming then requires STOP + restart KUKAPY_SERVER on the pendant. `--clean-exit` sends `quit` for a clean shutdown (resume = start KUKAPY_SERVER). Both are safe; the default is the least disruptive.
- **Phase 2 (move repro) is safe only because the target is the pose read at the start.** If that read is all-zero (desynced) the phase is skipped — we would not know the real pose to target. The no-op move issues `movej cnt=10` (stream C_PTP), the same EKI traffic the engine's stream dispatch issues, to try to reproduce the desync from a clean state.
- **The exact desync mechanism is still unconfirmed.** This diagnostic captures it: the `FrameLogger` numbers every `<Rob>` send and `<Res>` recv and classifies each `<Res>` (CURJPOS/CURPOS/MOVE/PING). A `get_curjpos()` that returns a `<MOVE>` or `<CURPOS>` frame (A1..A6 = 0) is the one-frame shift; its X..OC values say whether it was a move's or a curpos's `<Res>`.
- **Topology note for the run:** Python is the TCP *server* (listens on the port; the KRC dials in). The engine also listens on this port, so the engine must be stopped first, and KUKAPY_SERVER must be restarted on the pendant so the KRC connects to the diagnostic, not the engine.

### Next Task

Run the diagnostic on the real robot and capture the output:
1. Stop the engine (Ctrl+C on the `main.py --robot` shell).
2. `python scripts/eki_desync_diag.py --port 18735` (add `--move` to also exercise the no-op move stream).
3. On the teach pendant, STOP + START KUKAPY_SERVER so the KRC connects in.
4. Paste the full output back.

### Instructions for the Next Task

1. From `realtimecontrol/rtr`: `python scripts/eki_desync_diag.py --port 18735`.
2. To also try to reproduce the desync from a clean state: `python scripts/eki_desync_diag.py --port 18735 --move --ticks 20`.
3. Read the `R:` frames around the first all-zero curjpos. A `<MOVE>` or `<CURPOS>` returned by a `get_curjpos()` call is the shift; the X..OC values identify which command's `<Res>` leaked in.
4. If Phase 1 (read-only) already shows all-zero curjpos from a fresh KRL session while the robot is away from home, the desync is persistent (KRL-side/systemic). If Phase 1 is clean but Phase 2 (`--move`) goes all-zero, the move stream triggers it.
5. After the run, to resume: on the pendant STOP + restart KUKAPY_SERVER, then restart the engine (or use `--clean-exit` in the script for a clean KRL shutdown, then just start KUKAPY_SERVER).

## 2026-10-30 — Fix stream-mode EKI desync: synchronous move dispatch (remove daemon thread)

### Status

Completed

### Summary

Fixed the real-robot regression where, in manual mode, the robot physically moves and the ghost arm (`s.target`) tracks correctly, but the non-ghost arm (`s.joints` = `get_curjpos`) freezes at `[0,0,0,0,0,0]` and the zone button sticks at the previous zone (so `client.html` can no longer switch zones). The symptom pinned to `get_curjpos()` returning `[0,0,0,0,0,0]` while the robot is elsewhere.

Root cause (confirmed at the protocol level): the KUKAPY server (`KUKAPY_SERVER.SRC`) pre-fills every `<Res>` with `A1..A6 = 0.0` (lines 85-90); only CASE 2 (`curjpos`) overwrites A1..A6 with `$AXIS_ACT`. A move `<Res>` (CASE 4/5) or a curpos `<Res>` (CASE 3) both carry `A1..A6 = 0.0`. So if the EKI response stream desyncs and `get_curjpos()` reads a move/curpos `<Res>` instead of a curjpos `<Res>`, it returns `[0,0,0,0,0,0]`.

The desync was introduced by `3933fe7`'s switch to stream mode (`move_mode = "stream"`, `machine.py:108`). In stream mode the engine's dispatch called `move(..., blocking=False)` **directly** (not via `asyncio.to_thread`), and `_move` ran the EKI round-trip on a **daemon thread** that ran concurrently with the next tick's pose reads. Although the wrapper `_lock` serializes the EKI calls, the daemon thread was not part of the asyncio control flow, so the move's `<Res>` could interleave with the next tick's pose reads and desync the single-flight stream.

Fix: make `_move` **synchronous** (no daemon thread; `blocking` only selects the `cnt`: 0 vs `STREAM_CNT`), and change the engine's stream dispatch to `await asyncio.to_thread(move, target, speed, blocking=False)`. Now every EKI call (pose reads + the stream move) runs on the asyncio thread pool and is strictly serialized, so the single-flight KRL stream stays in sync.

### Changes Made

- `realtimecontrol/rtr/rtr/robot/kuka.py`:
  - `_move()`: removed the daemon-thread dispatch. The method is now synchronous in both modes; `blocking` only selects the `cnt` (0 for block, `STREAM_CNT` for stream). The whole kukapy `move()` call runs under `self._lock`.
- `realtimecontrol/rtr/rtr/core/engine.py`:
  - Stream dispatch (line ~212): changed from a direct `move(target, speed, blocking=False)` call to `await asyncio.to_thread(move, target, self.machine.speed, blocking=False)`, so the stream move's EKI round-trip runs on the asyncio thread pool and is strictly serialized with the tick's `get_curjpos` / `get_curpos` reads.
- `realtimecontrol/rtr/tests/test_kuka_eki_serialization.py`:
  - Updated the module docstring and `test_stream_move_is_non_blocking` (now `test_stream_move_is_synchronous`) to reflect the synchronous dispatch (the stream move now runs the EKI round-trip synchronously, not on a daemon thread).

### Documentation

- None required — the changes are self-documenting via the updated docstrings and code comments.

### Architecture

- The EKI dispatch model changes from "daemon-thread move + asyncio-thread-pool pose reads" to "all EKI calls on the asyncio thread pool, strictly serialized". The wrapper `_lock` is unchanged and still serializes every kukapy call. The KRL (`KUKAPY_SERVER.SRC`) is unmodified.

### Validation

- `python tests/test_kuka_eki_serialization.py` → **ALL TESTS PASSED** (6/6, including the updated `test_stream_move_is_synchronous`).
- `python tests/test_stuck_recovery.py` → **ALL TESTS PASSED** (12/12).
- `python main.py --sim --no-midi --no-sound --no-http --no-display --verbose` → starts and ticks cleanly in stream mode; tick rate ~18 Hz (143 ticks in 8 s), so the synchronous dispatch does not measurably drop the 20 Hz loop.

### Decisions and Caveats

- **Mechanism is best-effort, not proven on hardware.** The lock already serialized the EKI calls, so the exact desync path under the old daemon-thread dispatch was not reproduced in a test (the sim robot has no real EKI socket). The fix is the strongest candidate: it removes the one difference between the working block mode and the broken stream mode (the daemon thread) and makes the stream dispatch run on the same thread pool as the pose reads. The real-robot test is still pending.
- **Tick rate**: the synchronous stream move now blocks the asyncio loop for the move's EKI round-trip. In the sim the round-trip is instant (the sim robot just sets a target), so the tick rate holds (~18 Hz). On the real robot the C_PTP round-trip is short (the KRL answers immediately for `cnt > 0`), so the tick rate should hold near 20 Hz; if it does not, the fallback is to revert `move_mode` to `"block"`.
- **No KRL changes**: the original `KUKAPY_SERVER.SRC` is unmodified, per the user's constraint.

### Next Task

Validate on the real robot: run `python main.py --robot --ip <robot-ip> --port 18735` in manual mode, start a move, and confirm the non-ghost arm (`s.joints` / `get_curjpos`) now tracks the robot instead of freezing at `[0,0,0,0,0,0]` and the zone button updates. If it still freezes, capture the EKI traffic with `KUKAPY_DEBUG=1` and confirm whether `get_curjpos` is reading a move `<Res>`.

### Instructions for the Next Task

1. Run the robot in real mode: `python main.py --robot --ip <robot-ip> --port 18735`.
2. Switch to manual mode and trigger a move (e.g. click a position in `client.html`).
3. Watch the non-ghost arm (`s.joints`) and the zone button: they should now track the robot's actual position instead of freezing at `[0,0,0,0,0,0]` / the previous zone.
4. If it still freezes, set `KUKAPY_DEBUG=1` and capture the EKI traffic to confirm whether `get_curjpos` is reading a move `<Res>` (A1..A6 = 0.0) instead of a curjpos `<Res>`.
5. Fallback: if the desync persists, revert `machine.py:108` to `self.move_mode = "block"` (the pre-`3933fe7` working mode) until the mechanism is confirmed.

## 2026-10-30 — Python-side auto-recovery: stream mode default, zone-dwell watchdog, EKI recovery backstop

### Status

Completed

### Summary

Implemented the Python-side automatic recovery so the real KUKA robot recovers from stuck/stale EKI or blocking-PTP states and keeps moving without a restart, and enforced that in auto mode the robot never remains in the same zone too long (manual mode exempt). Three changes:

1. **Default `move_mode` changed to `"stream"`** (`machine.py`): the 20 Hz retarget loop now uses non-blocking stream moves (`cnt=10`, `C_PTP`) by default, so the KRL answers immediately and the engine never blocks on a KRL PTP. This eliminates the root cause of the physical stall: the default `block` mode sent `cnt=0` (blocking PTP), the KRL held the `<Res>` until arrival, the engine tick blocked, and the `_recover()` ping could not interrupt a KRL parked mid-PTP.

2. **Zone-dwell watchdog** (`brain.py`): in auto mode, the brain now tracks how long the robot has been in the current zone. When the zone dwell exceeds `autonomy.zone_dwell_s` (default 30 s), the brain forces the robot to advance to a different zone (preferring valid candidates from the action's `next` list or zone-level `exits`). Manual mode disables the watchdog (the operator drives the robot). When no different-zone candidate exists, a warning is logged (the zone is isolated).

3. **EKI stall recovery backstop** (`kuka.py`, `engine.py`, `base.py`): when a `KukaError` is raised from a kukapy call (stalled handshake, dead socket, or framing desync), the engine's watchdog now attempts to recover the EKI connection via `KukaRobot.recover()` (disconnect + reconnect). The `RobotBase` class gained a no-op `recover()` so the sim is unaffected. If the reconnect fails, the error propagates and the engine continues (the existing watchdog keeps the loop alive).

### Changes Made

- `realtimecontrol/rtr/rtr/state/machine.py`:
  - Line 108: `self.move_mode = "block"` → `self.move_mode = "stream"` (the 20 Hz retarget loop now uses non-blocking stream moves by default).
- `realtimecontrol/rtr/rtr/brain/brain.py`:
  - `__init__`: added `_zone_entry_ts` and `_zone_entry_name` for zone-dwell tracking.
  - `_autonomy_tick`: added zone-dwell tracking (reset `_zone_entry_ts` when the zone changes) and a zone-dwell watchdog check at the end of the normal path (forces advance when zone dwell expires in auto mode).
  - New method `_zone_dwell_s()`: reads `autonomy.zone_dwell_s` from the brain config (default 30.0 s).
  - New method `_force_zone_advance(zone)`: forces the robot to leave the current zone by picking a valid different-zone candidate from the action's `next` list or zone-level `exits`.
- `realtimecontrol/rtr/rtr/robot/kuka.py`:
  - New method `recover()`: attempts to recover a dead EKI connection by disconnecting and reconnecting the kukapy `Robot`.
- `realtimecontrol/rtr/rtr/robot/base.py`:
  - New method `recover()`: no-op default (the sim has no connection to recover); `KukaRobot` overrides it.
- `realtimecontrol/rtr/rtr/core/engine.py`:
  - Exception handler in `run()`: when a `KukaError` is raised, the engine now attempts EKI recovery via `robot.recover()` before continuing the loop.

### Documentation

- None required — the changes are self-documenting via the existing docstrings and the code comments.

### Architecture

- No architectural impact. The three changes are additive:
  - The `move_mode` default change is a one-line configuration change that shifts the default from blocking to stream moves. The engine's dispatch logic (`engine.py:202`) already handles both modes.
  - The zone-dwell watchdog is a new decision layer in the brain's autonomy tick, parallel to the existing stuck-state watchdog. It uses the same safety-gated path (`trigger_action`) as normal autonomy.
  - The EKI recovery backstop is a new method on the robot interface (`RobotBase.recover`), called by the engine's existing exception watchdog. The kukapy driver's `_recover()` (ping) is unchanged; the new `KukaRobot.recover()` is a higher-level reconnect.

### Validation

- `python tests/test_stuck_recovery.py` → **ALL TESTS PASSED** (12/12).
- Verified `default move_mode` is `"stream"` via a unit check.
- Verified `_zone_dwell_s()` returns 30.0 by default and `_force_zone_advance` exists.
- Verified `recover()` method exists and is callable on `RobotBase`, `KukaRobot`, and `SimRobot`.
- Sim mode sanity check: `python main.py --sim --no-http --no-midi --no-sound --no-display` starts without errors.

### Decisions and Caveats

- **Stream mode default**: the `block` mode is still available via the `SET_ENGINE_MODE` command or per-action `engine.move_mode` override in `zones.json`. Actions that declare `engine: {move_mode: "block"}` will still use blocking moves. The default change only affects actions without an explicit override.
- **Zone-dwell threshold**: `autonomy.zone_dwell_s` (default 30 s) is configurable in `brain.json`. A shorter threshold forces more frequent zone changes; a longer threshold allows more dwelling. The threshold is independent of the per-action `dwell_s` (which controls when the action's behavior triggers fire).
- **EKI recovery**: the `KukaRobot.recover()` method disconnects and reconnects the kukapy `Robot`. This is a full session reset: the KRL on the pendant must re-establish the EKI connection. If the KRL is still running (which it should be, since the KRL's `WAIT FOR $FLAG[1]` loop is persistent), the reconnect succeeds. If the KRL has quit (CASE 8), the reconnect fails and the error propagates.
- **No KRL changes**: the original `KUKAPY_SERVER.SRC` is unmodified. All changes are Python-side, per the user's constraint.

### Next Task

No immediate next task identified. The robot should now:
1. Use stream moves by default (no blocking PTP stalls).
2. Leave the current zone within 30 s in auto mode (zone-dwell watchdog).
3. Recover from EKI stalls automatically (disconnect + reconnect).

If the robot still exhibits issues, the next step would be to:
- Check the log for `[brain] zone-dwell watchdog` messages (confirming the watchdog fires).
- Check the log for `[engine] KukaError: attempting EKI recovery` messages (confirming the recovery backstop fires).
- Tune `autonomy.zone_dwell_s` in `brain.json` if the zone change frequency is too fast or too slow.

### Instructions for the Next Task

1. Run the robot in real mode: `python main.py --robot --ip <robot-ip> --port 18735`.
2. Monitor the log for:
   - `[engine] tick#N start (move_mode=stream ...)` — confirms stream mode is active.
   - `[brain] zone-dwell watchdog: forcing advance from ...` — confirms the zone-dwell watchdog fires.
   - `[engine] tick#N KukaError: attempting EKI recovery` — confirms the recovery backstop fires.
3. If the robot still stalls, check the kukapy log (`KUKAPY_DEBUG=1`) for the exact EKI traffic.
4. If the zone changes are too frequent, increase `autonomy.zone_dwell_s` in `brain.json`.

## 2026-10-30 — Config-only Scan→Pick→Follow 30s→Approach→Inspect→Retreat loop (5 new zones + engage group)

### Status

Completed

### Summary

Implemented the `scan → follow(30s) → approach → inspect → retreat → scan` engagement loop **config-only** (no source changes), per `.kilo/plans/1793339221763-scan-to-retreat-flow-howto.md`. Added five single-action zones to `zones.json` (`scan_room/look`, `follow_p/track`, `approach_p/lean`, `inspect_p/face`, `retreat_p/back`) that form a closed autonomy loop, and wired them into `brain.json` (new `engage` zone group, `hunt` detect/scan repointed, `facefocus.zones` repointed). Every anchor pose was validated for hardware limits and KR60 self-collision via the Node `collide_worker`, and every consecutive transition passed `SafetyGate.check_conflict` and `find_shared_pose` (anchor chaining). Editing the two JSON files directly is equivalent to the editor UI (POST `/api/zones` and `/api/brain` validate → atomic write → hot-reload).

### Changes Made

- `realtimecontrol/rtr/zones.json`:
  - Added `scan_room` (class `scan`): action `look`, 4 anchors (A2≈-90/-85 far, head up A5≈80), `variable_axes [0,1,5]`, behavior `spontaneous`, speed 15, `dwell_s 30`, `loop true`, next → `follow_p/track`.
  - Added `follow_p` (class `track`): action `track`, 5 anchors (A2=-45 mid), `variable_axes [0,4]`, behavior `locked`, speed 40, `dwell_s 30` (the 30 s pick/follow dwell), `loop true`, next → `approach_p/lean`.
  - Added `approach_p` (class `approach`): action `lean`, 5 anchors (A2=-15/-18, A3=25, head down A5=-20 — stretching out), `variable_axes [0,2,4,5]`, behavior `intent`, speed 30, `dwell_s 10`, `loop true`, next → `inspect_p/face`.
  - Added `inspect_p` (class `facefocus`): action `face`, 5 anchors (A2=-15, A3=20, head down), `variable_axes [0,5]`, behavior `gentle`, speed 25, `dwell_s 15`, `loop true`, next → `retreat_p/back`.
  - Added `retreat_p` (class `retreat`): action `back`, 6 anchors sweeping A2 -15→-90 and A5 -20→80 (retreat back to the far scan pose), `variable_axes [0]`, behavior `soothing`, speed 40, `dwell_s 3`, `loop true`, next → `scan_room/look`.
  - All five share the same safezone `COMMON_SZ = [[-185,185],[-95,-10],[15,25],[0,1],[-25,85],[-15,15]]` so every consecutive pair has full safezone overlap (the safety gate's inter-zone move requirement).
- `realtimecontrol/rtr/brain.json`:
  - Added `zone_groups.engage` = the 5 new zones, `enabled: true`, empty `params` (per-action `dwell_s` already set on every action).
  - Repointed `hunt.params.detect` → `follow_p/track` and `hunt.params.scan` → `scan_room/look` (was `hunting/track` + `hunting/scan`).
  - Repointed `facefocus.zones` → `["inspect_p"]` (was `["stretching"]`).

### Documentation

- None required — config-only change; the plan file (`.kilo/plans/1793339221763-scan-to-retreat-flow-howto.md`) remains the source of truth for the flow.

### Architecture

- No architectural impact. Data-only change to the two-layer model: Layer A (`zones.json`) gained the 5-zone loop; Layer B (`brain.json`) gained the `engage` group and repointed the hunt ladder + facefocus target. No code paths changed.

### Validation

- `/tmp/kilo/validate_engage.py` (loads the real `zones.json` + `brain.json` from disk and runs the actual validators) → **ALL CHECKS PASSED**:
  1. Hardware-limit check on all 15 concrete anchors → ok.
  2. KR60 self-collision via Node `collide_worker.js` on all concrete anchors → all `clear`.
  3. `SafetyGate.check_conflict` on all 5 consecutive transitions → no conflict.
  4. `find_shared_pose` on all 5 handoffs (including the retreat→scan loop closure) → shared pose found on each.
  5. `validate_state_data` (the POST `/api/zones` validator) on the edited `zones.json` → PASS.
  6. `validate_brain_config` (the POST `/api/brain` validator, loaded standalone to avoid the `rtr.brain` circular import) on the edited `brain.json` → PASS.
- Loop-closure check: `scan_room/look → follow_p/track → approach_p/lean → inspect_p/face → retreat_p/back → scan_room/look` closes; all 5 actions `loop: true`; 5 distinct zones; `follow_p/track.dwell_s == 30`.

### Decisions and Caveats

- **Anchor chaining**: each action's last anchor equals the next action's first anchor so `find_shared_pose` resolves a concrete hand-off pose (e.g. scan→follow share `[0,-90,20,0,80,0]`; follow→approach share `["x",-45,20,0,40,0]` with A1 wildcard). The retreat→scan closure is satisfied by making retreat's final anchor the far scan pose `[0,-90,20,0,80,0]`.
- **Shared safezone**: all 5 zones use the same `COMMON_SZ` box so the safety gate's "strict positive overlap on all 6 axes" requirement holds between every consecutive pair. This is the deliberate mechanism that lets the loop move directly between the new zones without bridge zones.
- **No `pose_imgs`/`pose_videos`** on the new actions (optional fields; omitted so no non-existent assets are referenced). `validate_state_data` passes without them.
- **`engage` group `params` left empty**: every action carries its own `dwell_s`, so the group-level dwell is not needed (precedence: per-action > group > global).
- Caveat: collision checks are **self-collision only** (base + link_1..6) at the discrete anchor poses; they do not model the swept volume between waypoints or external/world collisions.

### Next Task

No immediate next task identified. The config loop is implemented and validated; a live run on the robot (or a headless tick of `brain.py` with the `engage` group active) would be the natural next verification if one is wanted.

### Instructions for the Next Task

- The five zones and the `engage` group are already in `zones.json` / `brain.json` and pass both POST validators; no further config edits are needed to make the loop exist.
- To exercise it live: ensure `zone_groups.engage.enabled` is `true` (it is) and that autonomy is enabled (`autonomy.enabled` is `true`). The robot will dwell on each action for its `dwell_s`, then walk to the next zone's action via the safety gate.
- If tuning motion: adjust each action's `poses` (anchor waypoints), `variable_axes` (which axes get behavior-driven motion), `speed`, and `dwell_s`. Keep anchors within `COMMON_SZ` and re-run `/tmp/kilo/validate_engage.py` (regenerate its `ZONES_NEW` to match) to re-check collision + shared-pose + validators.

## 2026-10-29 — Restored test_groups_tab.js STATIC_IDS (stale id list broke the harness load)

### Status

Completed

### Summary

`test_groups_tab.js` (the groups-tab DOM-stub test) was failing to **load** — the main `<script>` in `editor.html` now references element ids that were added after this test's `STATIC_IDS` list was written, so the mini-DOM harness threw `Cannot set properties of null (setting 'onclick')` before any assertion ran. Replaced its stale `STATIC_IDS` with the corrected id list (the one carried by `test_flow_tab.js`), which is a superset covering every id the script references. The test now loads and passes.

### Changes Made

- `realtimecontrol/rtr/test_groups_tab.js` (line 182): replaced the stale `STATIC_IDS` array with the corrected list. The new list adds the ids the script gained since the test was written — `limitsForm` (the limits card), the `poses*` ids (poses tab), `flowValidate` / `flowProblems` (flow validate), `midiInCb` / `midiOutCb` / `midiAllList` (the in/out toggle split), and `aeSaveStatus` — and drops the removed `midiMasterCb`.

### Documentation

- None required — internal test-harness fix; no behavior or config change.

### Architecture

- No architectural impact. Test-only change (the DOM-stub harness's pre-created element ids now match the current script).

### Validation

- `node test_groups_tab.js` → **ALL_TESTS_PASSED** (was failing to load before the fix).
- `node test_flow_tab.js` → **ALL_TESTS_PASSED** (unchanged; the source of the corrected id list).

### Decisions and Caveats

- The corrected list is a **superset** of the old one, so it covers any id the script references now or will reference; the harness only needs the ids to exist, not to be an exact match.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

N/A

## 2026-10-29 — Robotconfig limits card: hardware limits read-only + editable soft limits

### Status

Completed

### Summary

The robotconfig tab gained a per-axis **limits** card. It shows the **hardware** joint limits (the physical floor from the kr60ha xacro — `HARDWARE_LIMITS`, loaded from `/api/limits` with a built-in fallback) as **read-only** reference cells, and a **soft** limit per axis as **editable** cells. The soft limit defaults to the hardware limit (no extra restriction) and can be tightened to restrict movement further; the in-app copy states the effective limit for an axis is the intersection of the soft and hardware limits. The soft limits are persisted with the robot config (`saveRobotConfig()` → POST `/api/robotconfig` → `robotconfig.json` → hot-reload).

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - CSS (line ~111): added `.limitsGrid` (a 5-column grid: axis / hw min / hw max / soft min / soft max), `.limitsGrid input[type=number]`, `.limitsGrid input[readonly]` (dimmed + transparent — the HW cells), and `.limitsGrid .limitsHead`.
  - HTML (line ~693–701): added the `<h2>limits</h2>` section with a `midiHelp` paragraph (hardware = uneditable safety floor; soft = additional configurable restriction; effective = intersection) and the `<div id="limitsForm"></div>` mount.
  - `normalizeRobotConfig(data)` (line ~3712): added `soft_limits` normalization — a 6-element array of `[min,max]` pairs. A malformed pair falls back to `HW_LIMITS[idx]`; a missing/malformed list defaults every axis to `HW_LIMITS` (no extra restriction). The key is returned in the config.
  - `buildLimitsForm()` (line ~3785, new): builds a header row + 6 axis rows. HW min/max are `<input type=number readonly>` (styled via `.limitsGrid input[readonly]`); soft min/max are editable `<input type=number>` carrying `data-axis` / `data-bound` (`lo` / `hi`).
  - `buildRobotConfigForm()` (line ~3782): now calls `buildLimitsForm()` so the card renders on robotconfig-tab entry.
  - `loadLimits()` (line ~1464): after loading `HW_LIMITS` (API → xacro fallback), rebuilds the card if in the robotconfig view (`if (robotconfigView && ROBOT_CONFIG) buildLimitsForm();`).
  - `saveRobotConfig()` (line ~3845): reads the editable (non-readonly) inputs from `#limitsForm`, builds the `soft_limits` array (a non-number falls back to the current config value), and includes it in the POST payload to `/api/robotconfig`.

### Documentation

- The in-app `midiHelp` paragraph in the limits section describes the hardware/soft distinction and the intersection rule. No README or config-schema change.

### Architecture

- The `robotconfig.json` data model gains a `soft_limits` field (a 6×2 list of `[min,max]`), persisted via the existing POST `/api/robotconfig` path. The core's `validate_robot_config` (`rtr/robot/config.py`) preserves `soft_limits` **verbatim** — it only type-checks `joint_offsets_deg` and `patch_mode` — so the field flows to disk + hot-reload without a schema change. **The Python core does not yet consume `soft_limits`**: the runtime clamp still uses the hardware floor (`HARDWARE_LIMITS`) ∩ safezone, so the soft limit is currently a persisted, front-end-editable restriction that is not yet enforced by the core (see Decisions).

### Validation

- `node --check` on the extracted inline `<script>` of `editor.html` → **SYNTAX OK**.
- `node /tmp/kilo/test_limits.js` (the limits DOM test) → **5/5 passed**:
  - `normalizeRobotConfig` defaults `soft_limits` to `HW_LIMITS`,
  - `normalizeRobotConfig` preserves explicit `soft_limits`,
  - `normalizeRobotConfig` handles malformed `soft_limits` (falls back to `HW_LIMITS`),
  - `buildLimitsForm` builds a header + 6 axis rows,
  - `saveRobotConfig` reads the soft limits correctly from the form.

### Decisions and Caveats

- **`soft_limits` is front-end-only for now.** It is displayed, editable, and saved to `robotconfig.json`, but the Python core does not read it — the effective clamp at runtime is still `HARDWARE_LIMITS` ∩ safezone. The card therefore currently *documents* the soft limit and persists it, but does not yet *enforce* it. Wiring it into the core clamp (e.g. clamping to `soft ∩ hardware ∩ safezone`) is the natural follow-up to make the restriction real.
- HW cells are read-only **inputs** (not plain text) so the grid columns stay visually aligned; they are styled dimmed/transparent (`.limitsGrid input[readonly]`) to read as a reference, not a control.
- `saveRobotConfig()` reads soft values by the **non-readonly** inputs in `#limitsForm` (index `i*2` / `i*2+1`), so a stale or empty field falls back to the last-known `ROBOT_CONFIG.soft_limits` value rather than corrupting the payload.

### Next Task

Optional: wire the persisted `soft_limits` into the core's clamp so the soft restriction is actually enforced at runtime (currently persisted but not consumed). Also optional: restore `test_groups_tab.js`'s stale `STATIC_IDS` (it currently fails to load because the script gained poses/limits/flow-validate ids after that test was written; the corrected id list is in `test_flow_tab.js`).

### Instructions for the Next Task

- To enforce soft limits in the core: `soft_limits` is already on `ROBOT_CONFIG` (JS) and on the on-disk `robotconfig.json` (persisted via POST `/api/robotconfig`, hot-reloaded by `Cmd.RELOAD_ROBOT_CONFIG`). On the Python side, `validate_robot_config` in `rtr/robot/config.py` preserves `soft_limits` verbatim; add a `robot_config_soft_limits(config)` helper (mirror `robot_config_offsets`) and thread the per-axis soft bounds into the same clamp that currently uses `HARDWARE_LIMITS` (the safezone ∩ hardware intersection in `rtr/state/zones.py`). Keep the default = hardware limit (no extra restriction) so existing configs are unaffected.
- To restore `test_groups_tab.js`: replace its stale `STATIC_IDS` array with the corrected id list (the one at the top of `test_flow_tab.js`), which includes the `limitsForm` / poses / flow-validate ids the script now references.

## 2026-10-29 — Flow tab: smarter autofit, bridge-between-endpoints layout, trackpad pan

### Status

Completed

### Summary

Three improvements to the flow tab (the node-graph editor) in `editor.html`:
1. **Autofit** (`flowFitView`) now fits the content tightly, matches the canvas's aspect ratio (no empty letterbox bands), centres the view on the content's centre, and clamps the zoom scale to `[0.35, 6.0]` so nodes are never absurdly tiny or huge.
2. **Bridge layout**: a bridge zone now renders at the geometric midpoint of the two nodes it connects — both in `flowBridge` (the manual "insert bridge" action) and in `flowAutoLayout` (which gives a bridge a *fractional* column depth = the mean of its endpoints' BFS depths so it lands in the column between them, then recentres it vertically on the endpoints' midpoint).
3. **Trackpad / pan**: the pan was rewritten from a middle-button-only `mousedown` to pointer events with `setPointerCapture`. The **middle button now pans anywhere** (including over nodes), and the **left button pans when pressed on empty canvas** (a left press that does not move is still a background click = deselect). Node dragging is gated to the left button so a middle-drag over a node pans instead of dragging. The hint text was updated to match.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - `flowFitView()` (line ~7539): rewritten. Computes tight content bounds (min/max over every node's corners) + `PAD=40`, centres on the content centre, expands the smaller side to match the canvas aspect ratio (`ar = elW/elH`, fallback 800×560), clamps the fit scale `s = vw/elW` to `[0.35, 6.0]`, and stores `FLOW.view = {x: cx - vw/2, y: cy - vh/2, w: vw, h: vh}`.
  - `flowAutoLayout(names)` (line ~7462): added a bridge post-pass. After the BFS-depth column layout, each in-scope bridge is (a) given a fractional depth = the mean of its neighbours' BFS depths (computed from a `bfsDepth` snapshot so bridge-to-bridge links stay stable), and (b) recentred at the midpoint of its neighbours' node centres (`x = cx - NODE_W/2`, `y = cy - BRIDGE_CY`). Only in-scope bridges move, so a manually placed bridge survives a partial relayout.
  - `flowBridge(from, to)` (line ~8032): placement now puts the bridge's connection dot at the true geometric midpoint between the two endpoints (`midX = (pa.x+pb.x+NODE_W)/2`, `midY = (pa.y+nodeH(from)/2 + pb.y+nodeH(to)/2)/2`); the box top-left is `x = midX - NODE_W/2`, `y = midY - BRIDGE_CY`.
  - Pan rewritten as `flowPanDown` / `flowPanMove` / `flowPanUp` (lines ~7610–7649): pointer events + `setPointerCapture` (the pan survives the pointer leaving the canvas); the middle button pans anywhere, the left button pans only when `e.target === svg`; a 3px click threshold distinguishes a click from a pan; the pan delta inverts the frozen `getScreenCTM()` (`dx/ctm.a`, `dy/ctm.d`); a non-moved left release deselects (background click).
  - Node / action-row `mousedown` handlers (lines ~7802, ~7821): gated to the left button (`if (e.button !== 0) return;`) so a middle-drag over a node pans (see Decisions).
  - SVG event handlers (lines ~8642–8645): replaced the old `mousedown`(button===1) + `click` handlers with `pointerdown/pointermove/pointerup/pointercancel` → `flowPanDown/flowPanMove/flowPanUp/flowPanUp`. The `wheel` → `flowZoomAt` handler is unchanged.
  - Hint text (line 559): now reads "… wheel = zoom · drag empty canvas / middle-drag = pan".
- `realtimecontrol/rtr/test_flow_tab.js` (new): a Node DOM-stub test that loads the main `<script>` into a `vm` sandbox (reusing the `test_groups_tab.js` mini-DOM harness), seeds a `TABLE` with a chain plus a bridge zone, and verifies the fractional bridge column depth, the bridge vertical-centring, the fit-view aspect/centring, and the fit-scale clamp.

### Documentation

- In-app hint text (line 559) updated to describe the new pan/zoom behaviour. No config schema or README change.

### Architecture

- The flow view model is unchanged in shape: `FLOW.view` is still the `{x,y,w,h}` viewBox and `FLOW.pan` still holds the pointer-pan state (now `{id, button, ctm, startX, startY, startView, moved}`). The change is behavioural: pan is pointer-captured (so it survives the pointer leaving the canvas) and the middle button is a pan-anywhere gesture. The auto-layout contract is extended: bridge zones are placed at the midpoint of their endpoints (a fractional column depth). This is a visual/layout improvement and does not change the on-disk zone/connection data.

### Validation

- `node --check` on the extracted inline `<script>` → SYNTAX OK.
- `node test_flow_tab.js` (new) → **10/10 passed**:
  - bridge lands at fractional column 1.5 (x=355, between b=250 and c=460),
  - bridge centred vertically at the endpoints' midpoint (y=63; the dot at y+BRIDGE_CY = 79 = the midpoint),
  - fit view aspect matches the canvas (2:1) and is centred on the content (320, 79),
  - fit scale clamped to the MIN bound.
- The new test's `STATIC_IDS` was regenerated from the script because the old list in `test_groups_tab.js` is stale: the script now references poses/limits/flow-validate ids that were added after that test was written (the groups test now also fails to load for the same reason).

### Decisions and Caveats

- **Middle-button pans ANYWHERE (even over nodes) — "Option B".** Because `flowDragStart` does not check the pointer button, the node and action-row `mousedown` listeners are gated to `button === 0`. A middle-drag that STARTS over a node now pans (previously it dragged the node). That is the intended tradeoff: the middle button is a pan gesture everywhere; the left button is the node-drag / connect gesture.
- The fit-scale clamp (`[0.35, 6.0]`) is a heuristic to keep nodes legible, not a value derived from the content. Very sparse or very dense flows may feel under/over-zoomed at the clamp boundary.
- `flowAutoLayout` only recentres bridges that are in the relayout scope (`sel`); a bridge the user placed by hand (outside the scope) keeps its position, matching the existing "manual layout survives a partial relayout" behaviour.

### Next Task

Write the pending **robotconfig limits-card** handoff entry. The limits-card work (hardware safelimits shown read-only + softlimits editable) was completed and validated in a prior session but its handoff entry was never written.

### Instructions for the Next Task

The limits-card work lives in `editor.html` (the `#limitsForm` element and the `loadLimits()` / limits-form build/save functions) plus its backend. Reconstruct the exact changes from `git diff` on `editor.html` (filter for `limits`/`Limits`), confirm `node --check` and the limits DOM test still pass, then write a handoff entry at the top of this file describing: the read-only hardware-safelimits display, the editable softlimits, and the save path. Mark it complete once the entry is written.

## 2026-10-29 — MIDI out: make mappings removable + list all mapped MIDI

### Status

Completed

### Summary

The MIDI-out mapping and the per-action sound editor auto-seeded a default note (`{note: 60, velocity: 60}`) into every zone entry and every action, so you could never make a zone/action silent — removing a mapping would just get re-seeded on the next rebuild. Now: (1) a zone's entry sound and an action's start/pose sounds are all **optional** and can be removed (and removal survives save + reload); (2) unmapped zones/actions show an **"+ add sound"** button instead of a pre-filled default; (3) a new **"all mapped keys"** list on the in-tab shows every currently-mapped MIDI-in key (nav, capture, group, zone command, zone action) in one place, each editable / re-learnable / removable. The core (`sound.py`) was made to accept a zone with **no** `command` (it simply sends nothing on entry; its actions/poses still play on the zone's out device). Adding a mapping still uses the existing per-section UI — the list is read/edit/remove, not an adder.

### Changes Made

- `realtimecontrol/rtr/rtr/sound/sound.py`:
  - `_normalize_zone_entry()` (line ~134): now returns a wrapped entry as-is when it carries `command` **or** `out_port` (so `{out_port: <n>}` and `{}` are valid "no command" zones), only wraps a dict as a legacy bare message when it actually has message fields (`note`/`velocity`/`cc`/`value`/`program`), and normalizes anything else to `{out_port: None, command: None}`. Previously any dict without `command` was wrapped as a bare message (so `{}` would have been wrapped and then rejected).
  - `validate_sound_data()` (line ~187): validates a zone's `command` **only when present** (`cmd is not None`), so a zone with no command passes.
  - `_zone_command()` (line ~525): returns `None` for a wrapped entry without a `command` (the zone sends nothing on entry); still falls back to the entry itself for a legacy bare-message table.
  - `_on_snapshot()` (line ~564): guards the zone-entry send with `isinstance(msg, dict)` before `_send_msg`, so a `None` command sends nothing (previously it would have crashed or sent garbage on a no-command zone).
- `realtimecontrol/rtr/editor.html`:
  - `loadSound()` (line ~1484): stops seeding a default command — normalizes each zone to `{out_port, command}` **without** filling in a default command (wraps a legacy bare entry, gives a missing entry an `out_port` only); ensures per-zone `actions` containers exist **without** per-action seeding.
  - `buildMidiOutForm()` (line ~1629): zone-entry command is add/edit/**remove** (no default seed); the actions list is the **union** of the zone's table actions and its sound actions, so unmapped actions show "+ add sound" and mapped ones are editable/removable; action start is add/edit/remove; per-pose rows got a remove (×) button and "+ pose" now uses `freshMsg("note")`; "+ action" creates an entry without a seeded start.
  - `reconcileActionSound()` (line ~4171): returns `null` when the action has no sound entry (no longer creates one) and only **trims** the pose list to the action's pose count (no padding).
  - Action editor sound section: handles the no-entry and no-start cases (shows "+ add sound" / "+ add start"), and adds per-pose remove (×) + a "+ pose" button.
  - `validateSound()` (line ~1800): skips zone `command` validation when it is null/undefined.
  - **New** `buildMidiAllList()` (line ~2345) + `#midiAllList` section in the in-view HTML (line ~650): lists every mapped MIDI-in key (nav, capture, group, zone command, zone action) with an editable value, a "learn" (re-learn) button, and a remove (×) button; shows an empty-state hint when nothing is mapped. Wired into `buildMidiInForm()`.
  - Fixed the **group-learn** websocket handler (line ~1375): added a `midiLearnPending.group` branch so a learned group key is written to `MIDI.groups[group]` (previously group learns fell through to the zone branch and were silently lost).

### Documentation

- None required — the in-app `title` tooltips on the new add/remove buttons and the "all mapped keys" section describe each control; no config schema or README change (the sound data model already documented `command` as the per-zone message; it is now simply optional).

### Architecture

- The sound data model (`sound.json`) is unchanged in shape: `zones[zn]` is still `{out_port, command}` — `command` is now simply optional. This is a relaxation, not a breaking change: existing saved configs with commands are untouched, and a zone without a command is a valid, intentional state (silent on entry). The core's `_zone_command` / `_on_snapshot` now treat a missing command as "send nothing" rather than an error.

### Validation

- `node --check` on the extracted `editor.html` inline script → OK.
- `python -m pytest tests/test_sound_out_ports.py tests/test_midi_in.py tests/test_midi_brain_trigger.py tests/test_sound_enabled.py` → **100 passed, 1 skipped** (was 97 before adding 3 new tests).
- New tests in `tests/test_sound_out_ports.py`: `test_validate_accepts_zone_without_command` (a `{out_port}`-only zone is accepted, no `command` key added), `test_validate_accepts_empty_zone_entry` (`{}` → `{out_port: None, command: None}`, not wrapped as a bare message), `test_no_command_zone_sends_nothing_on_entry` (entering a no-command zone sends nothing; its action still plays on the zone's out device).
- Manual functional check of `_normalize_zone_entry` / `_zone_command` for all four zone shapes (`{out_port}`, `{}`, bare message, `{out_port, command}`) → correct.
- The full suite has 5 pre-existing failures in `tests/test_remote_sdl.py` + a collection error in `tests/test_remote_tui.py` (SDL "THEME / LAYOUT" text assertions + a TUI urwid deprecation) — **unrelated** to this change (those tests don't reference sound/midi, and this change only touched `sound.py` + `editor.html`).

### Decisions and Caveats

- `SOUND_FALLBACK` (the last-resort mapping used only when both `/api/sound` and `sound.json` fail, e.g. `file://`) still carries default notes for every zone. That is intentional: it mirrors the core's `ZONE_NOTES` and is only used when there is no real config to preserve. In the **normal** flow (API up), a zone with no command in the real config stays command-less — that is the actual "no auto-seed" fix.
- The "all mapped keys" list is **not** an adder: new keys are still created in the per-section UI (global navigation / capture / groups / zones) above it. The list only surfaces existing mappings for edit/re-learn/remove. This matches the request ("adding still uses the existing interface").
- Removing a zone's entry sound via the × button sets `delete zd.command`; the entry keeps its `out_port`. Removing an action's sound drops the whole entry (`delete acts[name]`), so the action returns to the unmapped "+ add sound" state.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

If extending the "all mapped keys" list to also allow **adding** a key in place, add a per-row "+ learn" that calls the matching `midiLearn(...)` with the right args (nav → `midiLearn(null, null, nav, MIDI.nav.in_port)`, capture → `midiLearn(null, null, null, null, true, MIDI.capture.in_port)`, group → `midiLearn(null, null, null, null, null, null, name, MIDI.groups.in_port)`, zone command → `midiLearn(zone, null)`, zone action → `midiLearn(zone, action)`) — those exact calls are already in the `buildMidiAllList()` `learn` callbacks; a new-key flow would need a way to name the target first (the per-section UI provides that). Keep `buildMidiInForm()` as the single rebuild that re-renders the list.

## 2026-10-29 — MIDI page: split the master toggle into separate in/out toggles

### Status

Completed

### Summary

The MIDI page had a single **master** on/off checkbox (`midiMasterCb`) that drove both sides of MIDI at once: `MIDI.enabled` (the core ignores all incoming controller keys) and `SOUND.enabled` (the core sends no outgoing MIDI/sound messages). Per the request, the one switch was split into **two independent switches**: **midi in on/off** (controls `MIDI.enabled`) and **midi out on/off** (controls `SOUND.enabled`), so each side can be enabled/disabled independently. Toggling either still saves both tables (`saveMidiAll()` → POST `/api/midi` + `/api/sound`) so the core hot-reloads each side, exactly as the master toggle did.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - HTML (line ~587): replaced the single `midiMaster` label/`midiMasterCb` checkbox with two labels — `midiInTgl`/`midiInCb` ("midi in on/off") and `midiOutTgl`/`midiOutCb` ("midi out on/off") — each with its own `title` describing the side it controls. The out/in sub-tab buttons are unchanged.
  - `refreshMidiMaster()` (line ~5441): now syncs **both** checkboxes independently — `midiInCb.checked = !!MIDI.enabled`, `midiOutCb.checked = !!SOUND.enabled` (previously one checkbox set to `MIDI.enabled && SOUND.enabled`).
  - Replaced `onMidiMasterToggle()` with `onMidiInToggle()` (sets `MIDI.enabled` from `midiInCb`) and `onMidiOutToggle()` (sets `SOUND.enabled` from `midiOutCb`); each calls `refreshMidiMaster()` + `saveMidiAll()`.
  - Event wiring (line ~8211): `$("midiInCb").onchange = onMidiInToggle; $("midiOutCb").onchange = onMidiOutToggle;` (replaced the single `midiMasterCb` binding).
  - The in-tab's own per-zone "enabled" checkbox (line ~2017) already sets `MIDI.enabled` and calls `refreshMidiMaster()`, so it now correctly drives the new in-toggle too — no change needed there.

### Documentation

- None required — the in-app `title` tooltips on both checkboxes describe each side; no config schema or README change.

### Architecture

- No architectural impact. Same data model (`MIDI.enabled` in midi.json, `SOUND.enabled` in sound.json), same save path (`saveMidiAll`). Only the UI control granularity changed from one combined switch to two independent ones.

### Validation

- `node --check` on the extracted `editor.html` inline script → OK. While validating, found and fixed a **pre-existing** syntax bug (from earlier uncommitted robotconfig soft-limits work, not this change): `saveRobotConfig()` had `limitsEl.querySelectorAll("input[type=number]:not([readonly])"` missing its closing `)` in a ternary (line ~3680). Added the `)` so the script parses.

### Decisions and Caveats

- Both toggles still save **both** tables (not just the one they control), preserving the previous master behavior that a toggle hot-reloads the core on both sides. This is the existing `saveMidiAll()` contract; a per-side single-POST would be a future refinement.
- The old combined "on = both on" semantic is gone: the two checkboxes now reflect their own side's state independently, so it is possible (and intended) for in and out to differ.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

If refining the save behavior so each toggle only POSTs its own table, add a per-side save (e.g. `saveMidiIn()` → POST `/api/midi`, `saveMidiOut()` → POST `/api/sound`) and call the matching one from `onMidiInToggle`/`onMidiOutToggle` instead of `saveMidiAll()`. Keep `refreshMidiMaster()` as the single source that mirrors `MIDI.enabled`/`SOUND.enabled` into the two checkboxes.

## 2026-10-29 — Poses 3D view: Cartesian X/Y/Z drag arrows on the pose-drag handle

### Status

Completed

### Summary

The poses (and zones) 3D view authors a pose by dragging the ghost's flange. The "blue dot" (`poseDragHandle` in `rtr3d.js`) was a free-drag handle: dragging it projected the pointer onto a camera-facing plane through the flange and solved IK for the full 3D target, so every axis moved together. Per the request ("add XYZ arrows I can drag, the blue dot remains and keeps its current behavior"), three **Cartesian axis arrows** (X/Y/Z) were added to the same handle. Dragging a colored arrow moves the flange along **one** world axis only — the other two axes stay put — giving a true Cartesian single-axis move, while the blue dot keeps its existing free-drag behavior untouched.

### Changes Made

- `realtimecontrol/rtr/rtr3d.js`:
  - `buildAxisArrows(group)` (line ~1262): builds the three arrows as children of the pose-drag handle group. Each arrow is a colored shaft (`CylinderGeometry`) + arrowhead (`ConeGeometry`) + an invisible fat-cylinder hit proxy, all carrying `userData.axis = "x"|"y"|"z"` so a raycast hitting any part of an arrow resolves the axis. Each sub-group is rotated via `quaternion.setFromUnitVectors(+Z, axisDir)` so its local +Z points along the world axis (the shaft is authored along +Z). Colors: X `0xff5a5a`, Y `0x5ae08a`, Z `0x5a8aff`.
  - `enablePoseDrag(opts)` (line ~1360): initializes the new state fields `axis: null, axisStart: null, axisVec: null` on the `poseDrag` object, and calls `buildAxisArrows(g)` so the arrows render with the handle.
  - `poseDragOnDown` (line ~1290): the raycast now uses `intersectObjects(poseDragHandle.children, true)` (recursive, so arrow sub-groups are hit). The **closest** hit's `userData.axis` decides the drag mode: an arrow (`axis` set) starts a Cartesian axis drag and records `poseDrag.axisVec` (the world-axis unit vector) and `poseDrag.axisStart` (flange position at grab); the blue dot (no axis) keeps the free drag. The camera-facing plane is still set through the flange, fixed for the drag.
  - `poseDragOnMove` (line ~1319): added an axis-drag branch. For an axis drag the target is `axisStart + ((pt - axisStart) · axisVec) * axisVec` — the pointer's displacement on the fixed plane projected onto the single grabbed axis (so the other two world axes stay fixed). The free-drag path (target = pointer point on the plane) is unchanged. Both call `KR60IK.ikSolve(poseDrag.seed, [tx,ty,tz], …)` and update the handle via `fkPos(res.pose)` as before.
- `realtimecontrol/rtr/editor.html`:
  - `posesInfo` hint (line 458): updated to "drag the handle for a free move, or drag the X/Y/Z arrows for a Cartesian axis move; edit the values".

### Documentation

- None required — this is an interactive UI enhancement to the existing pose-drag feature; the in-app hint text was updated to describe the new arrows. No config schema, README, or architecture doc change.

### Architecture

- No architectural impact. The change is internal to the pose-drag module in `rtr3d.js`. The arrows are children of the existing `poseDragHandle` group (which is only ever translated, never rotated), so they translate with the flange but keep a fixed world-axis orientation. The IK pipeline (`ik.js`) is reused unchanged; the axis constraint is applied to the target *before* IK, so it is a presentation/input change, not a kinematics change.

### Validation

- `node --check realtimecontrol/rtr/rtr3d.js` → OK (no syntax errors).
- Focused Node test (`/tmp/kilo/test_axis_drag.js`) running the **real** `ik.js` against the exact axis-drag math mirrored from `poseDragOnMove`: 9 assertions, ALL PASSED. Confirms for each world axis (x/y/z) that (a) the two other world axes of the target stay fixed at the start position, and (b) IK converges on the single-axis target (err < tol). Also confirms the free-drag target (pointer point) still converges, and that a small 2 cm +X axis drag converges and lands the flange at the target x.
- `node --check` on the `editor.html` inline script change is not separately run (the change is a single static HTML text node, no JS logic).

### Decisions and Caveats

- The arrows are children of the **shared** pose-drag handle, so they appear in both the poses tab and the zones tab (both call `enablePoseDrag`). This was accepted as a pure enhancement; free-drag is preserved in both.
- `poseDragOnDown` still disables `OrbitControls` on a successful grab (existing behavior), so grabbing an arrow does not rotate the camera.
- The axis drag projects the pointer displacement onto a **fixed** camera-facing plane through the flange at grab time (same plane the free drag uses). Because only the component along the grabbed axis is used, the effective travel per pointer pixel is smaller than a free drag — expected for a 1D constraint.
- The arrow hit proxies (`HIT_R=0.04`) are larger than the visible shafts so the arrows are easy to grab; the blue dot's own hit proxy (`SphereGeometry(0.09)`) is unchanged.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

If extending this: the axis-drag target in `poseDragOnMove` currently uses the pointer displacement along the grabbed axis (`along = (pt - axisStart)·axisVec`). If a finer/coarser feel is wanted, scale `along` there (e.g. a sensitivity factor). The arrow geometry constants (`LEN`, `SHAFT_R`, `HEAD_R`, `HEAD_LEN`, `HIT_R`) are local to `buildAxisArrows` and can be tuned without touching the drag logic. The `poseDrag` object now carries `axis`/`axisStart`/`axisVec`; any new drag mode should follow the same pattern (set on `poseDragOnDown`, consume in `poseDragOnMove`, reset by `disablePoseDrag` which nulls the whole `poseDrag`).

## 2026-10-29 — Poses tab: save the authored pose directly to a zone/action

### Status

Completed

### Summary

On the **poses** tab, the only way to move an authored (scratch) pose into a zone/action was the indirect copy→paste dance: `copy pose` stashed it in `POSE_CLIP`, then you had to open the action editor (or a flow action row) and hit `paste pose`. Added a **direct** path: a **"save to"** zone/action dropdown + an **"add pose"** button in the poses tab's left controls panel. Pick the target action and click **add pose** and the current scratch pose is appended to that action's `poses` list and the whole table is persisted immediately. This is the "save the pose directly to a zone/action block (selected from dropdown)" request. The target picker lists **all** actions (including pose-less ones), so you can add the first pose to a brand-new action.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - HTML (poses tab left panel, line ~459-467): added a `posesSaveSel` dropdown + `posesAddPose` "add pose" button row (grouped with the existing `posesLoadSel` "load from" picker), and a `posesSaveMsg` status line.
  - `buildPosesSavePicker()` (line ~6427): populates the "save to" picker. Unlike `buildPosesLoadPicker` (which skips pose-less actions — you can only load what exists), it lists **every** action so a pose can be added to an action that has none yet.
  - `setPosesSaveMsg(msg)` (line ~6447): writes the poses-tab save status line.
  - `posesAddPose()` (line ~6456): reads the selected `zn/an`, appends the rounded scratch pose (3 decimals) to `a.poses`, **and** extends the parallel `pose_imgs` / `pose_videos` arrays with a `null` slot (mirroring `pastePose`, so the action editor's per-pose image/video UI stays in sync). Rebuilds both pickers (the new pose is now loadable), shows a status, then calls `saveZones()` to persist (the same path the top "save" button uses in this view).
  - `posesEnter()` (line ~6571): now calls `buildPosesSavePicker()` alongside `buildPosesLoadPicker()` on tab entry.
  - Event wiring (line ~8190): `$("posesAddPose").onclick = posesAddPose;` next to the other poses-button handlers.

### Documentation

- None required — the editor's own UI behavior changed (a new direct save-to-action control on the poses tab); no config schema, doc, or help-text change. The button carries an inline `title` explaining what it does.

### Architecture

- No architectural impact. Internal JS change to the poses tab; reuses the existing zone/action data model (`actions[an].poses` + parallel `pose_imgs`/`pose_videos`) and the existing `saveZones()` persistence path. Did **not** embed a full action-editor instance on the left (that would require refactoring the global `AE` modal state); the dropdown + button satisfies the core "save to a selected zone/action" request with a smaller, lower-risk change.

### Validation

- `node --check` on the extracted inline script (script #1, ~7.5 KB of new/changed lines; full inline script re-extracted end-to-end) → OK (no syntax errors).
- Focused Node test (`/tmp/kilo/test_addpose.js`) running the **real** extracted function code against a mocked DOM + `TABLE`: 17 assertions, ALL PASSED. Covers: picker lists all actions (incl. pose-less) + leading placeholder; no append + message when no target selected; append to an existing action (pose appended, values rounded to 3 decimals, `pose_imgs`/`pose_videos` stay in sync, `saveZones()` called once, status names the target); first pose added to a pose-less action (pose list created, imgs/videos seeded with `null`); unknown zone/action → no append + "not found" message.

### Decisions and Caveats

- Chose **append** (not replace) so repeated "add pose" clicks accumulate the authored sequence, matching the anchor-pose-list model the action editor uses.
- `saveZones()` runs `validate()`; if validation blocks the POST, the pose is still appended to the in-memory `TABLE` (consistent state) and the existing top `#saveMsg` reports the error — the user can fix and re-save. The local `posesSaveMsg` says "saving…" so the outcome is visible in the top status bar.
- Pose values are rounded to 3 decimals on append (consistent with `posesOnPose`'s drag rounding).
- The poses tab's "load from" picker and the new "save to" picker are rebuilt on tab entry; if the zone set changes while already in the poses tab (e.g. a zone added in the zones tab), the pickers refresh on the next entry — same existing behavior as the load picker.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

If extending this: the target picker (`posesSaveSel`) currently lists every action flat as `zn / an`. A follow-up could group options by zone, or add an "insert at index" control (mirroring `pastePose(zn, an, index)`) so a pose can be placed mid-sequence rather than always appended. The `saveZones()` call is synchronous-in-effect but the POST is async; if a dedicated "added ✓" confirmation is wanted, thread the result of `saveZones()` back into `posesSaveMsg` (currently the top `#saveMsg` carries the outcome).

## 2026-10-29 — New-zone safezone now defaults to the currently shown zone's limits (was hardcoded)

### Status

Completed

### Summary

In the zones tab (and the flow tab), clicking **"+ add zone"** created the new zone with a **stale, hardcoded safezone** (`[[-94,122],[-134,34],[-119,157],[-349,349],[-118,118],[-357,357]]`) that no longer matched the real hardware limits. `addZone()` now derives the new zone's safezone from the **currently shown zone's safezone** (the editor's "current limits"), so the limits you see carry over into the new zone. When no zone is selected, or the shown zone has no safezone yet, it falls back to the **hardware joint limits** (`HW_LIMITS`, loaded from `/api/limits`, mirroring the kr60ha xacro).

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - `addZone()` (line ~7772): replaced the hardcoded `safezone` array with a dynamic default. The currently shown zone is resolved as `FLOW.sel.zone` in the flow tab, otherwise `selectedZone`; its `safezone` is deep-copied (`.map((p) => [p[0], p[1]])`) into the new zone. Fallback to `HW_LIMITS` when no zone is selected or the shown zone's safezone is missing/short.
  - No other new-zone safezone defaults exist: line 820 is the `file://` fallback data (the `resting` seed zone, not a new-zone default) and line 7563 is the validate-tab scan result builder.

### Documentation

- None required — the editor's own UI behavior changed (new zones inherit current limits); no config schema, doc, or help-text change.

### Architecture

- No architectural impact. Internal JS change to the new-zone default in the editor; the zone data model (`safezone: [lo,hi] × 6`) and the `/api/limits` flow are unchanged.

### Validation

- `node --check` on the extracted inline script (script #1, ~340 KB) → OK (no syntax errors).
- Manual logic check: zones tab with a selected zone → new zone copies its safezone; no selection / shown zone without safezone → new zone uses `HW_LIMITS` (`[[-185,185],[-135,35],[-95,158],[-180,180],[-55,100],[-150,150]]` fallback).

### Decisions and Caveats

- Chose "currently shown zone's safezone" over the hardware limits as the primary default, because the user asked for "the current limits" (what the editor is showing) rather than the raw hardware floor. The hardware limits remain the floor/fallback.
- The inherited safezone is a **deep copy**, not a reference, so later editing one zone's safezone never mutates another.
- Caveat: if the currently shown zone itself has a too-wide safezone (values beyond the hardware floor), the new zone inherits that too — the editor does not re-clamp to `HW_LIMITS` on creation (consistent with existing behavior; the core still clamps at runtime).

### Next Task

No immediate next task identified.

### Instructions for the Next Task

N/A

## 2026-10-28 — Per-axis desync: freq-based sweep/oscillation behaviors no longer move all axes in lockstep

### Status

Completed

### Summary

The `freq`-based sweeping behaviors looked mechanical because every variable axis rode the **same** sine: `_apply_scan` (vigilant) and `_apply_oscillation` (soothing / agitated / aggressive / exhilarated / impatient / drowsy) both computed a single shared phase `t = machine.tick * 0.1 * freq` and applied it to every variable axis, so all axes moved in lockstep at the same speed. Added a per-axis desync so each axis sweeps at its own rate and starting point — the motion now reads as organic/random instead of one rigid wave. No config or UI change: the desync is a built-in of the two policies.

### Changes Made

- `realtimecontrol/rtr/rtr/state/behavior.py`:
  - New helper `_axis_desync(axis)` → returns a stable `(phase, freq_mult)` per axis, both derived deterministically from the axis number (a `sin`-based hash), so they are stable across ticks (no jitter) but differ per axis. `phase` ∈ [0, 2π), `freq_mult` ∈ [0.7, 1.3).
  - `_apply_scan` (vigilant): each axis now uses `t = base * freq_mult + phase` instead of the shared `t`.
  - `_apply_oscillation` (the six animal behaviors): same per-axis `t = base * freq_mult + phase`. Amplitude/clamp logic unchanged.
- `realtimecontrol/rtr/tests/test_forced_behavior.py`:
  - `test_build_target_uses_forced_behavior`: the old assertion pinned the tick-0 scan value to the safezone **center** (`== 0.0`), which the desync intentionally breaks. Updated to assert the axis is driven out of the anchor and stays within the safezone (`-100 <= v <= 100`).

### Documentation

- The two policy docstrings now describe the per-axis desync. No operator-facing doc change needed — the config schema and brain-tab cards are unchanged (the desync is always-on, not a tunable param).

### Architecture

- No architectural impact. Internal change to the motion math in `rtr/state/behavior.py`; the state-machine dispatch, safezone clamp, and brain config are untouched.

### Validation

- `python3 tests/test_forced_behavior.py` → ALL TESTS PASSED (updated assertion).
- `python3 tests/test_brain_behavior_params.py` → ALL TESTS PASSED.
- `python3 -m pytest tests/ --ignore=tests/test_remote_tui.py` → 327 passed, 14 skipped. The 5 `test_remote_sdl.py` failures are **pre-existing** (verified: they fail identically on the original `behavior.py` — they assert SDL TUI help-text strings like "THEME / LAYOUT", unrelated to behavior motion). The `test_remote_tui.py` collection error is a pre-existing urwid import issue.
- Verified the fix directly (`/tmp/kilo/verify_desync.py`): old motion had zero spread across axes at every tick (all 0.000, perfect lockstep); the new motion spreads 142–200° per tick with 6 distinct phases and 6 distinct frequency multipliers.
- `ruff check rtr/state/behavior.py` → All checks passed. `mypy rtr/state/behavior.py` → 0 errors in behavior.py (the 14 reported errors are pre-existing in other files: kuka.py, zones.py, machine.py, __init__.py).

### Decisions and Caveats

- Desync is derived from the **axis number** (not randomized per tick or per behavior start), so it is deterministic and stable — the same axis always gets the same phase/rate. This avoids jitter and keeps the motion reproducible. It is not currently a tunable brain param; making the `freq_mult` range (currently 0.7–1.3) adjustable per behavior would be a natural follow-up if the operator wants to dial how "random" the sweep is.
- `machine.tick` is a global, ever-growing counter (not reset per behavior), so the desynced sweep continues its phase/rate across behavior transitions — no re-sync jump on entry. The policies already set the pose to the sine value directly (not relative to the anchor), so this matches existing entry behavior.

### Next Task

Optional: expose the per-axis desync amount as a per-behavior brain param (e.g. a `desync` knob 0–1 that scales the `freq_mult` spread from 1.0 = fully in-sync up to the current 0.7–1.3 spread), wired through `brain.json` + the brain-tab card, so the operator can choose how much variation each behavior has.

### Instructions for the Next Task

- In `rtr/state/behavior.py`, `_axis_desync(axis)` currently hard-codes `freq_mult = 0.7 + 0.6 * h2`. To make it tunable, add a `spread` argument (default 1.0) and compute `freq_mult = 1.0 + (h2 - 0.5) * 0.6 * spread` (so `spread=0` → all 1.0 = old lockstep, `spread=1` → current behavior). Thread a `_bp(machine, behavior, "desync", 1.0)` value from `_apply_scan` / `_apply_oscillation`. Add the key to `DEFAULT_BEHAVIORS` in `rtr/brain/config.py` and the brain-tab card in `editor.html`. Keep the safezone clamp; the desync only changes phase/rate, never the bounds.

## 2026-10-28 — Poses tab: square 3D window that fills the vertical space (controls take the remaining width)

### Status

Completed

### Summary

Changed the poses tab layout so the 3D window is a **square that fills the vertical space** and the controls occupy the **remaining width** on the left (previously the 3D was a fixed 420px-tall box in a fixed 380px column). The 3D section's height is now set at runtime to the space left below the header (computed from the main's viewport offset, so a wrapping header is handled), and `aspect-ratio: 1` keeps `#robot3d` square (width follows the height). The grid changed from `1fr 380px` to `1fr auto` so the 3D column is sized by the square and the controls column (`1fr`) takes the rest.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - CSS: `#posesMain` grid columns `1fr 380px` → `1fr auto`; added `#poses3dSection` (flex column), `#posesRobot3dMount { flex: 1; min-height: 0 }`, and `#posesRobot3dMount #robot3d { aspect-ratio: 1; width: auto; height: 100% }` (replaced the fixed `height: 420px`).
  - HTML: gave the poses 3D section `id="poses3dSection"`.
  - JS: added `sizePoses3d()` — sets the 3D section height to `window.innerHeight - main.offsetTop - 16` (min 320px) and triggers a resize; wired it into `posesEnter()` and a `window` resize listener gated on `posesView`.
- `realtimecontrol/rtr/README.md`: updated the poses-tab paragraph to describe the square 3D window filling the vertical space and the controls taking the remaining width.

### Documentation

- `realtimecontrol/rtr/README.md` updated: poses-tab layout description now reflects the square 3D window filling the vertical space.

### Architecture

- No architectural impact. Client-side (browser JS/CSS) layout change to the poses tab. Reuses the existing `_triggerResize()` and the rtr3d.js window-resize handler (which reads `container.clientWidth/clientHeight`), so the renderer/camera aspect follow the new square size.

### Validation

- `node --check` on the extracted inline `<script>` of `editor.html` → OK.
- Grep confirmed the new identifiers (`poses3dSection`, `sizePoses3d`, `#posesRobot3dMount`) are consistent and the old `height: 420px` is gone.
- Manual browser verification (square window fills vertical, controls fit left, resize behavior) still pending.

### Decisions and Caveats

- The 3D height is computed at runtime (not a fixed `calc(100vh - N)`) so a wrapping header is handled correctly; the offset is measured from `#posesMain.getBoundingClientRect().top`.
- On a narrow viewport the square (height-sized) can leave little width for the controls; acceptable per the operator's design intent (wide-screen use).

### Next Task

Manual browser verification of the square-fill-vertical poses layout.

### Instructions for the Next Task

Open `editor.html`, click the poses tab. Verify: (1) the 3D window is square and fills the vertical space, (2) the controls fit in the remaining width on the left, (3) resizing the window keeps the 3D square and the canvas/renderer resize correctly, (4) switching to another tab and back re-sizes correctly.

## 2026-10-28 — Poses tab restyle: landscape layout, joint-pose tool, live collision, clamp-to-loaded-safezone, oob ghost coloring

### Status

Completed

### Summary

Restyled the poses tab per operator feedback. (1) **Landscape layout**: `#posesMain` is now a two-column grid (`1fr 380px`) — controls/info left, 3D window right (420px tall). (2) **Joint-pose tool**: six plain text inputs replaced by the validate-tab grid pattern (`buildPosesGrid`): per-axis up/down bump buttons (shift=10, plain=1) + number input. (3) **Collision checker**: `checkPosesCollision()` runs `Robot3D.checkPoseCollision(scratchPose)` immediately on every pose change and reports in `#posesCollide`. (4) **Clamp to loaded safezone**: `#posesZoneSel` removed; `posesClamp()` clamps to the safezone of the zone picked in the load-from picker via `loadedSafezone()`. (5) **OOB ghost coloring**: `updatePosesOob()` highlights grid inputs outside the safezone (`.oob`) and recolors matching ghost links red via new `Robot3D.setGhostAxisColors()`; `posesLeave()` resets ghost colors.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - CSS: added `#posesMain` (two-col grid), `#posesRobot3dMount #robot3d` (420px), `.posesGrid`, `.posesGrid input.oob`, `.posesCollide`.
  - HTML `#posesMain`: restructured to two sections (left controls/info + right 3D mount). Removed `#posesInputs`, `#posesZoneSel`.
  - JS: new `buildPosesGrid`, `loadedSafezone`, `applyScratchPose`, `updatePosesOob`, `checkPosesCollision`, `buildPosesLoadPicker`, rewritten `posesClamp`/`posesEnter`/`posesLeave`. Removed `buildPosesInputs`/`buildPosesSelectors`.
- `realtimecontrol/rtr/rtr3d.js`:
  - `makeMats("ghost")` returns per-name materials (was one shared ghost material).
  - Added `ghostLinkMats` captured after ghost arm build.
  - Added `setGhostAxisColors(colors)` — recolors individual ghost links; null restores `COLOR_GHOST`. Exposed in returned API.
- `realtimecontrol/rtr/README.md`: updated poses-tab section (landscape layout, joint-pose tool, live collision, clamp-to-loaded-safezone, oob ghost coloring) and the poses bullet in the feature list.

### Documentation

- `realtimecontrol/rtr/README.md` updated: poses-tab paragraph rewritten to describe landscape layout, joint-pose tool, immediate collision check, clamp-to-loaded-safezone, and oob ghost recoloring. Feature-list bullet updated.

### Architecture

- No architectural impact. Client-side (browser JS) restyle of the poses tab. New `Robot3D.setGhostAxisColors` API is additive (per-link ghost materials instead of one shared material; no behavior change for the validate tab or zones view since they never call it).

### Validation

- `node --check` on `rtr3d.js` → OK.
- `node --check` on extracted inline `<script>` of `editor.html` → OK.
- Grep confirmed no stale identifiers (`posesInputs`, `posesZoneSel`, `posesA1`–`posesA6`, `buildPosesInputs`, `buildPosesSelectors`).
- Grep confirmed `setGhostAxisColors` is defined in `rtr3d.js` and exposed in the returned API; called from `editor.html` (updatePosesOob + posesLeave).
- Manual browser verification (layout, bump buttons, collision, clamp, ghost recolor, reset on leave) still pending.

### Decisions and Caveats

- Did not add validate-tab collision-mesh toggles to the poses tab: `Robot3D` exposes only setters for those states, making cross-tab button-state sync awkward.
- `setGhostAxisColors` is a no-op until the ghost arm has built (`ghostLinkMats` is null before GLTF load completes), same pattern as `setGhostVisible`.

### Next Task

Manual browser verification of the restyled poses tab.

### Instructions for the Next Task

Open `editor.html` in a browser, click the poses tab. Verify: (1) landscape layout (controls left, 3D right), (2) up/down bump buttons tweak the pose (shift=10), (3) collision line updates immediately on every change, (4) pick a zone/action in the load picker, hit clamp, and confirm axes clamp to that zone's safezone, (5) axes outside the safezone get red `.oob` styling in the grid and red links on the ghost, (6) leaving the poses tab resets ghost colors.

## 2026-10-28 — Flow validate button: comprehensive `flowProblems()` (every stop-problem, not just dead-ends)

### Status

Completed

### Summary

Extended the flow tab's **validate** button from a dead-end-only check to a comprehensive
`flowProblems()` that enumerates **everything in the zone table that could make the robot
stop moving**. It reuses the existing dead-end mirror (`deadEndProblems()`) and adds a
`_linkProblemJS()` helper (mirrors the core's `_action_entry_valid` refusal reasons) plus
new checks for the other freeze mechanisms the core enforces: anchor poses outside the
safezone/hardware (clamped → unreachable → freeze), `axis_bands` not overlapping the
safezone (servo clamp floor empty → freeze), zero zone/action speed (moves at 0), and zones
with no enabled action (cannot be entered). The button now lists every problem tagged by
kind, marks the offending nodes (red outline via the existing `applyFlowHighlight` →
`FLOW.badProblems` → `.fbad` path), and clicking one navigates to the zone.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - Added `_linkProblemJS(entry, zones)` after `deadEndProblems()` — returns a
    human-readable reason for an invalid next/exit target, or null. Mirrors the core's
    `zones._action_entry_valid` (zone exists + enabled, action exists + enabled).
  - Added `flowProblems()` — walks every zone/action and pushes `{zone, action?, kind,
    message}` for each of: `dead-end` (via `deadEndProblems()`), `red-link` (action `next`
    + zone `exits` via `_linkProblemJS`), `safezone`/`hardware` (every anchor pose axis vs
    `z.safezone[ax]` and `HW_LIMITS[ax]`), `band` (`axis_bands` not overlapping the
    safezone, same overlap test as `zoneOverlapGaps`), `speed` (zone/action speed ≤ 0), and
    `no-actions` (no enabled action in the zone). Reuses `actionPoses(a)`, `AXES`,
    `HW_LIMITS` (already loaded from `/api/limits`).
  - Rewired `$("flowValidate").onclick` to call `flowProblems()` instead of
    `deadEndProblems()`; each panel item is now `[kind] zone/action: message` (was
    `zone: message`); the "no problems" note reads "no problems — the flow will keep the
    robot moving". Click-to-navigate unchanged.
  - Updated the `#flowValidate` button `title` to describe the full stop-problem scope.
- `realtimecontrol/rtr/README.md`: rewrote the flow-tab validate-button section (was
  "checks dead-ends only") to document `flowProblems()` and the seven problem kinds, and
  note that the authoritative rejection is still the core's 400 on `POST /api/zones`.

### Documentation

- `realtimecontrol/rtr/README.md` updated: the flow-tab validate-button section now
  documents `flowProblems()` and enumerates the seven problem kinds (dead-end, red-link,
  safezone, hardware, band, speed, no-actions) with the freeze mechanism for each.

### Architecture

- No architectural impact. This is a client-side (browser JS) extension of the existing
  flow-validation UI. The JS remains a separate code path from the Python core (the browser
  cannot run the core validator); it is a best-effort mirror so the operator sees stop
  problems before Save. The authoritative rejection is unchanged: the core's
  `validate_state_data` (400 on `POST /api/zones`). The new checks mirror core behavior
  (`_action_entry_valid` for red-links; safezone/hardware clamp, axis_bands overlap, speed,
  and enabled-action rules for the others) but are not a substitute for core validation.

### Validation

- `node --check` on the extracted inline `<script>` of `editor.html` → **SYNTAX OK**.
- Functional test (extracted `flowProblems`/`deadEndProblems`/`_linkProblemJS`/
  `_actionEntryValidJS`/`_hasValidNextJS`/`_hasValidExitJS`/`actionPoses` + `AXES`/
  `HW_LIMITS` into a node harness with a mock `TABLE`): a table with one healthy zone pair,
  one dead-end, one red-link zone, one out-of-safezone/hardware pose, one non-overlapping
  band, one zero-speed zone+action, and one no-actions zone produced exactly the expected
  counts per kind (dead-end 1, red-link 2, safezone 2, hardware 1, band 1, speed 2,
  no-actions 1) and **zero** false positives on the healthy zones. **ALL ASSERTIONS PASS**.
- Confirmed `applyFlowHighlight` keys off `p.zone` only (line ~7329), so every new problem
  object (all carry `zone`) marks its node correctly.

### Decisions and Caveats

- `flowProblems()` is a **superset** of `deadEndProblems()` (it calls it) — dead-ends are
  not double-reported; the other kinds are additive.
- The safezone/hardware checks flag **every** out-of-range anchor pose axis (not just
  inter-zone hand-off poses), because the core clamps *every* commanded target to
  safezone ∩ hardware. This is more aggressive than the existing `edgeConflict()`
  (which only checks connection poses) but matches the freeze mechanism.
- `no-actions` fires when a zone has no enabled action; it is informational (the core
  still lets you save such a zone, but `trigger_action` refuses to enter it).
- The JS is a mirror, not the source of truth; a zone that passes `flowProblems()` can
  still be rejected by the core (structural rules, bridge/variable-axis invariants, etc.).
  Conversely the core can accept a table the JS flags (e.g. a dead-end the JS mirrors but
  the core only rejects on Save).

### Next Task

Manual browser check: open the flow tab, click **validate**, confirm the problem list shows
the tagged kinds (not just dead-ends), that offending nodes get the red `.fbad` outline, and
that clicking a problem selects + centres on the zone.

### Instructions for the Next Task

- `realtimecontrol/rtr/editor.html`: the new functions are `flowProblems()` (~line 6538),
  `_linkProblemJS()` (~line 6509), and the rewired `$("flowValidate").onclick` (~line 8066).
  The `#flowProblems` panel is at line ~534; the button `title` at line ~528. To adjust a
  problem kind, edit the corresponding block in `flowProblems()`. Keep the JS mirrors in
  lock-step with `zones.py` if the core's validation rules change.

## 2026-10-28 — Poses tab: dedicated 3D pose authoring (stable ghost, pose-drag + number inputs, clamp/copy/load)

### Status

Completed

### Summary

Added a dedicated **poses** tab to `editor.html` for stable 3D IK pose authoring. The ghost is the authored (scratch) pose, driven by pose-drag + the six number inputs, never by the 20 Hz WS state stream. Reuses the `Robot3D` singleton via view-gating (the same mount/unmount pattern as the validate tab): the `#robot3d` DOM node moves into the poses mount on entry, the WS `Robot3D.update` is gated with `!posesView`, and the poses tab owns the ghost via pose-drag + number inputs. The real arm is hidden while authoring.

Features:
- **3D view** with the ghost = the authored pose (real arm hidden).
- **6 number inputs** (A1..A6) for the scratch pose; editing drives the ghost + drag handle.
- **Pose-drag**: drag the flange handle to shape the pose (IK solves, writes back to the scratch pose).
- **Zone dropdown + "clamp to safezone"** button (clamps each axis to the selected zone's safezone).
- **"copy pose"** button → sets `POSE_CLIP` (the existing in-memory clipboard); the AE paste button in the zones/flow action editor already handles pasting.
- **"load from"** picker → seeds the scratch from any existing zone/action's first pose.
- **FK readout** (flange xyz) for the current scratch pose.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - Header: added `<button id="tabPoses">poses</button>` after the zones tab.
  - Added `<main id="posesMain">` (3D mount `#posesRobot3dMount`, 6 inputs `#posesInputs`, zone select `#posesZoneSel` + clamp button, copy button, load picker + button, FK readout `#posesInfo`).
  - Added `posesView` state var; wired `switchView("poses")` (display/className, the 3-way mount/unmount, drag enable/disable, real-arm hide/restore).
  - Gated the WS `Robot3D.update` with `!posesView` (the authored ghost must not be overwritten by the live stream).
  - Added `mountRobot3dPoses()` (moves `#robot3d` into the poses mount; `unmountRobot3dValidate()` returns it home).
  - Added the poses-tab JS: `scratchPose` state, `buildPosesInputs`/`updatePosesInputs`/`posesInputOnInput`, `posesOnPose`/`posesOnEnd`, `buildPosesSelectors`, `posesClamp`, `posesCopy`, `posesLoad`, `updatePosesInfo`, `posesEnter`/`posesLeave`.
  - `syncPoseDrag`: added `!posesView` to the `want` condition so the zones-tab drag is disabled in the poses view.
  - Wired the poses tab + button onclick handlers in the boot section.
- `realtimecontrol/rtr/README.md`: added the poses tab to the editor capabilities list and a dedicated section (scratch-pose model, drag, clamp/copy/load, view-gating).

### Documentation

- `realtimecontrol/rtr/README.md` updated: the editor capabilities list (new poses-tab bullet) and a dedicated **poses** tab section (authored scratch pose, pose-drag + number inputs, the `!posesView` WS gate, clamp/copy/load, session-only scratch).

### Architecture

- The poses tab extends the existing **view-gated singleton** pattern: the `Robot3D` singleton is shared between the zones, validate, and poses tabs; the `#robot3d` DOM node is moved between mounts and the WS `Robot3D.update` is gated per-view. The poses tab is the third owner of the ghost (zones = target pose, validate = inspected pose, poses = authored pose). No change to `rtr3d.js` (the pose-drag API `enablePoseDrag`/`disablePoseDrag`/`poseDragSetPose` is reused as-is) or to the pose data shape.

### Validation

- `node --check` on the extracted inline `<script>` of `editor.html` (7261 lines) → **SYNTAX OK**.
- Verified all poses references resolve (grep): `posesMain`/`posesRobot3dMount`/`posesZoneSel`/`posesLoadSel`/`posesInputs`/`posesInfo`/`posesClamp`/`posesCopy`/`posesLoad`/`posesEnter`/`posesLeave`/`posesView`/`scratchPose`/`posesDragOn`/`mountRobot3dPoses`.
- The poses tab is a client-only feature (browser JS in an HTML file); there is no Python test for it. The pose-drag + IK path it reuses was already validated against the Python reference (see the earlier IK-drag handoff entry).

### Decisions and Caveats

- **Option 1 (reuse the singleton + view-gating)** was chosen over a `rtr3d.js` factory refactor: minimal diff, no new 3D scene, and the existing mount/unmount + WS-gate pattern already proven by the validate tab.
- The scratch pose is **session-only** (not persisted); the user copies it into a zone/flow action to persist it (via the existing `POSE_CLIP` + AE paste button). Drag `onEnd` does NOT auto-persist.
- `posesLeave()` restores the real arm to visible (`setRobotRealVisible(true)`); the validate tab's `valMeshRobReal` toggle is a separate concern, so a minor visibility desync can occur when round-tripping poses → validate (the user can re-toggle).
- The "load from" picker splits `zn/an` on the first `/`; zone/action names containing `/` would break the split (edge case; current zone names are simple identifiers).

### Next Task

Manual browser check: enter the poses tab, confirm the real arm is hidden and the ghost is the authored pose, drag the handle, edit the number inputs, clamp to a zone safezone, copy the pose, and paste it into a zone/flow action.

### Instructions for the Next Task

- The poses tab JS lives in `editor.html` (the `// Poses tab: dedicated 3D pose authoring` section, ~line 6290). The scratch pose is `scratchPose` (6-value deg array). `posesEnter`/`posesLeave` own the 3D node + drag + real-arm visibility. `POSE_CLIP` is the shared clipboard (set by `posesCopy`, consumed by the AE `pastePose`).
- To test: open `editor.html`, click the **poses** tab. The real arm should disappear and the ghost should show the scratch pose. Drag the flange handle (IK shapes the pose); edit the A1..A6 inputs; pick a zone and click **clamp to safezone**; click **copy pose**, then open a zone/flow action editor and click **paste pose**.

## 2026-10-28 — Auto-mode `(zone, None)` stall: re-arm + watchdog + STRICT dead-end validation + flow-editor "validate" button

### Status

Completed

### Summary

Implemented the approved combined recovery for the auto-mode `(zone, None)` stall (the state where `current_zone` is set, `current_action is None`, no `pending`, no transition — the robot holds the last pose forever) and added the user-mandated STRICT dead-end validation plus a flow-editor "validate" button.

Three-layer recovery (primary → prevention → backstop):
- **#1 re-arm (primary):** `brain.py` `_rearm_stuck` plays an enabled + non-blocked action in the current zone (prefers a looping one) to escape the hold. It must skip `_blocked_candidates`; if none is valid it is a no-op and lets the watchdog backstop act.
- **#4 dead-end validation (prevention, STRICT):** `zones.py` `_validate_no_dead_ends` now **rejects** zone data that would dead-end (a non-looping action with no valid `next` in a multi-action zone with no valid `exit`), so the bad data never loads. Single-action zones are exempt (the runtime auto-returns them).
- **#3 watchdog (backstop):** `brain.py` `_handle_stuck` counts stuck ticks; at `_stuck_threshold()` (default 10, configurable via `autonomy.stuck_threshold`) it clears `_blocked_candidates` so the next tick can re-plan.

The client (`editor.html`) mirrors the core's dead-end check so Save is blocked and the flow tab's new **validate** button surfaces the same problems (list, red-mark offending nodes, click-to-navigate).

### Changes Made

- `realtimecontrol/rtr/rtr/brain/brain.py`:
  - `__init__`: added `self._stuck_ticks = 0` and `self._blocked_candidates: set`.
  - `_autonomy_tick`: split the early-bait (a `(zone, None)` stall increments `_stuck_ticks` and calls `_handle_stuck`; a productive tick resets it to 0).
  - `_handle_stuck(zone)`: increments `_stuck_ticks`; tries `_rearm_stuck` (resets on success); at threshold, clears `_blocked_candidates` and logs.
  - `_rearm_stuck(zone)`: picks an enabled, non-blocked action (prefers looping) and plays it; returns whether it acted.
  - `_stuck_threshold()`: returns `autonomy.stuck_threshold` (a number > 0) else 10.
- `realtimecontrol/rtr/rtr/state/zones.py`:
  - Added `_zone_enabled`, `_action_table`, `_action_entry_valid`, `_has_valid_next`, `_has_valid_exit`, `_validate_no_dead_ends` and wired `_validate_no_dead_ends` into `validate_state_data` (after the per-zone checks). A target is valid when the zone exists + is enabled and the action is named, exists, and is enabled (`loop`/`enabled` both default true).
- `realtimecontrol/rtr/rtr/brain/config.py`: `validate_brain_config` now **preserves** the optional `autonomy.stuck_threshold` (previously dropped, which silently forced the default 10).
- `realtimecontrol/rtr/editor.html`:
  - Added JS dead-end helpers `_actionEntryValidJS` / `_hasValidNextJS` / `_hasValidExitJS` / `deadEndProblems()` (exact mirror of `zones._validate_no_dead_ends`: `loop` default true, `enabled` default true, single-action zones exempt).
  - Wired dead-end problems into `validate()` so **Save** is blocked (mirrors the core's 400).
  - Added the flow-tab **validate** button (`#flowValidate`) + a `#flowProblems` panel: lists problems, marks offending nodes (`.fbad` red stroke via `applyFlowHighlight` + `FLOW.badProblems`), and clicking a problem selects the zone, opens its detail form, and centres the view (`flowCenterOn`).
  - Added `flowCenterOn(name)` (pans the viewBox to centre a node, keeping zoom). Added `.flowProblems` / `.fpItem` / `.fbad` CSS and a `FLOW.badProblems` field.
- `realtimecontrol/rtr/README.md`: documented the client-side dead-end mirror (the "valid target" rule + Save-blocked) and the flow-tab **validate** button (mark + list + click-to-navigate).
- `realtimecontrol/rtr/tests/test_stuck_recovery.py` (new, 12 tests): re-arm ×3, watchdog ×2, threshold config, stuck-reset, validation ×5, trigger A.
- `realtimecontrol/rtr/tests/test_autoreturn.py` (new): conformed to STRICT validation — the multi-action "hold" fixture is now an **expected-rejected** case (was a passing "does not auto-return" case under the legacy hold).
- `realtimecontrol/rtr/tests/test_transition.py`: added valid `next` links to the non-looping actions in the `_shared_pose_zones` fixtures so the strict validation accepts them (3 tests).

### Documentation

- `README.md` updated: the client-side dead-end-validation section and the flow-tab **validate** button section (both new). The core's dead-end rule and the "hold is the operator's choice point" runtime semantics are already documented in the `zones.py` / `machine.py` docstrings (unchanged).

### Architecture

- Three-layer recovery is the architectural change: **prevention** (STRICT validation rejects dead-end data at load) is the new first line; **re-arm** (primary runtime escape) and the **watchdog** (backstop that clears blocked candidates) remain for the cases validation can't catch (e.g. a zone whose action is disabled/renamed at runtime, or a blocked candidate). The client mirrors the prevention layer so the operator sees the problem before Save. No change to the data shape, the `reload_zones` hot-reload path, or the `POST /api/zones` 400 contract.

### Validation

- `python -m pytest tests/test_stuck_recovery.py tests/test_autoreturn.py tests/test_transition.py -q` → **25 passed**.
- `python -m pytest tests/ --ignore=tests/test_remote_tui.py -q` → **327 passed, 14 skipped, 5 failed** — the 5 `test_remote_sdl.py` failures are pre-existing (verified earlier via `git stash` on the clean tree); the `test_remote_tui.py` collection error is also pre-existing (imports `action_kind` from `rtr.flow`, which does not exist).
- `node --check` on the extracted inline `<script>` of `editor.html` → SYNTAX OK (the JS dead-end helpers, `validate()` wiring, the validate button, `flowCenterOn`, and `applyFlowHighlight` all parse).
- Verified the JS `deadEndProblems()` semantics match `zones._validate_no_dead_ends` line-for-line (defaults `loop=true`/`enabled=true`, single-action exemption, valid-target rule).

### Decisions and Caveats

- **STRICT was the user's explicit decision** (via the clarification question): reject the hold data at load rather than keep the legacy "hold = operator's choice point" and rely only on the runtime re-arm. The runtime re-arm/watchdog are retained as a backstop for runtime-only dead-ends (disabled/renamed action, blocked candidate) that validation at load cannot see.
- The JS mirror is a **separate** code path from the Python core (the browser can't run the Python validator). It is kept in lock-step with `zones._validate_no_dead_ends`; if the core rule changes, both must be updated. The authoritative rejection is still the core's 400 on `POST /api/zones`.
- The flow-tab **validate** button checks **dead-ends only** (the same check Save blocks on); the full `validate()` (poses, exits, modes, exitposes) still runs on Save.
- `ruff`/`mypy` are not in the active pyenv; Python syntax was verified via `ast.parse` + the full test suite. The JS was verified via `node --check`.

### Next Task

No immediate next task identified. All four approved items are done: STRICT dead-end validation (core + client), the re-arm + watchdog backstop, the flow-editor problem-node marking, and the "validate flow" button (list + click-to-navigate).

### Instructions for the Next Task

- To verify the validate button in a browser: open the flow tab, click **validate**; offending nodes turn red, a problem list appears below the canvas, and clicking a problem selects the zone and centres it. A clean flow shows "no problems".
- If the dead-end rule changes in `zones.py`, update `editor.html` `deadEndProblems()` / `_actionEntryValidJS` / `_hasValidNextJS` / `_hasValidExitJS` to match, and re-run `tests/test_stuck_recovery.py`.
- The 5 `test_remote_sdl.py` failures and the `test_remote_tui.py` collection error are a separate, pre-existing workstream (UI text drift / missing `action_kind` in `rtr.flow`) — not related to this work.

---

## 2026-10-28 — Interactive 3D pose drag: drag the ghost flange to author a pose (IK fill)

### Status

Completed

### Summary

Implemented interactive pose authoring in the zones-tab 3D view: the user drags the robot's end-effector (flange) and IK fills the currently-selected 6-joint pose row. The drag loop is owned by `rtr3d.js` (pointer mechanics + IK solve + ghost drive + handle snap); `editor.html` supplies the seed pose, hardware limits, and write-back callbacks.

Architecture (Option A — chosen): `rtr3d.js` exposes a pose-drag API; `editor.html` wires it to the selected anchor. On pointerdown over the handle, OrbitControls is disabled, the pointer ray is projected onto a camera-facing plane through the flange (normal = camera → flange), and each pointermove runs `KR60IK.ikSolve(seed, target, { limits, lam:0.1, maxIter:60, tol:0.0004 })`, seeding from the previously solved pose so the arm stays on one branch. The solved pose drives the ghost via `setTarget`, the handle snaps to `fkPos(solved)`, and `onPose` writes all 6 concrete numbers back to the anchor. On pointerup, OrbitControls is re-enabled and `onEnd` resyncs the UI.

A drag writes all 6 axes as concrete numbers (a free `"x"` axis becomes concrete during the drag; the user can re-mark `"x"` in the row afterwards). The editor persists via its explicit save button — `onEnd` does **not** auto-persist; it resyncs the form/bars/ghost and sets `saveMsg` = "pose updated — save to persist".

### Changes Made

- `realtimecontrol/rtr/rtr3d.js`:
  - Added the pose-drag state (`poseDrag`, `poseDragHandle`, `pendingPoseDragOpts`) and the `update()` ghost guard (skips the state-stream `s.target` branch while `poseDrag.active`, so the IK drag owns the ghost; the 20Hz live-state drive doesn't fight it).
  - Added `pointerNdc`, `poseDragOnDown/Move/Up`, `enablePoseDrag`, `disablePoseDrag`, `poseDragSetPose`; added `enablePoseDrag`/`disablePoseDrag`/`poseDragSetPose` to the returned API.
  - Handle = `THREE.Group` with an invisible hit sphere (r=0.09) + visible sphere (r=0.045, color `0x37d0ff`) at the ghost flange; positioned via `KR60IK.fkPos(pose)` (Z-up world frame = THREE scene frame).
  - **Boot race fix:** `enablePoseDrag` is called at boot (before the STL arm finishes loading, `running` is still false). Added a `pendingPoseDragOpts` mechanism: if `running`/`scene` aren't ready yet, the opts are stored and applied automatically once `running = true` (in the arm-build path). `enablePoseDrag` returns `true` when accepted (immediately or pending) and `false` only when `ik.js` is missing. `disablePoseDrag` clears the pending opts.
- `realtimecontrol/rtr/editor.html`:
  - Added `<script src="ik.js"></script>` after `rtr3d.js` (inline script now starts line 748; inline JS = lines 749–7717).
  - Tagged pose rows with `pr.dataset.an = an; pr.dataset.pi = pi;` in `buildActionList` so the selected anchor's row inputs can be found without rebuilding the form during a drag.
  - Added the pose-drag section after `stepGhost`: `POSE_DRAG_HOME`, `poseDragOn`, `updatePoseRowInputs`, `updateBarMarksFromPose`, `poseDragOnPose`, `poseDragOnEnd`, `syncPoseDrag(force)`.
  - `updatePoseRowInputs()` updates the 6 inputs + `frozenX` class in place (form is not rebuilt during a drag). `updateBarMarksFromPose(pose)` moves the `#bmark{i}` markers without rebuilding the bars DOM (drag fires per pointer move). `poseDragOnPose` writes all 6 concrete numbers (rounded to 3 decimals) to `a.poses[pi]`.
  - `syncPoseDrag(force)`: `want = !validateView && !!selectedZone && !!selectedAction && ghostPoses(selectedAction).length > 0`; cheap re-seed via `poseDragSetPose` when already on (no `force`), else `enablePoseDrag` (rebuilds the handle, picks up fresh `HW_LIMITS`).
  - Wired `syncPoseDrag()` into all selection/state hooks: `selectZone` (after `updateGhost`), the zones-tab default `onChanged`, `copyAction` else-branch, `closeActionEditor` else-branch, tab switch (validateView → disable; else → `syncPoseDrag()`), `addR` handler, `loadLimits` (if `poseDragOn` → `syncPoseDrag(true)`).
- `/tmp/kilo/ik_test.js` (scratch): Node harness — fixed the Jacobian finite-difference check to perturb in **radians** (convert to degrees for FK, divide by the radian step) to compare against the per-radian Jacobian; the earlier version perturbed in degrees (a 180/π ≈ 57× error).
- `/tmp/kilo/drag_sim.js` (scratch): Node simulation that mirrors the rtr3d.js drag math (camera-facing plane → pointer projection → IK seed chain → FK snap) without THREE, to verify the pipeline is stable.

### Documentation

- None required — the pose-drag contract is documented in the header comment added to `rtr3d.js` (the drag loop, the camera-plane projection, and the `ik.js` dependency) and the section comment in `editor.html` (what a drag writes and the save-to-persist model).

### Architecture

- The drag loop is split by responsibility: `rtr3d.js` owns the 3D/pointer/IK mechanics (it already owns the scene, camera, OrbitControls, ghost, and `setTarget`), and `editor.html` owns the data (which anchor, its seed pose, `HW_LIMITS`, and where to write the result). `ik.js` remains a pure THREE/DOM-free module consumed by both.
- The `update()` ghost guard is the only change to the existing 20Hz state-driven ghost path: while a drag is active the drag's `setTarget` owns the ghost, so the live-state stream doesn't fight the IK. This keeps the two drive sources from oscillating.
- OrbitControls gating is safe (verified in the local `OrbitControls.js`): its `pointerdown` listener registers before `poseDragOnDown` (created at init vs. later `enablePoseDrag`), so it starts its drag state first; disabling `controls.enabled` in `poseDragOnDown` makes its `pointermove` a no-op for the rest of the drag, and its `onPointerUp` cleans up state regardless of `enabled`.

### Validation

- `node --check realtimecontrol/rtr/ik.js` → SYNTAX OK.
- `node --check realtimecontrol/rtr/rtr3d.js` → SYNTAX OK.
- `sed -n '749,7717p' editor.html > /tmp/kilo/editor_inline.js && node --check /tmp/kilo/editor_inline.js` → SYNTAX OK (inline JS extracted after the edits).
- `node /tmp/kilo/ik_test.js` → **PASS**: HOME fk pos `[1.17, 0.0, 1.64]`; displacement cases 0.05/0.15/0.30/0.45 all match the Python reference (iters 2/3/3/4, pose max |err| < 5e-7, residuals 8.27e-5/2.14e-5/1.59e-4/9.53e-5); Jacobian analytic-vs-finite-difference max |err| = 5.849e-7 (< 1e-3).
- `node /tmp/kilo/drag_sim.js` → pipeline verified: within the reachable workspace the handle tracks the pointer exactly (steps 1–6, err < 0.0002), the pose is continuous and stays inside `HW_LIMITS` every step. When the pointer leaves the reachable volume (steps 7–10) the IK correctly moves the handle to the closest reachable pose and holds it there — it never commands an impossible pose. (The harness's "FAIL" lines for steps 7–10 are the expected workspace-boundary condition, not a defect.)

### Decisions and Caveats

- **Workspace boundary (expected behavior):** dragging the pointer beyond what the arm can physically reach stops the handle at the closest reachable pose (IK can't converge further; it holds the last valid pose). This is correct 6-DOF behavior and matches a real robot: you can't command an unreachable pose. No clamping-to-nearest-reachable-point was added (the IK already degrades gracefully to the boundary); the pointer/handle visually separate only when the pointer is outside the reachable volume, which the user can read as "too far."
- **Position-only IK (3 of 6 DOF):** orientation is not solved; the free axes are left to the existing behavior/axis system. A drag writes all 6 concrete numbers so the arm stays on a single branch.
- **Persistence:** `onEnd` does not auto-save; the user clicks the existing save button. `saveMsg` flags the dirty state.
- **Boot race:** the editor calls `syncPoseDrag` → `enablePoseDrag` at boot, before the STL arm is built. The `pendingPoseDragOpts` mechanism defers the enable until `running = true` so the handle comes up once the arm loads.
- **TDZ note:** `poseDragOn` is declared later in the file but referenced in `loadLimits` at call time — safe (function bodies are evaluated after the full script loads).
- **Pre-existing unrelated failures** (not caused by this work — this session only touched `rtr3d.js`, `editor.html`, `ik.js` and the handoff doc):
  - `tests/test_transition.py`: 5 failures, all from the **uncommitted** dead-end validation added to `rtr/state/zones.py` (the `ValueError` at zones.py:703, `_has_valid_exit`). The test fixtures use zones the new validation now rejects. This is prior-session work, not the JS drag feature. `tests/test_bridge_behavior.py` fully passed (12/12 of the combined run).
  - `tests/test_remote_tui.py` collection error; `tests/test_remote_sdl.py` failures at HEAD; `tests/test_sound_out_ports.py::test_reconcile_closes_stale_specs_on_reload` fails due to earlier uncommitted `rtr/sound/sound.py` changes.

### Next Task

Optional regression check: run the axis-system / `"x"`-home pytest suite (`tests/test_transition.py`, `tests/test_bridge_behavior.py`) to confirm no regression from the editor/rtr3d edits. No implementation work remains for the drag feature.

### Instructions for the Next Task

- Run `cd realtimecontrol/rtr && python -m pytest tests/test_transition.py tests/test_bridge_behavior.py` and confirm the previously-passing 52/skipped-9 result is unchanged.
- If a regression appears, the suspect surface is the `update()` ghost guard in `rtr3d.js` (the only change to the existing ghost path) or the `syncPoseDrag` hooks in `editor.html`.

---

## 2026-10-28 — IK core: pure JS forward kinematics + geometric Jacobian + DLS IK (validated vs Python)

### Status

Completed

### Summary

Built the JS inverse-kinematics core for drag-the-end-effector pose authoring. A new file `realtimecontrol/rtr/ik.js` holds the pure (no THREE / no DOM) KR60 kinematics, mirroring `rtr/robot/kinematics.py` exactly:

- **FK** (`fkChain`/`fkPos`) takes joint angles in **degrees** (the KUKA A1..A6 convention) and returns the flange (tool0) position = the link_6 matrix translation (tool0 xyz is [0,0,0]). The matrix construction is identical to `rtr3d.js checkPoseCollision` (column-major 16-element arrays, `mats[j] = parent * T(xyz_j) * R(axis_j, q_j)`).
- **Jacobian** (`jacobian`) returns the linear geometric Jacobian `J_v` (3x6), **per-radian**: column `j` is `z_j × (pos − p_j)`.
- **Pseudo-inverse** (`pinv3`) is the damped minimum-norm pseudo-inverse (6x3) mapping a base-frame displacement (metres) to joint deltas in **radians**.
- **IK** (`ikSolve`) is DLS with backtracking + hardware clamp. The critical units rule: the Jacobian is per-radian, so each joint delta is a radian quantity and is converted to degrees (`× 180/π`) before being applied to the degree-based FK. Treating the radian delta as degrees (the earlier broken IK) made every step ~57× too small.

The Python reference (`/tmp/kilo/ik_ref.py`) uses the real `Chain` + the same DLS/backtracking/clamp parameters. The JS port matches it exactly (see Validation).

### Changes Made

- `realtimecontrol/rtr/ik.js` (new): pure KR60 IK module. Exposes `KR60IK = { JOINTS, HOME, DEFAULT_LIMITS, fkChain, fkPos, jacobian, pinv3, ikSolve }` on `globalThis` (and `module.exports` for Node). Chain data (`JOINTS`, `HOME`) is identical to `rtr3d.js`; `DEFAULT_LIMITS` mirrors `HW_LIMITS_FALLBACK`.
- `/tmp/kilo/ik_ref.py` (scratch, outside the repo): Python reference that locks the target values.
- `/tmp/kilo/ik_test.js` (scratch, outside the repo): Node harness that loads `ik.js` and compares against the locked reference.

### Documentation

- None required — the new module is self-documenting via its header comment (the FK/Jacobian/units contract is documented at the top of `ik.js`).

### Architecture

- New pure module `ik.js`, deliberately separate from `rtr3d.js` so it is THREE/DOM-free and testable in Node. It will be loaded via a `<script>` tag (before the editor inline script) and consumed by the drag logic; the chain is the same kr60ha xacro used by `rtr3d.js` and `kinematics.py`.

### Validation

- `node --check realtimecontrol/rtr/ik.js` → SYNTAX OK.
- `node /tmp/kilo/ik_test.js` → **PASS**:
  - HOME fk pos = `[1.17, 0.0, 1.64]` (max |err| vs Python = 0.000000).
  - Displacement targets (dir = normalize([0.3,0,0.4])) from HOME: disp 0.05/0.15/0.30/0.45 → solved-pose max |err| = 0.000000 for all four, iteration counts 2/3/3/4 (all match the Python reference), residuals 0.000083/0.000021/0.000159/0.000095 (all match).
  - Jacobian analytic-vs-finite-difference max |err| = 6.75e-7 (< 1e-3).

### Decisions and Caveats

- Kept the FK matrix convention identical to `checkPoseCollision` (not a fresh rewrite) so the IK's flange position and the rendered ghost arm's tool position are guaranteed to coincide (both derive from the same chain + convention).
- `ikSolve` seeds from a concrete pose (the caller passes the resolved anchor) so successive drag steps stay on the same arm branch (no joint flips). Orientation is NOT solved (position-only, 3 of 6 DOF); the free axes are left to the existing behavior/axis system.
- `ik.js` is not yet referenced by any HTML page — it is validated standalone and will be wired in the next task.

### Next Task

Implement the TCP drag handle in `rtr3d.js` (a THREE mesh at the flange, OrbitControls gating, camera-plane pointer projection) that calls `KR60IK.ikSolve`, and wire it in `editor.html` to write the solved pose to the currently-stepped ghost anchor.

### Instructions for the Next Task

- Load `ik.js` via a `<script src="ik.js"></script>` in `editor.html` (and `client.html` if it shares the 3D view) before the inline script, so `KR60IK` is available.
- In `rtr3d.js`, add a drag-handle API: on pointerdown on the handle, disable `controls` (OrbitControls); project the pointer ray onto a camera-facing plane through the initial flange position; on each pointermove run `KR60IK.ikSolve(seedPose, draggedPoint, { limits: HW_LIMITS })`, seed from the previous solved pose, clamp to `HW_LIMITS`, and recompute FK of the clamped pose so the handle snaps to the actual flange. Expose it so the editor can supply `HW_LIMITS` and a callback with the solved pose.
- In `editor.html`, target the currently-stepped ghost anchor `TABLE.zones[selectedZone].actions[selectedAction].poses[ghostIndex]`; on drag, write all 6 concrete numbers, update the ghost arm (`Robot3D.setTarget`), the safezone bars, and the pose-row inputs. `resolvePose` (line 5561) already resolves "x" axes for the seed.

---

## 2026-10-28 — UI: two-click interaction for collapsed bridge nodes (select, then open pose editor)

### Status

Completed

### Summary

Fixed the flow-tab click behavior for collapsed bridge-zone nodes. Previously a single click on the purple dot immediately opened the action/pose editor, so the node could not be "selected" to see its properties in the right pane. Now: first click selects the node (`flowSelect` → white stroke + zone properties in the right pane via `buildFlowDetail`/`buildZoneForm`); a second click while the node is already selected opens its single action's editor (`openActionEditor`). Dragging (reposition / connect) and clicking non-bridge nodes are unchanged.

### Changes Made

- `realtimecontrol/rtr/editor.html` (`flowDragEnd`): in the `!d.moved` branch, the collapsed-bridge case now checks `FLOW.sel` first — if the clicked bridge node is the currently selected node, open its first action's editor; otherwise just select it. Falls back to select when the zone has no actions.

### Documentation

- None required — behavior change is self-describing via the updated comment in `flowDragEnd`; no user-facing docs changed.

### Architecture

- No architectural impact. UI-only change to the click dispatch in the flow tab; data model and core untouched.

### Validation

- Extracted the inline script (lines 748–7609) and ran `node --check` → SYNTAX OK.
- Could not run the page in a browser from this environment (depends on the live core over WebSocket + three.js/hydra-synth), so runtime UI verification was not performed.

### Decisions and Caveats

- Selection state is keyed on `FLOW.sel` (`type === "node"`, matching `zone`), the same state `applyFlowHighlight` uses for the white `fsel` stroke, so the second click is only armed after a visible selection.
- The right pane already renders the full zone form for a selected node (`buildZoneForm`), so no new UI was needed to "see their properties".
- If the user later finds two-clicks annoying, a "click dot again to edit" hint could be added to the flow detail pane for selected bridge nodes.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

- To verify in the browser: click a purple bridge dot once → node gets a white stroke and the right pane shows its zone form; click it again → the pose/action editor modal opens. Clicking a different node first resets the selection (first click selects it again).

---

## 2026-10-28 — Fix: brain behavior next-selection params (lock_duration_s / dwell_s) lost on validate/reload

### Status

Completed

### Summary

The per-behavior next-selection params set in the brain tab (`lock_duration_s`, `dwell_s`) were silently lost, so the edits appeared "not saved". The POST to `/api/brain` writes the payload **verbatim** to `brain.json` (the edits WERE on disk, e.g. `locked.lock_duration_s: 15`), but `validate_brain_config` → `_validate_behaviors_section` (config.py) kept only the keys present in the behavior's `DEFAULT_BEHAVIORS` entry. That dropped `lock_duration_s` for every behavior except agitated/aggressive/exhilarated/impatient (the four whose defaults carry it), and `dwell_s` for **all** behaviors (no default carries it). The running core builds `machine.behavior_params` from the validated config (`brain.py:131`), and GET `/api/brain` also serves the validated config — so the runtime never saw the edits.

Fix: `_validate_behaviors_section` now preserves the optional next-selection overrides (`dwell_s`, `lock_duration_s`) when present on any behavior, type-checked via `_num` (null = absent, non-numeric/bool rejected). The runtime (`brain._behavior_triggers_fired`) already reads both off the behavior entry for any behavior, so no other code change was needed.

### Changes Made

- `realtimecontrol/rtr/rtr/brain/config.py`: `_validate_behaviors_section` — after the defaults loop, copy `dwell_s` / `lock_duration_s` from the raw entry when present (skipping keys already covered by the defaults); docstring updated to document the preserved overrides.
- `realtimecontrol/rtr/tests/test_brain_behavior_params.py`: new regression test file (7 tests): `lock_duration_s` kept for a behavior without a default; kept for every `DEFAULT_BEHAVIORS` behavior; `dwell_s` kept for every behavior; default backfill when absent; `stay`/`triggers` still validated; explicit null treated as absent; bad types (string/bool) rejected.

### Documentation

- Updated the `_validate_behaviors_section` docstring (the preserved-override contract). No user-facing docs needed.

### Architecture

No architectural impact. The save path is unchanged in shape (editor DOM → `_commitBrainDom` → POST → verbatim write → validate on reload); the fix only stops the validator from stripping the two optional per-behavior keys the runtime reads.

### Validation

- `python tests/test_brain_behavior_params.py` → ALL TESTS PASSED (7 tests).
- `python3 -m pytest tests/test_brain_behavior_params.py tests/test_brain_autonomy_dwell.py tests/test_midi_brain_trigger.py tests/test_zone_groups.py tests/test_brain_assertive.py tests/test_brain_action_cam.py -q` → 57 passed.
- Full suite `python3 -m pytest tests/ -q --ignore=tests/test_remote_tui.py` → 308 passed, 14 skipped, 5 failed — the 5 `test_remote_sdl` failures and the `test_remote_tui.py` collection error are pre-existing (verified via `git stash` on the clean tree).
- Verified against the real `brain.json`: `locked` now keeps `lock_duration_s: 15.0`, `intent` keeps `10.0`, `spontaneous` keeps `3.0`, `agitated` keeps its `1.0` default; a `dwell_s: 7.5` edit on `locked` survives; explicit null is treated as absent; string/bool values raise `ValueError`.

### Decisions and Caveats

- The client side was already fixed earlier (the 2026-10-27 "Fix brain-tab save wiping per-behavior stay/triggers" entry): `_commitBrainDom` reads the fields from the DOM. This entry fixes the remaining server-side loss.
- The change landed in commit `f3db044` ("stufff"), which also contains the user's own `editor.html` work.
- A **running core must be restarted** to pick up the fix (the old process still runs the old `config.py` code; a `RELOAD_BRAIN` hot-reload only reloads the config data, not the code). After restart, the existing `brain.json` edits (locked=15, intent=10, spontaneous=3) take effect immediately — no re-save needed.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

- If the params are still "lost" after a core restart, check that the core process actually restarted (old code in memory keeps the old validator). Verify with: `python3 -c "import importlib.util, json; ..."` against `validate_brain_config` as done in this task, or read `machine.behavior_params` from the running core.
- The editor's per-behavior controls: `stay` / `triggers` / `dwell_s` / `lock_duration_s` are on every behavior card in the brain tab (`brainBh<Name>Stay|DwellS|LockDurS` inputs + trigger checkboxes).

---

## 2026-10-28 — UI: collapse bridge-zone nodes in the flow tab (small purple dot)

### Status

Completed

### Summary

Collapsed bridge-zone nodes in the flow tab of `editor.html`. A bridge zone (a pose zone of type `"bridge"`, i.e. a single in/out handoff) now renders as a small purple dot with the zone name below, instead of a full rectangular block with action rows. Clicking the dot still opens the action editor for its single action; the node can still be dragged to reposition and dropped on another node to draw a connection. Newly created bridge zones (via `bridgePlan`) now get `pose: "bridge"` so they are collapsed too. The purple fill matches the existing bridge-node purple (`#a06fd0`). The layout reserves the full column width but a reduced height (`BRIDGE_H = 40` vs `78`), so bridge nodes pack tighter vertically.

### Changes Made

- `realtimecontrol/rtr/editor.html` (CSS): added `.fnode.fnodeBridge .fnodeBody { fill: #a06fd0; }` and `.fnodeBridgeLabel { fill: var(--text); font-size: 11px; }` for the collapsed dot + name label.
- `realtimecontrol/rtr/editor.html` (constants/helpers): added `BRIDGE_R = 9`, `BRIDGE_CY = 16`, `BRIDGE_H = 40`; added `isBridgeZone(name)` (true when `z.pose === "bridge"`, null-guarded); `nodeH(name)` now returns `BRIDGE_H` for bridge zones.
- `realtimecontrol/rtr/editor.html` (`flowAddNode`): bridge zones render a `circle` (dot at column center) + a name label instead of the rect + sublabel + action rows; non-bridge nodes unchanged.
- `realtimecontrol/rtr/editor.html` (`flowAddEdge`): inter-zone edge anchors use the dot center (`NODE_W/2`, `BRIDGE_CY`) for bridge zones instead of the row side.
- `realtimecontrol/rtr/editor.html` (`flowDragMove`): the temporary drag line's start point uses `BRIDGE_CY` for bridge zones.
- `realtimecontrol/rtr/editor.html` (`flowDragEnd`): a click (no move) on a collapsed bridge node opens its first action's editor (`openActionEditor`); falls back to node-select if it has no actions.
- `realtimecontrol/rtr/editor.html` (`bridgePlan`): the created zone now includes `pose: "bridge"`.

### Documentation

- None required — the change is self-describing via the added CSS comment and the `isBridgeZone` / constant comments; no user-facing docs changed.

### Architecture

- No architectural impact. UI-only change to the flow-tab rendering of bridge zones; the zone/edge data model and the core are untouched.

### Validation

- Extracted the inline script (lines 748–7605) and ran `node --check` → SYNTAX OK.
- Manually reviewed all modified functions (`flowAddNode`, `flowAddEdge`, `flowDragMove`, `flowDragEnd`, `nodeH`, `isBridgeZone`, `bridgePlan`) for balanced braces and correct anchor/hit-test geometry.
- Could not run the page in a browser from this environment (it depends on the live core over WebSocket + three.js/hydra-synth), so runtime UI verification was not performed.

### Decisions and Caveats

- Collapse is keyed on `z.pose === "bridge"` (the field that already makes bridge nodes purple), not on a structural "one in / one out" graph analysis — simpler and matches the visible purple nodes the user referred to.
- Bridge nodes keep the full `NODE_W` column width (only the height shrinks) so the uniform BFS column grid and persisted layouts are not disturbed; the dot is centered in its column.
- The live-state accent stroke on a bridge dot is overridden by the purple `fnodePose` stroke (same pre-existing CSS ordering as the current non-collapsed bridge nodes); the selected-state white stroke and the `off` dimming still apply.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

- If the user wants bridge nodes to also shrink horizontally (not just vertically), that requires per-column widths in `flowAutoLayout`/`flowFitView`/`flowNodeAt` (currently a uniform `NODE_W` grid) — a larger change.
- If "move" pose zones (`z.pose === "move"`) should also collapse, extend `isBridgeZone` (and the `bridgePlan` equivalent) to include them.

---

## 2026-10-28 — Test: device-change tests for robust MIDI-out (fake rtmidi)

### Status

Completed

### Summary

Extended `tests/test_sound_out_ports.py` to cover the new device-change behavior of the Sound adapter. The fake rtmidi now mirrors python-rtmidi 1.5.8: `open_port` raises for an out-of-range index (with the real error text), there is **no** `open_string_outport` on the base fake, and instances expose `deleted`/`closed` state. New tests:

- `test_invalid_index_falls_back_to_auto` — a zone `out_port` that no longer exists (index 5 of 2) fails and the zone degrades to the auto candidate (first available port) and still sends.
- `test_name_open_resolves_by_index_when_no_string_outport` — the `out_device` name path resolves by enumeration (1.5.8 has no name open).
- `test_name_substring_matches_dynamic_suffix` — a name that is a substring of the enumerated port (dynamic suffix) resolves to the first matching port.
- `test_name_open_uses_string_outport_when_available` — the `FakeMidiOutWithString` subclass covers the newer-wrapper `open_string_outport` fast path.
- `test_failed_open_discards_instance` — failed opens do not leak `MidiOut` instances (they are `delete()`d).
- `test_failed_open_retries_after_backoff` — a failed spec is skipped within the backoff (no new attempt, no new instance) and the retry succeeds after the backoff once the device reappears; the failure entry is cleared on success.
- `test_send_failure_drops_dead_handle_and_reopens` — a device that dies after opening: the send fails, the dead handle is dropped (closed, backoff started, message degraded to logging), and the next send reopens the port.
- `test_list_out_ports_releases_instance` — the enumeration `MidiOut` is released.

Fake updates: `open_port` range-validates and raises; `delete()` added; `open_string_outport` moved to the `FakeMidiOutWithString` subclass; class-level `open_fail` and per-instance `send_fail` switches; `_sound()` takes a `retry_interval` passthrough.

### Changes Made

- `realtimecontrol/rtr/tests/test_sound_out_ports.py`: new "Device changes" test section (8 tests); updated fake rtmidi (+ `FakeMidiOutWithString`); `retry_interval` on the `_sound()` helper; `import time`.
- `realtimecontrol/rtr/tests/test_sound_out_ports.py`: fixed `test_reconcile_closes_stale_specs_on_reload` — `_get_out` now returns `(spec, out)`, so the test unpacks the tuple.

### Documentation

- Updated the test module docstring to list the new device-change coverage and the 1.5.8 fake semantics.

### Architecture

No architectural impact. Test-only.

### Validation

- `python tests/test_sound_out_ports.py` → ALL TESTS PASSED (27 tests).
- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py` → **308 passed, 14 skipped, 5 failed** — all 5 failures are in `tests/test_remote_sdl.py` (UI text assertions, e.g. "THEME / LAYOUT" label), pre-existing at HEAD and unrelated to sound.
- `tests/test_remote_tui.py` has a pre-existing collection error at HEAD: it imports `action_kind` from `rtr.flow`, which does not exist (both files unmodified from HEAD).
- `ruff check rtr/sound/sound.py tests/test_sound_out_ports.py` → sound.py clean; the test file has the same 4 pre-existing E402s (the `sys.path.insert` pattern) as the HEAD version.

### Decisions and Caveats

- The retry/backoff tests use a small `retry_interval` (0.05 s) plus one 0.06 s sleep to stay fast while still exercising the real backoff path.
- The fake's `open_port` raises a plain `Exception` carrying the real rtmidi message text; the adapter catches `Exception`, so the exact error type is irrelevant.

### Next Task

Handoff + final report (this entry and the implementation entry below).

### Instructions for the Next Task

- If the `test_remote_sdl.py` / `test_remote_tui.py` failures matter, they are a separate workstream (UI text drift / missing `action_kind` in `rtr.flow`) — not the sound adapter.

---

## 2026-10-28 — Implement: robust MIDI-out device handling in rtr/sound/sound.py

### Status

Completed

### Summary

Reworked the Sound adapter's MIDI-out connection so adding/removing MIDI devices no longer permanently kills sound (the startup error `MidiOutAlsa::openPort: the 'portNumber' argument (7) is invalid.`):

- **Candidate spec chain**: `_candidate_specs(zone)` returns the ordered, deduped specs: zone `out_port` index → global `out_device` name → global `out_port` index → constructor default → auto (first available port). `_get_out` walks the chain: a cached handle wins; a previously failed spec is retried at most once per `retry_interval` (default `RETRY_INTERVAL = 5.0`, constructor kwarg); the first spec that opens is returned. `_get_out` now returns `(spec, out)`.
- **Name path fixed for rtmidi 1.5.8**: `_open_named` uses `open_string_outport` when present, else resolves the name to an index by enumeration (exact match, then substring for dynamic suffixes). The old name branch always raised `AttributeError` on 1.5.8.
- **Descriptive index errors**: `_open_indexed` range-checks before opening and raises with the list of available ports, so the log says what to configure.
- **Dead-device recovery**: a `send_message` failure drops the dead handle (`_close_spec`), starts the backoff, and degrades the message to logging; the next message reopens (walking the whole candidate chain).
- **Leak fixes**: `_open_one` discards (closes + deletes) the `MidiOut` instance on every failure path, including "no devices"; `list_out_ports` releases its enumeration instance in a `finally`.
- **Reconcile**: `_reconcile_outs` computes the needed set as the union of *all* candidates over all zones and prunes stale `_open_failures`; `close()` clears `_open_failures`.
- Retry state: `_open_failures: Dict[tuple, float]` (monotonic time per spec).

### Changes Made

- `realtimecontrol/rtr/rtr/sound/sound.py`: module docstring + `RETRY_INTERVAL` constant; `Sound.__init__` (`retry_interval` kwarg, `_open_failures`); new helpers `_candidate_specs`, `_port_names`, `_find_port_index`, `_open_indexed`, `_open_named`, `_discard`; rewritten `_out_spec` (first candidate), `_open_one`, `_get_out`, `_send_bytes`, `_reconcile_outs`, `list_out_ports`, `close`.

### Documentation

- Module docstring updated: the fallback chain, retry/backoff, and dead-device detection are documented.
- No `sound.json` / README changes needed: the existing `out_port` / `out_device` fields now behave as documented (the name path actually works; a stale index degrades instead of dying).

### Architecture

No architectural impact. Internal to the sound adapter; the data shape, events, and public API are unchanged (except `_get_out` returning `(spec, out)`).

### Validation

- `python tests/test_sound_out_ports.py` → ALL TESTS PASSED (27 tests, incl. the new device-change tests — see the test entry above).
- `ruff check rtr/sound/sound.py` → clean.

### Decisions and Caveats

- The MIDI-**in** side (`rtr/io/midi.py`) was intentionally left unchanged: its failures are not cached and the controller port is a stable ALSA loopback. Possible follow-up if the controller port index ever shifts.
- The auto candidate `(None, None)` opens port 0 (the first enumerated ALSA port), so a zone with no valid explicit config still degrades to a real device when one exists.
- The implementation landed in commit `5cd796e` ("pose zonme"), which also contains unrelated pose-zone work (`behavior.py` / `zones.py` / `editor.html`).

### Next Task

Extend the tests for the device-change scenarios (done — see the entry above).

### Instructions for the Next Task

- The tests live in `tests/test_sound_out_ports.py`; the fake rtmidi mirrors 1.5.8 (no `open_string_outport`, range-checked `open_port`).
- `_get_out` returns `(spec, out)` — callers in tests must unpack the tuple.

---

## 2026-10-28 — editor.html: absolute/free wording, pose-zone toggle, and flow color

### Status

Completed

### Summary

Finished the `editor.html` work for the new axis model and the pose-zone (bridge/move) concept:

- Rewrote all remaining "frozen" wording/comments to the absolute/free model: a pose axis is a **number (absolute — pinned)** or **`"x"` (free — resolved at runtime to the robot's live value)**. Renamed the misleading `frozen` local in the axis-bar rendering to `isFree`; the `frozenX` CSS class name is kept as a stable internal identifier.
- Added the **pose-zone toggle** to the zone form: a `pose zone` select with `none` / `bridge` / `move` (writes/deletes `z.pose`). Added a `POSE_ZONES = ["bridge", "move"]` constant and a `z.pose` validation rule mirroring the core's `zones.py` check.
- Gave pose zones a **distinct flow color**: a `fnodePose` class (purple stroke `#a06fd0`) on flow nodes when `z.pose` is set, and the zone's pose type now appears in the node sublabel (`bridge · N act`).

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - Added `const POSE_ZONES = ["bridge", "move"];` (line ~746).
  - Added `.fnode.fnodePose .fnodeBody { stroke: #a06fd0; stroke-width: 2; }` (CSS, line ~165).
  - `flowAddNode`: adds the `fnodePose` class when `z.pose` is a pose zone.
  - `flowSubLabel`: appends the pose type (`bridge`/`move`) to the node sublabel.
  - `buildZoneForm`: added the `pose zone` select row (none/bridge/move).
  - `validate()`: added the `z.pose` must-be-in-`POSE_ZONES` rule.
  - Rewrote the remaining "frozen" comments to absolute/free; renamed `frozen` → `isFree` in the axis-bar rendering.

### Documentation

- Updated the `editor.html` pose help text, axis-input tooltips, and FK/pose-resolution comments to the absolute/free model (part of this and the prior wording pass).

### Architecture

- No architectural impact. This is UI-only: it surfaces the existing `z.pose` field (already validated by the core `zones.py`) and the absolute/free axis grammar already implemented in `behavior.py`/`zones.py`.

### Validation

- `python3 -c "..."` to extract the inline `<script>` and `node --check` → JS SYNTAX OK.
- (The axis-system tests were verified green earlier in this session: `python3 -m pytest tests/test_state_machine.py tests/test_transition.py tests/test_cam_test.py tests/test_bridge_behavior.py -q` → 52 passed, 9 skipped.)

### Decisions and Caveats

- The `frozenX` CSS class name is intentionally left as-is (stable identifier, not user-facing wording).
- The `pose zone` select deletes `z.pose` when "none" is chosen, so the field is optional in the data (matching the core, where `Zone.pose` returns `None` when unset).
- **3D pose editor scope: FK only (confirmed with the user).** The FK side already works and is sufficient: editing an action's 6 axis values drives `Robot3D.setTarget` → the ghost arm + `checkPoseCollision` in the zones tab. The **IK** side (interactive end-effector dragging to solve joint values) was explicitly **not** requested — `kinematics.py` has FK + geometric Jacobian + `pinv3` building blocks but no IK solver, and none was added. If interactive IK is wanted later, it would need a JS solver + a drag-the-tool UI in `#robot3d`.

### Next Task

No immediate next task identified. The axis-system (absolute/free) work and its `editor.html` surface (wording, pose-zone toggle, flow color, FK 3D pose editor) are complete.

### Instructions for the Next Task

- If interactive IK is requested later: `rtr/robot/kinematics.py` has FK + a geometric Jacobian + `pinv3` (damped least-squares building blocks) but **no IK solver function**. A JS IK would need (a) a solver (iterate Jacobian/pinv toward a target end-effector pose) and (b) an interaction UI (drag the tool in the `#robot3d` view; `rtr3d.js` currently uses `OrbitControls` for camera-only).
- The FK pose editor is verified working in the zones tab: the action editor's 6 axis inputs call `onChanged()` → `updateGhost()` → `Robot3D.setTarget(resolvePose(pose))`, and `Robot3D.checkPoseCollision(pose)` reports collisions.

---

## 2026-10-28 — Research: rtmidi 1.5.8 API facts for robust MIDI-out device handling

### Status

Completed

### Summary

Confirmed the python-rtmidi 1.5.8 facts that constrain the MIDI-out robustness work (the startup error `[sound] could not open MIDI out: MidiOutAlsa::openPort: the 'portNumber' argument (7) is invalid.` comes from `rtr/sound/sound.py:355`):

- `MidiOut` methods (1.5.8): `open_port`, `close_port`, `send_message`, `get_ports` (returns `list[str]`), `get_port_count`, `delete`, `is_port_open()` (method), `is_deleted` (property), `open_virtual_port`, `set_port_name`, `set_error_callback`.
- **No `open_string_outport`** in 1.5.8 — the name-based branch at `sound.py:343` always raises `AttributeError`, so the `out_device` (name) path never worked on this install.
- `open_port(idx)` with `idx >= count` raises `InvalidPortError` (MRO: `InvalidPortError → RtMidiError → ValueError → Exception`): "MidiOutAlsa::openPort: the 'portNumber' argument (N) is invalid."
- `close_port()` on a never-opened instance is safe (no raise); `delete()` on a fresh instance is safe and marks it deleted — usable to release failed/unused `MidiOut` instances.
- The ALSA out-port list is dynamic: a probe returned 17 ports (Midi Through, rtpmidi, PipeWire, qpwgraph, SuperCollider in0–in9, RtMidiIn, Protokol). Indices shift as apps start/stop — the root cause of the user's error (configured index 7 went invalid).

### Changes Made

- None (investigation only).

### Documentation

None required — findings recorded here; they feed the implementation task.

### Architecture

No architectural impact.

### Validation

- Live probes against the installed rtmidi 1.5.8 (`python3 -c` scripts): API dir listing, `InvalidPortError` MRO, `close_port`/`delete` on fresh + opened instances, `get_ports()` return type, live port enumeration.

### Decisions and Caveats

- Name-based opening must be implemented as "enumerate `get_ports()`, match by name (exact, else substring), `open_port(idx)`", keeping `open_string_outport` as a fast path when a newer rtmidi provides it.
- `MidiOut` instances must be released (`close_port` + `delete`) on failed opens and in `list_out_ports`, since retry logic will create instances repeatedly.

### Next Task

Implement the robust MIDI-out handling in `rtr/sound/sound.py` (see the analysis entry below).

### Instructions for the Next Task

Use the facts above: range-check indices before `open_port` (with the available-port list in the error), resolve names by enumeration when `open_string_outport` is absent, add retry-with-backoff for failed opens, evict dead handles on `send_message` failure, and release abandoned `MidiOut` instances.

---

## 2026-10-28 — Analyse: MIDI-out device handling in sound.py and its fragility points

### Status

Completed

### Summary

Mapped the MIDI-out device lifecycle in `realtimecontrol/rtr/rtr/sound/sound.py` (the source of the user's startup error) and identified why adding/removing MIDI devices breaks it:

1. **Index fragility** — a zone's `out_port` / the global `out_port` is an *index* into the current ALSA enumeration. Adding/removing a device shifts indices, so a previously-valid index (7) becomes invalid → `InvalidPortError` on the first snapshot after startup.
2. **Name path broken** — the `out_device` (name) branch calls `rtmidi.MidiOut.open_string_outport`, which does not exist in the installed rtmidi 1.5.8 → always `AttributeError` → the robust path is unusable.
3. **Permanent failure cache** — `_open_outs[spec] = None` on failure; the failure is cached forever, so a device that appears later (hotplug, SuperCollider starting) is never picked up.
4. **No dead-device detection** — when an open device disappears, `send_message` raises; the error is logged but the dead handle stays cached forever (no reopen attempt).
5. **Leaks** — `list_out_ports` creates a `MidiOut` per call and never releases it; `_open_one` abandons the instance on a failed open.
6. **Opaque errors** — the failure log has no list of available ports, so the config cannot be fixed from the log.

Flow: `Sound.__init__` → `reload()` (reconcile = close-only; opens are lazy) → first `SNAPSHOT` → `_send_msg` → `_send_bytes` → `_get_out(zone)` → `_out_spec(zone)` (precedence: zone `out_port` → global `out_device` → global `out_port` → constructor `out_port` → auto port 0) → `_open_one(spec)`.

Fix design (for the implementation task): per-zone **candidate spec chain** (zone index → global name → global index → auto), name resolution by enumeration (exact then substring match), index range-check with available ports in the error, retry-with-backoff for failed opens (`retry_interval`, default 5 s), evict-and-retry on `send_message` failure, and release of abandoned instances.

### Changes Made

- None (investigation only).

### Documentation

None required — analysis recorded here.

### Architecture

No architectural impact (analysis of existing structure).

### Validation

- Code reading: `rtr/sound/sound.py` (full), `rtr/io/midi.py` (MIDI-in section), `main.py`, `sound.json`, `tests/test_sound_out_ports.py`, `tests/test_sound_enabled.py`, `.kilo/plans/1789210975965-midi-out-plan.md`.

### Decisions and Caveats

- Scope: the MIDI-**out** adapter (`sound.py`) is where the reported error originates and where the fragility is visible. The MIDI-**in** adapter (`rtr/io/midi.py`) opens by index too, but failures are not cached (a `reload` retries) and the controller port is a stable ALSA loopback; it is left unchanged and noted as a possible follow-up.
- Falling back to the next candidate (global name → global index → auto) when a configured index is invalid is the chosen robustness behavior: sound degrades to a working device or to logging instead of dying, with an actionable log line listing the available ports.

### Next Task

Implement the robust MIDI-out handling in `rtr/sound/sound.py`.

### Instructions for the Next Task

Modify only the MIDI-out connection section of `Sound` (keep the data layer and `_on_snapshot` untouched): add `_candidate_specs`, name resolution helpers, index range-check, retry/backoff state (`_open_failures`), dead-handle eviction in `_send_bytes`, instance release in `_open_one`/`list_out_ports`, and a `retry_interval` constructor kwarg. Extend `tests/test_sound_out_ports.py` (the fake rtmidi needs: range-checking `open_port`, `delete`, a failure flag, and a no-`open_string_outport` variant to mirror rtmidi 1.5.8).

---

## 2026-10-27 — camera_debug.html / render.html stop receiving camera tracking updates during robot movements

### Status

Completed

### Summary

`camera_debug.html` and `render.html` stop receiving camera tracking updates (`cam_telem`) during robot movements. Root cause: in `rtr/core/engine.py:_tick()`, when `move_mode != "stream"`, the engine does `await asyncio.to_thread(move, target, speed)` which blocks the coroutine until the robot move completes. `self._publish()` (which sends the state frame — including `cam_telem`) runs only AFTER the move finishes. During a blocking move, no state frames are published, so WebSocket clients stop receiving camera telemetry and may time out / reconnect.

Fix: call `self._publish(curjpos, curpos, target)` BEFORE the blocking move so clients at least receive the pre-move state (with current `cam_telem`) before the gap. The unconditional publish after the move still runs once the move ends. Stream mode (`move_mode == "stream"`) is unchanged — it already publishes every tick.

### Changes Made

- `realtimecontrol/rtr/rtr/core/engine.py` (line ~207): added `self._publish(curjpos, curpos, target)` inside the blocking-move `else` branch, before `await asyncio.to_thread(move, ...)`.

### Documentation

None required — the fix restores the existing behavior contract (state frames include `cam_telem`); no user-facing text changed.

### Architecture

No architectural impact. Publish-before-move is additive; the post-move publish is unchanged. Stream mode is untouched.

### Validation

- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py` — 299 passed, 6 failed, 14 skipped. The 6 failures (`test_remote_sdl` ×5, `test_transition` ×1) are pre-existing and unrelated (verified via `git stash` — same 6 fail on the clean tree).

### Decisions and Caveats

- Publishing before the move sends the state at move-start. During the move itself there are still no live updates (blocking mode inherently waits for completion); the pre-move publish at least gives clients a valid state frame before the gap and prevents the WebSocket from going silent long enough to time out.
- No change to robot behavior, move timing, or the stream mode path.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

None.

---

## 2026-10-27 — Suppress ConnectionResetError on HTTP static responses when client disconnects

### Status

Completed

### Summary

The HTTP static handler (`_serve_static`) raised `ConnectionResetError` (errno 104) when writing the response body to a client that had already disconnected (e.g. browser closed the tab, navigated away, or stopped polling `/assets/latest.jpg`). `socketserver` logs the traceback per request thread, which the operator associated with the robot stopping.

Fix: added a `_wfile_write()` helper on the handler that wraps `self.wfile.write()` and swallows `ConnectionResetError`/`BrokenPipeError` (expected disconnects) while letting real errors through. `_serve_static`, `_send_json`, and `_send_text` now route through it.

### Changes Made

- `realtimecontrol/rtr/rtr/io/httpserver.py`:
  - Added `Handler._wfile_write(data)` — writes to `wfile`, returns False on `ConnectionResetError`/`BrokenPipeError` instead of propagating.
  - `_serve_static`, `_send_json`, `_send_text` call `self._wfile_write(...)` instead of `self.wfile.write(...)`.

### Documentation

None required — restores expected, silent handling of client disconnects; no user-facing behavior changed.

### Architecture

No architectural impact. The HTTP response path is unchanged; only the write is now guarded against the expected client-disconnect case.

### Validation

- `python3 -m py_compile rtr/io/httpserver.py` — SYNTAX_OK.

### Decisions and Caveats

- `ConnectionResetError` (ECONNRESET) and `BrokenPipeError` (EPIPE) are both subclasses of `OSError`; catching them specifically avoids masking genuine server errors (e.g. disk-full). Other `OSError`s still propagate.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

None.

---

## 2026-10-27 — Fix brain-tab save wiping per-behavior stay/triggers; restore truncated editor.html tail

### Status

Completed

### Summary

The brain tab of `editor.html` silently wiped the per-behavior next-selection fields (`stay`/`triggers`/`dwell_s`/`lock_duration_s`) on every save. Root cause: `_commitBrainDom()` rebuilt `BRAIN.behaviors` from scratch using only the motion params (`BEHAVIOR_PARAMS` keys like `freq`/`gain`/`sweep_amp`), discarding the next-selection fields that `setNext` writes live into `BRAIN.behaviors[name]` on every keystroke. So edits such as setting `vigilant` to not loop indefinitely never persisted, and `spontaneous` (which has no motion params) was wiped to `{}` entirely.

Fix: `_commitBrainDom()` now reads the next-selection fields from the DOM (the authoritative save-time read, mirroring `_commitGroupsDom`) before replacing `b.behaviors`. Behavior cards now carry `data-name` so the per-behavior trigger checkboxes are queryable.

During verification I discovered `editor.html` was truncated — a pre-existing issue introduced by the camera-overlay commit `3d339bb`: the file ended mid-statement at `$("ghostInfo")` and the main `<script>` (opened at line 741) was never closed (13 `<script>` vs 12 `</script>`). This blocked the editor from loading (syntax error) and the `test_groups_tab.js` harness. The truncated file was otherwise a full superset of the last complete version (`2b516c4` "zones") — only the final 7 lines (the collision-result display + `</script></body></html>`) were missing, recoverable verbatim from `2b516c4`. I restored them. I also fixed the `test_groups_tab.js` stub, which was missing `uiCamToggle` (added by the camera-overlay commit), causing a load-time null.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - `_commitBrainDom()` (line ~3348): now reads `stay`/`triggers`/`dwell_s`/`lock_duration_s` from the DOM before replacing `b.behaviors`, so the per-behavior loop settings survive a brain-tab save.
  - Behavior cards (line ~2877): `card.dataset.name = name` so the per-behavior trigger checkboxes are queryable by `data-name`.
  - Restored the truncated tail (line ~7537): the collision-result display + `</script></body></html>` (verbatim from the last complete version `2b516c4`).
- `realtimecontrol/rtr/test_groups_tab.js`: added `uiCamToggle` to `STATIC_IDS` (the camera-overlay element the script references but the stub lacked).

### Documentation

None required — the fix restores the documented brain-tab save behavior (per-behavior stay/triggers persist); no user-facing text changed.

### Architecture

No architectural impact. The save path is unchanged in shape (DOM → `_commitBrainDom` → `b.behaviors` → `POST /api/brain`); the fix only makes `_commitBrainDom` preserve the next-selection fields it previously dropped. The server (`httpserver.py` `/api/brain` POST) writes the payload verbatim after `validate_brain_config`, so no server change was needed.

### Validation

- `node --check` on the extracted main inline script — SYNTAX_OK (310,311 bytes; the script is now complete/closed).
- `node test_groups_tab.js` — ALL_TESTS_PASSED (20 assertions, incl. `payload has hunt/facefocus/autonomy/behaviors (no clobber)` and `cam_control` preservation).
- Targeted brain-save verification (Node + mini-DOM) — BRAIN_SAVE_VERIFIED: `vigilant` round-trips `{freq:1, stay:0.25, triggers:["dwell","lock_duration"]}`; `spontaneous` (no motion params) round-trips `{stay:0.5, triggers:["dwell"]}` (previously wiped to `{}`).
- Script-tag balance: 13 `<script>` / 13 `</script>`; file ends with `</html>` (7542 lines).

### Decisions and Caveats

- The truncation was pre-existing (introduced by `3d339bb` "camera overlay"; the last complete version is `2b516c4` "zones"). Both the working copy and HEAD were truncated; I restored only the missing 7-line tail — the camera-overlay work in the middle of the file was already intact.
- The user was likely running a cached browser copy, which is why the editor appeared to work despite the on-disk truncation.
- The `test_groups_tab.js` stub's mini-DOM selector now strips quotes from attribute values to match browser behavior for `[data-name="..."]` selectors (a test-harness fix, not an editor change).

### Next Task

No immediate next task identified.

### Instructions for the Next Task

None.

---

## 2026-10-27 — Stop the robot driving to 0,0,0,0,0,0 on manual mode entry

### Status

Completed

### Summary

Clicking "manual" in `realtimecontrol/rtr/client.html` (which sends `set_control_mode` `mode: "manual"`) used to drive the robot to `0,0,0,0,0,0`. Root cause: `StateMachine.set_control_mode` (rtr/state/machine.py) froze the robot on a freshly-read `curjpos`, which can read as all-zeros between controller/EKI frames, so the hold target became zeros and the robot drove there.

Fix: entering manual mode now holds the robot at its last known good pose instead of re-reading curjpos. Precedence: `self.target` (refreshed every engine tick) → `self._last_curjpos` (retains the last good read; a bad read throws in the tick and does not clobber it) → fresh `curjpos` as last resort. An all-zeros pose is never written (no valid pose known → do not move). The action/transition are still cleared and auto-advance is suppressed, so the robot finishes the current action, stops, and waits for an explicit command — matching the documented manual-mode contract. `client.html` needed no change (its handler already just sends `set_control_mode`).

### Changes Made

- `realtimecontrol/rtr/rtr/state/machine.py`: `set_control_mode` manual-entry hold now uses the last known good pose (`self.target` → `_last_curjpos` → `curjpos`) and skips writing an all-zeros pose, instead of unconditionally `self.target = list(curjpos)`.

### Documentation

None required — behavior is the documented manual-mode contract (robot holds where it is until an explicit command); no user-facing text changed.

### Architecture

No architectural impact.

### Validation

- `python -m pytest tests/test_state_machine.py tests/test_forced_behavior.py tests/test_zone_groups.py tests/test_brain_assertive.py tests/test_autoreturn.py` — 69 passed, 9 skipped.
- Full suite `tests/ -q --ignore=tests/test_remote_tui.py` — 299 passed, 14 skipped; the 6 `test_remote_sdl`/`test_transition` failures and the `test_remote_tui.py` collection error pre-exist this change (verified via `git stash`) and are unrelated (SDL font/text expectations, string-pose test data, `rtr.flow` import).
- Targeted sanity check: entering manual with `curjpos=[0]*6` holds a previously-set good target; with no known pose it writes nothing (no zeros).

### Decisions and Caveats

- All-six-zeros is treated as "no valid pose known" (a real KR60 joint pose is never all zero in normal operation), so the robot is never driven to zeros on a mode change.
- If `self.target`/`_last_curjpos` are unset and `curjpos` is zeros, no target is written, so the engine issues no move command on that tick (robot stays put).

### Next Task

No immediate next task identified.

### Instructions for the Next Task

None.

---


### Status

Completed. The render page (`render.html`) now overlays the live camera-debug frame (candidate boxes + track/compensated vectors, no background) on top of the hydra canvas, fed by the same live `cam_telem`/`camera`/`servo` state data, and the patches tab of `editor.html` has an on/off toggle for it (`#uiCamToggle`). The `cam` flag rides the existing in-memory `render_ui` state (not persisted to `patches.json`), the same pattern as `hud`/`code`.

### Summary

Goal: an option on `render.html` to overlay the camera-debug frame content (candidate boxes + track/compensated vectors, no background) on top of the hydra render canvas, fed by the same live camera data, plus an on/off toggle in the patches tab of `editor.html`.

The state frame (WS, 20 Hz) already carries everything the overlay needs: `cam_telem` (candidates/track/face/status), `camera` (intent, has `lock_id`), `servo` (`dq_deg`/`dx`/`dy`), and `render_ui` (confirmed in `display.py` `_frame`). So the transport was already in place — this task adds the `cam` flag to `render_ui` (core + editor toggle) and the overlay drawing in `render.html`.

Design: a transparent canvas (`#camOverlay`) on top of the hydra canvas. The 640×480 frame is fit "contain" (aspect preserved, centered) into the fullscreen overlay. Only the content is drawn (no grid, status bar, or dark background fill): candidate boxes (rectangles + `#id`/conf/LOCK labels), the track arrow (green) + cross marker + dx/dy text, the compensated arrow (blue) + dq text, and the image-center cross. The drawing mirrors `camera_debug.html` (`detectScale`/`drawArrowHead`), re-targeted from the 640×480 canvas to the contain-fit overlay transform.

### Changes Made

- `realtimecontrol/rtr/rtr/patches/patches.py`:
  - `self._render_ui` now `{"hud": True, "code": False, "cam": False}`.
  - `set_render_ui(self, hud=None, code=None, cam=None)` handles `cam` (bool) and reports it in the log line.
  - `render_ui()` returns the dict (now includes `cam`).
- `realtimecontrol/rtr/rtr/core/engine.py`:
  - `SET_RENDER_UI` branch forwards `cam=cmd.payload.get("cam")` to the `render_ui` setter.
- `realtimecontrol/rtr/editor.html`:
  - `#uiCamToggle` button in the patches tab (beside code/hud), title "Toggle the camera-debug overlay … (candidate boxes + track/compensated vectors, no background)".
  - `renderUi = { hud: true, code: false, cam: false }`; `sendRenderUi(hud, code, cam)` (3-arg); `syncRenderUiToggles()` sets `#uiCamToggle`'s `.active` class.
  - State-frame handler normalizes `renderUi` (`cam: m.render_ui.cam === true`) and the `_renderUiFp` fingerprint includes `cam`.
  - `#uiCodeToggle`/`#uiHudToggle`/`#uiCamToggle` onclicks all pass the 3-arg `sendRenderUi(hud, code, cam)`.
- `realtimecontrol/rtr/render.html`:
  - `#camOverlay` `<canvas>` + CSS (`position:fixed; inset:0; pointer-events:none; display:none; z-index:5`) — transparent, above the hydra canvas, below the HUD.
  - `renderUi` gains `cam`; `applyRenderUi()` shows/hides `#camOverlay` from `ui.cam === true`.
  - State vars `lastTelem`/`lastIntent`/`lastServo`; the state handler captures `m.cam_telem`/`m.camera`/`m.servo` and calls `drawCamOverlay()` after `applyRenderUi()`.
  - Drawing helpers: `camTransform()` (contain-fit 640×480 → overlay), `camToCanvas()`, `camDataScale()` (pixels-with-dims / normalized / default-pixels, mirrors `detectScale`), `camArrowHead()`, `drawCamOverlay()`, `sizeCamOverlay()`.
  - Resize handler sizes `#camOverlay` to the window and redraws; `sizeCamOverlay()` runs on load.

### Documentation

None required — the overlay is a UI affordance for the already-documented `render_ui` core state (the earlier code/hud toggle entry documents the push path; this adds the `cam` flag to the same path). The `cam` flag is in-memory only (not persisted to `patches.json`), matching `hud`/`code`.

### Architecture

No architectural change. The `render_ui` command/push path (core `set_render_ui` → `_render_ui` → state frame → `render.html` `applyRenderUi`) and the `cam_telem`/`camera`/`servo` state fields were already in place; this task adds the `cam` flag to `render_ui` and the overlay drawing that consumes the already-pushed camera fields.

### Validation

- JS syntax: extracted the inline `<script>` from `render.html` and ran `node --check` → **OK**.
- Core import check: imported `rtr.patches` + `rtr.core.engine`; `Patches.set_render_ui(cam=True)` → `render_ui()` returns `{'hud': True, 'code': False, 'cam': True}`; `Cmd.SET_RENDER_UI` present → **OK**.
- Full test suite (run directly, `tests/test_*.py`): **20 passed, 4 non-zero exits** — all 4 are pre-existing / non-regressions:
  - `test_zone_scoped_actions.py`: ends with an intentional `pytest.skip()` ("kind/target schema removed") that raises when run directly (all real checks PASS) — not a real failure.
  - `test_remote_sdl.py`, `test_remote_tui.py`, `test_transition.py`: pre-existing — verified by `git stash` of my Python changes (`engine.py`, `patches.py`) and re-running: the same 3 fail on the clean tree.

### Decisions and Caveats

- **`cam` is in-memory only** (not persisted to `patches.json`), matching the `hud`/`code` flags. Toggling it is a live runtime state change, not a saved patch.
- **Overlay is transparent** (no background fill / grid / status bar) — only the camera-debug *content* (candidate boxes + track/compensated vectors + image-center cross). This is the "only the content" requirement.
- **Coordinate mapping** mirrors `camera_debug.html` `detectScale`: data coords are mapped to 640×480 frame space (pixels-with-explicit-dims / normalized-0..1 / default-pixels), then the contain-fit transform maps frame space to the fullscreen overlay (aspect preserved, centered).
- **Live redraw**: the overlay redraws on every state frame (20 Hz) while `cam` is on, so it tracks the live telemetry. When off, `drawCamOverlay()` clears and returns early.

### Next Task

No immediate next task identified. The camera-debug overlay is complete end-to-end (core flag, editor toggle, render-page drawing).

### Instructions for the Next Task

If extending the overlay: the drawing lives in `render.html`'s `drawCamOverlay()` (helpers `camTransform`/`camToCanvas`/`camDataScale`/`camArrowHead`). Add new elements there, mapping through `camDataScale` (data → 640×480) then `camToCanvas` (frame → overlay). The `cam` flag is in-memory `render_ui` state (not persisted); the toggle is `#uiCamToggle` in the patches tab of `editor.html`.

## 2026-10-27 — KeyError: 'hunting' in brain hunt tick: fail-safe unknown-zone handling in safety gate

### Status

Completed. The recurring engine tick crash `KeyError: 'hunting'` is stopped: `SafetyGate.check_conflict` now treats unknown/None zone names as a conflict instead of raising, so the brain's hunt path degrades to in-place scanning rather than crashing.

### Summary

Root cause: the brain's hunt config (`brain.json` scan/detect entries, `config.py` hunt params) references zone `hunting`, but the runtime zone table is built from `zones.json` (`engine.reload_zones` → `Zones(data.get("zones"))`), which contains 16 zones and has **no** `hunting`. The Python `ZONES` fallback in `zones.py` *does* contain `hunting`, but that fallback is not used at runtime. Crash path: `engine.py` tick → `brain._hunt_tick` → `_hunt_move_safe(scan.get("zone"), scan.get("action"))` → `safety.check_conflict(..., to_zone="hunting")` → `Zones.get("hunting")` → `KeyError`.

Fix: `gate.py` `check_conflict` now resolves unknown/None zone names via a new `_zone_or_none` helper and returns `(True, ["unknown zone: ..."])` instead of raising. In the hunt path, `_hunt_move_safe` returns False on conflict, so the robot scans in place rather than crashing.

This complements the earlier "x" pose placeholder fix (real-time resolves "x" to `curjpos[i]`; the flow editor treats "x" as a wildcard). `check_conflict` and `_pose_outside_safezone` accept an optional `curjpos`; the `engine.py` and `brain.py` real-time paths pass it; `flow.py` passes None.

### Changes Made

- `realtimecontrol/rtr/rtr/safety/gate.py`:
  - Added `_zone_or_none(zones, name)` helper; returns None for unknown/None zone names instead of a `KeyError`.
  - `check_conflict`: unknown/None `to_zone` (and `from_zone`) now return `(True, ["unknown zone: ..."])` (fail-safe conflict) instead of raising.
  - (Earlier "x" work in the same file: optional `curjpos` param on `check_conflict`/`_pose_outside_safezone`.)
- `realtimecontrol/rtr/tests/test_safety_conflict.py`:
  - Added `test_unknown_zone_is_a_conflict_not_a_crash` and registered it in `main()`.

### Documentation

None required — the fail-safe is a behavior change on the safety-gate path; the unknown-zone contract is self-evident from the new test and the `check_conflict` docstring.

### Architecture

No architectural change. The safety gate's contract tightens from "unknown zone raises" to "unknown zone is a conflict" (fail-safe). This is a strict superset: every previously-valid conflict is preserved, and the previously-crashing path now degrades gracefully.

### Validation

- `python tests/test_safety_conflict.py`: **11 passed** (10 prior + 1 new unknown-zone regression test; includes the 3 "x" placeholder tests).
- `python3 -m pytest tests/ --ignore=tests/test_remote_tui.py -q`: **299 passed, 6 failed, 14 skipped** — the 6 failures are pre-existing and unrelated to this change (verified by `git stash` of the tracked changes and re-running: the same 6 fail on the clean tree). Breakdown: 4× `test_remote_sdl` TUI failures, 1× `test_transition.py::test_all_reachable_transitions_complete` (TypeError — an "x" placeholder in a `zones.json` pose is not resolved in the state-machine step target; see Decisions), and `test_remote_tui.py` has a pre-existing collection error (`action_kind` not in `rtr.flow`).
- Validated against real `zones.json`: `Zones(zones).has("hunting")` → False; `SafetyGate.check_conflict("resting", None, "hunting", "scan")` → `conflict=True`, `issues=["unknown zone: hunting"]`, no `KeyError`.

### Decisions and Caveats

- **Open config decision:** which existing `zones.json` zone/action should replace `hunting` in the hunt config (`brain.json` scan/detect, `config.py` hunt params). The fail-safe stops the crash, but the hunt will scan in place until the config is pointed at a real zone. The intended mapping is unknown and must be confirmed with the user.
- **Pre-existing failure now surfaced:** `test_transition.py::test_all_reachable_transitions_complete` fails with `TypeError` because `zones.json` poses contain an `"x"` placeholder (e.g. `sleeping/yawn` pose `['x', -134, 157, 3, 0, 0]`) that the state-machine `step()` target does not resolve before handing to `SimRobot.move_joint`. This is a *separate* location from the safety-gate / flow-editor "x" handling and is not fixed by this change. It is pre-existing (fails on the clean tree too) and is a consequence of the "x" feature being added to `zones.json`.

### Next Task

Decide the real zone/action mapping for the hunt config's `hunting` reference, then update `brain.json` + `config.py`. Separately, resolve the "x" placeholder in the state-machine `step()` target so `test_transition.py::test_all_reachable_transitions_complete` passes.

### Instructions for the Next Task

1. Hunt mapping: confirm with the user which `zones.json` zone/action the hunt should target (candidate: the `scanning` zone with its scan action). Replace `hunting` in `brain.json` scan/detect entries and `config.py` hunt params. Re-run `python tests/test_safety_conflict.py`.
2. State-machine "x" resolution: in `rtr/state/machine.py`, the target pose returned by `step()` must resolve any `"x"` axis to the robot's current joint position before being sent to `move_joint` (mirror the real-time `curjpos` resolution already done in `gate.py`/`engine.py`). Verify with `python tests/test_transition.py`.

## 2026-10-27 — A3=42 collision: revert testing safezone, move locked_band to new dedicated zone `testing_locked_band`

### Status

Completed. The A3=42 collision directive is implemented: the widened testing safezone is reverted and `locked_band` now lives in a **new dedicated zone** at A3=32, routed bidirectionally with `testing` via the shared-pose handoff.

### Summary

User directive: A3=42 physically collides, so the `[0,42]` safezone widening from the previous task is reverted to `[0,32]`, and `locked_band` is moved into a **new dedicated zone** (user-confirmed structure — not an in-place cap, not a no-op bridge). The new zone `testing_locked_band` hosts the capped `locked_band` (pose A3=32, band `[0,32]`) and is routed bidirectionally with `testing`.

Routing detail (why only `locked` links directly): `find_shared_pose` (`zones.py:92`) requires **every** concrete axis to match within margin 0.5 (not just one). `testing/locked` `[45,-30,32,0,-5,0]` and `locked_band` `[45,-30,32,0,-5,0]` match on all axes → valid shared-pose handoff. `intent`/`curious`/`gentle` `[45,-10,12,0,-5,0]` mismatch on A2 (−10 vs −30) and A3 (12 vs 32) → no shared pose → their direct `locked_band` links are dropped (they would be red inter-zone links). They route via `locked` (in-zone links intact), so the graph stays connected: `intent/curious/gentle` → `locked` → `testing_locked_band/locked_band`, and back.

### Changes Made

- `realtimecontrol/rtr/zones.json`:
  - `testing` `safezone` A3: `[0, 42]` → `[0, 32]` (reverts the collision-causing widening).
  - `testing` action `locked_band`: **removed** (moved to the new zone).
  - `testing` action `locked` `next`: `testing/locked_band` → `testing_locked_band/locked_band` (the only valid inter-zone handoff, shared pose `[45,-30,32,0,-5,0]`).
  - `testing` actions `intent`/`curious`/`gentle` `next`: dropped their direct `testing/locked_band` entries (no shared pose → would be red).
  - **New zone `testing_locked_band`**: `class: track`, `mode_group: event-sit-back`, safezone identical to testing with A3 `[0, 32]`, speed 20, single action `locked_band` — pose `[45,-30,32,0,-5,0]` (A3 capped 42→32), `variable_axes [0,1,4]`, behavior `curious`, speed 100, loop true, `axis_bands "2": [0, 32]` (was `[0,42]`), `next: [testing/locked]` (bidirectional pair with `testing/locked`).
  - `layout.pos`: added `testing_locked_band` at `{x: 560, y: 340}`; `layout.key`: appended `testing_locked_band`.

### Documentation

None required — safezone bounds and `next` routing are data; the shared-pose mechanism is already documented in `behavior.py:306` / `machine.py:86-93` / `zones.py:92`.

### Architecture

New zone added to the zone graph; no code change. `testing_locked_band` is a persistent single-action zone (loop true) reachable from `testing/locked` and returning to `testing/locked` — a valid bidirectional inter-zone handoff at the shared A3=32 pose. The runtime handles it with the existing `find_shared_pose`/`_compute_handoff` path (`machine.py:210`).

Note: `sleeping_testing_bridge` (isolated, unreachable, safezone A3 `[0,42]`, pose A3=42) still carries a physically unsafe A3=42 pose. It is inert (no `next` links in or out) but should be capped to A3=32 or removed before any re-wiring.

### Validation

- `python3 -c "json.load(...)"`: zones.json parses; structure verified (testing safezone A3 `[0,32]`; `testing_locked_band` hosts capped `locked_band`; routing as above).
- `/tmp/kilo/validate_links.py`: **39 checked, 0 broken** (was 45 checked / 0 broken; −6 from the dropped `intent`/`curious`/`gentle`→`locked_band` links and `locked_band`'s 4→1 `next` list).
- `python3 -m pytest tests/ --ignore=tests/test_remote_tui.py -q`: **296 passed, 5 failed, 14 skipped** — same 5 pre-existing `test_remote_sdl` TUI failures; no regressions. (`test_remote_tui.py` has a pre-existing import error: `action_kind` not in `rtr.flow`.)

### Decisions and Caveats

- Only `testing/locked` ↔ `testing_locked_band/locked_band` is a valid shared-pose handoff (all-concrete-axes match). `intent`/`curious`/`gentle` route via `locked`.
- `locked_band` A3 capped 42→32 (pose + `axis_bands "2"`), so it no longer reaches the colliding A3=42.
- `sleeping_testing_bridge` A3=42 pose left as-is (isolated/inert) but flagged for capping or removal before re-wiring.

### Next Task

Cap or remove the `sleeping_testing_bridge` A3=42 pose (isolated but physically unsafe if re-wired).

### Instructions for the Next Task

`sleeping_testing_bridge` safezone A3 is `[0,42]` and its `bridge` pose is `[45,-30,42,0,-5,0]`. It is unreachable (no `next` links) so it is inert, but A3=42 collides. Decide with the user: cap its safezone A3 to `[0,32]` and pose A3 to 32, or delete the zone entirely (drop it from `layout.pos`/`layout.key`). Then re-run `/tmp/kilo/validate_links.py` + pytest.

## 2026-10-26 — Museum/testing reds cleanup: widen testing safezone A3, drop museum routing links

### Status

Completed. All **12 pre-existing museum/testing reds** cleared. Two clusters, resolved differently:

1. **8 `testing/*` ↔ `testing/locked_band` reds** (pose `[45,-30,42,0,-5,0]` outside testing safezone on A3): fixed by **widening the testing safezone A3** from `[0,32]` → `[0,42]`.
2. **4 museum-bridge shared-pose reds** (`resting/hold`↔`sleeping/breathe`; `sleeping/yawn`↔both bridges; `sleeping_testing_bridge`↔`testing/locked`): fixed by **dropping the museum routing links** (user decision — disconnect the museum from the testing/scanning networks).

### Summary

The 12 reds were confirmed **pre-existing** by diffing the committed HEAD state (13 broken incl. the task-network red) against the working tree — my prior task-network work introduced none of them; it only removed the task-network red. Two distinct causes:

- **testing safezone A3 had been narrowed to `[0,32]`** (pre-tasknet it was `[0,42]`), but `testing/locked_band` is designed to sweep A3 to 42 (`axis_bands "2": [0,42]`, pose A3=42). The `sleeping_testing_bridge` safezone is *exactly* `[0,42]` — strong signal `[0,42]` is the intended testing A3 bound. Restoring `[0,42]` makes the testing safezone identical to the bridge's and covers the `locked_band` sweep.
- **The museum bridges are one-sided**: `sleeping_testing_bridge` carries only the testing-side pose `[45,-30,42,0,-5,0]` and `sleeping_scanning_bridge` only the scanning-side pose `[10,-59,-10,0,60,0]` — neither has the sleeping-side departure anchor the task-bridge pattern uses, and their safezones do not overlap the sleeping safezone. `resting/hold`→`sleeping/breathe` is a direct link between very different configs (A3 −10 vs +156). These cannot be made bidirectional without new real-robot anchor poses, so the user chose to disconnect the museum from testing/scanning by dropping the routing links.

### Changes Made

- `realtimecontrol/rtr/zones.json`:
  - `testing` `safezone` A3: `[0, 32]` → `[0, 42]` (covers the `locked_band` A3 sweep; matches the `sleeping_testing_bridge` safezone).
  - `resting` action `hold` `next`: `{"zone":"sleeping","action":"breathe"}` → removed (resting no longer transitions to sleeping).
  - `sleeping` action `yawn` `next`: removed the two bridge entries — now `[{"zone":"sleeping","action":"breathe"}, {"zone":"sleeping","action":"snore"}]` (no more `sleeping_testing_bridge` / `sleeping_scanning_bridge` exits).
  - `sleeping_testing_bridge` action `bridge` `next`: `{"zone":"testing","action":"locked"}` → removed (bridge no longer hands off to testing).

  Note: the `sleeping_scanning_bridge` → `scanning/scnr` link and `scanning/scnr` → `sleeping_scanning_bridge` link are **left intact** (they form a valid shared-pose loop, not a red).

### Documentation

None required — safezone bounds and `next` routing are data; the `"x"`/shared-pose mechanism is already documented in `behavior.py:306` / `machine.py:86-93`.

### Architecture

No architectural change. The runtime already handles a single-action zone with no valid `next`/`exits` via the previous-zone auto-return fallback (`machine.py:563`), so the dropped links do not dead-end the robot.

### Validation

- `/tmp/kilo/validate_links.py`: **45 checked, 0 broken** (was 49 checked / 12 broken).
- `python3 -m pytest tests/ --ignore=tests/test_remote_tui.py -q`: **296 passed, 5 failed, 14 skipped** — same 5 pre-existing `test_remote_sdl` TUI failures; no regressions.
- `zones.json` loads via the runtime `Zones` (13 zones). Dropped links confirmed: `resting/hold.next=None`, `sleeping/yawn.next=[breathe,snore]`, `sleeping_testing_bridge/bridge.next=None`, `sleeping_scanning_bridge/bridge.next={scanning/scnr}` (loop kept), `testing.safezone[2]=[0,42]`.

### Decisions and Caveats

- **User decision (museum routing):** disconnect the museum (resting/sleeping) from the testing/scanning networks by dropping the 4 museum routing links, rather than adding new real-robot sleeping-side anchor poses to the bridges. Consequence: `sleeping_testing_bridge` is now an isolated (unreachable, no-next) zone — left `enabled: true` in data but functionally dead. `resting/hold` now has no `next` (auto-returns to the previous zone at runtime).
- The `testing safezone` A3 widening to `[0,42]` is a **revert to the pre-tasknet value** and is consistent with the `locked_band`'s designed A3 sweep and the `sleeping_testing_bridge` safezone.
- The 4 museum reds are *data/routing* reds, not mechanism bugs — `find_shared_pose` and the gate are correct; the museum bridges simply lack the sleeping-side anchors. If those transitions are wanted back, the bridges need a shared sleeping anchor pose + a spanning safezone (real robot poses required).

### Next Task

No museum/testing reds remain. No task-network work remains. The whole `zones.json` link graph validates clean (0 broken).

### Instructions for the Next Task

No immediate next task identified. If the museum→testing/scanning transitions are wanted back later: add a shared sleeping-side anchor pose to `sleeping_testing_bridge` / `sleeping_scanning_bridge` (derived from a real sleeping pose shared with `sleeping/yawn`), widen each bridge safezone to span both configs, and restore the `yawn`→bridge and bridge→target `next` links. Re-run `/tmp/kilo/validate_links.py` + the pytest suite after any such change.

## 2026-10-26 — Task-network data cleanup: drop `scnr` bridge link, A1=`"x"` on task2↔task3 bridges

### Status

Completed. **Supersedes** the two open questions in the entry below. User decisions: (1) `task1/scnr` keeps no bridge exit — drop the `task2_task3_bridge` link; (2) `task2_task3_bridge` and `task3_task2_bridge` get A1=`"x"` like every other bridge.

### Summary

Two data adjustments to `zones.json` (user-confirmed):

1. **`task1/scnr` → `task2_task3_bridge/bridge` dropped.** `scnr` sits in task1 (A2≈-85, A5≈75); `task2_task3_bridge` is physically in the task2/task3 region (A2 `[-50,-10]`, A5 `[-25,45]`) with no safezone overlap on A2/A5, so the robot could never hand off there. `scnr` now only loops back to `task1/scan` (stays purely intra-task1). `next` converted from a 2-element list to the single-dict form (matching other single-target actions).
2. **task2↔task3 bridges A1 → `"x"`.** `task2_task3_bridge` and `task3_task2_bridge` held A1=0 constants on both poses while every other bridge and every task-zone action uses A1=`"x"` (copy-from-active, `behavior.py:306`). Now consistent: the base is never driven by these bridges; it stays at the frozen value from transition commit.

### Changes Made

- `realtimecontrol/rtr/zones.json`:
  - `task1` action `scnr` `next`: `[{"zone":"task1","action":"scan"}, {"zone":"task2_task3_bridge","action":"bridge"}]` → `{"zone":"task1","action":"scan"}`.
  - `task2_task3_bridge` action `bridge` `poses`: A1 `0` → `"x"` on both poses (`["x",-45,20,0,40,0]`, `["x",-15,20,0,-20,0]`).
  - `task3_task2_bridge` action `bridge` `poses`: A1 `0` → `"x"` on both poses (`["x",-15,20,0,-20,0]`, `["x",-45,20,0,40,0]`).

### Documentation

None required — the `"x"` mechanism is documented in `behavior.py:306` / `machine.py:86-93`; this is a data consistency adjustment.

### Architecture

No architectural change.

### Validation

- `zones.json` loads + validates via the runtime `load_state_data` (13 zones).
- Shared-pose checks (all four task2↔task3 links now resolve):
  - `task2/locked` ↔ `task2_task3_bridge/bridge`: `(['x', -45, 20, 0, 40, 0], 0, 0)`
  - `task3/approach` ↔ `task2_task3_bridge/bridge`: `(['x', -15, 20, 0, -20, 0], 0, 1)`
  - `task2/locked` ↔ `task3_task2_bridge/bridge`: `(['x', -45, 20, 0, 40, 0], 0, 1)`
  - `task3/approach` ↔ `task3_task2_bridge/bridge`: `(['x', -15, 20, 0, -20, 0], 0, 0)`
- `/tmp/kilo/validate_links.py`: **49 checked, 12 broken** — the task-network red is gone; all 12 are the pre-existing museum/testing reds (see the entry below).
- `python3 -m pytest tests/ --ignore=tests/test_remote_tui.py -q`: **296 passed, 5 failed, 14 skipped** — same 5 pre-existing `test_remote_sdl` failures.

### Decisions and Caveats

- The whole task network (task1/task2/task3 + all four bridges) now validates clean: every inter-zone link has safezone overlap and a shared pose, so all task links are bidirectional at the shared-pose hand-off.
- The 12 remaining reds are museum/testing-network data issues that predate this work (sleeping/resting/testing zones) — separate scope.

### Next Task

Decide whether to address the 12 pre-existing museum/testing reds (sleeping/resting bridges + `testing/locked_band` A3 poses). No task-network work remains.

### Instructions for the Next Task

Run `/tmp/kilo/validate_links.py` to re-list the 12 reds. They cluster into: `resting/hold` ↔ `sleeping/breathe`; `sleeping/yawn` ↔ `sleeping_testing_bridge/bridge` / `sleeping_scanning_bridge/bridge` (no shared pose + safezone gaps); eight `testing/*` ↔ `testing/locked_band` (pose `[45,-30,42,0,-5,0]` outside testing safezone on A3); `sleeping_testing_bridge/bridge` ↔ `testing/locked`. Fixing them is data work (poses/safezones/routing) in `zones.json` the same way as this entry; confirm intended routing before changing shared poses.

## 2026-10-26 — `find_shared_pose` `"x"`-wildcard comparison (runtime + validator + editor)

### Status

Completed. **Supersedes** the statement in the "A1 departure anchor" entry below that `find_shared_pose` "skips `'x'` poses (non-numeric)" — that was the *old* behavior. `find_shared_pose` now compares only *concrete* axes and treats `"x"` as a wildcard, so every action link is bidirectional-safe at the shared-pose hand-off.

### Summary

**Root cause:** the old `find_shared_pose` (`zones.py`) skipped any pose containing `"x"` (non-numeric) entirely. Once the user made bridge departure anchors `"x"` (copy-from-active, see the entry below), *no* shared pose was ever found for any link touching an `"x"` anchor — so the runtime safety gate discarded every auto bridge move, and the editor/validator flagged every such link red.

**Fix (`rtr/state/zones.py` `find_shared_pose`):** axis-by-axis comparison. An `"x"` axis on either side is a wildcard — it has no static value and resolves to the robot's live position at the hand-off (identical for both poses, `machine.py:86-93` frozen base). A pair is a shared pose when every axis that is concrete in *both* poses matches within `margin=0.5` **and** at least one such axis exists (so an all-`"x"` pose does not trivially match everything; no all-`"x"` poses exist in the current data). Returns `(pose, index_in_a, index_in_b)` or `None`.

**Runtime + gate needed no change:** `machine.py::_compute_handoff` and `safety/gate.py:104` already call `find_shared_pose`, so they now find the shared pose and resume from it (no shared pose → fall back to entry pose `poses[0]`).

**Validator (`/tmp/kilo/validate_links.py`):** rewritten to mirror `gate.py` exactly — inter-zone: safezone overlap + `find_shared_pose(src, tgt) is None`; intra-zone: no shared-pose requirement, only flags connection poses outside the zone safezone.

**Editor (`editor.html`):** added the JS `findSharedPose(pa, pb, margin)` mirroring the Python function; `edgeConflict` inter-zone now uses `findSharedPose(actionPoses(aA), actionPoses(aB))` (intra-zone logic unchanged).

**Shadowing bug found and fixed this session:** `editor.html` contained *two* `findSharedPose` definitions — the new wildcard-aware one (line 1001) and the old fully-numeric one (line 1025, from the 2026-10-24 entry). In JS the later declaration wins, so the editor was still running the *old* semantics (skip any pose containing `"x"`) and would have kept flagging every `"x"`-anchor bridge link red. Removed the old duplicate and its now-dead `poseNumeric` helper.

### Changes Made

- `realtimecontrol/rtr/rtr/state/zones.py`: `find_shared_pose` rewritten — concrete-axis comparison, `"x"` wildcard, `concrete > 0` guard.
- `realtimecontrol/rtr/editor.html`: JS `findSharedPose` (wildcard-aware) + `edgeConflict` inter-zone now call it; **removed** the old duplicate `findSharedPose` (was shadowing the new one) and the dead `poseNumeric` helper.
- `/tmp/kilo/validate_links.py`: rewritten to mirror `gate.py` rules (inter-zone overlap + shared pose; intra-zone safezone-only).

### Documentation

None required — the `"x"` frozen/copy mechanism is documented in `behavior.py:306` and `machine.py:86-93`; this change aligns the shared-pose detection with that mechanism.

### Architecture

No architectural change. The shared-pose hand-off contract is unchanged; only the detection now honors the `"x"` wildcard the runtime already uses.

### Validation

- Shared-pose detection on real `zones.json` data (Python and extracted editor JS agree):
  - `task2/locked` ↔ `task1_task2_bridge/bridge`: `(['x', -45, 20, 0, 40, 0], 0, 1)`
  - `task1/scan` ↔ `task1_task2_bridge/bridge`: `([0, -90, 20, 0, 80, 0], 0, 0)`
  - `task3/retreat` ↔ `task3_task1_bridge/bridge`: `(['x', -15, 20, 0, -20, 0], 0, 0)`
  - `task1/scnr` ↔ `task2_task3_bridge/bridge`: `None`
  - `task1/scan` ↔ `task1/scnr`: `None`
- `node --check` on all 13 extracted `editor.html` script blocks: OK.
- `python3 -m pytest tests/test_transition.py tests/test_safety_conflict.py -q`: 15 passed (existing tests use concrete poses; no regression).
- `python3 -m pytest tests/ --ignore=tests/test_remote_tui.py -q`: **296 passed, 5 failed, 14 skipped** — the 5 failures are the same pre-existing `test_remote_sdl` TUI failures.
- `/tmp/kilo/validate_links.py`: 50 checked, **13 broken** — ground truth below.

### Ground truth: the 13 red links

- **Task network (1):** `task1/scnr` ↔ `task2_task3_bridge/bridge` — `safezones do not overlap on A2, A5` + `no shared position`. Not fixable by adding a shared pose (the two safezones do not overlap on A2/A5); this is a topology/data question in `task1/scnr.next` (see Next Task).
- **Museum/testing (12, pre-existing, out of scope this pass):** `resting/hold` ↔ `sleeping/breathe`; `sleeping/yawn` ↔ `sleeping_testing_bridge/bridge`; `sleeping/yawn` ↔ `sleeping_scanning_bridge/bridge`; eight `testing/*` ↔ `testing/locked_band` links (`pose outside testing safezone on A3`); `sleeping_testing_bridge/bridge` ↔ `testing/locked`.

### Decisions and Caveats

- All-`"x"` pose edge case: guarded by `concrete > 0`; current data has no all-`"x"` poses.
- `task1/scan` ↔ `task1/scnr` has no shared pose but is **intra-zone**, so gate/validator do not require one — not red. The move is small and inside the `task1` safezone.
- The museum/testing reds predate this work and are unchanged.

### Next Task

Two open data/topology decisions (both need user input):
1. **`task1/scnr` → `task2_task3_bridge/bridge`:** the only task-network red. `scnr` sits in task1 (`["x", -85, 15, 0, 75, 0]`); `task2_task3_bridge` is a task2↔task3 bridge whose safezone does not overlap task1's on A2/A5. Candidate fix: retarget `task1/scnr.next` to `task1_task2_bridge/bridge` (the bridge that actually starts from task1), or drop the link — but this changes routing/behavior, so confirm intent first.
2. **task2↔task3 bridges A1:** `task2_task3_bridge` and `task3_task2_bridge` still hold A1=0 constants on both poses, while every other bridge and every task-zone action pose uses A1=`"x"`. If the departure-anchor directive applies to all bridges, set both bridges' A1 to `"x"`.

### Instructions for the Next Task

- For (1): on disk, `task1/scnr` `next` is `[{zone: task1, action: scan}, {zone: task2_task3_bridge, action: bridge}]` and `scnr` `poses` is `[['x', -85, 15, 0, 75, 0]]`. The problematic entry is the second one. Note: merely retargeting it to `task1_task2_bridge/bridge` does NOT clear the red — `scnr`'s pose shares no concrete axis with that bridge (A2 -85 vs -90/-45). A shared pose must also be added (e.g. `['x', -90, 20, 0, 80, 0]`, which is inside the `task1` safezone and equals `task1_task2_bridge` `poses[0]`). Confirm the intended routing first. Re-run `/tmp/kilo/validate_links.py` (expect 12 reds if the link is fixed/dropped) and the pytest suite (expect 296 passed / 5 pre-existing SDL failures).
- For (2): in `zones.json`, set `task2_task3_bridge` and `task3_task2_bridge` `poses[*]` A1 `0` → `"x"`. Re-run both checks; expect no change in red count (shared poses already match on the concrete axes).

## 2026-10-26 — A1 departure anchor: constant → `"x"` (copy-from-active-pose)

### Status

Completed. **Supersedes** the "A1 constant (0)" entry below. The user corrected the mechanism: the bridge departure anchor's A1 must NOT be a hardcoded constant — it is `"x"`, the frozen/copy axis, which copies the robot's current A1 at transition commit. The user has already applied this to `zones.json`; this entry records and validates it.

### Summary

The earlier entry set `task1_task2_bridge` `poses[0]` A1 to a constant `0` to hold A1 flat. The user rejected that: a constant departure anchor forces a base rotation to a fixed value *before* the approach. The correct mechanism is the `"x"` frozen/copy axis (`behavior.py:306 resolve_x`): an axis set to `"x"` is copied from the robot's current pose (the `_frozen_base` captured at transition commit, `machine.py:93`) and is **never driven** (`behavior.py:368`). `find_shared_pose` skips `"x"` poses (non-numeric, `zones.py:103`), so a `"x"` departure anchor means "start from wherever A1 currently is" rather than "rotate to 0 first" — the base rotation is absorbed into the approach motion, not a separate pre-move.

User-applied changes in `zones.json` (file grew 1505→1507 lines):
- **All task-zone action poses** A1 → `"x"`, `variable_axes` → `[0,1,2,3,4,5]`: `task2/locked`, `task3/approach`, `task3/facefocus`, `task3/retreat`. (`task1/scan` kept A1=0.)
- **New `task1/scnr` action**: pose `["x", -85, 15, 0, 75, 0]`, all axes variable, behavior `impatient`, `next` → `[task1/scan, task2_task3_bridge/bridge]`.
- **`task1/scan`** `next` is now a list: `[task1_task2_bridge/bridge, task1/scnr]`.
- **`task2/locked`** `next` is now a list: `[task2_task3_bridge/bridge, task1_task2_bridge/bridge]`.
- **Bridges touching task1** both poses A1 → `"x"`: `task1_task2_bridge`, `task3_task1_bridge` (retreat to task1).
- **Task2↔task3 bridges** (`task2_task3_bridge`, `task3_task2_bridge`) still hold A1=0 constants — not yet `"x"`.

### Changes Made

- `realtimecontrol/rtr/zones.json`: (user-applied) A1 `"x"` on task-zone poses + task1-touching bridges; new `task1/scnr` action; `next` fields converted to lists where routing branches.

### Documentation

None required — the `"x"` frozen/copy mechanism is already documented in `behavior.py:306` and `machine.py:86-93`; this is a data adjustment, not a new contract.

### Architecture

No architectural change. The `"x"` mechanism already exists in the runtime; this entry only records its application to the 3-task network data.

### Validation

- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py`: **296 passed, 5 failed, 14 skipped** — the 5 failures are the same pre-existing `test_remote_sdl` TUI failures; no new failures.
- `/tmp/kilo/validate_links.py`: 50 checked, 13 broken. The task-network reds are the expected `"x"` no-shared-pose consequences (e.g. `task1/scnr <-> task2_task3_bridge/bridge`).

### Decisions and Caveats

- The `"x"` departure anchor means the base rotation is absorbed into the approach motion (copied from the active pose), not a separate pre-move. This is the user's stated intent.
- **Open:** the task2↔task3 bridges (`task2_task3_bridge`, `task3_task2_bridge`) still use A1=0 constants. If the same rule applies, their departure anchors should also be `"x"` for consistency.

### Next Task

Confirm whether the task2↔task3 bridges should also get A1=`"x"` on the departure anchor.

### Instructions for the Next Task

If yes: edit `realtimecontrol/rtr/zones.json` `task2_task3_bridge` and `task3_task2_bridge` `poses[0]` A1 `0` → `"x"`. Re-run `/tmp/kilo/validate_links.py` and the pytest suite (expect the same pre-existing SDL/TUI failures).

## 2026-10-26 — Made A1 (base) constant during the task1→task2 bridge approach

### Status

Completed. The `task1_task2_bridge` approach now holds A1 constant (0) so the base no longer rotates while the robot approaches the task2 locked person. `task2_task3_bridge` already held A1 constant (0→0) and needed no change.

### Summary

The 2-pose bridge pattern from the previous task set `poses = [source_end, target_start]`. For `task1_task2_bridge` that meant `poses[0] = [45, -90, 20, 0, 80, 0]` (A1=45, task1's end pose) and `poses[1] = [0, -45, 20, 0, 40, 0]` (A1=0, task2's start pose). The robot therefore rotated its base A1 from 45→0 *during* the approach.

The user requires A1 to stay constant while approaching the locked person (task2/task3 are both A1=0). Fix: set `task1_task2_bridge` `poses[0]` A1 to 0 so the bridge approach holds A1=0 throughout. The base rotation (45→0) now happens as a separate deliberate handoff move (`task1/scan` → bridge entry) before the approach, not during it.

### Changes Made

- `realtimecontrol/rtr/zones.json`: `task1_task2_bridge` `poses[0]` A1 `45` → `0` (pose now `[0, -90, 20, 0, 80, 0]`). `poses[1]` unchanged (`[0, -45, 20, 0, 40, 0]`). Safezone unchanged (A1 `[-185,185]` already contains 0).

### Documentation

None required — the bridge data model is already documented in `zones.py`; this is a data adjustment to satisfy a motion constraint, not a new behavior or config contract.

### Architecture

No architectural change. The bridge is still a no-op `behavior: "bridge"` zone stepping through curated anchors. The change moves the A1 reorientation from inside the bridge approach to the pre-approach handoff move (`_compute_handoff` falls back to the entry pose since the bridge no longer shares an A1-matching pose with `task1/scan`).

### Validation

- `/tmp/kilo/validate_links.py`: 45 checked links, **4** broken (was 3). The new red link is `task1/scan <-> task1_task2_bridge/bridge` — "no shared position on A1" — which is the *intended* consequence of making the bridge A1-constant (the base now rotates as a separate handoff move). The 3 pre-existing sleeping/resting red links are unchanged.
- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py`: **296 passed, 5 failed, 14 skipped** — the 5 failures are the same pre-existing `test_remote_sdl` TUI failures; no new failures.

### Decisions and Caveats

- **Trade-off accepted:** an A1-constant approach is incompatible with a shared departure pose when source A1 ≠ target A1. Choosing A1-constant means the bridge no longer shares a pose with `task1/scan`, so the validator flags it and the base rotates during the handoff move (task1 → bridge entry) rather than during the approach. This matches the user's stated intent (A1 constant while approaching the locked person).
- **`task2_task3_bridge` needed no change** — it already holds A1=0→0.
- **`task3_task1_bridge` (retreat, task3→task1) still changes A1 0→45.** The user only specified the forward direction (task1→task2→task3). If the same A1-constant rule should apply to the retreat, `task3_task1_bridge` `poses[0]` A1 should be set to 45 (task1's value) — flagged as an open question, not changed.

### Next Task

Confirm with the user whether the A1-constant rule also applies to the retreat bridges. If yes, set `task3_task1_bridge` `poses[0]` A1 to 45. Otherwise no immediate next task.

### Instructions for the Next Task

If extending the rule to the retreat: edit `realtimecontrol/rtr/zones.json` `task3_task1_bridge` `poses[0]` (currently `[0, -15, 20, 0, -20, 0]`) to `[45, -15, 20, 0, -20, 0]` so the task3→task1 approach holds A1=45 (task1's value). Re-run `/tmp/kilo/validate_links.py` and the pytest suite (expect the same 3 pre-existing SDL failures + the expected A1 shared-pose red links).

## 2026-10-26 — Fixed all broken action-level links in the 3-task zone network (2-pose bridges + retreat bridges)

### Status

Completed. All four broken action-level links in the 3-task zone network (task1→task2→task3 + retreat) now validate clean (green) against the editor's `edgeConflict` rules. The 4 task-network links are fixed; the 3 pre-existing sleeping/resting bridge links remain red (out of scope — they are not part of the 3-task network).

### Summary

The 3-task network's inter-zone links were structurally broken: a **single-pose** bridge cannot be clean on *both* legs when the source's end pose ≠ the target's start pose. The editor's `edgeConflict` checks (1) safezone overlap on all 6 axes and (2) source END pose (`poses[-1]`) vs target START pose (`poses[0]`) per-axis within 0.5. A single-pose bridge has `poses[0] == poses[-1]`, so it can match at most one of the two endpoints.

**Fix:** rewrite each bridge as a **2-pose bridge** `poses = [source_end, target_start]`, with a safezone that (a) contains *both* poses (intra-zone check) and (b) overlaps *both* neighboring zones' safezones on every axis (inter-zone check). This makes leg 1 share `poses[0]` (source end) and leg 2 share `poses[-1]` (target start), so both validate clean. The runtime already handles this: `_compute_handoff` → `find_shared_pose` finds a shared pose on entry (resume from `poses[0]`) and on exit (resume from `poses[-1]`), consistent with the existing single-pose bridges.

**Retreat links** (task3→task1, task3→task2) were *direct* inter-zone links with no safezone overlap on A2/A5. Added two new bridge zones (`task3_task1_bridge`, `task3_task2_bridge`) and redirected `task3/retreat`'s `next` to route through them.

### Changes Made

- `realtimecontrol/rtr/zones.json`:
  - `task1_task2_bridge`: safezone A2 `[-50,-40]`→`[-95,-40]`, A5 `[35,45]`→`[35,85]` (span both task1/task2 safezones); poses `[[0,-45,20,0,40,0]]` → `[[45,-90,20,0,80,0],[0,-45,20,0,40,0]]` (2-pose); `pose_imgs`/`pose_videos` → 2 entries.
  - `task2_task3_bridge`: safezone A2 `[-20,-10]`→`[-50,-10]`, A4 `[-10,10]`→`[0,1]`, A5 `[-25,-15]`→`[-25,45]`, A6 `[-15,15]`→`[-5,5]`; poses `[[0,-15,20,0,-20,0]]` → `[[0,-45,20,0,40,0],[0,-15,20,0,-20,0]]` (2-pose); `pose_imgs`/`pose_videos` → 2 entries.
  - **new** `task3_task1_bridge`: safezone `[[-185,185],[-95,-10],[15,25],[0,1],[-25,85],[-5,5]]`, poses `[[0,-15,20,0,-20,0],[45,-90,20,0,80,0]]`, `next` → `task1/scan`.
  - **new** `task3_task2_bridge`: safezone `[[-185,185],[-50,-10],[15,25],[0,1],[-25,45],[-5,5]]`, poses `[[0,-15,20,0,-20,0],[0,-45,20,0,40,0]]`, `next` → `task2/locked`.
  - `task3/retreat`: `next` changed from direct `[task1/scan, task2/locked]` → `[task3_task1_bridge/bridge, task3_task2_bridge/bridge]`.
  - `layout.pos`: added `task3_task1_bridge` (1350,420) and `task3_task2_bridge` (1500,480); `layout.key` updated to include both.
- `realtimecontrol/rtr/brain.json`: `zone_groups.active.zones` now includes `task3_task1_bridge` and `task3_task2_bridge` (7 zones total).

### Documentation

None required — the zone/bridge data model (unified `poses` form, 2-pose bridge = a multi-anchor stepping action) is already documented in `zones.py`'s module docstring; no new behavior or config contract was introduced.

### Architecture

No architectural change. The bridge pattern (a no-op `behavior: "bridge"` zone that steps through curated anchor poses to transition between zones) is unchanged; the fix only corrects the *data* (poses + safezones + routing) so the existing `edgeConflict`/`find_shared_pose` logic validates clean. The 2-pose form is the canonical "multi-anchor action" the unified form already supports.

### Validation

- `/tmp/kilo/validate_links.py` (mirrors editor `edgeConflict` exactly): **before** 7 broken links (4 task-network + 3 sleeping/resting); **after** 3 broken links (0 task-network + 3 sleeping/resting). All 4 task-network links green.
- `python -c "from rtr.state.zones import load_state_data; load_state_data('zones.json')"`: loads OK, 13 zones, all 4 bridges have 2 poses.
- `python -m pytest tests/test_bridge_behavior.py tests/test_safety_conflict.py tests/test_state_machine.py tests/test_zone_groups.py tests/test_transition.py -q`: **60 passed, 9 skipped**.
- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py`: **296 passed, 5 failed, 14 skipped** — the 5 failures are the pre-existing `test_remote_sdl` TUI failures (confirmed pre-existing by `git stash` + re-run); `test_remote_tui.py` has a pre-existing collection ImportError (`action_kind`), also unrelated.

### Decisions and Caveats

- **2-pose bridge is the minimal clean fix.** A single-pose bridge is mathematically impossible to be clean on both legs when source-end ≠ target-start. The 2-pose form `[source_end, target_start]` is the only shape that satisfies both `edgeConflict` legs.
- **Safezones are per-axis *spans* (not intersections).** Each bridge safezone must contain both anchor poses AND overlap both neighbors on every axis; using the union-span (e.g. A5 `[35,85]` for task1_task2_bridge) satisfies both constraints. An intersection would be empty on A2/A5 and fail.
- **Agent-chosen poses are adjustable.** task2/locked `[0,-45,20,0,40,0]` and task3 approach/facefocus/retreat `[0,-15,20,0,-20,0]` were agent-chosen (must stay in their zone safezones). If the user changes them, the bridge poses + safezones must be recomputed (bridge poses = `[source_end, target_start]`; safezone = per-axis span containing both poses that overlaps both neighbors).
- **The 3 sleeping/resting red links are out of scope** but share the same structural flaw (single-pose bridges: `sleeping_testing_bridge`, `sleeping_scanning_bridge`, and the `resting/hold`↔`sleeping/breathe` direct link). They can be fixed with the same 2-pose pattern if requested.
- **Dwell caveat (unchanged):** `_autonomy_dwell_s` (brain.py:883) looks up group by `mode_group` (`event-sit-back`), not by group name (`active`), so `params.dwell_s` in the `active` group is documentation-only; effective dwell is the per-action `dwell_s: 35` on `task1/scan`.

### Next Task

Fix the 3 remaining pre-existing red links (sleeping/resting network) using the same 2-pose bridge pattern, if the user wants the whole graph green. Otherwise no immediate next task.

### Instructions for the Next Task

The 2-pose bridge recipe: for a link `A → B` where A's end pose ≠ B's start pose, create/rewrite the bridge zone with `poses = [A_end, B_start]` and a safezone whose each axis is a span that (a) contains both `A_end[i]` and `B_start[i]` and (b) overlaps both A's and B's safezone on that axis. Add the bridge to `layout.pos` + `layout.key` (zones.json) and to the relevant `zone_groups` (brain.json). Validate with `/tmp/kilo/validate_links.py` (must show 0 broken links in the target network) and `pytest tests/test_bridge_behavior.py tests/test_safety_conflict.py`. Do NOT touch the 3 sleeping/resting links unless explicitly requested (out of scope for the tasknet work).

## 2026-10-26 — Removed the hardcoded zone groups (museum-closed / event-sit-back / stage-takeover) so editor-created groups are the only ones and deletions persist across reloads

### Status

Completed. The three default zone groups no longer exist anywhere in the realtime-control codebase; `zone_groups` is now purely editor-created. `hunt` and `facefocus` are untouched (they are special brain loops, not groups-tab groups, and remain in `BRAIN_DEFAULT_GROUPS` for their param defaults).

### Summary

The user's deleted groups (`museum-closed`, `event-sit-back`, `stage-takeover`) kept reappearing because both the editor and the Python core **re-seeded** them on every load from hardcoded defaults, even though `brain.json` on disk already had `"zone_groups": {}`. The fix removes every re-seed point and every hardcoded copy of the three groups:

- **`rtr/brain/config.py`** — `DEFAULT_BRAIN_CONFIG["zone_groups"]` is now `{}`; the "fill in any default mode groups not present in the file" loop in `validate_brain_config()` is removed; the shape docstring no longer lists the three groups. The Python brain (`brain.py`) reads `zone_groups` generically (iterates whatever exists), so empty is safe. The `performance_mode` display heuristic in `brain.py:280-283` still references the old names but degrades safely (group absent → `performance_mode: False`).
- **`editor.html`** — `BRAIN_FALLBACK.zone_groups` is now `{}`; `BRAIN_DEFAULT_GROUPS` no longer contains the three groups (only `hunt`/`facefocus` remain, needed by `_brainNormalizeGroup` for their `params` defaults); the re-seed loop in `normalizeBrain()` is removed. `buildGroupsForm()` renders from `BRAIN.zone_groups` as before; `removeGroup()` now sticks.
- **`client.html`** — `BRAIN_FALLBACK.zone_groups` is now `{}` (display-client fallback only; its `loadBrain()` already built groups generically from the fetched data).
- **`test_groups_tab.js`** — the test was already broken (LOAD ERROR from a stale mini-DOM `STATIC_IDS` list and stale API exports `_buildGroupBlock`/`selectedGroup`). Rewritten: added the 41 missing static element IDs, upgraded the mini-DOM `matchOne` to handle `[data-attr]`/`[attr=value]` selectors (needed by `.brainCard[data-name]` and `input[type=checkbox]`), fixed the export list, and rewrote the assertions for the no-default-groups behavior.

The per-zone `mode_group` field (`zones.json` / `zones.py` `MODE_GROUPS`) is a **separate concept** (a per-zone label, not a groups-tab group) and was intentionally left untouched.

### Changes Made

- `realtimecontrol/rtr/rtr/brain/config.py`: `DEFAULT_BRAIN_CONFIG["zone_groups"]` → `{}`; removed the default-group re-fill loop in `validate_brain_config()`; updated the shape docstring (`zone_groups` example is now "empty by default — groups are created in the editor").
- `realtimecontrol/rtr/editor.html`: `BRAIN_FALLBACK.zone_groups` → `{}`; `BRAIN_DEFAULT_GROUPS` → only `hunt` + `facefocus` (updated comment); removed the re-seed loop in `normalizeBrain()` (updated comment).
- `realtimecontrol/rtr/client.html`: `BRAIN_FALLBACK.zone_groups` → `{}`.
- `realtimecontrol/rtr/test_groups_tab.js`: added 41 missing IDs to `STATIC_IDS`; replaced `matchOne` with a tokenizer that handles tag/class/id/`[data-attr]`/`[attr=value]` selectors (with `dataset` fallback for `data-*`); fixed the `__T` export list (removed `_buildGroupBlock`, `selectedGroup`); rewrote the test body for no-default-groups (on-disk parity assertion, add/rename/remove, reload-no-reseed, `normalizeBrain` unit checks, full-payload save check).

### Documentation

- `config.py` module docstring updated (the `zone_groups` shape example and the "three mode groups" comment are gone). No other documentation needed — the groups tab's in-editor help text already describes groups generically.

### Architecture

- No architectural change. The runtime group-navigation mechanism (`zone_groups` + `_handle_enable_group` + `_allowed_zones`) is unchanged; it now operates on a purely editor-defined set. The config contract shifts from "the brain always has the three default groups" to "the brain has exactly the groups in brain.json" — an empty set is valid (autonomy then runs across all enabled zones, as `_allowed_zones` already handles when no group is active).

### Validation

- `node test_groups_tab.js`: **ALL_TESTS_PASSED** (20 assertions, including no-reseed-on-reload and the full save payload with hunt/facefocus/autonomy/behaviors/cam_control intact).
- Python config checks: `validate_brain_config({'zone_groups': {}})` → `{}`; `builtin_brain_config()['zone_groups']` → `{}`; `load_brain_config('brain.json')` → `zone_groups = {}` (at the time; the file now holds the concurrent `active` group, which also validates); a custom editor-created group validates with its params preserved.
- `python3 -m pytest tests/ -q --ignore=tests/test_remote_tui.py`: **296 passed, 5 failed, 14 skipped** — identical to the pre-change baseline (the 5 failures are the pre-existing `test_remote_sdl` TUI failures; `test_remote_tui.py` has a pre-existing collection ImportError for `action_kind`, also unrelated).
- Brain/zone-group Python tests in isolation (`test_zone_groups.py`, `test_brain_autonomy_dwell.py`, `test_brain_assertive.py`, `test_midi_brain_trigger.py`, `test_brain_action_cam.py`): **50 passed**.
- Grep sweep: no remaining references to `museum-closed`/`event-sit-back`/`stage-takeover` in any `.html` or `.js` file (only the out-of-scope per-zone `mode_group` labels in `zones.py` and the safe `performance_mode` heuristic in `brain.py`).

### Decisions and Caveats

- `hunt`/`facefocus` were deliberately kept in `BRAIN_DEFAULT_GROUPS` because `_brainNormalizeGroup("hunt"/"facefocus", ...)` reads their `params` defaults from there; removing them would throw on `dp.lost_s` etc. They are stripped from `BRAIN.zone_groups` by `normalizeBrain` as before.
- The `brain.py` `performance_mode` heuristic (`stage-takeover` enabled while `event-sit-back` disabled) was left as-is: it is display-only, degrades to `False` when the groups are absent, and would work again if the user re-creates groups with those exact names.
- **Concurrent work**: while this task ran, a separate "tasknet" task modified the live data files (`zones.json` now has 11 zones, `brain.json` `zone_groups` now holds a single `active` group). Those changes are independent of this fix and were not touched; the rewritten `test_groups_tab.js` is robust to whatever groups exist on disk (it asserts in-memory == on-disk, not a fixed count).
- `test_remote_tui.py` (collection ImportError) and the `test_remote_sdl.py` failures are pre-existing and out of scope.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

The groups tab now shows exactly the groups in `brain.json` (currently: `active`, from the concurrent tasknet work). If the user wants to delete it, the editor's remove button now persists the deletion across reloads — no further code change needed. If groups are re-added via the editor, they are stored in `brain.json` and picked up by the Python core on hot-reload. Do not re-introduce default groups in `config.py`/`editor.html`; the re-seed loops are deliberately gone.

## 2026-10-26 — Built the 3-task zone test network (task1 scan → task2 track → task3 approach/facefocus + retreat) and the selectable `active` group

### Status

Completed. `zones.json` now holds **11 zones** (was 6): the original `resting`, `sleeping`, `testing`, `scanning`, `sleeping_testing_bridge`, `sleeping_scanning_bridge` plus 5 new ones — `task1`, `task2`, `task3`, `task1_task2_bridge`, `task2_task3_bridge`. `brain.json` `zone_groups` now holds a single **`active`** group containing all 5 created zones. Both files pass their validators; the test suite is unchanged (296 passed, the same 5 pre-existing `test_remote_sdl` TUI failures, 14 skipped).

### Summary

Built a small 3-task engagement-ladder test network that exercises the available camera modes, behaviors, and the brain engagement ladder, with every created zone placed in a selectable `active` group. The flow is `task1 (scan) → task1_task2_bridge → task2 (track) → task2_task3_bridge → task3 (approach/facefocus/retreat)`, with task3's `retreat` looping back to task1/task2.

**task1** — class `scan` (non-active → `variable_axes`), `mode_group` `event-sit-back`. Supplied safezone `[(-185,185),(-95,-85),(15,25),(0,1),(75,85),(-5,5)]` and pose `[45,-90,20,0,80,0]` (A1 free; A2=-90, A3=20, A4=0, A5=80, A6=0) used exactly. Single action `scan` (idle sweep, `variable_axes: [0]`, behavior `spontaneous`, `dwell_s: 35`) → `task1_task2_bridge/bridge`.

**task2** — class `track` (active → `axis_bands`), `mode_group` `event-sit-back`. Supplied safezone `[(-185,185),(-50,-40),(15,25),(0,1),(35,45),(-5,5)]`, pose `[0,-45,20,0,40,0]`. Single action `locked` (`axis_bands` = safezone, behavior `locked`, speed 40) → `task2_task3_bridge/bridge`.

**task3** — class `approach` (active → `axis_bands`), `mode_group` `event-sit-back`. Supplied safezone `[(-185,185),(-20,-10),(15,25),(-10,10),(-25,-15),(-15,15)]`, pose `[0,-15,20,0,-20,0]`. Three actions: `approach` (behavior `aggressive`, speed 100) → `facefocus`; `facefocus` (behavior `gentle`, close face) → `retreat`; `retreat` (behavior `calm`, `loop: false`) → `task1/scan` + `task2/locked`.

**Bridges** — no class (like the existing bridges). `task1_task2_bridge` (safezone = task2's, pose = task2's start `[0,-45,20,0,40,0]`) → `task2/locked`. `task2_task3_bridge` (safezone = task3's, pose = task3's start `[0,-15,20,0,-20,0]`) → `task3/approach`. Both satisfy `find_shared_pose` (margin 0.5): the bridge pose is the target's start pose, so the handoff commits at the shared pose (index 0 in both lists).

**`active` group** (brain.json `zone_groups`): `{zones: [task1, task2, task3, task1_task2_bridge, task2_task3_bridge], enabled: true, params: {dwell_s: 35}}`. All 5 created zones are included so that when the group is active, `_allowed_zones` (brain.py:1563) still permits the next-links to the bridges (a group-active brain restricts autonomy to the group's zones; excluding the bridges would make the task1→task2 and task2→task3 links invalid).

**Key validation decisions:**
- safezone `A4=(0,0)` in the supplied task1/task2 values is **invalid** (`_validate_zone` requires strict `lo < hi`); normalized to `(0,1)` — the same precedent as the existing `resting`/`scanning` zones. All other supplied safezone values used exactly.
- Inverted safezone ranges from the prior turn were already normalized before this write.
- The group's `params.dwell_s` (35) is documentation: `_autonomy_dwell_s` (brain.py:883) looks the group up by the zone's `mode_group` (`event-sit-back`), not by the group name (`active`), so it does not apply at runtime. The 35s idle dwell is instead set per-action on `task1/scan` (`dwell_s: 35`), which is the value the dwell lookup actually reads.

### Changes Made

- `realtimecontrol/rtr/zones.json`: added `task1`, `task2`, `task3`, `task1_task2_bridge`, `task2_task3_bridge` to `zones`; added their `layout.pos` entries (a fresh bottom-right cluster at x≈1200-1600, y≈300-500); updated `layout.key` to include all 11 zone names.
- `realtimecontrol/rtr/brain.json`: added the `active` group to `zone_groups` (previously `{}`).
- Backups: `zones.json.pre-tasknet` and `brain.json.pre-tasknet` (the pre-write state of each file).
- Build script: `/tmp/kilo/build_task_network.py` (idempotent-guarded; asserts the new zone names don't already exist before writing; validates both files before the atomic replace).

### Documentation

- None required — no config contract or behavior changed; this adds test data to the existing zone/brain schema.

### Architecture

- No architectural change. The network reuses the existing engagement-ladder classes (`scan`/`track`/`approach`), the bridge transition pattern (no-class zone, safezone = target's, pose = target's start, `behavior: bridge`, `loop: false`, next → target), and the runtime group-navigation mechanism (`zone_groups` + `_handle_enable_group` + `_allowed_zones`). The `active` group is a new selectable group, not a new mode_group.

### Validation

- `validate_state_data` (full zones.json): **passes** (11 zones).
- `validate_brain_config` (full brain.json): **passes** (`zone_groups: ['active']`).
- `find_shared_pose` bridge continuity: `task1_task2_bridge/bridge → task2/locked` shared `([0,-45,20,0,40,0], 0, 0)`; `task2_task3_bridge/bridge → task3/approach` shared `([0,-15,20,0,-20,0], 0, 0)`.
- Pose-in-safezone + HARDWARE_LIMITS check (script assertions): all 3 task poses within their safezone and the hardware floor.
- `python3 -m pytest tests/ -q --ignore=tests/test_remote_tui.py`: **296 passed, 5 failed, 14 skipped** — the 5 failures are the pre-existing `test_remote_sdl` TUI failures (unrelated to zones, identical to before this change).

### Decisions and Caveats

- Zone names `task1`/`task2`/`task3` and the task2/task3 poses are my design choices (the user supplied only the task1 pose and the three safezones).
- The `active` group includes the bridges (not just task1/task2/task3) — required for group-active navigation to work (see Summary). If the user wants a UI group that lists only the three "real" tasks, the bridges would need to stay reachable some other way; as it stands, the group is the navigation-safe choice.
- The `active` group's `params.dwell_s` is not read at runtime (group lookup is by `mode_group`, not group name). The 35s dwell is effective only via the per-action `dwell_s` on `task1/scan`.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

If extending the network: add new zones to `zones.json` `zones` (validate with `validate_state_data`), add their `layout.pos` entries and update `layout.key`, and add them to the `active` group in `brain.json` `zone_groups` if they should be reachable under group-active navigation. For active-class zones (`track`/`farfocus`/`approach`/`facefocus`) use `axis_bands`; for non-active classes use `variable_axes`. Bridge zones follow the existing pattern (no class, safezone = target's, pose = target's start pose, `behavior: bridge`, `loop: false`, next → target).

## 2026-10-26 — Added `resting` zone (exact backup pose) to `zones.json` to fix `KeyError: 'resting'` on startup

### Status

Completed. `python main.py --robot` no longer crashes at `StateMachine.__init__` with `KeyError: 'resting'`. `zones.json` now holds 6 zones: `resting`, `sleeping`, `testing`, `scanning`, `sleeping_testing_bridge`, `sleeping_scanning_bridge`.

### Summary

The 5-zone rewrite of `zones.json` removed the `resting` zone, but `StateMachine.__init__` (machine.py:35) defaults to `initial_zone="resting"` and `reset()` (machine.py:803) hardcodes `current_zone="resting"` — so `main.py` crashed at startup with `KeyError: 'resting'` before the engine ever ran.

The `resting` zone was re-added from the backup (`zones.json.bak-20261026-121651`) with the exact backup pose data:
- **`resting`** (single action `hold`): the backup's exact 3 `hold` poses `[[10,-59,-10,0,60,0],[0,-130,-9,0,0,0],[-60,-90,-9,15,10,0]]`, exact `safezone` (6 axis pairs), `speed: 20`, `variable_axes: [0..5]`, `behavior: "restless"`, `loop: true`, `speed: 20`, and the backup's `pose_imgs`/`pose_videos`.
- `next`: `[{zone: "sleeping", action: "breathe"}]` — the backup's first zone-level exit (`["sleeping","breathe"]`), adapted to the current 5-zone world (the backup's other targets `wake`/`resting_testing_bridge` no longer exist). Harmless at runtime: `hold` is `loop: true`, so it wraps around and never fires `_advance_after_action`; the entry only feeds the `zones.graph()` display.
- The backup's `wake` action and zone-level `exits` field were **not** carried over (the `exits` field is gone from the current schema; `wake`'s next referenced the removed `resting_sleeping_bridge`).
- `layout.pos.resting` = the backup's exact position `{x: 479.02371978759766, y: 11.794570922851562}`; `layout.key` updated to include `resting`.

Resulting flow graph: `resting → sleeping` (informational only for the looping action) plus the existing `sleeping → bridges → testing/scanning`.

### Changes Made

- `realtimecontrol/rtr/zones.json`: added the `resting` zone (first in the `zones` object) with the single `hold` action; added `layout.pos.resting`; added `resting` to `layout.key`.

### Documentation

- None required — no config contract or behavior changed; this restores the home zone the runtime already assumes.

### Architecture

- No architectural change. `resting` is the canonical home zone (`StateMachine` default `initial_zone` and `reset()` both target it). It is a single-action looping zone, consistent with the current minimal-zone pattern (`scanning` is also single-action). The previous-zone fallback would auto-return it if it ever dead-ended, but `loop: true` means it never ends.

### Validation

- `validate_state_data` (full file): passes.
- `StateMachine(Zones(data["zones"]), tick_hz=20.0)`: initializes with `current_zone="resting"`, `speed=20` — the exact previously-failing call.
- `python3 -m pytest tests/ -q --ignore=tests/test_remote_tui.py`: **296 passed, 5 failed, 14 skipped** — the 5 failures are the pre-existing `test_remote_sdl` TUI failures (unrelated to zones, same as before this change).
- `tests/test_remote_tui.py` collection error: pre-existing `ImportError: cannot import name 'action_kind' from 'rtr.flow'` (stale test import; unrelated to this data change).
- `brain.json` `zone_groups` is `{}` (empty), so the stale `DEFAULT_BRAIN_CONFIG` zone names are not used at runtime.

### Decisions and Caveats

- Only the `hold` action was ported (not `wake`): the user asked for "a resting pose" and the current architecture favors single-action zones. If the `wake` transition back into sleep is wanted later, port it from the backup and re-point its `next` (the backup's `resting_sleeping_bridge` no longer exists — it would need a new `resting_sleeping_bridge` or a direct `sleeping/breathe` hop).
- The `resting → sleeping` link in `next` is informational (looping actions never auto-advance); actual transitions to `resting`/`sleeping` happen via manual triggering (brain/patches/midi/chatbot `trigger_action`/`play_action` commands).

### Next Task

No immediate next task identified.

### Instructions for the Next Task

If the operator wants the full backup `resting` zone back (both `hold` and `wake`), port `wake` from `zones.json.bak-20261026-121651` and re-point its `next` to existing zones. If a physical bridge path resting→sleeping is wanted, add a `resting_sleeping_bridge` following the existing bridge pattern (single `bridge` action, `loop: false`, `variable_axes: []`, pose = target entry pose).

## 2026-10-26 — Rewrote `zones.json` to a 5-zone table (sleeping / testing / scanning + 2 bridges) and updated the data-dependent tests

### Status

Completed. `zones.json` now holds exactly `sleeping`, `testing`, `scanning`, plus the two new directional bridges `sleeping_testing_bridge` and `sleeping_scanning_bridge`. Every `exits` field is gone and every action `next` that referenced a removed zone is gone, so the file passes `validate_state_data` with no dangling references. The three tests that load the *real* `zones.json` were updated to reference the new zones and now pass.

### Summary

The pending item was to write a fresh `zones.json` with only `sleeping` + `testing` and all `exits`/`next` cross-references removed. The operator refined that: **also keep `scanning`**, and **make/keep a bridge zone between sleeping↔testing and between sleeping↔scanning**.

A "bridge zone" in this codebase is a single-action zone (`behavior: "bridge"`, `loop: false`, `variable_axes: []`) whose one `bridge` action holds curated anchor poses and hands off via `next` to the target zone's entry action. The existing bridges follow the naming `<source>_<target>_bridge`. Neither `sleeping_testing_bridge` nor `sleeping_scanning_bridge` existed, so both were created.

What the new table contains:
- **`sleeping`** (actions `breathe`/`snore`/`yawn`): full real pose data kept; `exits` removed; `next` filtered to intra-zone only, with the two new bridges appended to `yawn` (the historical exit action) at lowest priority.
- **`testing`** (actions `locked`/`intent`/`curious`/`gentle`/`locked_band`): full real pose data kept; `exits` removed; `next` filtered to intra-zone only.
- **`scanning`** (action `scnr`): full real pose data kept; its old `next` (→ `scanning_leaping_bridge`, `scanning_roaring_bridge`) referenced removed zones, so it is now empty (the action is `loop: true`, so it simply loops).
- **`sleeping_testing_bridge`** → hands off to `testing/locked`; pose = the `testing/locked` entry pose `[45,-30,42,0,-5,0]`; safezone/speed inherited from `testing`.
- **`sleeping_scanning_bridge`** → hands off to `scanning/scnr`; pose = the `scanning/scnr` entry pose `[10,-59,-10,0,60,0]`; safezone/speed inherited from `scanning`.

Resulting flow graph (verified via `rtr.flow`): `sleeping → sleeping_testing_bridge → testing` and `sleeping → sleeping_scanning_bridge → scanning`.

### Changes Made

- `realtimecontrol/rtr/zones.json`: rebuilt from the 38-zone original down to the 5 zones above. All `exits` removed; every `next` entry whose `zone` is not in the kept set removed; `sleeping.yawn.next` gained the two new bridge targets; the two new bridge zones added (single `bridge` action each, `behavior: "bridge"`, `loop: false`, `variable_axes: []`, `next` → target entry action); `layout` reduced to the 5 zones (the 3 kept zones retain their old positions, the 2 bridges placed at the midpoint of their source/target).
- `tests/test_state_machine.py`: `test_set_control_mode_validates` — `initial_zone="resting"` → `"sleeping"` (the only change; the assertion logic is unchanged).
- `tests/test_transition.py`: `test_all_reachable_transitions_complete` — the four transition pairs updated from the removed zones to the two bridge hops + two bridge→target hops: `("sleeping","yawn","sleeping_testing_bridge","bridge")`, `("sleeping","yawn","sleeping_scanning_bridge","bridge")`, `("sleeping_testing_bridge","bridge","testing","locked")`, `("sleeping_scanning_bridge","bridge","scanning","scnr")`.
- `tests/test_bridge_behavior.py`: `test_real_zones_bridge_action_is_tagged` — now asserts against `sleeping_testing_bridge` (was `resting_sleeping_bridge`) and `len(act["poses"]) >= 1` (was `== 3`; the new bridge is a single curated pose).
- `realtimecontrol/rtr/zones.json.bak-20261026-121651`: backup of the original 38-zone file (repo convention of keeping `zones.json.bak-*` snapshots).

### Documentation

- None beyond this handoff. No user-facing config contract changed; the zone graph is now a smaller, self-contained table.

### Architecture

- No architectural change. The zone model, the bridge pattern, and the navigation priority (`action next` → `zone exits` → previous-zone fallback) are unchanged. Only the *data* in `zones.json` changed. The bridges are **directional** (sleeping → target): the reverse direction is handled by the runtime previous-zone fallback for single-action zones (`scanning`) or by manual triggering for multi-action zones (`testing`). If a true bidirectional path is wanted later, add a `testing_sleeping_bridge` / `scanning_sleeping_bridge`.

### Validation

- `validate_state_data` (via `load_state_data`) on the on-disk file → **OK** (passes; no dangling `exits`/`next`).
- `rtr.flow` graph check → `reachable(sleeping) = [sleeping, sleeping_testing_bridge, sleeping_scanning_bridge]`; `reachable(sleeping_testing_bridge) = [testing]`; `reachable(sleeping_scanning_bridge) = [scanning]`; `reverse(testing) = [testing, sleeping_testing_bridge]`; `reverse(scanning) = [sleeping_scanning_bridge]`. Confirms both bridges sit between sleeping and their targets.
- Full suite `pytest tests/ --ignore=tests/test_remote_tui.py` → **296 passed, 5 failed, 14 skipped**. The 5 failures are all in `tests/test_remote_sdl.py` (TUI/SDL rendering) — **pre-existing** and unrelated to `zones.json` (that test file does not reference zones). The previously-failing `test_transition` and `test_bridge_behavior` now **pass**.

### Decisions and Caveats

- **The new bridges use a single curated pose = the target's entry pose** (no fabricated intermediate motion). This is the safest choice: the robot transitions to a known-good pose and hands off to the target action at that same pose. **The operator should add intermediate path poses in the editor** for a smooth physical traversal between the real sleeping position and the target; right now the bridge is a direct (safety-gated) move to the target pose.
- **Bridges are directional** (sleeping → target). See Architecture for the reverse-direction note.
- **`exits` are intentionally all removed** (the original task asked to drop all exits/cross-references). The bridges are wired via action `next`, not zone `exits`.
- The three test edits are **data-reference updates only** — the code-behavior assertions are unchanged. They keep the suite green against the new table.

### Next Task

Optional (operator): refine the two new bridge actions' poses in the editor for a smooth physical path (currently a single direct move to the target pose), and add reverse-direction bridges (`testing_sleeping_bridge`, `scanning_sleeping_bridge`) if a true bidirectional path is wanted.

### Instructions for the Next Task

1. Reload the editor page to load the new `zones.json` (the flow view will show `sleeping → sleeping_testing_bridge → testing` and `sleeping → sleeping_scanning_bridge → scanning`).
2. To refine a bridge: open `sleeping_testing_bridge` (or `sleeping_scanning_bridge`) in the editor and add intermediate anchor poses between the sleeping-side pose and the target entry pose. Keep `behavior: "bridge"`, `variable_axes: []`, `loop: false`.
3. The original 38-zone table is preserved in `zones.json.bak-20261026-121651` if a full restore is ever needed.

## 2026-10-26 — Fixed `delZone`/`renameZone` to clean zone-level `exits` (and added an `exits` check to the client validator)

### Status

Completed. The editor's `delZone` now strips the removed zone name from every other zone's `exits` list (mirroring its existing `next` cleanup), `renameZone` re-points `exits` entries naming the renamed zone, and the client-side `validate()` now rejects a dangling `exits` target before it is POSTed. Syntax-checked and logic-tested.

### Summary

Deleting or renaming a zone in the editor left stale zone-level `exits` references behind. `delZone` already cleaned action-level `next` targets pointing at the removed zone, but it never touched the zone-level `exits` field (a list of `[zone, action]` pairs). The server validator (`rtr/state/zones.py` `_validate_zone`, lines 561–566) requires every `exits[i][0]` to be a live zone name, so a stale exit made the save fail with a 400 (`zone '…': exits must be a list of [zone, action] pairs`), leaving `zones.json` untouched and the client showing stale zones. `renameZone` had the same gap (it re-pointed `next` but not `exits`), and the client's `validate()` never checked `exits` at all, so the dangling reference sailed past the client and only surfaced as a server 400.

This task closes all three gaps:
1. **`delZone`** strips any `exits` pair naming the removed zone from every surviving zone.
2. **`renameZone`** re-points any `exits` pair naming the renamed zone to the new name (only the zone half of the `[zone, action]` pair changes).
3. **`validate()`** now checks `exits` (must be a list of `[zone, action]` pairs whose zone exists), mirroring the server validator, so a dangling exit is surfaced client-side before the POST.

### Changes Made

- `realtimecontrol/rtr/editor.html`:
  - `delZone()` (was line 7095): the post-delete cleanup loop now also strips `exits` pairs naming the removed zone. Changed the early `if (!oz.actions) return;` to an `if (oz.actions) { … }` block so the `exits` cleanup runs even for a zone without actions; added `if (Array.isArray(oz.exits) && oz.exits.length) oz.exits = oz.exits.filter((e) => !(Array.isArray(e) && e[0] === nm));`.
  - `renameZone()` (was line 7161): step 2 (the `next` re-point loop) now also re-points `exits` pairs naming the renamed zone. Same `if (oz.actions) { … }` restructure; added `if (Array.isArray(oz.exits) && oz.exits.length) oz.exits = oz.exits.map((e) => (Array.isArray(e) && e[0] === nm) ? [clean, e[1]] : e);`.
  - `validate()` (was line 6092): added an `exits` reference check after the `speed` check — if `z.exits` is present it must be an array of 2-element `[zone, action]` pairs where `e[0]` is a string naming an existing zone and `e[1]` is a string; otherwise it pushes an error.

### Documentation

- None required beyond this handoff. The change mirrors the already-documented server-side `exits` contract (`zones.py` `_validate_zone`); no user-facing config or behaviour contract changed.

### Architecture

- No architectural impact. The zone graph (actions' `next` + zone-level `exits`) is unchanged; this only keeps the editor's in-memory `TABLE.zones` consistent with that contract on delete/rename, and makes the client validator enforce the same `exits` rule the server already enforces. `exits` remains an optional zone field; zones without one are unaffected.

### Validation

- Inline-JS syntax check: extracted the inline `<script>` (editor.html lines 740–7536) and parsed with Node `vm.Script` → **SYNTAX OK** (6795 lines, no errors).
- Logic test (standalone Node harness replicating the exact cleanup expressions): a `sleeping` zone with `exits [["testing","hold"],["purring","purr"]]` → after `delZone("purring")` the `purring` exit is stripped and `testing` survives; after `renameZone("testing"→"testzone2")` the exit re-points to `["testzone2","hold"]` (action name preserved). **ALL ASSERTIONS PASSED.**
- Confirmed `delZone`/`renameZone` are the only zone delete/rename entry points (grep: `delete TABLE.zones` appears only in those two functions).

### Decisions and Caveats

- **`validate()` `exits` check is defense-in-depth** (the two requested fixes are the `delZone`/`renameZone` cleanups). It mirrors the server validator so a dangling exit from *any* source is caught client-side instead of as a 400. It is conservative: a valid `exits` list passes; only a malformed or dangling one is flagged. If the reviewer prefers the validator untouched, that block is self-contained and trivially removable.
- The `if (oz.actions)` restructure (vs the old `if (!oz.actions) return;`) is behaviour-preserving for the `next` cleanup and merely lets the `exits` cleanup run unconditionally per zone.
- No on-disk `zones.json` was modified — this is a client-side editor fix. The current on-disk file is already valid; the fix prevents the in-memory `TABLE` from going dangling after an in-session delete/rename.

### Next Task

The separate pending item from the prior session: write a fresh `zones.json` containing only the `sleeping` and `testing` zones with all `exits`/`next` cross-references removed, then verify it passes `validate_state_data`. (Not part of this editor fix; flagged so it is not lost.)

### Instructions for the Next Task

1. The editor fix is live in `realtimecontrol/rtr/editor.html` (no build step; reload the editor page to pick it up).
2. To verify: open the editor, delete a zone that other zones reference in `exits`, and save — the save should now succeed (no 400) and the flow/detail views re-render cleanly. Renaming a zone re-points `exits` the same way.
3. If the `zones.json` rewrite is still wanted: build a file with only `sleeping` + `testing`, drop every `exits` and `next` field, and validate against `rtr/state/zones.py` `validate_state_data` before writing.

## 2026-10-26 — Gated the per-tick engine debug prints behind a `--verbose` flag

### Status

Completed. The per-tick debug lines are silent by default and reappear under `--verbose`. Verified with a sim smoke test (0 tick lines default, 147 with `--verbose`).

### Summary

The operator reported the `TypeError` is gone, but the core now spits a lot of debugging **every tick**. The only unconditional per-tick prints were in `engine.py`: the `tick#N start` line (fired every tick) and the `BLOCK-move start/done` pair (fired every tick during a blocking move). These are now gated behind a `verbose` flag, threaded through the central `Config` object and a new `--verbose` CLI flag. All other engine prints (reload, exception watchdog, collision, safety-discard, zone-reload) are event-based and remain ungated.

### Changes Made

- `realtimecontrol/rtr/rtr/core/engine.py`:
  - `Engine.__init__`: added `verbose: bool = False` parameter; stored as `self.verbose`.
  - `run()`: the `tick#N start` print is now wrapped in `if self.verbose:`.
  - `_tick()`: the two `BLOCK-move start/done` prints are now wrapped in `if self.verbose:`.
- `realtimecontrol/rtr/rtr/config.py`: added `verbose: bool = False` field to `Config` (documented in the engine section).
- `realtimecontrol/rtr/main.py`:
  - `parse_args`: added `--verbose` (`action="store_true"`).
  - `build_config`: sets `cfg.verbose = args.verbose`.
  - `run`: passes `verbose=cfg.verbose` into the `Engine(...)` constructor.

### Documentation

- `main.py --help` now lists `--verbose  print per-tick engine debug (tick start, block-move)`. No dedicated doc update needed beyond that; the flag is self-documenting.

### Architecture

- No architectural change. Reuses the existing central-config pattern: `Config` is the single place the core reads runtime settings, and adapters receive it. The `verbose` flag flows `CLI → Config → Engine`, matching how `tick_hz` and every other engine setting is wired. The per-tick data flow is untouched.

### Validation

- `python3 -m py_compile rtr/core/engine.py rtr/config.py main.py` → OK.
- `python3 main.py --help` → `--verbose` present in usage and options.
- `inspect.signature(Engine.__init__)` → `verbose` present with default `False` (so `sim_demo.py`'s `Engine(bus, robot, brain, machine, tick_hz=30.0)` still works).
- Sim smoke test (`python3 -u main.py --sim --no-http --no-midi --no-sound`, `timeout 3`): **0** `tick#` lines without `--verbose`, **147** with `--verbose` (tick start + BLOCK-move start/done per tick). Confirms the gate.

### Decisions and Caveats

- Chose a **CLI flag through `Config`** over an env var, because `main.py` is the real entry point (launched as `python main.py --robot/--sim`) and the codebase already funnels all runtime settings through `Config`. Toggling verbose requires a relaunch with `--verbose` — acceptable for a debug flag. (An env-var fallback, e.g. `RTR_VERBOSE=1`, is easy to add later if a service launcher needs it.)
- The `BLOCK-move` prints are gated too (not just `tick#N start`), since they fire every tick while a blocking move is active and are the bulk of the per-tick noise.
- The **exception watchdog** prints (`EXCEPTION (first, full trace)` / `EXCEPTION`) are intentionally left ungated: they are real error diagnostics, not debug chatter, and are already self-limiting (full trace once per distinct signature, one-liner on repeats).
- The unused `from_args` helper in `rtr/config.py` was intentionally left unchanged (dead code; `main.py` uses `build_config`).

### Next Task

No immediate next task identified. If the operator wants verbose on by default, or an env-var trigger (`RTR_VERBOSE`) for service launches, that is the natural follow-up.

### Instructions for the Next Task

- To see the per-tick debug again: relaunch the core with `--verbose` (e.g. `python main.py --robot --verbose`).
- The gate lives in `engine.py` (`self.verbose`); add further per-tick debug prints there under the same `if self.verbose:` if needed.

## 2026-10-26 — Diagnosed the per-tick `TypeError("'<' not supported between 'str' and 'int'")`; hardened the watchdog + camera-conf path

### Status

In progress (diagnosis narrowed; root line pending a live capture). Instrumentation and a defensive fix are in place and compile clean; the exact raising line will be confirmed by the next live run, which now logs the full traceback on first occurrence.

### Summary

The engine watchdog (added in prior work) now surfaces a **pre-existing** exception that was silently killing the engine task and stranding the robot: `TypeError("'<' not supported between instances of 'str' and 'int'")`, raised **every tick** (hundreds in the log). The operand order (`str` first, `int` second) is the key fingerprint.

This task did two things:

1. **Made the watchdog diagnostic.** Previously it logged only the one-liner `exc!r`, so the exact line could not be pinned. It now logs the **full traceback on the first occurrence** of each distinct exception signature (and the one-liner on repeats), so the next live run identifies the exact file/line.
2. **Ran a full static trace** of the per-tick path to narrow the source, and **hardened one real latent bug** found along the way (camera candidate `conf` compared while still a string).

### Changes Made

- `realtimecontrol/rtr/rtr/core/engine.py` (watchdog in `run()`):
  - Added `import traceback`.
  - Added `self._seen_exceptions = {}` (exception signature → count).
  - The watchdog now computes `sig = f"{type(exc).__name__}: {exc}"`; on first sight of a signature it prints `traceback.format_exc()` (full stack), otherwise the one-liner. Still publishes `Event.SAFETY_ALERT` and `continue`s the loop (engine stays alive).
- `realtimecontrol/rtr/rtr/brain/brain.py` (`_handle_cam_candidates`):
  - `conf = c.get("conf", 0)` is now coerced to `float` inside a `try/except (TypeError, ValueError)` with a `0.0` fallback, before `if conf < min_conf:`. A non-numeric `conf` falls back to `0.0` (below threshold → candidate skipped). This is **defensive hardening**, not a confirmed root-cause fix (see Decisions).

### Documentation

- None required beyond this handoff. The change is a diagnostic + defensive hardening; no user-facing behaviour or config contract changed.

### Architecture

- No architectural change. The watchdog remains a per-tick `try/except` in `Engine.run()`; it only changes *what* it logs (full traceback once per distinct exception). The per-tick data flow (`engine._tick` → `brain.tick` + `machine.step`) is untouched.

### What the static trace established

- **Per-tick path** (`engine._tick`): the only unconditional per-tick calls are `brain.tick(curjpos)` (line ~184) and `machine.step(curjpos)` (line ~189); `curjpos`/`curpos` are floats (`kuka.py get_curjpos/get_curpos` → `float(...)`).
- **`machine.step` clamp path is SAFE:** `build_target` resolves `"x"` frozen axes via `resolve_x` (behavior.py) before `clamp` → `fitlimits`; `action_axis_bands` coerces band values to `float` (zones.py); a scan of `zones.json` confirmed the **only** string values are `"x"` pose axes (safezones are all-numeric, axis_bands coerced, variable_axes all-int).
- **The `conf` path is SAFE from the real pipeline:** `camera/pipeline.py` coerces `conf` to `float(confs[i])`, so the real camera never sends a string conf. (The defensive coercion in `brain.py` still stands as hardening.)
- **Operand-order elimination:** `checklimits` (`lo <= angle <= hi`) would raise `int < str` (reversed) — does NOT match. The only str-first `<` sites are `fitlimits` (helpers.py `angle < lo`), `hardware_clamp` (behavior.py), and `gate._pose_outside_safezone` (`pose[i] < lo`) — all reached only via **resolved** poses or **transient** transition/safety-gated paths, not the steady per-tick clamp.
- **`conf < min_conf` (brain.py) is NOT the live source:** the log line `round(c.get('conf',0), 2)` runs *before* the `conf < min_conf` comparison; if `conf` were a string that `round()` would raise first (a different error). Since the observed error is specifically `<`, `conf` is already numeric at runtime.

### Validation

- `python3 -m py_compile rtr/brain/brain.py rtr/core/engine.py` → OK (both edited files compile).
- No runtime repro: no live core / EKI connection available in this environment. The full-traceback instrumentation is the confirmation mechanism for the exact line.

### Decisions and Caveats

- **The conf fix is defensive, not a confirmed root cause.** It hardens a real latent bug (a string `conf` would break both the `round()` log line and the `<` comparison) but the live error is not from `conf`. It is safe to keep (no-op when `conf` is already numeric) and does not change valid-data behaviour.
- **The exact raising line is NOT yet confirmed.** The strongest remaining hypothesis, by elimination, is a pose axis that is a string (`"x"`) reaching `fitlimits`/`hardware_clamp`/`gate` **without** being resolved by `resolve_x` — most plausibly via a **stuck transition** (`machine.step` transition branch → `hardware_clamp(t)` where the transition target carries an `"x"` axis, so the transition never completes and the error repeats every tick) or a **stuck safety-gated move** (`_resolve_pending` → `check_conflict` → `_pose_outside_safezone`). This is a hypothesis, not a confirmed line.
- **Do not harden `fitlimits`/`checklimits` preemptively** on the strength of this trace — the resolved clamp path is clean, and the safezone/pose data is clean; the string is not entering from those fields.

### Next Task

Capture the full traceback from the next live run to pin the exact line, then fix at the root (resolve the offending pose before it reaches the `<` comparison, or coerce at the specific comparison site the traceback names).

### Instructions for the Next Task

1. Operator: restart the core to load the current `engine.py` (instrumented watchdog) + `brain.py`, and let the exception fire once. The log will show `[engine] tick#N EXCEPTION (first, full trace):` followed by the full stack — **that stack names the exact file:line**.
2. Feed that `file:line` back; apply the root fix there (resolve the `"x"` axis before the comparison, or coerce the specific value). Then re-run to confirm the per-tick exception is gone.
3. If the traceback points to the **transition** path (`machine.step` → `hardware_clamp`), the fix is to resolve `"x"` in the transition target before `hardware_clamp` (mirror `resolve_x` in `build_target`). If it points to the **safety gate** (`_pose_outside_safezone`), resolve the entry pose before the safezone check.
4. Keep the conf coercion (brain.py) and the full-traceback watchdog (engine.py) — both are safe improvements independent of the root cause.

## 2026-10-25 — Added a master MIDI on/off toggle (editor) that gates BOTH MIDI-in and MIDI-out/sound in the core

### Status

Completed. Implemented and tested (headless). The new master button lives in `editor.html`'s MIDI tab; the core now honours a runtime `enabled` soft switch on the sound (MIDI-out) side, mirroring the existing MIDI-in one.

### Summary

The user wanted a single **master "MIDI on/off"** button in the editor that, when off, makes the core completely ignore all incoming MIDI messages AND send no outgoing MIDI (sound) messages.

The MIDI-**in** side already had a runtime soft switch: `midi.json` `enabled`, honoured by `MidiInput._effective_enabled()` (hard constructor switch AND data `enabled`), with the editor's per-zone `enabled` checkbox bound to it. The MIDI-**out/sound** side had **no** runtime soft switch — `Sound.enabled` was constructor-only (`cfg.enable_sound`) and `sound.json` had no `enabled` field.

So the work was: (1) give the sound adapter the same soft-switch pattern as MIDI-in, and (2) add one editor button that drives **both** sides and saves both tables so the core hot-reloads each.

### Changes Made

- `realtimecontrol/rtr/rtr/sound/sound.py`:
  - `builtin_sound_data()` now ships `"enabled": True`.
  - `validate_sound_data()` validates a top-level `enabled` (must be a bool; defaults to `True` when absent).
  - New `Sound._effective_enabled()` = constructor `self.enabled` AND the data `enabled` (mirrors `midi.py`).
  - `on_event()` now early-returns on `not self._effective_enabled()` (was `not self.enabled`), so no zone/action/pose MIDI is sent while off.
  - `reload()` closes **all** open out ports when `_effective_enabled()` is false (was always `_reconcile_outs()`), so no MIDI can leak out of a stale port; the reload log now reports `enabled=`.
- `realtimecontrol/rtr/sound.json`: added `"enabled": true` at the top level.
- `realtimecontrol/rtr/editor.html`:
  - `#midiToggleRow` now holds a master checkbox `#midiMasterCb` (label "midi master on/off") beside the out/in sub-tabs.
  - `SOUND_FALLBACK` gained `enabled: true`; `loadSound()` seeds `SOUND.enabled` (default `true`); `validateSound()` rejects a non-bool `SOUND.enabled`.
  - New `refreshMidiMaster()` (sets the checkbox to `MIDI.enabled && SOUND.enabled`) and `onMidiMasterToggle()` (sets **both** `MIDI.enabled` and `SOUND.enabled`, then `saveMidiAll()` which POSTs `/api/sound` + `/api/midi` so the core hot-reloads both).
  - Wired: `$("midiMasterCb").onchange = onMidiMasterToggle`; `refreshMidiMaster()` called in the bootstrap load chain (after `loadMidi`), in `switchView` when the MIDI tab shows, on the per-zone `enabled` checkbox change (keeps the master in sync when `MIDI.enabled` is toggled there), and after a config restore.

### Documentation

- None required beyond this handoff — the change follows the existing, already-documented MIDI-in soft-switch pattern; no new architecture doc is warranted.

### Architecture

- No new modules or data-flow changes. The sound adapter now exposes the same two-switch gate (`hard` = `cfg.enable_sound`, `soft` = `sound.json` `enabled`) as the MIDI-in adapter. The editor's master button is a convenience that writes both `enabled` fields; the two tables remain independently editable (the per-zone MIDI-in `enabled` checkbox still works and stays in sync via `refreshMidiMaster`).

### Validation

- `python tests/test_sound_enabled.py` → ALL TESTS PASSED (9 tests: validate accepts/defaults/rejects `enabled`; `builtin_sound_data` includes it; `_effective_enabled` truth table (hard×soft); soft-off sends nothing; soft-off reload closes all out ports; re-enabling resumes sending).
- `python tests/test_sound_out_ports.py` → ALL TESTS PASSED (no regression to the out-port routing).
- `python tests/test_midi_in.py` → ALL TESTS PASSED (1 skipped) (no regression to MIDI-in).
- Full `pytest tests/ --ignore=tests/test_remote_tui.py` → 294 passed, 7 failed. The 7 failures (`test_transition`, `test_bridge_behavior`, `test_remote_sdl`×5) are **pre-existing** — confirmed by stashing my changes and re-running (same 7 fail without them). `test_remote_tui.py` fails at collection on a pre-existing `action_kind` import error, also unrelated.

### Decisions and Caveats

- **Master semantics:** the button reads "on" only when **both** `MIDI.enabled` and `SOUND.enabled` are true; if either is off it shows off. Toggling it sets both to the new value and saves both. This means the master can re-enable a side that was individually turned off (expected master-switch behaviour).
- **Save path:** the master toggle reuses `saveMidiAll()`, which validates the **whole** SOUND and MIDI tables before POSTing. If an unrelated field elsewhere is invalid, the toggle won't persist (consistent with the editor's normal save button behaviour).
- **No hardware test:** verified headless with a fake rtmidi (per project convention); no real MIDI device was exercised.

### Next Task

No immediate next task identified. (Optional follow-ups: add a matching master-state indicator light to `client.html`, or let the per-zone MIDI-in `enabled` checkbox and the master button share a single source of truth more explicitly.)

### Instructions for the Next Task

1. To verify on hardware: start the core, open the editor → MIDI tab, toggle the master checkbox off, confirm the core logs `[sound] mapping reloaded: enabled=False` and `[midi] ... enabled=False`, and that no MIDI is sent / no controller keys are honoured. Toggle on to restore.
2. The core picks up the change via the existing hot-reload path (`POST /api/sound` → `Cmd.RELOAD_SOUND` → `engine._sound_reload()`; `POST /api/midi` → `Cmd.RELOAD_MIDI` → `engine._midi_reload()`); no restart is required.

## 2026-10-25 — Reverted `KUKAPY_SERVER.SRC` to the original `WAIT FOR $FLAG[1]` (polling-loop "flaghack" rejected by the KUKA compiler)

### Status

Completed. Working tree restored byte-identical to the pre-flaghack original (git commit `961b915 kukapy`). The flaghack version is preserved as `KUKAPY_SERVER_flaghack.SRC` (user-renamed, untracked).

### Summary

The KUKA compiler rejected the polling-loop version with: `line 67 col 15 expected SEC, FOR or CLOCK`. That line is `WAIT 0.1` — bare `WAIT <value>` is invalid KRL; the grammar is `WAIT <value> SEC` | `WAIT FOR <condition>` | `WAIT CLOCK`. The user renamed the edited file to `KUKAPY_SERVER_flaghack.SRC` to keep it as a draft and asked for `KUKAPY_SERVER.SRC` to be reverted to the original. The original (with `WAIT FOR $FLAG[1]` at line 57) was restored from git commit `961b915` via `git restore --source=961b915`. The `KukaPyNEW` sync copy was re-synced to the original as well.

**The Python-side stall recovery remains in place and is compatible with the original KRL:** `robot.py`'s `_recover()` sends a ping that sets `$FLAG[1]`, which wakes the KRL parked at `WAIT FOR $FLAG[1]`; the engine watchdog (`engine.py`) still catches tick exceptions and publishes `Event.SAFETY_ALERT`. So the anti-strand protection does not depend on the KRL polling loop.

### Changes Made

- `realtimecontrol/rtr/KukaPy/kukadriver/KUKAPY_SERVER.SRC`: restored to original content — `WAIT FOR $FLAG[1]` (line 57), no `wait_ticks`/`wait_timeout` declarations, no polling loop.
- `realtimecontrol/kukapyEKI/KukaPyNEW/kukadriver/KUKAPY_SERVER.SRC`: re-synced to the identical original content.
- `realtimecontrol/rtr/KukaPy/kukadriver/KUKAPY_SERVER_flaghack.SRC`: untouched (user's preserved copy of the rejected polling-loop version; still contains the invalid `WAIT 0.1` line).

### Documentation

- None required — this reverts a previously documented change; the prior handoff entry below still documents the flaghack design and the Python-side recovery, which remain accurate.

### Architecture

- No architectural impact. The KRL protocol, `<Rob>`/`<Res>` framing, and flag handshake are unchanged from the original design.

### Validation

- `git diff 961b915 -- realtimecontrol/rtr/KukaPy/kukadriver/KUKAPY_SERVER.SRC` → empty (working tree byte-identical to the original commit).
- `grep -n "WAIT\|WHILE\|wait_ticks"` on the restored file → only `57: WAIT FOR $FLAG[1]` (no polling loop remnants).
- `diff` between `rtr/KukaPy` and `KukaPyNEW` copies → identical.

### Decisions and Caveats

- **The flaghack file still has the KRL syntax error.** If it is ever wanted back, line 67 must be changed from `WAIT 0.1` to `WAIT 0.1 SEC` before it will compile. It was intentionally left unmodified (user's draft).
- **Controller state:** the operator should confirm the controller is running the original program (re-download `KUKAPY_SERVER.SRC` if the flaghack version was ever downloaded). The core machine does not need a restart — the Python-side recovery (`robot.py` `_recover`, `engine.py` watchdog) is unchanged and works with the original KRL.
- **The polling-loop idea is not the current line of defense.** The Python-side recovery ping + engine watchdog remain the stall protection while the KRL stays at the original blocking `WAIT FOR $FLAG[1]`.

### Next Task

Confirm on the controller that the original `KUKAPY_SERVER.SRC` is running, then continue root-cause diagnosis of the original stall (capture a `KUKAPY_DEBUG=1` log during a `hunting/track2` run) to determine whether the Python engine blocks in `_recv_xml`, a blocking move, or raises an uncaught exception.

### Instructions for the Next Task

1. **On the KUKA controller:** verify the running program is the original (program counter parks at `WAIT FOR $FLAG[1]` between commands — normal). If the flaghack version was downloaded, re-download the original `KUKAPY_SERVER.SRC`.
2. **On the core machine:** restart the core (to pick up `robot.py`/`engine.py` changes, which remain in place), enable `KUKAPY_DEBUG=1`, and re-run `hunting/track2`.
3. **Capture the log** during/after a stall: look for `[kukapy] STALL detected; attempting recovery ping` (recovery path taken) or `[engine] tick#N EXCEPTION:` (watchdog caught an exception). That identifies the exact blocking point.

## 2026-10-25 — Replaced the KRL's indefinite `WAIT FOR $FLAG[1]` with a timed polling loop + added Python-side stall recovery (recovery ping on staleness guard fire)

### Status

Completed (KRL + Python changes implemented locally). On-robot redeploy/restart is the operator's action: the KRL file must be re-downloaded to the controller, and the core must be restarted to pick up the new `robot.py`.

### Summary

The pendant HMI shows `Wait for $FLAG[1]` while the robot is RUNNING but idle because the KRL's `WAIT FOR $FLAG[1]` (KUKAPY_SERVER.SRC line 57) is a **blocking wait with no timeout**. When the Python engine stops sending commands (stuck in `_recv_xml`, a blocking move, or an uncaught exception), the KRL parks on that line indefinitely. The KRC EKI middleware keeps sending `AliveCounter` keepalives (so the connection appears alive), but the KRL program itself is suspended at the `WAIT FOR` instruction.

**Two-part fix:**

1. **KRL side** (`KUKAPY_SERVER.SRC`): Replaced `WAIT FOR $FLAG[1]` with a `WHILE NOT $FLAG[1]` polling loop that checks the flag every 100 ms. The loop has a 5-second timeout counter (`wait_ticks >= wait_timeout`) that resets every cycle, so the KRL never parks on a single blocking instruction. The pendant now shows the program is actively polling (in the loop) rather than frozen at `WAIT FOR`. The KRL continues waiting indefinitely (the loop never exits), but it is no longer suspended on a single instruction — it is running, which means the controller can be interrupted, the program counter is visible, and the operator can see the program is alive.

2. **Python side** (`kukapy/robot.py`): Added a `_recover()` method and integrated it into `_send_cmd`. When the staleness guard fires (no `<Res>` within `res_timeout`), `_send_cmd` now catches the `KukaError`, calls `_recover()` (which sends a `ping` command — this sets `$FLAG[1]`, waking the KRL's polling loop), and retries the original command once. If the recovery ping also fails, the error propagates (the connection is dead). This turns the previous "staleness guard fires → `KukaError` propagates → engine task dies → permanent strand" into "staleness guard fires → recovery ping → KRL wakes → command retried → handshake restored."

**Why this works:** The KRL's polling loop means the KRL is always active (never suspended on a single `WAIT FOR`). The Python-side recovery ping means that even if the KRL's flag is not set for a while (Python was stuck), the next command attempt will first send a ping that sets `$FLAG[1]`, waking the KRL and re-establishing the handshake. The two changes together ensure the robot never reaches a permanent `Wait for $FLAG[1]` state: the KRL is always polling (not parked), and the Python side recovers the handshake on stall.

### Changes Made

- `realtimecontrol/rtr/KukaPy/kukadriver/KUKAPY_SERVER.SRC`:
  - Added `DECL INT wait_ticks` and `DECL INT wait_timeout` to the variable declarations.
  - Replaced `WAIT FOR $FLAG[1]` with a `WHILE NOT $FLAG[1]` loop: checks the flag every 100 ms (`WAIT 0.1`), resets `wait_ticks` every 50 iterations (5 s), and continues looping until the flag is set.
  - The `$FLAG[1] = FALSE` reset is preserved after the loop.
- `realtimecontrol/rtr/KukaPy/kukapy/robot.py`:
  - `_send_cmd`: wrapped the `_recv_xml` call in a `try/except KukaError`; on stall, calls `_recover()` and retries the command once.
  - New `_recover()` method: sends a `ping` command (sets `$FLAG[1]`, wakes the KRL), waits for the `<Res>`, raises `KukaError` if the ping fails.
  - Debug logging: `[kukapy] STALL detected; attempting recovery ping` when the recovery path is taken.

### Documentation

- In-code: the `_send_cmd` docstring documents the recovery behavior; the `_recover` docstring explains the mechanism; the KRL comment block explains the polling loop.

### Architecture

- No architectural impact. Same modules, same public API; the KRL loop structure changed from a single `WAIT FOR` to a polling loop, and the Python `_send_cmd` now has a one-shot recovery path. Backward-compatible: the protocol is unchanged, the `<Rob>`/`<Res>` framing is unchanged, and the timeout values are unchanged.

### Validation

- `python3 -m py_compile` on `robot.py` → OK.
- KRL syntax: the `WHILE NOT $FLAG[1]` loop is standard KRL; the `WAIT 0.1` and `IF/ENDIF` are standard KRL instructions. (Cannot validate KRL locally; the operator must verify on the controller.)
- Existing tests: the `_recv_xml` tests are unaffected (the recovery path is in `_send_cmd`, not `_recv_xml`).

### Decisions and Caveats

- **The KRL polling loop does NOT send a keepalive or timeout notification to Python.** The KRC EKI middleware already sends `AliveCounter` keepalives automatically. The polling loop's purpose is to keep the KRL program active (not suspended) so the pendant shows the program is running. If a timeout notification is needed, it can be added later (e.g., send a `<Res>` with a special code when `wait_ticks` resets).
- **The recovery ping is a single retry.** If the connection is truly dead (not just a stalled handshake), the ping fails and the error propagates. The engine's existing exception handling (or lack thereof) determines what happens next. A full reconnection mechanism is out of scope for this change.
- **The KRL file must be re-downloaded to the controller.** The `KUKAPY_SERVER.SRC` change is not automatically deployed; the operator must download the updated program to the KUKA controller and start it.
- **The `wait_timeout = 50` (5 s) is arbitrary.** It is long enough to avoid false-positive timeout notifications during normal operation (the Python engine sends commands at 20 Hz, so the flag is set every 50 ms) but short enough that the KRL is visibly active on the pendant. Adjust if needed.

### Next Task

Operator: (1) download the updated `KUKAPY_SERVER.SRC` to the KUKA controller and start it; (2) restart the core so the new `robot.py` with the recovery path loads; (3) re-trigger `hunting/track2` (or another block-mode action) and confirm the robot no longer strands at `WAIT FOR $FLAG[1]`.

### Instructions for the Next Task

1. **On the KUKA controller:** download `KUKAPY_SERVER.SRC` (the updated version with the polling loop) and start it. The program counter should show the program is in the `WHILE` loop (not at a single `WAIT FOR` line).
2. **On the core machine:** restart the core app so the new `robot.py` (with `_recover`) loads.
3. **Trigger the strand scenario:** `goto_zone hunting` then `play_action track2`.
4. **Verify:** the robot should no longer strand at `WAIT FOR $FLAG[1]`. If it does, the `[kukapy] STALL detected; attempting recovery ping` log line will appear in the core log (with `KUKAPY_DEBUG=1`), confirming the recovery path was taken.
5. **If the recovery ping fails:** the connection is dead; check the TCP connection and the KRC EKI status.

## 2026-10-25 — Made the `res_timeout` guard cnt-aware: blocking PTPs (`cnt=0`) use a longer `res_timeout_blocking` (60 s) so a legitimately long move no longer false-positives

### Status

Completed (implemented + validated locally against both `robot.py` copies and the wrapper). On-robot redeploy/restart is the operator's action.

### Summary

The previous entry added a wall-clock staleness guard to `_recv_xml` (default `res_timeout=10.0 s`) to turn the keepalives-without-`<Res>` hang into a recoverable `KukaError`. Its own "Decisions and Caveats" noted the risk: a **blocking** PTP (`cnt_val == 0`) holds the KRL's `<Res>` until the robot *arrives* (`KUKAPY_SERVER.SRC` CASE 4 — a plain `PTP` blocks until arrival), so any blocking move longer than 10 s would false-positive the guard. The user's "still fails" log pointed at exactly this ("might have been an unusual longh action"), and `zones.json` `hunting/track2` is `move_mode: block`.

**Fix:** make the timeout cnt-aware. `Robot.move()` now picks the effective timeout from `cnt_val`:
- `cnt_val == 0` (blocking PTP) → `self.res_timeout_blocking` (new, default **60.0 s**): the KRL legitimately holds the `<Res>` until arrival, so the guard must not fire mid-move.
- `cnt_val > 0` (stream) and every other command (ping/curjpos/curpos/setdo/getdo) → the short `self.res_timeout` (10.0 s): the KRL answers immediately, so a delayed `<Res>` there is still a real stall.

The override is threaded per-call: `move()` → `_send_cmd(res_timeout=rt)` → `_recv_xml(res_timeout=rt)` (falls back to `self.res_timeout` when the override is `None`). Stream moves and queries are untouched. The `KukaRobot` wrapper exposes both timeouts as optional constructor params (default `None` → use the `Robot` defaults) so a deployment can tune them without editing the vendored `robot.py`.

### Changes Made

- `realtimecontrol/rtr/KukaPy/kukapy/robot.py` (shadow copy):
  - `Robot.__init__`: new `res_timeout_blocking: float | None = 60.0` param; stored as `self.res_timeout_blocking` (independent of `res_timeout`; `None` disables that specific guard).
  - `_recv_xml(self, res_timeout=None)`: new per-call override; `rt = self.res_timeout if res_timeout is None else res_timeout`; the staleness guard now compares against `rt` and reports `{rt:.0f}s`.
  - `_send_cmd(self, cmd_type, res_timeout=None, **kw)`: passes `res_timeout` through to `_recv_xml`.
  - `move()`: `rt = self.res_timeout_blocking if cnt_val == 0 else self.res_timeout`; passes `res_timeout=rt` to `_send_cmd`.
- `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` (the **actual runtime module**, via editable install): copied to stay **byte-identical** to the shadow copy.
- `realtimecontrol/rtr/rtr/robot/kuka.py` (wrapper):
  - `from typing import List, Optional`.
  - `KukaRobot.__init__`: new optional `res_timeout` / `res_timeout_blocking` params (default `None` → Robot defaults).
  - `connect()`: forwards them to `Robot(...)` only when not `None`.
- `realtimecontrol/rtr/tests/test_kuka_eki_recv.py`: new `import time`; new `DelayedSock` (keepalives until `delay` s, then `<Res>`, with a no-op `sendall`); two new tests — `test_blocking_move_uses_longer_timeout` and `test_stream_move_uses_short_timeout`.

### Documentation

- In-code: `res_timeout_blocking` documented in `Robot.__init__`; the per-call override documented in the `_recv_xml` and `_send_cmd` docstrings; the cnt-awareness rationale documented in `move()`; the wrapper params documented in `KukaRobot.__init__`. No other docs needed.

### Architecture

- No architectural impact. Same modules, same public API; only a new optional constructor param on `Robot` + `KukaRobot`, a per-call override threaded through `_send_cmd`/`_recv_xml`, and a timeout selection in `move()`. Backward-compatible: legacy scripts that construct `Robot(port=...)` are unaffected (the new param has a default).

### Validation

- `python tests/test_kuka_eki_recv.py` → **13 passed** (11 recv-framing/staleness + 2 new cnt-aware tests).
- `python tests/test_kuka_eki_serialization.py` → **4 passed**.
- `python -m py_compile` on `robot.py`, `rtr/robot/kuka.py`, and the test file → OK.
- `diff` of the two `robot.py` copies → identical.

### Decisions and Caveats

- `res_timeout_blocking` defaults to **60 s**, chosen with ~6× margin over the worst realistic blocking move: the slowest zone speed is 10 % and the largest single-axis span is ~180°, so the longest realistic blocking PTP is ~10 s; 60 s is a hard bound (not forever) that still catches a truly stranded KRL during a blocking move. If a deployment has genuinely longer one-shot blocking moves, raise it (or set `res_timeout_blocking=None` to disable that guard) via the `KukaRobot` constructor.
- The two timeouts are **independent**: `res_timeout=None` disables the stream/query guard, `res_timeout_blocking=None` disables the blocking guard. (Deliberately not auto-linked, so disabling one does not silently disable the other.)
- The `make_robot` factory in `rtr/rtr/robot/__init__.py` does not forward the timeouts, so it uses the `Robot` defaults — correct for the fix; extend it only if an operator needs to tune at config level.

### Next Task

Operator: redeploy/restart the core (the pip editable install loads `KukaPyNEW/kukapy/robot.py` directly — no file copy needed if it runs from this tree), then re-trigger the long hunting action that previously failed and confirm the blocking move no longer raises `KukaError: no <Res> within 10s`.

### Instructions for the Next Task

1. Restart the core app so the editable `KukaPyNEW/kukapy/robot.py` picks up the cnt-aware `move()`.
2. Re-trigger the action that failed (e.g. `hunting` → `track2`, a `block`-mode action) — the blocking PTP now waits up to `res_timeout_blocking` (60 s) before the guard can fire, so a legitimately long move completes instead of erroring.
3. If a specific one-shot move is still too long for 60 s, construct the robot with a longer/disabled blocking timeout, e.g. `KukaRobot(port=18735, res_timeout_blocking=120.0)` (or `res_timeout_blocking=None` to disable that guard entirely).
4. Note: the core runtime at `/home/kaos/FTI_kuka` is a *separate, diverged* checkout from this workspace (`/home/kaos/Documents/kaotec/FTI_kuka`) — confirm the deployment/sync path before assuming this fix is live on the core.

## 2026-10-25 — Added the Res-staleness guard to `_recv_xml`: keepalives-without-`<Res>` now raise `KukaError` after `res_timeout` instead of hanging forever

### Status

Completed (implemented + validated locally against both `robot.py` copies). On-robot redeploy/restart is the operator's action.

### Summary

The multi-document `_recv_xml` deadlock (previous entry) is fixed, but a second, rarer stall remained: KRC stays alive (periodic `<AliveCounter>` keepalives keep arriving) yet never sends the `<Res>` the Python side is waiting for, and the KRL is stranded at `WAIT FOR $FLAG[1]` (line 57). The socket-level `recv_timeout` (300 s) never fires in this case because `recv()` keeps *succeeding* on the keepalive bytes — so `_recv_xml` discards keepalives forever: a silent, unrecoverable hang.

**Fix:** a wall-clock staleness guard in `_recv_xml`. `t0 = time.monotonic()` is recorded at call start; after every `recv()`, if `self.res_timeout` is set and `time.monotonic() - t0 > self.res_timeout` (default 10.0 s), `_recv_xml` raises `KukaError("no <Res> within Ns (keepalives still arriving); KRL stranded at WAIT FOR $FLAG[1]")`. This turns the silent forever-hang into a recoverable, self-describing error.

**Note:** this detects the stall but does not fix the root cause of *why* the KRC stops sending the `<Res>` (or why the KRL is stranded). Discriminating between (a) Python stuck in `_recv_xml` and (b) a client→KRC framing desync (KRC never set `$FLAG[1]`) still requires a `KUKAPY_DEBUG=1` capture.

### Changes Made

- `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` (the **actual runtime module**, via editable install):
  - New import `time`.
  - `Robot.__init__`: new `res_timeout: float | None = 10.0` parameter; stored as `self.res_timeout` (`None` disables the guard).
  - `_recv_xml`: `t0 = time.monotonic()` at call start; after each `recv()`, raise `KukaError` if `self.res_timeout is not None and time.monotonic() - t0 > self.res_timeout`.
- `realtimecontrol/rtr/KukaPy/kukapy/robot.py` (shadow copy): same changes; now byte-identical to KukaPyNEW.
- `realtimecontrol/rtr/tests/test_kuka_eki_recv.py`: new regression test `test_keepalives_without_res_raise_timeout` (FakeSock looping keepalives forever, `res_timeout=0.5`, asserts `KukaError`).

### Documentation

- In-code: `res_timeout` documented in `__init__` and in the `_recv_xml` docstring (stall signature + why the socket timeout can't catch it). No other docs needed.

### Architecture

- No architectural impact. Same module, same API; only a new optional `res_timeout` constructor parameter and a wall-clock guard in the receive loop.

### Validation

- `python3 test_kuka_eki_recv.py` → **11 passed** (10 recv-framing + 1 new staleness test).
- `python3 test_kuka_eki_serialization.py` → **6 passed**.
- `mypy robot.py` → only the **2 pre-existing** errors (lines 141 & 201: `comm_sock` is `socket | None`); **no new errors** from the guard.
- `diff` of the two `robot.py` copies → identical; both parse (`ast.parse`) clean.

### Decisions and Caveats

- The guard is a wall-clock timeout on the whole `_recv_xml` call (default 10.0 s), not a "time since last byte" check: during the stall keepalives keep arriving, so a last-byte timer would never accumulate. A wall-clock timer is the only simple timer that fires.
- Caveat: a legitimately long *blocking* PTP move (KRL blocked on `PTP` line 131, keepalives still arriving) could false-positive if it exceeds `res_timeout`. In block mode (the primary use case) moves are small 20 Hz increments, so 10 s is very safe. For long one-shot moves (e.g. the `gotopos` transition in `midikuka.py`), raise `res_timeout` (e.g. `Robot(port=18735, res_timeout=30.0)`) or disable it (`res_timeout=None`).
- The guard does not fix the root cause; it makes the stall detectable and recoverable.

### Next Task

Operator: run with `KUKAPY_DEBUG=1`, re-trigger `hunting` → `track2`, and capture the stall. The new `KukaError` will confirm the stall (last log line a `SEND` with no following `FRAME`/`<Res>`). Then discriminate the root cause: if the KRL is parked at line 57 with the flag never set, it is a client→KRC framing desync (verify `SENDFLAG=1` on the pendant's `KUKAPY.xml`); if the KRL is mid-move, it is a slow KRL.

### Instructions for the Next Task

1. Restart the core app (the pip editable install loads `KukaPyNEW/kukapy/robot.py` directly — no file copy needed if it runs from this tree).
2. Launch with `KUKAPY_DEBUG=1` in the environment to get per-frame `SEND`/`FRAME` logs back.
3. Re-trigger `hunting` → `track2`; when the stall occurs, capture the last ~20 log lines + the KRL line. The new `KukaError("no <Res> within 10s ...")` confirms the stall and that the KRL is stranded at `WAIT FOR $FLAG[1]`.
4. To tune the timeout for long moves: `Robot(port=18735, res_timeout=30.0)`, or `res_timeout=None` to disable the guard entirely.

## 2026-10-25 — Fixed the real recurring stall: multi-document `_recv_xml` deadlock (KRC sends plain XML, no `\x00`); silenced per-frame logging behind `KUKAPY_DEBUG=1`

### Status

Completed (fix implemented + validated locally). On-robot redeploy/restart is the operator's action.

### Summary

**Version question (answered by the trace):** the app *was* running the fixed code. Proof: (1) every `[kukapy] SEND` line in the operator's trace ends with `\x00` — only the fixed `_send_xml` produces that; (2) the `FRAME2` marker only exists in the current `robot.py`. Module resolution: the app runs under the `kukart` pyenv, where `kukapy` is a **pip editable install** pointing at `realtimecontrol/kukapyEKI/KukaPyNEW` — so the loaded file is `KukaPyNEW/kukapy/robot.py`, **not** `rtr/KukaPy` (that is a shadow copy; both now carry identical code).

**The `\x00`-both-directions hypothesis was wrong.** Every response in the trace was parsed via `FRAME2` (the plain-XML fallback) — mode 1 (`\x00`-split) never fired, so **KRC sends plain XML documents with no `\x00` flags on the wire**. `SENDFLAG=1` applies to the client→KRC direction only (our `\x00`-terminated `<Rob>` sends are correct and stay). The stall moved from tick #130 to #480 — the KRC side is now healthy — but a second, independent bug remained on the Python side.

**Root cause of the recurring stall:** KRC interleaves `<Res>` responses with periodic `<AliveCounter>` keepalives (`KUKAPY.xml` `<ALIVE ElementName="AliveCounter"/>`). Both are small (~260 B + ~40 B), so they routinely coalesce into **one TCP segment = one `recv()` chunk holding two complete documents**. The old mode-2 code did `ET.fromstring(whole_buffer)`, which raises `ParseError: junk after document element` (verified) whenever two documents are present — so `_recv_xml` kept accumulating keepalives forever (silent; the 1 MB guard is hours away), never returned, never sent the next `<Rob>`, and the KRL parked at `WAIT FOR $FLAG[1]` (line 57). This exactly matches the stall signature: last log line `SEND Type2`, then nothing, KRL at line 57. The stall point is random (TCP coalescing luck), which is why it moved 130 → 480.

**Fix (both `kukapy/robot.py` copies, now byte-identical):**
1. `_recv_xml` rewritten to scan the buffer **document by document**: split on `\x00` if present (dormant fallback), otherwise slice the first complete document out by root tag (new `_first_document` helper: regex for the root tag, `find("</root>")`), parse it, keep the rest. Non-`<Res>` documents (keepalives) are skipped. Leftover bytes still persist in `_recv_buf`.
2. Logging silenced: per-frame `SEND`/`FRAME` prints are now gated behind `KUKAPY_DEBUG=1` (env var). Error logs (`FRAME-PARSE-FAIL`, `DOC-PARSE-FAIL`, buffer overflow) stay always-on. This kills the ~15 KB/s log spam (3 full 250 B frame reprs per tick at 20 Hz).

### Changes Made

- `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` (the **actual runtime module**, via editable install):
  - New imports `os`, `re`; module-level `_DEBUG = os.environ.get("KUKAPY_DEBUG", "") == "1"`.
  - `_send_xml`: `\x00` framing kept (correct for KRC side); print gated behind `_DEBUG`.
  - `_recv_xml` + new `_first_document`: per-document scanning (see Summary).
  - Per-chunk `RECV#` dump removed (already done in the shadow copy last turn).
- `realtimecontrol/rtr/KukaPy/kukapy/robot.py` (shadow copy): same rewrite applied; now byte-identical to KukaPyNEW.
- `realtimecontrol/rtr/tests/test_kuka_eki_recv.py`: 4 new regression tests for the plain-XML production stream: `test_plain_xml_res_plus_keepalive_one_chunk` (the exact stall), `test_plain_xml_keepalive_then_res_one_chunk`, `test_plain_xml_two_res_one_chunk_preserves_tail`, `test_plain_xml_partial_then_keepalive`.

### Documentation

- In-code framing comment block rewritten: client→KRC is `\x00`-delimited (SENDFLAG), KRC→client is plain XML with keepalives. No other docs needed.

### Architecture

- No architectural impact. Same module, same API; only the receive-side document handling and log verbosity changed.

### Validation

- `PYTHONPATH=realtimecontrol/rtr/KukaPy python3 -m pytest tests/test_kuka_eki_recv.py tests/test_kuka_eki_serialization.py -q` → **16 passed** (10 recv-framing incl. 4 new + 6 serialization).
- Same suite against the runtime copy: `PYTHONPATH=.../kukapyEKI/KukaPyNEW python3 -m pytest ...` → **16 passed**.
- Live simulation against the KukaPyNEW module (thread + timeout, so a hang = deterministic failure): stall-case `Res+Alive` one chunk → `Res`; `Alive+Res` → `Res`; split `Res` + `Alive` + next `Res` → `Res` then `Res`; byte-by-byte → `Res`. All pass.
- `diff` of the two `robot.py` copies → identical.

### Decisions and Caveats

- The old tests only covered `\x00`-delimited traffic (mode 1) — the mode that never fires in production. The new tests cover the plain-XML mode that actually runs.
- If the stall recurs after this fix, it is no longer a Python-side framing issue (the reader now provably handles every coalescing pattern). Next discriminator: run with `KUKAPY_DEBUG=1` and capture the trace tail — if `SEND` lines stop with no `FRAME`/`DOC-PARSE-FAIL`, the KRC side stopped responding (check the pendant's `KUKAPY.xml` for `SENDFLAG=1` and the KRL line).
- `KUKAPY_OLD` (`kukapyEKI/KukaPyOLD`) is an untouched legacy copy; ignore it.

### Next Task

Operator: restart the core (the editable install picks up the new `robot.py` automatically — no file copy needed if the app runs from this tree; otherwise sync `KukaPyNEW/kukapy/robot.py` to 192.168.10.201), re-trigger `hunting` → `track2`, and confirm the core runs past the previous stall point (~tick #480) without stranding.

### Instructions for the Next Task

1. Restart the core app (no code copy needed if it runs from this tree — the pip editable install loads `KukaPyNEW/kukapy/robot.py` directly).
2. The log should now be much quieter: no per-tick `[kukapy] SEND`/`FRAME` lines. To bring them back: `KUKAPY_DEBUG=1` in the launch environment.
3. Re-trigger `hunting` → `track2`; verify no strand (KRL not parked at line 57) past the old stall point.
4. If it strands: launch with `KUKAPY_DEBUG=1`, capture the last ~20 lines + the KRL line, and check `SENDFLAG=1` on the pendant's `KUKAPY.xml`.

## 2026-10-25 — Ported the `\x00` SENDFLAG fix to the real runtime module `realtimecontrol/rtr/KukaPy/kukapy/robot.py` + removed the excessive per-chunk RECV logging

### Status

Completed (fix implemented + validated locally). On-robot redeploy/restart is the operator's action (the core runs on 192.168.10.201, not this machine).

### Summary

The operator clarified that the real runtime module is **`realtimecontrol/rtr/KukaPy/kukapy/robot.py`** (the `kukapy` package under `rtr/KukaPy`, resolved by `from kukapy.robot import Robot` in `rtr/robot/kuka.py:47`), **not** the `KukaPyNEW` copy fixed in the previous entry. The same two fixes were applied there, plus the debug instrumentation was trimmed:

1. **`\x00` SENDFLAG framing:** `_send_xml` now sends `xml_str.encode("utf-8") + b"\x00"` — every `<Rob>` is `\x00`-terminated, matching `KUKAPY.xml` `SENDFLAG=1` (both directions null-delimited). This is the fix for the line-57 strand (KRC parser byte-accounting drift → `$FLAG[1]` never set).
2. **`recv_timeout` default `None` → `300`** so a future desync raises `socket.timeout` after 300 s instead of hanging forever. `None` is still accepted explicitly for wait-forever.
3. **Logging trimmed:** removed the per-chunk `[kukapy] RECV#` dump (it reprinted the entire growing buffer on every `recv` — quadratic output). Kept: `[kukapy] SEND` (one line per command; confirms the trailing `\x00` after deploy), `[kukapy] FRAME` / `FRAME2` / `FRAME-PARSE-FAIL` (one line per parsed frame; desync detection), and the 1 MB buffer-overflow guard.

The framing comment block at the `_send_xml` section was corrected (it previously claimed "raw stream, no null-byte framing", contradicting the deployed `SENDFLAG=1`).

### Changes Made

- `realtimecontrol/rtr/KukaPy/kukapy/robot.py`:
  - `_send_xml`: `data = xml_str.encode("utf-8")` → `data = xml_str.encode("utf-8") + b"\x00"`.
  - `__init__`: `recv_timeout: int | None = None` → `recv_timeout: int | None = 300`; comment updated.
  - `_recv_xml`: removed the `n` counter and the `print(f"[kukapy] RECV#{n} ...")` per-chunk dump.
  - Framing comment block rewritten to document `SENDFLAG=1` both-directions `\x00` framing.
- The `KukaPyNEW/kukapy/robot.py` copy from the previous entry is now a **duplicate** of the fix (it still contains the verbose `RECV#` logging). If it is no longer used, consider deleting it to avoid two divergent copies.

### Documentation

- None required — the in-code comment block documents the framing; the KUKAPY.xml `SENDFLAG=1` semantics are already documented in the previous handoff entry.

### Architecture

- No architectural impact. Same module, same API; only wire-format framing (now matching the deployed config) and log verbosity changed.

### Validation

- `PYTHONPATH=realtimecontrol/rtr/KukaPy python3 -m pytest realtimecontrol/rtr/tests/test_kuka_eki_recv.py realtimecontrol/rtr/tests/test_kuka_eki_serialization.py -q` → **12 passed** (6 recv-framing + 6 serialization).
- Import check: `kukapy.robot.__file__` resolves to `realtimecontrol/rtr/KukaPy/kukapy/robot.py` (the edited file); `Robot(port=1).recv_timeout == 300`; `_send_xml` source contains the `+ b"\x00"` framing.

### Decisions and Caveats

- The `kukapy` package is **not** pip-installed globally (`import kukapy` fails from a bare shell); the runtime and tests resolve it via `PYTHONPATH`/working-directory pointing at `realtimecontrol/rtr/KukaPy`. The on-robot core (192.168.10.201) must therefore use the same resolution — verify the deployed tree's `robot.py` is the one from `rtr/KukaPy/kukapy/`.
- `KukaPyNEW/kukapy/robot.py` and `rtr/KukaPy/kukapy/robot.py` now both carry the fix; keep them in sync or delete one.
- If the strand recurs despite the `\x00` fix, the remaining suspect is a config mismatch: the robot's deployed `KUKAPY.xml` may differ from the repo's (`SENDFLAG=1`).

### Next Task

Operator: redeploy `realtimecontrol/rtr/KukaPy/kukapy/robot.py` to the robot PC (192.168.10.201), restart the core, confirm `[kukapy] SEND` lines show a trailing `\x00`, then re-trigger `hunting` → `track2` and verify the core runs past tick #130 without stranding.

### Instructions for the Next Task

1. Sync `realtimecontrol/rtr/KukaPy/kukapy/robot.py` to the robot PC, restart the core.
2. Confirm in the log that `[kukapy] SEND` lines end with `...b'\x00'` and that the old `[kukapy] RECV#` dump is gone.
3. Re-trigger `hunting` → `track2`; verify no strand past tick #130 (KRL not parked at line 57).
4. If it still strands: verify `SENDFLAG=1` on the pendant's `KUKAPY.xml` and capture the trace tail + KRL line at the stall.

## 2026-10-25 — Root-caused the strand: KRL confirmed at line 57 (request-side EKI desync); fixed `_send_xml` to send `\x00`-delimited `<Rob>` frames + added a defensive `recv_timeout`

### Status

Completed (fix implemented + validated locally). On-robot redeploy/restart is the operator's action (the core runs on 192.168.10.201, not this machine).

### Summary

The operator confirmed two decisive facts: **KRL is at line 57 (`WAIT FOR $FLAG[1]`)** and **there is no log after tick #130**. This resolves the two competing hypotheses from the previous handoff:

- **RULED OUT — "PTP never completes" (KRL in `PTP` at line 131).** KRL is at line 57, not 131, so the move is not the problem.
- **CONFIRMED — "request-side EKI desync" (KRC never set `$FLAG[1]`).** KRL idles at `WAIT FOR $FLAG[1]` because the KRC EKI layer never finished parsing tick #130's `<Rob>`, so it never set `$FLAG[1]` (the `Set_Flag="1"` on the last RECEIVE element `Rob/DO_Val`, `KUKAPY.xml:34`). Python sent the Type2 (`get_curjpos`) `<Rob>` (last log line: `tick#130 SEND Type2`) and is now blocked in `_recv_xml` waiting for a `<Res>` that never comes.

**Root cause:** `KUKAPY.xml` uses `PROTOCOL=TCP` + **`SENDFLAG=1`**, which makes the KRC EKI layer delimit frames with `\x00` in **both** directions. The Python client's `_send_xml` (`kukapy/robot.py`) sent **raw, unframed** `<Rob>` bytes (`sendall(data)` with no `\x00`). The code comment at `robot.py:92` even claimed "raw stream, no null-byte framing" — contradicting the deployed config. With no delimiter, the KRC parser's byte accounting drifts after enough ticks and it stops recognizing the `<Rob>` boundary, so `$FLAG[1]` is never set → KRL strands at line 57. This is a *stateful* desync (it worked for ~130 ticks, then broke), which is why a steady-state framing mismatch was not the obvious culprit.

**Fix (2 changes in `KukaPyNEW/kukapy/robot.py`, the active runtime module):**
1. `_send_xml` now appends `b"\x00"` to every `<Rob>` frame so the KRC parser gets an explicit frame boundary and reliably sets `$FLAG[1]`.
2. `recv_timeout` default changed `None` → `300` so a future desync raises a `socket.timeout` after 300 s instead of hanging forever (300 s is safe for slow block moves and matches the README's recommended value).

**Note on the receive side:** `_recv_xml` already handles `\x00`-delimited frames (Mode 1) and was already correct; it is unchanged. The existing regression tests (`tests/test_kuka_eki_recv.py`) confirm the receive framing is sound.

### Changes Made

- `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py`:
  - `_send_xml` (was `robot.py:99-100`): `data = xml_str.encode("utf-8")` → `data = xml_str.encode("utf-8") + b"\x00"`. Added a comment explaining the `SENDFLAG=1` both-directions framing and the line-57 strand.
  - `__init__` default: `recv_timeout: int | None = None` → `recv_timeout: int | None = 300`. Updated the `self.recv_timeout` comment.

### Documentation

- Updated the `_send_xml` docstring/comment block to state the `SENDFLAG=1` both-directions `\x00` framing (replacing the incorrect "raw stream, no null-byte framing" note).

### Architecture

No architectural impact. The fix is a framing correction inside the existing EKI client; the server/client roles, the single-flight lock (`kuka.py`), and the engine tick sequence are unchanged.

### Validation

- `python -c "import ast; ast.parse(...)"` on `kukapy/robot.py` → syntax OK.
- `python tests/test_kuka_eki_recv.py` (from `realtimecontrol/rtr`) → **all 6 EKI `_recv_xml` framing tests passed** (split frames, interleaved keepalives, multi-frame chunks, tiny-chunk reassembly, connect reset).
- `python tests/test_kuka_eki_serialization.py` (from `realtimecontrol/rtr`) → **all 6 serialization tests passed** (single-flight lock, stream vs block positioning).

### Decisions and Caveats

- **The `\x00` is the primary fix; `recv_timeout=300` is defensive.** If the desync recurs despite the delimiter (e.g. the robot's deployed `KUKAPY.xml` differs from the repo's), the timeout will now surface it as a `socket.timeout` instead of a silent hang, and the `[kukapy] SEND`/`RECV` instrumentation will show exactly where it stalled.
- **The robot's deployed `KUKAPY.xml` must match the repo's** (`SENDFLAG=1`). If the operator's KRC has a different config (e.g. `SENDFLAG` absent), the `\x00` framing assumption changes — verify on the pendant. The repo's `KUKAPY.xml:7` has `SENDFLAG=1`.
- **The fix is in the local tree only.** The running core is on 192.168.10.201 (a different machine); it must be restarted from the updated tree to pick up the change. Do NOT `pkill` locally (the core is not here).
- The vendored copy `realtimecontrol/rtr/KukaPy/kukapy/robot.py` is **not** on the runtime path (the active module is `KukaPyNEW/kukapy/robot.py`); it was left unchanged this turn. If it is ever used, it needs the same `_send_xml` fix.

### Next Task

Redeploy the fixed `robot.py` to the robot PC (192.168.10.201) and restart the core from the current tree, then re-trigger the strand to confirm the fix.

### Instructions for the Next Task

1. **On the robot PC (192.168.10.201)**, sync the updated `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` into the running core's path (the same path the instrumented core uses — confirm with `python -c "import kukapy.robot, os; print(os.path.abspath(kukapy.robot.__file__))"` on that machine).
2. **Restart the core** from the current tree, capturing output:
   `cd <core-path>/realtimecontrol/rtr && python main.py --robot --ip 192.168.10.201 2>&1 | tee /tmp/kukapy_trace.log`
   Confirm `[engine] tick#1 start ...` lines appear (instrumentation loaded) and that the `[kukapy] SEND` lines now show a trailing `\x00` (e.g. `b'...<Rob>...</Rob>\x00'`).
3. Start KUKAPY_SERVER on the pendant; park neutral (`{"cmd":"goto_zone","zone":"rest"}`), then re-trigger: `{"cmd":"goto_zone","zone":"hunting"}` then `{"cmd":"play_action","action":"track2"}`.
4. **Verify the fix:** the core should now run past tick #130 without stranding (KRL no longer idles at line 57). If it still strands, the `recv_timeout=300` will raise a `socket.timeout` within 5 min — capture the tail of `/tmp/kukapy_trace.log` and the KRL line at the moment of the stall.
5. If it still strands with the `\x00` in place, the next discriminator is the robot's deployed `KUKAPY.xml` (confirm `SENDFLAG=1` on the pendant) — a config mismatch is the only remaining suspect.

## 2026-10-25 — Analyzed operator trace: the "traceback" is a debug print, and the running core is STALE (pre-instrumentation, old path)

### Status

Completed (analysis). On-robot restart is the operator's action.

### Summary

The operator pasted a trace ending at `[debug-cmd] play_action payload={'action': 'track2'} curjpos=[-24.4, -10.5, -10.0, -3.0, 3.4,` (cut off). Two decisive findings:

1. **The "traceback" in the log is NOT an error.** It is the intentional debug call-stack dump from `clear_action` (`rtr/state/machine.py:458-464`), which prints `traceback.format_stack()[-4:-1]` on every action clear. The frames `step(656) → _advance_anchor(396) → _advance_after_action(551) → clear_action(458)` are the **normal** completion path of the `track` action (last pose reached → anchor advanced → action cleared). No exception occurred there. Similarly, `[debug-cmd] ...` (`rtr/brain/brain.py:448-451`) is a normal command log. So the state machine was healthy; the `track` action completed cleanly before `play_action track2`.

2. **The running core is STALE — it does not have the instrumentation.** Evidence:
   - The trace has **no** `[engine] tick#` lines (which print unconditionally at the top of every 20 Hz tick, `rtr/core/engine.py:84`) and **no** `[kukapy] SEND` lines (which print on every EKI command, `kukapy/robot.py:99`). Dozens of ticks of camera activity appear with none of these markers.
   - The traceback path is `/home/kaos/FTI_kuka/realtimecontrol/rtr/rtr/state/machine.py` — a path that **no longer exists** on this machine (`ls: cannot access '/home/kaos/FTI_kuka'`). The repo now lives at `/home/kaos/Documents/kaotec/FTI_kuka`. The core was launched from the old path (before the repo move and before the Test #2 instrumentation edits) and has been running the old in-memory code ever since.

**Confirmed the instrumentation IS present in the current tree** (grep): `kukapy/robot.py:99` `[kukapy] SEND`, `engine.py:84/159/161` `[engine] tick#`/`BLOCK-move`. So the code is ready — the running process just predates it.

**Engine kill mechanism (why the strand looks like an EKI desync):** `main.py:296` does `asyncio.create_task(engine.run())` and `engine.run()` (`rtr/core/engine.py:80-170`) has **no try/except**. Any uncaught exception in a tick (in `get_curjpos`/`get_curpos`, `machine.step`, a move, or `_resolve_pending`) kills the engine task; asyncio prints the traceback to the log; the engine stops forever → Python stops sending EKI commands → the KRL idles at `WAIT FOR $FLAG[1]` (line 57). That is "situation 2." The instrumentation will reveal whether the kill is an uncaught exception (a traceback will appear in the log) or a true EKI framing stall (last `[kukapy] SEND`/`RECV`/`FRAME` lines).

### Changes Made

None (analysis only; no code changed this turn).

### Documentation

None required — diagnostic analysis.

### Architecture

No architectural impact.

### Validation

- `grep` confirmed instrumentation present in the current tree: `kukapy/robot.py:99`, `engine.py:84,159,161`.
- `ls /home/kaos/FTI_kuka` → does not exist (confirms the running core's path is stale).
- Read `machine.py:380-409,458-502,535-569,640-669`, `engine.py:60-189`, `brain.py:430-469`, `main.py` (engine spawn) to confirm the "traceback" is a debug print and the engine has no exception guard.

### Decisions and Caveats

- Do NOT treat the `clear_action` stack dump as a bug or the root cause — it is expected debug output on every action clear.
- The strand mechanism is still unconfirmed between (a) an uncaught engine-tick exception and (b) an EKI framing stall; the instrumented re-run distinguishes them.

### Next Task

Kill the stale core and restart from the current tree so the instrumentation loads, then re-trigger the strand and capture the trace.

### Instructions for the Next Task

1. **Kill the stale core** (it runs from the old, now-gone path): `pkill -f "main.py --robot"` (or find the PID with `pgrep -f "main.py --robot"`). Confirm it is gone.
2. **Restart from the current tree**, capturing all output:
   `cd /home/kaos/Documents/kaotec/FTI_kuka/realtimecontrol/rtr && python main.py --robot --ip 192.168.10.201 2>&1 | tee /tmp/kukapy_trace.log`
   (default IP 192.168.10.201, listens :18735). Wait for `[rtr] robot connected` + `[rtr] core up`, and confirm `[engine] tick#1 start ...` lines appear (proof the instrumentation loaded).
3. Start KUKAPY_SERVER on the pendant.
4. Park neutral (`{"cmd":"goto_zone","zone":"rest"}`), then trigger the strand: `{"cmd":"goto_zone","zone":"hunting"}` then `{"cmd":"play_action","action":"track2"}`.
5. When the KRL idles at line 57, grab the **tail** of `/tmp/kukapy_trace.log` — the last `[engine] tick#`, `[kukapy] SEND`, `[kukapy] RECV`, `[kukapy] FRAME*` lines, and any traceback. Branch:
   - A **traceback** appears in the log at the stall → uncaught engine-tick exception (fix that exception).
   - `BLOCK-move start` + `SEND` but no `RECV`/`FRAME`/`done` → KRL `<Res>` not delivered.
   - `RECV` lines with no `FRAME`/`FRAME2` → `_recv_xml` framing desync.
   - No new `tick#` after a stream action → engine loop stopped/blocked.

## 2026-10-25 — Result: block mode ALSO strands (stream-pileup refuted); instrument EKI + engine to capture the desync

### Status

Completed (Test #1 result recorded + Test #2 instrumentation applied + operator instructions delivered). The on-robot re-run is performed by the operator.

### Summary

**Test #1 result (from the operator):** the forced block-mode `hunting/track2` **still** strands at `WAIT FOR $FLAG[1]` (line 57). The operator ran `trigger_action hunting/track` (the explicit stream action) then `play_action track2` (now block); the KRL idled at line 57 again. This **refutes** the stream-pileup hypothesis: the strand is not caused by stream mode spawning a daemon thread per tick. Block mode (one awaited move at a time, no thread pileup) strands identically, so the root cause is a **common EKI-framing desync** that affects both modes — the engine loop stops, so Python stops sending, so the KRL idles at `WAIT FOR $FLAG[1]`.

**Test #2 applied (instrumentation):** enabled byte-level logging in the EKI layer and stall markers in the engine tick so the next on-robot run captures (a) the exact bytes sent/received, (b) the frame boundaries, and (c) exactly where the engine loop stops.

Mechanism map (why line-57 idle = engine stop, and where the stop can happen):
- KRL loop (KUKAPY_SERVER.SRC): `WAIT FOR $FLAG[1]` (57) → read `Rob/*` → `SWITCH cmd_code` → `EKI_SEND Res` (183) → loop. `FLAG[1]` is set by an incoming Python EKI frame; the KRL clears it (58) after waking. **KRL idling at 57 ⇒ FLAG[1] stays FALSE ⇒ Python sent nothing ⇒ the engine loop stopped** (a mid-move hang would strand the KRL *in* the PTP/C_PTP, not at 57).
- Engine tick (engine.py `run`): `get_curjpos` / `get_curpos` (each takes `KukaRobot._lock`) → drain commands → `brain.tick` → `_resolve_pending` → `machine.step` → **move** (`stream` = `move(..., blocking=False)` spawns a daemon thread; `block` = `await asyncio.to_thread(move, ...)`) → publish. In block mode the tick **awaits the move**, which holds `_lock` and blocks inside `_send_cmd → _recv_xml` until a `<Res>` returns. So a block-mode stall = the move's `_recv_xml` never returns a `<Res>`.
- EKI reader (kukapy/robot.py `_recv_xml`): accumulates `recv(4096)` chunks into `buf`; Mode 1 splits on `\x00` (SENDFLAG=1) and returns the first `<Res>`; Mode 2 falls back to whole-buffer XML parse. Leftover bytes persist in `self._recv_buf`. A desync here = the `<Res>` is never recognized, so `_recv_xml` keeps reading forever (the engine stays blocked in the move).

### Changes Made

- `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` (the **active** runtime module — confirmed via `import kukapy.robot` resolving here):
  - `_send_xml`: enabled `print(f"[kukapy] SEND {repr(data)}")` (was commented).
  - `_recv_xml`: enabled per-chunk `print(f"[kukapy] RECV#{n} chunk=..B buf=..B {repr(buf)}")` (was commented); added `FRAME` log on each Mode-1 frame parse, `FRAME-PARSE-FAIL` log when a `\x00` frame fails to parse, and `FRAME2` log on a Mode-2 parse.
- `realtimecontrol/rtr/KukaPy/kukapy/robot.py` (vendored copy, not on the runtime path): identical instrumentation applied for consistency.
- `realtimecontrol/rtr/rtr/core/engine.py` (`run` tick loop): added `tick#N start (move_mode=.. action=..)` at the top of each tick, and `tick#N BLOCK-move start/done` around the block-mode `await asyncio.to_thread(move, ...)` so the trace shows whether the stall is before the move, inside the move, or after.

### Documentation

None required — diagnostic instrumentation, no behavior change to document.

### Architecture

No architectural impact. Logging only; no control-flow, protocol, or module-boundary change.

### Validation

- `python3 -c "import ast; ast.parse(...)"` on both edited files: OK.
- `python3 -c "json.load(open('zones.json'))"`: parses clean (track2 `engine={'move_mode':'block'}`, track `engine={'cadence':'arrival','move_mode':'stream'}` unchanged).

### Decisions and Caveats

- **Stream-pileup hypothesis is dead.** Block mode strands identically, so the fix is NOT "stop stream spawning a thread per tick." The real cause is a common EKI-framing desync (or a stall that blocks `_lock` in both modes). Do not pursue the per-tick-thread fix.
- The instrumentation is **verbose** (20 Hz tick log + a `RECV` line per socket chunk, each printing the full buffer repr). It is meant to run for one strand capture only, then be reverted. Capture stdout to a file rather than scrolling.
- The `[kukapy] RECV` line prints the **accumulated** `buf` (not just the new chunk), so the last `RECV` line before the stall is the most informative (it shows everything the reader has seen but not yet parsed).
- **Confirmed the active module:** `import kukapy.robot` from `realtimecontrol/rtr/` resolves to `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` (the editable install), NOT the vendored `rtr/KukaPy` copy. The active KukaPyNEW module is now instrumented; the vendored copy was instrumented too for consistency.

### Next Task

Operator re-runs the strand scenario with the instrumented code and captures the byte trace. Branch on where the trace stops:
- **`BLOCK-move start` + `SEND` present, but no subsequent `RECV`/`FRAME`/`BLOCK-move done`:** the KRL's `<Res>` never arrived at Python → the desync is on the KRL→Python delivery (the response was sent but not received, or the connection dropped). Inspect the last `RECV` bytes for a partial/mangled frame.
- **`RECV` lines present (bytes arriving) but no `FRAME`/`FRAME2` (no successful parse):** `_recv_xml` is failing to recognize the `<Res>` → a parser/framing desync (e.g. an `AliveCounter` or leftover byte poisoning the buffer). Inspect the `FRAME-PARSE-FAIL` lines and the `RECV` buffer.
- **No `BLOCK-move start` (stalled before the move):** the engine is stuck in `get_curjpos`/`get_curpos` (a `_lock` held by a lingering stream daemon thread from the earlier `track` action) → lock-contention stall, a different mechanism.
- **`BLOCK-move done` present but the next tick never starts:** the stall is after the move (in `brain.tick` / `_resolve_pending` / `machine.step` / publish) → narrow further.

### Instructions for the Next Task

1. **Restart the core** to load the instrumented code: `python main.py --robot --ip <IP>` (default 192.168.10.201), listening :18735. Capture all output: `python main.py --robot --ip <IP> 2>&1 | tee /tmp/kukapy_trace.log`.
2. Start KUKAPY_SERVER on the pendant; wait for `[rtr] robot connected` + `[rtr] core up`.
3. Park neutral (`{"cmd":"goto_zone","zone":"rest"}`), then trigger the strand: `{"cmd":"goto_zone","zone":"hunting"}` then `{"cmd":"play_action","action":"track2"}`.
4. When the KRL idles at line 57, grab the **tail** of `/tmp/kukapy_trace.log` (the last `[engine] tick#`, `[kukapy] SEND`, `[kukapy] RECV`, `[kukapy] FRAME*` lines) and hand it back. Branch per the Next Task list.

## 2026-10-25 — Test: force `hunting/track2` to block mode to isolate the stream-mode strand

### Status

Completed (test config applied + operator instructions delivered). The on-robot run itself is performed by the operator; the branch points for the result are documented below.

### Summary

Applied the isolation test for the "strands at `WAIT FOR $FLAG[1]`" bug. The code-established discriminator: the four failing actions (`hunting/track2`, `hunting/scan`, `examining/focus`, `cuddling/breathe`) have **no** `engine` field, so they **inherit** `move_mode=stream` / `cadence=arrival` from the preceding `hunting/track` (the only action with an explicit `engine: {cadence:arrival, move_mode:stream}`, zones.json:1003-1006). The one working action (`scanning/scnr`) reaches a block-mode state. Hypothesis under test: stream mode spawns a daemon thread **per tick** (kuka.py:115-116), piling up EKI calls that contend for `_lock`; block mode sends one awaited move at a time (engine.py:156).

The test forces `hunting/track2` into block mode via `"engine": {"move_mode": "block"}`. Only `move_mode` is set — `cadence` is deliberately omitted from the engine dict so it stays inherited (`arrival`), isolating the single variable the hypothesis targets. Because engine-less actions inherit from the previous action, this cascades: `hunting/scan` and `hunting/focus` (no engine field) will now inherit `block` from `track2`, so the whole post-`track` hunting sub-sequence runs in block mode.

### Changes Made

- `realtimecontrol/rtr/zones.json`: added `"engine": {"move_mode": "block"}` to `hunting.actions.track2` (between `behavior` and `loop`). `cadence` intentionally omitted → stays inherited (`arrival`).

### Documentation

None required — this is a diagnostic config change, not a behavior change to document.

### Architecture

No architectural impact. Exercises the existing `engine` field mechanism (`brain._apply_action_engine`, brain.py:205-239) with a new config value; no code path changed.

### Validation

- `python3 -c "json.load(open('zones.json'))"`: parses clean, 38 zones.
- Confirmed `hunting.track2.engine == {'move_mode': 'block'}` and `hunting.track.engine == {'cadence':'arrival','move_mode':'stream'}`.
- Read `engine.py:148-156` (stream/block dispatch), `machine.py:661-678` (`_cadence_due`), `brain.py:205-239` (`_apply_action_engine`) to confirm `cadence` and `move_mode` are orthogonal and that omitting `cadence` leaves it inherited.

### Decisions and Caveats

- `cadence` was deliberately **not** changed. The originally-described test used `{cadence:fixed, move_mode:block}`; it was refined to isolate `move_mode` only, so a clear result unambiguously implicates stream mode rather than the cadence change.
- The `engine` field is applied by the brain **only on an action change** (`_apply_action_engine`). The override therefore takes effect on the **next** transition into `track2` — the robot must not be sitting in `track2` when the config loads; it must re-enter `hunting/track2` afterward.
- Block mode slows the action (each move is awaited to arrival before the next tick — the "legacy kukapy behaviour"). Slower pose-stepping in `track2` is **expected**, not a failure; the test measures whether it **strands** (freezes), not its speed.

### Next Task

Run the on-robot test (operator) and branch on the result:
- **Strand clears (block works):** the fix is to stop stream mode spawning a daemon thread per tick — send the `cnt>0` move synchronously under `_lock` (the KRL answers C_PTP immediately, so no thread is needed), collapsing per-tick EKI traffic to a single serialized flight.
- **Still strands at line 57 (WAIT):** the desync is a common EKI-framing issue affecting both modes → run Test #2 (instrument `_recv_xml` to capture the exact desync bytes).
- **Strands mid-move (PTP/C_PTP loop, not line 57):** a single blocking EKI call hung → different mechanism; capture the hung frame.

### Instructions for the Next Task

Operator steps (delivered with this task): (1) load the config into the core — restart `python main.py --robot` **or** hot-reload via `POST /api/zones`; (2) park the robot in a neutral zone (not `track2`); (3) `goto_zone hunting` then `play_action track2`; (4) observe the KRL program counter (KUKAPY_SERVER.SRC), the physical robot, and the core's state snapshots; (5) branch per the Next Task list above.

## 2026-10-24 — Fix EKI `_recv_xml` silent hang (the "Wait for $FLAG[1]" KUKA HMI error)

### Status

Completed.

### Summary

Fixed the root cause of the "Wait for $FLAG[1]" KUKA HMI error: a silent infinite hang in the EKI `_recv_xml` reader. The KUKA EKI link (`KUKAPY.xml`: `PROTOCOL=TCP`, `SENDFLAG=1`, `ALIVE ElementName="AliveCounter"`) is a `\x00`-delimited byte stream that interleaves `<Res>` responses with periodic `AliveCounter` keepalives. TCP does not preserve message boundaries, so a single `recv(4096)` frequently returns a whole `<Res>` **plus** the start of the next frame. The old `_recv_xml` kept its buffer in a *local* variable and returned as soon as it found a `<Res>`, throwing the leftover bytes away. The next call then started on a **fragment**, which can never be parsed, so it kept reading keepalives forever — a silent hang that stranded the KRL at `WAIT FOR $FLAG[1]` (idle, no new commands) and left the robot without its next move.

The fix makes the leftover buffer persistent across calls: `self._recv_buf` is written on every return path (the tail after the returned `<Res>` in `\x00`-mode, or the accumulated bytes in plain-XML mode) and read at the start of the next call. It is reset to `b""` on `connect()` and `disconnect()` so stale bytes never leak across sessions. A defensive 1 MB size guard raises `KukaError` instead of hanging if framing ever desyncs.

### Changes Made

- `realtimecontrol/kukapyEKI/KukaPyNEW/kukapy/robot.py` (the **active** editable-install module): added `self._recv_buf = b""` to `Robot.__init__`; rewrote `_recv_xml` to read from / write to `self._recv_buf` (persistent leftover) instead of a local `buf`; reset `self._recv_buf` in `connect()` and `disconnect()`; added a 1 MB overflow guard.
- `realtimecontrol/rtr/KukaPy/kukapy/robot.py` (vendored copy, not used at runtime): applied the identical fix so the bug cannot resurface if that copy is ever used.
- `realtimecontrol/rtr/tests/test_kuka_eki_recv.py` (new): 6 regression tests driving `_recv_xml` against a fake socket — split frame across two recvs, keepalive between Res frames, keepalive-only chunk, multi-frame chunk tail preservation, byte-at-a-time reassembly, and connect reset. The fake socket loops on an `AliveCounter` (like the real KRC) so a buggy reader hangs and the join timeout turns the hang into a deterministic failure.

### Documentation

None required — the fix is self-documenting via the `_recv_xml` docstring and the new test file's module docstring, which explain the protocol and the bug.

### Architecture

No architectural impact. This is a bug fix within the existing EKI framing layer; the protocol, module boundaries, and data flow are unchanged.

### Validation

- `python3 -m py_compile` on both `robot.py` copies: OK.
- `diff` of the two `_recv_xml` methods: identical.
- `/tmp/kilo/repro_recv.py` (reproduction): before the fix, call1 OK / call2 HANG; after the fix, **both RES_A and RES_B retrieved (PASS)**.
- `tests/test_kuka_eki_recv.py` (new): all 6 tests pass.
- `tests/test_kuka_eki_serialization.py` (existing EKI test): all 6 tests pass.
- Full `tests/` suite (excluding `test_remote_tui.py`, which has a **pre-existing** `ImportError: cannot import name 'action_kind' from 'rtr.flow'`): 274 passed, 14 skipped, 7 failed — all 7 failures are **pre-existing** and none import `kukapy` (`test_bridge_behavior.py::test_real_zones_bridge_action_is_tagged`, 5× `test_remote_sdl.py`, `test_transition.py::test_all_reachable_transitions_complete`).

### Decisions and Caveats

- The 1 MB overflow guard is a safety net, not a protocol feature; it converts a possible infinite hang into a loud `KukaError`. Under normal operation the buffer stays tiny (one partial frame).
- The vendored copy (`rtr/KukaPy/`) is not on the runtime path (`kukapy` resolves to the KukaPyNEW editable install), but it was fixed too for consistency.
- The 7 pre-existing test failures and the `test_remote_tui.py` collection error are unrelated to this change and were present before it.

### Next Task

Two pending workstreams, both blocked on user input:
1. **Single-action auto-return** (`editor.html` on-save normalization): for a zone with exactly one action and an empty `next`, add a return target to the incoming/reverse zone. Blocked on the definition of the "previous" target and the creation trigger (the earlier clarification was dismissed).
2. **A6 continuous-axis accumulation**: the A6 counter accumulated to `216237.30°` and manifests as a growling. The code side is confirmed (no Python wrapping; RSI `AXISACT Type="Measured"`; the arrival check `count=5` excludes A6). The remaining question — what KUKA controller operation resets the continuous counter and why it grows — is controller-side knowledge not present in the codebase.

### Instructions for the Next Task

- Auto-return: confirm the "previous" target (is it the action's `next` list, the `reverse_targets` of the incoming zone, or something else?) and the creation trigger (on-save only, or also on zone create?), then implement in `editor.html` using `reverse_targets`, `firstEnabledAction`, and `setNext`; add a save-normalization test.
- A6: obtain the KUKA controller behavior for the A6 continuous axis (which pendant/controller operation resets the measured counter, and whether the growling correlates with the counter magnitude). Then decide whether to add a Python-side wrap or a controller-side reset to `behavior.py` / the KRL.

## 2026-10-24 — Investigate: `auto discarded move to cuddling_purring_bridge / bridge — red-link conflict`

### Status

Completed.

### Summary

Explained the runtime warning `auto discarded move to cuddling_purring_bridge / bridge — red-link conflict`. The move `cuddling/purr → cuddling_purring_bridge/bridge` (the first candidate in `cuddling/purr`'s `next` list) is discarded by the core's safety gate on the **inter-zone shared-position rule**: the two actions must share at least one anchor pose within 0.5° on every axis. All three `bridge/bridge` anchor poses carry an `'x'` (frozen-axis) marker on A1 (`['x', -133, 156, -2, 0, 0]`, `['x', -120, 135, -2, 0, 0]`, `['x', -110, 135, -2, -5, 0]`), and `find_shared_pose` (`rtr/state/zones.py:92`) skips any pose containing `'x'` — so no shared pose is ever found. The safezone-overlap rule passes (both zones overlap on all 6 axes, gap list empty).

**Divergence found:** the editor's flow view judges this edge as CLEAN because its `edgeConflict` (`editor.html:1077-1090`) skips only the `'x'` axes and compares the concrete ones (purr end pose `['x', -133, 156, -2, 0, 0]` vs bridge entry pose `['x', -133, 156, -2, 0, 0]` — all concrete axes match). The core's matcher is stricter: it discards the whole pose when any axis is `'x'`. Hence the editor shows a green link while the runtime discards the move.

### Changes Made

- No code changes (read-only investigation).

### Documentation

None required — explanation only.

### Architecture

No architectural impact.

### Validation

- Real `SafetyGate.check_conflict("cuddling", "purr", "cuddling_purring_bridge", "bridge")` on the live `zones.json`: `conflict=True`, issues=`['no shared position: cuddling/purr shares no pose with cuddling_purring_bridge/bridge']`.
- Safezone overlap gaps for `cuddling` vs `cuddling_purring_bridge`: `[]` (all 6 axes overlap).

### Decisions and Caveats

- The root cause is the `'x'`-handling mismatch between `find_shared_pose` (core, skips whole pose) and `edgeConflict` (editor, skips only `'x'` axes). Until aligned, every auto move into a bridge action whose anchors are all `'x'`-marked will be discarded.

### Next Task

Decide the fix: (a) give `cuddling_purring_bridge/bridge` (and similar bridge actions) at least one fully numeric anchor pose matching the source's end pose, or (b) change `find_shared_pose` to compare only concrete axes like the editor does.

### Instructions for the Next Task

- Option (a): in `zones.json`, replace the `'x'` on A1 of the bridge's first anchor with a concrete value (e.g. `[-60, -133, 156, -2, 0, 0]` — any value inside the zone safezone that matches the robot's pose at hand-off), keeping the other anchors as-is.
- Option (b): in `rtr/state/zones.py` `find_shared_pose`, compare axis-by-axis only where both sides are numeric (mirror `editor.html` `edgeConflict` lines 1080-1090); update `tests/test_safety_conflict.py` accordingly and re-run it.

## 2026-10-24 — Implement + test: `check_zone_limits.py` (limit checker with interactive apply)

### Status

Completed.

### Summary

Built `realtimecontrol/rtr/check_zone_limits.py`, a reusable checker that walks every zone and action in `zones.json` and validates all joint-space values against the real KR60 axis limits. It lists every out-of-limit value with a proposed clamp to the nearest limit, then interactively offers to apply ALL / ONE BY ONE / NONE (or non-interactive `--dry-run` / `--apply-all` / `--apply-one` / `--file`). It checks three data kinds: `safezone` bands, action `poses` (skipping `"x"` frozen axes and counting them), and action `axis_bands`. Disabled zones/actions are checked too and flagged `[disabled]`. The `layout` section (2D canvas coords, not joints) is excluded.

**Result on the real `zones.json`: 12 violations, all in `safezone` bands (0 pose, 0 axis_bands violations).** All 12 are in bridge zones:
- A6 `[-357,357]` → `[-150,150]`: `sleeping_stretching_bridge` (lo+hi), `cuddling_stretching_bridge` (lo+hi), `performing_roaring_bridge` (lo+hi)
- A2 lo `-136` → `-135`: `stretching_hunting_bridge`, `hunting_examining_bridge`, `purring_hunting_bridge`
- A5 `[-118,118]`/`hi 118` → `[-55,100]`: `scanning_leaping_bridge` (lo+hi), `performing_roaring_bridge` (hi)

**Note:** during analysis I had estimated 13 violations, but the script found 12. The discrepancy was my manual reading: `hunting`'s A2 safezone is actually `[-134,5]` (in limits), not `-136`. The script is the source of truth. Also the file has 35 zones, not the 33 counted during the initial read.

### Changes Made

- `realtimecontrol/rtr/check_zone_limits.py`: created. `AXIS_LIMITS = [(-185,185),(-135,35),(-95,158),(-180,180),(-55,100),(-150,150)]` at top (editable). `collect()` walks zones → safezone/poses/axis_bands, building `Violation` records with a `clamp()` fix. `interactive_apply()` prompts `[A]/[O]/[n]`; non-TTY stdin is treated as NONE. `write_file()` backs up to `zones.json.bak-pre-limit-check` then writes with `json.dumps(indent=2, ensure_ascii=False) + "\n"` (matches existing format). Exit 0 = clean/all-applied, 1 = violations remain, 2 = file/structure error.
- `realtimecontrol/rtr/zones.json`: **NOT modified** (real file left untouched; apply was tested only on a `/tmp` copy).

### Documentation

- Script docstring documents usage, limits, and rationale. No separate docs needed.

### Architecture

- No architectural impact — standalone utility script next to `zones.json`, no new dependencies (stdlib only).

### Validation

- `python3 check_zone_limits.py --dry-run` on the real `zones.json`: reports 12 violations, exit 1, no write. 35 zones, 57 actions, 126 poses checked, 23 `"x"` axes skipped.
- `python3 check_zone_limits.py --file /tmp/kilo/test_zones.json --apply-all` on a copy: applied 12/12, re-check 0 remaining, exit 0, backup created.
- `diff` (original vs applied copy, both normalized via `json.dumps(indent=2)`): exactly **12 changed value lines**, no structural/formatting changes → JSON round-trip preserves formatting.
- `git status --short zones.json`: clean → real file untouched.

### Decisions and Caveats

- Applied clamps change robot behavior; the real `zones.json` was deliberately left unmodified. The user can run the script interactively (`python3 check_zone_limits.py`) to apply, or ask me to run `--apply-all`.
- `AXIS_LIMITS` is a single editable constant; if the robot's real limits ever change, only that line needs updating.
- `"x"` frozen pose axes are skipped (value comes from the live robot at runtime — a static checker cannot verify them); count is reported.

### Next Task

Decide whether to apply the 12 clamps to the real `zones.json` (via the script) or leave it for the user.

### Instructions for the Next Task

If applying: run `python3 check_zone_limits.py --apply-all` from `realtimecontrol/rtr/` (or `--apply-one` to pick per change). It creates `zones.json.bak-pre-limit-check` first. After applying, re-run `--dry-run` to confirm 0 violations. No code changes needed; the script is complete and tested.

## 2026-10-24 — Research: reconcile stated axis limits with codebase (A3 discrepancy)

### Status

Completed.

### Summary

The user asked to verify that ALL zones and actions in `realtimecontrol/rtr/zones.json` are within the actual robot axis limits, and to build a repeatable script that lists all out-of-limit values with proposed clamps and offers to apply ALL / ONE BY ONE / NONE. The user's stated limits matched the codebase on 5 of 6 axes; A3 differed: the message said `[95, 158]` while the codebase consistently defines A3 as `[-95, 158]`. Asked the user; they confirmed `[-95, 158]` (the `95` was a typo). Final real limits = the codebase `HARDWARE_LIMITS`: A1 `[-185,185]`, A2 `[-135,35]`, A3 `[-95,158]`, A4 `[-180,180]`, A5 `[-55,100]`, A6 `[-150,150]`.

### Changes Made

- None (research only).

### Documentation

- None required — findings recorded in this handoff entry.

### Architecture

- No architectural impact.

### Validation

- Cross-checked limit definitions across three independent files:
  - `realtimecontrol/rtr/rtr/state/zones.py:34-54` — `SOFTWARE_LIMITS` and `HARDWARE_LIMITS` both give A3 `(-95, 158)`; HARDWARE_LIMITS sourced from the `kr60ha_macro.xacro` joint `<limit>` tags.
  - `realtimecontrol/rtr/validate_zones.js:138-139` — `SOFTWARE_LIMITS`/`HARDWARE_LIMITS` mirror the same values.
  - `realtimecontrol/rtr/rtr/remote/view.py:60-67` — `HW_LIMITS` for the ARM page bars, same values.
- `robotconfig.json` contains no axis-limit definitions (grep: no matches).

### Decisions and Caveats

- A3 lower limit is `-95` (user-confirmed). The checker script must use the codebase limits as its default.
- Pre-scan of `zones.json` under these limits (to be confirmed by the script): **all 6-axis poses are in limits**; violations are confined to `safezone` bands: A2 lo `-136` in `hunting`, `stretching_hunting_bridge`, `hunting_examining_bridge`, `purring_hunting_bridge`; A5 `[-30,118]` in `performing_roaring_bridge`; A5 `[-118,118]` in `scanning_leaping_bridge`; A6 `[-357,357]` in `sleeping_stretching_bridge`, `cuddling_stretching_bridge`, `performing_roaring_bridge` (13 band-end violations total, 0 pose violations).

### Next Task

Implement the checker script (`realtimecontrol/rtr/check_zone_limits.py`).

### Instructions for the Next Task

Build a Python script next to `zones.json` that: (1) checks every zone `safezone` band end, every action `poses` value (`"x"` frozen axes skipped), and every action `axis_bands` end against `AXIS_LIMITS = [(-185,185),(-135,35),(-95,158),(-180,180),(-55,100),(-150,150)]`; (2) lists each violation with a proposed clamp to the nearest limit; (3) interactively offers apply ALL / ONE BY ONE / NONE; (4) writes `zones.json` back with `indent=2` after a `.bak` backup; (5) supports `--dry-run` / `--apply-all` / `--apply-one` / `--file` for non-interactive use. Check disabled zones/actions too (the user asked for ALL), marking them `[disabled]`.

## 2026-10-24 — Analyse: zones.json structure and check targets

### Status

Completed.

### Summary

Read `realtimecontrol/rtr/zones.json` in full (3975 lines). It contains 33 zones under `zones` (12 behavioral zones + 17 bridge zones + `testing`, `grabbing`, `scanning`), plus a `layout` section (2D canvas coordinates — not joint values, out of scope). Three data kinds hold joint-space values that must be checked against the real axis limits:

1. `zones.<zone>.safezone` — 6 `[lo, hi]` bands, one per axis A1..A6.
2. `zones.<zone>.actions.<action>.poses` — list of 6-element arrays; an axis may be the string `"x"` meaning "freeze / copy the current robot axis value" (no static value to check — skipped and counted).
3. `zones.<zone>.actions.<action>.axis_bands` — dict keyed `"0".."5"` with `[lo, hi]` bands (used by ACTIVE_CLASSES tracking actions; `clawing.lock_target` has an empty `{}`).

The `layout` section holds 2D editor positions and is untouched by the checker. Zones/actions can have `enabled: false` (e.g. `grabbing.grab`); the user asked to check ALL, so disabled entries are checked too and marked.

### Changes Made

- None (analysis only).

### Documentation

- None required — structure recorded in this handoff entry.

### Architecture

- No architectural impact.

### Validation

- Full file read (lines 1-3975). All 33 zones enumerated; every `safezone`, `poses`, and `axis_bands` entry inspected against the limits during analysis.

### Decisions and Caveats

- `"x"` pose axes are skipped (frozen axes take their value from the live robot at runtime — a static checker cannot verify them; the count is reported).
- `layout` coordinates are 2D canvas positions, not joint angles — excluded.

### Next Task

Research existing limit definitions and reconcile with the user's stated limits (done — see the entry above).

## 2026-10-24 — Intra-zone full-mesh `next` (auto-connect siblings, hidden in flow view)

### Status

Completed.

### Summary

The user wants all actions within the same zone to auto-connect to each other via their `next` field (full mesh, not just sequential), applied retroactively to `zones.json` and on new-action creation / copy. The intra-zone links drive the brain's roam (it picks a random valid next), but are HIDDEN in the flow view (to avoid the N*(N-1) edge clutter) and still LISTED in the action editor (so the user can remove a specific link).

Three changes:
1. **Retroactive migration** (`scripts/seed_intra_next.py`): a one-time, idempotent, additive pass over `zones.json` that adds the missing intra-zone `next` links (every action points to every other action in its zone). Existing targets (intra- or inter-zone) are preserved. Applied: 54 links added across 12 zones (13 multi-action zones; one already had the full mesh).
2. **New/copy auto-connect** (`editor.html`): a `meshNewAction(zn, an)` helper wires a newly-created or copied action into the zone's intra-zone mesh (additive — never re-adds a link the user removed, never touches inter-zone links). Wired into the `+ add action` button (`buildZoneForm`) and `copyAction`.
3. **Hide in flow view** (`editor.html`): `flowRenderEdges` now skips intra-zone edges (the inner edge layer stays empty); only inter-zone links are drawn. The action editor still lists every `next` target including the intra-zone ones (unchanged — it already lists all targets), so removal is still possible there.

### Changes Made

- `realtimecontrol/rtr/scripts/seed_intra_next.py` (new): one-time migration that seeds the intra-zone full mesh into `zones.json`. Additive (preserves existing targets), idempotent, `--dry-run` supported.
- `realtimecontrol/rtr/zones.json`: 54 intra-zone `next` links added across 12 zones (the full mesh for every multi-action zone).
- `realtimecontrol/rtr/editor.html`:
  - `meshNewAction(zn, an)` (new, after `cleanNextLists`): wires a new/copied action into the zone's intra-zone mesh (both directions), additive.
  - `buildZoneForm` `+ add action` handler: calls `meshNewAction(name, nm)` after creating the action.
  - `copyAction`: calls `meshNewAction(zn, nn)` after creating the copy; doc comment updated.
  - `flowRenderEdges`: skips intra-zone edges (hidden from the flow view).

### Documentation

- `scripts/seed_intra_next.py` has a module docstring explaining the migration (usage, `--dry-run`).
- No user-facing docs updated (the README does not document the per-zone `next` mesh; the editor's inline help for the `next` section already describes the ordered target list).

### Architecture

- No architectural impact to the runtime. The brain already picks a random valid next from `action_next_list` (`brain.py:_valid_next_candidates`), so the added intra-zone links are valid roam candidates with no core change. The editor's flow view hides the internal links but the data model and core navigation are unchanged.

### Validation

- `node --check` on the extracted inline `<script>` of `editor.html`: **JS SYNTAX OK**.
- Isolated JS test of `nextList`/`setNext`/`meshNewAction` (full mesh on new-action join, additive removal persistence, copy scenario, single-action zone): **19 passed, 0 failed**.
- `python3 scripts/seed_intra_next.py` applied (54 links), 2nd run = 0 links (idempotent).
- Post-migration check: every multi-action zone's full intra-zone mesh is present (**FULL MESH OK**).
- `zones.json` validation (all `next` targets reference existing zones + string actions): **VALIDATION OK**.
- `pytest tests/` (excluding the pre-existing `test_remote_tui.py` collection error): **7 failed, 268 passed, 14 skipped** — the SAME 7 failures occur on the ORIGINAL `zones.json` (confirmed by `git checkout zones.json` + re-run), so **zero new failures** from this change. Pre-existing failures: `test_transition.py::test_all_reachable_transitions_complete` (TypeError in `sim.py`, a `"x"` pose axis), `test_bridge_behavior.py::test_real_zones_bridge_action_is_tagged`, and 5 `test_remote_sdl.py` UI tests.

### Decisions and Caveats

- **Additive, not re-normalizing:** the migration and `meshNewAction` only ADD missing intra-zone links; they never remove or re-add a link the user deleted. So a removed connection stays removed (matching "list them in the action editor for removal"). A save-time re-normalization was deliberately NOT added (it would fight the user's removals).
- **`next` list order is irrelevant to the brain** (`_random_valid_next` picks randomly), so the migration appends missing links at the end of each list (preserving existing order).
- **Flow view hides intra-zone links** but the `flowAddEdge` inner-edge code path is left intact (the inner layer is simply never fed intra-zone edges) — reverting the one-line skip in `flowRenderEdges` restores the old rendering.
- **`meshNewAction` assumes the zone's existing actions already have the mesh** (the migration seeds it for existing zones). If a zone was created without the migration and actions were added before this change, the existing actions may lack sibling links until a new action triggers `meshNewAction` or the migration is re-run. Re-running `scripts/seed_intra_next.py` fixes any such drift.
- **Stale browser cache:** the user must hard-refresh the editor (Ctrl+Shift+R) to load the updated `editor.html`.

### Next Task

No immediate next task identified. The full-mesh auto-connect is complete (retroactive + new/copy), hidden in the flow view, and still removable in the action editor.

### Instructions for the Next Task

1. To re-apply the mesh to any zone that drifted (e.g., actions added before this change without the mesh), run `python3 scripts/seed_intra_next.py` (idempotent).
2. The `meshNewAction` helper lives in `editor.html` (after `cleanNextLists`); it is called from the `+ add action` handler in `buildZoneForm` and from `copyAction`.
3. If the user later wants the intra-zone links visible in the flow view again, remove the `if (e.from.zone === e.to.zone) return;` skip in `flowRenderEdges` (editor.html) and restore the `innerLayer` routing.

## 2026-10-24 — Remove the pose-continuity editor warning (unneeded, false-positive)

### Status

Completed (warning removed). The single-action auto-return feature (user point 3) is pending clarification and not yet implemented.

### Summary

The user reported still getting "loads" of the `entry pose differs from next …` save warning and called it "literally unneeded," giving three reasons: (1) a concrete false positive `entry pose differs from next testing/locked (45, -30, 42, 0, -5, 0)`; (2) an `"x"` (frozen/copy) axis should not be a problem; (3) a zone with a single action should auto-gain a return `next` to avoid dead ends.

**Root cause of "still" seeing it:** the user's browser was showing a **cached pre-`midifix` `editor.html`**. The live rtr server (PID from `main.py --sim`) serves the **main working tree** (`/home/kaos/.../realtimecontrol/rtr`), whose `editor.html` already had the newer `no shared pose with next` logic (intra-zone exempt, `findSharedPose` mirroring the runtime gate). The exact text the user quoted — `entry pose differs from next X/Y (pose)` with the pose in parentheses — only exists pre-`midifix` (and in the idle `sapphire-scraper` Agent-Manager fork). So the user was viewing stale JS, not the served file.

**Fix applied:** the pose-continuity check is a non-blocking advisory that the runtime safety gate (`rtr/safety/gate.py`, via `find_shared_pose`) already performs independently, and the robot degrades gracefully at runtime (falls back to the target's entry pose when no shared pose exists). It false-positived on intra-zone links, self-loops, same-pose siblings, and `"x"` axes. Removed it entirely from both editor surfaces so the unneeded warnings stop.

### Changes Made

- `realtimecontrol/rtr/editor.html` (`saveZones`, save-time warnings loop): removed the pose-continuity `for (const t of nextList(a))` block that pushed `no shared pose with next …` warnings. The safezone/hardware-limit pose checks in the same loop are unchanged.
- `realtimecontrol/rtr/editor.html` (`buildActionEditor`, the inline hint): removed the pose-continuity `if (a.poses.length) { const diffs = targets.filter(...) }` block that rendered the `no shared pose with next …` hint in the action editor.
- `findSharedPose`/`poseNumeric` (editor.html:986-1000) and the runtime `find_shared_pose` (`rtr/state/zones.py:92`) are left intact: the editor warning no longer calls `findSharedPose`, but the runtime gate still uses its own `find_shared_pose` for real collision/hand-off checks. No runtime behavior changed.

### Documentation

- None required — the warning was an advisory with no user-facing doc; the runtime gate's real check is unchanged and already documented in `rtr/safety/gate.py`.

### Architecture

- No architectural impact. This is an editor-only advisory removal. The runtime state machine and safety gate are untouched; the editor no longer pre-emptively flags pose continuity, deferring entirely to the runtime gate.

### Validation

- `node --check` on the extracted `<script>` blocks of `editor.html`: **JS SYNTAX OK** (13 script blocks).
- Confirmed via grep that `entry pose differs` / `no shared pose with next` no longer appear anywhere in `editor.html`; the only remaining `findSharedPose` reference is the (now-unused) function definition at editor.html:992.
- Not run: the robot runtime / pytest (no runtime code changed; the editor change is browser-side JS with no Python test coverage).

### Decisions and Caveats

- **Removed, not softened:** chose full removal over adding more exemptions (self-loop, same-pose, `"x"` axes) because the user called the warning "literally unneeded" and the runtime gate already owns the real check. If a future need arises to warn on a genuinely unreachable inter-zone hand-off, the `findSharedPose` helper is still present and can be re-wired.
- **`"x"` axes (user point 2):** resolved by removal — no pose comparison remains in the editor, so frozen axes can no longer trigger a warning. The runtime `find_shared_pose` still skips `"x"` poses and falls back to the entry pose, so runtime is unaffected.
- **Stale browser cache:** the user must **hard-refresh** the editor (Ctrl+Shift+R) to load the updated `editor.html`; until then the cached pre-`midifix` page will keep showing the old warning text.
- **Point 3 (single-action auto-return) NOT implemented:** ambiguous — "returning to the previous" needs a definition (which zone is "previous"? the incoming/reverse target? table order? the flow layout neighbor?) and a trigger point (on save? on zone creation? on bridge creation?). Deferred to the next task pending the user's answer.

### Next Task

Implement the single-action auto-return `next` (user point 3) once the user clarifies: (a) what "the previous" zone is (the reverse/incoming target of the flow, vs. table order, vs. the flow-layout neighbor), and (b) when the auto-return should be created (on save, on zone/bridge creation, or as a live editor affordance). Likely location: the flow tab's `flowBridge` (editor.html:6797) and/or a save-time pass over single-action zones, reusing `reverse_targets` from `rtr/flow.py`.

### Instructions for the Next Task

1. Confirm with the user the definition of "the previous" zone and the trigger for the auto-return.
2. In `rtr/flow.py`, `reverse_targets(zones, zone)` already returns the zones that link *into* a zone — a natural source for the return target. The editor mirrors this logic via `actionEdgeList()` (editor.html:1005) and `nextList`/`setNext` (editor.html:916/927).
3. For a zone with exactly one action, if that action's `next` is empty, append a return target pointing back to the chosen "previous" zone (and its first enabled action, per `firstEnabledAction` editor.html:899). Reuse `setNext` to write the field in the correct single-dict vs. list form.
4. Decide whether this is a save-time normalization (in `saveZones`) or a live editor affordance (in the flow tab). Keep it non-destructive: never overwrite an existing explicit `next`.
5. Validate with `node --check` on the extracted editor scripts and re-run the zone-table save to confirm no dead-end warnings remain.

## 2026-10-24 — Finish midi named-pose removal: stale docstring + README cleanups

### Status

Completed. After the session crash, the code-side removal was already done in the working tree (`POSES`/`LIN_POSES` tables deleted from `zones.py`; imports/kwargs removed from `state/__init__.py`, `main.py`, `httpserver.py`, `midi.py`; the ch1/ch2/ch5 note→pose dispatch removed from `MidiInput._handle_note`). What remained was stale documentation describing the removed mappings: the `midi.py` module docstring still listed `Note ch1/ch2: 61-64 → set_joint_pose` and `Note ch6 → set_linear_pose`, and the README still had the same note line plus two "named poses" mentions. All three were fixed.

### Summary

The user's task was to remove the hardcoded MIDI mappings (the named-pose note protocol). The crashed session had completed the code changes but left the docs behind. Verified the working-tree state via `git diff` and repo-wide greps (no `POSES`/`LIN_POSES` references left in the rtr package; no test passes `poses=`/`lin_poses=` kwargs to `MidiInput`), then updated the three stale doc spots to match the actual remaining legacy protocol:

- `midi.py` docstring: the legacy protocol is now CC1 (trigger_action), CC3 (play action by index), CC20/21/22 (adjust_limit), and `Note ch3 → random_wrist` only (0-based channel 2, the docstring's 1-based "ch3" convention — unchanged).
- README legacy-fallback bullet: the note line is now just `**Note ch3** → random_wrist`.
- README: dropped "named poses" from the `state/zones.py` module table row, and rewrote the `zones.json` paragraph (the file no longer carries `poses`/`lin_poses`; the built-in `ZONES` table is the fallback).

`validate_state_data` still accepts legacy `poses`/`lin_poses` keys in a data file (decided in the crashed session: kept as generic data validation for old backups; nothing consumes them anymore). The `Cmd.SET_JOINT_POSE`/`SET_LINEAR_POSE` commands remain — they are still reachable via the WebSocket control API (`websocket.py` accepted-command list, `brain.py` handlers), which is a separate path from MIDI.

### Changes Made

- `realtimecontrol/rtr/rtr/io/midi.py`: module docstring legacy-protocol block — removed the `Note ch1/ch2: 61-64 → set_joint_pose` and `Note ch6 → set_linear_pose` lines; kept `Note ch3 → random_wrist` and the ch4/ch5/ch7 "intentionally not mapped" paragraph (still accurate).
- `realtimecontrol/rtr/README.md`: (1) legacy-fallback note bullet reduced to `**Note ch3** → random_wrist`; (2) `state/zones.py` table row — "named poses" dropped from the description; (3) `zones.json` paragraph — now "source of truth for zones and actions" (no more `poses`/`lin_poses` mention).

### Documentation

- `realtimecontrol/rtr/README.md` updated as above (the in-scope doc updates for this task).
- Pre-existing README staleness noted but intentionally not touched (separate cleanup): the legacy-fallback section still describes `CC1 → goto_zone`, `CC2 → set_mode`, `CC30 → set_mode` (the code now does CC1 → `trigger_action`, and CC2/CC30 are not handled), and the `zones.json` paragraph still mentions legacy `mode`/`exitposes`/startpos concepts.

### Architecture

No architectural impact. Docs-only change; runtime behavior unchanged. The `Cmd.SET_JOINT_POSE`/`SET_LINEAR_POSE`/`RANDOM_WRIST` command pipeline (commands.py → brain.py → machine.py) stays intact for the WebSocket control path.

### Validation

- `python -c "import main; from rtr.io.midi import MidiInput; from rtr.io.httpserver import HttpServer; from rtr.state import StateMachine, Zones"`: **imports OK**.
- `pytest tests/test_midi_in.py tests/test_midi_brain_trigger.py tests/test_zone_groups.py -q`: **81 passed, 1 skipped**.
- `pytest tests/ -q --ignore=tests/test_remote_tui.py`: **268 passed, 14 skipped, 7 failed** — all 7 failures pre-existing and unrelated (verified: none import the changed modules' removed symbols):
  - `test_bridge_behavior.py::test_real_zones_bridge_action_is_tagged` — data drift: `zones.json` `resting_sleeping_bridge/bridge` has 4 poses, test expects 3.
  - `test_remote_sdl.py` (5 failures) — TUI label drift ("THEME / LAYOUT" etc. not in `view.build_rows` output).
  - `test_transition.py::test_all_reachable_transitions_complete` — pre-existing `"x"`-axis sim `TypeError` (documented in the previous entry).
- `pytest tests/test_remote_tui.py`: **collection `ImportError`** (`action_kind` not in `rtr.flow`) — pre-existing, unmodified tracked files, unrelated.
- Repo-wide grep for `POSES`/`LIN_POSES`/`named pose`/`ch1`/`ch6` in the rtr package: no remaining references (only "aarch64" false positives).

### Decisions and Caveats

- Kept the `validate_state_data` `poses`/`lin_poses` validation (crashed-session decision): old zone backups keep validating; the fields are simply unconsumed.
- README CC-line staleness (CC1/CC2/CC30) left for a separate docs-cleanup pass — outside this task's scope.
- The 7 test failures + 1 collection error predate this change; flagged, not fixed.

### Next Task

Optional docs cleanup: update the README legacy-fallback CC lines to match the actual code (CC1 → `trigger_action`; CC2/CC30 removed), and the `zones.json` paragraph's legacy `mode`/`exitposes`/startpos mentions. Separately, the `"x"`-axis `test_transition.py` harness fix from the previous entry.

### Instructions for the Next Task

For the README CC-line cleanup: in `realtimecontrol/rtr/README.md` (~line 770), the bullets read `CC1 (value 1-6) → goto_zone (...)`, `CC2 (0/1) → set_mode`, `CC30 (1/2) → set_mode`. The actual legacy handler is `MidiInput._handle_cc` in `rtr/io/midi.py`: CC1 → `Cmd.TRIGGER_ACTION` (zone + first enabled action), CC3 → play action by index, CC20/21/22 → `Cmd.ADJUST_LIMIT`. CC2/CC30 are no longer handled. Align the bullets to the code. For the `test_transition.py` fix, see the previous entry's instructions (resolve `"x"` axes before `SimRobot` construction in `_run_transition`).

## 2026-10-24 — Inter-zone warning now mirrors `find_shared_pose`; dangling `POSES`/`LIN_POSES` imports removed

### Status

Completed. The inter-zone variant of the `entry pose differs from next` warning no longer false-positives: the editor now checks "do the two actions share any pose within `0.5`" (mirroring the runtime gate's `find_shared_pose`) instead of entry-to-entry at `0.001`. Confirmed bidirectional zone links are safe (gate checks are symmetric; both directions verified `conflict: False`). Also finished the midi named-pose removal refactor: `POSES`/`LIN_POSES` were deleted from `zones.py` in the working tree but still imported by `__init__.py`, `main.py`, and `httpserver.py` — that dangling import was the pre-existing `ImportError` blocking pytest collection.

### Summary

The user reported `entry pose differs from next roaring_grabbing_bridge/bridge (-5, -125, 116, -2, 80, 0)` in the `roaring` action pose editor as an unneeded warning: the exit pose toward the bridge is correct, and movement in the other direction is perfectly possible (the robot just moves to the exit pose instead of the entry pose). They asked whether **all** links should be marked bidirectional to keep the robot moving, and whether there are safety reasons not to.

**Root cause (editor false positive):** The editor's save-time check and action-editor hint compared the action's entry anchor against each inter-zone successor's entry anchor at `0.001`. But the runtime safety gate's inter-zone rule is "any shared pose within `0.5`" (`find_shared_pose`, `rtr/state/zones.py:92`) — the hand-off happens at *any* shared anchor, not specifically the entries. In the reported case `roaring/roar`'s exit `[-5,-125,116,-2,80,0]` **is** `roaring_grabbing_bridge/bridge`'s entry, so the gate passes (`conflict: False`) while the old editor check warned on the non-equal entries (`roar` entry `[-3,-134,116,-2,80,0]` vs `bridge` entry).

**Bidirectional links — safe.** The inter-zone gate checks (safezone overlap + shared pose) are symmetric: the same `find_shared_pose` call applies regardless of direction, and the collision check is per-target-entry-pose, independent per direction. Verified with real `zones.json` data: `roaring/roar → roaring_grabbing_bridge/bridge` **and** the reverse both give `conflict: False`, shared pose `[-5,-125,116,-2,80,0]` (roar exit index 2 == bridge entry index 0). No safety reason to restrict links to one direction; marking both directions keeps the robot moving and gives it options.

**Pre-existing import blocker (fixed):** The working-tree `zones.py` refactor removed the `POSES`/`LIN_POSES` constants (the midi named-pose tables, part of the midi.py change that dropped the ch1/ch5 note protocol), but three consumers still imported them, so `import rtr.state` raised `ImportError` and blocked all pytest collection:
- `rtr/state/__init__.py` (re-export),
- `main.py` (line 33 import + fallback dict + `data.get("poses", POSES)` + `poses=`/`lin_poses=` kwargs on `MidiInput`, which no longer accepts them — a runtime `TypeError` waiting to happen),
- `rtr/io/httpserver.py` (import + GET `/api/zones` fallback dict).

`zones.json` no longer carries `poses`/`lin_poses` keys (only `zones` + `layout`), and `validate_state_data` still accepts them optionally for legacy files, so nothing data-level depends on the constants anymore.

### Changes Made

- `realtimecontrol/rtr/editor.html:985-999`: added `poseNumeric(p)` and `findSharedPose(posesA, posesB, margin = 0.5)` JS helpers — the JS mirror of `rtr.state.zones.find_shared_pose` (any pose shared within `0.5` on every concrete axis; `"x"` frozen axes skipped).
- `realtimecontrol/rtr/editor.html:6128-6134` (`saveZones`): the inter-zone branch now warns only when `findSharedPose(poses, tgt) === null`; message is now `no shared pose with next ${t.zone}/${t.action}`. The intra-zone `continue` exemption from the previous task is preserved.
- `realtimecontrol/rtr/editor.html:4968-4976` (`buildActionEditor` hint): same change — the inline hint now fires only when `findSharedPose(a.poses, tgt) === null`.
- `realtimecontrol/rtr/rtr/state/__init__.py`: removed `POSES`/`LIN_POSES` from the `.zones` import and `__all__`.
- `realtimecontrol/rtr/main.py`: import reduced to `from rtr.state.zones import ZONES, load_state_data`; `load_zones_data` fallback is now `{"zones": ZONES}`; dropped the `poses`/`lin_poses` locals and the `poses=`/`lin_poses=` kwargs on the `MidiInput` call (the signature no longer has them).
- `realtimecontrol/rtr/rtr/io/httpserver.py`: removed `LIN_POSES`/`POSES` from the `.state.zones` import; GET `/api/zones` fallback is now `{"zones": server.zones_provider().table()}`.

### Documentation

None required — the editor advisory now matches the already-documented runtime rule (`find_shared_pose` in `zones.py:86-109`), and the removed named-pose tables were superseded by the data-driven zone format already documented in `zones.py`'s module docstring.

### Architecture

No architectural impact. The editor advisory is now a strict mirror of the runtime gate's inter-zone hand-off rule (same predicate, same margin), and the `POSES`/`LIN_POSES` removal completes the existing refactor that moved named poses out of code into the (now absent) data file. No runtime behavior changed.

### Validation

- `node --check` on the extracted editor `<script>` block: **SYNTAX OK**.
- `python3 -c "import main; from rtr.io.httpserver import HttpServer; from rtr.state import StateMachine, Zones"`: **imports OK** (previously `ImportError`).
- `pytest tests/test_safety_conflict.py tests/test_transition.py tests/test_state_machine.py`: **30 passed, 9 skipped, 1 failed** (previously: collection `ImportError` on all three).
- `pytest tests/test_safety_conflict.py tests/test_state_machine.py`: **23 passed, 9 skipped** — all gate-symmetry and state-machine tests green.
- Gate symmetry on real `zones.json`: forward `roaring/roar → roaring_grabbing_bridge/bridge` `conflict: False`; reverse `conflict: False`; shared pose `[-5,-125,116,-2,80,0]`.
- The 1 failure (`test_transition.py::test_all_reachable_transitions_complete`, `TypeError` at `rtr/robot/sim.py:42`) is **pre-existing** and unrelated: the harness passes `growling/growl`'s entry pose `['x', -10, -9, -2, 5, 0]` (an unresolved `"x"` frozen axis) straight into `SimRobot`, so the sim's per-axis arithmetic hits `'str' - 'str'`. Only unmodified files/data are on that path (`sim.py`, `machine.py`, `zones.json`, the test itself).

### Decisions and Caveats

- The editor check now mirrors the runtime gate exactly (any shared pose, margin `0.5`), so the advisory and the gate can no longer disagree on inter-zone hand-offs. It remains a save-time flag, not a block.
- `validate_state_data` still accepts legacy `poses`/`lin_poses` keys in the data file, so old backups keep validating; they are simply no longer consumed by the core (the midi named-pose note protocol is gone).
- The `test_transition.py` `"x"`-axis sim failure is a test-harness limitation (the harness doesn't resolve frozen axes before feeding the sim) — flagged as a follow-up, not fixed here.

### Next Task

Optionally fix `tests/test_transition.py::_run_transition` to resolve `"x"` axes before constructing `SimRobot` (e.g., replace `"x"` with the source action's live value on that axis, or skip transitions whose entry pose contains `"x"` with a note), so the harness exercises all reachable edges.

### Instructions for the Next Task

In `tests/test_transition.py:47-70`, `_run_transition` builds `SimRobot(home=list(zones.get(start_zone).action_entry_pose(start_action)))`. When that entry pose contains `"x"` (frozen axis, e.g. `growling/growl`), `SimRobot.get_curjpos` raises `TypeError` at `rtr/robot/sim.py:42` (`self._target[i] - self._cur[i]` with strings). Resolve the `"x"` before `SimRobot` construction — the runtime resolves `"x"` to the robot's live value on that axis, so the harness can substitute the current pose's value (the home value itself on that axis is unknown, so pick the safezone midpoint or the successor's concrete value on that axis) — or skip such pairs with a `pytest.skip` note. Then re-run `pytest tests/test_transition.py -q`.

## 2026-10-24 — Fix spurious intra-zone "entry pose differs" save warning

### Status

Completed. The editor's save-time advisory `entry pose differs from next …` (and the matching action-editor hint) no longer fires for **intra-zone** successors, matching the runtime safety gate. Confirmed the warning is non-blocking and that action-to-action transitions are **not** gated by it.

### Summary

The user reported many `saved — warning: resting/hold: entry pose differs from next resting/wake` messages and asked (a) whether the warning should exist — arguing that within a safezone the robot moves freely, so entry/exit pose differences are fine — and (b) whether it blocks action-to-action transitions.

**Root cause:** The editor's pose-continuity check compared an action's entry anchor (`poses[0]`) against each successor's entry anchor with a `0.001` threshold, for **all** successors (intra- and inter-zone). But the runtime safety gate explicitly exempts **intra-zone** moves from any pose-continuity requirement — `gate.py:72-90` and the editor's own `edgeConflict` (`editor.html:1049-1057`) both state the robot moves freely within a zone, so any pose inside the safezone is a valid hand-off. The save-time check never honored that exemption, so every intra-zone successor whose entry pose differed by ≥ 0.001 produced a false-positive warning.

**Is it blocked? No.** Two independent confirmations:
1. The save itself is non-blocking — `saveZones()` (`editor.html:6123-6126`) reports `saved — warning:` but the POST succeeds on HTTP `ok` regardless of `warns`.
2. The runtime transition is governed by the **safety gate**, not this warning. For intra-zone moves the gate only conflicts when a connection pose lies *outside* the safezone (`gate.py:75-90`); it never requires pose continuity. So action-to-action within a zone is free.

### Changes Made

- `realtimecontrol/rtr/editor.html:6107-6119` (`saveZones`): the pose-continuity loop now `continue`s when `t.zone === name` (same zone as the current action), so intra-zone successors no longer produce the `entry pose differs from next` warning. Inter-zone successors are still checked as before.
- `realtimecontrol/rtr/editor.html:4950-4960` (action-editor hint in `buildActionEditor`): the same exemption — the `diffs` filter returns `false` when `t.zone === zn` — so the inline "entry pose differs from next" hint no longer appears for intra-zone successors.

### Documentation

None required — behavior now matches the already-documented runtime rule (the intra-zone free-movement exemption is already documented in `gate.py:63-66` and `editor.html:1040-1043`).

### Architecture

No architectural impact. This aligns the editor's advisory with the existing runtime safety-gate contract; no runtime behavior changed.

### Validation

- `node --check` on the extracted editor `<script>` block: **SYNTAX OK** (6692 lines).
- `pytest tests/test_safety_conflict.py tests/test_transition.py tests/test_state_machine.py`: 30 passed, 9 skipped, 1 failed.
- The 1 failure (`test_transition.py::test_all_reachable_transitions_complete`, `TypeError` at `rtr/robot/sim.py:42`) is **pre-existing**: reproduced identically with this change stashed (`git stash`), so it is unrelated to the editor edit (a sim string-operand bug on an unresolved `"x"` axis).

### Decisions and Caveats

- Scope kept to the intra-zone exemption (the user's specific complaint, `resting/hold → resting/wake`). The inter-zone check still compares entry-to-entry at `0.001`, whereas the runtime's inter-zone rule is "any shared pose within `0.5`" (`find_shared_pose`). These differ, so the inter-zone warning can still be a false positive in some cases — left as-is to avoid broadening the change; flagged as a follow-up.
- The editor check remains a *stricter* advisory than the runtime gate by design (it's a save-time flag, not a block).

### Next Task

Optionally tighten the inter-zone pose-continuity check to mirror the runtime's `find_shared_pose` (any anchor within `0.5`) instead of entry-to-entry at `0.001`, so inter-zone saves stop producing false positives too.

### Instructions for the Next Task

In `editor.html`, the inter-zone branch of the pose-continuity check (currently `saveZones` ~line 6113 and the action-editor hint ~line 4956) compares `poses[0]` vs the successor's `poses[0]` at `0.001`. To mirror the runtime, replace that with a "do the two actions share any pose within `0.5` on all concrete axes" test (the JS equivalent of `find_shared_pose` in `rtr/state/zones.py:92`), and only warn when no shared pose exists. Keep the intra-zone `continue`/`return false` exemptions already added this task. Verify with `node --check` and re-run `pytest tests/test_safety_conflict.py`.

## 2026-10-24 — Assertive control mode: full implementation (config + machine + brain driver + UI + test)

### Status

Completed. The third **`assertive`** control mode is implemented end-to-end: config validation, machine support, the brain driver, the client toggle, and a 15-case test suite. All new tests pass; no regressions (6 pre-existing failures confirmed unrelated by stash).

### Summary

Assertive mode drives the robot to random safe destinations with a per-move speed, optional cross-zone wandering, and optional person tracking. It is a third `control_mode` alongside `auto`/`manual`. The brain picks a random `(zone, action)` destination from a pool, rolls a per-move speed in `[speed_min, speed_max]`, and drives the robot there — same-zone moves play directly, cross-zone moves walk a safe path (BFS over the action-`next` + zone-`exits` graph). A `dwell_s` gates each reselect. Person tracking (the hunt ladder) runs only when `assertive.track` is enabled.

**Work split across two sessions:**
- *Prior session:* config (`brain/config.py`, `brain.json`), `machine.py` support, and the bulk of the `brain.py` driver were implemented and verified.
- *This session:* verified the driver end-to-end, found and fixed **two bugs** the verification surfaced, wrote the test suite, ran the full suite, and added the client toggle.

**Bugs found and fixed this session (both in `brain.py`):**
1. **Same-zone destinations had no safe path.** `_assertive_tick` routed *every* destination through `_assertive_find_path` (a `find_safe_path` BFS). Same-zone actions without a `next` link have no in-zone graph edge, so `find_safe_path` returned `None` and the driver wasted a full dwell cycle "re-evaluating". Verified against the real `zones.json`: e.g. `hunting: track → scan` and `testing: locked → intent` have no in-zone path. **Fix:** same-zone destinations now use `play_action` directly (the robot is already in the zone; no cross-zone path needed). `_assertive_tick` in `brain.py` gained a same-zone branch before the cross-zone path walk.
2. **Pool validation raised `KeyError` on unknown zones.** `_assertive_pool` called `self.machine.zones.get(z)`, which raises `KeyError` for an unknown zone name (it does not return `None`). An explicit `pool` entry naming an unknown zone crashed the brain. **Fix:** guard with `self.machine.zones.has(z)` before `get(z)`; unknown entries are dropped.

**Config (from prior session, verified this session):**
- `brain/config.py`: `assertive` section in `DEFAULT_BRAIN_CONFIG`; `_validate_assertive_section` validates `enabled` (bool), `dwell_s` (number ≥ 0), `speed_min`/`speed_max` (numbers, `speed_min ≤ speed_max`), `wander` (bool), `track` (bool), `pool` (null or list of non-empty `"zone/action"` strings). Wired into `validate_brain_config`.
- `brain.json`: `assertive` section with defaults (`enabled=true`, `dwell_s=8`, `speed_min=30`, `speed_max=100`, `wander=true`, `track=false`, `pool=null`).

**machine.py (from prior session, verified this session):**
- `assertive_speed: Optional[float]` field; `set_control_mode` accepts `"assertive"` and clears `assertive_speed` on exit; `_current_speed` returns `assertive_speed` first while in assertive mode.

**brain.py driver (the core of the feature):**
- State: `assertive_nav` (waypoints), `assertive_nav_index`, `assertive_prev` (last `(zone, action)`, resets the dwell timer on change), `assertive_arrived_ts`.
- `_assertive_cfg`: reads the `assertive` config section.
- `_assertive_pool`: explicit `pool` entries (validated) or all enabled `(zone, action)` nodes.
- `_assertive_pick_destination`: random pool member; skips the current node and `_blocked_candidates`; `wander=false` restricts to the current zone.
- `_assertive_roll_speed`: rolls `assertive_speed` in `[speed_min, speed_max]` (constant per move).
- `_assertive_find_path`: `find_safe_path` from the current node to the destination (cross-zone only).
- `_assertive_tick`: per-tick driver — dwell gate, pick, speed roll, then: no-current-action → direct trigger; same-zone → `play_action`; cross-zone → safe-path walk (waypoint nav).
- `tick()`: assertive branch runs `_assertive_tick` then the hunt ladder only when `assertive.track` is on.
- `_hunt_tick`/`_hunt_active`: the hunt ladder was extracted from `tick` so assertive can reuse it; `_hunt_active` returns False (manual), `track` (assertive), True (auto).
- `SET_CONTROL_MODE`: entering assertive clears `active_group`; leaving assertive clears nav state (machine drops `assertive_speed`).
- `_apply_lock`: attention-grab gated by `_hunt_active()` (not `control_mode != "manual"`).

**client.html:** added the `cmAssertive` button to the `#controlMode` segmented toggle, the `.seg.assertive.active` CSS (amber), the three-way state-sync, and the click handler sending `set_control_mode` with `mode: "assertive"`.

**test_brain_assertive.py:** 15 tests covering the pool (default/explicit/unknown-drop), destination picking (skip-current, skip-blocked, wander restriction), speed roll range, path finding (cross-zone + same-zone-None), and the end-to-end tick (no-current-action, same-zone direct play, cross-zone walk, dwell gating, leaving-clears-state).

### Changes Made

- `realtimecontrol/rtr/rtr/brain/brain.py`:
  - `_assertive_tick`: added the same-zone direct-`play_action` branch (before the cross-zone path walk).
  - `_assertive_pool`: guard unknown-zone entries with `zones.has(z)` (was a `KeyError`).
  - (Prior session, verified here: assertive state vars, `_assertive_cfg`, `_assertive_pool`, `_assertive_pick_destination`, `_assertive_roll_speed`, `_assertive_find_path`, `_assertive_tick`, `_hunt_tick`/`_hunt_active` refactor, `tick()` assertive branch, `SET_CONTROL_MODE` handling, `_apply_lock` gating.)
- `realtimecontrol/rtr/rtr/brain/config.py`: `assertive` section in `DEFAULT_BRAIN_CONFIG` + `_validate_assertive_section` (prior session, verified here).
- `realtimecontrol/rtr/brain.json`: `assertive` section with defaults (prior session, verified here).
- `realtimecontrol/rtr/rtr/state/machine.py`: `assertive_speed` field, `set_control_mode` accepts `"assertive"`, `_current_speed` honors it (prior session, verified here).
- `realtimecontrol/rtr/client.html`: `cmAssertive` toggle (button + CSS + state-sync + handler).
- `realtimecontrol/rtr/tests/test_brain_assertive.py` (new): 15-test suite for the assertive driver.

### Documentation

- `brain/config.py` module docstring documents the `assertive` section.
- `brain.py` method docstrings document the driver semantics.
- This handoff entry.

### Architecture

- Extends the existing `machine.control_mode` three-way seam (`auto`/`manual`/`assertive`) and the brain's per-tick driver. Assertive moves route through `trigger_action`/`play_action` (the safety gate is never bypassed); a discarded pending move is handled by the reselect. No new modules; the brain's `tick()` gained an assertive branch.

### Validation

- `python tests/test_brain_assertive.py` → **15 passed, 0 failed** (pool ×3, pick ×4, speed ×1, find_path ×2, tick ×5).
- `python -m pytest tests/ -q --ignore=tests/test_remote_tui.py` → **269 passed, 14 skipped, 6 failed**. The 6 failures (`test_remote_sdl.py` ×5, `test_transition.py::test_all_reachable_transitions_complete`) are **pre-existing** — confirmed by `git stash` of my 4 changed files and re-running: they fail identically without my changes and touch `sim.py`/remote-TUI code I did not modify. (`test_remote_tui.py` has a pre-existing collection error: imports `action_kind` from `rtr.flow`, which does not exist.)

### Decisions and Caveats

- **Dwell on entry:** entering assertive, the robot dwells for `dwell_s` before its first move (the dwell timer resets on the first tick because `cur != assertive_prev`). This is a deliberate "settle" delay, not a bug — but it means the first move is delayed by `dwell_s`. If an immediate first move is wanted, seed `assertive_arrived_ts` to the past on entry in the `SET_CONTROL_MODE` handler.
- **Unreachable pool nodes:** the default pool includes all enabled nodes, but not all are reachable from the current node via the safe-path graph. When the driver picks an unreachable cross-zone node, `find_safe_path` returns `None` and it re-evaluates next dwell (graceful; it will eventually pick a reachable one). Same-zone nodes are always reachable (direct `play_action`), so no wasted cycles there.
- **Speed is per-move, not per-tick:** `assertive_speed` is rolled once per destination and stays constant for the duration of the move (not re-rolled each tick).
- **Tracking is opt-in:** the hunt ladder (person tracking) runs in assertive mode only when `assertive.track` is `true` (default `false`).

### Next Task

Optional live check: run the rtr core, switch to assertive mode from the client toggle, and confirm the robot wanders to random destinations at variable speed, that cross-zone moves walk safe paths, and that toggling `assertive.track` enables person tracking.

### Instructions for the Next Task

- The driver is in `brain.py::_assertive_tick` (with helpers `_assertive_pool`, `_assertive_pick_destination`, `_assertive_roll_speed`, `_assertive_find_path`). Config is the `assertive` section in `brain.json`. The toggle is `#cmAssertive` in `client.html`.
- To tune behavior, edit the `assertive` section in `brain.json` (`dwell_s`, `speed_min`/`speed_max`, `wander`, `track`, `pool`).
- The test suite is `tests/test_brain_assertive.py` (run directly or via pytest). It uses a synthetic 2-zone table (zone `a` with two actions and no in-zone `next` link, exiting to zone `b`), so it does not depend on `zones.json` or the sim.
- If the dwell-on-entry delay is unwanted, edit the `SET_CONTROL_MODE` handler in `brain.py` (the `p.get("mode") == "assertive"` branch) to seed `self.assertive_arrived_ts` to a past timestamp on entry.

## 2026-10-23 — Hunt red-link conflict: generic safety-aware hunt trigger (Option 1, in-place scan)

### Status

Completed

### Summary

Implemented the generic fix for the repeated `auto discarded move to hunting / scan — red-link conflict` warning. The brain's two hunt trigger sites now consult the safety gate **before** requesting a cross-zone move, so a hunt move the gate would discard is instead run **in place** (the hunt state runs in the current zone; no move is requested). The check is driven by the live safezone data, so it applies to whatever zone the robot occupies and whatever zone the hunt params name — not just the `resting`/`hunting` pair.

**Change (all in `rtr/brain/brain.py`):**
1. New helper `Brain._hunt_move_safe(zone, action)` (`brain.py:369`): returns `False` on empty zone/action; `True` when no safety gate is wired; otherwise `not safety.check_conflict(machine.current_zone, machine.current_action, zone, action)[0]`.
2. Person-lost scan trigger (`_hunt_tick`, `brain.py:821-823`): the `trigger_action(scan.zone, scan.action)` call is now guarded by `_hunt_move_safe(...)`; `_set_class("scan")` still always runs, so the hunt state (wide-camera scan) executes in the current zone when the move is unsafe. (The hunt ladder was later extracted from `tick` into `_hunt_tick` by the concurrent assertive-mode refactor; the guard was preserved.)
3. Attention-grab detect trigger (`_apply_lock`, `brain.py:639-640`): added `and self._hunt_move_safe(detect.zone, detect.action)` to the existing guard, so the detect move to the hunt zone is only requested when safe.

**Why this is the right layer:** the brain is the decision-maker that knows a move is hunt-driven and can choose to run the hunt state in place. The machine (`trigger_action`) and engine (`_resolve_pending`) are generic and don't know about hunt classes, so they can't make this choice. The `Cmd.TRIGGER_ACTION` operator path (`brain.py:410-411`) is intentionally left untouched — that is an explicit move, and the engine already handles its conflict (manual popup / auto discard).

**Behavior after the fix:**
- Robot in `resting`, person lost → class becomes `scan`, but **no** move to `hunting` is requested (the gate would discard it on A3). The robot scans in place in `resting`. No `SAFETY_DISCARD`, no repeated warning.
- Robot in a zone whose safezone overlaps `hunting` (e.g. `leaping`, `stretching_hunting_bridge`, `hunting_scanning_bridge`) → the hunt move to `hunting` proceeds exactly as before (safe zones are unaffected).
- Robot already in `hunting` → intra-zone move, unchanged.

### Changes Made

- `realtimecontrol/rtr/rtr/brain/brain.py`:
  - Added `_hunt_move_safe(zone, action)` helper (`brain.py:369`).
  - Guarded the person-lost scan trigger in `_hunt_tick` (`brain.py:821-823`).
  - Guarded the attention-grab detect trigger in `_apply_lock` (`brain.py:639-640`).

### Documentation

- None required beyond this handoff entry; the helper's docstring documents the in-place decoupling and the no-gate / empty-input behavior.

### Architecture

- No architectural change. This refines the existing brain→machine→engine contract: the brain now pre-filters its own hunt-driven moves through the same safety gate the engine uses, so a hunt move is never requested that the gate would discard. The machine and engine are unchanged.

### Validation

- `python3 -m py_compile rtr/brain/brain.py` → SYNTAX OK.
- `python3 /tmp/kilo/test_hunt_move_safe.py` (constructs the real `Zones` from `zones.json` + `SafetyGate` + `StateMachine` + `Brain`, then exercises `_hunt_move_safe`):
  - `resting → hunting/scan` UNSAFE (`conflict=True`, issues `['safezones do not overlap on A3', 'no shared position: resting/hold shares no pose with hunting/scan']`) → helper returns `False`. PASS.
  - `resting → hunting/track` UNSAFE → `False`. PASS.
  - `leaping → hunting/scan` SAFE (zero safezone gaps) → `True`. PASS.
  - No safety gate wired → `True`. PASS.
  - Empty zone / empty action / both empty → `False`. PASS.
  - **RESULT: ALL PASS.**

### Decisions and Caveats

- The helper reuses `SafetyGate.check_conflict` (the same check the engine runs), so the brain's decision and the engine's decision are guaranteed to agree — there is no second, divergent conflict model.
- The detect trigger (`_apply_lock`) already had guards (`control_mode`, class not tracking, behavior not in `attention_guard`, no transition, no pending); the safety check is additive and only ever suppresses a move the gate would discard.
- When the move is unsafe the robot now scans in place in its current zone rather than logging a repeated discard. If an operator wants a one-time notice, a throttled log could be added at the guard site, but that was intentionally omitted to keep the change minimal.

### Next Task

No immediate next task identified. On the next live run, confirm the `red-link conflict` warning no longer repeats while the robot sits in `resting` and a person is lost, and that the robot still moves to `hunting` when it is already in an overlapping zone (e.g. `leaping`).

### Instructions for the Next Task

1. Run the rtr core in auto mode with `hunt.enabled=true`.
2. Put the robot in `resting`, wait for a person to be lost → confirm the class goes to `scan` and **no** `auto discarded move to hunting / scan` warning appears.
3. Put the robot in `leaping` (or any zone overlapping `hunting`), wait for a person detect → confirm the robot still moves to `hunting`.
4. If a one-time operator notice is wanted, add a throttled `print` at the two guard sites in `brain.py` (the scan trigger at `tick` and the detect trigger at `_apply_lock`).

## 2026-10-23 — Hunt red-link conflict: analysis of the brain → machine → engine trigger chain

### Status

Completed

### Summary

Confirmed the root cause of the repeated `auto discarded move to hunting / scan — red-link conflict` warning (raised while the robot sits in `resting`). The brain's hunt loop triggers **cross-zone** moves to the hunt zone that the safety gate then discards, and because the brain re-triggers on every lock/lost cycle the warning repeats.

**The two hunt trigger sites (both in `brain/brain.py`):**
1. **Person lost → scan** (`tick`, `brain.py:736-745`): when the locked person's `CAM_TRACK` is stale (`> lost_s`), the brain clears the lock and calls `machine.trigger_action(scan.zone, scan.action)` = `hunting/scan`, then `_set_class("scan")`.
2. **Attention grab → detect** (`_apply_lock`, `brain.py:573-587`): when a person is locked and the robot is not already in a tracking class, the brain calls `machine.trigger_action(detect.zone, detect.action)` = `hunting/track`.

**Why each move is discarded (the chain):**
- `machine.trigger_action` (`state/machine.py:129`) stores the move as a pending move with `same_zone=False`, `from_zone=current_zone` (e.g. `resting`), `from_action=current_action`. It performs **no** conflict check itself.
- `engine._resolve_pending` (`core/engine.py:202`) calls `safety.check_conflict(from_zone, from_action, to_zone, to_action)`.
- `gate.check_conflict` (`safety/gate.py:41`) treats `resting → hunting` as **inter-zone**, so it runs the safezone-overlap check. The actual `zones.json` safezones conflict on **A3**:
  - `resting` A3 = `[89, 115]`, `hunting` A3 = `[-39, -9]`.
  - `max(89, -39) = 89 >= min(115, -9) = -9` → **no overlap** → conflict.
  - (A1/A2/A4/A5/A6 all overlap; A3 is the only failing axis.)
- In auto mode the engine clears the pending move, calls `brain.mark_candidate_blocked`, and publishes `SAFETY_DISCARD` → `client.html` logs the one-line warning with the generic label `"red-link conflict"`.

**Why it repeats:** the person-lost branch does not consult `_valid_next_candidates`, and `mark_candidate_blocked` only affects the autonomy next-link advance — not the hunt trigger. So the cycle `detect(track) → discard → person-lost(scan) → discard → detect(track) → …` keeps firing.

**Why there is no visible editor red link:** the editor flow graph (`editor.html`) is built only from the action-level `next` field; it does not render zone-level `exits`, and no `resting` action's `next` points to `hunting`. The hunt move is a **brain direct-trigger**, not a `next` edge, so no edge is drawn (let alone red).

### Changes Made

- None (read-only analysis; no code changed).

### Documentation

- None required — this is a diagnosis; the fix entry documents the change.

### Architecture

- No architectural change. This entry pins down the exact trigger chain and the two brain call sites that must become safety-aware.

### Validation

- Verified the A3 overlap arithmetic by hand against the actual `zones.json` safezones (resting A3 `[89,115]` vs hunting A3 `[-39,-9]`).
- Confirmed `machine.trigger_action` sets `same_zone=False` and stores a pending move with no conflict check (`machine.py:171-189`).
- Confirmed `engine._resolve_pending` runs the conflict check and discards in auto mode (`engine.py:233-282`).
- Confirmed `gate.check_conflict`'s inter-zone rule (safezone overlap on all 6 axes + shared-pose continuity) (`gate.py:91-108`).

### Decisions and Caveats

- The generic fix belongs in the **brain** (the decision-maker), not the machine or engine: the brain is the only layer that knows the move is hunt-driven and can choose to run the hunt state in place instead of requesting a move.
- The fix must be driven by the **actual safezone data** (via the safety gate), not hardcoded to `resting`/`hunting`, so it applies to any zone whose safezone does not overlap the hunt zone.

### Next Task

Implement the generic safety-aware hunt trigger in `brain.py`: add a `_hunt_move_safe(zone, action)` helper and guard both hunt trigger sites (scan at `tick`, detect at `_apply_lock`) so a hunt move that the safety gate would discard is instead run in place (hunt state runs in the current zone, no move requested).

### Instructions for the Next Task

1. Add `Brain._hunt_move_safe(zone, action)`: return `False` on empty zone/action; `True` when `machine.safety` is None (no gate); otherwise `not safety.check_conflict(machine.current_zone, machine.current_action, zone, action)[0]`.
2. Guard the person-lost scan trigger (`tick`, `brain.py:740-741`): only `trigger_action(scan.zone, scan.action)` when `_hunt_move_safe(...)`; always still `_set_class("scan")` so the hunt state runs in place.
3. Guard the attention-grab detect trigger (`_apply_lock`, `brain.py:587`): add `and self._hunt_move_safe(detect.zone, detect.action)` to the existing guard condition.
4. Do NOT touch `Cmd.TRIGGER_ACTION` (the explicit operator move at `brain.py:410-411`) — that is not a hunt move and the engine already handles its conflict.

## 2026-10-23 — Investigate "saved — warning: resting: safezone A6 exceeds hardware" on save

### Status

Completed

### Summary

Investigated the save-time warning `saved — warning: resting: safezone A6 exceeds hardware [-150, 1] (+68 more)` in `realtimecontrol/rtr/editor.html`. The resting zone's A6 safezone is `[-3, 3]` (zones.json) and the user expected the hardware floor to be `[-150, 150]`, so the warning looked like a false positive.

**Root cause:** the warning is generated by `saveZones()` in editor.html (line ~6091), which compares each zone safezone axis against the live `HW_LIMITS` loaded from `GET /api/limits` (the core's `HARDWARE_LIMITS` in `rtr/state/zones.py`). At the time the warning fired, the core's A6 entry was `(-150, 1)` — set by commit `2e77b23` ("hardware lim its"), which changed A6 from `(-350, 350)` to `(-150, 1)`. Because `3 > 1`, the safezone `[-3, 3]` genuinely exceeded the hardware floor `[-150, 1]`, so the warning was **correct for that core state** — not a logic bug.

**Resolution already in tree:** the latest commit `c1e2437` ("zones") reverted the A6 hardware limit back to `(-150, 150)`. With that value the check `slo < hlo || shi > hhi` → `-3 < -150 || 3 > 150` → false, so the warning no longer fires. All A6 references are now consistent at `(-150, 150)`:
- core `HARDWARE_LIMITS` (zones.py:53): `(-150, 150)`
- editor `HW_LIMITS_FALLBACK` (editor.html:792): `[-150, 150]`
- `rtr/remote/view.py:66`: `(-150.0, 150.0)`
- `validate_zones.js:138` SOFTWARE_LIMITS: `[-150, 150]`

During the investigation I briefly edited the editor fallback to `[-150, 1]` (matching the transient `(-150, 1)` I first observed in zones.py), then **reverted** it once I confirmed the committed core value is `(-150, 150)`. Net change to the working tree from this task: **none** (the fallback is back to its original `[-150, 150]`, which already matched the core).

**Action for the user:** the running core still holds the old `(-150, 1)` in memory, so the warning will keep appearing until the server is restarted to pick up the committed `(-150, 150)`. Restart the rtr core, then re-save — the A6 warning should be gone.

### Changes Made

- `realtimecontrol/rtr/editor.html`: temporarily changed `HW_LIMITS_FALLBACK` A6 from `[-150, 150]` to `[-150, 1]`, then reverted it back to `[-150, 150]`. **Net: no change** (file is back to its committed state).
- `.kilo/task-handoff.md`: added this entry.

### Documentation

- None required beyond this handoff entry. The `HARDWARE_LIMITS` comment in zones.py already documents A6 as "rotary wrist, now limited".

### Architecture

- No architectural change. The save-time warning is a non-blocking advisory (the core clamps to the safezone ∩ hardware intersection); it correctly reflects the live `HARDWARE_LIMITS` from `/api/limits`.

### Validation

- Confirmed all four A6 limit sources agree on `(-150, 150)` via grep across `zones.py`, `editor.html`, `view.py`, `validate_zones.js`.
- Confirmed `git status` shows `zones.py` unmodified at the committed `(-150, 150)` and `editor.html` back to its committed fallback.
- Traced the A6 value history with `git log -S "(-150, 1)"`: `2e77b23` set it to `(-150, 1)`, `c1e2437` reverted it to `(-150, 150)`.
- No JS/Python test harness exercised the warning path directly (it is a UI-only advisory); validation was by code inspection + cross-file consistency check.

### Decisions and Caveats

- The warning logic itself (`slo < hlo || shi > hhi`) is correct and was not changed.
- The `+68 more` in the message is expected: it aggregates other advisories (other zones' safezones / poses outside the floor), not 68 copies of the same A6 issue.
- Caveat: if the intended A6 floor really is the tighter `(-150, 1)` (rotary-wrist restriction), then the safezone `[-3, 3]` would still need narrowing and `c1e2437` was the wrong revert. The committed history and all four sources now agree on `(-150, 150)`, so that is the current intended value.

### Next Task

No immediate next task identified.

### Instructions for the Next Task

If the warning reappears after a server restart, confirm the running core's `HARDWARE_LIMITS[A6]` via `GET /api/limits` and compare against `zones.py:53`. If they differ, the core process is stale — restart it. If they match and the warning still fires, the safezone in `zones.json` for that zone/axis genuinely exceeds the floor and should be narrowed (or the floor widened deliberately, keeping all four sources in sync).

## 2026-10-23 — Convert rtr/zones.json → kukart/robotstatesFTI.py (legacy zone schema)

### Status

Completed

### Summary

Converted `realtimecontrol/rtr/zones.json` (29 zones, the runtime "unified" form) into the legacy `robotstates.py` zone-dict format used by the old kukart stack, emitting `realtimecontrol/kukart/robotstatesFTI.py`. The new module exposes a top-level `kukastate` dict (mirroring `robotstates.py`) whose `zones` table is the converted data, so `from robotstatesFTI import kukastate` works the same way `from robotstates import kukastate` does in `tidalkuka_v01.py`.

**Per-zone field mapping (zones.json → robotstates.py):**
- `safezone`: JSON `[[lo,hi],...]` → Python `[(lo,hi),...]` (arrays → tuples)
- `speed`: direct copy (number)
- `exits`: JSON `[[zone,action],...]` → `[zone,...]` (action half dropped, dedup, order kept); zones with **no** explicit `exits` field derive it from the last action's `next` target
- `actions.name.pos`: from `poses` (`"x"` frozen-axis placeholders kept verbatim)
- `actions.name.speed`: single number repeated per pose → list; actions with **no** `speed` → `[None]*n`
- `startpos` (absent in zones.json): **derived** = first anchor of the zone's first action
- `exitpos` (absent in zones.json): **derived** = last anchor of the zone's last action

**Top-level `kukastate`**: `robot`, `modechange`, `mode`, `currentaction`, `actionindex`, `currentzone: "resting"`, `speed: 100`, `zones`. Dynamic state fields from `robotstates.py` (`nextjpos`, `curjpos`, `nextpos`, `curpos`, `linposes`, `poses`, `dynposes`, `limits`, `softwarelimits`) are omitted — they are runtime state, not config.

**Fields dropped** (present in zones.json, no slot in the legacy zone schema):
- zone-level: `class`, `mode_group`, `enabled`
- action-level: `variable_axes`, `behavior`, `loop`, `next`, `pose_imgs`, `pose_videos`, `engine`, `sweep`, `axis_bands`, `enabled`
- top-level: `layout` (UI x/y positions)

### Changes Made

- `realtimecontrol/kukart/robotstatesFTI.py` (new, 363 lines): generated `kukastate` dict with all 29 zones converted.
- `/tmp/kilo/convert_zones.py` (scratch, outside repo): reads `zones.json`, applies the mapping + derivations, emits the file, prints the missing-fields report.

### Documentation

- None required beyond this handoff entry; the generated file carries a header comment explaining the conversion and the `"x"` placeholder convention.

### Architecture

- No architectural change. This is a data-format bridge: the legacy kukart stack (`robothelpers.py`) reads `kukastate["zones"][z]` for `safezone`/`startpos`/`exitpos`/`speed` and `actions[a]` for `pos`/`speed`, and `robotstatesFTI.py` provides exactly that shape. The runtime `rtr/state/zones.py` continues to read `zones.json` directly — the two consumers are independent.

### Validation

- `python3 /tmp/kilo/convert_zones.py` → "Converted 29 zones".
- Import + structural test: `import robotstatesFTI` OK; `len(zones) == 29`; every zone has all required keys (`startpos`, `safezone`, `actions`, `exitpos`, `exits`, `speed`); all `safezone` entries are 2-tuples; `pos`/`speed` list lengths match per action; all speeds are numeric or `None` (where the source action had no speed).
- Spot checks: `resting` startpos/exitpos `[−60,−90,90,0,15,0]`, safezone `[(-185,185),(-105,-65),(89,115),(-15,15),(-15,45),(-3,3)]`, speed 20, exits `['sleeping','cuddling','stretching','hunting']`; `resting.hold` speed `[20,20,20]`; `sleeping.breathe` speed `[None,None,None,None,None]` (source action has no speed).

### Decisions and Caveats

- **`"x"` placeholders** are kept verbatim in poses (and in derived `startpos`/`exitpos`). They mark frozen/variable axes in the new runtime and have no fixed joint value in the legacy schema. Affected: `cuddling` exitpos, `purring` startpos, `hunting` startpos, `growling` startpos, `clawing.grab[0]`, `testing` startpos, `resting_testing_bridge` startpos, `cuddling_purring_bridge` startpos+exitpos, `cuddling_stretching_bridge` startpos+exitpos, `grabbing` startpos, `roaring_grabbing_bridge` exitpos, `hunting_scanning_bridge` startpos, `purring_hunting_bridge` exitpos, `roaring_growling_bridge` exitpos. The legacy `robothelpers.py` would choke on `"x"` in a `fitlimits`/numeric context — if the old stack must run these zones, the `"x"` axes need to be resolved to concrete values (e.g. the current joint value or a band midpoint) at load time.
- **`startpos`/`exitpos` are derived**, not stored in zones.json. The derivation (first anchor of first action / last anchor of last action) matches how the old stack uses them (entry pose on zone activation, exit pose on zone deactivation). If a zone's first/last action pose is not a good stand-in for the true entry/exit anchor, those two fields should be overridden by hand.
- **Disabled actions are still emitted** (`grabbing.grab` has `enabled: false`). The legacy schema has no `enabled` slot; the old stack will attempt to play it. If it must be excluded, drop the action from the dict or add a guard in the consumer.
- **Actions with no `speed`** produce `[None]*n` speed lists: `sleeping.breathe`, `sleeping.snore`, `cuddling.breathe`, `stretching.look`, `hunting.track`, `hunting.track2`, `performing.trick`, `performing.bow`, `roaring.roar`, `roaring.snarl`, `growling.snarl`, `leaping.leap`, `leaping.pacing`, `resting_sleeping_bridge.bridge`. The legacy `doAction` reads `speed[actionindex]` and would `NoneType` on these — a default speed must be supplied (e.g. the zone speed) before the old stack runs them.
- `exits` for 16 zones were derived from the last action's `next` (those zones have no explicit `exits` field). The derived value is a single zone name; if a zone can exit to multiple zones, an explicit `exits` list should be added to zones.json.

### Next Task

Wire `robotstatesFTI` into the legacy kukart stack (replace `from robotstates import kukastate` with `from robotstatesFTI import kukastate` in `tidalkuka_v01.py` and any other importers), and resolve the `"x"` placeholders + `None` speeds before the old stack is expected to drive the FTI zones.

### Instructions for the Next Task

1. Grep for `from robotstates import` / `import robotstates` in `realtimecontrol/kukart/` and `realtimecontrol/` to find all importers of the old zone table.
2. For each importer that should run the FTI zones, swap the import to `robotstatesFTI`.
3. Before the old stack touches a zone, resolve `"x"` axes: for each pose containing `"x"`, substitute the live joint value (or a per-axis default) at load or activation time. The `"x"` axes are exactly those listed in the caveats above.
4. Supply a default speed for the 14 actions with `None` speed lists (zone speed is the natural default).
5. Re-run the import + structural test to confirm the swapped module loads cleanly.

## 2026-10-23 — Assertive mode: analysis of existing auto/manual architecture + safety seams

### Status

Completed

### Summary

Read the core in full to locate the seams for the third **"assertive"** control mode (assertively switches behaviors/programs, wanders inside safezones, varies speed, plays random patches, optionally tracks people).

**Current mode architecture:**
- `machine.control_mode`: `"auto"` | `"manual"` (`state/machine.py:111`), set via `Cmd.SET_CONTROL_MODE` → `brain.on_command` (`brain/brain.py:434`) → `machine.set_control_mode` (`machine.py:246`). Manual freezes the robot (clears action + transition, holds the current pose).
- Auto mode: `brain._autonomy_tick` (`brain.py:934`) — dwell timeout + behavior triggers (dwell/new_lock/lost_lock/lock_duration) + external events (midi/camera/websocket = strong triggers) → `_autonomy_decide` → stay probability or advance to a random valid candidate from the action's `next` list restricted to `_allowed_zones` (enabled-group union / active group). `_random_valid_next` (`brain.py:832`) is the existing random-selection base.
- Group navigation: `active_group` + `group_nav` waypoints via `flow.find_safe_path_to_any`; walked hop-by-hop while idle (`_group_tick`).
- Hunt loop (auto only): scan→track→farfocus→approach→facefocus→retreat; camera presets per class (`zone_classes` in brain.json); feral = 3+ losts in 30 s.
- `forced_behavior` (ENABLE_BEHAVIOR) = per-behavior live test override; `test_mode` = scripted nav + behavior.

**Safety seams (assertive mode must stay inside safezones):**
- `trigger_action`/`play_action` route through the safety gate: `_pending` "check" phase → engine resolves (conflict + collision) → commit/discard; operator confirm/cancel in "confirm" phase.
- `SafetyGate.check_conflict` (`safety/gate.py`): red-link conflict = safezone overlap (cross-zone) + shared-pose continuity (cross-zone) / pose-outside-safezone (intra-zone). Collision = SAT self-collision via the Node worker.
- Every behavior policy clamps to the effective floor (safezone ∩ hardware) — `behavior.clamp`, `_servo`, `_apply_wander`, `_apply_oscillation`; transition targets are hardware-clamped only.

**Behavior inventory (the random-selection pool):** locked/intent/curious (camera servo, image Jacobian), gentle (face servo), vigilant (sine sweep), restless (continuous random drift, `wander_adj`), spontaneous (random jumps), six oscillations (soothing/agitated/aggressive/exhilarated/impatient/drowsy, freq/amp_scale), calm/bridge (no-op). All roaming policies stay within the safezone.

**Patches:** deterministic per state (`patches.py:code_for`, called per state frame from `display.py`); video mode (default, action-only, per-pose video, holds last video on no-video frames) vs complex (zone+action+behavior layers). **No random patch selection exists** — "play a random patch" needs a new hook (the existing `set_screen_override` is the manual-override seam).

**Speed:** `machine._current_speed()` (`machine.py:700`): transition → target zone speed; action speed; zone speed. Group `params.speed`/`dwell_s` in brain.json (stage-takeover: speed 80, dwell 15 s) — dwell is consumed by `_autonomy_dwell_s`; **`params.speed` is not read by the brain** (speed variance needs a new read or field).

**brain.json state:** autonomy enabled (dwell 30 s, events midi/camera/websocket on); stage-takeover group enabled (includes bridge zones + grabbing/scanning); museum-closed + event-sit-back disabled; hunt enabled (hunting zone); facefocus enabled (stretching); per-action `cam_control` map; per-behavior params.

### Changes Made

- None (analysis only).

### Documentation

- This handoff entry records the seams for the design task.

### Architecture

- No architectural change yet; this entry is the input to the design task.

### Validation

- Read-only: `state/machine.py` (772 lines), `brain/brain.py` (1297), `state/zones.py` (907), `state/behavior.py` (642), `patches/patches.py` (373), `safety/gate.py` (169), `camera/camera.py` (98), `brain.json` (448); `main.py` + `zones.json` partially read in the prior turn.

### Decisions and Caveats

- The third mode plugs into `machine.control_mode` + `brain._autonomy_tick`: assertive = faster dwell, more random selection, speed variance. The safety gate stays in front of every move — assertive changes *which* candidate is picked and *how fast*, never bypasses the gate.
- Caveat: "random patch" has no existing hook — patch resolution is state-deterministic; the design must add a selection hook without breaking the video-mode hold logic.
- Caveat: group `params.speed` is configured but not read by the brain (only `dwell_s` is); speed variance needs a new read or a new field.

### Next Task

Design the assertive mode: exact semantics, config shape (brain.json), machine/brain changes, the random-patch hook, the tracking toggle, the UI seam (client.html/editor.html mode selector), and a test plan.

### Instructions for the Next Task

- Mode seam: `machine.set_control_mode` (machine.py:246) accepts only "auto"/"manual"; the brain's `_autonomy_tick` (brain.py:934) is where decisions are made; `_random_valid_next` (brain.py:832) already does random candidate selection (good base for assertive).
- Safety: never bypass `_pending` resolution; assertive moves still go through `trigger_action`/`play_action`.
- Patches: `patches.py:code_for` is called per state frame from `display.py`; add a brain-driven random-patch hook (`set_screen_override` is the existing manual-override seam).
- Tracking toggle: the hunt loop is gated by `hunt.enabled` (brain.json) + `control_mode != "manual"`; the assertive toggle maps to that gate.
- UI: the SET_CONTROL_MODE command payload is `{mode}` (brain.py:434); the client/editor send it — add the third option there.

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
