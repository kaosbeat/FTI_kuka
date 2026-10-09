"""SDL2 remote app for the Trimui NextUI handheld (PAK).

A terminal-style app rendered with pygame's SDL2 text renderer. It reuses the
shared pure view layer (:mod:`rtr.remote.view`) for all row content and the
state machine, the asyncio connection on a worker thread, and the raw
``/dev/input`` joystick reader. Input (keyboard + joystick) is unified to a
small set of app keys; quitting is an **L1+R1 shoulder chord** (or ``q``/``escape``).

This module is the device renderer; the desktop urwid TUI is
:mod:`rtr.remote.tui`. Both consume the same view layer.

Components:

- :class:`ChordDetector` -- pure, testable L1+R1 quit-chord logic.
- :class:`Renderer` -- draws view rows to a pygame surface (styles -> colors).
- :class:`InputController` -- maps joystick events to app keys + the quit chord.
- :class:`SdlRemote` -- the app: connection thread, main loop, input dispatch.
- :func:`selftest` -- headless self-test (no core, mock state, dummy driver).
"""

import argparse
import asyncio
import logging
import os
import queue
import signal
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from . import view
except ImportError:  # pragma: no cover - running the file directly, not as a pkg
    _pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from rtr.remote import view

try:
    import pygame
except ImportError:  # pragma: no cover - device always bundles pygame
    pygame = None

logger = logging.getLogger(__name__)

# SDL2 keyboard -> app keys (built only when pygame is importable).
KEY_MAP = {}
if pygame is not None:
    KEY_MAP = {
        pygame.K_UP: "up",
        pygame.K_DOWN: "down",
        pygame.K_LEFT: "left",
        pygame.K_RIGHT: "right",
        pygame.K_RETURN: "enter",
        pygame.K_q: "q",
        pygame.K_ESCAPE: "escape",
        pygame.K_h: "h",
    }


# ---------------------------------------------------------------------------
# L1+R1 quit chord (pure, testable).
# ---------------------------------------------------------------------------
class ChordDetector:
    """Detects the L1+R1 shoulder-quit chord.

    Tracks which shoulder buttons are currently pressed and when each was
    first pressed. On a press of L1 or R1, if the *other* shoulder is already
    pressed and its press is within ``window_ms`` -> the chord fires (quit).

    Holding L1 to page, then pressing R1 later than the window, pages (does
    not quit). Releases clear the tracking state.
    """

    def __init__(self, window_ms: int = 200):
        self.window_ms = window_ms
        self.pressed: Dict[int, float] = {}  # code -> press time (ms)

    def press(self, code: int, now_ms: float) -> bool:
        """Record a shoulder press; return True if this press completes the chord."""
        other = view.JOY_R1 if code == view.JOY_L1 else (
            view.JOY_L1 if code == view.JOY_R1 else None)
        if other is None:
            return False
        if other in self.pressed:
            if (now_ms - self.pressed[other]) <= self.window_ms:
                return True  # chord: the other shoulder is within the window
        self.pressed[code] = now_ms
        return False

    def release(self, code: int) -> None:
        self.pressed.pop(code, None)


# ---------------------------------------------------------------------------
# Color table (styles -> RGB). Mirrors the urwid Theme; overridable via config.
# ---------------------------------------------------------------------------
DEFAULT_COLORS: Dict[str, str] = {
    "bg": "#000000",
    "bar": "#ffff00",
    "bar_bg": "#000080",
    "conn_ok": "#00ff00",
    "conn_bad": "#ff0000",
    "page": "#808000",
    "divider": "#808000",
    "status": "#808000",
    "label": "#ffff00",
    "state_val": "#ffffff",
    "item": "#c0c0c0",
    "item_sel": "#000000",
    "item_sel_bg": "#ffff00",
    "item_dim": "#606060",
    "help": "#606060",
    "feral": "#ff0000",
    "text": "#c0c0c0",
}


def hex_to_rgb(h: str) -> Tuple[int, int, int]:
    """``"#rrggbb"`` -> ``(r, g, b)`` (also accepts a bare 6-digit string)."""
    h = h.lstrip("#")
    if len(h) != 6:
        return (255, 255, 255)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# ---------------------------------------------------------------------------
# Renderer (pygame) -- draws view rows to the screen.
# ---------------------------------------------------------------------------
class Renderer:
    """Draws a list of view rows (``style`` + ``text``/``pairs``) to a surface."""

    def __init__(self, screen, font, colors: Dict[str, str]):
        self.screen = screen
        self.font = font
        self.colors = colors
        self.w = screen.get_width()
        self.h = screen.get_height()
        # Character cell metrics: monospace font -> uniform cell width/height.
        self.char_w = self._tw("M")
        self.char_h = max(1, font.get_height())
        self.cols = max(1, self.w // self.char_w)
        self.rows = max(1, self.h // self.char_h)

    def _tw(self, text: str) -> int:
        if hasattr(self.font, "get_width"):
            return self.font.get_width(text)
        return self.font.size(text)[0]

    def _bg(self):
        return hex_to_rgb(self.colors.get("bg", "#000000"))

    def _style_colors(self, style: str):
        """Return ``(fg, bg)`` RGB for a style key (default bg is black)."""
        bg = self._bg()
        if style == "bar":
            return (hex_to_rgb(self.colors.get("bar", "#ffff00")),
                    hex_to_rgb(self.colors.get("bar_bg", "#000080")))
        if style == "item_sel":
            return (hex_to_rgb(self.colors.get("item_sel", "#000000")),
                    hex_to_rgb(self.colors.get("item_sel_bg", "#ffff00")))
        return hex_to_rgb(self.colors.get(style, "#c0c0c0")), bg

    def draw(self, rows: List[dict]) -> None:
        self.screen.fill(self._bg())
        y = 0
        for row in rows:
            if y >= self.h:
                break
            self._draw_row(row, y)
            y += self.char_h
        pygame.display.flip()

    def _draw_row(self, row: dict, y: int) -> None:
        style = row["style"]
        if style == "divider":
            self.screen.fill(self._style_colors("divider")[0],
                              (0, y, self.w, self.char_h))
            return
        if style == "state":
            self._draw_state_row(row, y)
            return
        fg, bg = self._style_colors(style)
        if bg != self._bg():
            self.screen.fill(bg, (0, y, self.w, self.char_h))
        text = row.get("text", "")
        surf = self.font.render(text, True, fg)
        self.screen.blit(surf, (0, y))

    def _draw_state_row(self, row: dict, y: int) -> None:
        """Draw a ``label: value`` state row (yellow labels, bright values)."""
        x = 0
        lab_fg = hex_to_rgb(self.colors.get("label", "#ffff00"))
        val_fg = hex_to_rgb(self.colors.get("state_val", "#ffffff"))
        sep_w = self._tw(" ") * 3
        for i, (lab, val) in enumerate(row["pairs"]):
            if i:
                x += sep_w
            lab_s = f"{lab}: "
            s = self.font.render(lab_s, True, lab_fg)
            self.screen.blit(s, (x, y))
            x += s.get_width()
            val_s = str(val)
            s = self.font.render(val_s, True, val_fg)
            self.screen.blit(s, (x, y))
            x += s.get_width()


# ---------------------------------------------------------------------------
# Input (joystick -> app keys + quit chord).
# ---------------------------------------------------------------------------
class InputController:
    """Maps joystick events to app keys and detects the L1+R1 quit chord.

    The keyboard is mapped directly in :class:`SdlRemote` (SDL2 ``KEYDOWN``);
    this controller handles the raw ``/dev/input`` joystick events.
    """

    def __init__(self, joy_map: Optional[Dict[int, str]] = None,
                 chord_window_ms: int = 200,
                 quit_codes: Optional[List[int]] = None):
        self.joy_map = dict(joy_map or view.DEFAULT_JOY_MAP)
        self.chord = ChordDetector(chord_window_ms)
        # Single-button quit (e.g. 172 = home/mode button on the Trimui).
        self.quit_codes = set(quit_codes or ())

    def on_joystick_event(self, ev, now_ms: float):
        """Process a raw joystick event ``(type, code, value)``.

        Returns ``("quit",)`` if the L1+R1 chord fired, ``("key", app_key)`` for a
        mapped press, else ``None``.
        """
        event_type, code, value = ev
        if event_type != 1:  # KEY events only (EV_KEY)
            return None
        if value == 1:  # press edge
            if code in self.quit_codes:
                return ("quit",)
            if code in (view.JOY_L1, view.JOY_R1):
                if self.chord.press(code, now_ms):
                    return ("quit",)
            app_key = self.joy_map.get(code)
            if app_key is not None:
                return ("key", app_key)
        elif value == 0:  # release edge
            if code in (view.JOY_L1, view.JOY_R1):
                self.chord.release(code)
        return None


# ---------------------------------------------------------------------------
# App: connection thread + pygame main loop + input dispatch.
# ---------------------------------------------------------------------------
class SdlRemote:
    """SDL2 remote app: live state + action/zone navigation.

    Owns the I/O (connection thread, queue, pygame main loop, joystick) and
    delegates all state and row content to a shared :class:`view.RemoteState`.
    Mirrors :class:`rtr.remote.tui.RemoteTUI`'s threading model.
    """

    def __init__(self, config, *, selftest: bool = False):
        self.config = config
        self.selftest = selftest
        self._q: "queue.Queue" = queue.Queue()
        self._rs = view.RemoteState()
        self._conn = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread = None
        self._joy = None
        self._joy_thread = None
        self._input = InputController(
            joy_map=getattr(config, "joy_map", None),
            chord_window_ms=getattr(config, "chord_window_ms", 200),
            quit_codes=getattr(config, "quit_codes", None))
        self._quit = False

    # ------------------------------------------------------------------
    # Connection (asyncio worker thread). Mirrors RemoteTUI.
    # ------------------------------------------------------------------
    def _start_conn(self) -> None:
        try:
            from .connection import Connection
        except ImportError:  # pragma: no cover - running the file directly, not as a pkg
            from rtr.remote.connection import Connection
        # Make the effective config visible in the log: the first thing to check
        # when "the app isn't using my config.json" (it proves which file was read
        # and what values it produced, so a wrong-file edit is obvious).
        print(f"CFG: config={getattr(self.config, 'config', None) or 'defaults'} "
              f"core={self.config.core_host}:{self.config.core_port} "
              f"http={getattr(self.config, 'http_host', None) or self.config.core_host}:"
              f"{self.config.http_port} device={self.config.device} "
              f"width={self.config.width}x{self.config.screen_height}", flush=True)
        self._loop = asyncio.new_event_loop()
        self._conn = Connection(
            ws_host=self.config.core_host,
            ws_port=self.config.core_port,
            http_host=getattr(self.config, "http_host", None),
            http_port=getattr(self.config, "http_port", 8766),
            http_poll=self.config.http_poll,
            identity="sdlremote",
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

    def _install_signal_handlers(self) -> None:
        """Make SIGTERM/SIGINT quit cleanly (so ``kill``/CTRL-C restore the display).

        Without this, a signal raises ``KeyboardInterrupt`` (or kills the process
        mid-frame) and the Mali GPU window is never torn down -> the panel keeps
        showing the last frame after the process dies. Setting ``_quit`` lets the
        main loop exit through the normal shutdown path (``_shutdown`` +
        ``pygame.quit()``). Only usable from the main thread; safe to skip otherwise.
        """
        def _handler(signum, frame):
            self._quit = True
        try:
            signal.signal(signal.SIGTERM, _handler)
            signal.signal(signal.SIGINT, _handler)
        except (ValueError, OSError):
            pass  # not the main thread / unsupported

    # ------------------------------------------------------------------
    # Joystick (raw /dev/input reader thread). Mirrors RemoteTUI.
    # ------------------------------------------------------------------
    def _start_joy(self) -> None:
        try:
            from .events import Joystick
        except ImportError:  # pragma: no cover - running the file directly, not as a pkg
            from rtr.remote.events import Joystick
        dev = getattr(self.config, "device", None)
        if not dev:
            print("JOY: no device configured; joystick disabled", flush=True)
            return
        if dev == "auto":
            devs = getattr(self.config, "devices", None) or []
            dev = next((d for d in devs if os.path.exists(d)), None)
            if dev is None:
                print(f"JOY: auto: no device found in {devs}", flush=True)
                return
        print(f"JOY: using device {dev}", flush=True)
        self._joy = Joystick(dev)

        def _reader() -> None:
            try:
                self._joy.open()
            except OSError as exc:
                print(f"JOY: FAILED to open {dev}: {exc}", flush=True)
                return
            print(f"JOY: opened {dev}", flush=True)
            n = 0
            retries = 0
            while True:
                ev = self._joy.read()
                if ev is None:
                    # EOF: the device can drop briefly (e.g. another process
                    # touched it). Reopen a few times before giving up.
                    retries += 1
                    if retries > 5:
                        print(f"JOY: reader stopped (EOF/error) on {dev} "
                              f"after {retries} retries", flush=True)
                        break
                    print(f"JOY: EOF on {dev}; reopening ({retries}/5)",
                          flush=True)
                    try:
                        self._joy.close()
                    except Exception:  # noqa: BLE001
                        pass
                    time.sleep(0.2)
                    try:
                        self._joy.open()
                    except OSError as exc:
                        print(f"JOY: reopen failed on {dev}: {exc}", flush=True)
                        break
                    continue
                retries = 0
                n += 1
                if n <= 5:
                    print(f"JOY: ev[{n}] type={ev[0]} code={ev[1]} val={ev[2]}",
                          flush=True)
                self._q.put(("joy", ev))

        self._joy_thread = threading.Thread(target=_reader, daemon=True)
        self._joy_thread.start()

    def _stop_joy(self) -> None:
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
    # Main-thread poll (drains the queue, dispatches joystick events).
    # ------------------------------------------------------------------
    def _poll(self) -> None:
        now_ms = time.monotonic() * 1000.0
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
                    res = self._input.on_joystick_event(payload, now_ms)
                    if res is not None:
                        self._dispatch(res)
        except queue.Empty:
            pass

    def _dispatch(self, res) -> None:
        if res[0] == "quit":
            self._quit = True
        elif res[0] == "key":
            self._handle_key(res[1])

    # ------------------------------------------------------------------
    # Input (app keys).
    # ------------------------------------------------------------------
    def _handle_key(self, key: str) -> None:
        if key in ("q", "escape"):
            self._quit = True
            return
        if key == "up":
            self._rs.move_cursor(-1)
        elif key == "down":
            self._rs.move_cursor(1)
        elif key == "left":
            self._rs.set_page(self._rs.page - 1)
        elif key == "right":
            self._rs.set_page(self._rs.page + 1)
        elif key == "enter":
            self._activate()
        elif key in ("h", "?"):
            self._rs.status = "←→ pages · ↑↓ move · enter select · q quit"
            self._rs.dirty = True

    def _activate(self) -> None:
        res = self._rs.activate()
        if res is not None:
            cmds, _ = res
            for cmd in cmds:
                self._send(cmd)

    def _send(self, cmd: Dict[str, Any]) -> None:
        if self._loop is not None and self._conn is not None:
            try:
                self._loop.call_soon_threadsafe(self._conn.send_command, cmd)
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # Renderer helpers.
    # ------------------------------------------------------------------
    def _screen_size(self) -> Tuple[int, int]:
        w = getattr(self.config, "width", 320) or 320
        h = getattr(self.config, "screen_height", 240) or 240
        return int(w), int(h)

    def _resolve_font(self) -> Optional[str]:
        """Resolve the configured font path.

        A relative path is resolved against the config file's directory (the
        PAK dir) so ``fonts/DejaVuSansMono.ttf`` works regardless of CWD; the
        CWD-relative form is a fallback. Returns None if nothing is found.
        """
        path = getattr(self.config, "font", None)
        if not path:
            return None
        if os.path.exists(path):
            return path
        cfg_path = getattr(self.config, "config", None)
        if cfg_path:
            base = os.path.dirname(os.path.abspath(cfg_path))
            cand = os.path.join(base, path)
            if os.path.exists(cand):
                return cand
        return None

    def _load_font(self):
        size = getattr(self.config, "font_size", 16) or 16
        path = self._resolve_font()
        if path:
            return pygame.font.Font(path, size)
        return pygame.font.Font(None, size)

    def _colors(self) -> Dict[str, str]:
        colors = dict(DEFAULT_COLORS)
        theme = getattr(self.config, "theme", None) or {}
        if isinstance(theme, dict):
            for k, v in theme.items():
                if k in colors and isinstance(v, str):
                    colors[k] = v
        return colors

    def _render_once(self, renderer) -> int:
        up = self._conn is not None and self._conn.connected
        ep = f"ws://{self.config.core_host}:{self.config.core_port}"
        rows = self._rs.build_rows(renderer.cols, connected=up, endpoint=ep)
        renderer.draw(rows)
        return len(rows)

    def _video_driver(self) -> str:
        """Return the SDL video driver to force, probing for a real one.

        SDL auto-detect on this device selects the ``offscreen`` driver, which
        renders into an in-memory surface and never touches ``/dev/fb0`` -> the
        panel stays black. So we probe the candidate framebuffer drivers in
        order and return the first whose ``set_mode`` succeeds with a real
        (non-offscreen/dummy) driver. An explicit ``SDL_VIDEODRIVER`` env
        override always wins (manual debugging).
        """
        if self.selftest:
            return "dummy"
        forced = os.environ.get("SDL_VIDEODRIVER", "").strip()
        if forced:
            return forced
        if pygame is None:
            return ""
        for cand in ("fbcm", "kmsdrm", "kms"):
            try:
                os.environ["SDL_VIDEODRIVER"] = cand
                pygame.display.init()
                pygame.display.set_mode((32, 32))
                drv = pygame.display.get_driver()
                pygame.display.quit()
                if drv and drv not in ("offscreen", "dummy"):
                    print(f"RTR: using video driver {drv!r} (tried {cand!r})",
                          flush=True)
                    return cand
            except Exception:
                try:
                    pygame.display.quit()
                except Exception:
                    pass
        os.environ.pop("SDL_VIDEODRIVER", None)  # fall back to auto-detect
        print("RTR: WARN no fb driver bound; falling back to auto-detect",
              flush=True)
        return ""

    # ------------------------------------------------------------------
    # Lifecycle.
    # ------------------------------------------------------------------
    def run(self) -> None:
        if pygame is None:
            raise RuntimeError("pygame not installed; run `pip install pygame`")
        self._install_signal_handlers()
        # The app reads the joystick RAW from /dev/input (the events.Joystick
        # reader) and never uses pygame's joystick API. Let SDL init its joystick
        # subsystem and it scans + grabs the same /dev/input/eventN device, which
        # sends EOF to our raw reader (one stale event, then EOF). Disable it so
        # SDL leaves the input device alone. Keyboard still works (it comes from
        # the video/display subsystem, not the joystick subsystem).
        os.environ["SDL_JOYSTICK"] = "disabled"
        driver = self._video_driver()
        if driver:
            os.environ["SDL_VIDEODRIVER"] = driver
        else:
            os.environ.pop("SDL_VIDEODRIVER", None)  # let SDL auto-detect
        pygame.init()
        pygame.display.init()
        w, h = self._screen_size()
        try:
            screen = pygame.display.set_mode((w, h))
        except Exception as exc:
            import traceback
            print(f"RTR: display init failed (driver={driver or 'auto'} "
                  f"size={w}x{h}): {exc}", flush=True)
            traceback.print_exc()
            raise
        pygame.display.set_caption("RTR REMOTE")
        font = self._load_font()
        renderer = Renderer(screen, font, self._colors())

        if not self.selftest:
            self._start_conn()
            self._start_joy()

        n_rows = self._render_once(renderer)
        if not self.selftest:
            _print_diag(driver, screen, renderer, n_rows)
        try:
            while not self._quit:
                self._poll()
                for event in pygame.event.get():
                    if event.type == pygame.KEYDOWN:
                        key = KEY_MAP.get(event.key)
                        if key is not None:
                            self._handle_key(key)
                self._render_once(renderer)
                pygame.event.pump()
                pygame.time.wait(100)
        finally:
            self._shutdown()
            pygame.quit()

    def diag(self) -> int:
        """Init the real display, print the framebuffer diagnostic, and exit.

        No connection, no joystick, no loop: a fast, isolated probe of the SDL
        video driver and the physical framebuffer state (the black-screen cause).
        """
        if pygame is None:
            raise RuntimeError("pygame not installed; run `pip install pygame`")
        driver = self._video_driver()
        if driver:
            os.environ["SDL_VIDEODRIVER"] = driver
        else:
            os.environ.pop("SDL_VIDEODRIVER", None)
        pygame.init()
        pygame.display.init()
        w, h = self._screen_size()
        screen = pygame.display.set_mode((w, h))
        active = pygame.display.get_driver()
        if active in ("offscreen", "dummy", ""):
            print(f"RTR DIAG-VERDICT: NO FRAMEBUFFER DRIVER (active={active!r}); "
                  f"the bundled SDL2 has no working fb driver -> point the PAK "
                  f"at the system SDL2 (see Dronage Terminal PAK).", flush=True)
        else:
            print(f"RTR DIAG-VERDICT: framebuffer driver bound: {active!r}",
                  flush=True)
        font = self._load_font()
        renderer = Renderer(screen, font, self._colors())
        n_rows = self._render_once(renderer)
        _print_diag(driver, screen, renderer, n_rows)
        pygame.quit()
        return 0

    def joy_diag(self) -> int:
        """Open the joystick device and print every event until Ctrl-C.

        A probe to discover the actual EV_KEY codes the hardware emits, so the
        ``joy_map`` in config.json can be corrected. Runs until you press
        Ctrl-C (unlimited time to press each button); at the end it lists the
        distinct button codes it saw.
        """
        try:
            from .events import Joystick, EVENT
        except ImportError:
            from rtr.remote.events import Joystick, EVENT
        import select
        dev = getattr(self.config, "device", None)
        if not dev:
            print("JOY-DIAG: no device configured")
            return 1
        if dev == "auto":
            devs = getattr(self.config, "devices", None) or []
            dev = next((d for d in devs if os.path.exists(d)), None)
            if dev is None:
                print(f"JOY-DIAG: no device found in {devs}")
                return 1
        print(f"JOY-DIAG: reading {dev}. Move the D-pad, press EVERY button "
              f"and both shoulders (L1/R1). Press Ctrl-C when done.", flush=True)
        joy = Joystick(dev)
        try:
            joy.open()
        except OSError as exc:
            print(f"JOY-DIAG: failed to open {dev}: {exc}")
            return 1
        seen_keys = {}
        count = 0
        try:
            while True:
                ready, _, _ = select.select([joy._fd], [], [], 2.0)
                if not ready:
                    continue
                data = os.read(joy._fd, 4096)
                if len(data) < EVENT.size:
                    continue
                n = len(data) // EVENT.size
                for i in range(n):
                    _sec, _usec, t, code, val = EVENT.unpack_from(data, i * EVENT.size)
                    if t in (1, 3):
                        count += 1
                        name = "KEY" if t == 1 else "ABS"
                        print(f"  [{count:3d}] {name} code={code} val={val}",
                              flush=True)
                        if t == 1 and val == 1:
                            seen_keys[code] = seen_keys.get(code, 0) + 1
        except KeyboardInterrupt:
            print("\nJOY-DIAG: stopped by Ctrl-C", flush=True)
        finally:
            joy.close()
        print(f"JOY-DIAG: saw {count} events total.", flush=True)
        if seen_keys:
            print("JOY-DIAG: distinct KEY presses (code -> count): "
                  f"{dict(sorted(seen_keys.items()))}", flush=True)
            print("JOY-DIAG: map these codes into config.json joy_map.", flush=True)
        else:
            print("JOY-DIAG: no KEY presses captured (only ABS or nothing).",
                  flush=True)
        return 0


# ---------------------------------------------------------------------------
# One-shot on-device diagnostic (driver, screen, framebuffer state, rows).
# ---------------------------------------------------------------------------
def _loaded_sdl2() -> str:
    """Return the path of the libSDL2 actually mapped into this process.

    Reads /proc/self/maps: the ground truth for which SDL2 the pygame module
    bound to (the bundled RPATH desktop build vs the system fb build).
    """
    try:
        with open("/proc/self/maps") as f:
            for line in f:
                if "libSDL2-2" in line:
                    parts = line.split()
                    if len(parts) >= 6:
                        return parts[5]
    except Exception:
        pass
    return "?"


def _print_diag(driver: str, screen, renderer, n_rows: int) -> None:
    import struct
    def _sys(path: str) -> str:
        try:
            with open(path) as f:
                return f.read().strip()
        except Exception:
            return "?"
    fb_screen = _sys("/sys/class/graphics/fb0/screen_size")
    fb_virtual = _sys("/sys/class/graphics/fb0/virtual_size")
    # yoffset = the scrolling frame-buffer's current display offset. If non-zero,
    # the visible window is elsewhere in the virtual buffer -> black screen.
    yoff = "?"
    try:
        import fcntl
        with open("/dev/fb0", "rb") as fbdev:
            buf = bytearray(100)  # sizeof(struct fb_var_screeninfo)
            fcntl.ioctl(fbdev, 0x2CBC, buf)  # FBIOGET_VSCREENINFO
            xres, yres, xv, yv, _xo, yoffset = struct.unpack_from("<6I", buf, 0)
            yoff = f"{yoffset} (xres={xres} yres={yres} virt={xv}x{yv})"
    except Exception as exc:
        yoff = f"err:{exc}"
    try:
        drv = pygame.display.get_driver()
    except Exception:
        drv = "?"
    print("RTR DIAG "
          f"driver={driver or 'auto'} sdl_driver={drv} "
          f"sdl2lib={_loaded_sdl2()} "
          f"screen={screen.get_size()} flags={screen.get_flags()} "
          f"fb_screen={fb_screen} fb_virtual={fb_virtual} "
          f"yoffset={yoff} rows={n_rows} cols={renderer.cols} "
          f"char_h={renderer.char_h}", flush=True)


# ---------------------------------------------------------------------------
# WebSocket connection test (isolated, no UI): the live-link probe.
# ---------------------------------------------------------------------------
def ws_test(config) -> int:
    """Open a real WebSocket to the core, send hello, read one frame, and exit.

    Independent of the full UI (no display, no joystick): a fast, isolated probe
    of the device -> core link. Each step is timed separately so the exact
    hanging step is exposed:

    1. raw TCP connect (proves L4 reachability, independent of websockets)
    2. WS handshake (the HTTP Upgrade -> 101; a stall here = upgrade not completing)
    3. hello send
    4. first state frame (the core broadcasts every tick, so this is near-instant)

    Prints ``WS-TEST PASS`` (exit 0) or a ``WS-TEST FAIL`` line naming the failing
    step (exit 1). The step name is the actual reason the app's live link is down.
    """
    import asyncio
    import json
    import socket
    import time
    try:
        import websockets
    except ImportError:
        print("WS-TEST FAIL: websockets not installed in this env", flush=True)
        return 1
    host = config.core_host
    port = config.core_port
    url = f"ws://{host}:{port}"
    print(f"WS-TEST: target {url}", flush=True)

    # Step 1: raw TCP connect (independent of the websockets library).
    t0 = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=4)
        sock.close()
        print(f"WS-TEST [1] TCP connect OK in {time.monotonic()-t0:.3f}s", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"WS-TEST FAIL [1] TCP: {exc.__class__.__name__}: {exc}", flush=True)
        return 1

    async def _do() -> None:
        # Step 2: the WebSocket handshake (open_timeout caps the upgrade).
        t1 = time.monotonic()
        ws = await asyncio.wait_for(
            websockets.connect(url, ping_interval=None, ping_timeout=None),
            timeout=5)
        print(f"WS-TEST [2] WS handshake OK in {time.monotonic()-t1:.3f}s",
              flush=True)
        try:
            # Step 3: hello.
            t2 = time.monotonic()
            await ws.send(json.dumps({"cmd": "hello", "client": "wstest"}))
            print(f"WS-TEST [3] hello sent in {time.monotonic()-t2:.3f}s", flush=True)
            # Step 4: first state frame (core ticks at 20 Hz -> near-instant).
            t3 = time.monotonic()
            frame = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(frame)
            print(f"WS-TEST [4] first frame type={data.get('type')} in "
                  f"{time.monotonic()-t3:.3f}s", flush=True)
            print("WS-TEST PASS: full link up", flush=True)
        finally:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    try:
        asyncio.run(asyncio.wait_for(_do(), timeout=12))
        return 0
    except Exception as exc:  # noqa: BLE001 - any failure is the diagnostic
        print(f"WS-TEST FAIL: {exc.__class__.__name__}: {exc}", flush=True)
        return 1


# ---------------------------------------------------------------------------
# Self-test (headless, no core): the easily testable result.
# ---------------------------------------------------------------------------
def selftest() -> bool:
    """Run the headless self-test (no core, mock state, dummy driver).

    Renders mock state across all pages, exercises the app keys, and verifies
    the L1+R1 quit chord. Prints ``SELFTEST PASS`` (True) or a FAIL line (False).
    """
    if pygame is None:
        print("SELFTEST FAIL: pygame not installed")
        return False
    try:
        from .config import Config
    except ImportError:
        from rtr.remote.config import Config
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    try:
        pygame.init()
        pygame.display.init()
        screen = pygame.display.set_mode((320, 240))
        font = pygame.font.Font(None, 16)
        renderer = Renderer(screen, font, dict(DEFAULT_COLORS))

        # Mock zone graph + state (a small two-zone loop).
        rs = view.RemoteState()
        zones = {
            "rest": {"actions": [{"name": "breathe",
                                    "next": {"zone": "patrol"}}]},
            "patrol": {"actions": [{"name": "scan",
                                      "next": {"zone": "rest"}}]},
        }
        rs.apply_zones(zones)
        rs.apply_state({"zone": "rest", "mode": "wander", "action": "breathe",
                        "moving": False, "speed": 0})
        rs.cursor = 0

        # Render every page (the renderer must build a non-empty row set).
        for page in range(len(view.PAGES)):
            rs.page = page
            rows = rs.build_rows(renderer.cols, connected=True,
                                  endpoint="ws://mock")
            renderer.draw(rows)
            if not rows:
                print("SELFTEST FAIL: build_rows returned no rows")
                pygame.quit()
                return False

        # Exercise the app keys (up/down/left/right/enter) against a live app.
        app = SdlRemote(Config(), selftest=True)
        app._rs = rs
        for key in ("up", "down", "left", "right", "enter"):
            app._handle_key(key)

        # L1+R1 chord (within the window) must report quit.
        ctrl = InputController()
        t0 = time.monotonic() * 1000.0
        first = ctrl.on_joystick_event((1, view.JOY_L1, 1), t0)
        second = ctrl.on_joystick_event((1, view.JOY_R1, 1), t0 + 50)
        if second != ("quit",):
            print(f"SELFTEST FAIL: L1+R1 chord not detected: {second}")
            pygame.quit()
            return False
        # A second press far outside the window must NOT quit (page nav only).
        ctrl2 = InputController()
        slow = ctrl2.on_joystick_event((1, view.JOY_L1, 1), t0)
        slow2 = ctrl2.on_joystick_event((1, view.JOY_R1, 1), t0 + 500)
        if slow2 == ("quit",):
            print(f"SELFTEST FAIL: slow L1+R1 wrongly quit: {slow2}")
            pygame.quit()
            return False

        pygame.quit()
        print("SELFTEST PASS")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"SELFTEST FAIL: {exc}")
        return False


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    # Surface WS/connection failures to stderr (the app's own logger is otherwise
    # unconfigured, so the real reason a link stays down was invisible).
    logging.basicConfig(level=logging.INFO, format="RTR %(name)s: %(message)s")
    try:
        from .config import _add_flags, from_args
    except ImportError:
        from rtr.remote.config import _add_flags, from_args

    parser = argparse.ArgumentParser(description="RTR remote SDL2 app")
    _add_flags(parser)
    parser.add_argument("--selftest", action="store_true",
                        help="run the headless self-test (no core, dummy driver)")
    parser.add_argument("--ws-test", action="store_true",
                        help="test the WebSocket link to the core and exit (no UI)")
    parser.add_argument("--diag", action="store_true",
                        help="print the display/framebuffer diagnostic and exit")
    parser.add_argument("--joy-diag", action="store_true",
                        help="read joystick events and print their codes, then exit")
    parser.add_argument("--font", dest="font",
                        help="font path (default: pygame default font)")
    parser.add_argument("--font-size", dest="font_size", type=int,
                        help="font size in px (default: 16)")
    parser.add_argument("--width", dest="width", type=int,
                        help="screen width in px (default: 320)")
    parser.add_argument("--screen-height", dest="screen_height", type=int,
                        help="screen height in px (default: 240)")
    args = parser.parse_args(argv)

    if args.selftest:
        return 0 if selftest() else 1

    if args.ws_test:
        config = from_args(args)
        return ws_test(config)

    if args.diag:
        config = from_args(args)
        if getattr(args, "width", None) is not None:
            config.width = args.width
        if getattr(args, "screen_height", None) is not None:
            config.screen_height = args.screen_height
        if getattr(args, "font", None) is not None:
            config.font = args.font
        if getattr(args, "font_size", None) is not None:
            config.font_size = args.font_size
        return SdlRemote(config).diag()

    if args.joy_diag:
        config = from_args(args)
        return SdlRemote(config).joy_diag()

    config = from_args(args)
    # SDL screen/font fields (the desktop `height` is urwid lines; the SDL app
    # uses `width`/`screen_height` in px). Defaults come from Config.
    if getattr(args, "width", None) is not None:
        config.width = args.width
    if getattr(args, "screen_height", None) is not None:
        config.screen_height = args.screen_height
    if getattr(args, "font", None) is not None:
        config.font = args.font
    if getattr(args, "font_size", None) is not None:
        config.font_size = args.font_size
    SdlRemote(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
