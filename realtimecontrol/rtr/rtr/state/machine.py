"""StateMachine: the single owner of the robot's composite state.

This is the deterministic heart of the core. It owns the *composite* state
``(zone, mode, action)`` plus the in-progress transition to a new zone, the current
target pose, the move speed, the informational flags, and the behaviour cadence. It
does **not** delegate the wander/random/track decision to a separate brain — the
:meth:`step` driver below dispatches to the pure policies in
:mod:`rtr.state.behavior` directly.

Behaviour modes
---------------
- ``wander`` (default): gentle continuous drift inside the current zone's safezone.
- ``random``: jump to a new random pose in the safezone at a fixed cadence.
- ``action``: step through the current zone's active action (see :meth:`step_action`).
- ``track``: advance A1 at a fixed rate, clamped to the safezone.
- ``hold``: keep the last commanded pose.

A ``set_joint_pose`` / ``random_wrist`` command sets the target directly and switches
the policy to ``hold`` (the robot goes there and stays, until told otherwise).

The old ``activateZone`` in ``robothelpers.py`` blocked a thread with a ``while``
loop + ``time.sleep`` while the robot reached the exit/start pose. Here the transition
is just data (a list of poses to pass through), and :meth:`update` advances it one
step per engine tick. Nothing blocks.
"""

import random
from typing import List, Optional

from ..robot.helpers import comparelist
from .behavior import clamp, hardware_clamp, random_pose, track, wander
from .zones import MODES, Zones


class StateMachine:
    """Owns the composite state ``(zone, mode, action)`` and drives it one tick at a time."""

    def __init__(self, zones: Zones, initial_zone: str = "init", tick_hz: float = 20.0):
        self.zones = zones
        self.tick_hz = tick_hz

        # Zone + transition (the state the machine always owned).
        self.current_zone = initial_zone
        self.speed = zones.get(initial_zone).speed
        self.current_action: Optional[str] = None
        self.action_index = 0
        self._transition: Optional[dict] = None

        # Behaviour state (moved from the old Brain).
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
        self.tick = 0

    # ------------------------------------------------------------------
    # Requested changes (called by the brain when a command arrives).
    # ------------------------------------------------------------------
    def request_zone(self, name: str, curjpos: List[float],
                     entry_action: Optional[str] = None,
                     handoff_pose: Optional[List[float]] = None) -> bool:
        """Begin a transition to ``name``. Returns False if the zone is unknown/disabled.

        The transition is routed through the zone ``exits`` graph: the robot walks the
        shortest path from the current zone to ``name``, passing each intermediate
        zone's exit pose then start pose. A target with no path is rejected.

        Each hop leaves the zone at its *per-edge* exit pose (``exitposes[to]`` if
        declared, else the zone's single ``exitpos``) and enters the next zone at its
        ``startpos``. The first hop may leave at a custom ``handoff_pose`` (the
        action's exit position) when one is given.

        ``entry_action`` (optional) names an action to activate on arrival; the commit
        (see :meth:`_commit_zone`) plays it instead of clearing the action.
        """
        if not self.zones.has(name):
            print(f"[state] unknown zone: {name}")
            return False
        if not self.zones.get(name).enabled:
            print(f"[state] zone disabled: {name}")
            return False
        # Never start a transition while one is already in progress.
        if self._transition is not None:
            return False

        path = self.zones.find_path(self.current_zone, name)
        if path is None:
            print(f"[state] no path from {self.current_zone!r} to {name!r}")
            return False
        if len(path) < 2:
            print(f"[state] already in zone {name!r}; no transition")
            return False

        steps: List[List[float]] = []
        # Walk the path: for each hop, leave the zone (per-edge exitpose) then enter
        # the next zone (startpos). The old "skip exit if already safe" shortcut is
        # dropped in favour of always routing through the graph (more predictable,
        # always safe). The first hop leaves at the custom hand-off pose when given.
        for i in range(len(path) - 1):
            if i == 0 and handoff_pose is not None:
                steps.append(list(handoff_pose))
            else:
                steps.append(self.zones.get(path[i]).exitpose_for(path[i + 1]))
            steps.append(self.zones.get(path[i + 1]).startpos)
        self._transition = {"zone": name, "steps": steps, "i": 0, "entry_action": entry_action}
        return True

    def set_mode(self, mode: str) -> None:
        """Switch the behaviour mode. Validates against :data:`MODES`.

        If the mode is ``action`` and no action is currently playing, start the first
        *enabled* action of the current zone.
        """
        if mode not in MODES:
            print(f"[state] unknown mode: {mode}")
            return
        self.mode = mode
        if mode == "action" and not self.current_action:
            zone = self.zones.get(self.current_zone)
            for name in zone.actions():
                if zone.action_enabled(name):
                    self.play_action(name)
                    break

    def set_target(self, pose: List[float], kind: str = "joint") -> None:
        """Set the current target pose and its kind (``"joint"`` | ``"linear"``).

        Does **not** clamp: joint callers clamp to the effective floor (the engine's
        ``set_joint_pose`` and :meth:`random_wrist`), and linear poses are Cartesian
        and must not be floor-clamped.
        """
        self.target = list(pose)
        self.target_kind = kind

    def random_wrist(self) -> None:
        """Randomise A4/A5 of the current target (effective-clamped), then ``hold``.

        Lifted from the old brain's ``RANDOM_WRIST`` handler.
        """
        if self.target:
            t = list(self.target)
            t[3] = random.randint(-349, 349)
            t[4] = random.randint(-118, 118)
            self.target = clamp(self, t)
        self.set_mode("hold")

    # ------------------------------------------------------------------
    # Actions (moved unchanged from the pre-consolidation machine).
    # ------------------------------------------------------------------
    def step_action(self) -> Optional[List[float]]:
        """Advance to the next pose of the current action.

        A looping action (``loop`` true, the default) wraps around. A single-run
        action (``loop`` false) does **not** wrap on its last pose; instead it hands
        off to the next step via :meth:`_advance_after_action`.

        Returns the pose, or None if there is no active action.
        """
        if not self.current_action:
            return None
        zone = self.zones.get(self.current_zone)
        actions = zone.actions()
        if self.current_action not in actions:
            return None
        action = actions[self.current_action]
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
        """Start (or restart) a named action in the current zone.

        Returns False if the action is unknown or disabled.
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
        """Hand off after a single-run action completes.

        Reads the action's ``target``:
        - no target  -> stop the action (the robot holds the last pose);
        - target in another zone -> begin an exit transition (leave at the hand-off
          pose, arrive at the target zone, activate the target action on arrival);
        - target in the same zone -> activate the target action (or fall back to the
          zone's default mode when there is no valid next action).
        """
        zone = self.zones.get(self.current_zone)
        tgt = zone.action_target(action)
        if tgt is None:
            self.clear_action()
            return
        target_zone = tgt.get("zone", self.current_zone)
        target_action = tgt.get("action")
        if target_zone != self.current_zone:
            if not self.request_exit(target_zone, target_action, tgt.get("pose")):
                # No path to the target zone: stop the action, fall back to the
                # zone's default mode (if declared).
                self.clear_action()
                dm = zone.default_mode
                if dm is not None:
                    self.mode = dm
            return
        # Same-zone advance: play the next action, else fall back.
        if target_action and target_action in zone.actions() and zone.action_enabled(target_action):
            self.play_action(target_action)
        else:
            self.clear_action()
            dm = zone.default_mode
            if dm is not None:
                self.mode = dm

    def request_exit(self, target_zone: str, entry_action: Optional[str],
                     handoff_pose: Optional[List[float]]) -> bool:
        """Begin a transition to ``target_zone`` carrying an entry action.

        A thin wrapper over :meth:`request_zone` that leaves the current zone at the
        action's hand-off ``pose`` (the exit position) and activates ``entry_action``
        on arrival. Returns False if the target is unknown/disabled/unreachable.
        """
        return self.request_zone(
            target_zone, None,
            entry_action=entry_action,
            handoff_pose=handoff_pose,
        )

    # ------------------------------------------------------------------
    # Per-tick driver.
    # ------------------------------------------------------------------
    def step(self, curjpos: List[float]) -> Optional[List[float]]:
        """Compute the target for this tick and return it (moved from the old Brain)."""
        self.tick += 1

        # Zone transitions take priority: forward the transition target.
        if self.transitioning:
            t = self.update(curjpos)
            if t is not None:
                self.target_kind = "joint"
                # A transition target (exitpos / startpos) is a curated hand-off pose
                # that must be reached EXACTLY: the arrival check (comparelist against
                # the *unclamped* step in :meth:`update`) only fires when the robot
                # gets there. The exit pose is by definition outside the current zone's
                # safezone (it is the hand-off to the next zone), so flooring it to the
                # safezone would move it and the robot could never reach the unclamped
                # step -> the transition stalls forever and, because this branch takes
                # priority every tick, the whole core freezes (moving=no, stuck).
                # Floor it to the hardware limits only.
                self.target = hardware_clamp(t)
                return self.target
            # Transition just completed; fall through to the zone behaviour.

        if self.mode == "wander":
            self.target = wander(self, curjpos)
            self.target_kind = "joint"
        elif self.mode == "random" and self.tick % self.random_every == 0:
            self.target = random_pose(self)
            self.target_kind = "joint"
        elif (
            self.mode == "action"
            and self.current_action
            and self.tick % self.action_every == 0
        ):
            pose = self.step_action()
            if pose is not None:
                self.target = clamp(self, pose)
                self.target_kind = "joint"
        elif self.mode == "track":
            self.target = track(self, curjpos)
            self.target_kind = "joint"
        # "hold" (and any other case): keep self.target as-is.

        # Floor the final result so every pose reaching the robot is within the
        # hard safety floor (safezone ∩ hardware), regardless of the policy.
        if self.target is not None:
            self.target = clamp(self, self.target)

        return self.target

    def update(self, curjpos: List[float]) -> Optional[List[float]]:
        """Drive the transition one step. Returns the target pose, or None when idle."""
        if self._transition is None:
            return None
        t = self._transition
        target = t["steps"][t["i"]]
        if comparelist(curjpos, target, margin=0.1, count=5):
            t["i"] += 1
            if t["i"] >= len(t["steps"]):
                # Arrived: commit the new zone, carrying the entry action if any.
                self._commit_zone(t["zone"], t.get("entry_action"))
                return None
        return target

    def _commit_zone(self, name: str, entry_action: Optional[str] = None) -> None:
        """Commit arrival at ``name``.

        Adopts the zone's speed; adopts the zone's default behaviour mode **if it
        declares one** (else keeps the current mode); ends the transition.

        When ``entry_action`` is given (an exit action's ``target.action`` pointing at
        this zone), it is played on arrival — this is the "entry action" hook. Without
        one, any playing action is cleared (the legacy behaviour).
        """
        zone = self.zones.get(name)
        self.current_zone = name
        self.speed = zone.speed
        dm = zone.default_mode
        if dm is not None:
            self.mode = dm
        self._transition = None
        if entry_action and entry_action in zone.actions() and zone.action_enabled(entry_action):
            # Entry action: play it (and make sure the action policy is active).
            self.play_action(entry_action)
            if self.mode != "action":
                self.mode = "action"
        else:
            self.clear_action()

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
