"""Test: the remote TUI's pure navigation logic reflects the action model.

The remote TUI lists a zone's actions and the zones reachable from it. The
shared navigation helpers live in :mod:`rtr.flow` (the zone graph is derived
from actions' ``next`` fields) and the display shaping in
:mod:`rtr.remote.view` (``item_label``); this test exercises them directly so
the display/activation logic is covered without a terminal.

Run directly (no pytest needed)::

    python tests/test_remote_tui.py

or under pytest::

    pytest tests/test_remote_tui.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.flow import (
    action_enabled,
    action_loops,
    action_next_list,
    activation_commands,
    build_items,
    reachable_targets,
    reverse_targets,
)
from rtr.remote.view import item_label


ZONES = {
    "wakeup": {
        "actions": {
            "look": {},  # internal (default), loops (default)
            "roar": {"loop": False},  # internal, no loop
            "breathe": {"next": {"zone": "rest", "action": "sleep"}},
        },
    },
    "rest": {
        "actions": {
            "sleep": {},
            "lie_down": {"enabled": False},  # disabled
        },
    },
}

# An asymmetric table: a -> b only, c -> a only; the incoming edges differ
# from the outgoing ones.
ASYM_ZONES = {
    "a": {"actions": {"go": {"next": {"zone": "b", "action": "land"}}}, "exits": []},
    "b": {"actions": {}, "exits": []},
    "c": {"actions": {"go": {"next": {"zone": "a", "action": "land"}}}, "exits": []},
}


# ---------------------------------------------------------------------------
# Per-action field helpers (loop / enabled / next).
# ---------------------------------------------------------------------------
def test_action_loops_defaults_true():
    assert action_loops({}) is True
    assert action_loops(None) is True


def test_action_loops_respects_value():
    assert action_loops({"loop": False}) is False
    assert action_loops({"loop": True}) is True


def test_action_enabled():
    assert action_enabled({}) is True
    assert action_enabled(None) is True
    assert action_enabled({"enabled": False}) is False
    assert action_enabled({"enabled": True}) is True


def test_action_next_list_absent_is_empty():
    assert action_next_list({}) == []
    assert action_next_list(None) == []
    assert action_next_list({"next": "banana"}) == []


def test_action_next_list_single_dict():
    assert action_next_list({"next": {"zone": "rest"}}) == [{"zone": "rest"}]


def test_action_next_list_list_form():
    assert action_next_list({"next": [{"zone": "a"}, {"zone": "b"}]}) == [
        {"zone": "a"},
        {"zone": "b"},
    ]
    # Non-dict entries in the list are dropped.
    assert action_next_list({"next": [{"zone": "a"}, "x"]}) == [{"zone": "a"}]


# ---------------------------------------------------------------------------
# reachable_targets / reverse_targets: the next-field zone graph.
# ---------------------------------------------------------------------------
def test_reachable_targets_from_next_fields():
    assert reachable_targets(ZONES, "wakeup") == ["rest"]
    assert reachable_targets(ZONES, "rest") == []


def test_reachable_targets_unknown_zone_is_empty():
    assert reachable_targets(ZONES, "does-not-exist") == []
    assert reachable_targets(ZONES, None) == []
    assert reachable_targets({}, "a") == []


def test_reachable_targets_ignores_malformed():
    zones = {"a": {"actions": {"x": "not-a-dict", "y": {"next": 3}}}}
    assert reachable_targets(zones, "a") == []


def test_reverse_targets_incoming_edges():
    # Only wakeup's breathe points at rest.
    assert reverse_targets(ZONES, "rest") == ["wakeup"]
    assert reverse_targets(ZONES, "wakeup") == []


def test_reverse_targets_asymmetric():
    # a -> b only, c -> a only: the incoming edges are not the zone's own exits.
    assert reverse_targets(ASYM_ZONES, "a") == ["c"]
    assert reverse_targets(ASYM_ZONES, "b") == ["a"]
    assert reverse_targets(ASYM_ZONES, "c") == []


def test_reverse_targets_unknown_zone_is_empty():
    assert reverse_targets(ZONES, "does-not-exist") == []
    assert reverse_targets(ZONES, None) == []
    assert reverse_targets({}, "a") == []


def test_reverse_targets_ignores_malformed_entries():
    zones = {"a": {"actions": {"x": {"next": {"zone": "b"}}}}, "b": "not-a-dict"}
    assert reverse_targets(zones, "b") == ["a"]


# ---------------------------------------------------------------------------
# build_items: action rows in table order, then the zones section.
# ---------------------------------------------------------------------------
def test_build_items_actions_then_zones():
    items = build_items(ZONES, "wakeup")
    got = [(it["section"], it["name"]) for it in items]
    # actions (table order), then the reachable zone.
    assert got == [
        ("action", "look"),
        ("action", "roar"),
        ("action", "breathe"),
        ("zones", "rest"),
    ]
    # Action rows carry the raw action dict; zone rows carry None.
    assert items[0]["kind"] == "action" and items[0]["action"] == {}
    assert items[3]["kind"] == "zone" and items[3]["action"] is None


def test_build_items_reverse_zone_flagged_back():
    items = build_items(ZONES, "rest")
    got = [(it["section"], it["name"]) for it in items]
    # rest's own actions first; wakeup is reverse-only (it points at rest).
    assert got == [
        ("action", "sleep"),
        ("action", "lie_down"),
        ("zones", "wakeup"),
    ]
    # Only zone rows carry the back flag; wakeup is reverse-only.
    zone_rows = [it for it in items if it["section"] == "zones"]
    assert [(it["name"], it["back"]) for it in zone_rows] == [("wakeup", True)]


def test_build_items_target_action_is_first_enabled():
    # A zone row's target_action is the first enabled action of the TARGET zone.
    items = build_items(ZONES, "rest")
    wakeup_row = [it for it in items if it["section"] == "zones"][0]
    assert wakeup_row["target_action"] == "look"
    items = build_items(ZONES, "wakeup")
    rest_row = [it for it in items if it["section"] == "zones"][0]
    # rest's lie_down is disabled, so the first enabled one is sleep.
    assert rest_row["target_action"] == "sleep"


def test_build_items_unknown_zone_is_empty():
    assert build_items(ZONES, "does-not-exist") == []
    assert build_items(ZONES, None) == []


def test_build_items_goto_is_reachable_then_reverse():
    # Asymmetric: a's reachable is b, c points back at a (reverse-only).
    items = build_items(ASYM_ZONES, "a")
    got = [(it["section"], it["name"]) for it in items]
    assert got == [
        ("action", "go"),
        ("zones", "b"),          # reachable, in order
        ("zones", "c"),          # reverse-only, after the reachable set
    ]
    zone_rows = [it for it in items if it["section"] == "zones"]
    assert [(it["name"], it["back"]) for it in zone_rows] == [
        ("b", False),
        ("c", True),
    ]


def test_build_items_no_back_when_reverse_subset_of_reachable():
    zones = {
        "a": {"actions": {"go": {"next": [{"zone": "b"}, {"zone": "c"}]}}},
        "b": {"actions": {"back": {"next": {"zone": "a"}}}},
        "c": {"actions": {"back": {"next": {"zone": "a"}}}},
    }
    items = build_items(zones, "a")
    zone_rows = [it for it in items if it["section"] == "zones"]
    # b and c are reachable (declared) and also point back; no row is back.
    assert [(it["name"], it["back"]) for it in zone_rows] == [
        ("b", False),
        ("c", False),
    ]


# ---------------------------------------------------------------------------
# item_label: name + loop marker + next annotation + back marker.
# ---------------------------------------------------------------------------
def test_item_label_plain_looping_action():
    assert item_label({"kind": "action", "name": "look", "action": {}}) == "look ↻"


def test_item_label_no_loop():
    assert item_label({"kind": "action", "name": "roar", "action": {"loop": False}}) == "roar"


def test_item_label_next_zone():
    it = {"kind": "action", "name": "breathe", "action": {"next": {"zone": "rest"}}}
    assert item_label(it) == "breathe ↻ → rest"


def test_item_label_next_action():
    it = {"kind": "action", "name": "step", "action": {"next": {"action": "roar"}}}
    assert item_label(it) == "step ↻ → roar"


def test_item_label_zone_has_no_markers():
    assert item_label({"kind": "zone", "name": "rest", "action": None}) == "rest"


def test_item_label_back_zone_gets_arrow_marker():
    # A reverse-only exit is marked ← so forward vs. back is visible.
    assert item_label({"kind": "zone", "name": "wakeup", "action": None, "back": True}) == "wakeup ←"


def test_item_label_forward_zone_has_no_arrow():
    assert item_label({"kind": "zone", "name": "rest", "action": None, "back": False}) == "rest"


# ---------------------------------------------------------------------------
# activation_commands: play_action for actions, trigger_action for zones.
# ---------------------------------------------------------------------------
def test_activation_commands_action():
    it = {"kind": "action", "name": "look", "action": {}}
    assert activation_commands(it) == [
        {"cmd": "play_action", "action": "look"},
    ]


def test_activation_commands_action_next_field_ignored():
    # An action with a next field is still played; the core resolves the
    # next hand-off on completion.
    it = {"kind": "action", "name": "breathe", "action": {"next": {"zone": "rest"}}}
    assert activation_commands(it) == [
        {"cmd": "play_action", "action": "breathe"},
    ]


def test_activation_commands_zone():
    it = {"kind": "zone", "name": "rest", "action": None, "target_action": "sleep"}
    assert activation_commands(it) == [
        {"cmd": "trigger_action", "zone": "rest", "action": "sleep"},
    ]


def test_activation_commands_zone_without_target_action():
    it = {"kind": "zone", "name": "rest", "action": None, "target_action": None}
    assert activation_commands(it) == [
        {"cmd": "trigger_action", "zone": "rest"},
    ]


def main():
    failures = 0
    tests = [
        ("test_action_loops_defaults_true", test_action_loops_defaults_true),
        ("test_action_loops_respects_value", test_action_loops_respects_value),
        ("test_action_enabled", test_action_enabled),
        ("test_action_next_list_absent_is_empty", test_action_next_list_absent_is_empty),
        ("test_action_next_list_single_dict", test_action_next_list_single_dict),
        ("test_action_next_list_list_form", test_action_next_list_list_form),
        ("test_reachable_targets_from_next_fields", test_reachable_targets_from_next_fields),
        ("test_reachable_targets_unknown_zone_is_empty", test_reachable_targets_unknown_zone_is_empty),
        ("test_reachable_targets_ignores_malformed", test_reachable_targets_ignores_malformed),
        ("test_reverse_targets_incoming_edges", test_reverse_targets_incoming_edges),
        ("test_reverse_targets_asymmetric", test_reverse_targets_asymmetric),
        ("test_reverse_targets_unknown_zone_is_empty", test_reverse_targets_unknown_zone_is_empty),
        ("test_reverse_targets_ignores_malformed_entries", test_reverse_targets_ignores_malformed_entries),
        ("test_build_items_actions_then_zones", test_build_items_actions_then_zones),
        ("test_build_items_reverse_zone_flagged_back", test_build_items_reverse_zone_flagged_back),
        ("test_build_items_target_action_is_first_enabled", test_build_items_target_action_is_first_enabled),
        ("test_build_items_unknown_zone_is_empty", test_build_items_unknown_zone_is_empty),
        ("test_build_items_goto_is_reachable_then_reverse", test_build_items_goto_is_reachable_then_reverse),
        ("test_build_items_no_back_when_reverse_subset_of_reachable", test_build_items_no_back_when_reverse_subset_of_reachable),
        ("test_item_label_plain_looping_action", test_item_label_plain_looping_action),
        ("test_item_label_no_loop", test_item_label_no_loop),
        ("test_item_label_next_zone", test_item_label_next_zone),
        ("test_item_label_next_action", test_item_label_next_action),
        ("test_item_label_zone_has_no_markers", test_item_label_zone_has_no_markers),
        ("test_item_label_back_zone_gets_arrow_marker", test_item_label_back_zone_gets_arrow_marker),
        ("test_item_label_forward_zone_has_no_arrow", test_item_label_forward_zone_has_no_arrow),
        ("test_activation_commands_action", test_activation_commands_action),
        ("test_activation_commands_action_next_field_ignored", test_activation_commands_action_next_field_ignored),
        ("test_activation_commands_zone", test_activation_commands_zone),
        ("test_activation_commands_zone_without_target_action", test_activation_commands_zone_without_target_action),
    ]
    for name, fn in tests:
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
