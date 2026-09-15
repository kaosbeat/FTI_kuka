"""Sound adapter (MIDI-out).

Sound is MIDI-controlled: the core does not synthesise audio, it sends MIDI messages
that an external sound engine (Max, Ableton, a script, a hardware unit) turns into
sound. This adapter maps the core's state (zone / mode / action / per-pose steps) to
MIDI messages and sends them on a MIDI-out port.

The mapping is **data-driven**: it lives in ``sound.json`` (edited in ``editor.html``,
served/saved over ``/api/sound``, hot-reloaded). The built-in :data:`ZONE_NOTES` and
:data:`MODE_CCS` tables remain the fallback when the file is missing or corrupt.

A **MIDI message** is one of:

- ``{"note": n, "velocity": v}`` — a note-on,
- ``{"cc": c, "value": v}``      — a control change,
- ``{"program": p}``             — a program change.

The global ``channel`` (0-15) is applied when a message is converted to bytes.

When no MIDI-out device is available the adapter degrades to **logging** each message
it would send (e.g. ``[sound] note ch0 48 vel60``), so the mapping is testable without
hardware. The core keeps running either way.
"""

import json
from typing import Any, Dict, Optional

from ..core.bus import StateBus
from ..core.commands import Event, Snapshot

# ---------------------------------------------------------------------------
# Built-in fallback mapping.
#
# Kept as (channel, note, velocity) / (channel, cc, value) tuples. ``sound.json`` is
# generated from these (see that file); they remain the fallback when the file is
# missing or corrupt.
# ---------------------------------------------------------------------------

# Default mapping: zone -> (channel, note, velocity).
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
    "track": (0, 70, 127),
    "hold": (0, 70, 0),
}


def builtin_sound_data() -> dict:
    """Build a sound data table from the built-in :data:`ZONE_NOTES` / :data:`MODE_CCS`.

    This is the fallback shape of ``sound.json`` (see :func:`validate_sound_data`).
    """
    channel = next(iter(ZONE_NOTES.values()))[0]
    return {
        "channel": channel,
        "zones": {name: {"note": n, "velocity": v} for name, (_, n, v) in ZONE_NOTES.items()},
        "modes": {name: {"cc": c, "value": v} for name, (_, c, v) in MODE_CCS.items()},
        "actions": {},
    }


# ---------------------------------------------------------------------------
# Loading + validation of the on-disk data file (``sound.json``).
# ---------------------------------------------------------------------------

def _is_int_in(v, lo: int, hi: int) -> bool:
    """True for a real int (bool excluded) within ``[lo, hi]``."""
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def validate_message(msg: Any, where: str) -> dict:
    """Validate a single MIDI message. Returns ``msg`` or raises :class:`ValueError`.

    A message has exactly one form: ``note``+``velocity``, ``cc``+``value``, or
    ``program``. Mixing forms is rejected.
    """
    if not isinstance(msg, dict):
        raise ValueError(f"{where}: message must be an object")
    has_note = ("note" in msg or "velocity" in msg)
    has_cc = ("cc" in msg or "value" in msg)
    has_prog = ("program" in msg)
    if sum([has_note, has_cc, has_prog]) > 1:
        raise ValueError(f"{where}: message must not mix note/cc/program fields")
    if has_note:
        if not (_is_int_in(msg.get("note"), 0, 127)
                and _is_int_in(msg.get("velocity"), 0, 127)):
            raise ValueError(f"{where}: note-on needs note (0-127) and velocity (0-127)")
    elif has_cc:
        if not (_is_int_in(msg.get("cc"), 0, 127)
                and _is_int_in(msg.get("value"), 0, 127)):
            raise ValueError(f"{where}: cc message needs cc (0-127) and value (0-127)")
    elif has_prog:
        if not _is_int_in(msg.get("program"), 0, 127):
            raise ValueError(f"{where}: program change needs program (0-127)")
    else:
        raise ValueError(f"{where}: message needs note+velocity, cc+value, or program")
    return msg


def validate_sound_data(data: Any) -> dict:
    """Validate a full sound data table. Returns ``data`` or raises :class:`ValueError`.

    Shape: ``{channel, zones, modes, actions}`` where ``zones``/``modes`` map a name to
    a MIDI message, and ``actions`` maps a name to ``{start?, poses?}`` (a message and/or
    a list of messages).
    """
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    channel = data.get("channel", 0)
    if not _is_int_in(channel, 0, 15):
        raise ValueError("'channel' must be an int 0-15")
    for key in ("zones", "modes", "actions"):
        v = data.get(key, {})
        if not isinstance(v, dict):
            raise ValueError(f"'{key}' must be an object")
    for name, msg in data["zones"].items():
        validate_message(msg, f"zones[{name!r}]")
    for name, msg in data["modes"].items():
        validate_message(msg, f"modes[{name!r}]")
    for name, act in data["actions"].items():
        if not isinstance(act, dict):
            raise ValueError(f"actions[{name!r}]: must be an object")
        if "start" in act:
            validate_message(act["start"], f"actions[{name!r}].start")
        poses = act.get("poses")
        if poses is not None:
            if not isinstance(poses, list):
                raise ValueError(f"actions[{name!r}].poses: must be a list")
            for i, msg in enumerate(poses):
                validate_message(msg, f"actions[{name!r}].poses[{i}]")
    return data


def load_sound_data(path: str) -> dict:
    """Load and validate the on-disk sound file. Raises ``OSError``/``ValueError``."""
    with open(path, "r", encoding="utf-8") as f:
        return validate_sound_data(json.load(f))


# ---------------------------------------------------------------------------
# The adapter.
# ---------------------------------------------------------------------------

class Sound:
    """Sends state-driven MIDI messages to a sound engine.

    The mapping is loaded from a callable ``sound_loader`` (which returns a validated
    sound data table). On each snapshot the adapter detects zone / mode / action
    changes and target-pose steps and sends the mapped MIDI messages.
    """

    def __init__(self, bus: StateBus, sound_loader, out_port: Optional[int] = None,
                 out_device: Optional[str] = None, enabled: bool = True):
        self.bus = bus
        self.enabled = enabled
        self._sound_loader = sound_loader
        self._data: Dict[str, Any] = {}
        self._out = None
        self._last_zone: Optional[str] = None
        self._last_mode: Optional[str] = None
        self._last_action: Optional[str] = None
        self._last_target: Optional[list] = None
        self._pose_idx = 0
        if enabled:
            self.reload()
            self._connect(out_port, out_device)
        bus.subscribe(self.on_event)

    # ------------------------------------------------------------------
    # Mapping (de)serialisation.
    # ------------------------------------------------------------------
    def current_data(self) -> dict:
        """The currently loaded sound data table (for the HTTP GET fallback)."""
        return self._data

    def reload(self) -> None:
        """Re-read the sound data via the loader.

        On failure keep the current table (logged); on success swap the table and
        publish :data:`Event.SOUND_CHANGED`.
        """
        try:
            data = self._sound_loader()
        except (OSError, ValueError) as exc:
            print(f"[sound] reload failed ({exc}); keeping current mapping")
            return
        self._data = data
        self.bus.publish(Event.SOUND_CHANGED, "sound")
        print(f"[sound] mapping reloaded: channel={data.get('channel', 0)} "
              f"zones={len(data.get('zones', {}))} "
              f"modes={len(data.get('modes', {}))} "
              f"actions={len(data.get('actions', {}))}")

    # ------------------------------------------------------------------
    # MIDI-out connection.
    # ------------------------------------------------------------------
    def _connect(self, out_port: Optional[int], out_device: Optional[str]) -> None:
        try:
            import rtmidi
        except ImportError:
            print("[sound] rtmidi not installed; logging only")
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
                    print("[sound] no MIDI out devices; logging only")
                    self._out = None
                    return
                self._out.open_port(0)
        except Exception as exc:  # noqa: BLE001 - sound must not kill the core
            print(f"[sound] could not open MIDI out: {exc}; logging only")
            self._out = None

    # ------------------------------------------------------------------
    # Event handling.
    # ------------------------------------------------------------------
    def on_event(self, event: Event, data) -> None:
        if not self.enabled:
            return
        if event == Event.SNAPSHOT:
            self._on_snapshot(data)

    def _on_snapshot(self, snap: Snapshot) -> None:
        data = self._data
        zones = data.get("zones", {})
        modes = data.get("modes", {})
        actions = data.get("actions", {})

        # 1. zone entry: stop the previous zone's note, start the new zone's message.
        if snap.zone != self._last_zone:
            prev = self._last_zone
            self._last_zone = snap.zone
            if prev is not None:
                prev_msg = zones.get(prev)
                if isinstance(prev_msg, dict) and "note" in prev_msg:
                    self._note_off(prev_msg)
            if snap.zone in zones:
                self._send_msg(zones[snap.zone])

        # 2. mode change: send the mode's CC message.
        if snap.mode != self._last_mode:
            self._last_mode = snap.mode
            if snap.mode in modes:
                self._send_msg(modes[snap.mode])

        # 3. action start: send the action's start message (or its first pose).
        if snap.action != self._last_action:
            self._last_action = snap.action
            self._pose_idx = 0
            if snap.action is not None:
                act = actions.get(snap.action)
                if isinstance(act, dict):
                    start = act.get("start")
                    if isinstance(start, dict):
                        self._send_msg(start)
                    else:
                        poses = act.get("poses")
                        if isinstance(poses, list) and poses:
                            self._send_msg(poses[0])

        # 4. per-pose step: while in action mode, a target-pose change advances the
        #    internal pose index. (Heuristic: the snapshot has no explicit pose index;
        #    exact index tracking is a later extension.)
        if (snap.mode == "action" and snap.action is not None
                and snap.target_pose != self._last_target):
            self._pose_idx += 1
            act = actions.get(snap.action)
            poses = act.get("poses") if isinstance(act, dict) else None
            if isinstance(poses, list) and poses:
                self._send_msg(poses[self._pose_idx % len(poses)])

        self._last_target = list(snap.target_pose)

    # ------------------------------------------------------------------
    # MIDI sending (degrades to logging when no device is open).
    # ------------------------------------------------------------------
    def _channel(self) -> int:
        ch = self._data.get("channel", 0)
        return ch if _is_int_in(ch, 0, 15) else 0

    def _send(self, msg) -> None:
        if self._out is None:
            self._log_bytes(msg)
            return
        try:
            self._out.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            print(f"[sound] send error: {exc}")

    def _send_msg(self, msg: dict) -> None:
        """Convert a data-driven message to bytes and send it."""
        ch = self._channel()
        if "note" in msg:
            self._send([0x90 | ch, msg["note"], msg["velocity"]])
        elif "cc" in msg:
            self._send([0xB0 | ch, msg["cc"], msg["value"]])
        elif "program" in msg:
            self._send([0xC0 | ch, msg["program"]])

    def _note_off(self, msg: dict) -> None:
        ch = self._channel()
        self._send([0x80 | ch, msg["note"], 0])

    def _log_bytes(self, msg) -> None:
        """Log a MIDI message the way it would be sent (no device open)."""
        ch = self._channel()
        if len(msg) >= 3 and msg[0] in (0x90 | ch, 0x80 | ch):
            print(f"[sound] {'note' if msg[0] == 0x90 | ch else 'note-off'} "
                  f"ch{ch} {msg[1]} vel{msg[2]}")
        elif len(msg) >= 3 and msg[0] == 0xB0 | ch:
            print(f"[sound] cc ch{ch} {msg[1]}={msg[2]}")
        elif len(msg) >= 2 and msg[0] == 0xC0 | ch:
            print(f"[sound] program ch{ch} {msg[1]}")
        else:
            print(f"[sound] {list(msg)}")

    def close(self) -> None:
        if self._out is not None:
            try:
                self._out.close_port()
            except Exception:  # noqa: BLE001
                pass
            self._out = None


def make_sound(bus: StateBus, sound_loader, enabled: bool,
               out_port=None, out_device=None) -> Sound:
    return Sound(bus, sound_loader, out_port=out_port, out_device=out_device,
                 enabled=enabled)
