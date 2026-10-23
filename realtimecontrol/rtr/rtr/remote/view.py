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

import math
import time
from typing import Any, Dict, List, Optional, Tuple

from ..flow import (
    action_enabled,
    action_loops,
    action_next_list,
    activation_commands,
    build_items,
)
from .led import (
    BLACK_IDX,
    LED_ACTIONS,
    LED_NAMES,
    NUM_COLORS,
    NUM_LEDS,
    PALETTE,
    WHITE_IDX,
    build_frame,
)


# ---------------------------------------------------------------------------
# Constants (moved from tui.py so both renderers share them).
# ---------------------------------------------------------------------------
SECTION_LABELS = {
    "action": "ACTIONS",
    "zones": "GO TO",
    "theme": "THEME / LAYOUT",
    "buttons": "BUTTONS",
}

PAGES = ("NAV", "CAMERA", "BRAIN", "HUNT", "CADENCE", "TRIGGERS", "ARM", "LED", "SETUP")

# Max events kept in the TRIGGERS log (ring buffer).
EVENT_CAP = 100

# Robot joint hardware limits (degrees) for the ARM page bars.
AXES = ["A1", "A2", "A3", "A4", "A5", "A6"]
HW_LIMITS = {
    "A1": (-185.0, 185.0),
    "A2": (-135.0, 35.0),
    "A3": (-95, 158.0),
    "A4": (-180.0, 180.0),
    "A5": (-55.0, 100.0),
    "A6": (-150.0,150.0),
}

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
    {"kind": "engine", "name": "cadence: midi",
     "cmd": {"cmd": "set_cadence", "mode": "midi"}},
    {"kind": "engine", "name": "cadence: fixed 0.5 s",
     "cmd": {"cmd": "set_cadence", "mode": "fixed", "every": 0.5}},
    {"kind": "engine", "name": "cadence: fixed 1.0 s",
     "cmd": {"cmd": "set_cadence", "mode": "fixed", "every": 1.0}},
    {"kind": "engine", "name": "cadence: fixed 2.0 s",
     "cmd": {"cmd": "set_cadence", "mode": "fixed", "every": 2.0}},
]


# The d-pad is an ANALOG axis (EV_ABS, type 3), not a digital key. On the
# Trimui the d-pad maps to:
#   axis 17 (TRACKBALL): negative = UP, positive = DOWN
#   axis 16 (WHEEL):     negative = LEFT, positive = RIGHT
# Encoded as {app_key: [axis_code, sign]} where sign is -1 (negative) or +1
# (positive). The sign is read from the raw ABS value (see sdlapp._abs_sign);
# only the sign matters (center = 0).
DEFAULT_DPAD_MAP = {
    "up": [17, -1],
    "down": [17, 1],
    "left": [16, -1],
    "right": [16, 1],
}

# The d-pad (analog ABS) app keys, in wizard order.
DPAD_MAP_KEYS = ("up", "down", "left", "right")
# The face buttons (digital EV_KEY) app keys, in wizard order.
FACE_MAP_KEYS = ("enter", "q", "h")
# The full calibration wizard sequence: (app_key, input_kind) where input_kind
# is "abs" (d-pad axis) or "key" (face button). The d-pad is captured as an
# ABS press edge; the face buttons as a KEY press edge.
CALIB_SEQUENCE = (
    ("up", "abs"),
    ("down", "abs"),
    ("left", "abs"),
    ("right", "abs"),
    ("enter", "key"),
    ("q", "key"),
    ("h", "key"),
)
# The app keys the SETTINGS button mapper offers (the D-pad, A/enter, B/q, X/h).
# The order here is the order of the mapper rows and of the calibration wizard.
SETTINGS_MAP_KEYS = DPAD_MAP_KEYS + FACE_MAP_KEYS


# ---------------------------------------------------------------------------
# SETTINGS page (page 5): THEME/LAYOUT font size + BUTTONS mapper.
# ---------------------------------------------------------------------------
def _code_for(jm: Optional[dict], app_key: str) -> str:
    """The joystick code currently mapped to ``app_key`` (L1/R1 excluded).

    L1/R1 are reserved for page navigation and are never offered as a mappable
    button, so they are skipped here. Returns ``"?"`` when no code is mapped.
    """
    for code in sorted(jm or {}):
        if jm[code] == app_key and code not in (JOY_L1, JOY_R1):
            return str(code)
    return "?"


def _dpad_for(dmap: Optional[dict], app_key: str) -> str:
    """The d-pad ``axis/sign`` currently mapped to ``app_key``, or ``"?"``."""
    entry = (dmap or {}).get(app_key)
    if not isinstance(entry, (list, tuple)) or len(entry) != 2:
        return "?"
    axis, sign = entry
    return f"{axis}/{sign}"


def build_settings_items(settings: Optional[dict]) -> List[dict]:
    """The selectable rows for the SETUP page (font size + button mapper).

    ``settings`` carries the live values (``font_size`` + ``joy_map`` +
    ``dpad_map``); the rows are rebuilt on every render so the mapper shows the
    current code for each app key. Each row is a ``kind="ui"`` item the renderer
    acts on locally. The d-pad rows are ``input="abs"`` (analog axis); the face
    rows are ``input="key"`` (digital button).
    """
    settings = settings or {}
    fs = settings.get("font_size", 16)
    jm = settings.get("joy_map") or {}
    dmap = settings.get("dpad_map") or {}
    items = [{
        "kind": "ui",
        "section": "theme",
        "ui": "font_size",
        "name": f"font size: {fs}",
    }]
    # The full calibration wizard (re-runnable from SETUP).
    items.append({
        "kind": "ui",
        "section": "buttons",
        "ui": "calibrate",
        "name": "calibrate controller",
    })
    for ak in DPAD_MAP_KEYS:
        items.append({
            "kind": "ui",
            "section": "buttons",
            "ui": "map",
            "app_key": ak,
            "input": "abs",
            "name": f"map {ak}: {_dpad_for(dmap, ak)}",
        })
    for ak in FACE_MAP_KEYS:
        items.append({
            "kind": "ui",
            "section": "buttons",
            "ui": "map",
            "app_key": ak,
            "input": "key",
            "name": f"map {ak}: {_code_for(jm, ak)}",
        })
    return items


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
    nxt = action_next_list(a)
    if nxt:
        first = nxt[0]
        nz = first.get("zone")
        if isinstance(nz, str):
            s += f" → {nz}"
        else:
            na = first.get("action")
            if isinstance(na, str):
                s += f" → {na}"
    return s


def build_led_items(led_colors: Optional[List[int]] = None) -> List[dict]:
    """The selectable rows for the LED page (22 LEDs + 4 actions).

    Each LED row shows its name and current palette color. The 4 action rows
    (all white / all off / test all / restore) are ``kind="led_action"``.
    """
    led_colors = led_colors or [BLACK_IDX] * NUM_LEDS
    items = []
    for i in range(NUM_LEDS):
        items.append({
            "kind": "led",
            "led": i,
            "name": f"LED {i + 1:<3} {LED_NAMES[i]:<6} {PALETTE[led_colors[i]][0]}",
        })
    for action in LED_ACTIONS:
        items.append({"kind": "led_action", "action": action, "name": action})
    return items


def current_items(page: int, items: List[dict],
                  settings: Optional[dict] = None,
                  led_colors: Optional[List[int]] = None) -> List[dict]:
    """The selectable rows for the current page.

    Name-based dispatch: SETUP uses SETTINGS rows, CADENCE uses fixed options,
    LED uses LED items, every other page uses the zone/action ``items``.
    """
    name = PAGES[page] if 0 <= page < len(PAGES) else ""
    if name == "SETUP":
        return build_settings_items(settings)
    if name == "CADENCE":
        return CADENCE_ITEMS
    if name == "LED":
        return build_led_items(led_colors)
    return items


def _ui_status(item: dict) -> str:
    """The status line for activating a ``kind="ui"`` SETTINGS row."""
    ui = item.get("ui")
    if ui == "font_size":
        return "font size saved"
    if ui == "calibrate":
        return "calibrate: press UP now"
    if ui == "map":
        return f"map {item.get('app_key', '')}: capture next press"
    return "→ " + item.get("name", "")


def activate_commands(item: dict) -> Tuple[Optional[List[dict]], str]:
    """The core command(s) to send and the status message for activating an item.

    An engine row sends its single ``cmd``; an action row sends ``play_action``;
    a zone row sends ``trigger_action`` (see :func:`rtr.flow.activation_commands`).
    A ``ui`` row (SETUP page) is a local action, not a core command: it sends
    nothing (``None``) and the renderer acts on it. A ``led`` row advances that
    LED's color; a ``led_action`` row executes the local LED action. Both return
    ``(None, status)`` — the renderer handles the sysfs write.
    Returns ``(commands, status)``.
    """
    if item.get("kind") == "ui":
        return None, _ui_status(item)
    if item.get("kind") == "engine":
        return [item["cmd"]], f"→ {item['name']}"
    if item.get("kind") == "led":
        return None, f"LED {item['led'] + 1} → next color"
    if item.get("kind") == "led_action":
        return None, f"→ {item['name']}"
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


def _pose_xyz(pose) -> Optional[Tuple[float, float, float]]:
    """The (x, y, z) of a pose, or None when it has no numeric x/y/z.

    The core sends ``cart`` (cartesian X,Y,Z,A,B,C) and ``target`` (a joint or
    cartesian pose) as flat float lists, not dicts. Accept both shapes: a dict
    with ``x``/``y``/``z`` keys, or a list/tuple whose first three values are the
    position. Returns None for anything else (the caller shows a placeholder).
    """
    if isinstance(pose, dict):
        vals = (pose.get("x"), pose.get("y"), pose.get("z"))
        if all(isinstance(v, (int, float)) for v in vals):
            return (float(vals[0]), float(vals[1]), float(vals[2]))
        return None
    if isinstance(pose, (list, tuple)) and len(pose) >= 3:
        try:
            return (float(pose[0]), float(pose[1]), float(pose[2]))
        except (TypeError, ValueError):
            return None
    return None


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
    cm = state.get("control_mode") or "—"
    forced = state.get("forced_behavior")
    forced_s = forced if forced else "—"
    rows.append({"pairs": [("CONTROL", cm), ("FORCED", forced_s)], "style": "state"})
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


def _brain_content(state, live: Optional[dict] = None):
    """Page 2: robot state map (zone, mode, action, behavior, heading, safety,
    autonomy, target, MIDI monitor)."""
    live = live or {}
    rows = []
    rows.append({"text": "  BRAIN", "style": "label"})
    rows.append({"style": "divider"})
    zone = state.get("zone", "—")
    mode = state.get("mode", "—")
    action = state.get("action", "—")
    rows.append({"pairs": [("ZONE", zone), ("MODE", mode), ("ACTION", action)],
                 "style": "state"})
    behavior = state.get("behavior") or "—"
    bp = state.get("behavior_params")
    params = "—" if not bp else " ".join(f"{k}={v}" for k, v in bp.items())
    forced = state.get("forced_behavior")
    forced_s = f" ({forced})" if forced else ""
    rows.append({"pairs": [("BEHAVIOR", behavior + forced_s), ("PARAMS", params)],
                 "style": "state"})
    cm = state.get("control_mode") or "—"
    rows.append({"pairs": [("CONTROL", cm)], "style": "state"})
    moving = state.get("moving")
    speed = state.get("speed", "—")
    mv = "—" if moving is None else ("yes" if moving else "no")
    rows.append({"pairs": [("MOVING", mv), ("SPEED", speed)], "style": "state"})
    # Heading: A1 (base rotation) from the live joint pose.
    joints = live.get("joints") or state.get("joints")
    if joints and len(joints) >= 6:
        a1 = joints[0]
        rows.append({"pairs": [("HEADING", f"A1={a1:.1f}°")], "style": "state"})
    else:
        rows.append({"pairs": [("HEADING", "—")], "style": "state"})
    # Safety readout (from editor.html port).
    safety = live.get("safety")
    if safety:
        rows.append({"style": "divider"})
        rows.append({"text": "  SAFETY", "style": "label"})
        rows.append({"pairs": [("SAFETY", str(safety))], "style": "state"})
    # Autonomy readout.
    auto = live.get("autonomy_enabled")
    if auto is not None:
        groups = live.get("autonomy_enabled_groups")
        groups_s = " ".join(groups) if isinstance(groups, list) and groups else "—"
        perf = live.get("performance_mode")
        perf_s = "yes" if perf else "no"
        dwell = live.get("autonomy_dwell_remaining")
        dwell_s = f"{dwell:.1f}s" if dwell is not None else "—"
        rows.append({"pairs": [("AUTONOMY", "on" if auto else "off"),
                                ("GROUPS", groups_s), ("PERF", perf_s)],
                     "style": "state"})
        rows.append({"pairs": [("DWELL", dwell_s)], "style": "state"})
    # Target readout.
    target_xyz = _pose_xyz(live.get("target"))
    if target_xyz:
        tx, ty, tz = target_xyz
        rows.append({"pairs": [("TARGET", f"x={tx:.1f} y={ty:.1f} z={tz:.1f}")],
                      "style": "state"})
    # MIDI monitor readout.
    midi = live.get("midi_monitor")
    if midi:
        rows.append({"style": "divider"})
        rows.append({"text": "  MIDI", "style": "label"})
        if isinstance(midi, dict):
            for k, v in list(midi.items())[:6]:
                rows.append({"pairs": [(k.upper(), str(v))], "style": "state"})
        else:
            rows.append({"pairs": [("MIDI", str(midi))], "style": "state"})
    rows.append({"style": "divider"})
    rows.append({"text": "  STATE", "style": "label"})
    rows.append({"text": f"  zone: {zone}", "style": "text"})
    rows.append({"text": f"  mode: {mode}", "style": "text"})
    rows.append({"text": f"  action: {action}", "style": "text"})
    if forced:
        rows.append({"text": f"  forced: {forced}", "style": "text"})
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


def _settings_content(settings, cursor, current_len, width):
    """Page 5: SETTINGS (THEME/LAYOUT font size + BUTTONS mapper rows)."""
    rows = []
    rows.append({"text": "  SETUP", "style": "label"})
    rows.append({"style": "divider"})
    items = build_settings_items(settings)
    current_section = None
    for i, item in enumerate(items):
        sec = item["section"]
        if sec != current_section:
            rows.append({"text": f"  {SECTION_LABELS.get(sec, sec.upper())}", "style": "label"})
            current_section = sec
        rows.append(_item_row(i, item, cursor, current_len, width))
    return rows


def _triggers_content(events: List[Tuple[float, dict]], width: int) -> List[dict]:
    """TRIGGERS page: timestamped log of core trigger/event frames."""
    rows = []
    rows.append({"text": "  TRIGGERS", "style": "label"})
    rows.append({"style": "divider"})
    if not events:
        rows.append({"text": "  (no events yet — waiting for core)", "style": "help"})
        return rows
    # Show the most recent events (newest last), clipped to fit the page.
    for ts, ev in events:
        etype = ev.get("type", "?")
        ts_str = time.strftime("%H:%M:%S", time.localtime(ts))
        # Extract a few key fields for context.
        extra = ""
        if etype == "zone":
            extra = f" zone={ev.get('zone', '?')}"
        elif etype == "mode":
            extra = f" mode={ev.get('mode', '?')}"
        elif etype == "safety_discard":
            extra = f" reason={ev.get('reason', '?')}"
        elif etype == "pose_conflict":
            extra = f" pose={ev.get('pose', '?')}"
        elif etype == "midi_learn":
            extra = f" note={ev.get('note', '?')}"
        elif etype == "cam_control":
            extra = f" cmd={ev.get('cmd', '?')}"
        elif etype == "behavior":
            extra = f" b={ev.get('behavior', '?')}"
        line = f"  {ts_str}  {etype:<15}{extra}"
        style = "text"
        if etype in ("safety_discard", "pose_conflict"):
            style = "feral"
        rows.append({"text": _clip(line, width), "style": style})
    return rows


def _arm_content(live: dict, width: int) -> List[dict]:
    """ARM page: ASCII robot-arm position from live joints/cart/target.

    Renders joint-angle bars against hardware limits, the tool (cart) and
    target positions, and a simple top-view ASCII sketch of the arm.
    """
    rows = []
    rows.append({"text": "  ARM", "style": "label"})
    rows.append({"style": "divider"})
    joints = live.get("joints")
    cart = live.get("cart")
    target = live.get("target")
    # Joint-angle bars (A1..A6) against hardware limits.
    if joints and len(joints) >= 6:
        for i, axis in enumerate(AXES):
            angle = joints[i]
            lo, hi = HW_LIMITS[axis]
            span = hi - lo
            if span > 0:
                frac = max(0.0, min(1.0, (angle - lo) / span))
            else:
                frac = 0.0
            bar_w = 20
            filled = int(frac * bar_w)
            bar = "█" * filled + "░" * (bar_w - filled)
            rows.append({"text": f"  {axis} {angle:7.1f}°  [{bar}] {lo:.0f}..{hi:.0f}",
                         "style": "text"})
    else:
        rows.append({"text": "  (no joint data)", "style": "help"})
    rows.append({"style": "divider"})
    # Tool (cart) and target positions (the core sends them as flat pose lists).
    cart_xyz = _pose_xyz(cart)
    target_xyz = _pose_xyz(target)
    if cart_xyz:
        x, y, z = cart_xyz
        rows.append({"pairs": [("TOOL", f"x={x:.1f} y={y:.1f} z={z:.1f}")],
                      "style": "state"})
    else:
        rows.append({"pairs": [("TOOL", "—")], "style": "state"})
    if target_xyz:
        tx, ty, tz = target_xyz
        rows.append({"pairs": [("TARGET", f"x={tx:.1f} y={ty:.1f} z={tz:.1f}")],
                      "style": "state"})
    else:
        rows.append({"pairs": [("TARGET", "—")], "style": "state"})
    # Distance between tool and target.
    if cart_xyz and target_xyz:
        dx = cart_xyz[0] - target_xyz[0]
        dy = cart_xyz[1] - target_xyz[1]
        dz = cart_xyz[2] - target_xyz[2]
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        rows.append({"pairs": [("DIST", f"{dist:.1f} mm")], "style": "state"})
    # Simple top-view ASCII (x/y plane).
    if cart_xyz and target_xyz:
        rows.append({"style": "divider"})
        rows.append({"text": "  TOP VIEW (x/y)", "style": "label"})
        cx, cy = cart_xyz[0], cart_xyz[1]
        tx, ty = target_xyz[0], target_xyz[1]
        # Find bounds and scale to a small grid.
        xs = [cx, tx, 0]
        ys = [cy, ty, 0]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        span_x = max(xmax - xmin, 1.0)
        span_y = max(ymax - ymin, 1.0)
        gw, gh = 24, 8
        grid = [[" "] * (gw + 1) for _ in range(gh + 1)]
        def _plot(val_x, val_y, ch):
            col = int((val_x - xmin) / span_x * gw)
            row = int((val_y - ymin) / span_y * gh)
            col = max(0, min(gw, col))
            row = max(0, min(gh, row))
            grid[gh - row][col] = ch
        _plot(0, 0, "o")   # base
        _plot(cx, cy, "T") # tool
        _plot(tx, ty, "*") # target
        for row in grid:
            rows.append({"text": "  " + " ".join(row), "style": "text"})
        rows.append({"text": "  o=base  T=tool  *=target", "style": "help"})
    return rows


def _led_content(led_colors: List[int], cursor: int, current_len: int, width: int,
                 led_available: bool = False) -> List[dict]:
    """LED page: 22 LED rows + 4 action rows (cursor-navigable)."""
    rows = []
    rows.append({"text": "  LED", "style": "label"})
    rows.append({"style": "divider"})
    if not led_available:
        rows.append({"text": "  (sysfs unavailable — preview only)", "style": "conn_bad"})
    items = build_led_items(led_colors)
    for i in range(NUM_LEDS):
        rows.append(_item_row(i, items[i], cursor, current_len, width))
    rows.append({"style": "divider"})
    for j, action in enumerate(LED_ACTIONS):
        item = {"kind": "led_action", "action": action, "name": action}
        row_idx = NUM_LEDS + j
        if row_idx == cursor and row_idx < current_len:
            rows.append({"text": _clip(f"  ▸ {action}", width), "style": "item_sel"})
        else:
            rows.append({"text": _clip(f"    {action}", width), "style": "item"})
    return rows


def build_rows(state, zones, page, cursor, engine, hunt, cam_telem, width,
               *, connected=False, endpoint="", status="",
               settings: Optional[dict] = None,
               live: Optional[dict] = None,
               events: Optional[List[Tuple[float, dict]]] = None,
               led_colors: Optional[List[int]] = None,
               led_available: bool = False) -> List[dict]:
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
        settings: the live SETTINGS values (font_size + joy_map) for the SETUP page.
    """
    items = build_items(zones, state.get("zone"))
    current = current_items(page, items, settings, led_colors)
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
    name = PAGES[page] if 0 <= page < len(PAGES) else ""
    live = live or {}
    if name == "NAV":
        rows.extend(_nav_content(state, zones, items, cursor, current_len, width))
    elif name == "CAMERA":
        rows.extend(_camera_content(cam_telem, cursor, current_len, width))
    elif name == "BRAIN":
        rows.extend(_brain_content(state, live))
    elif name == "HUNT":
        rows.extend(_hunt_content(hunt, cam_telem, cursor, current_len, width))
    elif name == "CADENCE":
        rows.extend(_cadence_content(engine, cursor, current_len, width))
    elif name == "TRIGGERS":
        rows.extend(_triggers_content(events or [], width))
    elif name == "ARM":
        rows.extend(_arm_content(live, width))
    elif name == "LED":
        rows.extend(_led_content(led_colors or [], cursor, current_len, width,
                                  led_available))
    elif name == "SETUP":
        rows.extend(_settings_content(settings, cursor, current_len, width))
    rows.append({"style": "divider"})
    rows.append({"text": _clip(status, width), "style": "status"})
    if name == "SETUP":
        help_txt = "←→ font · ↑↓ move · enter select · L1/R1 pages"
    elif name == "LED":
        help_txt = "←→ pages · ↑↓ move · enter select · q quit"
    else:
        help_txt = "←→ pages · ↑↓ move · enter select · home/q quits"
    rows.append({"text": _clip(help_txt, width), "style": "help"})
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
        # Live SETTINGS values (font_size + joy_map) for the SETUP page; the SDL
        # app populates this from its config. Empty by default (desktop TUI).
        self.settings: Dict[str, Any] = {}
        # High-frequency live state (joints, cart, target, safety, autonomy, MIDI).
        self.live: Dict[str, Any] = {}
        self.live_key: Any = object()
        # TRIGGERS event ring buffer: list of (timestamp, event_dict).
        self.events: List[Tuple[float, dict]] = []
        # LED state: per-LED palette indices + sysfs availability flag.
        self.led_colors: List[int] = [BLACK_IDX] * NUM_LEDS
        self.led_available: bool = False

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
               data.get("cadence_every"),
               data.get("behavior"), data.get("behavior_params"),
               data.get("forced_behavior"), data.get("control_mode"))
        if key != self.state_key:
            self.state = {
                "zone": data.get("zone"),
                "mode": data.get("mode"),
                "action": data.get("action"),
                "moving": data.get("moving"),
                "speed": data.get("speed"),
                "behavior": data.get("behavior"),
                "behavior_params": data.get("behavior_params"),
                "forced_behavior": data.get("forced_behavior"),
                "control_mode": data.get("control_mode"),
            }
            self.state_key = key
            self.rebuild_items()
            self.dirty = True
        # High-frequency live state: joints, cart, target, safety, autonomy, MIDI.
        live_key = (
            data.get("joints"),
            data.get("cart"),
            data.get("target"),
            data.get("safety"),
            data.get("autonomy_enabled"),
            data.get("autonomy_enabled_groups"),
            data.get("performance_mode"),
            data.get("autonomy_dwell_remaining"),
            data.get("midi_monitor"),
        )
        if live_key != self.live_key:
            self.live = {
                "joints": data.get("joints"),
                "cart": data.get("cart"),
                "target": data.get("target"),
                "safety": data.get("safety"),
                "autonomy_enabled": data.get("autonomy_enabled"),
                "autonomy_enabled_groups": data.get("autonomy_enabled_groups"),
                "performance_mode": data.get("performance_mode"),
                "autonomy_dwell_remaining": data.get("autonomy_dwell_remaining"),
                "midi_monitor": data.get("midi_monitor"),
            }
            self.live_key = live_key
            self.dirty = True

    def apply_zones(self, table) -> None:
        if table != self.zones:
            self.zones = table
            self.rebuild_items()
            self.dirty = True

    def apply_event(self, data: dict) -> None:
        """Record a trigger/event frame in the capped ring buffer."""
        ts = time.time()
        self.events.append((ts, data))
        if len(self.events) > EVENT_CAP:
            self.events = self.events[-EVENT_CAP:]
        self.dirty = True

    def set_led_color(self, i: int, idx: int) -> None:
        """Set a single LED's palette index (wraps around)."""
        self.led_colors[i] = idx % NUM_COLORS
        self.dirty = True

    def led_all(self, idx: int) -> None:
        """Set all LEDs to a single palette index."""
        self.led_colors = [idx] * NUM_LEDS
        self.dirty = True

    def rebuild_items(self) -> None:
        zone = self.state.get("zone")
        self.items = build_items(self.zones, zone)
        if self.cursor >= len(self.items):
            self.cursor = 0

    def current_items(self) -> List[dict]:
        return current_items(self.page, self.items, self.settings, self.led_colors)

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

    def activate(self) -> Optional[Tuple[Optional[List[dict]], str]]:
        """Activate the selected item; returns ``(commands, status)`` or None.

        A ``kind="ui"`` SETTINGS row returns ``(None, status)``: the renderer
        acts on it locally (font size / capture) and sends no core command.
        """
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
                           status=self.status, settings=self.settings,
                           live=self.live, events=self.events,
                           led_colors=self.led_colors,
                           led_available=self.led_available)
