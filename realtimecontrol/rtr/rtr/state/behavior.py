"""Behaviour policies: the motion math for the state machine.

These are the wander / random / track policies and the clamp helpers, lifted from the
old ``Brain`` (``brain.py``) as free functions. Each takes the
:class:`~rtr.state.machine.StateMachine` (or the resolved limits) explicitly and reads
the per-axis settings (``limitadjust``, ``track_speed``, ``tick_hz``) and the current
zone from it. The motion math is unchanged from the original ``Brain`` methods — only
its home and its access to the shared state change.

The :class:`~rtr.state.machine.StateMachine`'s per-tick ``step`` dispatches to these;
they are the deterministic core of the behaviour layer.
"""

import random
from typing import List

from ..robot.helpers import fitlimits
from .zones import HARDWARE_LIMITS, effective_limits


def safezone(machine) -> List:
    """The current zone's safezone (the per-axis soft limits)."""
    return machine.zones.get(machine.current_zone).safezone


def effective(machine) -> List:
    """The per-axis hard safety floor: the zone safezone ∩ the hardware limits."""
    return effective_limits(safezone(machine))


def clamp(machine, pose: List[float]) -> List[float]:
    """Clamp every axis (A1–A6) to the effective floor (safezone ∩ hardware)."""
    limits = effective(machine)
    return [fitlimits(i, pose[i], limits) for i in range(len(pose))]


def hardware_clamp(pose: List[float]) -> List[float]:
    """Clamp every axis to the hardware limits only (no safezone).

    Used for zone-transition targets: the exit/start poses sit outside the current
    zone's safezone by design, so the safezone floor must not apply to them — only the
    hardware floor. This keeps them reachable so the state machine's arrival check
    fires and the transition can complete.
    """
    return [fitlimits(i, pose[i], HARDWARE_LIMITS) for i in range(len(pose))]


def wander(machine, curjpos: List[float]) -> List[float]:
    """Gentle continuous drift of A1-A3, wrist derived, clamped to the floor."""
    pose = list(curjpos)
    limits = effective(machine)
    for i in range(3):
        val = (0.5 - random.random()) * machine.limitadjust[i] * (1.0 / machine.tick_hz)
        pose[i] = fitlimits(i, pose[i] + val, limits)
    # Keep the wrist oriented the way the legacy code did.
    pose[4] = fitlimits(4, -(pose[1] + pose[2]), limits)
    return pose


def random_pose(machine) -> List[float]:
    """A fully random pose inside the current zone's effective floor."""
    limits = effective(machine)
    pose = [random.uniform(lo, hi) for (lo, hi) in limits[:5]]
    pose[4] = fitlimits(4, -(pose[1] + pose[2]), limits)
    pose.append(0.0)  # A6 kept at 0 (the step clamp floors it)
    return pose


def track(machine, curjpos: List[float]) -> List[float]:
    """Advance A1 at ``track_speed`` deg/s, clamped to the floor."""
    pose = list(curjpos)
    limits = effective(machine)
    step = machine.track_speed * (1.0 / machine.tick_hz)
    pose[0] = fitlimits(0, pose[0] + step, limits)
    return pose
