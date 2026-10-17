"""Brain decision config: autonomy + mode groups + hunt/facefocus + cam_control.

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
        "museum-closed":  { "zones": [...], "enabled": true, "params": { "speed": 10, "dwell_s": 60.0 } },
        "event-sit-back": { "zones": [...], "enabled": true, "params": { "speed": 40, "dwell_s": 30.0 } },
        "stage-takeover": { "zones": [...], "enabled": true, "params": { "speed": 80, "dwell_s": 15.0 } }
      },
      "hunt":      { "zones": [...], "enabled": true, "params": {...} },
      "facefocus": { "zones": [...], "enabled": true, "params": {...} },
      "cam_control": { "home/rest": { "active": "wide", "mode": "idle", "lock": "none" }, ... }
    }

- ``autonomy.enabled`` — master switch for autonomous navigation (following a
  looping action's ``next`` link — or a random valid zone exit when there is none —
  once it has dwelt, or an event fires).
- ``autonomy.dwell_s`` — the global default dwell time (seconds). A per-action
  ``dwell_s`` in zones.json and a mode-group ``params.dwell_s`` override it.
- ``autonomy.events`` — which external events can trigger an immediate next link
  (``midi`` note-on, ``camera`` detect/hunt lock, ``websocket`` proceed).
- ``zone_groups.<name>`` — a thematic mode group of zones. ``zones`` is the list
  of zone names; ``enabled`` whether the group is part of the current autonomy
  level; ``params`` carries the group's base ``speed`` and default ``dwell_s``.
  Navigation runs across the **union** of all enabled groups' zones.
- ``hunt`` (top-level): the person-tracking loop. Its ``params`` carry the
  hunt-loop timing/trigger data (``lost_s``, ``min_conf``, ``attention_guard``,
  ``detect``/``scan``). ``min_conf`` is the lock sensitivity: candidates with
  confidence below it are ignored (lower = more sensitive).
- ``facefocus`` (top-level): the "check out that human" face actions. Its
  ``params`` carry the face-cycling data (``actions``, ``action_s``,
  ``face_lost_s``).
- ``cam_control`` (top-level): the per-action camera baseline, keyed by
  ``"zone/action"`` (``active`` camera, ``mode``, and ``lock`` = ``"none"`` |
  ``"auto"`` | a specific id). Applied on every action change; the hunt/facefocus
  logic overrides it while tracking.
"""

import copy
import json
from typing import Any, Dict

# The camera intent vocabulary (kept in sync with ``rtr.camera.camera``;
# defined here — rather than imported — to avoid the core↔brain import cycle).
CAMERA_CHOICES = ("wide", "close", "both", "none")
CAMERA_MODES = ("idle", "track", "analyze")

# The engagement-ladder class vocabulary (the rungs of the per-zone ladder).
# ``ignore`` → ``scan`` → ``track`` → ``farfocus`` → ``approach`` → ``facefocus``
# is the escalation order; ``retreat`` de-escalates back down. Defined here (not
# imported) to avoid the core↔brain import cycle; kept in sync with the
# ``zone_classes`` section of ``brain.json``.
ZONE_CLASSES = ("ignore", "scan", "track", "farfocus", "approach", "facefocus", "retreat")

# The per-behavior motion parameters (the tunable knobs of the motion policies in
# ``rtr/state/behavior.py``). Values match the historical hardcoded constants so a
# ``brain.json`` without a ``behaviors`` section is a no-op. Only the parameterized
# behaviors carry entries: ``calm`` / ``bridge`` are no-ops and ``spontaneous`` has
# no tunable knob.
DEFAULT_BEHAVIORS: Dict[str, dict] = {
    "locked":      {"gain": 0.3},
    "curious":     {"gain": 0.3, "look_factor": 0.5},
    "intent":      {"gain": 0.3},
    "gentle":      {"gain": 0.3, "face_factor": 0.5},
    "vigilant":    {"freq": 1.0},
    "restless":    {"wander_adj": 5.0},
    "spontaneous": {},
    "soothing":    {"freq": 0.5, "amp_scale": 0.1},
    "agitated":    {"freq": 1.0, "amp_scale": 0.3},
    "aggressive":  {"freq": 1.0, "amp_scale": 0.3},
    "exhilarated": {"freq": 2.0, "amp_scale": 0.5},
    "impatient":   {"freq": 1.0, "amp_scale": 0.3},
    "drowsy":      {"freq": 0.3, "amp_scale": 0.2},
}

# The autonomy + mode-group + hunt/facefocus + cam_control decision parameters,
# with their default values. ``builtin_brain_config()`` is a faithful stand-in for
# a missing ``brain.json``.
DEFAULT_BRAIN_CONFIG: Dict[str, Any] = {
    "autonomy": {
        "enabled": True,
        "dwell_s": 30.0,
        "events": {"midi": True, "camera": True, "websocket": True},
    },
    "zone_groups": {
        "museum-closed": {
            "zones": ["resting", "sleeping", "cuddling", "purring"],
            "enabled": True,
            "params": {"speed": 10, "dwell_s": 60.0},
        },
        "event-sit-back": {
            "zones": ["stretching", "hunting", "examining", "performing"],
            "enabled": True,
            "params": {"speed": 40, "dwell_s": 30.0},
        },
        "stage-takeover": {
            "zones": ["roaring", "growling", "clawing", "leaping"],
            "enabled": True,
            "params": {"speed": 80, "dwell_s": 15.0},
        },
    },
    "hunt": {
        "zones": ["hunting"],
        "enabled": True,
        "params": {
            "lost_s": 2.0,
            "min_conf": 0.1,
            "centrality_weight": 0.1,
            "attention_guard": ["locked", "intent", "curious", "gentle"],
            "detect": {"zone": "hunting", "action": "track"},
            "scan": {"zone": "hunting", "action": "scan"},
        },
    },
    "facefocus": {
        "zones": ["stretching"],
        "enabled": True,
        "params": {
            "actions": ["wink", "inspect", "call"],
            "action_s": 3.0,
            "face_lost_s": 2.5,
        },
    },
    "zone_classes": {
        "ignore": {
            "camera": {"active": "none", "mode": "idle", "lock": "none"},
            "params": {},
            "color": "#8a8a8a",
        },
        "scan": {
            "camera": {"active": "wide", "mode": "idle", "lock": "none"},
            "params": {"scan_s": 2.0},
            "color": "#5b9bd5",
        },
        "track": {
            "camera": {"active": "wide", "mode": "track", "lock": "auto"},
            "params": {
                "lost_s": 2.0,
                "min_conf": 0.1,
                "centrality_weight": 0.1,
                "attention_guard": ["locked", "intent", "curious", "gentle"],
                "detect": {"zone": "hunting", "action": "track"},
                "scan": {"zone": "hunting", "action": "scan"},
            },
            "color": "#2e6fd0",
        },
        "farfocus": {
            "camera": {"active": "wide", "mode": "track", "lock": "auto"},
            "params": {
                "lost_s": 2.0,
                "min_conf": 0.1,
                "centrality_weight": 0.1,
                "attention_guard": ["locked", "intent", "curious", "gentle"],
                "detect": {"zone": "hunting", "action": "track"},
                "scan": {"zone": "hunting", "action": "scan"},
            },
            "color": "#00b0c8",
        },
        "approach": {
            "camera": {"active": "wide", "mode": "track", "lock": "auto"},
            "params": {
                "lost_s": 2.0,
                "min_conf": 0.1,
                "centrality_weight": 0.1,
                "attention_guard": ["locked", "intent", "curious", "gentle"],
                "detect": {"zone": "hunting", "action": "track"},
                "scan": {"zone": "hunting", "action": "scan"},
                "approach_speed": 100,
                "keep_in_sight": 120,
                "engage_dist": 250,
            },
            "color": "#f0862c",
        },
        "facefocus": {
            "camera": {"active": "close", "mode": "analyze", "lock": "auto"},
            "params": {
                "actions": ["wink", "inspect", "call"],
                "action_s": 3.0,
                "face_lost_s": 2.5,
            },
            "color": "#d9489a",
        },
        "retreat": {
            "camera": {"active": "wide", "mode": "idle", "lock": "none"},
            "params": {"retreat_speed": 40, "break_off_s": 3.0},
            "color": "#4caf50",
        },
    },
    "cam_control": {
        "resting/hold": {"active": "wide", "mode": "idle", "lock": "none"},
        "resting/wake": {"active": "wide", "mode": "idle", "lock": "none"},
        "sleeping/breathe": {"active": "wide", "mode": "idle", "lock": "none"},
        "sleeping/snore": {"active": "wide", "mode": "idle", "lock": "none"},
        "sleeping/yawn": {"active": "wide", "mode": "idle", "lock": "none"},
        "cuddling/breathe": {"active": "wide", "mode": "idle", "lock": "none"},
        "cuddling/purr": {"active": "wide", "mode": "idle", "lock": "none"},
        "purring/hold": {"active": "wide", "mode": "idle", "lock": "none"},
        "purring/purr": {"active": "wide", "mode": "idle", "lock": "none"},
        "stretching/look": {"active": "wide", "mode": "track", "lock": "auto"},
        "stretching/wink": {"active": "wide", "mode": "track", "lock": "auto"},
        "stretching/inspect": {"active": "wide", "mode": "track", "lock": "auto"},
        "stretching/call": {"active": "wide", "mode": "track", "lock": "auto"},
        "hunting/track": {"active": "wide", "mode": "track", "lock": "auto"},
        "hunting/track2": {"active": "wide", "mode": "track", "lock": "auto"},
        "hunting/focus": {"active": "wide", "mode": "track", "lock": "auto"},
        "hunting/scan": {"active": "wide", "mode": "track", "lock": "auto"},
        "examining/focus": {"active": "close", "mode": "analyze", "lock": "auto"},
        "examining/face": {"active": "close", "mode": "analyze", "lock": "auto"},
        "performing/trick": {"active": "both", "mode": "track", "lock": "auto"},
        "performing/bow": {"active": "both", "mode": "track", "lock": "auto"},
        "roaring/roar": {"active": "wide", "mode": "track", "lock": "auto"},
        "roaring/snarl": {"active": "wide", "mode": "track", "lock": "auto"},
        "growling/growl": {"active": "wide", "mode": "track", "lock": "auto"},
        "growling/snarl": {"active": "wide", "mode": "track", "lock": "auto"},
        "clawing/claw": {"active": "close", "mode": "analyze", "lock": "auto"},
        "clawing/grab": {"active": "close", "mode": "analyze", "lock": "auto"},
        "leaping/leap": {"active": "wide", "mode": "track", "lock": "auto"},
        "leaping/pacing": {"active": "wide", "mode": "track", "lock": "auto"},
    },
    "behaviors": DEFAULT_BEHAVIORS,
}

# The known params shapes (validated); used to fill defaults when a section omits
# a field.
_HUNT_PARAMS = {
    "lost_s": 2.0,
    "min_conf": 0.1,
    "centrality_weight": 0.1,
    "attention_guard": ["locked", "intent", "curious", "gentle"],
    "detect": {"zone": "hunting", "action": "track"},
    "scan": {"zone": "hunting", "action": "scan"},
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


def _validate_hunt_params(p: dict, where: str) -> dict:
    """Validate the hunt params (the hunt-loop timing/trigger data)."""
    d = _HUNT_PARAMS
    return {
        "lost_s": _num(p.get("lost_s"), f"{where}.lost_s", d["lost_s"]),
        "min_conf": _num(p.get("min_conf"), f"{where}.min_conf", d["min_conf"]),
        "centrality_weight": _num(p.get("centrality_weight"), f"{where}.centrality_weight",
                                   d["centrality_weight"]),
        "attention_guard": _str_list(p.get("attention_guard"), f"{where}.attention_guard",
                                      d["attention_guard"]),
        "detect": _zone_action(p.get("detect"), f"{where}.detect", d["detect"]),
        "scan": _zone_action(p.get("scan"), f"{where}.scan", d["scan"]),
    }


def _validate_facefocus_params(p: dict, where: str) -> dict:
    """Validate the facefocus params (the face-cycling data)."""
    d = _FACEFOCUS_PARAMS
    return {
        "actions": _str_list(p.get("actions"), f"{where}.actions", d["actions"]),
        "action_s": _num(p.get("action_s"), f"{where}.action_s", d["action_s"]),
        "face_lost_s": _num(p.get("face_lost_s"), f"{where}.face_lost_s", d["face_lost_s"]),
    }


def _validate_behaviors_section(data: Any) -> dict:
    """Validate the per-behavior motion params (the tunable knobs of behavior.py).

    Known behaviors (see :data:`DEFAULT_BEHAVIORS`) are always present in the result
    — missing keys are filled from the defaults so the runtime always sees a complete
    section. Unknown behavior names are ignored (dropped); present params are
    type-checked as numbers.
    """
    if data is not None and not isinstance(data, dict):
        raise ValueError("'behaviors' must be an object")
    data = data or {}
    result: Dict[str, dict] = {}
    for name, defaults in DEFAULT_BEHAVIORS.items():
        entry = data.get(name)
        if not isinstance(entry, dict):
            entry = {}
        out: dict = {}
        for key, default in defaults.items():
            out[key] = _num(entry.get(key), f"behaviors.{name}.{key}", default)
        result[name] = out
    return result


def _validate_mode_group(name: str, g: Any) -> dict:
    """Validate a mode group: ``{zones, enabled, [params: {speed, dwell_s}]}``.

    ``zones`` are non-empty strings (zone existence is checked by the caller, which
    has the zone table). ``params`` carries the group's base ``speed`` and default
    ``dwell_s`` (both numbers, optional).
    """
    where = f"zone_groups.{name}"
    if not isinstance(g, dict):
        raise ValueError(f"'{where}' must be an object")
    result = {
        "zones": _str_list(g.get("zones"), f"{where}.zones", []),
        "enabled": _bool(g.get("enabled"), f"{where}.enabled", False),
    }
    params = g.get("params")
    if params is not None:
        if not isinstance(params, dict):
            raise ValueError(f"'{where}.params' must be an object")
        p = {}
        for key in ("speed", "dwell_s"):
            if key in params:
                v = params[key]
                if not (isinstance(v, (int, float)) and not isinstance(v, bool)):
                    raise ValueError(f"'{where}.params.{key}' must be a number")
                p[key] = v
        result["params"] = p
    return result


def _validate_cam_entry(where: str, entry: Any) -> dict:
    """Validate a camera intent entry: ``{active, mode, lock}``."""
    if not isinstance(entry, dict):
        raise ValueError(f"'{where}' must be an object")
    active = entry.get("active", "wide")
    if not (isinstance(active, str) and active in CAMERA_CHOICES):
        raise ValueError(f"'{where}.active' must be one of {CAMERA_CHOICES}")
    mode = entry.get("mode", "idle")
    if not (isinstance(mode, str) and mode in CAMERA_MODES):
        raise ValueError(f"'{where}.mode' must be one of {CAMERA_MODES}")
    lock = entry.get("lock", "none")
    if not (isinstance(lock, str) and lock):
        raise ValueError(f"'{where}.lock' must be a non-empty string")
    return {"active": active, "mode": mode, "lock": lock}


def _validate_zone_classes(data: Any) -> dict:
    """Validate the zone_classes section (per-class camera/params/color).

    Each entry must be one of :data:`ZONE_CLASSES` and carries a ``camera`` intent
    (``{active, mode, lock}``), a ``params`` dict (class-specific tuning data),
    and a ``color`` hex string for the editor UI.
    """
    if data is not None and not isinstance(data, dict):
        raise ValueError("'zone_classes' must be an object")
    result: Dict[str, dict] = {}
    for name, entry in (data or {}).items():
        if name not in ZONE_CLASSES:
            raise ValueError(f"'zone_classes' key {name!r} must be one of {ZONE_CLASSES}")
        if not isinstance(entry, dict):
            raise ValueError(f"'zone_classes.{name}' must be an object")
        where = f"zone_classes.{name}"
        cam = entry.get("camera")
        if cam is not None and not isinstance(cam, dict):
            raise ValueError(f"'{where}.camera' must be an object")
        cam_cfg = _validate_cam_entry(where + ".camera", cam) if cam is not None else {}
        params = entry.get("params")
        if params is not None and not isinstance(params, dict):
            raise ValueError(f"'{where}.params' must be an object")
        color = entry.get("color")
        if color is not None and not (isinstance(color, str) and color):
            raise ValueError(f"'{where}.color' must be a non-empty string")
        result[name] = {
            "camera": cam_cfg,
            "params": params or {},
            "color": color or "",
        }
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

    # --- zone_groups (the three mode groups) --------------------------------
    groups = data.get("zone_groups")
    if groups is not None and not isinstance(groups, dict):
        raise ValueError("'zone_groups' must be an object")
    groups = groups or {}
    zone_groups: Dict[str, dict] = {}
    for name, g in groups.items():
        zone_groups[name] = _validate_mode_group(name, g)
    # Fill in any default mode groups not present in the file, so the brain always
    # has the full set.
    for name in DEFAULT_BRAIN_CONFIG["zone_groups"]:
        if name not in zone_groups:
            zone_groups[name] = _validate_mode_group(name, DEFAULT_BRAIN_CONFIG["zone_groups"][name])

    # --- hunt (top-level) ---------------------------------------------------
    hunt = data.get("hunt")
    if hunt is not None and not isinstance(hunt, dict):
        raise ValueError("'hunt' must be an object")
    hunt = hunt or {}
    dh = DEFAULT_BRAIN_CONFIG["hunt"]
    hunt_params = hunt.get("params")
    hunt_cfg = {
        "zones": _str_list(hunt.get("zones"), "hunt.zones", dh["zones"]),
        "enabled": _bool(hunt.get("enabled"), "hunt.enabled", dh["enabled"]),
        "params": _validate_hunt_params(
            hunt_params if isinstance(hunt_params, dict) else {}, "hunt.params"),
    }

    # --- facefocus (top-level) ---------------------------------------------
    ff = data.get("facefocus")
    if ff is not None and not isinstance(ff, dict):
        raise ValueError("'facefocus' must be an object")
    ff = ff or {}
    df = DEFAULT_BRAIN_CONFIG["facefocus"]
    ff_params = ff.get("params")
    ff_cfg = {
        "zones": _str_list(ff.get("zones"), "facefocus.zones", df["zones"]),
        "enabled": _bool(ff.get("enabled"), "facefocus.enabled", df["enabled"]),
        "params": _validate_facefocus_params(
            ff_params if isinstance(ff_params, dict) else {}, "facefocus.params"),
    }

    # --- zone_classes (top-level) -------------------------------------------
    zc = data.get("zone_classes")
    if zc is not None and not isinstance(zc, dict):
        raise ValueError("'zone_classes' must be an object")
    zone_classes_cfg = _validate_zone_classes(zc)

    # --- cam_control (top-level, keyed by "zone/action") -------------------
    cam = data.get("cam_control")
    if cam is not None and not isinstance(cam, dict):
        raise ValueError("'cam_control' must be an object")
    cam = cam or {}
    cam_control = {key: _validate_cam_entry(f"cam_control.{key}", entry)
                  for key, entry in cam.items()}

    # --- behaviors (top-level) ---------------------------------------------
    behaviors_cfg = _validate_behaviors_section(data.get("behaviors"))

    return {
        "autonomy": autonomy_cfg,
        "zone_groups": zone_groups,
        "hunt": hunt_cfg,
        "facefocus": ff_cfg,
        "zone_classes": zone_classes_cfg,
        "cam_control": cam_control,
        "behaviors": behaviors_cfg,
    }


def load_brain_config(path: str) -> dict:
    """Load and validate the on-disk brain config. Raises ``OSError``/``ValueError``."""
    with open(path, "r", encoding="utf-8") as f:
        return validate_brain_config(json.load(f))
