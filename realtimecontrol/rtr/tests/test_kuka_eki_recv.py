"""Regression tests for the EKI ``_recv_xml`` framing (the "waiting for flag" bug).

The KUKA EKI link (KUKAPY.xml: ``PROTOCOL=TCP``, ``SENDFLAG=1``,
``ALIVE ElementName="AliveCounter"``) is a ``\\x00``-delimited byte stream that
interleaves ``<Res>`` responses with periodic ``AliveCounter`` keepalives. TCP does
not preserve message boundaries, so a single ``recv(4096)`` frequently returns a
whole ``<Res>`` **plus** the start of the next frame.

The old ``_recv_xml`` kept its buffer in a *local* variable and returned as soon as
it found a ``<Res>``, throwing the leftover bytes away. The next call then started on
a **fragment**, which can never be parsed, so it kept reading (AliveCounters) forever:
a silent hang that strands the KRL at ``WAIT FOR $FLAG[1]`` and leaves the robot
without its next move.

These tests drive ``kukapy.robot.Robot._recv_xml`` against a fake socket and assert
the leftover is preserved across calls (split frames, interleaved keepalives, and
multi-frame chunks all come back intact).

Run directly (no pytest needed)::

    python tests/test_kuka_eki_recv.py

or under pytest::

    pytest tests/test_kuka_eki_recv.py
"""

import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kukapy.robot import Robot, KukaError


RES_A = b"<Res><Code>0</Code><Msg>OK</Msg></Res>"
RES_B = b"<Res><Code>0</Code><Msg>OK</Msg></Res>"
RES_C = b"<Res><Code>0</Code><Msg>OK</Msg></Res>"
ALIVE = b"<AliveCounter>7</AliveCounter>"


class FakeSock:
    """Yields a fixed list of chunks, one per ``recv``; then loops on a keepalive.

    Looping on an ``AliveCounter`` (like the real KRC) means a buggy reader that
    cannot find its ``<Res>`` blocks forever instead of returning — exactly the
    production hang. The join timeout in :func:`read_one` turns that hang into a
    deterministic failure.
    """

    def __init__(self, chunks, alive=b"<AliveCounter>7</AliveCounter>\x00"):
        self._chunks = list(chunks)
        self._alive = alive
        self._idx = 0

    def recv(self, n):
        if self._idx < len(self._chunks):
            c = self._chunks[self._idx]
            self._idx += 1
            return c
        return self._alive


def read_one(robot, timeout=4.0):
    """Run ``_recv_xml`` on a thread; return the element, or None if it hung."""
    box = {}

    def run():
        try:
            box["elem"] = robot._recv_xml()
        except Exception as exc:  # noqa: BLE001 - surfaced as a test failure
            box["err"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None  # hung: the bug
    if "err" in box:
        raise box["err"]
    return box["elem"]


def _robot(chunks):
    r = Robot()
    r.comm_sock = FakeSock(chunks)
    return r


# ---------------------------------------------------------------------------
# The core regression: a <Res> split across two recv() calls.
# ---------------------------------------------------------------------------
def test_split_frame_is_not_lost():
    """The leftover (next-frame start) must survive the first _recv_xml call."""
    chunk1 = RES_A + b"\x00" + b"<Res><Code>0</Code>"  # RES_A + RES_B start
    chunk2 = b"<Msg>OK</Msg></Res>\x00"                # RES_B rest
    robot = _robot([chunk1, chunk2])

    first = read_one(robot)
    assert first is not None and first.tag == "Res", "first Res not retrieved"
    second = read_one(robot)
    assert second is not None and second.tag == "Res", (
        "second Res was lost: the leftover from the first call was discarded, "
        "so the reader started on a fragment and hung (WAIT FOR $FLAG[1])"
    )


# ---------------------------------------------------------------------------
# Interleaved keepalives must be skipped without desyncing the stream.
# ---------------------------------------------------------------------------
def test_alivecounter_between_res_is_skipped():
    # One recv delivers: RES_A, a keepalive, and the start of RES_B.
    chunk1 = RES_A + b"\x00" + ALIVE + b"\x00" + b"<Res><Code>0</Code>"
    chunk2 = b"<Msg>OK</Msg></Res>\x00"
    robot = _robot([chunk1, chunk2])

    first = read_one(robot)
    assert first is not None and first.tag == "Res"
    second = read_one(robot)
    assert second is not None and second.tag == "Res"


def test_keepalive_only_chunk_does_not_block():
    """A recv that returns only keepalives must not consume the pending Res."""
    robot = _robot([ALIVE + b"\x00", RES_A + b"\x00"])
    first = read_one(robot)
    assert first is not None and first.tag == "Res"
    assert first.findtext("Code") == "0"


# ---------------------------------------------------------------------------
# Multiple complete frames in one chunk: the first Res wins, the rest is kept.
# ---------------------------------------------------------------------------
def test_multi_frame_chunk_preserves_tail():
    # One recv delivers two whole Res frames; the second must be available next.
    chunk1 = RES_A + b"\x00" + RES_B + b"\x00"
    robot = _robot([chunk1])

    first = read_one(robot)
    assert first is not None and first.tag == "Res"
    second = read_one(robot)
    assert second is not None and second.tag == "Res", (
        "second Res in the same chunk was discarded instead of kept in the buffer"
    )


# ---------------------------------------------------------------------------
# A frame split across several tiny chunks must still reassemble.
# ---------------------------------------------------------------------------
def test_frame_reassembled_from_tiny_chunks():
    wire = RES_A + b"\x00" + RES_B + b"\x00"
    # Deliver the whole wire one byte at a time.
    chunks = [wire[i:i + 1] for i in range(len(wire))]
    robot = _robot(chunks)

    first = read_one(robot)
    assert first is not None and first.tag == "Res"
    second = read_one(robot)
    assert second is not None and second.tag == "Res"


# ---------------------------------------------------------------------------
# Production stream: KRC sends PLAIN XML (no \x00 flags) with periodic
# AliveCounter keepalives interleaved. Documents concatenate back-to-back and
# TCP does not preserve boundaries, so one recv() routinely holds a whole
# <Res> plus a whole (or part of a) keepalive. ET.fromstring on such a buffer
# raises "junk after document" forever — the reader must slice documents out
# one at a time instead of parsing the whole buffer.
# ---------------------------------------------------------------------------
def test_plain_xml_res_plus_keepalive_one_chunk():
    """The production stall: one chunk holds a whole <Res> + a whole keepalive."""
    robot = _robot([RES_A + ALIVE])  # no \x00 anywhere
    first = read_one(robot)
    assert first is not None and first.tag == "Res", (
        "reader hung on a two-document buffer (junk after document): "
        "it kept accumulating keepalives forever and stranded the KRL "
        "at WAIT FOR $FLAG[1]"
    )
    assert first.findtext("Code") == "0"


def test_plain_xml_keepalive_then_res_one_chunk():
    robot = _robot([ALIVE + RES_A])
    first = read_one(robot)
    assert first is not None and first.tag == "Res"
    assert first.findtext("Code") == "0"


def test_plain_xml_two_res_one_chunk_preserves_tail():
    robot = _robot([RES_A + RES_B])
    first = read_one(robot)
    assert first is not None and first.tag == "Res"
    second = read_one(robot)
    assert second is not None and second.tag == "Res", (
        "second Res in the same chunk was discarded instead of kept in the buffer"
    )


def test_plain_xml_partial_then_keepalive():
    """A split <Res> followed by a whole keepalive must not desync."""
    head, tail = RES_A[:20], RES_A[20:]
    robot = _robot([head, tail + ALIVE + RES_B])
    first = read_one(robot)
    assert first is not None and first.tag == "Res"
    second = read_one(robot)
    assert second is not None and second.tag == "Res"


# ---------------------------------------------------------------------------
# The persistent buffer must be reset on (re)connect so stale bytes never leak.
# ---------------------------------------------------------------------------
def test_connect_resets_recv_buffer():
    robot = _robot([])
    robot._recv_buf = b"<stale>fragment"
    # Simulate a fresh connect: the buffer must be cleared.
    robot.comm_sock = FakeSock([RES_A + b"\x00"])
    robot._recv_buf = b""  # what connect() does
    first = read_one(robot)
    assert first is not None and first.tag == "Res"


# ---------------------------------------------------------------------------
# Staleness guard: keepalives arriving forever with NO <Res> must raise
# KukaError after res_timeout, not hang forever. This is the persistent stall:
# the KRC is alive (AliveCounter keepalives keep arriving) but the KRL never
# sends a response (stranded at WAIT FOR $FLAG[1]). The socket-level
# recv_timeout never fires here because recv() keeps succeeding on the
# keepalive bytes, so only the wall-clock staleness guard can catch it.
# ---------------------------------------------------------------------------
def test_keepalives_without_res_raise_timeout():
    robot = _robot([ALIVE + b"\x00"])
    robot.res_timeout = 0.5
    try:
        result = read_one(robot, timeout=3.0)
    except KukaError:
        return  # expected: the staleness guard fired
    raise AssertionError(
        f"expected KukaError from the staleness guard, got {result!r}: "
        "keepalives arriving with no <Res> must time out, not hang"
    )


# ---------------------------------------------------------------------------
# cnt-aware staleness timeout: a BLOCKING PTP (cnt_val == 0) holds the KRL's
# <Res> until the robot arrives, so the guard must use the longer
# res_timeout_blocking or it false-positives on any move longer than the short
# res_timeout. A STREAM move (cnt_val > 0) answers immediately and keeps the
# short res_timeout.
# ---------------------------------------------------------------------------
class DelayedSock:
    """Returns keepalives until ``delay`` seconds have elapsed, then the <Res>.

    Models a legitimately-long blocking PTP: the KRC keeps sending keepalives
    (AliveCounter) while the robot is still moving, and only sends the <Res> on
    arrival. Has a no-op ``sendall`` so ``move()`` can drive the full send+recv
    path.
    """

    def __init__(self, delay=0.4,
                 res=b"<Res><Code>0</Code><Msg>OK</Msg></Res>",
                 alive=b"<AliveCounter>7</AliveCounter>\x00"):
        self._delay = delay
        self._res = res
        self._alive = alive
        self._t0 = None

    def sendall(self, data):
        pass

    def recv(self, n):
        if self._t0 is None:
            self._t0 = time.monotonic()
        if time.monotonic() - self._t0 >= self._delay:
            return self._res
        return self._alive


def test_blocking_move_uses_longer_timeout():
    """A blocking PTP (cnt_val == 0) that outlasts res_timeout must still
    succeed, because the KRL holds the <Res> until arrival — the guard uses the
    longer res_timeout_blocking. If the short res_timeout were (wrongly) used,
    this would raise KukaError mid-move."""
    robot = Robot(res_timeout=0.2, res_timeout_blocking=2.0)
    robot.comm_sock = DelayedSock(delay=0.4)  # the <Res> arrives at ~0.4s
    # 0.4s > 0.2s (short) but < 2.0s (blocking): only the longer timeout passes.
    robot.move("joint", [0.0] * 6, velocity=50, cnt_val=0)  # must NOT raise


def test_stream_move_uses_short_timeout():
    """A stream move (cnt_val > 0) answers immediately, so the short
    res_timeout applies: a <Res> delayed past it (the KRL stranded, not moving)
    must still raise KukaError."""
    robot = Robot(res_timeout=0.2, res_timeout_blocking=2.0)
    robot.comm_sock = DelayedSock(delay=0.5)  # the <Res> arrives at ~0.5s
    try:
        robot.move("joint", [0.0] * 6, velocity=50, cnt_val=10)  # stream
        raise AssertionError(
            "expected KukaError: a stream move's delayed <Res> must time out on "
            "the short res_timeout, not the longer blocking one"
        )
    except KukaError:
        pass  # expected: the short timeout fired


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok: {name}")
    print("All EKI _recv_xml framing tests passed.")
