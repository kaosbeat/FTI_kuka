"""Engine: the asyncio tick loop that drives the whole core.

Each tick the engine:

1. reads the current joint pose from the robot,
2. folds any queued commands (from MIDI / WebSocket) into the state machine via the
   thin command interpreter (the brain),
3. asks the state machine for the next target (it drives the transition or the current
   behaviour mode),
4. commands the robot to that target,
5. publishes a state snapshot to every subscriber (display, camera, sound), and
6. publishes a change event (zone / mode / action) whenever one of them changes.

This single loop is what replaces the old daemon-thread ``kukaLoop`` + blocking
``activateZone`` + ``queue_handler``. Nothing blocks: zone transitions are data the
state machine advances one step per tick.
"""

import asyncio
from typing import Callable, Optional

from ..brain.brain import Brain
from ..core.bus import StateBus
from ..core.commands import Cmd, Event, Snapshot
from ..robot.base import RobotBase
from ..robot.helpers import comparelist
from ..state.machine import StateMachine
from ..state.zones import Zones


class Engine:
    """Runs the core's control loop."""

    def __init__(self, bus: StateBus, robot: RobotBase, brain: Brain,
                 machine: StateMachine, tick_hz: float = 20.0,
                 zone_loader: Callable[[], dict] = None,
                 sound_reload: Callable[[], None] = None,
                 patch_reload: Callable[[], None] = None,
                 screen_patch_override: "Callable[[str | None], None]" = None):
        self.bus = bus
        self.robot = robot
        self.brain = brain
        self.machine = machine
        self.tick_hz = tick_hz
        self._zone_loader = zone_loader
        self._sound_reload = sound_reload
        self._patch_reload = patch_reload
        # Sets (or clears) the manual hydra screen override; the core then pushes it in
        # every state frame so the 3D tool screen and the render page follow it.
        self._screen_patch_override = screen_patch_override
        self._running = False
        # The previous tick's (zone, mode, action), used to emit change events.
        self._prev_state = None

    async def run(self) -> None:
        """Run until :meth:`stop` is called."""
        self._running = True
        # Seed the state machine to the robot's real pose (this also seeds the target).
        curjpos = await asyncio.to_thread(self.robot.get_curjpos)
        self.machine.reset(curjpos)
        self._prev_state = (self.machine.current_zone, self.machine.mode,
                            self.machine.current_action)

        interval = 1.0 / self.tick_hz
        while self._running:
            loop_start = asyncio.get_event_loop().time()

            curjpos = await asyncio.to_thread(self.robot.get_curjpos)
            curpos = await asyncio.to_thread(self.robot.get_curpos)

            for cmd in self.bus.drain_commands():
                if cmd.cmd == Cmd.RELOAD_ZONES:
                    self.reload_zones()
                elif cmd.cmd == Cmd.RELOAD_SOUND:
                    if self._sound_reload is not None:
                        self._sound_reload()
                elif cmd.cmd == Cmd.RELOAD_PATCHES:
                    if self._patch_reload is not None:
                        self._patch_reload()
                elif cmd.cmd == Cmd.SET_SCREEN_PATCH:
                    if self._screen_patch_override is not None:
                        self._screen_patch_override(cmd.payload.get("code"))
                else:
                    self.brain.on_command(cmd, curjpos)

            target = self.machine.step(curjpos)

            if target is not None:
                if self.machine.target_kind == "linear":
                    await asyncio.to_thread(self.robot.move_linear, target, self.machine.speed)
                else:
                    await asyncio.to_thread(self.robot.move_joint, target, self.machine.speed)

            self._publish(curjpos, curpos, target)
            self._publish_changes()

            # Keep the tick rate steady.
            elapsed = asyncio.get_event_loop().time() - loop_start
            sleep_for = interval - elapsed
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    def stop(self) -> None:
        self._running = False

    def reload_zones(self) -> None:
        """Re-read the zone data file and swap it into the machine.

        The robot's pose is untouched. If the current zone no longer exists the
        machine resets to ``"init"``. A read/validation failure keeps the current
        zones (logged) so a bad edit never kills the core.
        """
        if self._zone_loader is None:
            return
        try:
            data = self._zone_loader()
        except (OSError, ValueError) as exc:
            print(f"[engine] zone reload failed ({exc}); keeping current zones")
            return
        zones = Zones(data.get("zones"))
        m = self.machine
        # Drop an in-progress transition whose target zone was deleted.
        if m._transition is not None and not zones.has(m._transition["zone"]):
            m._transition = None
        if not zones.has(m.current_zone):
            m.current_zone = "init"
            m.current_action = None
            m.action_index = 0
        m.zones = zones
        m.speed = zones.get(m.current_zone).speed
        self.bus.publish(Event.ZONES_CHANGED, zones.names())
        print(f"[engine] zones reloaded: {', '.join(zones.names())}")

    def _publish(self, curjpos, curpos, target: Optional[list]) -> None:
        moving = not comparelist(curjpos, target if target is not None else curjpos,
                                 margin=0.5, count=5)
        snap = Snapshot(
            zone=self.machine.current_zone,
            mode=self.machine.mode,
            action=self.machine.current_action,
            joint_pose=list(curjpos),
            cart_pose=list(curpos),
            target_pose=list(target) if target is not None else list(curjpos),
            speed=self.machine.speed,
            flags=dict(self.machine.flags),
            moving=moving,
        )
        self.bus.set_snapshot(snap)
        self.bus.publish(Event.SNAPSHOT, snap)

    def _publish_changes(self) -> None:
        """Publish a change event for whichever of (zone, mode, action) changed this tick.

        These make the documented ``zone_changed`` / ``mode_changed`` / ``action_changed``
        events real (the camera and display already subscribe to the zone/mode ones).
        """
        m = self.machine
        cur = (m.current_zone, m.mode, m.current_action)
        if self._prev_state is None or cur == self._prev_state:
            self._prev_state = cur
            return
        prev_zone, prev_mode, prev_action = self._prev_state
        if cur[0] != prev_zone:
            self.bus.publish(Event.ZONE_CHANGED, m.current_zone)
        if cur[1] != prev_mode:
            self.bus.publish(Event.MODE_CHANGED, m.mode)
        if cur[2] != prev_action:
            self.bus.publish(Event.ACTION_CHANGED, m.current_action)
        self._prev_state = cur
