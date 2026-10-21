"""Snapshot HTTP server: serve the latest pipeline frame as a JPEG.

The editor's "real camera" snapshot is an ``<img>`` pointed at the RPi
(``http://<rpi-ip>:8080/?action=snapshot``). This module is the RPi-side
endpoint: it keeps the latest BGR frame from the pipeline and serves it over
plain HTTP, so the browser can grab a still from the physical camera.

Design notes:
- The handler **ignores the path and query string** entirely — every GET
  returns the latest frame. That makes the editor's cache-buster query
  (``&_=<ts>``) work as-is, and any URL shape (``/?action=snapshot``,
  ``/snapshot``, ``/``) behaves the same.
- ``cv2`` is imported **lazily** inside :meth:`SnapshotServer._jpeg` so this
  module imports cleanly on machines without OpenCV (tests, the core).
- The server runs in a daemon thread; ``start()``/``stop()`` manage its
  lifetime. Port ``0`` binds an ephemeral port (useful for tests).
"""

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

logger = logging.getLogger(__name__)


def _make_handler(snapshot: "SnapshotServer"):
    """Build a request handler that serves ``snapshot``'s latest frame."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "RtrSnapshot"

        def do_GET(self):
            jpeg = snapshot._jpeg()
            if jpeg is None:
                self._respond(503, b"no frame yet")
                return
            self._respond(200, jpeg, image=True)

        def _respond(self, code: int, body: bytes, image: bool = False) -> None:
            self.send_response(code)
            if image:
                self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            # The <img> cache-busters with a query param, but tell the browser
            # not to cache regardless (the frame changes every pipeline tick).
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            # Silence per-request logging (the pipeline already logs stats).
            pass

    return Handler


class SnapshotServer:
    """Serves the latest camera frame as a JPEG over HTTP.

    - :meth:`set_frame` stores the latest BGR ndarray (thread-safe).
    - :meth:`start` spawns a daemon thread running a ``ThreadingHTTPServer``.
    - :meth:`stop` shuts the server down.
    - Any GET returns the latest frame as ``image/jpeg`` (503 until the first
      frame arrives).
    """

    def __init__(self, port: int = 8080, host: str = "0.0.0.0"):
        self._port = port
        self._host = host
        self._lock = threading.Lock()
        self._frame = None  # latest BGR ndarray
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- frame storage (thread-safe) ---------------------------------------
    def set_frame(self, frame) -> None:
        """Store the latest BGR frame (called from the pipeline thread)."""
        with self._lock:
            self._frame = frame

    def frame(self):
        """Return the latest stored frame (or None)."""
        with self._lock:
            return self._frame

    # -- JPEG encoding (cv2 is lazy: only needed when a frame is served) ----
    def _jpeg(self) -> Optional[bytes]:
        """Encode the latest frame as JPEG bytes (None if no frame yet)."""
        with self._lock:
            frame = self._frame
        if frame is None:
            return None
        import cv2
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            return None
        return buf.tobytes()

    # -- lifecycle ----------------------------------------------------------
    @property
    def port(self) -> int:
        """The bound port (reflects the ephemeral port when started with 0)."""
        return self._port

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> None:
        """Start the HTTP server in a daemon thread (idempotent)."""
        if self._server is not None:
            return
        self._server = ThreadingHTTPServer((self._host, self._port),
                                           _make_handler(self))
        # Reflect the actually-bound port (matters for the ephemeral case).
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()
        logger.info("snapshot server listening on %s:%d", self._host, self._port)

    def stop(self) -> None:
        """Stop the HTTP server (idempotent)."""
        if self._server is None:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        finally:
            if self._thread is not None:
                self._thread.join(timeout=2.0)
            self._thread = None
            self._server = None
        logger.info("snapshot server stopped")
