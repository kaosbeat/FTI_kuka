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
        """Read one event; returns (type, code, value) or None on EOF/error."""
        if self._fd is None:
            return None
        try:
            data = os.read(self._fd, EVENT.size)
        except OSError as exc:
            logger.debug("read error: %s", exc)
            return None
        if len(data) != EVENT.size:
            return None
        sec, usec, event_type, code, value = EVENT.unpack(data)
        if event_type in (EVENT_TYPE_KEY, EVENT_TYPE_ABS):
            return (event_type, code, value)
        return None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
