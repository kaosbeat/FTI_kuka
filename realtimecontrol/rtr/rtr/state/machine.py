"""StateMachine: zones, transitions, and actions.

This is the deterministic heart of the core. It owns the *current zone* and the
*in-progress transition* to a new zone, and it manages the current *action* (a
sequence of poses). It does **not** decide the wander/random/track behaviour on its
own — that lives in the :class:`~rtr.brain.brain.Brain`, which asks the state machine
for the transition target and, when idle, produces the zone behaviour itself.

The old ``activateZone`` in ``robothelpers.py`` blocked a thread with a ``while``
loop + ``time.sleep`` while the robot reached the exit/start pose. Here the transition
is just data (a list of poses to pass through), and :meth:`update` advances it one
step per engine tick. Nothing blocks.
"""

from typing import List, Optional

from ..robot.helpers import comparelist, posSafe
from .zones import Zones


class StateMachine:
    """Tracks the current zone, a pending zone transition, and the active action."""

    def __init__(self, zones: Zones, initial_zone: str = "init"):
        self.zones = zones
        self.current_zone = initial_zone
        self.speed = zones.get(initial_zone).speed
        self.current_action: Optional[str] = None
        self.action_index = 0
        self._transition: Optional[dict] = None

    # ------------------------------------------------------------------
    # Requested changes (called by the brain when a command arrives).
    # ------------------------------------------------------------------
    def request_zone(self, name: str, curjpos: List[float]) -> bool:
        """Begin a transition to ``name``. Returns False if the zone is unknown/disabled."""
        if not self.zones.has(name):
            print(f"[state] unknown zone: {name}")
            return False
        if not self.zones.get(name).enabled:
            print(f"[state] zone disabled: {name}")
            return False
        # Never start a transition while one is already in progress.
        if self._transition is not None:
            return False

        zone = self.zones.get(name)
        steps: List[List[float]] = []
        # If we are not already inside the new zone's safezone, leave the current
        # zone through its exit pose first.
        if not posSafe(curjpos, zone.safezone):
            steps.append(self.zones.get(self.current_zone).exitpos)
        steps.append(zone.startpos)
        self._transition = {"zone": name, "steps": steps, "i": 0}
        return True

    def step_action(self) -> Optional[List[float]]:
        """Advance to the next pose of the current action, wrapping around.

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
            self.action_index = 0
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

    # ------------------------------------------------------------------
    # Per-tick driver.
    # ------------------------------------------------------------------
    def update(self, curjpos: List[float]) -> Optional[List[float]]:
        """Drive the transition one step. Returns the target pose, or None when idle."""
        if self._transition is None:
            return None
        t = self._transition
        target = t["steps"][t["i"]]
        if comparelist(curjpos, target, margin=0.1, count=5):
            t["i"] += 1
            if t["i"] >= len(t["steps"]):
                # Arrived: commit the new zone.
                self.current_zone = t["zone"]
                self.speed = self.zones.get(self.current_zone).speed
                self._transition = None
                return None
        return target

    @property
    def transitioning(self) -> bool:
        return self._transition is not None

    def reset(self, curjpos: List[float]) -> None:
        """Snap the state machine to the robot's actual pose (used at startup)."""
        self._transition = None
        self.current_zone = "init"
        self.current_action = None
        self.action_index = 0
        self.speed = self.zones.get(self.current_zone).speed
