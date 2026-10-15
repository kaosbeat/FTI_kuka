"""StateMachine: the single owner of the robot's composite state.

This is the deterministic heart of the core. It owns the *composite* state
``(zone, action)`` plus the in-progress transition to a target action, the current
target pose, the move speed, the camera telemetry, and the
behaviour cadence. It does **not** delegate motion decisions to a separate brain —
the :meth:`step` driver dispatches to the pure policies in
:mod:`rtr.state.behavior` directly.

Zones are safe-boundaries (``safezone`` + ``speed`` + ``actions``) grouped into
mode groups and linked via zone-level ``exits`` (list of ``[zone, action]`` pairs)
and actions' ``next`` fields. Every action steps through an ordered list of anchor
poses (``poses``); the action's behaviour drives its variable axes on top of the
current anchor, and the non-variable axes rest at the current anchor.

Transitions
-----------
A transition is a single target pose — the *entry pose* of the target action
(the first anchor, ``poses[0]``). :meth:`update` advances it one step per engine tick;
when the robot arrives (:func:`comparelist`) the transition commits and the action
begins. Nothing blocks.
"""

import random
from typing import List, Optional

from ..robot.helpers import comparelist
from .behavior import build_target, clamp, hardware_clamp
from .zones import Zones


class StateMachine:
    """Owns the composite state ``(zone, action)`` and drives it one tick at a time."""

    def __init__(self, zones: Zones, initial_zone: str = "resting", tick_hz: float = 20.0,
                 safety=None):
        self.zones = zones
        self.tick_hz = tick_hz
        self.safety = safety  # SafetyGate or None (no safety gating)

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
        # ``gentle`` behaviour reads it). Empty dict until the first face frame arrives.
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
        # Per-behavior motion params (the tunable knobs of rtr/state/behavior.py),
        # set by Brain.reload() from the brain config's ``behaviors`` section. Empty
        # dict when the config has no ``behaviors`` (the behavior defaults apply).
        self.behavior_params: dict = {}

        # Per-axis wander nudge, in degrees per *trigger* (legacy meaning). Scaled by
        # 1/tick_hz so the overall drift rate is independent of the tick rate.
        self.limitadjust: List[float] = [5.0, 5.0, 5.0]

        self.target: Optional[List[float]] = None
        self.target_kind: str = "joint"  # "joint" | "linear"

        # Pending move (safety gate): when a safety gate is wired, trigger_action
        # / play_action validate and store the move here instead of committing
        # immediately. The engine resolves it (conflict + collision checks) before
        # committing or discarding.
        self._pending: Optional[dict] = None

        # Engine move mode + cadence for the discrete behaviours.
        #   move_mode: "block" (wait for each move to arrive) | "stream" (fire
        #     without waiting, so the 20 Hz loop keeps retargeting the robot).
        #   cadence:   "fixed" (advance every cadence_every ticks) | "arrival"
        #     (advance when the robot reaches the current target).
        #   cadence_every: the fixed-rhythm interval, in ticks.
        self.move_mode = "block"
        self.cadence = "fixed"
        self.cadence_every = max(1, int(tick_hz))
        self.tick = 0
        # Control mode: "auto" (the brain drives autonomous decisions) or
        # "manual" (only explicit client commands move the robot).
        self.control_mode = "auto"
        # Forced behavior (live test override): when set, the robot runs this
        # behavior's motion policy regardless of the current action's declared
        # behavior. Set by the enable_behavior command; cleared by clear_behavior.
        # The robot keeps running (no freeze) while a behavior is forced.
        self.forced_behavior: Optional[str] = None

    # ------------------------------------------------------------------
    # Requested changes (called by the brain when a command arrives).
    # ------------------------------------------------------------------
    def trigger_action(self, zone: str, action: str) -> bool:
        """Begin a transition to ``action`` in ``zone`` and start it on arrival.

        The robot moves to the action's *entry pose* (the first anchor,
        ``poses[0]``), then commits: the zone/action become current and the action
        begins. There is no zone-level graph walk — the link between zones is carried
        entirely by the action's ``next`` field, which is what :meth:`trigger_action`
        is called with.

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

        # Safety gate: when wired, store the move as pending (the engine resolves
        # it: conflict + collision checks) instead of committing immediately.
        if self.safety is not None:
            self._pending = {
                "zone": zone,
                "action": action,
                "entry": list(entry),
                "from_zone": self.current_zone,
                "from_action": self.current_action,
                "same_zone": False,
                "phase": "check",
                "conflict": None,
                "issues": None,
                "collision": None,
                "collisions": None,
            }
            return True

        # No safety gate: old immediate-commit behavior.
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

    def set_control_mode(self, mode: str, curjpos: Optional[List[float]] = None) -> None:
        """Set the control mode: ``"auto"`` (brain drives) or ``"manual"`` (operator only).

        In manual mode the brain skips autonomous decisions (hunt, facefocus,
        autonomy advance) and the machine does not auto-advance after a
        single-run action completes. Explicit client commands
        (``trigger_action``, ``play_action``, ``set_joint_pose``,
        ``random_wrist``, ``stop``) still work.

        Entering manual mode also freezes the robot: an already-running action
        would otherwise keep retargeting the pose every tick (see :meth:`step`),
        which is what "resets" an inspected pose. Clearing the action and any
        in-progress transition and holding the current pose makes the robot stop
        where it is until an explicit command moves it. ``curjpos`` (the robot's
        current joint position) is used for the hold pose when provided.
        """
        if mode not in ("manual", "auto"):
            print(f"[state] invalid control mode: {mode}")
            return
        if mode == "manual" and self.control_mode != "manual":
            self._transition = None
            self._pending = None
            self.current_action = None
            self.action_index = 0
            if curjpos is not None:
                self.target = list(curjpos)
                self.target_kind = "joint"
        self.control_mode = mode
        print(f"[state] control mode: {mode}")

    def enable_behavior(self, behavior: str) -> None:
        """Enable a forced behavior (live test override).

        Sets ``forced_behavior`` so the behavior policies run this behavior's
        motion regardless of the current action's declared behavior. Also sets
        ``control_mode`` to ``"manual"`` to prevent the brain from auto-advancing
        zones/actions, but does **not** freeze the robot (the action keeps
        running so the forced behavior has variable axes to drive).
        """
        self.forced_behavior = behavior
        self.control_mode = "manual"
        print(f"[state] forced behavior: {behavior}")

    def clear_behavior(self) -> None:
        """Clear the forced behavior and return to auto mode.

        The robot resumes autonomous navigation (``control_mode = "auto"``)
        and the behavior policies use the current action's declared behavior.
        """
        self.forced_behavior = None
        self.control_mode = "auto"
        print("[state] forced behavior cleared; control mode: auto")

    # ------------------------------------------------------------------
    # Actions.
    # ------------------------------------------------------------------
    def _advance_anchor(self, zone, action) -> None:
        """Advance to the next anchor of the current action (read-then-advance).

        The pose emitted this tick is the one at the *pre-advance* index; the index
        moves on so the next tick reads the next anchor. A looping action (``loop``
        true, the default) wraps around. A single-run action (``loop`` false) does
        **not** wrap on its last anchor; instead it hands off via
        :meth:`_advance_after_action`.
        """
        name = self.current_action
        n = len(action["poses"])
        self.action_index += 1
        if self.action_index >= n:
            if zone.action_loops(name):
                self.action_index = 0
            else:
                self._advance_after_action(name)

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

        # Safety gate: when wired, store the move as pending (the engine resolves
        # it) instead of committing immediately.
        if self.safety is not None:
            entry = zone.action_entry_pose(name)
            if entry is None:
                print(f"[state] no entry pose for {self.current_zone}/{name}")
                return False
            self._pending = {
                "zone": self.current_zone,
                "action": name,
                "entry": list(entry),
                "from_zone": self.current_zone,
                "from_action": self.current_action,
                "same_zone": True,
                "phase": "check",
                "conflict": None,
                "issues": None,
                "collision": None,
                "collisions": None,
            }
            return True

        self.current_action = name
        self.action_index = 0
        return True

    def clear_action(self) -> None:
        self.current_action = None
        self.action_index = 0

    # ------------------------------------------------------------------
    # Pending move API (safety gate).
    # ------------------------------------------------------------------
    @property
    def pending(self) -> bool:
        """True when a safety-gated move is pending (unresolved)."""
        return self._pending is not None

    def pending_info(self) -> Optional[dict]:
        """The pending move dict (for the state frame), or None."""
        if self._pending is None:
            return None
        return dict(self._pending)

    def commit_pending(self) -> None:
        """Commit the pending move (the engine has verified it is safe).

        Cross-zone: set ``_transition`` + speed; do not change ``current_action``
        until arrival. Same-zone: set ``current_action`` + reset ``action_index``.
        """
        if self._pending is None:
            return
        p = self._pending
        self._pending = None
        z = self.zones.get(p["zone"])
        self.speed = z.speed
        if p.get("same_zone"):
            self.current_action = p["action"]
            self.action_index = 0
        else:
            self._transition = {"zone": p["zone"], "action": p["action"],
                                 "pose": p["entry"]}

    def clear_pending(self) -> None:
        """Discard the pending move (blocked or cancelled)."""
        self._pending = None

    def confirm_pending(self) -> None:
        """Confirm a pending move in the ``"confirm"`` phase (operator override).

        Commits the move despite the safety block.
        """
        if self._pending is None or self._pending.get("phase") != "confirm":
            return
        self.commit_pending()

    def cancel_pending(self) -> None:
        """Cancel a pending move in the ``"confirm"`` phase (operator choice)."""
        if self._pending is None or self._pending.get("phase") != "confirm":
            return
        self._pending = None

    def set_pending_confirm(self) -> None:
        """Transition a pending move to the ``"confirm"`` phase (waiting for
        operator decision)."""
        if self._pending is not None:
            self._pending["phase"] = "confirm"

    def _advance_after_action(self, action: str) -> None:
        """Hand off after a single-run action completes (its last anchor is reached).

        Priority: the action's ordered ``next`` list (first valid candidate) >
        zone-level ``exits`` (first valid, deterministic). No valid target ->
        stop the action (the robot holds the last pose).

        In manual mode the action is simply cleared (no auto-advance): the
        operator explicitly triggers the next action.
        """
        # Clear the action first so that if the pending move is later discarded
        # (blocked by the safety gate), the robot holds instead of trying to
        # re-advance an out-of-bounds anchor index.
        self.clear_action()
        if self.control_mode == "manual":
            return
        zone = self.zones.get(self.current_zone)
        for entry in zone.action_next_list(action):
            tz, ta = entry.get("zone"), entry.get("action")
            if self._try_go(tz, ta):
                return
        # No valid next candidate: fall back to the first valid zone-level exit.
        for tz, ta in zone.exits():
            if self._try_go(tz, ta):
                return

    def _try_go(self, tz: str, ta: str) -> bool:
        """Attempt to hand off to ``(tz, ta)``. Returns True if the handoff was taken.

        A candidate is taken when the zone exists and is enabled and the action is
        named, exists, and is enabled. Same-zone uses ``play_action``; cross-zone
        uses ``trigger_action`` (clearing the action if the transition is refused).
        When a safety gate is wired, ``trigger_action`` stores a pending move
        instead of committing immediately; in that case the action is NOT cleared
        (the pending will be resolved by the engine).
        """
        if not isinstance(tz, str) or not self.zones.has(tz):
            return False
        z = self.zones.get(tz)
        if not z.enabled:
            return False
        if not isinstance(ta, str) or ta not in z.actions() or not z.action_enabled(ta):
            return False
        if tz != self.current_zone:
            if not self.trigger_action(tz, ta):
                if not self.pending:
                    self.clear_action()
            return True
        self.play_action(ta)
        return True

    # ------------------------------------------------------------------
    # Per-tick driver.
    # ------------------------------------------------------------------
    def step(self, curjpos: List[float]) -> Optional[List[float]]:
        """Compute the target for this tick and return it.

        Every action is handled by one unified path (read-then-advance): read the
        current anchor (``poses[action_index]``), apply the action's wiggle on top,
        clamp, and — when the cadence fires — advance the anchor index. Continuous
        wiggles re-target every tick; ``spontaneous`` re-targets only on the cadence
        (holding between, like the legacy fixed-pose stepping).
        """
        self.tick += 1
        self.speed = self._current_speed()

        # A pending safety-gated move holds the robot until the engine resolves it.
        if self._pending is not None:
            return self.target

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
            poses = action.get("poses")
            if isinstance(poses, list) and poses:
                spontaneous = zone.action_behavior(self.current_action) == "spontaneous"
                if not spontaneous or self._cadence_due(curjpos):
                    pose = build_target(self, curjpos)
                    if pose is not None:
                        self.target = clamp(self, pose)
                        self.target_kind = "joint"
                if self._cadence_due(curjpos):
                    self._advance_anchor(zone, action)
        # No action / no poses: hold the last target.

        return self.target

    def _cadence_due(self, curjpos: List[float]) -> bool:
        """Whether the discrete cadence fires this tick.

        - ``"fixed"``: fire every ``cadence_every`` ticks (the legacy fixed-rhythm).
        - ``"arrival"``: fire when the robot reaches the current target
          (comparelist), i.e. advance-when-ends.
        """
        if self.cadence == "arrival":
            return self.target is not None \
                and comparelist(curjpos, self.target, margin=0.1, count=5)
        return self.tick % self.cadence_every == 0

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
        - action with a declared ``speed`` -> the action's speed;
        - otherwise (no action, or no declared action speed) -> the zone's speed.
        """
        if self._transition is not None:
            return self.zones.get(self._transition["zone"]).speed
        if self.current_action:
            s = self.zones.get(self.current_zone).action_speed(self.current_action)
            if s is not None:
                return s
        return self.zones.get(self.current_zone).speed

    @property
    def behavior(self) -> str:
        """The active behaviour: the forced behavior (if set), else the current
        action's declared behaviour, else ``"calm"``."""
        if self.forced_behavior is not None:
            return self.forced_behavior
        if self.current_action:
            return self.zones.get(self.current_zone).action_behavior(self.current_action)
        return "calm"

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
        self._pending = None
        self.current_zone = "resting"
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
        self._pending = None
        self.current_action = None
        self.action_index = 0
        self.target = list(curjpos)
        self.target_kind = "joint"
