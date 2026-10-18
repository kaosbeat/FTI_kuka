"""Behaviour policies: the motion math for the state machine.

These are the variable-axis behaviour policies and the clamp helpers. Each behaviour
drives the *variable* axes of the current action while the *non-variable* axes are
held at the current anchor pose (``poses[action_index]``). The
:class:`~rtr.state.machine.StateMachine`'s per-tick ``step`` dispatches to
:func:`build_target` for every action: it reads the current anchor, applies the
wiggle (variable axes + behaviour) on top, and advances the anchor index on the
configured cadence.

Camera-driven behaviours (``locked`` / ``intent`` / ``curious``) read the camera telemetry
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

# ``curious`` is a reduced-motion behaviour: it moves less than ``locked`` for the same
# offset. Applied on top of the (per-action or default) gain.
_LOOK_FACTOR = 0.5

# ``gentle`` is a reduced-motion behaviour driven by the close camera's face offset:
# it nudges the variable axes toward the face (kept subtle) so the robot holds its
# exterior pose and only gestures toward the face location.
_FACE_FACTOR = 0.5


def _bp(machine, behavior, key, default):
    """Read a per-behavior param off the machine's ``behavior_params``, else ``default``.

    ``behavior_params`` is a plain dict the brain writes from the config (see
    ``Brain.reload``); this keeps the behaviour policies pure (no config loading).
    Missing section / behavior / key all fall back to ``default``.
    """
    params = getattr(machine, "behavior_params", None) or {}
    entry = params.get(behavior)
    if not isinstance(entry, dict):
        return default
    val = entry.get(key)
    return val if isinstance(val, (int, float)) and not isinstance(val, bool) else default


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
    """Compute the target pose for the current action this tick.

    Reads the *current* anchor (``poses[action_index]``), applies the action's wiggle
    (variable axes + behaviour) on top, and returns the pose. The non-variable axes
    stay at the current anchor; the variable axes are driven by the action's
    behaviour. When ``machine.forced_behavior`` is set (live test override), the
    forced behavior's policy runs on the action's variable axes instead of the
    action's declared behavior. Returns ``None`` if there is no active action or
    the action has no ``poses``.
    """
    if not machine.current_action:
        machine.servo_state = {}
        return None
    zone = machine.zones.get(machine.current_zone)
    name = machine.current_action
    action = zone._action(name)
    poses = action.get("poses")
    if not (isinstance(poses, list) and poses):
        machine.servo_state = {}
        return None  # no active action, or the action has no poses
    idx = machine.action_index if 0 <= machine.action_index < len(poses) else 0
    anchor = list(poses[idx])
    # Forced behavior (live test override): run this behavior's policy on the
    # action's variable axes regardless of the action's declared behavior.
    active_behavior = machine.forced_behavior or zone.action_behavior(name)
    _apply_behavior(machine, anchor, zone.action_variable_axes(name),
                    active_behavior, curjpos, action)
    return anchor


def _apply_behavior(machine, pose, var_axes, behavior, curjpos, action) -> None:
    """Drive the variable axes according to the behaviour policy.

    Non-variable axes remain at the current anchor (already set in ``pose``).
    """
    # ``calm`` and ``bridge`` are no-op policies: they drive no variable axes (a bridge
    # action holds its curated poses so the robot transitions safely between zones).
    if not var_axes or behavior in ("calm", "bridge"):
        return

    limits = effective(machine)
    # Visual-servoing gain: the fraction of the image error corrected per tick
    # (dimensionless). Priority: per-action ``gain`` (zones.json) > per-behavior
    # ``gain`` (brain config) > built-in default.
    image_gain = _bp(machine, behavior, "gain", _DEFAULT_IMAGE_GAIN)
    if isinstance(action, dict):
        g = action.get("gain")
        if isinstance(g, (int, float)) and not isinstance(g, bool) and g:
            image_gain = g

    if behavior in ("locked", "curious", "intent"):
        _apply_camera(machine, pose, var_axes, behavior, curjpos, image_gain, action)
    elif behavior == "gentle":
        _apply_face(machine, pose, var_axes, curjpos, image_gain, action, behavior)
    elif behavior == "vigilant":
        _apply_scan(machine, pose, var_axes, limits)
    elif behavior == "restless":
        _apply_wander(machine, pose, var_axes, limits, curjpos)
    elif behavior == "spontaneous":
        _apply_random(pose, var_axes, limits)
    elif behavior == "soothing":
        _apply_oscillation(machine, pose, var_axes, limits,
                           freq=_bp(machine, behavior, "freq", 0.5),
                           amp_scale=_bp(machine, behavior, "amp_scale", 0.1))
    elif behavior == "agitated":
        _apply_oscillation(machine, pose, var_axes, limits,
                           freq=_bp(machine, behavior, "freq", 1.0),
                           amp_scale=_bp(machine, behavior, "amp_scale", 0.3))
    elif behavior == "aggressive":
        _apply_oscillation(machine, pose, var_axes, limits,
                           freq=_bp(machine, behavior, "freq", 1.0),
                           amp_scale=_bp(machine, behavior, "amp_scale", 0.3))
    elif behavior == "exhilarated":
        _apply_oscillation(machine, pose, var_axes, limits,
                           freq=_bp(machine, behavior, "freq", 2.0),
                           amp_scale=_bp(machine, behavior, "amp_scale", 0.5))
    elif behavior == "impatient":
        _apply_oscillation(machine, pose, var_axes, limits,
                           freq=_bp(machine, behavior, "freq", 1.0),
                           amp_scale=_bp(machine, behavior, "amp_scale", 0.3))
    elif behavior == "drowsy":
        _apply_oscillation(machine, pose, var_axes, limits,
                           freq=_bp(machine, behavior, "freq", 0.3),
                           amp_scale=_bp(machine, behavior, "amp_scale", 0.2))


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
    the current anchor). ``curious`` uses a reduced offset (see :data:`_LOOK_FACTOR`).

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
    if behavior == "curious":
        look = _bp(machine, "curious", "look_factor", _LOOK_FACTOR)
        dx *= look
        dy *= look
    _servo(machine, pose, var_axes, dx, dy, curjpos, gain, action, behavior)


def _apply_face(machine, pose, var_axes, curjpos, gain, action, behavior) -> None:
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
    face = _bp(machine, "gentle", "face_factor", _FACE_FACTOR)
    dx *= face
    dy *= face
    _servo(machine, pose, var_axes, dx, dy, curjpos, gain, action, behavior)


def _servo(machine, pose, var_axes, dx, dy, curjpos, gain, action, behavior) -> None:
    """Map the image offset to joint deltas via the image Jacobian.

    Shared by the camera behaviours (``locked`` / ``intent`` / ``curious``) and the face
    behaviour:

        dq = -gain · pinv2(J_img_var) · (dx, dy)

    ``gain`` is the fraction of the image error corrected per tick (dimensionless),
    so the offset decays to zero (convergent). A zero offset means no motion (the
    axes rest at the current anchor).

    Falls back to the legacy fixed-axis gain when the kinematics are not wired
    (``machine.camera_geometry`` is ``None``), e.g. a bare sim.

    Records the servo state (``machine.servo_state``) on every call — including the
    zero-offset case — so the display always reflects the last tick's vector.

    **Recentering (the hunting ``track`` action).** When a person is locked and the
    robot is in the hunting zone's ``track`` action (behavior ``locked``), this is the
    recentering loop: the camera's track offset ``(dx, dy)`` is driven to zero so the
    locked person stays at the image center. The conditions for it to run are:

    1. The action declares ``variable_axes`` (the joints the servo may move) and a
       camera-servo behavior (``locked`` / ``intent`` / ``curious``) — the hunting
       ``track`` action uses ``variable_axes: [0, 1, 4]`` and behavior ``locked``.
    2. The brain stashes a fresh track offset on ``machine.camera_state`` (the
       ``CAM_TRACK`` telemetry for the locked id; a stale/absent offset is 0).
    3. The kinematics are wired (``machine.camera_geometry`` is not ``None``), so the
       image Jacobian (which carries the camera-rotation compensation) is available.

    The image Jacobian is derived from the camera's *current* pose (position *and*
    orientation), so the joint deltas already compensate for how the camera is
    mounted/rotated — no separate rotation term is needed.

    **Safezone clamp.** The resulting variable-axis pose is clamped to the effective
    floor (the current zone's safezone ∩ the hardware limits) before it is recorded,
    so a recentering command can never leave the zone's safe bounds. This matches the
    clamping in :func:`_apply_wander` / :func:`_apply_oscillation`; the
    ``StateMachine.step`` driver clamps the final target again (idempotent). When the
    clamp truncates a delta, ``dq_deg`` still reports the requested (Jacobian) delta
    and ``proposed`` reports the clamped pose, so the debug UI shows the difference.

    **Proposed movement.** ``machine.servo_state["proposed"]`` is the full 6-axis
    target pose after the servo deltas are applied and clamped — the exact pose the
    robot is about to be commanded to. The debug UI renders it alongside the
    compensated vector.
    """
    if dx == 0.0 and dy == 0.0:
        machine.servo_state = {
            "behavior": behavior, "dx": 0.0, "dy": 0.0,
            "dq_deg": [0.0] * len(var_axes), "axes": list(var_axes),
            "proposed": list(pose),
        }
        return
    geo = getattr(machine, "camera_geometry", None)
    if geo is None:
        # Legacy fallback: fixed axis-aligned gain (degrees per pixel).
        dq_deg = [dx * _DEFAULT_GAIN] * len(var_axes)
        for axis in var_axes:
            pose[axis] = pose[axis] + dx * _DEFAULT_GAIN
    else:
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
        dq_deg = [q * _DEG_PER_RAD for q in dq_rad]
        for k, ax in enumerate(var_axes):
            pose[ax] = pose[ax] + dq_rad[k] * _DEG_PER_RAD
    # Safezone clamp: keep the recentering command inside the current zone's safe
    # bounds (safezone ∩ hardware), matching _apply_wander / _apply_oscillation.
    limits = effective(machine)
    for axis in var_axes:
        pose[axis] = fitlimits(axis, pose[axis], limits)
    machine.servo_state = {
        "behavior": behavior, "dx": dx, "dy": dy,
        "dq_deg": dq_deg, "axes": list(var_axes),
        "proposed": list(pose),
    }


def _apply_scan(machine, pose, var_axes, limits) -> None:
    """Sweep variable axes back and forth within the safezone (sine oscillation)."""
    t = machine.tick * 0.1 * _bp(machine, "vigilant", "freq", 1.0)
    for axis in var_axes:
        lo, hi = limits[axis]
        center = (lo + hi) / 2.0
        amplitude = (hi - lo) / 2.0
        pose[axis] = center + amplitude * math.sin(t)


def _apply_wander(machine, pose, var_axes, limits, curjpos) -> None:
    """Gentle continuous drift of variable axes (the legacy wander policy)."""
    for axis in var_axes:
        adj = machine.limitadjust[axis] if axis < len(machine.limitadjust) else \
            _bp(machine, "restless", "wander_adj", 5.0)
        val = (0.5 - random.random()) * adj * (1.0 / machine.tick_hz)
        pose[axis] = fitlimits(axis, curjpos[axis] + val, limits)


def _apply_random(pose, var_axes, limits) -> None:
    """Jump variable axes to random values within the safezone."""
    for axis in var_axes:
        lo, hi = limits[axis]
        pose[axis] = random.uniform(lo, hi)


def _apply_oscillation(machine, pose, var_axes, limits, freq=1.0, amp_scale=0.5) -> None:
    """Oscillate the variable axes around the action's base pose (bounded sine).

    Shared by the animal behaviours (``soothing`` / ``agitated`` / ``aggressive`` / ``exhilarated`` /
    ``impatient`` / ``drowsy``). ``freq`` scales the sine rate (relative to the vigilant base
    rate); ``amp_scale`` scales the amplitude so the motion stays a small fraction of
    the safezone width. The axis is clamped to the effective safezone so the pose
    always remains within the zone's safe bounds (no collision from overshoot).
    """
    t = machine.tick * 0.1 * freq
    for axis in var_axes:
        lo, hi = limits[axis]
        center = pose[axis]  # the current-anchor value (pose starts as the anchor)
        amplitude = (hi - lo) / 2.0 * amp_scale
        val = center + amplitude * math.sin(t)
        pose[axis] = max(lo, min(hi, val))
