"""Real KUKA KR60 via kukapy.

This is a thin wrapper around :class:`kukapy.robot.Robot` so the core depends only on
:mod:`~rtr.robot.base` and never on kukapy directly. kukapy is imported lazily inside
:meth:`connect`, so the rest of the system (and the sim) runs without it installed.

The API surface is exactly what ``kukart/tidalkuka.py`` already uses, so behaviour is
unchanged: ``get_curjpos`` / ``get_curpos`` read the pose, ``move`` sends a target the
controller interpolates.
"""

from typing import List

from .base import RobotBase


class KukaRobot(RobotBase):
    """Drives the physical robot through the EKI (kukapy)."""

    def __init__(self, port: int = 18735):
        self._port = port
        self._robot = None

    def connect(self) -> None:
        try:
            from kukapy.robot import Robot
        except ImportError as exc:
            raise RuntimeError(
                "kukapy is not installed. Run `pip install git+https://github.com/"
                "JasonLvernex/KukaPy.git` or use --sim."
            ) from exc
        self._robot = Robot(port=self._port)
        self._robot.connect()

    def disconnect(self) -> None:
        if self._robot is not None:
            try:
                self._robot.disconnect()
            finally:
                self._robot = None

    def get_curjpos(self) -> List[float]:
        return list(self._robot.get_curjpos())

    def get_curpos(self) -> List[float]:
        return list(self._robot.get_curpos())

    def move_joint(self, pose: List[float], speed: float) -> None:
        self._robot.move("joint", list(pose), speed)

    def move_linear(self, pose: List[float], speed: float) -> None:
        self._robot.move("pose", list(pose), speed, linear=True)
