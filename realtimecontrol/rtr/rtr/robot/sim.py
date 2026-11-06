"""Headless robot for development without the KUKA.

The sim keeps a virtual joint pose and, on each :meth:`get_curjpos` call (once per
engine tick), moves it toward the last commanded target. That mirrors the real
controller's behaviour closely enough for the core's transition and wander logic to
run end to end: the engine sees the pose approach a target,
:func:`~rtr.robot.helpers.comparelist` fires, and zone transitions complete in a
predictable amount of time.

The move is coordinated: every axis is paced so they all reach the target on the
same tick (the real controller's block/arrival semantics). The total rest-to-rest
time is the max over the per-axis trapezoidal/triangular profile times (see
:func:`~rtr.state.machine.motion_profile_time`); each axis then walks at
``|diff| / T`` deg/s, so the slowest axis (the one that sets ``T``) and the fastest
arrive together. The pace is recomputed every tick so a retarget mid-move re-plans.

The speed is treated as degrees/second (the kukapy speed percentage is not modelled).
It does not model cartesian kinematics; :meth:`get_curpos` returns a trivially
derived pose so the display still moves.
"""

from typing import List

from .base import RobotBase
from .helpers import motion_profile_time


class SimRobot(RobotBase):
    """A virtual 6-axis robot that moves toward commanded joint poses, all axes in sync."""

    def __init__(self, home: List[float] = None, tick_hz: float = 20.0,
                 snap_eps: float = 0.05, accel_deg_s2: float = 100.0):
        self._home = list(home) if home else [0, -90, 90, 0, 90, 0]
        self._cur = list(self._home)
        self._target = list(self._home)
        self._snap_eps = snap_eps
        self._dt = 1.0 / tick_hz
        self._speed = 0.0
        # The trapezoidal-profile acceleration (deg/s^2) used to pace the coordinated
        # move. Matches the perform default (acceleration_max_deg_s2 = 100.0) so the
        # sim's arrival timing is consistent with the engine's time-to-arrive estimate.
        self._accel = max(0.0, float(accel_deg_s2))
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def get_curjpos(self) -> List[float]:
        # Coordinated move: pace every axis so they all reach the target on the same
        # tick. The total rest-to-rest time T is the max over the per-axis
        # trapezoidal/triangular profile times; each axis walks |diff|/T deg/s so the
        # slowest (the one that sets T) and the fastest arrive together. Recomputed
        # every tick, so a retarget mid-move re-plans the pace.
        n = len(self._cur)
        t_total = max(
            (motion_profile_time(abs(self._target[i] - self._cur[i]),
                                 self._speed, self._accel)
             for i in range(n)),
            default=0.0,
        )
        for i in range(n):
            diff = self._target[i] - self._cur[i]
            if abs(diff) <= self._snap_eps:
                self._cur[i] = self._target[i]
                continue
            if t_total <= 0:
                # No time budget (speed/accel invalid): the axis cannot move; hold.
                continue
            step = (abs(diff) / t_total) * self._dt
            if step >= abs(diff):
                self._cur[i] = self._target[i]
            else:
                self._cur[i] += step if diff > 0 else -step
        return list(self._cur)

    def get_curpos(self) -> List[float]:
        # Not a real FK; just a stand-in so the display has a moving value.
        return [self._cur[0], self._cur[1], self._cur[2], 0.0, 0.0, 0.0]

    def move_joint(self, pose: List[float], speed: float, blocking: bool = True) -> None:
        # The sim is inherently non-blocking: it only sets the target and walks
        # toward it on the next ``get_curjpos``. ``blocking`` is accepted for
        # interface parity with the real robot (where blocking waits for arrival)
        # but has no effect here — the sim always streams.
        self._target = list(pose)
        self._speed = max(0.0, float(speed))

    def move_linear(self, pose: List[float], speed: float, blocking: bool = True) -> None:
        # Linear cartesian moves are not simulated; hold the current pose.
        pass
