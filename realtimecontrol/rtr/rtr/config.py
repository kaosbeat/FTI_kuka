"""Central configuration for the RTR core.

Everything that used to be a magic number or a global in ``tidalkuka.py`` lives here
so the core has one place to read from. Adapters receive a :class:`Config` and never
reach for globals.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    """Runtime configuration for the core and its adapters."""

    # --- robot -----------------------------------------------------------
    # "sim" runs headless; "kuka" talks to the real controller via kukapy.
    robot_kind: str = "sim"
    robot_ip: str = "192.168.10.201"
    robot_port: int = 18735

    # --- engine ----------------------------------------------------------
    # Engine tick rate in Hz. The controller interpolates between targets, so a
    # modest rate is enough and keeps the loop responsive to commands.
    tick_hz: float = 20.0

    # --- MIDI ------------------------------------------------------------
    # Index of the MIDI input port used for control. Sound uses its own out port.
    midi_in_port: Optional[int] = 0
    midi_out_port: Optional[int] = None
    midi_out_device: Optional[str] = None

    # --- WebSocket -------------------------------------------------------
    ws_host: str = "0.0.0.0"
    ws_port: int = 8765

    # --- HTTP server (static pages + /api/zones) -------------------------
    # Serves client.html / editor.html / rtr3d.js / assets and the zone API.
    http_host: str = "0.0.0.0"
    http_port: int = 8766
    enable_http: bool = True

    # --- zone data -------------------------------------------------------
    # On-disk zone/pose table (zones.json). The built-in dicts are the fallback.
    zones_path: str = "zones.json"

    # --- sound data ------------------------------------------------------
    # On-disk MIDI-out mapping (sound.json). The built-in ZONE_NOTES/MODE_CCS
    # are the fallback when the file is missing or corrupt.
    sound_path: str = "sound.json"

    # --- patches data (hydra screen code) --------------------------------
    # On-disk hydra patch table (patches.json), matched to zone/mode/action.
    # The built-in DEFAULT_HYDRA_CODE is the fallback when the file is missing.
    patches_path: str = "patches.json"

    # --- display / camera / sound ---------------------------------------
    # Only enabled when the corresponding hardware is present.
    enable_display: bool = True
    enable_camera: bool = False
    enable_sound: bool = True

    # Camera device index for the vision adapter (unused in sim).
    camera_device: int = 0

    def __post_init__(self):
        if self.robot_kind not in ("sim", "kuka"):
            raise ValueError(f"unknown robot_kind: {self.robot_kind!r}")


def from_args(args) -> Config:
    """Build a :class:`Config` from parsed CLI arguments (see ``main.py``)."""
    cfg = Config()
    for name in ("robot_kind", "robot_ip", "robot_port", "tick_hz",
                "midi_in_port", "midi_out_port", "ws_host", "ws_port",
                "enable_display", "enable_camera", "enable_sound", "camera_device"):
        val = getattr(args, name, None)
        if val is not None:
            setattr(cfg, name, val)
    return cfg
