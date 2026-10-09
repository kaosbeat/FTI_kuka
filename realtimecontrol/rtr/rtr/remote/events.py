"""Joystick events abstraction.

Wraps raw Linux input events from ``/dev/input/event*`` devices. The module
exposes a :class:`Joystick` class that reads from any device (configurable via
CLI), returning events as tuples ``(event_type, code, value)``.

This mirrors the pattern from ``rtr/remote/joystick_test.py`` but with proper
error handling and logging consistent with core modules.
"""

import logging
import os
import struct
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Linux input_event: timeval (8 bytes) + type (2) + code (2) + value (4)
EVENT = struct.Struct("llHHI")
EVENT_TYPE_KEY = 1
EVENT_TYPE_ABS = 3


class Joystick:
    """Read raw input events from a Linux joystick device.

    Opens the device read-only and yields events until EOF or error.
    """

    def __init__(self, device: str) -> None:
        self.device = device
        self._fd: Optional[int] = None

    def open(self) -> None:
        if self._fd is not None:
            return
        self._fd = os.open(self.device, os.O_RDONLY)
        logger.debug("opened %s", self.device)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            logger.debug("closed %s", self.device)

    def read(self) -> Optional[Tuple[int, int, int]]:
        """Read the next KEY/ABS event; returns (type, code, value) or None on EOF/error.

        Non-KEY/ABS events (e.g. EV_SYN, type 0) are consumed and skipped: they
        are part of a normal input batch, not EOF. Treating them as EOF made the
        reader reopen the device after every button press (a spurious ``EOF`` on
        each keypress). A real 0-byte read (the device dropped) still returns None.
        """
        if self._fd is None:
            return None
        while True:
            try:
                data = os.read(self._fd, EVENT.size)
            except OSError as exc:
                logger.debug("read error: %s", exc)
                return None
            if len(data) != EVENT.size:
                if len(data) == 0:      # real EOF: the device dropped
                    return None
                continue                # short read: retry
            sec, usec, event_type, code, value = EVENT.unpack(data)
            if event_type in (EVENT_TYPE_KEY, EVENT_TYPE_ABS):
                return (event_type, code, value)
            # EV_SYN / other: consume, keep reading for the next real event.

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
