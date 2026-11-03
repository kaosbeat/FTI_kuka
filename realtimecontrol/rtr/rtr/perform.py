"""Perform Mode global config (perform.json).

The perform config holds the global settings for the live perform mode:

- ``default_base_beats``: the fallback base beat count for an action that does not
  specify its own (in zones.json). An action's own ``base_beats`` overrides it.
- ``internal_bpm``: the fallback BPM when no external MIDI clock is present.
- ``clock_timeout``: seconds of silence before an external clock is considered lost.
- ``velocity_max_deg_s``: the max velocity (degrees/sec) at CC-A = 127. The CC-A
  normalized value (0-1) is scaled by this to get the actual velocity.
- ``multipliers``: the CC-B banded multiplier values (1/16 … 16, divide and multiply).

The built-in :data:`DEFAULT_PERFORM_CONFIG` is the fallback when the file is
missing or corrupt.
"""

import copy
import json
from typing import Any

# The fallback shape of perform.json (used when the file is missing or corrupt).
DEFAULT_PERFORM_CONFIG = {
    "default_base_beats": 4,
    "internal_bpm": 130.0,
    "clock_timeout": 1.0,
    "velocity_max_deg_s": 30.0,
    "multipliers": [0.0625, 0.125, 0.25, 0.5, 1, 2, 4, 8, 16],
}


def builtin_perform_config() -> dict:
    """A deep copy of the built-in perform config (the fallback shape)."""
    return copy.deepcopy(DEFAULT_PERFORM_CONFIG)


def _num(value: Any, where: str, default: float) -> float:
    """A number field: ``default`` when absent, else a number (bools rejected)."""
    if value is None:
        return default
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"'{where}' must be a number")
    return float(value)


def validate_perform_config(data: Any) -> dict:
    """Validate a perform config dict. Fill in defaults; return ``data`` or raise.

    Shape: ``{"default_base_beats": int, "internal_bpm": float, "clock_timeout": float,
             "velocity_max_deg_s": float, "multipliers": [0.0625, 0.125, 0.25, 0.5, 1, 2, 4, 8, 16]}``.
    Missing keys take the built-in defaults; bad types raise :class:`ValueError`.
    """
    if not isinstance(data, dict):
        raise ValueError("perform config must be an object")

    cfg: dict = {}

    # default_base_beats: non-negative int.
    dbb = data.get("default_base_beats", DEFAULT_PERFORM_CONFIG["default_base_beats"])
    if not (isinstance(dbb, int) and not isinstance(dbb, bool) and dbb >= 0):
        raise ValueError("'default_base_beats' must be a non-negative int")
    cfg["default_base_beats"] = dbb

    # internal_bpm: positive number.
    cfg["internal_bpm"] = _num(data.get("internal_bpm"), "internal_bpm",
                                DEFAULT_PERFORM_CONFIG["internal_bpm"])
    if cfg["internal_bpm"] <= 0:
        raise ValueError("'internal_bpm' must be positive")

    # clock_timeout: non-negative number.
    cfg["clock_timeout"] = _num(data.get("clock_timeout"), "clock_timeout",
                                 DEFAULT_PERFORM_CONFIG["clock_timeout"])
    if cfg["clock_timeout"] < 0:
        raise ValueError("'clock_timeout' must be non-negative")

    # velocity_max_deg_s: positive number.
    cfg["velocity_max_deg_s"] = _num(data.get("velocity_max_deg_s"), "velocity_max_deg_s",
                                      DEFAULT_PERFORM_CONFIG["velocity_max_deg_s"])
    if cfg["velocity_max_deg_s"] <= 0:
        raise ValueError("'velocity_max_deg_s' must be positive")

    # multipliers: a non-empty list of positive numbers (the CC-B banded values).
    mult = data.get("multipliers", DEFAULT_PERFORM_CONFIG["multipliers"])
    if not (isinstance(mult, list) and mult
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in mult)):
        raise ValueError("'multipliers' must be a non-empty list of positive numbers")
    cfg["multipliers"] = list(mult)

    return cfg


def load_perform_config(path: str) -> dict:
    """Load and validate the on-disk perform config. Raise ``OSError``/``ValueError``."""
    with open(path, "r", encoding="utf-8") as f:
        return validate_perform_config(json.load(f))
