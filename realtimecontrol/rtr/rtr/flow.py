"""Pure navigation helpers shared by TUI and MIDI.

This module (a sibling of ``core``/``state``/``io``/``remote``) contains the
building blocks for virtual cursor navigation over zone actions and exits.
Both :mod:`rtr.remote.tui` (urwid UI) and :mod:`rtr.io.midi` (MIDI input)
import from here to avoid layering violations (the helpers are pure data,
not I/O).
"""

from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ACTION_KINDS = ("internal", "entry", "exit")
_SECTION_ORDER = {"entry": 0, "internal": 1, "exit": 2, "zones": 3}


# ---------------------------------------------------------------------------
# Zone exit helpers
# ---------------------------------------------------------------------------
def _zone_exits(z: dict) -> List[str]:
    """The names of zones reachable from this zone (the exits graph)."""
    e = z.get("exits", [])
    return [x for x in e if isinstance(x, str)] if isinstance(e, list) else []


def reverse_exits(zones: Dict[str, dict], zone: Optional[str]) -> List[str]:
    """The zones that can reach this one (incoming edges), in table order.

    ``reverse_exits(z, cur)`` is every zone ``n`` whose ``exits`` list names
    ``cur`` — the zones the robot could have come from. In a symmetric table
    this equals the zone's own exits; in an asymmetric one it is the "back"
    option the UI offers alongside the forward exits.
    """
    if not isinstance(zones, dict) or not isinstance(zone, str):
        return []
    out: List[str] = []
    for n, z in zones.items():
        if not isinstance(z, dict):
            continue
        e = z.get("exits")
        if isinstance(e, list) and zone in e:
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# Action helpers
# ---------------------------------------------------------------------------
def action_kind(a: dict) -> str:
    """The action's kind (internal/entry/exit); defaults to internal."""
    k = a.get("kind") if isinstance(a, dict) else None
    return k if isinstance(k, str) and k in ACTION_KINDS else "internal"


def action_loops(a: dict) -> bool:
    """Whether the action loops; defaults to True (today's behaviour)."""
    if isinstance(a, dict):
        v = a.get("loop")
        if isinstance(v, bool):
            return v
    return True


def action_target(a: dict) -> Optional[dict]:
    """The action's target hand-off dict, or None when it declares none."""
    if isinstance(a, dict):
        t = a.get("target")
        if isinstance(t, dict):
            return t
    return None


def action_enabled(a: dict) -> bool:
    """Whether the action is enabled; defaults to True."""
    if isinstance(a, dict):
        v = a.get("enabled")
        if isinstance(v, bool):
            return v
    return True


def activation_commands(item: dict) -> List[dict]:
    """The core command(s) to send when an item is activated.

    An action (any kind) is played in ``action`` mode; the core's
    ``_advance_after_action`` resolves an exit action's ``target`` hand-off on
    completion. A zone (from the exits graph) is a direct ``goto_zone``.
    """
    if item.get("kind") == "exit":
        return [{"cmd": "goto_zone", "zone": item["name"]}]
    return [
        {"cmd": "play_action", "action": item["name"]},
        {"cmd": "set_mode", "mode": "action"},
    ]


# ---------------------------------------------------------------------------
# Build items
# ---------------------------------------------------------------------------
def build_items(zones: Dict[str, dict], zone: Optional[str]) -> List[dict]:
    """The selectable rows for a zone, grouped by action kind + the exits graph.

    Returns a list of item dicts: ``{"section", "kind", "name", "action", "back"}``
    (the ``back`` flag is only meaningful on the "zones" rows). Actions are ordered
    entry → internal → exit (then name); the "zones" section is the ordered union
    of the zone's declared ``exits`` (declared order) and its ``reverse_exits``
    (table order, skipping names already listed), so the operator is offered only
    the possible next states. A "zones" row has ``back=True`` when its name is not
    one of the zone's own exits (a reverse-only entry, marked ``←`` by
    :func:`item_label`).
    """
    items: List[dict] = []
    z = zones.get(zone) if isinstance(zones, dict) and zone else None
    if not isinstance(z, dict):
        return items
    actions = z.get("actions", {})
    action_items: List[dict] = []
    if isinstance(actions, dict):
        for name, a in actions.items():
            action_items.append({
                "section": action_kind(a),
                "kind": "action",
                "name": name,
                "action": a if isinstance(a, dict) else None,
            })
    elif isinstance(actions, list):
        for name in actions:
            if isinstance(name, str):
                action_items.append({
                    "section": "internal",
                    "kind": "action",
                    "name": name,
                    "action": None,
                })
    action_items.sort(key=lambda it: (_SECTION_ORDER.get(it["section"], 1), it["name"]))
    items.extend(action_items)
    exits = _zone_exits(z)
    exit_set = set(exits)
    for name in exits:
        items.append({
            "section": "zones", "kind": "exit", "name": name, "action": None, "back": False,
        })
    for name in reverse_exits(zones, zone):
        if name not in exit_set:
            items.append({
                "section": "zones", "kind": "exit", "name": name, "action": None, "back": True,
            })
    return items
