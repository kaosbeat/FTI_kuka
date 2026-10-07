"""StateMachine: the single owner of the robot's composite state.

This is the deterministic heart of the core. It owns the *composite* state
``(zone, action)`` plus the in-progress transition to a target action, the current
target pose, the move speed, the informational flags, the camera telemetry, and the
behaviour cadence. It does **not** delegate motion decisions to a separate brain —
the :meth:`step` driver dispatches to the pure policies in
:mod:`rtr.state.behavior` directly.

Zones are pure safe-boundaries (``safezone`` + ``speed`` + ``actions``). They carry
no ``exits``/``startpos``/``mode`` of their own; the navigation graph is derived
from actions' ``next`` fields. Each action declares a behaviour (``track`` /
``focus`` / ``scan`` / ``look`` / ``wander`` / ``random`` / ``hold``) that drives its
variable axes; the non-variable axes rest at the action's ``base_pose``.

Transitions
-----------
A transition is a single target pose — the *entry pose* of the target action
(``base_pose`` for a variable-axis action, the first pose of the sequence for a legacy
fixed-pose one). :meth:`update` advances it one step per engine tick; when the robot
arrives (:func:`comparelist`) the transition commits and the action begins. Nothing
blocks.
"""

import random
from typing import List, Optional

from ..robot.helpers import comparelist
from .behavior import build_target, clamp, hardware_clamp
from .zones import Zones


class StateMachine:
    """Owns the composite state ``(zone, action)`` and drives it one tick at a time."""

    def __init__(self, zones: Zones, initial_zone: str = "init", tick_hz: float = 20.0):
        self.zones = zones
        self.tick_hz = tick_hz

        # Zone + transition (the state the machine always owned).
        self.current_zone = initial_zone
        self.speed = zones.get(initial_zone).speed
        self.current_action: Optional[str] = None
        self.action_index = 0
        self._transition: Optional[dict] = None

        # Camera telemetry (the brain stashes the latest CAM_TRACK payload here; the
        # behaviour policies read it). Empty dict until the first frame arrives.
        self.camera_state: dict = {}
        # Face telemetry (the brain stashes the latest CAM_FACE payload here; the
        # ``face`` behaviour reads it). Empty dict until the first face frame arrives.
        self.face_state: dict = {}
        # Servo state (the behaviour policies write the last computed servo vector
        # here on every tick; the display reads it for the state frame). Shape:
        # {"behavior", "dx", "dy", "dq_deg", "axes"}; empty dict when no servo ran.
        self.servo_state: dict = {}
        # Hunt state (the brain writes this on every hunt-state change; the display
        # reads it for the state frame). Shape: {"state", "lock_id", "feral"}.
        self.hunt_state: dict = {}
        # Kinematics + camera geometry for the orientation-aware servo (wired in
        # main.py). ``chain`` is the KR60 FK/Jacobian; ``camera_geometry`` is the
        # tool->camera mount. Both None when not wired (the camera behaviours fall
        # back to the legacy fixed-axis gain).
        self.chain = None
        self.camera_geometry = None

        # Per-axis wander nudge, in degrees per *trigger* (legacy meaning). Scaled by
        # 1/tick_hz so the overall drift rate is independent of the tick rate.
        self.limitadjust: List[float] = [5.0, 5.0, 5.0]

        self.target: Optional[List[float]] = None
        self.target_kind: str = "joint"  # "joint" | "linear"

        # Informational flags (published to display/sound; not motion selectors).
        self.flags = {
            "wandermode": 0,
            "dynmode": 0,
            "randomwristmode": 0,
            "reachmode": 0,
        }

        # Cadence (in ticks) for the discrete behaviours.
        self.random_every = max(1, int(tick_hz))
        self.action_every = max(1, int(tick_hz))
        self.tick = 0

    # ------------------------------------------------------------------
    # Requested changes (called by the brain when a command arrives).
    # ------------------------------------------------------------------
    def trigger_action(self, zone: str, action: str) -> bool:
        """Begin a transition to ``action`` in ``zone`` and start it on arrival.

        The robot moves to the action's *entry pose* (``base_pose`` for a
        variable-axis action, the first pose of the sequence for a legacy one), then
        commits: the zone/action become current and the action begins. There is no
        zone-level graph walk — the link between zones is carried entirely by the
        action's ``next`` field, which is what :meth:`trigger_action` is called with.

        Returns False if the zone or action is unknown/disabled, or a transition is
        already in progress.
        """
        if not self.zones.has(zone):
            print(f"[state] unknown zone: {zone}")
            return False
        z = self.zones.get(zone)
        if not z.enabled:
            print(f"[state] zone disabled: {zone}")
            return False
        if action not in z.actions():
            print(f"[state] unknown action in {zone}: {action}")
            return False
        if not z.action_enabled(action):
            print(f"[state] action disabled in {zone}: {action}")
            return False
        # Never start a transition while one is already in progress.
        if self._transition is not None:
            return False
        entry = z.action_entry_pose(action)
        if entry is None:
            print(f"[state] no entry pose for {zone}/{action}")
            return False
        # Shared-position continuity: the connection moves the robot to the target's
        # entry pose (the shared position where the handoff happens). If the current
        # action's end pose does not match it, the robot must cross a gap — warn. The
        # editor flags the same conflict on the connection (red "!" marker).
        if self.current_action:
            cz = self.zones.get(self.current_zone)
            end = cz.action_end_pose(self.current_action)
            if end is not None and any(abs(end[i] - entry[i]) > 0.5 for i in range(6)):
                print(f"[state] no shared position: {self.current_zone}/{self.current_action} "
                      f"ends at {end} but {zone}/{action} starts at {entry}")
        self._transition = {"zone": zone, "action": action, "pose": entry}
        # Move toward the entry pose at the target zone's speed.
        self.speed = z.speed
        return True

    def set_target(self, pose: List[float], kind: str = "joint") -> None:
        """Set the current target pose and its kind (``"joint"`` | ``"linear"``).

        Does **not** clamp: joint callers clamp to the effective floor (the brain's
        ``set_joint_pose`` and :meth:`random_wrist`), and linear poses are Cartesian
        and must not be floor-clamped.
        """
        self.target = list(pose)
        self.target_kind = kind

    def random_wrist(self) -> None:
        """Randomise A4/A5 of the current target (effective-clamped), then hold."""
        if self.target:
            t = list(self.target)
            t[3] = random.randint(-349, 349)
            t[4] = random.randint(-118, 118)
            self.target = clamp(self, t)
        self.clear_action()  # hold the target

    # ------------------------------------------------------------------
    # Actions.
    # ------------------------------------------------------------------
    def step_action(self) -> Optional[List[float]]:
        """Advance to the next pose of a *legacy* fixed-pose action.

        A looping action (``loop`` true, the default) wraps around. A single-run
        action (``loop`` false) does **not** wrap on its last pose; instead it hands
        off to the next step via :meth:`_advance_after_action`.

        Returns the pose, or None if there is no active legacy action.
        """
        if not self.current_action:
            return None
        zone = self.zones.get(self.current_zone)
        if self.current_action not in zone.actions():
            return None
        action = zone._action(self.current_action)
        if "pos" not in action:
            return None
        poses = action["pos"]
        pose = list(poses[self.action_index])
        self.action_index += 1
        if self.action_index >= len(poses):
            if zone.action_loops(self.current_action):
                self.action_index = 0
            else:
                self._advance_after_action(self.current_action)
        return pose

    def play_action(self, name: str) -> bool:
        """Start (or restart) a named action in the *current* zone (no transition).

        The robot is assumed to already be in the zone; the action begins on the next
        tick. Returns False if the action is unknown or disabled.
        """
        zone = self.zones.get(self.current_zone)
        if name not in zone.actions():
            print(f"[state] unknown action in {self.current_zone}: {name}")
            return False
        if not zone.action_enabled(name):
            print(f"[state] action disabled in {self.current_zone}: {name}")
            return False
        self.current_action = name
        self.action_index = 0
        return True

    def clear_action(self) -> None:
        self.current_action = None
        self.action_index = 0

    def _advance_after_action(self, action: str) -> None:
        """Hand off after a single-run (legacy) action completes.

        Reads the action's ``next`` field:
        - no next  -> stop the action (the robot holds the last pose);
        - next in another zone -> :meth:`trigger_action` (transition to its entry pose);
        - next in the same zone -> :meth:`play_action`.
        """
        zone = self.zones.get(self.current_zone)
        n = zone.action_next(action)
        if n is None:
            self.clear_action()
            return
        target_zone = n.get("zone", self.current_zone)
        target_action = n.get("action")
        if target_zone != self.current_zone:
            if not self.trigger_action(target_zone, target_action):
                self.clear_action()
            return
        if target_action and target_action in zone.actions() and zone.action_enabled(target_action):
            self.play_action(target_action)
        else:
            self.clear_action()

    # ------------------------------------------------------------------
    # Per-tick driver.
    # ------------------------------------------------------------------
    def step(self, curjpos: List[float]) -> Optional[List[float]]:
        """Compute the target for this tick and return it."""
        self.tick += 1
        self.speed = self._current_speed()

        # Zone transitions take priority: forward the transition target.
        if self.transitioning:
            t = self.update(curjpos)
            if t is not None:
                self.target_kind = "joint"
                # A transition target (the entry pose) sits outside the current zone's
                # safezone by design; floor it to the hardware limits only so it stays
                # reachable and the arrival check can fire (see hardware_clamp).
                self.target = hardware_clamp(t)
                return self.target
            # Transition just completed; fall through to the new action's first tick.

        if self.current_action:
            zone = self.zones.get(self.current_zone)
            action = zone._action(self.current_action)
            if "pos" in action:
                # Legacy fixed-pose: advance at the action cadence.
                if self.tick % self.action_every == 0:
                    pose = self.step_action()
                    if pose is not None:
                        self.target = clamp(self, pose)
                        self.target_kind = "joint"
            else:
                # Variable-axis: ``random`` re-targets at a cadence (the rest servo
                # continuously every tick).
                if zone.action_behavior(self.current_action) == "random" \
                        and self.tick % self.random_every != 0:
                    pass  # hold the last target this tick
                else:
                    pose = build_target(self, curjpos)
                    if pose is not None:
                        self.target = clamp(self, pose)
                        self.target_kind = "joint"
        # No action: hold the last target.

        return self.target

    def update(self, curjpos: List[float]) -> Optional[List[float]]:
        """Drive the transition one step. Returns the target pose, or None when idle.

        When the robot reaches the entry pose (comparelist), the transition commits
        and returns None for that tick.
        """
        if self._transition is None:
            return None
        t = self._transition
        pose = t["pose"]
        if comparelist(curjpos, pose, margin=0.1, count=5):
            self._commit(t["zone"], t["action"])
            return None
        return pose

    def _commit(self, zone: str, action: str) -> None:
        """Commit arrival at ``action`` in ``zone`` and begin it."""
        z = self.zones.get(zone)
        self.current_zone = zone
        self.speed = z.speed
        self.current_action = action
        self.action_index = 0
        self._transition = None

    def _current_speed(self) -> float:
        """The move speed for the current state.

        - transition in progress -> the target zone's speed;
        - variable-axis action -> the action's speed (when declared);
        - otherwise (legacy action, no action) -> the current zone's speed.
        """
        if self._transition is not None:
            return self.zones.get(self._transition["zone"]).speed
        if self.current_action:
            zone = self.zones.get(self.current_zone)
            if "pos" not in zone._action(self.current_action):
                s = zone.action_speed(self.current_action)
                if s is not None:
                    return s
        return self.zones.get(self.current_zone).speed

    @property
    def behavior(self) -> str:
        """The current action's behaviour (the state-frame mode), or ``"hold"``."""
        if self.current_action:
            return self.zones.get(self.current_zone).action_behavior(self.current_action)
        return "hold"

    @property
    def effective_behavior(self) -> str:
        """The behaviour that is (or is about to be) running.

        While a transition is in progress this is the transition target's behaviour
        (the action about to begin); otherwise the current action's behaviour.
        """
        if self._transition is not None:
            z = self.zones.get(self._transition["zone"])
            return z.action_behavior(self._transition["action"])
        return self.behavior

    @property
    def transitioning(self) -> bool:
        return self._transition is not None

    def reset(self, curjpos: List[float]) -> None:
        """Snap the state machine to the robot's actual pose (used at startup).

        Seeds the target to the robot's pose so the first tick has something to hold.
        """
        self._transition = None
        self.current_zone = "init"
        self.current_action = None
        self.action_index = 0
        self.speed = self.zones.get(self.current_zone).speed
        self.target = list(curjpos)
        self.target_kind = "joint"

    def halt(self, curjpos: List[float]) -> None:
        """Stop all motion: clear the transition and action, hold the current pose.

        Unlike :meth:`reset`, this preserves the current zone (no zone change).
        The robot holds its current joint position.
        """
        self._transition = None
        self.current_action = None
        self.action_index = 0
        self.target = list(curjpos)
        self.target_kind = "joint"
