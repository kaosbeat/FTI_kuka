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


def finite_diff_jacobian(chain: Chain, j, h=1e-5):
    """Numerical linear Jacobian (3x6): column i = (fk(j+h e_i) - fk(j))/h."""
    base = fk_pos(chain, j)
    cols = []
    for i in range(6):
        jp = list(j)
        jp[i] += h
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
        if not (0.3 < reach < 2.6):
            print(f"    !! reach {reach:.3f} m outside plausible KR60 range [0.3, 2.6]")
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
    d_base = [0.01, -0.02, 0.005]  # a small base-frame displacement
    P = pinv3(Jv)
    dq = [sum(P[i][c] * d_base[c] for c in range(3)) for i in range(6)]
    # Apply dq and check the tool moves ~d_base (first-order).
    p0 = fk_pos(chain, j)
    p1 = fk_pos(chain, [j[k] + dq[k] for k in range(6)])
    moved = [p1[k] - p0[k] for k in range(3)]
    moved_n = math.sqrt(sum(x * x for x in moved))
    d_n = math.sqrt(sum(x * x for x in d_base))
    print(f"  d_base={d_base} -> dq={[round(x,5) for x in dq]}")
    print(f"  tool moved {moved} (|d|={moved_n:.5f}, target |d|={d_n:.5f})")
    if abs(moved_n - d_n) > 0.25 * d_n + 1e-3:
        print("    !! tool did not move ~d_base (pinv mapping off)")
        ok = False

    print(f"\n{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
