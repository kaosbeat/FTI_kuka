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
from ..camera.camera import CameraController


class Display:
    """Sends state to the P5live display over the WebSocket transport."""

    def __init__(self, bus: StateBus, send: Callable[[dict], None],
                 enabled: bool = True,
                 patch_code: Callable[[str, str, str], str] = None,
                 camera: Optional[CameraController] = None):
        self.bus = bus
        self._send = send
        self.enabled = enabled
        # Resolves the hydra patch for a state (action > mode > zone > default). The
        # core is the single source of truth: it pushes the resolved code in every
        # state frame so all display clients (client.html, render.html, the 3D tool
        # screen) stay in sync no matter what triggered the change (MIDI, WS, HTTP).
        self._patch_code = patch_code
        # The core-side camera controller; its intent is broadcast in every state
        # frame and via CAM_CONTROL event frames for the fast path.
        self._camera = camera
        bus.subscribe(self.on_event)

    def on_event(self, event: Event, data) -> None:
        if not self.enabled:
            return
        if event == Event.SNAPSHOT:
            self._send(self._frame(data))
        elif event == Event.ZONE_CHANGED:
            self._send({"type": "zone", "zone": data})
        elif event == Event.MODE_CHANGED:
            self._send({"type": "mode", "mode": data})
        elif event == Event.ZONES_CHANGED:
            self._send({"type": "zones_changed", "zones": data})
        elif event == Event.SOUND_CHANGED:
            self._send({"type": "sound_changed"})
        elif event == Event.PATCHES_CHANGED:
            self._send({"type": "patches_changed"})
        elif event == Event.MIDI_CHANGED:
            self._send({"type": "midi_changed"})
        elif event == Event.MIDI_LEARN:
            # A learned key captured by MidiInput; the editor writes it into the
            # mapping. ``data`` is the {"key": ..., "msg": ...} payload.
            self._send({"type": "midi_learn", **data})
        elif event == Event.CAM_CONTROL:
            # Fast-path camera control frame (emitted when the intent changes).
            self._send({"type": "cam_control", **data})

    def _patch_for(self, snap: Snapshot) -> str | None:
        """The resolved hydra patch for this state, or None if resolution is unavailable.

        A missing resolver (or a failure) degrades to None, so a client that receives
        no patch falls back to matching the table it fetched itself.
        """
        if self._patch_code is None:
            return None
        try:
            code = self._patch_code(snap.zone, snap.mode, snap.action)
        except Exception:  # noqa: BLE001 - a bad resolver must not kill the frame
            return None
        return code if isinstance(code, str) and code else None

    def _camera_field(self) -> Optional[dict]:
        """The camera intent for the state frame, or None when no camera is wired."""
        if self._camera is None:
            return None
        return self._camera.intent.to_dict()

    def _frame(self, snap: Snapshot) -> dict:
        """A P5live-friendly frame for the current state (includes the resolved patch)."""
        frame = {
            "type": "state",
            "zone": snap.zone,
            "mode": snap.mode,
            "action": snap.action,
            "patch": self._patch_for(snap),
            "joints": list(snap.joint_pose),
            "cart": list(snap.cart_pose),
            "target": list(snap.target_pose),
            "speed": snap.speed,
            "moving": snap.moving,
            "flags": dict(snap.flags),
        }
        cam = self._camera_field()
        if cam is not None:
            frame["camera"] = cam
        return frame


def make_display(bus: StateBus, send: Callable[[dict], None], enabled: bool,
                 patch_code: Callable[[str, str, str], str] = None,
                 camera: Optional[CameraController] = None) -> Display:
    return Display(bus, send, enabled=enabled, patch_code=patch_code, camera=camera)
