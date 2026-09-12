"""RTR entry point.

Wires the core (state machine + brain + engine) and its adapters (robot, display,
camera, sound, MIDI, WebSocket) together, then runs the engine until Ctrl-C.

Examples
--------
    python main.py --sim                          # headless, no hardware
    python main.py --robot --ip 192.168.10.201    # real KUKA
    python main.py --sim --no-midi --no-sound     # bare core + WebSocket
"""

import argparse
import asyncio
import signal
import sys

from rtr.config import Config, from_args
from rtr.core.bus import StateBus
from rtr.core.engine import Engine
from rtr.robot import make_robot
from rtr.state import StateMachine, Zones
from rtr.brain import Brain
from rtr.display import make_display
from rtr.sound import make_sound
from rtr.camera import make_camera
from rtr.io import MidiInput, WebSocketServer


def parse_args(argv):
    parser = argparse.ArgumentParser(description="RTR - RealTimeRobot core")
    parser.add_argument("--sim", action="store_true", help="run headless (default)")
    parser.add_argument("--robot", action="store_true", help="use the real KUKA via kukapy")
    parser.add_argument("--ip", default="192.168.10.201", help="robot IP")
    parser.add_argument("--port", type=int, default=18735, help="robot EKI port")
    parser.add_argument("--tick", type=float, default=20.0, help="engine tick rate (Hz)")
    parser.add_argument("--ws-port", type=int, default=8765, help="WebSocket port")
    parser.add_argument("--midi", type=int, default=0, help="MIDI in port index")
    parser.add_argument("--no-midi", action="store_true", help="disable MIDI input")
    parser.add_argument("--no-sound", action="store_true", help="disable sound (MIDI out)")
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
    cfg.midi_in_port = None if args.no_midi else args.midi
    cfg.enable_sound = not args.no_sound
    cfg.enable_display = not args.no_display
    cfg.enable_camera = args.camera
    return cfg


async def run(cfg: Config) -> None:
    bus = StateBus()

    # --- core -----------------------------------------------------------
    robot = make_robot(cfg.robot_kind, port=cfg.robot_port, tick_hz=cfg.tick_hz)
    zones = Zones()
    machine = StateMachine(zones)
    brain = Brain(machine, zones, tick_hz=cfg.tick_hz)
    engine = Engine(bus, robot, brain, machine, tick_hz=cfg.tick_hz)

    # --- adapters -------------------------------------------------------
    ws = WebSocketServer(bus, host=cfg.ws_host, port=cfg.ws_port,
                         enabled=cfg.enable_display)
    display = make_display(bus, ws.broadcast, enabled=cfg.enable_display)
    sound = make_sound(bus, enabled=cfg.enable_sound,
                       out_port=cfg.midi_out_port, out_device=cfg.midi_out_device)
    camera = make_camera(bus, enabled=cfg.enable_camera)
    midi = MidiInput(bus, in_port=cfg.midi_in_port,
                     enabled=cfg.midi_in_port is not None)

    # The WebSocket bridge comes up first, so the control interface is always
    # reachable (and keeps running) while we wait for the robot.
    await ws.start()

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
          f"mode={brain.mode} ws={cfg.ws_port}")

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
