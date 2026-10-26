"""Tests for the ``bridge`` behaviour.

``bridge`` is a no-op policy for bridge zones: it drives no variable axes and does no
random movement. A bridge action is the unified ``poses`` form (an ordered list of
anchor poses, one from the source-zone end + one from the target-zone start,
``variable_axes: []``, ``loop: false``) tagged with ``behavior: "bridge"`` so the robot
transitions safely between zones.

Covered:
- an action may carry a ``behavior`` marker (accepted only when it names a known
  behaviour; the bridge action uses ``"bridge"``);
- an action may NOT pair ``behavior: "bridge"`` with a non-empty
  ``variable_axes`` list (the no-op constraint);
- the no-op dispatch: a ``bridge`` action holds its pose (no motion driven);
- the real ``zones.json`` ``resting_sleeping_bridge/bridge`` action loads and is tagged.

Run directly (no pytest needed)::

    python tests/test_bridge_behavior.py

or under pytest::

    pytest tests/test_bridge_behavior.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.state.zones import BEHAVIORS, Zones, load_state_data, validate_state_data
from rtr.state.machine import StateMachine
from rtr.state.behavior import build_target


def _zones():
    return Zones(load_state_data(os.path.join(ROOT, "zones.json"))["zones"])


def _zone(actions):
    """A minimal single-zone table with the given actions dict."""
    return {"zones": {"a": {
        "safezone": [[0, 10]] * 6,
        "speed": 1,
        "actions": actions,
    }}}


# ---------------------------------------------------------------------------
# Validation: the ``behavior`` marker on an action.
# ---------------------------------------------------------------------------
def test_legacy_action_accepts_bridge_behavior():
    data = validate_state_data(_zone({
        "bridge": {"poses": [[1] * 6, [2] * 6], "loop": False,
                   "behavior": "bridge"},
    }))
    assert data["zones"]["a"]["actions"]["bridge"]["behavior"] == "bridge"
    # The accessor reports the marker (not the "calm" default).
    z = Zones(data["zones"])
    assert z.get("a").action_behavior("bridge") == "bridge"


def test_legacy_action_without_behavior_is_still_valid():
    # ``behavior`` is optional on an action; absence must not be an error.
    data = validate_state_data(_zone({
        "x": {"poses": [[1] * 6]},
    }))
    z = Zones(data["zones"])
    # No marker -> the accessor falls back to "calm".
    assert z.get("a").action_behavior("x") == "calm"


def test_legacy_action_rejects_unknown_behavior():
    try:
        validate_state_data(_zone({
            "x": {"poses": [[1] * 6], "behavior": "not-a-behavior"},
        }))
        raise AssertionError("expected ValueError for an unknown behavior marker")
    except ValueError as exc:
        assert "behavior" in str(exc)


# ---------------------------------------------------------------------------
# Validation: the no-op constraint on the unified form.
# ---------------------------------------------------------------------------
def test_var_axis_bridge_requires_empty_variable_axes():
    try:
        validate_state_data(_zone({
            "x": {"poses": [[1] * 6], "variable_axes": [0, 2], "behavior": "bridge",
                  "speed": 1},
        }))
        raise AssertionError("expected ValueError: bridge must not drive variable axes")
    except ValueError as exc:
        assert "bridge" in str(exc)


def test_var_axis_bridge_with_empty_variable_axes_is_valid():
    data = validate_state_data(_zone({
        "x": {"poses": [[1] * 6], "variable_axes": [], "behavior": "bridge", "speed": 1},
    }))
    assert data["zones"]["a"]["actions"]["x"]["behavior"] == "bridge"


def test_bridge_is_a_known_behavior():
    assert "bridge" in BEHAVIORS


# ---------------------------------------------------------------------------
# The no-op dispatch: a ``bridge`` action holds its pose (no motion driven).
# ---------------------------------------------------------------------------
def test_bridge_noop_holds_base_pose():
    data = validate_state_data(_zone({
        "hold": {"poses": [[1, 2, 3, 4, 5, 6]], "variable_axes": [], "behavior": "bridge",
                "speed": 10},
    }))
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.current_zone = "a"
    m.current_action = "hold"
    t = build_target(m, [0] * 6)
    assert t == [1, 2, 3, 4, 5, 6], "bridge no-op must hold the base pose (no motion)"


def test_bridge_behavior_property_surfaces_marker():
    # The machine's ``behavior`` (the state-frame mode) reports the marker for a
    # bridge action so the display can show it.
    data = validate_state_data(_zone({
        "bridge": {"poses": [[1] * 6, [2] * 6], "loop": False,
                   "behavior": "bridge"},
    }))
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.current_zone = "a"
    m.current_action = "bridge"
    assert m.behavior == "bridge"


# ---------------------------------------------------------------------------
# The real zones.json bridge action.
# ---------------------------------------------------------------------------
def test_real_zones_bridge_action_is_tagged():
    z = _zones()
    assert z.has("sleeping_testing_bridge")
    act = z.get("sleeping_testing_bridge").actions()["bridge"]
    # Unified form: curated anchor poses, no wiggle, no loop.
    assert "poses" in act and "pos" not in act and "base_pose" not in act
    assert len(act["poses"]) >= 1
    assert act["loop"] is False
    assert act["behavior"] == "bridge"
    # The accessor surfaces the marker.
    assert z.get("sleeping_testing_bridge").action_behavior("bridge") == "bridge"


def main():
    tests = [
        test_legacy_action_accepts_bridge_behavior,
        test_legacy_action_without_behavior_is_still_valid,
        test_legacy_action_rejects_unknown_behavior,
        test_var_axis_bridge_requires_empty_variable_axes,
        test_var_axis_bridge_with_empty_variable_axes_is_valid,
        test_bridge_is_a_known_behavior,
        test_bridge_noop_holds_base_pose,
        test_bridge_behavior_property_surfaces_marker,
        test_real_zones_bridge_action_is_tagged,
    ]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as exc:
            failures += 1
            print(f"FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{'ALL TESTS PASSED' if failures == 0 else f'{failures} TEST(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
