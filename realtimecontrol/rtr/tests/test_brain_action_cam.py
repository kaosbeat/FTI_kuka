"""Tests for the action-level ``cam_control`` (camera intent per ``zone/action``).

The camera baseline moved from zone-level to action-level: the ``cam_control``
map is keyed by ``"zone/action"`` and the brain applies it on every **action**
change (not just a zone change). This lets two actions in the same zone carry
different camera intents.

Run directly (no pytest needed)::

    python tests/test_brain_action_cam.py

or under pytest::

    pytest tests/test_brain_action_cam.py
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
# Zone table: one zone with two looping actions. ``loop1`` / ``loop2`` carry
# different camera intents so an action change is observable in the camera.
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


class MockCamera:
    """Records ``set_intent`` calls so the test can assert the applied intent."""

    def __init__(self):
        self.calls = []

    def set_intent(self, **kw):
        self.calls.append(kw)


def _brain(machine, cam_control):
    """A Brain with a ``cam_control`` keyed by ``"zone/action"`` and a mock camera."""
    cfg = validate_brain_config({
        "autonomy": {"enabled": False, "dwell_s": 30.0,
                     "events": {"midi": False, "camera": False, "websocket": False}},
        "zone_groups": {
            "home": {"zones": [], "behaviors": [], "enabled": False},
            "hunt": {"zones": [], "behaviors": [], "enabled": False},
            "facefocus": {"zones": [], "behaviors": [], "enabled": False},
            "perform": {"zones": [], "behaviors": [], "enabled": False},
            "g": {"zones": ["a"], "behaviors": [], "enabled": True},
        },
        "cam_control": cam_control,
    })
    cam = MockCamera()
    return Brain(machine, camera=cam, config_loader=lambda: cfg), cam


# ---------------------------------------------------------------------------
# The camera intent is keyed by "zone/action" and applied on action change.
# ---------------------------------------------------------------------------

def test_cam_applies_for_current_action():
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    b, cam = _brain(m, {"a/loop1": {"active": "close", "mode": "analyze", "lock": "auto"}})
    m.play_action("loop1")
    b._autonomy_tick([0] * 6)
    assert cam.calls, "camera intent should have been applied for the current action"
    assert cam.calls[-1]["active"] == "close"
    assert cam.calls[-1]["mode"] == "analyze"


def test_cam_changes_with_action_in_same_zone():
    """The core change: two actions in the same zone carry different intents.

    Previously the intent was applied on zone change only, so both actions would
    share one baseline. Now each action's entry is applied as the action changes.
    """
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    b, cam = _brain(m, {
        "a/loop1": {"active": "close", "mode": "analyze", "lock": "auto"},
        "a/loop2": {"active": "wide", "mode": "idle", "lock": "none"},
    })
    m.play_action("loop1")
    b._autonomy_tick([0] * 6)
    assert cam.calls[-1]["active"] == "close"

    # Same zone, different action -> the new action's intent is applied.
    m.play_action("loop2")
    b._autonomy_tick([0] * 6)
    assert cam.calls[-1]["active"] == "wide"
    assert cam.calls[-1]["mode"] == "idle"
    assert cam.calls[-1].get("clear_lock") is True  # lock "none" clears the lock


def test_cam_not_reapplied_when_action_unchanged():
    """No redundant intent push while the action stays put."""
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    b, cam = _brain(m, {"a/loop1": {"active": "close", "mode": "analyze", "lock": "auto"}})
    m.play_action("loop1")
    b._autonomy_tick([0] * 6)
    first = len(cam.calls)
    b._autonomy_tick([0] * 6)  # same (zone, action) -> no new intent
    assert len(cam.calls) == first


def test_cam_ignores_legacy_zone_key():
    """A zone-level key ("a") is no longer matched; only "zone/action" is read.

    Guards the migration: an old-format entry must not be picked up by the
    action-level lookup (it would need to be re-keyed to "a/<action>").
    """
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    b, cam = _brain(m, {"a": {"active": "close", "mode": "analyze", "lock": "auto"}})
    m.play_action("loop1")
    b._autonomy_tick([0] * 6)
    assert not cam.calls, "a legacy zone-keyed entry should not be applied"


def main():
    tests = [
        test_cam_applies_for_current_action,
        test_cam_changes_with_action_in_same_zone,
        test_cam_not_reapplied_when_action_unchanged,
        test_cam_ignores_legacy_zone_key,
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
