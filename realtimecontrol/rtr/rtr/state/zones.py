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

from dataclasses import dataclass
from typing import Dict, List, Tuple

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

    def actions(self) -> Dict[str, dict]:
        return self.data.get("actions", {})


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
