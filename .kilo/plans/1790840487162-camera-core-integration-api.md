# Camera ↔ Core Integration API (RPI)

## Goal

Define the **API/protocol** that lets the vision camera (running on the RPI, where
`input_processing/trackfollowpi.py` runs) talk to the central `rtr` core, so the core's
brain can drive the cameras and react to what they see. This plan is the *contract* only —
the deep vision-pipeline integration (YOLO/face specifics, servoing gains) is a follow-up.

## Resolved decisions (from the design interview)

- **Decision ownership — brain-driven.** The RPI runs detection/tracking (YOLO+ByteTrack)
  and reports candidates + a live pixel offset. The **brain (core side)** decides which
  person to lock, when to switch wide/close camera, and issues the move command. The RPI
  *executes* the lock and reports the ongoing offset.
- **"The zone it is in" = the robot behavior zone** (`init/rest/wakeup/stretch/wander/wildwander`).
  The move command is gated/shaped by the robot's current behavior zone (its safezone),
  with the camera's pixel offset setting the specific target.
- **Transport — reuse the existing WS.** Single existing WS port (`8765`). RPI→core sends
  new `CAM_*` command types through the existing bus→brain path (telemetry modeled as
  commands). Core→RPI control is carried in the broadcast **state frame** + new **event
  frames**. Reuses `remote/connection.py` reconnect logic; no new ports/channels.
- **Two cameras — `camera` + `mode` fields.** The control frame carries both a `camera`
  field (`wide`/`close`/`both`) and a `mode` field (`idle`/`track`/analyze`); the brain
  selects the physical camera and the behavior independently.

## Topology

| Host | Runs | Role |
|------|------|------|
| **RPI** | `camera_remote` process + two physical cameras + YOLO/ByteTrack/face pipeline | Sensor + tracker. Runs where `trackfollowpi.py` runs. Sends telemetry, executes lock/camera config. |
| **Core host** | `rtr` core (engine + state machine + brain) + KUKA robot link | Decision maker. Receives telemetry, issues camera control + move commands. |

They communicate over the existing WS (core = server, RPI = client via `Connection`).

## The protocol (the API)

### Direction 1: RPI → Core (telemetry-as-commands)

New `Cmd` members in `core/commands.py`, sent by the RPI via
`Connection.send_command()`. The core's WS handler (`io/websocket.py`) already submits any
incoming JSON as a `Command` to the bus; the engine routes it to `brain.on_command`.

| Cmd | Payload | Meaning |
|-----|---------|---------|
| `cam_status` | `{camera, mode, active, fps, ok}` | Periodic health + which camera/mode is live. |
| `cam_candidates` | `{camera, candidates: [{id, x1, y1, x2, y2, conf}]}` | Detected persons on the active camera — the brain's lock candidates. |
| `cam_track` | `{camera, id, cx, cy, dx, dy, w, h}` | Locked person's live center + offset from image center (the servoing input). |
| `cam_face` | `{camera, id, bbox, features}` | Face analysis from the close camera (`features` schema to refine). |

`dx/dy` are pixels from image center (`cx - W/2`, `cy - H/2`), matching
`trackfollowpi.py`'s existing offset computation.

### Direction 2: Core → RPI (control: state frame + event frames)

The brain owns a **camera intent** = `{active, mode, lock_id}`. Broadcast two ways (both
through the existing `ws.broadcast`, called by the `Display` adapter):

- **State-frame field** — the `state` frame (`display.py::_frame`) gains a `camera` field
  carrying the current intent every tick. Authoritative; a (re)connecting RPI reads it
  immediately and needs no diffing.
- **`cam_control` event frame** — emitted when the intent *changes*:
  `{"type": "cam_control", "active": "close", "mode": "analyze", "lock_id": 3}`.
  Lets the RPI react fast without diffing the state frame.

The RPI `camera_remote` receives both (via `Connection.on_state` reading the `camera` field,
plus a new `on_cam_control` callback for the event frame) and configures the physical cameras.

### The brain's decision logic (core side)

Extended `Brain` (or a brain-resident `CameraPolicy`) receives the `CAM_*` telemetry and:

1. **Lock** — from `cam_candidates`, pick a target (recommended: highest confidence, or
   most central). Send `cam_control {lock_id}` to the RPI.
2. **Camera switch** — decide `wide` vs `close` from context (e.g. person far / no face →
   `wide`+`track`; person close + face visible → `close`+`analyze`).
3. **Move** — from `cam_track` (`dx/dy`) + the robot's current **behavior zone**, issue a
   move command to the state machine. The state machine already clamps every target to the
   current zone's safezone (`machine.py:308`), so the zone shapes the motion.
   - Recommended (simplest, reuses existing commands): issue an incremental
     `SET_LINEAR_POSE` nudge each tick proportional to the offset (robot in `hold`).
   - Alternative (cleaner long-term): extend the `track` behavior to accept an offset input.
   - Zones like `rest`/`init` may only allow a head-only `look` action (those zones declare
     a `look` action); free zones (`wander`/`wildwander`) allow full tracking.
   - probably a direction vector, based on the current pose will have to be calculated to decide what the relative motion command should be. If the camera is upside down or sideways, the core should know and keep this in mind. The RPi will just give absolote motion vectors. Is the camera is mounted on the robot arm on the toolhead, the direction can be caclulated. A calibration mechanism is a smart plan too 

## File / module layout

**Core host side (existing files, extended):**
- `rtr/core/commands.py` — add `Cmd.CAM_STATUS / CAM_CANDIDATES / CAM_TRACK / CAM_FACE` and
  `Event.CAM_CONTROL`.
- `rtr/brain/brain.py` — handle `CAM_*` commands; add camera decision logic (lock/switch/move).
- `rtr/camera/camera.py` — refactor the in-process stub into the core-side **camera
  controller**: owns the camera intent, receives `CAM_*` telemetry (via brain), exposes the
  intent to the display, emits `cam_control` events.
- `rtr/display/display.py` — add the `camera` field to the state frame; emit `cam_control`
  event frames.
- `rtr/io/websocket.py` — **no change** (telemetry-as-commands already flow through the
  existing handler; `cam_control` is broadcast via the existing `ws.broadcast`).

**RPI host side (new files, in the `rtr` package so they share the command/event defs):**
- `rtr/camera/camera_remote.py` — standalone process (a `Connection` client, reusing
  `remote/connection.py`): runs the two-camera pipeline; receives `cam_control` (configures
  active camera/mode/lock); sends `CAM_*` telemetry.
- `rtr/camera/pipeline.py` — the two-camera YOLO+ByteTrack+face logic, refactored from
  `input_processing/trackfollowpi.py` (+ the deepface/emo scripts for the close camera).
  Exposes `configure(camera, mode, lock_id)` and per-frame results (candidates, tracked
  offset, face).
- a config file for the network settigns on the RPi  

**Validation (new, on the core host):**
- `rtr/camera/camera_mock.py` — a mock camera remote (a `Connection` client) that plays back
  simulated `CAM_*` telemetry and reacts to `cam_control`. Lets us test the full
  camera↔core loop in `--sim` mode with no RPI/hardware. We can make use of the laptop cam to have a real camera image, this would help fast iteration

## Implementation steps (ordered)

1. Add the `CAM_*` `Cmd` types + `CAM_CONTROL` `Event` to `core/commands.py`.
2. Define the camera-intent structure + the core-side camera controller
  (`camera/camera.py` refactor).
3. Wire `display/display.py` to broadcast the `camera` state field + `cam_control` events.
4. Extend the brain to receive `CAM_*` telemetry and make camera + zone-gated move decisions.
5. Build the RPI `camera_remote` (`camera_remote.py` + `pipeline.py`), refactoring
   `trackfollowpi.py` into the two-camera pipeline.
6. Build the `camera_mock` for sim validation.
7. Validate (below).

## Validation plan

- **Sim loop (no RPi hardware):** run the core with `--sim` + the `camera_mock`. The mock emits
  `cam_candidates`/`cam_track`; observe the brain (a) picking a lock, (b) emitting
  `cam_control` (camera switch + lock_id), and (c) issuing zone-gated move commands. Confirm
  a (re)connecting mock picks up the current intent from the `camera` state field.
- **Protocol round-trip:** confirm every `CAM_*` command is accepted by the WS handler and
  routed to the brain, and every `cam_control`/`camera` frame is received by the mock.
- **RPI bring-up (follow-up):** point the real `camera_remote` at the core host; confirm
  both cameras stream, the brain can switch wide↔close, and lock + move work end-to-end.

## Open questions / decisions to refine (not blocking the API)

- Exact servoing gain/axis mapping (pixel offset → joint/Cartesian delta) per zone. This will be decided in a field test, using the calibration tool, per zone action, per camera
- Per-zone motion policy table (which zones allow full tracking vs head-only `look`).
- Close-camera `features` schema (landmarks? identity? emotion?).
- Candidate reporting rate (every frame vs on-change) and the lock-selection heuristic.
- Whether the RPI camera remote needs WS auth (the current WS has none).
