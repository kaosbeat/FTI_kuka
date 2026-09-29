"""MIDI-in adapter: per-zone mapping of controller messages to core commands.

This replaces the ``MidiInputHandler`` in ``tidalkuka.py``. Instead of mutating a
global dict it emits :class:`~rtr.core.commands.Command` objects onto the
:class:`~rtr.core.bus.StateBus` (thread-safe, so the rtmidi callbacks can call it
directly).

Per-zone model (from ``midi.json``)
-----------------------------------
``midi.json`` is ``{"enabled": bool, "zones": {z: {in_port, command, actions}}}``:
each zone has its own MIDI-in device index (``in_port``, ``null`` → the default
port), a ``command`` key that activates (gotos) the zone, and an ``actions`` map
that sends a learned key to a zone action. One ``MidiIn`` is opened per distinct
``in_port`` (rtmidi's callback does not report the source port, so multiple ports
need separate handles). A message on port P is routed to the zones with
``in_port == P``: a matching ``command`` → GOTO_ZONE, a matching ``actions`` key →
PLAY_ACTION. Unmapped messages on the **default** port fall back to the legacy
hardcoded protocol below, so existing controllers keep working.

Learn capture is armed per-zone: ``start_learn(zone, action)`` captures the next
mappable message on the zone's ``in_port`` and submits ``MIDI_LEARN {key, msg,
zone, action}`` (the editor writes the key into the zone's ``command`` or
``actions``). The captured message is never dispatched, so arming learn never moves
the robot.

Global navigation (from ``midi.json`` ``nav``)
----------------------------------------------
The optional top-level ``nav`` object maps the four navigation commands
(``next_action`` / ``next_zone`` / ``back`` / ``retrigger``) to MIDI keys. They are
**global** (matched on any port, after the per-zone dispatch) and drive a virtual
cursor over the current zone's ``build_items`` rows (imported from :mod:`rtr.flow`),
mirroring the TUI flow-map navigation: ``next_action`` / ``next_zone`` advance the
cursor to the next action / exit row (wrapping) and activate it; ``back`` activates
the first reverse-exit row; ``retrigger`` re-activates the currently-playing action.
A nav key is learned with ``start_learn(nav=<command>)`` (captured on the default
port) and stored in ``nav[<command>]``.

The legacy top-level ``in_port`` and ``mapping`` fields of ``midi.json`` are
**deprecated**: the validator still accepts and preserves them so old files load,
but dispatch no longer uses them (the hardcoded protocol is the fallback on the
default port).

Legacy hardcoded protocol (default port only)
---------------------------------------------
- CC1 (value 1-6)      → goto_zone (path-routed through the zone exits graph)
- CC2 (0/1)            → set_mode (wander / action)
- CC3 (value i)        → play the i-th action (1-based) in the current zone; 0 clears
- CC13                 → set_flag("dynvel", value)
- CC20 / 21 / 22       → adjust_limit (index 0/1/2)
- CC30 (1/2)           → set_mode (random / wander)
- Note ch1: 41/42/73/74 → set_flag (wandermode / randomwristmode / dynmode / reachmode)
- Note ch1 / ch2: 61-64 → set_joint_pose (named ch1 poses)
- Note ch3             → random_wrist
- Note ch6             → set_linear_pose (random cartesian pose)

The per-trigger random walks on ch4/ch5 and the ch7 "step" trigger are subsumed by the
brain's continuous tick in the new architecture, so they are intentionally not mapped.
"""

import json
from typing import Any, Optional

from ..core.bus import StateBus
from ..core.commands import Cmd, Command, Event
from ..flow import activation_commands, build_items
from ..state.zones import LIN_POSES, POSES

# Legacy hardcoded protocol tables (channel-0 fallback, default port only).
ZONE_BY_CC1 = {1: "init", 2: "rest", 3: "wakeup", 4: "stretch", 5: "wander", 6: "wildwander"}

# ch1 flag notes -> flag name.
FLAG_BY_NOTE = {41: "wandermode", 42: "randomwristmode", 73: "dynmode", 74: "reachmode"}

# The global navigation commands: a relative virtual cursor over the current
# zone's ``build_items`` rows (mirrors the TUI flow-map navigation).
NAV_COMMANDS = ("next_action", "next_zone", "back", "retrigger")


# ---------------------------------------------------------------------------
# midi.json data layer (per-zone device + command + per-action commands).
#
# Mirrors the ``sound.json`` / :mod:`sound` data layer: a built-in fallback table,
# a validator, and a loader. ``zones[z]`` is ``{"in_port": int|null, "command":
# key|null, "actions": {a: key}}``. A **key** is one of:
#
# - ``cc:<cc>:<value>``        — a control change (channel 0, matching legacy),
# - ``note:<ch>:<note>``       — a note-on (channel 0-7; velocity ignored),
# - ``program:<ch>:<prog>``    — a program change (channel 0-7).
#
# The legacy top-level ``in_port`` and ``mapping`` are **deprecated**: the validator
# still accepts and preserves them (validating the mapping contents) so old files
# load, but dispatch no longer uses them.
# ---------------------------------------------------------------------------

def builtin_midi_data() -> dict:
    """The fallback shape of ``midi.json`` (no zones → the legacy protocol wins).

    The ``nav`` section holds the four global navigation keys (initially null,
    learned later); the per-zone table is empty so the legacy protocol still wins.
    """
    return {"enabled": True, "zones": {},
            "nav": {"next_action": None, "next_zone": None,
                    "back": None, "retrigger": None}}


def _key_int(key: str, s: str, lo: int, hi: int) -> int:
    """Parse one numeric key field; raise :class:`ValueError` if not an int in [lo, hi]."""
    try:
        v = int(s)
    except ValueError:
        raise ValueError(f"key {key!r}: field {s!r} is not an integer")
    if not lo <= v <= hi:
        raise ValueError(f"key {key!r}: field {s!r} out of range [{lo}, {hi}]")
    return v


def validate_midi_key(key: Any) -> None:
    """Validate a key. Raise :class:`ValueError` on a bad key.

    A key is ``cc:<cc>:<value>``, ``note:<ch>:<note>``, or ``program:<ch>:<prog>``.
    """
    if not isinstance(key, str):
        raise ValueError(f"key {key!r} must be a string")
    parts = key.split(":")
    if len(parts) != 3:
        raise ValueError(
            f"key {key!r} must be cc:<cc>:<value>, note:<ch>:<note>, "
            f"or program:<ch>:<prog>")
    kind, a, b = parts
    if kind == "cc":
        _key_int(key, a, 0, 127)
        _key_int(key, b, 0, 127)
    elif kind in ("note", "program"):
        _key_int(key, a, 0, 7)
        _key_int(key, b, 0, 127)
    else:
        raise ValueError(f"key {key!r}: unknown kind {kind!r} (expected cc/note/program)")


def validate_midi_target(target: Any, where: str) -> None:
    """Validate a single legacy mapping target. Raise :class:`ValueError` on a bad one.

    Kept for the deprecated top-level ``mapping`` (old files). Shape:
    ``{"target": "zone"|"mode"|"action"|"clear"|"random_action", ...}``.
    """
    if not isinstance(target, dict):
        raise ValueError(f"{where}: target must be an object")
    kind = target.get("target")
    if kind not in ("zone", "mode", "action", "clear", "random_action"):
        raise ValueError(f"{where}: 'target' must be one of "
                         f"zone/mode/action/clear/random_action")
    if kind == "zone":
        zone = target.get("zone")
        if not (isinstance(zone, str) and zone):
            raise ValueError(f"{where}: zone target needs a non-empty 'zone' string")
    elif kind == "mode":
        mode = target.get("mode")
        if not (isinstance(mode, str) and mode):
            raise ValueError(f"{where}: mode target needs a non-empty 'mode' string")
    elif kind in ("action", "random_action"):
        zone = target.get("zone")
        if not (isinstance(zone, str) and zone):
            raise ValueError(f"{where}: {kind} target needs a non-empty 'zone' string")
        if kind == "action":
            action = target.get("action")
            if not (isinstance(action, str) and action):
                raise ValueError(f"{where}: action target needs a non-empty 'action' string")


def validate_midi_data(data: Any) -> dict:
    """Validate a full midi data table. Return ``data`` or raise :class:`ValueError`.

    Shape: ``{"enabled": bool, "zones": {z: {in_port, command, actions: {a: key}}}}``.
    The deprecated top-level ``in_port`` and ``mapping`` are accepted and preserved.
    """
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("'enabled' must be a bool")
    zones = data.get("zones", {})
    if not isinstance(zones, dict):
        raise ValueError("'zones' must be an object")
    for zone, zdata in zones.items():
        if not isinstance(zdata, dict):
            raise ValueError(f"zones[{zone!r}] must be an object")
        in_port = zdata.get("in_port")
        if in_port is not None:
            if not (isinstance(in_port, int) and not isinstance(in_port, bool)
                    and in_port >= 0):
                raise ValueError(f"zones[{zone!r}].in_port must be a non-negative int or null")
        command = zdata.get("command")
        if command is not None:
            validate_midi_key(command)
        actions = zdata.get("actions", {})
        if not isinstance(actions, dict):
            raise ValueError(f"zones[{zone!r}].actions must be an object")
        for action, akey in actions.items():
            if akey is not None:
                validate_midi_key(akey)
    # The global navigation keys (optional): each a MIDI key or null.
    nav = data.get("nav", {})
    if not isinstance(nav, dict):
        raise ValueError("'nav' must be an object")
    for name, nkey in nav.items():
        if name not in NAV_COMMANDS:
            raise ValueError(f"nav[{name!r}] is not a known nav command "
                              f"(expected one of {NAV_COMMANDS})")
        if nkey is not None:
            validate_midi_key(nkey)
    # Deprecated top-level fields: accepted and preserved (not used for dispatch).
    in_port = data.get("in_port")
    if in_port is not None:
        if not (isinstance(in_port, int) and not isinstance(in_port, bool)
                and in_port >= 0):
            raise ValueError("'in_port' must be a non-negative int or null")
    mapping = data.get("mapping", {})
    if not isinstance(mapping, dict):
        raise ValueError("'mapping' must be an object")
    for key, target in mapping.items():
        validate_midi_key(key)
        validate_midi_target(target, f"mapping[{key!r}]")
    return data


def load_midi_data(path: str) -> dict:
    """Load and validate the on-disk midi file. Raise ``OSError``/``ValueError``."""
    with open(path, "r", encoding="utf-8") as f:
        return validate_midi_data(json.load(f))


class MidiInput:
    """Reads MIDI and turns it into commands on the bus, routed per-zone.

    The mapping is **data-driven**: it lives in ``midi.json`` (edited in
    ``editor.html``, served/saved over ``/api/midi``, hot-reloaded). Each zone has its
    own ``in_port`` (the MIDI-in device index; ``null`` → the default port), a
    ``command`` key that activates the zone, and an ``actions`` map for per-action
    keys. One ``MidiIn`` is opened per distinct ``in_port`` (rtmidi's callback does
    not report the source port, so multiple ports need separate handles). A message on
    port P routes to the zones with ``in_port == P``: a matching ``command`` →
    GOTO_ZONE, a matching ``actions`` key → PLAY_ACTION. Unmapped messages on the
    default port fall back to the legacy hardcoded protocol.
    """

    def __init__(self, bus: StateBus, in_port: Optional[int] = None, enabled: bool = True,
                 poses=None, lin_poses=None, zones_provider=None, midi_loader=None):
        self.bus = bus
        # The default port (legacy fallback + the port for zones with a null in_port).
        self.in_port = in_port
        self.enabled = enabled
        # Named pose tables come from the loaded zone data (wired in main.py);
        # the built-in dicts are the fallback when the data file omits them.
        self.poses = poses if poses is not None else POSES
        self.lin_poses = lin_poses if lin_poses is not None else LIN_POSES
        # The live zone table (hot-reloads); used to resolve CC3 action indices.
        self.zones_provider = zones_provider
        # The loaded midi table (enabled / zones); the built-in fallback is the
        # default until the loader succeeds.
        self._midi_loader = midi_loader
        self._data = builtin_midi_data()
        # Open MidiIn handles, keyed by port index (one per distinct in_port).
        self._midi_ins: dict = {}
        # The armed learn capture: {"port", "zone", "action", "nav"} or None.
        self._learn = None
        self._current_zone = "init"
        self._current_action = None
        # The nav cursor: the zone the rows were built for + the rows + the index.
        self._nav_zone = None
        self._nav_items: list = []
        self._nav_cursor = 0
        if enabled:
            self.reload()
        # Track the current zone/action so CC3 and the nav cursor can resolve them.
        bus.subscribe(self.on_event)

    def on_event(self, event: Event, data) -> None:
        """Track the current zone/action from each snapshot (CC3 + the nav cursor)."""
        if event == Event.SNAPSHOT:
            self._current_zone = data.zone
            self._current_action = data.action

    # ------------------------------------------------------------------
    # Table access + reload.
    # ------------------------------------------------------------------
    def current_data(self) -> dict:
        """The currently loaded midi data table (for the HTTP GET fallback)."""
        return self._data

    def reload(self) -> None:
        """Re-read the midi data via the loader (if present) and reconnect the ports.

        On a loader failure keep the current table (logged); on success swap the table
        and publish :data:`Event.MIDI_CHANGED`. The ports reconnect on every reload so
        an ``in_port`` change in ``midi.json`` takes effect.
        """
        loaded = False
        if self._midi_loader is not None:
            try:
                data = self._midi_loader()
            except (OSError, ValueError) as exc:
                print(f"[midi] reload failed ({exc}); keeping current mapping")
            else:
                self._data = data
                loaded = True
        self._reconnect()
        if loaded:
            self.bus.publish(Event.MIDI_CHANGED, "midi")
            print(f"[midi] mapping reloaded: ports={sorted(self._in_ports())} "
                  f"enabled={self._effective_enabled()} "
                  f"zones={len(self._data.get('zones', {}))}")

    # ------------------------------------------------------------------
    # Effective settings + per-zone helpers.
    # ------------------------------------------------------------------
    def _effective_enabled(self) -> bool:
        """The hard switch (constructor ``enabled``) AND the soft switch (``midi.json``)."""
        return self.enabled and bool(self._data.get("enabled", True))

    def _zone_in_port(self, zone) -> Optional[int]:
        """The ``in_port`` for ``zone`` (its own, else the default port)."""
        zdata = self._data.get("zones", {}).get(zone)
        ip = zdata.get("in_port") if isinstance(zdata, dict) else None
        if isinstance(ip, int) and not isinstance(ip, bool) and ip >= 0:
            return ip
        return self.in_port

    def _zones_by_port(self, port) -> list:
        """The zones whose effective ``in_port`` is ``port``."""
        return [z for z in self._data.get("zones", {})
                if self._zone_in_port(z) == port]

    def _in_ports(self) -> set:
        """The distinct ports to open: every zone's in_port plus the default port."""
        ports = {self.in_port}
        for z in self._data.get("zones", {}):
            ports.add(self._zone_in_port(z))
        return ports

    # ------------------------------------------------------------------
    # MIDI-in connection (one handle per distinct port).
    # ------------------------------------------------------------------
    def _reconnect(self) -> None:
        """Open/close the set of MIDI-in handles for the current table.

        No-op when disabled. MIDI must never kill the core: on any failure the handle
        stays closed and the adapter degrades to doing nothing.
        """
        if not self._effective_enabled():
            self._disconnect()
            return
        wanted = self._in_ports()
        for port in list(self._midi_ins):
            if port not in wanted:
                self._close_in(port)
        for port in sorted(wanted):
            if port not in self._midi_ins:
                self._open_in(port)

    def _open_in(self, port) -> None:
        """Open one ``MidiIn`` for ``port``; a no-op on any failure."""
        try:
            import rtmidi
        except ImportError:
            print("[midi] rtmidi not installed; MIDI input disabled")
            return
        try:
            midi = rtmidi.MidiIn()
            ports = midi.get_ports()
            if not ports:
                print("[midi] no MIDI in ports; MIDI input disabled")
                return
            idx = port if (port is not None and port < len(ports)) else 0
            midi.open_port(idx)
            cb = self._make_callback(idx)
            midi.set_callback(cb)
            self._midi_ins[idx] = (midi, cb)
        except Exception as exc:  # noqa: BLE001 - MIDI must not kill the core
            print(f"[midi] could not open MIDI in port {port}: {exc}")

    def _close_in(self, port) -> None:
        """Close (and evict) a single in handle."""
        handle = self._midi_ins.pop(port, None)
        if handle is not None:
            midi, _cb = handle
            try:
                midi.close_port()
            except Exception:  # noqa: BLE001
                pass

    def _disconnect(self) -> None:
        for port in list(self._midi_ins):
            self._close_in(port)

    def _make_callback(self, port):
        """A per-port rtmidi callback bound to its port index."""
        def cb(event, data=None):
            self._on_message(port, event, data)
        return cb

    # ------------------------------------------------------------------
    # Learn capture (armed per-zone).
    # ------------------------------------------------------------------
    def start_learn(self, zone=None, action=None, nav=None) -> None:
        """Arm the learn capture for a zone key or a global nav key.

        A zone learn (``zone``/``action``) captures the next mappable message on the
        zone's ``in_port``; a nav learn (``nav``) captures on the default port. The key
        is submitted as ``Cmd.MIDI_LEARN`` with the matching context (the editor writes
        it into the zone's ``command``/``actions`` or into ``nav[nav]``). The captured
        message is **not** dispatched, so arming learn never moves the robot.
        """
        if nav is not None:
            self._learn = {"port": self.in_port, "zone": None, "action": None, "nav": nav}
        else:
            self._learn = {"port": self._zone_in_port(zone), "zone": zone,
                            "action": action, "nav": None}

    def stop_learn(self) -> None:
        """Disarm the learn capture (without disconnecting the port)."""
        self._learn = None

    @property
    def is_learning(self) -> bool:
        """Whether the learn capture is currently armed."""
        return self._learn is not None

    def _key_for(self, message) -> Optional[str]:
        """The canonical key for a mappable message, or None when it can't be one.

        Note-offs (0x80-0x8F and velocity-0 note-ons) have no key: they are excluded
        from both learn capture and per-zone dispatch.
        """
        status = message[0]
        if status == 0xB0:
            if len(message) < 3:
                return None
            return f"cc:{message[1]}:{message[2]}"
        if 0x90 <= status <= 0x9F:
            if len(message) < 3:
                return None
            if message[2] == 0:
                return None
            return f"note:{status - 0x90}:{message[1]}"
        if 0xC0 <= status <= 0xCF:
            if len(message) < 2:
                return None
            return f"program:{status - 0xC0}:{message[1]}"
        return None

    def list_ports(self) -> list:
        """The available MIDI in ports (empty when rtmidi is unavailable).

        rtmidi returns the port names as plain strings; a ``.name`` attribute is
        tolerated too (older wrappers / test fakes).
        """
        try:
            import rtmidi
        except ImportError:
            return []
        try:
            midi = rtmidi.MidiIn()
            ports = midi.get_ports()
            return [{"index": i, "name": p.name if hasattr(p, "name") else p}
                    for i, p in enumerate(ports)]
        except Exception:  # noqa: BLE001 - enumeration must not kill the core
            return []

    # ------------------------------------------------------------------
    # Nav cursor: a virtual cursor over the current zone's build_items rows.
    # ------------------------------------------------------------------
    def _zone_table(self) -> dict:
        """The live zone table as a plain dict (for ``build_items``).

        ``zones_provider()`` returns a :class:`~rtr.state.zones.Zones` object in
        production (call ``.table()``) or a plain dict in tests; both are accepted.
        """
        if self.zones_provider is None:
            return {}
        z = self.zones_provider()
        if z is None:
            return {}
        if hasattr(z, "table"):
            return z.table()
        if isinstance(z, dict):
            return z
        return {}

    def _rebuild_nav(self) -> None:
        """Rebuild the nav rows for the current zone and reseed the cursor.

        The cursor is seeded to the current action's row (or 0 when it has no row),
        so ``retrigger`` re-activates the currently-playing action.
        """
        self._nav_zone = self._current_zone
        self._nav_items = build_items(self._zone_table(), self._current_zone)
        cursor = 0
        if self._current_action is not None:
            for i, item in enumerate(self._nav_items):
                if item.get("kind") == "action" and item.get("name") == self._current_action:
                    cursor = i
                    break
        self._nav_cursor = cursor

    def _ensure_nav(self) -> None:
        """Lazily rebuild the nav rows if the zone changed since the last build."""
        if self._nav_zone != self._current_zone:
            self._rebuild_nav()

    def _nav_activate(self, item: dict) -> None:
        """Send the activation commands for a nav row (play_action/goto_zone)."""
        for entry in activation_commands(item):
            d = dict(entry)
            name = d.pop("cmd")
            self._submit(Cmd(name), d)

    def _nav_next(self, kind: str) -> None:
        """Advance the cursor to the next row of ``kind`` (wrapping) and activate it."""
        items = self._nav_items
        n = len(items)
        if n == 0:
            return
        start = (self._nav_cursor + 1) % n
        for offset in range(n):
            idx = (start + offset) % n
            if items[idx].get("kind") == kind:
                self._nav_cursor = idx
                self._nav_activate(items[idx])
                return

    def _nav_back(self) -> None:
        """Activate the first reverse-exit row (the zone the robot could come from)."""
        for item in self._nav_items:
            if item.get("kind") == "exit" and item.get("back"):
                self._nav_activate(item)
                return

    def _nav_retrigger(self) -> None:
        """Re-activate the currently-playing action (the cursor does not move)."""
        if self._current_action is None:
            return
        for item in self._nav_items:
            if item.get("kind") == "action" and item.get("name") == self._current_action:
                self._nav_activate(item)
                return

    def _nav_command(self, name: str) -> None:
        """Dispatch one nav command (next_action / next_zone / back / retrigger)."""
        if name == "next_action":
            self._nav_next("action")
        elif name == "next_zone":
            self._nav_next("exit")
        elif name == "back":
            self._nav_back()
        elif name == "retrigger":
            self._nav_retrigger()

    # ------------------------------------------------------------------
    # Incoming messages: per-zone dispatch, nav, legacy protocol on the default port.
    # ------------------------------------------------------------------
    def __call__(self, event, data=None) -> None:
        """Legacy shim: route to the default port (direct/callback use)."""
        self._on_message(self.in_port, event, data)

    def _on_message(self, port, event, data=None) -> None:
        """rtmidi callback body for one port. ``event`` is ``(message, deltatime)``."""
        message = event[0]
        if len(message) < 2:
            return
        # Learn capture: only on the armed port; the message is not dispatched.
        if self._learn is not None:
            if port == self._learn["port"]:
                key = self._key_for(message)
                if key is not None:
                    self.bus.submit(Command(cmd=Cmd.MIDI_LEARN,
                                             payload={"key": key, "msg": list(message),
                                                       "zone": self._learn["zone"],
                                                       "action": self._learn["action"],
                                                       "nav": self._learn["nav"]}))
                    self._learn = None
            return
        # Per-zone dispatch: a matching command → GOTO_ZONE, an actions key → PLAY_ACTION.
        if self._dispatch_zone_key(port, message):
            return
        # Nav dispatch: the global navigation keys (any port).
        if self._dispatch_nav(message):
            return
        # Legacy fallback: only on the default port.
        if port == self.in_port:
            self._legacy_dispatch(message)

    def _dispatch_nav(self, message) -> bool:
        """Match the global nav keys (any port); True if a nav command was dispatched."""
        key = self._key_for(message)
        if key is None:
            return False
        for name, nkey in self._data.get("nav", {}).items():
            if nkey == key:
                self._ensure_nav()
                self._nav_command(name)
                return True
        return False

    def _dispatch_zone_key(self, port, message) -> bool:
        """Route a message to the zones with ``in_port == port``; True if dispatched."""
        key = self._key_for(message)
        if key is None:
            return False
        for zone in self._zones_by_port(port):
            zdata = self._data["zones"][zone]
            if zdata.get("command") == key:
                self._submit(Cmd.GOTO_ZONE, {"zone": zone})
                return True
            for action, akey in zdata.get("actions", {}).items():
                if akey == key:
                    self._submit(Cmd.PLAY_ACTION, {"action": action})
                    return True
        return False

    def _legacy_dispatch(self, message) -> None:
        """The hardcoded channel-0 protocol (default port only)."""
        status = message[0]
        if status == 0xB0:
            if len(message) < 3:
                return
            self._handle_cc(message[1], message[2])
        elif 0x90 <= status <= 0x9F:
            if len(message) < 3:
                return
            self._handle_note(status - 0x90, message[1], message[2])

    def _handle_cc(self, cc: int, value: int) -> None:
        """Legacy CC protocol (channel 0)."""
        if cc == 1 and value in ZONE_BY_CC1:
            self._submit(Cmd.GOTO_ZONE, {"zone": ZONE_BY_CC1[value]})
        elif cc == 2:
            self._submit(Cmd.SET_MODE, {"mode": "action" if value else "wander"})
        elif cc == 3:
            self._play_action_by_index(value)
        elif cc == 13:
            self._submit(Cmd.SET_FLAG, {"flag": "dynvel", "value": value})
        elif cc in (20, 21, 22):
            self._submit(Cmd.ADJUST_LIMIT, {"index": cc - 20, "value": value / 4})
        elif cc == 30:
            self._submit(Cmd.SET_MODE, {"mode": "random" if value == 1 else "wander"})

    def _play_action_by_index(self, index: int) -> None:
        """CC3: play the ``index``-th action (1-based) in the current zone.

        ``0`` clears the active action. The index is resolved against the current
        zone's action table (in order), so it tracks hot-reloaded zone data.
        """
        if index == 0:
            self._submit(Cmd.CLEAR_ACTION, {})
            return
        zones = self.zones_provider() if self.zones_provider is not None else None
        if zones is None or not zones.has(self._current_zone):
            print(f"[midi] CC3: unknown current zone {self._current_zone!r}")
            return
        actions = list(zones.get(self._current_zone).actions())
        if index <= 0 or index > len(actions):
            print(f"[midi] CC3 index {index} out of range for zone "
                  f"{self._current_zone!r} ({len(actions)} action(s))")
            return
        self._submit(Cmd.PLAY_ACTION, {"action": actions[index - 1]})

    def _handle_note(self, channel: int, note: int, velocity: int) -> None:
        """Legacy note protocol (channel 0/1/2/5)."""
        if velocity == 0:  # note off
            return
        if channel in (0, 1) and 61 <= note <= 64:
            table = self.poses.get("ch1")
            if table and 0 <= note - 61 < len(table):
                self._submit(Cmd.SET_JOINT_POSE, {"pose": table[note - 61]})
        elif channel == 0 and note in FLAG_BY_NOTE:
            self._submit(Cmd.SET_FLAG, {"flag": FLAG_BY_NOTE[note], "value": velocity})
        elif channel == 2:
            self._submit(Cmd.RANDOM_WRIST, {})
        elif channel == 5:
            table = self.lin_poses.get("pos1")
            if table:
                self._submit(Cmd.SET_LINEAR_POSE, {"pose": table[velocity % len(table)]})

    def _submit(self, cmd: Cmd, payload: dict) -> None:
        self.bus.submit(Command(cmd=cmd, payload=payload))

    def stop(self) -> None:
        self._learn = None
        self._disconnect()
