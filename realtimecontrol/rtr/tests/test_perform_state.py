"""Test: Machine perform state (rtr/state/machine.py perform dict).

Covers:
- set_perform_velocity (clamping to 0.0-1.0).
- set_perform_multiplier (only accepts 1/2/4/8/16).
- update_perform_deadline (base_beats × multiplier × 60/BPM).
- update_perform_time_to_arrive (distance/velocity, late flag).
- perform_state() returns a copy.

All tests are headless: no robot, no hardware.

Run directly (no pytest needed)::

    python tests/test_perform_state.py

or under pytest::

    pytest tests/test_perform_state.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.state.machine import StateMachine
from rtr.state.zones import Zones


def _make_machine() -> StateMachine:
    """A minimal machine with a single test zone."""
    zones_data = {
        "test": {
            "enabled": True,
            "speed": 10.0,
            "actions": {
                "act1": {
                    "enabled": True,
                    "poses": [[0, 0, 0, 0, 0, 0]],
                    "behavior": "idle",
                    "speed": 10.0,
                    "base_beats": 4,
                }
            },
        }
    }
    zones = Zones(zones_data)
    return StateMachine(zones, initial_zone="test")


def test_perform_initial_state():
    """The perform state starts with velocity=0, multiplier=1, no deadline."""
    m = _make_machine()
    s = m.perform_state()
    assert s["velocity"] == 0.0
    assert s["multiplier"] == 1
    assert s["base_beats"] == 0
    assert s["deadline"] == 0.0
    assert s["time_to_arrive"] == 0.0
    assert s["late"] is False


def test_set_perform_velocity():
    """set_perform_velocity clamps to [0.0, 1.0]."""
    m = _make_machine()
    m.set_perform_velocity(0.5)
    assert m.perform["velocity"] == 0.5

    m.set_perform_velocity(2.0)
    assert m.perform["velocity"] == 1.0

    m.set_perform_velocity(-0.5)
    assert m.perform["velocity"] == 0.0


def test_set_perform_multiplier():
    """set_perform_multiplier accepts any positive number (divide + multiply)."""
    m = _make_machine()
    m.set_perform_multiplier(4)
    assert m.perform["multiplier"] == 4

    m.set_perform_multiplier(0.5)  # fractional divide step is valid
    assert m.perform["multiplier"] == 0.5

    m.set_perform_multiplier(0)  # non-positive -> ignored
    assert m.perform["multiplier"] == 0.5  # unchanged

    m.set_perform_multiplier(16)
    assert m.perform["multiplier"] == 16


def test_set_perform_multiplier_rejects_non_numeric():
    """Non-numeric / bool values are ignored (the current multiplier is kept)."""
    m = _make_machine()
    m.set_perform_multiplier(2)
    m.set_perform_multiplier("2x")  # invalid
    assert m.perform["multiplier"] == 2
    m.set_perform_multiplier(True)  # bool is rejected
    assert m.perform["multiplier"] == 2


def test_update_perform_deadline():
    """deadline = base_beats × multiplier × (60/BPM)."""
    m = _make_machine()
    m.set_perform_multiplier(4)
    m.update_perform_deadline(base_beats=8, bpm=120.0)
    # 8 × 4 × (60/120) = 8 × 4 × 0.5 = 16.0 s
    assert m.perform["deadline"] == 16.0
    assert m.perform["base_beats"] == 8


def test_update_perform_deadline_zero_bpm():
    """When BPM is 0, deadline is 0 (no timing)."""
    m = _make_machine()
    m.update_perform_deadline(base_beats=4, bpm=0.0)
    assert m.perform["deadline"] == 0.0


def test_update_perform_time_to_arrive():
    """time_to_arrive = distance / velocity; late when > deadline."""
    m = _make_machine()
    m.set_perform_velocity(0.5)
    m.set_perform_multiplier(2)
    m.update_perform_deadline(base_beats=4, bpm=120.0)
    # deadline = 4 × 2 × 0.5 = 4.0 s

    # distance = 10.0, velocity = 0.5 → tta = 20.0 s → late
    m.update_perform_time_to_arrive(distance=10.0)
    assert abs(m.perform["time_to_arrive"] - 20.0) < 0.001
    assert m.perform["late"] is True

    # Now increase velocity so tta < deadline.
    m.set_perform_velocity(0.9)
    m.update_perform_time_to_arrive(distance=10.0)
    # tta = 10.0 / 0.9 = 11.11 s → still late (11.11 > 4.0)
    assert m.perform["late"] is True


def test_update_perform_time_to_arrive_not_late():
    """When tta < deadline, late is False."""
    m = _make_machine()
    m.set_perform_velocity(0.9)
    m.set_perform_multiplier(16)
    m.update_perform_deadline(base_beats=4, bpm=120.0)
    # deadline = 4 × 16 × 0.5 = 32.0 s

    m.update_perform_time_to_arrive(distance=10.0)
    # tta = 10.0 / 0.9 = 11.11 s → not late (11.11 < 32.0)
    assert m.perform["late"] is False


def test_update_perform_zero_velocity():
    """When velocity is 0, time_to_arrive is infinity (late)."""
    m = _make_machine()
    m.set_perform_velocity(0.0)
    m.set_perform_multiplier(2)
    m.update_perform_deadline(base_beats=4, bpm=120.0)

    m.update_perform_time_to_arrive(distance=10.0)
    assert m.perform["time_to_arrive"] == float("inf")
    assert m.perform["late"] is True


def test_perform_state_returns_copy():
    """perform_state() returns a copy (mutations don't affect the machine)."""
    m = _make_machine()
    m.set_perform_velocity(0.5)
    s = m.perform_state()
    s["velocity"] = 0.99
    assert m.perform["velocity"] == 0.5


def main():
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {name}: {e}")
    if failures:
        print(f"\n{failures} TEST(S) FAILED")
        return 1
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
