"""Tests for the per-action ``dwell_s`` (the autonomy advance delay).

Task 4 of the autonomous-mode zone-groups plan: the ``Zone.action_dwell_s``
accessor and the brain's ``_autonomy_dwell_s`` read path (a per-action override
with a global ``autonomy.dwell_s`` fallback). A looping action's ``next`` link is
followed once it has dwelt for its dwell time — the per-action value wins when
present, otherwise the global default applies.

Run directly (no pytest needed)::

    python tests/test_brain_autonomy_dwell.py

or under pytest::

    pytest tests/test_brain_autonomy_dwell.py
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

from rtr.state.zones import Zones, validate_state_data
from rtr.state.machine import StateMachine
from rtr.brain.brain import Brain
from rtr.brain.config import validate_brain_config


# ---------------------------------------------------------------------------
# Zone table: a single zone with looping actions. ``loop1`` carries a per-action
# ``dwell_s`` and a same-zone ``next`` link (so the advance is an in-zone
# ``play_action``, not a cross-zone transition). ``loop2`` is the target and has
# no ``dwell_s`` (the global fallback case).
# ---------------------------------------------------------------------------

def _zones():
    data = validate_state_data({"zones": {
        "a": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "loop1": {
                    "poses": [[0] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                    "dwell_s": 0.5,
                    "next": {"zone": "a", "action": "loop2"},
                },
                "loop2": {
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


def _brain(machine, dwell_global=30.0):
    """A Brain with autonomy on, a large global dwell, and only zone ``a`` enabled.

    The default zone groups are disabled so the navigation union is exactly
    ``{"a"}`` (no reliance on the built-in home/hunt/facefocus groups).
    """
    cfg = validate_brain_config({
        "autonomy": {
            "enabled": True,
            "dwell_s": dwell_global,
            "events": {"midi": False, "camera": False, "websocket": False},
        },
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
# Zone.action_dwell_s accessor.
# ---------------------------------------------------------------------------

def test_action_dwell_s_returns_value():
    z = _zones().get("a")
    assert z.action_dwell_s("loop1") == 0.5


def test_action_dwell_s_returns_none_when_absent():
    z = _zones().get("a")
    assert z.action_dwell_s("loop2") is None


def test_action_dwell_s_returns_none_when_malformed():
    data = validate_state_data({"zones": {
        "a": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "bad": {
                    "poses": [[0] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                    "dwell_s": "soon",  # not a number -> the accessor ignores it
                },
            },
        },
    }})
    z = Zones(data["zones"]).get("a")
    assert z.action_dwell_s("bad") is None


# ---------------------------------------------------------------------------
# Brain._autonomy_dwell_s read path (per-action override vs global fallback).
# ---------------------------------------------------------------------------

def test_autonomy_dwell_s_uses_per_action_override():
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    b = _brain(m)
    # "loop1" has dwell_s 0.5; the global is 30.0. The per-action override wins.
    assert b._autonomy_dwell_s("a", "loop1") == 0.5


def test_autonomy_dwell_s_falls_back_to_global():
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    b = _brain(m)
    # "loop2" has no dwell_s; fall back to the global 30.0.
    assert b._autonomy_dwell_s("a", "loop2") == 30.0


# ---------------------------------------------------------------------------
# The per-action dwell actually gates the advance (mocked time).
# ---------------------------------------------------------------------------

def test_dwell_fires_with_per_action_override():
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    m.play_action("loop1")
    b = _brain(m)  # global dwell 30.0, per-action loop1 dwell 0.5

    real_time = time.time
    fake = {"t": 1000.0}
    time.time = lambda: fake["t"]
    try:
        # First tick: resets the dwell timer at t=1000.0; no advance yet.
        b._autonomy_tick([0] * 6)
        assert m.current_action == "loop1"
        # Advance past the per-action dwell (0.5 s) — still far under the global 30 s.
        fake["t"] += 0.5
        b._autonomy_tick([0] * 6)
        # The advance fired because the per-action dwell (0.5 s) expired, not the
        # global (30 s). Had the brain used the global, no advance would have fired.
        assert m.current_action == "loop2"
    finally:
        time.time = real_time


def main():
    tests = [
        test_action_dwell_s_returns_value,
        test_action_dwell_s_returns_none_when_absent,
        test_action_dwell_s_returns_none_when_malformed,
        test_autonomy_dwell_s_uses_per_action_override,
        test_autonomy_dwell_s_falls_back_to_global,
        test_dwell_fires_with_per_action_override,
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
