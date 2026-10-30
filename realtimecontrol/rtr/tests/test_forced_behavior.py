"""Tests for the ``forced_behavior`` live-test-override mechanism.

Enabling a behavior (the brain-tab "enable test" button) sets ``forced_behavior`` on
the state machine and switches ``control_mode`` to ``"manual"`` so the brain stops
auto-advancing zones/actions, but does **not** freeze the robot (the current action
keeps running so the forced behavior has variable axes to drive). The behavior
policies (``build_target``) run the forced behavior's motion on the action's variable
axes instead of the action's declared behavior. Clearing (the "stop" button) clears
``forced_behavior`` and returns ``control_mode`` to ``"auto"``.

Covered:
- ``enable_behavior`` sets ``forced_behavior`` and ``control_mode = "manual"``;
- ``enable_behavior`` does NOT freeze the robot (the action keeps running), unlike
  ``set_control_mode("manual")`` which clears the action;
- ``clear_behavior`` clears ``forced_behavior`` and returns ``control_mode = "auto"``;
- the ``behavior`` property prefers the forced behavior over the action's declared one;
- ``build_target`` dispatches the forced behavior's policy (not the action's).

Run directly (no pytest needed)::

    python tests/test_forced_behavior.py

or under pytest::

    pytest tests/test_forced_behavior.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.state.zones import Zones, validate_state_data
from rtr.state.machine import StateMachine
from rtr.state.behavior import build_target


def _zone(actions):
    """A minimal single-zone table with the given actions dict.

    Axis 0's safezone is (-100, 100) (well inside the A1 hardware limit (-185, 185)),
    so its effective range is (-100, 100) and its safezone center is 0.
    """
    return {"zones": {"a": {
        "safezone": [[-100, 100]] * 6,
        "speed": 1,
        "actions": actions,
    }}}


def _machine(actions):
    """A StateMachine on a single-zone table, ready to run an action in zone ``a``."""
    data = validate_state_data(_zone(actions))
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.current_zone = "a"
    return m


# A "look" action: variable axis 0, a no-op declared behavior ("calm").
_LOOK = {"poses": [[1, 2, 3, 4, 5, 6]], "variable_axes": [0], "behavior": "calm", "speed": 1}


def test_enable_behavior_sets_forced_and_manual():
    m = _machine({"look": _LOOK})
    m.enable_behavior("vigilant")
    assert m.forced_behavior == "vigilant"
    assert m.control_mode == "manual"


def test_enable_behavior_does_not_freeze_the_robot():
    # The current action keeps running (not cleared) — the forced behavior has
    # variable axes to drive.
    m = _machine({"look": _LOOK})
    m.current_action = "look"
    m.enable_behavior("vigilant")
    assert m.current_action == "look", "enable_behavior must not freeze (action keeps running)"
    # Contrast: the plain manual path freezes by clearing the action.
    m2 = _machine({"look": _LOOK})
    m2.current_action = "look"
    m2.set_control_mode("manual")
    assert m2.current_action is None, "set_control_mode('manual') freezes (clears the action)"


def test_clear_behavior_returns_to_auto():
    m = _machine({"look": _LOOK})
    m.enable_behavior("vigilant")
    m.clear_behavior()
    assert m.forced_behavior is None
    assert m.control_mode == "auto"


def test_behavior_property_prefers_forced():
    m = _machine({"look": _LOOK})
    m.current_action = "look"
    m.enable_behavior("vigilant")
    assert m.behavior == "vigilant"


def test_behavior_property_falls_back_to_action():
    m = _machine({"look": _LOOK})
    m.current_action = "look"
    assert m.forced_behavior is None
    assert m.behavior == "calm"


def test_build_target_uses_forced_behavior():
    # The action declares "calm" (a no-op policy: it holds the anchor pose). Forcing
    # "vigilant" (a scan policy) must drive the variable axis somewhere within the
    # safezone instead of holding the anchor.
    # No forced behavior: the action's own "calm" no-op holds the anchor.
    m = _machine({"look": _LOOK})
    m.current_action = "look"
    t_plain = build_target(m, [0] * 6)
    assert t_plain == [1, 2, 3, 4, 5, 6], "calm no-op must hold the anchor pose"
    # Forced "vigilant": the scan policy drives axis 0 out of the anchor.
    m2 = _machine({"look": _LOOK})
    m2.current_action = "look"
    m2.enable_behavior("vigilant")
    t_forced = build_target(m2, [0] * 6)
    assert t_forced[0] != 1, "forced vigilant must drive the variable axis (not hold the anchor)"
    # The scan is desynced per axis (a stable per-axis phase + frequency offset), so at
    # tick 0 the axis sits at an offset within the safezone, not the rigid shared center.
    assert -100.0 <= t_forced[0] <= 100.0, "scan at tick 0 stays within the safezone"
    # Non-variable axes stay at the anchor.
    assert t_forced[1:] == [2, 3, 4, 5, 6], "non-variable axes stay at the anchor"


def main():
    tests = [
        test_enable_behavior_sets_forced_and_manual,
        test_enable_behavior_does_not_freeze_the_robot,
        test_clear_behavior_returns_to_auto,
        test_behavior_property_prefers_forced,
        test_behavior_property_falls_back_to_action,
        test_build_target_uses_forced_behavior,
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
