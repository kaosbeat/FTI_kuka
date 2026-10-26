"""Tests for the consolidated StateMachine (zone × mode × action).

Covers the behaviour that moved from the old ``Brain`` into
:class:`rtr.state.machine.StateMachine`:

- mode set / override (validated against ``MODES``),
- play / clear action,
- ``request_zone`` path routing incl. per-edge ``exitpose_for`` selection,
- commit adopts the zone's default mode (if declared) and clears any playing action,
- transition steps are hardware-clamped only (the safezone floor must not apply),
- ``hold`` keeps the target,
- wander / random / track stay inside the effective floor (safezone ∩ hardware).

Run directly (no pytest needed)::

    python tests/test_state_machine.py

or under pytest::

    pytest tests/test_state_machine.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest

from rtr.state.zones import Zones, load_state_data, validate_state_data
from rtr.state.machine import StateMachine
from rtr.state.behavior import hardware_clamp


def _zones():
    return Zones(load_state_data(os.path.join(ROOT, "zones.json"))["zones"])


# ---------------------------------------------------------------------------
# Mode set / override.
# ---------------------------------------------------------------------------
def test_set_mode_and_override():
    pytest.skip("zone-level mode (m.mode / set_mode) was removed in the multi-pose refactor; "
                "motion behaviour is now a per-action policy, not a zone mode")


def test_set_mode_action_starts_first_enabled_action():
    pytest.skip("mode 'action' was removed; the first-enabled-action entry is now resolved by "
                "trigger_action, not by a mode change")


# ---------------------------------------------------------------------------
# Play / clear action.
# ---------------------------------------------------------------------------
def test_play_and_clear_action():
    data = validate_state_data({"zones": {
        "a": {"safezone": [[0, 10]] * 6, "speed": 1,
             "actions": {
                 "x": {"poses": [[1] * 6]},
                 "y": {"poses": [[2] * 6], "enabled": False},
             }},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    assert m.play_action("x") is True
    assert m.current_action == "x"
    m.clear_action()
    assert m.current_action is None
    # Unknown / disabled actions are rejected.
    assert m.play_action("does-not-exist") is False
    assert m.current_action is None
    assert m.play_action("y") is False
    assert m.current_action is None


# ---------------------------------------------------------------------------
# request_zone path routing + per-edge exitpose selection.
# ---------------------------------------------------------------------------
def test_request_zone_routes_through_graph():
    pytest.skip("request_zone (multi-hop graph walk) was removed; a transition is now a single "
                "trigger_action to a target action's entry pose")


def test_request_zone_uses_per_edge_exitpose():
    pytest.skip("per-edge exitposes were removed with request_zone; a transition now targets the "
                "single entry pose (poses[0]) of the target action")


# ---------------------------------------------------------------------------
# Commit adopts the default mode + clears the action.
# ---------------------------------------------------------------------------
def test_commit_adopts_default_mode_and_clears_action():
    pytest.skip("zone-level default-mode adoption was removed; _commit now only sets the zone "
                "and begins the target action")


def test_commit_keeps_mode_when_zone_declares_none():
    pytest.skip("zone-level mode was removed; commit no longer touches a mode")


# ---------------------------------------------------------------------------
# Transition steps are hardware-clamped only.
# ---------------------------------------------------------------------------
def test_transition_step_is_hardware_clamped():
    data = validate_state_data({"zones": {
        "a": {"safezone": [[-5, 5]] * 6, "speed": 1,
             "actions": {"stay": {"poses": [[0] * 6]}}},
        "b": {"safezone": [[0, 10]] * 6, "speed": 1,
             "actions": {"enter": {"poses": [[8] * 6]}}},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    assert m.trigger_action("b", "enter") is True
    assert m.transitioning
    entry = list(m._transition["pose"])  # [8]*6: outside a's safezone, within hardware
    # The entry pose is outside a's safezone on every axis, so a safezone clamp would
    # move it; the hardware clamp leaves it untouched (it is within the hardware limits).
    assert hardware_clamp(entry) == entry
    t = m.step([0] * 6)
    assert t == hardware_clamp(entry), "transition step must be hardware-clamped only"


# ---------------------------------------------------------------------------
# hold keeps the target.
# ---------------------------------------------------------------------------
def test_hold_keeps_target():
    # With no action running, the machine holds the last commanded target.
    data = validate_state_data({"zones": {
        "a": {"safezone": [[-5, 5]] * 6, "speed": 1,
             "actions": {"x": {"poses": [[1] * 6]}}},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.set_target([2] * 6, "joint")
    before = list(m.target)
    t = m.step([0] * 6)
    assert t == before
    assert m.target == before


# ---------------------------------------------------------------------------
# wander / random / track stay inside the effective floor.
# ---------------------------------------------------------------------------
def test_wander_stays_in_floor():
    pytest.skip("zone mode 'wander' was removed; wander is now the 'restless' per-action behaviour")


def test_random_stays_in_floor():
    pytest.skip("zone mode 'random' was removed; random motion is now the 'spontaneous' "
                "per-action behaviour")


def test_track_stays_in_floor_and_advances_a1():
    pytest.skip("zone mode 'track' was removed; tracking is now a per-action camera behaviour")


# ---------------------------------------------------------------------------
# Action kinds: loop vs single-run, and the target hand-off (Step 2).
# ---------------------------------------------------------------------------
def test_loop_action_wraps_around_and_keeps_playing():
    data = validate_state_data({"zones": {
        "a": {"startpos": [0] * 6, "exitpos": [0] * 6,
             "safezone": [[0, 10]] * 6, "exits": [], "speed": 1,
             "actions": {"spin": {"poses": [[1] * 6, [2] * 6, [3] * 6]}}},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.play_action("spin")
    # 3 anchors, loop defaults to True: with cadence_every=1 each step advances one
    # anchor; after 3 steps the index wraps to 0 and the action is still playing.
    m.cadence_every = 1
    m.step([0] * 6); m.step([0] * 6); m.step([0] * 6)
    assert m.action_index == 0
    assert m.current_action == "spin"


def test_single_run_advances_to_next_action_in_zone():
    data = validate_state_data({"zones": {
        "a": {"safezone": [[0, 10]] * 6, "speed": 1,
             "actions": {
                 "go": {"poses": [[1] * 6, [2] * 6],
                        "loop": False, "next": [{"zone": "a", "action": "done"}]},
                 "done": {"poses": [[5] * 6]},
             }},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.play_action("go")
    m.cadence_every = 1
    m.step([0] * 6)          # index 0 -> 1 (not yet complete)
    m.step([0] * 6)          # index 1 -> 2 >= len(2); loop False -> advance to done
    assert m.current_action == "done"
    assert m.action_index == 0


def test_single_run_with_no_target_stops_the_action():
    data = validate_state_data({"zones": {
        "a": {"startpos": [0] * 6, "exitpos": [0] * 6,
             "safezone": [[0, 10]] * 6, "exits": [], "speed": 1,
             "actions": {"once": {"poses": [[1] * 6, [2] * 6], "loop": False}}},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.play_action("once")
    m.cadence_every = 1
    m.step([0] * 6)          # index 0 -> 1
    m.step([0] * 6)          # index 1 -> 2 >= len(2); loop False; no target -> stop
    assert m.current_action is None


def test_single_run_exit_triggers_transition_and_entry():
    data = validate_state_data({"zones": {
        "a": {"safezone": [[0, 10]] * 6, "speed": 1,
             "actions": {
                 "leave": {"poses": [[1] * 6], "loop": False,
                            "next": [{"zone": "b", "action": "enter"}]},
             }},
        "b": {"safezone": [[0, 10]] * 6, "speed": 1,
             "actions": {"enter": {"poses": [[2] * 6]}}},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.play_action("leave")
    m.cadence_every = 1
    m.step([0] * 6)          # index 0 -> 1 >= len(1); loop False -> advance -> exit transition
    assert m.transitioning
    assert m._transition["zone"] == "b"
    assert m._transition["action"] == "enter"
    # A transition is a single target pose: the entry pose (poses[0]) of the target action.
    assert m._transition["pose"] == [2] * 6
    # Committing arrival plays the entry action.
    m._commit({"zone": "b", "action": "enter", "pose": [2] * 6, "resume_index": 0})
    assert m.current_zone == "b"
    assert m.current_action == "enter"
    assert m.action_index == 0


# ---------------------------------------------------------------------------
# Control mode (manual / auto).
# ---------------------------------------------------------------------------
def test_set_control_mode_validates():
    m = StateMachine(_zones(), initial_zone="sleeping", tick_hz=20.0)
    assert m.control_mode == "auto"  # default
    m.set_control_mode("manual")
    assert m.control_mode == "manual"
    m.set_control_mode("auto")
    assert m.control_mode == "auto"
    # Invalid mode is rejected and the mode is unchanged.
    m.set_control_mode("nope")
    assert m.control_mode == "auto"


def test_manual_mode_blocks_auto_advance():
    """In manual mode, a single-run action does not advance to the next action."""
    data = validate_state_data({"zones": {
        "a": {"startpos": [0] * 6, "exitpos": [0] * 6,
             "safezone": [[0, 10]] * 6, "exits": [], "speed": 1,
             "actions": {
                 "go": {"poses": [[1] * 6, [2] * 6],
                        "loop": False, "next": [{"zone": "a", "action": "done"}]},
                 "done": {"poses": [[5] * 6]},
             }},
        "b": {"startpos": [2] * 6, "exitpos": [0] * 6,
             "safezone": [[0, 10]] * 6, "exits": [], "speed": 1},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.set_control_mode("manual")
    m.play_action("go")
    m.cadence_every = 1
    m.step([0] * 6)          # index 0 -> 1
    m.step([0] * 6)          # index 1 -> 2 >= len(2); loop False -> advance
    # In manual mode the action is cleared (no advance to "done").
    assert m.current_action is None
    assert m.action_index == 0


def test_auto_mode_still_advances():
    """In auto mode (the default), a single-run action advances to the next action."""
    data = validate_state_data({"zones": {
        "a": {"startpos": [0] * 6, "exitpos": [0] * 6,
             "safezone": [[0, 10]] * 6, "exits": [], "speed": 1,
             "actions": {
                 "go": {"poses": [[1] * 6, [2] * 6],
                        "loop": False, "next": [{"zone": "a", "action": "done"}]},
                 "done": {"poses": [[5] * 6]},
             }},
        "b": {"startpos": [2] * 6, "exitpos": [0] * 6,
             "safezone": [[0, 10]] * 6, "exits": [], "speed": 1},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    # Default is auto mode.
    m.play_action("go")
    m.cadence_every = 1
    m.step([0] * 6)          # index 0 -> 1
    m.step([0] * 6)          # index 1 -> 2 >= len(2); loop False -> advance
    # In auto mode the action advances to "done".
    assert m.current_action == "done"
    assert m.action_index == 0


def test_manual_mode_allows_explicit_commands():
    """In manual mode, explicit commands (trigger_action, play_action) still work."""
    data = validate_state_data({"zones": {
        "a": {"startpos": [0] * 6, "exitpos": [0] * 6,
             "safezone": [[0, 10]] * 6, "exits": [], "speed": 1,
             "actions": {"x": {"poses": [[1] * 6]}}},
        "b": {"startpos": [2] * 6, "exitpos": [0] * 6,
             "safezone": [[0, 10]] * 6, "exits": [], "speed": 1,
             "actions": {"y": {"poses": [[2] * 6]}}},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.set_control_mode("manual")
    # trigger_action still works in manual mode.
    assert m.trigger_action("b", "y") is True
    assert m.transitioning
    # Commit the transition (simulating arrival).
    m._commit({"zone": "b", "action": "y", "pose": [2] * 6, "resume_index": 0})
    assert m.current_zone == "b"
    assert m.current_action == "y"
    # play_action still works in manual mode (same zone).
    assert m.play_action("x") is False  # "x" is in zone "a", not "b"
    m.clear_action()
    assert m.current_action is None


# ---------------------------------------------------------------------------
# Multi-pose wiggle: the anchor advances on cadence while the variable axis
# wiggles on top of the current anchor.
# ---------------------------------------------------------------------------
def test_multi_pose_wiggle_advances_anchor_and_wiggles_var_axis():
    """A two-anchor action with a variable axis: the non-var axis rests at the
    current anchor (snapping A -> B when the cadence advances the index), while the
    var axis wiggles every tick on top of the current anchor.
    """
    a_pose = [0] * 6
    b_pose = [5, 0, 0, 0, 0, 0]
    data = validate_state_data({"zones": {
        "a": {"startpos": [0] * 6, "exitpos": [0] * 6,
             "safezone": [[-100, 100]] * 6, "exits": [], "speed": 1,
             "actions": {"wig": {"poses": [a_pose, b_pose],
                                  "variable_axes": [4],
                                  "behavior": "restless"}}},
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.play_action("wig")
    m.cadence_every = 3
    targets = [m.step([0] * 6) for _ in range(6)]
    # Non-variable axis (A0) rests at the current anchor: A for ticks 1-3 (index 0),
    # B for ticks 4-6 (index 1). Read-then-advance emits the pre-advance anchor, so
    # the index advances at tick 3 (cadence_every=3) and B is first emitted at tick 4.
    assert all(t[0] == 0 for t in targets[:3]), f"A0 should rest at anchor A (0): {targets[:3]}"
    assert all(t[0] == 5 for t in targets[3:]), f"A0 should snap to anchor B (5): {targets[3:]}"
    # The variable axis (A4) wiggles every tick on top of the anchor: it takes multiple
    # distinct values across the run (restless is a continuous random drift).
    var_values = [t[4] for t in targets]
    assert len(set(var_values)) >= 2, f"A4 should wiggle to distinct values: {var_values}"


# ---------------------------------------------------------------------------
# Midi cadence: the operator advances the current action's pose on the learned
# midi note (machine.midi_pulse), consumed once per tick by _cadence_due.
# ---------------------------------------------------------------------------
def test_midi_cadence_advances_on_pulse_only():
    """In "midi" cadence the anchor advances only when a pulse is armed; the pulse
    is consumed on the next step. No pulse -> no advance."""
    data = validate_state_data({"zones": {
        "a": {"safezone": [[0, 10]] * 6, "speed": 1,
              "actions": {"spin": {"poses": [[1] * 6, [2] * 6, [3] * 6]}}}}})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.play_action("spin")
    m.cadence = "midi"
    m.step([0] * 6)
    assert m.action_index == 0  # no pulse -> no advance
    m.midi_pulse()
    m.step([0] * 6)
    assert m.action_index == 1  # pulse consumed, advanced once
    assert m._midi_pulse is False
    m.step([0] * 6)
    assert m.action_index == 1  # no new pulse -> holds


def test_midi_cadence_spontaneous_advances_on_pulse():
    """Regression: a spontaneous action also advances on the midi pulse. The pulse is
    consumed inside _cadence_due, which step() must call exactly once per tick (the
    old double-call swallowed the pulse for spontaneous actions)."""
    data = validate_state_data({"zones": {
        "a": {"safezone": [[0, 10]] * 6, "speed": 1,
              "actions": {"spin": {"poses": [[1] * 6, [2] * 6, [3] * 6],
                                  "behavior": "spontaneous"}}}}})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    assert z.get("a").action_behavior("spin") == "spontaneous"
    m.play_action("spin")
    m.cadence = "midi"
    m.midi_pulse()
    m.step([0] * 6)
    assert m.action_index == 1  # pulse consumed AND anchor advanced on the same tick
    assert m._midi_pulse is False


def test_midi_pulse_is_noop_outside_midi_cadence():
    """midi_pulse only arms in "midi" cadence; in any other mode it is a no-op."""
    data = validate_state_data({"zones": {
        "a": {"safezone": [[0, 10]] * 6, "speed": 1,
              "actions": {"spin": {"poses": [[1] * 6, [2] * 6]}}}}})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.play_action("spin")
    for mode in ("arrival", "fixed"):
        m.cadence = mode
        m.midi_pulse()
        assert m._midi_pulse is False, f"pulse must be a no-op in {mode!r} cadence"


def test_action_lifecycle_clears_midi_pulse():
    """A stale midi pulse is cleared at every action lifecycle boundary so it never
    carries over into a new action."""
    data = validate_state_data({"zones": {
        "a": {"safezone": [[0, 10]] * 6, "speed": 1,
              "actions": {"spin": {"poses": [[1] * 6, [2] * 6, [3] * 6]}}}}})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    m.play_action("spin")
    m.cadence = "midi"
    m.midi_pulse()
    assert m._midi_pulse is True
    m.clear_action()
    assert m._midi_pulse is False
    m.play_action("spin")
    m.midi_pulse()
    assert m._midi_pulse is True
    m.play_action("spin")  # re-playing clears the pulse
    assert m._midi_pulse is False


def main():
    tests = [
        test_play_and_clear_action,
        test_transition_step_is_hardware_clamped,
        test_hold_keeps_target,
        test_loop_action_wraps_around_and_keeps_playing,
        test_single_run_advances_to_next_action_in_zone,
        test_single_run_with_no_target_stops_the_action,
        test_single_run_exit_triggers_transition_and_entry,
        test_set_control_mode_validates,
        test_manual_mode_blocks_auto_advance,
        test_auto_mode_still_advances,
        test_manual_mode_allows_explicit_commands,
        test_multi_pose_wiggle_advances_anchor_and_wiggles_var_axis,
        test_midi_cadence_advances_on_pulse_only,
        test_midi_cadence_spontaneous_advances_on_pulse,
        test_midi_pulse_is_noop_outside_midi_cadence,
        test_action_lifecycle_clears_midi_pulse,
    ]
    # The old zone-mode tests (m.mode / set_mode / request_zone / _commit_zone, zone
    # modes wander/random/track/hold, per-edge exitposes, default-mode adoption) test
    # concepts removed in the multi-pose refactor; they are marked as pytest skips and
    # are not driven by this runner.
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
