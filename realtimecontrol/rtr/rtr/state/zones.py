"""Zone and pose data.

This is the static description of where the robot may be and what it may do. It is
lifted verbatim from ``kukart/robotstates.py`` so behaviour is unchanged, but now
lives in one typed module instead of a global dict.

A zone declares:

- ``safezone``  – per-axis ``(min, max)`` limits that are always safe in this zone,
- ``startpos``  – the pose the robot moves to when entering the zone,
- ``exitpos``   – the pose the robot moves to when leaving the zone,
- ``exits``     – the names of zones reachable from this one,
- ``actions``   – named motions (sequences of poses + speeds) available in the zone,
- ``speed``     – default move speed while in the zone.
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

ZONES: Dict[str, dict] = {
    "init": {
        "startpos": [-60, -90, 90, 0, 15, 0],
        "safezone": [(-94, 122), (-105, -65), (89, 115), (-15, 15), (-15, 45), (-357, 357)],
        "actions": [],
        "exitpos": [-4, -131, 155, 3, -10, 0],  # exit to rest position
        "exits": ["rest"],
        "speed": 20,
    },
    "rest": {
        "startpos": [-3, -133, 156, -2, 0, 0],
        "safezone": [(-5, 5), (-134, -130), (155, 157), (-3, 3), (-10, 10), (-357, 357)],
        "actions": {
            "breathe": {
                "pos": [[-5, -130, 155, -2, 0, 0],
                        [-3, -134, 157, -2, 0, 0],
                        [5, -132, 155, -2, 0, 0]],
                "speed": [10, 10, 10],
            },
            "look": {
                "pos": [[-5, -130, 155, -2, 10, 0],
                        [-3, -134, 157, -3, -10, 0],
                        [5, -132, 155, 3, -10, 0]],
                "speed": [100, 100, 100],
            },
        },
        "exitpos": [-3, -134, 156, -2, 0, 0],  # exit to wakeup position
        "exits": ["wakeup"],
        "speed": 20,
    },
    "wakeup": {
        "startpos": [-3, -134, 156, -2, 0, 0],
        "safezone": [(-94, 122), (-134, -123), (136, 157), (-23, 23), (-25, 25), (-357, 357)],
        "actions": {
            "breathe": {
                "pos": [[-85, -130, 137, -2, 0, 0],
                        [-73, -134, 150, -2, 0, 0],
                        [35, -132, 140, -2, 0, 0]],
                "speed": [10, 10, 10],
            },
            "look": {
                "pos": [[-85, -130, 137, -2, 0, 0],
                        [-73, -134, 150, -2, 0, 0],
                        [35, -132, 140, -2, 0, 0]],
                "speed": [100, 100, 100],
            },
        },
        "exitpos": [-3, -97, 16, -2, 90, 0],  # exit to stretch position
        "exits": ["stretch", "wander"],
        "speed": 40,
    },
    "stretch": {
        "startpos": [-3, -97, 16, -2, 0, 0],
        "safezone": [(-94, 122), (-98, -96), (13, 19), (-2, 2), (85, 95), (-357, 357)],
        "actions": {
            "breathe": {
                "pos": [[85, -97, 17, -2, 0, 0],
                        [73, -97, 15, -2, 0, 0],
                        [35, -97, 14, -2, 0, 0]],
                "speed": [10, 10, 10],
            },
            "look": {
                "pos": [[85, -97, 17, -2, 0, 0],
                        [73, -97, 15, -2, 0, 0],
                        [35, -97, 14, -2, 0, 0]],
                "speed": [100, 100, 100],
            },
        },
        "exitpos": [-60, -65, 157, -2, 0, 0],  # exit to wander position
        "exits": ["wander"],
        "speed": 50,
    },
    "wander": {
        "startpos": [60, -65, 60, 0, 45, 0],
        "safezone": [(-94, 122), (-70, -60), (29, 100), (-4, 4), (-118, 118), (-357, 357)],
        "actions": {
            "breathe": {
                "pos": [[85, -65, 37, -2, 0, 0],
                        [-85, -66, 59, -2, 0, 0],
                        [35, -68, 40, -2, 0, 0]],
                "speed": [10, 10, 10],
            },
            "look": {
                "pos": [[-85, -66, 70, -2, 0, 0],
                        [-73, -69, 50, -2, 0, 0],
                        [35, -61, 44, -2, 0, 0]],
                "speed": [100, 100, 100],
            },
        },
        "exitpos": [-3, -97, 157, -2, 0, 0],
        "exits": ["stretch", "wildwander"],
        "speed": 30,
    },
    "wildwander": {
        "startpos": [60, -22.5, -112.5, 0, 45, 0],
        "safezone": [(-94, 122), (-124, -60), (-19, 157), (-1, 1), (-118, 118), (-357, 357)],
        "actions": {
            "breathe": {
                "pos": [[-85, -120, 137, 0, 0, 0],
                        [-73, -114, 150, 0, 0, 0],
                        [35, -112, 140, -2, 0, 0]],
                "speed": [10, 10, 10],
            },
            "look": {
                "pos": [[-85, -120, 137, 0, 0, 0],
                        [-73, -114, 150, 0, 0, 0],
                        [35, -112, 140, -2, 0, 0]],
                "speed": [100, 100, 100],
            },
        },
        "exitpos": [-3, -97, 157, -2, 0, 0],
        "exits": ["stretch", "wander"],
        "speed": 100,
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
#
# The file is the editable source of truth (see the editor page). The dicts above
# are kept as built-in fallback defaults so the core still runs if the file is
# missing or corrupt.
# ---------------------------------------------------------------------------

def _is_num(v) -> bool:
    """True for real numbers (bool is excluded on purpose)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate_zone(name: str, z: dict, all_names: set) -> None:
    if not isinstance(z, dict):
        raise ValueError(f"zone {name!r}: must be an object")
    if "enabled" in z and not isinstance(z["enabled"], bool):
        raise ValueError(f"zone {name!r}: 'enabled' must be a bool")
    for key in ("startpos", "exitpos"):
        v = z.get(key)
        if not (isinstance(v, list) and len(v) == 6 and all(_is_num(x) for x in v)):
            raise ValueError(f"zone {name!r}: {key} must be a list of 6 numbers")
    sz = z.get("safezone")
    if not (isinstance(sz, list) and len(sz) == 6):
        raise ValueError(f"zone {name!r}: safezone must be a list of 6 [lo, hi] pairs")
    for i, pair in enumerate(sz):
        if not (isinstance(pair, list) and len(pair) == 2
                and _is_num(pair[0]) and _is_num(pair[1]) and pair[0] < pair[1]):
            raise ValueError(f"zone {name!r}: safezone axis {i} must be [lo, hi] with lo < hi")
    exits = z.get("exits", [])
    if not (isinstance(exits, list) and all(isinstance(e, str) for e in exits)):
        raise ValueError(f"zone {name!r}: exits must be a list of zone names")
    for e in exits:
        if e not in all_names:
            raise ValueError(f"zone {name!r}: exit {e!r} does not name an existing zone")
    if not _is_num(z.get("speed")):
        raise ValueError(f"zone {name!r}: speed must be a number")
    acts = z.get("actions", {})
    if not isinstance(acts, dict):
        raise ValueError(f"zone {name!r}: actions must be an object")
    for an, a in acts.items():
        if not isinstance(a, dict):
            raise ValueError(f"zone {name!r} action {an!r}: must be an object")
        if "enabled" in a and not isinstance(a["enabled"], bool):
            raise ValueError(f"zone {name!r} action {an!r}: 'enabled' must be a bool")
        pos = a.get("pos")
        if not (isinstance(pos, list) and pos and all(
                isinstance(p, list) and len(p) == 6 and all(_is_num(x) for x in p) for p in pos)):
            raise ValueError(f"zone {name!r} action {an!r}: pos must be a list of 6-number poses")
        spd = a.get("speed")
        if not (isinstance(spd, list) and len(spd) == len(pos) and all(_is_num(x) for x in spd)):
            raise ValueError(f"zone {name!r} action {an!r}: speed must have one value per pose")


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


@dataclass
class Zone:
    """A single zone, with typed accessors over the raw dict."""

    name: str
    data: dict

    @property
    def safezone(self) -> List[Tuple[float, float]]:
        return self.data["safezone"]

    @property
    def startpos(self) -> List[float]:
        return list(self.data["startpos"])

    @property
    def exitpos(self) -> List[float]:
        return list(self.data["exitpos"])

    @property
    def exits(self) -> List[str]:
        return list(self.data["exits"])

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


class Zones:
    """Typed wrapper over the :data:`ZONES` table."""

    def __init__(self, zones: Dict[str, dict] = None):
        self._zones = zones if zones is not None else ZONES
        self._by_name: Dict[str, Zone] = {n: Zone(n, d) for n, d in self._zones.items()}

    def names(self) -> List[str]:
        return list(self._by_name)

    def has(self, name: str) -> bool:
        return name in self._by_name

    def get(self, name: str) -> Zone:
        return self._by_name[name]

    def can_exit_to(self, current: str, target: str) -> bool:
        """True if ``target`` is a declared exit of ``current``."""
        return target in self._by_name[current].exits

    def table(self) -> Dict[str, dict]:
        """A copy of the full zone table (for the HTTP ``GET /api/zones`` endpoint)."""
        return {n: dict(d) for n, d in self._zones.items()}
