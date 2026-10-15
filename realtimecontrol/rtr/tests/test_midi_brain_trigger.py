"""Tests for the MIDI note-on → brain autonomy trigger wiring.

Task 5 of the autonomous-mode zone-groups plan: a note-on received by the MIDI
adapter calls ``brain._handle_midi_trigger(note)``, which sets
``_autonomy_triggered`` when ``autonomy.events.midi`` is true.

Run directly (no pytest needed)::

    python tests/test_midi_brain_trigger.py

or under pytest::

    pytest tests/test_midi_brain_trigger.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import rtr.core  # noqa: F401  (resolve the circular import before rtr.brain.brain)

from rtr.core.bus import StateBus
from rtr.state.zones import Zones, validate_state_data
from rtr.state.machine import StateMachine
from rtr.brain.brain import Brain
from rtr.brain.config import validate_brain_config
from rtr.io.midi import MidiInput


# ---------------------------------------------------------------------------
# Helpers: a minimal zone table + a brain with the given events config.
# ---------------------------------------------------------------------------

def _zones():
    data = validate_state_data({"zones": {
        "a": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "act": {
                    "poses": [[0] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
            },
        },
    }})
    return Zones(data["zones"])


def _brain(events_midi: bool):
    """A Brain with autonomy enabled and the given ``events.midi`` setting."""
    cfg = validate_brain_config({
        "autonomy": {
            "enabled": True,
            "dwell_s": 30.0,
            "events": {"midi": events_midi, "camera": False, "websocket": False},
        },
        "zone_groups": {
            "home": {"zones": [], "behaviors": [], "enabled": False},
            "hunt": {"zones": [], "behaviors": [], "enabled": False},
            "facefocus": {"zones": [], "behaviors": [], "enabled": False},
            "perform": {"zones": [], "behaviors": [], "enabled": False},
            "g": {"zones": ["a"], "behaviors": ["hold"], "enabled": True},
        },
    })
    m = StateMachine(_zones(), initial_zone="a", tick_hz=20.0)
    return Brain(m, camera=None, config_loader=lambda: cfg)


def _midi(brain):
    """A disabled MidiInput (no rtmidi) wired to the given brain."""
    return MidiInput(StateBus(), in_port=0, enabled=False, brain=brain)


# ---------------------------------------------------------------------------
# The note-on → brain trigger.
# ---------------------------------------------------------------------------

def test_note_on_triggers_brain_when_midi_event_enabled():
    """A note-on (velocity > 0) sets ``_autonomy_triggered`` when ``events.midi`` is true."""
    b = _brain(events_midi=True)
    midi = _midi(b)
    # A note-on on channel 0, note 60, velocity 100.
    midi._on_message(0, ([0x90, 60, 100], 0.0))
    assert b._autonomy_triggered is True


def test_note_on_does_not_trigger_brain_when_midi_event_disabled():
    """A note-on does NOT set ``_autonomy_triggered`` when ``events.midi`` is false."""
    b = _brain(events_midi=False)
    midi = _midi(b)
    midi._on_message(0, ([0x90, 60, 100], 0.0))
    assert b._autonomy_triggered is False


def test_note_off_does_not_trigger_brain():
    """A note-off (velocity == 0) does not trigger the brain (even with events.midi true)."""
    b = _brain(events_midi=True)
    midi = _midi(b)
    # A note-off: velocity 0.
    midi._on_message(0, ([0x90, 60, 0], 0.0))
    assert b._autonomy_triggered is False


def test_cc_does_not_trigger_brain():
    """A control change does not trigger the brain (only note-ons do)."""
    b = _brain(events_midi=True)
    midi = _midi(b)
    # CC1 value 60.
    midi._on_message(0, ([0xB0, 1, 60], 0.0))
    assert b._autonomy_triggered is False


def test_no_brain_is_safe():
    """When no brain is wired, note-ons are handled without error."""
    midi = _midi(brain=None)
    # Should not raise.
    midi._on_message(0, ([0x90, 60, 100], 0.0))


def main():
    tests = [
        test_note_on_triggers_brain_when_midi_event_enabled,
        test_note_on_does_not_trigger_brain_when_midi_event_disabled,
        test_note_off_does_not_trigger_brain,
        test_cc_does_not_trigger_brain,
        test_no_brain_is_safe,
    ]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"\n{'ALL TESTS PASSED' if failures == 0 else f'{failures} TEST(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
