"""SafetyGate: pre-movement safety checks (red-link conflict + collision).

The gate mirrors the editor's ``edgeConflict`` logic (``editor.html`` line 649):
a move between two actions is *conflicting* when the two zones' safezones do
not overlap on at least one axis, or when the source action's end pose and the
target action's entry pose differ by >= 0.5 on any axis.

The collision check delegates to a persistent Node worker (see
:mod:`rtr.safety.collide_worker`) that runs the SAT self-collision test on the
KR60 link models.
"""

from typing import List, Optional, Tuple

from ..state.zones import Zones, find_shared_pose


AXES = ["A1", "A2", "A3", "A4", "A5", "A6"]


class SafetyGate:
    """Pre-movement safety checks: red-link conflict + optional collision.

    Parameters
    ----------
    zones : Zones
        The zone table (provides safezone + action poses for conflict checks).
    collision_checker : CollisionChecker or None
        When provided, the gate also runs a self-collision check on the target
        entry pose before allowing the move. When None, only the red-link
        conflict check runs (degraded mode).
    """

    def __init__(self, zones: Zones, collision_checker=None):
        self.zones = zones
        self.collision = collision_checker

    # ------------------------------------------------------------------
    # Red-link conflict (mirrors editor.html edgeConflict, line 649).
    # ------------------------------------------------------------------
    def check_conflict(self, from_zone: str, from_action: Optional[str],
                       to_zone: str, to_action: str) -> Tuple[bool, List[str]]:
        """Check the red-link conflict for a move from ``(from_zone, from_action)``
        to ``(to_zone, to_action)``.

        Returns ``(conflict, issues)`` where ``issues`` is a list of human-readable
        reason strings. ``conflict`` is True when at least one issue exists.

        Rules (mirrors ``editor.html`` ``edgeConflict``):

        **Inter-zone** (``from_zone != to_zone``):
        1. **Safezone overlap**: the two zones' safezones must overlap on all 6
           axes. For each axis, the overlap gap is reported when
           ``max(lo_a, lo_b) >= min(hi_a, hi_b)``. When either zone has no
           safezone data, the issue is ``"no safezone"``.
        2. **Shared-position continuity**: the source and target actions must share
           at least one pose (any anchor appearing in both pose lists, within 0.5
           degrees on every axis) so the robot can hand off at that pose. When the
           source action is None (no current action) or the two actions share no
           pose, this check is skipped / flagged accordingly, but the safezone
           overlap still applies.

        **Intra-zone** (``from_zone == to_zone``): the shared-position check does
        NOT apply — the robot moves freely within the zone, so any pose inside the
        zone's safezone is a valid hand-off. The only conflict is a connection pose
        (source end / target entry) that lies OUTSIDE the zone's safezone.
        """
        issues: List[str] = []
        zA = self.zones.get(from_zone)
        zB = self.zones.get(to_zone)

        if from_zone == to_zone:
            # Intra-zone: no shared-position requirement. Flag a connection pose
            # that lies outside the zone's safezone (the robot cannot reach it).
            end = zA.action_end_pose(from_action) if from_action is not None else None
            start = zB.action_entry_pose(to_action)
            end_gaps = self._pose_outside_safezone(end, zA)
            if end_gaps:
                issues.append(
                    f"pose outside {from_zone} safezone on {', '.join(end_gaps)}: "
                    f"{from_zone}/{from_action} ends "
                    f"[{', '.join(str(v) for v in end)}]"
                )
            start_gaps = self._pose_outside_safezone(start, zB)
            if start_gaps:
                issues.append(
                    f"pose outside {to_zone} safezone on {', '.join(start_gaps)}: "
                    f"{to_zone}/{to_action} starts "
                    f"[{', '.join(str(v) for v in start)}]"
                )
        else:
            # Safezone overlap (mirrors zoneOverlapGaps, line 637).
            gaps = self._zone_overlap_gaps(zA, zB)
            if gaps:
                issues.append(f"safezones do not overlap on {', '.join(gaps)}")

            # Shared-position continuity: the two actions must share at least one
            # pose (any anchor appearing in both lists) so the robot can hand off
            # there. When they share no pose, flag it (the robot cannot cross).
            if from_action is not None:
                src = zA._action(from_action).get("poses")
                tgt = zB._action(to_action).get("poses")
                if isinstance(src, list) and isinstance(tgt, list) and src and tgt:
                    if find_shared_pose(src, tgt) is None:
                        issues.append(
                            f"no shared position: {from_zone}/{from_action} shares "
                            f"no pose with {to_zone}/{to_action}"
                        )

        return (len(issues) > 0), issues

    @staticmethod
    def _zone_overlap_gaps(zA, zB) -> List[str]:
        """The axes on which two zones' safezones do NOT overlap.

        Mirrors ``editor.html`` ``zoneOverlapGaps`` (line 637). Returns a list
        of axis names (``["A1", ...]``); empty list = full overlap.
        """
        if zA is None or zB is None:
            return ["no safezone"]
        szA = zA.safezone
        szB = zB.safezone
        if szA is None or szB is None:
            return ["no safezone"]
        gaps: List[str] = []
        for i in range(6):
            alo, ahi = szA[i]
            blo, bhi = szB[i]
            if max(alo, blo) >= min(ahi, bhi):
                gaps.append(AXES[i])
        return gaps

    @staticmethod
    def _pose_outside_safezone(pose, z) -> List[str]:
        """The axes on which a pose lies OUTSIDE a zone's safezone.

        Mirrors the editor's ``.oob`` highlight (and ``editor.html``
        ``poseOutsideSafezone``). Returns a list of axis names (``["A1", ...]``);
        empty list = the pose is within range. A None pose or safezone yields [].
        """
        if pose is None or z is None:
            return []
        sz = z.safezone
        if sz is None:
            return []
        out: List[str] = []
        for i in range(6):
            lo, hi = sz[i]
            if pose[i] < lo or pose[i] > hi:
                out.append(AXES[i])
        return out

    # ------------------------------------------------------------------
    # Collision (delegates to the Node worker).
    # ------------------------------------------------------------------
    def check_collision(self, entry: List[float]) -> dict:
        """Run the self-collision check on a pose.

        Returns a dict with keys:
        - ``collision``: bool (True when a collision is detected)
        - ``collisions``: list of collision details (from the worker)
        - ``error``: str (only when the worker reported an error)

        When no collision checker is wired, returns ``{"collision": False,
        "collisions": []}`` (degraded mode: no collision check).
        """
        if self.collision is None:
            return {"collision": False, "collisions": []}
        return self.collision.check(entry)
