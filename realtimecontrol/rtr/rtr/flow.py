"""Pure navigation helpers shared by TUI and MIDI.

This module (a sibling of ``core``/``state``/``io``/``remote``) contains the
building blocks for virtual cursor navigation over zone actions and the
zone graph derived from actions' ``next`` fields. Both :mod:`rtr.remote.tui`
(urwid UI) and :mod:`rtr.io.midi` (MIDI input) import from here to avoid
layering violations (the helpers are pure data, not I/O).

Zones are pure safe-boundaries; the navigation graph is derived from the
``next`` fields on actions. Each action may declare ``next: {zone, action}``
naming the zone and action to trigger when the action completes.
"""

from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SECTION_ORDER = {"action": 0, "zones": 1}


# ---------------------------------------------------------------------------
# Zone graph helpers (derived from action ``next`` fields).
# ---------------------------------------------------------------------------
def _first_enabled_action(zones: Dict[str, dict], zone: str) -> Optional[str]:
    """The first enabled action name in ``zone`` (table order), or None.

    Used to resolve the entry action when a zone row is activated via
    ``trigger_action``.
    """
    z = zones.get(zone)
    if not isinstance(z, dict):
        return None
    actions = z.get("actions", {})
    if not isinstance(actions, dict):
        return None
    for name, a in actions.items():
        if not (isinstance(a, dict) and a.get("enabled") is False):
            return name
    return None


def reachable_targets(zones: Dict[str, dict], zone: Optional[str]) -> List[str]:
    """The zones reachable from ``zone`` via its actions' ``next`` fields.

    Returns zone names in first-seen order (table order of the actions).
    """
    if not isinstance(zones, dict) or not isinstance(zone, str):
        return []
    z = zones.get(zone)
    if not isinstance(z, dict):
        return []
    actions = z.get("actions", {})
    seen = set()
    out: List[str] = []
    if isinstance(actions, dict):
        for a in actions.values():
            if isinstance(a, dict):
                n = a.get("next")
                if isinstance(n, dict) and isinstance(n.get("zone"), str):
                    if n["zone"] not in seen:
                        seen.add(n["zone"])
                        out.append(n["zone"])
    return out


def reverse_targets(zones: Dict[str, dict], zone: Optional[str]) -> List[str]:
    """The zones that can reach ``zone`` (incoming edges from ``next`` fields).

    Returns zone names in table order (first-seen order in the table).
    """
    if not isinstance(zones, dict) or not isinstance(zone, str):
        return []
    out: List[str] = []
    for n, z in zones.items():
        if not isinstance(z, dict):
            continue
        actions = z.get("actions", {})
        if isinstance(actions, dict):
            for a in actions.values():
                if isinstance(a, dict):
                    nxt = a.get("next")
                    if isinstance(nxt, dict) and nxt.get("zone") == zone:
                        if n not in out:
                            out.append(n)
                        break
    return out


# ---------------------------------------------------------------------------
# Action helpers
# ---------------------------------------------------------------------------
def action_loops(a: dict) -> bool:
    """Whether the action loops; defaults to True."""
    if isinstance(a, dict):
        v = a.get("loop")
        if isinstance(v, bool):
            return v
    return True


def action_next(a: dict) -> Optional[dict]:
    """The action's ``next`` field (``{zone, action}``), or None when absent."""
    if isinstance(a, dict):
        n = a.get("next")
        if isinstance(n, dict):
            return n
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

    An action row sends ``play_action``. A zone row (from the ``next`` graph)
    sends ``trigger_action`` with the zone name and its first enabled action;
    the core transitions to that zone and begins the action.
    """
    if item.get("kind") == "zone":
        cmd = {"cmd": "trigger_action", "zone": item["name"]}
        if item.get("target_action"):
            cmd["action"] = item["target_action"]
        return [cmd]
    return [{"cmd": "play_action", "action": item["name"]}]


# ---------------------------------------------------------------------------
# Build items
# ---------------------------------------------------------------------------
def build_items(zones: Dict[str, dict], zone: Optional[str]) -> List[dict]:
    """The selectable rows for a zone: its actions + reachable zones.

    Returns a list of item dicts: ``{"section", "kind", "name", "action",
    "target_action", "back"}``. Actions come from the current zone's action
    table (in order); the "zones" section is the ordered union of
    ``reachable_targets`` (from the zone's actions' ``next`` fields) and
    ``reverse_targets`` (zones whose actions point back to this zone).
    A "zones" row has ``back=True`` when it is a reverse-only target (not in
    the zone's own reachable set), and ``target_action`` is the first
    enabled action of the target zone (used by ``activation_commands``).
    """
    items: List[dict] = []
    z = zones.get(zone) if isinstance(zones, dict) and zone else None
    if not isinstance(z, dict):
        return items
    actions = z.get("actions", {})
    if isinstance(actions, dict):
        for name, a in actions.items():
            items.append({
                "section": "action",
                "kind": "action",
                "name": name,
                "action": a if isinstance(a, dict) else None,
            })
    fwd = reachable_targets(zones, zone)
    fwd_set = set(fwd)
    for name in fwd:
        items.append({
            "section": "zones",
            "kind": "zone",
            "name": name,
            "action": None,
            "target_action": _first_enabled_action(zones, name),
            "back": False,
        })
    for name in reverse_targets(zones, zone):
        if name not in fwd_set:
            items.append({
                "section": "zones",
                "kind": "zone",
                "name": name,
                "action": None,
                "target_action": _first_enabled_action(zones, name),
                "back": True,
            })
    return items
