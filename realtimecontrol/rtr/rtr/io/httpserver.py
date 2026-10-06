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
  - ``GET /api/sound/ports`` → the available MIDI out ports (for the output-device
    picker in the editor's MIDI tab).
- **The limits API**:
  - ``GET /api/limits`` → the per-axis hardware limits (a static constant from the
    kr60ha xacro). Read-only; the hardware limits are fixed by the robot's mechanics.
- **The patches API** (hydra screen code):
  - ``GET /api/patches`` → the current patches table (read fresh from ``patches.json``,
    falling back to the running table / built-in default if the file is bad).
  - ``POST /api/patches`` → validate the table; on success write it atomically to
    ``patches.json`` and submit a ``RELOAD_PATCHES`` command so the running core hot-reloads.
    On validation failure the file is untouched and a 400 is returned.
- **The screen API**:
  - ``GET /api/screen`` → the hydra patch (JS) rendered on the tool's screen, i.e. the
    ``default`` entry of the patches table.
- **The brain API** (decision config: hunt-loop parameters):
  - ``GET /api/brain`` → the current brain decision config (read fresh from ``brain.json``,
    falling back to the running config / built-in default if the file is bad).
  - ``POST /api/brain`` → validate the config; on success write it atomically to
    ``brain.json`` and submit a ``RELOAD_BRAIN`` command so the running core hot-reloads.
    On validation failure the file is untouched and a 400 is returned.
- **The MIDI-in API** (learned MIDI-in mapping):
  - ``GET /api/midi`` → the current MIDI-in mapping (read fresh from ``midi.json``,
    falling back to the running table if the file is bad).
  - ``POST /api/midi`` → validate the mapping; on success write it atomically to
    ``midi.json`` and submit a ``RELOAD_MIDI`` command so the running core hot-reloads.
    On validation failure the file is untouched and a 400 is returned.
  - ``GET /api/midi/ports`` → the available MIDI in ports (for the input-device picker).
  - ``POST /api/midi/learn`` → arm (``{"action": "start"}``) or disarm
    (``{"action": "stop"}``) the learn capture on the running :class:`MidiInput`.
    A start may target a zone command (``zone``), a zone action (``zone`` +
    ``action_name``), or a global navigation command (``nav``).

Only the standard library is used (no new dependencies).
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..core.bus import StateBus
from ..core.commands import Cmd, Command
from ..brain import (
    builtin_brain_config,
    load_brain_config,
    validate_brain_config,
)
from ..patches import (
    DEFAULT_HYDRA_CODE,
    load_patches_data,
    validate_patches_data,
)
from ..sound import load_sound_data, validate_sound_data
from .midi import load_midi_data, validate_midi_data
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
                  enabled: bool = True, screen_code: str = None,
                  patches_provider=None, patches_path: str = None,
                  midi_provider=None, midi_path: str = None, midi=None,
                  sound=None, brain_provider=None, brain_path: str = None):
        self.bus = bus
        self.zones_provider = zones_provider
        self.sound_provider = sound_provider
        self.zones_path = os.path.abspath(zones_path)
        self.sound_path = os.path.abspath(sound_path)
        self.patches_path = os.path.abspath(patches_path) if patches_path else None
        self.patches_provider = patches_provider
        self.midi_path = os.path.abspath(midi_path) if midi_path else None
        self.midi_provider = midi_provider
        # The running MidiInput (for the learn + list-ports actions); may be None.
        self.midi = midi
        # The running Sound (for the list-out-ports action); may be None.
        self.sound = sound
        # The brain decision config (data-driven hunt-loop parameters).
        self.brain_provider = brain_provider
        self.brain_path = os.path.abspath(brain_path) if brain_path else None
        self.root = os.path.abspath(root)
        self.host = host
        self.port = port
        self.enabled = enabled
        # The hydra patch (JS) rendered on the tool's screen; served at /api/screen.
        self.screen_code = screen_code
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
            elif path == "/api/sound/ports":
                self._get_sound_ports()
            elif path == "/api/patches":
                self._get_patches()
            elif path == "/api/limits":
                self._get_limits()
            elif path == "/api/screen":
                self._get_screen()
            elif path == "/api/midi":
                self._get_midi()
            elif path == "/api/midi/ports":
                self._get_midi_ports()
            elif path == "/api/brain":
                self._get_brain()
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
            elif path == "/api/patches":
                self._post_patches()
            elif path == "/api/midi":
                self._post_midi()
            elif path == "/api/midi/learn":
                self._post_midi_learn()
            elif path == "/api/brain":
                self._post_brain()
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

        def _get_sound_ports(self):
            if server.sound is None:
                self._send_json(200, {"ports": []})
                return
            self._send_json(200, {"ports": server.sound.list_out_ports()})

        # ------------------------------------------------------------------
        # Patches API (hydra screen code).
        # ------------------------------------------------------------------
        def _patches_data(self) -> dict:
            """The current patches table: read fresh from the file, fall back to
            the running table (or the built-in default) if the file is bad."""
            if server.patches_path:
                try:
                    return load_patches_data(server.patches_path)
                except (OSError, ValueError):
                    pass
            if server.patches_provider is not None:
                return server.patches_provider()
            return {"default": server.screen_code or DEFAULT_HYDRA_CODE,
                    "zones": {}, "modes": {}, "actions": {}}

        def _get_patches(self):
            self._send_json(200, self._patches_data())

        def _post_patches(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else b""
            try:
                data = json.loads(body)
                validate_patches_data(data)
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            # Atomic write: dump to a temp file, then replace.
            tmp = server.patches_path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, server.patches_path)
            except OSError as exc:
                self._send_json(500, {"error": f"could not write patches: {exc}"})
                return
            # Ask the running core to hot-reload the new table.
            server.bus.submit(Command(cmd=Cmd.RELOAD_PATCHES, payload={}))
            self._send_json(200, {"ok": True})

        # ------------------------------------------------------------------
        # Brain API (decision config: hunt-loop parameters).
        # ------------------------------------------------------------------
        def _get_brain(self):
            if server.brain_path:
                try:
                    return self._send_json(200, load_brain_config(server.brain_path))
                except (OSError, ValueError):
                    pass
            if server.brain_provider is not None:
                self._send_json(200, server.brain_provider())
                return
            self._send_json(200, builtin_brain_config())

        def _post_brain(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else b""
            try:
                data = json.loads(body)
                validate_brain_config(data)
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            # Atomic write: dump to a temp file, then replace.
            tmp = server.brain_path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, server.brain_path)
            except OSError as exc:
                self._send_json(500, {"error": f"could not write brain: {exc}"})
                return
            # Ask the running core to hot-reload the new config.
            server.bus.submit(Command(cmd=Cmd.RELOAD_BRAIN, payload={}))
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
        # Screen API.
        # ------------------------------------------------------------------
        def _get_screen(self):
            # The hydra patch (JS) rendered on the tool's screen. Now data-driven:
            # it is the ``default`` entry of the patches table (see /api/patches).
            self._send_json(200, {"code": self._patches_data().get("default")
                                    or server.screen_code or DEFAULT_HYDRA_CODE})

        # ------------------------------------------------------------------
        # MIDI-in API (learned MIDI-in mapping).
        # ------------------------------------------------------------------
        def _get_midi(self):
            if server.midi_path:
                try:
                    data = load_midi_data(server.midi_path)
                except (OSError, ValueError):
                    data = server.midi_provider() if server.midi_provider else {}
            else:
                data = server.midi_provider() if server.midi_provider else {}
            self._send_json(200, data)

        def _post_midi(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else b""
            try:
                data = json.loads(body)
                validate_midi_data(data)
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            if not server.midi_path:
                self._send_json(500, {"error": "midi path not configured"})
                return
            # Atomic write: dump to a temp file, then replace.
            tmp = server.midi_path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, server.midi_path)
            except OSError as exc:
                self._send_json(500, {"error": f"could not write midi: {exc}"})
                return
            # Ask the running core to hot-reload the new mapping.
            server.bus.submit(Command(cmd=Cmd.RELOAD_MIDI, payload={}))
            self._send_json(200, {"ok": True})

        def _get_midi_ports(self):
            if server.midi is None:
                self._send_json(200, {"ports": []})
                return
            self._send_json(200, {"ports": server.midi.list_ports()})

        def _post_midi_learn(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else b""
            try:
                req = json.loads(body)
            except (ValueError, json.JSONDecodeError):
                req = {}
            action = req.get("action", "start")
            if server.midi is None:
                self._send_json(500, {"error": "midi input not configured"})
                return
            if action == "stop":
                server.midi.stop_learn()
            else:
                server.midi.start_learn(
                    req.get("zone"), req.get("action_name"), req.get("nav"))
            self._send_json(200, {"ok": True, "learning": server.midi.is_learning})

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
