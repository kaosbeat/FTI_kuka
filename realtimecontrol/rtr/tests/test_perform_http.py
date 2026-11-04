"""Test: the /api/perform HTTP endpoint (rtr/io/httpserver.py).

Covers the live perform-mode config API added in Task 9:
- GET /api/perform returns the on-disk perform.json (read fresh).
- GET /api/perform falls back to the running provider when the file is unreadable.
- POST /api/perform validates + persists the config and submits Cmd.RELOAD_PERFORM.
- POST /api/perform rejects bad configs (400) and leaves the file untouched.

All tests are headless: a real ThreadingHTTPServer on an auto-assigned local port,
no robot, no hardware.

Run directly (no pytest needed)::

    python tests/test_perform_http.py

or under pytest::

    pytest tests/test_perform_http.py
"""

import contextlib
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.core.commands import Cmd  # noqa: E402
from rtr.io.httpserver import HttpServer  # noqa: E402
from rtr.perform import builtin_perform_config  # noqa: E402


class FakeBus:
    """Captures the commands the handler submits (so the test can assert the
    hot-reload command was dispatched)."""

    def __init__(self):
        self.submitted = []

    def submit(self, cmd):
        self.submitted.append(cmd)


@contextlib.contextmanager
def _server(perform_path, bus, provider=None):
    """Start a real HttpServer on an auto-assigned local port; yield the port."""
    srv = HttpServer(
        bus,
        zones_provider=lambda: None,
        sound_provider=lambda: None,
        zones_path="zones.json",
        sound_path="sound.json",
        root=ROOT,
        host="127.0.0.1",
        port=0,
        perform_provider=provider,
        perform_path=perform_path,
    )
    srv.start()
    port = srv._httpd.server_address[1]
    try:
        yield port
    finally:
        srv.stop()


def _request(port, method, data=None):
    """Issue a request to /api/perform; return (status, parsed-json-body)."""
    url = f"http://127.0.0.1:{port}/api/perform"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(
        url, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _tmp_perform(data):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


def test_get_perform_reads_file():
    """GET /api/perform returns the on-disk perform.json (read fresh)."""
    path = _tmp_perform({"internal_bpm": 100.0, "multipliers": [1, 2, 4]})
    try:
        with _server(path, FakeBus()) as port:
            status, body = _request(port, "GET")
            assert status == 200
            assert body["internal_bpm"] == 100.0
            assert body["multipliers"] == [1, 2, 4]
            assert body["default_base_beats"] == 4  # filled from default
    finally:
        os.unlink(path)


def test_get_perform_falls_back_to_provider():
    """When the file is unreadable, GET returns the running provider's config."""
    running = builtin_perform_config()
    running["internal_bpm"] = 75.0
    with _server("/nonexistent/perform.json", FakeBus(), provider=lambda: running) as port:
        status, body = _request(port, "GET")
        assert status == 200
        assert body["internal_bpm"] == 75.0


def test_post_perform_valid_persists_and_reloads():
    """A valid POST writes the file atomically and submits Cmd.RELOAD_PERFORM."""
    path = _tmp_perform(builtin_perform_config())
    bus = FakeBus()
    new_cfg = {"internal_bpm": 120.0, "multipliers": [1, 4, 8]}
    try:
        with _server(path, bus) as port:
            status, body = _request(port, "POST", new_cfg)
            assert status == 200
            assert body.get("ok") is True
            # The file on disk was updated.
            with open(path, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
            assert on_disk["internal_bpm"] == 120.0
            assert on_disk["multipliers"] == [1, 4, 8]
            # The hot-reload command was dispatched to the running core.
            assert any(c.cmd == Cmd.RELOAD_PERFORM for c in bus.submitted), \
                "expected a RELOAD_PERFORM command"
    finally:
        os.unlink(path)


def test_post_perform_invalid_rejected_file_untouched():
    """A bad POST returns 400 and leaves the existing file untouched."""
    path = _tmp_perform({"internal_bpm": 100.0, "multipliers": [1, 2, 4]})
    bus = FakeBus()
    try:
        with _server(path, bus) as port:
            status, body = _request(port, "POST", {"internal_bpm": -5, "multipliers": []})
            assert status == 400
            assert "error" in body
            # The file is unchanged.
            with open(path, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
            assert on_disk["internal_bpm"] == 100.0
            assert on_disk["multipliers"] == [1, 2, 4]
            # No reload command was dispatched.
            assert not any(c.cmd == Cmd.RELOAD_PERFORM for c in bus.submitted)
    finally:
        os.unlink(path)


def test_serves_perform_html():
    """The static handler serves perform.html (the live perform page)."""
    path = _tmp_perform(builtin_perform_config())
    try:
        with _server(path, FakeBus()) as port:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/perform.html") as r:
                assert r.status == 200
                html = r.read().decode("utf-8")
            assert "perform" in html.lower()
    finally:
        os.unlink(path)


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
