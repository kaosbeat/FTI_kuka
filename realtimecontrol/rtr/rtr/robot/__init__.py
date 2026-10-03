"""Robot abstraction: base interface, Kuka and Sim implementations, limit helpers."""

from .base import RobotBase
from .helpers import (
    JOINT_COUNT,
    LIMITED_JOINTS,
    checklimits,
    clamp_pose,
    comparelist,
    fitlimits,
    posSafe,
)
from .kinematics import Chain, load_chain
from .kuka import KukaRobot
from .sim import SimRobot

__all__ = [
    "RobotBase",
    "KukaRobot",
    "SimRobot",
    "make_robot",
    "fitlimits",
    "checklimits",
    "posSafe",
    "comparelist",
    "clamp_pose",
    "JOINT_COUNT",
    "LIMITED_JOINTS",
    "Chain",
    "load_chain",
]


def make_robot(kind: str, port: int = 18735, tick_hz: float = 20.0) -> RobotBase:
    """Build a robot by kind ("kuka" or "sim")."""
    if kind == "kuka":
        return KukaRobot(port=port)
    if kind == "sim":
        return SimRobot(tick_hz=tick_hz)
    raise ValueError(f"unknown robot kind: {kind!r}")
