"""Display adapter (P5live).

The display is a P5live sketch running in a browser. It connects to the core's
WebSocket server as a client and renders the robot's state. This adapter is the core's
view of that display: it turns each state snapshot into the small, JSON-friendly
message P5live expects, and it is the seam for display-specific cues (e.g. "zone
changed to stretch → P5live shows the stretch animation").

Transport is owned by :class:`~rtr.io.websocket.WebSocketServer`; this adapter only
decides *what* to send. It holds a ``send`` callable (the WS server's broadcast) so it
has no dependency on the transport itself.
"""

from typing import Callable, Optional

from ..core.bus import StateBus
from ..core.commands import Event, Snapshot


class Display:
    """Sends state to the P5live display over the WebSocket transport."""

    def __init__(self, bus: StateBus, send: Callable[[dict], None],
                 enabled: bool = True):
        self.bus = bus
        self._send = send
        self.enabled = enabled
        bus.subscribe(self.on_event)

    def on_event(self, event: Event, data) -> None:
        if not self.enabled:
            return
        if event == Event.SNAPSHOT:
            self._send(self._frame(Snapshot, data))
        elif event == Event.ZONE_CHANGED:
            self._send({"type": "zone", "zone": data})
        elif event == Event.MODE_CHANGED:
            self._send({"type": "mode", "mode": data})
        elif event == Event.ZONES_CHANGED:
            self._send({"type": "zones_changed", "zones": data})

    @staticmethod
    def _frame(snapshot_cls, snap) -> dict:
        """A P5live-friendly frame for the current state."""
        return {
            "type": "state",
            "zone": snap.zone,
            "mode": snap.mode,
            "action": snap.action,
            "joints": list(snap.joint_pose),
            "cart": list(snap.cart_pose),
            "target": list(snap.target_pose),
            "speed": snap.speed,
            "moving": snap.moving,
            "flags": dict(snap.flags),
        }


def make_display(bus: StateBus, send: Callable[[dict], None], enabled: bool) -> Display:
    return Display(bus, send, enabled=enabled)
