"""Camera control adapter.

The camera is the robot's primary input. Per the project notes it runs two modes:

- **track** – follows a person, emits fast control commands,
- **analyze** – decodes emotion, emits slow control commands.

This adapter is the *control* side of the camera: it reacts to the core's state
(which zone / mode is active) and configures the camera backend accordingly, and it is
the seam where the actual vision pipeline (``input_processing/``) plugs in. The heavy
vision work is delegated to a :class:`CameraBackend`, so this module stays small and
testable.

The backend reports detections back to the core as commands (e.g. a tracked person
becomes a ``goto_zone`` / ``set_joint_pose`` for the head to follow).
"""

from typing import Callable, Optional

from ..core.bus import StateBus
from ..core.commands import Command, Event

CAMERA_MODES = ("idle", "track", "analyze")


class CameraBackend:
    """Interface for the concrete vision pipeline (e.g. the YOLO trackers)."""

    def configure(self, mode: str) -> None:
        raise NotImplementedError

    def update(self, snapshot) -> Optional[Command]:
        """Optional: return a Command for the core based on the latest frame."""
        return None


class NullCameraBackend(CameraBackend):
    """No-op backend used when the camera hardware is absent."""

    def configure(self, mode: str) -> None:
        pass


class Camera:
    """Drives the camera backend from the core's state."""

    def __init__(self, bus: StateBus, backend: CameraBackend = None,
                 on_command: Callable[[Command], None] = None):
        self.bus = bus
        self.backend = backend or NullCameraBackend()
        self._on_command = on_command
        self.mode = "idle"
        bus.subscribe(self.on_event)

    def on_event(self, event: Event, data) -> None:
        # Zone changes imply a new camera configuration (e.g. wide vs close).
        if event in (Event.ZONE_CHANGED, Event.MODE_CHANGED):
            self.apply_for_zone(data if isinstance(data, str) else self._zone_of(data))

    def apply_for_zone(self, zone: str) -> None:
        """Pick a camera mode for the zone and configure the backend."""
        # Zones that are about looking / interacting track the person; rest/idle idle.
        if zone in ("rest", "init"):
            mode = "idle"
        elif zone in ("wakeup", "stretch", "wander", "wildwander"):
            mode = "track"
        else:
            mode = "idle"
        self.set_mode(mode)

    def set_mode(self, mode: str) -> None:
        if mode not in CAMERA_MODES:
            return
        self.mode = mode
        try:
            self.backend.configure(mode)
        except Exception as exc:  # noqa: BLE001 - camera must not kill the core
            print(f"[camera] configure error: {exc}")

    def poll(self, snapshot) -> None:
        """Call from the engine each tick to let the backend emit a command."""
        cmd = self.backend.update(snapshot)
        if cmd is not None and self._on_command:
            self._on_command(cmd)

    @staticmethod
    def _zone_of(snapshot) -> str:
        return getattr(snapshot, "zone", "init")


def make_camera(bus: StateBus, enabled: bool, on_command=None) -> Camera:
    """Build a camera adapter (disabled -> null backend)."""
    return Camera(bus, backend=NullCameraBackend(), on_command=on_command)
