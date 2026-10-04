"""Camera geometry: where the toolhead-mounted camera is pointing (P2/P3).

This is the **orientation term** the previous servo was missing. The camera is
mounted on the toolhead (the lenses in ``assets/tool.glb``), so its pose in the
base frame is a function of the 6 joint angles:

    T_base_cam(j) = FK(j) · T_tool_cam

where ``FK(j)`` is the tool (``$FLANGE``) pose (see :mod:`rtr.robot.kinematics`)
and ``T_tool_cam`` is the fixed tool→camera mount (``camera_mount.json``). As the
arm moves, ``FK(j)`` changes, so the camera's base-frame orientation changes — and
with it, the direction the image-x/image-y offsets point in the base frame.

Two families of functions:

- :meth:`CameraGeometry.offset_to_base_direction` (legacy) rotates the camera-frame
  offset vector ``(dx, dy, z_cam)`` into the base frame.
- The **visual-servoing** trio (P3): :meth:`CameraGeometry.image_offset` maps a
  world target to its image offset ``(dx, dy)``; :meth:`CameraGeometry.estimate_target`
  inverts it (offset + working depth → world position); :meth:`CameraGeometry.image_jacobian`
  is the image Jacobian ``d(dx, dy)/d(joints)``. The servo law is
  ``dq = -gain · pinv2(J_img_var) · (dx, dy)``: it drives the image offset to zero
  and is correct in *any* arm configuration, because the Jacobian is derived from
  the exact camera pose.

Pure stdlib, reusing the rotation helpers from :mod:`rtr.robot.kinematics`.
"""

import json
import math
import os
from typing import List, Optional, Tuple

from ..robot.kinematics import (
    Chain, load_chain, mat3_mul, mat3_transpose, mat3_vec, rot_rpy,
)

Mat3 = List[List[float]]
Vec3 = List[float]

# Default mount data file (next to main.py / zones.json, like the other data files).
_DEFAULT_MOUNT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "camera_mount.json",
)


class CameraGeometry:
    """The camera pose as a function of the arm, and the offset→base-frame rotation.

    ``chain`` is the KR60 kinematic chain (``$FLANGE`` end-effector); ``tool_cam``
    is the fixed transform from the tool frame to the camera frame: ``{xyz, rpy}``.
    ``fx``/``fy`` are the focal lengths (pixels); ``target_depth`` is the nominal
    working distance (metres) for :meth:`estimate_target`.
    """

    def __init__(self, chain: Chain, tool_cam: dict,
                fx: float = 500.0, fy: float = 500.0, target_depth: float = 2.0):
        self.chain = chain
        self.tool_cam = tool_cam
        self.fx = fx
        self.fy = fy
        self.target_depth = target_depth

    def camera_pose(self, joints_deg: List[float]) -> Tuple[Vec3, Mat3]:
        """The camera frame pose (position + orientation) in the base frame."""
        t_tool, R_tool = self.chain.fk(joints_deg)
        tr, tp, ty = self.tool_cam["rpy"]
        R_cam = mat3_mul(R_tool, rot_rpy(tr, tp, ty))
        off = mat3_vec(R_tool, self.tool_cam["xyz"])
        t_cam = [t_tool[k] + off[k] for k in range(3)]
        return t_cam, R_cam

    def offset_to_base_direction(self, dx: float, dy: float, z_cam: float,
                                 joints_deg: List[float]) -> Vec3:
        """Rotate the camera-frame offset ``(dx, dy, z_cam)`` into the base frame.

        ``(dx, dy)`` are the tracked person's pixel offsets from image center (RPI);
        ``z_cam`` is the optical-axis term (the focal length, in pixels). Together
        they form the ray from the camera to the person; the result is that ray in
        the base frame — the direction the robot moves toward to center the target.
        """
        _, R_cam = self.camera_pose(joints_deg)
        return mat3_vec(R_cam, [dx, dy, z_cam])

    # ------------------------------------------------------------------
    # Visual servoing (P3): the image offset, its inverse, and the image Jacobian.
    # ------------------------------------------------------------------

    def image_offset(self, joints_deg: List[float], target: Vec3) -> Tuple[float, float]:
        """The tracked target's image offset ``(dx, dy)`` in pixels, given its world
        (base-frame) position.

        ``dx = fx · x_c / z_c`` and ``dy = fy · y_c / z_c`` where ``(x_c, y_c, z_c)``
        is the target in the camera frame (``z_c`` is the depth along the optical
        axis). This is exactly what the RPI reports (the target's pixel offset from
        image center). A target behind the camera (``z_c`` near 0) returns ``(0, 0)``.
        """
        C, R = self.camera_pose(joints_deg)
        pc = mat3_vec(mat3_transpose(R), [target[k] - C[k] for k in range(3)])
        if abs(pc[2]) < 1e-6:
            return 0.0, 0.0
        return self.fx * pc[0] / pc[2], self.fy * pc[1] / pc[2]

    def estimate_target(self, joints_deg: List[float], dx: float, dy: float,
                        depth: Optional[float] = None) -> Vec3:
        """Estimate the tracked target's world position from the image offset.

        The RPI gives only ``(dx, dy)`` (no depth), so the target is reconstructed
        at a working distance ``depth`` (default :attr:`target_depth`): the camera-
        frame position is ``(dx·depth/fx, dy·depth/fy, depth)``, rotated to the base
        frame and added to the camera position. A slightly-off depth still yields a
        target in the right *direction*, which is all the servo needs.
        """
        depth = self.target_depth if depth is None else depth
        C, R = self.camera_pose(joints_deg)
        xc = dx * depth / self.fx
        yc = dy * depth / self.fy
        off = mat3_vec(R, [xc, yc, depth])
        return [C[k] + off[k] for k in range(3)]

    def image_jacobian(self, joints_deg: List[float], target: Vec3,
                       eps_rad: float = 1e-4) -> List[List[float]]:
        """The visual-servoing image Jacobian ``d(dx, dy)/d(joints)`` (2x6, px/rad).

        Computed by finite differences over the 6 joints (FK is cheap). For the
        tracked target ``target`` (fixed while differentiating), perturbing joint
        ``i`` by ``eps_rad`` moves the camera (position *and* orientation) and shifts
        the target's image offset; the ratio is the Jacobian column. Reduced to the
        action's variable axes and pseudo-inverted, this maps the image offset to the
        joint deltas that shrink it: ``dq = -gain · pinv2(J_var) · (dx, dy)``.
        """
        d0x, d0y = self.image_offset(joints_deg, target)
        deg = 180.0 / math.pi
        J = [[0.0] * 6 for _ in range(2)]
        for i in range(6):
            jp = list(joints_deg)
            jp[i] += eps_rad * deg
            dxi, dyi = self.image_offset(jp, target)
            J[0][i] = (dxi - d0x) / eps_rad
            J[1][i] = (dyi - d0y) / eps_rad
        return J


def load_camera_geometry(mount_path: Optional[str] = None,
                         chain_path: Optional[str] = None) -> CameraGeometry:
    """Build the camera geometry from the mount data file (default ``camera_mount.json``).

    The mount file carries the tool→camera transform (``tool_cam``), the camera
    intrinsics (``fx``/``fy``), the working distance (``target_depth``), and the
    legacy optical-axis term (``z_cam_default``). The kinematic chain defaults to
    ``kr60_chain.json``.
    """
    mount_path = mount_path or _DEFAULT_MOUNT_PATH
    with open(mount_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    chain = load_chain(chain_path)
    return CameraGeometry(
        chain, data["tool_cam"],
        fx=float(data.get("fx", 500.0)),
        fy=float(data.get("fy", 500.0)),
        target_depth=float(data.get("target_depth", 2.0)),
    )
