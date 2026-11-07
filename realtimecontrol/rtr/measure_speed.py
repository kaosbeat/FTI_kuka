"""Standalone KUKA speed-measurement tool (terminal, no web UI).

Measures the real angular-speed profile of each axis — and of all axes combined —
so you can drop a measured profile into ``robotconfig.json`` (``axis_max_deg_s``)
and the perform-mode speed model.

How it works
------------
The KUKA controller does the motion planning: you command a PTP move with a
velocity % (1-100) and an acceleration % (1-100), and the controller walks each
axis along a trapezoidal (or triangular) profile up to its configured max. This
tool commands a move at the given % (default 100/100 = "as fast as possible") and
samples ``get_curjpos()`` as fast as the single-flight EKI link allows, recording
``(t, pose)``. From the position-vs-time trace it recovers, per axis:

- **top speed** (deg/s): the plateau of the velocity curve (trapezoidal) or the
  peak (triangular — a *lower bound* on the true max; make the move longer to get
  a real plateau).
- **acceleration** (deg/s^2): ``top_speed / ramp_time`` (robust for both profile
  shapes — for a triangular move it equals the controller's real max accel).
- **braking** (deg/s^2): ``top_speed / decel_time``.

It borrows the EKI connection (the vendored ``kukapy.Robot``) but is NOT part of
``main.py`` and has no web interface — it is a plain terminal program.

Usage
-----
    python measure_speed.py --readme               # full in-program guide
    python measure_speed.py --dry-run              # print the plan (no movement)
    python measure_speed.py --sim                  # validate the pipeline (no robot)
    python measure_speed.py                        # real robot, default port
    python measure_speed.py --amplitude 60 --out speed.json

Starting pose & confirmation
-----------------------------
The robot must start from a known pose. Set ``START_POSE`` in the CONFIG
section below, then run the tool: it reads the robot's LIVE pose, prints it
(raw + app/virtual), and asks you to CONFIRM the robot is there before any
movement. The per-axis targets are printed too, so you know exactly where the
robot will go.

A6 +90 offset
--------------
The app (``rtr.robot.kuka.KukaRobot``) works in a *virtual* joint space = raw
EKI pose + ``joint_offsets_deg`` (A6 carries a +90 calibration, see
``robotconfig.json``). This tool works in **RAW** EKI space: it reads/writes the
physical axis angles and clamps to the physical hardware limits. A constant
offset cancels in the velocity difference, so the measured speeds are identical
in either space. The tool prints a note explaining what is and is not offset.

Safety
------
Each test starts from the robot's CURRENT pose and moves only ``--amplitude``
degrees on the axis under test (clamped to the KR60 hardware joint limits), then
returns to the start pose. Press Ctrl-C at any time to stop and park the robot.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
# Make ``rtr`` and the vendored ``kukapy`` importable regardless of the CWD.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_KUKA_DIR = os.path.join(ROOT, "KukaPy")
if _KUKA_DIR not in sys.path:
    sys.path.insert(0, _KUKA_DIR)

from rtr.robot.sim import SimRobot
from rtr.state.zones import HARDWARE_LIMITS

AXIS_NAMES = ["A1", "A2", "A3", "A4", "A5", "A6"]
# Continuous-positioning count for stream (non-blocking) moves: the KRL answers
# immediately so the sampling loop can start (mirrors rtr.robot.kuka.STREAM_CNT).
STREAM_CNT = 10
# A move is treated as "arrived" when every axis is within this of the target.
ARRIVAL_EPS = 0.5  # degrees
# The safe speed (%) used to park the robot between tests.
PARK_VEL = 20
PARK_ACC = 20


# ---------------------------------------------------------------------------
# CONFIG (edit these for your robot)
# ---------------------------------------------------------------------------
# The starting pose (RAW EKI joint angles, degrees) the robot must be at before
# the tests run. The tool reads the robot's LIVE pose, prints it (raw + app),
# and asks you to CONFIRM the robot is here before any movement. Set this to the
# pose you want to measure from.
START_POSE = [0.0, -135.0, 45.0, 0.0, 30.0, 0.0]
# Tolerance (degrees) for the "is the robot at the starting pose" self-check.
# If any axis is further than this from START_POSE, the tool warns loudly.
START_EPS = 1.0
# The app's joint_offsets_deg (from robotconfig.json): the app's *virtual* joint
# space = raw EKI pose + this offset. A6 carries a +90 calibration. This tool
# works in RAW space (see the module docstring); this is printed so you can map
# between the app's display and what this tool reads/sends. Keep in sync with
# robotconfig.json's "joint_offsets_deg".
JOINT_OFFSETS_DEG = [0.0, 0.0, 0.0, 0.0, 0.0, 90.0]


# ---------------------------------------------------------------------------
# Pure analysis (no hardware) — takes a list of (t, pose[6]) samples.
# ---------------------------------------------------------------------------

def _moving_average(values, window):
    """Centred moving average (shrinks toward the nearest end at the boundaries)."""
    n = len(values)
    if n == 0:
        return []
    half = window // 2
    out = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        seg = values[lo:hi]
        out.append(sum(seg) / len(seg))
    return out


def analyze_run(samples, axis):
    """Recover top speed / acceleration / braking for ``axis`` from a move trace.

    ``samples`` is a list of ``(t, pose)`` where ``t`` is seconds and ``pose`` is
    the 6-axis joint angles in degrees. Returns a dict with the metrics
    (``top_speed``, ``accel``, ``brake``, ``profile``, ``duration``, ``n``) plus the
    smoothed velocity curve (``v_curve``) for inspection.

    The velocity is the interval difference of the position trace, smoothed with a
    short moving average to kill per-sample jitter. Top speed is the (smoothed)
    velocity peak. Acceleration is ``top_speed / ramp_time`` where ``ramp_time`` is
    the time from the first captured sample until the velocity first reaches 95% of
    the peak — this equals the controller's real max accel for a triangular move and
    the ramp rate for a trapezoidal one. Braking is ``top_speed / decel_time`` where
    ``decel_time`` runs from the peak until the velocity last drops below 5% of the
    peak. The profile is "trapezoidal" if the velocity holds near the peak for three
    consecutive samples (a real plateau); otherwise "triangular" (the move was too
    short to reach the max, so ``top_speed`` is a lower bound).
    """
    if len(samples) < 3:
        return {"error": f"too few samples ({len(samples)})", "n": len(samples)}
    ts = [s[0] for s in samples]
    pos = [s[1][axis] for s in samples]
    duration = ts[-1] - ts[0]
    # Interval velocities (deg/s), timed at the interval midpoint.
    t_mid, v = [], []
    for i in range(1, len(samples)):
        dt = ts[i] - ts[i - 1]
        if dt <= 0:
            continue
        t_mid.append((ts[i] + ts[i - 1]) / 2.0)
        v.append((pos[i] - pos[i - 1]) / dt)
    if len(v) < 3:
        return {"error": "not enough velocity intervals", "n": len(samples)}
    v_smooth = _moving_average(v, 5)
    top_speed = max(v_smooth)
    if top_speed <= 0:
        return {"error": "no positive motion captured", "n": len(samples)}
    # Acceleration: time from the first captured sample until the velocity first
    # reaches 95% of the peak, then top_speed / that time.
    ramp_t = None
    for i, vv in enumerate(v_smooth):
        if vv >= 0.95 * top_speed:
            ramp_t = t_mid[i] - t_mid[0]
            break
    accel = (top_speed / ramp_t) if (ramp_t is not None and ramp_t > 0) else None
    # Braking: from the END of the plateau (the last sample still near the peak —
    # where the decel ramp actually begins) until the velocity last drops below 5%
    # of the peak; top_speed / that time is the decel rate. Measuring from the start
    # of the plateau would wrongly fold the whole constant-speed phase into the brake.
    end_plateau_i = -1
    for i in range(len(v_smooth)):
        if v_smooth[i] >= 0.95 * top_speed:
            end_plateau_i = i
    stop_t = None
    for i in range(len(v_smooth) - 1, -1, -1):
        if v_smooth[i] <= 0.05 * top_speed:
            stop_t = t_mid[i]
            break
    brake = (top_speed / (stop_t - t_mid[end_plateau_i])) if (
        stop_t is not None and stop_t > t_mid[end_plateau_i]) else None
    # Trapezoidal vs triangular: a trapezoidal move holds the peak for a real plateau;
    # a triangular one peaks and turns around (so top_speed is a *lower bound* on the
    # true max). Compare the measured duration to the minimum duration of a pure
    # triangular profile at the measured peak (2 * top_speed / accel — the time to
    # ramp up and back down). If the move ran meaningfully longer than that minimum,
    # there is a real plateau (trapezoidal); otherwise the axis never held the peak.
    if accel:
        t_tri = 2.0 * top_speed / accel
        profile = "trapezoidal" if duration > 1.1 * t_tri else "triangular"
    else:
        # No measurable ramp (e.g. the sim's constant-velocity profile): flat = plateau.
        profile = "trapezoidal"
    return {
        "top_speed": round(top_speed, 2),
        "accel": round(accel, 2) if accel is not None else None,
        "brake": round(brake, 2) if brake is not None else None,
        "profile": profile,
        "duration": round(duration, 3),
        "n": len(samples),
        "v_curve": [(round(t, 3), round(vv, 2)) for t, vv in zip(t_mid, v_smooth)],
    }


# ---------------------------------------------------------------------------
# Drivers: the real KUKA (vendored kukapy) and the headless sim.
# ---------------------------------------------------------------------------

class RealDriver:
    """The physical KUKA via the vendored kukapy EKI client (raw, no offsets)."""

    def __init__(self, ip, port):
        from kukapy.robot import Robot
        # The KRC dials in; the host/IP are informational (the listener binds 0.0.0.0).
        self._robot = Robot(port=port)
        self._ip = ip

    def connect(self):
        self._robot.connect()

    def disconnect(self):
        self._robot.disconnect()

    def get_curjpos(self):
        return list(self._robot.get_curjpos())

    def move(self, pose, velocity, acceleration, cnt):
        self._robot.move("joint", list(pose), velocity=velocity,
                         acceleration=acceleration, cnt_val=cnt)

    def clock(self):
        return time.monotonic()


class SimDriver:
    """The headless SimRobot, to validate the measurement pipeline with no hardware.

    The sim treats the ``velocity`` argument as deg/s (not a %), so a velocity of
    100 means 100 deg/s; its accel is fixed (100 deg/s^2). Enough to exercise the
    sampling + analysis code end to end. The clock is the sim's own timeline (one
    tick per ``get_curjpos`` call) so the recovered velocity is consistent with how
    the sim advances the pose.
    """

    def __init__(self, tick_hz=200.0, accel_deg_s2=100.0):
        self._tick_hz = float(tick_hz)
        self._sim = SimRobot(tick_hz=self._tick_hz, accel_deg_s2=accel_deg_s2)
        self._n = 0

    def connect(self):
        self._sim.connect()

    def disconnect(self):
        self._sim.disconnect()

    def get_curjpos(self):
        self._n += 1
        return list(self._sim.get_curjpos())

    def move(self, pose, velocity, acceleration, cnt):
        self._sim.move_joint(list(pose), speed=max(0.0, float(velocity)))

    def clock(self):
        return self._n / self._tick_hz


# ---------------------------------------------------------------------------
# Test runner.
# ---------------------------------------------------------------------------

def sample_run(driver, target, velocity, acceleration, timeout):
    """Command a stream move to ``target`` and sample the pose until arrival/timeout.

    Returns the list of ``(t, pose)`` samples (``t`` from ``driver.clock()``).
    """
    driver.move(target, velocity, acceleration, cnt=STREAM_CNT)
    samples = []
    last_good = None
    t_start = driver.clock()
    while True:
        pose = driver.get_curjpos()
        t = driver.clock()
        # Lightweight stale-read guard: skip an all-zero frame when the last accepted
        # read was non-zero (the mis-paired move-<Res> signature on the real EKI).
        all_zero = all(abs(x) < 1e-6 for x in pose)
        if all_zero and last_good is not None and not all(abs(x) < 1e-6 for x in last_good):
            continue
        last_good = list(pose)
        samples.append((t, list(pose)))
        if all(abs(pose[i] - target[i]) <= ARRIVAL_EPS for i in range(len(target))):
            break
        if t - t_start > timeout:
            print(f"  [warn] timeout after {t - t_start:.2f}s; parking at target")
            break
    return samples


def park(driver, pose, timeout=15.0):
    """Move to ``pose`` at a safe speed and poll until the robot is there (real + sim)."""
    driver.move(pose, PARK_VEL, PARK_ACC, cnt=STREAM_CNT)
    t_start = driver.clock()
    while True:
        p = driver.get_curjpos()
        if all(abs(p[i] - pose[i]) <= ARRIVAL_EPS for i in range(len(pose))):
            return
        if driver.clock() - t_start > timeout:
            print(f"  [warn] park timeout; continuing")
            return


def _clamp_target(home, axes, amplitude):
    """Target pose = home shifted by +amplitude on ``axes``, clamped to HW limits."""
    target = list(home)
    for axis in axes:
        lo, hi = HARDWARE_LIMITS[axis]
        target[axis] = min(hi, max(lo, home[axis] + amplitude))
    return target


def print_offset_note():
    """Explain what the A6 +90 offset does and does not affect in this tool."""
    if all(abs(x) < 1e-9 for x in JOINT_OFFSETS_DEG):
        print("\n=== joint offset ===")
        print("No joint offsets configured (all zero) — raw space == app space.")
        return
    off = ", ".join(f"{AXIS_NAMES[i]}={JOINT_OFFSETS_DEG[i]:+.0f}"
                    for i in range(6) if JOINT_OFFSETS_DEG[i])
    print("\n=== joint offset (A6 +90 calibration) ===")
    print(f"Configured offsets: {off}  (app virtual = raw + offset)")
    print("This tool works in RAW EKI joint space (what get_curjpos() returns and")
    print("what it sends to the controller). The app's joint_offsets_deg are NOT")
    print("applied to the poses it reads, sends, or clamps to. Concretely:")
    print("  - the poses printed here are the PHYSICAL axis angles (raw);")
    print("  - the app's display = raw + offset (e.g. app A6 = raw A6 + 90);")
    print("  - HARDWARE_LIMITS are the physical (raw) joint limits, so the safety")
    print("    clamp is correct in raw space;")
    print("  - the measured speeds are IDENTICAL in either space: a constant offset")
    print("    cancels in the velocity difference, so top_speed / accel / brake are")
    print("    the same whether you read raw or app (virtual) space.")


def confirm_start(driver, args):
    """Self-check + operator confirmation that the robot is at START_POSE.

    Reads the robot's live RAW pose, compares it to the configured START_POSE,
    prints both (raw + app/virtual equivalents) and the per-axis delta, and asks
    the operator to confirm before any movement. Returns True to proceed, False
    to abort. (Skipped in --sim mode by the caller: the sim has no physical
    robot to verify.)
    """
    print("\n=== starting-pose check (before any movement) ===")
    print_offset_note()
    exp = [float(x) for x in START_POSE]
    cur = [float(x) for x in driver.get_curjpos()]
    exp_v = [exp[i] + JOINT_OFFSETS_DEG[i] for i in range(6)]
    cur_v = [cur[i] + JOINT_OFFSETS_DEG[i] for i in range(6)]
    print(f"expected start (RAW):        {[round(x, 1) for x in exp]}")
    print(f"expected start (app/virt):   {[round(x, 1) for x in exp_v]}   (raw + offset)")
    print(f"current pose   (RAW):        {[round(x, 1) for x in cur]}")
    print(f"current pose   (app/virt):   {[round(x, 1) for x in cur_v]}   (raw + offset)")
    delta = [abs(cur[i] - exp[i]) for i in range(6)]
    maxd = max(delta)
    print("per-axis |current - expected| (raw): "
          + "  ".join(f"{AXIS_NAMES[i]}={delta[i]:.1f}" for i in range(6)))
    if maxd > START_EPS:
        print(f"  !! WARNING: the robot is {maxd:.1f} deg away from the expected start "
              f"(tolerance {START_EPS} deg). Verify before continuing.")
    else:
        print(f"  OK: the robot is within {START_EPS} deg of the expected start pose.")
    if args.yes:
        print("  (--yes: skipping interactive confirmation)")
        return True
    ans = input("  Is the robot at the starting pose? (y/N): ").strip().lower()
    return ans in ("y", "yes")


def run_all(driver, args, axes):
    """Run the individual-axis and (optionally) combined tests; return the metrics."""
    home = driver.get_curjpos()
    print(f"home pose: {[round(x, 1) for x in home]}")
    results = {"individual": {}, "combined": None}

    # Individual axis tests: one axis moves by +amplitude, the rest hold.
    for axis in axes:
        park(driver, home)
        target = _clamp_target(home, [axis], args.amplitude)
        moved = target[axis] - home[axis]
        if abs(moved) < 1.0:
            print(f"  {AXIS_NAMES[axis]}: no room to move (clamped); skip")
            continue
        print(f"  {AXIS_NAMES[axis]}: {home[axis]:.1f} -> {target[axis]:.1f} deg "
              f"({moved:.1f} deg)")
        samples = sample_run(driver, target, args.velocity, args.acceleration, args.timeout)
        results["individual"][AXIS_NAMES[axis]] = analyze_run(samples, axis)

    # Combined test: all six axes move by +amplitude at once (coordinated move).
    if args.combined:
        park(driver, home)
        target = _clamp_target(home, list(range(6)), args.amplitude)
        print(f"  combined: -> {[round(x, 1) for x in target]}")
        samples = sample_run(driver, target, args.velocity, args.acceleration, args.timeout)
        results["combined"] = {AXIS_NAMES[i]: analyze_run(samples, i) for i in range(6)}

    park(driver, home)
    return results


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------

def _fmt(x):
    return "-" if x is None else f"{x:.1f}"


def print_report(results, args):
    print("\n=== measured profile ===")
    print(f"velocity={args.velocity}%  acceleration={args.acceleration}%  "
          f"amplitude={args.amplitude} deg  device={'sim' if args.sim else 'kuka'}")

    hdr = f"{'axis':<6}{'top (deg/s)':>14}{'accel (deg/s^2)':>18}{'brake (deg/s^2)':>18}  profile"
    print(f"\n-- individual axes --\n{hdr}")
    for name in AXIS_NAMES:
        m = results["individual"].get(name)
        if not m or m.get("error"):
            print(f"{name:<6}{'(skipped)':>14}")
            continue
        print(f"{name:<6}{m['top_speed']:>14.1f}{_fmt(m['accel']):>18}"
              f"{_fmt(m['brake']):>18}  {m['profile']}")

    if results["combined"]:
        print(f"\n-- all axes combined --\n{hdr}")
        for name in AXIS_NAMES:
            m = results["combined"][name]
            if m.get("error"):
                print(f"{name:<6}{'(error)':>14}  {m['error']}")
                continue
            print(f"{name:<6}{m['top_speed']:>14.1f}{_fmt(m['accel']):>18}"
                  f"{_fmt(m['brake']):>18}  {m['profile']}")

    # The drop-in: the 6 individual top speeds (the trapezoidal plateaus, or the best
    # lower bounds) go straight into robotconfig.json's axis_max_deg_s. Axes that were
    # NOT measured (skipped for lack of room, or an error) come out as 0.0 and are
    # flagged — a 0.0 must never be copied into the config (it would disable that axis).
    dropin, missing = [], []
    for name in AXIS_NAMES:
        m = results["individual"].get(name)
        if m and not m.get("error"):
            dropin.append(m["top_speed"])
        else:
            dropin.append(0.0)
            missing.append(name)
    print(f"\n-- drop-in for robotconfig.json --\n"
          f"  \"axis_max_deg_s\": {[round(v, 1) for v in dropin]}")
    if missing:
        print(f"  !! INCOMPLETE — {', '.join(missing)} were not measured; the 0.0 for "
              f"those axes is a PLACEHOLDER and must NOT be copied into the config. "
              f"Re-run from a pose with room on those axes.")
    accels = [m["accel"] for m in results["individual"].values()
              if m and m.get("accel")]
    if accels:
        print(f"  (max accel observed: {max(accels):.1f} deg/s^2 "
              f"— perform.json acceleration_max_deg_s2)")
    tri = [n for n in AXIS_NAMES
           if results["individual"].get(n)
           and results["individual"][n].get("profile") == "triangular"]
    if tri:
        print(f"  note: {', '.join(tri)} were TRIANGULAR (no plateau) — the top speed "
              f"is a lower bound; raise --amplitude to reach the true max.")


def write_profile(results, args):
    dropin, missing = [], []
    for name in AXIS_NAMES:
        m = results["individual"].get(name)
        if m and not m.get("error"):
            dropin.append(m["top_speed"])
        else:
            dropin.append(0.0)
            missing.append(name)
    profile = {
        "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "device": "sim" if args.sim else "kuka",
        "velocity_percent": args.velocity,
        "acceleration_percent": args.acceleration,
        "amplitude_deg": args.amplitude,
        "axes": results,
        "drop_in": {
            "axis_max_deg_s": [round(v, 1) for v in dropin],
            "complete": not missing,
            "missing_axes": missing,
        },
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    print(f"\nwrote profile to {args.out}")
    if missing:
        print(f"  !! INCOMPLETE — {', '.join(missing)} not measured; the 0.0 placeholders "
              f"are flagged in missing_axes and must not be used.")


def print_plan(args, axes):
    """Print the plan (no robot movement). Uses START_POSE as the assumed home, so
    you can see exactly where the robot will go before running for real."""
    home = [float(x) for x in START_POSE]
    print("dry run — no robot movement")
    print(f"device={'sim' if args.sim else 'kuka'}  ip={args.ip}  port={args.port}")
    print(f"velocity={args.velocity}%  acceleration={args.acceleration}%  "
          f"amplitude={args.amplitude} deg")
    print_offset_note()
    print(f"\nstarting pose (RAW, from CONFIG): {[round(x, 1) for x in home]}")
    print(f"  (app/virtual = raw + offset: "
          f"{[round(home[i] + JOINT_OFFSETS_DEG[i], 1) for i in range(6)]})")
    print("\nplanned individual tests (one axis moves +amplitude at a time, "
          "then returns):")
    for axis in axes:
        t = _clamp_target(home, [axis], args.amplitude)
        lo, hi = HARDWARE_LIMITS[axis]
        print(f"  {AXIS_NAMES[axis]}: -> {[round(x, 1) for x in t]}  "
              f"(clamped to [{lo}, {hi}])")
    if args.combined:
        t = _clamp_target(home, list(range(6)), args.amplitude)
        print(f"\nplanned combined test (all six axes at once): -> "
              f"{[round(x, 1) for x in t]}")
    print(f"\nfinal: park back at the starting pose "
          f"{[round(x, 1) for x in home]}")


def print_readme():
    """The full in-program usage guide (--readme)."""
    print("""
MEASURE_SPEED — KUKA per-axis speed measurement (standalone terminal tool)
==========================================================================

WHAT IT DOES
  Commands the KUKA to move each axis — and all axes combined — as fast as
  possible, samples the joint positions, and recovers the real angular speed
  profile: top speed (deg/s), acceleration (deg/s^2), braking (deg/s^2), and the
  profile shape (trapezoidal vs triangular). The result is a drop-in for
  robotconfig.json's axis_max_deg_s (the field the perform-mode speed model reads).

HOW IT WORKS
  - Borrows the EKI connection (vendored kukapy) but is NOT part of main.py and
    has no web UI — it is a plain terminal program.
  - Reads/writes RAW EKI joint angles (A1..A6, degrees). The app's A6 +90 offset
    (joint_offsets_deg) is NOT applied — the tool prints a note explaining what is
    and is not offset. Measured speeds are identical in raw or app space.
  - Each test starts from the current pose and moves only --amplitude degrees on
    the axis under test (clamped to the KR60 hardware joint limits), then returns.

USAGE
  python measure_speed.py --readme               # this guide
  python measure_speed.py --dry-run              # print the plan, no movement
  python measure_speed.py --sim                  # pipeline test (no hardware)
  python measure_speed.py                        # real robot, default port
  python measure_speed.py --combined --out speed.json

BEFORE RUNNING (real robot)
  1. Set START_POSE in the CONFIG section to the pose the robot is in.
  2. The tool reads the live pose, prints it (raw + app), and asks you to confirm
     the robot is there before any movement. The planned targets are printed too.
  3. Raise --amplitude until the report shows profile=trapezoidal for every axis.
     A triangular row means the move was too short to reach that axis's true max
     (its top_speed is a lower bound).

FLAGS
  --ip, --port         EKI connection (default 192.168.10.201:18735)
  --sim                run against the headless SimRobot (no hardware)
  --amplitude N        degrees each axis moves (default 45)
  --axes SPEC          axes to test individually (default all, e.g. '1,3' or '1-6')
  --combined           also run the all-axes-at-once test
  --velocity N         velocity % (default 100 = as fast as possible)
  --acceleration N     acceleration % (default 100)
  --timeout N          per-move timeout in seconds (default 20)
  --out FILE           write the measured profile to this JSON file
  --dry-run            print the plan without moving the robot
  --yes                skip the interactive starting-pose confirmation
  --readme             print this guide

SAFETY
  - Ctrl-C at any time stops and parks the robot.
  - The tool parks the robot at a safe speed between tests.
  - A 0.0 in the drop-in means that axis was NOT measured — NEVER copy a 0.0 into
    robotconfig.json (it would disable that axis in perform mode).
""")


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

# Holds the active driver so the SIGINT handler can stop the robot.
_DRIVER = [None]


def _on_sigint(signum, frame):
    print("\n[interrupted] stopping robot...")
    drv = _DRIVER[0]
    try:
        if drv is not None:
            p = drv.get_curjpos()
            drv.move(list(p), velocity=1, acceleration=1, cnt=0)  # blocking stop
    except Exception as exc:  # noqa: BLE001
        print(f"  (stop failed: {exc})")
    raise SystemExit(130)


def parse_axes(spec):
    """Parse a comma-separated axis spec ('1,2,3' or '1-6') into 0-based indices."""
    axes = set()
    for part in spec.replace("-", ", ").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            axes.update(range(int(a) - 1, int(b)))
        else:
            axes.add(int(part) - 1)
    axes = sorted(a for a in axes if 0 <= a < 6)
    return axes or list(range(6))


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Measure the KUKA's real angular speed profile (per axis + combined).")
    p.add_argument("--ip", default="192.168.10.201",
                   help="robot IP (informational; the KRC dials in)")
    p.add_argument("--port", type=int, default=18735, help="EKI port (default 18735)")
    p.add_argument("--sim", action="store_true",
                   help="run against the headless SimRobot (no hardware; validates the pipeline)")
    p.add_argument("--amplitude", type=float, default=45.0,
                   help="degrees each axis moves in a test (default 45)")
    p.add_argument("--axes", default="1,2,3,4,5,6",
                   help="axes to test individually (default all, e.g. '1,2,3' or '1-6')")
    p.add_argument("--combined", action="store_true",
                   help="also run the all-axes-at-once test")
    p.add_argument("--velocity", type=int, default=100,
                   help="velocity %% (default 100 = as fast as possible)")
    p.add_argument("--acceleration", type=int, default=100,
                   help="acceleration %% (default 100)")
    p.add_argument("--timeout", type=float, default=20.0,
                   help="per-move timeout in seconds (default 20)")
    p.add_argument("--out", default=None,
                   help="write the measured profile to this JSON file")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan without moving the robot")
    p.add_argument("--yes", action="store_true",
                   help="skip the interactive starting-pose confirmation")
    p.add_argument("--readme", action="store_true",
                   help="print the full in-program usage guide")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.readme:
        print_readme()
        return 0
    axes = parse_axes(args.axes)
    if args.dry_run:
        print_plan(args, axes)
        return 0

    driver = SimDriver() if args.sim else RealDriver(args.ip, args.port)
    _DRIVER[0] = driver
    signal.signal(signal.SIGINT, _on_sigint)
    print(f"connecting ({'sim' if args.sim else args.ip}:{args.port})...")
    driver.connect()
    try:
        # The starting-pose self-check + operator confirmation (real robot only;
        # the sim has no physical robot to verify, so it is skipped there).
        if not args.sim and not confirm_start(driver, args):
            print("aborted — the robot was not confirmed at the starting pose.")
            return 1
        results = run_all(driver, args, axes)
    finally:
        driver.disconnect()
    print_report(results, args)
    if args.out:
        write_profile(results, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
