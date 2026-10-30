"""Regression tests: single-action zone auto-return to the runtime previous zone.

A single-action zone whose only action has no valid ``next`` (and the zone has no
``exits``) used to dead-end the robot: after the action completed the machine held
the last pose and never moved again. The fix tracks the runtime previous zone
(``_prev_zone``, captured in ``_commit`` on every cross-zone arrival, cleared on
``reset``) and, in ``_advance_after_action``, returns a single-action zone to that
previous zone when it has no valid next/exits. Multi-action zones keep the legacy
hold (they are the operator's choice points), and an action's ``next`` still takes
priority over the fallback.

Run directly (no pytest needed)::

    python tests/test_autoreturn.py

or under pytest::

    pytest tests/test_autoreturn.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.state.zones import Zones, validate_state_data
from rtr.state.machine import StateMachine
from rtr.robot.sim import SimRobot


def _graph():
    """A two-zone graph: "a" (the previous zone) and "b" (a single-action dead-end).

    - "a": one enabled action "stay" (poses [[0]*6]).
    - "b": one single-run action "go" (poses [[8]*6], loop=false), NO next, NO exits.
    """
    return validate_state_data({"zones": {
        "a": {"safezone": [[-5, 5]] * 6, "speed": 1,
             "actions": {"stay": {"poses": [[0] * 6]}}},
        "b": {"safezone": [[5, 10]] * 6, "speed": 1,
             "actions": {"go": {"poses": [[8] * 6], "loop": False}}},
    }})


def test_single_action_zone_auto_returns_to_prev_zone():
    """A single-action dead-end zone returns to the runtime previous zone.

    The robot transitions a -> b, runs b's single-run action, and — with no next/exits
    — must be handed back to "a" (the zone it came from) instead of dead-ending.
    """
    z = Zones(_graph()["zones"])
    machine = StateMachine(z, initial_zone="a", tick_hz=20.0)
    sim = SimRobot(home=[0] * 6, tick_hz=20.0)
    machine.trigger_action("b", "go")
    sim.connect()

    visited_b = False
    for _ in range(2000):
        curjpos = sim.get_curjpos()
        t = machine.step(curjpos)
        if t is not None:
            sim.move_joint(t, machine.speed)
        if machine.current_zone == "b":
            visited_b = True
        # The robot is back in "a" and not mid-transition: the auto-return completed.
        if machine.current_zone == "a" and visited_b and not machine.transitioning:
            break
    assert visited_b, "robot never reached the dead-end zone 'b'"
    assert machine.current_zone == "a", (
        f"expected the robot to auto-return to 'a', but it ended in "
        f"{machine.current_zone!r} (transitioning={machine.transitioning})"
    )
    assert not machine.transitioning, "auto-return left a dangling transition"


def test_commit_captures_prev_zone():
    """A cross-zone commit records the zone the robot came from.

    The dead-end zone "b" auto-returns immediately, so capture ``_prev_zone`` at the
    first tick where the robot is in "b" (before it hands back to "a").
    """
    z = Zones(_graph()["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    assert m._prev_zone is None
    assert m.trigger_action("b", "go") is True
    sim = SimRobot(home=[0] * 6, tick_hz=20.0)
    sim.connect()
    captured = None
    for _ in range(2000):
        curjpos = sim.get_curjpos()
        t = m.step(curjpos)
        if t is not None:
            sim.move_joint(t, m.speed)
        if m.current_zone == "b" and m._prev_zone is not None:
            captured = m._prev_zone
            break
    assert captured == "a", f"expected _prev_zone captured as 'a', got {captured!r}"


def test_next_takes_priority_over_prev_fallback():
    """A single-action zone WITH a valid next uses next, not the previous-zone fallback."""
    data = validate_state_data({"zones": {
        "a": {"safezone": [[-5, 5]] * 6, "speed": 1,
             "actions": {"stay": {"poses": [[0] * 6]}}},
        "b": {"safezone": [[5, 10]] * 6, "speed": 1,
             "actions": {"go": {"poses": [[8] * 6], "loop": False,
                               "next": {"zone": "c", "action": "enter"}}}},
        "c": {"safezone": [[10, 15]] * 6, "speed": 1,
             "actions": {"enter": {"poses": [[12] * 6]}}},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="b", tick_hz=20.0)
    m._prev_zone = "a"
    m.play_action("go")
    m._advance_after_action("go")
    assert m._transition is not None and m._transition["zone"] == "c", (
        "the action's next must take priority over the previous-zone fallback"
    )


def test_multi_action_zone_hold_data_is_rejected():
    """A multi-action zone with a non-looping action, no valid next, and no valid
    exit is rejected by validation (it would dead-end the robot).

    The legacy "operator choice point" hold (a multi-action zone holding after a
    non-looping action completes) is no longer loadable: the strict dead-end
    validation rejects the data at load time so the robot is never left holding.
    The :meth:`StateMachine._advance_after_action` hold branch remains as a safety
    fallback (e.g. for a zone reload that removes the next at runtime).
    """
    data = {"zones": {
        "a": {"safezone": [[-5, 5]] * 6, "speed": 1,
             "actions": {"stay": {"poses": [[0] * 6]}}},
        "b": {"safezone": [[5, 10]] * 6, "speed": 1,
             "actions": {
                 "go": {"poses": [[8] * 6], "loop": False},
                 "idle": {"poses": [[8] * 6]},
             }},
    }}
    try:
        validate_state_data(data)
        raised = False
    except ValueError:
        raised = True
    assert raised, "expected validate_state_data to reject the multi-action hold data"


def test_reset_clears_prev_zone():
    """reset() clears the previous-zone tracking and snaps to the resting zone."""
    data = validate_state_data({"zones": {
        "resting": {"safezone": [[-5, 5]] * 6, "speed": 1,
                    "actions": {"stay": {"poses": [[0] * 6]}}},
        "a": {"safezone": [[-5, 5]] * 6, "speed": 1,
              "actions": {"stay": {"poses": [[0] * 6]}}},
        "b": {"safezone": [[5, 10]] * 6, "speed": 1,
              "actions": {"go": {"poses": [[8] * 6], "loop": False}}},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m._prev_zone = "a"
    m.reset([0] * 6)
    assert m._prev_zone is None
    assert m.current_zone == "resting"
