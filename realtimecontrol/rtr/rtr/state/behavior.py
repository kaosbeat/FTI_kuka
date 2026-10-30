"""Behaviour policies: the motion math for the state machine.

These are the behaviour policies and the clamp helpers. Each behaviour drives the
*free* (``"x"``) axes of the current action while the *absolute* (numbered) axes are
held at the current anchor pose (``poses[action_index]``). The
:class:`~rtr.state.machine.StateMachine`'s per-tick ``step`` dispatches to
:func:`build_target` for every action: it reads the current anchor, applies the
behavior on the free axes, and advances the anchor index on the configured cadence.

Camera-driven behaviours (``locked`` / ``intent`` / ``curious``) read the camera telemetry
the brain stashes on ``machine.camera_state`` each tick — the brain no longer nudges
the target directly.
"""

import math
import random
from typing import List

from ..robot.helpers import fitlimits
from ..robot.kinematics import cross, pinv2, pinv3
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

# Default test-sweep amplitude: the fraction of the safezone half-width the camera
# explores each tick (see :func:`_target_aware_sweep`). Overridable per behaviour via
# the brain config's ``sweep_amp`` key (the brain-tab cards). Kept modest so the camera
# servo still holds the target centred while the axes are exercised for the test.
_SWEEP_AMP = 0.5

# The reference lock distance (metres) the sweep amplitude is normalised against: a
# target at this range moves the camera by exactly ``sweep_amp`` of the safezone
# half-width. A target farther away scales the amplitude up (more room to roam while
# the target stays in view); a closer target scales it down.
_SWEEP_DIST_REF = 2.0

# Per-behaviour per-tick base-frame displacement scale (metres), multiplied by
# ``sweep_amp``. The per-tick delta integrates to a position oscillation of roughly
# 10x the per-tick magnitude (sum of sin over the ~63-tick period), so these are
# kept small to roam within the safezone without the clamp doing all the work.
_SWEEP_LOCKED_SCALE = 0.02
_SWEEP_INTENT_SCALE = 0.05
_SWEEP_CURIOS_SCALE = 0.08


def _sweep_enabled(action) -> bool:
    """True when the action opts into the test sweep (``"sweep": true`` in zones.json)."""
    return isinstance(action, dict) and action.get("sweep") is True


def _sweep_amp(machine, behavior) -> float:
    """The sweep amplitude (fraction of the safezone half-width) for a behaviour.

    Read from the per-behaviour brain params (``sweep_amp`` on the brain-tab card),
    falling back to :data:`_SWEEP_AMP`.
    """
    return _bp(machine, behavior, "sweep_amp", _SWEEP_AMP)


def _perp(v):
    """A unit vector perpendicular to ``v`` (cross with the up vector; falling back to
    the x-axis when ``v`` is parallel to up). Returns ``[0,0,0]`` for a degenerate ``v``.
    """
    c = cross(v, [0.0, 0.0, 1.0])
    n = math.sqrt(sum(x * x for x in c))
    if n < 1e-6:
        c = cross(v, [1.0, 0.0, 0.0])
        n = math.sqrt(sum(x * x for x in c))
    if n < 1e-6:
        return [0.0, 0.0, 0.0]
    return [c[k] / n for k in range(3)]


def _perp2(v):
    """A unit vector perpendicular to both ``v`` and :func:`_perp(v)`.

    ``cross(_perp(v), v)`` gives a vector orthogonal to both ``v`` and ``_perp(v)``.
    Used for the ``curious`` behaviour to explore a direction distinct from ``locked``.
    Returns ``[0,0,0]`` for a degenerate ``v``.
    """
    p = _perp(v)
    c = cross(p, v)
    n = math.sqrt(sum(x * x for x in c))
    if n < 1e-6:
        return [0.0, 0.0, 0.0]
    return [c[k] / n for k in range(3)]


def _lock_info(machine, curjpos, action):
    """Compute the lock geometry for the target-aware sweep.

    Returns a dict with:
      - ``cam``: the camera position in the base frame (metres),
      - ``target``: the tracked target's world position (metres),
      - ``lock``: the lock vector ``target - cam`` (camera → target, metres),
      - ``dist``: the lock length (metres),
      - ``depth``: the working depth used for the target estimate (metres),
      - ``jacobian``: the camera position Jacobian (3x6, m/rad).

    Returns ``None`` when the kinematics are not wired (the sweep falls back to the
    legacy blind drift).
    """
    geo = getattr(machine, "camera_geometry", None)
    if geo is None:
        return None
    state = machine.camera_state or {}
    dx = state.get("dx", 0.0)
    dy = state.get("dy", 0.0)
    if not isinstance(dx, (int, float)) or isinstance(dx, bool):
        dx = 0.0
    if not isinstance(dy, (int, float)) or isinstance(dy, bool):
        dy = 0.0
    depth = geo.target_depth
    if isinstance(action, dict):
        td = action.get("target_depth")
        if isinstance(td, (int, float)) and not isinstance(td, bool) and td > 0:
            depth = td
    C, _R = geo.camera_pose(curjpos)
    T = geo.estimate_target(curjpos, dx, dy, depth)
    L = [T[k] - C[k] for k in range(3)]
    D = math.sqrt(sum(x * x for x in L))
    return {"cam": C, "target": T, "lock": L, "dist": D, "depth": depth,
            "jacobian": geo.camera_position_jacobian(curjpos)}


def _move_cam(machine, pose, var_axes, info, delta_C) -> None:
    """Move the camera by a base-frame displacement ``delta_C`` (metres).

    Converts the displacement to joint deltas via the camera position Jacobian
    (reduced to the variable axes): ``dq = pinv3(Jc_var) @ delta_C``. The minimum-norm
    solve spreads the move across the variable axes; the servo then re-centers the
    target at the new camera position.
    """
    J = info["jacobian"]
    J_var = [[J[r][ax] for ax in var_axes] for r in range(3)]
    P = pinv3(J_var)
    n = len(var_axes)
    dq_rad = [sum(P[i][c] * delta_C[c] for c in range(3)) for i in range(n)]
    for k, ax in enumerate(var_axes):
        pose[ax] = pose[ax] + dq_rad[k] * _DEG_PER_RAD


def _sweep_locked(machine, pose, var_axes, info, amp, t):
    """Locked sweep: tiny perpendicular drift (short path, all axes)."""
    L = info["lock"]
    d = _perp(L)
    mag = amp * _SWEEP_LOCKED_SCALE * math.sin(t)
    _move_cam(machine, pose, var_axes, info, [d[k] * mag for k in range(3)])


def _sweep_intent(machine, pose, var_axes, info, amp, t):
    """Intent sweep: radial drift along the lock vector (zoom out / wider view)."""
    D = info["dist"]
    if D < 1e-6:
        return
    L = info["lock"]
    d = [L[k] / D for k in range(3)]
    mag = amp * _SWEEP_INTENT_SCALE * math.sin(t + math.pi / 3.0)
    _move_cam(machine, pose, var_axes, info, [d[k] * mag for k in range(3)])


def _sweep_curious(machine, pose, var_axes, info, amp, t):
    """Curious sweep: broad perpendicular drift (view the target from different sides)."""
    L = info["lock"]
    d = _perp2(L)
    mag = amp * _SWEEP_CURIOS_SCALE * math.sin(t + 2.0 * math.pi / 3.0)
    _move_cam(machine, pose, var_axes, info, [d[k] * mag for k in range(3)])


def _sweep_gentle(machine, pose, var_axes, floor, t):
    """Gentle sweep: sweep one variable axis at a time (sequential, joint-space).

    Cycles through the variable axes every 30 ticks; the selected axis receives a
    small sinusoidal increment while the others hold. No lock geometry is needed.
    """
    if not var_axes:
        return
    ax = var_axes[(machine.tick // 30) % len(var_axes)]
    lo, hi = floor[ax]
    amp = (hi - lo) / 2.0 * _sweep_amp(machine, "gentle") * 0.01
    pose[ax] += amp * math.sin(t)


def _sweep_blind(machine, pose, var_axes, floor, t):
    """Blind sweep: legacy per-axis sinusoidal drift (fallback when kinematics are not wired)."""
    a = _sweep_amp(machine, "locked") * 0.01
    for ax in var_axes:
        lo, hi = floor[ax]
        pose[ax] += (hi - lo) / 2.0 * a * math.sin(t)


def _add_sweep(machine, pose, var_axes, behavior, curjpos, action, axis_bands=None) -> None:
    """Target-aware test sweep: move the camera to a new position within the safezone
    while keeping the locked target in view, using a behaviour-specific strategy.

    Runs on top of the camera servo (see :func:`_apply_behavior`). Unlike the old
    blind sinusoidal drift, the motion is computed from the **lock vector** (the
    camera→target direction): each behaviour explores the safezone in a different way,
    all while the servo keeps the target centred. The amplitude is a fraction of the
    safezone half-width (the brain-tab ``sweep_amp``).

    Behaviour strategies:
      - ``locked``: minimal motion (short path) — a tiny perpendicular drift.
      - ``intent``: radial drift (zoom out / wider view, then return).
      - ``curious``: broad perpendicular drift (view the target from different sides).
      - ``gentle``: sweep one variable axis at a time (sequential, joint-space).

    Falls back to the legacy blind drift when the kinematics are not wired.
    """
    limits = effective(machine)
    if axis_bands:
        floor = list(limits)
        for ax, (lo, hi) in axis_bands.items():
            floor[ax] = [max(lo, limits[ax][0]), min(hi, limits[ax][1])]
    else:
        floor = limits

    t = machine.tick * 0.1
    if behavior == "gentle":
        _sweep_gentle(machine, pose, var_axes, floor, t)
    else:
        info = _lock_info(machine, curjpos, action)
        if info is None:
            _sweep_blind(machine, pose, var_axes, floor, t)
        else:
            amp = _sweep_amp(machine, behavior)
            if behavior == "locked":
                _sweep_locked(machine, pose, var_axes, info, amp, t)
            elif behavior == "intent":
                _sweep_intent(machine, pose, var_axes, info, amp, t)
            elif behavior == "curious":
                _sweep_curious(machine, pose, var_axes, info, amp, t)
            else:
                _sweep_blind(machine, pose, var_axes, floor, t)

    for ax in var_axes:
        pose[ax] = fitlimits(ax, pose[ax], floor)
    # Keep the debug UI's proposed pose in step with the swept target.
    if isinstance(machine.servo_state, dict) and "proposed" in machine.servo_state:
        machine.servo_state["proposed"] = list(pose)


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


def resolve_x(pose, ref):
    """Resolve ``"x"`` (frozen / copy-from-previous) axes in a pose to ``ref``.

    A pose axis set to the string ``"x"`` means "copy the robot's current value on
    this axis and freeze it for the action" (e.g. the camera-lock pose's A1 when a
    claw action starts). Returns a new pose with every ``"x"`` replaced by the
    matching value from ``ref`` (the frozen base, or the live ``curjpos`` as a
    fallback); non-``"x"`` axes are copied through unchanged.
    """
    if not isinstance(pose, list):
        return pose
    return [ref[ax] if ax < len(ref) and pose[ax] == "x" else pose[ax]
            for ax in range(len(pose))]


def build_target(machine, curjpos: List[float]):
    """Compute the target pose for the current action this tick.

    Reads the *current* anchor (``poses[action_index]``) and returns the target pose.
    Each axis is either absolute (a number — pinned to the pose value) or free
    (``"x"`` — driven by the action's behavior: face-tracking tracks the face,
    ``spontaneous`` is random, ``bridge`` holds or face-tracks, ...). The free axes
    start at the robot's current value and are driven from there; the absolute axes
    hold their pose value. When ``machine.forced_behavior`` is set (live test
    override), the forced behavior's policy runs on the free axes instead of the
    action's declared behavior. Returns ``None`` if there is no active action or the
    action has no ``poses``.
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

    axis_bands = zone.action_axis_bands(name)
    if axis_bands:
        # Active-class axis_bands (track/farfocus/approach/facefocus): the anchor is
        # the robot's *current* position clamped into the band (recalculated every
        # tick), so the servo roams within the zone safezone instead of looping
        # through anchor poses. The variable axes are the band's keys; a band with
        # min == max is a fixed axis (excluded — the servo never moves it). The
        # servo drives the remaining axes to keep the locked person centered,
        # hard-clamped to [min, max] ∩ safezone (see :func:`_servo`).
        anchor = [float(c) for c in curjpos]
        limits = effective(machine)
        for ax, (lo, hi) in axis_bands.items():
            anchor[ax] = max(max(lo, limits[ax][0]), min(min(hi, limits[ax][1]), anchor[ax]))
        var_axes = [ax for ax, (lo, hi) in sorted(axis_bands.items()) if lo != hi]
    else:
        idx = machine.action_index if 0 <= machine.action_index < len(poses) else 0
        # ``"x"`` axes (frozen / copy-from-previous) take the frozen base — the
        # robot's position captured at the action's transition commit — falling back
        # to the live curjpos when no frozen base is set (a restart without a
        # transition). Non-``"x"`` axes stay at the anchor pose value.
        frozen = getattr(machine, "_frozen_base", None)
        ref = frozen if frozen is not None else curjpos
        anchor = resolve_x(poses[idx], ref)
        var_axes = zone.action_variable_axes(name)
        # A ``"x"`` axis is frozen (never driven), even if listed in variable_axes.
        var_axes = [ax for ax in var_axes if poses[idx][ax] != "x"]

    # Forced behavior (live test override): run this behavior's policy on the
    # action's variable axes regardless of the action's declared behavior.
    active_behavior = machine.forced_behavior or zone.action_behavior(name)
    _apply_behavior(machine, anchor, var_axes, active_behavior, curjpos, action, axis_bands)
    return anchor


def _apply_behavior(machine, pose, var_axes, behavior, curjpos, action, axis_bands=None) -> None:
    """Drive the variable axes according to the behaviour policy.

    Non-variable axes remain at the current anchor (already set in ``pose``).
    ``axis_bands`` (the active-class per-axis ``[min, max]`` bands, or ``None``) is
    threaded to the servo so its clamp uses the band ∩ safezone floor.
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
        _apply_camera(machine, pose, var_axes, behavior, curjpos, image_gain, action, axis_bands)
    elif behavior == "gentle":
        _apply_face(machine, pose, var_axes, curjpos, image_gain, action, behavior, axis_bands)
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

    # Test sweep (zone-local, gated by the action's ``sweep`` flag): on top of the
    # camera servo, drift every variable axis so a ``testing`` action visibly uses all
    # of them (bounded by the safezone). The camera behaviours are the only ones that
    # recenter a tracked target, so the sweep only makes sense there.
    if behavior in ("locked", "curious", "intent", "gentle") and _sweep_enabled(action):
        _add_sweep(machine, pose, var_axes, behavior, curjpos, action, axis_bands)


def _apply_camera(machine, pose, var_axes, behavior, curjpos, gain, action, axis_bands=None) -> None:
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
    _servo(machine, pose, var_axes, dx, dy, curjpos, gain, action, behavior, axis_bands)


def _apply_face(machine, pose, var_axes, curjpos, gain, action, behavior, axis_bands=None) -> None:
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
    _servo(machine, pose, var_axes, dx, dy, curjpos, gain, action, behavior, axis_bands)


def _apply_bridge(machine, pose, var_axes, curjpos, action, axis_bands=None) -> None:
    """Drive the free bridge axis (A1): hold by default, track the face when face-locked.

    The bridge is the pose-zone transition policy. The free axis (A1, axis 0) is held
    at the robot's current value by default so the robot transitions safely between
    zones. When a face is locked (the brain stashed a valid face on
    ``machine.face_state``), A1 keeps the face direction: the face offset drives the
    free axis via the image Jacobian (reusing :func:`_servo`). Non-free axes are
    absolute (pinned to the pose value) and are not driven here.
    """
    state = machine.face_state or {}
    face_locked = bool(state.get("ok")) and state.get("id") is not None
    if not face_locked:
        return  # hold: the free axis stays at its current value (no motion)
    # Face-locked: keep the face direction (drive the free axis to track the face).
    dx = state.get("dx", 0.0)
    dy = state.get("dy", 0.0)
    if not isinstance(dx, (int, float)) or isinstance(dx, bool):
        dx = 0.0
    if not isinstance(dy, (int, float)) or isinstance(dy, bool):
        dy = 0.0
    gain = _bp(machine, "bridge", "gain", _DEFAULT_IMAGE_GAIN)
    _servo(machine, pose, var_axes, dx, dy, curjpos, gain, action, "bridge", axis_bands)


def _servo(machine, pose, var_axes, dx, dy, curjpos, gain, action, behavior, axis_bands=None) -> None:
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

    **Safezone clamp.** The resulting variable-axis pose is clamped before it is
    recorded, so a recentering command can never leave the zone's safe bounds. For
    axis_bands actions (``axis_bands`` is not ``None``) the floor is the per-axis band
    ∩ safezone — the action's roaming range intersected with the hard safety floor —
    so the servo roams within the band without ever leaving the zone safezone.
    Otherwise the floor is the effective floor (safezone ∩ hardware), matching the
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
    # Clamp: keep the recentering command inside the current zone's safe bounds. For
    # axis_bands actions the floor is the per-axis band ∩ safezone (the band is the
    # action's roaming range; the safezone is the hard safety floor). Otherwise the
    # effective floor (safezone ∩ hardware), matching _apply_wander / _apply_oscillation.
    limits = effective(machine)
    if axis_bands:
        floor = list(limits)
        for ax, (lo, hi) in axis_bands.items():
            floor[ax] = [max(lo, limits[ax][0]), min(hi, limits[ax][1])]
        for axis in var_axes:
            pose[axis] = fitlimits(axis, pose[axis], floor)
    else:
        for axis in var_axes:
            pose[axis] = fitlimits(axis, pose[axis], limits)
    machine.servo_state = {
        "behavior": behavior, "dx": dx, "dy": dy,
        "dq_deg": dq_deg, "axes": list(var_axes),
        "proposed": list(pose),
    }


def _axis_desync(axis):
    """Per-axis desync for the sweep/oscillation policies (stable across ticks).

    Returns ``(phase, freq_mult)`` for a single variable axis, both derived
    deterministically from the axis number so they are stable across ticks (no
    jitter) but differ per axis. The phase offset + small frequency multiplier
    desynchronise the axes so they no longer all ride one shared sine in lockstep —
    each axis sweeps at its own rate and starting point, so the motion reads as
    organic rather than a single rigid wave.
    """
    a = axis + 1.0
    h1 = (math.sin(a * 12.9898) * 43758.5453) % 1.0
    h2 = (math.sin(a * 78.233) * 43758.5453) % 1.0
    return h1 * 2.0 * math.pi, 0.7 + 0.6 * h2  # phase in [0, 2π), freq_mult in [0.7, 1.3)


def _apply_scan(machine, pose, var_axes, limits) -> None:
    """Sweep variable axes back and forth within the safezone (sine oscillation).

    Each axis rides its own sine — a stable per-axis phase offset + frequency
    multiplier (see :func:`_axis_desync`) keeps the axes from moving in lockstep, so
    the sweep reads as organic rather than one rigid wave.
    """
    base = machine.tick * 0.1 * _bp(machine, "vigilant", "freq", 1.0)
    for axis in var_axes:
        phase, freq_mult = _axis_desync(axis)
        t = base * freq_mult + phase
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
    the safezone width. Each axis rides its own sine (a stable per-axis phase + frequency
    offset, see :func:`_axis_desync`) so the axes do not all move in lockstep. The axis is
    clamped to the effective safezone so the pose always remains within the zone's safe
    bounds (no collision from overshoot).
    """
    base = machine.tick * 0.1 * freq
    for axis in var_axes:
        phase, freq_mult = _axis_desync(axis)
        t = base * freq_mult + phase
        lo, hi = limits[axis]
        center = pose[axis]  # the current-anchor value (pose starts as the anchor)
        amplitude = (hi - lo) / 2.0 * amp_scale
        val = center + amplitude * math.sin(t)
        pose[axis] = max(lo, min(hi, val))
