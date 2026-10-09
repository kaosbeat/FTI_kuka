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
from .config import builtin_brain_config


class Brain:
    """Maps commands onto the state machine. Holds no robot state of its own."""

    def __init__(self, machine: StateMachine,
                 camera=None, config_loader=None):
        self.machine = machine
        self.camera = camera
        # Receive-side visibility: log CAM_* telemetry (throttled).
        self._last_cand_ids = None
        self._cam_log_ts: dict = {}
        # Decision config (brain.json): the hunt-loop parameters. Loaded via a callable
        # ``config_loader`` (returns a validated config, raising on a bad file); falls
        # back to the built-in defaults when no loader is given or the file is bad.
        self._config_loader = config_loader
        self._config = builtin_brain_config()
        if config_loader is not None:
            self.reload()
        # Hunt loop: scan -> detect -> lock -> track -> lost -> rescan.
        # The brain owns this (it is the decision-maker); the machine owns (zone, action).
        self._hunt_state = "idle"  # "idle" | "tracking" | "facefocus" | "scanning"
        self._hunt_lock_id = None
        self._hunt_last_track_ts = 0.0
        # Feral detection: the robot is "feral" when it has lost a locked person
        # 3+ times within a 30 s window (rapid hunt-loop cycling).
        self._feral_lost_ts: list = []
        self._feral: bool = False
        # Facefocus (the "check out that human" sub-state of tracking): while a face is
        # visible the robot stretches into the exterior pose and cycles the face actions
        # (wink / inspect / call), nudging toward the face location.
        self._facefocus_action_index = 0
        self._facefocus_last_action_ts = 0.0
        self._facefocus_last_face_ts = 0.0
        # Autonomy navigation state (following next links across the enabled zone union).
        self._autonomy_action_start_ts = 0.0
        self._autonomy_prev: Optional[tuple] = None  # last (zone, action)
        self._autonomy_triggered = False  # event fired; advance on next tick
        self._enabled_groups: set = set()
        self._enabled_zones: set = set()
        if self._config is not None:
            self._recompute_enabled()

    def config(self) -> dict:
        """The currently-loaded brain decision config."""
        return self._config

    def reload(self) -> None:
        """Re-read the brain config (hunt-loop parameters) from disk.

        On failure keep the current config (logged) so a bad edit never kills the core.
        """
        if self._config_loader is None:
            return
        try:
            cfg = self._config_loader()
        except (OSError, ValueError) as exc:
            print(f"[brain] config reload failed ({exc}); keeping current config")
            return
        self._config = cfg
        self._recompute_enabled()
        groups = cfg.get("zone_groups", {})
        # Zone-name cross-reference: warn (not reject) on unknown zone names so a
        # construction-time false negative never kills the core.
        zone_names = set(self.machine.zones.names()) if self.machine else set()
        for gname, g in groups.items():
            if not isinstance(g, dict):
                continue
            for zn in g.get("zones", []):
                if zn not in zone_names:
                    print(f"[brain] WARNING: zone_groups.{gname} references unknown zone {zn!r}")
        hunt = groups.get("hunt", {})
        hp = hunt.get("params", {})
        print(f"[brain] config reloaded: hunt.enabled={hunt.get('enabled')} "
              f"lost_s={hp.get('lost_s')} "
              f"detect={hp.get('detect', {}).get('zone')}/{hp.get('detect', {}).get('action')} "
              f"scan={hp.get('scan', {}).get('zone')}/{hp.get('scan', {}).get('action')}")
        ff = groups.get("facefocus", {})
        fp = ff.get("params", {})
        print(f"[brain] facefocus: enabled={ff.get('enabled')} zones={ff.get('zones')} "
              f"actions={fp.get('actions')} action_s={fp.get('action_s')} "
              f"face_lost_s={fp.get('face_lost_s')}")

    def _autonomy_cfg(self) -> dict:
        """The autonomy master config (``{enabled, dwell_s, events}``)."""
        return self._config.get("autonomy", {})

    def _hunt_group(self) -> dict:
        """The hunt zone-group config (``{zones, behaviors, enabled, params}``)."""
        return self._config.get("zone_groups", {}).get("hunt", {})

    def _facefocus_group(self) -> dict:
        """The facefocus zone-group config (``{zones, behaviors, enabled, params}``)."""
        return self._config.get("zone_groups", {}).get("facefocus", {})

    def _recompute_enabled(self) -> None:
        """Recompute the set of enabled groups and their zone union.

        Called after config load/reload so the autonomy tick and behavior gates
        always see the current autonomy level.
        """
        self._enabled_groups = set()
        self._enabled_zones = set()
        for name, g in self._config.get("zone_groups", {}).items():
            if isinstance(g, dict) and g.get("enabled"):
                self._enabled_groups.add(name)
                for z in g.get("zones", []):
                    if isinstance(z, str):
                        self._enabled_zones.add(z)

    def hunt_info(self) -> dict:
        """The hunt state for the state frame (the display/editor reads it live)."""
        return {
            "state": self._hunt_state,
            "lock_id": self._hunt_lock_id,
            "feral": self._feral,
        }

    def autonomy_info(self) -> dict:
        """The autonomy state for the state frame (the display/editor reads it live).

        Returns a dict with:
        - ``enabled``: bool, whether autonomy is on.
        - ``enabled_groups``: sorted list of enabled group names.
        - ``performance_mode``: bool, derived — the ``perform`` group is enabled
          while both ``hunt`` and ``facefocus`` are disabled.
        - ``dwell_remaining``: float seconds remaining for the current action's
          dwell, or None when autonomy is off / no active action.
        """
        cfg = self._autonomy_cfg()
        enabled = bool(cfg.get("enabled", False))
        groups = sorted(self._enabled_groups)
        # Performance mode: perform enabled, hunt and facefocus disabled.
        perform = self._config.get("zone_groups", {}).get("perform", {})
        hunt = self._config.get("zone_groups", {}).get("hunt", {})
        facefocus = self._config.get("zone_groups", {}).get("facefocus", {})
        perf = bool(perform.get("enabled", False)) and not bool(hunt.get("enabled", False)) and not bool(facefocus.get("enabled", False))
        # Dwell remaining: only meaningful when autonomy is on and there is an active action.
        dwell_remaining = None
        if enabled:
            zone = self.machine.current_zone
            action = self.machine.current_action
            if zone and action:
                dwell = self._autonomy_dwell_s(zone, action)
                elapsed = time.time() - self._autonomy_action_start_ts
                dwell_remaining = max(0.0, dwell - elapsed)
        return {
            "enabled": enabled,
            "enabled_groups": groups,
            "performance_mode": perf,
            "dwell_remaining": dwell_remaining,
        }

    def _update_hunt_state(self) -> None:
        """Write the current hunt state to the machine (the display reads it)."""
        self.machine.hunt_state = {
            "state": self._hunt_state,
            "lock_id": self._hunt_lock_id,
            "feral": self._feral,
        }

    def _record_lost(self) -> None:
        """Record a person-lost event and update the feral flag.

        Feral = 3+ lost events within a 30 s sliding window (the hunt loop is
        cycling rapidly: detect → track → lost → rescan → detect → …).
        """
        now = time.time()
        self._feral_lost_ts.append(now)
        # Prune events older than the window.
        cutoff = now - 30.0
        self._feral_lost_ts = [t for t in self._feral_lost_ts if t >= cutoff]
        self._feral = len(self._feral_lost_ts) >= 3

    def _clear_feral_if_stale(self) -> None:
        """Clear the feral flag when all lost events fall outside the 30 s window."""
        if not self._feral:
            return
        now = time.time()
        cutoff = now - 30.0
        self._feral_lost_ts = [t for t in self._feral_lost_ts if t >= cutoff]
        if len(self._feral_lost_ts) < 3:
            self._feral = False

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
        elif c == Cmd.SET_ENGINE_MODE:
            self._handle_set_engine_mode(p)
        elif c == Cmd.SET_CADENCE:
            self._handle_set_cadence(p)
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
        elif c == Cmd.PROCEED:
            self._handle_proceed()

    # ------------------------------------------------------------------
    # Engine move mode + cadence.
    # ------------------------------------------------------------------
    def _handle_set_engine_mode(self, p: dict) -> None:
        """Set the engine move mode: ``"block"`` (wait per move) | ``"stream"`` (retarget)."""
        mode = p.get("mode")
        if mode not in ("block", "stream"):
            print(f"[brain] invalid engine mode: {mode}")
            return
        self.machine.move_mode = mode
        print(f"[brain] engine mode: {mode}")

    def _handle_set_cadence(self, p: dict) -> None:
        """Set the cadence: ``"fixed"`` (every N s) | ``"arrival"`` (when the move ends)."""
        mode = p.get("mode", "fixed")
        if mode not in ("fixed", "arrival"):
            print(f"[brain] invalid cadence mode: {mode}")
            return
        self.machine.cadence = mode
        if mode == "fixed":
            # ``every`` is in seconds; convert to ticks (min 1 tick).
            try:
                every_s = float(p.get("every", 1.0))
            except (TypeError, ValueError):
                every_s = 1.0
            self.machine.cadence_every = max(1, int(every_s * self.machine.tick_hz))
        print(f"[brain] cadence: {mode} every={self.machine.cadence_every} ticks")

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

        The lock heuristic: the highest-confidence candidate above ``min_conf``
        (the hunt sensitivity threshold), breaking ties by centrality (smaller
        distance from image center wins). Candidates below ``min_conf`` are
        ignored, so a low threshold ("very sensitive") locks even weak detections.
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
        min_conf = self._hunt_group().get("params", {}).get("min_conf", 0.1)
        best = None
        best_score = -1
        for c in cands:
            conf = c.get("conf", 0)
            if conf < min_conf:
                continue  # below the sensitivity threshold: not a valid lock target
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
            ff = self._facefocus_group()
            ff_enabled = bool(ff.get("enabled", False))
            if self.camera is not None and lock_id is not None:
                if ff_enabled:
                    # Facefocus: the close camera does the face analysis (it also sends
                    # track for the locked person, so the hunt lost-check still works).
                    self.camera.set_intent(active="close", mode="analyze", lock_id=lock_id)
                else:
                    self.camera.set_intent(lock_id=lock_id, mode="track")
                if lock_id != self._hunt_lock_id:
                    # New lock: the new target's CAM_TRACK lags ~1 frame, so seed the
                    # lost timer now (a stale timestamp would cause a false "lost").
                    self._hunt_last_track_ts = time.time()
                    self._hunt_lock_id = lock_id
                # Camera event: trigger autonomy advance if enabled.
                if ff_enabled and self._autonomy_cfg().get("events", {}).get("camera", False):
                    self._autonomy_triggered = True
                # Attention grab: pull the robot into the hunt unless it is already
                # tracking/looking, already facefocusing, or a zone transition is in
                # progress.
                hunt = self._hunt_group()
                hp = hunt.get("params", {})
                guard = tuple(hp.get("attention_guard", []))
                detect = hp.get("detect", {})
                if (self._hunt_state != "facefocus"
                        and self.machine.behavior not in guard
                        and not self.machine.transitioning
                        and hunt.get("enabled", False)):
                    self.machine.trigger_action(detect.get("zone"), detect.get("action"))
                # Facefocus keeps the facefocus state; otherwise the lock means tracking.
                if self._hunt_state != "facefocus":
                    self._hunt_state = "tracking"
                self._update_hunt_state()

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
        """Store face analysis telemetry and drive the facefocus decision.

        The face offset (the face bbox centroid minus the image center, in pixels) is
        stashed on ``machine.face_state``; the ``face`` behaviour reads it each tick.
        When a face bbox is present and facefocus is enabled, the robot stretches into
        the exterior pose and cycles the face actions (wink / inspect / call).
        """
        if self.camera is not None:
            self.camera.handle_telemetry(Cmd.CAM_FACE, p)
        bbox = p.get("bbox")
        if bbox is None:
            return
        # Store the face offset on the machine (the `face` behaviour reads it).
        self.machine.face_state = {
            "dx": p.get("dx", 0.0),
            "dy": p.get("dy", 0.0),
            "id": p.get("id"),
            "ok": True,
        }
        self._facefocus_last_face_ts = time.time()
        self._cam_log("face", f"face: id={p.get('id')} bbox={bbox}")
        ff = self._facefocus_group()
        if not bool(ff.get("enabled", False)):
            return
        fp = ff.get("params", {})
        actions = fp.get("actions", [])
        zones = ff.get("zones", [])
        zone = zones[0] if zones else None
        if self._hunt_state != "facefocus":
            # Enter facefocus: stretch and run the first face action.
            first = actions[0] if actions else None
            if zone and first:
                self.machine.trigger_action(zone, first)
            self._hunt_state = "facefocus"
            self._facefocus_action_index = 0
            self._facefocus_last_action_ts = time.time()
            self._update_hunt_state()
        else:
            # Cycle the face actions on the timer (in-zone, no transition).
            if (actions and not self.machine.transitioning
                    and time.time() - self._facefocus_last_action_ts >= fp.get("action_s", 3.0)):
                self._facefocus_action_index = (self._facefocus_action_index + 1) % len(actions)
                self.machine.play_action(actions[self._facefocus_action_index])
                self._facefocus_last_action_ts = time.time()

    # ------------------------------------------------------------------
    # Per-tick hunt bookkeeping.
    # ------------------------------------------------------------------
    def _sync_camera(self) -> None:
        """Force the camera to close/analyze while a face action runs.

        A face action (behaviour ``"face"``) needs the close camera's face telemetry
        to move (the ``face`` behaviour reads ``machine.face_state``). This covers
        manually triggered face actions, where no CAM_FACE-driven lock exists to set
        the intent. The effective behaviour is the current action's, or the
        transition target's while a transition is in progress. Leaving a face action
        does not force wide — the hunt handlers keep camera ownership (tracking →
        wide/track on face-lost, wide/idle on person-lost).
        """
        if self.camera is None:
            return
        ff = self._facefocus_group()
        if not bool(ff.get("enabled", False)):
            return
        if self.machine.effective_behavior == "face":
            self.camera.set_intent(active="close", mode="analyze",
                                   lock_id=self._hunt_lock_id)

    def tick(self, curjpos: List[float]) -> None:
        """Detect a lost lock / lost face and rescan (the hunt's time-driven step).

        Called by the engine after the command drain and before ``machine.step``, so a
        triggered transition is driven the same tick. The ``tracking`` and ``facefocus``
        states arm the lost checks; ``scanning`` waits for the next
        ``CAM_CANDIDATES`` re-detect.

         - Person lost (the locked person's ``CAM_TRACK`` is stale): clear the lock and
           rescan (wide/idle, the scan action).
         - Face lost (facefocus only; the face is stale): back to tracking (wide/track,
           the detect action). The person is still locked, so the face can reappear.
        """
        self._sync_camera()
        hunt = self._hunt_group()
        hunt_enabled = bool(hunt.get("enabled", False))
        if self._hunt_state not in ("tracking", "facefocus") or not hunt_enabled:
            self._clear_feral_if_stale()
            self._update_hunt_state()
        else:
            hp = hunt.get("params", {})
            lost_s = hp.get("lost_s", 2.0)
            if time.time() - self._hunt_last_track_ts > lost_s:
                # Person lost: clear the lock and rescan.
                if self.camera is not None:
                    self.camera.set_intent(active="wide", mode="idle", clear_lock=True)
                self._hunt_lock_id = None
                self.machine.face_state = {}
                scan = hp.get("scan", {})
                self.machine.trigger_action(scan.get("zone"), scan.get("action"))
                self._hunt_state = "scanning"
                self._record_lost()
                self._update_hunt_state()
                return
            self._clear_feral_if_stale()
            if self._hunt_state == "facefocus":
                ff = self._facefocus_group()
                fp = ff.get("params", {})
                face_lost_s = fp.get("face_lost_s", 2.5)
                if time.time() - self._facefocus_last_face_ts > face_lost_s:
                    # Face lost: clear the face state, back to tracking (the person is
                    # still locked; the detect action and the wide camera wait for the
                    # face to reappear).
                    self.machine.face_state = {}
                    if self.camera is not None:
                        self.camera.set_intent(active="wide", mode="track",
                                               lock_id=self._hunt_lock_id)
                    detect = hp.get("detect", {})
                    self.machine.trigger_action(detect.get("zone"), detect.get("action"))
                    self._hunt_state = "tracking"
                    self._update_hunt_state()
        # Autonomy navigation: follow the current action's next link when dwell
        # expires or an event fires.
        self._autonomy_tick(curjpos)

    # ------------------------------------------------------------------
    # Autonomy navigation (follow next links across the enabled zone union).
    # ------------------------------------------------------------------
    def _autonomy_dwell_s(self, zone: str, action: str) -> float:
        """The dwell time for an action: per-action override or the global default."""
        z = self.machine.zones.get(zone)
        if z is not None:
            d = z.action_dwell_s(action)
            if d is not None:
                return d
        return self._autonomy_cfg().get("dwell_s", 30.0)

    def _autonomy_follow_next(self, zone: str, action: str) -> None:
        """Follow the current action's ``next`` link if its target is in the enabled union.

        The brain can ONLY follow existing action ``next`` links from the flow editor.
        No manual override, no arbitrary zone jumps. If the target zone is not in the
        enabled union, consume the trigger and stay (the action keeps looping).
        """
        z = self.machine.zones.get(zone)
        if z is None:
            return
        n = z.action_next(action)
        if n is None:
            return
        target_zone = n.get("zone")
        target_action = n.get("action")
        if not target_zone or not target_action:
            return
        # Gate: the target zone must be in the union of enabled groups' zones.
        if target_zone not in self._enabled_zones:
            return
        # Follow the link: same-zone uses play_action, cross-zone uses trigger_action.
        if target_zone == zone:
            self.machine.play_action(target_action)
        else:
            self.machine.trigger_action(target_zone, target_action)

    def _autonomy_tick(self, curjpos: List[float]) -> None:
        """Per-tick autonomy decision: advance to the next link when dwell expires.

        Called at the end of :meth:`tick`, after hunt bookkeeping. Detects a
        (zone, action) change to reset the dwell timer, then checks whether the
        current action should advance (dwell expired or event triggered).
        """
        cur = (self.machine.current_zone, self.machine.current_action)
        if cur != self._autonomy_prev:
            # New (zone, action): reset the dwell timer.
            self._autonomy_prev = cur
            self._autonomy_action_start_ts = time.time()
            self._autonomy_triggered = False
        if not self._autonomy_cfg().get("enabled", False):
            return
        if self.machine.transitioning:
            return
        zone, action = cur
        if not zone or not action:
            return
        # The current action must loop for autonomy to consider advancing (a
        # non-looping action has no "next" to follow in the same zone).
        z = self.machine.zones.get(zone)
        if z is None or not z.action_loops(action):
            return
        dwell = self._autonomy_dwell_s(zone, action)
        now = time.time()
        if self._autonomy_triggered or (now - self._autonomy_action_start_ts >= dwell):
            self._autonomy_follow_next(zone, action)
            self._autonomy_triggered = False

    # ------------------------------------------------------------------
    # External event handlers (autonomy triggers).
    # ------------------------------------------------------------------
    def _handle_midi_trigger(self, note: int) -> None:
        """MIDI note-on event: trigger an immediate next-link advance.

        Called by the MIDI adapter on note-on. Sets ``_autonomy_triggered`` so the
        next :meth:`_autonomy_tick` follows the current action's next link.
        """
        if self._autonomy_cfg().get("events", {}).get("midi", False):
            self._autonomy_triggered = True

    def _handle_proceed(self) -> None:
        """WebSocket PROCEED event: trigger an immediate next-link advance."""
        if self._autonomy_cfg().get("events", {}).get("websocket", False):
            self._autonomy_triggered = True
