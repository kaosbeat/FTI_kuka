# Zone Class Engagement Ladder

## Goal
Give each zone a **class** — a rung on a per-zone **engagement ladder** — that drives the robot's hunting behavior and which camera (wide = far, close = near) is engaged. Add a **matching algorithm** that escalates/de-escalates the class (the far→close switch is gated by three criteria), add a **head-pose model** to the close camera for the "looking at the robot" criterion, and make the whole system **configurable + color-coded** from `editor.html`.

## Resolved Decisions (interviewed with the user)
1. **Taxonomy** — 7 classes: `ignore`, `scan`, `track`, `farfocus`, `approach`, `facefocus`, `retreat`. (`approach` = committed, moving closer, wide cam keeps them in sight; `retreat` = step back / break off.)
2. **Data model** — a single unified **`zone_classes`** section in `brain.json` (one source of truth). The existing `hunt` / `facefocus` groups and the per-action `cam_control` hunting entries **fold into it**.
3. **Gaze criterion** — a real **head-pose model** runs on the close-cam face crop and reports `{yaw, pitch, looking}` in `CAM_FACE.features`. (`features` is currently `{}`; the face model is a YOLO face *detector*, not a gaze estimator.)
4. **Transition graph** — escalation ladder `ignore → scan → track → farfocus → approach → facefocus`; de-escalation `facefocus → retreat → scan`; the far→close gate = **visible in close + standing still + looking at robot**.

## Grounding (current code)
- `cam_control` is keyed **by action** → `{active, mode, lock}` (`brain.json`). Hunting actions: `hunting` (wide/track/auto), `stretching` (wide/track/auto), `examining` (close/analyze/auto). Non-hunting actions (`resting`, `sleeping`, …) keep their own entries.
- Hunt state machine: `idle → tracking → facefocus → scanning` (`brain.py:50` `_hunt_state`; `tick()` `brain.py:552–609`).
- `Camera.set_intent(active, mode, lock_id, clear_lock)` (`camera.py:55–83`) — only updates supplied fields, publishes `CAM_CONTROL` on change.
- Wide↔close matching is **already by `id`**: the close cam is told the lock and reports `CAM_FACE {id, bbox, dx, dy, features}` for the locked person (`pipeline.py:428–471`, `FaceDetector.analyze` `pipeline.py:324–344` picks the face box with best IoU over the locked person bbox). `CAM_TRACK {id, dx, dy, w, h, locked}` carries the offset.
- `FaceDetector` is stateless; the close `Camera` runs the face model (`pipeline.py:362–419`). Both cameras share `W/H`.
- `zones.json` per-zone fields: `{safezone, speed, enabled, actions, …}` + `layout`.
- Editor brain-tab JS: `buildBrainForm`, `renderBrainLive`, `BRAIN_FALLBACK`, `_brainNormalizeGroup` (hunt/facefocus get validated params blocks). Flow tab connects **actions** (bidirectional), not classes — the "blue arrow" is an action link.

## The 7 Classes (camera preset + role)
| Class | Camera preset `{active, mode, lock}` | Role |
|-------|--------------------------------------|------|
| `ignore` | `{none, idle, none}` | No camera input, no action |
| `scan` | `{wide, idle, none}` | Look, don't act |
| `track` | `{wide, track, auto}` | Lock + follow from distance |
| `farfocus` | `{wide, track, auto}` | Far-distance interaction |
| `approach` | `{wide, track, auto}` | Move closer, keep in sight (wide cam) |
| `facefocus` | `{close, analyze, auto}` | Close-distance interaction |
| `retreat` | `{wide, idle, none}` | Step back / break off |

`none` for `active` = no camera engaged (the pipeline sends no `CAM_*` telemetry for that zone). `auto` lock = the current hunt lock (existing behavior).

## Data Model
### `brain.json` — new `zone_classes` section
Each class = `{camera: {active, mode, lock}, params: {...}, behaviors: [...], color: "<hex>"}`. Params inherit from the existing groups:
- `track` / `farfocus` inherit the **hunt** params: `lost_s`, `min_conf`, `centrality_weight`, `attention_guard`, `detect`, `scan`.
- `facefocus` inherits the **facefocus** params: `actions`, `action_s`, `face_lost_s`.
- `approach` adds: `approach_speed`, `keep_in_sight` (max offset to stay locked while moving), `engage_dist` (distance at which to switch to close).
- `retreat` adds: `retreat_speed`, `break_off_s` (seconds before breaking off).
- `scan` adds: `scan_s` (dwell before re-evaluating).
- `ignore`: no params.
`color` is the editor color-coding (one per class).

### `zones.json` — per-zone `class` field
Each zone gets `class: "<one of the 7>"` = the **baseline** (starting) class. The brain's runtime state is the **current** class (driven by the matching algorithm); the editor shows the baseline (editable) and the live current class.

### Migration (fold-in)
- `hunt` group (`zones: ["hunting"]`) → the `track` / `farfocus` classes (the hunting zones take `track` or `farfocus` as their baseline).
- `facefocus` group (`zones: ["stretching"]`) → the `facefocus` class.
- Per-action `cam_control` hunting entries (`hunting`, `stretching`, `examining`) → the class camera presets. **Non-hunting** actions keep their `cam_control` entries unchanged.
- `config.py`: `validate_brain_config` accepts `zone_classes`; `load_brain_config` / `builtin_brain_config` default the 7 classes.

## Matching Algorithm (transition graph)
The brain drives the per-zone **current** class. Gates:
- `ignore → scan`: zone becomes active / a person enters.
- `scan → track`: a person is detected + locked.
- `track → farfocus`: the locked person is at far distance (wide cam).
- **`farfocus → approach`** (the far→close switch): the locked person is (1) **visible in the close cam** (`CAM_FACE` present with the locked `id`), (2) **standing still** (bbox center + size stable over a short window), and (3) **looking at the robot** (`CAM_FACE.features.looking == true`). On success → move closer, keep in sight (wide cam).
- `approach → facefocus`: close enough (`engage_dist`) → switch to close cam, close interaction.
- `facefocus → retreat`: the person leaves / breaks off.
- **any → scan**: person lost.
- **any → ignore**: zone disabled / no person.

De-escalation is monotone down the ladder (`facefocus → retreat → scan`); escalation is monotone up. The "standing still" check: the brain keeps a short window (a few frames) of the locked person's bbox (center + size) and checks stability (max delta below a threshold).

## Head-Pose Model (close camera)
- Runs on the **close-cam face crop** (the `CAM_FACE` box, already selected by IoU in `FaceDetector.analyze`).
- Outputs `{yaw, pitch, looking}` into `CAM_FACE.features` (currently `{}`).
- `looking` = head **yaw within a threshold** (facing the close cam = facing the robot). The threshold is a class/global param.
- A small model (YOLO-style regressor or a dedicated head-pose net) via the existing `resolve_model` path. Adds one model + some RPI latency; the close cam already runs the face model, so this is a second model on the same camera.
- `camera_mock.py` mocks a plausible `{yaw, pitch, looking}` for debugging.

## Implementation Tasks (ordered)
1. **Config / data model**
   - `brain.json`: add `zone_classes` (7 classes, camera presets + params + behaviors + colors); add `class` to the hunting zones; fold in the `hunt`/`facefocus` groups + hunting `cam_control` entries.
   - `rtr/brain/config.py`: `validate_brain_config` accepts `zone_classes`; `load_brain_config` / `builtin_brain_config` default the 7 classes.
   - `editor.html`: `BRAIN_FALLBACK` + `_brainNormalizeGroup` handle `zone_classes`.
2. **Head-pose model**
   - `rtr/camera/pipeline.py`: add a head-pose estimator on the close-cam face crop; extend `FaceDetector.analyze` (or a new `HeadPoseDetector`) to emit `{yaw, pitch, looking}` into `CAM_FACE.features`.
   - `rtr/camera/camera_mock.py`: mock the head-pose output.
3. **Brain class state machine**
   - `rtr/brain/brain.py`: replace the hunt state machine (`idle/tracking/facefocus/scanning`) with the 7-class ladder; the current class selects the camera preset (via `set_intent`); implement the transition gates in `tick()`; the far→close gate = visible + still + looking; the "standing still" check (bbox stability window).
4. **Editor UI**
   - `editor.html` brain tab: `zone_classes` config UI (7 classes, each with camera preset + params + behaviors + color).
   - `editor.html` zones tab: per-zone `class` dropdown + **color-coding** of zones by class.
   - Live rendering: show the current class per zone from the WS `state`.
5. **Validation** (see below).

## Files Affected
- `realtimecontrol/rtr/brain.json` — `zone_classes` + per-zone `class`.
- `realtimecontrol/rtr/zones.json` — per-zone `class` field.
- `realtimecontrol/rtr/rtr/brain/config.py` — validation + defaults for `zone_classes`.
- `realtimecontrol/rtr/rtr/brain/brain.py` — class state machine + matching algorithm.
- `realtimecontrol/rtr/rtr/camera/pipeline.py` — head-pose model.
- `realtimecontrol/rtr/rtr/camera/camera_mock.py` — head-pose mock.
- `realtimecontrol/rtr/editor.html` — `zone_classes` UI + per-zone class + color-coding.

## Validation Plan
- **Config**: `validate_brain_config` round-trips a `zone_classes` config (all 7 classes present, camera presets valid, per-zone `class` valid).
- **Head-pose**: run the close cam on a test video / mock; confirm `CAM_FACE.features` carries `{yaw, pitch, looking}` and `looking` flips when the head turns away.
- **Transition graph**: a scripted scenario (person enters → escalates `scan→track→farfocus→approach→facefocus` when the 3 gates pass; breaks off → `retreat→scan` when the person leaves/turns away). Confirm the far→close gate requires **all three** criteria.
- **Editor**: load the editor, set a zone's class, confirm the color-coding updates and the `zone_classes` params are editable + validate cleanly.

## Risks / Open Questions
- **RPI load**: a third model (head-pose) on the close cam adds latency; the threshold for `looking` and the model size are tunable. (Out of scope to benchmark now.)
- **`none` camera active**: the pipeline currently assumes wide/close are both run; `active: none` must suppress `CAM_*` telemetry for that zone (confirm the `set_intent` / pipeline path handles it).
- **Non-hunting actions**: they keep their `cam_control` entries; the class system only governs hunting zones. Confirm no hunting action is left unmapped.
