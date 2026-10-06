"""Two-camera YOLO + ByteTrack + face pipeline for the RPI.

Refactored from ``input_processing/trackfollowpi.py``. Runs two physical cameras
(wide + close) with person detection/tracking (YOLO + ByteTrack) and face
analysis on the close camera. Exposes ``configure(camera, mode, lock_id)`` and
per-frame results (candidates, tracked offset, face) for the camera remote to
send as ``CAM_*`` telemetry.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

# Camera identifiers.
CAMERA_WIDE = "wide"
CAMERA_CLOSE = "close"
CAMERA_BOTH = "both"

# Behaviour modes.
MODE_IDLE = "idle"
MODE_TRACK = "track"
MODE_ANALYZE = "analyze"

# Local model directory (excluded from git; drop .pt weights here).
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


def resolve_model(path: str) -> str:
    """Resolve a model path: use it as-is if it exists, else look in MODEL_DIR."""
    if os.path.isabs(path) or os.path.exists(path):
        return path
    local = os.path.join(MODEL_DIR, path)
    if os.path.exists(local):
        return local
    return path


@dataclass
class FrameResult:
    """Per-frame pipeline output."""
    camera: str = CAMERA_WIDE
    mode: str = MODE_IDLE
    active: bool = False
    fps: float = 0.0
    ok: bool = True
    # Candidates (all detected persons on the active camera).
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    # Tracked person (the locked target).
    track: Optional[Dict[str, Any]] = None
    # Face analysis (close camera only).
    face: Optional[Dict[str, Any]] = None
    # Raw BGR frame from the active camera (debug display).
    frame: Optional[np.ndarray] = None


def annotate(frame: np.ndarray, result: FrameResult) -> np.ndarray:
    """Draw candidates, the locked target, and a status overlay (debug window)."""
    img = frame.copy()
    for c in result.candidates:
        x1, y1 = int(c["x1"]), int(c["y1"])
        x2, y2 = int(c["x2"]), int(c["y2"])
        locked = result.track is not None and c["id"] == result.track["id"]
        color = (0, 255, 0) if locked else (0, 160, 255)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        label = f"#{c['id']} {c['conf']:.2f}" + (" LOCK" if locked else "")
        cv2.putText(img, label, (x1, max(0, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    lock_txt = result.track["id"] if result.track else "-"
    text = (f"{result.camera} | mode={result.mode} | lock={lock_txt} | "
            f"{result.fps:.0f} fps | {len(result.candidates)} cand")
    cv2.putText(img, text, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    return img


class Camera:
    """A single physical camera with YOLO + ByteTrack tracking."""

    def __init__(self, name: str, camera_num: int = 0,
                 width: int = 640, height: int = 480,
                 frame_rate: int = 30,
                 model_path: str = "yolo26n.pt",
                 tracker_cfg: str = "bytetrack.yaml",
                 video_source: Optional[str] = None):
        self.name = name
        self.camera_num = camera_num
        self.video_source = video_source
        self.W = width
        self.H = height
        self.model_path = model_path
        self.tracker_cfg = tracker_cfg
        self.frame_rate = frame_rate

        # Locked target state.
        self.lock_id: Optional[int] = None
        self.lock_active: bool = False
        self.center_hist: List[Tuple[float, float]] = []
        self.center_smooth_frames = 5

        # YOLO model (lazy-loaded on first frame).
        self._model = None
        # Camera handle (lazy-loaded).
        self._cam = None
        self._frame_count = 0
        self._fps = 0.0
        self._last_time = 0.0

    def _ensure_model(self):
        if self._model is None:
            from ultralytics import YOLO
            self._model = YOLO(resolve_model(self.model_path))

    def _ensure_cam(self):
        if self._cam is None:
            self._open_camera()

    def _open_camera(self):
        """Open the frame source: a video file (debug) or the physical camera (RPI)."""
        if self.video_source is not None:
            try:
                self._cam = cv2.VideoCapture(self.video_source)
                if not self._cam.isOpened():
                    print(f"[camera:{self.name}] could not open video: {self.video_source}")
                    self._cam = None
            except Exception as exc:
                print(f"[camera:{self.name}] could not open video: {exc}")
                self._cam = None
            return
        # Physical camera (RPI-specific; degrades gracefully).
        try:
            from picamera2 import Picamera2
            from libcamera import Transform
            self._cam = Picamera2(camera_num=self.camera_num)
            config = self._cam.create_video_configuration(
                main={"format": "RGB888", "size": (self.W, self.H)},
                controls={"FrameRate": self.frame_rate},
            )
            config["transform"] = Transform(hflip=True, vflip=True)
            self._cam.configure(config)
            self._cam.start()
        except Exception as exc:
            print(f"[camera:{self.name}] could not open: {exc}")
            self._cam = None

    def grab_frame(self) -> Optional[np.ndarray]:
        """Grab a single BGR frame from the video source or the camera."""
        self._ensure_cam()
        if self._cam is None:
            return None
        if self.video_source is not None:
            ok, frame = self._cam.read()
            if not ok:
                # Video exhausted: loop back to the start for continuous debugging.
                try:
                    self._cam.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, frame = self._cam.read()
                except Exception:
                    return None
                if not ok:
                    return None
            return frame
        try:
            arr = self._cam.capture_array()
            return arr
        except Exception:
            return None

    def detect(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """Run YOLO tracking on a frame; return candidate dicts."""
        self._ensure_model()
        if self._model is None:
            return []
        try:
            results = self._model.track(
                frame,
                persist=True,
                classes=[0],  # COCO person
                tracker=self.tracker_cfg,
                verbose=False,
            )[0]
        except Exception:
            return []

        candidates = []
        if results.boxes is not None and results.boxes.id is not None:
            ids = results.boxes.id.cpu().numpy().astype(int)
            xyxy = results.boxes.xyxy.cpu().numpy()
            confs = results.boxes.conf.cpu().numpy() if results.boxes.conf is not None else [0.0] * len(ids)
            for i, tid in enumerate(ids):
                x1, y1, x2, y2 = xyxy[i]
                candidates.append({
                    "id": int(tid),
                    "x1": float(x1),
                    "y1": float(y1),
                    "x2": float(x2),
                    "y2": float(y2),
                    "conf": float(confs[i]) if i < len(confs) else 0.0,
                })
        return candidates

    def track_offset(self, candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Compute the tracked offset for the locked target."""
        if self.lock_id is None or not self.lock_active:
            return None
        for c in candidates:
            if c["id"] == self.lock_id:
                x1, y1, x2, y2 = c["x1"], c["y1"], c["x2"], c["y2"]
                cx = 0.5 * (x1 + x2)
                cy = 0.5 * (y1 + y2)
                # Smooth the center.
                self.center_hist.append((cx, cy))
                if len(self.center_hist) > self.center_smooth_frames:
                    self.center_hist.pop(0)
                sx = sum(p[0] for p in self.center_hist) / len(self.center_hist)
                sy = sum(p[1] for p in self.center_hist) / len(self.center_hist)
                img_cx = self.W / 2.0
                img_cy = self.H / 2.0
                dx = sx - img_cx
                dy = sy - img_cy
                w = x2 - x1
                h = y2 - y1
                return {
                    "camera": self.name,
                    "id": int(self.lock_id),
                    "cx": float(cx),
                    "cy": float(cy),
                    "dx": float(dx),
                    "dy": float(dy),
                    "w": float(w),
                    "h": float(h),
                }
        # Lock target not found in this frame.
        self.lock_active = False
        self.center_hist.clear()
        return None

    def set_lock(self, lock_id: Optional[int]) -> None:
        """Set or clear the locked target."""
        self.lock_id = lock_id
        self.lock_active = lock_id is not None
        self.center_hist.clear()

    def process(self) -> Optional[FrameResult]:
        """Run one frame of the pipeline; return the result."""
        import time
        now = time.time()
        if self._last_time > 0:
            dt = now - self._last_time
            if dt > 0:
                self._fps = 0.9 * self._fps + 0.1 * (1.0 / dt)
        self._last_time = now
        self._frame_count += 1

        frame = self.grab_frame()
        if frame is None:
            return FrameResult(camera=self.name, active=False, ok=False)

        candidates = self.detect(frame)
        track = self.track_offset(candidates)

        return FrameResult(
            camera=self.name,
            active=True,
            fps=self._fps,
            ok=True,
            candidates=candidates,
            track=track,
            frame=frame,
        )

    def stop(self):
        if self._cam is not None:
            try:
                if self.video_source is not None:
                    self._cam.release()
                else:
                    self._cam.stop()
            except Exception:
                pass
            self._cam = None


class FaceDetector:
    """Face analysis for the close camera (stub; deepface integration is a follow-up)."""

    def __init__(self, model_path: str = "yolov12n-face.pt"):
        self.model_path = model_path
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            try:
                from ultralytics import YOLO
                self._model = YOLO(resolve_model(self.model_path))
            except Exception:
                self._model = None

    def analyze(self, frame: np.ndarray, person_bbox: Optional[Tuple[float, float, float, float]] = None) -> Optional[Dict[str, Any]]:
        """Analyze a face in the frame; return a face dict or None."""
        # Stub: return a simple face dict if a person is tracked.
        # Real face analysis (landmarks, identity, emotion) is a follow-up.
        if person_bbox is None:
            return None
        x1, y1, x2, y2 = person_bbox
        return {
            "bbox": [float(x1), float(y1), float(x2), float(y2)],
            "features": {},
        }


class Pipeline:
    """Two-camera pipeline: wide (track) + close (analyze).

    The brain's ``cam_control`` intent configures which camera is active,
    what mode it runs in, and which person to lock. The pipeline runs both
    cameras but only sends telemetry for the active one.
    """

    def __init__(self,
                 wide_cam_num: int = 0,
                 close_cam_num: int = 1,
                 width: int = 640,
                 height: int = 480,
                 frame_rate: int = 30,
                 model_path: str = "yolo26n.pt",
                 tracker_cfg: str = "bytetrack.yaml",
                 wide_video: Optional[str] = None,
                 close_video: Optional[str] = None):
        self.wide = Camera(CAMERA_WIDE, camera_num=wide_cam_num,
                            width=width, height=height, frame_rate=frame_rate,
                            model_path=model_path, tracker_cfg=tracker_cfg,
                            video_source=wide_video)
        self.close = Camera(CAMERA_CLOSE, camera_num=close_cam_num,
                             width=width, height=height, frame_rate=frame_rate,
                             model_path=model_path, tracker_cfg=tracker_cfg,
                             video_source=close_video)
        self.face = FaceDetector(model_path=model_path)
        # Current intent (set by the brain via cam_control).
        self.active: str = CAMERA_WIDE
        self.mode: str = MODE_IDLE
        self.lock_id: Optional[int] = None
        self._running = False

    def configure(self, active: str, mode: str, lock_id: Optional[int]) -> None:
        """Configure the pipeline from the brain's cam_control intent."""
        self.active = active
        self.mode = mode
        # Apply the lock to the appropriate camera.
        if active == CAMERA_WIDE or active == CAMERA_BOTH:
            self.wide.set_lock(lock_id)
        if active == CAMERA_CLOSE or active == CAMERA_BOTH:
            self.close.set_lock(lock_id)
        if active not in (CAMERA_WIDE, CAMERA_CLOSE, CAMERA_BOTH):
            self.active = CAMERA_WIDE

    def process(self) -> FrameResult:
        """Run one frame of the pipeline; return the result for the active camera."""
        if self.active == CAMERA_WIDE:
            result = self.wide.process()
            if self.mode == MODE_ANALYZE and result.track is not None:
                result.face = self._analyze_face(result)
            result.mode = self.mode
            return result
        elif self.active == CAMERA_CLOSE:
            result = self.close.process()
            if self.mode == MODE_ANALYZE and result.track is not None:
                result.face = self._analyze_face(result)
            result.mode = self.mode
            return result
        elif self.active == CAMERA_BOTH:
            # Run both; return the wide result (primary) with close face data.
            result = self.wide.process()
            close_result = self.close.process()
            if self.mode == MODE_ANALYZE and close_result.track is not None:
                result.face = self._analyze_face(close_result)
            result.mode = self.mode
            return result
        return FrameResult(camera=self.active, active=False)

    def _analyze_face(self, result: FrameResult) -> Optional[Dict[str, Any]]:
        if result.track is None:
            return None
        bbox = (result.track.get("cx", 0) - result.track.get("w", 0) / 2,
                result.track.get("cy", 0) - result.track.get("h", 0) / 2,
                result.track.get("cx", 0) + result.track.get("w", 0) / 2,
                result.track.get("cy", 0) + result.track.get("h", 0) / 2)
        return self.face.analyze(None, bbox)

    def to_telemetry(self, result: FrameResult) -> Dict[str, Dict[str, Any]]:
        """Convert a frame result to the CAM_* telemetry payloads."""
        out = {}
        # Always send status.
        out["status"] = {
            "camera": result.camera,
            "mode": self.mode,
            "active": result.active,
            "fps": result.fps,
            "ok": result.ok,
        }
        # Send candidates (all detected persons).
        if result.candidates:
            out["candidates"] = {
                "camera": result.camera,
                "candidates": result.candidates,
            }
        # Send track (locked person offset).
        if result.track is not None:
            out["track"] = result.track
        # Send face (close camera analysis). The face offset (dx, dy) is the face bbox
        # centroid minus the image center, in pixels — the error the `face` behaviour
        # servos to zero. Both cameras share W/H, so the wide camera's dims are used.
        if result.face is not None:
            bbox = result.face.get("bbox")
            dx = None
            dy = None
            if bbox and len(bbox) >= 4:
                x1, y1, x2, y2 = bbox[0], bbox[1], bbox[2], bbox[3]
                dx = (x1 + x2) / 2.0 - self.wide.W / 2.0
                dy = (y1 + y2) / 2.0 - self.wide.H / 2.0
            out["face"] = {
                "camera": result.camera,
                "id": result.track["id"] if result.track else None,
                "bbox": bbox,
                "dx": dx,
                "dy": dy,
                "features": result.face.get("features", {}),
            }
        return out

    def stop(self):
        self.wide.stop()
        self.close.stop()
