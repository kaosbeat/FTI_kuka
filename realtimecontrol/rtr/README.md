# RTR - RealTimeRobot

A clean, modular control system for the KUKA KR60. This rewrites and reorganises the
earlier iterations in `kukart/`, keeping only what works and separating the tangled
`tidalkuka.py` into small, testable modules.

Everything runs in one Python process (the **core**), which is the hub of the system.
Hardware and external tools are **adapters** that plug into the core over clean
boundaries, so none of them know about the others.

```
                  ┌──────────────────────────────────────────────┐
                  │                  CORE  (asyncio)             │
    ┌───────────┐  │  ┌────────┐   ┌─────────┐   ┌────────────┐  │
    │  MIDI in  │──┼──┤  Brain │──▶│ State-  │   │   Robot    │  │
    │ (rtmidi)  │  │  │(commands)│ │ Machine │   │ (kukapy)   │  │
   └───────────┘  │  └────────┘   └─────────┘   └─────┬──────┘  │
                  │        │                          │          │
   ┌───────────┐  │        │  ┌───────────┐           │ (thread) │
   │  WebSocket│◀─┼───────┘  │  StateBus │           ▼          │
   │ (websockets)│──┼────────▶ (events)  │      ┌─────────┐    │
   └───────────┘  │  ▲          └─────────┘      │ KUKA    │    │
                  │  │                           │  KR60   │    │
                  │  │   ┌─────────┐  ┌─────────┐│  (real  │    │
                  │  │   │ Display │  │ Camera  ││  or sim)│    │
                  │  └───┤ (P5live)│  │ (vision)│  └─────────┘    │
                  │      └─────────┘  └─────────┘                  │
                  │      ┌─────────┐                               │
                  │      │  Sound  │  MIDI out (sound is MIDI-     │
                  │      │         │  controlled)                  │
                  │      └─────────┘                               │
                  └──────────────────────────────────────────────┘
```

## The core loop

The engine ticks at a fixed rate (`--tick`, default 20 Hz). Each tick it:

1. reads the current joint pose from the **robot**,
2. folds any queued commands (from MIDI / WebSocket) into the **machine** via the **brain**
   (the thin command interpreter),
3. asks the **machine** for the next target pose (its `step` driver advances the transition
   or dispatches the current behaviour mode to the policies in `state/behavior.py`),
4. commands the robot to that target,
5. publishes a state **snapshot** to every subscribed adapter (display, camera, sound),
   and a change event (`zone_changed` / `mode_changed` / `action_changed`) when one of
   those changes.

Commands (from MIDI or WebSocket) never touch the robot directly. They are queued on
the `StateBus` and the engine folds them into the next tick. That keeps all motion
decisions in one place and makes the robot thread-safe by construction.

**Robot I/O runs in threads.** The KUKA EKI link is a blocking request/response over a
single socket, so a slow `get_curjpos` / `move` would stall the asyncio loop and freeze
the WebSocket + snapshots. The engine calls every robot method through
`asyncio.to_thread(...)` (see `core/engine.py`), so the tick loop, the WebSocket server,
and the broadcast all stay responsive even while a motion command is in flight. This is
the same idea as `tidalkuka.py`, which did all robot I/O in a daemon thread.

## The connection model (how the real robot attaches)

This is the part that used to be the source of confusion, so it is spelled out here.

The KUKA EKI link is **reversed** compared to a normal "client dials a server":

| Role        | Side |
| ----------- | ---- |
| **TCP server** | Python (this process) — listens on a port, waits for the KRC to connect |
| **TCP client** | KRC (the robot) — EKI initiates the *outbound* connection to Python |

So **Python is the server** and the **robot is the client that dials in**. The PC never
connects to the robot; the robot's `KUKAPY_SERVER.SRC` program connects out to the PC.

`kukapy.robot.Robot` implements this: `connect()` binds a socket to `0.0.0.0:18735`,
prints `Listening on :18735 — start KUKAPY_SERVER on the pendant now...`, and then
**blocks in `accept()`** until the KRC dials in. Every `move` / `get_*` call is a
request/response over that one socket (XML frames, `SENDFLAG=1` null-delimited).

This is why `tidalkuka.py` "just worked": it called `robot.connect()` at module level,
*before* its event loop even started, so the blocking `accept()` never touched the loop.

## Startup sequence (`main.py`)

`main.py` wires the core + adapters and runs until Ctrl-C. The order matters:

1. Build the `StateBus`, `Zones`, `StateMachine`, `Brain`, `Engine`, and the adapters
   (WebSocket, display, sound, camera, MIDI). No robot I/O yet.
2. **Start the WebSocket server first** (`await ws.start()`). The control interface is
   up and reachable before we wait for the robot.
3. **Connect the robot in a thread** (`await asyncio.to_thread(robot.connect)`). Because
   `connect()` is a blocking `accept()`, running it on the event loop would freeze
   everything (including the WebSocket) for up to the 60 s connect timeout. Running it in
   a thread keeps the loop alive while we wait for the KRC to dial in. On `--sim` this
   returns immediately (the sim has no real socket).
4. Seed the state machine to the robot's real pose and start the engine loop.
5. Block until SIGINT, then shut down in order: engine → WebSocket → MIDI → sound → robot.

`python main.py` (no `--robot`) defaults to `--sim`. For the real robot the start order
is: **run `python main.py --robot` first** (it prints the `Listening on :18735` line),
**then** start `KUKAPY_SERVER` on the teach pendant. The KRC dials in and the engine
starts folding commands into ticks.

## Modules

| Path              | Responsibility                                                        |
| ----------------- | --------------------------------------------------------------------- |
| `core/engine.py`  | The asyncio tick loop that drives everything (robot I/O in threads).  |
| `core/bus.py`     | `StateBus`: thread-safe state store + command queue + event pub/sub.  |
| `core/commands.py`| Command + event + Snapshot type definitions (the core's API).         |
| `state/machine.py`| `StateMachine`: the single owner of the robot's composite state (zone, mode, action, target, speed, flags, cadence); drives the transition and the behaviour each tick. Deterministic. |
| `state/behavior.py`| The motion policies (wander / random / track) + clamp helpers, as free functions the machine's `step` dispatches to. |
| `state/zones.py`  | Zone/pose data (safe areas, start/exit poses, per-edge `exitposes`, per-zone default `mode`, actions, named poses). |
| `brain/brain.py`  | `Brain`: a thin **command interpreter** — maps `Command` objects onto the machine's operations; holds no robot state. The plug-in seam for the LLM / realtime control. |
| `robot/base.py`   | `RobotBase` interface (implemented by Kuka and Sim).                  |
| `robot/kuka.py`   | Real robot via `kukapy` (the EKI TCP server).                          |
| `robot/sim.py`    | Headless robot for development without hardware.                       |
| `robot/helpers.py`| Joint-limit math (`fitlimits`, `posSafe`, `comparelist`).             |
| `camera/camera.py`| Camera control adapter (track/follow, analysis).                       |
| `display/display.py`| Display adapter: turns each snapshot into a P5live frame over the WS. |
| `sound/sound.py`  | Sound adapter: configurable MIDI-out, data-driven from `sound.json`.  |
| `patches/patches.py`| Patches adapter: hydra screen code, data-driven from `patches.json`, matched to zone/mode/action. |
| `io/midi.py`      | MIDI-in adapter: learned-first dispatch from `midi.json`, falling back to the legacy hardcoded protocol for unmapped messages. |
| `io/websocket.py` | WebSocket server: the single bridge for external control + state.     |
| `io/httpserver.py`| HTTP server: serves the pages + `/api/zones`, `/api/sound`, `/api/patches`, `/api/midi`, `/api/limits`, `/api/screen` (port 8766, stdlib-only).|
| `client.html`     | Browser control page (served over HTTP; WebSocket client + patch chooser). |
| `editor.html`     | Zone/action + sound + patches editor page (create/edit/enable-disable; hot-reloads core).|
| `render.html`     | Fullscreen hydra render page (WebSocket-driven; the matched patch, re-eval on state change).|
| `chatbot.py`      | Optional LLM brain: senses free-form input and answers the core with a JSON command. |
| `rtr/remote/tui.py` | Remote TUI (urwid): a terminal/handheld client that groups the current zone's actions by kind, marks loops (`↻`) and targets, and plays an action or `goto_zone`s to an exit. |
| `rtr3d.js`        | Shared Robot3D builder: the real KR60 (kr60ha xacro chain + visual STL meshes) + helpers (used by client + editor). |
| `zones.json`      | On-disk zone/pose data — the editable source of truth.                |
| `sound.json`      | On-disk MIDI-out mapping (channel + zone/mode/action messages).       |
| `patches.json`    | On-disk hydra screen code (default + zone/mode/action patches).       |
| `midi.json`       | On-disk MIDI-in mapping (input port + learned key → target mapping).  |
| `assets/`         | Blender-editable environment + tool GLBs and their stdlib-only generators. |

## The state machine

The base of everything is the state machine. Given a specific **zone** the robot is
allowed to do some things: go to specific areas, activate extra tools, music, screen,
camera.

The `StateMachine` is the **single owner of the robot's composite state** —
`(zone, mode, action)` plus the in-progress transition, the current target pose, the
move speed, the informational flags, and the behaviour cadence. It used to be split:
the machine owned (zone, transition, action) and the `Brain` owned (mode, target,
flags, cadence). That split is gone. The `Brain` is now a **thin command interpreter**
that maps incoming `Command` objects onto the machine's operations and holds no state of
its own; the per-tick decision is made by the machine's `step` driver, which dispatches
to the pure motion policies in `state/behavior.py` (wander / random / track + the clamp
helpers). This lets the machine be driven and tested in isolation, and keeps the
`Brain` as the plug-in seam where an LLM or realtime controller changes what a command
does.

A **zone** is an axial area. Given the 6 axes, it defines what safe positions at all
times when in a current zone:

```python
"safezone": [(-94, 122), (-98,-96), (13,19), (-2,2), (85,95), (-357,357)],
```

If the robot's axes are all within the ranges specified, it can safely move to any of
the other locations in that zone.

On top of the per-zone safezone there is a **hardware floor**: the per-axis limits from
the `kr60ha` xacro (`HARDWARE_LIMITS` in `state/zones.py`, exposed read-only by
`GET /api/limits`). Every commanded pose is clamped to the **intersection** of the
current zone's safezone and the hardware limits (`effective_limits`), so a safezone wider
than the hardware range safely shrinks to the hardware range. Note **A6** (the rotary
wrist) is now limited too — it was previously treated as free. The state machine applies
this floor to every pose it emits (wander / random / action / track / hold), so the
published `target_pose` snapshot is always within the floor. Zone-transition targets are
the one exception: the exit/start hand-off poses sit outside the current zone's safezone
by design, so `step` floors them to the **hardware** limits only (`behavior.hardware_clamp`)
— flooring them to the safezone would move them and the transition would never arrive
(guarded by `tests/test_transition.py`).

A zone also declares:

- `startpos` / `exitpos` — the pose used when entering / leaving the zone,
- `exits` — the zones this one can transition to,
- `actions` — named motions possible while in the zone,
- `speed` — default move speed while in the zone.

### Zones

The live graph (see **`zones.json`** — the editable source of truth — for the exact
limits and the seeded per-zone default `mode`):

```
init ─▶ rest ─▶ wakeup ─▶ stretch ─▶ wander ─▶ wildwander
        ▲        │          ▲           │
        └────────┘          └───────────┘   (back-edges: rest→init,
                                             wakeup→rest, stretch→wakeup,
                                             wander→stretch, wildwander→wander)
```

A zone's `exits` list is the set of zones it can transition to (the edges above,
including the back-edges). Each zone declares an optional **default `mode`** (the
behaviour it adopts on entry — `init`/`rest` → `hold`, `wakeup`/`stretch` → `action`,
`wander`/`wildwander` → `wander`); on committing a transition the machine adopts that
default and clears any playing action (a zone without a `mode` keeps the current mode).

A `goto_zone` is **path-routed** through the zone `exits` graph: `Zones.find_path(from,
to)` (a BFS) returns the shortest sequence of zones, and `StateMachine.request_zone`
builds the step list by walking it — for each hop it appends the current zone's
**per-edge exit pose** (`Zones.exitpose_for(next)`: the `exitposes[next]` override if
declared, else the zone's single `exitpos`) then the next zone's `startpos`. A target
with no path is rejected (logged, no transition); the old direct transition is the
path-length-2 case. `StateMachine.update` advances the step list one pose per tick;
nothing blocks.

### Actions

When in a zone, specific actions are possible. An action is a sequence of poses, e.g.
*rotate 15° on A3, then -30° on A4*. It wraps around and awaits the next action command.
When an action would go outside the current zone, it ends at its limit and goes to await
the next action.

Some actions do not act on motors but enable the camera or an image on the display.

Actions are **per-zone**: each zone's `actions` table lists only the actions that zone
*offers* (there is no shared catalog — each zone owns its actions independently, so the
same action name can carry different poses in different zones). An action a zone does not
list simply does not exist there — `play_action` rejects it, so the robot can never perform
an action the zone does not allow. `editor.html` edits a zone's own actions (the "+ add
action" button adds to the current zone), and `client.html` only shows — and only lets you
select — the actions the current zone offers.

### Action kinds, looping, and targets

Each action can declare three optional fields (all backward compatible — an action without
them behaves exactly as before):

- **`kind`** — `internal` (the default), `entry`, or `exit`. It is a semantic label the
  editor and flow tab use to position and colour the action; the state machine's runtime
  behaviour is driven by `loop` + `target` below.
- **`loop`** — `true` (the default) wraps the action around on its last pose; `false`
  makes it **single-run** — it does not wrap and instead hands off via `target` when its
  last pose completes.
- **`target`** — the hand-off, an object `{zone, action, pose}`:
  - `zone` — the zone the action hands off to (must name an existing zone); defaults to
    the current zone.
  - `action` — the action to activate in the target zone on arrival (the **entry action**
    hook).
  - `pose` — a 6-number hand-off pose (the exit position the robot leaves at).

The hand-off is resolved by `_advance_after_action` (`state/machine.py`) the moment a
single-run action completes its last pose:
- **no target** → the action stops (the robot holds the last pose).
- **target in the same zone** → plays the target action (falls back to the zone's default
  mode when there is no valid next action).
- **target in another zone** → begins an **exit transition**: leaves at the hand-off
  `pose`, travels to the target zone, and activates the target `action` on arrival
  (`_commit_zone` plays the entry action and switches to `action` mode). An unreachable
  target stops the action and falls back to the zone's default mode.

An **exit** action is the natural place for a cross-zone `target`; the flow tab draws it as
an orange arrow (see the flow-tab section below).

## Control

The robot is controlled from the central server (this process). It keeps track of the
state, takes inputs, and decides what to do next. Some of this is programmed, some can
be output from a small LLM, some can be realtime control from software, hardware, or
MIDI instruments.

Inputs are **commands** (see `core/commands.py`). They are flat JSON, e.g.
`{"cmd": "goto_zone", "zone": "rest"}`:

| Command           | Meaning                                            |
| ----------------- | -------------------------------------------------- |
| `goto_zone`       | Transition to a zone (path-routed through the exits graph). |
| `set_mode`        | `wander` / `random` / `action` / `track` / `hold`. |
| `play_action`     | Play a named action in the current zone.           |
| `set_joint_pose`  | Move to a specific 6-axis joint pose (then `hold`).|
| `set_linear_pose` | Move to a cartesian pose (linear). Not simulated.  |
| `adjust_limit`    | Nudge a wander drift limit A1/A2/A3 (MIDI CC 20/21/22). |
| `set_flag`        | Toggle an informational flag (`wandermode`, `dynmode`, ...). |
| `random_wrist`    | Randomise A4/A5 of the current target (then `hold`).|
| `set_screen_patch`| Push a hydra patch to the screens (sets the `Patches` screen override); `code: null` clears it so the screens follow the live state match. |
| `stop`            | Defined in the `Cmd` enum but **not handled** (the brain's `on_command` has no case for it) — currently a no-op. |

Outputs are **events** the `StateBus` publishes: a full `snapshot` each tick, plus
`zone_changed` / `mode_changed` / `action_changed` — the engine compares the machine's
`(zone, mode, action)` to the previous tick and publishes each that changed (so the
camera and display handlers, which subscribe to them, fire for real). The `snapshot` is a
flat JSON structure (`zone`, `mode`, `action`, `joint_pose`, `cart_pose`, `target_pose`,
`speed`, `flags`, `moving`) — this is what the display and any external client see.

### The behaviour modes

The `StateMachine` owns the behaviour policy. Its per-tick `step` driver dispatches to
the pure policies in `state/behavior.py` (each takes the machine explicitly and reads the
zone / `limitadjust` / `track_speed` / `tick_hz` from it):

- **wander** (default): gentle continuous drift of A1–A3 inside the current zone's
  safezone; the wrist (A5) is derived as `-(A2+A3)` and clamped.
- **random**: jump to a new random pose in the safezone at a fixed cadence.
- **action**: step through the current zone's active action, one pose per cadence.
- **track**: advance A1 at a fixed rate, clamped to the safezone.
- **hold**: keep the last commanded pose.

A `set_joint_pose` / `random_wrist` command sets the target directly and switches to
`hold` (the robot goes there and stays, until told otherwise). Zone transitions are
handled by the machine; while one is in progress its `step` forwards the transition
target (floored to the hardware limits only — see the hardware-floor note above). The
`Brain` no longer participates in the per-tick decision — it only folds commands into
the machine (`on_command`).

### P5live / WebSocket / client.html

The `io/websocket.py` adapter is the **single bridge** to P5live and any external
client. Two directions, one port (default 8765):

- **In** — clients send JSON commands; the server submits them to the `StateBus`.
- **Out** — the core broadcasts a state frame to every connected client (driven by the
  `Display` adapter).

`client.html` is a browser page (no build step) that opens a WebSocket to the core and
is the hand-rolled stand-in for P5live. It shows the current state (zone / mode / action
/ speed / moving / flags), all six axes with their target and position within the current
zone's safezone, and a live log; and it sends commands (zone buttons, mode buttons, action
buttons, a 6-axis joint-pose editor, wander-limit sliders, random-wrist). It also has a
**patch chooser** that previews a hydra patch and pushes the selected slot to the screens
(`set_screen_patch`); picking **auto** clears the override so the screens follow the live
state (see the Patches section). The action
buttons are built from the current zone's action table, so an action the zone does not
offer has no button at all, and a disabled one is shown but not selectable. The zone
buttons keep every zone but enable only the current zone's **available next states** —
its declared `exits` and the **reverse exits** (the zones whose `exits` include the current
one, where the robot could have come from) — dimming the rest (the core still accepts any
reachable zone; the UI only restricts what it offers). It auto-connects
to `ws://localhost:8765`, retrying every 2 s. It is best served over the HTTP server
(`http://localhost:8766/client.html`); opened from `file://` it still works against the
WebSocket, but the environment GLB, the KR60 meshes, and the live zone table are
unavailable (it falls back to its built-in zones, a placeholder arm, and no environment).

### The remote TUI (terminal / handheld)

`rtr/remote/tui.py` is a terminal client for driving the core from a handheld or a second
screen. It opens the same two links as the other clients — a **WebSocket** to 8765 for live
state + commands and the **HTTP** zone table on 8766 — and renders the current zone's
actions in a urwid list. It is the canonical `run()` for the remote module; the top-level
`remote/tui.py` is a thin launcher that delegates to it.

The list groups the current zone's actions by their `kind` (see the action-model section
above) and ends with the zone's **available next states** — its declared `exits` plus the
**reverse exits** (the zones whose `exits` include this one, where the robot could have
come from):

- **ENTRY / INTERNAL / EXIT** — the zone's actions, grouped by `kind`. A looping action is
  marked `↻`, and an action with a `target` is annotated with its hand-off (`→ <zone>` or
  `→ <action>`). A disabled action is dimmed.
- **GO TO** — the zone's `exits` (declared order) followed by the reverse-only entries,
  marked `←` (a zone you could have come from, in a one-directional graph); selecting one
  sends a direct `goto_zone`.

Selecting an **action** (any kind) plays it: it sends `play_action` + `set_mode action`, and
the core's `_advance_after_action` resolves an exit action's `target` hand-off on completion.
Navigation is manual: `↑`/`↓` move the cursor, `enter` activates the selected row, and
`q`/`escape` quits.

```bash
# from the rtr/ directory — the top-level launcher, or the canonical module directly
python remote/tui.py --core-host 127.0.0.1 --core-port 8765
python rtr/remote/tui.py --core-host 127.0.0.1 --core-port 8765
```

The pure navigation logic (`build_items`, `item_label`, `activation_commands`, and the
`action_kind` / `action_loops` / `action_target` / `action_enabled` helpers) is exercised
headlessly in `tests/test_remote_tui.py`.

### The HTTP server, editor, and zones.json

The core also runs a small **HTTP server** (`io/httpserver.py`, stdlib-only, default port
**8766**, `--http-port` / `--no-http`). It exists because browsers block loading local
assets (three.js, the environment GLB, shared JS) from a `file://` page. It serves:

- `GET /` → `client.html`, plus `/client.html`, `/editor.html`, `/render.html`,
  `/rtr3d.js`, `/assets/<file>` (path-traversal safe),
- `GET /api/zones` → the current zone data table (read fresh from `zones.json`),
- `POST /api/zones` → validates the full table and, on success, writes it atomically to
  `zones.json` and submits a `reload_zones` command so the running core **hot-reloads**
  (the robot keeps running; if the current zone was deleted the machine resets to `init`).
  On validation failure the file is untouched and a 400 is returned.
- `GET /api/limits` → the per-axis **hardware limits** (a static constant from the
  `kr60ha` xacro). Read-only — the hardware limits are fixed by the robot's mechanics.
- `GET /api/patches` → the current **patches** table (hydra screen code; read fresh from
  `patches.json`, falling back to the running table / built-in default).
- `POST /api/patches` → validates the table; on success writes it atomically to
  `patches.json` and submits a `reload_patches` command so the running core **hot-reloads**
  (mirrors `/api/zones`). On validation failure the file is untouched and a 400 is returned.
- `GET /api/screen` → the **hydra patch** (JS) rendered on the tool's screen: the `default`
  entry of the patches table (data-driven; see the Patches section). Read-only.
- `GET /api/midi` → the current **MIDI-in** mapping (read fresh from `midi.json`, falling
  back to the running table; see the MIDI-in section).
- `POST /api/midi` → validates the mapping; on success writes it atomically to `midi.json`
  and submits a `reload_midi` command so the running core **hot-reloads** (mirrors
  `/api/zones`). On validation failure the file is untouched and a 400 is returned.
  (`/api/midi/ports` and `/api/midi/learn` are the extra MIDI-in endpoints below.)

**The tool screen.** The tool GLB (`assets/tool.glb`) carries a red mesh that stands in for
a screen. `rtr3d.js` renders a hydra patch onto a dedicated canvas and maps that canvas onto
the red mesh, so the tool shows a live WebGL image. The patch the screen shows is the
**core's resolved patch**, pushed in every `state` frame (see the Patches section) — so the
3D tool screen follows the same live zone/mode/action matching as the fullscreen render page
and the client preview. `GET /api/screen` (the `default` entry of the **patches** table,
`patches.json`) is only the **seed** shown before the first state frame arrives (and the
fallback when the core is unreachable). The hydra canvas has a **configurable resolution**
(`SCREEN_RESOLUTION` in `rtr3d.js`, default `640x360`; overridable per view via
`Robot3D.init`'s `screenResolution` option) — that is the size to design patches to.

**`zones.json`** is the editable source of truth for zones, actions, and the named poses
(`poses` / `lin_poses`). The built-in dicts in `state/zones.py` remain the fallback if the
file is missing or corrupt. Each zone and action has an **`enabled`** flag (default true):
a disabled zone is rejected by `goto_zone` (the UI dims it; the robot already inside keeps
running and can still exit), and a disabled action is rejected by `play_action`. A zone may
also declare an optional **`mode`** (its default behaviour on entry — `init`/`rest` →
`hold`, `wakeup`/`stretch` → `action`, `wander`/`wildwander` → `wander`; a zone without a
`mode` keeps the current mode on entry) and an optional **`exitposes`** map
(`{"<target-zone>": [6 numbers]}`) that overrides the single `exitpos` for a specific
exit edge (the flow tab edits both; see the editor section). Both are backward compatible —
a `zones.json` without them works exactly as before.

**`editor.html`** is the zone/action editor. It has its own 3D preview (the shared
`rtr3d.js` Robot3D), mirrors the live robot over the same WebSocket, and lets you:

- add / remove zones,
- edit each zone (enabled, speed, **default mode**, startpos, exitpos, safezone lo/hi,
  exits),
- edit a zone's **own** actions (each zone owns its actions independently — there is no
  shared catalog; the "+ add action" button adds to the current zone), and edit each
  action (enabled, name, pose rows, per-pose speed),
- step through an action's poses locally with the ghost (target) arm,
- **Save** the whole table (`POST /api/zones` → the core hot-reloads, so `client.html` and
  the live robot pick it up immediately), and **Play** an action over the WebSocket.

It loads the table from `GET /api/zones` (falling back to `/zones.json`, then a built-in),
and validates client-side before posting (all poses 6 numbers, exits reference existing
zones; a `mode` field, if present, must be one of the behaviour modes; `exitposes`, if
present, must be a map of existing zone → 6 numbers; it warns, non-blocking, when a pose
falls outside its zone's safezone **or** the hardware floor). The per-axis **hardware
limits** are shown as a reference row in the 3D preview panel (loaded from `GET
/api/limits`, falling back to a built-in constant that mirrors the xacro).

The **action editor** is a modal opened from a zone's action list (the **edit** button on
each action row) or by clicking an action row in the flow tab. It edits one action's
**kind** / **loop** / **enabled**, its **poses** (one row per pose, with add / swap /
remove and out-of-bounds highlighting), its **target** (`zone` / `action` / `pose` — the
selects appear only for the relevant kinds), and its per-pose **MIDI sound** (reconciled to
the pose count). It has a live hydra **patch preview** (a second, lazily-created `Hydra`
canvas that re-evals the action's patch code as you type) and saves all three tables in
parallel (`POST /api/zones` + `/api/sound` + `/api/patches`), re-running the client-side
`validate()` (which now also checks the `kind` / `loop` / `target` rules) before posting.

The **flow** tab (4th tab) is a 2D SVG **flowchart** of the zone graph: **nodes** are the
zones (label = the "mood"; small text = the default mode + action count, or the live
mode/action for the live zone, which gets an accent ring), and **arrows** are the exit
edges (labelled **custom** when the edge has a per-edge `exitposes` override, else
**shared**). Drag a node onto another to **draw an arrow** (appends the target to the
source's `exits`, no duplicates; a temporary arrow follows the pointer); click a node or
arrow to select it. Selecting a **node** opens the zone form in the right-hand detail
panel (the same form as the zones tab, including the default-mode select); selecting an
**arrow** opens the **edge** form: a per-edge exitpose (6-axis grid), a "use zone default"
button (clears the override), and "remove link" (deletes the exit + any `exitposes`
entry). Node positions are auto-laid-out by BFS depth from `init` and are draggable
(session-only, not persisted); wheel zooms, middle-drag pans. The flow edits mutate the
table in place, so the same **Save** posts them (`POST /api/zones`).

Each node also shows its **actions as rows** (one per action, stacked below the label): an
**entry** action is a left-aligned row with a connector dot on the left, an **exit** action
is a right-aligned row with a dot on the right, and an **internal** action is centred with
no dot; a looping action appends a `↻` marker. The node grows to fit its rows (`nodeH`),
and the layout / fit / hit-test / edge anchoring all derive from it, so they stay consistent
as actions are added or removed. Clicking a row opens the **action editor** modal (the rows
themselves are read-only). Cross-zone **target** edges are drawn as orange dashed arrows
(the `flowArrowTarget` marker) from an exit action's row to the target zone's entry action
(or the zone center when none is named), so the hand-off graph is visible at a glance.

A second **sound** tab edits the `sound.json` mapping (see the Sound section) and a third
**patches** tab edits the `patches.json` hydra screen code (see the Patches section).

### The 3D model (real KR60 meshes)

The 3D view in `client.html` and `editor.html` is the **real KR60**, built from the
`kr60ha` ROS model: the `kr60ha_macro.xacro` joint chain (base at the origin, A1 about
−Z) drives the visual STL meshes in `assets/kr60ha/visual/` (`base_link` + `link_1..6`).
Each KUKA joint variable (deg) drives one rotor about its xacro axis (the FK convention).
The **current** arm is rendered in the real KUKA colors (base + wrist black, links
orange); the **target** (ghost) arm is the same geometry as a green translucent overlay,
so the "current / target" legend still applies. A **ghost arm** button in each 3D view
toggles the ghost on/off (`Robot3D.setGhostVisible` — the ghost rotors live in one group,
so the whole arm hides at once; the shared base pedestal stays). The 3D view in
`client.html` also has a **fill window** button (the 3D section takes over the whole
window, hiding the other cards) and a **fullscreen** button (the browser Fullscreen API
on the 3D section); both re-fit the WebGL renderer via a synthetic window resize event.
The STLs are loaded
once and shared between the two arms. If the assets or `STLLoader` are unavailable (e.g. `file://`), the
view degrades to placeholders and keeps running. The chain is driven as-is; the computed
flange differs slightly from the older `kuka_kr60_abs.urdf` schematic (the 3D view is a
visualization — the real robot is driven by the KUKA controller, not the model).

### The Blender → GLB environment

`assets/environment.glb` is a **Blender-editable** environment rendered around the KR60
model in `client.html` (and the editor). glTF is Y-up; the scene is Z-up, so the loader
rotates the loaded scene +90° about X to sit it on the floor. The starter GLB is generated
by `assets/make_environment.py` (stdlib-only, since Blender is not available in this
environment): a 10×10 m floor, a pedestal cylinder at the origin, and two corner walls so
the robot is not boxed in. To edit it:

1. Open `assets/environment.glb` in Blender.
2. Edit the scene.
3. `File > Export > glTF 2.0 (.glb)` over the same file.

The client loads `assets/environment.glb` on init and logs "environment not loaded" (and
continues) if it is missing or fails to load (e.g. `file://` usage).

### The Blender → GLB tool

`assets/tool.glb` is a **Blender-editable** end-effector rendered at the KR60's
$FLANGE (the tool0 frame) in `client.html` (and the editor). It replaces the old white
placeholder box that was built directly in `rtr3d.js`. glTF is Y-up and the scene is
Z-up, so the loader rotates the loaded scene +90° about X (maps +Y → +Z) before attaching
it to the tool node — the same convention as the environment. The starter GLB is generated
by `assets/make_tool.py` (stdlib-only, since Blender is not available in this
environment): a single 0.09 × 0.14 × 0.09 m box (Y-up) centered on the origin, which reads
as the old 0.09 × 0.09 × 0.14 m box once rotated. To edit it:

1. Open `assets/tool.glb` in Blender.
2. Replace the box with your own end-effector design (keep it Y-up and centered on the
   origin so it stays attached to the same point).
3. `File > Export > glTF 2.0 (.glb)` over the same file.

The tool is attached to the tool node (a child of the A6 rotor), so it follows the arm
exactly like the old box did, and it drives the tool readout. The **current** arm shows
the tool in its own materials; the **target** (ghost) arm shows the same geometry with its
materials swapped to the translucent green, so the target tool reads like the ghost links.
The client loads `assets/tool.glb` on init and logs "tool asset not loaded" (and falls
back to the white box) if it is missing or fails to load (e.g. `file://` usage).

The tool's **red mesh is a screen**: `rtr3d.js` finds it (by its red material) and renders a
hydra patch onto it. The patch the screen shows is the **core's resolved patch**, pushed in
each `state` frame (see the Patches section); `GET /api/screen` (the `default` entry of the
patches table) is the seed shown until the first state frame arrives, and a built-in
placeholder is used when the core is unreachable. The screen is only on the **current**
arm's tool (the ghost arm's tool is recoloured green). If the GLB has no red mesh, or
hydra-synth fails to load, the tool renders as-is and the situation is logged.

### Sound (configurable MIDI-out)

Sound is MIDI-controlled: the core does not synthesise audio, it sends MIDI messages
that an external sound engine (Max, Ableton, a script, a hardware unit) turns into sound.
The `sound.py` adapter opens a single MIDI-out port and, on each state snapshot, fires the
mapped messages for the **triggers**:

- **zone entry** — `zones[<zone>]` (the previous zone's note is turned off first),
- **mode change** — `modes[<mode>]` (a CC),
- **action start** — `actions[<name>].start` (or its first pose),
- **per-pose step** — while in `action` mode, each change of the target pose advances an
  internal pose index and sends `actions[<name>].poses[i % len(poses)]`.

The mapping is **data-driven**: it lives in **`sound.json`** (a separate file, not bundled
in `zones.json`). A **MIDI message** is one of `{note, velocity}` (note-on),
`{cc, value}` (control change), or `{program}` (program change); the global `channel`
(0-15) is applied when a message is converted to bytes. The schema:

```json
{
  "channel": 0,
  "zones":   { "init": { "note": 48, "velocity": 60 }, "...": { "cc": 70, "value": 32 } },
  "modes":   { "wander": { "cc": 70, "value": 32 }, "...": { "cc": 70, "value": 64 } },
  "actions": { "breathe": { "start": { "note": 60, "velocity": 80 },
                            "poses": [ { "note": 62, "velocity": 70 }, "..." ] } }
}
```

`actions` is keyed by **action name** globally (not per-zone — per-zone action MIDI is a
later extension). `start` is optional; without it, pose 0 is used on start. The built-in
`ZONE_NOTES` / `MODE_CCS` in `sound.py` are the fallback if the file is missing or corrupt.

`sound.json` is served and saved over the same HTTP server as `zones.json`:

- `GET /api/sound` → the current mapping (read fresh, falling back to the running table).
- `POST /api/sound` → validates the mapping; on success writes it atomically and submits
  a `reload_sound` command so the running core **hot-reloads** (mirrors `/api/zones`).
  On validation failure the file is untouched and a 400 is returned.

**`editor.html`** has a dedicated **sound** tab (alongside the zone tab): a global channel
input, a per-zone message list, a per-mode message list, and a per-action block (a `start`
message + a list of `poses`, with add/remove rows). It loads from `GET /api/sound`
(falling back to `/sound.json`, then a built-in), validates client-side, and saves to
`POST /api/sound`; it re-fetches on the `sound_changed` WebSocket frame.

When no MIDI-out device is open (sim / no rig), the adapter **degrades to logging** each
message it would send (e.g. `[sound] note ch0 48 vel60`), so the mapping is testable
without hardware; the core keeps running either way.

### Patches (hydra screen code)

The tool screen (and the fullscreen render page) show a **hydra** patch — a hydra-synth JS
code string. The patches are **data-driven**: they live in **`patches.json`** (edited in
`editor.html`, served/saved over `/api/patches`, hot-reloaded), mirroring `sound.json`.
The built-in `DEFAULT_HYDRA_CODE` (`"osc(4, 0.1, 1.2).out()"`) in `patches/patches.py` is
the fallback if the file is missing or corrupt.

A patch table maps the core's state to code. The schema:

```json
{
  "default": "osc(4, 0.1, 1.2).out()",
  "zones":   { "init": "osc(2, 0.1, 1.2).out()", "rest": "osc(3, 0.08, 1.2).out()", "..." : "..." },
  "modes":   { "wander": "osc(4, 0.3, 1.2).out()", "..." : "..." },
  "actions": { "breathe": "osc(6, 0.2, 1.2).out()", "..." : "..." }
}
```

`zones` / `modes` / `actions` each map a name to a hydra code string (the zones are seeded
by default; modes and actions start empty). `default` is the code used when nothing else
matches. **Match precedence (most specific wins): `action > mode > zone > default`** — a
state's patch is looked up by action name first, then mode, then zone, then `default`
(`match_patch` in `patches/patches.py`).

**The core is the single source of truth for the resolved patch.** It resolves the patch
for every snapshot (`Patches.code_for`, i.e. `match_patch`) and pushes the code in the
`patch` field of each broadcast `state` frame (built by `display.py`). Every consumer —
`client.html`, `render.html`, and the **3D tool screen** in `rtr3d.js` — prefers that
pushed `patch`, so all of them stay in sync no matter what triggered the zone/mode/action
change (MIDI, WebSocket, or HTTP). The resolver is wired in `main.py`
(`make_display(..., patch_code=patches.code_for)`).

**Manual screen override.** The tool client (`client.html`) can push a specific patch slot
to the screens. Selecting a slot sends a `set_screen_patch` command with that slot's code;
the `Patches` adapter holds it as an in-memory **screen override** (`set_screen_override`),
and `code_for` returns the override while it is set — so the pushed `patch` field (and thus
the 3D tool screen and `render.html`) follow the operator's choice. Selecting **auto** sends
`set_screen_patch` with `code: null`, which clears the override and the screens fall back to
the live `action > mode > zone > default` match. The override is a **live control**: it is
held in memory (not persisted to `patches.json`) and is handled by the engine
(`SET_SCREEN_PATCH` → `patches.set_screen_override`, wired in `main.py`).

As a **fallback**, the browser pages also **fetch the whole table once** and match locally
(`matchPatch`) on every state frame, so a page served over `file://` — or one talking to an
older core that doesn't push `patch` — still resolves the patch correctly. A
`PATCHES_CHANGED` event (published when the table is hot-reloaded) tells the pages to
re-fetch. The `Patches` adapter (`patches/patches.py`) holds the table, re-reads it on
`RELOAD_PATCHES`, and serves `current_data` to the HTTP GET fallback. The 3D tool screen
seeds itself from `GET /api/screen` (the `default` entry) until the first state frame
arrives, then follows the pushed patch.

- **`render.html`** is the **fullscreen** hydra render page. It opens the same WebSocket as
  `client.html` (port 8765), fetches the patches table, and renders the matched patch onto
  a full-window hydra canvas, re-evaluating on each state frame (zone / mode / action). A
  small HUD shows the current zone / mode / action and the code in use. This is the page to
  open on a display to see the live hydra output.
- **`editor.html`** has a dedicated **patches** tab (alongside the zone and sound tabs): a
  `default` code field, a `zones` list, a `modes` list, and an `actions` list, each with a
  **live hydra preview** (the code is `eval`'d as you type). It loads from `GET /api/patches`
  (falling back to `/patches.json`, then a built-in), validates client-side, and saves to
  `POST /api/patches` (the core hot-reloads); it re-fetchs on the `patches_changed` frame.
- **`client.html`** has a **patch chooser**: it shows the patch currently matched to the
  live state (action > mode > zone > default) with its own small preview, so you can see
  which patch the render page / tool screen is using without opening a second page.

When no hydra-synth is available (or the code fails to `eval`), the pages fall back to the
built-in default code and log the failure; the core keeps running either way.

### MIDI-in

The `io/midi.py` adapter maps controller messages to core commands. The mapping is
**data-driven**: it lives in **`midi.json`** (edited in `editor.html`, served/saved over
`/api/midi`, hot-reloaded), mirroring `sound.json`. A **mapping key** is one of:

- `cc:<cc>:<value>` — a control change (channel 0, matching the legacy protocol),
- `note:<ch>:<note>` — a note-on (channel 0-7; velocity is ignored, and a note-off reuses
  its note-on's key),
- `program:<ch>:<prog>` — a program change (channel 0-7).

A **target** is `{"target": "<kind>", ...}` where the kind is one of:

| kind | extra fields | dispatches |
|------|--------------|------------|
| `zone` | `zone` | `goto_zone` (path-routed through the exits graph) |
| `mode` | `mode` | `set_mode` |
| `action` | `zone`, `action` | `play_action` for `action` (played in the **current** zone; the `zone` field is required by the validator but informational at dispatch) |
| `clear` | — | `clear_action` |
| `random_action` | `zone` | a random **enabled** action in `zone` (preferring non-looping ones) |

The schema:

```json
{
  "in_port": 0,
  "enabled": true,
  "mapping": {
    "cc:7:42":     { "target": "zone", "zone": "wander" },
    "note:0:60":   { "target": "mode", "mode": "action" },
    "program:0:5": { "target": "action", "zone": "wander", "action": "look" },
    "cc:9:0":      { "target": "clear" },
    "cc:4:7":      { "target": "random_action", "zone": "wander" }
  }
}
```

**Learned-first dispatch.** A message that matches a mapping key dispatches its learned
target; a message with **no** mapping falls back to the **legacy hardcoded protocol** below,
so an existing controller keeps working until a mapping exists. The built-in
`builtin_midi_data` table has an **empty** `mapping`, so the legacy protocol is the effective
default until `midi.json` supplies one.

**The legacy fallback** (the physical protocol the Tidal / MIDI controller already uses):

- **CC1 (value 1-6)** → `goto_zone` (`init`/`rest`/`wakeup`/`stretch`/`wander`/`wildwander`),
  path-routed through the exits graph.
- **CC2 (0/1)** → `set_mode` (`wander`/`action`).
- **CC3 (value `i`)** → plays the `i`-th action (**1-based**) in the current zone; `0` clears.
  The index is resolved against the current zone's action table (in order), so it tracks
  hot-reloaded zone data; a bad index or a zone with no actions is a logged no-op.
- **CC13** → `set_flag` (`dynvel`); **CC20/21/22** → `adjust_limit`; **CC30 (1/2)** →
  `set_mode` (`random`/`wander`).
- **Note ch1** 41/42/73/74 → `set_flag` (`wandermode`/`randomwristmode`/`dynmode`/`reachmode`);
  **ch1/ch2** 61-64 → `set_joint_pose` (named poses); **ch3** → `random_wrist`; **ch6** →
  `set_linear_pose`.

**Learn.** The editor captures a live controller message and writes it into the mapping.
`POST /api/midi/learn` with `{"action": "start"}` arms the capture on the running
`MidiInput`; the next mappable message is **not** dispatched — it is submitted as a
`midi_learn` command, the core publishes a `MIDI_LEARN` event, and the display forwards it to
the WebSocket as `{"type": "midi_learn", "key": ..., "msg": ...}`. The editor writes the
captured `key` into the mapping (replacing a row's key, or creating a new row) and rebuilds.
`{"action": "stop"}` disarms. Learn is **single-shot**: it disarms after one capture.

**Input device.** The `in_port` field selects the MIDI in port (a non-negative integer);
when it is absent or `null`, the `--midi` port (default 0) is used. `GET /api/midi/ports` lists the
available in ports (`{"ports": [{"index", "name"}, ...]}`) for the picker. `enabled` is the
soft on/off switch (the constructor switch is the hard one; MIDI works only when both are on).
The mapping file is `--midi-json` (default `midi.json` next to `main.py`); if it is missing
or corrupt the built-in table is used (logged), so the core always starts.

`midi.json` is served and saved over the same HTTP server as `zones.json`:

- `GET /api/midi` → the current mapping (read fresh from `midi.json`, falling back to the
  running table).
- `POST /api/midi` → validates the mapping; on success writes it atomically and submits a
  `reload_midi` command so the running core **hot-reloads** (the adapter re-reads the file,
  swaps its table, publishes `midi_changed`, and reconnects the in port). On validation
  failure the file is untouched and a 400 is returned.
- `GET /api/midi/ports` → the available MIDI in ports (for the input-device picker).
- `POST /api/midi/learn` → arm (`{"action": "start"}`) or disarm (`{"action": "stop"}`) the
  learn capture; returns `{"ok": true, "learning": <bool>}`.

**`editor.html`** has a dedicated **midi-control** tab (alongside the zone, sound, and
patches tabs): an input-device section (an `in_port` picker from `GET /api/midi/ports` with a
`default (no override)` option, an **enabled** checkbox, a **refresh** button, and a
port-count / no-device hint) and a learned-mapping section (a table of `key → target` rows
with an editable key, a target-kind select, the kind's name field(s), a **learn** button, and
a remove button; a **+ add mapping** button and a **stop learn** button sit below). It loads
from `GET /api/midi` (falling back to `/midi.json`, then a built-in), validates client-side,
and saves to `POST /api/midi`; it re-fetches on the `midi_changed` frame and writes captured
keys on the `midi_learn` frame.

When no MIDI-in device is open (sim / no rig), the adapter degrades to doing nothing on each
message; the core keeps running either way.

### The chatbot (an LLM brain)

`chatbot.py` is an optional, self-contained brain that drives the running core over its
WebSocket. It is **supersimple**: stdlib + `websockets` only, no `openai` package — it
speaks straight to any **OpenAI-compatible** endpoint with `urllib`.

It is a **sense → act** loop, not a conversation:

- **in** — you type free-form **sensor descriptions** (simulating the robot's camera and
  microphone, which are not installed yet). E.g. *"I look at the robot and move my hands
  to get its attention."*
- **out** — the brain replies with a one-line **`REASON:`** (why it chose this, for
  debugging) and then a **JSON command** (or a short list of them) that changes the
  core's state machine. No other words. The way the robot *moves* is how it expresses
  what it feels.

Example exchange:

```
you> I look at the robot and move my hands to get its attention
robot> REASON: you are calling me, I want to wake up and see you
robot> [{"cmd": "goto_zone", "zone": "wakeup"}, {"cmd": "play_action", "action": "look"}]
```

**Do-nothing is a valid reply.** If the brain decides nothing should change, it replies
with its REASON line and an empty list `[]`, and the robot stays exactly where it is
(the chatbot prints `robot> why: ...` and `robot> [] (no action)` and sends nothing).
A reply is one of: a single command object, a list of command objects (several things in
order), or `[]`. The REASON line is printed for debugging (the chatbot's `--no-reason`
hides it); it is never sent to the core — only the JSON is.

**How it changes the state machine.** The JSON is validated against the core's live zone
table and sent over the WebSocket. The **core is the safety boundary**: unknown zones,
disabled zones, and unreachable paths are rejected there (and the chatbot refuses unknown
zones/modes client-side too). The allowed commands are `goto_zone`, `set_mode`,
`play_action`, and `clear_action`.

**The sentient prompt.** The system prompt casts the model as the *mind* of the robot —
a **restless, ADHD** persona that cannot stay still and acts on every impulse, but it
**never speaks**; it only acts. Each turn it is given a `[body]` line (zone / mode /
action / moving / speed / the six joints) plus the sensor input, and told to reply with
one short `REASON:` line (why, for the human debugging it) and then only JSON. It is
**biased toward moving**: if it could go somewhere or do something, it should, and it
should not repeat what it did last turn. It maps feelings to movement: threatened → go
far (`wildwander`); bored / restless → `goto_zone` a new zone or `set_mode wander`;
wanting to see → `look` (not every turn); being called → `wakeup` + stretch; fidgety →
`breathe` or `set_mode random`; and only when genuinely at peace (rare) → stay put. The
`[]` ("do nothing") reply is the last resort. The prompt is built from the live
`GET /api/zones` table and re-synced if the core hot-reloads its zones.

**Two drivers** share the same conversation: the REPL (`you>`) and an optional autonomous
loop — every `--poll` seconds the brain "senses" its own state and may act on its own
(`--poll 0` disables it).

**The endpoint.** By default it talks to the local Unsloth Studio at
`http://127.0.0.1:8888/v1`. The model must be **loaded** in Unsloth Studio (override with
`--model`; `--api-key` or `RTR_LLM_API_KEY` for auth). The default model is a *reasoning*
model, so `--max-tokens` is large (1500) to leave room for its hidden thinking before the
JSON reply.

```bash
# the core must be running first
python main.py --sim            # or --robot

# drive the robot: type sensor descriptions, it answers with a JSON action
python chatbot.py --api-key <key>

# check the core link (read-only), then exit
python chatbot.py --selftest

# disable the autonomous self-sense loop and just drive it by hand
python chatbot.py --api-key <key> --poll 0

# hide the brain's REASON line (you only see the JSON it sends)
python chatbot.py --api-key <key> --no-reason
```

## Running

```bash
# without hardware (recommended for development)
python main.py --sim

# with the real robot (Python listens; start KUKAPY_SERVER on the pendant)
python main.py --robot

# bare core + WebSocket only
python main.py --sim --no-midi --no-sound

# self-contained wander loop, no WebSocket, prints a line each second
python sim_demo.py

# the LLM brain: chat with the robot and let it steer the core (core must be running)
python chatbot.py --api-key <key>
```

### Camera remotes

The camera runs as a **separate process** on the RPi (where the physical cameras are),
connecting to the core over the same WebSocket. It sends `CAM_*` telemetry up and
receives the `cam_control` intent down. Weights are resolved from `rtr/camera/models/`
(drop the `.pt` files there; the dir is git-ignored).

```bash
# the real two-cam pipeline (wide + close), on the RPi:
python -m rtr.camera.camera_remote \
  --core-host 192.168.1.185 --core-port 8765 \
  --wide-cam 0 --close-cam 1 --show

# a mock camera for headless / --sim validation (no hardware):
python -m rtr.camera.camera_mock \
  --core-host 127.0.0.1 --core-port 8765 --candidates 3
```

`camera_remote` flags:

| Flag | Default | Meaning |
| ---- | ------- | ------- |
| `--core-host` | `127.0.0.1` | Core WebSocket host (pass the core's IP). |
| `--core-port` | `8765` | Core WebSocket port. |
| `--http-port` | `8766` | Core HTTP port. |
| `--wide-cam` / `--close-cam` | `0` / `1` | Physical camera device numbers. |
| `--width` / `--height` | `640` / `480` | Frame size. |
| `--fps` | `30` | Target frame rate. |
| `--model` | `yolo26n.pt` | Person detector weight (resolved in `rtr/camera/models`). |
| `--tracker` | `bytetrack.yaml` | ByteTrack config. |
| `--show` | off | Open the OpenCV debug window (candidate boxes + lock + overlay). |
| `--log-level` | `INFO` | Log verbosity. |

`camera_mock` flags: `--core-host`, `--core-port`, `--http-port`, `--candidates`
(simulated persons, default `3`), `--fps` (telemetry rate, default `30`), `--log-level`.

With the core running, open the pages over the HTTP server (port 8766):

- `http://localhost:8766/client.html` — the control page (3D view + environment + zones).
- `http://localhost:8766/editor.html` — the zone/action/sound/patches editor.
- `http://localhost:8766/render.html` — the fullscreen hydra render page (the matched patch).

`GET http://localhost:8766/api/zones` returns the live zone table; `POST` to the same URL
saves it (the core hot-reloads). `GET/POST /api/sound` do the same for the MIDI-out
mapping, and `GET/POST /api/midi` do the same for the MIDI-in mapping. `GET /api/limits`
returns the per-axis hardware limits (read-only). Use
`--http-port` to change the port or `--no-http` to disable the server (the WebSocket on
8765 still works).

### kukapy (real robot only)

`kukapy` is the EKI client that implements the server role above. It is not on PyPI by
the name used here; it is installed **editable** from the local checkout
(`../kukapyEKI/KukaPyNEW`) into the venv that runs `main.py`:

```bash
/home/kaos/.pyenv/versions/kukart/bin/pip install -e ../kukapyEKI/KukaPyNEW
```

`main.py` imports it lazily inside `robot.connect()`, so `--sim` runs without it
installed. `KukaPyNEW/pyproject.toml` needed two fixes to build: the build backend
(`setuptools.build_meta`) and an explicit `packages = ["kukapy"]` (the flat layout has
three top-level dirs).

The on-robot side (`KUKAPY_SERVER.SRC` + `KUKAPY.xml`) is deployed per the KukaPy
README: set `<IP>` to the PC's address, `<PORT>` to 18735, `PROTOCOL=TCP`,
`SENDFLAG=1`, cold-restart the KRC, and keep the start order (Python first, then the
server on the pendant).

## What changed from `kukart/tidalkuka.py`

- The single global `kukastate` dict + blocking `activateZone` `while` loop is gone.
  State lives in a typed `StateBus`; zone transitions are data advanced one step per
  tick, so nothing blocks.
- The robot I/O moved from one daemon thread (`kukaLoop`) into the asyncio engine, with
  each blocking call offloaded via `asyncio.to_thread` so the loop never stalls.
- The blocking `robot.connect()` no longer runs on the event loop at startup; the
  WebSocket comes up first and the connect runs in a thread.
- All inputs funnel through `Command` objects on the bus (MIDI + WebSocket), instead of
  mutating a shared dict from the MIDI callback.
- A browser control page (`client.html`) replaces the P5live sketch for development.
