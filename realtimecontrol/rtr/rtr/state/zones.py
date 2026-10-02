"""Zone and pose data.

A zone is a pure safe-boundary: ``safezone`` + ``speed`` + ``actions``. Zones are
linked only via actions' ``next`` fields — there is no zone-level ``exits`` graph.

Each action declares one of two forms:

- **Variable-axis form**: ``base_pose`` + ``variable_axes`` + ``behavior`` +
  ``speed`` (+ optional ``loop`` and ``next``). The non-variable axes are held
  at ``base_pose``; the variable axes are driven by the named behavior policy.
- **Legacy fixed-pose form**: ``pos`` (list of poses) + ``speed`` (list of
  speeds) + ``loop`` (+ optional ``next``). Steps through the pose sequence.

An action has either ``base_pose`` or ``pos``, not both.
"""

import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Per-axis software limits (the union of every zone's safezone, used as a hard clamp).
SOFTWARE_LIMITS: List[Tuple[float, float]] = [
    (-95, 123), (-135, 35), (-120, 158), (-350, 350), (-119, 119), (-358, 358),
]

# Default clamp used when a command does not name a zone.
LIMITS: List[Tuple[float, float]] = [
    (-94, 122), (-134, 34), (-119, 157), (-349, 349), (-118, 118), (-357, 357),
]

# Per-axis *hardware* limits (degrees), from the ``kr60ha_macro.xacro`` joint
# ``<limit>`` tags. These are the physical joint limits of the real KR60 and form a
# hard safety floor: every commanded pose is clamped to the intersection of the
# current zone's safezone and these limits.
HARDWARE_LIMITS: List[Tuple[float, float]] = [
    (-185, 185),   # A1
    (-135, 35),    # A2
    (-120, 158),    # A3
    (-350, 350),    # A4
    (-119, 119),    # A5
    (-350, 350),    # A6 (rotary wrist, now limited)
]

# Behavior names: the policies that drive variable axes.
BEHAVIORS = ("track", "focus", "scan", "look", "wander", "random", "hold")

# Kept for backward compat (MIDI legacy dispatch, display patches); no longer
# used by zones or the state machine.
MODES = ("wander", "random", "action", "track", "hold")


def effective_limits(zone_safezone: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Per-axis intersection of a zone's safezone and the hardware limits.

    The effective range for each axis is ``[max(sz_lo, hw_lo), min(sz_hi, hw_hi)]``.
    """
    return [
        (max(sz[0], hw[0]), min(sz[1], hw[1]))
        for sz, hw in zip(zone_safezone, HARDWARE_LIMITS)
    ]


# ---------------------------------------------------------------------------
# Built-in fallback zone table (used when zones.json is missing or corrupt).
# Zones are pure safe-boundaries: safezone + speed + actions. The zone graph
# is derived from actions' ``next`` fields.
# ---------------------------------------------------------------------------
ZONES: Dict[str, dict] = {
    "init": {
        "enabled": True,
        "safezone": [(-94, 122), (-105, -65), (89, 115), (-15, 15), (-15, 45), (-357, 357)],
        "speed": 20,
        "actions": {
            "wake": {
                "base_pose": [-60, -90, 90, 0, 15, 0],
                "variable_axes": [],
                "behavior": "hold",
                "speed": 20,
                "loop": True,
                "next": {"zone": "rest", "action": "breathe"},
            }
        },
    },
    "rest": {
        "enabled": True,
        "safezone": [(-5, 5), (-134, -130), (155, 157), (-3, 3), (-10, 10), (-357, 357)],
        "speed": 20,
        "actions": {
            "breathe": {
                "base_pose": [-3, -133, 156, -2, 0, 0],
                "variable_axes": [0, 1, 2],
                "behavior": "wander",
                "speed": 10,
                "loop": True,
            },
            "look": {
                "base_pose": [-3, -133, 156, -2, 0, 0],
                "variable_axes": [0, 4],
                "behavior": "look",
                "speed": 100,
                "loop": True,
                "next": {"zone": "wakeup", "action": "breathe"},
            },
        },
    },
    "wakeup": {
        "enabled": True,
        "safezone": [(-94, 122), (-134, -123), (136, 157), (-23, 23), (-25, 25), (-357, 357)],
        "speed": 40,
        "actions": {
            "breathe": {
                "base_pose": [-3, -134, 156, -2, 0, 0],
                "variable_axes": [0, 1, 2],
                "behavior": "wander",
                "speed": 10,
                "loop": True,
                "next": {"zone": "stretch", "action": "look"},
            },
            "look": {
                "base_pose": [-3, -134, 156, -2, 0, 0],
                "variable_axes": [0, 4],
                "behavior": "look",
                "speed": 100,
                "loop": True,
            },
        },
    },
    "stretch": {
        "enabled": True,
        "safezone": [(-94, 122), (-98, -96), (13, 19), (-2, 2), (85, 95), (-357, 357)],
        "speed": 100,
        "actions": {
            "look": {
                "base_pose": [-3, -97, 16, -2, 0, 0],
                "variable_axes": [0, 4],
                "behavior": "look",
                "speed": 100,
                "loop": True,
                "next": {"zone": "wander", "action": "breathe"},
            },
        },
    },
    "wander": {
        "enabled": True,
        "safezone": [(-94, 122), (-70, -60), (29, 100), (-4, 4), (-118, 118), (-357, 357)],
        "speed": 30,
        "actions": {
            "breathe": {
                "base_pose": [60, -65, 60, 0, 45, 0],
                "variable_axes": [0, 1, 2],
                "behavior": "wander",
                "speed": 10,
                "loop": True,
                "next": {"zone": "wildwander", "action": "breathe"},
            },
            "look": {
                "base_pose": [60, -65, 60, 0, 45, 0],
                "variable_axes": [0, 4],
                "behavior": "look",
                "speed": 100,
                "loop": True,
            },
        },
    },
    "wildwander": {
        "enabled": True,
        "safezone": [(-94, 122), (-124, -60), (-19, 157), (-1, 1), (-118, 118), (-357, 357)],
        "speed": 100,
        "actions": {
            "breathe": {
                "base_pose": [60, -22.5, -112.5, 0, 45, 0],
                "variable_axes": [0, 1, 2],
                "behavior": "wander",
                "speed": 10,
                "loop": True,
                "next": {"zone": "wander", "action": "breathe"},
            },
            "look": {
                "base_pose": [60, -22.5, -112.5, 0, 45, 0],
                "variable_axes": [0, 4],
                "behavior": "look",
                "speed": 100,
                "loop": True,
            },
        },
    },
    "watch": {
        "enabled": True,
        "safezone": [(-20, 20), (-134, -130), (155, 157), (-3, 3), (0, 60), (-357, 357)],
        "speed": 20,
        "actions": {
            "track": {
                "base_pose": [-3, -133, 156, -2, 45, 0],
                "variable_axes": [0],
                "behavior": "track",
                "speed": 20,
                "loop": True,
                "next": {"zone": "perform", "action": "trick"},
            },
            "focus": {
                "base_pose": [-3, -133, 156, -2, 45, 0],
                "variable_axes": [4],
                "behavior": "focus",
                "speed": 20,
                "loop": True,
            },
        },
    },
    "perform": {
        "enabled": True,
        "safezone": [(-30, 30), (-98, -96), (13, 19), (-2, 2), (-30, 60), (-357, 357)],
        "speed": 40,
        "actions": {
            "trick": {
                "base_pose": [-3, -97, 16, -2, 45, 0],
                "variable_axes": [4],
                "behavior": "scan",
                "speed": 40,
                "loop": True,
                "next": {"zone": "watch", "action": "track"},
            },
            "bow": {
                "base_pose": [-3, -97, 16, -2, 0, 0],
                "variable_axes": [4],
                "behavior": "scan",
                "speed": 40,
                "loop": True,
            },
        },
    },
    "fume": {
        "enabled": True,
        "safezone": [(-94, 122), (-134, -123), (136, 157), (-23, 23), (0, 118), (-357, 357)],
        "speed": 40,
        "actions": {
            "roar": {
                "base_pose": [-3, -134, 156, -2, 90, 0],
                "variable_axes": [4],
                "behavior": "scan",
                "speed": 40,
                "loop": True,
                "next": {"zone": "watch", "action": "track"},
            },
            "snarl": {
                "base_pose": [-3, -134, 156, -2, 90, 0],
                "variable_axes": [4],
                "behavior": "scan",
                "speed": 40,
                "loop": True,
            },
        },
    },
}


# Named joint poses (MIDI channels 1/2 pick one of these).
POSES: Dict[str, List[List[float]]] = {
    "init": [[0, -90, 90, 0, 90, 0]],  # home (baseline)
    "ch1": [
        [0, -80, 90, 0, 80, 0],
        [0, -90, 90, 0, 40, 0],
        [0, -70, 90, 0, 60, 0],
        [0, -100, 90, 0, 20, 0],
    ],
}

# Cartesian (linear) poses.
LIN_POSES: Dict[str, List[List[float]]] = {
    "pos1": [
        [855.209656, -577.229004, 1515.260742, 145.978836, -27.013065, -179.960007],
        [500.209656, -527.229004, 1515.260742, 105.978836, 55.013065, 117.960007],
        [1780.209656, -527.229004, 1515.260742, 105.978836, 55.013065, 117.960007],
        [1750.209656, -527.229004, 1515.260742, 105.978836, 55.013065, 117.960007],
    ],
}


# ---------------------------------------------------------------------------
# Loading + validation of the on-disk data file (``zones.json``).
# ---------------------------------------------------------------------------

def _is_num(v) -> bool:
    """True for real numbers (bool is excluded on purpose)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate_action(name: str, a: dict, all_zone_names: set) -> None:
    """Validate a single action dict (new variable-axis or legacy fixed-pose form)."""
    if not isinstance(a, dict):
        raise ValueError(f"action {name!r}: must be an object")
    if "enabled" in a and not isinstance(a["enabled"], bool):
        raise ValueError(f"action {name!r}: 'enabled' must be a bool")

    has_base = "base_pose" in a
    has_pos = "pos" in a
    if has_base and has_pos:
        raise ValueError(f"action {name!r}: must have either 'base_pose' or 'pos', not both")
    if not has_base and not has_pos:
        raise ValueError(f"action {name!r}: must have either 'base_pose' or 'pos'")

    if has_base:
        bp = a["base_pose"]
        if not (isinstance(bp, list) and len(bp) == 6 and all(_is_num(x) for x in bp)):
            raise ValueError(f"action {name!r}: base_pose must be a list of 6 numbers")
        va = a.get("variable_axes", [])
        if not (isinstance(va, list) and all(
                isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 5
                for x in va)):
            raise ValueError(f"action {name!r}: variable_axes must be a list of axis indices (0-5)")
        beh = a.get("behavior", "hold")
        if not (isinstance(beh, str) and beh in BEHAVIORS):
            raise ValueError(f"action {name!r}: behavior must be one of {BEHAVIORS}")
        spd = a.get("speed")
        if spd is not None and not _is_num(spd):
            raise ValueError(f"action {name!r}: speed must be a number (variable-axis form)")
    else:
        pos = a["pos"]
        if not (isinstance(pos, list) and pos and all(
                isinstance(p, list) and len(p) == 6 and all(_is_num(x) for x in p)
                for p in pos)):
            raise ValueError(f"action {name!r}: pos must be a non-empty list of 6-number poses")
        spd = a.get("speed")
        if spd is not None and not (
                isinstance(spd, list) and len(spd) == len(pos) and all(_is_num(x) for x in spd)):
            raise ValueError(f"action {name!r}: speed must have one value per pose")

    if "loop" in a and not isinstance(a["loop"], bool):
        raise ValueError(f"action {name!r}: 'loop' must be a bool")

    if "next" in a:
        n = a["next"]
        if not isinstance(n, dict):
            raise ValueError(f"action {name!r}: 'next' must be an object")
        if "zone" in n:
            if not (isinstance(n["zone"], str) and n["zone"] in all_zone_names):
                raise ValueError(f"action {name!r}: next.zone must name an existing zone")
        if "action" in n and not isinstance(n["action"], str):
            raise ValueError(f"action {name!r}: next.action must be a string")


def _validate_zone(name: str, z: dict, all_names: set) -> None:
    """Validate a single zone dict."""
    if not isinstance(z, dict):
        raise ValueError(f"zone {name!r}: must be an object")
    if "enabled" in z and not isinstance(z["enabled"], bool):
        raise ValueError(f"zone {name!r}: 'enabled' must be a bool")
    sz = z.get("safezone")
    if not (isinstance(sz, list) and len(sz) == 6):
        raise ValueError(f"zone {name!r}: safezone must be a list of 6 [lo, hi] pairs")
    for i, pair in enumerate(sz):
        if not (isinstance(pair, list) and len(pair) == 2
                and _is_num(pair[0]) and _is_num(pair[1]) and pair[0] < pair[1]):
            raise ValueError(f"zone {name!r}: safezone axis {i} must be [lo, hi] with lo < hi")
    if not _is_num(z.get("speed")):
        raise ValueError(f"zone {name!r}: speed must be a number")
    acts = z.get("actions", {})
    if not isinstance(acts, dict):
        raise ValueError(f"zone {name!r}: actions must be an object")
    for an, a in acts.items():
        _validate_action(an, a, all_names)


def validate_state_data(data) -> dict:
    """Validate a full zone/pose data table. Returns ``data`` or raises ``ValueError``."""
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    zones = data.get("zones")
    if not (isinstance(zones, dict) and zones):
        raise ValueError("'zones' must be a non-empty object")
    names = set(zones)
    for name, z in zones.items():
        _validate_zone(name, z, names)
    for key in ("poses", "lin_poses"):
        if key in data:
            table = data[key]
            if not isinstance(table, dict):
                raise ValueError(f"{key!r} must be an object")
            for pn, ps in table.items():
                if not (isinstance(ps, list) and all(
                        isinstance(p, list) and len(p) == 6 and all(_is_num(x) for x in p)
                        for p in ps)):
                    raise ValueError(f"{key}[{pn!r}] must be a list of 6-number poses")
    return data


def load_state_data(path) -> dict:
    """Load and validate the on-disk data file. Raises ``OSError``/``ValueError``."""
    with open(path, "r", encoding="utf-8") as f:
        return validate_state_data(json.load(f))


# ---------------------------------------------------------------------------
# Typed accessors.
# ---------------------------------------------------------------------------

@dataclass
class Zone:
    """A single zone, with typed accessors over the raw dict."""

    name: str
    data: dict

    @property
    def safezone(self) -> List[Tuple[float, float]]:
        return self.data["safezone"]

    @property
    def speed(self) -> float:
        return self.data["speed"]

    @property
    def enabled(self) -> bool:
        """Whether this zone is enabled (default true when the flag is absent)."""
        return bool(self.data.get("enabled", True))

    def actions(self) -> Dict[str, dict]:
        """The zone's action table as a dict (empty when absent or malformed)."""
        a = self.data.get("actions", {})
        return a if isinstance(a, dict) else {}

    def action_enabled(self, name: str) -> bool:
        """Whether a named action is enabled (default true when the flag is absent)."""
        a = self.data.get("actions", {})
        if not isinstance(a, dict):
            return True
        act = a.get(name)
        if not isinstance(act, dict):
            return True
        return bool(act.get("enabled", True))

    def _action(self, name: str) -> dict:
        """The raw action dict for ``name`` (empty dict when absent/malformed)."""
        a = self.data.get("actions", {})
        if not isinstance(a, dict):
            return {}
        act = a.get(name)
        return act if isinstance(act, dict) else {}

    def action_loops(self, name: str) -> bool:
        """Whether the action loops (wraps around) or runs once.

        Defaults to ``True`` when the field is absent.
        """
        return bool(self._action(name).get("loop", True))

    def action_base_pose(self, name: str) -> Optional[List[float]]:
        """The action's ``base_pose`` (variable-axis form), or None for legacy actions."""
        a = self._action(name)
        bp = a.get("base_pose")
        if isinstance(bp, list) and len(bp) == 6:
            return list(bp)
        return None

    def action_variable_axes(self, name: str) -> List[int]:
        """The action's ``variable_axes`` (list of 0-5 indices), or [] for legacy."""
        a = self._action(name)
        va = a.get("variable_axes")
        if isinstance(va, list):
            return [x for x in va if isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 5]
        return []

    def action_behavior(self, name: str) -> str:
        """The action's behavior name, or ``"hold"`` for legacy/absent."""
        a = self._action(name)
        beh = a.get("behavior")
        if isinstance(beh, str) and beh in BEHAVIORS:
            return beh
        return "hold"

    def action_next(self, name: str) -> Optional[dict]:
        """The action's ``next`` field (``{zone, action}``), or None."""
        a = self._action(name)
        n = a.get("next")
        return n if isinstance(n, dict) else None

    def action_speed(self, name: str) -> Optional[float]:
        """The action's speed as a number (variable-axis form), or None.

        Legacy actions use a per-pose speed list; this accessor returns None for
        those (the machine falls back to the zone speed).
        """
        a = self._action(name)
        s = a.get("speed")
        if _is_num(s):
            return s
        return None

    def action_entry_pose(self, name: str) -> Optional[List[float]]:
        """The pose the robot moves to when transitioning into this action.

        For variable-axis actions this is the ``base_pose``. For legacy actions
        it is the first pose in the ``pos`` sequence. Returns None if the action
        is unknown or malformed.
        """
        a = self._action(name)
        bp = a.get("base_pose")
        if isinstance(bp, list) and len(bp) == 6:
            return list(bp)
        pos = a.get("pos")
        if isinstance(pos, list) and pos and isinstance(pos[0], list) and len(pos[0]) == 6:
            return list(pos[0])
        return None


class Zones:
    """Typed wrapper over the zone table."""

    def __init__(self, zones: Dict[str, dict] = None):
        self._zones = zones if zones is not None else ZONES
        self._by_name: Dict[str, Zone] = {n: Zone(n, d) for n, d in self._zones.items()}

    def names(self) -> List[str]:
        return list(self._by_name)

    def has(self, name: str) -> bool:
        return name in self._by_name

    def get(self, name: str) -> Zone:
        return self._by_name[name]

    def graph(self) -> Dict[str, List[str]]:
        """The zone graph derived from all actions' ``next`` fields.

        Returns ``{zone_name: [reachable_zone_names]}`` where each reachable
        zone is named by some action's ``next.zone`` in that zone. Used by the
        display and editor to render the navigation graph.
        """
        g: Dict[str, List[str]] = {}
        for name, z in self._by_name.items():
            targets = set()
            for an in z.actions():
                n = z.action_next(an)
                if n and isinstance(n.get("zone"), str):
                    targets.add(n["zone"])
            g[name] = sorted(targets)
        return g

    def table(self) -> Dict[str, dict]:
        """A copy of the full zone table (for the HTTP ``GET /api/zones`` endpoint)."""
        return {n: dict(d) for n, d in self._zones.items()}
