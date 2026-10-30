"""Real KUKA KR60 via kukapy.

This is a thin wrapper around :class:`kukapy.robot.Robot` so the core depends only on
:mod:`~rtr.robot.base` and never on kukapy directly. kukapy is imported lazily inside
:meth:`connect`, so the rest of the system (and the sim) runs without it installed.

The API surface is exactly what ``kukart/tidalkuka.py`` already uses, so behaviour is
unchanged: ``get_curjpos`` / ``get_curpos`` read the pose, ``move`` sends a target the
controller interpolates.
"""

import threading
from typing import List, Optional

from .base import RobotBase

# Continuous-positioning percentage for stream (non-blocking) moves. The KUKAPY
# server treats ``cmd_cnt > 0`` as a continuous move (``PTP ... C_PTP`` /
# ``LIN ... C_DIS``): the KRL issues the move and answers immediately instead of
# holding until arrival, so the 20 Hz retarget stream keeps flowing. Block moves
# use cnt 0 (exact, blocking PTP).
STREAM_CNT = 10


class KukaRobot(RobotBase):
    """Drives the physical robot through the EKI (kukapy).

    ``joint_offsets`` (A1..A6, degrees) shifts the physical home relative to the
    core's virtual joint space. It is subtracted from joint targets on the way out
    and added back to the read pose on the way in, so the core's joint space (and
    its comparelist arrival check) stays consistent. The sim is never offset.
    """

    def __init__(self, port: int = 18735, joint_offsets: List[float] = None,
                 res_timeout: Optional[float] = None,
                 res_timeout_blocking: Optional[float] = None):
        self._port = port
        self._robot = None
        self._joint_offsets = list(joint_offsets) if joint_offsets else [0.0] * 6
        # EKI staleness timeouts (seconds). Left as None to use the kukapy
        # Robot defaults (res_timeout=10.0 for stream moves/queries,
        # res_timeout_blocking=60.0 for blocking PTPs, which hold the <Res>
        # until the robot arrives). Pass a value or None to override per
        # deployment; None for res_timeout_blocking disables that guard.
        self._res_timeout = res_timeout
        self._res_timeout_blocking = res_timeout_blocking
        # Serializes every EKI (kukapy) call. The KUKAPY server is a strict
        # single-flight request/response loop (KUKAPY_SERVER.SRC: ``WAIT FOR
        # $FLAG[1]`` -> one command -> one <Res>). Concurrent send/recv on the
        # socket from the stream-move thread and the tick loop desyncs it and
        # strands the KRL at "WAIT FOR $FLAG[1]" (the "waiting for flag" error).
        self._lock = threading.Lock()

    def connect(self) -> None:
        try:
            from kukapy.robot import Robot
        except ImportError as exc:
            raise RuntimeError(
                "kukapy is not installed. Run `pip install git+https://github.com/"
                "JasonLvernex/KukaPy.git` or use --sim."
            ) from exc
        kwargs = {}
        if self._res_timeout is not None:
            kwargs["res_timeout"] = self._res_timeout
        if self._res_timeout_blocking is not None:
            kwargs["res_timeout_blocking"] = self._res_timeout_blocking
        self._robot = Robot(port=self._port, **kwargs)
        self._robot.connect()

    def disconnect(self) -> None:
        if self._robot is not None:
            try:
                self._robot.disconnect()
            finally:
                self._robot = None

    def get_curjpos(self) -> List[float]:
        with self._lock:
            raw = list(self._robot.get_curjpos())
        return [raw[i] + self._joint_offsets[i] for i in range(len(raw))]

    def get_curpos(self) -> List[float]:
        with self._lock:
            return list(self._robot.get_curpos())

    def move_joint(self, pose: List[float], speed: float, blocking: bool = True) -> None:
        shifted = [pose[i] - self._joint_offsets[i] for i in range(len(pose))]
        self._move(shifted, speed, blocking, linear=False)

    def move_linear(self, pose: List[float], speed: float, blocking: bool = True) -> None:
        self._move(pose, speed, blocking, linear=True)

    def recover(self) -> None:
        """Attempt to recover a dead EKI connection: disconnect + reconnect.

        Called by the engine's watchdog when a ``KukaError`` is raised from a
        kukapy call (stalled handshake, dead socket, or framing desync). The
        reconnect re-establishes the EKI session so the engine can continue
        without a full restart. Raises the original error if the reconnect fails.
        """
        if self._robot is None:
            return
        try:
            self._robot.disconnect()
        except Exception:
            pass
        self._robot = None
        try:
            self.connect()
            print("[kuka] EKI connection recovered")
        except Exception as exc:
            print(f"[kuka] EKI recovery failed: {exc}")
            raise

    def set_joint_offsets(self, offsets: List[float]) -> None:
        """Update the joint home offset (hot-reload from the robot config)."""
        self._joint_offsets = list(offsets)

    def _move(self, pose: List[float], speed: float,
              blocking: bool, linear: bool) -> None:
        """Issue a move to the controller.

        ``blocking=True`` (the legacy behaviour): exact positioning (cnt 0), so the
        KRL's PTP holds until the robot arrives and the caller blocks for the whole
        move.

        ``blocking=False`` (stream): continuous positioning (``STREAM_CNT`` > 0), so
        the KRL answers immediately (``PTP ... C_PTP``) and the move runs on a daemon
        thread. The 20 Hz engine loop keeps streaming a fresh target each tick and
        the robot retargets mid-move, like the sim.

        Every kukapy call (this move, and the tick loop's ``get_curjpos`` /
        ``get_curpos``) runs under :attr:`_lock`: the KUKAPY server is a strict
        single-flight request/response loop, so the EKI traffic must be serialized
        or the socket desyncs and the KRL strands at "WAIT FOR $FLAG[1]".
        """
        cnt = 0 if blocking else STREAM_CNT

        def _do() -> None:
            # Preserve the exact legacy call forms: the joint move is issued
            # without a ``linear`` kwarg; the cartesian move with ``linear=True``.
            with self._lock:
                if linear:
                    self._robot.move("pose", list(pose), speed, linear=True, cnt_val=cnt)
                else:
                    self._robot.move("joint", list(pose), speed, cnt_val=cnt)

        if blocking:
            _do()
            return
        t = threading.Thread(target=_do, daemon=True)
        t.start()
