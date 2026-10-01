"""Brain: the command interpreter.

The state machine is the single owner of the robot's state (zone, mode, action,
target, speed, flags, cadence). The brain no longer holds any of that — it is a thin
layer that maps incoming :class:`~rtr.core.commands.Command` objects onto the machine's
operations. It is the seam where "programmed logic, a small LLM, or realtime control"
can plug in: subclass :class:`Brain` and override :meth:`on_command` to change what a
command does, without touching the state machine, the robot, or the adapters.

The per-tick decision (which target to command the robot to) is made by the
:meth:`~rtr.state.machine.StateMachine.step` driver, not here. The brain only folds
commands into the machine; the engine calls ``machine.step`` every tick.
"""

import time
from typing import List, Optional

from ..core.commands import Cmd, Command
from ..state.behavior import clamp
from ..state.machine import StateMachine
from ..state.zones import MODES  # re-exported for `from rtr.brain import MODES`

# Zones that allow full tracking motion (the robot can move to follow a person).
TRACK_ZONES = ("wander", "wildwander")
# Zones that allow reduced motion (a head-only or partial nudge).
LOOK_ZONES = ("wakeup", "stretch")
# Zones that are stationary (no tracking motion; head-only actions are handled
# by the zone's own action table).
STILL_ZONES = ("init", "rest")

# Placeholder servoing gain (pixels → degrees). To be tuned per zone / camera
# in a field test with the calibration tool.
_CAM_GAIN = 0.05  # degrees per pixel of offset


class Brain:
    """Maps commands onto the state machine. Holds no robot state of its own."""

    def __init__(self, machine: StateMachine,
                 camera=None):
        self.machine = machine
        self.camera = camera
        # Receive-side visibility: log CAM_* telemetry (throttled).
        self._last_cand_ids = None
        self._cam_log_ts: dict = {}

    def _cam_log(self, key: str, msg: str, force: bool = False) -> None:
        """Log camera telemetry at most once per second (force overrides)."""
        now = time.time()
        if force or now - self._cam_log_ts.get(key, 0.0) >= 1.0:
            print(f"[brain] cam {msg}")
            self._cam_log_ts[key] = now

    # ------------------------------------------------------------------
    # Command intake.
    # ------------------------------------------------------------------
    def on_command(self, cmd: Command, curjpos: List[float]) -> None:
        """Fold one command into the state machine."""
        c = cmd.cmd
        p = cmd.payload
        if c == Cmd.GOTO_ZONE:
            self.machine.request_zone(p.get("zone"), curjpos)
        elif c == Cmd.SET_MODE:
            self.machine.set_mode(p.get("mode", "wander"))
        elif c == Cmd.PLAY_ACTION:
            self.machine.play_action(p.get("action"))
        elif c == Cmd.CLEAR_ACTION:
            self.machine.clear_action()
        elif c == Cmd.SET_JOINT_POSE:
            # Joint poses are floored to the effective floor (safezone ∩ hardware);
            # linear poses are Cartesian and must not be floor-clamped.
            self.machine.set_target(clamp(self.machine, list(p["pose"])), "joint")
            self.machine.set_mode("hold")
        elif c == Cmd.SET_LINEAR_POSE:
            self.machine.set_target(list(p["pose"]), "linear")
        elif c == Cmd.ADJUST_LIMIT:
            i = int(p.get("index", 0))
            self.machine.limitadjust[i] = float(p.get("value", self.machine.limitadjust[i]))
        elif c == Cmd.RANDOM_WRIST:
            self.machine.random_wrist()
        elif c == Cmd.SET_FLAG:
            name = p.get("flag")
            if name in self.machine.flags:
                self.machine.flags[name] = p.get("value")
        elif c == Cmd.CAM_STATUS:
            self._handle_cam_status(p)
        elif c == Cmd.CAM_CANDIDATES:
            self._handle_cam_candidates(p)
        elif c == Cmd.CAM_TRACK:
            self._handle_cam_track(p, curjpos)
        elif c == Cmd.CAM_FACE:
            self._handle_cam_face(p)

    # ------------------------------------------------------------------
    # Camera telemetry handling.
    # ------------------------------------------------------------------
    def _handle_cam_status(self, p: dict) -> None:
        """Store the camera health/status telemetry."""
        self._cam_log("status", f"status: camera={p.get('camera')} fps={p.get('fps', 0):.1f} ok={p.get('ok')}")
        if self.camera is not None:
            self.camera.handle_telemetry(Cmd.CAM_STATUS, p)

    def _handle_cam_candidates(self, p: dict) -> None:
        """Store candidates and pick a lock target.

        The lock heuristic: highest confidence, breaking ties by centrality
        (smaller distance from image center wins).
        """
        if self.camera is not None:
            self.camera.handle_telemetry(Cmd.CAM_CANDIDATES, p)
        cands = p.get("candidates", [])
        ids = tuple(c.get("id") for c in cands)
        changed = ids != self._last_cand_ids
        self._last_cand_ids = ids
        if cands:
            self._cam_log("cand", f"candidates: ids={list(ids)} conf={[round(c.get('conf', 0), 2) for c in cands]}", force=changed)
        elif self._cam_log_ts.get("cand"):
            self._cam_log("cand", "candidates: (none)")
        if not cands:
            return
        best = None
        best_score = -1
        for c in cands:
            conf = c.get("conf", 0)
            # Centrality: smaller distance from (0.5, 0.5) is better.
            cx = (c.get("x1", 0) + c.get("x2", 0)) / 2
            cy = (c.get("y1", 0) + c.get("y2", 0)) / 2
            dist = (cx - 0.5) ** 2 + (cy - 0.5) ** 2
            score = conf - dist * 0.1  # confidence dominates, centrality breaks ties
            if score > best_score:
                best_score = score
                best = c
        if best is not None:
            lock_id = best.get("id")
            if self.camera is not None and lock_id is not None:
                self.camera.set_intent(lock_id=lock_id)

    def _handle_cam_track(self, p: dict, curjpos: List[float]) -> None:
        """Store track data and issue a zone-gated nudge.

        The nudge is an incremental joint pose offset proportional to the pixel
        offset (dx, dy). The gain and axis mapping are placeholders pending the
        field-test calibration.
        """
        if self.camera is not None:
            self.camera.handle_telemetry(Cmd.CAM_TRACK, p)
        self._cam_log("track", f"track: id={p.get('id')} dx={p.get('dx', 0):.1f} dy={p.get('dy', 0):.1f}")

        zone = self.machine.current_zone
        if zone in STILL_ZONES:
            return  # no tracking motion in stationary zones

        dx = p.get("dx", 0)
        dy = p.get("dy", 0)
        if dx == 0 and dy == 0:
            return

        # Reduced gain in look zones, full gain in track zones.
        gain = _CAM_GAIN if zone in TRACK_ZONES else _CAM_GAIN * 0.5

        # Simple axis mapping: dx → A1 (pan), dy → A2 (tilt).
        # The sign convention depends on the camera mount orientation; the
        # RPI reports absolute pixel offsets so the core must know the mount.
        nudge = [0.0] * 6
        nudge[0] = dx * gain  # A1: horizontal
        nudge[1] = dy * gain  # A2: vertical

        # Apply the nudge to the current joint pose.
        target = [curjpos[i] + nudge[i] for i in range(6)]
        self.machine.set_target(clamp(self.machine, target), "joint")
        self.machine.set_mode("hold")

    def _handle_cam_face(self, p: dict) -> None:
        """Store face analysis telemetry.

        Face-driven camera switching (close+analyze) is a follow-up; for now
        we just store the data and switch to close+analyze when a face is
        detected.
        """
        if self.camera is not None:
            self.camera.handle_telemetry(Cmd.CAM_FACE, p)
            # Simple heuristic: face detected → close camera + analyze mode.
            if p.get("bbox") is not None:
                self.camera.set_intent(active="close", mode="analyze")
                self._cam_log("face", f"face: id={p.get('id')} bbox={p.get('bbox')}")
