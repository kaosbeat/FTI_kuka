"""Pure LED constants and sysfs backend (no SDL/pygame dependency).

Extracted from :mod:`rtr.remote.ledtest` so the desktop TUI can drive the
22 handheld LEDs without pulling in the SDL2 app.

Components:

- LED layout constants (22 names, 8-color palette, sysfs keys).
- :func:`build_frame` -- pure: 22 palette indices -> the exact ``frame_hex``
  string (``RRGGBB`` values, space-separated, trailing space).
- :class:`LedSysfs` -- the sysfs backend: startup snapshot, safe
  ``frame_hex`` write (the ``effect_enable`` 1->0 toggle), and exit restore.
  A memory backend records writes in order for headless tests.

Safe-write rule: two consecutive writes to ``frame_hex`` while
``effect_enable`` is 0 can block the kernel. Every frame write is therefore
bracketed by ``effect_enable=1`` then ``effect_enable=0``. The app snapshots
the relevant sysfs knobs on startup and restores the exact saved values on
exit (not fixed defaults).
"""

import os
from typing import Dict, List, Optional


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

# The sysfs knobs the app snapshots on startup and restores on exit.
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

# Action rows for the LED page (all white / all off / test all / restore).
LED_ACTIONS = ["all white", "all off", "test all", "restore"]


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
        self._mem: Dict[str, str] = {}
        self.writes: List[tuple] = []
        self._snap: Optional[Dict[str, str]] = None

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

    def write_frame(self, frame: str) -> None:
        """Write a ``frame_hex`` frame safely (bracketed by the effect toggle).

        Sequence: ``frame_hex`` -> ``effect_enable=1`` -> ``effect_enable=0``.
        """
        self.write(FRAME_FILE, frame)
        self.write("effect_enable", "1")
        self.write("effect_enable", "0")
