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
