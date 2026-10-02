"""Behaviour policies: the motion math for the state machine.

These are the variable-axis behaviour policies and the clamp helpers. Each behaviour
drives the *variable* axes of the current action while the *non-variable* axes are
held at the action's ``base_pose``. The :class:`~rtr.state.machine.StateMachine`'s
per-tick ``step`` dispatches to :func:`build_target` for variable-axis actions; legacy
fixed-pose actions (a ``pos`` list) are stepped by the machine's ``step_action``.

Camera-driven behaviours (``track`` / ``focus`` / ``look``) read the camera telemetry
the brain stashes on ``machine.camera_state`` each tick — the brain no longer nudges
the target directly.
"""

import math
import random
from typing import List

from ..robot.helpers import fitlimits
from .zones import HARDWARE_LIMITS, effective_limits

# Default gain for camera-driven behaviours (degrees of joint travel per pixel of
# offset). An action may override this with its own ``gain`` field.
_DEFAULT_GAIN = 0.05

# ``look`` is a reduced-motion behaviour: it moves less than ``track`` for the same
# offset. Applied on top of the (per-action or default) gain.
_LOOK_FACTOR = 0.5


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

    Used for zone-transition targets: the transition pose sits outside the current
    zone's safezone by design, so the safezone floor must not apply — only the
    hardware floor. This keeps the target reachable so the machine's arrival check
    fires and the transition can complete.
    """
    return [fitlimits(i, pose[i], HARDWARE_LIMITS) for i in range(len(pose))]


def build_target(machine, curjpos: List[float]):
    """Compute the target pose for a *variable-axis* action this tick.

    Returns the pose, or ``None`` if there is no active action or the action is a
    legacy fixed-pose action (those are stepped by the machine's ``step_action``).
    The non-variable axes stay at ``base_pose``; the variable axes are driven by the
    action's behaviour.
    """
    if not machine.current_action:
        return None
    zone = machine.zones.get(machine.current_zone)
    name = machine.current_action
    action = zone._action(name)
    if not action or "pos" in action:
        return None  # no variable-axis action active (none, unknown, or legacy)

    base = zone.action_base_pose(name)
    if base is None:
        return None
    pose = list(base)
    _apply_behavior(machine, pose, zone.action_variable_axes(name),
                    zone.action_behavior(name), curjpos, action)
    return pose


def _apply_behavior(machine, pose, var_axes, behavior, curjpos, action) -> None:
    """Drive the variable axes according to the behaviour policy.

    Non-variable axes remain at ``base_pose`` (already set in ``pose``).
    """
    if not var_axes or behavior == "hold":
        return

    limits = effective(machine)
    gain = _DEFAULT_GAIN
    if isinstance(action, dict):
        g = action.get("gain")
        if isinstance(g, (int, float)) and not isinstance(g, bool) and g:
            gain = g

    if behavior in ("track", "look"):
        _apply_camera(machine, pose, var_axes, "dx", behavior, gain)
    elif behavior == "focus":
        _apply_camera(machine, pose, var_axes, "dy", "focus", gain)
    elif behavior == "scan":
        _apply_scan(machine, pose, var_axes, limits)
    elif behavior == "wander":
        _apply_wander(machine, pose, var_axes, limits, curjpos)
    elif behavior == "random":
        _apply_random(pose, var_axes, limits)


def _apply_camera(machine, pose, var_axes, cam_key, behavior, gain) -> None:
    """Drive variable axes from camera telemetry (track/focus/look).

    The camera offset (``dx`` or ``dy``, pixels) is scaled by the gain and added to
    the base_pose value of each variable axis. A zero offset means no motion (the
    axis rests at base_pose). ``look`` uses a reduced gain (see :data:`_LOOK_FACTOR`).
    """
    state = machine.camera_state or {}
    offset = state.get(cam_key, 0.0)
    if not isinstance(offset, (int, float)) or isinstance(offset, bool):
        offset = 0.0
    if behavior == "look":
        offset *= _LOOK_FACTOR
    if offset == 0.0:
        return
    for axis in var_axes:
        pose[axis] = pose[axis] + offset * gain


def _apply_scan(machine, pose, var_axes, limits) -> None:
    """Sweep variable axes back and forth within the safezone (sine oscillation)."""
    t = machine.tick * 0.1
    for axis in var_axes:
        lo, hi = limits[axis]
        center = (lo + hi) / 2.0
        amplitude = (hi - lo) / 2.0
        pose[axis] = center + amplitude * math.sin(t)


def _apply_wander(machine, pose, var_axes, limits, curjpos) -> None:
    """Gentle continuous drift of variable axes (the legacy wander policy)."""
    for axis in var_axes:
        adj = machine.limitadjust[axis] if axis < len(machine.limitadjust) else 5.0
        val = (0.5 - random.random()) * adj * (1.0 / machine.tick_hz)
        pose[axis] = fitlimits(axis, curjpos[axis] + val, limits)


def _apply_random(pose, var_axes, limits) -> None:
    """Jump variable axes to random values within the safezone."""
    for axis in var_axes:
        lo, hi = limits[axis]
        pose[axis] = random.uniform(lo, hi)
