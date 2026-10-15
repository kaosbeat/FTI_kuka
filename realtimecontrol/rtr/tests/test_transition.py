"""Regression test: zone transitions must complete (not stall).

Bug this guards against
------------------------
A transition target (exitpos / startpos) used to be floored to the *current* zone's
safezone (``brain._clamp``). An exit pose is by definition outside the current zone's
safezone (it is the hand-off to the next zone), so the clamp moved it. The state
machine's arrival check, however, compares the robot's pose to the *unclamped* step
(``comparelist`` in ``StateMachine.update``). The robot therefore chased a pose it could
never reach -> the transition never advanced, the robot froze at the clamped pose with
``moving=False``, and (because the transition branch takes priority every tick) the
whole core stayed stuck until a zone reload or reset.

Fix: floor transition targets to the *hardware* limits only
(``behavior.hardware_clamp``), so the curated exit/start poses are reached exactly and
the arrival check fires.

Ported to the consolidated :class:`rtr.state.machine.StateMachine` (the per-tick
``step`` driver moved here from the old ``Brain``; the clamp helpers live in
:mod:`rtr.state.behavior`).

Run directly (no pytest needed)::

    python tests/test_transition.py

or under pytest::

    pytest tests/test_transition.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.state.zones import Zones, load_state_data, validate_state_data
from rtr.state.machine import StateMachine
from rtr.state.behavior import clamp, hardware_clamp
from rtr.robot.sim import SimRobot


def _zones():
    return Zones(load_state_data(os.path.join(ROOT, "zones.json"))["zones"])


def _run_transition(zones, start_zone, start_action, target_zone, target_action,
                    ticks: int = 2000) -> tuple:
    """Drive the machine + sim loop for a trigger_action and report whether it committed.

    The robot starts at the source action's entry pose and is transitioned to the
    target action's entry pose. Returns ``(committed, final_zone, transitioning)``.
    """
    home = list(zones.get(start_zone).action_entry_pose(start_action))
    sim = SimRobot(home=home, tick_hz=20.0)
    machine = StateMachine(zones, initial_zone=start_zone, tick_hz=20.0)
    machine.play_action(start_action)
    machine.trigger_action(target_zone, target_action)

    sim.connect()
    committed = False
    for _ in range(ticks):
        curjpos = sim.get_curjpos()
        t = machine.step(curjpos)
        if t is not None:
            sim.move_joint(t, machine.speed)
        if machine.current_zone == target_zone and not machine.transitioning:
            committed = True
            break
    return committed, machine.current_zone, machine.transitioning


def test_all_reachable_transitions_complete():
    """Every transition driven through a real exits-graph edge must commit in bounded time."""
    zones = _zones()
    pairs = [
        ("resting", "hold", "sleeping", "breathe"),
        ("resting", "hold", "stretching", "look"),
        ("stretching", "look", "hunting", "track"),
        ("hunting", "track", "growling", "growl"),
    ]
    for start_zone, start_action, target_zone, target_action in pairs:
        committed, final_zone, transitioning = _run_transition(
            zones, start_zone, start_action, target_zone, target_action)
        assert committed, (
            f"transition {start_zone!r}/{start_action!r} -> "
            f"{target_zone!r}/{target_action!r} did not commit "
            f"(final_zone={final_zone!r}, transitioning={transitioning})"
        )
        assert not transitioning, (
            f"transition {start_zone!r}/{start_action!r} -> "
            f"{target_zone!r}/{target_action!r} left a pending transition"
        )


def test_transition_target_is_not_safezone_clamped():
    """The machine must floor a transition target to the hardware limits, not the safezone.

    An entry pose sits outside the source zone's safezone by design (it is the hand-off
    to the next zone). If it were safezone-clamped, the robot would chase a pose it can
    never reach and the arrival check would never fire — the transition stalls. Here the
    entry pose is outside the source safezone on every axis but within the hardware
    limits, so the hardware clamp leaves it untouched and the transition commits.
    """
    data = validate_state_data({"zones": {
        "a": {"safezone": [[-5, 5]] * 6, "speed": 1,
             "actions": {"stay": {"poses": [[0] * 6]}}},
        "b": {"safezone": [[0, 10]] * 6, "speed": 1,
             "actions": {"enter": {"poses": [[8] * 6]}}},
    }})
    z = Zones(data["zones"])
    machine = StateMachine(z, initial_zone="a", tick_hz=20.0)
    assert machine.trigger_action("b", "enter") is True
    entry = list(machine._transition["pose"])  # [8]*6

    # The entry pose is outside a's safezone on every axis (the safezone clamp moves
    # it), but it is within the hardware limits (the hardware clamp leaves it untouched).
    assert clamp(machine, entry) != entry, "expected the entry pose to be outside the source safezone"
    assert hardware_clamp(entry) == entry, (
        "hardware clamp should leave the (in-hardware-limits) entry pose untouched"
    )

    # Driving the loop: the robot reaches the (hardware-clamped) entry pose and the
    # transition commits. With the old safezone clamp this would stall.
    sim = SimRobot(home=[0] * 6, tick_hz=20.0)
    sim.connect()
    committed = False
    for _ in range(2000):
        curjpos = sim.get_curjpos()
        t = machine.step(curjpos)
        if t is not None:
            sim.move_joint(t, machine.speed)
        if machine.current_zone == "b" and not machine.transitioning:
            committed = True
            break
    assert committed, "transition did not commit (stalled) — the target must be hardware-clamped"


def main():
    failures = 0
    for name, fn in [
        ("test_all_reachable_transitions_complete", test_all_reachable_transitions_complete),
        ("test_transition_target_is_not_safezone_clamped", test_transition_target_is_not_safezone_clamped),
    ]:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:
            failures += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{'ALL TESTS PASSED' if failures == 0 else f'{failures} TEST(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
