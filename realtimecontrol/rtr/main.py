"""RTR entry point.

Wires the core (state machine + brain + engine) and its adapters (robot, display,
camera, sound, MIDI, WebSocket, HTTP) together, then runs the engine until Ctrl-C.

Examples
--------
    python main.py --sim                          # headless, no hardware
    python main.py --robot --ip 192.168.10.201    # real KUKA
    python main.py --sim --no-midi --no-sound     # bare core + WebSocket
"""

import argparse
import asyncio
import os
import signal
import sys

from rtr.config import Config
from rtr.core.bus import StateBus
from rtr.core.engine import Engine
from rtr.robot import make_robot
from rtr.state import StateMachine, Zones
from rtr.state.zones import LIN_POSES, POSES, ZONES, load_state_data
from rtr.brain import Brain
from rtr.display import make_display
from rtr.sound import make_sound, load_sound_data, builtin_sound_data
from rtr.patches import (
    Patches,
    load_patches_data,
    builtin_patches_data,
    DEFAULT_HYDRA_CODE,
)
from rtr.camera import make_camera
from rtr.io import (
    HttpServer,
    MidiInput,
    WebSocketServer,
    builtin_midi_data,
    load_midi_data,
)

# The directory this file lives in: where client.html / editor.html / assets / zones.json are.
ROOT = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Tool screen (hydra).
#
# The tool GLB carries a red mesh that stands in for a screen. The browser renders
# a hydra patch onto it (see rtr3d.js). The patch is now data-driven: it lives in
# ``patches.json`` (edited in ``editor.html``), matched to zone/mode/action, and is
# served at GET /api/screen (the ``default`` entry) and GET /api/patches (the whole
# table). The built-in :data:`DEFAULT_HYDRA_CODE` is the fallback.
# ---------------------------------------------------------------------------


def parse_args(argv):
    parser = argparse.ArgumentParser(description="RTR - RealTimeRobot core")
    parser.add_argument("--sim", action="store_true", help="run headless (default)")
    parser.add_argument("--robot", action="store_true", help="use the real KUKA via kukapy")
    parser.add_argument("--ip", default="192.168.10.201", help="robot IP")
    parser.add_argument("--port", type=int, default=18735, help="robot EKI port")
    parser.add_argument("--tick", type=float, default=20.0, help="engine tick rate (Hz)")
    parser.add_argument("--ws-port", type=int, default=8765, help="WebSocket port")
    parser.add_argument("--http-port", type=int, default=8766, help="HTTP server port")
    parser.add_argument("--no-http", action="store_true", help="disable the HTTP server")
    parser.add_argument("--zones", default="zones.json",
                        help="zone data file (default: zones.json next to main.py)")
    parser.add_argument("--midi", type=int, default=0, help="MIDI in port index")
    parser.add_argument("--no-midi", action="store_true", help="disable MIDI input")
    parser.add_argument("--midi-json", default="midi.json",
                        help="MIDI-in learned mapping file (default: midi.json next to main.py)")
    parser.add_argument("--sound", default="sound.json",
                        help="sound data file (default: sound.json next to main.py)")
    parser.add_argument("--no-sound", action="store_true", help="disable sound (MIDI out)")
    parser.add_argument("--patches", default="patches.json",
                        help="patches data file (default: patches.json next to main.py)")
    parser.add_argument("--no-display", action="store_true", help="disable display (P5live)")
    parser.add_argument("--camera", action="store_true", help="enable the camera adapter")
    return parser.parse_args(argv)


def build_config(args) -> Config:
    cfg = Config()
    cfg.robot_kind = "kuka" if args.robot else "sim"
    cfg.robot_ip = args.ip
    cfg.robot_port = args.port
    cfg.tick_hz = args.tick
    cfg.ws_port = args.ws_port
    cfg.http_port = args.http_port
    cfg.enable_http = not args.no_http
    cfg.zones_path = _resolve(args.zones)
    cfg.sound_path = _resolve(args.sound)
    cfg.patches_path = _resolve(args.patches)
    cfg.midi_in_port = None if args.no_midi else args.midi
    cfg.midi_path = _resolve(args.midi_json)
    cfg.enable_sound = not args.no_sound
    cfg.enable_display = not args.no_display
    cfg.enable_camera = args.camera
    return cfg


def _resolve(path: str) -> str:
    """Make a path absolute, anchoring relative paths at the rtr directory."""
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def load_zones_data(path: str) -> dict:
    """Load the zone data file, falling back to the built-in tables with a warning."""
    try:
        return load_state_data(path)
    except (OSError, ValueError) as exc:
        print(f"[rtr] zones file {path!r} unavailable ({exc}); using built-in zones")
        return {"zones": ZONES, "poses": POSES, "lin_poses": LIN_POSES}


def load_sound_data_fallback(path: str) -> dict:
    """Load the sound data file, falling back to the built-in tables with a warning."""
    try:
        return load_sound_data(path)
    except (OSError, ValueError) as exc:
        print(f"[rtr] sound file {path!r} unavailable ({exc}); using built-in sound")
        return builtin_sound_data()


def load_patches_data_fallback(path: str) -> dict:
    """Load the patches data file, falling back to the built-in table with a warning."""
    try:
        return load_patches_data(path)
    except (OSError, ValueError) as exc:
        print(f"[rtr] patches file {path!r} unavailable ({exc}); using built-in patches")
        return builtin_patches_data()


def load_midi_data_fallback(path: str) -> dict:
    """Load the MIDI-in mapping file, falling back to the built-in table with a warning."""
    try:
        return load_midi_data(path)
    except (OSError, ValueError) as exc:
        print(f"[rtr] midi file {path!r} unavailable ({exc}); using built-in midi")
        return builtin_midi_data()


async def run(cfg: Config) -> None:
    bus = StateBus()

    # --- zone data --------------------------------------------------------
    data = load_zones_data(cfg.zones_path)
    zones = Zones(data["zones"])
    poses = data.get("poses", POSES)
    lin_poses = data.get("lin_poses", LIN_POSES)

    # --- core -----------------------------------------------------------
    robot = make_robot(cfg.robot_kind, port=cfg.robot_port, tick_hz=cfg.tick_hz)
    machine = StateMachine(zones, tick_hz=cfg.tick_hz)
    brain = Brain(machine)
    # Sound is created before the engine so the engine can hold its reload hook.
    sound = make_sound(bus, sound_loader=lambda: load_sound_data_fallback(cfg.sound_path),
                       enabled=cfg.enable_sound,
                       out_port=cfg.midi_out_port, out_device=cfg.midi_out_device)
    # The hydra patches (tool screen) are data-driven too; the engine holds the
    # reload hook so a POST /api/patches hot-reloads the running table.
    patches = Patches(bus, patches_loader=lambda: load_patches_data_fallback(cfg.patches_path))
    # The MIDI-in learned mapping is data-driven as well; created before the engine
    # so the engine can hold its reload hook (POST /api/midi hot-reloads the table).
    midi = MidiInput(bus, in_port=cfg.midi_in_port,
                     enabled=cfg.midi_in_port is not None,
                     poses=poses, lin_poses=lin_poses,
                     zones_provider=lambda: machine.zones,
                     midi_loader=lambda: load_midi_data_fallback(cfg.midi_path))
    engine = Engine(bus, robot, brain, machine, tick_hz=cfg.tick_hz,
                    zone_loader=lambda: load_state_data(cfg.zones_path),
                    sound_reload=sound.reload,
                    patch_reload=patches.reload,
                    midi_reload=midi.reload,
                    screen_patch_override=patches.set_screen_override)

    # --- adapters -------------------------------------------------------
    ws = WebSocketServer(bus, host=cfg.ws_host, port=cfg.ws_port,
                         enabled=cfg.enable_display)
    http = HttpServer(bus, zones_provider=lambda: machine.zones,
                      sound_provider=sound.current_data,
                      patches_provider=patches.current_data,
                      midi_provider=midi.current_data,
                      zones_path=cfg.zones_path, sound_path=cfg.sound_path,
                      patches_path=cfg.patches_path, midi_path=cfg.midi_path,
                      midi=midi, sound=sound, root=ROOT,
                      host=cfg.http_host, port=cfg.http_port,
                      enabled=cfg.enable_http)
    # The display pushes the resolved hydra patch in every state frame (the core is
    # the single source of truth), so the pages and the 3D tool screen stay in sync
    # no matter what triggered the change (MIDI, WebSocket, HTTP).
    display = make_display(bus, ws.broadcast, enabled=cfg.enable_display,
                           patch_code=patches.code_for)
    camera = make_camera(bus, enabled=cfg.enable_camera)

    # The WebSocket + HTTP bridges come up first, so the control interface is
    # always reachable (and keeps running) while we wait for the robot.
    await ws.start()
    http.start()

    # The KUKA EKI link is a blocking listen/accept: Python is the TCP *server*
    # and the KRC dials in (start KUKAPY_SERVER on the pendant). Run it in a
    # thread so the event loop stays responsive while we wait for the robot.
    try:
        await asyncio.to_thread(robot.connect)
    except Exception as exc:
        print(f"[rtr] robot connect failed: {exc}")
        raise
    print(f"[rtr] robot connected: {cfg.robot_kind}")

    print(f"[rtr] core up: robot={cfg.robot_kind} zone={machine.current_zone} "
          f"mode={machine.mode} ws={cfg.ws_port} http={cfg.http_port if http.enabled else 'off'}")

    # --- run until interrupted ----------------------------------------
    engine_task = asyncio.create_task(engine.run())
    try:
        await asyncio.Future()  # block until SIGINT
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("\n[rtr] shutting down")
        engine.stop()
        engine_task.cancel()
        await asyncio.gather(engine_task, return_exceptions=True)
        await ws.stop()
        http.stop()
        midi.stop()
        sound.close()
        robot.disconnect()
        print("[rtr] bye")


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    cfg = build_config(args)
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
