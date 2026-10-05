"""WebSocket / HTTP client to the core.

This is the client-side link to the core. It speaks the core's real protocol:

- **WebSocket** (``ws://<host>:<ws_port>``): the core broadcasts state frames;
  we subscribe to them and send commands as JSON. Commands use the
  :class:`rtr.core.commands.Command` schema, e.g.
  ``{"cmd": "trigger_action", "zone": "rest", "action": "breathe"}``.
- **HTTP** (``http://<host>:<http_port>``): ``GET /api/zones`` returns the zone
  data table (``{"zones": {...}}``). The table is what the TUI navigates: each
  zone lists its ``actions`` and the navigation graph is derived from the
  per-action ``next`` fields.

Frames the core sends over the WebSocket (see :class:`rtr.display.display.Display`):

- ``{"type": "state", "zone", "mode", "action", "patch", "joints", "cart",
  "target", "speed", "moving", "flags"}`` — a full state snapshot, every tick.
  ``mode`` carries the current behavior (wander/track/focus/scan/look/wander/random/hold).
- ``{"type": "zone", "zone": name}`` — a zone change event.
- ``{"type": "mode", "mode": name}`` — a behavior change event.
- ``{"type": "zones_changed", "zones": [names]}`` — the zone table changed.

The :class:`Connection` keeps a WebSocket link (with automatic reconnect) and a
slow HTTP poll of the zone table. Callbacks fire on the asyncio thread; the TUI
drains them through a thread-safe queue.
"""

import asyncio
import http.client
import json
import logging
import time
from typing import Any, Callable, Dict, Optional

try:
    import websockets
except ImportError:
    websockets = None

logger = logging.getLogger(__name__)

# Seconds between background refetches of the zone table (out-of-band edits).
_ZONES_POLL_S = 30.0
# Seconds to wait before retrying a dropped WebSocket link.
_WS_RETRY_S = 2.0
# HTTP timeout for the zone-table fetch (seconds).
_HTTP_TIMEOUT_S = 5.0


class Connection:
    """WebSocket/HTTP client to the core.

    Parameters
    ----------
    ws_host : str
        WebSocket host.
    ws_port : int
        WebSocket port.
    http_host : str, optional
        HTTP host for the zone table (defaults to ``ws_host``).
    http_port : int
        HTTP port for the zone table (the core's HTTP server, default 8766).
    http_poll : int
        Seconds between state polls when the WebSocket is unavailable.
    on_state : Callable[[Dict[str, Any]], None]
        Callback for each ``state`` frame.
    on_zones : Callable[[Dict[str, dict]], None]
        Callback with the full zone table (``{"name": zone_data}``).
    on_error : Callable[[str], None]
        Optional callback for errors.
    on_event : Callable[[Dict[str, Any]], None]
        Optional callback for each non-``state`` event frame (zone / mode /
        cam_control / ...). Lets a client react to specific event frames without
        wrapping the low-level message handler.
    """

    def __init__(self, ws_host: str, ws_port: int, http_host: Optional[str] = None,
                 http_port: int = 8766, http_poll: int = 30,
                 on_state: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_zones: Optional[Callable[[Dict[str, dict]], None]] = None,
                 on_error: Optional[Callable[[str], None]] = None,
                 on_event: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.ws_host = ws_host
        self.ws_port = ws_port
        self.http_host = http_host or ws_host
        self.http_port = http_port
        self.http_poll = http_poll
        self.on_state = on_state or (lambda _s: None)
        self.on_zones = on_zones or (lambda _z: None)
        self.on_error = on_error or (lambda _e: None)
        self.on_event = on_event or (lambda _d: None)

        self.connected = False  # True while the WebSocket link is up.
        self._ws: Optional[Any] = None
        self._tasks = []
        self._stopped = asyncio.Event()

    # ------------------------------------------------------------------
    # Zone table (HTTP).
    # ------------------------------------------------------------------
    def _fetch_zones_blocking(self) -> Optional[Dict[str, dict]]:
        """One blocking ``GET /api/zones``; returns the zones table or None."""
        conn = None
        try:
            conn = http.client.HTTPConnection(
                self.http_host, self.http_port, timeout=_HTTP_TIMEOUT_S)
            conn.request("GET", "/api/zones")
            resp = conn.getresponse()
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                zones = data.get("zones", {})
                return zones if isinstance(zones, dict) else None
            logger.warning("GET /api/zones status %d", resp.status)
        except Exception as exc:  # noqa: BLE001 - network/parse failure is not fatal
            logger.warning("GET /api/zones failed: %s", exc)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        return None

    async def _zones_task(self) -> None:
        """Fetch the zone table now, then keep refreshing in the background."""
        loop = asyncio.get_running_loop()
        while not self._stopped.is_set():
            table = await loop.run_in_executor(None, self._fetch_zones_blocking)
            if table:
                self.on_zones(table)
            await asyncio.sleep(_ZONES_POLL_S)

    # ------------------------------------------------------------------
    # State (WebSocket).
    # ------------------------------------------------------------------
    def _on_message(self, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("bad JSON frame: %r", message[:120])
            return
        t = data.get("type")
        if t == "state":
            self.on_state(data)
        else:
            # Non-state event frame: hand it to the client's event callback (the
            # camera remote reacts to ``cam_control`` this way).
            self.on_event(data)
            if t in ("zone", "mode", "zones_changed", "sound_changed", "patches_changed"):
                logger.debug("event frame: %s", data)
            else:
                logger.debug("unknown frame: %s", data)

    async def _ws_task(self) -> None:
        """Keep a WebSocket link up; reconnect on drop until stopped."""
        while not self._stopped.is_set():
            if websockets is None:
                logger.warning("websockets not installed; no live state")
                return
            try:
                async with websockets.connect(
                        f"ws://{self.ws_host}:{self.ws_port}",
                        ping_interval=30, ping_timeout=10) as ws:
                    self._ws = ws
                    self.connected = True
                    logger.info("WS connected to ws://%s:%d", self.ws_host, self.ws_port)
                    async for message in ws:
                        self._on_message(message)
            except Exception as exc:  # noqa: BLE001 - connect/read failure retries
                logger.info("WS unavailable (%s)", exc)
            finally:
                self._ws = None
                self.connected = False
            await asyncio.sleep(_WS_RETRY_S)

    async def _send(self, cmd: Dict[str, Any]) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps(cmd))
        except Exception as exc:  # noqa: BLE001
            logger.warning("send failed: %s", exc)

    def send_command(self, cmd: Dict[str, Any]) -> None:
        """Send a command to the core (e.g. ``{"cmd": "trigger_action", "zone": "rest", "action": "breathe"}``).

        Synchronous; schedules the send on the running loop. Call it from the
        asyncio thread (the TUI marshals cross-thread calls via ``call_soon_threadsafe``).
        """
        if self._ws is None:
            logger.warning("no WS link; command dropped: %s", cmd)
            self.on_error("not connected; command dropped")
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._send(cmd))

    # ------------------------------------------------------------------
    # Lifecycle.
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Start the WebSocket link and the zone-table poller."""
        self._stopped = asyncio.Event()
        self._tasks = [
            asyncio.create_task(self._ws_task()),
            asyncio.create_task(self._zones_task()),
        ]

    async def stop(self) -> None:
        """Stop all tasks and close the link."""
        self._stopped.set()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self.connected = False

    async def wait_connected(self, timeout: float = 5.0) -> bool:
        """Wait until the WebSocket handshake completes (or the timeout expires).

        :meth:`start` only spawns the connect task and returns before the
        handshake, so callers that must know the link is actually up should
        await this. Returns ``True`` if the link is up, ``False`` otherwise.
        """
        deadline = time.monotonic() + timeout
        while True:
            if self.connected and self._ws is not None:
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.2)
