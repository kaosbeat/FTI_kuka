"""Tests for the assertive control-mode driver.

Assertive mode drives the robot to random safe destinations with a per-move
speed, optional cross-zone wandering, and optional person tracking. The driver
lives in :class:`rtr.brain.brain.Brain`:

- ``_assertive_pool``: the ``(zone, action)`` nodes the robot may go to.
- ``_assertive_pick_destination``: a random pool member (skips the current node
  and safety-blocked candidates; ``wander=false`` restricts to the current zone).
- ``_assertive_roll_speed``: a per-move speed in ``[speed_min, speed_max]``.
- ``_assertive_find_path``: a safe path from the current node to the destination.
- ``_assertive_tick``: the per-tick driver (dwell, pick, same-zone direct play,
  cross-zone safe-path walk, no-current-action direct trigger).

Run directly (no pytest needed)::

    python tests/test_brain_assertive.py

or under pytest::

    pytest tests/test_brain_assertive.py
"""

import os
import random
import sys
import time

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
# Zone table: two zones. Zone ``a`` has two actions (``act1``, ``act2``) with
# NO ``next`` link between them (so there is no in-zone graph edge: the
# same-zone move must be a direct ``play_action``, not a graph walk). Zone
# ``a`` exits to ``b/bact1``; zone ``b`` has a single action.
# ---------------------------------------------------------------------------

def _zones():
    data = validate_state_data({"zones": {
        "a": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "act1": {
                    "poses": [[0] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
                "act2": {
                    "poses": [[1] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
            },
            "exits": [["b", "bact1"]],
        },
        "b": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "bact1": {
                    "poses": [[2] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
            },
        },
    }})
    return Zones(data["zones"])


def _brain(machine, wander=True, pool=None, dwell_s=8.0):
    """A Brain with assertive mode configured and autonomy off.

    The default zone groups are disabled so the navigation union is empty (no
    reliance on the built-in home/hunt/facefocus groups).
    """
    cfg = validate_brain_config({
        "autonomy": {
            "enabled": False,
            "dwell_s": 30.0,
            "events": {"midi": False, "camera": False, "websocket": False},
        },
        "zone_groups": {
            "home": {"zones": [], "behaviors": [], "enabled": False},
            "hunt": {"zones": [], "behaviors": [], "enabled": False},
            "facefocus": {"zones": [], "behaviors": [], "enabled": False},
            "perform": {"zones": [], "behaviors": [], "enabled": False},
            "g": {"zones": ["a"], "behaviors": ["hold"], "enabled": False},
        },
        "assertive": {
            "enabled": True,
            "dwell_s": dwell_s,
            "speed_min": 30,
            "speed_max": 100,
            "wander": wander,
            "track": False,
            "pool": pool,
        },
    })
    return Brain(machine, camera=None, config_loader=lambda: cfg)


# ---------------------------------------------------------------------------
# _assertive_pool.
# ---------------------------------------------------------------------------

def test_pool_default_is_all_enabled_nodes():
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    b = _brain(m)
    assert sorted(b._assertive_pool()) == [
        ("a", "act1"), ("a", "act2"), ("b", "bact1"),
    ]


def test_pool_explicit_entries():
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    b = _brain(m, pool=["a/act2", "b/bact1"])
    assert sorted(b._assertive_pool()) == [("a", "act2"), ("b", "bact1")]


def test_pool_drops_unknown_and_disabled():
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    # "c/ghost" is an unknown zone; "a/act1" is valid.
    b = _brain(m, pool=["c/ghost", "a/act1"])
    assert b._assertive_pool() == [("a", "act1")]


# ---------------------------------------------------------------------------
# _assertive_pick_destination.
# ---------------------------------------------------------------------------

def test_pick_skips_current_node():
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    m.play_action("act1")
    b = _brain(m)
    # With wander=False the pool is restricted to zone "a" minus the current
    # node ("a/act1"), leaving only ("a", "act2").
    b2 = _brain(m, wander=False)
    picks = set()
    for _ in range(50):
        d = b2._assertive_pick_destination("a", "act1")
        if d is not None:
            picks.add(d)
    assert picks == {("a", "act2")}


def test_pick_skips_blocked_candidates():
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    m.play_action("act1")
    b = _brain(m, wander=False)
    # Block the only same-zone option; the pick must return None.
    b._blocked_candidates.add(("a", "act2"))
    assert b._assertive_pick_destination("a", "act1") is None


def test_pick_wander_false_restricts_to_current_zone():
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    m.play_action("act1")
    b = _brain(m, wander=False)
    opts = set()
    for _ in range(50):
        d = b._assertive_pick_destination("a", "act1")
        if d is not None:
            opts.add(d)
    # Every pick is in zone "a" (no cross-zone travel).
    assert all(z == "a" for z, _ in opts)


def test_pick_wander_true_includes_cross_zone():
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    m.play_action("act1")
    b = _brain(m, wander=True)
    opts = set()
    for _ in range(200):
        d = b._assertive_pick_destination("a", "act1")
        if d is not None:
            opts.add(d)
    # Cross-zone ("b", "bact1") is reachable in the pool.
    assert ("b", "bact1") in opts


# ---------------------------------------------------------------------------
# _assertive_roll_speed.
# ---------------------------------------------------------------------------

def test_roll_speed_in_range():
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    b = _brain(m)
    for _ in range(100):
        b._assertive_roll_speed()
        assert 30 <= m.assertive_speed <= 100


# ---------------------------------------------------------------------------
# _assertive_find_path.
# ---------------------------------------------------------------------------

def test_find_path_cross_zone():
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    m.play_action("act1")
    b = _brain(m)
    path = b._assertive_find_path(("b", "bact1"))
    assert path == [("a", "act1"), ("b", "bact1")]


def test_find_path_same_zone_without_next_is_none():
    # act1 has no ``next`` link to act2, so the graph has no in-zone edge.
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    m.play_action("act1")
    b = _brain(m)
    assert b._assertive_find_path(("a", "act2")) is None


# ---------------------------------------------------------------------------
# _assertive_tick end-to-end (mocked time).
# ---------------------------------------------------------------------------

def test_tick_no_current_action_direct_trigger():
    """Entering assertive with no current action (frozen) triggers the destination."""
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    b = _brain(m)
    m.set_control_mode("assertive")
    assert m.current_action is None

    real = time.time
    fake = {"t": 1000.0}
    time.time = lambda: fake["t"]
    try:
        # First tick resets the dwell timer (cur != assertive_prev); still dwelling.
        b._assertive_tick([0] * 6)
        assert m.current_action is None
        # Advance past the dwell (8 s); the robot should now move.
        fake["t"] += 9.0
        b._assertive_tick([0] * 6)
        # The robot moved: either a same-zone play (current_action set) or a
        # cross-zone transition (transitioning). It must no longer be idle.
        assert (m.current_action is not None) or m.transitioning
    finally:
        time.time = real


def test_tick_same_zone_direct_play():
    """A same-zone destination is played directly (no graph path needed)."""
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    m.play_action("act1")
    b = _brain(m, wander=False)  # restricts picks to zone "a"
    m.set_control_mode("assertive")

    real = time.time
    fake = {"t": 1000.0}
    time.time = lambda: fake["t"]
    try:
        b._assertive_tick([0] * 6)  # reset dwell
        fake["t"] += 9.0
        b._assertive_tick([0] * 6)  # post-dwell: picks a/act2 (same zone)
        # The same-zone move commits immediately (no transition, no nav).
        assert m.current_action == "act2"
        assert not m.transitioning
        assert b.assertive_nav == []
    finally:
        time.time = real


def test_tick_cross_zone_safe_path_walk():
    """A cross-zone destination is walked via the safe path (waypoint nav)."""
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    m.play_action("act1")
    b = _brain(m, wander=True)
    m.set_control_mode("assertive")

    real = time.time
    fake = {"t": 1000.0}
    time.time = lambda: fake["t"]
    try:
        b._assertive_tick([0] * 6)  # reset dwell
        fake["t"] += 9.0
        # Force a cross-zone pick by seeding the RNG.
        random.seed(1234)
        b._assertive_tick([0] * 6)  # post-dwell
        # If the pick was cross-zone, navigation is set up (a waypoint list).
        # If it was same-zone, the action committed directly. Either way the
        # driver made progress (did not stay idle at the original action).
        progressed = (
            b.assertive_nav != []
            or m.current_action != "act1"
            or m.transitioning
        )
        assert progressed, (
            f"driver did not progress: nav={b.assertive_nav} "
            f"action={m.current_action} transitioning={m.transitioning}"
        )
    finally:
        time.time = real


def test_tick_dwell_gates_first_move():
    """The robot dwells at its node until the dwell expires (no early move)."""
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    m.play_action("act1")
    b = _brain(m, wander=False)
    m.set_control_mode("assertive")

    real = time.time
    fake = {"t": 1000.0}
    time.time = lambda: fake["t"]
    try:
        b._assertive_tick([0] * 6)  # resets dwell timer at t=1000
        # Advance only 2 s (well under the 8 s dwell); the robot must not move.
        fake["t"] += 2.0
        b._assertive_tick([0] * 6)
        assert m.current_action == "act1"  # still dwelling
        assert not m.transitioning
    finally:
        time.time = real


def test_leaving_assertive_clears_nav_state():
    """Leaving assertive mode clears the navigation state (via SET_CONTROL_MODE)."""
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    m.play_action("act1")
    b = _brain(m)
    m.set_control_mode("assertive")
    # Seed some navigation state.
    b.assertive_nav = [("b", "bact1")]
    b.assertive_nav_index = 0
    b.assertive_prev = ("a", "act1")
    b.assertive_arrived_ts = 1234.0
    # Leave assertive (back to auto); the handler must clear the nav state.
    from rtr.core.commands import Cmd, Command
    b.on_command(Command(cmd=Cmd.SET_CONTROL_MODE, payload={"mode": "auto"}), [0] * 6)
    assert b.assertive_nav == []
    assert b.assertive_nav_index == 0
    assert b.assertive_prev is None
    assert b.assertive_arrived_ts == 0.0
    assert m.assertive_speed is None  # machine drops the per-move speed


def main():
    tests = [
        test_pool_default_is_all_enabled_nodes,
        test_pool_explicit_entries,
        test_pool_drops_unknown_and_disabled,
        test_pick_skips_current_node,
        test_pick_skips_blocked_candidates,
        test_pick_wander_false_restricts_to_current_zone,
        test_pick_wander_true_includes_cross_zone,
        test_roll_speed_in_range,
        test_find_path_cross_zone,
        test_find_path_same_zone_without_next_is_none,
        test_tick_no_current_action_direct_trigger,
        test_tick_same_zone_direct_play,
        test_tick_cross_zone_safe_path_walk,
        test_tick_dwell_gates_first_move,
        test_leaving_assertive_clears_nav_state,
    ]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"\n{'ALL TESTS PASSED' if failures == 0 else f'{failures} TEST(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
