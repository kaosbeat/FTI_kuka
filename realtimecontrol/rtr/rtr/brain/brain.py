"""Brain: the decision layer.

The brain owns the robot's *behaviour policy* and turns the current state plus any
pending commands into a single target pose each engine tick. It is the seam where
"programmed logic, a small LLM, or realtime control" can plug in: subclass
:class:`Brain` and override :meth:`step` (or :meth:`on_command`) to change what the
robot decides, without touching the state machine, the robot, or the adapters.

Behaviour modes
---------------
- ``wander`` (default): gentle continuous drift inside the current zone's safezone.
- ``random``: jump to a new random pose in the safezone at a fixed cadence.
- ``action``: step through the current zone's active action (see
  :meth:`StateMachine.step_action`).
- ``track``: advance A1 at a fixed rate, clamped to the safezone.
- ``hold``: keep the last commanded pose.

A ``set_joint_pose`` / ``random_wrist`` command sets the target directly and switches
the policy to ``hold`` (the robot goes there and stays, until told otherwise).

Zone transitions are handled by the :class:`StateMachine`; while one is in progress
the brain simply forwards its target.
"""

import random
from typing import List, Optional

from ..core.commands import Cmd, Command
from ..robot.helpers import fitlimits
from ..state.machine import StateMachine
from ..state.zones import HARDWARE_LIMITS, Zones, effective_limits

MODES = ("wander", "random", "action", "track", "hold")


class Brain:
    """Decides the next target pose for the robot, one engine tick at a time."""

    def __init__(self, machine: StateMachine, zones: Zones, tick_hz: float = 20.0):
        self.machine = machine
        self.zones = zones
        self.tick_hz = tick_hz

        self.mode: str = "wander"
        # Per-axis wander nudge, in degrees per *trigger* (legacy meaning). Scaled by
        # 1/tick_hz so the overall drift rate is independent of the tick rate.
        self.limitadjust: List[float] = [5.0, 5.0, 5.0]
        self.track_speed: float = 10.0  # deg/s for track mode

        self.target: Optional[List[float]] = None
        self.target_kind: str = "joint"  # "joint" | "linear"

        # Informational flags (published to display/sound; not motion selectors).
        self.flags = {
            "wandermode": 0,
            "dynmode": 0,
            "randomwristmode": 0,
            "reachmode": 0,
        }

        # Cadence (in ticks) for the discrete modes.
        self.random_every = max(1, int(tick_hz))
        self.action_every = max(1, int(tick_hz))
        self._tick = 0

    # ------------------------------------------------------------------
    # Command intake.
    # ------------------------------------------------------------------
    def on_command(self, cmd: Command, curjpos: List[float]) -> None:
        c = cmd.cmd
        p = cmd.payload
        if c == Cmd.GOTO_ZONE:
            self.machine.request_zone(p.get("zone"), curjpos)
        elif c == Cmd.SET_MODE:
            self.set_mode(p.get("mode", "wander"))
        elif c == Cmd.PLAY_ACTION:
            self.machine.play_action(p.get("action"))
        elif c == Cmd.CLEAR_ACTION:
            self.machine.clear_action()
        elif c == Cmd.SET_JOINT_POSE:
            pose = self._clamp(list(p["pose"]))
            self.target = pose
            self.target_kind = "joint"
            self.set_mode("hold")
        elif c == Cmd.SET_LINEAR_POSE:
            self.target = list(p["pose"])
            self.target_kind = "linear"
        elif c == Cmd.ADJUST_LIMIT:
            i = int(p.get("index", 0))
            self.limitadjust[i] = float(p.get("value", self.limitadjust[i]))
        elif c == Cmd.RANDOM_WRIST:
            if self.target:
                t = list(self.target)
                t[3] = random.randint(-349, 349)
                t[4] = random.randint(-118, 118)
                self.target = self._clamp(t)
            self.set_mode("hold")
        elif c == Cmd.SET_FLAG:
            name = p.get("flag")
            if name in self.flags:
                self.flags[name] = p.get("value")

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            print(f"[brain] unknown mode: {mode}")
            return
        self.mode = mode
        if mode == "action" and not self.machine.current_action:
            # Start the first *enabled* action of the current zone.
            zone = self.machine.zones.get(self.machine.current_zone)
            for name in zone.actions():
                if zone.action_enabled(name):
                    self.machine.play_action(name)
                    break

    # ------------------------------------------------------------------
    # Per-tick decision.
    # ------------------------------------------------------------------
    def step(self, curjpos: List[float]) -> Optional[List[float]]:
        """Compute the target for this tick and return it."""
        self._tick += 1

        # Zone transitions take priority: forward the state machine's target.
        if self.machine.transitioning:
            t = self.machine.update(curjpos)
            if t is not None:
                self.target_kind = "joint"
                # A transition target (exitpos / startpos) is a curated hand-off pose
                # that must be reached EXACTLY: the state machine's arrival check
                # (comparelist against the *unclamped* step) only fires when the robot
                # gets there. The exit pose is by definition outside the current zone's
                # safezone (it is the hand-off to the next zone), so flooring it to the
                # safezone would move it and the robot could never reach the unclamped
                # step -> the transition stalls forever and, because this branch takes
                # priority every tick, the whole core freezes (moving=no, stuck).
                # Floor it to the hardware limits only.
                self.target = self._hardware_clamp(t)
                return self.target
            # Transition just completed; fall through to the zone behaviour.

        if self.mode == "wander":
            self.target = self._wander(curjpos)
            self.target_kind = "joint"
        elif self.mode == "random" and self._tick % self.random_every == 0:
            self.target = self._random_pose()
            self.target_kind = "joint"
        elif (
            self.mode == "action"
            and self.machine.current_action
            and self._tick % self.action_every == 0
        ):
            pose = self.machine.step_action()
            if pose is not None:
                self.target = self._clamp(pose)
                self.target_kind = "joint"
        elif self.mode == "track":
            self.target = self._track(curjpos)
            self.target_kind = "joint"
        # "hold" (and any other case): keep self.target as-is.

        # Floor the final result so every pose reaching the robot is within the
        # hard safety floor (safezone ∩ hardware), regardless of the policy.
        if self.target is not None:
            self.target = self._clamp(self.target)

        return self.target

    # ------------------------------------------------------------------
    # Policies.
    # ------------------------------------------------------------------
    def _safezone(self):
        return self.machine.zones.get(self.machine.current_zone).safezone

    def _effective(self):
        """The per-axis hard safety floor: the zone safezone ∩ the hardware limits."""
        return effective_limits(self._safezone())

    def _clamp(self, pose: List[float]) -> List[float]:
        """Clamp every axis (A1–A6) to the effective floor (safezone ∩ hardware)."""
        return [fitlimits(i, pose[i], self._effective()) for i in range(len(pose))]

    def _hardware_clamp(self, pose: List[float]) -> List[float]:
        """Clamp every axis to the hardware limits only (no safezone).

        Used for zone-transition targets: the exit/start poses sit outside the current
        zone's safezone by design, so the safezone floor must not apply to them — only
        the hardware floor. This keeps them reachable so the state machine's arrival
        check fires and the transition can complete.
        """
        return [fitlimits(i, pose[i], HARDWARE_LIMITS) for i in range(len(pose))]

    def _wander(self, curjpos: List[float]) -> List[float]:
        """Gentle continuous drift of A1-A3, wrist derived, clamped to the floor."""
        pose = list(curjpos)
        limits = self._effective()
        for i in range(3):
            val = (0.5 - random.random()) * self.limitadjust[i] * (1.0 / self.tick_hz)
            pose[i] = fitlimits(i, pose[i] + val, limits)
        # Keep the wrist oriented the way the legacy code did.
        pose[4] = fitlimits(4, -(pose[1] + pose[2]), limits)
        return pose

    def _random_pose(self) -> List[float]:
        """A fully random pose inside the current zone's effective floor."""
        limits = self._effective()
        pose = [random.uniform(lo, hi) for (lo, hi) in limits[:5]]
        pose[4] = fitlimits(4, -(pose[1] + pose[2]), limits)
        pose.append(0.0)  # A6 kept at 0 (the step clamp floors it)
        return pose

    def _track(self, curjpos: List[float]) -> List[float]:
        """Advance A1 at ``track_speed`` deg/s, clamped to the floor."""
        pose = list(curjpos)
        limits = self._effective()
        step = self.track_speed * (1.0 / self.tick_hz)
        pose[0] = fitlimits(0, pose[0] + step, limits)
        return pose
