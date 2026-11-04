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

# KukaError is imported lazily inside get_curjpos (same pattern as the Robot
# class in connect) so the sim runs without kukapy installed.
_KukaError = None
def _get_kuka_error():
    global _KukaError
    if _KukaError is None:
        from kukapy.robot import KukaError
        _KukaError = KukaError
    return _KukaError

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
        # EKI stale-read detector: the KUKAPY server's CASE 4 (movej) responds
        # with Res/A1-A6 = 0.0 (the loop's initialization values), because CASE 4
        # never writes Res/A1-A6 — only CASE 2 (curjpos) does. If that zero-filled
        # move <Res> lands in _recv_buf and gets misread as the next tick's
        # curjpos response, the engine sees [0,0,0,0,0,0] raw (→ [0,0,0,0,0,90]
        # with the J6 offset). The robot physically moved, but the readback is
        # stale. Track consecutive all-zero raw reads; when the streak reaches the
        # threshold, raise KukaError so the engine's watchdog triggers recover().
        self._zero_streak = 0
        self._zero_streak_threshold = 3

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
        try:
            self._robot.connect()
        except Exception:
            # A failed connect must not leave a half-initialized Robot object
            # (comm_sock = None) behind: the next tick would then raise a raw
            # AttributeError deep in kukapy, which the engine watchdog does not
            # treat as recoverable. Reset to None so the link state is clean.
            self._robot = None
            raise

    def disconnect(self) -> None:
        if self._robot is not None:
            try:
                self._robot.disconnect()
            finally:
                self._robot = None

    def _eki_call(self, method: str, *args, **kwargs):
        """Run a kukapy ``Robot`` method, translating a dead/None link to KukaError.

        A dropped connection leaves ``self._robot`` as a ``Robot`` object with
        ``comm_sock = None``; the next call then raises
        ``AttributeError("'NoneType' object has no attribute 'sendall'")`` deep in
        kukapy. That is NOT a ``KukaError``, so the engine's watchdog (which only
        recovers on ``KukaError``) never retries and the engine spins forever.
        Translating every dead-link failure to ``KukaError`` here makes the link
        recoverable from the watchdog's existing path.
        """
        if self._robot is None:
            KukaError = _get_kuka_error()
            raise KukaError("EKI not connected (robot is None)")
        fn = getattr(self._robot, method)
        try:
            return fn(*args, **kwargs)
        except AttributeError as exc:
            msg = str(exc)
            # The dead-socket signature: comm_sock is None, so kukapy tries to
            # call sendall/recv on it. Translate to KukaError (recoverable).
            if "NoneType" in msg and ("sendall" in msg or "recv" in msg):
                KukaError = _get_kuka_error()
                raise KukaError(f"EKI socket dead: {msg}") from exc
            raise

    def get_curjpos(self) -> List[float]:
        with self._lock:
            raw = list(self._eki_call("get_curjpos"))
        # Stale-read detection: the KUKAPY server's move response (CASE 4) carries
        # Res/A1-A6 = 0.0 (initialization values). If that zero-filled <Res> is
        # misread as a curjpos response, raw is all zeros. Track consecutive all-zero
        # reads; when the streak reaches the threshold, raise KukaError so the
        # engine's watchdog triggers recover() (reconnect clears _recv_buf).
        all_zero = all(abs(v) < 1e-6 for v in raw)
        if all_zero:
            self._zero_streak += 1
            if self._zero_streak >= self._zero_streak_threshold:
                KukaError = _get_kuka_error()
                raise KukaError(
                    f"EKI stale read: raw curjpos all-zero for "
                    f"{self._zero_streak} consecutive reads (threshold "
                    f"{self._zero_streak_threshold}); move <Res> desync suspected"
                )
        else:
            self._zero_streak = 0
        return [raw[i] + self._joint_offsets[i] for i in range(len(raw))]

    def get_curpos(self) -> List[float]:
        with self._lock:
            return list(self._eki_call("get_curpos"))

    def move_joint(self, pose: List[float], speed: float, blocking: bool = True) -> None:
        shifted = [pose[i] - self._joint_offsets[i] for i in range(len(pose))]
        self._move(shifted, speed, blocking, linear=False)

    def move_linear(self, pose: List[float], speed: float, blocking: bool = True) -> None:
        self._move(pose, speed, blocking, linear=True)

    def recover(self) -> None:
        """Recover a dead or desynced EKI link.

        Two cases:

        1. **Stale read** (local desync, KRL alive): the KRL is still connected
           and responding, but a zero-filled move ``<Res>`` was mis-paired as a
           curjpos response. A **soft resync** (clear the receive buffer + ping)
           drops the mis-paired frame and re-establishes the request/response
           pairing WITHOUT quitting the KRL. This keeps the robot running.

        2. **Dead link** (socket closed, KRL gone): a **hard reconnect** (close
           the local socket + re-listen). The KUKAPY_SERVER on the teach pendant
           must be restarted to dial back in; the re-listen blocks until it does.

        Neither path sends the ``quit`` (Type 8) command: the KRL EXITs on quit
        (KUKAPY_SERVER.SRC CASE 8) and will not come back, which is what made the
        old disconnect+reconnect leave the engine in an unrecoverable
        ``AttributeError`` loop. Raises the original error if the reconnect fails.
        """
        # Case 1: soft resync (the link is alive but mis-paired).
        if self._robot is not None and self._robot.comm_sock is not None:
            try:
                self._robot._recv_buf = b""  # drop any mis-paired leftover frame
                self._robot.ping()           # sets $FLAG[1]; KRL answers, pairing re-syncs
                self._zero_streak = 0
                print("[kuka] EKI soft-resync OK (link alive, pairing re-synced)")
                return
            except Exception:
                pass  # link is dead; fall through to the hard reconnect
        # Case 2: hard reconnect (close the local socket, re-listen). No quit —
        # the KRL is already gone, and a quit would kill a live KRL and never
        # return it.
        if self._robot is not None:
            try:
                if self._robot.comm_sock is not None:
                    self._robot.comm_sock.close()
            except Exception:
                pass
            self._robot = None
        try:
            self.connect()
            self._zero_streak = 0
            print("[kuka] EKI connection recovered")
        except Exception as exc:
            print(f"[kuka] EKI recovery failed: {exc}")
            raise

    def set_joint_offsets(self, offsets: List[float]) -> None:
        """Update the joint home offset (hot-reload from the robot config)."""
        self._joint_offsets = list(offsets)

    def _move(self, pose: List[float], speed: float,
              blocking: bool, linear: bool) -> None:
        """Issue a move to the controller (synchronous, under :attr:`_lock`).

        ``blocking=True``: exact positioning (cnt 0), so the KRL's PTP holds until
        the robot arrives and the caller blocks for the whole move.

        ``blocking=False`` (stream): continuous positioning (``STREAM_CNT`` > 0), so
        the KRL answers immediately (``PTP ... C_PTP``) and the move returns
        quickly. The 20 Hz engine loop keeps streaming a fresh target each tick and
        the robot retargets mid-move, like the sim.

        The move is issued **synchronously** (no daemon thread): the caller is
        responsible for running it off the asyncio loop (the engine's stream dispatch
        uses ``asyncio.to_thread``). Every kukapy call (this move, and the tick
        loop's ``get_curjpos`` / ``get_curpos``) runs under :attr:`_lock`: the
        KUKAPY server is a strict single-flight request/response loop, so the EKI
        traffic must be serialized or the socket desyncs and the KRL strands at
        "WAIT FOR $FLAG[1]".
        """
        cnt = 0 if blocking else STREAM_CNT
        # Preserve the exact legacy call forms: the joint move is issued without a
        # ``linear`` kwarg; the cartesian move with ``linear=True``.
        with self._lock:
            if linear:
                self._eki_call("move", "pose", list(pose), speed, linear=True, cnt_val=cnt)
            else:
                self._eki_call("move", "joint", list(pose), speed, cnt_val=cnt)
