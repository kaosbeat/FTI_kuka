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
        return [0.0] * 6

    def get_curpos(self):
        self._enter()
        self._rt()
        self._exit()
        return [0.0] * 6

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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok: {name}")
    print("All KUKA EKI serialization tests passed.")
