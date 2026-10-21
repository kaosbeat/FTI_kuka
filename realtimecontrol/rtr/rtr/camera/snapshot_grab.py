"""Core-side snapshot grabber: pull the RPi's latest frame into a local JPEG.

The camera frame lives on the **RPi** (see :mod:`snapshot` — the
``SnapshotServer`` that serves the latest pipeline frame at ``:8080``). The
**core** (this machine) wants that frame as a local file (``assets/latest.jpg``)
so the render page can reference ``/assets/latest.jpg``. This module is the
core-side client: it fetches the JPEG over HTTP and writes it to disk atomically.

Design notes:
- :meth:`SnapshotGrabber.grab` is **non-blocking**: it spawns a daemon thread
  that does the ``urllib`` GET, so a slow RPi never stalls the engine tick.
- An **in-flight debounce guard** drops a grab while one is already running, so
  rapid-fire triggers (e.g. a MIDI key held down) don't pile up concurrent
  fetches of the same frame.
- The on-disk write is **atomic**: the JPEG is written to a temp file in the
  target directory and moved into place with ``os.replace``, so a reader never
  sees a half-written file.
- ``urllib`` is used (no third-party deps); the request carries a short timeout
  so a dead RPi can't hang the thread indefinitely.
"""

import logging
import os
import tempfile
import threading
import urllib.request

logger = logging.getLogger(__name__)


class SnapshotGrabber:
    """Fetches the RPi's latest frame as a JPEG and writes it to a local file.

    - :meth:`grab` is non-blocking (spawns a daemon thread per grab).
    - The in-flight guard drops a grab while a previous one is still running.
    - The on-disk write is atomic (temp file + ``os.replace``).
    """

    def __init__(self, url: str, path: str, timeout: float = 5.0):
        self._url = url
        self._path = os.path.abspath(path)
        self._timeout = timeout
        self._lock = threading.Lock()
        self._in_flight = False

    @property
    def path(self) -> str:
        """The absolute on-disk path the JPEG is written to."""
        return self._path

    def grab(self) -> bool:
        """Start a non-blocking fetch of the latest frame.

        Returns ``True`` if a grab was started, ``False`` if one was already in
        flight (the debounce guard). The fetch itself runs on a daemon thread.
        """
        with self._lock:
            if self._in_flight:
                return False
            self._in_flight = True
        threading.Thread(target=self._fetch, daemon=True).start()
        return True

    def _fetch(self) -> None:
        """Fetch the JPEG and write it to disk atomically (runs on a thread)."""
        try:
            with urllib.request.urlopen(self._url, timeout=self._timeout) as resp:
                data = resp.read()
            self._write(data)
        except Exception as exc:  # noqa: BLE001 - a dead RPi must not kill the core
            logger.warning("snapshot grab failed: %s", exc)
            print(f"[snapshot] grab failed: {exc}")
        finally:
            with self._lock:
                self._in_flight = False

    def _write(self, data: bytes) -> None:
        """Write ``data`` to the target path atomically (temp file + replace)."""
        directory = os.path.dirname(self._path)
        os.makedirs(directory, exist_ok=True)
        # A temp file in the same directory (so os.replace is an atomic rename).
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".latest_", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, self._path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
