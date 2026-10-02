"""Sound adapter (MIDI-out).

Sound is MIDI-controlled: the core does not synthesise audio, it sends MIDI messages
that an external sound engine (Max, Ableton, a script, a hardware unit) turns into
sound. This adapter maps the core's state (zone entry, action playback, per-pose
steps) to MIDI messages and sends them on a MIDI-out port.

The mapping is **data-driven**: it lives in ``sound.json`` (edited in ``editor.html``,
served/saved over ``/api/sound``, hot-reloaded). The built-in :data:`ZONE_NOTES` and
:data:`MODE_CCS` tables remain the fallback when the file is missing or corrupt.

Each **zone** maps to ``{out_port, command}``: ``out_port`` is the MIDI-out device index
for that zone (``null`` → the global fallback), and ``command`` is the zone-entry
message. Action / pose messages are sent on the **active zone's** out device. The
former per-mode CCs are now plain actions on the ``rest`` zone (the legacy ``modes``
block is migrated into ``actions.rest`` on load).

A **MIDI message** (a zone's ``command`` or an action/pose entry) is one of:

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

# Mode -> continuous controller (channel, cc, value). The mode CCs are actions on the
# ``rest`` zone in the data shape (``actions.rest[mode] = {start: <cc msg>, poses: []}``).
MODE_CCS = {
    "wander": (0, 70, 32),
    "random": (0, 70, 64),
    "action": (0, 70, 96),
    "track": (0, 70, 127),
    "hold": (0, 70, 0),
}


def builtin_sound_data() -> dict:
    """Build a sound data table from the built-in :data:`ZONE_NOTES` / :data:`MODE_CCS`.

    This is the fallback shape of ``sound.json`` (see :func:`validate_sound_data`): each
    zone is ``{out_port: None, command: <msg>}`` (no per-zone device → global fallback),
    and the mode CCs are actions on the ``rest`` zone (``actions.rest[mode] =
    {start: <cc msg>, poses: []}``).
    """
    channel = next(iter(ZONE_NOTES.values()))[0]
    return {
        "channel": channel,
        "zones": {name: {"out_port": None, "command": {"note": n, "velocity": v}}
                 for name, (_, n, v) in ZONE_NOTES.items()},
        "actions": {"rest": {name: {"start": {"cc": c, "value": v}, "poses": []}
                             for name, (_, c, v) in MODE_CCS.items()}},
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


def _normalize_zone_entry(raw: Any, where: str) -> dict:
    """Return a zone entry in the per-zone shape ``{out_port, command}``.

    A legacy bare-message entry (``{note, velocity}`` etc.) is wrapped as
    ``{out_port: None, command: <msg>}``; an already-wrapped entry is returned as-is.
    """
    if isinstance(raw, dict) and "command" in raw:
        return raw
    return {"out_port": None, "command": raw}


def validate_sound_data(data: Any) -> dict:
    """Validate a full sound data table. Returns ``data`` or raises :class:`ValueError`.

    Shape: ``{channel, out_port?, out_device?, zones, actions}`` where ``zones[z]`` is
    ``{out_port (int>=0|null), command (<msg>)}`` (a legacy bare-message zone entry is
    normalized to this shape in place) and ``actions`` maps a zone name to an object
    mapping an action name to ``{start?, poses?}`` (a message and/or a list of
    messages). ``out_port`` (a non-negative int index) and ``out_device`` (a
    device-name string) are the optional **global** fallbacks used when a zone's own
    ``out_port`` is null.

    The legacy ``modes`` block (a name → MIDI message map) is migrated **in place**
    into ``actions["rest"]`` (each mode becomes ``{start: <msg>, poses: []}``, added
    only if not already present) and then dropped; the migration is idempotent.
    """
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    channel = data.get("channel", 0)
    if not _is_int_in(channel, 0, 15):
        raise ValueError("'channel' must be an int 0-15")
    out_port = data.get("out_port")
    if out_port is not None:
        if not (isinstance(out_port, int) and not isinstance(out_port, bool) and out_port >= 0):
            raise ValueError("'out_port' must be a non-negative int or null")
    out_device = data.get("out_device")
    if out_device is not None and not isinstance(out_device, str):
        raise ValueError("'out_device' must be a string or null")
    for key in ("zones", "actions"):
        v = data.get(key, {})
        if not isinstance(v, dict):
            raise ValueError(f"'{key}' must be an object")
    for name, raw in data["zones"].items():
        entry = _normalize_zone_entry(raw, f"zones[{name!r}]")
        data["zones"][name] = entry  # normalize legacy bare entries in place
        op = entry.get("out_port")
        if op is not None:
            if not (isinstance(op, int) and not isinstance(op, bool) and op >= 0):
                raise ValueError(f"zones[{name!r}].out_port must be a non-negative int or null")
        validate_message(entry.get("command"), f"zones[{name!r}].command")
    for zone, acts in data["actions"].items():
        if not isinstance(acts, dict):
            raise ValueError(f"actions[{zone!r}]: must be an object")
        for name, act in acts.items():
            if not isinstance(act, dict):
                raise ValueError(f"actions[{zone!r}][{name!r}]: must be an object")
            if "start" in act:
                validate_message(act["start"], f"actions[{zone!r}][{name!r}].start")
            poses = act.get("poses")
            if poses is not None:
                if not isinstance(poses, list):
                    raise ValueError(f"actions[{zone!r}][{name!r}].poses: must be a list")
                for i, msg in enumerate(poses):
                    validate_message(msg, f"actions[{zone!r}][{name!r}].poses[{i}]")
    # Legacy ``modes`` block → ``actions["rest"]`` (in place, idempotent): each mode
    # becomes a rest-zone action that sends its CC message on playback.
    modes = data.get("modes")
    if modes is not None:
        if not isinstance(modes, dict):
            raise ValueError("'modes' must be an object")
        for name, msg in modes.items():
            validate_message(msg, f"modes[{name!r}]")
        rest = data["actions"].setdefault("rest", {})
        for name, msg in modes.items():
            if name not in rest:
                rest[name] = {"start": msg, "poses": []}
        data.pop("modes", None)
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

    Each zone has its own out device (``zones[z].out_port``, an index). A single
    ``MidiOut`` per distinct device is opened lazily and cached in ``_open_outs``;
    zone-entry, action, and pose messages route to the active zone's out device.
    """

    def __init__(self, bus: StateBus, sound_loader, out_port: Optional[int] = None,
                 out_device: Optional[str] = None, enabled: bool = True):
        self.bus = bus
        self.enabled = enabled
        self._sound_loader = sound_loader
        # Global fallbacks for the out device; a zone's own out_port wins.
        self._out_port_default = out_port
        self._out_device_default = out_device
        self._data: Dict[str, Any] = {}
        # Lazy cache of open out ports, keyed by the (port_index, device_name) spec.
        self._open_outs: Dict[tuple, Any] = {}
        self._last_zone: Optional[str] = None
        self._last_mode: Optional[str] = None
        self._last_action: Optional[str] = None
        self._last_target: Optional[list] = None
        self._pose_idx = 0
        if enabled:
            self.reload()
        bus.subscribe(self.on_event)

    # ------------------------------------------------------------------
    # Mapping (de)serialisation.
    # ------------------------------------------------------------------
    def current_data(self) -> dict:
        """The currently loaded sound data table (for the HTTP GET fallback)."""
        return self._data

    def reload(self) -> None:
        """Re-read the sound data via the loader and reconcile the out ports.

        On a loader failure keep the current table (logged); on success swap the table
        and publish :data:`Event.SOUND_CHANGED`. Out ports are reconciled so a change to
        a zone's ``out_port`` (or the global fallback) closes stale devices.
        """
        loaded = False
        try:
            data = self._sound_loader()
        except (OSError, ValueError) as exc:
            print(f"[sound] reload failed ({exc}); keeping current mapping")
        else:
            self._data = data
            loaded = True
        self._reconcile_outs()
        if loaded:
            self.bus.publish(Event.SOUND_CHANGED, "sound")
            print(f"[sound] mapping reloaded: channel={data.get('channel', 0)} "
                  f"zones={len(data.get('zones', {}))} "
                  f"actions={len(data.get('actions', {}))}")

    # ------------------------------------------------------------------
    # MIDI-out connection (one port per distinct device, lazy + cached).
    # ------------------------------------------------------------------
    def _effective_out_port(self) -> Optional[int]:
        """The global ``sound.json`` out_port (if present) else the constructor out_port."""
        op = self._data.get("out_port")
        if isinstance(op, int) and not isinstance(op, bool) and op >= 0:
            return op
        return self._out_port_default

    def _effective_out_device(self) -> Optional[str]:
        """The global ``sound.json`` out_device (if present) else the constructor out_device."""
        od = self._data.get("out_device")
        if isinstance(od, str) and od:
            return od
        return self._out_device_default

    def _zone_out_port(self, zone) -> Optional[int]:
        """The out_port index configured directly on ``zone`` (None if unset)."""
        entry = self._data.get("zones", {}).get(zone)
        op = entry.get("out_port") if isinstance(entry, dict) else None
        if isinstance(op, int) and not isinstance(op, bool) and op >= 0:
            return op
        return None

    def _out_spec(self, zone) -> tuple:
        """The ``(port_index, device_name)`` to open for ``zone``.

        Precedence: the zone's own ``out_port`` (index) → the global ``out_device``
        (name) → the global ``out_port`` (index) → the constructor out_port → auto
        (the first available port).
        """
        zop = self._zone_out_port(zone)
        if zop is not None:
            return (zop, None)
        if self._effective_out_device():
            return (None, self._effective_out_device())
        op = self._effective_out_port()
        if op is not None:
            return (op, None)
        return (None, None)  # auto: first available port

    def _open_one(self, spec: tuple) -> Any:
        """Open one ``MidiOut`` for the ``(port_index, device_name)`` spec; None on failure."""
        port_index, device_name = spec
        try:
            import rtmidi
        except ImportError:
            print("[sound] rtmidi not installed; logging only")
            return None
        try:
            out = rtmidi.MidiOut()
            if device_name:
                out.open_string_outport(device_name)
            elif port_index is not None:
                out.open_port(port_index)
            else:
                # No explicit port: pick the first available out device, if any.
                count = out.get_port_count()
                if count == 0:
                    print("[sound] no MIDI out devices; logging only")
                    return None
                out.open_port(0)
            return out
        except Exception as exc:  # noqa: BLE001 - sound must not kill the core
            print(f"[sound] could not open MIDI out: {exc}; logging only")
            return None

    def _get_out(self, zone) -> Any:
        """The open ``MidiOut`` for ``zone`` (opened lazily); None when unavailable."""
        spec = self._out_spec(zone)
        if spec not in self._open_outs:
            self._open_outs[spec] = self._open_one(spec)
        return self._open_outs[spec]

    def _close_spec(self, spec: tuple) -> None:
        """Close (and evict) a single cached out port."""
        out = self._open_outs.pop(spec, None)
        if out is not None:
            try:
                out.close_port()
            except Exception:  # noqa: BLE001
                pass

    def _reconcile_outs(self) -> None:
        """Close out ports no longer referenced by any zone (e.g. after a reload)."""
        needed = {self._out_spec(z) for z in self._data.get("zones", {})}
        for spec in list(self._open_outs):
            if spec not in needed:
                self._close_spec(spec)

    def list_out_ports(self) -> list:
        """The available MIDI out ports (empty when rtmidi is unavailable).

        rtmidi returns the port names as plain strings; a ``.name`` attribute is
        tolerated too (older wrappers / test fakes).
        """
        try:
            import rtmidi
        except ImportError:
            return []
        try:
            midi = rtmidi.MidiOut()
            ports = midi.get_ports()
            return [{"index": i, "name": p.name if hasattr(p, "name") else p}
                    for i, p in enumerate(ports)]
        except Exception:  # noqa: BLE001 - enumeration must not kill the core
            return []

    # ------------------------------------------------------------------
    # Event handling.
    # ------------------------------------------------------------------
    def on_event(self, event: Event, data) -> None:
        if not self.enabled:
            return
        if event == Event.SNAPSHOT:
            self._on_snapshot(data)

    def _zone_command(self, zone) -> Any:
        """The zone entry's command message (read from the per-zone ``{out_port, command}``).

        Falls back to the entry itself for a legacy bare-message table (unvalidated),
        so the adapter is robust to raw tables in tests.
        """
        entry = self._data.get("zones", {}).get(zone)
        if isinstance(entry, dict) and "command" in entry:
            return entry["command"]
        return entry

    def _on_snapshot(self, snap: Snapshot) -> None:
        data = self._data
        zones = data.get("zones", {})
        actions = data.get("actions", {})

        # 1. zone entry: stop the previous zone's note (on the prev zone's out), start
        #    the new zone's message (on the new zone's out).
        if snap.zone != self._last_zone:
            prev = self._last_zone
            self._last_zone = snap.zone
            if prev is not None:
                prev_msg = self._zone_command(prev)
                if isinstance(prev_msg, dict) and "note" in prev_msg:
                    self._note_off(prev_msg, prev)
            if snap.zone in zones:
                self._send_msg(self._zone_command(snap.zone), snap.zone)

        # 2. action start: send the action's start message (or its first pose).
        if snap.action != self._last_action:
            self._last_action = snap.action
            self._pose_idx = 0
            if snap.action is not None:
                act = actions.get(snap.zone, {}).get(snap.action)
                if isinstance(act, dict):
                    start = act.get("start")
                    if isinstance(start, dict):
                        self._send_msg(start, snap.zone)
                    else:
                        poses = act.get("poses")
                        if isinstance(poses, list) and poses:
                            self._send_msg(poses[0], snap.zone)

        # 3. per-pose step: while an action is playing, a target-pose change advances
        #    the internal pose index. (Heuristic: the snapshot has no explicit pose
        #    index; exact index tracking is a later extension.)
        if (snap.action is not None
                and snap.target_pose != self._last_target):
            self._pose_idx += 1
            act = actions.get(snap.zone, {}).get(snap.action)
            poses = act.get("poses") if isinstance(act, dict) else None
            if isinstance(poses, list) and poses:
                self._send_msg(poses[self._pose_idx % len(poses)], snap.zone)

        self._last_target = list(snap.target_pose)

    # ------------------------------------------------------------------
    # MIDI sending (degrades to logging when no device is open).
    # ------------------------------------------------------------------
    def _channel(self) -> int:
        ch = self._data.get("channel", 0)
        return ch if _is_int_in(ch, 0, 15) else 0

    def _send_bytes(self, zone, msg) -> None:
        """Send raw MIDI bytes on ``zone``'s out device (log when none is open)."""
        out = self._get_out(zone)
        if out is None:
            self._log_bytes(msg)
            return
        try:
            out.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            print(f"[sound] send error: {exc}")

    def _send_msg(self, msg: dict, zone: str) -> None:
        """Convert a data-driven message to bytes and send it on ``zone``'s out."""
        ch = self._channel()
        if "note" in msg:
            self._send_bytes(zone, [0x90 | ch, msg["note"], msg["velocity"]])
        elif "cc" in msg:
            self._send_bytes(zone, [0xB0 | ch, msg["cc"], msg["value"]])
        elif "program" in msg:
            self._send_bytes(zone, [0xC0 | ch, msg["program"]])

    def _note_off(self, msg: dict, zone: str) -> None:
        ch = self._channel()
        self._send_bytes(zone, [0x80 | ch, msg["note"], 0])

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
        for spec in list(self._open_outs):
            self._close_spec(spec)


def make_sound(bus: StateBus, sound_loader, enabled: bool,
               out_port=None, out_device=None) -> Sound:
    return Sound(bus, sound_loader, out_port=out_port, out_device=out_device,
                 enabled=enabled)
