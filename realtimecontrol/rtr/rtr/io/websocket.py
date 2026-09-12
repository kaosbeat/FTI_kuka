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
import json
from typing import Dict, Set

from ..core.bus import StateBus
from ..core.commands import Command


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
            await self._server.close()
            self._server = None

    async def _handler(self, connection, *args) -> None:
        self._clients.add(connection)
        print(f"[ws] client connected ({len(self._clients)} total)")
        try:
            async for message in connection:
                try:
                    data = json.loads(message)
                    self.bus.submit(Command.from_dict(dict(data)))
                except Exception as exc:  # noqa: BLE001 - bad client msg must not kill server
                    print(f"[ws] bad command: {exc}")
        finally:
            self._clients.discard(connection)

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
