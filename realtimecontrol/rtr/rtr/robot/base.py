"""Robot interface.

The core only ever talks to a :class:`RobotBase`. The real KUKA (via ``kukapy``) and
the headless :class:`~rtr.robot.sim.SimRobot` both implement it, so the rest of the
system is hardware-agnostic.

All methods are synchronous and fast: ``move_*`` only *set* a target (the controller
interpolates), and the getters read the current pose. The engine calls them directly
each tick.
"""

from abc import ABC, abstractmethod
from typing import List


class RobotBase(ABC):
    """Minimal control surface the core needs from a robot."""

    @abstractmethod
    def connect(self) -> None:
        """Establish the connection to the controller."""

    @abstractmethod
    def disconnect(self) -> None:
        """Tear down the connection."""

    @abstractmethod
    def get_curjpos(self) -> List[float]:
        """Current joint positions (A1..A6), in degrees."""

    @abstractmethod
    def get_curpos(self) -> List[float]:
        """Current cartesian pose (X,Y,Z, A,B,C), in mm / degrees."""

    @abstractmethod
    def move_joint(self, pose: List[float], speed: float) -> None:
        """Move to a joint pose (A1..A6). Non-blocking: the controller interpolates."""

    @abstractmethod
    def move_linear(self, pose: List[float], speed: float) -> None:
        """Move to a cartesian pose along a straight line (TCP linear)."""
