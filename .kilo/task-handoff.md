# Task Handoff Log

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
