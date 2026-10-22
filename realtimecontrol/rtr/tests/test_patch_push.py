"""Test: the core pushes the resolved hydra patch in every state frame.

The core is the single source of truth for the tool-screen patch. This test asserts
that the ``Display`` adapter, given a ``patch_code`` resolver (the ``Patches``
adapter's ``code_for``), includes the resolved code in the state frame it sends, and
that the resolution concatenates zone + action/default + behavior in order. It also
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


def _snap(zone, mode, action, action_index=None):
    return Snapshot(zone=zone, mode=mode, action=action,
                    joint_pose=[0.0] * 6, cart_pose=[0.0] * 3, target_pose=[0.0] * 6,
                    speed=20.0, moving=False, action_index=action_index)


class _MockMachine:
    """A duck-typed machine exposing the attributes Display._frame accesses."""
    def __init__(self, behavior):
        self.behavior = behavior
        self.current_action = None
        self.hunt_state = None
        self.pending = None
        self.forced_behavior = None
        self.behavior_params = {}


def _frame_for(zone, mode, action, patches, behavior=None, action_index=None):
    """Capture the state frame the Display sends for a given snapshot."""
    sent = []
    bus = StateBus()
    machine = _MockMachine(behavior) if behavior is not None else None
    Display(bus, send=lambda frame: sent.append(frame),
            enabled=True, patch_code=patches.code_for, machine=machine)
    bus.publish(Event.SNAPSHOT, _snap(zone, mode, action, action_index))
    return sent[0]


def test_state_frame_carries_concatenated_patch():
    """The state frame includes the concatenated zone + action + behavior patch."""
    p = _patches()
    frame = _frame_for("wander", "action", "look", p, behavior="calm")
    assert frame["type"] == "state"
    expected = "ZONE_CODE\nACTION_CODE\nBEHAVIOR_CODE"
    assert frame["patch"] == expected


def test_concatenation_zone_action_only():
    """Without a behavior, the concatenation is zone + action/default."""
    p = _patches()
    frame = _frame_for("wander", "action", "look", p)
    expected = "ZONE_CODE\nACTION_CODE"
    assert frame["patch"] == expected


def test_concatenation_zone_default():
    """Without an action, the action slot falls back to default."""
    p = _patches()
    frame = _frame_for("wander", "action", None, p)
    expected = "ZONE_CODE\nosc(4, 0.1, 1.2).out()"
    assert frame["patch"] == expected


def test_concatenation_default_only():
    """Without a zone or action, only the default is used."""
    p = _patches()
    frame = _frame_for("rest", None, None, p)
    assert frame["patch"] == "osc(4, 0.1, 1.2).out()"


def test_concatenation_with_behavior_only():
    """A behavior patch is included when the zone and action are missing."""
    p = _patches()
    frame = _frame_for("rest", None, None, p, behavior="calm")
    expected = "osc(4, 0.1, 1.2).out()\nBEHAVIOR_CODE"
    assert frame["patch"] == expected


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
            patch_code=lambda *a, **kw: 1 / 0)
    bus.publish(Event.SNAPSHOT, _snap("wander", None, None))
    assert sent[0]["type"] == "state"
    assert sent[0]["patch"] is None


class _MockZones:
    """A duck-typed zones object exposing ``action_pose_imgs`` for the pose level."""
    def __init__(self, imgs):
        self._imgs = imgs

    def action_pose_imgs(self, zone, action):
        return self._imgs


def test_pose_injection_in_frame():
    """When the state frame is inside an action with per-pose images, the pose globals
    (pose + poseindex) are injected at the top of the action patch."""
    imgs = ["pose0.png", None, "pose2.png"]
    p = Patches(StateBus(), patches_loader=lambda: {
        "default": "osc(4, 0.1, 1.2).out()",
        "actions": {"wander": {"look": "s0.initImage(poseindex[pose])"}},
    }, poses_provider=lambda: _MockZones(imgs))
    sent = []
    bus = StateBus()
    machine = _MockMachine("calm")
    Display(bus, send=lambda f: sent.append(f), enabled=True, patch_code=p.code_for, machine=machine)
    snap = Snapshot(zone="wander", mode="action", action="look",
                    joint_pose=[0.0] * 6, cart_pose=[0.0] * 3, target_pose=[0.0] * 6,
                    speed=20.0, moving=False, action_index=1)
    bus.publish(Event.SNAPSHOT, snap)
    frame = sent[0]
    assert frame["type"] == "state"
    patch = frame["patch"]
    assert "var pose = 1" in patch
    assert "var poseindex = [\"/assets/imgs/pose0.png\", null, \"/assets/imgs/pose2.png\"]" in patch
    assert "s0.initImage(poseindex[pose])" in patch


def test_no_pose_injection_without_action_index():
    """Without an action_index (pose_index=None), no pose globals are injected."""
    imgs = ["pose0.png"]
    p = Patches(StateBus(), patches_loader=lambda: {
        "default": "osc(4, 0.1, 1.2).out()",
        "actions": {"wander": {"look": "s0.initImage(poseindex[pose])"}},
    }, poses_provider=lambda: _MockZones(imgs))
    sent = []
    bus = StateBus()
    machine = _MockMachine("calm")
    Display(bus, send=lambda f: sent.append(f), enabled=True, patch_code=p.code_for, machine=machine)
    snap = Snapshot(zone="wander", mode="action", action="look",
                    joint_pose=[0.0] * 6, cart_pose=[0.0] * 3, target_pose=[0.0] * 6,
                    speed=20.0, moving=False, action_index=None)
    bus.publish(Event.SNAPSHOT, snap)
    assert sent[0]["patch"] == "s0.initImage(poseindex[pose])"


def test_no_pose_injection_without_pose_imgs():
    """When the action has no per-pose images, no pose globals are injected."""
    p = Patches(StateBus(), patches_loader=lambda: {
        "default": "osc(4, 0.1, 1.2).out()",
        "actions": {"wander": {"look": "ACTION_CODE"}},
    }, poses_provider=lambda: _MockZones(None))
    sent = []
    bus = StateBus()
    machine = _MockMachine("calm")
    Display(bus, send=lambda f: sent.append(f), enabled=True, patch_code=p.code_for, machine=machine)
    snap = Snapshot(zone="wander", mode="action", action="look",
                    joint_pose=[0.0] * 6, cart_pose=[0.0] * 3, target_pose=[0.0] * 6,
                    speed=20.0, moving=False, action_index=0)
    bus.publish(Event.SNAPSHOT, snap)
    assert sent[0]["patch"] == "ACTION_CODE"


def main():
    failures = 0
    for name, fn in [
        ("test_state_frame_carries_concatenated_patch", test_state_frame_carries_concatenated_patch),
        ("test_concatenation_zone_action_only", test_concatenation_zone_action_only),
        ("test_concatenation_zone_default", test_concatenation_zone_default),
        ("test_concatenation_default_only", test_concatenation_default_only),
        ("test_concatenation_with_behavior_only", test_concatenation_with_behavior_only),
        ("test_state_frame_without_resolver_has_no_patch", test_state_frame_without_resolver_has_no_patch),
        ("test_bad_resolver_degrades_to_no_patch", test_bad_resolver_degrades_to_no_patch),
        ("test_pose_injection_in_frame", test_pose_injection_in_frame),
        ("test_no_pose_injection_without_action_index", test_no_pose_injection_without_action_index),
        ("test_no_pose_injection_without_pose_imgs", test_no_pose_injection_without_pose_imgs),
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
