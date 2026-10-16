"""SDL2 launcher menu for the Trimui NextUI handheld (PAK).

A small SDL2 app that lists the available remote apps and launches the
selected one as a child process. It reuses the SDL2 display/font helpers
from :mod:`rtr.remote.sdlapp` and the raw ``/dev/input`` joystick reader.
Input is joystick-primary (the device has no keyboard) with keyboard
support for local development.

This module is the PAK entry point (launched from ``launch.sh``); it
replaces the direct launch of ``sdlapp.py``.

Components:

- :data:`APPS` -- the registry of launchable apps.
- :class:`MainMenu` -- the menu app: display, input dispatch, child launch.
- :func:`selftest` -- headless self-test (dummy driver, no core).
"""

import argparse
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from typing import List, Optional

try:
    from . import view
except ImportError:  # pragma: no cover - running the file directly, not as a pkg
    _pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from rtr.remote import view

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
# App registry (the apps the menu can launch).
# ---------------------------------------------------------------------------
APPS: List[dict] = [
    {"module": "sdlapp.py", "title": "kukapp", "config_key": "displaytitle"},
]


def _center(text: str, width: int) -> str:
    pad = max(0, width - len(text))
    left = pad // 2
    return " " * left + text + " " * (pad - left)


# ---------------------------------------------------------------------------
# Menu app: display + input + child launch.
# ---------------------------------------------------------------------------
class MainMenu:
    """SDL2 launcher menu: lists apps, launches the selected one.

    Joystick-primary (the device has no keyboard); keyboard is supported
    for local development. The menu reuses the SDL2 helpers from
    :mod:`rtr.remote.sdlapp` and the raw ``/dev/input`` joystick reader.
    """

    def __init__(self, config, *, selftest: bool = False):
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
        self._status = "ready"
        self._quit = False
        # Renderer objects (set in run()/diag(); None until then).
        self._screen = None
        self._font = None
        self._renderer = None

    # ------------------------------------------------------------------
    # Display setup.
    # ------------------------------------------------------------------
    def _install_signal_handlers(self) -> None:
        """Make SIGTERM/SIGINT quit cleanly (restore the display on exit)."""
        def _handler(signum, frame):
            self._quit = True
        try:
            signal.signal(signal.SIGTERM, _handler)
            signal.signal(signal.SIGINT, _handler)
        except (ValueError, OSError):
            pass

    def _setup_display(self) -> None:
        """Init the SDL2 display, font, and renderer for the menu."""
        self._install_signal_handlers()
        # Disable SDL's joystick subsystem so it doesn't grab the /dev/input
        # device (we read it raw via events.Joystick).
        os.environ["SDL_JOYSTICK"] = "disabled"
        driver = video_driver(False)
        if driver:
            os.environ["SDL_VIDEODRIVER"] = driver
        else:
            os.environ.pop("SDL_VIDEODRIVER", None)
        pygame.init()
        pygame.display.init()
        screen, w, h = set_mode(self.config, False)
        pygame.display.set_caption("RTR MENU")
        font = load_font(self.config, False, w, h)
        self._screen = screen
        self._font = font
        self._renderer = Renderer(screen, font, colors(self.config))

    # ------------------------------------------------------------------
    # Joystick (raw /dev/input reader thread). Mirrors SdlRemote.
    # ------------------------------------------------------------------
    def _start_joy(self) -> None:
        try:
            from .events import Joystick
        except ImportError:  # pragma: no cover
            from rtr.remote.events import Joystick
        dev = getattr(self.config, "device", None)
        if not dev:
            print("MENU: no device configured; joystick disabled", flush=True)
            return
        if dev == "auto":
            devs = getattr(self.config, "devices", None) or []
            dev = next((d for d in devs if os.path.exists(d)), None)
            if dev is None:
                print(f"MENU: auto: no device found in {devs}", flush=True)
                return
        print(f"MENU: using device {dev}", flush=True)
        self._joy = Joystick(dev)

        def _reader() -> None:
            try:
                self._joy.open()
            except OSError as exc:
                print(f"MENU: FAILED to open {dev}: {exc}", flush=True)
                return
            print(f"MENU: opened {dev}", flush=True)
            n = 0
            retries = 0
            while True:
                ev = self._joy.read()
                if ev is None:
                    # EOF: the device can drop briefly (e.g. another process
                    # touched it). Reopen a few times before giving up.
                    retries += 1
                    if retries > 5:
                        print(f"MENU: reader stopped (EOF/error) on {dev} "
                              f"after {retries} retries", flush=True)
                        break
                    print(f"MENU: EOF on {dev}; reopening ({retries}/5)",
                          flush=True)
                    try:
                        self._joy.close()
                    except Exception:  # noqa: BLE001
                        pass
                    time.sleep(0.2)
                    try:
                        self._joy.open()
                    except OSError as exc:
                        print(f"MENU: reopen failed on {dev}: {exc}", flush=True)
                        break
                    continue
                retries = 0
                n += 1
                if n <= 5:
                    print(f"MENU: ev[{n}] type={ev[0]} code={ev[1]} val={ev[2]}",
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
    def _title_for(self, app: dict) -> str:
        """Resolve the display title for an app (config override or static)."""
        key = app.get("config_key")
        if key:
            val = getattr(self.config, key, None)
            if val:
                return str(val)
        return str(app.get("title", "?"))

    def _build_rows(self) -> List[dict]:
        """Build the menu rows: bar + app items + status + help."""
        rows: List[dict] = []
        cols = self._renderer.cols if self._renderer else 80
        rows.append({"text": _center("RTR MENU", cols), "style": "bar"})
        rows.append({"style": "divider"})
        for i, app in enumerate(APPS):
            title = self._title_for(app)
            if i == self._cursor:
                rows.append({"text": f"  ▸ {title}", "style": "item_sel"})
            else:
                rows.append({"text": f"    {title}", "style": "item"})
        rows.append({"style": "divider"})
        rows.append({"text": self._status, "style": "status"})
        rows.append({"text": "↑↓ move · A select · home quits", "style": "help"})
        return rows

    # ------------------------------------------------------------------
    # Launch mechanism.
    # ------------------------------------------------------------------
    def _launch_cmd(self, app: dict) -> List[str]:
        """Build the child command: [python, repo/rtr/remote/<module>, --config]."""
        module = app["module"]
        repo = getattr(self.config, "repo", "/storage/rtr-remote")
        cmd = [sys.executable, os.path.join(repo, "rtr", "remote", module)]
        cfg = getattr(self.config, "config", None)
        if cfg:
            cmd.extend(["--config", cfg])
        return cmd

    def _launch(self, app: dict) -> None:
        """Stop the joystick, quit pygame, run the child, restore the display."""
        self._stop_joy()
        pygame.quit()
        cmd = self._launch_cmd(app)
        print(f"MENU: launching: {' '.join(cmd)}", flush=True)
        try:
            subprocess.run(cmd)
        except Exception as exc:
            print(f"MENU: launch failed: {exc}", flush=True)
        self._restore_display()

    def _restore_display(self) -> None:
        """Re-init the display after the child exits (best-effort)."""
        try:
            self._setup_display()
        except Exception as exc:
            print(f"MENU: restore failed: {exc}", flush=True)
            self._quit = True

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
        elif key == "down":
            self._cursor = min(len(APPS) - 1, self._cursor + 1)
        elif key == "enter":
            if 0 <= self._cursor < len(APPS):
                self._launch(APPS[self._cursor])
        elif key in ("h", "?"):
            self._status = "↑↓ move · A select · home quits"

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
                renderer.draw(self._build_rows())
                pygame.event.pump()
                pygame.time.wait(100)
        finally:
            self._stop_joy()
            pygame.quit()

    def diag(self) -> int:
        """Init the real display, print the diagnostic, and exit.

        No joystick, no loop: a fast, isolated probe of the SDL video driver
        and the menu layout.
        """
        if pygame is None:
            raise RuntimeError("pygame not installed; run `pip install pygame`")
        self._setup_display()
        active = pygame.display.get_driver()
        screen = self._screen
        renderer = self._renderer
        rows = self._build_rows()
        renderer.draw(rows)
        print(f"MENU DIAG driver={active} screen={screen.get_size()} "
              f"cols={renderer.cols} char_h={renderer.char_h} "
              f"rows={len(rows)}", flush=True)
        pygame.quit()
        return 0


# ---------------------------------------------------------------------------
# Self-test (headless, no core).
# ---------------------------------------------------------------------------
def selftest() -> bool:
    """Run the headless self-test (dummy driver, no core, no launch).

    Builds + renders the menu rows, exercises cursor up/down, verifies
    L1/R1 page nav returns page_left/page_right, and verifies the launch
    command is non-empty (without actually launching).
    Prints ``MENU SELFTEST PASS`` (True) or a FAIL line (False).
    """
    if pygame is None:
        print("MENU SELFTEST FAIL: pygame not installed")
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
        app = MainMenu(cfg, selftest=True)
        app._screen = screen
        app._font = font
        app._renderer = renderer

        # Build + render the menu rows.
        rows = app._build_rows()
        renderer.draw(rows)
        if not rows:
            print("MENU SELFTEST FAIL: _build_rows returned no rows")
            pygame.quit()
            return False

        # Cursor up/down.
        app._handle_key("down")
        app._handle_key("up")
        if app._cursor != 0:
            print(f"MENU SELFTEST FAIL: cursor not back to 0: {app._cursor}")
            pygame.quit()
            return False

        # L1/R1 page nav (no-op for single page, but the mapping must work).
        ctrl = InputController()
        t0 = time.monotonic() * 1000.0
        l1 = ctrl.on_joystick_event((1, view.JOY_L1, 1), t0)
        r1 = ctrl.on_joystick_event((1, view.JOY_R1, 1), t0 + 50)
        if l1 != ("key", "page_left") or r1 != ("key", "page_right"):
            print(f"MENU SELFTEST FAIL: L1/R1 page nav wrong: {l1} {r1}")
            pygame.quit()
            return False

        # Launch command must be non-empty (do not actually launch).
        cmd = app._launch_cmd(APPS[0])
        if not cmd:
            print("MENU SELFTEST FAIL: _launch_cmd returned empty")
            pygame.quit()
            return False

        pygame.quit()
        print("MENU SELFTEST PASS")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"MENU SELFTEST FAIL: {exc}")
        return False


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="MENU %(name)s: %(message)s")
    try:
        from .config import _add_flags, from_args
    except ImportError:
        from rtr.remote.config import _add_flags, from_args

    parser = argparse.ArgumentParser(description="RTR remote SDL2 launcher menu")
    _add_flags(parser)
    parser.add_argument("--selftest", action="store_true",
                        help="run the headless self-test (dummy driver, no core)")
    parser.add_argument("--diag", action="store_true",
                        help="print the display diagnostic and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return 0 if selftest() else 1

    config = from_args(args)

    if args.diag:
        return MainMenu(config).diag()

    MainMenu(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
