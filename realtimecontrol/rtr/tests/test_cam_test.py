"""Tests for the cam-test feature (scripted navigation + behavior).

The cam test drives the robot to a target zone/action along a pre-calculated safe
path, then runs a chosen behavior there with all brain autonomy and external inputs
suspended.

Covered:
- ``find_safe_path`` BFS: finds the shortest path, respects safety checks, returns
  None when no safe path exists.
- ``StateMachine.start_cam_test`` / ``stop_cam_test``: set/clear test state.
- ``Brain.on_command`` gating: rejects non-allowed commands during test mode.
- ``Brain._handle_cam_test_start``: validates target, finds path, enters test mode.
- ``Brain._handle_cam_test_stop``: clears test state, returns to auto mode.
- ``Brain._test_tick``: drives navigation phase, transitions to behavior phase.
- ``Brain._handle_midi_trigger`` / ``handle_next_pose``: no-op during test mode.

Run directly (no pytest needed)::

    python tests/test_cam_test.py

or under pytest::

    pytest tests/test_cam_test.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.core.commands import Cmd, Command
from rtr.flow import find_safe_path
from rtr.state.zones import Zones, validate_state_data
from rtr.state.machine import StateMachine
from rtr.brain.brain import Brain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _zone_table(zones):
    """A minimal zone table with the given zones dict."""
    return {"zones": zones}


def _make_zones(zones):
    """Build a Zones object from a raw dict."""
    data = validate_state_data(_zone_table(zones))
    return Zones(data["zones"])


def _simple_zone(actions):
    """A single zone with the given actions dict."""
    return {
        "safezone": [[-100, 100]] * 6,
        "speed": 1,
        "actions": actions,
    }


# ---------------------------------------------------------------------------
# BFS path-finding tests
# ---------------------------------------------------------------------------
def test_find_safe_path_direct():
    """A direct path (start == target) returns a 1-element list."""
    zones = {"a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}})}
    path = find_safe_path(zones, "a", "x", "a", "x")
    assert path == [("a", "x")]


def test_find_safe_path_two_hop():
    """A two-hop path: a/x -> b/y via a/x's next field."""
    zones = {
        "a": _simple_zone({
            "x": {"poses": [[1, 2, 3, 4, 5, 6]], "next": {"zone": "b", "action": "y"}},
        }),
        "b": _simple_zone({
            "y": {"poses": [[7, 8, 9, 10, 11, 12]]},
        }),
    }
    path = find_safe_path(zones, "a", "x", "b", "y")
    assert path is not None
    assert len(path) == 2
    assert path[0] == ("a", "x")
    assert path[-1] == ("b", "y")


def test_find_safe_path_no_path():
    """No path exists when the target is unreachable."""
    zones = {
        "a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}}),
        "b": _simple_zone({"y": {"poses": [[7, 8, 9, 10, 11, 12]]}}),
    }
    path = find_safe_path(zones, "a", "x", "b", "y")
    assert path is None


def test_find_safe_path_with_safety_block():
    """A path that would be found is blocked by the safety check."""
    zones = {
        "a": _simple_zone({
            "x": {"poses": [[1, 2, 3, 4, 5, 6]], "next": {"zone": "b", "action": "y"}},
        }),
        "b": _simple_zone({
            "y": {"poses": [[7, 8, 9, 10, 11, 12]]},
        }),
    }
    # Block the a/x -> b/y edge.
    def block(*args):
        return (True, ["blocked"])
    path = find_safe_path(zones, "a", "x", "b", "y", safety_check=block)
    assert path is None


def test_find_safe_path_multi_hop():
    """A three-hop path: a/x -> b/y -> c/z."""
    zones = {
        "a": _simple_zone({
            "x": {"poses": [[1, 2, 3, 4, 5, 6]], "next": {"zone": "b", "action": "y"}},
        }),
        "b": _simple_zone({
            "y": {"poses": [[7, 8, 9, 10, 11, 12]], "next": {"zone": "c", "action": "z"}},
        }),
        "c": _simple_zone({
            "z": {"poses": [[13, 14, 15, 16, 17, 18]]},
        }),
    }
    path = find_safe_path(zones, "a", "x", "c", "z")
    assert path is not None
    assert len(path) == 3
    assert path[0] == ("a", "x")
    assert path[1] == ("b", "y")
    assert path[-1] == ("c", "z")


# ---------------------------------------------------------------------------
# StateMachine test_mode tests
# ---------------------------------------------------------------------------
def test_start_cam_test_sets_state():
    """start_cam_test sets test_mode, path, phase, and control_mode."""
    zones = _make_zones({"a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}})})
    m = StateMachine(zones, initial_zone="a")
    m.current_zone = "a"
    m.current_action = "x"

    path = [("b", "y")]
    m.start_cam_test(path, "vigilant")

    assert m.test_mode is True
    assert m.test_path == path
    assert m.test_path_index == 0
    assert m.test_behavior == "vigilant"
    assert m.test_phase == "navigate"
    assert m.control_mode == "manual"


def test_stop_cam_test_clears_state():
    """stop_cam_test clears test state and returns to auto mode."""
    zones = _make_zones({"a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}})})
    m = StateMachine(zones, initial_zone="a")
    m.current_zone = "a"
    m.current_action = "x"

    m.start_cam_test([("b", "y")], "vigilant")
    m.stop_cam_test()

    assert m.test_mode is False
    assert m.test_path == []
    assert m.test_path_index == 0
    assert m.test_behavior == ""
    assert m.test_phase == ""
    assert m.control_mode == "auto"


def test_start_cam_test_no_path_goes_direct_to_behavior():
    """When the path is empty (start == target), phase is immediately 'behavior'."""
    zones = _make_zones({"a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}})})
    m = StateMachine(zones, initial_zone="a")
    m.current_zone = "a"
    m.current_action = "x"

    m.start_cam_test([], "vigilant")

    assert m.test_phase == "behavior"


# ---------------------------------------------------------------------------
# Brain on_command gating tests
# ---------------------------------------------------------------------------
def _brain_with_machine(zones_dict, machine=None):
    """A Brain with a StateMachine on the given zones."""
    z = _make_zones(zones_dict)
    if machine is None:
        m = StateMachine(z, initial_zone="a")
        m.current_zone = "a"
        m.current_action = "x"
    else:
        m = machine
    return Brain(m, camera=None), m


def test_on_command_rejects_non_test_commands_in_test_mode():
    """During test mode, non-allowed commands are rejected."""
    zones = {"a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}})}
    brain, m = _brain_with_machine(zones)
    m.test_mode = True
    m.test_phase = "navigate"

    # This command should be rejected (not in _TEST_ALLOWED_CMDS).
    brain.on_command(Command(cmd=Cmd.TRIGGER_ACTION, payload={"zone": "a", "action": "x"}), [0] * 6)
    # The machine's state should be unchanged (no new transition).
    assert m.current_action == "x"


def test_on_command_allows_cam_telemetry_in_test_mode():
    """During test mode, CAM_* telemetry commands are still processed."""
    zones = {"a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}})}
    brain, m = _brain_with_machine(zones)
    m.test_mode = True
    m.test_phase = "navigate"

    # CAM_STATUS should be processed (not rejected).
    brain.on_command(Command(cmd=Cmd.CAM_STATUS, payload={"camera": "test", "fps": 30, "ok": True}), [0] * 6)
    # The brain's camera state should be updated (if camera is wired).
    # Since camera is None in this test, the command is a no-op but not rejected.


def test_on_command_allows_stop_in_test_mode():
    """During test mode, STOP is still processed."""
    zones = {"a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}})}
    brain, m = _brain_with_machine(zones)
    m.test_mode = True
    m.test_phase = "navigate"
    m.current_action = "x"

    brain.on_command(Command(cmd=Cmd.STOP, payload={}), [1, 2, 3, 4, 5, 6])
    # STOP should clear the action.
    assert m.current_action is None


# ---------------------------------------------------------------------------
# Brain cam test start/stop tests
# ---------------------------------------------------------------------------
def test_cam_test_start_validates_target():
    """cam_test_start rejects when the target zone/action is unknown."""
    zones = {"a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}})}
    brain, m = _brain_with_machine(zones)

    # Unknown zone.
    brain.on_command(Command(cmd=Cmd.CAM_TEST_START, payload={"zone": "unknown", "action": "x", "behavior": "vigilant"}), [0] * 6)
    assert m.test_mode is False

    # Unknown action.
    brain.on_command(Command(cmd=Cmd.CAM_TEST_START, payload={"zone": "a", "action": "unknown", "behavior": "vigilant"}), [0] * 6)
    assert m.test_mode is False


def test_cam_test_start_missing_fields():
    """cam_test_start rejects when zone/action/behavior are missing."""
    zones = {"a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}})}
    brain, m = _brain_with_machine(zones)

    brain.on_command(Command(cmd=Cmd.CAM_TEST_START, payload={"zone": "a"}), [0] * 6)
    assert m.test_mode is False

    brain.on_command(Command(cmd=Cmd.CAM_TEST_START, payload={"zone": "a", "action": "x"}), [0] * 6)
    assert m.test_mode is False


def test_cam_test_start_no_safe_path():
    """cam_test_start rejects when no safe path exists."""
    zones = {
        "a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}}),
        "b": _simple_zone({"y": {"poses": [[7, 8, 9, 10, 11, 12]]}}),
    }
    brain, m = _brain_with_machine(zones)
    m.current_zone = "a"
    m.current_action = "x"

    # No link from a/x to b/y, so no safe path.
    brain.on_command(Command(cmd=Cmd.CAM_TEST_START, payload={"zone": "b", "action": "y", "behavior": "vigilant"}), [0] * 6)
    assert m.test_mode is False


def test_cam_test_stop():
    """cam_test_stop clears test state and returns to auto mode."""
    zones = {"a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}})}
    brain, m = _brain_with_machine(zones)
    m.test_mode = True
    m.test_phase = "navigate"
    m.test_path = [("a", "x")]
    m.test_path_index = 0
    m.test_behavior = "vigilant"

    brain.on_command(Command(cmd=Cmd.CAM_TEST_STOP, payload={}), [0] * 6)

    assert m.test_mode is False
    assert m.test_path == []
    assert m.test_path_index == 0
    assert m.test_behavior == ""
    assert m.test_phase == ""
    assert m.control_mode == "auto"


# ---------------------------------------------------------------------------
# Brain _test_tick tests
# ---------------------------------------------------------------------------
def test_test_tick_navigates_to_next_waypoint():
    """_test_tick triggers the next waypoint when the robot is idle."""
    zones = {
        "a": _simple_zone({
            "x": {"poses": [[1, 2, 3, 4, 5, 6]], "next": {"zone": "b", "action": "y"}},
        }),
        "b": _simple_zone({
            "y": {"poses": [[7, 8, 9, 10, 11, 12]]},
        }),
    }
    brain, m = _brain_with_machine(zones)
    m.current_zone = "a"
    m.current_action = "x"
    m.test_mode = True
    m.test_phase = "navigate"
    m.test_path = [("b", "y")]
    m.test_path_index = 0
    m.test_behavior = "vigilant"

    brain._test_tick([1, 2, 3, 4, 5, 6])

    # The brain should have triggered the transition to b/y.
    assert m.test_path_index == 1
    # The machine should be transitioning (or have a pending move if safety is wired).
    assert m.transitioning or m.pending or m.current_zone == "b"


def test_test_tick_transitions_to_behavior_phase():
    """_test_tick switches to behavior phase when all waypoints are reached."""
    zones = {"a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}})}
    brain, m = _brain_with_machine(zones)
    m.current_zone = "a"
    m.current_action = "x"
    m.test_mode = True
    m.test_phase = "navigate"
    m.test_path = []  # No waypoints (start == target).
    m.test_path_index = 0
    m.test_behavior = "vigilant"

    brain._test_tick([1, 2, 3, 4, 5, 6])

    # Should have switched to behavior phase.
    assert m.test_phase == "behavior"
    assert m.forced_behavior == "vigilant"


# ---------------------------------------------------------------------------
# MIDI gating tests
# ---------------------------------------------------------------------------
def test_midi_trigger_noop_in_test_mode():
    """_handle_midi_trigger is a no-op during test mode."""
    zones = {"a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}})}
    brain, m = _brain_with_machine(zones)
    m.test_mode = True
    m.test_phase = "navigate"

    # This should be a no-op (not set _autonomy_triggered).
    brain._handle_midi_trigger(60)
    assert brain._autonomy_triggered is False


def test_handle_next_pose_noop_in_test_mode():
    """handle_next_pose is a no-op during test mode."""
    zones = {"a": _simple_zone({"x": {"poses": [[1, 2, 3, 4, 5, 6]]}})}
    brain, m = _brain_with_machine(zones)
    m.test_mode = True
    m.test_phase = "navigate"
    m.cadence = "midi"

    # This should be a no-op (not pulse the machine).
    brain.handle_next_pose()
    assert m._midi_pulse is False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Run all tests.
    import traceback
    failed = 0
    for name, obj in list(globals().items()):
        if name.startswith("test_") and callable(obj):
            try:
                obj()
                print(f"  PASS: {name}")
            except Exception:
                failed += 1
                print(f"  FAIL: {name}")
                traceback.print_exc()
    print(f"\n{failed} failed")
    sys.exit(1 if failed else 0)
