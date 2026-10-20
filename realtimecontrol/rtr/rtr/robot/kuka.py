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
    """Drives the physical robot through the EKI (kukapy).

    ``joint_offsets`` (A1..A6, degrees) shifts the physical home relative to the
    core's virtual joint space. It is subtracted from joint targets on the way out
    and added back to the read pose on the way in, so the core's joint space (and
    its comparelist arrival check) stays consistent. The sim is never offset.
    """

    def __init__(self, port: int = 18735, joint_offsets: List[float] = None):
        self._port = port
        self._robot = None
        self._joint_offsets = list(joint_offsets) if joint_offsets else [0.0] * 6

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
        raw = list(self._robot.get_curjpos())
        return [raw[i] + self._joint_offsets[i] for i in range(len(raw))]

    def get_curpos(self) -> List[float]:
        return list(self._robot.get_curpos())

    def move_joint(self, pose: List[float], speed: float, blocking: bool = True) -> None:
        shifted = [pose[i] - self._joint_offsets[i] for i in range(len(pose))]
        self._move(shifted, speed, blocking, linear=False)

    def move_linear(self, pose: List[float], speed: float, blocking: bool = True) -> None:
        self._move(pose, speed, blocking, linear=True)

    def set_joint_offsets(self, offsets: List[float]) -> None:
        """Update the joint home offset (hot-reload from the robot config)."""
        self._joint_offsets = list(offsets)

    def _move(self, pose: List[float], speed: float,
              blocking: bool, linear: bool) -> None:
        """Issue a move to the controller.

        ``blocking=True`` (the legacy behaviour): the classic kukapy ``move`` polls
        until the robot arrives, so the caller blocks for the whole move.

        ``blocking=False`` (stream): run the same blocking kukapy ``move`` on a
        daemon thread and return immediately, so the 20 Hz engine loop keeps
        streaming a fresh target each tick and the robot retargets mid-move,
        like the sim. Each tick's move call re-targets the in-flight move.

        NOTE: this relies on kukapy's EKI socket being safe to poll for the
        current pose (``get_curjpos``) while a background ``move`` is also
        polling it. That is not exercised in the sim and must be verified on
        the robot before relying on stream mode.
        """
        def _do() -> None:
            # Preserve the exact legacy call forms: the joint move is issued
            # without a ``linear`` kwarg; the cartesian move with ``linear=True``.
            if linear:
                self._robot.move("pose", list(pose), speed, linear=True)
            else:
                self._robot.move("joint", list(pose), speed)

        if blocking:
            _do()
            return
        import threading
        t = threading.Thread(target=_do, daemon=True)
        t.start()
