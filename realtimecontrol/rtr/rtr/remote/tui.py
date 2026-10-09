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

This module is a thin urwid renderer: all row content and the state machine
live in the shared pure view layer (:mod:`rtr.remote.view`); each row dict is
mapped here to a urwid widget through the :class:`Theme` palette.
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

from . import view

logger = logging.getLogger(__name__)


class _Quit(Exception):
    """Raised from the input filter to cleanly tear down the MainLoop."""


# ---------------------------------------------------------------------------
# High-color yellow theme.
#
# urwid colors are named palette entries (no hex). This is a yellow "high
# color" scheme: bright yellow accents on a dark background, bold labels, and
# a strong black-on-yellow highlight for the selected row. The named style keys
# produced by :mod:`rtr.remote.view` map to these AttrSpecs.
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


class RemoteTUI:
    """Remote TUI (urwid): live state + action/zone navigation.

    Owns the I/O (connection thread, queue, urwid MainLoop, joystick) and
    delegates all state and row content to a shared :class:`view.RemoteState`.
    """

    def __init__(self, config):
        self.config = config
        self._q: "queue.Queue" = queue.Queue()
        self._rs = view.RemoteState()
        self._conn = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread = None
        self._top = None
        self._mainloop = None
        self._joy = None
        self._joy_thread = None

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
            identity="tuiremote",
            on_state=self._on_state,
            on_zones=self._on_zones,
            on_error=self._on_error,
        )
        self._loop.create_task(self._conn.start())
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _shutdown(self) -> None:
        self._stop_joy()
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

    # ------------------------------------------------------------------
    # Joystick (page navigation via L1/R1). Read on a daemon thread because the
    # device read blocks; events are pushed into the queue for the main thread.
    # ------------------------------------------------------------------
    def _start_joy(self) -> None:
        from .events import Joystick
        dev = getattr(self.config, "device", None)
        if not dev:
            return
        self._joy = Joystick(dev)

        def _reader() -> None:
            try:
                self._joy.open()
            except OSError as exc:
                logger.warning("joystick %s unavailable: %s", dev, exc)
                return
            while True:
                ev = self._joy.read()
                if ev is None:
                    break  # EOF or device error; stop the reader
                self._q.put(("joy", ev))

        self._joy_thread = threading.Thread(target=_reader, daemon=True)
        self._joy_thread.start()

    def _stop_joy(self) -> None:
        # Closing the fd makes the blocking read raise, ending the reader thread.
        if self._joy is not None:
            try:
                self._joy.close()
            except Exception:  # noqa: BLE001
                pass
            self._joy = None

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
                    self._rs.apply_state(payload)
                elif kind == "zones":
                    self._rs.apply_zones(payload)
                elif kind == "error":
                    self._rs.status = str(payload)
                    self._rs.dirty = True
                elif kind == "joy":
                    self._rs.on_joystick(payload)
        except queue.Empty:
            pass
        if self._rs.dirty:
            self._rs.dirty = False
            self._rebuild()
        if self._mainloop is not None:
            self._mainloop.set_alarm_in(0.1, self._poll)

    # ------------------------------------------------------------------
    # Rendering: map view row dicts → urwid widgets.
    # ------------------------------------------------------------------
    def _width(self) -> int:
        if self._mainloop is not None:
            try:
                return self._mainloop.terminal_size[0]
            except Exception:  # noqa: BLE001
                pass
        return 80

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

    def _row_widget(self, row: dict):
        """Map one view row dict (``style`` + ``text``/``pairs``) to a widget."""
        style = row["style"]
        if style == "divider":
            return urwid.AttrWrap(urwid.Divider(div_char="─"),
                                   Theme.attr(Theme.YELLOW_DIM, Theme.BG))
        if style == "state":
            return self._state_row(row["pairs"])
        text = row.get("text", "")
        if style == "bar":
            return urwid.AttrWrap(urwid.Text(text),
                                   Theme.attr(Theme.YELLOW, Theme.ACCENT_BG, "bold"))
        if style == "conn_ok":
            return urwid.AttrWrap(urwid.Text(text), Theme.attr(Theme.GREEN))
        if style == "conn_bad":
            return urwid.AttrWrap(urwid.Text(text), Theme.attr(Theme.RED))
        if style == "page":
            return urwid.AttrWrap(urwid.Text(text), Theme.attr(Theme.YELLOW_DIM))
        if style == "label":
            return urwid.AttrWrap(urwid.Text(text),
                                   Theme.attr(Theme.YELLOW, Theme.BG, "bold"))
        if style == "item":
            return urwid.AttrWrap(urwid.Text(text), Theme.attr(Theme.TEXT))
        if style == "item_sel":
            return urwid.AttrWrap(urwid.Text(text),
                                   Theme.attr("black", Theme.YELLOW, "bold"))
        if style == "item_dim":
            return urwid.AttrWrap(urwid.Text(text), Theme.attr(Theme.DIM))
        if style == "status":
            return urwid.AttrWrap(urwid.Text(text), Theme.attr(Theme.YELLOW_DIM))
        if style == "help":
            return urwid.AttrWrap(urwid.Text(text), Theme.attr(Theme.DIM))
        if style == "feral":
            return urwid.AttrWrap(urwid.Text(text),
                                   Theme.attr(Theme.RED, Theme.BG, "bold"))
        # "text" and any unknown style: plain text.
        return urwid.AttrWrap(urwid.Text(text), Theme.attr(Theme.TEXT))

    def _build_rows(self):
        width = self._width()
        up = self._conn is not None and self._conn.connected
        ep = f"ws://{self.config.core_host}:{self.config.core_port}"
        rows = self._rs.build_rows(width, connected=up, endpoint=ep)
        return [self._row_widget(r) for r in rows]

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

    def _activate(self) -> None:
        res = self._rs.activate()
        if res is not None:
            cmds, _ = res
            for cmd in cmds:
                self._send(cmd)

    def _on_unhandled(self, key):
        if key == "up":
            self._rs.move_cursor(-1)
            return True
        if key == "down":
            self._rs.move_cursor(1)
            return True
        if key == "home":
            self._rs.cursor = 0
            self._rs.dirty = True
            return True
        if key == "end":
            self._rs.cursor = max(0, len(self._rs.items) - 1)
            self._rs.dirty = True
            return True
        if key == "enter":
            self._activate()
            return True
        if key == "left":
            self._rs.set_page(self._rs.page - 1)
            return True
        if key == "right":
            self._rs.set_page(self._rs.page + 1)
            return True
        if key in ("h", "?"):
            self._rs.status = "←→ pages · ↑↓ move · enter select · q quit"
            self._rs.dirty = True
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
        self._start_joy()
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
        self._rs.dirty = False
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
