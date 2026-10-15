"""Test: the per-zone MIDI-in mapping (midi.json data layer + per-zone dispatch).

Covers the ``rtr/io/midi.py`` feature for the per-zone ``midi`` editor tab:

- the data layer (``validate_midi_key`` / ``validate_midi_data`` /
  ``load_midi_data`` / ``builtin_midi_data``), including the deprecated top-level
  ``in_port`` / ``mapping`` fields (accepted and preserved);
- ``MidiInput``'s per-zone dispatch (a zone's ``command`` → GOTO_ZONE, an ``actions``
  key → PLAY_ACTION; port-scoped; note-offs excluded);
- the legacy hardcoded protocol as a fallback **on the default port only**;
- learn capture (``start_learn(zone, action)`` / ``stop_learn`` / ``stop`` /
  ``is_learning``), armed per-zone and scoped to the zone's port;
- the global nav cursor (``nav`` section of ``midi.json``): validation of the nav
  keys, the virtual cursor over the current zone's ``build_items`` rows
  (``next_action`` wraps, ``next_zone`` / ``back`` → GOTO_ZONE, ``retrigger`` replays
  the current action), nav dispatch on any port, and nav learn capture on the default
  port (never dispatched);
- ``reload()`` (re-read via the loader, swap the table, publish ``MIDI_CHANGED``,
  reconnect the ports);
- the effective enabled setting, the per-port helpers, and ``list_ports()``.

All tests are headless: no live rtmidi hardware, no browser. ``MidiInput`` is created
disabled (so it never opens a real port) and fed messages the way the per-port rtmidi
callback would; commands are read back off the :class:`StateBus`.

Run directly (no pytest needed)::

    python tests/test_midi_in.py

or under pytest::

    pytest tests/test_midi_in.py
"""

import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.core.bus import StateBus
from rtr.core.commands import Event
from rtr.io.midi import (
    MidiInput,
    builtin_midi_data,
    load_midi_data,
    validate_midi_data,
    validate_midi_key,
    validate_midi_target,
)


# ---------------------------------------------------------------------------
# Data layer: keys, the per-zone table, and the deprecated legacy fields.
# ---------------------------------------------------------------------------
def test_validate_midi_key_accepts_valid_keys():
    for key in ("cc:7:42", "cc:0:0", "cc:127:127",
                "note:0:60", "note:7:127",
                "program:0:0", "program:7:127"):
        validate_midi_key(key)  # must not raise


def test_validate_midi_key_rejects_bad_keys():
    for key in ("cc:7", "cc:7:42:1", "xyz:7:42", "note:8:60", "cc:999:1", ""):
        try:
            validate_midi_key(key)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for key {key!r}")


def test_validate_midi_target_accepts_valid_targets():
    # The legacy mapping target (kept for the deprecated top-level mapping).
    validate_midi_target({"target": "zone", "zone": "wander"}, "")
    validate_midi_target({"target": "mode", "mode": "action"}, "")
    validate_midi_target({"target": "action", "zone": "wander", "action": "look"}, "")
    validate_midi_target({"target": "clear"}, "")
    validate_midi_target({"target": "random_action", "zone": "wander"}, "")


def test_validate_midi_target_rejects_bad_targets():
    for tgt in (
        {"target": "nope"},
        {"target": "zone"},                       # zone target without a zone name
        {"target": "mode"},                       # mode target without a mode name
        {"target": "action", "zone": "wander"},   # action target without an action name
        {"target": "random_action"},              # random_action without a zone
        "not-a-dict",
        None,
    ):
        try:
            validate_midi_target(tgt, "")
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for target {tgt!r}")


def test_validate_midi_data_accepts_per_zone():
    data = {"enabled": True,
            "zones": {"wander": {"in_port": 1, "command": "cc:5:10",
                                   "actions": {"look": "note:0:60"}},
                      "rest": {"in_port": None, "command": None, "actions": {}}}}
    assert validate_midi_data(data) is data


def test_validate_midi_data_accepts_legacy_fields():
    # The deprecated top-level in_port / mapping are accepted and preserved.
    data = {"enabled": True, "zones": {},
            "in_port": 0, "mapping": {"cc:7:42": {"target": "zone", "zone": "wander"}}}
    assert validate_midi_data(data)["mapping"] == {
        "cc:7:42": {"target": "zone", "zone": "wander"}}


def test_validate_midi_data_rejects_bad_tables():
    for data in (
        "not-a-dict",
        {"enabled": "yes"},
        {"zones": "not-a-dict"},
        {"zones": {"wander": "not-a-dict"}},
        {"zones": {"wander": {"in_port": -1}}},
        {"zones": {"wander": {"in_port": "0"}}},
        {"zones": {"wander": {"command": "cc:7"}}},            # bad key
        {"zones": {"wander": {"actions": "not-a-dict"}}},
        {"zones": {"wander": {"actions": {"look": "nope"}}}},  # bad key
        {"in_port": -1},                                        # legacy bad
        {"mapping": "not-a-dict"},
        {"mapping": {"cc:7": {"target": "zone", "zone": "wander"}}},   # bad key
        {"mapping": {"cc:7:42": {"target": "nope"}}},                  # bad target
    ):
        try:
            validate_midi_data(data)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for data {data!r}")


def test_validate_midi_data_accepts_nav():
    # A nav object with valid keys (MIDI key or null) is accepted.
    data = {"enabled": True, "zones": {},
            "nav": {"next_action": "cc:1:5", "next_zone": "note:0:60",
                    "back": None, "retrigger": "program:0:3"}}
    assert validate_midi_data(data) is data


def test_validate_midi_data_rejects_unknown_nav_name():
    try:
        validate_midi_data({"enabled": True, "zones": {},
                             "nav": {"next_actions": "cc:1:5"}})
    except ValueError:
        return
    raise AssertionError("expected ValueError for an unknown nav name")


def test_validate_midi_data_rejects_bad_nav_key():
    for bad in ("cc:7", "xyz:1:5", 5):
        try:
            validate_midi_data({"enabled": True, "zones": {},
                                 "nav": {"next_action": bad}})
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for nav key {bad!r}")


def test_builtin_midi_data_is_empty_zones():
    assert builtin_midi_data() == {"enabled": True, "zones": {},
                                    "nav": {"next_action": None, "next_zone": None,
                                             "back": None, "retrigger": None}}


def test_load_midi_data_reads_and_validates():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "midi.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"enabled": true, "zones": {"wander": {"in_port": 1, '
                    '"command": "cc:1:5", "actions": {}}}}')
        data = load_midi_data(p)
        assert data["zones"]["wander"]["command"] == "cc:1:5"
        assert data["zones"]["wander"]["in_port"] == 1


# ---------------------------------------------------------------------------
# MidiInput helpers.
# ---------------------------------------------------------------------------
def _midi(data=None, zones=None, in_port=0):
    """A disabled MidiInput (never opens a real port) with an optional table + zones."""
    bus = StateBus()
    m = MidiInput(bus, in_port=in_port, enabled=False)
    if data is not None:
        m._data = data
    if zones is not None:
        m.zones_provider = lambda: zones
    return m, bus


def _feed(m, message, port=None):
    """Feed a MIDI message on a specific port (the default port if None)."""
    port = m.in_port if port is None else port
    m._on_message(port, (list(message), 0.0))


def _cmds(bus):
    """The (command name, payload) pairs currently queued on the bus."""
    return [(c.cmd.value, c.payload) for c in bus.drain_commands()]


# ---------------------------------------------------------------------------
# Per-zone dispatch.
# ---------------------------------------------------------------------------
def test_zone_command_triggers_zone():
    zones = {"wander": {"actions": {"look": {}}}}
    m, bus = _midi({"zones": {"wander": {"in_port": 1, "command": "cc:5:10",
                                          "actions": {"look": {}}}}}, zones=zones)
    _feed(m, [0xB0, 5, 10], port=1)
    assert _cmds(bus) == [("trigger_action", {"zone": "wander", "action": "look"})]


def test_zone_action_triggers_action():
    m, bus = _midi({"zones": {"wander": {"in_port": 1, "command": None,
                                          "actions": {"look": "note:0:60"}}}})
    _feed(m, [0x90, 60, 90], port=1)
    assert _cmds(bus) == [("trigger_action", {"zone": "wander", "action": "look"})]


def test_dispatch_is_port_scoped():
    # The key only maps the zone on port 1; the same key on the default port is a no-op.
    m, bus = _midi({"zones": {"wander": {"in_port": 1, "command": "cc:5:10"}}})
    _feed(m, [0xB0, 5, 10], port=0)
    assert _cmds(bus) == []


def test_zone_null_in_port_uses_default_port():
    # A zone with a null in_port listens on the default port.
    zones = {"wander": {"actions": {"look": {}}}}
    m, bus = _midi({"zones": {"wander": {"in_port": None, "command": "cc:5:10",
                                          "actions": {"look": {}}}}}, zones=zones)
    _feed(m, [0xB0, 5, 10])
    assert _cmds(bus) == [("trigger_action", {"zone": "wander", "action": "look"})]


def test_note_off_not_dispatched():
    m, bus = _midi({"zones": {"wander": {"in_port": 1, "command": "note:0:60"}}})
    _feed(m, [0x80, 60, 0], port=1)  # note-off (velocity 0): excluded
    assert _cmds(bus) == []


# ---------------------------------------------------------------------------
# Legacy hardcoded protocol: fallback on the default port only.
# ---------------------------------------------------------------------------
def test_unmapped_falls_back_to_legacy_on_default_port():
    # CC1 value 2 -> "rest" (the legacy hardcoded protocol, default port).
    zones = {"rest": {"actions": {"rest": {}}}}
    m, bus = _midi({"zones": {}}, zones=zones)
    _feed(m, [0xB0, 1, 2])
    assert _cmds(bus) == [("trigger_action", {"zone": "rest", "action": "rest"})]


def test_legacy_mode_on_default_port():
    pytest.skip("CC2/set_mode is no longer part of the legacy MIDI protocol")


def test_legacy_only_on_default_port():
    # The legacy protocol does not fire on a non-default port.
    m, bus = _midi({"zones": {"wander": {"in_port": 1}}})
    _feed(m, [0xB0, 1, 2], port=1)  # CC1 value 2 = "rest", but on port 1 (not default)
    assert _cmds(bus) == []


def test_unmapped_note_off_dispatches_nothing():
    m, bus = _midi({"zones": {}})
    _feed(m, [0x80, 60, 0])  # note-off (velocity 0)
    assert _cmds(bus) == []


def test_unmapped_program_dispatches_nothing():
    # The legacy protocol has no program-change handling.
    m, bus = _midi({"zones": {}})
    _feed(m, [0xC0, 5])
    assert _cmds(bus) == []


# ---------------------------------------------------------------------------
# Learn capture (armed per-zone, scoped to the zone's port).
# ---------------------------------------------------------------------------
def test_learn_arms_and_captures_on_zone_port():
    m, bus = _midi({"zones": {"wander": {"in_port": 1, "command": None,
                                          "actions": {"look": None}}}})
    m.start_learn("wander", "look")
    assert m.is_learning is True
    _feed(m, [0xB0, 5, 10], port=1)  # on the zone's port
    cmds = _cmds(bus)
    assert len(cmds) == 1
    assert cmds[0][0] == "midi_learn"
    assert cmds[0][1] == {"key": "cc:5:10", "msg": [0xB0, 5, 10],
                           "zone": "wander", "action": "look", "nav": None}
    # Learn is single-shot: disarmed after capture.
    assert m.is_learning is False


def test_learn_scoped_to_port():
    m, bus = _midi({"zones": {"wander": {"in_port": 1}}})
    m.start_learn("wander")
    _feed(m, [0xB0, 5, 10], port=0)  # wrong port (default)
    assert _cmds(bus) == []  # not captured
    assert m.is_learning is True  # still armed


def test_learn_not_dispatched():
    # Arming learn must never move the robot: the captured message is not dispatched.
    m, bus = _midi({"zones": {"wander": {"in_port": 1, "command": "cc:5:10"}}})
    m.start_learn("wander")
    _feed(m, [0xB0, 5, 10], port=1)
    cmds = _cmds(bus)
    assert cmds[0][0] == "midi_learn"
    assert "goto_zone" not in [c[0] for c in cmds]


def test_learn_ignores_unmappable_message_stays_armed():
    m, bus = _midi({"zones": {"wander": {"in_port": 1}}})
    m.start_learn("wander")
    _feed(m, [0xA0, 0, 64], port=1)  # pitch bend: not a key
    assert _cmds(bus) == []
    assert m.is_learning is True
    _feed(m, [0xB0, 1, 5], port=1)  # the next mappable message is captured
    cmds = _cmds(bus)
    assert cmds[0][0] == "midi_learn"
    assert cmds[0][1]["key"] == "cc:1:5"


def test_stop_learn_disarms_without_capturing():
    m, bus = _midi({"zones": {"wander": {"in_port": 1}}})
    m.start_learn("wander")
    m.stop_learn()
    assert m.is_learning is False
    _feed(m, [0xB0, 5, 10], port=1)
    assert _cmds(bus) == []  # not capturing, and unmapped -> nothing dispatched


def test_stop_disarms_learn():
    m, bus = _midi({"zones": {"wander": {"in_port": 1}}})
    m.start_learn("wander")
    m.stop()
    assert m.is_learning is False
    _feed(m, [0xB0, 5, 10], port=1)
    assert _cmds(bus) == []


# ---------------------------------------------------------------------------
# Global nav cursor (a virtual cursor over the current zone's build_items rows).
#
# Rows for "init" in ``_NAV_ZONES``: actions (internal, sorted) idle, stand;
# exits rest (declared); reverse-exit wakeup (wakeup.exits = ["init"], not a
# forward exit of init → back=True).
# ---------------------------------------------------------------------------
_NAV_ZONES = {
    "init": {"actions": {"idle": {}, "stand": {"next": {"zone": "rest"}}}},
    "wakeup": {"actions": {"wake": {"next": {"zone": "init"}}}},
    "rest": {"actions": {"rest": {}}},
}


def test_nav_next_action_wraps_and_activates():
    m, bus = _midi({"nav": {"next_action": "cc:11:0"}}, zones=_NAV_ZONES)
    m._current_zone = "init"
    # The cursor starts at row 0 (idle); the next action row is stand.
    _feed(m, [0xB0, 11, 0])
    assert _cmds(bus) == [("play_action", {"action": "stand"})]
    # Wraps past the zone rows back to the first action row (idle).
    _feed(m, [0xB0, 11, 0])
    assert _cmds(bus) == [("play_action", {"action": "idle"})]


def test_nav_next_zone_gotos_zone():
    m, bus = _midi({"nav": {"next_zone": "cc:12:0"}}, zones=_NAV_ZONES)
    m._current_zone = "init"
    _feed(m, [0xB0, 12, 0])
    # The next zone row after the cursor is rest (a forward target).
    assert _cmds(bus) == [("trigger_action", {"zone": "rest", "action": "rest"})]


def test_nav_back_gotos_first_reverse_exit():
    m, bus = _midi({"nav": {"back": "cc:13:0"}}, zones=_NAV_ZONES)
    m._current_zone = "init"
    _feed(m, [0xB0, 13, 0])
    # The first reverse-target row is wakeup (back=True).
    assert _cmds(bus) == [("trigger_action", {"zone": "wakeup", "action": "wake"})]


def test_nav_retrigger_replays_current_action():
    m, bus = _midi({"nav": {"retrigger": "cc:14:0"}}, zones=_NAV_ZONES)
    m._current_zone = "init"
    m._current_action = "stand"
    _feed(m, [0xB0, 14, 0])
    # Re-activates the currently-playing action (stand), not the next row.
    assert _cmds(bus) == [("play_action", {"action": "stand"})]


def test_nav_dispatch_fires_on_any_port():
    # Nav is global: the key fires on a non-default port too (contrast with the
    # port-scoped per-zone dispatch).
    m, bus = _midi({"zones": {"wander": {"in_port": 1}},
                    "nav": {"next_zone": "cc:12:0"}}, zones=_NAV_ZONES)
    m._current_zone = "init"
    _feed(m, [0xB0, 12, 0], port=1)
    assert _cmds(bus) == [("trigger_action", {"zone": "rest", "action": "rest"})]


def test_nav_learn_captured_on_default_port():
    m, bus = _midi({"nav": {"next_action": None}}, zones=_NAV_ZONES)
    m.start_learn(nav="next_action")
    assert m.is_learning is True
    _feed(m, [0xB0, 11, 0])  # the default port
    cmds = _cmds(bus)
    assert len(cmds) == 1
    assert cmds[0][0] == "midi_learn"
    assert cmds[0][1] == {"key": "cc:11:0", "msg": [0xB0, 11, 0],
                           "zone": None, "action": None, "nav": "next_action"}
    # Learn is single-shot: disarmed after capture.
    assert m.is_learning is False


def test_nav_learn_not_dispatched():
    # While a nav learn is armed, the captured message is not dispatched.
    m, bus = _midi({"nav": {"next_action": "cc:11:0"}}, zones=_NAV_ZONES)
    m._current_zone = "init"
    m.start_learn(nav="next_action")
    _feed(m, [0xB0, 11, 0])
    cmds = _cmds(bus)
    assert cmds[0][0] == "midi_learn"
    assert "play_action" not in [c[0] for c in cmds]
    assert "goto_zone" not in [c[0] for c in cmds]


# ---------------------------------------------------------------------------
# reload(): re-read via the loader, swap the table, publish, reconnect.
# ---------------------------------------------------------------------------
def test_reload_swaps_table_and_publishes():
    zones = {"wander": {"actions": {"look": {}}}}
    bus = StateBus()
    m = MidiInput(bus, in_port=0, enabled=False,
                  zones_provider=lambda: zones,
                  midi_loader=lambda: {"enabled": True,
                                        "zones": {"wander": {"in_port": 1,
                                                              "command": "cc:1:5",
                                                              "actions": {"look": {}}}}})
    m._reconnect = lambda: None  # headless: no real port
    events = []
    bus.subscribe(lambda e, d: events.append(e))
    m.reload()
    assert m._data["zones"]["wander"]["command"] == "cc:1:5"
    assert Event.MIDI_CHANGED in events
    # The new table is now live: the mapped key dispatches.
    _feed(m, [0xB0, 1, 5], port=1)
    assert _cmds(bus) == [("trigger_action", {"zone": "wander", "action": "look"})]


def test_reload_failure_keeps_current_table():
    bus = StateBus()
    m = MidiInput(bus, in_port=0, enabled=False,
                  midi_loader=lambda: (_ for _ in ()).throw(OSError("boom")))
    m._reconnect = lambda: None
    m._data = {"enabled": True, "zones": {"wander": {"in_port": 1, "command": "cc:9:9"}}}
    events = []
    bus.subscribe(lambda e, d: events.append(e))
    m.reload()
    # The bad loader is ignored: the current table is kept and no MIDI_CHANGED is published.
    assert m._data["zones"]["wander"]["command"] == "cc:9:9"
    assert Event.MIDI_CHANGED not in events


# ---------------------------------------------------------------------------
# Per-port helpers + effective settings + list_ports.
# ---------------------------------------------------------------------------
def test_zone_in_port_defaults_to_default_port():
    m, _ = _midi({"zones": {"a": {"in_port": 1}, "b": {}}})
    assert m._zone_in_port("a") == 1
    assert m._zone_in_port("b") == 0  # null -> default port


def test_zones_by_port():
    m, _ = _midi({"zones": {"a": {"in_port": 1}, "b": {"in_port": 1},
                              "c": {"in_port": 2}}})
    assert sorted(m._zones_by_port(1)) == ["a", "b"]
    assert m._zones_by_port(2) == ["c"]


def test_in_ports_union():
    # The default port plus every zone's in_port (a null in_port uses the default).
    m, _ = _midi({"zones": {"a": {"in_port": 0}, "b": {"in_port": 2}, "c": {}}})
    assert m._in_ports() == {0, 2}


def test_effective_enabled_combines_hard_and_soft():
    m, _ = _midi({"enabled": True, "zones": {}})
    m.enabled = True  # the hard switch (constructor) is on
    assert m._effective_enabled() is True
    m._data = {"enabled": False, "zones": {}}
    assert m._effective_enabled() is False  # soft switch (midi.json) off
    m._data = {"enabled": True, "zones": {}}
    m.enabled = False  # the hard switch overrides everything
    assert m._effective_enabled() is False


def test_list_ports_with_fake_rtmidi():
    # Real rtmidi returns the port names as plain strings.
    fake = types.ModuleType("rtmidi")

    class _MidiIn:
        def get_ports(self):
            return ["In A", "In B"]

    fake.MidiIn = _MidiIn
    saved = sys.modules.get("rtmidi")
    sys.modules["rtmidi"] = fake
    try:
        m, _ = _midi({"zones": {}})
        assert m.list_ports() == [{"index": 0, "name": "In A"},
                                   {"index": 1, "name": "In B"}]
    finally:
        if saved is not None:
            sys.modules["rtmidi"] = saved
        else:
            del sys.modules["rtmidi"]


def test_current_data_returns_loaded_table():
    data = {"enabled": True,
            "zones": {"wander": {"in_port": 1, "command": "cc:1:5", "actions": {}}}}
    m, _ = _midi(data)
    assert m.current_data() == data


# ---------------------------------------------------------------------------
# Runner.
# ---------------------------------------------------------------------------
def main():
    failures = 0
    tests = sorted([(k, v) for k, v in list(globals().items())
                    if k.startswith("test_") and callable(v)],
                   key=lambda kv: kv[0])
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
