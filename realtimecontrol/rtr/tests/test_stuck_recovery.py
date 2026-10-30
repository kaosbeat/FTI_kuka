"""Tests for the auto-mode ``(zone, None)`` stuck-state recovery.

The stuck state: ``current_zone`` set, ``current_action`` None, no pending move, no
transition. It arises three ways in auto mode:

- **A** — a non-looping action's hand-off is discarded by the safety gate (the
  action is cleared, the pending move is dropped, the candidate is marked blocked).
- **B** — a data dead-end: the action's ``next``/``exits`` are all invalid/disabled
  and the zone is not a single-action zone (no auto-return).
- **C** — a zone reload removes/renames the current action.

Three layers of recovery are tested here:

1. **Re-arm** (primary): the brain plays a valid action in the current zone so the
   robot is never left holding.
2. **Watchdog** (backstop): when re-arm keeps failing (all candidates blocked), the
   brain clears the blocked-candidate set after a threshold so a target can be
   retried.
3. **Validation** (prevention): ``validate_state_data`` rejects a non-looping
   action with no valid next in a multi-action zone with no valid exit.

Run directly (no pytest needed)::

    python tests/test_stuck_recovery.py

or under pytest::

    pytest tests/test_stuck_recovery.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ``rtr.core`` must load before ``rtr.brain.brain`` (a pre-existing circular
# import: rtr.core.__init__ -> rtr.core.engine -> rtr.brain.brain). Loading the
# package first resolves the cycle, matching how main.py boots the core.
import rtr.core  # noqa: F401

from rtr.state.zones import Zones, validate_state_data
from rtr.state.machine import StateMachine
from rtr.brain.brain import Brain
from rtr.brain.config import validate_brain_config


# ---------------------------------------------------------------------------
# Zone table: a single zone "a" with a looping action ("loop") and a non-looping
# action ("go"). The looping action is what the re-arm prefers.
# ---------------------------------------------------------------------------

def _zones_data():
    """A zone "a" with a looping action ("loop") and a non-looping action ("go").

    "go" has a valid next pointing to "loop" so the zone passes the dead-end
    validation (a non-looping action with no valid next in a multi-action zone
    would be rejected). The re-arm tests simulate the stuck state manually, so the
    next link is not exercised here.
    """
    return {"zones": {
        "a": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "loop": {
                    "poses": [[0] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
                "go": {
                    "poses": [[1] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": False,
                    "next": {"zone": "a", "action": "loop"},
                },
            },
        },
    }}


def _brain(machine, stuck_threshold=None):
    """A Brain with autonomy on and only zone ``a`` enabled.

    The default zone groups are disabled so the navigation union is exactly
    ``{"a"}`` (no reliance on the built-in home/hunt/facefocus groups).
    """
    autonomy = {"enabled": True,
                "dwell_s": 30.0,
                "events": {"midi": False, "camera": False, "websocket": False}}
    if stuck_threshold is not None:
        autonomy["stuck_threshold"] = stuck_threshold
    cfg = validate_brain_config({
        "autonomy": autonomy,
        "zone_groups": {
            "home": {"zones": [], "behaviors": [], "enabled": False},
            "hunt": {"zones": [], "behaviors": [], "enabled": False},
            "facefocus": {"zones": [], "behaviors": [], "enabled": False},
            "perform": {"zones": [], "behaviors": [], "enabled": False},
            "g": {"zones": ["a"], "behaviors": ["hold"], "enabled": True},
        },
    })
    return Brain(machine, camera=None, config_loader=lambda: cfg)


# ---------------------------------------------------------------------------
# Re-arm (primary recovery).
# ---------------------------------------------------------------------------

def test_rearm_plays_a_looping_action_when_stuck():
    """A robot stuck at (zone, None) is re-armed with a looping action.

    The robot is in zone "a" with no active action (the stuck state). The brain's
    autonomy tick must play a valid action in the zone — preferring the looping
    action — so the robot is never left holding.
    """
    z = Zones(validate_state_data(_zones_data())["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    b = _brain(m)
    # Simulate the stuck state: a zone is set but no action is active.
    m.current_action = None
    assert m.current_zone == "a" and m.current_action is None
    b._autonomy_tick([0] * 6)
    # The brain re-armed the robot with the looping action.
    assert m.current_action == "loop", (
        f"expected the re-arm to play the looping action, got "
        f"{m.current_action!r}")
    # The stuck counter is reset after a successful re-arm.
    assert b._stuck_ticks == 0


def test_rearm_noop_when_zone_has_no_enabled_action():
    """A re-arm in a zone with no enabled action is a no-op (the robot stays stuck).

    The watchdog counter increments so the backstop can act later.
    """
    data = validate_state_data({"zones": {
        "a": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "loop": {
                    "poses": [[0] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                    "enabled": False,
                },
            },
        },
    }})
    z = Zones(data["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    b = _brain(m)
    m.current_action = None
    b._autonomy_tick([0] * 6)
    # No enabled action to play: the robot stays stuck (no action committed).
    assert m.current_action is None
    # The stuck counter incremented (the re-arm failed).
    assert b._stuck_ticks == 1


def test_rearm_skips_blocked_candidates():
    """A re-arm skips candidates blocked by the safety gate.

    When the only looping action is blocked, the re-arm falls back to the first
    enabled, non-blocked action (the non-looping one).
    """
    z = Zones(validate_state_data(_zones_data())["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    b = _brain(m)
    m.current_action = None
    # Block the looping action (the re-arm must skip it).
    b.mark_candidate_blocked("a", "loop")
    b._autonomy_tick([0] * 6)
    # The re-arm skipped the blocked looping action and played the non-looping one.
    assert m.current_action == "go", (
        f"expected the re-arm to fall back to the non-looping action, got "
        f"{m.current_action!r}")


# ---------------------------------------------------------------------------
# Watchdog (backstop).
# ---------------------------------------------------------------------------

def test_watchdog_clears_blocked_candidates_after_threshold():
    """When re-arm keeps failing (all candidates blocked), the watchdog clears the
    blocked-candidate set after the threshold so a target can be retried.
    """
    z = Zones(validate_state_data(_zones_data())["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    b = _brain(m, stuck_threshold=3)
    m.current_action = None
    # Block every candidate in the zone so the re-arm always fails.
    b.mark_candidate_blocked("a", "loop")
    b.mark_candidate_blocked("a", "go")
    # Ticks 1..3: the re-arm fails, the stuck counter increments.
    for i in range(3):
        b._autonomy_tick([0] * 6)
        assert m.current_action is None, f"tick {i}: expected still stuck"
    # After the threshold (3 ticks), the watchdog cleared the blocked set.
    assert b._stuck_ticks == 0, "the watchdog should have reset the stuck counter"
    assert b._blocked_candidates == set(), (
        "the watchdog should have cleared the blocked candidates")
    # On the next tick the re-arm retries (the blocked set is empty) and succeeds.
    b._autonomy_tick([0] * 6)
    assert m.current_action == "loop", (
        f"expected the re-arm to retry after the watchdog cleared the set, got "
        f"{m.current_action!r}")


def test_stuck_threshold_reads_config():
    """The stuck threshold reads the optional autonomy.stuck_threshold (default 10)."""
    z = Zones(validate_state_data(_zones_data())["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    b_default = _brain(m)
    assert b_default._stuck_threshold() == 10
    b_custom = _brain(m, stuck_threshold=5)
    assert b_custom._stuck_threshold() == 5


def test_stuck_ticks_reset_on_normal_path():
    """The stuck counter resets when the robot has a zone and an action (not stuck)."""
    z = Zones(validate_state_data(_zones_data())["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    b = _brain(m)
    # A normal (zone, action) state resets the stuck counter.
    m.current_action = "loop"
    b._stuck_ticks = 7  # simulate a prior stuck run
    b._autonomy_tick([0] * 6)
    assert b._stuck_ticks == 0


# ---------------------------------------------------------------------------
# Validation (prevention): dead-end data is rejected.
# ---------------------------------------------------------------------------

def test_validation_rejects_multi_action_dead_end():
    """A multi-action zone with a non-looping action, no valid next, and no valid
    exit is rejected (it would dead-end the robot)."""
    data = {"zones": {
        "a": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "go": {
                    "poses": [[0] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": False,
                    # No valid next.
                },
                "idle": {
                    "poses": [[1] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
            },
            # No exits.
        },
    }}
    try:
        validate_state_data(data)
        raised = False
    except ValueError:
        raised = True
    assert raised, "expected validate_state_data to reject the dead-end zone"


def test_validation_allows_single_action_zone():
    """A single-action zone with a non-looping action and no next/exits is NOT
    rejected (the runtime auto-return fallback handles it)."""
    data = {"zones": {
        "a": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "go": {
                    "poses": [[0] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": False,
                    # No next, no exits — but single-action zones are exempt.
                },
            },
        },
    }}
    # Must not raise.
    validate_state_data(data)


def test_validation_allows_non_looping_with_valid_next():
    """A multi-action zone with a non-looping action that HAS a valid next is NOT
    rejected (the robot can advance to the next action)."""
    data = {"zones": {
        "a": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "go": {
                    "poses": [[0] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": False,
                    "next": {"zone": "a", "action": "idle"},
                },
                "idle": {
                    "poses": [[1] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
            },
        },
    }}
    # Must not raise.
    validate_state_data(data)


def test_validation_allows_zone_with_valid_exit():
    """A multi-action zone with a non-looping action, no valid next, but a valid
    zone-level exit is NOT rejected (the robot can use the exit)."""
    data = {"zones": {
        "a": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "go": {
                    "poses": [[0] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": False,
                    # No valid next.
                },
                "idle": {
                    "poses": [[1] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
            },
            "exits": [["b", "enter"]],
        },
        "b": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "enter": {
                    "poses": [[2] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
            },
        },
    }}
    # Must not raise.
    validate_state_data(data)


def test_validation_rejects_exit_pointing_to_disabled_zone():
    """A zone exit that points to a disabled zone is not a valid exit (the dead-end
    is still rejected)."""
    data = {"zones": {
        "a": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "go": {
                    "poses": [[0] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": False,
                    # No valid next.
                },
                "idle": {
                    "poses": [[1] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
            },
            "exits": [["b", "enter"]],
        },
        "b": {
            "enabled": False,  # disabled zone -> the exit is not valid.
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "enter": {
                    "poses": [[2] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
            },
        },
    }}
    try:
        validate_state_data(data)
        raised = False
    except ValueError:
        raised = True
    assert raised, "expected validate_state_data to reject (exit points to disabled zone)"


# ---------------------------------------------------------------------------
# Trigger A: safety discard leaves the robot stuck; the re-arm recovers.
# ---------------------------------------------------------------------------

def test_trigger_a_safety_discard_then_rearm():
    """A non-looping action's hand-off is discarded by the safety gate (the robot is
    stuck); the brain's re-arm recovers by playing a valid action in the zone.

    The scenario: the robot is in zone "a" with the non-looping action "go"
    completing. Its hand-off to "loop" was stored as a pending move, but the safety
    gate discarded it (the candidate is marked blocked). The robot is now stuck at
    (a, None). The brain's re-arm must play a valid action (the looping one, since
    the blocked one is skipped).
    """
    z = Zones(validate_state_data(_zones_data())["zones"])
    m = StateMachine(z, initial_zone="a", tick_hz=20.0)
    b = _brain(m)
    # Simulate the stuck state after a safety discard: the action is cleared, the
    # pending move is dropped, the candidate is marked blocked.
    m.current_action = None
    b.mark_candidate_blocked("a", "go")
    b._autonomy_tick([0] * 6)
    # The re-arm skipped the blocked "go" and played the looping "loop".
    assert m.current_action == "loop", (
        f"expected the re-arm to play the looping action after the safety discard, "
        f"got {m.current_action!r}")


def main():
    tests = [
        test_rearm_plays_a_looping_action_when_stuck,
        test_rearm_noop_when_zone_has_no_enabled_action,
        test_rearm_skips_blocked_candidates,
        test_watchdog_clears_blocked_candidates_after_threshold,
        test_stuck_threshold_reads_config,
        test_stuck_ticks_reset_on_normal_path,
        test_validation_rejects_multi_action_dead_end,
        test_validation_allows_single_action_zone,
        test_validation_allows_non_looping_with_valid_next,
        test_validation_allows_zone_with_valid_exit,
        test_validation_rejects_exit_pointing_to_disabled_zone,
        test_trigger_a_safety_discard_then_rearm,
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
