"""Test: Machine perform state (rtr/state/machine.py perform dict).

Covers:
- set_perform_velocity (clamping to 0.0-100.0, the CC-A percentage).
- set_perform_axis_max_deg_s (per-axis max angular speed, deg/s at zone speed 100).
- set_perform_multiplier (any positive number).
- update_perform_deadline (base_beats × multiplier × 60/BPM).
- update_perform_time_to_arrive (max over per-joint motion-profile times, late flag).
  In perform mode the per-axis speed is axis_max_deg_s[i] × (zone_speed/100) ×
  (cc_a/100); each axis follows its own trapezoidal/triangular profile.
- _current_speed (perform mode -> max per-axis deg/s; otherwise the zone %).
- perform_state() returns a JSON-safe copy (infinite time_to_arrive -> None).

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

from rtr.state.machine import StateMachine, motion_profile_time
from rtr.state.zones import Zones


def _make_machine(speed: float = 10.0) -> StateMachine:
    """A minimal machine with a single test zone.

    ``speed`` is the zone/action speed (a percentage, 0-100) — the ``base`` in the
    perform-mode deg/s model. Defaults to 10.0 (a low speed) so non-perform tests
    that read ``m.speed`` keep a stable value; the TTA tests pass 100.0 so the
    per-axis deg/s math comes out clean.
    """
    zones_data = {
        "test": {
            "enabled": True,
            "speed": speed,
            "actions": {
                "act1": {
                    "enabled": True,
                    "poses": [[0, 0, 0, 0, 0, 0]],
                    "behavior": "idle",
                    "speed": speed,
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
    """set_perform_velocity (CC-A) clamps to [0.0, 100.0] (a percentage)."""
    m = _make_machine()
    m.set_perform_velocity(50)
    assert m.perform["velocity"] == 50.0

    m.set_perform_velocity(150.0)
    assert m.perform["velocity"] == 100.0

    m.set_perform_velocity(-50)
    assert m.perform["velocity"] == 0.0


def test_set_perform_axis_max_deg_s():
    """set_perform_axis_max_deg_s stores 6 non-negative values; bad input is ignored."""
    m = _make_machine()
    m.set_perform_axis_max_deg_s([30.0, 40.0, 50.0, 60.0, 70.0, 80.0])
    assert m.perform["axis_max_deg_s"] == [30.0, 40.0, 50.0, 60.0, 70.0, 80.0]

    # Negative values are clamped to 0.
    m.set_perform_axis_max_deg_s([-5.0, 12.0, 60.0, 120.0, 120.0, 180.0])
    assert m.perform["axis_max_deg_s"][0] == 0.0

    # A malformed list (wrong length) is ignored — the previous value is kept.
    before = list(m.perform["axis_max_deg_s"])
    m.set_perform_axis_max_deg_s([1.0, 2.0, 3.0])
    assert m.perform["axis_max_deg_s"] == before


def test_current_speed_perform_mode():
    """_current_speed (perform mode) = max over per-axis deg/s, floored at 1.0.

    per-axis speed = axis_max[i] × (zone_speed/100) × (cc_a/100).
    """
    m = _make_machine(speed=100.0)
    m.set_control_mode("perform")
    m.set_perform_axis_max_deg_s([60.0, 60.0, 60.0, 120.0, 120.0, 180.0])
    m.set_perform_velocity(100)  # cc_a = 1.0
    # per-axis speeds = [60,60,60,120,120,180] × 1.0 × 1.0 → max = 180.0 deg/s
    assert abs(m._current_speed() - 180.0) < 1e-9

    m.set_perform_velocity(50)  # cc_a = 0.5 → max = 90.0 deg/s
    assert abs(m._current_speed() - 90.0) < 1e-9


def test_current_speed_perform_mode_zero_velocity_floors():
    """At cc_a = 0 the per-axis speed is 0; _current_speed floors at 1.0 deg/s."""
    m = _make_machine(speed=100.0)
    m.set_control_mode("perform")
    m.set_perform_axis_max_deg_s([60.0, 60.0, 60.0, 120.0, 120.0, 180.0])
    m.set_perform_velocity(0.0)
    assert m._current_speed() == 1.0


def test_current_speed_non_perform_returns_zone_percentage():
    """Outside perform mode _current_speed returns the zone/action speed (%) as-is."""
    m = _make_machine(speed=42.0)
    assert m.control_mode == "auto"
    assert m._current_speed() == 42.0


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


def test_motion_profile_time_trapezoidal():
    """Trapezoidal: D >= v^2/a → t = D/v + v/a."""
    # v=30, a=100 → threshold v^2/a = 9.0. D=30.0 >= 9.0 → trapezoidal.
    # t = 30.0/30.0 + 30.0/100.0 = 1.0 + 0.3 = 1.3 s
    assert abs(motion_profile_time(30.0, 30.0, 100.0) - 1.3) < 1e-9


def test_motion_profile_time_triangular():
    """Triangular: D < v^2/a → t = 2*sqrt(D/a)."""
    # v=30, a=100 → threshold 9.0. D=4.0 < 9.0 → triangular.
    # t = 2*sqrt(4.0/100.0) = 2*0.2 = 0.4 s
    assert abs(motion_profile_time(4.0, 30.0, 100.0) - 0.4) < 1e-9


def test_motion_profile_time_zero_distance():
    """A zero distance takes no time (regardless of v/a)."""
    assert motion_profile_time(0.0, 30.0, 100.0) == 0.0


def test_motion_profile_time_zero_velocity():
    """A non-positive velocity (or acceleration) means the axis cannot move (inf)."""
    assert motion_profile_time(10.0, 0.0, 100.0) == float("inf")
    assert motion_profile_time(10.0, 30.0, 0.0) == float("inf")


def _setup_perform_tta(m, axis_max=(30.0, 30.0, 30.0, 30.0, 30.0, 30.0),
                       cc_a=100.0, accel=100.0):
    """Enter perform mode and seed the per-axis deg/s model for a TTA test.

    Zone speed is 100 (from _make_machine), so the per-axis speed is
    ``axis_max[i] × (100/100) × (cc_a/100) = axis_max[i] × cc_a/100``.
    """
    m.set_control_mode("perform")
    m.set_perform_axis_max_deg_s(list(axis_max))
    m.set_perform_acceleration_max_deg_s2(accel)
    m.set_perform_velocity(cc_a)
    return m


def test_update_perform_time_to_arrive_max_over_joints():
    """time_to_arrive = MAX over the per-joint rest-to-rest times (coordinated move).

    cc_a=100, zone speed=100, axis_max=30 → per-axis speed = 30.0 deg/s.
    """
    m = _make_machine(speed=100.0)
    _setup_perform_tta(m, cc_a=100.0)  # per-axis speed = 30 × 1.0 × 1.0 = 30.0 deg/s
    # joint 0: D=30.0 → trapezoidal 1.3 s; joint 1: D=4.0 → triangular 0.4 s.
    # max(1.3, 0.4, 0, 0, 0, 0) = 1.3 s
    m.update_perform_time_to_arrive([30.0, 4.0, 0.0, 0.0, 0.0, 0.0])
    assert abs(m.perform["time_to_arrive"] - 1.3) < 1e-9


def test_update_perform_time_to_arrive_late():
    """late = True when the max-over-joints time exceeds the deadline.

    cc_a=50, zone speed=100, axis_max=30 → per-axis speed = 15.0 deg/s.
    """
    m = _make_machine(speed=100.0)
    _setup_perform_tta(m, cc_a=50.0)  # per-axis speed = 30 × 1.0 × 0.5 = 15.0 deg/s
    m.set_perform_multiplier(1)
    m.update_perform_deadline(base_beats=4, bpm=120.0)
    # deadline = 4 × 1 × (60/120) = 2.0 s
    # D=30.0, v=15.0, a=100 → trapezoidal: t = 30.0/15.0 + 15.0/100.0 = 2.0 + 0.15 = 2.15 s
    m.update_perform_time_to_arrive([30.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert abs(m.perform["time_to_arrive"] - 2.15) < 1e-9
    assert m.perform["late"] is True  # 2.15 > 2.0


def test_update_perform_time_to_arrive_not_late():
    """When the max-over-joints time is under the deadline, late is False."""
    m = _make_machine(speed=100.0)
    _setup_perform_tta(m, cc_a=100.0)  # per-axis speed = 30.0 deg/s
    m.set_perform_multiplier(16)
    m.update_perform_deadline(base_beats=4, bpm=120.0)
    # deadline = 4 × 16 × 0.5 = 32.0 s
    # D=30.0 → t = 1.3 s → not late (1.3 < 32.0)
    m.update_perform_time_to_arrive([30.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert abs(m.perform["time_to_arrive"] - 1.3) < 1e-9
    assert m.perform["late"] is False


def test_update_perform_zero_velocity():
    """When CC-A velocity is 0, the per-axis speed is 0 → time_to_arrive is infinity (late)."""
    m = _make_machine(speed=100.0)
    _setup_perform_tta(m, cc_a=0.0)  # per-axis speed = 30 × 1.0 × 0 = 0 deg/s
    m.set_perform_multiplier(2)
    m.update_perform_deadline(base_beats=4, bpm=120.0)

    m.update_perform_time_to_arrive([10.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert m.perform["time_to_arrive"] == float("inf")
    assert m.perform["late"] is True


def test_perform_state_sanitizes_infinite_time_to_arrive():
    """perform_state() maps an infinite time_to_arrive to None (JSON-safe).

    The internal state keeps float("inf") (cc_a 0 -> cannot arrive), but the
    broadcast copy must be JSON-serializable: JSON has no Infinity, and
    json.dumps would emit the non-JSON token ``Infinity`` that the client's
    JSON.parse rejects ("Unexpected token 'I'").
    """
    m = _make_machine(speed=100.0)
    _setup_perform_tta(m, cc_a=0.0)  # per-axis speed = 0 deg/s
    m.update_perform_time_to_arrive([10.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert m.perform["time_to_arrive"] == float("inf")  # internal state unchanged
    s = m.perform_state()
    assert s["time_to_arrive"] is None

    # Finite values pass through untouched.
    m.set_perform_velocity(100)  # per-axis speed = 30 × 1.0 × 1.0 = 30.0 deg/s
    m.update_perform_time_to_arrive([30.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    s2 = m.perform_state()
    assert abs(s2["time_to_arrive"] - 1.3) < 1e-9


def test_perform_state_returns_copy():
    """perform_state() returns a copy (mutations don't affect the machine)."""
    m = _make_machine()
    m.set_perform_velocity(50)
    s = m.perform_state()
    s["velocity"] = 99
    assert m.perform["velocity"] == 50.0


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
