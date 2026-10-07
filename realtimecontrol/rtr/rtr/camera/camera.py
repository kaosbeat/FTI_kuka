"""Core-side camera controller.

Owns the camera intent (which camera is active, in which mode, with which lock)
and the latest telemetry from the RPI. The brain drives the intent; the display
reads it for the state frame and the ``CAM_CONTROL`` event for the fast path.

The RPI runs the physical cameras + vision pipeline (YOLO/ByteTrack/face) and
sends ``CAM_*`` telemetry to the core via the existing WS. The brain receives
that telemetry, decides the lock / camera switch / move, and updates the intent
here. The display broadcasts the intent on every state frame and emits a
``cam_control`` event frame when it changes.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..core.bus import StateBus
from ..core.commands import Cmd, Event


CAMERA_CHOICES = ("wide", "close", "both")
CAMERA_MODES = ("idle", "track", "analyze")


@dataclass
class CameraIntent:
    """The brain's current camera configuration for the RPI."""

    active: str = "wide"
    mode: str = "idle"
    lock_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"active": self.active, "mode": self.mode, "lock_id": self.lock_id}


class CameraController:
    """Owns the camera intent and the latest RPI telemetry.

    The brain calls :meth:`set_intent` to change the configuration and
    :meth:`handle_telemetry` to store incoming ``CAM_*`` payloads. The display
    reads :attr:`intent` for the state frame and subscribes to
    ``Event.CAM_CONTROL`` for the fast-path event frame.
    """

    def __init__(self, bus: StateBus):
        self.bus = bus
        self.intent = CameraIntent()
        # Latest telemetry from the RPI (updated by the brain on each CAM_* command).
        self.status: Optional[Dict[str, Any]] = None
        self.candidates: Optional[Dict[str, Any]] = None
        self.track: Optional[Dict[str, Any]] = None
        self.face: Optional[Dict[str, Any]] = None

    def set_intent(self, active: str = None, mode: str = None,
                   lock_id: Optional[int] = None,
                   clear_lock: bool = False) -> None:
        """Change the camera intent; publish a ``CAM_CONTROL`` event on change.

        Only the supplied fields are updated; omitted fields keep their current
        value. ``lock_id`` may be ``None`` to leave the lock unchanged; pass
        ``clear_lock=True`` to explicitly clear a stale lock.
        """
        changed = False
        if active is not None and active in CAMERA_CHOICES:
            if self.intent.active != active:
                self.intent.active = active
                changed = True
        if mode is not None and mode in CAMERA_MODES:
            if self.intent.mode != mode:
                self.intent.mode = mode
                changed = True
        if clear_lock:
            if self.intent.lock_id is not None:
                self.intent.lock_id = None
                changed = True
        elif lock_id is not None:
            if self.intent.lock_id != lock_id:
                self.intent.lock_id = lock_id
                changed = True
        if changed:
            self.bus.publish(Event.CAM_CONTROL, self.intent.to_dict())

    def handle_telemetry(self, cmd: Cmd, payload: Dict[str, Any]) -> None:
        """Store the latest telemetry from a ``CAM_*`` command (called by the brain)."""
        if cmd == Cmd.CAM_STATUS:
            self.status = payload
        elif cmd == Cmd.CAM_CANDIDATES:
            self.candidates = payload
        elif cmd == Cmd.CAM_TRACK:
            self.track = payload
        elif cmd == Cmd.CAM_FACE:
            self.face = payload


def make_camera(bus: StateBus, enabled: bool = True, **kwargs) -> CameraController:
    """Build the core-side camera controller."""
    return CameraController(bus)
