#!/usr/bin/env python3
"""EKI desync diagnostic for the real KUKA robot (run ON the hardware).

The bug
-------
In manual mode the robot physically moves and the ghost/target arm tracks, but the
non-ghost arm (``s.joints`` = ``get_curjpos``) freezes at ``[0,0,0,0,0,0]`` and the
zone button sticks. The KUKAPY server (``KUKAPY_SERVER.SRC``) is a strict
single-flight loop: every command gets exactly one ``<Res>``, and every ``<Res>``
is pre-filled with ``A1..A6 = 0.0`` — only CASE 2 (``curjpos``) overwrites
``A1..A6`` with ``$AXIS_ACT``. CASE 3 (``curpos``) sets ``X..OC``; CASE 4/5
(``movej``/``movep``) sets neither.

So ``get_curjpos()`` returning ``[0,0,0,0,0,0]`` means the client read a
``<Res>`` that belongs to a *move* or *curpos* command — the single-flight
response stream has shifted by one frame. This script reproduces the engine's
exact tick pattern while logging every EKI frame and classifying each ``<Res>``
by its fields, so we can see which command's response leaked into
``get_curjpos``.

Safety
------
The robot is in MANUAL mode (operator control). This script sends ONLY:
  * reads (ping / curjpos / curpos) — no motion, and
  * with ``--move``: no-op joint moves whose target is the pose read at the
    start (the robot is already there, so the move is a no-op).
It never sends ``quit`` (cmd 8) by default. ``quit`` is the ONE command that
makes the KRL ``EXIT`` (KUKAPY_SERVER.SRC CASE 8) and stop KUKAPY_SERVER, and
the KRL has no reconnect loop, so an automatic quit + reconnect cannot work.
``--clean-exit`` sends ``quit`` for a clean KRL shutdown (the same teardown the
engine does on Ctrl+C, main.py:315) if you want KUKAPY_SERVER stopped cleanly.

How to run
----------
From the ``realtimecontrol/rtr`` directory. Python is the TCP *server* (it
listens on the port and the KRC dials in — see ``kukapy.robot.Robot.connect``),
so the running engine (which also listens on this port) must be stopped first,
and KUKAPY_SERVER must be (re)started on the teach pendant so the KRC connects
to THIS script instead of the engine.

    # 1) stop the engine (Ctrl+C on the main.py --robot shell)
    # 2) start this script — it listens and waits for the KRC:
    python scripts/eki_desync_diag.py --port 18735
    # 3) on the teach pendant: STOP + START KUKAPY_SERVER (it connects in)
    #    (if it is still "running" but stranded from the engine, stop it first)

    # add a no-op move repro (target = current pose, so the robot does not move):
    python scripts/eki_desync_diag.py --port 18735 --move

Phases
------
1. READ-ONLY baseline: ping + interleaved curjpos/curpos reads (the engine's
   read order), from a FRESH KRL session. If curjpos is already all-zero here
   while the robot is away from home, the desync is *persistent* (KRL-side or
   systemic) — a fresh session did not clear it.
2. MOVE repro (``--move`` only): the engine tick pattern (curjpos + curpos +
   stream movej cnt=10) with a *no-op* move (target = the pose read at the
   start) so the move EKI traffic is exercised without moving the robot.
   Skipped automatically if that pose read is all-zero (we would not know the
   real pose to target). If curjpos goes all-zero partway through, the move
   stream REPRODUCED the desync and the first zero tick is where the ``<Res>``
   stream shifted.
"""

import argparse
import os
import sys
import time

# Make ``kukapy`` importable the same way the real system does. It is not pip
# installed; the package lives at ``realtimecontrol/rtr/KukaPy/kukapy``.
_RTR_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_RTR_ROOT, os.path.join(_RTR_ROOT, "KukaPy")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from kukapy.robot import Robot, KukaError  # noqa: E402


# ---------------------------------------------------------------------------
# Frame classification
# ---------------------------------------------------------------------------

def _a16(elem):
    return [float(elem.findtext(f"A{i}", "0") or 0) for i in range(1, 7)]


def _x6(elem):
    return [float(elem.findtext(k, "0") or 0) for k in ("X", "Y", "Z", "OA", "OB", "OC")]


def classify(elem) -> str:
    """Classify a <Res> by its field pattern (the KRL never tags the response).

    Only CASE 2 (curjpos) sets A1..A6. CASE 3 (curpos) sets X..OC and leaves
    A1..A6 at 0. CASE 4/5 (move) sets neither. So the field pattern identifies
    which command produced the response even though the <Res> has no type field.

    Caveat: a *real* curjpos read while the robot is at home (all joints 0) is
    indistinguishable from a move's <Res> by values alone — both are all-zero.
    The classification is reliable when the robot is away from home, which is
    the bug state we are diagnosing.
    """
    code = (elem.findtext("Code") or "?").strip()
    msg = (elem.findtext("Msg") or "").strip()
    a = _a16(elem)
    x = _x6(elem)
    if code == "0" and msg == "pong":
        return "PING"
    if any(v != 0.0 for v in a):
        return "CURJPOS"   # only CASE 2 sets A1..A6 -> a real joint read
    if any(v != 0.0 for v in x):
        return "CURPOS"    # CASE 3: X..OC set, A1..A6 left at 0
    return "MOVE"          # CASE 4/5: A1..A6=0 and X..OC=0


def _is_zero(v) -> bool:
    return all(abs(x) < 1e-9 for x in v)


# ---------------------------------------------------------------------------
# Frame logger: wrap the Robot's low-level send/recv to number every frame
# ---------------------------------------------------------------------------

class FrameLogger:
    """Shadows ``Robot._send_xml`` / ``Robot._recv_xml`` to log each frame.

    A single shared sequence number is used for sends and recvs, so a healthy
    stream reads ``S:001 R:001 S:002 R:002 ...`` (one response per command, in
    order). A shift shows up as a RECV whose classification does not match the
    command that was just sent.
    """

    def __init__(self, robot: Robot):
        self.robot = robot
        self.n = 0
        self._send = robot._send_xml
        self._recv = robot._recv_xml
        robot._send_xml = self.send
        robot._recv_xml = self.recv

    def send(self, elem):
        self.n += 1
        code = elem.findtext("Type")
        cnt = elem.findtext("Cnt")
        a = [elem.findtext(f"A{i}", "0") for i in range(1, 7)]
        print(f"  S:{self.n:03d} SEND Type={code} Cnt={cnt} A1..A6={[round(float(v),1) for v in a]}")
        self._send(elem)

    def recv(self, res_timeout=None):
        t0 = time.monotonic()
        elem = self._recv(res_timeout=res_timeout)
        self.n += 1
        cls = classify(elem)
        a = _a16(elem)
        x = _x6(elem)
        code = (elem.findtext("Code") or "?").strip()
        # The tell: a curjpos read that comes back as a MOVE or CURPOS frame is
        # the desync (the caller asked for a joint read but consumed a move's
        # <Res>, which has A1..A6 = 0). ``leftover`` is the byte count left in
        # the recv buffer after this frame — a large leftover means several
        # documents coalesced into one TCP segment.
        leftover = len(self.robot._recv_buf)
        print(f"  R:{self.n:03d} RECV <{cls}> Code={code} "
              f"A1..A6={[round(v,1) for v in a]} X..OC={[round(v,1) for v in x]} "
              f"leftover={leftover}B ({time.monotonic()-t0:.3f}s)")
        return elem


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

def phase_readonly(robot, tag, reads):
    """Ping + interleaved curjpos/curpos reads (the engine's read order).

    Returns the list of curjpos reads so the caller can judge zeros.
    """
    print(f"\n--- {tag} ---")
    try:
        robot.ping()
        print("  ping OK")
    except KukaError as exc:
        print(f"  ping FAILED: {exc}")
        return None

    reads_out = []
    for i in range(1, reads + 1):
        j = robot.get_curjpos()
        p = robot.get_curpos()
        jz = _is_zero(j)
        pz = _is_zero(p)
        mark_j = "  <-- ZERO" if jz else ""
        mark_p = "  <-- ZERO" if pz else ""
        print(f"  #{i} curjpos={_fmt(j)}{mark_j}")
        print(f"     curpos ={_fmt(p)}{mark_p}")
        reads_out.append(j)
    return reads_out


def phase_move(robot, ticks, velocity):
    """Engine tick pattern with a NO-OP move (target = the pose read at start).

    Exercises the exact stream-move EKI traffic (movej cnt=10) without moving
    the robot: the target is the pose read at the start, so a C_PTP to the
    current position is a no-op. Safe only if that pose read is real (not
    desynced); skipped automatically if it is all-zero.
    """
    print(f"\n--- PHASE 2: move repro (no-op moves, cnt=10) ---")
    home = robot.get_curjpos()
    if _is_zero(home):
        print("  SKIPPED: curjpos is all-zero (desynced) so we do not know the real")
        print("            pose to target a no-op move. Restart KUKAPY_SERVER on the")
        print("            pendant, re-run, then add --move to reproduce from a clean state.")
        return None
    print(f"  no-op move target (current pose) = {_fmt(home)}")
    reads_out = []
    for t in range(1, ticks + 1):
        j = robot.get_curjpos()
        p = robot.get_curpos()
        robot.move("joint", list(home), velocity=velocity, cnt_val=10)
        jz = _is_zero(j)
        mark = "  <-- ZERO" if jz else ""
        print(f"  tick#{t} curjpos={_fmt(j)}{mark}")
        reads_out.append(j)
    return reads_out


def _fmt(v):
    return "[" + " ".join(f"{x:.1f}" for x in v) + "]"


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------

def teardown(robot, clean_exit: bool) -> None:
    """Close the session without (by default) sending ``quit``.

    Default (``clean_exit=False``): close the socket WITHOUT sending ``quit``.
    The KRL has no reconnect loop and only ``cmd 8`` (quit) makes it ``EXIT``,
    so we leave KUKAPY_SERVER parked at WAIT FOR $FLAG[1] rather than killing
    it. To resume: on the pendant, STOP + restart KUKAPY_SERVER, then restart
    the engine.

    ``clean_exit=True``: send ``quit`` (cmd 8) + close. The KRL does a clean
    EXIT + EKI_CLOSE + EKI_CLEAR — the same teardown the engine does on Ctrl+C
    (main.py:315). To resume: start KUKAPY_SERVER on the pendant, then restart
    the engine.
    """
    try:
        if clean_exit:
            robot.disconnect()  # sends quit + closes the socket
        else:
            if robot.comm_sock is not None:
                robot.comm_sock.close()
                robot.comm_sock = None
            robot._recv_buf = b""
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="EKI desync diagnostic (real robot)")
    ap.add_argument("--port", type=int, default=18735, help="EKI port (default 18735)")
    ap.add_argument("--move", action="store_true",
                    help="add the no-op move repro (target = current pose)")
    ap.add_argument("--ticks", type=int, default=20, help="ticks for --move (default 20)")
    ap.add_argument("--reads", type=int, default=5, help="reads per phase (default 5)")
    ap.add_argument("--velocity", type=int, default=10,
                   help="move velocity %% for --move (default 10)")
    ap.add_argument("--timeout", type=int, default=60,
                   help="seconds to wait for the KRC to connect (default 60)")
    ap.add_argument("--clean-exit", action="store_true",
                   help="send quit on exit (clean KRL shutdown); default leaves KRL parked")
    args = ap.parse_args()

    print("=" * 72)
    print(" EKI DESYNC DIAGNOSTIC — real robot")
    print(f" port={args.port}  move={args.move}  reads={args.reads}  "
          f"clean_exit={args.clean_exit}  timeout={args.timeout}s")
    print("=" * 72)
    print("The robot is in MANUAL mode. This script sends only reads and, with")
    print("--move, no-op moves to the current pose. It does NOT send quit by")
    print(f"default (clean_exit={args.clean_exit}).")
    print()
    print("  1) The engine must be STOPPED (it also listens on this port).")
    print("  2) This script is now listening — start KUKAPY_SERVER on the")
    print("     teach pendant (stop + start it) so the KRC connects in.")
    print("Press Enter to continue.")
    input()

    robot = Robot(port=args.port, socket_timeout=args.timeout)
    try:
        robot.connect()
    except Exception as exc:
        print(f"connect FAILED: {exc}")
        print("Start KUKAPY_SERVER on the pendant and re-run.")
        return 1
    print(f"connected (KRC from {robot.comm_sock.getpeername()})")

    log = FrameLogger(robot)
    summary = {}

    # ---- Phase 1: read-only baseline -------------------------------------
    p1 = phase_readonly(robot, "PHASE 1: read-only baseline (no moves)", args.reads)
    if p1 is not None:
        zeros = sum(1 for j in p1 if _is_zero(j))
        summary["phase1"] = (zeros, len(p1))
        print(f"  PHASE 1: curjpos all-zero in {zeros}/{len(p1)} reads "
              f"-> {'DESYNC ACTIVE (persistent)' if zeros else 'ok'}")

    # ---- Phase 2: move repro (optional, no-op moves) ----------------------
    if args.move:
        p2 = phase_move(robot, args.ticks, args.velocity)
        if p2 is not None:
            zeros = sum(1 for j in p2 if _is_zero(j))
            summary["phase2"] = (zeros, len(p2))
            first = next((i for i, j in enumerate(p2, 1) if _is_zero(j)), None)
            print(f"  PHASE 2: curjpos all-zero in {zeros}/{len(p2)} ticks "
                  f"{'(first at tick ' + str(first) + ')' if first else ''}")

    # ---- Summary ----------------------------------------------------------
    print("\n" + "=" * 72)
    print(" SUMMARY")
    print("=" * 72)
    for key, label in (("phase1", "read-only"), ("phase2", "no-op moves")):
        if key in summary:
            z, m = summary[key]
            print(f"  {label:18s}: curjpos all-zero in {z}/{m} reads")

    p1z, p1m = summary.get("phase1", (0, 0))
    p2z, p2m = summary.get("phase2", (0, 0))

    print("\n CONCLUSION:")
    if p1m > 0 and p1z > 0:
        print(f"  PHASE 1 (read-only): curjpos all-zero in {p1z}/{p1m} reads from a FRESH")
        print("  KRL session. If the robot is away from home, this is a PERSISTENT")
        print("  desync (KRL-side or systemic) — a fresh session did NOT clear it.")
        print("  Capture the R: frames: the leaked <Res> classification (MOVE/CURPOS)")
        print("  tells you exactly which command's response shifted into get_curjpos.")
        if p2m > 0:
            if p2z == 0:
                print(f"  PHASE 2 (no-op moves): curjpos was REAL in all {p2m} ticks — the")
                print("  move stream did NOT trigger a desync from a clean state.")
            else:
                print(f"  PHASE 2 (no-op moves): curjpos went all-zero in {p2z}/{p2m} ticks —")
                print("  the move stream REPRODUCED the desync. The first zero tick is")
                print("  where the <Res> stream shifted.")
    elif p1m > 0 and p1z == 0:
        print(f"  PHASE 1 (read-only): curjpos was REAL in all {p1m} reads — no desync")
        print("  was present from a fresh KRL session. The bug is either intermittent")
        print("  or specific to the engine's running state (client-side). Re-run while")
        print("  the robot is in the stuck state, or use --move to try to trigger it.")
    else:
        print("  PHASE 1 failed (ping or connect error) — inspect the output above.")

    print("\n NEXT: paste this whole output back. The key lines are the R: frames")
    print(" around the first all-zero curjpos: a <MOVE> or <CURPOS> frame returned by")
    print(" a get_curjpos() call is the one-frame shift, and its X..OC values tell you")
    print(" whether it was a move's or a curpos's <Res>.")

    # ---- Teardown ---------------------------------------------------------
    teardown(robot, args.clean_exit)
    if args.clean_exit:
        print("\n[teardown] sent quit — KUKAPY_SERVER stopped cleanly. To resume:")
        print("  start KUKAPY_SERVER on the pendant, then restart the engine.")
    else:
        print("\n[teardown] closed WITHOUT quit — KUKAPY_SERVER is parked (not killed).")
        print("To resume: on the pendant, STOP + restart KUKAPY_SERVER, then restart")
        print("the engine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
