"""Test: the LED-wall red-light overlay (robotconfig normalization + state frame).

Covers:
- robot_config_red_light() normalization: defaults, partial fill, range clamping,
  and rejection of malformed / non-numeric / bool values.
- The Display state frame carries a ``red_light`` field when a provider is wired.
- The frame omits ``red_light`` when no provider is wired, and degrades to no
  field when the provider throws or returns a non-dict.

All tests are headless: no robot, no hardware, no browser.

Run directly (no pytest needed)::

    python tests/test_red_light.py

or under pytest::

    pytest tests/test_red_light.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.core.bus import StateBus
from rtr.core.commands import Event, Snapshot
from rtr.display import Display
from rtr.robot.config import robot_config_red_light, DEFAULT_RED_LIGHT


def _snap(zone="rest", mode=None, action=None):
    return Snapshot(zone=zone, mode=mode, action=action,
                    joint_pose=[0.0] * 6, cart_pose=[0.0] * 3, target_pose=[0.0] * 6,
                    speed=20.0, moving=False)


def _frame_with(red_light_provider=None):
    """Capture the state frame the Display sends, with an optional red-light provider."""
    sent = []
    bus = StateBus()
    Display(bus, send=lambda f: sent.append(f), enabled=True, patch_code=None,
            red_light_provider=red_light_provider)
    bus.publish(Event.SNAPSHOT, _snap())
    return sent[0]


# --- robot_config_red_light normalization --------------------------------------

def test_red_light_defaults_when_absent():
    """No red_light key -> the full default config (disabled, centered)."""
    rl = robot_config_red_light({})
    assert rl == DEFAULT_RED_LIGHT
    assert rl["enabled"] is False
    assert rl["x"] == 0.5 and rl["y"] == 0.5
    assert rl["bar_w"] == 1.0 and rl["bar_h"] == 160.0 and rl["radius"] == 80.0
    assert rl["morph"] is True and rl["per_beat"] == 1.0


def test_red_light_defaults_when_not_a_dict():
    """A non-dict red_light value is ignored (defaults returned)."""
    assert robot_config_red_light({"red_light": "on"}) == DEFAULT_RED_LIGHT
    assert robot_config_red_light({"red_light": [1, 2]}) == DEFAULT_RED_LIGHT
    assert robot_config_red_light({"red_light": None}) == DEFAULT_RED_LIGHT


def test_red_light_partial_fill():
    """A partial config fills the missing fields from the defaults."""
    rl = robot_config_red_light({"red_light": {"enabled": True, "x": 0.25}})
    assert rl["enabled"] is True and rl["x"] == 0.25
    assert rl["y"] == 0.5 and rl["bar_w"] == 1.0 and rl["per_beat"] == 1.0


def test_red_light_clamps_ranges():
    """Out-of-range values are clamped so a hand-edited file can't kill the overlay."""
    rl = robot_config_red_light({"red_light": {
        "x": 5.0, "y": -2.0, "bar_w": -3, "bar_h": 40, "radius": 12, "per_beat": 0.01,
    }})
    assert rl["x"] == 1.0 and rl["y"] == 0.0
    assert rl["bar_w"] == 1.0 and rl["bar_h"] == 40.0 and rl["radius"] == 12.0
    assert rl["per_beat"] == 0.05  # clamped to the minimum


def test_red_light_rejects_malformed_types():
    """Non-numeric / bool values are ignored (the default for that field is kept)."""
    rl = robot_config_red_light({"red_light": {
        "enabled": "yes", "x": "left", "bar_w": True, "morph": "no",
    }})
    assert rl["enabled"] is False  # bool string rejected -> default
    assert rl["x"] == 0.5  # string rejected -> default
    assert rl["bar_w"] == 1.0  # bool rejected (bool is an int subclass) -> default
    assert rl["morph"] is True  # string rejected -> default


def test_red_light_bool_enabled_and_morph():
    """Boolean enabled/morph values are honored."""
    rl = robot_config_red_light({"red_light": {"enabled": True, "morph": False}})
    assert rl["enabled"] is True and rl["morph"] is False


def test_red_light_numeric_types_coerced_to_float():
    """Int and float values are both accepted and coerced to float."""
    rl = robot_config_red_light({"red_light": {"bar_w": 2, "bar_h": 120.5, "per_beat": 2}})
    assert rl["bar_w"] == 2.0 and rl["bar_h"] == 120.5 and rl["per_beat"] == 2.0


# --- Display state frame wiring ------------------------------------------------

def test_state_frame_carries_red_light_when_provider_wired():
    """A wired provider's dict is forwarded into the state frame as red_light."""
    cfg = {"red_light": {"enabled": True, "x": 0.3, "y": 0.7, "per_beat": 2}}
    frame = _frame_with(red_light_provider=lambda: robot_config_red_light(cfg))
    assert "red_light" in frame
    assert frame["red_light"]["enabled"] is True
    assert frame["red_light"]["x"] == 0.3 and frame["red_light"]["y"] == 0.7
    assert frame["red_light"]["per_beat"] == 2.0


def test_state_frame_omits_red_light_without_provider():
    """No provider -> the frame has no red_light field (clients use their fallback)."""
    frame = _frame_with()
    assert "red_light" not in frame


def test_state_frame_degrades_when_provider_throws():
    """A provider that throws degrades to no red_light field (the frame is still sent)."""
    def bad_provider():
        raise RuntimeError("boom")
    frame = _frame_with(red_light_provider=bad_provider)
    assert "red_light" not in frame


def test_state_frame_degrades_when_provider_returns_non_dict():
    """A provider returning a non-dict degrades to no red_light field."""
    frame = _frame_with(red_light_provider=lambda: "on")
    assert "red_light" not in frame


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
