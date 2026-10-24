"""Zone and pose data.

A zone is a safe-boundary: ``safezone`` + ``speed`` + ``actions``. Zones are
grouped into mode groups (``museum-closed``, ``event-sit-back``,
``stage-takeover``) and linked via zone-level ``exits`` (list of ``[zone, action]``
pairs) and actions' ``next`` fields.

Every action uses a single unified form:

- ``poses``: a non-empty ordered list of full 6-axis *anchor* poses the robot
  steps through (on the machine's cadence). A single-anchor action (``poses:
  [[...]]``) is a resting pose; a multi-anchor action steps through them.
- ``variable_axes``: a list of axis indices (0-5) driven by the behaviour policy
  (the wiggle). Default ``[]`` (no wiggle — the anchors are held exactly).
- ``behavior``: one of :data:`BEHAVIORS`. Default ``"calm"``. ``calm`` / ``bridge``
  are no-op policies (they drive no variable axes).
- ``speed``: a single move-speed number (absent → the zone speed).
- ``loop``: bool, default ``true``. ``false`` = run once, then hand off via ``next``.
- ``next``: a single ``{zone, action}`` dict or an ordered list of them (unchanged).
- ``enabled``: bool, default true (unchanged).

The wiggle (variable axes + behaviour) is applied on top of the *current* anchor
every tick; the non-variable axes are pinned to the current anchor. Both the
former variable-axis form (``base_pose`` + wiggle) and the former legacy
fixed-pose form (a ``pos`` sequence, no wiggle) are expressible as special cases
of this unified form.
"""

import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Per-axis software limits (the union of every zone's safezone, used as a hard clamp).
SOFTWARE_LIMITS: List[Tuple[float, float]] = [
    (-185, 185), (-135, 35), (-95, 158), (-180, 180), (-55, 100), (-150, 150),
]

# Default clamp used when a command does not name a zone.
LIMITS: List[Tuple[float, float]] = [
    (-94, 122), (-134, 34), (-95, 157),(-180, 180), (-55, 100), (-150, 150),
]

# Per-axis *hardware* limits (degrees), from the ``kr60ha_macro.xacro`` joint
# ``<limit>`` tags. These are the physical joint limits of the real KR60 and form a
# hard safety floor: every commanded pose is clamped to the intersection of the
# current zone's safezone and these limits.
HARDWARE_LIMITS: List[Tuple[float, float]] = [
    (-185, 185),   # A1
    (-135, 35),    # A2
    (-95, 158),    # A3 
    (-180, 180),    # A4
    (-55, 100),    # A5
    (-150, 150),    # A6 (rotary wrist, now limited)
]

# Behavior names: the policies that drive variable axes.
#
# ``bridge`` is a no-op policy for bridge zones: it drives NO variable axes and does
# NO random movement. A bridge action holds its specific (curated) poses so the robot
# can transition safely between zones. It is the only behaviour a bridge zone may use.
BEHAVIORS = (
    "locked", "intent", "vigilant", "curious", "restless", "spontaneous", "gentle", "calm",
    "soothing", "agitated", "aggressive", "exhilarated", "impatient", "drowsy",
    "bridge",
)

# Mode groups: the three thematic clusters of zones.
MODE_GROUPS = ("museum-closed", "event-sit-back", "stage-takeover")

# Engagement-ladder class vocabulary (kept in sync with ``rtr.brain.config.ZONE_CLASSES``;
# defined here rather than imported to avoid the state↔brain import cycle).
ZONE_CLASSES = ("ignore", "scan", "track", "farfocus", "approach", "facefocus", "retreat")

# The engagement classes in which the robot actively tracks a locked person. Their
# actions use ``axis_bands`` (per-axis min/max bands) instead of the legacy
# ``variable_axes`` list: the servo drives the banded axes to keep the person
# centered, clamped to the band, so the robot roams within the zone safezone
# rather than looping through anchor poses.
ACTIVE_CLASSES = ("track", "farfocus", "approach", "facefocus")

# Kept for backward compat (MIDI legacy dispatch, display patches); no longer
# used by zones or the state machine.
MODES = ("wander", "random", "action", "track", "hold")


def _pose_numeric(p) -> bool:
    """True when every axis of the pose is a real number (no ``"x"`` frozen axes)."""
    return (isinstance(p, list)
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in p))


def find_shared_pose(poses_a: List[List[float]], poses_b: List[List[float]],
                     margin: float = 0.5) -> Optional[Tuple[List[float], int, int]]:
    """Find a pose that appears in both pose lists.

    Returns ``(pose, index_in_a, index_in_b)`` for the first pair of poses
    (scanning A in order, B in order) that match within ``margin`` on every
    axis, or ``None`` when no shared pose exists. Poses containing a non-numeric
    axis (an ``"x"`` frozen/copy axis) are skipped — they have no fixed value to
    compare against.
    """
    for i, pa in enumerate(poses_a):
        if not _pose_numeric(pa):
            continue
        for j, pb in enumerate(poses_b):
            if _pose_numeric(pb) and all(abs(pa[k] - pb[k]) <= margin for k in range(len(pa))):
                return (list(pa), i, j)
    return None


def effective_limits(zone_safezone: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Per-axis intersection of a zone's safezone and the hardware limits.
350
    The effective range for each axis is ``[max(sz_lo, hw_lo), min(sz_hi, hw_hi)]``.
    """
    return [
        (max(sz[0], hw[0]), min(sz[1], hw[1]))
        for sz, hw in zip(zone_safezone, HARDWARE_LIMITS)
    ]


# ---------------------------------------------------------------------------
# Built-in fallback zone table (used when zones.json is missing or corrupt).
# Zones are safe-boundaries + mode_group + exits + actions. The zone graph is
# derived from both actions' ``next`` fields and zone-level ``exits``.
# ---------------------------------------------------------------------------
ZONES: Dict[str, dict] = {
    "resting": {
        "enabled": True,
        "mode_group": 'museum-closed',
        "safezone": [(-94, 122), (-105, -65), (89, 115), (-15, 15), (-15, 45), (-357, 357)],
        "speed": 20,
        "exits": [('sleeping', 'breathe'), ('cuddling', 'breathe'), ('stretching', 'look'), ('hunting', 'track')],
        "actions": {
            "hold": {
                "poses": [[-60, -90, 90, 0, 15, 0]],
                "variable_axes": [],
                "behavior": 'calm',
                "speed": 20,
                "loop": True,
                "next": {}
            },
            "wake": {
                "poses": [[-60, -90, 90, 0, 15, 0]],
                "variable_axes": [0, 1, 2, 4],
                "behavior": 'restless',
                "speed": 20,
                "loop": True,
                "next": {}
            }
        }
    },
    "sleeping": {
        "enabled": True,
        "mode_group": 'museum-closed',
        "safezone": [(-5, 5), (-134, -130), (155, 157), (-3, 3), (-10, 10), (-357, 357)],
        "speed": 20,
        "exits": [('cuddling', 'breathe'), ('purring', 'purr'), ('resting', 'hold'), ('stretching', 'look')],
        "actions": {
            "breathe": {
                "poses": [[-3, -133, 156, -2, 0, 0], [-5, -130, 155, -2, 0, 0], [0, -134, 157, -2, 0, 0]],
                "variable_axes": [],
                "behavior": 'calm',
                "loop": True,
                "next": {}
            },
            "snore": {
                "poses": [[-3, -133, 156, -2, 0, 0], [-3, -133, 156, -2, 5, 0]],
                "variable_axes": [],
                "behavior": 'calm',
                "loop": True,
                "next": {}
            },
            "yawn": {
                "poses": [[-3, -133, 156, -2, 0, 0]],
                "variable_axes": [4],
                "behavior": 'vigilant',
                "speed": 10,
                "loop": True,
                "next": {}
            }
        }
    },
    "cuddling": {
        "enabled": True,
        "mode_group": 'museum-closed',
        "safezone": [(-5, 5), (-134, -130), (155, 157), (-3, 3), (-10, 10), (-357, 357)],
        "speed": 20,
        "exits": [('purring', 'purr'), ('sleeping', 'breathe'), ('resting', 'wake')],
        "actions": {
            "breathe": {
                "poses": [[-3, -133, 156, -2, 0, 0], [-2, -132, 156, -2, 0, 0], [-4, -134, 156, -2, 0, 0]],
                "variable_axes": [],
                "behavior": 'calm',
                "loop": True,
                "next": {}
            },
            "purr": {
                "poses": [[-3, -133, 156, -2, 0, 0]],
                "variable_axes": [0, 2],
                "behavior": 'soothing',
                "speed": 10,
                "loop": True,
                "next": {}
            }
        }
    },
    "purring": {
        "enabled": True,
        "mode_group": 'museum-closed',
        "safezone": [(-5, 5), (-134, -130), (155, 157), (-3, 3), (-10, 10), (-357, 357)],
        "speed": 20,
        "exits": [('sleeping', 'breathe'), ('resting', 'hold'), ('cuddling', 'breathe')],
        "actions": {
            "hold": {
                "poses": [[-3, -133, 156, -2, 0, 0]],
                "variable_axes": [],
                "behavior": 'calm',
                "speed": 20,
                "loop": True,
                "next": {}
            },
            "purr": {
                "poses": [[-3, -133, 156, -2, 0, 0]],
                "variable_axes": [0, 2],
                "behavior": 'soothing',
                "speed": 10,
                "loop": True,
                "next": {}
            }
        }
    },
    "stretching": {
        "enabled": True,
        "mode_group": 'event-sit-back',
        "safezone": [(-94, 122), (-98, -96), (13, 19), (-2, 2), (-5, 5), (-357, 357)],
        "speed": 100,
        "exits": [('hunting', 'track'), ('examining', 'focus'), ('performing', 'trick'), ('resting', 'hold'), ('roaring', 'roar')],
        "actions": {
            "look": {
                "poses": [[85, -97, 17, -2, 0.3, 0], [80, -97, 15, -2, 3, 0], [90, -97, 19, -2, -3, 0]],
                "variable_axes": [],
                "behavior": 'calm',
                "loop": True,
                "next": {}
            },
            "wink": {
                "poses": [[85, -97, 17, -2, 0.3, 0]],
                "variable_axes": [4, 5],
                "behavior": 'gentle',
                "speed": 100,
                "loop": True,
                "next": {}
            },
            "inspect": {
                "poses": [[85, -97, 17, -2, 0.3, 0]],
                "variable_axes": [4, 5],
                "behavior": 'gentle',
                "speed": 100,
                "loop": True,
                "next": {}
            },
            "call": {
                "poses": [[85, -97, 17, -2, 0.3, 0]],
                "variable_axes": [4, 5],
                "behavior": 'gentle',
                "speed": 100,
                "loop": True,
                "next": {}
            }
        }
    },
    "hunting": {
        "enabled": True,
        "mode_group": 'event-sit-back',
        "safezone": [(-20, 20), (-134, -130), (155, 157), (-3, 3), (0, 60), (-357, 357)],
        "speed": 20,
        "exits": [('examining', 'focus'), ('stretching', 'look'), ('performing', 'trick'), ('growling', 'growl')],
        "actions": {
            "track": {
                "poses": [[-3, -133, 156, -2, 45, 0]],
                "variable_axes": [3, 4],
                "behavior": 'locked',
                "speed": 20,
                "loop": True,
                "next": {}
            },
            "focus": {
                "poses": [[-3, -133, 156, -2, 45, 0]],
                "variable_axes": [4],
                "behavior": 'intent',
                "speed": 20,
                "loop": True,
                "next": {}
            },
            "scan": {
                "poses": [[-3, -133, 156, -2, 45, 0]],
                "variable_axes": [4],
                "behavior": 'vigilant',
                "speed": 20,
                "loop": True,
                "next": {}
            }
        }
    },
    "examining": {
        "enabled": True,
        "mode_group": 'event-sit-back',
        "safezone": [(-20, 20), (-134, -130), (155, 157), (-3, 3), (0, 60), (-357, 357)],
        "speed": 20,
        "exits": [('hunting', 'track'), ('stretching', 'call'), ('performing', 'trick')],
        "actions": {
            "focus": {
                "poses": [[-3, -133, 156, -2, 45, 0]],
                "variable_axes": [4],
                "behavior": 'intent',
                "speed": 20,
                "loop": True,
                "next": {}
            },
            "face": {
                "poses": [[-3, -133, 156, -2, 45, 0]],
                "variable_axes": [4, 5],
                "behavior": 'gentle',
                "speed": 20,
                "loop": True,
                "next": {}
            }
        }
    },
    "performing": {
        "enabled": True,
        "mode_group": 'event-sit-back',
        "safezone": [(-30, 30), (-98, -50), (13, 19), (-2, 2), (-30, 60), (-357, 357)],
        "speed": 40,
        "exits": [('hunting', 'track'), ('stretching', 'look'), ('roaring', 'roar')],
        "actions": {
            "trick": {
                "poses": [[-3, -97, 16, -2, 20, 0], [0, -95, 15, -2, 30, 0], [-5, -90, 17, -2, 10, 0]],
                "variable_axes": [],
                "behavior": 'calm',
                "loop": True,
                "next": {}
            },
            "bow": {
                "poses": [[-3, -97, 16, -2, 20, 0], [0, -95, 15, -2, 40, 0], [-3, -90, 16, -2, 50, 0]],
                "variable_axes": [],
                "behavior": 'calm',
                "loop": True,
                "next": {}
            }
        }
    },
    "roaring": {
        "enabled": True,
        "mode_group": 'stage-takeover',
        "safezone": [(-94, 122), (-134, -123), (136, 157), (-23, 23), (0, 118), (-357, 357)],
        "speed": 40,
        "exits": [('growling', 'growl'), ('clawing', 'claw'), ('leaping', 'leap'), ('performing', 'trick')],
        "actions": {
            "roar": {
                "poses": [[-3, -134, 156, -2, 90, 0], [0, -130, 155, -2, 95, 0], [-5, -125, 150, -2, 100, 0]],
                "variable_axes": [],
                "behavior": 'calm',
                "loop": True,
                "next": {}
            },
            "snarl": {
                "poses": [[-3, -134, 156, -2, 90, 0], [0, -130, 155, -2, 80, 0]],
                "variable_axes": [],
                "behavior": 'calm',
                "loop": True,
                "next": {}
            }
        }
    },
    "growling": {
        "enabled": True,
        "mode_group": 'stage-takeover',
        "safezone": [(-94, 122), (-134, -123), (136, 157), (-23, 23), (0, 118), (-357, 357)],
        "speed": 40,
        "exits": [('roaring', 'roar'), ('clawing', 'claw'), ('leaping', 'leap')],
        "actions": {
            "growl": {
                "poses": [[-3, -134, 156, -2, 90, 0]],
                "variable_axes": [4],
                "behavior": 'vigilant',
                "speed": 40,
                "loop": True,
                "next": {}
            },
            "snarl": {
                "poses": [[-3, -134, 156, -2, 90, 0], [0, -130, 155, -2, 85, 0]],
                "variable_axes": [],
                "behavior": 'calm',
                "loop": True,
                "next": {}
            }
        }
    },
    "clawing": {
        "enabled": True,
        "mode_group": 'stage-takeover',
        "safezone": [(-94, 122), (-98, -96), (13, 19), (-2, 2), (-5, 5), (-357, 357)],
        "speed": 40,
        "exits": [('growling', 'snarl'), ('roaring', 'roar'), ('leaping', 'leap'), ('stretching', 'look')],
        "actions": {
            "claw": {
                "poses": [[85, -97, 17, -2, 0, 0]],
                "variable_axes": [0, 2],
                "behavior": 'agitated',
                "speed": 40,
                "loop": True,
                "next": {}
            },
            "grab": {
                "poses": [[85, -97, 17, -2, 0, 0]],
                "variable_axes": [0, 2],
                "behavior": 'aggressive',
                "speed": 40,
                "loop": True,
                "next": {}
            }
        }
    },
    "leaping": {
        "enabled": True,
        "mode_group": 'stage-takeover',
        "safezone": [(-94, 122), (-124, -60), (-19, 157), (-2, 2), (-118, 118), (-357, 357)],
        "speed": 100,
        "exits": [('roaring', 'roar'), ('clawing', 'claw'), ('growling', 'growl'), ('resting', 'hold')],
        "actions": {
            "leap": {
                "poses": [[-85, -120, 137, 0, 0, 0], [-90, -115, 140, -2, 10, 0], [-80, -110, 145, 0, 20, 0]],
                "variable_axes": [],
                "behavior": 'calm',
                "loop": True,
                "next": {}
            },
            "pacing": {
                "poses": [[-85, -120, 137, 0, 0, 0], [-75, -115, 130, 0, -5, 0], [-90, -124, 140, 0, 5, 0]],
                "variable_axes": [],
                "behavior": 'calm',
                "loop": True,
                "next": {}
            }
        }
    }
}


# ---------------------------------------------------------------------------
# Loading + validation of the on-disk data file (``zones.json``).
# ---------------------------------------------------------------------------

def _is_num(v) -> bool:
    """True for real numbers (bool is excluded on purpose)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate_action(name: str, a: dict, all_zone_names: set) -> None:
    """Validate a single action dict (unified ``poses`` form)."""
    if not isinstance(a, dict):
        raise ValueError(f"action {name!r}: must be an object")
    if "enabled" in a and not isinstance(a["enabled"], bool):
        raise ValueError(f"action {name!r}: 'enabled' must be a bool")

    poses = a.get("poses")
    if not (isinstance(poses, list) and poses and all(
            isinstance(p, list) and len(p) == 6
            and all(_is_num(x) or x == "x" for x in p)
            for p in poses)):
        raise ValueError(f"action {name!r}: poses must be a non-empty list of 6-axis poses (number or \"x\")")

    # Optional per-pose reference images: a list aligned with ``poses`` (each entry
    # is a filename string or ``null`` for "no image"). When present it must match
    # the pose count exactly so the two stay in lock-step.
    pimgs = a.get("pose_imgs")
    if pimgs is not None:
        if not (isinstance(pimgs, list) and len(pimgs) == len(poses)
                and all(e is None or (isinstance(e, str) and e) for e in pimgs)):
            raise ValueError(f"action {name!r}: pose_imgs must be a list matching the pose count (filename or null)")

    # Optional per-pose videos (the new action-only patch system): a list aligned
    # with ``poses`` (each entry is a filename string or ``null`` for "no video").
    # When present it must match the pose count exactly so the two stay in lock-step,
    # exactly like ``pose_imgs``. A pose with a video renders that video fullscreen.
    pvids = a.get("pose_videos")
    if pvids is not None:
        if not (isinstance(pvids, list) and len(pvids) == len(poses)
                and all(e is None or (isinstance(e, str) and e) for e in pvids)):
            raise ValueError(f"action {name!r}: pose_videos must be a list matching the pose count (filename or null)")

    va = a.get("variable_axes", [])
    if not (isinstance(va, list) and all(
            isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 5
            for x in va)):
        raise ValueError(f"action {name!r}: variable_axes must be a list of axis indices (0-5)")
    ab = a.get("axis_bands")
    if ab is not None:
        if not isinstance(ab, dict):
            raise ValueError(f"action {name!r}: axis_bands must be an object")
        for k, band in ab.items():
            ax = Zone._axis_band_key(k)
            if ax is None:
                raise ValueError(f"action {name!r}: axis_bands key {k!r} must be an axis index (0-5)")
            if not (isinstance(band, list) and len(band) == 2
                    and _is_num(band[0]) and _is_num(band[1])):
                raise ValueError(f"action {name!r}: axis_bands[{k!r}] must be a [min, max] pair")
    beh = a.get("behavior", "calm")
    if not (isinstance(beh, str) and beh in BEHAVIORS):
        raise ValueError(f"action {name!r}: behavior must be one of {BEHAVIORS}")
    # ``bridge`` is a no-op policy (like ``calm``): it drives no variable axes. An
    # action may not pair it with a non-empty ``variable_axes`` list.
    if beh == "bridge" and va:
        raise ValueError(f"action {name!r}: 'bridge' is a no-op behaviour and must not drive variable axes")
    spd = a.get("speed")
    if spd is not None and not _is_num(spd):
        raise ValueError(f"action {name!r}: speed must be a number")

    if "loop" in a and not isinstance(a["loop"], bool):
        raise ValueError(f"action {name!r}: 'loop' must be a bool")

    if "next" in a:
        n = a["next"]
        if isinstance(n, dict):
            items = [n]
        elif isinstance(n, list):
            items = n
        else:
            raise ValueError(f"action {name!r}: 'next' must be an object or a list of objects")
        for entry in items:
            if not isinstance(entry, dict):
                raise ValueError(f"action {name!r}: 'next' entries must be objects")
            if "zone" in entry:
                if not (isinstance(entry["zone"], str) and entry["zone"] in all_zone_names):
                    raise ValueError(f"action {name!r}: next.zone must name an existing zone")
            if "action" in entry and not isinstance(entry["action"], str):
                raise ValueError(f"action {name!r}: next.action must be a string")


def _validate_zone(name: str, z: dict, all_names: set) -> None:
    """Validate a single zone dict."""
    if not isinstance(z, dict):
        raise ValueError(f"zone {name!r}: must be an object")
    if "enabled" in z and not isinstance(z["enabled"], bool):
        raise ValueError(f"zone {name!r}: 'enabled' must be a bool")
    if "mode_group" in z and not (isinstance(z["mode_group"], str) and z["mode_group"] in MODE_GROUPS):
        raise ValueError(f"zone {name!r}: mode_group must be one of {MODE_GROUPS}")
    if "class" in z and not (isinstance(z["class"], str) and z["class"] in ZONE_CLASSES):
        raise ValueError(f"zone {name!r}: class must be one of {ZONE_CLASSES}")
    sz = z.get("safezone")
    if not (isinstance(sz, list) and len(sz) == 6):
        raise ValueError(f"zone {name!r}: safezone must be a list of 6 [lo, hi] pairs")
    for i, pair in enumerate(sz):
        if not (isinstance(pair, list) and len(pair) == 2
                and _is_num(pair[0]) and _is_num(pair[1]) and pair[0] < pair[1]):
            raise ValueError(f"zone {name!r}: safezone axis {i} must be [lo, hi] with lo < hi")
    if not _is_num(z.get("speed")):
        raise ValueError(f"zone {name!r}: speed must be a number")
    exits = z.get("exits")
    if exits is not None:
        if not (isinstance(exits, list) and all(
                isinstance(e, list) and len(e) == 2 and isinstance(e[0], str) and e[0] in all_names
                and isinstance(e[1], str) for e in exits)):
            raise ValueError(f"zone {name!r}: exits must be a list of [zone, action] pairs")
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
    def cls(self) -> str:
        """The zone's engagement-ladder class (default ``"scan"`` when absent)."""
        return self.data.get("class", "scan")

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
        """The action's first anchor pose (``poses[0]``), or None when absent/malformed.

        Kept for compatibility; the runtime reads the *current* anchor
        (``poses[action_index]``) directly from the machine each tick.
        """
        a = self._action(name)
        poses = a.get("poses")
        if isinstance(poses, list) and poses and isinstance(poses[0], list) and len(poses[0]) == 6:
            return list(poses[0])
        return None

    def action_variable_axes(self, name: str) -> List[int]:
        """The action's ``variable_axes`` (list of 0-5 indices), or [] when absent."""
        a = self._action(name)
        va = a.get("variable_axes")
        if isinstance(va, list):
            return [x for x in va if isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 5]
        return []

    def action_axis_bands(self, name: str) -> Dict[int, List[float]]:
        """The action's ``axis_bands`` (dict of axis -> ``[min, max]``), or ``{}`` when absent.

        Used by active-class actions (see :data:`ACTIVE_CLASSES`): the keys are the
        variable axes the camera servo may move, and each value is the absolute
        ``[min, max]`` band the axis roams within (clamped to the zone safezone). A
        band of ``[v, v]`` (min == max) is a fixed axis (the servo never moves it).
        Malformed keys/bands are dropped; the result is keyed by int axis index.
        """
        a = self._action(name)
        ab = a.get("axis_bands")
        if not isinstance(ab, dict):
            return {}
        out: Dict[int, List[float]] = {}
        for k, band in ab.items():
            ax = self._axis_band_key(k)
            if ax is None:
                continue
            if not (isinstance(band, list) and len(band) == 2 and all(_is_num(x) for x in band)):
                continue
            lo, hi = float(band[0]), float(band[1])
            if lo > hi:
                lo, hi = hi, lo
            out[ax] = [lo, hi]
        return out

    @staticmethod
    def _axis_band_key(k) -> Optional[int]:
        """Normalize an ``axis_bands`` key (JSON string or int) to a 0-5 int, else None."""
        if isinstance(k, bool):
            return None
        if isinstance(k, int) and 0 <= k <= 5:
            return k
        if isinstance(k, str) and k.strip().lstrip("-").isdigit():
            v = int(k)
            return v if 0 <= v <= 5 else None
        return None

    def action_behavior(self, name: str) -> str:
        """The action's behavior name, or ``"calm"`` when absent/unknown."""
        a = self._action(name)
        beh = a.get("behavior")
        if isinstance(beh, str) and beh in BEHAVIORS:
            return beh
        return "calm"

    def action_next(self, name: str):
        """The action's ``next`` field as stored (a single ``{zone, action}`` dict,
        an ordered list of ``{zone, action}`` dicts, or None when absent)."""
        a = self._action(name)
        n = a.get("next")
        if isinstance(n, dict) or isinstance(n, list):
            return n
        return None

    def action_next_list(self, name: str) -> List[dict]:
        """The action's ``next`` as an ordered list of ``{zone, action}`` dicts.

        Accepts the legacy single-dict form (wrapped into a 1-element list) or the
        new list form. Returns ``[]`` when the field is absent or malformed.
        """
        a = self._action(name)
        n = a.get("next")
        if isinstance(n, dict):
            return [n]
        if isinstance(n, list):
            return [x for x in n if isinstance(x, dict)]
        return []

    def action_dwell_s(self, name: str) -> Optional[float]:
        """The action's dwell time in seconds (autonomy advance delay), or None.

        When None, the brain falls back to the global ``autonomy.dwell_s``.
        """
        a = self._action(name)
        d = a.get("dwell_s")
        if _is_num(d):
            return d
        return None

    def action_speed(self, name: str) -> Optional[float]:
        """The action's speed as a single number, or None.

        When None, the machine falls back to the zone speed.
        """
        a = self._action(name)
        s = a.get("speed")
        if _is_num(s):
            return s
        return None

    @property
    def mode_group(self) -> Optional[str]:
        """The zone's mode group, or None when absent."""
        mg = self.data.get("mode_group")
        if isinstance(mg, str) and mg in MODE_GROUPS:
            return mg
        return None

    def exits(self) -> List[Tuple[str, str]]:
        """The zone-level exits as a list of (zone, action) tuples.

        Returns [] when the field is absent or malformed.
        """
        e = self.data.get("exits")
        if not (isinstance(e, list)):
            return []
        return [(pair[0], pair[1]) for pair in e
                if isinstance(pair, list) and len(pair) == 2
                and isinstance(pair[0], str) and isinstance(pair[1], str)]

    def action_entry_pose(self, name: str) -> Optional[List[float]]:
        """The pose the robot moves to when transitioning into this action.

        This is the first anchor (``poses[0]``). Returns None if the action
        is unknown or malformed.
        """
        return self.action_base_pose(name)

    def action_end_pose(self, name: str) -> Optional[List[float]]:
        """The pose the action ends at (its last anchor, ``poses[-1]``).

        Returns None if the action is unknown or malformed. Used for the
        shared-position continuity check between connected actions (the
        source's end pose should match the target's start pose).
        """
        a = self._action(name)
        poses = a.get("poses")
        if isinstance(poses, list) and poses and isinstance(poses[-1], list) and len(poses[-1]) == 6:
            return list(poses[-1])
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
        """The zone graph derived from actions' ``next`` fields and zone-level ``exits``.

        Returns ``{zone_name: [reachable_zone_names]}`` where each reachable
        zone is named by some action's ``next.zone`` or zone-level ``exits`` entry.
        Used by the display and editor to render the navigation graph.
        """
        g: Dict[str, List[str]] = {}
        for name, z in self._by_name.items():
            targets = set()
            for an in z.actions():
                for n in z.action_next_list(an):
                    if isinstance(n.get("zone"), str):
                        targets.add(n["zone"])
            for tz, _ in z.exits():
                targets.add(tz)
            g[name] = sorted(targets)
        return g

    def table(self) -> Dict[str, dict]:
        """A copy of the full zone table (for the HTTP ``GET /api/zones`` endpoint)."""
        return {n: dict(d) for n, d in self._zones.items()}

    def action_pose_imgs(self, zone_name: str, action_name: str) -> Optional[List]:
        """The action's per-pose reference-image filenames (aligned with ``poses``), or None.

        Each entry is a filename string or ``None`` (a pose with no image). Used by the
        patches adapter to build the ``poseindex`` array for the pose level.
        """
        if not self.has(zone_name):
            return None
        imgs = self.get(zone_name)._action(action_name).get("pose_imgs")
        return imgs if isinstance(imgs, list) else None

    def action_pose_videos(self, zone_name: str, action_name: str) -> Optional[List]:
        """The action's per-pose video filenames (aligned with ``poses``), or None.

        Each entry is a filename string or ``None`` (a pose with no video). Used by the
        patches adapter to build the video patch (the new action-only patch system).
        """
        if not self.has(zone_name):
            return None
        vids = self.get(zone_name)._action(action_name).get("pose_videos")
        return vids if isinstance(vids, list) else None

    def mode_groups(self) -> Dict[str, List[str]]:
        """Zones grouped by mode_group: ``{group_name: [zone_names]}``.

        Zones without a mode_group are collected under ``"ungrouped"``.
        """
        groups: Dict[str, List[str]] = {}
        for name, z in self._by_name.items():
            mg = z.mode_group or "ungrouped"
            groups.setdefault(mg, []).append(name)
        return groups
