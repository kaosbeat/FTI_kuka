"""Brain: the command interpreter.

The state machine is the single owner of the robot's state (zone, action, target,
speed, flags, cadence, camera telemetry). The brain no longer holds any of that — it
is a thin layer that maps incoming :class:`~rtr.core.commands.Command` objects onto
the machine's operations. It is the seam where "programmed logic, a small LLM, or
realtime control" can plug in: subclass :class:`Brain` and override :meth:`on_command`
to change what a command does, without touching the state machine, the robot, or the
adapters.

The per-tick decision (which target to command the robot to) is made by the
:meth:`~rtr.state.machine.StateMachine.step` driver, not here. The brain only folds
commands into the machine; the engine calls ``machine.step`` every tick.

Camera telemetry is stashed on ``machine.camera_state`` (see :meth:`_handle_cam_track`)
and read by the variable-axis behaviours — the brain no longer nudges the target pose
directly.
"""

import time
from typing import List, Optional

from ..core.commands import Cmd, Command
from ..state.behavior import clamp
from ..state.machine import StateMachine
from ..state.zones import MODES  # re-exported for `from rtr.brain import MODES`


class Brain:
    """Maps commands onto the state machine. Holds no robot state of its own."""

    def __init__(self, machine: StateMachine,
                 camera=None):
        self.machine = machine
        self.camera = camera
        # Receive-side visibility: log CAM_* telemetry (throttled).
        self._last_cand_ids = None
        self._cam_log_ts: dict = {}
        # Hunt loop: scan -> detect -> lock -> track -> lost -> rescan.
        # The brain owns this (it is the decision-maker); the machine owns (zone, action).
        self._hunt_state = "idle"  # "idle" | "tracking" | "scanning"
        self._hunt_lock_id = None
        self._hunt_last_track_ts = 0.0
        self._hunt_lost_s = 2.0

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
        if c == Cmd.TRIGGER_ACTION:
            self.machine.trigger_action(p.get("zone"), p.get("action"))
        elif c == Cmd.PLAY_ACTION:
            self.machine.play_action(p.get("action"))
        elif c == Cmd.CLEAR_ACTION:
            self.machine.clear_action()
        elif c == Cmd.SET_JOINT_POSE:
            # Joint poses are floored to the effective floor (safezone ∩ hardware);
            # linear poses are Cartesian and must not be floor-clamped.
            self.machine.set_target(clamp(self.machine, list(p["pose"])), "joint")
            self.machine.clear_action()  # hold the target
        elif c == Cmd.SET_LINEAR_POSE:
            self.machine.set_target(list(p["pose"]), "linear")
        elif c == Cmd.ADJUST_LIMIT:
            i = int(p.get("index", 0))
            self.machine.limitadjust[i] = float(p.get("value", self.machine.limitadjust[i]))
        elif c == Cmd.RANDOM_WRIST:
            self.machine.random_wrist()
        elif c == Cmd.STOP:
            self.machine.halt(curjpos)
        elif c == Cmd.SET_FLAG:
            name = p.get("flag")
            if name in self.machine.flags:
                self.machine.flags[name] = p.get("value")
        elif c == Cmd.CAM_STATUS:
            self._handle_cam_status(p)
        elif c == Cmd.CAM_CANDIDATES:
            self._handle_cam_candidates(p)
        elif c == Cmd.CAM_TRACK:
            self._handle_cam_track(p)
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
                self.camera.set_intent(lock_id=lock_id, mode="track")
                if lock_id != self._hunt_lock_id:
                    # New lock: the new target's CAM_TRACK lags ~1 frame, so seed the
                    # lost timer now (a stale timestamp would cause a false "lost").
                    self._hunt_last_track_ts = time.time()
                    self._hunt_lock_id = lock_id
                # Attention grab: pull the robot into the hunt unless it is already
                # tracking/looking or a zone transition is in progress.
                if (self.machine.behavior not in ("track", "focus", "look")
                        and not self.machine.transitioning):
                    self.machine.trigger_action("wakeup", "look")
                self._hunt_state = "tracking"

    def _handle_cam_track(self, p: dict) -> None:
        """Store the track telemetry on the machine; the behaviours read it.

        The brain no longer nudges the target pose directly. The camera offset
        (dx/dy) is folded into the variable-axis behaviour of whatever action is
        currently playing (track/focus/look read ``machine.camera_state`` each tick).
        """
        if self.camera is not None:
            self.camera.handle_telemetry(Cmd.CAM_TRACK, p)
        self._cam_log("track", f"track: id={p.get('id')} dx={p.get('dx', 0):.1f} dy={p.get('dy', 0):.1f}")
        # Refresh the hunt lost-timer only for the locked id (a stale track for a
        # different id — e.g. a lingering lock while scanning — must not count).
        if p.get("id") == self._hunt_lock_id:
            self._hunt_last_track_ts = time.time()
        self.machine.camera_state = {
            "dx": p.get("dx", 0.0),
            "dy": p.get("dy", 0.0),
            "w": p.get("w", 0.0),
            "h": p.get("h", 0.0),
            "id": p.get("id"),
            "locked": p.get("locked", False),
            "ok": p.get("ok", True),
        }

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

    # ------------------------------------------------------------------
    # Per-tick hunt bookkeeping.
    # ------------------------------------------------------------------
    def tick(self, curjpos: List[float]) -> None:
        """Detect a lost lock and rescan (the hunt's time-driven step).

        Called by the engine after the command drain and before ``machine.step``, so a
        triggered transition is driven the same tick. Only the ``tracking`` state arms
        the lost check; ``scanning`` waits for the next ``CAM_CANDIDATES`` re-detect.
        """
        if self._hunt_state != "tracking":
            return
        if time.time() - self._hunt_last_track_ts <= self._hunt_lost_s:
            return
        # Lost: clear the lock and rescan.
        if self.camera is not None:
            self.camera.set_intent(lock_id=None, mode="idle")
        self._hunt_lock_id = None
        self.machine.trigger_action("wakeup", "scan")
        self._hunt_state = "scanning"
