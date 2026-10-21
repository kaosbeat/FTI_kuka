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
                 patch_code: Callable[..., str] = None,
                 camera: Optional[CameraController] = None,
                 machine=None,
                 render_ui_provider: Callable[[], dict] = None,
                 midi_monitor_provider: Callable[[], list] = None):
        self.bus = bus
        self._send = send
        self.enabled = enabled
        self._machine = machine
        # Returns the render-page UI state ({hud, code}); pushed in every state
        # frame so the render page can toggle its HUD and code overlay.
        self._render_ui_provider = render_ui_provider
        # Resolves the hydra patch for a state by concatenating zone + action/default
        # + behavior (the extra-patches model), with the pose globals injected for an
        # action that has per-pose images. The core is the single source of truth: it
        # pushes the resolved code in every state frame so all display clients
        # (client.html, render.html, the 3D tool screen) stay in sync no matter what
        # triggered the change (MIDI, WS, HTTP).
        self._patch_code = patch_code
        # The core-side camera controller; its intent is broadcast in every state
        # frame and via CAM_CONTROL event frames for the fast path.
        self._camera = camera
        # Returns the MIDI monitor ring (recent raw in / commands out); pushed in
        # every state frame so the editor's live monitor and the client's activity
        # lights can show the actual MIDI traffic.
        self._midi_monitor_provider = midi_monitor_provider
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
        elif event == Event.SAFETY_DISCARD:
            # Auto mode discarded a safety-blocked move; the control client logs it.
            self._send({"type": "safety_discard", **data})
        elif event == Event.POSE_CONFLICT:
            # A SET_JOINT_POSE cleared the active action; the control client logs it.
            self._send({"type": "pose_conflict", **data})

    def _patch_for(self, snap: Snapshot) -> str | None:
        """The resolved hydra patch for this state, or None if resolution is unavailable.

        A missing resolver (or a failure) degrades to None, so a client that receives
        no patch falls back to matching the table it fetched itself.
        """
        if self._patch_code is None:
            return None
        try:
            beh = self._machine.behavior if self._machine is not None else None
            code = self._patch_code(snap.zone, snap.mode, snap.action, snap.action_index, behavior=beh)
        except Exception:  # noqa: BLE001 - a bad resolver must not kill the frame
            return None
        return code if isinstance(code, str) and code else None

    def _render_ui_field(self) -> Optional[dict]:
        """The render-page UI state for the state frame, or None when no provider is wired."""
        if self._render_ui_provider is None:
            return None
        try:
            ui = self._render_ui_provider()
        except Exception:  # noqa: BLE001 - a bad provider must not kill the frame
            return None
        return ui if isinstance(ui, dict) else None

    def _midi_monitor_field(self) -> Optional[list]:
        """The MIDI monitor ring for the state frame, or None when no provider is wired.

        A failure in the provider degrades to None (the frame is still sent)."""
        if self._midi_monitor_provider is None:
            return None
        try:
            ring = self._midi_monitor_provider()
        except Exception:  # noqa: BLE001 - a bad provider must not kill the frame
            return None
        return list(ring) if isinstance(ring, (list, tuple)) else None

    def _camera_field(self) -> Optional[dict]:
        """The camera intent for the state frame, or None when no camera is wired."""
        if self._camera is None:
            return None
        return self._camera.intent.to_dict()

    def _camera_telem(self) -> Optional[dict]:
        """The latest RPI telemetry for the state frame (for the debug page).

        The brain stashes every ``CAM_*`` payload on the controller; echoing it in
        the state frame lets the browser debug view (camera_debug.html) render the
        candidates / lock / offset without a second transport.
        """
        if self._camera is None:
            return None
        return {
            "status": self._camera.status,
            "candidates": self._camera.candidates,
            "track": self._camera.track,
            "face": self._camera.face,
        }

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
        }
        rui = self._render_ui_field()
        if rui is not None:
            frame["render_ui"] = rui
        # Current behavior + its tunable params (the editor/TUI show them; a no-op
        # behavior like calm/bridge carries no params, so the field is absent).
        if self._machine is not None and self._machine.current_action:
            beh = self._machine.behavior
            frame["behavior"] = beh
            bp = self._machine.behavior_params
            if beh in bp:
                frame["behavior_params"] = dict(bp[beh])
        cam = self._camera_field()
        if cam is not None:
            frame["camera"] = cam
        telem = self._camera_telem()
        if telem is not None:
            frame["cam_telem"] = telem
        if snap.hunt is not None:
            frame["hunt"] = dict(snap.hunt)
        elif self._machine is not None and self._machine.hunt_state:
            frame["hunt"] = dict(self._machine.hunt_state)
        if snap.servo:
            frame["servo"] = dict(snap.servo)
        if snap.autonomy_enabled is not None:
            frame["autonomy_enabled"] = snap.autonomy_enabled
        if snap.autonomy_enabled_groups is not None:
            frame["autonomy_enabled_groups"] = snap.autonomy_enabled_groups
        if snap.performance_mode is not None:
            frame["performance_mode"] = snap.performance_mode
        if snap.autonomy_dwell_remaining is not None:
            frame["autonomy_dwell_remaining"] = snap.autonomy_dwell_remaining
        if snap.control_mode is not None:
            frame["control_mode"] = snap.control_mode
        if snap.forced_behavior is not None:
            frame["forced_behavior"] = snap.forced_behavior
        elif self._machine is not None and self._machine.forced_behavior is not None:
            frame["forced_behavior"] = self._machine.forced_behavior
        if snap.safety is not None:
            frame["safety"] = dict(snap.safety)
        elif self._machine is not None and self._machine.pending:
            frame["safety"] = self._machine.pending_info()
        # Engine cadence state (the CADENCE page's "current" row reads these;
        # the Snapshot carries them, so forward them like the other optional fields).
        if snap.move_mode is not None:
            frame["move_mode"] = snap.move_mode
        if snap.cadence is not None:
            frame["cadence"] = snap.cadence
        if snap.cadence_every is not None:
            frame["cadence_every"] = snap.cadence_every
        # The MIDI monitor ring (recent raw in / commands out); the editor's live
        # monitor and the client's activity lights read it from the state frame.
        mm = self._midi_monitor_field()
        if mm is not None:
            frame["midi_monitor"] = mm
        return frame


def make_display(bus: StateBus, send: Callable[[dict], None], enabled: bool,
                 patch_code: Callable[..., str] = None,
                 camera: Optional[CameraController] = None,
                 machine=None,
                 render_ui_provider: Callable[[], dict] = None,
                 midi_monitor_provider: Callable[[], list] = None) -> Display:
    return Display(bus, send, enabled=enabled, patch_code=patch_code,
                   camera=camera, machine=machine,
                   render_ui_provider=render_ui_provider,
                   midi_monitor_provider=midi_monitor_provider)
