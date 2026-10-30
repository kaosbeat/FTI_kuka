"""Tests for the RPi snapshot HTTP server (``rtr/camera/snapshot.py``).

The snapshot server serves the latest pipeline frame as a JPEG over plain HTTP,
so the editor's "real camera" snapshot (an ``<img>`` pointed at
``http://<rpi>:8080/?action=snapshot``) can grab a still from the physical
camera.

These tests run **without OpenCV**: ``SnapshotServer._jpeg`` is shadowed on the
instance (the real one lazy-imports cv2, which is absent on the dev machine),
so cv2 is never touched. Covered:

- GET returns 200 + ``image/jpeg`` + the encoded body once a frame is set.
- GET returns 503 before the first frame arrives.
- The handler ignores the path and query string (the editor's cache-buster
  ``?action=snapshot&_=<ts>`` works as-is).
- Port 0 binds an ephemeral port; ``.port`` reflects the bound port.
- ``stop()`` is idempotent.
- ``set_frame``/``frame`` are thread-safe (lock-protected).

Run directly (no pytest needed)::

    python tests/test_camera_snapshot.py

or under pytest::

    pytest tests/test_camera_snapshot.py
"""

import http.client
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.camera.snapshot import SnapshotServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get(port: int, path: str):
    """Issue a GET; return (status, headers, body)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def _make_server(jpeg: bytes):
    """Create an ephemeral-port server serving a fixed JPEG payload (no cv2).

    The instance ``_jpeg`` is shadowed to mimic the real one: it returns
    ``None`` until a frame is set (so the handler's 503 path is exercised),
    otherwise the fixed payload — without importing cv2.
    """
    server = SnapshotServer(port=0)

    def fake_jpeg():
        return jpeg if server.frame() is not None else None

    server._jpeg = fake_jpeg
    server.start()
    return server


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_serves_frame_200_jpeg():
    jpeg = b"fake-jpeg-bytes"
    server = _make_server(jpeg)
    try:
        server.set_frame(object())  # the frame is opaque; _jpeg is shadowed
        status, headers, body = _get(server.port, "/?action=snapshot&_=")
        assert status == 200
        assert headers.get("Content-Type") == "image/jpeg"
        assert body == jpeg
    finally:
        server.stop()


def test_no_frame_returns_503():
    server = _make_server(b"")
    try:
        status, headers, body = _get(server.port, "/?action=snapshot&_=")
        assert status == 503
        assert body == b"no frame yet"
    finally:
        server.stop()


def test_path_and_query_are_ignored():
    jpeg = b"fake-jpeg-bytes"
    server = _make_server(jpeg)
    try:
        server.set_frame(object())
        # The editor's exact cache-buster shape, plus other shapes: all identical.
        for path in ("/?action=snapshot&_=123",
                     "/?action=snapshot",
                     "/snapshot",
                     "/"):
            status, headers, body = _get(server.port, path)
            assert status == 200
            assert headers.get("Content-Type") == "image/jpeg"
            assert body == jpeg
    finally:
        server.stop()


def test_ephemeral_port_is_reported():
    server = _make_server(b"")
    try:
        assert server.port > 0  # port=0 means ephemeral; .port reflects the bind
    finally:
        server.stop()


def test_stop_is_idempotent():
    server = _make_server(b"")
    server.stop()
    server.stop()  # second stop is a no-op, must not raise
    assert not server.running


def test_set_frame_is_thread_safe():
    """set_frame stores the latest frame; frame() returns it (lock-protected)."""
    server = SnapshotServer(port=0)
    assert server.frame() is None
    marker = object()
    server.set_frame(marker)
    assert server.frame() is marker
    # Concurrent access must not raise and must not corrupt the stored frame.
    errors = []

    def worker():
        try:
            for _ in range(1000):
                server.set_frame(object())
                server.frame()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import traceback
    failed = 0
    for name, obj in list(globals().items()):
        if name.startswith("test_") and callable(obj):
            try:
                obj()
                print(f"  PASS: {name}")
            except Exception:
                failed += 1
                print(f"  FAIL: {name}")
                traceback.print_exc()
    print(f"\n{failed} failed")
