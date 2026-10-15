"""SafetyGate red-link conflict: intra-zone vs inter-zone rules.

The red-link conflict (mirrored in ``editor.html`` ``edgeConflict``) has two
distinct rules:

- **Inter-zone** links (``from_zone != to_zone``): the two zones' safezones must
  overlap AND the source's end pose must match the target's entry pose (shared
  position) within 0.5 on every axis.

- **Intra-zone** links (``from_zone == to_zone``): NO shared-position requirement —
  the robot moves freely within the zone, so any pose inside the zone's safezone is
  a valid hand-off. The only conflict is a connection pose (source end / target
  entry) that lies OUTSIDE the zone's safezone.

Run directly (no pytest needed)::

    python tests/test_safety_conflict.py

or under pytest::

    pytest tests/test_safety_conflict.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.safety.gate import SafetyGate
from rtr.state.zones import Zones, validate_state_data


def _gate(zones_data: dict) -> SafetyGate:
    return SafetyGate(Zones(validate_state_data(zones_data)["zones"]))


def _zone(sz, actions):
    return {"safezone": sz, "speed": 1, "actions": actions}


def test_intra_zone_no_shared_pose_is_not_a_conflict():
    """An intra-zone link whose two poses differ must NOT be flagged (no shared
    position requirement inside a zone). Both poses are inside the safezone."""
    g = _gate({"zones": {
        "z": _zone([[-10, 10]] * 6, {
            "a": {"poses": [[5, 5, 5, 5, 5, 5]]},
            "b": {"poses": [[-5, -5, -5, -5, -5, -5]]},
        })
    }})
    conflict, issues = g.check_conflict("z", "a", "z", "b")
    assert not conflict, issues


def test_intra_zone_pose_outside_safezone_is_a_conflict():
    """An intra-zone link is conflicting when the source's end pose lies outside the
    zone's safezone (the robot cannot reach it)."""
    g = _gate({"zones": {
        "z": _zone([[-10, 10]] * 6, {
            "a": {"poses": [[50, 0, 0, 0, 0, 0]]},
            "b": {"poses": [[0, 0, 0, 0, 0, 0]]},
        })
    }})
    conflict, issues = g.check_conflict("z", "a", "z", "b")
    assert conflict, "expected a conflict"
    assert any("pose outside" in i for i in issues), issues


def test_intra_zone_target_entry_outside_safezone_is_a_conflict():
    """The target's entry pose outside the safezone is also a conflict."""
    g = _gate({"zones": {
        "z": _zone([[-10, 10]] * 6, {
            "a": {"poses": [[0, 0, 0, 0, 0, 0]]},
            "b": {"poses": [[0, -50, 0, 0, 0, 0]]},
        })
    }})
    conflict, issues = g.check_conflict("z", "a", "z", "b")
    assert conflict, "expected a conflict"
    assert any("pose outside" in i and "A2" in i for i in issues), issues


def test_intra_zone_no_source_action_is_not_a_conflict():
    """No current action (``from_action`` is None) -> no source pose to check, so no
    conflict (the target entry is in range)."""
    g = _gate({"zones": {
        "z": _zone([[-10, 10]] * 6, {
            "b": {"poses": [[0, 0, 0, 0, 0, 0]]},
        })
    }})
    conflict, issues = g.check_conflict("z", None, "z", "b")
    assert not conflict, issues


def test_inter_zone_shared_position_mismatch_is_a_conflict():
    """Inter-zone links keep the shared-position continuity requirement."""
    g = _gate({"zones": {
        "a": _zone([[-10, 50]] * 6, {"x": {"poses": [[10, 0, 0, 0, 0, 0]]}}),
        "b": _zone([[-10, 50]] * 6, {"y": {"poses": [[40, 0, 0, 0, 0, 0]]}}),
    }})
    conflict, issues = g.check_conflict("a", "x", "b", "y")
    assert conflict, "expected a conflict"
    assert any("no shared position" in i for i in issues), issues


def test_inter_zone_matching_shared_pose_is_not_a_conflict():
    """Inter-zone link whose end/start poses match and safezones overlap -> ok."""
    g = _gate({"zones": {
        "a": _zone([[-10, 50]] * 6, {"x": {"poses": [[10, 10, 10, 10, 10, 10]]}}),
        "b": _zone([[-10, 50]] * 6, {"y": {"poses": [[10, 10, 10, 10, 10, 10]]}}),
    }})
    conflict, issues = g.check_conflict("a", "x", "b", "y")
    assert not conflict, issues


def test_inter_zone_safezone_gap_is_a_conflict():
    """Inter-zone link whose zones' safezones do not overlap -> conflict."""
    g = _gate({"zones": {
        "a": _zone([[0, 5]] * 6, {"x": {"poses": [[0, 0, 0, 0, 0, 0]]}}),
        "b": _zone([[6, 10]] * 6, {"y": {"poses": [[6, 6, 6, 6, 6, 6]]}}),
    }})
    conflict, issues = g.check_conflict("a", "x", "b", "y")
    assert conflict, "expected a conflict"
    assert any("safezones do not overlap" in i for i in issues), issues


def main():
    tests = [
        test_intra_zone_no_shared_pose_is_not_a_conflict,
        test_intra_zone_pose_outside_safezone_is_a_conflict,
        test_intra_zone_target_entry_outside_safezone_is_a_conflict,
        test_intra_zone_no_source_action_is_not_a_conflict,
        test_inter_zone_shared_position_mismatch_is_a_conflict,
        test_inter_zone_matching_shared_pose_is_not_a_conflict,
        test_inter_zone_safezone_gap_is_a_conflict,
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
