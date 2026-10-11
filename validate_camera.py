"""Core-side camera telemetry + cam_control validation for the RTR restructure.

The RPI physical video pipeline (``rtr.camera.pipeline``) needs cv2/numpy/ultralytics
plus model weights and cannot run headless here. This harness exercises the CORE side
of the camera restructure instead, end to end: the real ``CameraController`` +
``Brain`` + ``StateMachine`` wired together the way ``main.py`` does, fed synthetic
``CAM_*`` telemetry, and walked across the animal-themed 12-zone network.

It asserts the two things the restructure changed on the camera path:

1. ``cam_control`` — every zone applies its ``{active, mode, lock}`` intent on a zone
   change (the brain drives the intent; the controller publishes ``CAM_CONTROL`` on
   change). The walk crosses all three mode groups and exercises every active/mode
   pair in the map.
2. ``CAM_*`` telemetry — each payload (status / candidates / track / face) lands on
   the controller and, for track, on ``machine.camera_state`` (which the variable-axis
   behaviours read each tick).

Run from the repo root::

    python validate_camera.py

Exits non-zero on the first failure.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
# The rtr package lives at realtimecontrol/rtr/rtr; make it importable.
RTR_DIR = os.path.join(ROOT, "realtimecontrol", "rtr")
sys.path.insert(0, RTR_DIR)

# Import core before brain (mirrors main.py's order) to avoid the brain<->core cycle.
from rtr.core.engine import Engine  # noqa: E402,F401
from rtr.core.bus import StateBus  # noqa: E402
from rtr.core.commands import Cmd, Command  # noqa: E402
from rtr.state.zones import Zones, load_state_data  # noqa: E402
from rtr.state.machine import StateMachine  # noqa: E402
from rtr.brain.brain import Brain  # noqa: E402
from rtr.brain.config import load_brain_config  # noqa: E402
from rtr.camera.camera import make_camera  # noqa: E402

ZONES_PATH = os.path.join(RTR_DIR, "zones.json")
BRAIN_PATH = os.path.join(RTR_DIR, "brain.json")

# A walk that crosses museum-closed -> event-sit-back -> stage-takeover and hits every
# cam_control active/mode pair in the map (wide/idle, wide/track, close/analyze,
# both/track). Each hop is a declared exit of the current zone.
WALK = [
    ("resting", "stretching", "look"),
    ("stretching", "hunting", "track"),
    ("hunting", "examining", "focus"),
    ("examining", "performing", "trick"),
    ("performing", "roaring", "roar"),
]

_failures = 0


def check(label: str, ok, detail: str = "") -> bool:
    """Assert one check; tally failures and print a PASS/FAIL line."""
    global _failures
    ok = bool(ok)
    if not ok:
        _failures += 1
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


def main() -> int:
    zdata = load_state_data(ZONES_PATH)
    zones = Zones(zdata["zones"])
    bcfg = load_brain_config(BRAIN_PATH)
    cam_control = bcfg.get("cam_control", {})

    bus = StateBus()
    camera = make_camera(bus, enabled=True)
    machine = StateMachine(zones, initial_zone="resting", tick_hz=20.0)
    brain = Brain(machine, camera=camera,
                  config_loader=lambda: load_brain_config(BRAIN_PATH))

    # Capture CAM_CONTROL events: the controller publishes one on every intent change.
    cam_events = []

    def on_cam(event, data):
        cam_events.append(dict(data))

    bus.subscribe(on_cam)

    # Seed the "robot" pose. The sim robot snaps to the target each tick, so a
    # triggered transition commits within a couple of ticks.
    curjpos = list(zones.get("resting").action_entry_pose("hold") or [0] * 6)

    def tick() -> None:
        """One engine tick, mirroring Engine.run's order: drain -> brain.tick -> step."""
        nonlocal curjpos
        for cmd in bus.drain_commands():
            brain.on_command(cmd, curjpos)
        brain.tick(curjpos)
        target = machine.step(curjpos)
        if target is not None:
            curjpos = list(target)

    def hop(tz: str, ta: str) -> None:
        """Trigger a zone transition through the real command path and pump until it
        commits, then one extra brain.tick so the zone's cam_control intent (applied
        on zone change) is flushed."""
        bus.submit(Command(Cmd.TRIGGER_ACTION, {"zone": tz, "action": ta}))
        for _ in range(8):
            tick()
            if machine.current_zone == tz and machine.current_action == ta \
               and not machine.transitioning:
                break
        brain.tick(curjpos)

    print("=== Boot + baseline cam (resting) ===")
    tick()
    check("initial zone is resting", machine.current_zone == "resting",
          f"got {machine.current_zone}")
    check("resting cam active=wide", camera.intent.active == "wide",
          f"got {camera.intent.active}")
    check("resting cam mode=idle", camera.intent.mode == "idle",
          f"got {camera.intent.mode}")
    check("resting cam lock cleared (none)", camera.intent.lock_id is None,
          f"got {camera.intent.lock_id}")

    print("=== CAM_* telemetry stashing (stay at resting, no lock) ===")
    status_p = {"camera": "wide", "fps": 24.0, "ok": True}
    cand_p = {"candidates": []}
    track_p = {"id": None, "dx": 0.0, "dy": 0.0, "w": 0.0, "h": 0.0,
              "locked": False, "ok": True}
    face_p = {"bbox": None, "id": None, "dx": 0.0, "dy": 0.0}
    bus.submit(Command(Cmd.CAM_STATUS, status_p))
    bus.submit(Command(Cmd.CAM_CANDIDATES, cand_p))
    bus.submit(Command(Cmd.CAM_TRACK, track_p))
    bus.submit(Command(Cmd.CAM_FACE, face_p))
    tick()
    check("CAM_STATUS stashed on controller",
          camera.status is not None and camera.status.get("camera") == "wide")
    check("CAM_CANDIDATES stashed on controller",
          camera.candidates is not None and camera.candidates.get("candidates") == [])
    check("CAM_TRACK stashed on controller", camera.track is not None)
    check("CAM_TRACK stashed on machine.camera_state",
          machine.camera_state.get("ok") is True and machine.camera_state.get("dx") == 0.0)
    check("CAM_FACE stashed on controller", camera.face is not None)
    check("empty candidates do not lock", brain._hunt_lock_id is None,
          f"got {brain._hunt_lock_id}")
    check("still at resting after telemetry", machine.current_zone == "resting",
          f"got {machine.current_zone}")

    print("=== Zone walk: cam_control intents + transitions ===")
    for cur, tz, ta in WALK:
        exits = [tuple(e) for e in zones.get(cur).exits()]
        if not check(f"{cur} -> {tz}/{ta} is a declared exit", (tz, ta) in exits,
                     f"exits={exits}"):
            continue
        expected = cam_control.get(tz, {})
        prev = (camera.intent.active, camera.intent.mode, camera.intent.lock_id)
        n_events_before = len(cam_events)
        hop(tz, ta)
        committed = machine.current_zone == tz and machine.current_action == ta
        check(f"{cur} -> {tz}/{ta} transition commits", committed,
              f"zone={machine.current_zone} action={machine.current_action}")
        check(f"{tz} cam active={expected.get('active')}",
              camera.intent.active == expected.get("active"),
              f"got {camera.intent.active}")
        check(f"{tz} cam mode={expected.get('mode')}",
              camera.intent.mode == expected.get("mode"),
              f"got {camera.intent.mode}")
        check(f"{tz} cam lock resolves to None (no hunt lock)",
              camera.intent.lock_id is None, f"got {camera.intent.lock_id}")
        # set_intent publishes CAM_CONTROL exactly when the intent triple changed.
        new = (camera.intent.active, camera.intent.mode, camera.intent.lock_id)
        changed = (prev != new)
        n_events = len(cam_events) - n_events_before
        check(f"{tz} CAM_CONTROL published iff intent changed",
              n_events == (1 if changed else 0),
              f"changed={changed} events={n_events} payload={cam_events[n_events_before:]}")

    print()
    if _failures:
        print(f"=== FAILED: {_failures} check(s) failed ===")
        return 1
    print("=== All camera validations passed. ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
