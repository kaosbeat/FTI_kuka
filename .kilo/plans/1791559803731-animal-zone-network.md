# RTR Animal-Themed Zone/Action Network Plan

## Goal

Rework the `zones` field in `realtimecontrol/rtr/` into a connected, animal-themed zone/action network grouped by mode, with camera-driven behaviors, full adapter integration (sound, patches, MIDI, camera pipeline), and deterministic collision validation.

## Status — COMPLETE (2026-10-10)

All 11 implementation steps are done, plus the follow-up action-level `next`-link pass that makes the flow editor's graph navigable (all 28 action `next` fields were empty; the editor only draws links from `next`, never from zone-level `exits`). Validation (all green):

- `validate_zones.js` — **12/12 zones pass**, 109 poses checked, 0 failures (collision + joint limits).
- `validate_all.py` — **all PASS**: 5 adapter configs load; 12 zones + 3 mode groups; brain `cam_control` covers all 12; StateMachine + Brain construct; 5-hop walk `home→stretch/look→hunt/track→closeup/focus→perform/trick→roar/roar` crossing two mode-group boundaries commits cleanly.
- `validate_camera.py` — **all PASS**: per-zone `cam_control` intents, `CAM_*` telemetry stashing, `CAM_CONTROL` published iff the intent triple changed.
- **Action graph** — all 28 action-level `next` fields populated as a single strongly-connected **28-action cycle** through all 12 zones (`home/hold` → `home/wake` → `sleep/breathe` → … → `leap/pacing` → back to `home/hold`). Every action reachable, no orphans. The flow editor (`editor.html` `actionEdgeList`) now draws the connected graph; cross-mode-group edges intentionally have no shared position (red "!" in the editor) since no safe connector pose exists between those groups.
- **Live debug run** — core `main.py --sim --camera` + `camera_mock` (random-walk "video source") over WS: zone transitions fire sound + `cam_control`; camera `cam_status`/`cam_candidates`/`cam_track`/`cam_face` telemetry is stashed by the brain. Full camera↔core loop verified end-to-end.

Note: the headless dev env lacked `websockets`/`cv2`/`numpy`/`ultralytics`. The core-side suites and the WS loop run with `websockets` available; the physical RPI camera path needs `cv2`/`numpy`/`ultralytics` and is not runnable headless. A local video virtualenv (`rtr-venv/`, numpy 2.5.3 + cv2 5.0.0 + ultralytics 8.4.175) now makes the `camera_mock --video` end-to-end path runnable locally.

## Current State

- **Zones** (`zones.json`): 10 zones — `init`, `rest`, `wakeup`, `stretch`, `wander`, `wildwander`, `watch`, `perform`, `fume`, `init_rest_bridge`. Linked only via action-level `next` fields; no zone-level graph.
- **Behaviors** (`behavior.py`): `track`, `focus`, `scan`, `look`, `wander`, `random`, `hold`.
- **State machine** (`machine.py`): owns `(zone, action)` pair, tick 20 Hz, transitions commit via `comparelist`.
- **Brain** (`brain.py`): thin command interpreter, no robot state; camera telemetry stashed on `machine.camera_state`.
- **Camera** (`pipeline.py`): two cameras (wide = person tracking, close = face analysis), YOLO+ByteTrack, `configure(camera, mode, lock_id)` → `to_telemetry()` → CAM_* payloads. Modes: `idle`, `track`, `analyze`.
- **Adapters**: `sound.py` (MIDI-out), `patches.py` (Hydra screen), both data-driven from JSON, degrade to logging without hardware.
- **Collision tools**: `collide.js` (exact SAT), `test_*.js`, `bench_*.js`, KR60 STLs in `assets/kr60ha/collision/`, `environment_collision.glb` (low-res env model).
- **Limits** (`zones.py`): `SOFTWARE_LIMITS`, `LIMITS`, `HARDWARE_LIMITS` — per-axis joint ranges.

## New Zone Network (Animal-Themed)

### Zone List

| Zone | Theme | Mode Group | Replaces | Key Behaviors |
|------|-------|------------|----------|---------------|
| `home` | Inspection / cage home pose | museum-closed | `init` | `hold`, `wake` |
| `sleep` | Breathing / low-energy rest | museum-closed | `rest` | `breathe`, `snore`, `yawn` |
| `cuddle` | Low resting poses, curled | museum-closed | new | `breathe`, `purr` |
| `purr` | Calm, content, minimal motion | museum-closed | new | `hold`, `purr` |
| `stretch` | Full-body extension, testing the cage | event-sit-back | `stretch`, `wakeup` | `look`, `wink`, `inspect`, `call` |
| `hunt` | Camera tracking, sweet/aggressive | event-sit-back | `watch` | `track`, `focus`, `scan` |
| `closeup` | Reach case edge (~1.5 m), face zoom, top axes | event-sit-back | new | `focus`, `face` |
| `perform` | Choreographic, audience-facing | event-sit-back | `perform` | `trick`, `bow` |
| `roar` | Threat display, anger | stage-takeover | `fume` | `roar`, `snarl` |
| `growl` | Low aggression, warning | stage-takeover | new | `growl`, `snarl` |
| `claw` | Aggressive reaching, grasping | stage-takeover | new | `claw`, `grab` |
| `leap` | Dynamic movement, full reach | stage-takeover | `wander`, `wildwander` | `leap`, `pacing` |

### Zone-Level `exits` Graph

Add a zone-level `exits` field to `zones.json` (list of `[zone, action]` pairs) so the state machine can navigate the network without relying solely on action-level `next`:

```
home     → [sleep/breathe, cuddle/breathe, stretch/look]
sleep    → [home/wake, cuddle/breathe, purr/hold, hunt/track]
cuddle   → [sleep/breathe, purr/hold, stretch/look]
purr     → [sleep/breathe, home/wake, hunt/track]
stretch  → [hunt/track, leap/pacing, closeup/focus]
hunt     → [closeup/focus, perform/trick, roar/roar, sleep/breathe]
closeup  → [hunt/track, perform/trick, roar/roar]
perform  → [hunt/track, roar/roar, sleep/breathe]
roar     → [growl/snarl, home/wake, sleep/breathe]
growl    → [roar/roar, claw/grab, home/wake]
claw     → [leap/pacing, growl/snarl, home/wake]
leap     → [hunt/track, claw/grab, sleep/breathe]
```

### Zone Schema (per zone in `zones.json`)

```json
{
  "enabled": true,
  "mode_group": "museum-closed",
  "safezone": [[lo, hi], ...],
  "speed": 20,
  "exits": [["zone_name", "action_name"], ...],
  "actions": {
    "action_name": {
      "enabled": true,
      "base_pose": [j1, j2, j3, j4, j5, j6],
      "variable_axes": [0, 3],
      "behavior": "track",
      "speed": 20,
      "loop": true,
      "next": {"zone": "...", "action": "..."}
    }
  }
}
```

- `mode_group`: one of `museum-closed`, `event-sit-back`, `stage-takeover`.
- `exits`: zone-level navigation graph (new).
- `base_pose` / `variable_axes` / `behavior` / `speed` / `loop` / `next`: existing action fields, unchanged.
- `safezone`: per-axis joint limits for the zone, must be within `SOFTWARE_LIMITS`.

## Mode Groups (`brain.json` `zone_groups`)

Replace the current `home` / `hunt` / `facefocus` / `perform` groups with three mode groups:

```json
{
  "zone_groups": {
    "museum-closed": {
      "enabled": true,
      "zones": ["home", "sleep", "cuddle", "purr"],
      "behaviors": ["hold", "breathe", "purr"],
      "params": { "speed": 10, "dwell_s": 60.0 }
    },
    "event-sit-back": {
      "enabled": true,
      "zones": ["stretch", "hunt", "closeup", "perform"],
      "behaviors": ["track", "focus", "scan", "look"],
      "params": { "speed": 40, "dwell_s": 30.0 }
    },
    "stage-takeover": {
      "enabled": true,
      "zones": ["roar", "growl", "claw", "leap"],
      "behaviors": ["track", "focus", "wander", "random"],
      "params": { "speed": 80, "dwell_s": 15.0 }
    }
  }
}
```

The brain's autonomy logic selects the active mode group, then picks a zone within it. `SET_ENGINE_MODE` command switches mode groups at runtime.

## Adapter Mappings

### Sound (`sound.json`)

Add entries for all 12 zones and their actions. Keep the existing structure:
- `zones`: `{ zone_name: { out_port, command: {note, velocity} } }`
- `actions`: `{ zone_name: { action_name: { start: {note|cc, velocity|value}, poses: [...] } } }`

Key mappings:
- `home/wake` → note 48, vel 60 (inspection hum)
- `sleep/breathe` → note 36, vel 50 (low breathing)
- `sleep/snore` → note 30, vel 40 (snoring)
- `cuddle/breathe` → note 40, vel 45
- `purr/purr` → cc 70, value 64 (purr tone)
- `stretch/look` → note 55, vel 75
- `hunt/track` → cc 70, value 127 (tracking lock)
- `closeup/focus` → note 67, vel 80 (close zoom)
- `perform/trick` → note 60, vel 60
- `roar/roar` → note 64, vel 90 (roar)
- `growl/snarl` → note 58, vel 85
- `claw/grab` → note 62, vel 80
- `leap/pacing` → note 64, vel 90

### Patches (`patches.json`)

Add Hydra code entries for all 12 zones and their actions. Precedence: action > mode > zone > default.

- `museum-closed` zones: soft `osc()` with low amplitude, posterize effects.
- `event-sit-back` zones: video sources (`test1.mp4`, `hand08.mp4`), kaleid/scale effects.
- `stage-takeover` zones: high-energy `osc()` with higher frequency, rotate effects.

### Camera Pipeline (`pipeline.py` → brain `cam_control`)

Per-zone camera configuration (driven by brain, not hardcoded in zones.json):

| Zone | Active Camera | Mode | Lock |
|------|--------------|------|------|
| `home` | wide | idle | none |
| `sleep` | wide | idle | none |
| `cuddle` | wide | idle | none |
| `purr` | wide | idle | none |
| `stretch` | wide | track | auto |
| `hunt` | wide | track | auto |
| `closeup` | close | analyze | auto |
| `perform` | both | track | auto |
| `roar` | wide | track | auto |
| `growl` | wide | track | auto |
| `claw` | close | analyze | auto |
| `leap` | wide | track | auto |

The brain's `cam_control` intent calls `pipeline.configure(camera, mode, lock_id)` when entering a zone. `to_telemetry()` results feed `CAM_STATUS`, `CAM_CANDIDATES`, `CAM_TRACK`, `CAM_FACE` commands to the state machine.

### MIDI (`midi.json`)

Update `zones` and `nav` entries to match the new zone names. Keep `enabled: true`.

## Collision Validation (Deterministic)

### Scope

Cover three collision categories:
1. **Tool-robot self-collision**: KR60 arm links vs. each other (STL meshes in `assets/kr60ha/collision/`).
2. **Tool-environment collision**: tool tip / flange vs. `environment_collision.glb` (floor, pedestal, walls, vitrine edge).
3. **Robot-environment collision**: base and link meshes vs. environment.

### Procedure

1. For each zone, collect all action poses (`base_pose` + `pos` list entries).
2. For each pose, compute FK joint angles → link transforms.
3. Run SAT collision checks:
   - `collide.js` (exact SAT) for link-vs-link self-collision.
   - `test_env_collide2.js` for robot-vs-environment using `environment_collision.glb`.
   - `test_base_floor.js` for base/floor.
4. Verify all poses are within `SOFTWARE_LIMITS` (per-axis check in `zones.py`).
5. Report: pose, zone, action, collision status (CLEAR / CONTACT / OUT_OF_LIMITS).

### Validation Script

Create `validate_zones.js` (or extend `test_env_collide2.js`) that:
- Parses `zones.json`.
- For each zone × action × pose: runs collision checks.
- Outputs a pass/fail table.
- Exits non-zero on any collision or limit violation.

Run this script before deploying new zone data.

## Implementation Steps (all complete — see Status)

1. **Update `zones.json`**: Replace the 10 old zones with 12 new animal-themed zones. Add `mode_group` and `exits` fields. Keep `safezone`, `speed`, `actions` structure. Migrate existing poses from old zones to new zones (map `init`→`home`, `rest`→`sleep`, `wakeup`→`stretch`, `watch`→`hunt`, `perform`→`perform`, `fume`→`roar`, `wander`/`wildwander`→`leap`). Create new zones: `cuddle`, `purr`, `closeup`, `growl`, `claw`.

2. **Update `brain.json`**: Replace `zone_groups` with `museum-closed`, `event-sit-back`, `stage-takeover`. Update `autonomy` params.

3. **Update `sound.json`**: Add entries for all 12 zones and their actions.

4. **Update `patches.json`**: Add Hydra code entries for all 12 zones and their actions.

5. **Update `midi.json`**: Add zone and nav entries for the new zone names.

6. **Update `moods.json`**: Replace the 9 old moods with 12 new animal-themed moods. Update `links` to match the new `exits` graph.

7. **Update `machine.py`**: Add zone-level `exits` navigation. When an action completes and `loop` is false, check the zone's `exits` list for the next zone/action. Keep action-level `next` as a higher-priority override.

8. **Update `brain.py`**: Add `cam_control` intent per zone (camera, mode, lock). Call `pipeline.configure()` on zone entry.

9. **Update `behavior.py`**: Add `purr`, `claw`, `grab`, `leap`, `pacing`, `snore` behaviors if not already present. Reuse existing `hold`, `breathe`, `track`, `focus`, `scan`, `look`, `wander`, `random`.

10. **Create `validate_zones.js`**: Deterministic collision validation script. Run FK on all poses, check self-collision, env collision, and joint limits.

11. **Test**: Run `validate_zones.js`. Verify all zones pass collision and limit checks. Run the engine in debug mode with a video source (not physical camera) to verify zone transitions and camera telemetry.

## Risks & Mitigations

- **Pose migration**: Old zone poses may not fit the new zone semantics. Mitigation: reuse existing `pos` arrays where the motion matches; create new poses only for genuinely new behaviors.
- **Collision regressions**: New zones (cuddle, purr, closeup, growl, claw) have no existing poses. Mitigation: derive initial poses from neighboring zones (e.g., `cuddle` from `sleep`, `claw` from `stretch`), then validate with `validate_zones.js`.
- **Camera pipeline**: Physical cameras are RPI-specific; the pipeline degrades gracefully. Mitigation: test with `video_source` (debug video) before deploying to RPI.
- **Mode group switching**: `SET_ENGINE_MODE` must cleanly transition between mode groups without dropping the current action. Mitigation: the state machine's `comparelist` commit ensures atomic transitions.

## Open Questions

- Exact pose values for new zones (`cuddle`, `purr`, `closeup`, `growl`, `claw`, `leap`): need to be derived from the robot's kinematic range and validated against the environment. Recommend starting from neighboring zone poses and adjusting.
- Whether `closeup` zone's "reach case edge ~1.5 m" pose requires the vitrine edge to be modeled in `environment_collision.glb` (currently not modeled per the environment notes).
- Whether the `purr` behavior is a new behavior in `behavior.py` or a variant of `hold` with a specific pose sequence.
