"""Test: the /api/videos HTTP endpoint (rtr/io/httpserver.py).

Covers the video-registry preload endpoint added for the leak-free video
feature:
- GET /api/videos returns the union of referenced video filenames (from the
  zones/actions' pose_videos) plus the files actually on disk (on_disk).
- GET /api/videos degrades gracefully when the zones provider is unavailable
  (videos empty, on_disk still served).

All tests are headless: a real ThreadingHTTPServer on an auto-assigned local
port, no robot, no hardware.

Run directly (no pytest needed)::

    python tests/test_videos_http.py

or under pytest::

    pytest tests/test_videos_http.py
"""

import contextlib
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.io.httpserver import HttpServer  # noqa: E402
from rtr.state.zones import Zones  # noqa: E402


class FakeBus:
    """Captures the commands the handler submits (the videos endpoint submits
    none, but the constructor requires a bus)."""

    def __init__(self):
        self.submitted = []

    def submit(self, cmd):
        self.submitted.append(cmd)


@contextlib.contextmanager
def _server(zones_provider, root, bus):
    """Start a real HttpServer on an auto-assigned local port; yield the port."""
    srv = HttpServer(
        bus,
        zones_provider=zones_provider,
        sound_provider=lambda: None,
        zones_path="zones.json",
        sound_path="sound.json",
        root=root,
        host="127.0.0.1",
        port=0,
    )
    srv.start()
    port = srv._httpd.server_address[1]
    try:
        yield port
    finally:
        srv.stop()


def _get_videos(port):
    """Issue GET /api/videos; return (status, parsed-json-body)."""
    url = f"http://127.0.0.1:{port}/api/videos"
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _tmp_root_with_videos(files):
    """A temp root with an assets/videos/ dir containing the given filenames."""
    d = tempfile.mkdtemp()
    vdir = os.path.join(d, "assets", "videos")
    os.makedirs(vdir)
    for f in files:
        with open(os.path.join(vdir, f), "wb") as fh:
            fh.write(b"")
    return d


def test_get_videos_returns_union_and_on_disk():
    """GET /api/videos returns the union of referenced videos + on-disk files."""
    zones = Zones({
        "zoneA": {"actions": {"act1": {"pose_videos": ["v1.mp4", "v2.mp4", None]}}},
        "zoneB": {"actions": {"act2": {"pose_videos": ["v3.mp4", "v1.mp4"]}}},
    })
    root = _tmp_root_with_videos(["v1.mp4", "v2.mp4"])
    try:
        with _server(lambda: zones, root, FakeBus()) as port:
            status, body = _get_videos(port)
            assert status == 200
            # The union of referenced videos (deduped, None skipped).
            assert set(body["videos"]) == {"v1.mp4", "v2.mp4", "v3.mp4"}
            # The files actually on disk (so the client marks the rest missing).
            assert set(body["on_disk"]) == {"v1.mp4", "v2.mp4"}
    finally:
        shutil.rmtree(root)


def test_get_videos_handles_missing_zones():
    """When the zones provider is unavailable, videos is empty but on_disk is served."""
    root = _tmp_root_with_videos(["a.mp4"])

    def _raise_zones():
        raise RuntimeError("no zones")

    try:
        with _server(_raise_zones, root, FakeBus()) as port:
            status, body = _get_videos(port)
            assert status == 200
            assert body["videos"] == []
            assert body["on_disk"] == ["a.mp4"]
    finally:
        shutil.rmtree(root)


def test_get_videos_empty_when_no_videos_dir():
    """When assets/videos/ is absent, on_disk is empty (no crash)."""
    zones = Zones({"zoneA": {"actions": {"act1": {"pose_videos": ["v1.mp4"]}}}})
    root = tempfile.mkdtemp()  # no assets/videos/ dir
    try:
        with _server(lambda: zones, root, FakeBus()) as port:
            status, body = _get_videos(port)
            assert status == 200
            assert body["videos"] == ["v1.mp4"]
            assert body["on_disk"] == []
    finally:
        shutil.rmtree(root)


def main():
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {name}: {e}")
    if failures:
        print(f"\n{failures} TEST(S) FAILED")
        return 1
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
