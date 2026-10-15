"""Test: the core pushes the resolved hydra patch in every state frame.

The core is the single source of truth for the tool-screen patch. This test asserts
that the ``Display`` adapter, given a ``patch_code`` resolver (the ``Patches``
adapter's ``code_for``), includes the resolved code in the state frame it sends, and
that the resolution follows the action > mode > zone > default precedence. It also
asserts that without a resolver the frame carries no patch (so a client falls back to
matching the table it fetched itself).

Run directly (no pytest needed)::

    python tests/test_patch_push.py

or under pytest::

    pytest tests/test_patch_push.py
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
    ``wander`` so the action lookup is scoped to it.
    """
    table = {
        "default": "osc(4, 0.1, 1.2).out()",
        "zones": {"wander": "ZONE_CODE"},
        "modes": {"action": "MODE_CODE"},
        "actions": {"wander": {"look": "ACTION_CODE"}},
    }
    return Patches(StateBus(), patches_loader=lambda: table)


def _snap(zone, mode, action):
    return Snapshot(zone=zone, mode=mode, action=action,
                    joint_pose=[0.0] * 6, cart_pose=[0.0] * 3, target_pose=[0.0] * 6,
                    speed=20.0, moving=False)


def _frame_for(zone, mode, action, patches):
    """Capture the state frame the Display sends for a given snapshot."""
    sent = []
    bus = StateBus()
    Display(bus, send=lambda frame: sent.append(frame),
            enabled=True, patch_code=patches.code_for)
    bus.publish(Event.SNAPSHOT, _snap(zone, mode, action))
    return sent[0]


def test_state_frame_carries_resolved_patch():
    """The state frame includes the resolved patch code (the core's single source of truth)."""
    p = _patches()
    frame = _frame_for("wander", "action", "look", p)
    assert frame["type"] == "state"
    assert frame["patch"] == "ACTION_CODE"  # the action entry wins


def test_patch_precedence_action_mode_zone_default():
    """Resolution follows action > mode > zone > default, one scope at a time."""
    p = _patches()
    assert _frame_for("wander", "action", "look", p)["patch"] == "ACTION_CODE"
    assert _frame_for("wander", "action", None, p)["patch"] == "MODE_CODE"
    assert _frame_for("wander", None, None, p)["patch"] == "ZONE_CODE"
    assert _frame_for("rest", None, None, p)["patch"] == "osc(4, 0.1, 1.2).out()"


def test_state_frame_without_resolver_has_no_patch():
    """Without a resolver (patch_code=None) the frame carries no patch (clients fall back)."""
    sent = []
    bus = StateBus()
    Display(bus, send=lambda f: sent.append(f), enabled=True, patch_code=None)
    bus.publish(Event.SNAPSHOT, _snap("wander", None, None))
    assert sent[0]["patch"] is None


def test_bad_resolver_degrades_to_no_patch():
    """A resolver that throws degrades to no patch (the frame is still sent)."""
    sent = []
    bus = StateBus()
    Display(bus, send=lambda f: sent.append(f), enabled=True,
            patch_code=lambda *a: 1 / 0)
    bus.publish(Event.SNAPSHOT, _snap("wander", None, None))
    assert sent[0]["type"] == "state"
    assert sent[0]["patch"] is None


def main():
    failures = 0
    for name, fn in [
        ("test_state_frame_carries_resolved_patch", test_state_frame_carries_resolved_patch),
        ("test_patch_precedence_action_mode_zone_default", test_patch_precedence_action_mode_zone_default),
        ("test_state_frame_without_resolver_has_no_patch", test_state_frame_without_resolver_has_no_patch),
        ("test_bad_resolver_degrades_to_no_patch", test_bad_resolver_degrades_to_no_patch),
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
