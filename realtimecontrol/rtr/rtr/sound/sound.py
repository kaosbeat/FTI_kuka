"""Sound adapter (MIDI-out).

Sound is MIDI-controlled: the core does not synthesise audio, it sends MIDI messages
that an external sound engine (Max, Ableton, a script, a hardware unit) turns into
sound. This adapter maps the core's state (zone / mode / action) to MIDI messages and
sends them on a MIDI-out port.

The mapping is a small, editable table so the sound design lives in one place. When
no MIDI-out device is available the adapter degrades to logging, so the core still
runs (e.g. in sim or on a machine without the sound rig).
"""

from typing import Optional

from ..core.bus import StateBus
from ..core.commands import Event, Snapshot

# Default mapping: zone -> (channel, note, velocity). Edit to taste.
ZONE_NOTES = {
    "init": (0, 48, 60),
    "rest": (0, 36, 50),
    "wakeup": (0, 52, 70),
    "stretch": (0, 55, 75),
    "wander": (0, 60, 80),
    "wildwander": (0, 64, 90),
}

# Mode -> continuous controller (channel, cc, value).
MODE_CCS = {
    "wander": (0, 70, 32),
    "random": (0, 70, 64),
    "action": (0, 70, 96),
    "track": (0, 70, 128),
    "hold": (0, 70, 0),
}


class Sound:
    """Sends state-driven MIDI messages to a sound engine."""

    def __init__(self, bus: StateBus, out_port: Optional[int] = None,
                 out_device: Optional[str] = None, enabled: bool = True):
        self.bus = bus
        self.enabled = enabled
        self._out = None
        self._last_zone = None
        self._last_mode = None
        if enabled:
            self._connect(out_port, out_device)
        bus.subscribe(self.on_event)

    def _connect(self, out_port: Optional[int], out_device: Optional[str]) -> None:
        try:
            import rtmidi
        except ImportError:
            print("[sound] rtmidi not installed; sound disabled")
            self.enabled = False
            return
        try:
            self._out = rtmidi.MidiOut()
            if out_device:
                self._out.open_string_outport(out_device)
            elif out_port is not None:
                self._out.open_port(out_port)
            else:
                # No explicit port: pick the first available out device, if any.
                count = self._out.get_port_count()
                if count == 0:
                    print("[sound] no MIDI out devices; sound disabled")
                    self.enabled = False
                    return
                self._out.open_port(0)
        except Exception as exc:  # noqa: BLE001 - sound must not kill the core
            print(f"[sound] could not open MIDI out: {exc}")
            self.enabled = False
            self._out = None

    def on_event(self, event: Event, data) -> None:
        if not self.enabled:
            return
        if event == Event.SNAPSHOT:
            self._on_snapshot(data)

    def _on_snapshot(self, snap: Snapshot) -> None:
        if snap.zone != self._last_zone:
            self._last_zone = snap.zone
            ch, note, vel = ZONE_NOTES.get(snap.zone, (0, 60, 64))
            self._note_off(ch, note)
            self._note_on(ch, note, vel)
        if snap.mode != self._last_mode:
            self._last_mode = snap.mode
            ch, cc, val = MODE_CCS.get(snap.mode, (0, 70, 0))
            self._cc(ch, cc, val)

    def _send(self, msg) -> None:
        if self._out is None:
            return
        try:
            self._out.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            print(f"[sound] send error: {exc}")

    def _note_on(self, ch: int, note: int, vel: int) -> None:
        self._send([0x90 | ch, note, vel])

    def _note_off(self, ch: int, note: int) -> None:
        self._send([0x80 | ch, note, 0])

    def _cc(self, ch: int, cc: int, val: int) -> None:
        self._send([0xB0 | ch, cc, val])

    def close(self) -> None:
        if self._out is not None:
            try:
                self._out.close_port()
            except Exception:  # noqa: BLE001
                pass
            self._out = None


def make_sound(bus: StateBus, enabled: bool, out_port=None, out_device=None) -> Sound:
    return Sound(bus, out_port=out_port, out_device=out_device, enabled=enabled)
