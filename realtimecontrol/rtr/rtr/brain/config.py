"""Brain decision config: the hunt-loop parameters.

Data-driven: the brain's hunt-loop decisions (what to do on detect / track / lost)
live in ``brain.json`` (edited in ``editor.html``, served/saved over ``/api/brain``,
hot-reloaded). The built-in :data:`DEFAULT_BRAIN_CONFIG` is the fallback when the
file is missing or corrupt — and its values match the hunt logic's defaults, so a
missing file is a no-op.

Shape::

    {
      "hunt": {
        "lost_s": 2.0,
        "attention_guard": ["track", "focus", "look"],
        "detect": { "zone": "wakeup", "action": "look" },
        "scan":   { "zone": "wakeup", "action": "scan" }
      }
    }

- ``lost_s`` — seconds a locked target's ``CAM_TRACK`` may go stale before "lost"
  clears the lock and rescans.
- ``attention_guard`` — behaviors that suppress the detect attention-grab (the robot
  is already hunting when the behavior is one of these).
- ``detect`` — the (zone, action) the brain triggers when it locks a target.
- ``scan`` — the (zone, action) the brain triggers when the lock is lost.
"""

import copy
import json
from typing import Any, Dict

# The hunt-loop decision parameters, with their default values. These mirror the
# values the hunt logic used to hardcode, so ``builtin_brain_config()`` is a faithful
# stand-in for a missing ``brain.json``.
DEFAULT_BRAIN_CONFIG: Dict[str, Any] = {
    "hunt": {
        "lost_s": 2.0,
        "attention_guard": ["track", "focus", "look"],
        "detect": {"zone": "wakeup", "action": "look"},
        "scan": {"zone": "wakeup", "action": "scan"},
    }
}


def builtin_brain_config() -> dict:
    """A deep copy of the built-in config (the fallback shape of ``brain.json``)."""
    return copy.deepcopy(DEFAULT_BRAIN_CONFIG)


# ---------------------------------------------------------------------------
# Validation. ``validate_brain_config`` fills in defaults for missing keys so the
# brain always sees a complete config.
# ---------------------------------------------------------------------------

def _num(value: Any, where: str, default: float) -> float:
    if value is None:
        return default
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"'{where}' must be a number")
    return float(value)


def _str(value: Any, where: str, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{where}' must be a non-empty string")
    return value


def _str_list(value: Any, where: str, default: list) -> list:
    if value is None:
        return list(default)
    if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
        raise ValueError(f"'{where}' must be a list of non-empty strings")
    return list(value)


def _zone_action(value: Any, where: str, default: dict) -> dict:
    if value is None:
        return dict(default)
    if not isinstance(value, dict):
        raise ValueError(f"'{where}' must be an object with 'zone' and 'action'")
    return {
        "zone": _str(value.get("zone"), f"{where}.zone", default["zone"]),
        "action": _str(value.get("action"), f"{where}.action", default["action"]),
    }


def validate_brain_config(data: Any) -> dict:
    """Validate and normalise a brain config. Returns the filled config or raises :class:`ValueError`.

    Missing keys are filled from :data:`DEFAULT_BRAIN_CONFIG`; present keys are type-checked.
    """
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    hunt = data.get("hunt")
    if hunt is not None and not isinstance(hunt, dict):
        raise ValueError("'hunt' must be an object")
    hunt = hunt or {}
    d = DEFAULT_BRAIN_CONFIG["hunt"]
    return {
        "hunt": {
            "lost_s": _num(hunt.get("lost_s"), "hunt.lost_s", d["lost_s"]),
            "attention_guard": _str_list(hunt.get("attention_guard"),
                                         "hunt.attention_guard", d["attention_guard"]),
            "detect": _zone_action(hunt.get("detect"), "hunt.detect", d["detect"]),
            "scan": _zone_action(hunt.get("scan"), "hunt.scan", d["scan"]),
        }
    }


def load_brain_config(path: str) -> dict:
    """Load and validate the on-disk brain config. Raises ``OSError``/``ValueError``."""
    with open(path, "r", encoding="utf-8") as f:
        return validate_brain_config(json.load(f))
