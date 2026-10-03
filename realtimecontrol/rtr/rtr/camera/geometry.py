"""Camera geometry: where the toolhead-mounted camera is pointing (P2).

This is the **orientation term** the previous servo was missing. The camera is
mounted on the toolhead (the lenses in ``assets/tool.glb``), so its pose in the
base frame is a function of the 6 joint angles:

    T_base_cam(j) = FK(j) · T_tool_cam

where ``FK(j)`` is the tool (``$FLANGE``) pose (see :mod:`rtr.robot.kinematics`)
and ``T_tool_cam`` is the fixed tool→camera mount (``camera_mount.json``). As the
arm moves, ``FK(j)`` changes, so the camera's base-frame orientation changes — and
with it, the direction the image-x/image-y offsets point in the base frame.

The core function is :meth:`CameraGeometry.offset_to_base_direction`, which rotates
the camera-frame offset vector ``(dx, dy, z_cam)`` into the base frame. The pixel
offsets ``dx``/``dy`` (from the RPI) and the optical-axis term ``z_cam`` (the focal
length, in pixels) form the ray from the camera to the tracked person; rotating it
by the camera's base orientation gives the base-frame direction the robot must move
toward to center the target — in *any* arm configuration.

Pure stdlib, reusing the rotation helpers from :mod:`rtr.robot.kinematics`.
"""

import json
import os
from typing import List, Optional, Tuple

from ..robot.kinematics import Chain, load_chain, mat3_mul, mat3_vec, rot_rpy

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
    """

    def __init__(self, chain: Chain, tool_cam: dict):
        self.chain = chain
        self.tool_cam = tool_cam

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


def load_camera_geometry(mount_path: Optional[str] = None,
                         chain_path: Optional[str] = None) -> CameraGeometry:
    """Build the camera geometry from the mount data file (default ``camera_mount.json``).

    The mount file carries the tool→camera transform (``tool_cam``) and the default
    optical-axis term (``z_cam_default``). The kinematic chain defaults to
    ``kr60_chain.json``.
    """
    mount_path = mount_path or _DEFAULT_MOUNT_PATH
    with open(mount_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    chain = load_chain(chain_path)
    return CameraGeometry(chain, data["tool_cam"])
