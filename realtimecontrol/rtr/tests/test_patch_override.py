"""Test: a manual hydra screen override wins over the zone/action/behavior concatenation.

The operator can push a specific patch slot to the screens from the editor's
broadcast toggle. The core holds that choice as an in-memory override on the
``Patches`` adapter (it is not written to ``patches.json``). While the override is
set, ``code_for`` returns it (so the state frame the core pushes to the 3D tool
screen and the render page carries it); when it is cleared (``None`` / empty
string), ``code_for`` falls back to the normal zone + action/default + behavior
concatenation.

Run directly (no pytest needed)::

    python tests/test_patch_override.py

or under pytest::

    pytest tests/test_patch_override.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.core.bus import StateBus
from rtr.core.commands import Event, Snapshot
from rtr.display import Display
from rtr.patches import Patches


def _patches():
    """A Patches adapter backed by a small test table (one code per scope).

    The actions table is two-level (zone -> action -> code); the test zone is
    ``wander`` so the action lookup is scoped to it. The modes table holds
    behavior patches (keyed by behavior name).
    """
    table = {
        "default": "osc(4, 0.1, 1.2).out()",
        "zones": {"wander": "ZONE_CODE"},
        "modes": {"calm": "BEHAVIOR_CODE"},
        "actions": {"wander": {"look": "ACTION_CODE"}},
    }
    return Patches(StateBus(), patches_loader=lambda: table)


def _snap(zone, mode, action):
    return Snapshot(zone=zone, mode=mode, action=action,
                    joint_pose=[0.0] * 6, cart_pose=[0.0] * 3, target_pose=[0.0] * 6,
                    speed=20.0, moving=False)


class _MockMachine:
    """A duck-typed machine exposing the attributes Display._frame accesses."""
    def __init__(self, behavior):
        self.behavior = behavior
        self.current_action = None
        self.hunt_state = None
        self.pending = None
        self.forced_behavior = None
        self.behavior_params = {}


def _frame_for(zone, mode, action, patches, behavior=None):
    """Capture the state frame the Display sends for a given snapshot."""
    sent = []
    bus = StateBus()
    machine = _MockMachine(behavior) if behavior is not None else None
    Display(bus, send=lambda frame: sent.append(frame),
            enabled=True, patch_code=patches.code_for, machine=machine)
    bus.publish(Event.SNAPSHOT, _snap(zone, mode, action))
    return sent[0]


def test_override_wins_over_concatenation():
    """While set, code_for returns the override regardless of zone/mode/action."""
    p = _patches()
    p.set_screen_override("PUSHED_CODE")
    assert p.code_for("wander", "action", "look") == "PUSHED_CODE"
    assert p.code_for("wander", "action", "look", behavior="calm") == "PUSHED_CODE"
    assert p.code_for("wander", None, None) == "PUSHED_CODE"
    assert p.code_for("rest", None, None) == "PUSHED_CODE"


def test_override_cleared_falls_back_to_concatenation():
    """After clearing, code_for falls back to the zone + action/default + behavior concatenation."""
    p = _patches()
    p.set_screen_override("PUSHED_CODE")
    p.set_screen_override(None)
    # Full concatenation: zone + action + behavior
    assert p.code_for("wander", "action", "look", behavior="calm") == "ZONE_CODE\nACTION_CODE\nBEHAVIOR_CODE"
    # Zone + action (no behavior)
    assert p.code_for("wander", "action", "look") == "ZONE_CODE\nACTION_CODE"
    # Zone + default (no action)
    assert p.code_for("wander", None, None) == "ZONE_CODE\nosc(4, 0.1, 1.2).out()"
    # Default only (no zone, no action, no behavior)
    assert p.code_for("rest", None, None) == "osc(4, 0.1, 1.2).out()"


def test_empty_string_clears_override():
    """An empty string is treated as a clear (the client sends null/empty for auto)."""
    p = _patches()
    p.set_screen_override("PUSHED_CODE")
    p.set_screen_override("")
    assert p.code_for("wander", "action", "look") == "ZONE_CODE\nACTION_CODE"


def test_frame_carries_override_when_set():
    """The pushed state frame carries the override while it is set."""
    p = _patches()
    p.set_screen_override("PUSHED_CODE")
    frame = _frame_for("wander", "action", "look", p, behavior="calm")
    assert frame["type"] == "state"
    assert frame["patch"] == "PUSHED_CODE"


def test_frame_carries_concatenation_when_cleared():
    """The pushed state frame carries the concatenated patch once the override is cleared."""
    p = _patches()
    p.set_screen_override("PUSHED_CODE")
    p.set_screen_override(None)
    frame = _frame_for("wander", "action", "look", p, behavior="calm")
    assert frame["patch"] == "ZONE_CODE\nACTION_CODE\nBEHAVIOR_CODE"


def main():
    failures = 0
    for name, fn in [
        ("test_override_wins_over_concatenation", test_override_wins_over_concatenation),
        ("test_override_cleared_falls_back_to_concatenation", test_override_cleared_falls_back_to_concatenation),
        ("test_empty_string_clears_override", test_empty_string_clears_override),
        ("test_frame_carries_override_when_set", test_frame_carries_override_when_set),
        ("test_frame_carries_concatenation_when_cleared", test_frame_carries_concatenation_when_cleared),
    ]:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}: {exc}")
    print(f"\n{'ALL TESTS PASSED' if failures == 0 else f'{failures} TEST(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
