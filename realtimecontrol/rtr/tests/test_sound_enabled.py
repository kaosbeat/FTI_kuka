"""Test: the Sound adapter's ``enabled`` soft switch (the master MIDI-on/off, out side).

The out/sound side of MIDI is gated by two switches:

- a **hard** switch (the constructor ``enabled`` — ``cfg.enable_sound``), and
- a **soft** switch (the ``sound.json`` ``enabled`` field — hot-reloaded from the
  editor's master MIDI on/off button).

The effective gate is ``hard AND soft`` (:meth:`Sound._effective_enabled`), mirroring
the MIDI-in adapter (:mod:`rtr.io.midi`). This test asserts, with a fake rtmidi (no
hardware):

- ``validate_sound_data`` accepts a bool ``enabled``, defaults to ``True`` when absent,
  and rejects a non-bool.
- ``builtin_sound_data`` ships ``"enabled": True``.
- ``_effective_enabled`` is the constructor flag AND the data flag.
- With the soft switch off, ``on_event`` sends no MIDI at all (zone / action / pose).
- With the soft switch off, ``reload`` closes every open out port (no MIDI can leak).
- Turning the soft switch back on (a new table) resumes normal sending.

Run directly (no pytest needed)::

    python tests/test_sound_enabled.py

or under pytest::

    pytest tests/test_sound_enabled.py
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
from rtr.sound import Sound, builtin_sound_data, validate_sound_data


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
    """A minimal snapshot for driving ``on_event`` (zone/mode/action only)."""
    return Snapshot(zone=zone, mode=mode, action=action,
                    joint_pose=[0.0] * 6, cart_pose=[0.0] * 3,
                    target_pose=target_pose if target_pose is not None else [0.0] * 6,
                    speed=20.0, moving=False, action_index=action_index)


def _base_table(enabled):
    """A two-zone table with an ``enabled`` flag."""
    return {
        "enabled": enabled,
        "channel": 0,
        "zones": {
            "a": {"out_port": 0, "command": {"note": 10, "velocity": 50}},
            "b": {"out_port": 1, "command": {"note": 20, "velocity": 60}},
        },
        "actions": {"a": {"step": {"start": {"cc": 70, "value": 32}, "poses": []}}},
    }


# ---------------------------------------------------------------------------
# validate_sound_data: the ``enabled`` field.
# ---------------------------------------------------------------------------
def test_validate_accepts_enabled_bool():
    data = validate_sound_data({"enabled": False, "channel": 0, "zones": {},
                                 "modes": {}, "actions": {}})
    assert data["enabled"] is False
    data2 = validate_sound_data({"enabled": True, "channel": 0, "zones": {},
                                  "modes": {}, "actions": {}})
    assert data2["enabled"] is True


def test_validate_enabled_defaults_to_true_when_absent():
    data = validate_sound_data({"channel": 0, "zones": {}, "modes": {}, "actions": {}})
    assert data.get("enabled", True) is True  # absent → treated as on


def test_validate_rejects_non_bool_enabled():
    for bad in (0, 1, "true", None, 1.0):
        try:
            validate_sound_data({"enabled": bad, "channel": 0, "zones": {},
                                   "modes": {}, "actions": {}})
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for enabled={bad!r}")


def test_builtin_sound_data_includes_enabled():
    data = builtin_sound_data()
    assert data["enabled"] is True


# ---------------------------------------------------------------------------
# _effective_enabled: hard (constructor) AND soft (data) switch.
# ---------------------------------------------------------------------------
def test_effective_enabled_requires_both_switches():
    # (hard, soft) → effective
    cases = [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ]
    for hard, soft, expected in cases:
        s, _ = _sound(_base_table(soft), enabled=hard)
        assert s._effective_enabled() is expected, f"hard={hard} soft={soft}"


# ---------------------------------------------------------------------------
# Soft switch off → no MIDI sent (zone / action / pose).
# ---------------------------------------------------------------------------
def test_soft_switch_off_sends_nothing():
    FakeMidiOut.instances = []
    with _fake_rtmidi():
        s, _ = _sound(_base_table(False))
        # Zone entry + action start + a pose step: none may be sent while disabled.
        s.on_event(Event.SNAPSHOT, _snap(zone="a", action="step", action_index=0))
        s.on_event(Event.SNAPSHOT, _snap(zone="b"))
        s.on_event(Event.SNAPSHOT, _snap(zone="a", action="step", action_index=1))
        assert s._open_outs == {}  # no out port was ever opened
        total = sum(len(o.sent) for o in FakeMidiOut.instances)
        assert total == 0


# ---------------------------------------------------------------------------
# Soft switch off → reload closes every open out port (nothing can leak).
# ---------------------------------------------------------------------------
def test_soft_switch_off_reload_closes_all_out_ports():
    FakeMidiOut.instances = []
    with _fake_rtmidi():
        s, holder = _sound(_base_table(True))
        s._get_out("a")  # open port 0 lazily
        assert (0, None) in s._open_outs
        # Flip the soft switch off and reload: all open ports must close.
        holder["table"] = _base_table(False)
        s.reload()
        assert s._open_outs == {}
        assert FakeMidiOut.instances[0].closed is True


# ---------------------------------------------------------------------------
# Turning the soft switch back on resumes normal sending.
# ---------------------------------------------------------------------------
def test_soft_switch_on_resumes_sending():
    FakeMidiOut.instances = []
    with _fake_rtmidi():
        s, holder = _sound(_base_table(False))
        # Disabled: nothing sent.
        s.on_event(Event.SNAPSHOT, _snap(zone="a"))
        assert s._open_outs == {}
        # Re-enable via a new table and reload: the same snapshot path sends again.
        holder["table"] = _base_table(True)
        s.reload()
        s._last_zone = None  # force a fresh zone entry
        s.on_event(Event.SNAPSHOT, _snap(zone="a"))
        assert (0, None) in s._open_outs
        assert s._open_outs[(0, None)].sent == [[0x90, 10, 50]]


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
