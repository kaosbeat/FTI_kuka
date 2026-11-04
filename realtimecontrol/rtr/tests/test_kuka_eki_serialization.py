"""Tests for the KUKA EKI serialization fix (the "waiting for flag" bug).

The KUKAPY server (KUKAPY_SERVER.SRC) is a strict single-flight request/response
loop: ``WAIT FOR $FLAG[1]`` -> one command -> one ``<Res>``. Every EKI call
(``get_curjpos`` / ``get_curpos`` / ``move``) is one such round-trip on a single
socket. In stream mode the engine runs each move via ``asyncio.to_thread`` (the
wrapper's ``_move`` is synchronous; the move's ``<Res>`` round-trip is serialized
with the tick-loop pose reads by the wrapper lock). Without serialization the
request/response pairing desyncs and the KRL strands at ``WAIT FOR $FLAG[1]`` (the
"waiting for flag" error).

These tests run the wrapper against a fake kukapy robot (no hardware) and assert:

- all EKI calls are serialized (at most one in flight at a time), even when a stream
  move and the tick-loop pose reads race;
- stream moves use continuous positioning (``cnt_val > 0``) so the KRL answers
  immediately and the lock is held briefly;
- block moves keep exact positioning (``cnt_val == 0``).

Run directly (no pytest needed)::

    python tests/test_kuka_eki_serialization.py

or under pytest::

    pytest tests/test_kuka_eki_serialization.py
"""

import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.robot.kuka import KukaRobot, STREAM_CNT


class FakeKukapyRobot:
    """Stands in for ``kukapy.robot.Robot``.

    Each method simulates the single-flight EKI round-trip (a short hold) and records
    how many round-trips are in flight at once. If two EKI calls ever overlap,
    ``max_in_flight`` exceeds 1 -- exactly the race that desyncs the KUKAPY server.
    """

    def __init__(self, round_trip: float = 0.002):
        self._round_trip = round_trip
        self._in_flight = 0
        self._bk = threading.Lock()  # protects the bookkeeping counters only
        self.max_in_flight = 0
        self.move_calls = []  # [(move_type, cnt_val, linear), ...]
        self._move_done = threading.Event()

    def _enter(self) -> None:
        with self._bk:
            self._in_flight += 1
            if self._in_flight > self.max_in_flight:
                self.max_in_flight = self._in_flight

    def _exit(self) -> None:
        with self._bk:
            self._in_flight -= 1

    def _rt(self) -> None:
        time.sleep(self._round_trip)

    def get_curjpos(self):
        self._enter()
        self._rt()
        self._exit()
        return [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]

    def get_curpos(self):
        self._enter()
        self._rt()
        self._exit()
        return [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]

    def move(self, move_type, vals, velocity=25, acceleration=100,
             cnt_val=0, linear=False, **kw):
        self._enter()
        self._rt()
        self.move_calls.append((move_type, cnt_val, bool(linear)))
        self._move_done.set()
        self._exit()

    def wait_move(self, timeout: float = 5.0) -> bool:
        return self._move_done.wait(timeout)


def _make_robot() -> tuple:
    rob = KukaRobot()
    fake = FakeKukapyRobot()
    rob._robot = fake
    return rob, fake


# ---------------------------------------------------------------------------
# Serialization: a stream move racing the tick-loop pose reads must not overlap.
# ---------------------------------------------------------------------------
def test_eki_calls_are_serialized():
    rob, fake = _make_robot()

    def fire_stream_move():
        rob.move_joint([0.0] * 6, 50, blocking=False)  # synchronous EKI round-trip

    t = threading.Thread(target=fire_stream_move)
    t.start()
    # The tick loop's pose reads, racing the stream move on the same socket.
    for _ in range(40):
        rob.get_curjpos()
        rob.get_curpos()
    t.join()

    # With the wrapper lock, at most one EKI round-trip is in flight at a time.
    assert fake.max_in_flight == 1, (
        f"EKI calls overlapped (max_in_flight={fake.max_in_flight}); the KUKAPY "
        "server would desync and strand at WAIT FOR $FLAG[1]"
    )


def test_pose_reads_are_serialized():
    rob, fake = _make_robot()

    def reader():
        for _ in range(20):
            rob.get_curjpos()
            rob.get_curpos()

    threads = [threading.Thread(target=reader) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert fake.max_in_flight == 1, (
        f"pose reads overlapped (max_in_flight={fake.max_in_flight})"
    )


# ---------------------------------------------------------------------------
# Positioning mode: stream = continuous (cnt_val > 0), block = exact (cnt_val == 0).
# ---------------------------------------------------------------------------
def test_stream_move_uses_continuous_positioning():
    rob, fake = _make_robot()

    rob.move_joint([0.0] * 6, 50, blocking=False)  # stream
    assert fake.wait_move(), "stream move never reached the (fake) controller"
    move_type, cnt_val, linear = fake.move_calls[0]
    assert move_type == "joint" and linear is False
    assert cnt_val == STREAM_CNT and cnt_val > 0, (
        f"stream move should be continuous (cnt_val>0), got cnt_val={cnt_val}"
    )


def test_stream_linear_move_uses_continuous_positioning():
    rob, fake = _make_robot()

    rob.move_linear([0.0] * 6, 50, blocking=False)  # stream cartesian
    assert fake.wait_move(), "stream linear move never reached the (fake) controller"
    move_type, cnt_val, linear = fake.move_calls[0]
    assert move_type == "pose" and linear is True
    assert cnt_val == STREAM_CNT and cnt_val > 0, (
        f"stream linear move should be continuous (cnt_val>0), got cnt_val={cnt_val}"
    )


def test_block_move_uses_exact_positioning():
    rob, fake = _make_robot()

    rob.move_joint([0.0] * 6, 50, blocking=True)  # block
    move_type, cnt_val, linear = fake.move_calls[0]
    assert move_type == "joint" and linear is False
    assert cnt_val == 0, (
        f"block move should be exact (cnt_val==0), got cnt_val={cnt_val}"
    )


def test_stream_move_is_synchronous():
    """A stream move runs the EKI round-trip synchronously (no daemon thread).

    The wrapper's ``_move`` is synchronous in both modes; the engine runs it off the
    asyncio loop via ``asyncio.to_thread``. The move's round-trip still happens
    inside the call (the KRL answers immediately for a C_PTP move), so the call
    takes at least the round-trip time.
    """
    rob, fake = _make_robot()
    fake._round_trip = 0.05  # make the round-trip noticeably long

    start = time.perf_counter()
    rob.move_joint([0.0] * 6, 50, blocking=False)
    elapsed = time.perf_counter() - start

    assert elapsed >= 0.04, (
        f"stream move returned in {elapsed*1000:.1f} ms; it should run the EKI "
        "round-trip synchronously (no daemon thread)"
    )


# ---------------------------------------------------------------------------
# Dead-socket translation and EKI recovery (no-quit) tests.
# ---------------------------------------------------------------------------
#
# A dropped connection leaves ``self._robot`` as a ``Robot`` with
# ``comm_sock = None``. The next kukapy call then raises a raw
# ``AttributeError("'NoneType' object has no attribute 'sendall'")``, which is
# NOT a ``KukaError`` — the engine watchdog never recovers and the engine spins
# forever. The fix translates that to ``KukaError`` (via ``_eki_call``) and
# rewrites ``recover()`` to do a soft resync (clear ``_recv_buf`` + ``ping``)
# instead of sending ``quit`` (Type 8), which would kill the KRL permanently.


class FakeSocket:
    """A socket object that records close/sendall/recv calls."""
    def __init__(self, alive=True):
        self._alive = alive
        self.closed = False
        self.recv_buf = b""

    def close(self):
        self.closed = True

    def sendall(self, data):
        if not self._alive:
            raise OSError("socket is closed")

    def recv(self, n):
        if not self._alive:
            raise OSError("socket is closed")
        return self.recv_buf[:n]


class FakeKukapyRobotWithSocket:
    """A kukapy Robot that models the socket layer for recovery tests.

    Tracks whether ``ping`` and ``disconnect`` were called so the tests can
    assert the recovery path chose soft resync (ping) over quit (disconnect).
    """

    def __init__(self, alive=True):
        self.comm_sock = FakeSocket(alive) if alive else None
        self._recv_buf = b"<Res><Code>0</Code></Res>"  # stale leftover frame
        self.ping_called = False
        self.disconnect_called = False
        self._alive = alive

    def ping(self):
        if not self._alive:
            raise OSError("socket is closed")
        self.ping_called = True

    def disconnect(self):
        self.disconnect_called = True
        if self.comm_sock is not None:
            self.comm_sock.close()
        self.comm_sock = None

    def connect(self):
        # Simulate a successful re-listen (the KRL dials back in).
        self.comm_sock = FakeSocket(alive=True)
        self._alive = True

    def get_curjpos(self):
        # Simulate the dead-socket AttributeError when the link is dead.
        if self.comm_sock is None or not self._alive:
            raise AttributeError(
                "'NoneType' object has no attribute 'sendall'"
            )
        return [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]

    def get_curpos(self):
        if self.comm_sock is None or not self._alive:
            raise AttributeError(
                "'NoneType' object has no attribute 'sendall'"
            )
        return [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]

    def move(self, move_type, vals, velocity=25, acceleration=100,
             cnt_val=0, linear=False, **kw):
        if self.comm_sock is None or not self._alive:
            raise AttributeError(
                "'NoneType' object has no attribute 'sendall'"
            )


def _make_robot_with_socket(alive=True):
    """Create a KukaRobot with a socket-aware fake kukapy robot."""
    from kukapy.robot import KukaError
    rob = KukaRobot()
    fake = FakeKukapyRobotWithSocket(alive=alive)
    rob._robot = fake
    return rob, fake


def test_eki_call_translates_dead_socket_to_kuka_error():
    """A dead socket (comm_sock=None) must raise KukaError, not AttributeError.

    The engine watchdog only recovers on KukaError. If a dead link raises a raw
    AttributeError, the watchdog never retries and the engine spins forever.
    """
    from kukapy.robot import KukaError
    rob, fake = _make_robot_with_socket(alive=True)
    # Simulate the link dropping: comm_sock is set to None (as after a drop).
    fake.comm_sock = None
    fake._alive = False
    try:
        rob.get_curjpos()
        raise AssertionError(
            "expected KukaError from a dead socket, got success: "
            "a dead link must be recoverable, not a silent AttributeError"
        )
    except KukaError as exc:
        assert "EKI socket dead" in str(exc), (
            f"expected 'EKI socket dead' in the KukaError message, got: {exc}"
        )
    except AttributeError as exc:
        raise AssertionError(
            f"got raw AttributeError instead of KukaError: {exc}. "
            "The engine watchdog only recovers on KukaError, so a raw "
            "AttributeError means the engine will spin forever."
        )


def test_eki_call_raises_kuka_error_when_robot_is_none():
    """When self._robot is None, _eki_call must raise KukaError."""
    from kukapy.robot import KukaError
    rob = KukaRobot()  # _robot is None by default
    try:
        rob.get_curjpos()
        raise AssertionError(
            "expected KukaError when robot is None, got success"
        )
    except KukaError as exc:
        assert "EKI not connected" in str(exc), (
            f"expected 'EKI not connected' in the KukaError message, got: {exc}"
        )


def test_recover_soft_resync_does_not_quit():
    """recover() on a live link must do a soft resync (clear _recv_buf + ping),
    NOT send quit (disconnect). Quitting kills the KRL permanently."""
    rob, fake = _make_robot_with_socket(alive=True)
    # Pre-condition: the link is alive (comm_sock is not None).
    assert fake.comm_sock is not None
    # Pre-condition: there is a stale leftover frame in the buffer.
    assert fake._recv_buf != b""

    rob.recover()

    # The stale buffer must be cleared.
    assert fake._recv_buf == b"", (
        f"expected _recv_buf to be cleared after soft resync, got: {fake._recv_buf!r}"
    )
    # ping() must have been called (re-establishes the request/response pairing).
    assert fake.ping_called, (
        "expected ping() to be called during soft resync: "
        "the KRL must answer a ping to re-sync the pairing"
    )
    # disconnect() must NOT have been called (no quit).
    assert not fake.disconnect_called, (
        "recover() must NOT send quit (disconnect) on a live link: "
        "the KRL EXITs on quit and will not come back"
    )
    # The zero-streak counter must be reset.
    assert rob._zero_streak == 0, (
        f"expected _zero_streak to be reset to 0 after recover, got: {rob._zero_streak}"
    )
    # The link must still be alive.
    assert fake.comm_sock is not None


def test_recover_hard_reconnect_when_link_dead():
    """recover() on a dead link must do a hard reconnect (close socket + re-listen),
    NOT send quit. The KRL is already gone; quit would kill a live KRL."""
    rob, fake = _make_robot_with_socket(alive=False)
    # Pre-condition: the link is dead (comm_sock is None).
    assert fake.comm_sock is None
    fake._alive = False

    # Mock connect() so it doesn't try to listen on a real port.
    connected = []
    def mock_connect():
        # Simulate a successful re-listen: set _robot to a new fake.
        rob._robot = FakeKukapyRobotWithSocket(alive=True)
        rob._robot.ping_called = True  # mark as reconnected
        connected.append(True)
    orig_connect = rob.connect
    rob.connect = mock_connect
    try:
        rob.recover()
    finally:
        rob.connect = orig_connect

    # disconnect() must NOT have been called (no quit).
    assert not fake.disconnect_called, (
        "recover() must NOT send quit even on a dead link: "
        "a quit would kill a live KRL that dialed back in"
    )
    # After recover, the robot must be reconnected (comm_sock is not None).
    assert rob._robot is not None, (
        "expected _robot to be set after hard reconnect"
    )
    assert rob._robot.comm_sock is not None, (
        "expected the link to be re-established after hard reconnect"
    )
    # The zero-streak counter must be reset.
    assert rob._zero_streak == 0
    # connect() must have been called.
    assert connected, "expected connect() to be called during hard reconnect"


def test_connect_resets_robot_on_failure():
    """connect() must reset self._robot to None if the underlying Robot.connect()
    fails, so no half-initialized Robot (comm_sock=None) is left behind."""
    import kukapy.robot as kukapy_robot
    orig_robot = kukapy_robot.Robot

    class FailingRobot:
        def __init__(self, **kw):
            pass

        def connect(self):
            raise OSError("connection refused")

        def disconnect(self):
            pass

    try:
        kukapy_robot.Robot = FailingRobot
        rob = KukaRobot()
        try:
            rob.connect()
            raise AssertionError("expected connect() to raise, got success")
        except (OSError, RuntimeError):
            pass
        # The critical assertion: no half-initialized Robot left behind.
        assert rob._robot is None, (
            f"expected _robot to be None after a failed connect, got: {rob._robot!r}. "
            "A half-initialized Robot with comm_sock=None causes an AttributeError "
            "loop in the engine."
        )
    finally:
        kukapy_robot.Robot = orig_robot


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok: {name}")
    print("All KUKA EKI serialization tests passed.")
