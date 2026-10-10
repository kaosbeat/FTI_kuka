"""WebSocket server: the single bridge to P5live and any external client.

Two directions, one port:

- **In** – clients send JSON commands (the :class:`~rtr.core.commands.Command`
  schema); the server submits them to the bus.
- **Out** – the core broadcasts state to every connected client. The
  :class:`~rtr.display.display.Display` adapter decides the P5live frame and calls
  :meth:`broadcast`; this class is pure transport and owns the set of live clients.

:meth:`broadcast` is synchronous (called from the bus, which is driven by the async
engine) and schedules the actual send on the running event loop.
"""

import asyncio
import inspect
import json
import time
from typing import Dict, Set

from ..core.bus import StateBus
from ..core.commands import Command


# Commands the browser control pages send (used to tell a browser client apart
# from the RPI camera remote, which only ever sends ``cam_*`` telemetry).
_BROWSER_CMDS = {
    "trigger_action", "play_action", "clear_action",
    "set_joint_pose", "set_linear_pose", "adjust_limit",
    "random_wrist", "set_flag", "set_screen_patch",
    "stop", "proceed", "cam_force_lock",
}


class WebSocketServer:
    """Bidirectional JSON transport between the core and external clients."""

    def __init__(self, bus: StateBus, host: str = "0.0.0.0", port: int = 8765,
                 enabled: bool = True):
        self.bus = bus
        self.host = host
        self.port = port
        self.enabled = enabled
        self._clients: Set = set()
        self._server = None
        # Per-connection identity (connection log: who is who, from which IP).
        self._ids: Dict = {}
        self._addrs: Dict = {}
        self._cmd_ts: Dict = {}
        self._msg_count: Dict = {}  # connection -> messages received (liveness proof)
        self._seq = 1

    async def start(self) -> None:
        if not self.enabled:
            return
        try:
            import websockets
        except ImportError:
            print("[ws] 'websockets' not installed; WebSocket server disabled")
            self.enabled = False
            return
        self._server = await websockets.serve(self._handler, self.host, self.port)
        print(f"[ws] listening on ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        if self._server is not None:
            # websockets' close() is sync in some versions (returns None) and an
            # async coroutine in others; handle both so shutdown never crashes.
            result = self._server.close()
            if inspect.isawaitable(result):
                await result
            self._server = None

    def _label(self, connection) -> str:
        return self._ids.get(connection, "?")

    def _handle_hello(self, connection, data) -> None:
        """Explicit client identification: ``{"cmd": "hello", "client": "camera"}``.

        Every client should send this as its first frame. The label is used in all
        subsequent logs, so the log reads "camera <- cam_status" instead of
        "client6 <- cam_status".
        """
        client = data.get("client")
        if not isinstance(client, str) or not client.strip():
            print(f"[ws] hello without client name from "
                  f"{self._addrs.get(connection)}; keeping {self._label(connection)}")
            return
        self._ids[connection] = client.strip()
        print(f"[ws] hello: {client.strip()} from {self._addrs.get(connection)}")

    def _identify(self, connection, cmd) -> None:
        """Fallback label for clients that never send ``hello``.

        The RPI camera remote only ever sends ``cam_*`` telemetry; the browser
        control pages send operator commands. This is what lets the log say
        "camera connected from <ip>" vs "screen connected from <ip>".
        """
        name = str(cmd)
        cur = self._ids.get(connection, "")
        if not cur.startswith("client"):
            return  # already identified (by hello or an earlier command)
        if name.startswith("cam_"):
            self._ids[connection] = "camera"
        elif name in _BROWSER_CMDS:
            self._ids[connection] = "screen"
        else:
            self._ids[connection] = "client"
        print(f"[ws] identified {self._label(connection)} as "
              f"{self._ids[connection]} (from {self._addrs.get(connection)})")

    def _log_cmd(self, connection, cmd) -> None:
        """Log incoming commands (throttled) so telemetry flow is visible."""
        name = str(cmd)
        now = time.time()
        # Camera telemetry is the interesting stream: log ~1/s. Other commands
        # are noisier: log less often.
        interval = 1.0 if name.startswith("cam_") else 3.0
        if now - self._cmd_ts.get(name, 0.0) >= interval:
            self._cmd_ts[name] = now
            print(f"[ws] {self._label(connection)} <- {name}")

    async def _handler(self, connection, *args) -> None:
        self._clients.add(connection)
        try:
            addr = tuple(connection.remote_address)
        except Exception:
            addr = None
        self._ids[connection] = f"client{self._seq}"
        self._seq += 1
        self._addrs[connection] = addr
        self._msg_count[connection] = 0
        print(f"[ws] {self._label(connection)} connected from {addr} "
              f"({len(self._clients)} total)")
        try:
            async for message in connection:
                n = self._msg_count.get(connection, 0) + 1
                self._msg_count[connection] = n
                # First two messages: log the raw payload unthrottled so we can
                # see with zero ambiguity whether a client is actually
                # transmitting, and exactly what it sends. The throttled cmd log
                # alone cannot prove a client is alive (it only fires on flow).
                if n <= 2:
                    print(f"[ws] {self._label(connection)} msg#{n} raw: "
                          f"{str(message)[:300]}")
                try:
                    data = json.loads(message)
                    if isinstance(data, dict) and data.get("cmd") == "hello":
                        self._handle_hello(connection, data)
                        continue  # hello is transport-level; never reaches the bus
                    if isinstance(data, dict) and data.get("cmd") is not None:
                        self._identify(connection, data.get("cmd"))
                        self._log_cmd(connection, data.get("cmd"))
                    self.bus.submit(Command.from_dict(dict(data)))
                except Exception as exc:  # noqa: BLE001 - bad client msg must not kill server
                    print(f"[ws] bad command: {exc}")
        finally:
            self._clients.discard(connection)
            lbl = self._ids.pop(connection, None)
            self._addrs.pop(connection, None)
            total = self._msg_count.pop(connection, 0)
            print(f"[ws] {lbl or 'client'} disconnected "
                  f"({len(self._clients)} total, {total} msgs recv)")

    def broadcast(self, payload: Dict) -> None:
        """Send a JSON payload to every connected client (fire-and-forget)."""
        if not self._clients:
            return
        msg = json.dumps(payload)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # not in an async context; nothing safe to do
        loop.create_task(self._send_to_all(msg))

    async def _send_to_all(self, msg: str) -> None:
        if not self._clients:
            return
        await asyncio.gather(
            *[c.send(msg) for c in list(self._clients)],
            return_exceptions=True,
        )
