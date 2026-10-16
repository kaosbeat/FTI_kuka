# RTR: Blender-editable 3D environment + zone/action editor page

## Goal

1. A Blender-editable **environment** (GLB) rendered in the existing 3D view of
   `realtimecontrol/rtr/client.html`.
2. A separate **editor page** (`editor.html`) to create/edit/enable/disable zones and
   actions, with live preview in `client.html` (hot reload into the running core).

## Decisions (agreed with user)

- Core gains a small **HTTP server** (default port **8766**) that serves `client.html`,
  `editor.html`, shared JS, and `assets/` — required because browsers block loading
  local assets from `file://` pages.
- Environment format: **GLB** (Blender exports natively; three.js `GLTFLoader`; one file).
- Zone/action data moves from hardcoded `rtr/state/zones.py` to **`zones.json`**;
  the editor saves via an HTTP endpoint; the running core **hot-reloads** so
  `client.html` reflects changes instantly (no restart).
- **Enabled flags** per zone and per action (default true). Disabled zone: core rejects
  `goto_zone` to it, UI dims it; robot already inside keeps running and can still exit.
  Disabled action: core rejects `play_action`, UI dims/hides it. Both stay editable.
- `editor.html` has its **own 3D preview** via the Robot3D builder extracted from
  `client.html` into a shared JS file; mirrors live state over WS and can step through
  an action's poses locally (ghost arm) without the core.

## Current state (facts)

- Core: `main.py` wires `StateBus` + `Engine` (20 Hz tick) + `Brain` + `StateMachine` +
  `Zones` (hardcoded dict in `rtr/state/zones.py`) + WS server (`rtr/io/websocket.py`,
  port 8765, JSON commands in / state snapshots out).
- `client.html`: self-contained page, opens from `file://`, auto-connects to
  `ws://localhost:8765`. 3D view = hand-built schematic (cylinders/spheres) from the
  URDF joint chain (`JOINTS` const), Z-up scene (`camera.up = +Z`), two arms (current
  orange / target green), OrbitControls, three.js **r128** from CDN (non-module
  `examples/js` builds).
- `client.html` currently **hardcodes** the zone table in JS (`ZONES` const mirroring
  `zones.py`) to draw axis bars + dim unreachable zones; action buttons hardcoded to
  `["breathe","look"]`.
- `StateBus.submit` is thread-safe (queue) — the HTTP handler (separate thread) can
  submit commands to the engine.
- `POSES` / `LIN_POSES` (in `zones.py`) are read by `rtr/io/midi.py` at module level.
- Blender is **not installed** in this environment → the starter GLB must be generated
  without Blender (stdlib-only Python); the user edits it in their own Blender.
- Existing KR60 link meshes (`*.dae`) are **not** used by `client.html` (schematic only);
  out of scope.

## File map

```
realtimecontrol/rtr/
├── client.html             (modified)
├── editor.html             (new)
├── rtr3d.js                (new: shared Robot3D + helpers, extracted from client.html)
├── zones.json              (new: generated once from current zones.py data)
├── assets/
│   ├── environment.glb     (new: starter scene, Blender-editable)
│   └── make_environment.py (new: stdlib-only generator for environment.glb)
└── rtr/
    ├── state/zones.py      (modified: JSON loader, enabled accessors)
    ├── state/machine.py    (modified: reject disabled zones/actions)
    ├── brain/brain.py      (modified: first *enabled* action for action mode)
    ├── core/commands.py    (modified: + Cmd.RELOAD_ZONES, Event.ZONES_CHANGED)
    ├── core/engine.py      (modified: zone_loader + reload_zones(), handles RELOAD_ZONES)
    ├── display/display.py  (modified: zones_changed frame)
    ├── io/httpserver.py    (new: static files + /api/zones GET/POST)
    ├── io/midi.py          (modified: POSES/LIN_POSES from loaded data)
    └── config.py           (modified: + http_port, zones_path)
main.py                     (modified: wire HTTP server + zone loader; --http-port flag)
```

## Tasks (ordered)

### 1. Zone data → zones.json + loader + enabled flags

- Create `realtimecontrol/rtr/zones.json` from the current `ZONES`, `POSES`,
  `LIN_POSES` dicts (one-time generation; keep the built-in dicts in `zones.py` as
  fallback defaults). Schema:
  ```json
  {
    "zones": { "<name>": {
        "enabled": true,
        "startpos": [6 numbers], "exitpos": [6 numbers],
        "safezone": [[lo,hi] x6], "exits": ["<zone>", ...],
        "speed": number,
        "actions": { "<name>": {"enabled": true,
                                 "pos": [[6 numbers] xN], "speed": [N numbers]}}
    }},
    "poses": {...}, "lin_poses": {...}
  }
  ```
- `rtr/state/zones.py`:
  - `load_state_data(path) -> dict` (JSON, with validation; on failure raise/return
    built-ins — caller decides).
  - `Zones` keeps working over the raw dict; add `Zone.enabled` (default true when
    absent) and `Zone.action_enabled(name)` accessors.
  - `main.py` / `Config`: `zones_path` (default `zones.json` next to `main.py`);
    load at startup, fall back to built-in `ZONES` + warning if file missing/corrupt.
- `rtr/io/midi.py`: take `POSES` / `LIN_POSES` from the loaded data (constructor arg,
  wired in `main.py`) instead of module-level import of the hardcoded dict.

### 2. Core: hot reload of zones

- `core/commands.py`: `Cmd.RELOAD_ZONES = "reload_zones"`,
  `Event.ZONES_CHANGED = "zones_changed"`.
- `core/engine.py`: constructor takes `zone_loader: Callable[[], dict]`; in the command
  drain, on `RELOAD_ZONES` call `self.reload_zones()`:
  - reload data from file, build new `Zones`;
  - keep `machine.current_zone` if it still exists, else reset machine to `"init"`
    (pose unchanged);
  - swap `machine.zones` and `brain.zones`;
  - `bus.publish(Event.ZONES_CHANGED, <zone names>)`.
- `rtr/state/machine.py`: `request_zone` rejects disabled zones (log, return False);
  `play_action` rejects disabled actions.
- `rtr/brain/brain.py`: `set_mode("action")` picks the first **enabled** action.
- `display/display.py`: on `ZONES_CHANGED` send `{"type": "zones_changed"}`.

### 3. HTTP server (new `rtr/io/httpserver.py`)

- Stdlib only: `http.server.ThreadingHTTPServer` + `BaseHTTPRequestHandler` in a daemon
  thread (no new dependencies). Config: `http_port` (default **8766**), `enable_http`.
- Serves the `realtimecontrol/rtr/` directory (path-traversal safe):
  - `GET /` → `client.html`
  - `GET /client.html`, `/editor.html`, `/rtr3d.js`, `/assets/<file>` → static
- API:
  - `GET /api/zones` → current live zone table (from the running `Zones`).
  - `POST /api/zones` → validate full table (same schema as task 1): zones dict,
    6-number startpos/exitpos, safezone pairs lo<hi, exits reference existing zones,
    actions with N×6 poses + N speeds, enabled flags bool. On success: **atomic write**
    (`tmp` + `os.replace`) to `zones.json`, then `bus.submit(Command(RELOAD_ZONES))`.
    On validation failure: HTTP 400 + error message, file untouched.
- `main.py`: start it alongside the WS server (before robot connect, same order as
  today); add `--http-port` CLI flag; graceful "port busy → disabled" log.

### 4. client.html: dynamic zones + environment + shared JS

- Extract the Robot3D IIFE (+ `fmt` helper) into `rtr3d.js`; `client.html` and
  `editor.html` both load it.
- **Environment**: add `GLTFLoader.js` (three r128 CDN, same pattern as OrbitControls).
  On init, load `assets/environment.glb`; add to scene with `rotation.x = -PI/2`
  (glTF is Y-up, this scene is Z-up) at the robot base origin; on load failure log
  "environment not loaded" and continue (file:// usage degrades gracefully).
- **Dynamic zone table**: keep the current JS `ZONES` const as `FALLBACK_ZONES`;
  on WS connect fetch `GET /api/zones` (same origin, port 8766) and replace it; on a
  `zones_changed` event re-fetch and rebuild. If the fetch fails (e.g. file:// usage)
  keep the fallback.
- Rebuild zone buttons + action buttons from the fetched table (actions of the current
  zone), dimming disabled zones/actions (existing dim logic for unreachable exits
  stays).
- Everything else (axes, bars, log, commands) unchanged.

### 5. Starter environment (no Blender available here)

- `assets/make_environment.py`: **stdlib-only** Python script that writes a valid
  glTF 2.0 binary (`environment.glb`) with a simple starter scene, meters, Blender
  Z-up conventions (the exporter-style Y-up conversion is handled by the loader
  rotation in task 4):
  - `floor`: 10×10 m plane at z=0 (dark gray),
  - `pedestal`: cylinder r=0.22 h=0.48 at origin (matches the schematic pedestal),
  - `wall_x` / `wall_z`: two corner walls (~8 m × 3 m) so the robot is not boxed in,
  - simple base-color materials (pbrMetallicRoughness), named nodes.
- Generate `assets/environment.glb` by running the script.
- User workflow (documented in the README): open `environment.glb` in Blender → edit →
  `File > Export > glTF 2.0 (.glb)` over the same file.

### 6. editor.html (new page)

- Same dark theme/styling as `client.html`; loads `rtr3d.js`.
- Connects to the same WS as client.html (live mirror: current arm follows the robot;
  state readout: zone/mode/action/speed/moving).
- Loads the zone table from `GET /api/zones` (fallback to built-ins if core is down).
- Layout:
  - **Left**: zone list (buttons) + add/remove zone.
  - **Center**: zone form — enabled toggle, speed, startpos (6), exitpos (6),
    safezone (12 inputs: lo/hi × 6), exits (checkboxes over other zones), actions
    list: per action enabled toggle, name, pose rows (6 inputs each, add/remove row),
    per-pose speed values.
  - **Right**: 3D preview (Robot3D) + per-axis safezone bars (like client.html) +
    ghost-arm stepping: the *target* arm shows the selected action's current pose row;
    prev/next buttons step through it locally (works without the core).
- Buttons:
  - **Save** → `POST /api/zones` with the whole table; show success/error. The core
    hot-reloads, so `client.html` (and the live robot) pick it up immediately.
  - **Play** (per action) → sends `play_action` over WS so the action can be previewed
    on the live robot / in `client.html`.
- Client-side validation before POST: all poses 6 numbers; exits reference existing
  zones; warn (non-blocking) when a pose falls outside its zone's safezone.

### 7. Docs

- `realtimecontrol/rtr/README.md`: short sections for the HTTP server (port 8766,
  pages + `/api/zones`), `zones.json` as the data source, the editor page, and the
  Blender → GLB workflow.

## Edge cases / failure modes

- `zones.json` corrupt at startup → core falls back to built-in `ZONES` + warning.
- Bad `POST /api/zones` → 400, file untouched (atomic replace only on success).
- Current zone deleted on reload → machine resets to `"init"` (pose unchanged);
  current zone disabled on reload → robot keeps running there, can still exit.
- HTTP port 8766 busy → server disabled with a log line; WS + everything else works.
- Pages opened from `file://` → still work (WS to localhost); environment GLB and
  `/api/zones` unavailable → graceful fallback (built-in zones, no environment).

## Validation

1. `python main.py --sim --no-midi --no-sound` (venv `/home/kaos/.pyenv/versions/kukart`).
2. `curl http://localhost:8766/api/zones` → zone table JSON; `GET /client.html`,
   `/editor.html`, `/assets/environment.glb` → 200.
3. POST a modified table (add a zone / disable an action) → 200; `zones.json` on disk
   updated; core logs the reload; a WS client receives the `zones_changed` frame.
4. POST invalid data (pose with 5 values, exit referencing missing zone) → 400, file
   unchanged.
5. GLB structural check: magic `glTF`, chunk lengths, JSON chunk parses, accessors
   consistent; then visual check in the browser (user).
6. Browser: `http://localhost:8766/client.html` → environment renders around the
   schematic robot; edit a zone/action in `editor.html` → Save → `client.html` buttons/
   bars update live; Play an action → robot (sim) executes it.

## Out of scope

- Replacing the schematic robot with the real KR60 DAE meshes / URDF rigging.
- Generating a `.blend` file (Blender not installed here); the GLB is the source of
  truth the user edits in their own Blender.
- Editing `SOFTWARE_LIMITS` / `LIMITS` from the editor (kept in Python).
- Auth on the HTTP/WS interfaces (local-machine tooling, same as today).
