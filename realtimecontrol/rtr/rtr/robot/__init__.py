"""Robot abstraction: base interface, Kuka and Sim implementations, limit helpers."""

from .base import RobotBase
from .config import (
    DEFAULT_ROBOT_CONFIG,
    builtin_robot_config,
    load_robot_config,
    robot_config_offsets,
    robot_config_patch_mode,
    validate_robot_config,
)
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
    "DEFAULT_ROBOT_CONFIG",
    "builtin_robot_config",
    "load_robot_config",
    "robot_config_offsets",
    "robot_config_patch_mode",
    "validate_robot_config",
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


def make_robot(kind: str, port: int = 18735, tick_hz: float = 20.0,
               joint_offsets: list = None) -> RobotBase:
    """Build a robot by kind ("kuka" or "sim").

    ``joint_offsets`` (A1..A6, degrees) is the physical robot's home offset; it is
    applied only to the KUKA (the sim is never offset).
    """
    if kind == "kuka":
        return KukaRobot(port=port, joint_offsets=joint_offsets)
    if kind == "sim":
        return SimRobot(tick_hz=tick_hz)
    raise ValueError(f"unknown robot kind: {kind!r}")
