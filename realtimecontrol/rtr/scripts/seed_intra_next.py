#!/usr/bin/env python3
"""Seed the intra-zone full mesh into zones.json (idempotent, additive).

Every action in a zone gets a ``next`` target for every OTHER action in the same
zone (full mesh). Existing ``next`` targets (intra- or inter-zone) are preserved;
only the missing intra-zone connections are added. The brain picks a random valid
next, so the robot can roam to any sibling action in the zone.

Usage:
  python3 seed_intra_next.py            # apply the migration to zones.json
  python3 seed_intra_next.py --dry-run  # preview the changes without writing
"""
import argparse
import json
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # rtr/
ZONES_PATH = HERE / "zones.json"


def next_list(a):
    """The action's ``next`` as a list of {zone, action} dicts (mirrors the editor)."""
    n = a.get("next")
    if isinstance(n, dict):
        return [n]
    if isinstance(n, list):
        return [x for x in n if isinstance(x, dict)]
    return []


def write_next(a, targets):
    """Write the action's ``next`` from a list of targets (0 -> delete, 1 -> dict, 2+ -> list)."""
    if not targets:
        a.pop("next", None)
        return
    clean = []
    for t in targets:
        o = OrderedDict()
        if t.get("zone"):
            o["zone"] = t["zone"]
        if t.get("action"):
            o["action"] = t["action"]
        clean.append(o)
    if len(clean) == 1:
        a["next"] = clean[0]
    else:
        a["next"] = clean


def seed(zones):
    """Add the missing intra-zone ``next`` links. Returns (added, changed_zones)."""
    added = 0
    changed_zones = 0
    for zn, z in zones.items():
        if not isinstance(z, dict):
            continue
        actions = z.get("actions")
        if not isinstance(actions, dict):
            continue
        names = list(actions.keys())
        if len(names) < 2:
            continue  # single-action (or empty) zones have no intra-zone links
        zone_changed = False
        for an, a in actions.items():
            if not isinstance(a, dict):
                continue
            targets = next_list(a)
            have = {(t.get("zone"), t.get("action")) for t in targets}
            local_changed = False
            for other in names:
                if other == an:
                    continue
                if (zn, other) in have:
                    continue
                targets.append(OrderedDict([("zone", zn), ("action", other)]))
                have.add((zn, other))
                added += 1
                local_changed = True
                zone_changed = True
            if local_changed:
                write_next(a, targets)
        if zone_changed:
            changed_zones += 1
    return added, changed_zones


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="preview without writing")
    args = ap.parse_args()

    with open(ZONES_PATH) as f:
        data = json.load(f, object_pairs_hook=OrderedDict)

    zones = data.get("zones", {})
    added, changed = seed(zones)

    if args.dry_run:
        print(f"dry-run: would add {added} intra-zone next link(s) across {changed} zone(s)")
        return

    with open(ZONES_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    print(f"zones.json: added {added} intra-zone next link(s) across {changed} zone(s)")


if __name__ == "__main__":
    main()
