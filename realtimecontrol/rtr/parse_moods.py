"""Compile the mood network (``moods.json``) into the zone table (``zones.json``).

``moods.json`` is the creative, editable source of truth: each mood carries its full
zone data (``mode`` / ``speed`` / ``startpos`` / ``exitpos`` / ``safezone`` /
``actions``) plus a human ``meaning``. The ``links`` list is the *undirected* graph of
how moods flow into one another (how the robot moves). This script expands each
undirected link into symmetric ``exits`` and emits the ``zones.json`` the state machine
consumes, validating the result with :func:`rtr.state.zones.validate_state_data` before
writing.

Run directly::

    python parse_moods.py

Usage: ``python parse_moods.py [moods.json] [zones.json]`` (both default to the files
in this directory).
"""

import json
import os
import sys
from collections import deque

from rtr.state.zones import validate_state_data


def load_moods(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_exits(links) -> dict:
    """Expand undirected links into a per-zone sorted list of exits.

    Each link ``[a, b]`` means the robot may move a→b *and* b→a, so both directions are
    added. The result maps every mood name to its (sorted) list of exit zone names.
    """
    adj = {}
    for a, b in links:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return {name: sorted(neigh) for name, neigh in adj.items()}


def validate_links(links, moods):
    """Raise ``ValueError`` if a link names an unknown mood or is a self-loop."""
    names = set(moods)
    for a, b in links:
        if a not in names:
            raise ValueError(f"link [{a!r}, {b!r}]: {a!r} is not a mood")
        if b not in names:
            raise ValueError(f"link [{a!r}, {b!r}]: {b!r} is not a mood")
        if a == b:
            raise ValueError(f"link [{a!r}, {b!r}]: self-loop")


def build_zones(moods, exits) -> dict:
    """Assemble the zone table: each mood's data plus its computed ``exits``."""
    zones = {}
    for name, m in moods.items():
        zones[name] = {
            "enabled": m.get("enabled", True),
            "mode": m["mode"],
            "startpos": m["startpos"],
            "exitpos": m["exitpos"],
            "safezone": m["safezone"],
            "exits": exits.get(name, []),
            "speed": m["speed"],
            "actions": m.get("actions", {}),
        }
    return zones


def is_connected(zones) -> bool:
    """True if every zone is reachable from every other on the ``exits`` graph (BFS)."""
    names = list(zones)
    for start in names:
        seen = {start}
        q = deque([start])
        while q:
            node = q.popleft()
            for nxt in zones[node]["exits"]:
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        if seen != set(names):
            return False
    return True


def parse(moods_path: str, zones_path: str) -> dict:
    data = load_moods(moods_path)
    moods = data["moods"]
    links = data.get("links", [])
    validate_links(links, moods)
    exits = build_exits(links)
    zones = build_zones(moods, exits)
    if not is_connected(zones):
        raise ValueError("the mood graph is not fully connected")
    out = {
        "zones": zones,
        "poses": data.get("poses", {}),
        "lin_poses": data.get("lin_poses", {}),
    }
    validate_state_data(out)
    with open(zones_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    return out


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    moods_path = argv[1] if len(argv) > 1 else os.path.join(here, "moods.json")
    zones_path = argv[2] if len(argv) > 2 else os.path.join(here, "zones.json")
    out = parse(moods_path, zones_path)
    zones = out["zones"]
    print(f"wrote {zones_path}")
    print(f"moods: {len(zones)}  links: {sum(len(z['exits']) for z in zones.values()) // 2}")
    for name in zones:
        print(f"  {name:12s} exits={zones[name]['exits']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
