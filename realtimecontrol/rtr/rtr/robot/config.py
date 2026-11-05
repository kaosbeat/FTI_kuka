"""Robot calibration config (physical-robot-only parameters).

These parameters correct the *real* KUKA's joint space relative to the core's
virtual joint space. They are applied only to the physical adapter
(:class:`~rtr.robot.kuka.KukaRobot`) — the sim is never offset. The on-disk shape
is a flat JSON object (see :data:`DEFAULT_ROBOT_CONFIG`), edited in ``editor.html``
and hot-reloaded via the ``RELOAD_ROBOT_CONFIG`` command.

Shape::

    { "joint_offsets_deg": [0, 0, 0, 0, 0, 0] }

- ``joint_offsets_deg`` — the physical robot's A1..A6 home, shifted by these
  degrees relative to the core's virtual joint space. On the way out the offset is
  subtracted from each joint target; on the way in it is added back to the read
  pose, so the core always sees a consistent joint space and its arrival check
  (comparelist) keeps firing. Index 5 (A6) is the calibration the user tunes; the
  others default to 0 (no change) and are left in so more axes can be calibrated
  later.

Unknown top-level keys are preserved (not clobbered) so the config can grow new
calibration parameters without losing hand-edited data.
"""

import json
from typing import Any, Dict, List

JOINT_COUNT = 6
JOINT_OFFSETS_KEY = "joint_offsets_deg"
PATCH_MODE_KEY = "patch_mode"
RED_LIGHT_KEY = "red_light"
# The LED-wall calibration overlay: a pulsating red light drawn on the render
# page (a 1px-wide vertical bar that blooms into a circle on the MIDI clock),
# positioned by a normalized (x, y) offset. The core only normalizes it — it is
# pushed to the render page in the state frame (Display) and edited in the
# robotconfig tab; the robot itself never sees it.
DEFAULT_RED_LIGHT: Dict[str, Any] = {
    "enabled": False,
    "x": 0.5,
    "y": 0.5,
    "bar_w": 1.0,
    "bar_h": 160.0,
    "radius": 80.0,
    "morph": True,
    "per_beat": 1.0,
}
# The two patch systems. "video" = the new action-only, video-per-pose system
# (only actions have a patch; a per-pose video, if present, replaces the patch
# with a 3-line video template). "complex" = the legacy layered system (zone +
# action/default + behavior patches concatenated, with the pose-index variable
# mechanism). The new system is the default; the legacy one is opt-in.
DEFAULT_PATCH_MODE = "video"
VALID_PATCH_MODES = ("video", "complex")

DEFAULT_ROBOT_CONFIG: Dict[str, Any] = {
    JOINT_OFFSETS_KEY: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    PATCH_MODE_KEY: DEFAULT_PATCH_MODE,
}


def builtin_robot_config() -> Dict[str, Any]:
    """A fresh copy of the built-in defaults (the fallback when no file exists)."""
    return {
        JOINT_OFFSETS_KEY: list(DEFAULT_ROBOT_CONFIG[JOINT_OFFSETS_KEY]),
        PATCH_MODE_KEY: DEFAULT_PATCH_MODE,
    }


def _num_list(value: Any, where: str, n: int, default: List[float]) -> List[float]:
    """Coerce ``value`` to a list of exactly ``n`` floats, or raise ValueError."""
    if value is None:
        return list(default)
    if not isinstance(value, (list, tuple)) or len(value) != n:
        raise ValueError(f"'{where}' must be a list of {n} numbers")
    out = []
    for i, v in enumerate(value):
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"'{where}[{i}]' must be a number")
        out.append(float(v))
    return out


def _patch_mode(value: Any, where: str) -> str:
    """Coerce ``value`` to a valid patch mode, or raise ValueError."""
    if value is None:
        return DEFAULT_PATCH_MODE
    if not isinstance(value, str) or value not in VALID_PATCH_MODES:
        raise ValueError(f"'{where}' must be one of {list(VALID_PATCH_MODES)}")
    return value


def validate_robot_config(data: Any) -> Dict[str, Any]:
    """Validate and normalise a robot config. Returns the filled config or raises :class:`ValueError`.

    ``joint_offsets_deg`` is type-checked and filled from the defaults; ``patch_mode``
    is validated against :data:`VALID_PATCH_MODES` and filled from the default; any
    other top-level keys are preserved verbatim so the config can grow new parameters.
    """
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    out = dict(data)
    out[JOINT_OFFSETS_KEY] = _num_list(
        data.get(JOINT_OFFSETS_KEY), JOINT_OFFSETS_KEY, JOINT_COUNT,
        DEFAULT_ROBOT_CONFIG[JOINT_OFFSETS_KEY])
    out[PATCH_MODE_KEY] = _patch_mode(data.get(PATCH_MODE_KEY), PATCH_MODE_KEY)
    return out


def load_robot_config(path: str) -> Dict[str, Any]:
    """Load and validate the on-disk robot config. Raises ``OSError``/``ValueError``."""
    with open(path, "r", encoding="utf-8") as f:
        return validate_robot_config(json.load(f))


def robot_config_offsets(config: Dict[str, Any]) -> List[float]:
    """The 6-element joint offset list from a robot config (defaults if absent)."""
    return list(config.get(JOINT_OFFSETS_KEY, DEFAULT_ROBOT_CONFIG[JOINT_OFFSETS_KEY]))


def robot_config_patch_mode(config: Dict[str, Any]) -> str:
    """The patch mode from a robot config (``video`` default / ``complex``), or the default."""
    mode = config.get(PATCH_MODE_KEY, DEFAULT_PATCH_MODE)
    return mode if mode in VALID_PATCH_MODES else DEFAULT_PATCH_MODE


def robot_config_red_light(config: Dict[str, Any]) -> Dict[str, Any]:
    """The red-light overlay config, normalized (defaults filled, ranges clamped).

    The render page draws a pulsating CRT red light at the normalized ``(x, y)``
    offset: a vertical bar (``bar_w`` x ``bar_h`` px, 1px wide by default) that
    blooms into a circle of ``radius`` px on every pulse, timed to the MIDI clock
    (``per_beat`` pulses per beat; ``morph`` off keeps the bar static). The core
    only normalizes the field (it is pushed to the render page in the state
    frame); it never consumes it. Malformed fields fall back to the defaults;
    out-of-range values are clamped so a hand-edited file can never kill the
    overlay.
    """
    out = dict(DEFAULT_RED_LIGHT)
    rl = config.get(RED_LIGHT_KEY)
    if isinstance(rl, dict):
        if isinstance(rl.get("enabled"), bool):
            out["enabled"] = rl["enabled"]
        for key in ("x", "y", "bar_w", "bar_h", "radius", "per_beat"):
            v = rl.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[key] = float(v)
        if isinstance(rl.get("morph"), bool):
            out["morph"] = rl["morph"]
    out["x"] = max(0.0, min(1.0, out["x"]))
    out["y"] = max(0.0, min(1.0, out["y"]))
    out["bar_w"] = max(1.0, out["bar_w"])
    out["bar_h"] = max(1.0, out["bar_h"])
    out["radius"] = max(1.0, out["radius"])
    out["per_beat"] = max(0.05, out["per_beat"])
    return out
