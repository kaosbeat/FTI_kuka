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
                 midi_reload: Callable[[], None] = None,
                  brain_reload: Callable[[], None] = None,
                  robot_reload: Callable[[], None] = None,
                   screen_patch_override: "Callable[[str | None], None]" = None,
                  render_ui_setter: "Callable[[], None]" = None,
                  snapshot_grab: "Callable[[], None]" = None):
        self.bus = bus
        self.robot = robot
        self.brain = brain
        self.machine = machine
        self.tick_hz = tick_hz
        self._zone_loader = zone_loader
        self._sound_reload = sound_reload
        self._patch_reload = patch_reload
        self._midi_reload = midi_reload
        self._brain_reload = brain_reload
        self._robot_reload = robot_reload
        # Sets (or clears) the manual hydra screen override; the core then pushes it in
        # every state frame so the 3D tool screen and the render page follow it.
        self._screen_patch_override = screen_patch_override
        # Sets the render-page UI state ({hud, code}); pushed in every state frame
        # so the render page can toggle its HUD and code overlay.
        self._render_ui_setter = render_ui_setter
        # Fetches the RPi's latest camera frame into the local snapshot file
        # (a non-blocking grab; the core-side client, see rtr/camera/snapshot_grab).
        self._snapshot_grab = snapshot_grab
        self._running = False
        # The previous tick's (zone, mode, action), used to emit change events.
        self._prev_state = None

    async def run(self) -> None:
        """Run until :meth:`stop` is called."""
        self._running = True
        # Seed the state machine to the robot's real pose (this also seeds the target).
        curjpos = await asyncio.to_thread(self.robot.get_curjpos)
        self.machine.reset(curjpos)
        self._prev_state = (self.machine.current_zone, self.machine.behavior,
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
                elif cmd.cmd == Cmd.RELOAD_MIDI:
                    if self._midi_reload is not None:
                        self._midi_reload()
                elif cmd.cmd == Cmd.RELOAD_BRAIN:
                    if self._brain_reload is not None:
                        self._brain_reload()
                elif cmd.cmd == Cmd.RELOAD_ROBOT_CONFIG:
                    if self._robot_reload is not None:
                        self._robot_reload()
                elif cmd.cmd == Cmd.MIDI_LEARN:
                    # A learned MIDI key captured by MidiInput (rtmidi thread). Publish
                    # it here, inside the loop, so the display adapter can forward it to
                    # the WebSocket (ws.broadcast needs a running loop).
                    self.bus.publish(Event.MIDI_LEARN, cmd.payload)
                elif cmd.cmd == Cmd.SET_SCREEN_PATCH:
                    if self._screen_patch_override is not None:
                        self._screen_patch_override(cmd.payload.get("code"))
                elif cmd.cmd == Cmd.SET_RENDER_UI:
                    if self._render_ui_setter is not None:
                        self._render_ui_setter(
                            hud=cmd.payload.get("hud"),
                            code=cmd.payload.get("code"),
                        )
                elif cmd.cmd == Cmd.SNAPSHOT_GRAB:
                    # Fetch the RPi's latest camera frame into the local snapshot
                    # file (non-blocking: the grabber spawns its own thread).
                    if self._snapshot_grab is not None:
                        self._snapshot_grab()
                else:
                    if cmd.cmd == Cmd.SET_JOINT_POSE and self.machine.current_action is not None:
                        # A raw joint pose is about to clear the active action (the
                        # brain's SET_JOINT_POSE handler calls clear_action); surface
                        # the conflict so the operator sees what was interrupted.
                        self.bus.publish(Event.POSE_CONFLICT, {
                            "action": self.machine.current_action,
                            "zone": self.machine.current_zone,
                            "pose": cmd.payload.get("pose"),
                        })
                    self.brain.on_command(cmd, curjpos)

            # Hunt bookkeeping (lost detection / rescan) before the step, so a
            # triggered transition is driven the same tick.
            self.brain.tick(curjpos)

            # Resolve any pending safety-gated move before stepping.
            await self._resolve_pending(curjpos)

            target = self.machine.step(curjpos)

            if target is not None:
                if self.machine.target_kind == "linear":
                    move = self.robot.move_linear
                else:
                    move = self.robot.move_joint
                if self.machine.move_mode == "stream":
                    # Non-blocking: fire without waiting, so the 20 Hz loop keeps
                    # streaming a fresh target every tick (the robot retargets, like
                    # the sim). This is the base contract (move_* only set a target).
                    move(target, self.machine.speed, blocking=False)
                else:
                    # Block: wait for this move to arrive before the next tick
                    # (the legacy kukapy behaviour).
                    await asyncio.to_thread(move, target, self.machine.speed)

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
            m._prev_zone = None  # the previous zone is stale (the robot was reset)
        elif m.current_action and m.current_action not in zones.get(m.current_zone).actions():
            # The action was removed/renamed: stop it (hold the current pose).
            m.current_action = None
            m.action_index = 0
        m.zones = zones
        m.speed = zones.get(m.current_zone).speed
        self.bus.publish(Event.ZONES_CHANGED, zones.names())
        print(f"[engine] zones reloaded: {', '.join(zones.names())}")

    async def _resolve_pending(self, curjpos) -> None:
        """Resolve a pending safety-gated move (conflict + collision checks).

        Called every tick before ``machine.step``. When the machine has a pending
        move in the ``"check"`` phase, this method:

        1. Computes the red-link conflict via the safety gate.
        2. If no conflict, runs the self-collision check on the target entry pose.
        3. If the move is blocked (conflict or collision):
           - Manual mode: set pending to ``"confirm"`` phase + publish
             ``Event.SAFETY_ALERT`` (the operator decides via the editor popup).
           - Auto mode: clear the pending + mark the candidate blocked in the brain
             (the brain skips it on the next autonomy tick).
        4. If the move is safe: commit the pending.

        When the pending is in the ``"confirm"`` phase, this method does nothing
        (the brain handles ``SAFETY_CONFIRM`` / ``SAFETY_CANCEL`` commands).
        """
        m = self.machine
        if m._pending is None:
            return
        p = m._pending
        if p.get("phase") != "check":
            return  # waiting for operator confirm/cancel

        safety = m.safety
        if safety is None:
            # No safety gate wired (should not happen, but be safe).
            m.commit_pending()
            return

        # 1. Red-link conflict check.
        conflict, issues = safety.check_conflict(
            p["from_zone"], p["from_action"], p["zone"], p["action"]
        )
        p["conflict"] = conflict
        p["issues"] = issues

        # 2. Collision check (only when no conflict, and a checker is wired).
        blocked = conflict
        if not conflict and safety.collision is not None:
            try:
                result = await asyncio.to_thread(safety.collision.check, p["entry"])
            except Exception as exc:
                print(f"[engine] collision check failed: {exc}")
                result = {"collision": True, "collisions": [], "error": str(exc)}
            p["collision"] = result.get("collision", False)
            p["collisions"] = result.get("collisions", [])
            if result.get("collision"):
                blocked = True
        elif not conflict and safety.collision is None:
            print("[engine] WARNING: no collision checker wired; degrading to conflict-only")

        if blocked:
            if m.control_mode == "manual":
                # Show the popup; the operator decides (confirm or cancel).
                m.set_pending_confirm()
                self.bus.publish(Event.SAFETY_ALERT, {
                    "zone": p["zone"],
                    "action": p["action"],
                    "conflict": conflict,
                    "issues": issues,
                    "collision": p.get("collision"),
                    "collisions": p.get("collisions"),
                })
            else:
                # Auto mode: discard the move and mark the candidate blocked.
                discard = {
                    "zone": p["zone"],
                    "action": p["action"],
                    "conflict": conflict,
                    "issues": issues,
                    "collision": p.get("collision"),
                    "collisions": p.get("collisions"),
                }
                m.clear_pending()
                self.brain.mark_candidate_blocked(p["zone"], p["action"])
                # Surface the discard in the control-client log window (warning).
                self.bus.publish(Event.SAFETY_DISCARD, discard)
                print(f"[engine] auto mode: discarded move to {discard['zone']}/{discard['action']} "
                      f"(conflict={conflict}, collision={discard.get('collision')})")
        else:
            # Safe: commit the move.
            m.commit_pending()

    def _publish(self, curjpos, curpos, target: Optional[list]) -> None:
        moving = not comparelist(curjpos, target if target is not None else curjpos,
                                 margin=0.5, count=5)
        info = self.brain.autonomy_info()
        snap = Snapshot(
            zone=self.machine.current_zone,
            mode=self.machine.behavior,
            action=self.machine.current_action,
            joint_pose=list(curjpos),
            cart_pose=list(curpos),
            target_pose=list(target) if target is not None else list(curjpos),
            speed=self.machine.speed,
            moving=moving,
            hunt=self.brain.hunt_info(),
            servo=dict(self.machine.servo_state),
            move_mode=self.machine.move_mode,
            cadence=self.machine.cadence,
            cadence_every=self.machine.cadence_every,
            autonomy_enabled=info["enabled"],
            autonomy_enabled_groups=info["enabled_groups"],
            active_group=info.get("active_group"),
            performance_mode=info["performance_mode"],
            autonomy_dwell_remaining=info["dwell_remaining"],
            control_mode=self.machine.control_mode,
            action_index=self.machine.action_index,
            forced_behavior=self.machine.forced_behavior,
        )
        self.bus.set_snapshot(snap)
        self.bus.publish(Event.SNAPSHOT, snap)

    def _publish_changes(self) -> None:
        """Publish a change event for whichever of (zone, mode, action) changed this tick.

        These make the documented ``zone_changed`` / ``mode_changed`` / ``action_changed``
        events real (the camera and display already subscribe to the zone/mode ones).
        """
        m = self.machine
        cur = (m.current_zone, m.behavior, m.current_action)
        if self._prev_state is None or cur == self._prev_state:
            self._prev_state = cur
            return
        prev_zone, prev_mode, prev_action = self._prev_state
        if cur[0] != prev_zone:
            self.bus.publish(Event.ZONE_CHANGED, m.current_zone)
        if cur[1] != prev_mode:
            self.bus.publish(Event.MODE_CHANGED, m.behavior)
        if cur[2] != prev_action:
            self.bus.publish(Event.ACTION_CHANGED, m.current_action)
        self._prev_state = cur
