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

    GOTO_ZONE = "goto_zone"
    SET_MODE = "set_mode"
    PLAY_ACTION = "play_action"
    SET_JOINT_POSE = "set_joint_pose"
    SET_LINEAR_POSE = "set_linear_pose"
    ADJUST_LIMIT = "adjust_limit"
    SET_FLAG = "set_flag"
    RANDOM_WRIST = "random_wrist"
    STOP = "stop"


class Event(str, Enum):
    """Events the core publishes to subscribers."""

    ZONE_CHANGED = "zone_changed"
    MODE_CHANGED = "mode_changed"
    ACTION_CHANGED = "action_changed"
    POSE_UPDATED = "pose_updated"
    SNAPSHOT = "snapshot"


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

    def to_dict(self) -> Dict[str, Any]:
        return {
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
