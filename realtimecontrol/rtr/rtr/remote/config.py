"""Remote client configuration.

Shared by the desktop urwid TUI (:mod:`rtr.remote.tui`) and the SDL2 device
app (:mod:`rtr.remote.sdlapp`). Mirrors the core ``Config`` pattern with its
own defaults and a CLI loader.

The SDL app additionally reads the screen/font/color/``joy_map``/``repo``
fields below; the desktop TUI ignores those (it uses a fixed yellow ``Theme``
and the terminal size).
"""

import argparse
import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Dict, List, Optional

from . import view

ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Default SDL color table (style key -> hex). The desktop TUI ignores this
# (it uses the fixed yellow ``Theme``); the SDL app merges it over its own
# ``DEFAULT_COLORS``. Keys map 1:1 to the style keys produced by
# :mod:`rtr.remote.view`.
DEFAULT_THEME: Dict[str, str] = {
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


@dataclass
class Config:
    """Runtime configuration for the remote client (desktop TUI + SDL app)."""

    # --- core connection -------------------------------------------------
    # Default: connect to the local core WebSocket server.
    core_host: str = "192.168.1.185"
    core_port: int = 8765
    # The core's HTTP server (zone table) runs on its own port (default 8766).
    http_host: Optional[str] = None
    http_port: int = 8766
    # If WebSocket disconnects, poll /api/zones at this interval (seconds).
    http_poll: int = 30

    # --- joystick --------------------------------------------------------
    # Device to read from. Override with --device; "auto" scans ``devices``.
    device: str = "/dev/input/event3"
    # Optional list of devices to auto-scan when ``device`` is "auto".
    devices: Optional[List[str]] = None
    # EV_KEY -> app-key map for the digital buttons (override for a different
    # gamepad). L1/R1 are reserved for page navigation and are not mappable.
    joy_map: Dict[int, str] = field(default_factory=lambda: dict(view.DEFAULT_JOY_MAP))
    # D-pad map: app_key -> [axis_code, sign] (the d-pad is an ANALOG axis,
    # EV_ABS). sign is -1 (negative) or +1 (positive); center = 0.
    dpad_map: Dict[str, List[int]] = field(
        default_factory=lambda: dict(view.DEFAULT_DPAD_MAP))
    # Whether the calibration wizard has been completed (persisted). On the
    # first launch (calibrated is False) the wizard runs automatically; the
    # SETUP "calibrate" row re-runs it regardless of this flag.
    calibrated: bool = False
    # Legacy: the L1+R1 quit chord is gone (home is the only quit). Kept so
    # existing config files that still carry it load without error.
    chord_window_ms: int = 200
    # Single-button quit codes (e.g. 172 = BTN_MODE/home on the Trimui).
    quit_codes: List[int] = field(default_factory=lambda: [172])

    # --- desktop TUI layout --------------------------------------------
    # Fixed height in urwid lines (desktop); keep small to avoid truncation.
    height: int = 4

    # --- SDL screen (device) -------------------------------------------
    # Render size in pixels. The SDL app renders at the larger of this and the
    # panel's native size (e.g. the Trimui's 1024x768). Override with
    # --width / --screen-height to force an exact size.
    width: int = 320
    screen_height: int = 240
    # Design reference height in px: the canvas the ``font_size`` is sized for.
    # Kept SEPARATE from the render size (screen_height) so auto-scaling still
    # works when screen_height is set to the native panel height. The font is
    # scaled by (render_height / design_height), so this must be the small
    # design canvas height (240), not the on-screen height.
    design_height: int = 240
    # Font path (None -> pygame default) and size in px, at the design canvas.
    font: Optional[str] = None
    font_size: int = 16
    # style key -> hex color overrides for the SDL renderer.
    theme: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_THEME))

    # --- PAK -------------------------------------------------------------
    # Fixed repo path on the device (PAK default; harmless elsewhere).
    repo: str = "/storage/rtr-remote"
    # Title the launcher (:mod:`rtr.remote.mainmenu`) shows for this app in its
    # menu. The launcher reads config.displaytitle (falling back to the static
    # title in its APPS registry).
    displaytitle: str = "kukapp"
    # Title the launcher shows for the LED test app (rtr.remote.ledtest).
    # Kept separate from displaytitle so the kukapp title is untouched.
    ledtest_title: str = "ledtest"

    def __post_init__(self):
        if self.height < 1:
            raise ValueError(f"height must be >= 1, got {self.height}")
        # Normalise joy_map keys to int (JSON round-trips them as strings).
        if self.joy_map:
            self.joy_map = {int(k): v for k, v in self.joy_map.items()}

    def to_json(self, path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self.__dict__, f, indent=2)

    @classmethod
    def from_json(cls, path) -> "Config":
        path = Path(path)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Tolerate unknown keys (forward/backward compat): the persisted file
        # may carry non-field attributes written by ``to_json`` (e.g. the
        # resolved ``config`` path, the ``daemon`` flag) or fields from other
        # versions. Only pass known dataclass fields to the constructor.
        field_names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in field_names})


def _add_flags(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add remote-specific CLI flags to the given parser."""
    g = parser.add_argument_group("Remote client")
    g.add_argument("--daemon", action="store_true",
                   help="run as background service (detached)")
    g.add_argument("--device", dest="device",
                   help="input device to read (default: /dev/input/event3)")
    g.add_argument("--height", dest="height", type=int,
                   help="UI height in lines (desktop default: 4)")
    g.add_argument("--core-host", dest="core_host",
                   help="core WebSocket host (default: 127.0.0.1)")
    g.add_argument("--core-port", dest="core_port", type=int,
                   help="core WebSocket port (default: 8765)")
    g.add_argument("--http-host", dest="http_host",
                   help="core HTTP host for the zone table (default: core host)")
    g.add_argument("--http-port", dest="http_port", type=int,
                   help="core HTTP port (default: 8766)")
    g.add_argument("--repo", dest="repo",
                   help="repo path (device default: /storage/rtr-remote)")
    g.add_argument("--chord-window-ms", dest="chord_window_ms", type=int,
                   help="L1+R1 quit-chord window in ms (default: 200)")
    g.add_argument("--config", dest="config", type=str,
                   help="path to remote config file (overrides CLI)")
    return parser


def from_args(args) -> Config:
    """Build a :class:`Config` from parsed CLI arguments.

    A ``--config`` JSON file supplies the base values; explicit CLI flags
    (those actually passed, i.e. non-None) override it. Without ``--config``,
    the dataclass defaults apply.
    """
    path = getattr(args, "config", None)
    cfg = Config.from_json(Path(path)) if path else Config()
    # Keep the resolved path as an attribute for traceability (not a dataclass
    # field; harmless elsewhere).
    cfg.config = path
    for name in ("daemon", "device", "height", "core_host", "core_port",
                 "http_host", "http_port", "repo", "chord_window_ms"):
        val = getattr(args, name, None)
        if val is not None:
            setattr(cfg, name, val)
    return cfg
