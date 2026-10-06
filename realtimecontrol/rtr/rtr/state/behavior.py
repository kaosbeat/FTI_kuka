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
from ..robot.kinematics import pinv2
from .zones import HARDWARE_LIMITS, effective_limits

# Legacy fallback gain (degrees of joint travel per pixel of offset), used only when
# the kinematics are not wired (``machine.camera_geometry`` is None, e.g. a bare sim).
_DEFAULT_GAIN = 0.05

# Default visual-servoing gain: the FRACTION of the image error (dx, dy) corrected
# per tick (dimensionless, 0 < gain < 1 for stability). The image Jacobian maps the
# offset to the exact joint deltas that shrink it, so the target's offset decays to
# zero. An action may override it with its own ``gain`` field. Calibrated per
# zone/action in P4.
_DEFAULT_IMAGE_GAIN = 0.3

_DEG_PER_RAD = 180.0 / math.pi

# ``look`` is a reduced-motion behaviour: it moves less than ``track`` for the same
# offset. Applied on top of the (per-action or default) gain.
_LOOK_FACTOR = 0.5

# ``face`` is a reduced-motion behaviour driven by the close camera's face offset:
# it nudges the variable axes toward the face (kept subtle) so the robot holds its
# exterior pose and only gestures toward the face location.
_FACE_FACTOR = 0.5


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
    # Visual-servoing gain: the fraction of the image error corrected per tick
    # (dimensionless). An action may override it with its own ``gain`` field.
    image_gain = _DEFAULT_IMAGE_GAIN
    if isinstance(action, dict):
        g = action.get("gain")
        if isinstance(g, (int, float)) and not isinstance(g, bool) and g:
            image_gain = g

    if behavior in ("track", "look", "focus"):
        _apply_camera(machine, pose, var_axes, behavior, curjpos, image_gain, action)
    elif behavior == "face":
        _apply_face(machine, pose, var_axes, curjpos, image_gain, action)
    elif behavior == "scan":
        _apply_scan(machine, pose, var_axes, limits)
    elif behavior == "wander":
        _apply_wander(machine, pose, var_axes, limits, curjpos)
    elif behavior == "random":
        _apply_random(pose, var_axes, limits)


def _apply_camera(machine, pose, var_axes, behavior, curjpos, gain, action) -> None:
    """Drive variable axes from the camera offset (visual servoing).

    The image offset (``dx``, ``dy``, pixels) is the error to be driven to zero. The
    tracked target's world position is estimated from the offset + a working depth
    (see :meth:`CameraGeometry.estimate_target`), the **image Jacobian**
    ``d(dx, dy)/d(joints)`` is computed from the camera's *current* pose, and the
    offset is mapped to joint deltas over the variable axes:

        dq = -gain · pinv2(J_img_var) · (dx, dy)

    ``gain`` is the fraction of the image error corrected per tick (dimensionless),
    so the offset decays to zero (convergent). This is correct in *any* arm
    configuration, because the image Jacobian is derived from the exact camera pose
    (position *and* orientation). A zero offset means no motion (the axes rest at
    base_pose). ``look`` uses a reduced offset (see :data:`_LOOK_FACTOR`).

    Falls back to the legacy fixed-axis gain when the kinematics are not wired
    (``machine.camera_geometry`` is ``None``), e.g. a bare sim.
    """
    state = machine.camera_state or {}
    dx = state.get("dx", 0.0)
    dy = state.get("dy", 0.0)
    if not isinstance(dx, (int, float)) or isinstance(dx, bool):
        dx = 0.0
    if not isinstance(dy, (int, float)) or isinstance(dy, bool):
        dy = 0.0
    if behavior == "look":
        dx *= _LOOK_FACTOR
        dy *= _LOOK_FACTOR
    _servo(machine, pose, var_axes, dx, dy, curjpos, gain, action)


def _apply_face(machine, pose, var_axes, curjpos, gain, action) -> None:
    """Drive variable axes from the close camera's face offset (subtle).

    Reads the face offset the brain stashes on ``machine.face_state`` each tick
    (the face bbox centroid minus the image center, in pixels). The offset is
    reduced by :data:`_FACE_FACTOR` so the robot only gestures toward the face,
    holding its exterior pose.
    """
    state = machine.face_state or {}
    dx = state.get("dx", 0.0)
    dy = state.get("dy", 0.0)
    if not isinstance(dx, (int, float)) or isinstance(dx, bool):
        dx = 0.0
    if not isinstance(dy, (int, float)) or isinstance(dy, bool):
        dy = 0.0
    dx *= _FACE_FACTOR
    dy *= _FACE_FACTOR
    _servo(machine, pose, var_axes, dx, dy, curjpos, gain, action)


def _servo(machine, pose, var_axes, dx, dy, curjpos, gain, action) -> None:
    """Map the image offset to joint deltas via the image Jacobian.

    Shared by the camera behaviours (``track`` / ``focus`` / ``look``) and the face
    behaviour:

        dq = -gain · pinv2(J_img_var) · (dx, dy)

    ``gain`` is the fraction of the image error corrected per tick (dimensionless),
    so the offset decays to zero (convergent). A zero offset means no motion (the
    axes rest at base_pose).

    Falls back to the legacy fixed-axis gain when the kinematics are not wired
    (``machine.camera_geometry`` is ``None``), e.g. a bare sim.
    """
    if dx == 0.0 and dy == 0.0:
        return
    geo = getattr(machine, "camera_geometry", None)
    if geo is None:
        # Legacy fallback: fixed axis-aligned gain (degrees per pixel).
        for axis in var_axes:
            pose[axis] = pose[axis] + dx * _DEFAULT_GAIN
        return
    # Working depth for the target estimate: an action may override it with a
    # ``target_depth`` field (metres); otherwise the geometry default is used.
    depth = geo.target_depth
    if isinstance(action, dict):
        td = action.get("target_depth")
        if isinstance(td, (int, float)) and not isinstance(td, bool) and td > 0:
            depth = td
    # Estimate the tracked target's world position, then the image Jacobian.
    target = geo.estimate_target(curjpos, dx, dy, depth)
    J = geo.image_jacobian(curjpos, target)
    # Reduce the image Jacobian to the variable axes and invert it.
    J_var = [[J[r][ax] for ax in var_axes] for r in range(2)]
    P = pinv2(J_var)
    n = len(var_axes)
    dq_rad = [-gain * sum(P[i][c] * [dx, dy][c] for c in range(2)) for i in range(n)]
    for k, ax in enumerate(var_axes):
        pose[ax] = pose[ax] + dq_rad[k] * _DEG_PER_RAD


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
