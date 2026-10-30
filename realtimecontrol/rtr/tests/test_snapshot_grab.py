"""Tests for the core-side snapshot grabber (``rtr/camera/snapshot_grab.py``).

The grabber pulls the RPi's latest camera frame (a JPEG over HTTP) and writes it
to a local file (``assets/latest.jpg``) so the render page can reference
``/assets/latest.jpg``. These tests run **without a live RPi**: the ``urllib``
GET is shadowed (a fake response object), so no network is touched. Covered:

- :meth:`SnapshotGrabber.grab` writes the fetched bytes to the target path.
- The ``path`` property is the absolute on-disk path.
- The grab is **non-blocking**: it spawns a daemon thread (the call returns
  before the fetch completes).
- The **in-flight debounce guard** drops a grab while one is running (returns
  ``False``), and re-arms once the first grab finishes.
- A failed fetch (dead URL) is swallowed (no raise) and leaves no file behind.
- The on-disk write is **atomic**: the target path only ever holds a complete
  file (the temp file is moved into place with ``os.replace``).

Run directly (no pytest needed)::

    python tests/test_snapshot_grab.py

or under pytest::

    pytest tests/test_snapshot_grab.py
"""

import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.camera import snapshot_grab as sg
from rtr.camera.snapshot_grab import SnapshotGrabber


class _FakeResp:
    """A context-managed response object that returns a fixed byte payload."""

    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(result):
    """Shadow ``urllib.request.urlopen`` in the grabber module.

    ``result`` is a callable: ``(url) -> _FakeResp`` on success, or a
    ``BaseException`` class to raise. Returns a restore function.
    """
    saved = sg.urllib.request.urlopen

    def fake(url, timeout=None):
        if isinstance(result, type) and issubclass(result, BaseException):
            raise result("boom")
        return result(url)

    sg.urllib.request.urlopen = fake

    def restore():
        sg.urllib.request.urlopen = saved

    return restore


def _wait_until(cond, timeout=5.0):
    """Poll ``cond`` until it returns True or the timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_grab_writes_fetched_bytes_to_path():
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "latest.jpg")
        jpeg = b"fake-jpeg-bytes"
        restore = _patch_urlopen(lambda url: _FakeResp(jpeg))
        try:
            grabber = SnapshotGrabber(url="http://rpi:8080/?action=snapshot",
                                        path=target)
            assert grabber.grab() is True
            assert _wait_until(lambda: os.path.exists(target))
            with open(target, "rb") as f:
                assert f.read() == jpeg
        finally:
            restore()


def test_path_property_is_absolute():
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "latest.jpg")
        grabber = SnapshotGrabber(url="http://rpi:8080/", path=target)
        assert grabber.path == os.path.abspath(target)


def test_grab_is_non_blocking():
    # The fetch runs on a daemon thread: grab() returns immediately, before the
    # fetch completes. We make the fetch slow and assert grab() returns fast.
    started = threading.Event()

    def slow(url):
        started.set()
        time.sleep(0.2)
        return _FakeResp(b"late")

    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "latest.jpg")
        restore = _patch_urlopen(slow)
        try:
            grabber = SnapshotGrabber(url="http://rpi:8080/", path=target)
            t0 = time.time()
            assert grabber.grab() is True
            elapsed = time.time() - t0
            assert elapsed < 0.2  # returned before the (slow) fetch finished
            assert started.wait(2.0)  # the thread did start
            assert _wait_until(lambda: os.path.exists(target))
        finally:
            restore()


def test_in_flight_guard_drops_concurrent_grab():
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "latest.jpg")
        in_flight = threading.Event()

        def slow(url):
            in_flight.set()
            time.sleep(0.3)
            return _FakeResp(b"one")

        restore = _patch_urlopen(slow)
        try:
            grabber = SnapshotGrabber(url="http://rpi:8080/", path=target)
            assert grabber.grab() is True  # the first grab starts
            assert in_flight.wait(2.0)
            assert grabber.grab() is False  # dropped: one is in flight
            assert _wait_until(lambda: os.path.exists(target))
            # Re-armed once the first grab finishes.
            assert _wait_until(lambda: grabber.grab() is True)
        finally:
            restore()


def test_failed_fetch_is_swallowed_and_leaves_no_file():
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "latest.jpg")
        restore = _patch_urlopen(OSError)
        try:
            grabber = SnapshotGrabber(url="http://dead:8080/", path=target)
            assert grabber.grab() is True  # starts (no raise)
            # The fetch thread raises and swallows; no file is written.
            assert _wait_until(lambda: not grabber._in_flight)
            assert not os.path.exists(target)
        finally:
            restore()


def test_write_is_atomic_no_leftover_temp_files():
    # A write produces a complete file at the target path and leaves no
    # temp files behind (the temp file is moved into place with os.replace).
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "latest.jpg")
        grabber = SnapshotGrabber(url="http://rpi:8080/", path=target)
        grabber._write(b"new-complete")
        with open(target, "rb") as f:
            assert f.read() == b"new-complete"
        # No leftover temp files in the directory (only latest.jpg remains).
        entries = os.listdir(d)
        assert entries == ["latest.jpg"]


def test_write_creates_parent_directory():
    # The target's parent directory is created if it does not yet exist.
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "assets", "latest.jpg")
        grabber = SnapshotGrabber(url="http://rpi:8080/", path=target)
        grabber._write(b"jpeg")
        assert os.path.exists(target)
        with open(target, "rb") as f:
            assert f.read() == b"jpeg"


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
