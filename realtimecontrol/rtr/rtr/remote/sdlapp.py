"""SDL2 remote app for the Trimui NextUI handheld (PAK).

A terminal-style app rendered with pygame's SDL2 text renderer. It reuses the
shared pure view layer (:mod:`rtr.remote.view`) for all row content and the
state machine, the asyncio connection on a worker thread, and the raw
``/dev/input`` joystick reader. Input (keyboard + joystick) is unified to a
small set of app keys; the d-pad is an analog axis (EV_ABS), the face buttons
are digital (EV_KEY), L1/R1 page globally, and the home button quits.

This module is the device renderer; the desktop urwid TUI is
:mod:`rtr.remote.tui`. Both consume the same view layer.

Components:

- :func:`_abs_sign` -- decodes a raw ABS value to its sign (the d-pad axis).
- :class:`Renderer` -- draws view rows to a pygame surface (styles -> colors).
- :class:`InputController` -- maps joystick events to app keys (d-pad + face).
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
    from . import clockfix  # noqa: F401 - patches time.get_clock_info at import
except ImportError:  # pragma: no cover - running the file directly, not as a pkg
    _pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from rtr.remote import clockfix  # noqa: F401

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
# ABS value decoding (the d-pad is an analog axis).
# ---------------------------------------------------------------------------
def _abs_sign(value: int) -> int:
    """Return the sign of a raw ABS value as -1 / 0 / +1.

    The ``events.Joystick`` reader unpacks the value as an unsigned 32-bit
    integer (struct ``I``), so a negative axis position comes back as a large
    unsigned number (e.g. ``4294967295`` = -1, ``4294901759`` = -40801). The
    sign is recovered by treating the value as a signed 32-bit int: if the high
    bit is set it is negative. Only the sign matters for the d-pad (center = 0).
    """
    signed = value - 0x100000000 if value >= 0x80000000 else value
    if signed < 0:
        return -1
    if signed > 0:
        return 1
    return 0


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
# Input (joystick -> app keys + page nav).
# ---------------------------------------------------------------------------
class InputController:
    """Maps joystick events to app keys (digital keys + the analog d-pad).

    The keyboard is mapped directly in :class:`SdlRemote` (SDL2 ``KEYDOWN``);
    this controller handles the raw ``/dev/input`` joystick events:

    - **EV_KEY** (type 1): the digital face buttons (A/B/X), the single-button
      quit (home), and the L1/R1 shoulders (reserved for GLOBAL page nav).
    - **EV_ABS** (type 3): the d-pad, an analog axis. A press edge (the axis
      leaving center in a mapped direction) emits that direction.

    There is no quit chord: L1/R1 are pure page navigation and the home button
    (``quit_codes``) is the only quit.
    """

    def __init__(self, joy_map: Optional[Dict[int, str]] = None,
                 dpad_map: Optional[Dict[str, List[int]]] = None,
                 quit_codes: Optional[List[int]] = None):
        self.joy_map = dict(joy_map or view.DEFAULT_JOY_MAP)
        self.dpad_map = dict(dpad_map or view.DEFAULT_DPAD_MAP)
        # Single-button quit (e.g. 172 = home/mode button on the Trimui).
        self.quit_codes = set(quit_codes or ())
        # Per-axis d-pad state for press-edge detection: axis_code -> app_key
        # (the currently-active direction) or None (at rest / center).
        self._abs_state: Dict[int, Optional[str]] = {}

    def on_joystick_event(self, ev, now_ms: float):
        """Process a raw joystick event ``(type, code, value)``.

        Returns ``("quit",)`` for a quit button, ``("key", app_key)`` for a
        mapped press (digital or d-pad), else ``None``.
        """
        event_type, code, value = ev
        if event_type == 3:  # EV_ABS: the analog d-pad
            return self._on_abs(code, value)
        if event_type != 1:  # other events ignored
            return None
        if value == 1:  # KEY press edge
            if code in self.quit_codes:
                return ("quit",)
            if code in (view.JOY_L1, view.JOY_R1):
                # L1/R1 are reserved for GLOBAL page navigation: a single
                # shoulder press always pages (never a mappable button).
                # Handled here, before the joy_map lookup, so the SETTINGS
                # page can't swallow it for a local action (font size) and
                # trap the user -- the shoulders are the reliable way out.
                return ("key", "page_left" if code == view.JOY_L1 else "page_right")
            app_key = self.joy_map.get(code)
            if app_key is not None:
                return ("key", app_key)
        return None

    def _on_abs(self, code: int, value: int):
        """Map an EV_ABS d-pad event to a direction on a press edge.

        The d-pad is an analog axis: at rest the value is 0 (center); pressing a
        direction moves the axis to a non-zero value. Only the sign of the value
        matters (see :func:`_abs_sign`). We emit a direction when the axis leaves
        center in a mapped direction (a press edge); holding it does not repeat,
        and returning to center (release) emits nothing.
        """
        sign = _abs_sign(value)
        if sign == 0:  # center: the axis is released
            self._abs_state[code] = None
            return None
        # Find the direction mapped to (code, sign), if any.
        direction = None
        for ak, entry in self.dpad_map.items():
            if isinstance(entry, (list, tuple)) and len(entry) == 2 \
               and entry[0] == code and entry[1] == sign:
                direction = ak
                break
        prev = self._abs_state.get(code)
        self._abs_state[code] = direction
        if direction is not None and prev != direction:
            return ("key", direction)
        return None


# ---------------------------------------------------------------------------
# SDL display/font helpers (module-level). Shared by :class:`SdlRemote` and the
# launcher (:mod:`rtr.remote.mainmenu`); kept at module level so the launcher
# reuses the exact same driver/font/size probing.
# ---------------------------------------------------------------------------
def native_size(selftest: bool) -> Tuple[int, int]:
    """Probe the panel's native size, or ``(0, 0)`` if unavailable.

    Tries SDL's current video mode first (portable across drivers), then the
    kernel framebuffer's ``virtual_size`` (the ground truth on Linux). Either
    can be absent (selftest/offscreen, no fb0), in which case ``(0, 0)`` is
    returned and the caller falls back to the config canvas.
    """
    if selftest or pygame is None:
        return (0, 0)
    try:
        info = pygame.display.Info()
        w = int(getattr(info, "current_w", 0) or 0)
        h = int(getattr(info, "current_h", 0) or 0)
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    try:
        with open("/sys/class/graphics/fb0/virtual_size") as f:
            txt = f.read().strip()
        if "x" in txt:
            a, b = txt.split("x", 1)
            w, h = int(a), int(b)
            if w > 0 and h > 0:
                return w, h
    except Exception:
        pass
    return (0, 0)


def screen_size(config, selftest: bool) -> Tuple[int, int]:
    """Render size: the native panel if larger, else the config canvas.

    The layout and ``font_size`` are designed for the small config canvas
    (``width``/``screen_height``, default 320x240). On a device whose panel is
    larger (e.g. the Trimui's 1024x768), render at the native size so the layout
    fills the panel; the font is then scaled to the render height (see
    :func:`font_size_for`). A config dimension larger than the native one wins
    (an explicit override for a bigger display).
    """
    cw = int(getattr(config, "width", 320) or 320)
    ch = int(getattr(config, "screen_height", 240) or 240)
    nw, nh = native_size(selftest)
    if nw > 0 and nh > 0:
        return max(cw, nw), max(ch, nh)
    return cw, ch


def set_mode(config, selftest: bool):
    """Set the display to the render size, falling back to the design size.

    Tries :func:`screen_size` first (the native panel when it is larger than the
    config canvas). If that ``set_mode`` fails, retries at the config design
    size; if that also fails, the exception propagates. Returns ``(screen, w, h)``
    for the size actually in use.
    """
    w, h = screen_size(config, selftest)
    cfg_w = int(getattr(config, "width", 320) or 320)
    cfg_h = int(getattr(config, "screen_height", 240) or 240)
    try:
        screen = pygame.display.set_mode((w, h))
    except Exception as exc:
        if (w, h) == (cfg_w, cfg_h):
            raise
        print(f"RTR: set_mode {w}x{h} failed ({exc}); retrying "
              f"{cfg_w}x{cfg_h}", flush=True)
        screen = pygame.display.set_mode((cfg_w, cfg_h))
        w, h = cfg_w, cfg_h
    return screen, w, h


def resolve_font(config) -> Optional[str]:
    """Resolve the configured font path.

    Tries, in order: the literal path (absolute, or CWD-relative), then the path
    relative to the config file's directory (the PAK dir, so
    ``fonts/DejaVuSansMono.ttf`` works regardless of CWD), then the path relative
    to this module's directory (the shipped code dir, where ``--ship-code`` puts
    fonts shipped with the app, e.g. ``PressStart2P.ttf``). Returns None if
    nothing is found (the caller falls back to the default).
    """
    path = getattr(config, "font", None)
    if not path:
        return None
    if os.path.exists(path):
        return path
    bases = []
    cfg_path = getattr(config, "config", None)
    if cfg_path:
        bases.append(os.path.dirname(os.path.abspath(cfg_path)))
    # The module dir (rtr/remote/) is where fonts shipped with the code live.
    bases.append(os.path.dirname(os.path.abspath(__file__)))
    for base in bases:
        cand = os.path.join(base, path)
        if os.path.exists(cand):
            return cand
    return None


def font_size_for(config, w: int, h: int) -> int:
    """Scale the base ``font_size`` to the render height.

    The base ``font_size`` (config, default 16) is sized for the design canvas
    height (config ``design_height``, default 240). On a taller render height the
    font is scaled proportionally so the same number of rows fills the panel and
    the text stays legible. The reference is ``design_height`` (NOT the render
    ``screen_height``), so a config that sets ``screen_height`` to the native
    panel height still scales up correctly. A render height at or below the
    design height keeps the base size.
    """
    base = int(getattr(config, "font_size", 16) or 16)
    design_h = int(getattr(config, "design_height", 240) or 240)
    if design_h <= 0 or h <= 0:
        return max(1, base)
    return max(1, int(round(base * (h / design_h))))


def load_font(config, selftest: bool, w: Optional[int] = None,
              h: Optional[int] = None):
    """Build the pygame font at the configured path + scaled size."""
    if w is None or h is None:
        w, h = screen_size(config, selftest)
    size = font_size_for(config, w, h)
    path = resolve_font(config)
    if path:
        return pygame.font.Font(path, size)
    return pygame.font.Font(None, size)


def colors(config) -> Dict[str, str]:
    """Merge the config ``theme`` over :data:`DEFAULT_COLORS` (style -> hex)."""
    cols = dict(DEFAULT_COLORS)
    theme = getattr(config, "theme", None) or {}
    if isinstance(theme, dict):
        for k, v in theme.items():
            if k in cols and isinstance(v, str):
                cols[k] = v
    return cols


def video_driver(selftest: bool) -> str:
    """Return the SDL video driver to force, probing for a real one.

    SDL auto-detect on this device selects the ``offscreen`` driver, which
    renders into an in-memory surface and never touches ``/dev/fb0`` -> the panel
    stays black. So we probe the candidate framebuffer drivers in order and
    return the first whose ``set_mode`` succeeds with a real (non-offscreen/dummy)
    driver. An explicit ``SDL_VIDEODRIVER`` env override always wins (manual
    debugging).
    """
    if selftest:
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
            dpad_map=getattr(config, "dpad_map", None),
            quit_codes=getattr(config, "quit_codes", None))
        self._quit = False
        # Renderer objects (set in run()/diag(); None until then so the
        # SETTINGS font adjuster is a no-op headless/selftest).
        self._screen = None
        self._font = None
        self._renderer = None
        # SETTINGS button-mapper capture state. ``_capture_seq`` is a list of
        # (app_key, kind) pairs; kind is "abs" (d-pad axis) or "key" (face btn).
        self._capture_active = False
        self._capture_seq: List[Tuple[str, str]] = []
        self._capture_idx = 0
        # The (axis, sign) last captured by the d-pad wizard; a repeat of the
        # same axis/sign is ignored until the axis returns to center and a new
        # direction is pressed (prevents a held axis from filling two steps).
        self._capture_last_abs: Optional[Tuple[int, int]] = None
        self._calibrated = False
        self._auto_calib_done = False

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
                    if self._capture_active:
                        self._on_capture(payload, now_ms)
                        continue
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
    # SETTINGS (page 5): THEME font size + BUTTONS mapper.
    # ------------------------------------------------------------------
    def _build_settings(self) -> None:
        """Seed the shared SETTINGS state from the config (font + both maps)."""
        self._rs.settings = {
            "font_size": int(getattr(self.config, "font_size", 16) or 16),
            "joy_map": dict(self._input.joy_map),
            "dpad_map": dict(self._input.dpad_map),
        }

    def _is_setup_page(self) -> bool:
        return (0 <= self._rs.page < len(view.PAGES)
                and view.PAGES[self._rs.page] == "SETUP")

    def _persist_settings(self) -> None:
        """Write the current config (font_size + joy_map) back to disk."""
        cfg_path = getattr(self.config, "config", None)
        if not cfg_path:
            return
        try:
            self.config.to_json(cfg_path)
        except Exception as exc:  # noqa: BLE001
            print(f"RTR: persist failed: {exc}", flush=True)

    def _apply_font_size(self) -> None:
        """Rebuild the font/renderer at the current ``font_size`` (no-op headless)."""
        if pygame is None or self._screen is None:
            return
        w, h = self._screen.get_size()
        self._font = self._load_font(w, h)
        self._renderer = Renderer(self._screen, self._font, self._colors())

    def _adjust_font_size(self, delta: int) -> None:
        fs = int(getattr(self.config, "font_size", 16) or 16)
        fs = max(4, min(32, fs + delta))
        self.config.font_size = fs
        self._rs.settings["font_size"] = fs
        self._apply_font_size()
        self._rs.status = f"font size: {fs}"
        self._rs.dirty = True

    def _handle_settings_key(self, key: str) -> None:
        """Input handling on the SETUP page (font adjust + mapper + cursor)."""
        if key == "up":
            self._rs.move_cursor(-1)
        elif key == "down":
            self._rs.move_cursor(1)
        elif key == "enter":
            self._activate()
        elif key in ("left", "right"):
            items = self._rs.current_items()
            item = items[self._rs.cursor] if 0 <= self._rs.cursor < len(items) else None
            if item is not None and item.get("ui") == "font_size":
                self._adjust_font_size(-2 if key == "left" else 2)
            else:
                self._rs.set_page(self._rs.page + (-1 if key == "left" else 1))
        elif key in ("h", "?"):
            self._rs.status = "L1/R1 pages · ↑↓ move · ←→ font · enter select"
            self._rs.dirty = True

    def _handle_ui(self, item: dict) -> None:
        """Act on a ``kind="ui"`` SETTINGS row (font save, calibrate, or capture)."""
        ui = item.get("ui")
        if ui == "font_size":
            self._persist_settings()
            self._rs.status = f"font size: {int(getattr(self.config, 'font_size', 16))} saved"
            self._rs.dirty = True
        elif ui == "calibrate":
            self._start_calibration()
        elif ui == "map":
            ak = item.get("app_key")
            kind = item.get("input", "key")
            if ak:
                self._start_capture_for([(ak, kind)])

    def _start_capture_for(self, seq) -> None:
        """Begin capturing ``seq``, a list of ``(app_key, kind)`` pairs.

        ``kind`` is ``"abs"`` (the d-pad axis) or ``"key"`` (a face button).
        """
        self._capture_active = True
        self._capture_seq = list(seq)
        self._capture_idx = 0
        self._capture_last_abs = None
        self._update_capture_status()

    def _start_calibration(self) -> None:
        """Run the calibration wizard: capture the d-pad + face buttons in order.

        Prompts the user to press each control ("press UP now", …), captures the
        mapping, and persists it (the SETUP "calibrate" row re-runs this).
        """
        self._auto_calib_done = True
        self._start_capture_for(list(view.CALIB_SEQUENCE))

    def _update_capture_status(self) -> None:
        if 0 <= self._capture_idx < len(self._capture_seq):
            ak, kind = self._capture_seq[self._capture_idx]
        else:
            ak, kind = "?", "key"
        if kind == "abs":
            # The d-pad: the user presses that direction on the d-pad now.
            prompt = f"press {ak.upper()} now"
        else:
            names = {"enter": "A (enter)", "q": "B (q)", "h": "X (h)"}
            prompt = f"press {names.get(ak, ak)}"
        self._rs.status = (f"CAPTURE {self._capture_idx + 1}/{len(self._capture_seq)}: "
                            f"{prompt}")
        self._rs.dirty = True

    def _on_capture(self, ev, now_ms: float) -> None:
        """Consume a raw joystick event while a capture is active.

        A press of L1/R1 aborts the capture (but still pages as normal). The next
        event matching the current step's kind assigns the mapping: an ``"abs"``
        step captures the d-pad axis + sign on a press edge; a ``"key"`` step
        captures the face-button code on a press edge.
        """
        event_type, code, value = ev
        if event_type == 1 and value == 1 and code in (view.JOY_L1, view.JOY_R1):
            self._abort_capture()
            res = self._input.on_joystick_event(ev, now_ms)
            if res is not None:
                self._dispatch(res)
            return
        if not 0 <= self._capture_idx < len(self._capture_seq):
            return
        ak, kind = self._capture_seq[self._capture_idx]
        if kind == "abs":
            if event_type != 3:  # EV_ABS only
                return
            sign = _abs_sign(value)
            if sign == 0:  # center: not a press
                return
            if (code, sign) == self._capture_last_abs:
                return  # repeat of the same axis/sign; wait for a new press
            self._set_dpad_map(ak, code, sign)
            self._capture_last_abs = (code, sign)
        else:
            if event_type != 1 or value != 1:  # KEY press edge only
                return
            self._set_joy_map(code, ak)
        self._capture_idx += 1
        if self._capture_idx >= len(self._capture_seq):
            self._capture_active = False
            self._calibrated = True
            self.config.calibrated = True
            self._persist_settings()
            self._rs.status = "mapped"
            self._rs.dirty = True
        else:
            self._update_capture_status()

    def _abort_capture(self) -> None:
        """Stop the capture (L1/R1 press); the user is back in normal control."""
        self._capture_active = False
        self._capture_seq = []
        self._capture_idx = 0
        self._capture_last_abs = None
        self._rs.status = "capture aborted (L1/R1)"
        self._rs.dirty = True

    def _set_joy_map(self, code: int, app_key: str) -> None:
        """Map ``code`` to ``app_key`` (replacing any prior non-L1/R1 mapping)."""
        for c in list(self._input.joy_map):
            if self._input.joy_map[c] == app_key and c not in (view.JOY_L1, view.JOY_R1):
                del self._input.joy_map[c]
        self._input.joy_map[code] = app_key
        cmap = getattr(self.config, "joy_map", None)
        if isinstance(cmap, dict):
            for c in list(cmap):
                if cmap[c] == app_key and c not in (view.JOY_L1, view.JOY_R1):
                    del cmap[c]
            cmap[code] = app_key
        self._rs.settings["joy_map"] = dict(self._input.joy_map)
        self._persist_settings()

    def _set_dpad_map(self, app_key: str, axis: int, sign: int) -> None:
        """Map a d-pad direction to an ``(axis, sign)`` entry (the analog d-pad)."""
        self._input.dpad_map[app_key] = [axis, sign]
        dmap = getattr(self.config, "dpad_map", None)
        if isinstance(dmap, dict):
            dmap[app_key] = [axis, sign]
        self._rs.settings["dpad_map"] = dict(self._input.dpad_map)

    # ------------------------------------------------------------------
    # Input (app keys).
    # ------------------------------------------------------------------
    def _handle_key(self, key: str) -> None:
        if key in ("q", "escape"):
            self._quit = True
            return
        # L1/R1 arrive as page_left / page_right (see InputController): GLOBAL
        # page navigation that works on every page, including SETUP, so the
        # SETTINGS page can never trap the user. The d-pad left/right is the
        # local key (font adjust on the font row; page nav elsewhere).
        if key == "page_left":
            self._rs.set_page(self._rs.page - 1)
            return
        if key == "page_right":
            self._rs.set_page(self._rs.page + 1)
            return
        if self._is_setup_page():
            self._handle_settings_key(key)
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
            cmds, _status = res
            if cmds is None:
                # A ``kind="ui"`` SETTINGS row: act locally, send nothing.
                items = self._rs.current_items()
                item = items[self._rs.cursor] if 0 <= self._rs.cursor < len(items) else None
                if item is not None:
                    self._handle_ui(item)
            else:
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
    def _native_size(self) -> Tuple[int, int]:
        return native_size(self.selftest)

    def _screen_size(self) -> Tuple[int, int]:
        return screen_size(self.config, self.selftest)

    def _set_mode(self):
        return set_mode(self.config, self.selftest)

    def _resolve_font(self) -> Optional[str]:
        return resolve_font(self.config)

    def _font_size_for(self, w: int, h: int) -> int:
        return font_size_for(self.config, w, h)

    def _load_font(self, w: Optional[int] = None, h: Optional[int] = None):
        return load_font(self.config, self.selftest, w, h)

    def _colors(self) -> Dict[str, str]:
        return colors(self.config)

    def _render_once(self, renderer) -> int:
        up = self._conn is not None and self._conn.connected
        ep = f"ws://{self.config.core_host}:{self.config.core_port}"
        rows = self._rs.build_rows(renderer.cols, connected=up, endpoint=ep)
        renderer.draw(rows)
        return len(rows)

    def _video_driver(self) -> str:
        return video_driver(self.selftest)

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
        screen, w, h = self._set_mode()
        pygame.display.set_caption("RTR REMOTE")
        font = self._load_font(w, h)
        renderer = Renderer(screen, font, self._colors())
        self._screen = screen
        self._font = font
        self._renderer = renderer
        self._build_settings()

        if not self.selftest:
            self._start_conn()
            self._start_joy()
            # First launch (calibrated is False): run the calibration wizard. It
            # prompts "press UP now", captures the d-pad + face buttons, and
            # persists the map (calibrated=True). The SETUP "calibrate" row
            # re-runs it on demand.
            if not self.config.calibrated:
                self._start_calibration()

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
        screen, w, h = self._set_mode()
        active = pygame.display.get_driver()
        if active in ("offscreen", "dummy", ""):
            print(f"RTR DIAG-VERDICT: NO FRAMEBUFFER DRIVER (active={active!r}); "
                  f"the bundled SDL2 has no working fb driver -> point the PAK "
                  f"at the system SDL2 (see Dronage Terminal PAK).", flush=True)
        else:
            print(f"RTR DIAG-VERDICT: framebuffer driver bound: {active!r}",
                  flush=True)
        font = self._load_font(w, h)
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
            websockets.connect(url, ping_interval=None, ping_timeout=None,
                               proxy=None),
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
    L1/R1 page navigation and the d-pad ABS edge/no-repeat/center checks.
    Prints ``SELFTEST PASS`` (True) or a FAIL line (False).
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

        # L1/R1 page globally (no quit chord): a single press pages.
        ctrl = InputController()
        t0 = time.monotonic() * 1000.0
        l1 = ctrl.on_joystick_event((1, view.JOY_L1, 1), t0)
        r1 = ctrl.on_joystick_event((1, view.JOY_R1, 1), t0 + 50)
        if l1 != ("key", "page_left") or r1 != ("key", "page_right"):
            print(f"SELFTEST FAIL: L1/R1 page nav wrong: {l1} {r1}")
            pygame.quit()
            return False
        # The d-pad is an analog axis: a press edge emits the direction.
        dpad = InputController()
        up = dpad.on_joystick_event((3, 17, 4294967295), t0)  # axis 17 negative
        if up != ("key", "up"):
            print(f"SELFTEST FAIL: d-pad UP not mapped: {up}")
            pygame.quit()
            return False
        # Holding it does not repeat; returning to center emits nothing.
        held = dpad.on_joystick_event((3, 17, 4294967295), t0 + 10)
        center = dpad.on_joystick_event((3, 17, 0), t0 + 20)
        if held is not None or center is not None:
            print(f"SELFTEST FAIL: d-pad repeat/center wrong: {held} {center}")
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
