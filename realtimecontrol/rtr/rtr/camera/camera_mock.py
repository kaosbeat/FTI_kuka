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
                 fps: float = 30.0):
        self.core_host = core_host
        self.core_port = core_port
        self.http_port = http_port
        self.num_candidates = num_candidates
        self.fps = fps

        # Current intent from the core.
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

        self._conn: Optional[Connection] = None
        self._running = False
        self._frame_count = 0
        self._last_face_sent = 0.0

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
        """Apply the camera intent."""
        active = intent.get("active", "wide")
        mode = intent.get("mode", "idle")
        lock_id = intent.get("lock_id")
        if active != self._active or mode != self._mode or lock_id != self._lock_id:
            logger.info("mock camera intent: %s", intent)
            self._active = active
            self._mode = mode
            self._lock_id = lock_id

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

    def _send_telemetry(self) -> None:
        """Send one frame of simulated CAM_* telemetry to the core."""
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
                self._send_telemetry()
            except Exception as exc:
                logger.error("mock telemetry error: %s", exc)
            elapsed = time.time() - start
            sleep_for = interval - elapsed
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    async def run(self) -> None:
        """Start the connection and the mock telemetry loop."""
        self._running = True

        self._conn = Connection(
            ws_host=self.core_host,
            ws_port=self.core_port,
            http_port=self.http_port,
            on_state=self._on_state,
            on_event=self._on_event,
            on_error=lambda e: logger.warning("connection error: %s", e),
        )
        await self._conn.start()
        if await self._conn.wait_connected(5.0):
            logger.info("mock camera connected to %s:%d", self.core_host, self.core_port)
        else:
            logger.warning("mock camera: WS link not up after 5s; telemetry will be dropped until it connects")

        mock_task = asyncio.create_task(self._mock_loop())
        try:
            await asyncio.Future()  # run until stopped
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._running = False
            mock_task.cancel()
            await asyncio.gather(mock_task, return_exceptions=True)
            await self._conn.stop()
            logger.info("mock camera stopped")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Mock camera remote for sim validation")
    parser.add_argument("--core-host", default="127.0.0.1", help="core WS host")
    parser.add_argument("--core-port", type=int, default=8765, help="core WS port")
    parser.add_argument("--http-port", type=int, default=8766, help="core HTTP port")
    parser.add_argument("--candidates", type=int, default=3, help="number of simulated persons")
    parser.add_argument("--fps", type=float, default=30.0, help="telemetry frame rate")
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
    )

    try:
        asyncio.run(mock.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
