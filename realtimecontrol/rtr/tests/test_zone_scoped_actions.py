"""Test: sound and hydra actions are zone-scoped (zone -> action -> entry).

The core matches sound and patches by ``(zone, action)`` from the ``Snapshot``. The
``actions`` tables in ``sound.json`` / ``patches.json`` are two-level maps
(``zone -> action -> entry``). This test asserts:

- ``match_patch`` resolves a zone-scoped action to the *right zone's* entry, and falls
  through to mode/zone/default when the action is absent for that zone.
- The same action name in different zones resolves independently.
- The sound adapter dispatches a zone-scoped action's start message for the right zone.

Run directly (no pytest needed)::

    python tests/test_zone_scoped_actions.py

or under pytest::

    pytest tests/test_zone_scoped_actions.py
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.core.bus import StateBus
from rtr.core.commands import Event, Snapshot
from rtr.patches import match_patch
from rtr.sound import Sound
from rtr.state.zones import Zones, validate_state_data


def test_match_patch_zone_scoped_action_wins():
    """A zone-scoped action resolves to the right zone's entry (action > mode > zone)."""
    table = {
        "default": "DEF",
        "zones": {"wander": "ZONE_CODE"},
        "modes": {"action": "MODE_CODE"},
        "actions": {"wander": {"look": "WANDER_LOOK"}, "rest": {"look": "REST_LOOK"}},
    }
    # The action entry for (wander, look) wins.
    assert match_patch(table, zone="wander", mode="action", action="look") == "WANDER_LOOK"
    # The same action name in a different zone resolves independently.
    assert match_patch(table, zone="rest", mode="action", action="look") == "REST_LOOK"


def test_match_patch_action_absent_falls_through():
    """When the action is absent for the zone, fall through to mode/zone/default."""
    table = {
        "default": "DEF",
        "zones": {"wander": "ZONE_CODE"},
        "modes": {"action": "MODE_CODE"},
        "actions": {"wander": {"look": "WANDER_LOOK"}},
    }
    # (wander, yawn) has no patch -> mode "action" wins.
    assert match_patch(table, zone="wander", mode="action", action="yawn") == "MODE_CODE"
    # (wander, look) with no mode -> zone "wander" wins.
    assert match_patch(table, zone="wander", mode=None, action="look") == "WANDER_LOOK"
    # No zone/action/mode -> default.
    assert match_patch(table, zone="rest", mode=None, action=None) == "DEF"


def test_match_patch_requires_zone_for_action():
    """An action without a zone cannot resolve (falls through to mode/zone/default)."""
    table = {
        "default": "DEF",
        "modes": {"action": "MODE_CODE"},
        "actions": {"wander": {"look": "WANDER_LOOK"}},
    }
    # No zone: the action lookup is skipped, mode wins.
    assert match_patch(table, zone=None, mode="action", action="look") == "MODE_CODE"


def _sound(table):
    """A Sound adapter over a two-level actions table; capture the bytes it sends."""
    sent = []
    bus = StateBus()
    s = Sound(bus, sound_loader=lambda: table, out_port=None, out_device=None, enabled=True)
    # rtmidi is not required: capture the byte messages the adapter would send.
    s._send_bytes = lambda zone, msg: sent.append(list(msg))
    return s, sent


def test_sound_dispatches_zone_scoped_action_start():
    """A zone-scoped action start message is dispatched for the right zone."""
    table = {
        "channel": 0,
        "zones": {"wander": {"out_port": None, "command": {"note": 60, "velocity": 80}}},
        "modes": {},
        "actions": {"wander": {"look": {"start": {"note": 72, "velocity": 90}, "poses": []}}},
    }
    s, sent = _sound(table)
    snap = Snapshot(zone="wander", mode="action", action="look",
                    joint_pose=[0.0] * 6, cart_pose=[0.0] * 3, target_pose=[0.0] * 6,
                    speed=20.0, moving=False)
    s.on_event(Event.SNAPSHOT, snap)
    # The action start (note 72) is sent on channel 0 (0x90 | 0).
    assert [0x90, 72, 90] in sent, sent


def test_sound_dispatches_nothing_for_absent_action():
    """An action absent for the zone sends no action message (only the zone note)."""
    table = {
        "channel": 0,
        "zones": {"wander": {"out_port": None, "command": {"note": 60, "velocity": 80}}},
        "modes": {},
        "actions": {"rest": {"look": {"start": {"note": 72, "velocity": 90}, "poses": []}}},
    }
    s, sent = _sound(table)
    snap = Snapshot(zone="wander", mode="action", action="look",
                    joint_pose=[0.0] * 6, cart_pose=[0.0] * 3, target_pose=[0.0] * 6,
                    speed=20.0, moving=False)
    s.on_event(Event.SNAPSHOT, snap)
    # The zone note (60) is sent, but the action start (72) is not (absent for wander).
    assert [0x90, 60, 80] in sent, sent
    assert [0x90, 72, 90] not in sent, sent


# ---------------------------------------------------------------------------
# New action schema (kind / loop / target) — validate_state_data round-trip.
# ---------------------------------------------------------------------------
def _action_table(kind=None, loop=None, target=None):
    """A minimal 2-zone table; zone 'a' has one action carrying the schema fields."""
    act = {"pos": [[0] * 6], "speed": [1], "enabled": True}
    if kind is not None:
        act["kind"] = kind
    if loop is not None:
        act["loop"] = loop
    if target is not None:
        act["target"] = target
    return {"zones": {
        "a": {"startpos": [0] * 6, "exitpos": [0] * 6,
              "safezone": [[0, 10]] * 6, "exits": ["b"], "speed": 1,
              "actions": {"move": act}},
        "b": {"startpos": [1] * 6, "exitpos": [0] * 6,
              "safezone": [[0, 10]] * 6, "exits": [], "speed": 1},
    }}


def _expect_value_error(table):
    """Assert that ``validate_state_data`` rejects ``table`` with a ValueError."""
    try:
        validate_state_data(table)
    except ValueError:
        return
    raise AssertionError("expected ValueError for " + repr(table["zones"]["a"]["actions"]["move"]))


def test_action_schema_accepts_valid_kind_loop_target():
    """A full kind/loop/target action round-trips through validate_state_data."""
    pytest.skip("kind/target schema removed; replaced by next field")


def test_action_schema_defaults_when_absent():
    """Without kind/loop/target the accessors default (internal / True / None)."""
    pytest.skip("kind/target schema removed; replaced by next field")


def test_action_schema_rejects_bad_kind():
    """A kind outside internal/entry/exit is rejected."""
    _expect_value_error(_action_table(kind="nope"))


def test_action_schema_rejects_bad_target_zone():
    """A target.zone that does not name an existing zone is rejected."""
    _expect_value_error(_action_table(target={"zone": "nowhere"}))


def test_action_schema_rejects_bad_loop_type():
    """A loop that is not a bool is rejected."""
    _expect_value_error(_action_table(loop="yes"))


def test_action_schema_rejects_bad_target_pose():
    """A target.pose that is not a 6-number list is rejected."""
    _expect_value_error(_action_table(target={"zone": "b", "pose": [1, 2]}))


def main():
    failures = 0
    for name, fn in [
        ("test_match_patch_zone_scoped_action_wins", test_match_patch_zone_scoped_action_wins),
        ("test_match_patch_action_absent_falls_through", test_match_patch_action_absent_falls_through),
        ("test_match_patch_requires_zone_for_action", test_match_patch_requires_zone_for_action),
        ("test_sound_dispatches_zone_scoped_action_start", test_sound_dispatches_zone_scoped_action_start),
        ("test_sound_dispatches_nothing_for_absent_action", test_sound_dispatches_nothing_for_absent_action),
        ("test_action_schema_accepts_valid_kind_loop_target", test_action_schema_accepts_valid_kind_loop_target),
        ("test_action_schema_defaults_when_absent", test_action_schema_defaults_when_absent),
        ("test_action_schema_rejects_bad_kind", test_action_schema_rejects_bad_kind),
        ("test_action_schema_rejects_bad_target_zone", test_action_schema_rejects_bad_target_zone),
        ("test_action_schema_rejects_bad_loop_type", test_action_schema_rejects_bad_loop_type),
        ("test_action_schema_rejects_bad_target_pose", test_action_schema_rejects_bad_target_pose),
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
