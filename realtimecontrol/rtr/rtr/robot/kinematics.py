"""KR60 kinematics: forward kinematics + geometric Jacobian (pure stdlib).

Built from the KR60 URDF chain (``kr60ha_macro.xacro``). The base frame is the
KUKA ``$ROBROOT`` (URDF ``base_link``, Z-up); the end-effector frame is the KUKA
``$FLANGE`` (URDF ``tool0``). Joint angles are the KUKA ``A1..A6`` (degrees),
mapped 1:1 to the URDF joint angles.

Pure stdlib (``math``) so the core stays dependency-free: the core has no numpy
today, and FK + a 6x6 Jacobian are trivially cheap at 20 Hz.

Two things this module provides (both are what the orientation-aware camera servo
needs):

- :meth:`Chain.fk` — the tool (``$FLANGE``) pose in the base frame as a function of
  the 6 joint angles. The camera is mounted on the tool, so this is the
  camera's base-frame pose (up to the fixed tool→camera mount, see
  :mod:`rtr.camera.geometry`).
- :meth:`Chain.jacobian` — the geometric Jacobian. Its linear part maps a base-frame
  displacement to joint deltas (``dq = J_v^+ d_base``), which is how a camera offset
  (rotated into the base frame) becomes a joint move that is correct in *any* arm
  configuration.
"""

import json
import math
import os
from typing import List, Optional, Tuple

# A 3x3 rotation is a list of 3 rows (each a list of 3 floats); a vector is 3 floats.
Mat3 = List[List[float]]
Vec3 = List[float]

IDENTITY_ROT: Mat3 = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
ORIGIN: Vec3 = [0.0, 0.0, 0.0]

# Default chain data file (next to main.py / zones.json, like the other data files).
_DEFAULT_CHAIN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "kr60_chain.json")


# ---------------------------------------------------------------------------
# 3x3 / vector helpers (pure stdlib).
# ---------------------------------------------------------------------------

def mat3_mul(a: Mat3, b: Mat3) -> Mat3:
    return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)] for r in range(3)]


def mat3_transpose(a: Mat3) -> Mat3:
    return [[a[r][c] for r in range(3)] for c in range(3)]


def mat3_vec(m: Mat3, v: Vec3) -> Vec3:
    return [sum(m[r][c] * v[c] for c in range(3)) for r in range(3)]


def cross(u: Vec3, v: Vec3) -> Vec3:
    return [u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0]]


def inv3(m: Mat3) -> Optional[Mat3]:
    """Inverse of a 3x3 matrix (None when singular)."""
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        return None
    inv_det = 1.0 / det
    return [
        [(e * i - f * h) * inv_det, (c * h - b * i) * inv_det, (b * f - c * e) * inv_det],
        [(f * g - d * i) * inv_det, (a * i - c * g) * inv_det, (c * d - a * f) * inv_det],
        [(d * h - e * g) * inv_det, (b * g - a * h) * inv_det, (a * e - b * d) * inv_det],
    ]


def rot_x(a: float) -> Mat3:
    c, s = math.cos(a), math.sin(a)
    return [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]


def rot_y(a: float) -> Mat3:
    c, s = math.cos(a), math.sin(a)
    return [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]]


def rot_z(a: float) -> Mat3:
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]


def rot_rpy(roll: float, pitch: float, yaw: float) -> Mat3:
    """ROS rpy convention: R = Rz(yaw) Ry(pitch) Rx(roll)."""
    return mat3_mul(mat3_mul(rot_z(yaw), rot_y(pitch)), rot_x(roll))


def rot_axis(axis: Vec3, theta: float) -> Mat3:
    """Rotation about an arbitrary unit axis by ``theta`` (Rodrigues).

    ``R = cos(theta)·I + sin(theta)·[k]× + (1-cos(theta))·k·kᵀ``.
    """
    x, y, z = axis
    c, s = math.cos(theta), math.sin(theta)
    t = 1.0 - c
    return [
        [c + t * x * x, t * x * y - s * z, t * x * z + s * y],
        [t * y * x + s * z, c + t * y * y, t * y * z - s * x],
        [t * z * x - s * y, t * z * y + s * x, c + t * z * z],
    ]


def pinv3(m: List[List[float]], lam: float = 0.1) -> List[List[float]]:
    """Damped minimum-norm pseudo-inverse of a 3 x n matrix.

    Returns an n x 3 matrix ``P`` such that ``dq = P @ d`` maps a 3D base-frame
    displacement ``d`` to joint deltas (minimum-norm, damped). Used for position
    servoing: ``dq = pinv3(J_v) @ d_base``.
    """
    n = len(m[0])
    # JJT = m @ m^T (3x3), damped.
    JJT = [[sum(m[r][k] * m[r2][k] for k in range(n)) for r2 in range(3)] for r in range(3)]
    JJT = [[JJT[r][c] + (lam * lam if r == c else 0.0) for c in range(3)] for r in range(3)]
    JJT_inv = inv3(JJT)
    if JJT_inv is None:
        return [[0.0] * 3 for _ in range(n)]
    # m^+ = m^T @ JJT_inv  (n x 3).
    return [[sum(m[r][i] * JJT_inv[r][c] for r in range(3)) for c in range(3)] for i in range(n)]


# ---------------------------------------------------------------------------
# The chain.
# ---------------------------------------------------------------------------

class Chain:
    """A serial manipulator chain (6 revolute joints + a fixed tool frame).

    ``joints`` is a list of 6 dicts ``{xyz, rpy, axis}`` (the URDF joint origins:
    translation + fixed rotation + rotation axis, all in the parent frame).
    ``tool`` is the fixed transform from link6 to the tool (``$FLANGE``):
    ``{xyz, rpy}``.
    """

    def __init__(self, joints: List[dict], tool: dict):
        if len(joints) != 6:
            raise ValueError("a KR60 chain has exactly 6 joints")
        self.joints = joints
        self.tool = tool

    def _walk(self, joints_deg: List[float]) -> Tuple[Vec3, Mat3, List[Tuple[Mat3, Vec3]]]:
        """Walk the chain; return (tool_pos, tool_R, [(R_child, t_child) per joint])."""
        R: Mat3 = [list(r) for r in IDENTITY_ROT]
        t: Vec3 = list(ORIGIN)
        frames = []
        for i in range(6):
            j = self.joints[i]
            r, p, y = j["rpy"]
            R_o = rot_rpy(r, p, y)
            R_j = rot_axis(j["axis"], math.radians(joints_deg[i]))
            R_child = mat3_mul(mat3_mul(R, R_o), R_j)
            t_off = mat3_vec(R, j["xyz"])
            t_child = [t[k] + t_off[k] for k in range(3)]
            frames.append((R_child, t_child))
            R, t = R_child, t_child
        tr, tp, ty = self.tool["rpy"]
        R_tool = mat3_mul(R, rot_rpy(tr, tp, ty))
        t_off = mat3_vec(R, self.tool["xyz"])
        t_tool = [t[k] + t_off[k] for k in range(3)]
        return t_tool, R_tool, frames

    def fk(self, joints_deg: List[float]) -> Tuple[Vec3, Mat3]:
        """Forward kinematics: the tool (``$FLANGE``) pose in the base frame."""
        t_tool, R_tool, _ = self._walk(joints_deg)
        return t_tool, R_tool

    def jacobian(self, joints_deg: List[float]) -> Tuple[Mat3, Mat3]:
        """The geometric Jacobian at ``joints_deg``.

        Returns ``(J_omega, J_v)``: the angular part (3x6) and the linear part
        (3x6). For position servoing use ``J_v``: ``dq = pinv3(J_v) @ d_base``.
        """
        t_ee, R_ee, frames = self._walk(joints_deg)
        J_omega = [[0.0] * 6 for _ in range(3)]
        J_v = [[0.0] * 6 for _ in range(3)]
        for i, (R_child, t_child) in enumerate(frames):
            axis_base = mat3_vec(R_child, self.joints[i]["axis"])
            for r in range(3):
                J_omega[r][i] = axis_base[r]
            d = [t_ee[k] - t_child[k] for k in range(3)]
            for r in range(3):
                J_v[r][i] = cross(axis_base, d)[r]
        return J_omega, J_v

    def linear_jacobian(self, joints_deg: List[float]) -> List[List[float]]:
        """The linear (position) Jacobian ``J_v`` (3x6) alone."""
        return self.jacobian(joints_deg)[1]


# ---------------------------------------------------------------------------
# Loading.
# ---------------------------------------------------------------------------

def load_chain(path: Optional[str] = None) -> Chain:
    """Load a chain from a JSON data file (default: ``kr60_chain.json`` next to the core)."""
    path = path or _DEFAULT_CHAIN_PATH
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Chain(data["joints"], data["tool"])
