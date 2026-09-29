"""Remote TUI (urwid) — live zone/action navigator.

A terminal UI for the remote handheld client. It opens a live link to the core
(see :mod:`rtr.remote.connection`) and lets the operator, from the current
state:

- **select an action** of the current zone (activate it), and
- **navigate to the next zone** (a declared exit of the current zone, or a
  reverse exit — a zone whose exits include the current one, where the robot
  could have come from).

Navigation is manual: ``up``/``down`` move the cursor over the current zone's
actions and exits, ``enter`` activates the selected one (sends the matching
command to the core), ``q``/``escape`` quit.

The theme is a yellow "high color" scheme: bright yellow accents on a dark
background, with a strong black-on-yellow highlight for the selected row.

Threading: the asyncio connection runs on a worker thread; the urwid MainLoop
runs on the main thread. The two exchange data through a thread-safe queue
that the MainLoop polls at a fixed interval, so the UI is always updated from
the main thread (no cross-thread widget access).
"""

import asyncio
import logging
import queue
import threading
from typing import Any, Dict, List, Optional

try:
    import urwid
except ImportError:
    urwid = None

from ..flow import (
    activation_commands,
    action_enabled,
    action_loops,
    action_target,
    build_items,
)

logger = logging.getLogger(__name__)


class _Quit(Exception):
    """Raised from the input filter to cleanly tear down the MainLoop."""


# ---------------------------------------------------------------------------
# High-color yellow theme.
#
# urwid colors are named palette entries (no hex). This is a yellow "high
# color" scheme: bright yellow accents on a dark background, bold labels, and
# a strong black-on-yellow highlight for the selected row.
# ---------------------------------------------------------------------------
class Theme:
    """A yellow "high color" palette (urwid 256-color ``AttrSpec`` names)."""
    BG = "black"
    ACCENT_BG = "dark blue"
    YELLOW = "yellow"
    YELLOW_DIM = "brown"
    TEXT = "light gray"
    BRIGHT = "white"
    RED = "light red"
    GREEN = "light green"
    DIM = "dark gray"

    @staticmethod
    def attr(fg, bg=None, *attrs):
        fg = fg + ("," + ",".join(attrs) if attrs else "")
        return urwid.AttrSpec(fg, bg or Theme.BG)


# ---------------------------------------------------------------------------
# Display shaping (no urwid) — the TUI renders these and the tests exercise
# them directly. Each zone's actions carry the optional ``kind`` / ``loop`` /
# ``target`` fields (see state/zones.py): the TUI groups the actions by kind,
# marks the looping ones, and annotates a target hand-off. The shared pure
# navigation helpers live in :mod:`rtr.flow`.
# ---------------------------------------------------------------------------
SECTION_LABELS = {
    "entry": "ENTRY",
    "internal": "INTERNAL",
    "exit": "EXIT",
    "zones": "GO TO",
}


def item_label(item: dict) -> str:
    """The display string for an item (name + loop marker + target annotation).

    A "zones" row with ``back=True`` (a reverse-only exit) gets a ``←`` marker so
    forward vs. back is visible in an asymmetric exits graph.
    """
    name = item.get("name", "")
    if item.get("kind") != "action":
        return f"{name} ←" if item.get("back") else name
    a = item.get("action")
    s = name
    if action_loops(a):
        s += " ↻"
    t = action_target(a)
    if t:
        tz = t.get("zone")
        if isinstance(tz, str):
            s += f" → {tz}"
        else:
            ta = t.get("action")
            if isinstance(ta, str):
                s += f" → {ta}"
    return s


class RemoteTUI:
    """Remote TUI (urwid): live state + action/zone navigation."""

    def __init__(self, config):
        self.config = config
        self._q: "queue.Queue" = queue.Queue()
        self._items: List[dict] = []  # selectable rows ({section, kind, name, action})
        self._cursor = 0
        self._state: Dict[str, Any] = {}
        self._state_key = object()
        self._zones: Dict[str, dict] = {}
        self._status = "initialising…"
        self._dirty = True
        self._conn = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread = None
        self._top = None
        self._mainloop = None

    # ------------------------------------------------------------------
    # Connection (asyncio worker thread).
    # ------------------------------------------------------------------
    def _start_conn(self) -> None:
        from .connection import Connection
        self._loop = asyncio.new_event_loop()
        self._conn = Connection(
            ws_host=self.config.core_host,
            ws_port=self.config.core_port,
            http_host=getattr(self.config, "http_host", None),
            http_port=getattr(self.config, "http_port", 8766),
            http_poll=self.config.http_poll,
            on_state=self._on_state,
            on_zones=self._on_zones,
            on_error=self._on_error,
        )
        self._loop.create_task(self._conn.start())
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _shutdown(self) -> None:
        if self._loop is None:
            return

        def _do():
            async def _stop_all():
                try:
                    await self._conn.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._loop.stop()
            self._loop.create_task(_stop_all())

        try:
            self._loop.call_soon_threadsafe(_do)
        except RuntimeError:
            pass

    def _on_state(self, data) -> None:
        self._q.put(("state", data))

    def _on_zones(self, table) -> None:
        self._q.put(("zones", table))

    def _on_error(self, msg) -> None:
        self._q.put(("error", msg))

    # ------------------------------------------------------------------
    # Main-thread poll (drains the queue, rebuilds on change).
    # ------------------------------------------------------------------
    def _poll(self, _loop=None, _user_data=None) -> None:
        # urwid alarm callbacks are invoked as ``callback(mainloop, user_data)``.
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "state":
                    self._apply_state(payload)
                elif kind == "zones":
                    self._apply_zones(payload)
                elif kind == "error":
                    self._status = str(payload)
                    self._dirty = True
        except queue.Empty:
            pass
        if self._dirty:
            self._dirty = False
            self._rebuild()
        if self._mainloop is not None:
            self._mainloop.set_alarm_in(0.1, self._poll)

    def _apply_state(self, data) -> None:
        key = (data.get("zone"), data.get("mode"), data.get("action"),
               data.get("moving"), data.get("speed"))
        if key != self._state_key:
            self._state = {
                "zone": data.get("zone"),
                "mode": data.get("mode"),
                "action": data.get("action"),
                "moving": data.get("moving"),
                "speed": data.get("speed"),
            }
            self._state_key = key
            self._rebuild_items()
            self._dirty = True

    def _apply_zones(self, table) -> None:
        if table != self._zones:
            self._zones = table
            self._rebuild_items()
            self._dirty = True

    def _rebuild_items(self) -> None:
        zone = self._state.get("zone")
        self._items = build_items(self._zones, zone)
        if self._cursor >= len(self._items):
            self._cursor = 0

    # ------------------------------------------------------------------
    # Rendering.
    # ------------------------------------------------------------------
    def _width(self) -> int:
        if self._mainloop is not None:
            try:
                return self._mainloop.terminal_size[0]
            except Exception:  # noqa: BLE001
                pass
        return 80

    def _bar(self, text: str, fg, bg, *attrs):
        w = self._width()
        pad = max(0, w - len(text))
        left = pad // 2
        s = " " * left + text + " " * (pad - left)
        return urwid.AttrWrap(urwid.Text(s), Theme.attr(fg, bg, *attrs))

    def _line(self, text: str, fg, bg=None, *attrs):
        s = text[:self._width()]
        return urwid.AttrWrap(urwid.Text(s), Theme.attr(fg, bg or Theme.BG, *attrs))

    def _label(self, text: str):
        return urwid.AttrWrap(urwid.Text(f"  {text}"),
                               Theme.attr(Theme.YELLOW, Theme.BG, "bold"))

    def _divider(self):
        return urwid.AttrWrap(urwid.Divider(div_char="─"),
                               Theme.attr(Theme.YELLOW_DIM, Theme.BG))

    def _state_row(self, pairs):
        """A row of ``label: value`` pairs (yellow bold labels, bright values).

        Uses urwid's *list* markup form — a plain string is not auto-detected
        as ``(tag)text`` markup in urwid 4.x, so we build an explicit list of
        ``(attr, text)`` segments.
        """
        parts: List[Any] = []
        for i, (lab, val) in enumerate(pairs):
            if i:
                parts.append("   ")
            parts.append((Theme.attr(Theme.YELLOW, Theme.BG, "bold"), f"{lab}: "))
            parts.append((Theme.attr(Theme.BRIGHT, Theme.BG), str(val)))
        return urwid.AttrMap(urwid.Text(parts), {})

    def _item_row(self, i: int, item: dict):
        if i == self._cursor and i < len(self._items):
            s = f"  ▸ {item_label(item)}"
            return urwid.AttrWrap(urwid.Text(s[:self._width()]),
                                   Theme.attr("black", Theme.YELLOW, "bold"))
        s = f"    {item_label(item)}"
        if item.get("kind") == "action" and item.get("action") is not None \
           and not action_enabled(item["action"]):
            return urwid.AttrWrap(urwid.Text(s[:self._width()]),
                                   Theme.attr(Theme.DIM, Theme.BG))
        return urwid.AttrWrap(urwid.Text(s[:self._width()]),
                               Theme.attr(Theme.TEXT, Theme.BG))

    def _build_rows(self):
        rows = []
        rows.append(self._bar("RTR REMOTE", Theme.YELLOW, Theme.ACCENT_BG, "bold"))
        up = self._conn is not None and self._conn.connected
        ep = f"ws://{self.config.core_host}:{self.config.core_port}"
        if up:
            rows.append(self._line(f"CONNECTED   {ep}", Theme.GREEN))
        else:
            rows.append(self._line(f"DISCONNECTED  {ep}", Theme.RED))
        rows.append(self._divider())
        zone = self._state.get("zone", "—")
        mode = self._state.get("mode", "—")
        action = self._state.get("action", "—")
        rows.append(self._state_row([("ZONE", zone), ("MODE", mode), ("ACTION", action)]))
        moving = self._state.get("moving")
        speed = self._state.get("speed", "—")
        mv = "—" if moving is None else ("yes" if moving else "no")
        rows.append(self._state_row([("MOVING", mv), ("SPEED", speed)]))
        rows.append(self._divider())
        if not self._items:
            if not self._zones and not self._state:
                rows.append(self._line("  connecting… waiting for core state", Theme.DIM))
            else:
                rows.append(self._line("  (no actions or exits in this zone)", Theme.DIM))
        else:
            current_section = None
            for i, item in enumerate(self._items):
                sec = item["section"]
                if sec != current_section:
                    rows.append(self._label(SECTION_LABELS.get(sec, sec.upper())))
                    current_section = sec
                rows.append(self._item_row(i, item))
        rows.append(self._divider())
        rows.append(self._line(self._status, Theme.YELLOW_DIM))
        rows.append(self._line("↑↓ move · enter select · q quit", Theme.DIM))
        return rows

    def _build_top(self):
        """A fresh top-level ``Pile`` for the current state (box widget).

        Each row is a flow widget (``Text``/``Divider``), so it must be packed
        with the ``("pack", w)`` form; a bare widget would be rendered as a
        weighted box row and the ``Text`` rows would receive a 2-tuple size.
        """
        return urwid.Pile([("pack", w) for w in self._build_rows()])

    def _rebuild(self) -> None:
        """Rebuild the top widget and swap it into the MainLoop (if running).

        A fresh ``Pile`` is rendered from scratch, so no explicit cache
        invalidation is needed (urwid 4.x has no ``invalidate_caching``).
        """
        self._top = self._build_top()
        if self._mainloop is not None:
            self._mainloop.widget = self._top

    # ------------------------------------------------------------------
    # Input.
    # ------------------------------------------------------------------
    def _input_filter(self, keys, raw):
        if any(k in ("q", "escape") for k in keys):
            raise _Quit
        return keys

    def _move_cursor(self, d: int) -> None:
        if not self._items:
            return
        self._cursor = max(0, min(len(self._items) - 1, self._cursor + d))
        self._dirty = True

    def _activate(self) -> None:
        if 0 <= self._cursor < len(self._items):
            item = self._items[self._cursor]
            for cmd in activation_commands(item):
                self._send(cmd)
            if item.get("kind") == "action":
                self._status = f"→ play {item['name']}"
            else:
                self._status = f"→ goto {item['name']}"
            self._dirty = True

    def _on_unhandled(self, key):
        if key == "up":
            self._move_cursor(-1)
            return True
        if key == "down":
            self._move_cursor(1)
            return True
        if key == "home":
            self._cursor = 0
            self._dirty = True
            return True
        if key == "end":
            self._cursor = max(0, len(self._items) - 1)
            self._dirty = True
            return True
        if key == "enter":
            self._activate()
            return True
        if key in ("h", "?"):
            self._status = "↑↓ move · enter select · q quit"
            return True
        return None

    def _send(self, cmd: Dict[str, Any]) -> None:
        if self._loop is not None and self._conn is not None:
            try:
                self._loop.call_soon_threadsafe(self._conn.send_command, cmd)
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # Lifecycle.
    # ------------------------------------------------------------------
    def run(self) -> None:
        if urwid is None:
            raise RuntimeError("urwid not installed; run `pip install urwid`")
        self._top = self._build_top()
        self._start_conn()
        self._mainloop = urwid.MainLoop(self._top,
                                         input_filter=self._input_filter,
                                         unhandled_input=self._on_unhandled)
        self._mainloop.set_alarm_in(0.1, self._poll)
        try:
            self._mainloop.run()
        except _Quit:
            pass
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._dirty = False
        if self._mainloop is not None:
            self._mainloop.stop()


def run(config=None) -> None:
    """Run the remote TUI.

    Args:
        config: Config instance, or None to build one from the command line.
    """
    if config is None:
        import argparse
        from rtr.remote.config import _add_flags, from_args
        parser = argparse.ArgumentParser(description="RTR remote TUI")
        _add_flags(parser)
        ns = parser.parse_args()
        config = from_args(ns)
    RemoteTUI(config).run()


if __name__ == "__main__":
    # Allow running directly with `python tui.py`: bootstrap the rtr package
    # root onto sys.path, then run through the packaged module so relative
    # imports (e.g. `.connection`) resolve correctly.
    import os
    import sys
    _pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from rtr.remote import tui as _pkg_tui
    _pkg_tui.run()
