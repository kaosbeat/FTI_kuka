"""Trimui LED test app (PAK).

A small terminal-style SDL2 app that drives the 22 handheld LEDs from
``/sys/class/led_anim/`` so each one can be verified individually. Input is
joystick-primary (the device has no keyboard) with keyboard support for local
development.

The app reuses the SDL2 display/font helpers from :mod:`rtr.remote.sdlapp` and
the raw ``/dev/input`` joystick reader, mirroring the launcher
(:mod:`rtr.remote.mainmenu`). It is launched as a child process from the menu.

Components:

- :func:`build_frame` -- pure: a list of 22 palette indices -> the exact
  ``frame_hex`` string (``RRGGBB`` values, space-separated, trailing space).
- :class:`LedSysfs` -- the sysfs backend: startup snapshot, safe ``frame_hex``
  write (the ``effect_enable`` 1->0 toggle), and exit restore. A memory
  backend records writes in order for the headless self-test.
- :class:`LedApp` -- the app: display, input dispatch, per-LED color cycling,
  live preview, and a non-blocking test sweep.
- :func:`selftest` -- headless self-test (dummy driver, memory backend, no sysfs).

Safe-write rule: two consecutive writes to ``frame_hex`` while
``effect_enable`` is 0 can block the kernel. Every frame write is therefore
bracketed by ``effect_enable=1`` then ``effect_enable=0`` (see
:meth:`LedSysfs.write_frame`). The app snapshots the relevant sysfs knobs on
startup and restores the exact saved values on exit (not fixed defaults).
"""

import argparse
import logging
import os
import queue
import signal
import sys
import threading
import time
from typing import Dict, List, Optional

try:
    from .sdlapp import (
        Renderer,
        InputController,
        DEFAULT_COLORS,
        KEY_MAP,
        colors,
        load_font,
        set_mode,
        video_driver,
    )
except ImportError:  # pragma: no cover - running the file directly, not as a pkg
    from rtr.remote.sdlapp import (
        Renderer,
        InputController,
        DEFAULT_COLORS,
        KEY_MAP,
        colors,
        load_font,
        set_mode,
        video_driver,
    )

try:
    import pygame
except ImportError:  # pragma: no cover - device always bundles pygame
    pygame = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LED layout + palette.
# ---------------------------------------------------------------------------
# The 22 LEDs, in ``frame_hex`` order. The driver supports up to 22 XRGB values
# per frame (``/sys/class/led_anim/help``: "total 22 XRGB 32bpp data"); a shorter
# frame leaves the remaining LEDs off. The sysfs ``effect_*`` knobs name the
# groups: ``m`` = the 8-LED top bar, ``f1``/``f2`` = the two stick clicks
# (L4/R4), ``l``/``r`` = the 4 quarter LEDs around each analog stick (L3/R3),
# ``rear`` = the 4 back buttons (L1/L2/R1/R2).
#
#    1-8   top bar, left to right            (m)
#    9     R4  right stick click             (f2)
#   10     L4  left stick click              (f1)
#   11-14  L3 quarters NW/SW/SE/NE           (l)   [confirmed by user]
#   15-18  R3 quarters NW/SW/SE/NE           (r)   [order unverified]
#   19-22  rear buttons L1/L2/R1/R2          (rear) [order unverified]
#
# The physical order of 9-22 is confirmed for the L3 quarters (11-14); the rest
# is a best guess. Run the "test all" sweep to verify each position (it lights
# one LED per tick in frame order).
LED_NAMES: List[str] = [
    "top1", "top2", "top3", "top4", "top5", "top6", "top7", "top8",
    "R4", "L4",
    "L3-NW", "L3-SW", "L3-SE", "L3-NE",
    "R3-NW", "R3-SW", "R3-SE", "R3-NE",
    "L1", "L2", "R1", "R2",
]
NUM_LEDS = len(LED_NAMES)

# Fixed 8-color palette. Index 7 (black) is "off". Each LED stores its color
# as a palette index; :func:`build_frame` converts indices to ``RRGGBB``.
PALETTE: List[tuple] = [
    ("red",     (255, 0, 0)),
    ("green",   (0, 255, 0)),
    ("blue",    (0, 0, 255)),
    ("yellow",  (255, 255, 0)),
    ("cyan",    (0, 255, 255)),
    ("magenta", (255, 0, 255)),
    ("white",   (255, 255, 255)),
    ("black",   (0, 0, 0)),
]
PALETTE_HEX: List[str] = [
    f"{r:02X}{g:02X}{b:02X}" for (_n, (r, g, b)) in PALETTE
]
NUM_COLORS = len(PALETTE)
WHITE_IDX = PALETTE.index(("white", (255, 255, 255)))
BLACK_IDX = PALETTE.index(("black", (0, 0, 0)))

# The sysfs knobs the app snapshots on startup and restores on exit. The real
# device state differs from the old assumed defaults, so we restore the exact
# saved values (never fixed constants).
SAFE_KEYS = [
    "effect_enable",
    "effect_m",
    "effect_cycles_m",
    "max_scale",
    "max_scale_f1f2",
    "max_scale_lr",
]
FRAME_FILE = "frame_hex"
LED_SYSFS_BASE = "/sys/class/led_anim/"

# Action rows shown below the LED rows (scrollable, like the LED rows).
ACTIONS = ["all white", "all off", "test all", "restore"]

# Screen geometry. The screen is a fixed title row + a scrollable window + a
# fixed help row. The window scrolls over the 22 LED rows + a divider + the 4
# action rows (WINDOW_CONTENT_ROWS = 27 content rows), so the cursor can reach
# every LED and action. The number of *visible* rows is the renderer's
# ``rows`` (screen height // char_h), which depends on the font size; the layout
# reads it at runtime (see :meth:`LedApp._screen_rows`) so it adapts. At the
# current config (font_size 6 -> size 19 -> char_h 19) the panel shows ~40 rows,
# so all 27 content rows fit with no scrolling; if the font is enlarged the
# window shrinks and the same scroll logic kicks in.
DEFAULT_SCREEN_ROWS = 22  # fallback before the renderer exists (selftest/diag)
WINDOW_CONTENT_ROWS = NUM_LEDS + 1 + len(ACTIONS)  # 27 (22 LEDs + divider + 4)
INTERACTIVE_ROWS = NUM_LEDS + len(ACTIONS)  # 26 (cursor range 0..25)

# The test sweep: how many frames to emit per LED (white then black) before
# moving on. Non-blocking: the main loop advances it one frame per tick.
SWEEP_FRAMES_PER_LED = 2


def build_frame(colors: List[int]) -> str:
    """Build the exact ``frame_hex`` string from 22 palette indices.

    Returns ``"RRGGBB RRGGBB ... RRGGBB "`` -- 22 space-separated values with a
    required trailing space. Out-of-range indices clamp to black (off).
    """
    parts = []
    for idx in colors:
        if 0 <= idx < NUM_COLORS:
            parts.append(PALETTE_HEX[idx])
        else:
            parts.append(PALETTE_HEX[BLACK_IDX])
    return " ".join(parts) + " "


def _center(text: str, width: int) -> str:
    pad = max(0, width - len(text))
    left = pad // 2
    return " " * left + text + " " * (pad - left)


# ---------------------------------------------------------------------------
# sysfs backend (real + memory).
# ---------------------------------------------------------------------------
class LedSysfs:
    """Drives the LED sysfs interface with a safe-write pattern.

    The real backend reads/writes files under ``/sys/class/led_anim/``. The
    memory backend (``memory=True``) keeps values in a dict and records every
    write in order, so the headless self-test can assert the exact
    ``frame_hex`` strings and the ``effect_enable`` toggle sequence without
    touching real sysfs.

    Safe write: :meth:`write_frame` writes ``frame_hex`` and then brackets it
    with ``effect_enable=1`` -> ``effect_enable=0``. Two consecutive
    ``frame_hex`` writes while ``effect_enable`` is 0 can block the kernel, so
    the toggle is mandatory on every write.
    """

    def __init__(self, base: str = LED_SYSFS_BASE, memory: bool = False):
        self.base = base
        self.memory = memory
        # Memory backend state: file -> current value (string).
        self._mem: Dict[str, str] = {}
        # Ordered record of (file, value) writes (memory backend; for tests).
        self.writes: List[tuple] = []
        # The startup snapshot (set by :meth:`snapshot`).
        self._snap: Optional[Dict[str, str]] = None

    # -- low-level file I/O -------------------------------------------------
    def _path(self, name: str) -> str:
        return os.path.join(self.base, name)

    def read(self, name: str) -> str:
        if self.memory:
            return self._mem.get(name, "")
        with open(self._path(name), "r") as f:
            return f.read().strip()

    def write(self, name: str, value: str) -> None:
        if self.memory:
            self._mem[name] = value
            self.writes.append((name, value))
            return
        with open(self._path(name), "w") as f:
            f.write(value)

    # -- snapshot / restore -------------------------------------------------
    def snapshot(self) -> Dict[str, str]:
        """Read the SAFE_KEYS now (the startup state to restore on exit)."""
        snap: Dict[str, str] = {}
        for key in SAFE_KEYS:
            try:
                snap[key] = self.read(key)
            except OSError as exc:
                print(f"LED: snapshot read {key} failed: {exc}", flush=True)
                snap[key] = ""
        self._snap = snap
        return snap

    def restore(self) -> None:
        """Write the startup snapshot values back (exact, not fixed defaults)."""
        if not self._snap:
            return
        for key in SAFE_KEYS:
            val = self._snap.get(key)
            if val is None or val == "":
                continue
            try:
                self.write(key, val)
            except OSError as exc:
                print(f"LED: restore write {key} failed: {exc}", flush=True)

    # -- safe frame write ---------------------------------------------------
    def write_frame(self, frame: str) -> None:
        """Write a ``frame_hex`` frame safely (bracketed by the effect toggle).

        Sequence: ``frame_hex`` -> ``effect_enable=1`` -> ``effect_enable=0``.
        The toggle between any two ``frame_hex`` writes prevents the
        kernel-level block caused by two consecutive frame writes while
        ``effect_enable`` is 0.
        """
        self.write(FRAME_FILE, frame)
        self.write("effect_enable", "1")
        self.write("effect_enable", "0")


# ---------------------------------------------------------------------------
# App: display + input + LED control.
# ---------------------------------------------------------------------------
class LedApp:
    """LED test app: per-LED color cycling, live preview, test sweep.

    Joystick-primary (the device has no keyboard); keyboard is supported for
    local development. The app reuses the SDL2 helpers from
    :mod:`rtr.remote.sdlapp` and the raw ``/dev/input`` joystick reader,
    mirroring :class:`~rtr.remote.mainmenu.MainMenu`.

    Rows: a fixed title bar, a 20-row scrollable window over the 22 LED rows
    (one per LED, with a live color swatch) + a divider + the 4 action rows
    (all white / all off / test all / restore), and a fixed help row. The
    device shows exactly 22 rows, so the middle window scrolls as the cursor
    moves.
    """

    def __init__(self, config, *, selftest: bool = False, sysfs: Optional[LedSysfs] = None):
        self.config = config
        self.selftest = selftest
        self._q: "queue.Queue" = queue.Queue()
        self._joy = None
        self._joy_thread = None
        self._input = InputController(
            joy_map=getattr(config, "joy_map", None),
            dpad_map=getattr(config, "dpad_map", None),
            quit_codes=getattr(config, "quit_codes", None))
        self._cursor = 0
        self._scroll_top = 0
        self._quit = False
        # Per-LED palette index (22 LEDs); all start black (off).
        self._colors: List[int] = [BLACK_IDX] * NUM_LEDS
        # Non-blocking test sweep state: None (idle) or (led, frame_idx).
        self._sweep: Optional[tuple] = None
        # sysfs backend (memory for selftest; real otherwise).
        self._sysfs = sysfs if sysfs is not None else LedSysfs(memory=self.selftest)
        # Renderer objects (set in run()/diag(); None until then).
        self._screen = None
        self._font = None
        self._renderer = None

    # ------------------------------------------------------------------
    # Display setup.
    # ------------------------------------------------------------------
    def _install_signal_handlers(self) -> None:
        def _handler(signum, frame):
            self._quit = True
        try:
            signal.signal(signal.SIGTERM, _handler)
            signal.signal(signal.SIGINT, _handler)
        except (ValueError, OSError):
            pass

    def _setup_display(self) -> None:
        self._install_signal_handlers()
        os.environ["SDL_JOYSTICK"] = "disabled"
        driver = video_driver(False)
        if driver:
            os.environ["SDL_VIDEODRIVER"] = driver
        else:
            os.environ.pop("SDL_VIDEODRIVER", None)
        pygame.init()
        pygame.display.init()
        screen, w, h = set_mode(self.config, False)
        pygame.display.set_caption("RTR LED TEST")
        font = load_font(self.config, False, w, h)
        self._screen = screen
        self._font = font
        self._renderer = Renderer(screen, font, colors(self.config))

    # ------------------------------------------------------------------
    # Joystick (raw /dev/input reader thread). Mirrors MainMenu.
    # ------------------------------------------------------------------
    def _start_joy(self) -> None:
        try:
            from .events import Joystick
        except ImportError:  # pragma: no cover
            from rtr.remote.events import Joystick
        dev = getattr(self.config, "device", None)
        if not dev:
            print("LED: no device configured; joystick disabled", flush=True)
            return
        if dev == "auto":
            devs = getattr(self.config, "devices", None) or []
            dev = next((d for d in devs if os.path.exists(d)), None)
            if dev is None:
                print(f"LED: auto: no device found in {devs}", flush=True)
                return
        print(f"LED: using device {dev}", flush=True)
        self._joy = Joystick(dev)

        def _reader() -> None:
            try:
                self._joy.open()
            except OSError as exc:
                print(f"LED: FAILED to open {dev}: {exc}", flush=True)
                return
            print(f"LED: opened {dev}", flush=True)
            n = 0
            retries = 0
            while True:
                ev = self._joy.read()
                if ev is None:
                    retries += 1
                    if retries > 5:
                        print(f"LED: reader stopped (EOF/error) on {dev} "
                              f"after {retries} retries", flush=True)
                        break
                    print(f"LED: EOF on {dev}; reopening ({retries}/5)", flush=True)
                    try:
                        self._joy.close()
                    except Exception:  # noqa: BLE001
                        pass
                    time.sleep(0.2)
                    try:
                        self._joy.open()
                    except OSError as exc:
                        print(f"LED: reopen failed on {dev}: {exc}", flush=True)
                        break
                    continue
                retries = 0
                n += 1
                if n <= 5:
                    print(f"LED: ev[{n}] type={ev[0]} code={ev[1]} val={ev[2]}",
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

    # ------------------------------------------------------------------
    # Row building.
    # ------------------------------------------------------------------
    def _screen_rows(self) -> int:
        """Visible rows = the renderer's ``rows`` (h // char_h), or the fallback
        before the renderer exists (selftest / diag before display init)."""
        return self._renderer.rows if self._renderer else DEFAULT_SCREEN_ROWS

    def _window_rows(self) -> int:
        """Rows available to the scrollable window (screen minus title + help)."""
        return max(1, self._screen_rows() - 2)

    def _max_scroll(self) -> int:
        """Largest scroll offset: 0 when all content fits (no scrolling)."""
        return max(0, WINDOW_CONTENT_ROWS - self._window_rows())

    def _total_rows(self) -> int:
        return self._screen_rows()

    def _cursor_pos(self, c: int) -> int:
        """Window position of cursor ``c`` (0..25) within the 27 content rows.

        The divider sits at content index 22 (after the 22 LED rows), so action
        cursors (22..25) are offset by +1.
        """
        return c if c < NUM_LEDS else c + 1

    def _window_content(self) -> List[dict]:
        """Build the 27 scrollable content rows: 22 LEDs + divider + 4 actions."""
        content: List[dict] = []
        for i in range(NUM_LEDS):
            content.append(self._led_row(i))
        content.append({"style": "divider"})
        for j, action in enumerate(ACTIONS):
            c = NUM_LEDS + j
            if c == self._cursor:
                content.append({"text": f"  ▸ {action}", "style": "item_sel"})
            else:
                content.append({"text": f"    {action}", "style": "item"})
        return content

    def _build_rows(self) -> List[dict]:
        """Build the screen rows: title + window slice (+ blank pad) + help.

        The window shows up to :meth:`_window_rows` content rows; when the
        content is shorter than the window (the normal ~40-row case) the gap is
        padded with blank rows so the help row stays pinned to the bottom.
        """
        cols = self._renderer.cols if self._renderer else 80
        content = self._window_content()
        wr = self._window_rows()
        top = max(0, min(self._scroll_top, self._max_scroll()))
        window = content[top: top + wr]
        for _ in range(wr - len(window)):
            window.append({"style": "item", "text": ""})
        rows: List[dict] = [{"text": _center("LED TEST", cols), "style": "bar"}]
        rows.extend(window)
        rows.append({"text": "↑↓ move · ←→ color · A write · home quits",
                      "style": "help"})
        return rows

    def _ensure_visible(self) -> None:
        """Clamp the scroll so the cursor row stays inside the window."""
        wr = self._window_rows()
        pos = self._cursor_pos(self._cursor)
        if pos < self._scroll_top:
            self._scroll_top = pos
        elif pos >= self._scroll_top + wr:
            self._scroll_top = pos - (wr - 1)
        if self._scroll_top < 0:
            self._scroll_top = 0
        if self._scroll_top > self._max_scroll():
            self._scroll_top = self._max_scroll()

    def _led_row(self, i: int) -> dict:
        """Build one LED row: a live color swatch + name + current color."""
        idx = self._colors[i]
        name = LED_NAMES[i]
        color_name = PALETTE[idx][0]
        swatch = "■" if idx != BLACK_IDX else "·"
        label = f"  {swatch} {name:<6} {color_name}"
        if i == self._cursor:
            return {"text": f"▸ {label}", "style": "item_sel"}
        return {"text": f"  {label}", "style": "item"}

    # ------------------------------------------------------------------
    # LED actions.
    # ------------------------------------------------------------------
    def _set_color(self, i: int, idx: int) -> None:
        self._colors[i] = idx % NUM_COLORS

    def _write_current(self) -> None:
        """Write the current per-LED colors as a frame (safe write)."""
        frame = build_frame(self._colors)
        try:
            self._sysfs.write_frame(frame)
        except OSError as exc:
            print(f"LED: write failed: {exc}", flush=True)

    def _all_white(self) -> None:
        self._colors = [WHITE_IDX] * NUM_LEDS
        self._write_current()

    def _all_off(self) -> None:
        self._colors = [BLACK_IDX] * NUM_LEDS
        self._write_current()

    def _restore(self) -> None:
        try:
            self._sysfs.restore()
        except OSError as exc:
            print(f"LED: restore failed: {exc}", flush=True)

    # -- test sweep (non-blocking) ----------------------------------------
    def _start_sweep(self) -> None:
        self._sweep = (0, 0)

    def _cancel_sweep(self) -> None:
        if self._sweep is None:
            return
        self._sweep = None
        self._write_current()

    def _sweep_tick(self) -> None:
        """Advance the test sweep one frame (called each main-loop tick).

        Lights one LED white (frame 0) then black (frame 1), moving on to the
        next LED. Non-blocking: one frame per tick keeps the UI responsive and
        avoids consecutive ``frame_hex`` writes.
        """
        if self._sweep is None:
            return
        led, frame_idx = self._sweep
        if frame_idx >= SWEEP_FRAMES_PER_LED:
            led += 1
            frame_idx = 0
            if led >= NUM_LEDS:
                # Sweep complete: leave everything off.
                self._sweep = None
                self._colors = [BLACK_IDX] * NUM_LEDS
                self._write_current()
                return
        # Build the sweep frame: all black except the current LED (white on
        # frame 0, black on frame 1).
        colors = [BLACK_IDX] * NUM_LEDS
        if frame_idx == 0:
            colors[led] = WHITE_IDX
        frame = build_frame(colors)
        try:
            self._sysfs.write_frame(frame)
        except OSError as exc:
            print(f"LED: sweep write failed: {exc}", flush=True)
            self._sweep = None
            return
        self._sweep = (led, frame_idx + 1)

    # ------------------------------------------------------------------
    # Input handling.
    # ------------------------------------------------------------------
    def _handle_key(self, key: str) -> None:
        if key in ("q", "escape"):
            self._quit = True
            return
        if key in ("page_left", "page_right"):
            return  # single page; no-op
        if key == "up":
            self._cursor = max(0, self._cursor - 1)
            self._ensure_visible()
        elif key == "down":
            self._cursor = min(INTERACTIVE_ROWS - 1, self._cursor + 1)
            self._ensure_visible()
        elif key in ("left", "right"):
            # On a LED row: cycle that LED's color (live preview). On an
            # action row: cancel the sweep (if running).
            if self._cursor < NUM_LEDS:
                delta = 1 if key == "right" else -1
                self._set_color(self._cursor,
                                 self._colors[self._cursor] + delta)
                self._write_current()
            elif self._sweep is not None:
                self._cancel_sweep()
        elif key == "enter":
            if self._cursor < NUM_LEDS:
                self._write_current()
            else:
                j = self._cursor - NUM_LEDS
                if 0 <= j < len(ACTIONS):
                    if ACTIONS[j] == "all white":
                        self._all_white()
                    elif ACTIONS[j] == "all off":
                        self._all_off()
                    elif ACTIONS[j] == "test all":
                        self._start_sweep()
                    elif ACTIONS[j] == "restore":
                        self._restore()
        elif key in ("h", "?"):
            pass

    def _dispatch(self, res) -> None:
        if res[0] == "quit":
            self._quit = True
        elif res[0] == "key":
            self._handle_key(res[1])

    # ------------------------------------------------------------------
    # Main-thread poll (drains the queue, dispatches joystick events).
    # ------------------------------------------------------------------
    def _poll(self) -> None:
        now_ms = time.monotonic() * 1000.0
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "joy":
                    res = self._input.on_joystick_event(payload, now_ms)
                    if res is not None:
                        self._dispatch(res)
        except queue.Empty:
            pass

    # ------------------------------------------------------------------
    # Lifecycle.
    # ------------------------------------------------------------------
    def run(self) -> None:
        if pygame is None:
            raise RuntimeError("pygame not installed; run `pip install pygame`")
        self._setup_display()
        # Snapshot the sysfs state now (restore the exact values on exit).
        try:
            self._sysfs.snapshot()
        except OSError as exc:
            print(f"LED: snapshot failed: {exc}", flush=True)
        if not self.selftest:
            self._start_joy()
        renderer = self._renderer
        try:
            while not self._quit:
                self._poll()
                for event in pygame.event.get():
                    if event.type == pygame.KEYDOWN:
                        key = KEY_MAP.get(event.key)
                        if key is not None:
                            self._handle_key(key)
                self._sweep_tick()
                renderer.draw(self._build_rows())
                pygame.event.pump()
                pygame.time.wait(100)
        finally:
            self._stop_joy()
            # Restore the startup sysfs state (exact saved values).
            try:
                self._sysfs.restore()
            except OSError as exc:
                print(f"LED: restore failed: {exc}", flush=True)
            pygame.quit()

    def diag(self) -> int:
        """Init the real display, print the diagnostic, and exit.

        No joystick, no loop: a fast, isolated probe of the SDL video driver
        and the LED layout.
        """
        if pygame is None:
            raise RuntimeError("pygame not installed; run `pip install pygame`")
        self._setup_display()
        active = pygame.display.get_driver()
        screen = self._screen
        renderer = self._renderer
        rows = self._build_rows()
        renderer.draw(rows)
        print(f"LED DIAG driver={active} screen={screen.get_size()} "
              f"cols={renderer.cols} char_h={renderer.char_h} "
              f"rows={len(rows)}", flush=True)
        pygame.quit()
        return 0


# ---------------------------------------------------------------------------
# Self-test (headless, memory backend, no real sysfs).
# ---------------------------------------------------------------------------
def selftest() -> bool:
    """Run the headless self-test (dummy driver, memory backend, no sysfs).

    Builds + renders the LED rows, exercises cursor up/down, verifies
    ``build_frame`` produces the exact ``frame_hex`` string (22 values,
    trailing space), verifies the safe-write sequence (``frame_hex`` ->
    ``effect_enable=1`` -> ``effect_enable=0``), and verifies restore writes
    the snapshot values back. Prints ``LED SELFTEST PASS`` (True) or a FAIL
    line (False).
    """
    if pygame is None:
        print("LED SELFTEST FAIL: pygame not installed")
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

        cfg = Config()
        # Memory backend: seed the snapshot values the app will restore.
        sysfs = LedSysfs(memory=True)
        sysfs.write("effect_enable", "1")
        sysfs.write("effect_m", "2")
        sysfs.write("effect_cycles_m", "-1")
        sysfs.write("max_scale", "100")
        sysfs.write("max_scale_f1f2", "100")
        sysfs.write("max_scale_lr", "100")
        sysfs.writes.clear()

        app = LedApp(cfg, selftest=True, sysfs=sysfs)
        app._screen = screen
        app._font = font
        app._renderer = renderer

        # Snapshot the seeded state.
        snap = app._sysfs.snapshot()
        if snap.get("effect_enable") != "1" or snap.get("effect_m") != "2":
            print(f"LED SELFTEST FAIL: snapshot wrong: {snap}")
            pygame.quit()
            return False

        # Build + render the LED rows.
        rows = app._build_rows()
        renderer.draw(rows)
        if len(rows) != app._total_rows():
            print(f"LED SELFTEST FAIL: rows={len(rows)} != {app._total_rows()}")
            pygame.quit()
            return False

        # Cursor up/down.
        app._handle_key("down")
        app._handle_key("up")
        if app._cursor != 0:
            print(f"LED SELFTEST FAIL: cursor not back to 0: {app._cursor}")
            pygame.quit()
            return False

        # build_frame: exact string (14 values, trailing space).
        app._colors = [WHITE_IDX] * NUM_LEDS
        frame = build_frame(app._colors)
        expected = " ".join([PALETTE_HEX[WHITE_IDX]] * NUM_LEDS) + " "
        if frame != expected:
            print(f"LED SELFTEST FAIL: build_frame wrong: {frame!r}")
            pygame.quit()
            return False

        # Safe write: frame_hex -> effect_enable=1 -> effect_enable=0.
        app._write_current()
        if len(sysfs.writes) != 3:
            print(f"LED SELFTEST FAIL: expected 3 writes, got {len(sysfs.writes)}")
            pygame.quit()
            return False
        if sysfs.writes[0][0] != FRAME_FILE:
            print(f"LED SELFTEST FAIL: first write not frame_hex: {sysfs.writes[0]}")
            pygame.quit()
            return False
        if sysfs.writes[1] != ("effect_enable", "1"):
            print(f"LED SELFTEST FAIL: missing effect_enable=1: {sysfs.writes[1]}")
            pygame.quit()
            return False
        if sysfs.writes[2] != ("effect_enable", "0"):
            print(f"LED SELFTEST FAIL: missing effect_enable=0: {sysfs.writes[2]}")
            pygame.quit()
            return False

        # Per-LED targeting: set LED 3 (index 2) to red, write, verify the
        # frame has red at position 2 and black elsewhere.
        app._sysfs.writes.clear()
        app._colors = [BLACK_IDX] * NUM_LEDS
        app._set_color(2, 0)  # red
        app._write_current()
        written = sysfs.writes[0]
        if written[0] != FRAME_FILE:
            print(f"LED SELFTEST FAIL: targeting write not frame_hex: {written}")
            pygame.quit()
            return False
        parts = written[1].split()
        if len(parts) != NUM_LEDS:
            print(f"LED SELFTEST FAIL: frame has {len(parts)} values, "
                  f"expected {NUM_LEDS}")
            pygame.quit()
            return False
        if parts[2] != PALETTE_HEX[0]:
            print(f"LED SELFTEST FAIL: LED 3 not red: {parts[2]}")
            pygame.quit()
            return False
        if parts[0] != PALETTE_HEX[BLACK_IDX] or parts[21] != PALETTE_HEX[BLACK_IDX]:
            print("LED SELFTEST FAIL: other LEDs not black")
            pygame.quit()
            return False

        # Test sweep: start it, advance a few ticks, verify frames are written.
        app._sysfs.writes.clear()
        app._start_sweep()
        if app._sweep is None:
            print("LED SELFTEST FAIL: sweep did not start")
            pygame.quit()
            return False
        app._sweep_tick()
        app._sweep_tick()
        if not sysfs.writes:
            print("LED SELFTEST FAIL: sweep wrote nothing")
            pygame.quit()
            return False

        # Restore writes the snapshot values back.
        app._sysfs.writes.clear()
        app._restore()
        restored = {name: val for name, val in sysfs.writes}
        if restored.get("effect_enable") != "1" or restored.get("effect_m") != "2":
            print(f"LED SELFTEST FAIL: restore wrong: {restored}")
            pygame.quit()
            return False

        pygame.quit()
        print("LED SELFTEST PASS")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"LED SELFTEST FAIL: {exc}")
        return False


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="LED %(name)s: %(message)s")
    try:
        from .config import _add_flags, from_args
    except ImportError:
        from rtr.remote.config import _add_flags, from_args

    parser = argparse.ArgumentParser(description="Trimui LED test app")
    _add_flags(parser)
    parser.add_argument("--selftest", action="store_true",
                        help="run the headless self-test (dummy driver, no sysfs)")
    parser.add_argument("--diag", action="store_true",
                        help="print the display diagnostic and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return 0 if selftest() else 1

    config = from_args(args)

    if args.diag:
        return LedApp(config).diag()

    LedApp(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
