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
   │ (rtmidi)  │  │  │ (decide)│  │ Machine │   │ (kukapy)   │  │
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
2. folds any queued commands (from MIDI / WebSocket) into the **brain**,
3. asks the **brain** for the next target pose (it drives the state machine's
   transition or the current behaviour mode),
4. commands the robot to that target,
5. publishes a state **snapshot** to every subscribed adapter (display, camera, sound).

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
| `state/machine.py`| `StateMachine`: zones, transitions, actions. Deterministic.           |
| `state/zones.py`  | Zone/pose data (safe areas, start/exit poses, actions, named poses).  |
| `brain/brain.py`  | `Brain`: decides the next pose each tick (wander/random/action/track/hold). |
| `robot/base.py`   | `RobotBase` interface (implemented by Kuka and Sim).                  |
| `robot/kuka.py`   | Real robot via `kukapy` (the EKI TCP server).                          |
| `robot/sim.py`    | Headless robot for development without hardware.                       |
| `robot/helpers.py`| Joint-limit math (`fitlimits`, `posSafe`, `comparelist`).             |
| `camera/camera.py`| Camera control adapter (track/follow, analysis).                       |
| `display/display.py`| Display adapter: turns each snapshot into a P5live frame over the WS. |
| `sound/sound.py`  | Sound adapter: MIDI-out, driven by the state.                          |
| `io/midi.py`      | MIDI-in adapter: maps controller messages to core commands.           |
| `io/websocket.py` | WebSocket server: the single bridge for external control + state.     |
| `client.html`     | A self-contained browser control page (WebSocket client).             |

## The state machine

The base of everything is the state machine. Given a specific **zone** the robot is
allowed to do some things: go to specific areas, activate extra tools, music, screen,
camera.

A **zone** is an axial area. Given the 6 axes, it defines what safe positions at all
times when in a current zone:

```python
"safezone": [(-94, 122), (-98,-96), (13,19), (-2,2), (85,95), (-357,357)],
```

If the robot's axes are all within the ranges specified, it can safely move to any of
the other locations in that zone.

A zone also declares:

- `startpos` / `exitpos` — the pose used when entering / leaving the zone,
- `exits` — the zones this one can transition to,
- `actions` — named motions possible while in the zone,
- `speed` — default move speed while in the zone.

### Zones

`init → rest → wakeup → {stretch, wander} → wildwander` (see `state/zones.py` for the
graph and the exact limits). A transition is just data: a list of poses to pass through
(the current zone's `exitpos`, then the target zone's `startpos`). `StateMachine.update`
advances it one step per tick; nothing blocks.

### Actions

When in a zone, specific actions are possible. An action is a sequence of poses, e.g.
*rotate 15° on A3, then -30° on A4*. It wraps around and awaits the next action command.
When an action would go outside the current zone, it ends at its limit and goes to await
the next action.

Some actions do not act on motors but enable the camera or an image on the display.

## Control

The robot is controlled from the central server (this process). It keeps track of the
state, takes inputs, and decides what to do next. Some of this is programmed, some can
be output from a small LLM, some can be realtime control from software, hardware, or
MIDI instruments.

Inputs are **commands** (see `core/commands.py`). They are flat JSON, e.g.
`{"cmd": "goto_zone", "zone": "rest"}`:

| Command           | Meaning                                            |
| ----------------- | -------------------------------------------------- |
| `goto_zone`       | Transition to a zone (via exit/start poses).       |
| `set_mode`        | `wander` / `random` / `action` / `track` / `hold`. |
| `play_action`     | Play a named action in the current zone.           |
| `set_joint_pose`  | Move to a specific 6-axis joint pose (then `hold`).|
| `set_linear_pose` | Move to a cartesian pose (linear). Not simulated.  |
| `adjust_limit`    | Nudge a wander drift limit A1/A2/A3 (MIDI CC 20/21/22). |
| `set_flag`        | Toggle an informational flag (`wandermode`, `dynmode`, ...). |
| `random_wrist`    | Randomise A4/A5 of the current target (then `hold`).|
| `stop`            | Defined in the `Cmd` enum but **not handled** by the brain — currently a no-op. |

Outputs are **events** the `StateBus` publishes: `zone_changed`, `mode_changed`,
`action_changed`, `pose_updated`, and a full `snapshot` each tick. The `snapshot` is a
flat JSON structure (`zone`, `mode`, `action`, `joint_pose`, `cart_pose`, `target_pose`,
`speed`, `flags`, `moving`) — this is what the display and any external client see.

### The brain's behaviour modes

The `Brain` owns the behaviour policy and turns the current state + pending commands
into one target pose per tick:

- **wander** (default): gentle continuous drift of A1–A3 inside the current zone's
  safezone; the wrist (A5) is derived as `-(A2+A3)` and clamped.
- **random**: jump to a new random pose in the safezone at a fixed cadence.
- **action**: step through the current zone's active action, one pose per cadence.
- **track**: advance A1 at a fixed rate, clamped to the safezone.
- **hold**: keep the last commanded pose.

A `set_joint_pose` / `random_wrist` command sets the target directly and switches to
`hold` (the robot goes there and stays, until told otherwise). Zone transitions are
handled by the `StateMachine`; while one is in progress the brain simply forwards its
target.

### P5live / WebSocket / client.html

The `io/websocket.py` adapter is the **single bridge** to P5live and any external
client. Two directions, one port (default 8765):

- **In** — clients send JSON commands; the server submits them to the `StateBus`.
- **Out** — the core broadcasts a state frame to every connected client (driven by the
  `Display` adapter).

`client.html` is a self-contained browser page (no build step, no server) that opens a
WebSocket to the core and is the hand-rolled stand-in for P5live. It shows the current
state (zone / mode / action / speed / moving / flags), all six axes with their target
and position within the current zone's safezone, and a live log; and it sends commands
(zone buttons, mode buttons, action buttons, a 6-axis joint-pose editor, wander-limit
sliders, random-wrist). Open it from any browser (`file://` works) and it auto-connects
to `ws://localhost:8765`, retrying every 2 s.

### Sound

Sound is MIDI-controlled. The `sound.py` adapter opens a MIDI-out port and, for each
state snapshot, maps the current zone/mode to the MIDI messages (notes, CCs) that
trigger the sound.

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
```

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
