"""The core's public API: command and event types.

Commands are the only way to ask the core to do something. They come from any adapter
(MIDI, WebSocket, a local tool, an LLM bridge) and are folded into the next engine
tick. Events are what the core publishes back out. Keeping this in one module means
every adapter speaks the same small vocabulary.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Cmd(str, Enum):
    """Commands an adapter can send to the core."""

    TRIGGER_ACTION = "trigger_action"
    PLAY_ACTION = "play_action"
    CLEAR_ACTION = "clear_action"
    SET_JOINT_POSE = "set_joint_pose"
    SET_LINEAR_POSE = "set_linear_pose"
    ADJUST_LIMIT = "adjust_limit"
    SET_FLAG = "set_flag"
    RANDOM_WRIST = "random_wrist"
    RELOAD_ZONES = "reload_zones"
    RELOAD_SOUND = "reload_sound"
    RELOAD_MIDI = "reload_midi"
    RELOAD_PATCHES = "reload_patches"
    RELOAD_BRAIN = "reload_brain"
    MIDI_LEARN = "midi_learn"
    SET_SCREEN_PATCH = "set_screen_patch"
    STOP = "stop"
    SET_ENGINE_MODE = "set_engine_mode"
    SET_CADENCE = "set_cadence"
    CAM_STATUS = "cam_status"
    CAM_CANDIDATES = "cam_candidates"
    CAM_TRACK = "cam_track"
    CAM_FACE = "cam_face"
    PROCEED = "proceed"


class Event(str, Enum):
    """Events the core publishes to subscribers."""

    ZONE_CHANGED = "zone_changed"
    MODE_CHANGED = "mode_changed"
    ACTION_CHANGED = "action_changed"
    ZONES_CHANGED = "zones_changed"
    SOUND_CHANGED = "sound_changed"
    MIDI_CHANGED = "midi_changed"
    MIDI_LEARN = "midi_learn"
    PATCHES_CHANGED = "patches_changed"
    POSE_UPDATED = "pose_updated"
    SNAPSHOT = "snapshot"
    CAM_CONTROL = "cam_control"


@dataclass
class Command:
    """A single request to change the core's behaviour.

    ``cmd`` names the operation and ``payload`` carries its arguments. The payload
    schema per command is documented in the README and enforced by the state machine /
    brain, not here, so the API stays flat and easy to send over the wire.
    """

    cmd: Cmd
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"cmd": self.cmd.value, **self.payload}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Command":
        cmd = Cmd(data.pop("cmd"))
        return cls(cmd=cmd, payload=data)


@dataclass
class Snapshot:
    """A point-in-time view of the core, published to subscribers.

    This is what P5live (and any other consumer) sees. It is deliberately a flat,
    JSON-serialisable structure.
    """

    zone: str
    mode: Optional[str]
    action: Optional[str]
    joint_pose: List[float]
    cart_pose: List[float]
    target_pose: List[float]
    speed: float
    flags: Dict[str, Any]
    moving: bool
    # Additive optional fields (absent from older snapshots; clients ignore unknowns).
    hunt: Optional[dict] = None  # brain hunt state: {"state", "lock_id", "feral"}
    servo: Optional[dict] = None  # last servo vector: {"behavior","dx","dy","dq_deg","axes"}
    move_mode: Optional[str] = None  # engine move mode: "block" | "stream"
    cadence: Optional[str] = None  # cadence mode: "fixed" | "arrival"
    cadence_every: Optional[int] = None  # fixed-rhythm interval, in ticks

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "zone": self.zone,
            "mode": self.mode,
            "action": self.action,
            "joint_pose": list(self.joint_pose),
            "cart_pose": list(self.cart_pose),
            "target_pose": list(self.target_pose),
            "speed": self.speed,
            "flags": dict(self.flags),
            "moving": self.moving,
        }
        if self.hunt is not None:
            d["hunt"] = dict(self.hunt)
        if self.servo:
            d["servo"] = dict(self.servo)
        if self.move_mode is not None:
            d["move_mode"] = self.move_mode
        if self.cadence is not None:
            d["cadence"] = self.cadence
        if self.cadence_every is not None:
            d["cadence_every"] = self.cadence_every
        return d
