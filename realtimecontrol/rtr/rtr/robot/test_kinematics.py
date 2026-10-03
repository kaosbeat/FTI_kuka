"""Self-test for the KR60 kinematics (P1).

Verifies, with no hardware:

1. **FK reach** — at known poses (home / rest / stretch) the tool lands at a
   physically plausible distance from the base (the KR60 reaches ~2.3 m).
2. **Jacobian consistency** — the analytic linear Jacobian ``J_v`` matches a
   finite-difference Jacobian computed from :meth:`Chain.fk`. This is the strong
   check: if the two agree, the FK and Jacobian are internally consistent.

Run:  python test_kinematics.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from rtr.robot.kinematics import Chain, load_chain, pinv3


def fk_pos(chain: Chain, j):
    return chain.fk(j)[0]


def finite_diff_jacobian(chain: Chain, j, h=1e-6):
    """Numerical linear Jacobian (3x6), derivative w.r.t. **radians** (matches the
    analytic geometric Jacobian, which is per-radian).

    Joint values are stored in degrees, so a ``h``-radian perturbation is
    ``h * 180/pi`` degrees; the result is divided by ``h`` (radians).
    """
    base = fk_pos(chain, j)
    deg_per_rad = 180.0 / math.pi
    cols = []
    for i in range(6):
        jp = list(j)
        jp[i] += h * deg_per_rad
        p = fk_pos(chain, jp)
        cols.append([(p[k] - base[k]) / h for k in range(3)])
    # cols are the 3 component differences; assemble into a 3x6.
    J = [[cols[i][r] for i in range(6)] for r in range(3)]
    return J


def max_err(a, b):
    n = len(a[0])
    return max(abs(a[r][i] - b[r][i]) for r in range(len(a)) for i in range(n))


def main() -> int:
    chain = load_chain()
    ok = True

    # --- 1. FK reach -------------------------------------------------------
    print("== FK reach (tool position in base frame, metres) ==")
    poses = {
        "home   [  0,-90, 90,  0, 90,  0]": [0, -90, 90, 0, 90, 0],
        "rest   [-3,-133,156, -2,  0,  0]": [-3, -133, 156, -2, 0, 0],
        "stretch[-3, -97, 16, -2,  0,  0]": [-3, -97, 16, -2, 0, 0],
    }
    for name, j in poses.items():
        p, R = chain.fk(j)
        reach = math.sqrt(sum(x * x for x in p))
        print(f"  {name}  pos=({p[0]:7.3f},{p[1]:7.3f},{p[2]:7.3f})  reach={reach:.3f} m")
        # The KR60 reaches ~2.3-2.7 m; the zone poses in zones.json span this range.
        if not (0.3 < reach < 2.8):
            print(f"    !! reach {reach:.3f} m outside plausible KR60 range [0.3, 2.8]")
            ok = False

    # --- 2. Jacobian consistency ------------------------------------------
    print("\n== Jacobian: analytic vs finite-difference (linear J_v) ==")
    test_poses = {
        "home": [0, -90, 90, 0, 90, 0],
        "rest": [-3, -133, 156, -2, 0, 0],
        "mid": [30, -70, 80, 10, 45, -20],
        "wild": [60, -22.5, -112.5, 0, 45, 0],
    }
    for name, j in test_poses.items():
        _, Jv = chain.jacobian(j)
        Jnum = finite_diff_jacobian(chain, j)
        err = max_err(Jv, Jnum)
        status = "ok" if err < 1e-3 else "FAIL"
        print(f"  {name:6s}  max |J_analytic - J_numeric| = {err:.3e}  [{status}]")
        if err >= 1e-3:
            ok = False

    # --- 3. Pseudo-inverse sanity -----------------------------------------
    print("\n== Pseudo-inverse sanity (dq = J_v^+ d_base drives the tool) ==")
    j = [30, -70, 80, 10, 45, -20]
    _, Jv = chain.jacobian(j)
    d_base = [0.01, -0.02, 0.005]  # a small base-frame displacement (metres)
    P = pinv3(Jv)
    dq = [sum(P[i][c] * d_base[c] for c in range(3)) for i in range(6)]  # radians
    # Apply dq (radians -> degrees) and check the tool moves ~d_base (first-order).
    deg_per_rad = 180.0 / math.pi
    p0 = fk_pos(chain, j)
    p1 = fk_pos(chain, [j[k] + dq[k] * deg_per_rad for k in range(6)])
    moved = [p1[k] - p0[k] for k in range(3)]
    moved_n = math.sqrt(sum(x * x for x in moved))
    d_n = math.sqrt(sum(x * x for x in d_base))
    print(f"  d_base={d_base} -> dq(rad)={[round(x,5) for x in dq]}")
    print(f"  tool moved {moved} (|d|={moved_n:.5f}, target |d|={d_n:.5f})")
    if abs(moved_n - d_n) > 0.25 * d_n + 1e-3:
        print("    !! tool did not move ~d_base (pinv mapping off)")
        ok = False

    # --- 4. Camera geometry (P2: the orientation term) ---------------------
    print("\n== Camera geometry: offset rotated into the base frame (P2) ==")
    from rtr.camera.geometry import load_camera_geometry
    from rtr.robot.kinematics import mat3_mul, mat3_vec, rot_rpy
    geo = load_camera_geometry()
    tc = geo.tool_cam

    # 4a. camera_pose(j) == FK_tool(j) · T_tool_cam (compose and check).
    j = [0, -90, 90, 0, 90, 0]
    t_cam, R_cam = geo.camera_pose(j)
    t_tool, R_tool = chain.fk(j)
    R_expect = mat3_mul(R_tool, rot_rpy(*tc["rpy"]))
    off = mat3_vec(R_tool, tc["xyz"])
    t_expect = [t_tool[k] + off[k] for k in range(3)]
    pose_err = max(abs(R_cam[r][c] - R_expect[r][c]) for r in range(3) for c in range(3))
    pose_err = max(pose_err, max(abs(t_cam[k] - t_expect[k]) for k in range(3)))
    print(f"  camera_pose == FK · T_tool_cam: max err {pose_err:.3e}  [{'ok' if pose_err < 1e-9 else 'FAIL'}]")
    if pose_err >= 1e-9:
        ok = False

    # 4b. offset_to_base_direction == R_cam_base · (dx, dy, z_cam).
    dx, dy, z_cam = 120.0, -40.0, 500.0
    d_base = geo.offset_to_base_direction(dx, dy, z_cam, j)
    d_expect = mat3_vec(R_cam, [dx, dy, z_cam])
    rot_err = max(abs(d_base[k] - d_expect[k]) for k in range(3))
    print(f"  offset_to_base_direction == R_cam · v: max err {rot_err:.3e}  [{'ok' if rot_err < 1e-9 else 'FAIL'}]")
    if rot_err >= 1e-9:
        ok = False

    # 4c. The base-frame direction CHANGES with the arm (the whole point).
    print("  same image offset (dx=+120px), two arm poses -> different base directions:")
    for name, jp in [("home", [0, -90, 90, 0, 90, 0]),
                     ("turned", [120, -90, 90, 0, 90, 0])]:
        db = geo.offset_to_base_direction(dx, dy, z_cam, jp)
        n = math.sqrt(sum(x * x for x in db))
        unit = [x / n for x in db]
        print(f"    {name:6s} A1={jp[0]:4.0f}  d_base=({db[0]:8.1f},{db[1]:8.1f},{db[2]:8.1f})  "
              f"unit=({unit[0]:+.2f},{unit[1]:+.2f},{unit[2]:+.2f})")

    # --- 5. Orientation-aware servo (P3: same offset, different arm -> different move)
    print("\n== Orientation-aware servo (P3): watch/track, A1 driven by the offset ==")
    from rtr.state.machine import StateMachine
    from rtr.state.zones import Zones, ZONES
    from rtr.state.behavior import build_target
    zones = Zones(ZONES)
    machine = StateMachine(zones, tick_hz=20.0)
    machine.chain = chain
    machine.camera_geometry = geo
    machine.camera_state = {"dx": 120.0, "dy": 0.0, "w": 0.0, "h": 0.0,
                            "id": 1, "locked": True, "ok": True}
    machine.current_zone = "watch"
    machine.current_action = "track"  # var_axes=[0], behavior="track"
    base = zones.get("watch").action_base_pose("track")
    a1_deltas = {}
    for a1 in [0.0, 60.0, 120.0]:
        curjpos = [a1, base[1], base[2], base[3], base[4], base[5]]
        pose = build_target(machine, curjpos)
        d1 = pose[0] - base[0]
        a1_deltas[a1] = d1
        print(f"  A1={a1:4.0f}  ->  A1 delta={d1:+.4f} deg")
    # The orientation term makes the A1 response pose-dependent: at least two of the
    # three deltas must differ (a fixed gain would give the same delta at every pose).
    distinct = len(set(round(v, 4) for v in a1_deltas.values()))
    print(f"  distinct A1 deltas across poses: {distinct}/3  [{'ok' if distinct >= 2 else 'FAIL'}]")
    if distinct < 2:
        ok = False

    print(f"\n{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
