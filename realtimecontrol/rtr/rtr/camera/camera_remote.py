"""RPI camera remote: standalone process that runs the two-camera pipeline.

Connects to the core via the existing WebSocket (reusing
:class:`rtr.remote.connection.Connection`). Receives ``cam_control`` event
frames and the ``camera`` field in state frames to configure the physical
cameras. Sends ``CAM_*`` telemetry (status, candidates, track, face) to the
core via ``Connection.send_command``.

Run on the RPI where the physical cameras are attached::

    python -m rtr.camera.camera_remote --core-host 192.168.1.10
"""

import argparse
import asyncio
import logging
import time
from typing import Any, Dict, Optional

from ..core.commands import Cmd
from ..remote.connection import Connection
from .pipeline import Pipeline

logger = logging.getLogger(__name__)


class CameraRemote:
    """RPI-side camera remote: runs the pipeline and bridges to the core."""

    def __init__(self, core_host: str = "127.0.0.1", core_port: int = 8765,
                 http_port: int = 8766,
                 wide_cam_num: int = 0, close_cam_num: int = 1,
                 width: int = 640, height: int = 480,
                  frame_rate: int = 30,
                  model_path: str = "yolo26n.pt",
                  tracker_cfg: str = "bytetrack.yaml"):
        self.core_host = core_host
        self.core_port = core_port
        self.http_port = http_port

        self.pipeline = Pipeline(
            wide_cam_num=wide_cam_num,
            close_cam_num=close_cam_num,
            width=width,
            height=height,
            frame_rate=frame_rate,
            model_path=model_path,
            tracker_cfg=tracker_cfg,
        )

        self._conn: Optional[Connection] = None
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Current intent from the core (active, mode, lock_id).
        self._intent = {"active": "wide", "mode": "idle", "lock_id": None}

    def _on_state(self, data: Dict[str, Any]) -> None:
        """Handle a state frame from the core; extract the camera intent."""
        cam = data.get("camera")
        if cam is not None:
            self._apply_intent(cam)

    def _on_cam_control(self, data: Dict[str, Any]) -> None:
        """Handle a cam_control event frame (fast path for intent changes)."""
        self._apply_intent(data)

    def _on_event(self, data: Dict[str, Any]) -> None:
        """Handle a non-state event frame (the Connection's on_event hook)."""
        if data.get("type") == "cam_control":
            self._on_cam_control(data)

    def _apply_intent(self, intent: Dict[str, Any]) -> None:
        """Apply the camera intent to the pipeline."""
        active = intent.get("active", "wide")
        mode = intent.get("mode", "idle")
        lock_id = intent.get("lock_id")
        if active != self._intent["active"] or mode != self._intent["mode"] or lock_id != self._intent["lock_id"]:
            logger.info("camera intent: %s", intent)
            self._intent = {"active": active, "mode": mode, "lock_id": lock_id}
            self.pipeline.configure(active, mode, lock_id)

    def _send_telemetry(self, payloads: Dict[str, Dict[str, Any]]) -> None:
        """Send CAM_* telemetry to the core via the WS connection."""
        if self._conn is None or not self._conn.connected:
            return
        if "status" in payloads:
            self._conn.send_command({"cmd": Cmd.CAM_STATUS.value, **payloads["status"]})
        if "candidates" in payloads:
            self._conn.send_command({"cmd": Cmd.CAM_CANDIDATES.value, **payloads["candidates"]})
        if "track" in payloads:
            self._conn.send_command({"cmd": Cmd.CAM_TRACK.value, **payloads["track"]})
        if "face" in payloads:
            self._conn.send_command({"cmd": Cmd.CAM_FACE.value, **payloads["face"]})

    async def _pipeline_loop(self) -> None:
        """Run the pipeline at the target frame rate; send telemetry each frame."""
        interval = 1.0 / self.pipeline.wide.frame_rate
        while self._running:
            start = time.time()
            try:
                result = self.pipeline.process()
                payloads = self.pipeline.to_telemetry(result)
                self._send_telemetry(payloads)
            except Exception as exc:
                logger.error("pipeline error: %s", exc)
            elapsed = time.time() - start
            sleep_for = interval - elapsed
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    async def run(self) -> None:
        """Start the connection and the pipeline loop."""
        self._running = True
        self._loop = asyncio.get_running_loop()

        self._conn = Connection(
            ws_host=self.core_host,
            ws_port=self.core_port,
            http_port=self.http_port,
            on_state=self._on_state,
            on_event=self._on_event,
            on_error=lambda e: logger.warning("connection error: %s", e),
        )
        await self._conn.start()
        logger.info("camera remote connected to %s:%d", self.core_host, self.core_port)

        # Apply the current intent from the state frame (the Connection's
        # on_state callback will fire when the first state frame arrives).

        pipeline_task = asyncio.create_task(self._pipeline_loop())
        try:
            await asyncio.Future()  # run until stopped
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._running = False
            pipeline_task.cancel()
            await asyncio.gather(pipeline_task, return_exceptions=True)
            await self._conn.stop()
            self.pipeline.stop()
            logger.info("camera remote stopped")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="RPI camera remote")
    parser.add_argument("--core-host", default="127.0.0.1", help="core WS host")
    parser.add_argument("--core-port", type=int, default=8765, help="core WS port")
    parser.add_argument("--http-port", type=int, default=8766, help="core HTTP port")
    parser.add_argument("--wide-cam", type=int, default=0, help="wide camera device number")
    parser.add_argument("--close-cam", type=int, default=1, help="close camera device number")
    parser.add_argument("--width", type=int, default=640, help="frame width")
    parser.add_argument("--height", type=int, default=480, help="frame height")
    parser.add_argument("--fps", type=int, default=30, help="target frame rate")
    parser.add_argument("--model", default="yolo26n.pt", help="YOLO model path (resolved in rtr/camera/models)")
    parser.add_argument("--tracker", default="bytetrack.yaml", help="tracker config")
    parser.add_argument("--log-level", default="INFO", help="log level")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(name)s %(levelname)s: %(message)s")

    remote = CameraRemote(
        core_host=args.core_host,
        core_port=args.core_port,
        http_port=args.http_port,
        wide_cam_num=args.wide_cam,
        close_cam_num=args.close_cam,
        width=args.width,
        height=args.height,
        frame_rate=args.fps,
        model_path=args.model,
        tracker_cfg=args.tracker,
    )

    try:
        asyncio.run(remote.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
