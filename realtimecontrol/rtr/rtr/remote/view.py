"""Pure view layer for the remote TUI.

Turns remote state (zone/action table + live state + page/cursor) into a list
of rows plus the selectable items, with no framework (urwid/pygame) dependency.
Both renderers consume this: the urwid desktop TUI (:mod:`rtr.remote.tui`) and
the SDL2 device app (:mod:`rtr.remote.sdlapp`).

A row is a dict with a ``style`` and either ``text`` (a simple text row) or
``pairs`` (a ``label: value`` state row)::

    {"text": "  ▸ zone A", "style": "item_sel"}
    {"pairs": [("ZONE", "a"), ("MODE", "idle")], "style": "state"}
    {"style": "divider"}

Style keys are named (no urwid/pygame types); each renderer maps them to its
own palette (the ``Theme`` class in :mod:`rtr.remote.tui` and the color table
in :mod:`rtr.remote.sdlapp`).
"""

from typing import Any, Dict, List, Optional, Tuple

from ..flow import (
    action_enabled,
    action_loops,
    action_next,
    activation_commands,
    build_items,
)


# ---------------------------------------------------------------------------
# Constants (moved from tui.py so both renderers share them).
# ---------------------------------------------------------------------------
SECTION_LABELS = {
    "action": "ACTIONS",
    "zones": "GO TO",
}

PAGES = ("NAV", "CAMERA", "BRAIN", "HUNT", "CADENCE")

# Joystick button codes (Linux EV_KEY) for page navigation. L1 = previous page,
# R1 = next page (mirrors the keyboard left/right). The device is configured via
# --device (default /dev/input/event3; the handheld is typically event2).
JOY_L1 = 310
JOY_R1 = 311

# The app keys the renderers understand; input (keyboard + joystick) is unified
# to this small set before it reaches the state machine.
APP_KEYS = ("up", "down", "left", "right", "enter", "q", "escape", "h")

# Default EV_KEY → app-key map for the joystick (override via config ``joy_map``).
# d-pad: 15/16/17/18; A=29 (enter), B=30 (q), H=8 (h);
# shoulders: L1=310 (left / page-prev), R1=311 (right / page-next).
DEFAULT_JOY_MAP = {
    15: "up",
    16: "down",
    17: "left",
    18: "right",
    29: "enter",
    30: "q",
    8: "h",
    310: "left",
    311: "right",
}

# Selectable rows for the CADENCE page. Each sends a command to the core.
# The fixed-rhythm interval is offered in seconds (a few sensible steps); the
# core converts it to ticks. "arrival" advances as soon as the robot reaches
# its target; the fixed options advance every N seconds.
CADENCE_ITEMS = [
    {"kind": "engine", "name": "move: block",
     "cmd": {"cmd": "set_engine_mode", "mode": "block"}},
    {"kind": "engine", "name": "move: stream",
     "cmd": {"cmd": "set_engine_mode", "mode": "stream"}},
    {"kind": "engine", "name": "cadence: arrival",
     "cmd": {"cmd": "set_cadence", "mode": "arrival"}},
    {"kind": "engine", "name": "cadence: fixed 0.5 s",
     "cmd": {"cmd": "set_cadence", "mode": "fixed", "every": 0.5}},
    {"kind": "engine", "name": "cadence: fixed 1.0 s",
     "cmd": {"cmd": "set_cadence", "mode": "fixed", "every": 1.0}},
    {"kind": "engine", "name": "cadence: fixed 2.0 s",
     "cmd": {"cmd": "set_cadence", "mode": "fixed", "every": 2.0}},
]


# ---------------------------------------------------------------------------
# Row shaping helpers (framework-free).
# ---------------------------------------------------------------------------
def _clip(text: str, width: int) -> str:
    return text[:width] if width > 0 else text


def _center(text: str, width: int) -> str:
    pad = max(0, width - len(text))
    left = pad // 2
    return " " * left + text + " " * (pad - left)


def item_label(item: dict) -> str:
    """The display string for an item (name + loop marker + next annotation).

    A "zones" row with ``back=True`` (a reverse-only target) gets a ``←`` marker
    so forward vs. back is visible in an asymmetric next-field graph.
    """
    name = item.get("name", "")
    if item.get("kind") != "action":
        return f"{name} ←" if item.get("back") else name
    a = item.get("action")
    s = name
    if action_loops(a):
        s += " ↻"
    n = action_next(a)
    if n:
        nz = n.get("zone")
        if isinstance(nz, str):
            s += f" → {nz}"
        else:
            na = n.get("action")
            if isinstance(na, str):
                s += f" → {na}"
    return s


def current_items(page: int, items: List[dict]) -> List[dict]:
    """The selectable rows for the current page (NAV items vs. CADENCE options)."""
    return CADENCE_ITEMS if page == 4 else items


def activate_commands(item: dict) -> Tuple[List[dict], str]:
    """The core command(s) to send and the status message for activating an item.

    An engine row sends its single ``cmd``; an action row sends ``play_action``;
    a zone row sends ``trigger_action`` (see :func:`rtr.flow.activation_commands`).
    Returns ``(commands, status)``.
    """
    if item.get("kind") == "engine":
        return [item["cmd"]], f"→ {item['name']}"
    cmds = activation_commands(item)
    if item.get("kind") == "action":
        return cmds, f"→ play {item['name']}"
    return cmds, f"→ trigger {item['name']}"


def _item_row(i: int, item: dict, cursor: int, current_len: int, width: int) -> dict:
    """One selectable row: selected (cursor), disabled (dim), or normal."""
    if i == cursor and i < current_len:
        return {"text": _clip(f"  ▸ {item_label(item)}", width), "style": "item_sel"}
    s = _clip(f"    {item_label(item)}", width)
    if item.get("kind") == "action" and item.get("action") is not None \
       and not action_enabled(item["action"]):
        return {"text": s, "style": "item_dim"}
    return {"text": s, "style": "item"}


# ---------------------------------------------------------------------------
# Page content builders (pure: state → rows).
# ---------------------------------------------------------------------------
def _nav_content(state, zones, items, cursor, current_len, width):
    """Page 0: zone/action navigation (the original TUI content)."""
    rows = []
    zone = state.get("zone", "—")
    mode = state.get("mode", "—")
    action = state.get("action", "—")
    rows.append({"pairs": [("ZONE", zone), ("MODE", mode), ("ACTION", action)],
                 "style": "state"})
    moving = state.get("moving")
    speed = state.get("speed", "—")
    mv = "—" if moving is None else ("yes" if moving else "no")
    rows.append({"pairs": [("MOVING", mv), ("SPEED", speed)], "style": "state"})
    rows.append({"style": "divider"})
    if not items:
        if not zones and not state:
            rows.append({"text": "  connecting… waiting for core state", "style": "help"})
        else:
            rows.append({"text": "  (no actions or exits in this zone)", "style": "help"})
    else:
        current_section = None
        for i, item in enumerate(items):
            sec = item["section"]
            if sec != current_section:
                rows.append({"text": f"  {SECTION_LABELS.get(sec, sec.upper())}", "style": "label"})
                current_section = sec
            rows.append(_item_row(i, item, cursor, current_len, width))
    return rows


def _camera_content(cam_telem, cursor, current_len, width):
    """Page 1: camera overview (person count, status, track, face)."""
    rows = []
    rows.append({"text": "  CAMERA", "style": "label"})
    rows.append({"style": "divider"})
    status = cam_telem.get("status", {})
    if status:
        cam = status.get("camera", "—")
        fps = status.get("fps", 0)
        ok = status.get("ok")
        ok_s = "—" if ok is None else ("yes" if ok else "no")
        rows.append({"pairs": [("CAM", cam), ("FPS", f"{fps:.1f}"), ("OK", ok_s)],
                     "style": "state"})
    cands = cam_telem.get("candidates") or {}
    cand_list = cands.get("candidates") or []
    n = len(cand_list)
    rows.append({"pairs": [("PERSONS", n)], "style": "state"})
    for c in cand_list[:5]:
        cid = c.get("id", "?")
        conf = c.get("conf", 0)
        rows.append({"text": _clip(f"  #{cid}  conf={conf:.2f}", width), "style": "text"})
    track = cam_telem.get("track", {})
    if track:
        tid = track.get("id", "?")
        dx = track.get("dx", 0)
        dy = track.get("dy", 0)
        w = track.get("w", 0)
        h = track.get("h", 0)
        rows.append({"pairs": [("TRACK",
                                 f"#{tid} dx={dx:.1f} dy={dy:.1f} w={w:.0f} h={h:.0f}")],
                     "style": "state"})
    face = cam_telem.get("face", {})
    if face:
        fid = face.get("id", "?")
        fdx = face.get("dx", 0)
        fdy = face.get("dy", 0)
        rows.append({"pairs": [("FACE", f"#{fid} dx={fdx:.1f} dy={fdy:.1f}")],
                     "style": "state"})
    rows.append({"style": "divider"})
    rows.append({"text": "  CAMERA ACTIONS", "style": "label"})
    rows.append(_item_row(0, {"kind": "action", "name": "detect", "action": None},
                           cursor, current_len, width))
    rows.append(_item_row(1, {"kind": "action", "name": "stop_hunt", "action": None},
                           cursor, current_len, width))
    return rows


def _brain_content(state):
    """Page 2: robot state map (zone, mode, action, heading)."""
    rows = []
    rows.append({"text": "  BRAIN", "style": "label"})
    rows.append({"style": "divider"})
    zone = state.get("zone", "—")
    mode = state.get("mode", "—")
    action = state.get("action", "—")
    rows.append({"pairs": [("ZONE", zone), ("MODE", mode), ("ACTION", action)],
                 "style": "state"})
    moving = state.get("moving")
    speed = state.get("speed", "—")
    mv = "—" if moving is None else ("yes" if moving else "no")
    rows.append({"pairs": [("MOVING", mv), ("SPEED", speed)], "style": "state"})
    # Heading: A1 (base rotation) from the joint pose.
    joints = state.get("joints")
    if joints and len(joints) >= 6:
        a1 = joints[0]
        rows.append({"pairs": [("HEADING", f"A1={a1:.1f}°")], "style": "state"})
    else:
        rows.append({"pairs": [("HEADING", "—")], "style": "state"})
    rows.append({"style": "divider"})
    rows.append({"text": "  STATE", "style": "label"})
    rows.append({"text": f"  zone: {zone}", "style": "text"})
    rows.append({"text": f"  mode: {mode}", "style": "text"})
    rows.append({"text": f"  action: {action}", "style": "text"})
    return rows


def _hunt_content(hunt, cam_telem, cursor, current_len, width):
    """Page 3: hunt state with feral RED warning."""
    rows = []
    rows.append({"text": "  HUNT", "style": "label"})
    rows.append({"style": "divider"})
    hunt_state = hunt.get("state", "idle")
    lock_id = hunt.get("lock_id")
    feral = hunt.get("feral", False)
    lock_s = f"#{lock_id}" if lock_id is not None else "—"
    rows.append({"pairs": [("STATE", hunt_state), ("LOCK", lock_s)], "style": "state"})
    if feral:
        rows.append({"text": "  ⚠ FERAL — rapid hunt-loop cycling", "style": "feral"})
    else:
        rows.append({"text": "  feral: no", "style": "conn_ok"})
    face = cam_telem.get("face") or {}
    face_vis = bool(face.get("id") is not None)
    rows.append({"pairs": [("FACE", "visible" if face_vis else "not visible")],
                 "style": "state"})
    rows.append({"style": "divider"})
    rows.append({"text": "  HUNT ACTIONS", "style": "label"})
    rows.append(_item_row(0, {"kind": "action", "name": "stop_hunt", "action": None},
                           cursor, current_len, width))
    return rows


def _cadence_content(engine, cursor, current_len, width):
    """Page 4: engine move mode + cadence (current state + set options)."""
    rows = []
    rows.append({"text": "  CADENCE", "style": "label"})
    rows.append({"style": "divider"})
    eng = engine
    mm = eng.get("move_mode") or "—"
    cad = eng.get("cadence") or "—"
    every = eng.get("cadence_every")
    every_s = f"{every} ticks" if every is not None else "—"
    rows.append({"pairs": [("MOVE", mm), ("CADENCE", cad), ("EVERY", every_s)],
                 "style": "state"})
    rows.append({"style": "divider"})
    rows.append({"text": "  SET", "style": "label"})
    for i, item in enumerate(CADENCE_ITEMS):
        rows.append(_item_row(i, item, cursor, current_len, width))
    return rows


def build_rows(state, zones, page, cursor, engine, hunt, cam_telem, width,
               *, connected=False, endpoint="", status="") -> List[dict]:
    """Build the full screen as a list of rows for the current state.

    Args:
        state: the live robot state dict (zone/mode/action/moving/speed/joints).
        zones: the zone/action table.
        page: the current page index (see :data:`PAGES`).
        cursor: the current selection index (shared across pages).
        engine: the engine cadence state (move_mode/cadence/cadence_every).
        hunt: the hunt state dict.
        cam_telem: the camera telemetry dict.
        width: the terminal width in columns (bar centering + text clipping).
        connected: whether the core link is up (drives the connection line).
        endpoint: the core endpoint string for the connection line.
        status: the status line text.
    """
    items = build_items(zones, state.get("zone"))
    current = current_items(page, items)
    current_len = len(current)
    rows: List[dict] = []
    rows.append({"text": _center("RTR REMOTE", width), "style": "bar"})
    if connected:
        rows.append({"text": _clip(f"CONNECTED   {endpoint}", width), "style": "conn_ok"})
    else:
        rows.append({"text": _clip(f"DISCONNECTED  {endpoint}", width), "style": "conn_bad"})
    page_names = " | ".join(PAGES)
    cur = PAGES[page]
    rows.append({"text": _clip(f"  [{cur}]  {page_names}", width), "style": "page"})
    rows.append({"style": "divider"})
    if page == 0:
        rows.extend(_nav_content(state, zones, items, cursor, current_len, width))
    elif page == 1:
        rows.extend(_camera_content(cam_telem, cursor, current_len, width))
    elif page == 2:
        rows.extend(_brain_content(state))
    elif page == 3:
        rows.extend(_hunt_content(hunt, cam_telem, cursor, current_len, width))
    elif page == 4:
        rows.extend(_cadence_content(engine, cursor, current_len, width))
    rows.append({"style": "divider"})
    rows.append({"text": _clip(status, width), "style": "status"})
    rows.append({"text": _clip("←→ pages · ↑↓ move · enter select · home/q quits", width),
                 "style": "help"})
    return rows


# ---------------------------------------------------------------------------
# Shared state (pure, no I/O) — mirrors the RemoteTUI data fields so both
# renderers share the same state-transition logic.
# ---------------------------------------------------------------------------
class RemoteState:
    """Holds the remote's state and the pure transitions over it.

    The renderers own the I/O (connection, input, joystick) and call these
    methods from their main thread; :meth:`build_rows` renders the current
    state into rows.
    """

    def __init__(self):
        self.state: Dict[str, Any] = {}
        self.state_key: Any = object()
        self.zones: Dict[str, dict] = {}
        self.status = "initialising…"
        self.dirty = True
        self.page = 0
        self.cursor = 0
        self.items: List[dict] = []
        self.hunt: Dict[str, Any] = {}
        self.cam_telem: Dict[str, Any] = {}
        self.engine: Dict[str, Any] = {}

    def apply_state(self, data) -> None:
        self.hunt = data.get("hunt", {})
        self.cam_telem = data.get("cam_telem", {})
        self.engine = {
            "move_mode": data.get("move_mode"),
            "cadence": data.get("cadence"),
            "cadence_every": data.get("cadence_every"),
        }
        key = (data.get("zone"), data.get("mode"), data.get("action"),
               data.get("moving"), data.get("speed"),
               data.get("hunt", {}).get("state"),
               data.get("hunt", {}).get("feral"),
               data.get("move_mode"), data.get("cadence"),
               data.get("cadence_every"))
        if key != self.state_key:
            self.state = {
                "zone": data.get("zone"),
                "mode": data.get("mode"),
                "action": data.get("action"),
                "moving": data.get("moving"),
                "speed": data.get("speed"),
            }
            self.state_key = key
            self.rebuild_items()
            self.dirty = True

    def apply_zones(self, table) -> None:
        if table != self.zones:
            self.zones = table
            self.rebuild_items()
            self.dirty = True

    def rebuild_items(self) -> None:
        zone = self.state.get("zone")
        self.items = build_items(self.zones, zone)
        if self.cursor >= len(self.items):
            self.cursor = 0

    def current_items(self) -> List[dict]:
        return current_items(self.page, self.items)

    def page_item_count(self) -> int:
        return len(self.current_items())

    def set_page(self, p: int) -> None:
        self.page = max(0, min(len(PAGES) - 1, p))
        n = self.page_item_count()
        self.cursor = max(0, min(n - 1, self.cursor)) if n else 0
        self.dirty = True

    def move_cursor(self, d: int) -> None:
        n = self.page_item_count()
        if not n:
            return
        self.cursor = max(0, min(n - 1, self.cursor + d))
        self.dirty = True

    def on_joystick(self, ev) -> None:
        """Map joystick L1/R1 presses to page navigation (on press only)."""
        event_type, code, value = ev
        if event_type != 1 or value != 1:  # KEY event, press edge only
            return
        if code == JOY_L1:
            self.set_page(self.page - 1)
        elif code == JOY_R1:
            self.set_page(self.page + 1)

    def activate(self) -> Optional[Tuple[List[dict], str]]:
        """Activate the selected item; returns ``(commands, status)`` or None."""
        items = self.current_items()
        if 0 <= self.cursor < len(items):
            item = items[self.cursor]
            cmds, status = activate_commands(item)
            self.status = status
            self.dirty = True
            return cmds, status
        return None

    def build_rows(self, width: int, *, connected: bool = False,
                   endpoint: str = "") -> List[dict]:
        return build_rows(self.state, self.zones, self.page, self.cursor,
                           self.engine, self.hunt, self.cam_telem, width,
                           connected=connected, endpoint=endpoint,
                           status=self.status)
