"""Brain decision config: autonomy + zone groups.

Data-driven: the brain's autonomous navigation decisions live in ``brain.json``
(edited in ``editor.html``, served/saved over ``/api/brain``, hot-reloaded). The
built-in :data:`DEFAULT_BRAIN_CONFIG` is the fallback when the file is missing or
corrupt — and its values match the hunt/facefocus logic's defaults, so a missing
file is a no-op.

Shape::

    {
      "autonomy": {
        "enabled": true,
        "dwell_s": 30.0,
        "events": { "midi": true, "camera": true, "websocket": true }
      },
      "zone_groups": {
        "home":      { "zones": [...], "behaviors": [...], "enabled": true },
        "hunt":      { "zones": [...], "behaviors": [...], "enabled": true,  "params": {...} },
        "facefocus": { "zones": [...], "behaviors": [...], "enabled": true,  "params": {...} },
        "perform":   { "zones": [...], "behaviors": [...], "enabled": false }
      }
    }

- ``autonomy.enabled`` — master switch for autonomous navigation (following a
  looping action's ``next`` link once it has dwelt for ``dwell_s`` or an event
  fires).
- ``autonomy.dwell_s`` — the global default dwell time (seconds) before a looping
  action's ``next`` link is followed. A per-action ``dwell_s`` in zones.json
  overrides it.
- ``autonomy.events`` — which external events can trigger an immediate next link
  (``midi`` note-on, ``camera`` detect/hunt lock, ``websocket`` proceed).
- ``zone_groups.<name>`` — a named group of zones + the behaviors enabled in them:
  - ``zones`` — list of zone names (must exist in zones.json).
  - ``behaviors`` — list of behavior fields enabled in those zones.
  - ``enabled`` — whether this group is part of the current autonomy level.
  - ``params`` — optional; group-specific decision data (only ``hunt`` and
    ``facefocus`` have a validated shape today; other groups may carry an
    unvalidated passthrough).

The autonomy level is the free combination of enabled groups; navigation runs
across the **union** of all enabled groups' zones. Hunt and facefocus are zone
groups, not separate special modes:

- ``hunt`` (zones ``wakeup``): fast/aggressive person tracking. Its ``params``
  carry the hunt-loop timing/trigger data (``lost_s``, ``min_conf``,
  ``attention_guard``, ``detect``/``scan``). detect→``look``, lost→``scan``.
  ``min_conf`` is the lock sensitivity: candidates with confidence below it are
  ignored (lower = more sensitive; the mock camera's 0.3–0.99 range locks at
  the default 0.1).
- ``facefocus`` (zones ``stretch``): the "check out that human" face actions. Its
  ``params`` carry the face-cycling data (``actions``, ``action_s``,
  ``face_lost_s``).
"""

import copy
import json
from typing import Any, Dict

from ..state.zones import BEHAVIORS

# The autonomy + zone-group decision parameters, with their default values. These
# mirror the values the old top-level ``hunt`` / ``facefocus`` sections used, so
# ``builtin_brain_config()`` is a faithful stand-in for a missing ``brain.json``.
DEFAULT_BRAIN_CONFIG: Dict[str, Any] = {
    "autonomy": {
        "enabled": True,
        "dwell_s": 30.0,
        "events": {"midi": True, "camera": True, "websocket": True},
    },
    "zone_groups": {
        "home": {
            "zones": ["init", "rest", "init_rest_bridge"],
            "behaviors": ["hold"],
            "enabled": True,
        },
        "hunt": {
            "zones": ["wakeup"],
            "behaviors": ["look", "scan"],
            "enabled": True,
            "params": {
                "lost_s": 2.0,
                "min_conf": 0.1,
                "attention_guard": ["track", "focus", "look", "face"],
                "detect": {"zone": "wakeup", "action": "look"},
                "scan": {"zone": "wakeup", "action": "scan"},
            },
        },
        "facefocus": {
            "zones": ["stretch"],
            "behaviors": ["face"],
            "enabled": True,
            "params": {
                "actions": ["wink", "inspect", "call"],
                "action_s": 3.0,
                "face_lost_s": 2.5,
            },
        },
        "perform": {
            "zones": ["watch", "perform", "fume"],
            "behaviors": ["track", "focus"],
            "enabled": False,
        },
    },
}

# The groups with a known ``params`` shape (validated); every other group's
# ``params`` is an unvalidated passthrough for future use.
_HUNT_PARAMS = {
    "lost_s": 2.0,
    "min_conf": 0.1,
    "attention_guard": ["track", "focus", "look", "face"],
    "detect": {"zone": "wakeup", "action": "look"},
    "scan": {"zone": "wakeup", "action": "scan"},
}
_FACEFOCUS_PARAMS = {
    "actions": ["wink", "inspect", "call"],
    "action_s": 3.0,
    "face_lost_s": 2.5,
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


def _bool(value: Any, where: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"'{where}' must be a boolean")
    return value


def _zone_action(value: Any, where: str, default: dict) -> dict:
    if value is None:
        return dict(default)
    if not isinstance(value, dict):
        raise ValueError(f"'{where}' must be an object with 'zone' and 'action'")
    return {
        "zone": _str(value.get("zone"), f"{where}.zone", default["zone"]),
        "action": _str(value.get("action"), f"{where}.action", default["action"]),
    }


def _behaviors(value: Any, where: str, default: list) -> list:
    """A list of known behavior names (validated against :data:`BEHAVIORS`)."""
    lst = _str_list(value, where, default)
    for b in lst:
        if b not in BEHAVIORS:
            raise ValueError(f"'{where}' contains unknown behavior {b!r} (expected one of {BEHAVIORS})")
    return lst


def _validate_hunt_params(p: dict, where: str) -> dict:
    """Validate the ``hunt`` group's ``params`` (the hunt-loop timing/trigger data)."""
    d = _HUNT_PARAMS
    return {
        "lost_s": _num(p.get("lost_s"), f"{where}.lost_s", d["lost_s"]),
        "min_conf": _num(p.get("min_conf"), f"{where}.min_conf", d["min_conf"]),
        "attention_guard": _str_list(p.get("attention_guard"), f"{where}.attention_guard",
                                      d["attention_guard"]),
        "detect": _zone_action(p.get("detect"), f"{where}.detect", d["detect"]),
        "scan": _zone_action(p.get("scan"), f"{where}.scan", d["scan"]),
    }


def _validate_facefocus_params(p: dict, where: str) -> dict:
    """Validate the ``facefocus`` group's ``params`` (the face-cycling data)."""
    d = _FACEFOCUS_PARAMS
    return {
        "actions": _str_list(p.get("actions"), f"{where}.actions", d["actions"]),
        "action_s": _num(p.get("action_s"), f"{where}.action_s", d["action_s"]),
        "face_lost_s": _num(p.get("face_lost_s"), f"{where}.face_lost_s", d["face_lost_s"]),
    }


def _validate_group(name: str, g: Any) -> dict:
    """Validate a single zone group: ``{zones, behaviors, enabled, [params]}``.

    ``zones`` are non-empty strings (zone existence is checked by the caller, which
    has the zone table). ``behaviors`` are known behavior names. ``params`` is only
    validated for the known group shapes (``hunt`` / ``facefocus``); other groups
    carry an unvalidated passthrough.
    """
    where = f"zone_groups.{name}"
    if not isinstance(g, dict):
        raise ValueError(f"'{where}' must be an object")
    result = {
        "zones": _str_list(g.get("zones"), f"{where}.zones", []),
        "behaviors": _behaviors(g.get("behaviors"), f"{where}.behaviors", []),
        "enabled": _bool(g.get("enabled"), f"{where}.enabled", False),
    }
    params = g.get("params")
    if params is not None:
        if name == "hunt":
            if not isinstance(params, dict):
                raise ValueError(f"'{where}.params' must be an object")
            result["params"] = _validate_hunt_params(params, f"{where}.params")
        elif name == "facefocus":
            if not isinstance(params, dict):
                raise ValueError(f"'{where}.params' must be an object")
            result["params"] = _validate_facefocus_params(params, f"{where}.params")
        else:
            # Unvalidated passthrough for future group param shapes.
            if not isinstance(params, dict):
                raise ValueError(f"'{where}.params' must be an object")
            result["params"] = dict(params)
    elif name in ("hunt", "facefocus"):
        # Fill the known params defaults so the brain always has complete data.
        if name == "hunt":
            result["params"] = copy.deepcopy(_HUNT_PARAMS)
        else:
            result["params"] = copy.deepcopy(_FACEFOCUS_PARAMS)
    return result


def validate_brain_config(data: Any) -> dict:
    """Validate and normalise a brain config. Returns the filled config or raises :class:`ValueError`.

    Missing keys are filled from :data:`DEFAULT_BRAIN_CONFIG`; present keys are
    type-checked. This is a pure type validator: it does **not** check that a
    group's zone names exist in zones.json (that cross-reference is done by the
    brain's :meth:`~rtr.brain.brain.Brain.reload`, which has the zone table).
    """
    if not isinstance(data, dict):
        raise ValueError("data must be an object")

    # --- autonomy ----------------------------------------------------------
    autonomy = data.get("autonomy")
    if autonomy is not None and not isinstance(autonomy, dict):
        raise ValueError("'autonomy' must be an object")
    autonomy = autonomy or {}
    a = DEFAULT_BRAIN_CONFIG["autonomy"]
    events = autonomy.get("events")
    if events is not None and not isinstance(events, dict):
        raise ValueError("'autonomy.events' must be an object")
    events = events or {}
    ae = a["events"]
    autonomy_cfg = {
        "enabled": _bool(autonomy.get("enabled"), "autonomy.enabled", a["enabled"]),
        "dwell_s": _num(autonomy.get("dwell_s"), "autonomy.dwell_s", a["dwell_s"]),
        "events": {
            "midi": _bool(events.get("midi"), "autonomy.events.midi", ae["midi"]),
            "camera": _bool(events.get("camera"), "autonomy.events.camera", ae["camera"]),
            "websocket": _bool(events.get("websocket"), "autonomy.events.websocket", ae["websocket"]),
        },
    }

    # --- zone_groups -------------------------------------------------------
    groups = data.get("zone_groups")
    if groups is not None and not isinstance(groups, dict):
        raise ValueError("'zone_groups' must be an object")
    groups = groups or {}
    zone_groups: Dict[str, dict] = {}
    for name, g in groups.items():
        zone_groups[name] = _validate_group(name, g)

    # Fill in any default groups that were not present in the file, so the brain
    # always has the full set (e.g. a brain.json with only "hunt" still gets "home").
    for name in DEFAULT_BRAIN_CONFIG["zone_groups"]:
        if name not in zone_groups:
            zone_groups[name] = _validate_group(name, DEFAULT_BRAIN_CONFIG["zone_groups"][name])

    return {"autonomy": autonomy_cfg, "zone_groups": zone_groups}


def load_brain_config(path: str) -> dict:
    """Load and validate the on-disk brain config. Raises ``OSError``/``ValueError``."""
    with open(path, "r", encoding="utf-8") as f:
        return validate_brain_config(json.load(f))
