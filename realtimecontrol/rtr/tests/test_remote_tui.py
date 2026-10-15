"""Test: the remote TUI's pure navigation logic reflects the action model.

The remote TUI groups a zone's actions by their optional ``kind``
(internal/entry/exit), marks the looping ones, annotates a ``target`` hand-off,
and sends the right command on activation. The shared navigation helpers live
in :mod:`rtr.flow` and the display shaping in :mod:`rtr.remote.tui`; this test
exercises them directly so the display/activation logic is covered without a
terminal.

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
    activation_commands,
    action_enabled,
    action_kind,
    action_loops,
    action_target,
    build_items,
    reverse_exits,
)
from rtr.remote.tui import item_label


ZONES = {
    "wakeup": {
        "actions": {
            "look": {},  # internal (default), loops (default)
            "roar": {"loop": False},  # internal, no loop
            "breathe": {"kind": "exit", "loop": True, "target": {"zone": "rest"}},
        },
        "exits": ["stretch", "rest"],
    },
    "rest": {
        "actions": {
            "lie_down": {"kind": "entry"},  # entry, loops (default)
            "sleep": {"enabled": False},  # internal, disabled
        },
        "exits": [],
    },
}


# A symmetric table: every edge is mirrored, so reverse exits equal exits.
SYM_ZONES = {
    "a": {"exits": ["b", "c"]},
    "b": {"exits": ["a", "c"]},
    "c": {"exits": ["a", "b"]},
}

# An asymmetric table: a -> b only, c -> a only; the incoming edges differ
# from the outgoing ones.
ASYM_ZONES = {
    "a": {"exits": ["b"]},
    "b": {"exits": []},
    "c": {"exits": ["a"]},
}


# ---------------------------------------------------------------------------
# Per-action field helpers (kind / loop / target / enabled).
# ---------------------------------------------------------------------------
def test_action_kind_defaults_internal():
    assert action_kind({}) == "internal"
    assert action_kind(None) == "internal"


def test_action_kind_respects_value():
    assert action_kind({"kind": "entry"}) == "entry"
    assert action_kind({"kind": "exit"}) == "exit"


def test_action_kind_rejects_invalid():
    assert action_kind({"kind": "banana"}) == "internal"
    assert action_kind({"kind": 3}) == "internal"


def test_action_loops_defaults_true():
    assert action_loops({}) is True
    assert action_loops(None) is True


def test_action_loops_respects_value():
    assert action_loops({"loop": False}) is False
    assert action_loops({"loop": True}) is True


def test_action_target_absent_is_none():
    assert action_target({}) is None
    assert action_target(None) is None


def test_action_target_returns_dict():
    assert action_target({"target": {"zone": "rest"}}) == {"zone": "rest"}


def test_action_enabled():
    assert action_enabled({}) is True
    assert action_enabled(None) is True
    assert action_enabled({"enabled": False}) is False
    assert action_enabled({"enabled": True}) is True


# ---------------------------------------------------------------------------
# reverse_exits: incoming edges, in table order.
# ---------------------------------------------------------------------------
def test_reverse_exits_symmetric():
    # Every incoming edge is also an outgoing exit; table order is preserved.
    assert reverse_exits(SYM_ZONES, "a") == ["b", "c"]
    assert reverse_exits(SYM_ZONES, "b") == ["a", "c"]
    assert reverse_exits(SYM_ZONES, "c") == ["a", "b"]


def test_reverse_exits_asymmetric():
    # a -> b only, c -> a only: the incoming edges are not a's own exits.
    assert reverse_exits(ASYM_ZONES, "a") == ["c"]
    assert reverse_exits(ASYM_ZONES, "b") == ["a"]
    assert reverse_exits(ASYM_ZONES, "c") == []


def test_reverse_exits_unknown_zone_is_empty():
    assert reverse_exits(ZONES, "does-not-exist") == []
    assert reverse_exits(ZONES, None) == []
    assert reverse_exits({}, "a") == []


def test_reverse_exits_ignores_malformed_entries():
    zones = {"a": {"exits": ["b"]}, "b": "not-a-dict", "c": {"exits": "b"}}
    # Only dict zones with a list-form exits that names the target count.
    assert reverse_exits(zones, "b") == ["a"]
    assert reverse_exits(zones, "c") == []


# ---------------------------------------------------------------------------
# build_items: grouping by kind, ordering, and the exits graph.
# ---------------------------------------------------------------------------
def test_build_items_groups_by_kind_and_orders():
    items = build_items(ZONES, "wakeup")
    got = [(it["section"], it["name"]) for it in items]
    # internal (by name) -> exit -> zones (exits, in declared order).
    assert got == [
        ("internal", "look"),
        ("internal", "roar"),
        ("exit", "breathe"),
        ("zones", "stretch"),
        ("zones", "rest"),
    ]
    # Action rows carry the raw action dict; zone rows carry None.
    assert items[0]["kind"] == "action" and items[0]["action"] == {}
    assert items[4]["kind"] == "exit" and items[4]["action"] is None


def test_build_items_entry_before_internal():
    items = build_items(ZONES, "rest")
    got = [(it["section"], it["name"]) for it in items]
    # entry first, then internal; no exits, but wakeup points at rest (reverse).
    assert got == [("entry", "lie_down"), ("internal", "sleep"), ("zones", "wakeup")]
    # The reverse-only row is flagged back (its name is not one of rest's exits).
    assert items[2]["back"] is True


def test_build_items_unknown_zone_is_empty():
    assert build_items(ZONES, "does-not-exist") == []
    assert build_items(ZONES, None) == []


def test_build_items_handles_list_actions():
    zones = {"z": {"actions": ["a", "b"], "exits": ["n"]}}
    items = build_items(zones, "z")
    got = [(it["section"], it["name"], it["kind"]) for it in items]
    # List-form actions are all treated as internal; the exit is a zone row.
    assert got == [
        ("internal", "a", "action"),
        ("internal", "b", "action"),
        ("zones", "n", "exit"),
    ]


def test_build_items_goto_is_exits_then_reverse():
    # Asymmetric: a's exits are ["b"], but c points at a (reverse-only).
    items = build_items(ASYM_ZONES, "a")
    got = [(it["section"], it["name"], it["back"]) for it in items]
    assert got == [
        ("zones", "b", False),  # declared exit, in declared order
        ("zones", "c", True),   # reverse-only, after the exits
    ]


def test_build_items_goto_no_back_when_reverse_subset_of_exits():
    # Symmetric: every reverse edge is already a declared exit, so no row is
    # flagged back and the ordering is the declared exits order.
    items = build_items(SYM_ZONES, "a")
    got = [(it["section"], it["name"], it["back"]) for it in items]
    assert got == [
        ("zones", "b", False),
        ("zones", "c", False),
    ]


def test_build_items_goto_forward_exits_flagged_back_false():
    items = build_items(ZONES, "wakeup")
    zones_rows = [it for it in items if it["section"] == "zones"]
    assert [(it["name"], it["back"]) for it in zones_rows] == [
        ("stretch", False),
        ("rest", False),
    ]


# ---------------------------------------------------------------------------
# item_label: name + loop marker + target annotation.
# ---------------------------------------------------------------------------
def test_item_label_plain_looping_action():
    assert item_label({"kind": "action", "name": "look", "action": {}}) == "look ↻"


def test_item_label_no_loop():
    assert item_label({"kind": "action", "name": "roar", "action": {"loop": False}}) == "roar"


def test_item_label_target_zone():
    it = {"kind": "action", "name": "breathe", "action": {"target": {"zone": "rest"}}}
    assert item_label(it) == "breathe ↻ → rest"


def test_item_label_target_action():
    it = {"kind": "action", "name": "step", "action": {"target": {"action": "roar"}}}
    assert item_label(it) == "step ↻ → roar"


def test_item_label_zone_has_no_markers():
    assert item_label({"kind": "exit", "name": "stretch", "action": None}) == "stretch"


def test_item_label_back_zone_gets_arrow_marker():
    # A reverse-only exit is marked ← so forward vs. back is visible.
    assert item_label({"kind": "exit", "name": "wakeup", "action": None, "back": True}) == "wakeup ←"


def test_item_label_forward_zone_has_no_arrow():
    assert item_label({"kind": "exit", "name": "stretch", "action": None, "back": False}) == "stretch"


# ---------------------------------------------------------------------------
# activation_commands: play_action + set_mode for actions, goto_zone for zones.
# ---------------------------------------------------------------------------
def test_activation_commands_action():
    it = {"kind": "action", "name": "look", "action": {}}
    assert activation_commands(it) == [
        {"cmd": "play_action", "action": "look"},
        {"cmd": "set_mode", "mode": "action"},
    ]


def test_activation_commands_action_any_kind():
    # An exit-kind action is still played (the core resolves its target on
    # completion); only the exits-graph zone rows use goto_zone.
    it = {"kind": "action", "name": "breathe", "action": {"kind": "exit"}}
    assert activation_commands(it) == [
        {"cmd": "play_action", "action": "breathe"},
        {"cmd": "set_mode", "mode": "action"},
    ]


def test_activation_commands_zone():
    assert activation_commands({"kind": "exit", "name": "stretch", "action": None}) == [
        {"cmd": "goto_zone", "zone": "stretch"},
    ]


def main():
    failures = 0
    tests = [
        ("test_action_kind_defaults_internal", test_action_kind_defaults_internal),
        ("test_action_kind_respects_value", test_action_kind_respects_value),
        ("test_action_kind_rejects_invalid", test_action_kind_rejects_invalid),
        ("test_action_loops_defaults_true", test_action_loops_defaults_true),
        ("test_action_loops_respects_value", test_action_loops_respects_value),
        ("test_action_target_absent_is_none", test_action_target_absent_is_none),
        ("test_action_target_returns_dict", test_action_target_returns_dict),
        ("test_action_enabled", test_action_enabled),
        ("test_reverse_exits_symmetric", test_reverse_exits_symmetric),
        ("test_reverse_exits_asymmetric", test_reverse_exits_asymmetric),
        ("test_reverse_exits_unknown_zone_is_empty", test_reverse_exits_unknown_zone_is_empty),
        ("test_reverse_exits_ignores_malformed_entries", test_reverse_exits_ignores_malformed_entries),
        ("test_build_items_groups_by_kind_and_orders", test_build_items_groups_by_kind_and_orders),
        ("test_build_items_entry_before_internal", test_build_items_entry_before_internal),
        ("test_build_items_unknown_zone_is_empty", test_build_items_unknown_zone_is_empty),
        ("test_build_items_handles_list_actions", test_build_items_handles_list_actions),
        ("test_build_items_goto_is_exits_then_reverse", test_build_items_goto_is_exits_then_reverse),
        ("test_build_items_goto_no_back_when_reverse_subset_of_exits", test_build_items_goto_no_back_when_reverse_subset_of_exits),
        ("test_build_items_goto_forward_exits_flagged_back_false", test_build_items_goto_forward_exits_flagged_back_false),
        ("test_item_label_plain_looping_action", test_item_label_plain_looping_action),
        ("test_item_label_no_loop", test_item_label_no_loop),
        ("test_item_label_target_zone", test_item_label_target_zone),
        ("test_item_label_target_action", test_item_label_target_action),
        ("test_item_label_zone_has_no_markers", test_item_label_zone_has_no_markers),
        ("test_item_label_back_zone_gets_arrow_marker", test_item_label_back_zone_gets_arrow_marker),
        ("test_item_label_forward_zone_has_no_arrow", test_item_label_forward_zone_has_no_arrow),
        ("test_activation_commands_action", test_activation_commands_action),
        ("test_activation_commands_action_any_kind", test_activation_commands_action_any_kind),
        ("test_activation_commands_zone", test_activation_commands_zone),
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
