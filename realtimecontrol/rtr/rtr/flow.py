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

from collections import deque
from typing import Callable, Dict, List, Optional, Tuple


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
                for n in action_next_list(a):
                    if isinstance(n.get("zone"), str) and n["zone"] not in seen:
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
                    if any(x.get("zone") == zone for x in action_next_list(a)):
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


def action_next(a: dict):
    """The action's ``next`` field as stored (a single ``{zone, action}`` dict,
    an ordered list of ``{zone, action}`` dicts, or None when absent)."""
    if isinstance(a, dict):
        n = a.get("next")
        if isinstance(n, dict) or isinstance(n, list):
            return n
    return None


def action_next_list(a: dict) -> List[dict]:
    """The action's ``next`` as an ordered list of ``{zone, action}`` dicts.

    Accepts the legacy single-dict form (wrapped into a 1-element list) or the
    new list form. Returns ``[]`` when the field is absent or malformed.
    """
    if not isinstance(a, dict):
        return []
    n = a.get("next")
    if isinstance(n, dict):
        return [n]
    if isinstance(n, list):
        return [x for x in n if isinstance(x, dict)]
    return []


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


# ---------------------------------------------------------------------------
# Safe path-finding (BFS over the (zone, action) graph).
# ---------------------------------------------------------------------------
def find_safe_path(
    zones: Dict[str, dict],
    from_zone: str,
    from_action: str,
    to_zone: str,
    to_action: str,
    safety_check: Optional[Callable[..., Tuple[bool, List[str]]]] = None,
) -> Optional[List[Tuple[str, str]]]:
    """BFS over the ``(zone, action)`` node graph to find a safe path.

    Nodes are ``(zone, action)`` pairs. Edges come from:
    - each action's ``next`` field (ordered list of ``{zone, action}``), and
    - each zone's ``exits`` field (list of ``[zone, action]`` pairs).

    When ``safety_check`` is provided (signature:
    ``check_conflict(from_zone, from_action, to_zone, to_action) -> (bool, issues)``),
    each edge is filtered: edges where ``check_conflict`` returns ``True`` (a
    conflict exists) are excluded from the graph.

    Returns the shortest path as a list of ``(zone, action)`` waypoints
    (including the start and end), or ``None`` when no safe path exists.
    """
    if not isinstance(zones, dict):
        return None
    start = (from_zone, from_action)
    target = (to_zone, to_action)
    if start == target:
        return [start]

    # Build the adjacency list: node -> list of (neighbor_node, is_valid).
    # A node is valid when the zone exists, is enabled, and the action exists
    # and is enabled.
    def _node_valid(z: str, a: str) -> bool:
        if not isinstance(z, str) or not isinstance(a, str):
            return False
        zone = zones.get(z)
        if not isinstance(zone, dict):
            return False
        if zone.get("enabled") is False:
            return False
        actions = zone.get("actions", {})
        if not isinstance(actions, dict):
            return False
        act = actions.get(a)
        if not isinstance(act, dict):
            return False
        if act.get("enabled") is False:
            return False
        return True

    def _edges_from(z: str, a: str) -> List[Tuple[str, str]]:
        """All (zone, action) successors of node (z, a)."""
        out: List[Tuple[str, str]] = []
        zone = zones.get(z)
        if not isinstance(zone, dict):
            return out
        # Edges from the action's next field.
        act = zone.get("actions", {}).get(a, {})
        for n in action_next_list(act):
            nz, na = n.get("zone"), n.get("action")
            if isinstance(nz, str) and isinstance(na, str):
                out.append((nz, na))
        # Edges from the zone's exits field.
        exits = zone.get("exits", [])
        if isinstance(exits, list):
            for e in exits:
                if isinstance(e, (list, tuple)) and len(e) >= 2:
                    out.append((e[0], e[1]))
        return out

    # BFS.
    parent: Dict[Tuple[str, str], Tuple[str, str]] = {}
    visited: set = {start}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        if cur == target:
            # Reconstruct the path.
            path = [target]
            node = target
            while node != start:
                node = parent[node]
                path.append(node)
            path.reverse()
            return path
        for neighbor in _edges_from(*cur):
            if neighbor in visited:
                continue
            if not _node_valid(*neighbor):
                continue
            if safety_check is not None:
                conflict, _issues = safety_check(*cur, *neighbor)
                if conflict:
                    continue
            visited.add(neighbor)
            parent[neighbor] = cur
            queue.append(neighbor)
    return None


def find_safe_path_to_any(
    zones: Dict[str, dict],
    from_zone: str,
    from_action: str,
    targets: List[Tuple[str, str]],
    safety_check: Optional[Callable[..., Tuple[bool, List[str]]]] = None,
) -> Optional[List[Tuple[str, str]]]:
    """BFS over the ``(zone, action)`` graph to the shortest safe path to **any**
    node in ``targets``.

    ``targets`` is a list of ``(zone, action)`` nodes (the group's enabled nodes).
    This is the multi-target version of :func:`find_safe_path`: it stops as soon as
    any target node is reached, so the robot lands on the *nearest* reachable node
    in the group rather than a specific one.

    Returns the shortest path as a list of ``(zone, action)`` waypoints (including
    the start and the reached target), or ``None`` when no target is reachable.
    """
    if not isinstance(zones, dict) or not targets:
        return None
    start = (from_zone, from_action)
    target_set = set(targets)
    if start in target_set:
        return [start]

    # Build the adjacency list: node -> list of (neighbor_node).
    # A node is valid when the zone exists, is enabled, and the action exists
    # and is enabled (same validity rule as find_safe_path).
    def _node_valid(z: str, a: str) -> bool:
        if not isinstance(z, str) or not isinstance(a, str):
            return False
        zone = zones.get(z)
        if not isinstance(zone, dict):
            return False
        if zone.get("enabled") is False:
            return False
        actions = zone.get("actions", {})
        if not isinstance(actions, dict):
            return False
        act = actions.get(a)
        if not isinstance(act, dict):
            return False
        if act.get("enabled") is False:
            return False
        return True

    def _edges_from(z: str, a: str) -> List[Tuple[str, str]]:
        """All (zone, action) successors of node (z, a)."""
        out: List[Tuple[str, str]] = []
        zone = zones.get(z)
        if not isinstance(zone, dict):
            return out
        # Edges from the action's next field.
        act = zone.get("actions", {}).get(a, {})
        for n in action_next_list(act):
            nz, na = n.get("zone"), n.get("action")
            if isinstance(nz, str) and isinstance(na, str):
                out.append((nz, na))
        # Edges from the zone's exits field.
        exits = zone.get("exits", [])
        if isinstance(exits, list):
            for e in exits:
                if isinstance(e, (list, tuple)) and len(e) >= 2:
                    out.append((e[0], e[1]))
        return out

    # Multi-target BFS.
    parent: Dict[Tuple[str, str], Tuple[str, str]] = {}
    visited: set = {start}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for neighbor in _edges_from(*cur):
            if neighbor in visited:
                continue
            if not _node_valid(*neighbor):
                continue
            if safety_check is not None:
                conflict, _issues = safety_check(*cur, *neighbor)
                if conflict:
                    continue
            visited.add(neighbor)
            parent[neighbor] = cur
            if neighbor in target_set:
                # Reached a target: reconstruct the path.
                path = [neighbor]
                node = neighbor
                while node != start:
                    node = parent[node]
                    path.append(node)
                path.reverse()
                return path
            queue.append(neighbor)
    return None
