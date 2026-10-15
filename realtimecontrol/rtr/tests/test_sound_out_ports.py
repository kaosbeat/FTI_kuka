"""Test: the Sound adapter's per-zone MIDI-out device routing (out_port per zone).

Each ``sound.json`` zone is ``{out_port, command}``: ``out_port`` is the MIDI-out device
index for that zone (``null`` → the global fallback) and ``command`` is the zone-entry
message. This test asserts, with a fake rtmidi (no hardware):

- ``validate_sound_data`` accepts the per-zone ``{out_port, command}`` shape, normalizes
  a legacy bare-message zone entry in place, and rejects bad per-zone / global out fields.
- The effective out spec prefers a zone's own ``out_port``, then the global ``out_device``
  (name), then the global ``out_port``, then the constructor default, then auto.
- Zone-entry / note-off / mode-CC messages route to the active zone's out device (one
  ``MidiOut`` per distinct device, opened lazily and cached).
- ``_reconcile_outs`` closes stale out ports on reload; ``close`` closes all.
- ``list_out_ports`` returns ``{index, name}`` entries.

Run directly (no pytest needed)::

    python tests/test_sound_out_ports.py

or under pytest::

    pytest tests/test_sound_out_ports.py
"""

import os
import sys
import types

# Make the package importable when run directly.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from contextlib import contextmanager

from rtr.core.bus import StateBus
from rtr.core.commands import Event, Snapshot
from rtr.sound import Sound, validate_sound_data


# ---------------------------------------------------------------------------
# A fake rtmidi so no real MIDI hardware is touched (per-test, save/restore).
# ---------------------------------------------------------------------------
class FakeMidiOut:
    """Records opens/sends per instance; reports two fake out ports."""

    instances = []

    def __init__(self):
        self.opened = None  # ("port", idx) or ("string", name)
        self.sent = []
        self.closed = False
        FakeMidiOut.instances.append(self)

    def get_port_count(self):
        return 2

    def open_port(self, idx):
        self.opened = ("port", idx)

    def open_string_outport(self, name):
        self.opened = ("string", name)

    def close_port(self):
        self.closed = True

    def get_ports(self):
        return ["Fake:Port-0", "Fake:Port-1"]

    def send_message(self, msg):
        self.sent.append(list(msg))


_fake_module = types.ModuleType("rtmidi")
_fake_module.MidiOut = FakeMidiOut


@contextmanager
def _fake_rtmidi():
    """Swap in the fake rtmidi for the duration of the block (restores on exit)."""
    saved = sys.modules.get("rtmidi")
    sys.modules["rtmidi"] = _fake_module
    try:
        yield
    finally:
        if saved is not None:
            sys.modules["rtmidi"] = saved
        else:
            sys.modules.pop("rtmidi", None)


def _sound(table, out_port=None, out_device=None, enabled=True):
    """A Sound over a mutable table; returns (sound, table_holder)."""
    holder = {"table": table}
    bus = StateBus()
    s = Sound(bus, sound_loader=lambda: holder["table"], out_port=out_port,
              out_device=out_device, enabled=enabled)
    return s, holder


def _snap(zone="init", mode=None, action=None, action_index=None, target_pose=None):
    """A minimal snapshot for driving ``on_event`` (zone/mode/action only).

    ``action_index`` / ``target_pose`` are optional; the per-pose MIDI test drives them
    to verify the pose note keys on the anchor index, not the target pose.
    """
    return Snapshot(zone=zone, mode=mode, action=action,
                    joint_pose=[0.0] * 6, cart_pose=[0.0] * 3,
                    target_pose=target_pose if target_pose is not None else [0.0] * 6,
                    speed=20.0, moving=False, action_index=action_index)


# ---------------------------------------------------------------------------
# validate_sound_data: the per-zone {out_port, command} shape.
# ---------------------------------------------------------------------------
def test_validate_per_zone_accepts_wrapped():
    data = validate_sound_data({
        "channel": 0,
        "zones": {"wander": {"out_port": 1, "command": {"note": 60, "velocity": 80}}},
        "modes": {}, "actions": {}})
    assert data["zones"]["wander"]["out_port"] == 1
    assert data["zones"]["wander"]["command"] == {"note": 60, "velocity": 80}


def test_validate_normalizes_legacy_bare_entry_in_place():
    table = {"channel": 0, "zones": {"wander": {"note": 60, "velocity": 80}},
             "modes": {}, "actions": {}}
    data = validate_sound_data(table)
    # The original object is normalized in place (legacy bare entry → wrapper).
    assert table["zones"]["wander"] == {"out_port": None,
                                        "command": {"note": 60, "velocity": 80}}
    assert data["zones"]["wander"]["out_port"] is None


def test_validate_rejects_bad_per_zone_out_port():
    for bad in ({"out_port": -1}, {"out_port": 1.5}, {"out_port": "x"}, {"out_port": True}):
        tbl = {"channel": 0,
               "zones": {"z": {"out_port": bad["out_port"],
                                "command": {"note": 1, "velocity": 1}}},
               "modes": {}, "actions": {}}
        try:
            validate_sound_data(tbl)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for per-zone out_port={bad['out_port']!r}")


def test_validate_rejects_bad_per_zone_command():
    try:
        validate_sound_data({"channel": 0,
                              "zones": {"z": {"out_port": None, "command": {"note": 200}}},
                              "modes": {}, "actions": {}})
    except ValueError:
        return
    raise AssertionError("expected ValueError for an out-of-range command")


# ---------------------------------------------------------------------------
# validate_sound_data: the legacy modes block → actions["rest"] migration.
# ---------------------------------------------------------------------------
def test_validate_migrates_legacy_modes_into_rest():
    table = {
        "channel": 0,
        "zones": {"rest": {"out_port": None, "command": {"note": 36, "velocity": 50}}},
        "modes": {"wander": {"cc": 70, "value": 32}, "random": {"cc": 70, "value": 64}},
        "actions": {"rest": {"custom": {"start": {"cc": 90, "value": 10}, "poses": []}}},
    }
    first = validate_sound_data(table)
    # The legacy modes block is dropped.
    assert "modes" not in first
    # Each mode becomes a rest-zone action {start: <msg>, poses: []}.
    assert first["actions"]["rest"]["wander"] == {"start": {"cc": 70, "value": 32},
                                                  "poses": []}
    assert first["actions"]["rest"]["random"] == {"start": {"cc": 70, "value": 64},
                                                  "poses": []}
    # Pre-existing actions["rest"] entries are preserved.
    assert first["actions"]["rest"]["custom"] == {"start": {"cc": 90, "value": 10},
                                                  "poses": []}
    # The migration is idempotent: a second validation is a no-op.
    assert validate_sound_data(first) == first


# ---------------------------------------------------------------------------
# validate_sound_data: the global out_port / out_device fallbacks (unchanged).
# ---------------------------------------------------------------------------
def test_validate_accepts_global_out_fields():
    data = validate_sound_data({"channel": 0, "out_port": 2, "out_device": "Foo",
                                "zones": {}, "modes": {}, "actions": {}})
    assert data["out_port"] == 2 and data["out_device"] == "Foo"


def test_validate_omits_out_fields():
    # Absent global out_port / out_device is fine (zones fall back to auto).
    data = validate_sound_data({"channel": 0, "zones": {}, "modes": {}, "actions": {}})
    assert "out_port" not in data and "out_device" not in data


def test_validate_rejects_bad_global_out_port():
    for bad in ({"out_port": -1}, {"out_port": 1.5}, {"out_port": "x"}, {"out_port": True}):
        try:
            validate_sound_data({"channel": 0, "zones": {}, "modes": {}, "actions": {}, **bad})
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for global out_port={bad['out_port']!r}")


def test_validate_rejects_bad_global_out_device():
    try:
        validate_sound_data({"channel": 0, "zones": {}, "modes": {}, "actions": {},
                              "out_device": 5})
    except ValueError:
        return
    raise AssertionError("expected ValueError for out_device=5")


# ---------------------------------------------------------------------------
# Effective out spec: zone out_port > global out_device > global out_port > default > auto.
# ---------------------------------------------------------------------------
def test_out_spec_prefers_zone_out_port():
    s, _ = _sound({"channel": 0,
                   "zones": {"a": {"out_port": 1, "command": {"note": 1, "velocity": 1}},
                              "b": {"out_port": None, "command": {"note": 2, "velocity": 2}}},
                   "modes": {}, "actions": {}, "out_port": 5, "out_device": "Dev"})
    assert s._out_spec("a") == (1, None)
    # b has null out_port → falls back to the global out_device (name).
    assert s._out_spec("b") == (None, "Dev")


def test_out_spec_falls_back_to_global_out_port():
    s, _ = _sound({"channel": 0,
                   "zones": {"b": {"out_port": None, "command": {"note": 2, "velocity": 2}}},
                   "modes": {}, "actions": {}, "out_port": 5})
    assert s._out_spec("b") == (5, None)


def test_out_spec_falls_back_to_constructor_default():
    s, _ = _sound({"channel": 0,
                   "zones": {"b": {"out_port": None, "command": {"note": 2, "velocity": 2}}},
                   "modes": {}, "actions": {}}, out_port=7)
    assert s._out_spec("b") == (7, None)


def test_out_spec_auto_when_unset():
    s, _ = _sound({"channel": 0,
                   "zones": {"b": {"out_port": None, "command": {"note": 2, "velocity": 2}}},
                   "modes": {}, "actions": {}})
    assert s._out_spec("b") == (None, None)


# ---------------------------------------------------------------------------
# Per-zone routing (fake rtmidi, no hardware).
# ---------------------------------------------------------------------------
def test_per_zone_routing_distinct_ports():
    FakeMidiOut.instances = []
    with _fake_rtmidi():
        s, _ = _sound({"channel": 0,
                       "zones": {"a": {"out_port": 0, "command": {"note": 10, "velocity": 50}},
                                  "b": {"out_port": 1, "command": {"note": 20, "velocity": 60}}},
                       "modes": {}, "actions": {}})
        s.on_event(Event.SNAPSHOT, _snap(zone="a"))
        s.on_event(Event.SNAPSHOT, _snap(zone="b"))
        assert set(s._open_outs) == {(0, None), (1, None)}
        # a's note-on, then a's note-off (on the way out) → port 0.
        assert s._open_outs[(0, None)].sent == [[0x90, 10, 50], [0x80, 10, 0]]
        # b's note-on → port 1.
        assert s._open_outs[(1, None)].sent == [[0x90, 20, 60]]


def test_mode_cc_sent_as_rest_action():
    # The mode CCs are no longer a separate ``modes`` block: they are actions on the
    # ``rest`` zone, sent as the action's ``start`` message on playback.
    FakeMidiOut.instances = []
    with _fake_rtmidi():
        s, _ = _sound({"channel": 0,
                       "zones": {"rest": {"out_port": 0, "command": {"note": 36, "velocity": 50}},
                                  "wander": {"out_port": 1, "command": {"note": 20, "velocity": 60}}},
                       "actions": {"rest": {"wander": {"start": {"cc": 70, "value": 32}, "poses": []}}}})
        s.on_event(Event.SNAPSHOT, _snap(zone="rest"))
        s.on_event(Event.SNAPSHOT, _snap(zone="rest", action="wander"))
        # Only the rest zone's out (port 0) is opened; the mode CC lands on it.
        assert set(s._open_outs) == {(0, None)}
        assert s._open_outs[(0, None)].sent[-1] == [0xB0, 70, 32]


def test_mode_change_sends_nothing():
    # A mode-only change (zone/action/pose unchanged) sends no CC: the legacy
    # mode-change branch is gone, and mode CCs are now rest-zone actions.
    FakeMidiOut.instances = []
    with _fake_rtmidi():
        s, _ = _sound({"channel": 0,
                       "zones": {"rest": {"out_port": 0,
                                           "command": {"note": 36, "velocity": 50}}},
                       "actions": {"rest": {"wander": {"start": {"cc": 70, "value": 32},
                                                    "poses": []}}}})
        s.on_event(Event.SNAPSHOT, _snap(zone="rest", mode="wander", action="wander"))
        sent = list(s._open_outs[(0, None)].sent)
        # Only the mode changes; nothing new is sent.
        s.on_event(Event.SNAPSHOT, _snap(zone="rest", mode="action", action="wander"))
        assert s._open_outs[(0, None)].sent == sent


def test_per_pose_midi_keys_on_action_index():
    # Per-pose MIDI fires when the machine's anchor index (action_index) advances.
    # It keys on action_index, NOT the target pose, so a wiggle action — whose target
    # moves every tick — does not misalign the pose notes.
    FakeMidiOut.instances = []
    with _fake_rtmidi():
        s, _ = _sound({"channel": 0,
                       "zones": {"a": {"out_port": 0,
                                        "command": {"note": 5, "velocity": 40}}},
                       "actions": {"a": {"wiggle": {
                           "poses": [{"note": 10, "velocity": 50},
                                      {"note": 11, "velocity": 50},
                                      {"note": 12, "velocity": 50}]}}}})
        # Action start at anchor 0: zone-entry note (5) + first pose note (10).
        s.on_event(Event.SNAPSHOT, _snap(zone="a", action="wiggle", action_index=0))
        sent = list(s._open_outs[(0, None)].sent)
        assert [0x90, 10, 50] in sent
        # Target pose moves every tick (wiggle) but the anchor stays at 0: no new note.
        s.on_event(Event.SNAPSHOT, _snap(zone="a", action="wiggle", action_index=0,
                                          target_pose=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]))
        assert s._open_outs[(0, None)].sent == sent
        # Anchor advances to 1 → pose[1] (note 11).
        s.on_event(Event.SNAPSHOT, _snap(zone="a", action="wiggle", action_index=1))
        assert s._open_outs[(0, None)].sent[-1] == [0x90, 11, 50]


def test_reconcile_closes_stale_specs_on_reload():
    FakeMidiOut.instances = []
    with _fake_rtmidi():
        s, holder = _sound({"channel": 0,
                            "zones": {"a": {"out_port": 0, "command": {"note": 10, "velocity": 50}}},
                            "modes": {}, "actions": {}})
        out0 = s._get_out("a")
        assert (0, None) in s._open_outs
        # Reload with a table that no longer references port 0.
        holder["table"] = {"channel": 0,
                             "zones": {"a": {"out_port": 1,
                                              "command": {"note": 11, "velocity": 50}}},
                             "modes": {}, "actions": {}}
        s.reload()
        assert (0, None) not in s._open_outs  # closed (stale)
        assert getattr(out0, "closed", False) is True
        assert (1, None) not in s._open_outs  # not opened yet (lazy)


def test_close_closes_all():
    FakeMidiOut.instances = []
    with _fake_rtmidi():
        s, _ = _sound({"channel": 0,
                       "zones": {"a": {"out_port": 0, "command": {"note": 10, "velocity": 50}},
                                  "b": {"out_port": 1, "command": {"note": 20, "velocity": 60}}},
                       "modes": {}, "actions": {}})
        s._get_out("a")
        s._get_out("b")
        assert len(s._open_outs) == 2
        s.close()
        assert s._open_outs == {}


# ---------------------------------------------------------------------------
# list_out_ports: {index, name} entries (fake rtmidi).
# ---------------------------------------------------------------------------
def test_list_out_ports_shape():
    with _fake_rtmidi():
        s, _ = _sound({"channel": 0, "zones": {}, "modes": {}, "actions": {}})
        assert s.list_out_ports() == [
            {"index": 0, "name": "Fake:Port-0"},
            {"index": 1, "name": "Fake:Port-1"},
        ]


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
