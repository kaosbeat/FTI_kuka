"""Mock camera remote for sim validation.

A :class:`Connection` client that plays back simulated ``CAM_*`` telemetry and
reacts to ``cam_control`` event frames. Lets us test the full camera↔core loop
in ``--sim`` mode with no RPI/hardware.

Run alongside the core::

    python -m rtr.camera.camera_mock --core-host 127.0.0.1

The mock:
- Sends ``cam_status`` every second (health + which camera/mode is live).
- Sends ``cam_candidates`` with 2-3 simulated persons (random walk).
- Sends ``cam_track`` with a locked person's offset (drifts slowly).
- Sends ``cam_face`` when in analyze mode with a face visible.
- Reacts to ``cam_control`` by updating its active camera/mode/lock.
- Reads the ``camera`` field from state frames (for reconnect sync).
"""

import argparse
import asyncio
import logging
import random
import time
from typing import Any, Dict, Optional

from ..core.commands import Cmd
from ..remote.connection import Connection

logger = logging.getLogger(__name__)


class CameraMock:
    """Mock camera remote: simulates the RPI pipeline for --sim validation."""

    def __init__(self, core_host: str = "127.0.0.1", core_port: int = 8765,
                 http_port: int = 8766,
                 num_candidates: int = 3,
                 fps: float = 30.0,
                 video: Optional[str] = None,
                 width: int = 640, height: int = 480,
                 model_path: str = "yolo26n.pt",
                 tracker_cfg: str = "bytetrack.yaml"):
        self.core_host = core_host
        self.core_port = core_port
        self.http_port = http_port
        self.num_candidates = num_candidates
        self.fps = fps
        self.video = video

        # Shared bookkeeping.
        self._conn: Optional[Connection] = None
        self._running = False
        self._frame_count = 0
        self._last_face_sent = 0.0
        self._known_ids = set()

        if video:
            # Real pipeline on a video file (same algorithm as the RPI).
            from .pipeline import Pipeline
            self.pipeline = Pipeline(
                wide_cam_num=0, close_cam_num=1,
                width=width, height=height, frame_rate=int(fps),
                model_path=model_path, tracker_cfg=tracker_cfg,
                wide_video=video,
            )
            self._intent = {"active": "wide", "mode": "idle", "lock_id": None}
            self._sent = {"status": 0, "candidates": 0, "track": 0, "face": 0}
            self._last_stats = 0.0
            self._video_fps = self._get_video_fps(video, fallback=fps)
        else:
            self.pipeline = None
            # Current intent from the core (random-walk simulation).
            self._active = "wide"
            self._mode = "idle"
            self._lock_id: Optional[int] = None
            # Simulated person positions (random walk).
            self._people = []
            for i in range(num_candidates):
                self._people.append({
                    "id": i + 1,
                    "x": random.uniform(0.2, 0.8),
                    "y": random.uniform(0., 0.8),
                    "vx": random.uniform(-0.001, 0.001),
                    "vy": random.uniform(-0.001, 0.001),
                    "conf": random.uniform(0.6, 0.95),
                })

    def _on_state(self, data: Dict[str, Any]) -> None:
        """Handle a state frame; extract the camera intent (for reconnect sync)."""
        cam = data.get("camera")
        if cam is not None:
            self._apply_intent(cam)

    def _on_cam_control(self, data: Dict[str, Any]) -> None:
        """Handle a cam_control event frame (fast path)."""
        self._apply_intent(data)

    def _on_event(self, data: Dict[str, Any]) -> None:
        """Handle a non-state event frame (the Connection's on_event hook)."""
        if data.get("type") == "cam_control":
            self._on_cam_control(data)

    def _apply_intent(self, intent: Dict[str, Any]) -> None:
        """Apply the camera intent (to the pipeline or the simulation state)."""
        active = intent.get("active", "wide")
        mode = intent.get("mode", "idle")
        lock_id = intent.get("lock_id")
        if self.video:
            new_intent = {"active": active, "mode": mode, "lock_id": lock_id}
            if new_intent != self._intent:
                logger.info("mock camera intent: %s", new_intent)
                self._intent = new_intent
                self.pipeline.configure(active, mode, lock_id)
        else:
            if active != self._active or mode != self._mode or lock_id != self._lock_id:
                logger.info("mock camera intent: %s", intent)
                self._active = active
                self._mode = mode
                self._lock_id = lock_id

    def _get_video_fps(self, path: str, fallback: float = 30.0) -> float:
        """Read the video's FPS (fallback if unavailable)."""
        try:
            import cv2
            cap = cv2.VideoCapture(path)
            fps = cap.get(cv2.CAP_PROP_FPS)
            cap.release()
            return float(fps) if fps and fps > 0 else fallback
        except Exception:
            return fallback

    def _send_telemetry(self, payloads: Dict[str, Dict[str, Any]]) -> None:
        """Send CAM_* telemetry payloads to the core (video mode)."""
        if self._conn is None or not self._conn.connected:
            return
        for key, cmd in (("status", Cmd.CAM_STATUS), ("candidates", Cmd.CAM_CANDIDATES),
                         ("track", Cmd.CAM_TRACK), ("face", Cmd.CAM_FACE)):
            if key in payloads:
                self._conn.send_command({"cmd": cmd.value, **payloads[key]})
                self._sent[key] += 1

    def _log_targets(self, result) -> None:
        """Log when candidate targets appear or disappear (video mode)."""
        if not result.active:
            return
        ids = {c["id"] for c in result.candidates}
        for cid in sorted(ids - self._known_ids):
            c = next(x for x in result.candidates if x["id"] == cid)
            logger.info("target found: id=%s conf=%.2f (%d candidates)", cid, c["conf"], len(ids))
        for cid in sorted(self._known_ids - ids):
            logger.info("target lost: id=%s", cid)
        self._known_ids = ids

    async def _video_loop(self) -> None:
        """Run the real pipeline on the video; send telemetry each frame.

        Mirrors CameraRemote._pipeline_loop: the blocking YOLO inference runs in a
        thread so the asyncio loop (and the WebSocket link) stay responsive.
        """
        interval = 1.0 / self._video_fps
        loop = asyncio.get_running_loop()
        while self._running:
            start = time.time()
            try:
                result = await loop.run_in_executor(None, self.pipeline.process)
                payloads = self.pipeline.to_telemetry(result)
                self._send_telemetry(payloads)
                self._log_targets(result)
            except Exception as exc:
                logger.error("pipeline error: %s", exc)
            now = time.time()
            if now - self._last_stats >= 5.0:
                logger.info("telemetry sent: %s",
                            " ".join(f"{k}={v}" for k, v in self._sent.items()))
                self._last_stats = now
            elapsed = now - start
            sleep_for = interval - elapsed
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    def _step_people(self) -> None:
        """Advance the simulated people by a small random walk."""
        for p in self._people:
            p["vx"] += random.uniform(-0.0005, 0.0005)
            p["vy"] += random.uniform(-0.0005, 0.0005)
            p["vx"] = max(-0.002, min(0.002, p["vx"]))
            p["vy"] = max(-0.002, min(0.002, p["vy"]))
            p["x"] += p["vx"]
            p["y"] += p["vy"]
            # Bounce off edges.
            if p["x"] < 0.05 or p["x"] > 0.95:
                p["vx"] *= -1
                p["x"] = max(0.05, min(0.95, p["x"]))
            if p["y"] < 0.05 or p["y"] > 0.95:
                p["vy"] *= -1
                p["y"] = max(0.05, min(0.95, p["y"]))
            # Slightly fluctuate confidence.
            p["conf"] = max(0.3, min(0.99, p["conf"] + random.uniform(-0.01, 0.01)))

    def _send_mock_telemetry(self) -> None:
        """Send one frame of simulated CAM_* telemetry to the core (random-walk mode)."""
        if self._conn is None or not self._conn.connected:
            return

        self._step_people()
        self._frame_count += 1

        # Build candidates (all simulated persons).
        candidates = []
        for p in self._people:
            w = random.uniform(0.08, 0.15)
            h = random.uniform(0.12, 0.25)
            candidates.append({
                "id": p["id"],
                "x1": max(0, p["x"] - w / 2),
                "y1": max(0, p["y"] - h / 2),
                "x2": min(1.0, p["x"] + w / 2),
                "y2": min(1.0, p["y"] + h / 2),
                "conf": p["conf"],
            })

        # Send status (every frame for simplicity; could be throttled).
        self._conn.send_command({
            "cmd": Cmd.CAM_STATUS.value,
            "camera": self._active,
            "mode": self._mode,
            "active": True,
            "fps": self.fps,
            "ok": True,
        })

        # Send candidates.
        self._conn.send_command({
            "cmd": Cmd.CAM_CANDIDATES.value,
            "camera": self._active,
            "candidates": candidates,
        })

        # Send track (if we have a lock and the locked person is visible).
        if self._lock_id is not None:
            locked = next((p for p in self._people if p["id"] == self._lock_id), None)
            if locked is not None:
                W, H = 640, 480
                cx = locked["x"] * W
                cy = locked["y"] * H
                dx = cx - W / 2.0
                dy = cy - H / 2.0
                w = random.uniform(50, 100)
                h = random.uniform(80, 180)
                self._conn.send_command({
                    "cmd": Cmd.CAM_TRACK.value,
                    "camera": self._active,
                    "id": self._lock_id,
                    "cx": cx,
                    "cy": cy,
                    "dx": dx,
                    "dy": dy,
                    "w": w,
                    "h": h,
                })

                # Send face (when in analyze mode, with some probability).
                if self._mode == "analyze" and time.time() - self._last_face_sent > 0.5:
                    self._last_face_sent = time.time()
                    self._conn.send_command({
                        "cmd": Cmd.CAM_FACE.value,
                        "camera": self._active,
                        "id": self._lock_id,
                        "bbox": [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2],
                        "features": {"detected": True, "emotion": "neutral"},
                    })

    async def _mock_loop(self) -> None:
        """Run the mock telemetry loop at the target frame rate."""
        interval = 1.0 / self.fps
        while self._running:
            start = time.time()
            try:
                self._send_mock_telemetry()
            except Exception as exc:
                logger.error("mock telemetry error: %s", exc)
            elapsed = time.time() - start
            sleep_for = interval - elapsed
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    async def run(self) -> None:
        """Start the connection and the telemetry loop (video or random-walk)."""
        self._running = True

        self._conn = Connection(
            ws_host=self.core_host,
            ws_port=self.core_port,
            http_port=self.http_port,
            identity="camera",
            on_state=self._on_state,
            on_event=self._on_event,
            on_error=lambda e: logger.warning("connection error: %s", e),
        )
        await self._conn.start()
        if await self._conn.wait_connected(5.0):
            logger.info("mock camera connected to %s:%d", self.core_host, self.core_port)
        else:
            logger.warning("mock camera: WS link not up after 5s; telemetry will be dropped until it connects")

        if self.video:
            logger.info("mock camera: running pipeline on video %s", self.video)
            loop_task = asyncio.create_task(self._video_loop())
        else:
            loop_task = asyncio.create_task(self._mock_loop())
        try:
            await asyncio.Future()  # run until stopped
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._running = False
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)
            await self._conn.stop()
            if self.video:
                self.pipeline.stop()
            logger.info("mock camera stopped")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Mock camera remote for sim validation")
    parser.add_argument("--core-host", default="127.0.0.1", help="core WS host")
    parser.add_argument("--core-port", type=int, default=8765, help="core WS port")
    parser.add_argument("--http-port", type=int, default=8766, help="core HTTP port")
    parser.add_argument("--candidates", type=int, default=3, help="number of simulated persons")
    parser.add_argument("--fps", type=float, default=30.0, help="telemetry frame rate")
    parser.add_argument("--video", default=None,
                        help="video file to run the real YOLO+ByteTrack pipeline on (overrides the random-walk simulation)")
    parser.add_argument("--width", type=int, default=640, help="frame width (video mode)")
    parser.add_argument("--height", type=int, default=480, help="frame height (video mode)")
    parser.add_argument("--model", default="yolo26n.pt", help="YOLO model path (video mode)")
    parser.add_argument("--tracker", default="bytetrack.yaml", help="tracker config (video mode)")
    parser.add_argument("--log-level", default="INFO", help="log level")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(name)s %(levelname)s: %(message)s")

    mock = CameraMock(
        core_host=args.core_host,
        core_port=args.core_port,
        http_port=args.http_port,
        num_candidates=args.candidates,
        fps=args.fps,
        video=args.video,
        width=args.width,
        height=args.height,
        model_path=args.model,
        tracker_cfg=args.tracker,
    )

    try:
        asyncio.run(mock.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
