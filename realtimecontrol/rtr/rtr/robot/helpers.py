"""Joint-limit math, lifted from ``kukart/robothelpers.py``.

These are pure functions over 6-axis joint poses and per-axis ``(min, max)`` limits.
They are the safety layer: every pose the core commands is clamped through
:func:`fitlimits` before it reaches the robot.
"""

from typing import List, Optional, Sequence, Tuple

# Joint poses are 6-element lists (A1..A6). A6 (index 5) is a rotary axis with no
# meaningful soft limit, so limit checks skip it.
JOINT_COUNT = 6
LIMITED_JOINTS = 5  # A1..A5 are soft-limited; A6 is free.


def fitlimits(joint: int, angle: float, limits: Sequence[Tuple[float, float]]) -> float:
    """Clamp ``angle`` for ``joint`` into ``limits[joint]``."""
    lo, hi = limits[joint]
    if angle < lo:
        return lo
    if angle > hi:
        return hi
    return angle


def checklimits(joint: int, angle: float, limits: Sequence[Tuple[float, float]]) -> bool:
    """True if ``angle`` is within ``limits[joint]``."""
    lo, hi = limits[joint]
    return lo <= angle <= hi


def posSafe(pos: Sequence[float], limits: Sequence[Tuple[float, float]]) -> bool:
    """True if every limited joint of ``pos`` is within its limit in ``limits``.

    A6 (index 5) is always treated as free.
    """
    for i in range(min(LIMITED_JOINTS, len(pos))):
        if not checklimits(i, pos[i], limits):
            return False
    return True


def comparelist(
    list1: Sequence[float],
    list2: Sequence[float],
    margin: float,
    count: Optional[int] = None,
) -> bool:
    """True if the first ``count`` elements of both lists are within ``margin``.

    ``count`` defaults to the length of ``list1``. Used to decide whether the robot
    has reached a target pose.
    """
    n = len(list1) if count is None else count
    if len(list1) < n or len(list2) < n:
        return False
    return all(abs(a - b) <= margin for a, b in zip(list1[:n], list2[:n]))


def clamp_pose(pose: Sequence[float], limits: Sequence[Tuple[float, float]]) -> List[float]:
    """Return a copy of ``pose`` with every limited joint clamped to ``limits``."""
    return [fitlimits(i, pose[i], limits) for i in range(len(pose))]
