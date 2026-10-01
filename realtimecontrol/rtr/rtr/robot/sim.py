"""Headless robot for development without the KUKA.

The sim keeps a virtual joint pose and, on each :meth:`get_curjpos` call (once per
engine tick), moves it toward the last commanded target at the commanded speed. That
mirrors the real controller's behaviour closely enough for the core's transition and
wander logic to run end to end: the engine sees the pose approach a target,
:func:`~rtr.robot.helpers.comparelist` fires, and zone transitions complete in a
predictable amount of time.

The speed is treated as degrees/second (the kukapy speed percentage is not modelled).
It does not model cartesian kinematics; :meth:`get_curpos` returns a trivially
derived pose so the display still moves.
"""

from typing import List

from .base import RobotBase


class SimRobot(RobotBase):
    """A virtual 6-axis robot that moves toward commanded joint poses at a fixed rate."""

    def __init__(self, home: List[float] = None, tick_hz: float = 20.0,
                 snap_eps: float = 0.05):
        self._home = list(home) if home else [0, -90, 90, 0, 90, 0]
        self._cur = list(self._home)
        self._target = list(self._home)
        self._snap_eps = snap_eps
        self._dt = 1.0 / tick_hz
        self._speed = 0.0
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def get_curjpos(self) -> List[float]:
        # Move each axis toward the target at ``speed`` deg/s, without overshooting.
        for i in range(len(self._cur)):
            diff = self._target[i] - self._cur[i]
            if abs(diff) <= self._snap_eps:
                self._cur[i] = self._target[i]
                continue
            step = self._speed * self._dt
            if step >= abs(diff):
                self._cur[i] = self._target[i]
            else:
                self._cur[i] += step if diff > 0 else -step
        return list(self._cur)

    def get_curpos(self) -> List[float]:
        # Not a real FK; just a stand-in so the display has a moving value.
        return [self._cur[0], self._cur[1], self._cur[2], 0.0, 0.0, 0.0]

    def move_joint(self, pose: List[float], speed: float) -> None:
        self._target = list(pose)
        self._speed = max(0.0, float(speed))

    def move_linear(self, pose: List[float], speed: float) -> None:
        # Linear cartesian moves are not simulated; hold the current pose.
        pass
