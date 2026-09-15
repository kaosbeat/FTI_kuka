"""MIDI-in adapter: maps controller messages to core commands.

This replaces the ``MidiInputHandler`` in ``tidalkuka.py``. The mapping is the same
physical protocol the Tidal / MIDI controller already uses, but instead of mutating a
global dict it emits :class:`~rtr.core.commands.Command` objects onto the
:class:`~rtr.core.bus.StateBus` (thread-safe, so the rtmidi callback can call it
directly).

Protocol (from the legacy code)
--------------------------------
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

from typing import List, Optional

from ..core.bus import StateBus
from ..core.commands import Cmd, Command, Event
from ..state.zones import LIN_POSES, POSES

# CC1 value -> zone name.
ZONE_BY_CC1 = {1: "init", 2: "rest", 3: "wakeup", 4: "stretch", 5: "wander", 6: "wildwander"}

# ch1 flag notes -> flag name.
FLAG_BY_NOTE = {41: "wandermode", 42: "randomwristmode", 73: "dynmode", 74: "reachmode"}


class MidiInput:
    """Reads MIDI and turns it into commands on the bus."""

    def __init__(self, bus: StateBus, in_port: Optional[int] = 0, enabled: bool = True,
                 poses=None, lin_poses=None, zones_provider=None):
        self.bus = bus
        self.in_port = in_port
        self.enabled = enabled
        # Named pose tables come from the loaded zone data (wired in main.py);
        # the built-in dicts are the fallback when the data file omits them.
        self.poses = poses if poses is not None else POSES
        self.lin_poses = lin_poses if lin_poses is not None else LIN_POSES
        # The live zone table (hot-reloads); used to resolve CC3 action indices.
        self.zones_provider = zones_provider
        self._current_zone = "init"
        self._midi = None
        if enabled:
            self._connect()
        # Track the current zone so CC3 can resolve an action index in it.
        bus.subscribe(self.on_event)

    def on_event(self, event: Event, data) -> None:
        """Track the current zone from each snapshot (for CC3 action indexing)."""
        if event == Event.SNAPSHOT:
            self._current_zone = data.zone

    def _connect(self) -> None:
        try:
            import rtmidi
        except ImportError:
            print("[midi] rtmidi not installed; MIDI input disabled")
            self.enabled = False
            return
        try:
            self._midi = rtmidi.MidiIn()
            ports = self._midi.get_ports()
            if self.in_port is not None and self.in_port < len(ports):
                self._midi.open_port(self.in_port)
            elif len(ports) > 0:
                self._midi.open_port(0)
            else:
                print("[midi] no MIDI in ports; MIDI input disabled")
                self.enabled = False
                return
            self._midi.set_callback(self.__call__)
        except Exception as exc:  # noqa: BLE001 - MIDI must not kill the core
            print(f"[midi] could not open MIDI in: {exc}")
            self.enabled = False
            self._midi = None

    def __call__(self, event, data=None) -> None:
        """rtmidi callback. ``event`` is ``(message, deltatime)``."""
        message = event[0]
        if len(message) < 3:
            return
        status, a, b = message[0], message[1], message[2]

        # CC messages (status 0xB0, channel 0).
        if status == 0xB0:
            self._handle_cc(a, b)
        # Note-on messages (status 0x90..0x9F, channels 0..7).
        elif 0x90 <= status <= 0x9F:
            self._handle_note(status - 0x90, a, b)

    # ------------------------------------------------------------------
    def _handle_cc(self, cc: int, value: int) -> None:
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
        if self._midi is not None:
            try:
                self._midi.close_port()
            except Exception:  # noqa: BLE001
                pass
            self._midi = None
