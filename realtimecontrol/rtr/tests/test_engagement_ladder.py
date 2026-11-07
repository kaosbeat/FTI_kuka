"""Integration test for the engagement-ladder class state machine (Task 5).

Drives the full transition graph of :class:`rtr.brain.brain.Brain` end to end:

- escalation:  ``scan`` → ``track`` (lock) → ``farfocus`` (tick)
               → ``approach`` (far→close gate) → ``facefocus`` (close enough)
- de-escalation: ``facefocus`` → ``retreat`` (face stale) → ``scan`` (break-off)
- any class → ``scan`` on person-lost (stale ``CAM_TRACK``)

The far→close gate is exercised with real telemetry: stable ``CAM_TRACK``
samples fill the stillness window and a ``CAM_FACE`` payload with the
head-pose ``looking`` feature passes the gate.

Run directly (no pytest needed)::

    python tests/test_engagement_ladder.py

or under pytest::

    pytest tests/test_engagement_ladder.py
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ``rtr.core`` must load before ``rtr.brain.brain`` (a pre-existing circular
# import: rtr.core.__init__ -> rtr.core.engine -> rtr.brain.brain). Loading the
# package first resolves the cycle, matching how main.py boots the core.
import rtr.core  # noqa: F401

from rtr.core.commands import Cmd
from rtr.state.zones import Zones, validate_state_data
from rtr.state.machine import StateMachine
from rtr.brain.brain import Brain
from rtr.brain.config import validate_brain_config


# ---------------------------------------------------------------------------
# Zone table: a ``hunting`` zone (the detect/scan actions of the hunt loop) with
# a looping ``track`` action and a looping ``scan`` action.
# ---------------------------------------------------------------------------

def _zones():
    data = validate_state_data({"zones": {
        "hunting": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "track": {
                    "poses": [[0] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
                "scan": {
                    "poses": [[1] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
            },
        },
    }})
    return Zones(data["zones"])


# ---------------------------------------------------------------------------
# Brain config: hunt group enabled (so the ladder runs) + the unified
# ``zone_classes`` section (the single source of truth for the ladder).
# ---------------------------------------------------------------------------

HUNT_PARAMS = {
    "lost_s": 2.0,
    "min_conf": 0.1,
    "centrality_weight": 0.1,
    "attention_guard": ["locked", "intent", "curious", "gentle"],
    "detect": {"zone": "hunting", "action": "track"},
    "scan": {"zone": "hunting", "action": "scan"},
}

ZONE_CLASSES = {
    "ignore":    {"camera": {"active": "none",  "mode": "idle",    "lock": "none"},
                  "params": {}, "color": "#8a8a8a"},
    "scan":      {"camera": {"active": "wide",  "mode": "idle",    "lock": "none"},
                  "params": {"scan_s": 2.0}, "color": "#5b9bd5"},
    "track":     {"camera": {"active": "wide",  "mode": "track",   "lock": "auto"},
                  "params": dict(HUNT_PARAMS), "color": "#2e6fd0"},
    "farfocus":  {"camera": {"active": "wide",  "mode": "track",   "lock": "auto"},
                  "params": dict(HUNT_PARAMS), "color": "#00b0c8"},
    "approach":  {"camera": {"active": "wide",  "mode": "track",   "lock": "auto"},
                  "params": dict(HUNT_PARAMS, approach_speed=100,
                                  keep_in_sight=120, engage_dist=250.0),
                  "color": "#f0862c"},
    "facefocus": {"camera": {"active": "close", "mode": "analyze", "lock": "auto"},
                  "params": {"actions": ["wink", "inspect", "call"],
                              "action_s": 3.0, "face_lost_s": 2.5},
                  "color": "#d9489a"},
    "retreat":   {"camera": {"active": "wide",  "mode": "idle",    "lock": "none"},
                  "params": {"retreat_speed": 40, "break_off_s": 3.0},
                  "color": "#4caf50"},
}


class MockCamera:
    """Records ``set_intent`` calls so the test can assert the applied preset."""

    def __init__(self):
        self.calls = []

    def set_intent(self, **kw):
        self.calls.append(kw)

    def handle_telemetry(self, *a, **kw):
        pass


def _brain(machine):
    """A Brain with the hunt group enabled and the ``zone_classes`` ladder."""
    cfg = validate_brain_config({
        "autonomy": {"enabled": False, "dwell_s": 30.0,
                     "events": {"midi": False, "camera": False, "websocket": False}},
        "zone_groups": {
            "hunt": {"zones": ["hunting"], "behaviors": [], "enabled": True},
            "g": {"zones": ["hunting"], "behaviors": [], "enabled": True},
        },
        "hunt": {"zones": ["hunting"], "enabled": True, "params": dict(HUNT_PARAMS)},
        "zone_classes": ZONE_CLASSES,
    })
    cam = MockCamera()
    return Brain(machine, camera=cam, config_loader=lambda: cfg), cam


def _cmd(cmd, **payload):
    from rtr.core.commands import Command
    return Command(cmd, payload)


# ---------------------------------------------------------------------------
# Escalation: scan → track → farfocus → approach → facefocus.
# ---------------------------------------------------------------------------

def test_lock_from_scan_sets_track():
    """A candidate above ``min_conf`` locks and resets the class to ``track``."""
    m = StateMachine(_zones(), initial_zone="hunting", tick_hz=20.0)
    b, cam = _brain(m)
    assert b.current_class == "scan"
    b.on_command(_cmd(Cmd.CAM_CANDIDATES, camera="wide",
                      candidates=[{"id": 1, "x1": 0.4, "y1": 0.3,
                                    "x2": 0.6, "y2": 0.8, "conf": 0.9}]), [0] * 6)
    assert b._hunt_lock_id == 1
    assert b.current_class == "track"
    # The track camera preset (wide/track, auto lock) is applied.
    assert cam.calls[-1]["active"] == "wide"
    assert cam.calls[-1]["mode"] == "track"


def test_track_escalates_to_farfocus_on_tick():
    m = StateMachine(_zones(), initial_zone="hunting", tick_hz=20.0)
    b, _ = _brain(m)
    b.on_command(_cmd(Cmd.CAM_CANDIDATES, camera="wide",
                      candidates=[{"id": 1, "x1": 0.4, "y1": 0.3,
                                    "x2": 0.6, "y2": 0.8, "conf": 0.9}]), [0] * 6)
    assert b.current_class == "track"
    b.tick([0] * 6)
    assert b.current_class == "farfocus"


def test_farfocus_gate_requires_face_still_and_looking():
    """The far→close gate fails without face telemetry, a still bbox, or looking."""
    m = StateMachine(_zones(), initial_zone="hunting", tick_hz=20.0)
    b, _ = _brain(m)
    b.on_command(_cmd(Cmd.CAM_CANDIDATES, camera="wide",
                      candidates=[{"id": 1, "x1": 0.4, "y1": 0.3,
                                    "x2": 0.6, "y2": 0.8, "conf": 0.9}]), [0] * 6)
    b.tick([0] * 6)
    assert b.current_class == "farfocus"

    # No face telemetry yet: the gate must not pass.
    b.tick([0] * 6)
    assert b.current_class == "farfocus"

    # Face visible but the person is moving (the stillness window is unstable):
    # the gate must not pass.
    for _ in range(5):
        b.on_command(_cmd(Cmd.CAM_TRACK, camera="wide", id=1,
                           dx=0, dy=0, w=100, h=200), [0] * 6)
    b.machine.face_state = {"id": 1, "features": {"detected": True, "looking": True}}
    b._still_window = [(0, 0, 100, 200)] * 3 + [(50, 50, 100, 200)]
    b.tick([0] * 6)
    assert b.current_class == "farfocus"

    # Face visible + standing still + looking: the gate passes → approach.
    b._still_window = [(0, 0, 100, 200)] * 5
    b.tick([0] * 6)
    assert b.current_class == "approach"


def test_approach_escalates_to_facefocus_when_close():
    """``approach`` → ``facefocus`` once the bbox size passes ``engage_dist``."""
    m = StateMachine(_zones(), initial_zone="hunting", tick_hz=20.0)
    b, _ = _brain(m)
    b.on_command(_cmd(Cmd.CAM_CANDIDATES, camera="wide",
                      candidates=[{"id": 1, "x1": 0.4, "y1": 0.3,
                                    "x2": 0.6, "y2": 0.8, "conf": 0.9}]), [0] * 6)
    b.tick([0] * 6)                       # → farfocus
    b._still_window = [(0, 0, 100, 200)] * 5
    b.machine.face_state = {"id": 1, "features": {"detected": True, "looking": True}}
    b.tick([0] * 6)                       # → approach (gate passes)
    assert b.current_class == "approach"

    # Still far away: the bbox size is below engage_dist → no escalation.
    b.on_command(_cmd(Cmd.CAM_TRACK, camera="wide", id=1,
                       dx=0, dy=0, w=100, h=200), [0] * 6)
    b.tick([0] * 6)
    assert b.current_class == "approach"

    # Close enough: the bbox size passes engage_dist → facefocus.
    b.on_command(_cmd(Cmd.CAM_TRACK, camera="wide", id=1,
                       dx=0, dy=0, w=400, h=400), [0] * 6)
    b.tick([0] * 6)
    assert b.current_class == "facefocus"


# ---------------------------------------------------------------------------
# De-escalation: facefocus → retreat → scan.
# ---------------------------------------------------------------------------

def _drive_to_facefocus(b, m):
    """Helper: run the escalation ladder to ``facefocus`` with live telemetry."""
    b.on_command(_cmd(Cmd.CAM_CANDIDATES, camera="wide",
                      candidates=[{"id": 1, "x1": 0.4, "y1": 0.3,
                                    "x2": 0.6, "y2": 0.8, "conf": 0.9}]), [0] * 6)
    b.tick([0] * 6)                       # → farfocus
    for _ in range(5):
        b.on_command(_cmd(Cmd.CAM_TRACK, camera="wide", id=1,
                           dx=0, dy=0, w=100, h=200), [0] * 6)
    b.on_command(_cmd(Cmd.CAM_FACE, camera="close", id=1,
                       bbox=[280, 160, 360, 320], dx=0, dy=0,
                       features={"detected": True, "emotion": "neutral",
                                  "yaw": 0, "pitch": 0, "looking": True}), [0] * 6)
    b.tick([0] * 6)                       # → approach (gate passes)
    b.on_command(_cmd(Cmd.CAM_TRACK, camera="wide", id=1,
                       dx=0, dy=0, w=400, h=400), [0] * 6)
    b.tick([0] * 6)                       # → facefocus
    assert b.current_class == "facefocus"


def test_facefocus_deescalates_to_retreat_on_face_stale():
    m = StateMachine(_zones(), initial_zone="hunting", tick_hz=20.0)
    b, _ = _brain(m)
    _drive_to_facefocus(b, m)
    # The face went stale (the person turned away): age the face timestamp.
    b._facefocus_last_face_ts = time.time() - 5.0
    b.tick([0] * 6)
    assert b.current_class == "retreat"
    # The retreat camera preset (wide/idle, lock cleared) is applied.
    assert b.machine.face_state == {}


def test_retreat_returns_to_scan_after_break_off():
    m = StateMachine(_zones(), initial_zone="hunting", tick_hz=20.0)
    b, _ = _brain(m)
    _drive_to_facefocus(b, m)
    b._facefocus_last_face_ts = time.time() - 5.0
    b.tick([0] * 6)                       # → retreat
    assert b.current_class == "retreat"
    # The break-off time has not expired: still retreat.
    b.tick([0] * 6)
    assert b.current_class == "retreat"
    # The break-off time expires: → scan.
    b._retreat_start_ts = time.time() - 5.0
    b.tick([0] * 6)
    assert b.current_class == "scan"


# ---------------------------------------------------------------------------
# Person-lost: any class → scan (the locked person's CAM_TRACK is stale).
# ---------------------------------------------------------------------------

def test_person_lost_from_track_returns_to_scan():
    m = StateMachine(_zones(), initial_zone="hunting", tick_hz=20.0)
    b, _ = _brain(m)
    b.on_command(_cmd(Cmd.CAM_CANDIDATES, camera="wide",
                      candidates=[{"id": 1, "x1": 0.4, "y1": 0.3,
                                    "x2": 0.6, "y2": 0.8, "conf": 0.9}]), [0] * 6)
    assert b.current_class == "track"
    # The locked person's CAM_TRACK goes stale: → scan (lock cleared).
    b._hunt_last_track_ts = time.time() - 5.0
    b.tick([0] * 6)
    assert b.current_class == "scan"
    assert b._hunt_lock_id is None


def test_person_lost_from_facefocus_returns_to_scan():
    """Even from ``facefocus``, a stale lock (not just a stale face) → scan."""
    m = StateMachine(_zones(), initial_zone="hunting", tick_hz=20.0)
    b, _ = _brain(m)
    _drive_to_facefocus(b, m)
    # The lock itself goes stale (the person left the wide cam's view).
    b._hunt_last_track_ts = time.time() - 5.0
    b.tick([0] * 6)
    assert b.current_class == "scan"
    assert b._hunt_lock_id is None
    assert b.machine.face_state == {}


# ---------------------------------------------------------------------------
# Camera presets: each class applies its ``zone_classes`` camera intent.
# ---------------------------------------------------------------------------

def test_camera_presets_follow_the_ladder():
    m = StateMachine(_zones(), initial_zone="hunting", tick_hz=20.0)
    b, cam = _brain(m)
    b.on_command(_cmd(Cmd.CAM_CANDIDATES, camera="wide",
                      candidates=[{"id": 1, "x1": 0.4, "y1": 0.3,
                                    "x2": 0.6, "y2": 0.8, "conf": 0.9}]), [0] * 6)
    assert cam.calls[-1]["active"] == "wide"
    assert cam.calls[-1]["mode"] == "track"
    b.tick([0] * 6)                       # → farfocus (same wide/track preset)
    assert cam.calls[-1]["active"] == "wide"
    b._still_window = [(0, 0, 100, 200)] * 5
    b.machine.face_state = {"id": 1, "features": {"detected": True, "looking": True}}
    b.tick([0] * 6)                       # → approach (same wide/track preset)
    assert cam.calls[-1]["active"] == "wide"
    b.on_command(_cmd(Cmd.CAM_TRACK, camera="wide", id=1,
                       dx=0, dy=0, w=400, h=400), [0] * 6)
    b.tick([0] * 6)                       # → facefocus (close/analyze preset)
    assert cam.calls[-1]["active"] == "close"
    assert cam.calls[-1]["mode"] == "analyze"
    b._facefocus_last_face_ts = time.time() - 5.0
    b.tick([0] * 6)                       # → retreat (wide/idle, lock cleared)
    last = cam.calls[-1]
    assert last["active"] == "wide"
    assert last["mode"] == "idle"
    assert last.get("clear_lock") is True
    b._retreat_start_ts = time.time() - 5.0
    b.tick([0] * 6)                       # → scan (wide/idle, lock cleared)
    last = cam.calls[-1]
    assert last["active"] == "wide"
    assert last["mode"] == "idle"


if __name__ == "__main__":
    import inspect
    import traceback

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and inspect.isfunction(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failures += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    sys.exit(1 if failures else 0)
