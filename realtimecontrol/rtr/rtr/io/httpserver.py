"""HTTP server: static pages + the zone API.

This is a small stdlib-only server (``ThreadingHTTPServer``) that runs in a daemon
thread alongside the WebSocket server. It exists because browsers block loading local
assets (three.js, the environment GLB, shared JS) from a ``file://`` page, so the
control pages are best served over HTTP.

It serves two kinds of requests:

- **Static files** from the ``realtimecontrol/rtr/`` directory (path-traversal safe):
  ``/`` → ``client.html``, plus ``/client.html``, ``/editor.html``, ``/rtr3d.js`` and
  ``/assets/<file>``.
- **The zone API**:
  - ``GET /api/zones``  → the current zone data table (read fresh from ``zones.json``,
    falling back to the running :class:`Zones` + built-in poses if the file is bad).
  - ``POST /api/zones`` → validate the full table; on success write it atomically to
    ``zones.json`` and submit a ``RELOAD_ZONES`` command so the running core hot-reloads.
    On validation failure the file is untouched and a 400 is returned.
- **The sound API**:
  - ``GET /api/sound``  → the current sound mapping (read fresh from ``sound.json``,
    falling back to the running mapping table if the file is bad).
  - ``POST /api/sound`` → validate the mapping; on success write it atomically to
    ``sound.json`` and submit a ``RELOAD_SOUND`` command so the running core hot-reloads.
    On validation failure the file is untouched and a 400 is returned.
- **The limits API**:
  - ``GET /api/limits`` → the per-axis hardware limits (a static constant from the
    kr60ha xacro). Read-only; the hardware limits are fixed by the robot's mechanics.

Only the standard library is used (no new dependencies).
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..core.bus import StateBus
from ..core.commands import Cmd, Command
from ..sound import load_sound_data, validate_sound_data
from ..state.zones import (
    HARDWARE_LIMITS,
    LIN_POSES,
    POSES,
    load_state_data,
    validate_state_data,
)

# MIME types for the static files we serve.
_CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json; charset=utf-8",
    ".stl": "model/stl",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}


class HttpServer:
    """Serves the control pages + the zone API on a daemon thread."""

    def __init__(self, bus: StateBus, zones_provider, sound_provider,
                 zones_path: str, sound_path: str, root: str,
                 host: str = "0.0.0.0", port: int = 8766,
                 enabled: bool = True):
        self.bus = bus
        self.zones_provider = zones_provider
        self.sound_provider = sound_provider
        self.zones_path = os.path.abspath(zones_path)
        self.sound_path = os.path.abspath(sound_path)
        self.root = os.path.abspath(root)
        self.host = host
        self.port = port
        self.enabled = enabled
        self._httpd = None

    def start(self) -> None:
        if not self.enabled:
            return
        handler = _make_handler(self)
        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
            self._httpd.daemon_threads = True  # handler threads won't block shutdown
        except OSError as exc:
            print(f"[http] port {self.port} busy ({exc}); HTTP server disabled")
            self.enabled = False
            return
        t = threading.Thread(target=self._httpd.serve_forever)
        t.daemon = True
        t.start()
        print(f"[http] listening on http://{self.host}:{self.port}")

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
            self._httpd = None


def _make_handler(server: HttpServer):
    """Build a request handler class bound to this server instance."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "RTRHTTP/1.0"

        # Silence the default per-request logging (we print our own startup line).
        def log_message(self, format, *args):
            pass

        # ------------------------------------------------------------------
        # Routing.
        # ------------------------------------------------------------------
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/zones":
                self._get_zones()
            elif path == "/api/sound":
                self._get_sound()
            elif path == "/api/limits":
                self._get_limits()
            elif self._is_static(path):
                self._serve_static(path)
            else:
                self._send_text(404, "not found")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/zones":
                self._post_zones()
            elif path == "/api/sound":
                self._post_sound()
            else:
                self._send_text(404, "not found")

        # ------------------------------------------------------------------
        # Zone API.
        # ------------------------------------------------------------------
        def _get_zones(self):
            try:
                data = load_state_data(server.zones_path)
            except (OSError, ValueError):
                # File missing/corrupt: fall back to the running zones + built-in poses.
                data = {"zones": server.zones_provider().table(),
                        "poses": POSES, "lin_poses": LIN_POSES}
            self._send_json(200, data)

        def _post_zones(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else b""
            try:
                data = json.loads(body)
                validate_state_data(data)
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            # Atomic write: dump to a temp file, then replace.
            tmp = server.zones_path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, server.zones_path)
            except OSError as exc:
                self._send_json(500, {"error": f"could not write zones: {exc}"})
                return
            # Ask the running core to hot-reload the new table.
            server.bus.submit(Command(cmd=Cmd.RELOAD_ZONES, payload={}))
            self._send_json(200, {"ok": True})

        # ------------------------------------------------------------------
        # Sound API.
        # ------------------------------------------------------------------
        def _get_sound(self):
            try:
                data = load_sound_data(server.sound_path)
            except (OSError, ValueError):
                # File missing/corrupt: fall back to the running mapping table.
                data = server.sound_provider()
            self._send_json(200, data)

        def _post_sound(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else b""
            try:
                data = json.loads(body)
                validate_sound_data(data)
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            # Atomic write: dump to a temp file, then replace.
            tmp = server.sound_path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, server.sound_path)
            except OSError as exc:
                self._send_json(500, {"error": f"could not write sound: {exc}"})
                return
            # Ask the running core to hot-reload the new mapping.
            server.bus.submit(Command(cmd=Cmd.RELOAD_SOUND, payload={}))
            self._send_json(200, {"ok": True})

        # ------------------------------------------------------------------
        # Limits API.
        # ------------------------------------------------------------------
        def _get_limits(self):
            # Static constant from the kr60ha xacro; no POST (the hardware limits
            # are fixed by the robot's mechanics, not editable).
            self._send_json(200, {
                "axes": ["A1", "A2", "A3", "A4", "A5", "A6"],
                "hardware_limits": HARDWARE_LIMITS,
            })

        # ------------------------------------------------------------------
        # Static files.
        # ------------------------------------------------------------------
        def _is_static(self, path) -> bool:
            if path == "/":
                return True
            return (path.endswith(".html") or path.endswith(".js") or
                    path.endswith(".json") or path.startswith("/assets/"))

        def _serve_static(self, path):
            rel = "client.html" if path == "/" else path.lstrip("/")
            base = os.path.realpath(server.root)
            full = os.path.realpath(os.path.join(server.root, rel))
            if not (full == base or full.startswith(base + os.sep)):
                self._send_text(403, "forbidden")
                return
            if not os.path.isfile(full):
                self._send_text(404, "not found")
                return
            ctype = _CTYPES.get(os.path.splitext(full)[1].lower(),
                                 "application/octet-stream")
            with open(full, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # ------------------------------------------------------------------
        # Response helpers.
        # ------------------------------------------------------------------
        def _send_json(self, code: int, obj) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, code: int, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler
