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
        ("sleeping", "yawn", "sleeping_testing_bridge", "bridge"),
        ("sleeping", "yawn", "sleeping_scanning_bridge", "bridge"),
        ("sleeping_testing_bridge", "bridge", "testing", "locked"),
        ("sleeping_scanning_bridge", "bridge", "scanning", "scnr"),
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


# ---------------------------------------------------------------------------
# Shared-pose handoff: the switch happens at ANY pose shared by the current and
# target actions (not just the entry/exit anchors), and the target resumes from
# the index *after* the shared pose.
# ---------------------------------------------------------------------------
def _shared_pose_zones():
    """A zone with two actions that share a middle pose.

    A = [p1, p2, p3]  poses [[1,0,0,0,0,0], [2,0,0,0,0,0], [3,0,0,0,0,0]]
    B = [p4, p5, p2, p6]  poses [[4,0,0,0,0,0], [5,0,0,0,0,0], [2,0,0,0,0,0], [6,0,0,0,0,0]]

    The shared pose is p2 = [2,0,0,0,0,0] (index 1 in A, index 2 in B), so the
    handoff resumes B at index 3 (p6).
    """
    return validate_state_data({"zones": {
        "z": {"safezone": [[-10, 10]] * 6, "speed": 1, "actions": {
            "a": {"poses": [[1, 0, 0, 0, 0, 0], [2, 0, 0, 0, 0, 0], [3, 0, 0, 0, 0, 0]],
                  "loop": False},
            "b": {"poses": [[4, 0, 0, 0, 0, 0], [5, 0, 0, 0, 0, 0],
                             [2, 0, 0, 0, 0, 0], [6, 0, 0, 0, 0, 0]],
                  "loop": False},
        }}}
    })["zones"]


def test_compute_handoff_returns_shared_pose_and_resume_index():
    """_compute_handoff finds the shared (non-entry) pose and the index after it."""
    z = Zones(_shared_pose_zones())
    m = StateMachine(z, initial_zone="z", tick_hz=20.0)
    m.play_action("a")  # current action = a (legacy immediate commit)
    handoff, resume = m._compute_handoff("z", "b")
    assert handoff == [2, 0, 0, 0, 0, 0], handoff
    assert resume == 3, resume  # index after the shared pose (index 2) in b


def test_same_zone_shared_pose_transition_resumes_after_shared_pose():
    """Same-zone play_action transitions to the shared pose; arrival resumes the
    target at the index after the shared pose (not at the entry)."""
    z = Zones(_shared_pose_zones())
    m = StateMachine(z, initial_zone="z", tick_hz=20.0)
    m.play_action("a")
    assert m.play_action("b") is True
    # A shared-pose handoff creates a transition (not an immediate commit).
    assert m.transitioning
    assert m._transition["pose"] == [2, 0, 0, 0, 0, 0]
    assert m._transition["resume_index"] == 3
    # Simulate arrival at the shared pose -> the transition commits.
    assert m.update([2, 0, 0, 0, 0, 0]) is None
    assert m.current_action == "b"
    assert m.action_index == 3  # resumed after the shared pose, not at index 0


def test_cross_zone_shared_pose_transition_uses_shared_pose():
    """Cross-zone trigger_action transitions to the shared pose (not the entry pose)."""
    data = validate_state_data({"zones": {
        "a": {"safezone": [[-10, 10]] * 6, "speed": 1, "actions": {
            "x": {"poses": [[1, 0, 0, 0, 0, 0], [2, 0, 0, 0, 0, 0]], "loop": False}}},
        "b": {"safezone": [[-10, 10]] * 6, "speed": 1, "actions": {
            "y": {"poses": [[3, 0, 0, 0, 0, 0], [2, 0, 0, 0, 0, 0], [7, 0, 0, 0, 0, 0]],
                  "loop": False}}},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.play_action("x")
    assert m.trigger_action("b", "y") is True
    # Shared pose [2,0,0,0,0,0] (index 1 in x, index 1 in y) -> resume y at index 2.
    assert m._transition["pose"] == [2, 0, 0, 0, 0, 0]
    assert m._transition["resume_index"] == 2
    assert m.update([2, 0, 0, 0, 0, 0]) is None
    assert m.current_zone == "b"
    assert m.current_action == "y"
    assert m.action_index == 2


def test_no_shared_pose_falls_back_to_entry_pose():
    """When the actions share no pose, the handoff is the target's entry pose at index 0."""
    data = validate_state_data({"zones": {
        "z": {"safezone": [[-10, 10]] * 6, "speed": 1, "actions": {
            "a": {"poses": [[1, 0, 0, 0, 0, 0]], "loop": False},
            "b": {"poses": [[8, 0, 0, 0, 0, 0], [9, 0, 0, 0, 0, 0]], "loop": False},
        }}}
    })
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="z", tick_hz=20.0)
    m.play_action("a")
    handoff, resume = m._compute_handoff("z", "b")
    assert handoff == [8, 0, 0, 0, 0, 0]  # entry pose (poses[0])
    assert resume == 0
    # No shared pose -> no transition (legacy immediate commit).
    assert m.play_action("b") is True
    assert not m.transitioning
    assert m.current_action == "b"
    assert m.action_index == 0


def test_same_action_restart_is_immediate_not_a_handoff():
    """Re-triggering the current action is a restart (entry pose, index 0), not a
    shared-pose handoff — even though the action trivially shares poses with itself."""
    z = Zones(_shared_pose_zones())
    m = StateMachine(z, initial_zone="z", tick_hz=20.0)
    m.play_action("a")
    handoff, resume = m._compute_handoff("z", "a")
    assert handoff == [1, 0, 0, 0, 0, 0]  # entry pose (poses[0])
    assert resume == 0
    assert m.play_action("a") is True
    assert not m.transitioning
    assert m.current_action == "a"
    assert m.action_index == 0


def test_shared_pose_is_last_anchor_non_looping_hands_off():
    """When the shared pose is the target's LAST anchor and the target is non-looping,
    the target has nothing left to play, so it hands off via ``next`` (the transition
    is not left dangling)."""
    data = validate_state_data({"zones": {
        "a": {"safezone": [[-10, 10]] * 6, "speed": 1, "actions": {
            "x": {"poses": [[1, 0, 0, 0, 0, 0], [2, 0, 0, 0, 0, 0]], "loop": False},
            # "fin" holds the shared pose [2,0,0,0,0,0] as its only (last) anchor and
            # is non-looping; after the handoff there is nothing left to play.
            "fin": {"poses": [[2, 0, 0, 0, 0, 0]], "loop": False,
                    "next": [{"zone": "a", "action": "end"}]},
            "end": {"poses": [[3, 0, 0, 0, 0, 0]], "loop": True},
        }}}
    })
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.play_action("x")
    # The shared pose [2,0,0,0,0,0] is the last (and only) anchor of "fin".
    assert m.play_action("fin") is True
    assert m.transitioning
    assert m._transition["pose"] == [2, 0, 0, 0, 0, 0]
    assert m._transition["resume_index"] == 1  # index after the last anchor
    assert m.update([2, 0, 0, 0, 0, 0]) is None
    # "fin" has no further poses; it hands off via its ``next`` -> "end" (in auto
    # mode) and the just-completed transition is cleared (no dangling transition).
    assert not m.transitioning
    assert m.current_action == "end"


def main():
    failures = 0
    for name, fn in [
        ("test_all_reachable_transitions_complete", test_all_reachable_transitions_complete),
        ("test_transition_target_is_not_safezone_clamped", test_transition_target_is_not_safezone_clamped),
        ("test_compute_handoff_returns_shared_pose_and_resume_index",
         test_compute_handoff_returns_shared_pose_and_resume_index),
        ("test_same_zone_shared_pose_transition_resumes_after_shared_pose",
         test_same_zone_shared_pose_transition_resumes_after_shared_pose),
        ("test_cross_zone_shared_pose_transition_uses_shared_pose",
         test_cross_zone_shared_pose_transition_uses_shared_pose),
        ("test_no_shared_pose_falls_back_to_entry_pose",
         test_no_shared_pose_falls_back_to_entry_pose),
        ("test_same_action_restart_is_immediate_not_a_handoff",
         test_same_action_restart_is_immediate_not_a_handoff),
        ("test_shared_pose_is_last_anchor_non_looping_hands_off",
         test_shared_pose_is_last_anchor_non_looping_hands_off),
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
