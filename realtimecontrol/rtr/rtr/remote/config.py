"""Remote TUI configuration.

Mirrors the core :class:`~rtr.config.Config` pattern with its own defaults and
a CLI loader.
"""

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent.parent


@dataclass
class Config:
    """Runtime configuration for the remote TUI client."""

    # --- core connection -------------------------------------------------
    # Default: connect to the local core WebSocket server.
    core_host: str = "127.0.0.1"
    core_port: int = 8765
    # The core's HTTP server (zone table) runs on its own port (default 8766).
    http_host: Optional[str] = None
    http_port: int = 8766

    # --- joystick --------------------------------------------------------
    # Device to read from. Override with --device if needed.
    device: str = "/dev/input/event3"

    # --- TUI layout ------------------------------------------------------
    # Fixed height in urwid lines; keep small to avoid truncation.
    height: int = 4

    # --- theme -----------------------------------------------------------
    # Simple palette and font for urwid widgets.
    theme: dict = field(default_factory=lambda: {
        "colors": {
            "normal": "#111111",
            "bright": "#cccccc",
            "highlight": "#4444aa",
            "reverse": "#4444aa",
            "error": "#ff3333",
            "warning": "#ffaa00",
            "success": "#33ff33",
        },
        "font": "monospace",
    })

    # --- HTTP fallback ---------------------------------------------------
    # If WebSocket disconnects, poll /api/zones at this interval (seconds).
    http_poll: int = 30

    def __post_init__(self):
        if self.height < 1:
            raise ValueError(f"height must be >= 1, got {self.height}")

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self.__dict__, f, indent=2)

    @classmethod
    def from_json(cls, path: Path) -> "Config":
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**data)


def _add_flags(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add remote-specific CLI flags to the given parser."""
    g = parser.add_argument_group("Remote TUI")
    g.add_argument("--daemon", action="store_true",
                   help="run as background service (detached)")
    g.add_argument("--device", dest="device",
                   help="input device to read (default: /dev/input/event3)")
    g.add_argument("--height", dest="height", type=int,
                   help="UI height in lines (default: 4)")
    g.add_argument("--core-host", dest="core_host",
                   help="core WebSocket host (default: 127.0.0.1)")
    g.add_argument("--core-port", dest="core_port", type=int,
                   help="core WebSocket port (default: 8765)")
    g.add_argument("--http-host", dest="http_host",
                   help="core HTTP host for the zone table (default: core host)")
    g.add_argument("--http-port", dest="http_port", type=int,
                   help="core HTTP port for the zone table (default: 8766)")
    g.add_argument("--config", dest="config", type=str,
                   help="path to remote config file (overrides CLI)")
    return parser


def from_args(args) -> Config:
    """Build a :class:`Config` from parsed CLI arguments (see ``main.py``)."""
    cfg = Config()
    for name in ("daemon", "device", "height", "core_host", "core_port",
                 "http_host", "http_port", "config"):
        val = getattr(args, name, None)
        if val is not None:
            setattr(cfg, name, val)
    return cfg
