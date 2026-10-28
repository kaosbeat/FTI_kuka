"""Brain: the command interpreter.

The state machine is the single owner of the robot's state (zone, action, target,
speed, cadence, camera telemetry). The brain no longer holds any of that — it
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

import random
import time
from typing import List, Optional, Tuple

from ..core.commands import Cmd, Command
from ..flow import find_safe_path, find_safe_path_to_any
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
        self.current_class = "scan"  # engagement class (one of ZONE_CLASSES)
        # Standing-still check: a short window of the locked person's bbox.
        self._still_window: List[Tuple[float, float, float, float]] = []
        self._still_window_size = 5
        self._still_threshold = 10.0
        # Retreat timing (facefocus -> retreat -> scan).
        self._retreat_start_ts = 0.0
        self._hunt_lock_id = None
        self._hunt_last_track_ts = 0.0
        # Lock-state tracking for the per-behavior trigger system: ``_prev_lock_id``
        # is the previous tick's lock id (edge detection for new_lock/lost_lock) and
        # ``_lock_start_ts`` is when the current lock was (re)acquired (the
        # lock_duration timer). Updated at the end of every autonomy tick.
        self._prev_lock_id = None
        self._lock_start_ts = 0.0
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
        self._cam_action = None  # last (zone, action) the camera intent was applied to
        self._enabled_groups: set = set()
        self._enabled_zones: set = set()
        # Blocked candidates: (zone, action) pairs discarded by the safety gate in
        # auto mode. The brain skips these on the next autonomy tick. Cleared when
        # the current (zone, action) changes or a candidate commits.
        self._blocked_candidates: set = set()
        # Stuck watchdog: counts consecutive autonomy ticks where the robot is in a
        # (zone, None) state (a zone is set but no action is active, no pending
        # move, no transition). Used to re-arm a stuck robot and, as a backstop,
        # to clear the blocked-candidate set once re-arming keeps failing.
        self._stuck_ticks: int = 0
        # Runtime group navigation: a single active group at a time. ``active_group``
        # is the enabled group name (None when no group is active); ``group_nav``
        # holds the (zone, action) waypoints to walk (the start is stripped) and
        # ``group_nav_index`` the next waypoint to trigger. Once the robot has
        # walked all waypoints, ``group_nav`` is cleared and autonomy resumes,
        # restricted to the active group's zones.
        self.active_group: Optional[str] = None
        self.group_nav: List[tuple] = []
        self.group_nav_index: int = 0
        # Assertive mode navigation: the brain drives the robot to random safe
        # destinations. ``assertive_nav`` holds the (zone, action) waypoints to walk
        # (the start is stripped); ``assertive_nav_index`` the next waypoint to
        # trigger. ``assertive_prev`` is the last seen (zone, action) (resets the
        # dwell timer on a state change); ``assertive_arrived_ts`` stamps when the
        # robot reached the current node (the dwell timer for the next reselect).
        # See the ``assertive`` config section.
        self.assertive_nav: List[tuple] = []
        self.assertive_nav_index: int = 0
        self.assertive_prev: Optional[tuple] = None
        self.assertive_arrived_ts: float = 0.0
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
        # The behavior policies read these off the machine (behavior.py stays pure);
        # a config without a ``behaviors`` section leaves the machine's defaults.
        if self.machine is not None:
            self.machine.behavior_params = cfg.get("behaviors", {})
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
        # Zone-class cross-reference: each zone's baseline class must be a valid
        # zone_classes key (warn, not reject, so a bad edit never kills the core).
        zone_classes = cfg.get("zone_classes", {})
        if self.machine:
            for zn in self.machine.zones.names():
                z = self.machine.zones.get(zn)
                if z is not None and z.cls not in zone_classes:
                    print(f"[brain] WARNING: zone {zn!r} has invalid class {z.cls!r} (not in zone_classes)")
        hunt = cfg.get("hunt", {})
        hp = hunt.get("params", {})
        print(f"[brain] config reloaded: hunt.enabled={hunt.get('enabled')} "
              f"lost_s={hp.get('lost_s')} "
              f"detect={hp.get('detect', {}).get('zone')}/{hp.get('detect', {}).get('action')} "
              f"scan={hp.get('scan', {}).get('zone')}/{hp.get('scan', {}).get('action')}")
        ff = cfg.get("facefocus", {})
        fp = ff.get("params", {})
        print(f"[brain] facefocus: enabled={ff.get('enabled')} zones={ff.get('zones')} "
              f"actions={fp.get('actions')} action_s={fp.get('action_s')} "
              f"face_lost_s={fp.get('face_lost_s')}")

    def _autonomy_cfg(self) -> dict:
        """The autonomy master config (``{enabled, dwell_s, events}``)."""
        return self._config.get("autonomy", {})

    def _assertive_cfg(self) -> dict:
        """The assertive-mode config (``{enabled, dwell_s, speed_min, speed_max, wander, track, pool}``)."""
        return self._config.get("assertive", {})

    def _hunt_group(self) -> dict:
        """The hunt config (``{zones, enabled, params}``) — a top-level section, not a zone group."""
        return self._config.get("hunt", {})

    def _facefocus_group(self) -> dict:
        """The facefocus config (``{zones, enabled, params}``) — a top-level section, not a zone group."""
        return self._config.get("facefocus", {})

    def _cam_control(self) -> dict:
        """The camera-control map (``"zone/action"`` -> ``{active, mode, lock}``)."""
        return self._config.get("cam_control", {})

    def _apply_action_cam(self, zone: str, action: str) -> None:
        """Apply the action's camera intent (the ``cam_control["zone/action"]`` entry).

        Called on every action change (regardless of autonomy): the action's camera
        baseline is set here, and the hunt/facefocus logic overrides it while a
        person is actively tracked. ``lock: "auto"`` resolves to the current hunt
        lock (or None); ``"none"`` clears any lock; a specific id locks it.
        """
        if self.camera is None or not zone or not action:
            return
        entry = self._cam_control().get(f"{zone}/{action}")
        if not isinstance(entry, dict):
            return
        active = entry.get("active")
        mode = entry.get("mode")
        lock = entry.get("lock")
        if lock == "auto":
            self.camera.set_intent(active=active, mode=mode, lock_id=self._hunt_lock_id)
        elif lock == "none":
            self.camera.set_intent(active=active, mode=mode, clear_lock=True)
        else:
            self.camera.set_intent(active=active, mode=mode, lock_id=lock)

    def _apply_action_engine(self, zone: str, action: str) -> None:
        """Apply the action's engine config (``zones[zone].actions[action]["engine"]``).

        Called on every action change, alongside :meth:`_apply_action_cam`. An action
        may declare an ``engine`` field (``{cadence, move_mode}``) to switch the
        machine's movement cadence and retargeting mode for that action.

        The hunting ``track`` action uses this to run the recentering loop
        responsively: ``cadence: "arrival"`` advances the anchor only when the move
        ends (so the servo keeps retargeting the locked person until centered) and
        ``move_mode: "stream"`` retargets every tick instead of blocking per move.
        Actions without an ``engine`` field leave the machine settings untouched
        (they inherit whatever the previous action set).
        """
        z = self.machine.zones.get(zone)
        if z is None:
            return
        a = z.actions().get(action)
        if not isinstance(a, dict):
            return
        eng = a.get("engine")
        if not isinstance(eng, dict):
            return
        cadence = eng.get("cadence")
        if cadence in ("fixed", "arrival", "midi"):
            self.machine.cadence = cadence
            if cadence == "fixed":
                try:
                    every_s = float(eng.get("every", 1.0))
                except (TypeError, ValueError):
                    every_s = 1.0
                self.machine.cadence_every = max(1, int(every_s * self.machine.tick_hz))
        mode = eng.get("move_mode")
        if mode in ("block", "stream"):
            self.machine.move_mode = mode

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
            "state": self.current_class,
            "lock_id": self._hunt_lock_id,
            "feral": self._feral,
        }

    def autonomy_info(self) -> dict:
        """The autonomy state for the state frame (the display/editor reads it live).

        Returns a dict with:
        - ``enabled``: bool, whether autonomy is on.
        - ``enabled_groups``: sorted list of enabled group names.
        - ``performance_mode``: bool, derived — the ``stage-takeover`` group is
          enabled while the ``event-sit-back`` group is disabled.
        - ``dwell_remaining``: float seconds remaining for the current action's
          dwell, or None when autonomy is off / no active action.
        """
        cfg = self._autonomy_cfg()
        enabled = bool(cfg.get("enabled", False))
        groups = sorted(self._enabled_groups)
        # Performance mode: the stage-takeover group is enabled while the
        # event-sit-back group is disabled (the robot "takes over the stage").
        zgs = self._config.get("zone_groups", {})
        stage = zgs.get("stage-takeover", {})
        event = zgs.get("event-sit-back", {})
        perf = bool(stage.get("enabled", False)) and not bool(event.get("enabled", False))
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
            "active_group": self.active_group,
            "performance_mode": perf,
            "dwell_remaining": dwell_remaining,
        }

    def _update_hunt_state(self) -> None:
        """Write the current hunt state to the machine (the display reads it)."""
        self.machine.hunt_state = {
            "state": self.current_class,
            "lock_id": self._hunt_lock_id,
            "feral": self._feral,
        }

    def _class_camera_preset(self, cls: str) -> dict:
        """The camera preset for an engagement class (from ``zone_classes`` config)."""
        zc = self._config.get("zone_classes", {})
        entry = zc.get(cls, {})
        return entry.get("camera", {"active": "wide", "mode": "idle", "lock": "none"})

    def _set_class(self, cls: str) -> None:
        """Set the current engagement class and apply its camera preset."""
        self.current_class = cls
        preset = self._class_camera_preset(cls)
        if self.camera is not None:
            active = preset.get("active")
            mode = preset.get("mode")
            lock = preset.get("lock")
            if lock == "auto":
                self.camera.set_intent(active=active, mode=mode, lock_id=self._hunt_lock_id)
            elif lock == "none":
                self.camera.set_intent(active=active, mode=mode, clear_lock=True)
            else:
                self.camera.set_intent(active=active, mode=mode, lock_id=lock)
        if cls == "track":
            self._still_window.clear()
        if cls == "retreat":
            self._retreat_start_ts = time.time()
        self._update_hunt_state()

    def _is_still(self) -> bool:
        """Check if the locked person is standing still (bbox stability)."""
        if len(self._still_window) < 2:
            return False
        first = self._still_window[0]
        last = self._still_window[-1]
        return (abs(last[0] - first[0]) < self._still_threshold
                and abs(last[1] - first[1]) < self._still_threshold
                and abs(last[2] - first[2]) < self._still_threshold
                and abs(last[3] - first[3]) < self._still_threshold)

    def _far_close_gate(self) -> bool:
        """The far->close gate: visible in close cam + standing still + looking."""
        if self._hunt_lock_id is None:
            return False
        fs = self.machine.face_state
        if not fs or fs.get("id") != self._hunt_lock_id:
            return False
        if not self._is_still():
            return False
        features = fs.get("features", {})
        return bool(features.get("looking", False))

    def _is_close_enough(self) -> bool:
        """Check if the person is close enough for facefocus (``engage_dist``)."""
        zc = self._config.get("zone_classes", {})
        engage_dist = zc.get("approach", {}).get("params", {}).get("engage_dist", 250.0)
        cs = self.machine.camera_state
        if not cs:
            return False
        w = cs.get("w", 0.0)
        h = cs.get("h", 0.0)
        return (w + h) / 2.0 > engage_dist

    def _hunt_move_safe(self, zone: str, action: str) -> bool:
        """Whether a hunt move to ``(zone, action)`` is conflict-free from the current
        position.

        The hunt state (scan/track/...) always runs on the camera, but the *physical*
        move to the hunt zone only happens when the safety gate would allow it. This
        decouples the hunt state from the physical zone: when the current zone's
        safezone does not overlap the hunt zone's safezone (or the actions share no
        pose), the robot stays where it is and runs the hunt state in place, instead of
        requesting a move the safety gate would discard (the repeated "red-link
        conflict" warning). The check is driven by the live safezone data, so it
        applies to whatever zone the robot currently occupies and whatever zone the
        hunt params name — not just a fixed pair.

        Returns ``True`` when the move is safe, or when no safety gate is wired (the
        move is not conflict-checked, so it is allowed). Returns ``False`` when
        ``zone``/``action`` is empty, or when the safety gate reports a conflict.
        """
        if not zone or not action:
            return False
        safety = self.machine.safety
        if safety is None:
            return True
        # Pass the live pose so ``"x"`` (frozen / copy-from-previous) axes in the
        # connection poses resolve to the robot's current position (the concrete
        # value the robot will hold).
        conflict, _issues = safety.check_conflict(
            self.machine.current_zone, self.machine.current_action, zone, action,
            self.machine._last_curjpos)
        return not conflict

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
    # Commands allowed during cam test mode (all others are rejected).
    _TEST_ALLOWED_CMDS = frozenset({
        Cmd.CAM_TEST_START, Cmd.CAM_TEST_STOP,
        Cmd.CAM_STATUS, Cmd.CAM_CANDIDATES, Cmd.CAM_TRACK,
        Cmd.CAM_FACE, Cmd.CAM_FORCE_LOCK, Cmd.STOP,
    })

    def on_command(self, cmd: Command, curjpos: List[float]) -> None:
        """Fold one command into the state machine."""
        c = cmd.cmd
        p = cmd.payload
        # Cam test mode: reject all commands except the allowed set.
        if self.machine.test_mode and c not in self._TEST_ALLOWED_CMDS:
            print(f"[brain] cam test mode: rejecting command {c.value}")
            return
        # [debug] log every command that can clear the current action, with the
        # robot pose at the moment it arrives (to pin down what clears hold).
        if c in (Cmd.STOP, Cmd.SET_CONTROL_MODE, Cmd.CLEAR_ACTION, Cmd.SET_JOINT_POSE,
                 Cmd.PLAY_ACTION, Cmd.TRIGGER_ACTION):
            print(f"[debug-cmd] {c.value if hasattr(c, 'value') else c} payload={p} "
                  f"curjpos={[round(v, 1) for v in curjpos]} "
                  f"action={self.machine.current_action} zone={self.machine.current_zone} "
                  f"mode={self.machine.control_mode}")
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
        elif c == Cmd.SET_CONTROL_MODE:
            # Manual mode disables all groups (the operator drives the robot);
            # clear the active group before the machine freezes the pose.
            if p.get("mode") == "manual" and self.active_group is not None:
                self.clear_group()
            # Assertive mode is independent of groups: entering it clears the active
            # group; leaving it clears the assertive navigation state (the machine
            # drops the per-move speed when it exits assertive mode).
            if p.get("mode") == "assertive" and self.active_group is not None:
                self.clear_group()
            if self.machine.control_mode == "assertive" and p.get("mode") != "assertive":
                self.assertive_nav = []
                self.assertive_nav_index = 0
                self.assertive_prev = None
                self.assertive_arrived_ts = 0.0
            # Pass the current joint position so entering manual mode holds the
            # robot where it is (a freeze), not just stops new autonomous triggers.
            self.machine.set_control_mode(p.get("mode"), curjpos)
        elif c == Cmd.ENABLE_GROUP:
            self._handle_enable_group(p.get("group"), curjpos)
        elif c == Cmd.CLEAR_GROUP:
            self.clear_group()
        elif c == Cmd.ENABLE_BEHAVIOR:
            self.machine.enable_behavior(p.get("behavior"))
        elif c == Cmd.CLEAR_BEHAVIOR:
            self.machine.clear_behavior()
        elif c == Cmd.CAM_STATUS:
            self._handle_cam_status(p)
        elif c == Cmd.CAM_CANDIDATES:
            self._handle_cam_candidates(p)
        elif c == Cmd.CAM_TRACK:
            self._handle_cam_track(p)
        elif c == Cmd.CAM_FACE:
            self._handle_cam_face(p)
        elif c == Cmd.CAM_FORCE_LOCK:
            self._handle_cam_force_lock(p)
        elif c == Cmd.PROCEED:
            self._handle_proceed()
        elif c == Cmd.SAFETY_CONFIRM:
            self._handle_safety_confirm()
        elif c == Cmd.SAFETY_CANCEL:
            self._handle_safety_cancel()
        elif c == Cmd.CAM_TEST_START:
            self._handle_cam_test_start(p, curjpos)
        elif c == Cmd.CAM_TEST_STOP:
            self._handle_cam_test_stop()

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
        """Set the cadence: ``"fixed"`` (every N s) | ``"arrival"`` (when the move
        ends) | ``"midi"`` (on the learned midi note)."""
        mode = p.get("mode", "fixed")
        if mode not in ("fixed", "arrival", "midi"):
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
        params = self._hunt_group().get("params", {})
        min_conf = params.get("min_conf", 0.1)
        centrality_weight = params.get("centrality_weight", 0.1)
        # Frame dims (pixels). Present from the real pipeline and the pixel mock;
        # absent from a legacy normalized mock (then coords are already 0..1).
        fw = p.get("w")
        fh = p.get("h")
        best = None
        best_score = -1
        for c in cands:
            conf = c.get("conf", 0)
            # A camera candidate may carry ``conf`` as a string (pipeline
            # serialization); comparing it to the numeric ``min_conf`` would raise
            # TypeError every tick and kill the engine. Coerce to a number; a
            # non-numeric value falls back to 0.0 (below threshold, so skipped).
            try:
                conf = float(conf)
            except (TypeError, ValueError):
                conf = 0.0
            if conf < min_conf:
                continue  # below the sensitivity threshold: not a valid lock target
            # Centrality: smaller distance from (0.5, 0.5) is better. Normalize the
            # pixel coords to 0..1 first (the score is defined in normalized space);
            # without w/h the coords are assumed already normalized.
            cx = (c.get("x1", 0) + c.get("x2", 0)) / 2
            cy = (c.get("y1", 0) + c.get("y2", 0)) / 2
            if fw and fh:
                cx /= fw
                cy /= fh
            dist = (cx - 0.5) ** 2 + (cy - 0.5) ** 2
            score = conf - dist * centrality_weight  # confidence dominates, centrality breaks ties
            if score > best_score:
                best_score = score
                best = c
        if best is not None:
            self._apply_lock(best.get("id"))

    def _apply_lock(self, lock_id) -> None:
        """Apply a lock to candidate ``lock_id`` (camera intent + class escalation).

        Shared by the automatic heuristic (:meth:`_handle_cam_candidates`) and the
        manual debug lock (:meth:`_handle_cam_force_lock`). On a new lock, resets the
        class to ``track`` (the tick loop escalates from there). Triggers the
        attention grab unless already in an active tracking class.
        """
        if self.camera is not None and lock_id is not None:
            if lock_id != self._hunt_lock_id:
                # New lock: seed the lost timer (the new target's CAM_TRACK lags
                # ~1 frame) and reset to the tracking class.
                self._hunt_last_track_ts = time.time()
                self._hunt_lock_id = lock_id
                self._set_class("track")
            if self._autonomy_cfg().get("events", {}).get("camera", False):
                self._autonomy_triggered = True
            # Attention grab: pull the robot into the hunt unless it is already
            # in an active tracking class, or a zone transition is in progress.
            hunt = self._hunt_group()
            hp = hunt.get("params", {})
            guard = tuple(hp.get("attention_guard", []))
            detect = hp.get("detect", {})
            if (self._hunt_active()
                    and self.current_class not in ("track", "farfocus", "approach", "facefocus")
                    and self.machine.behavior not in guard
                    and not self.machine.transitioning
                    and not self.machine.pending
                    and hunt.get("enabled", False)
                    and self._hunt_move_safe(detect.get("zone"), detect.get("action"))):
                self.machine.trigger_action(detect.get("zone"), detect.get("action"))

    def _handle_cam_force_lock(self, p: dict) -> None:
        """Manual lock (debug): force-lock a candidate id, bypassing ``min_conf``.

        Lets a debug client (the camera debug page) lock a specific person so the
        hunt/track/facefocus behaviour can be exercised without a real detection
        clearing the ``min_conf`` threshold.
        """
        lock_id = p.get("id")
        if lock_id is None:
            print("[brain] force_lock: no id; ignored")
            return
        self._cam_log("force_lock", f"force_lock: id={lock_id} (manual)")
        self._apply_lock(lock_id)

    def _handle_cam_track(self, p: dict) -> None:
        """Store the track telemetry on the machine; the behaviours read it.

        The brain no longer nudges the target pose directly. The camera offset
        (dx/dy) is folded into the variable-axis behaviour of whatever action is
        currently playing (track/focus/look read ``machine.camera_state`` each tick).
        Also appends to the stillness window for the locked id (the far→close gate
        checks bbox stability over a short window).
        """
        if self.camera is not None:
            self.camera.handle_telemetry(Cmd.CAM_TRACK, p)
        self._cam_log("track", f"track: id={p.get('id')} dx={p.get('dx', 0):.1f} dy={p.get('dy', 0):.1f}")
        # Refresh the hunt lost-timer only for the locked id (a stale track for a
        # different id — e.g. a lingering lock while scanning — must not count).
        if p.get("id") == self._hunt_lock_id:
            self._hunt_last_track_ts = time.time()
            # Append to the stillness window (bbox center offset + size).
            if self.current_class in ("track", "farfocus", "approach"):
                self._still_window.append((
                    p.get("dx", 0.0), p.get("dy", 0.0),
                    p.get("w", 0.0), p.get("h", 0.0),
                ))
                if len(self._still_window) > self._still_window_size:
                    self._still_window.pop(0)
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
        """Store face analysis telemetry on the machine.

        The face offset (the face bbox centroid minus the image center, in pixels) and
        the head-pose features (``{yaw, pitch, looking}``) are stashed on
        ``machine.face_state``; the ``face`` behaviour and the far→close gate read
        them each tick. Face actions are cycled only when the current class is
        ``facefocus`` (the tick loop drives the escalation to that class).
        """
        if self.camera is not None:
            self.camera.handle_telemetry(Cmd.CAM_FACE, p)
        bbox = p.get("bbox")
        if bbox is None:
            return
        self.machine.face_state = {
            "dx": p.get("dx", 0.0),
            "dy": p.get("dy", 0.0),
            "id": p.get("id"),
            "features": p.get("features", {}),
            "ok": True,
        }
        self._facefocus_last_face_ts = time.time()
        self._cam_log("face", f"face: id={p.get('id')} bbox={bbox}")
        if self.machine.control_mode == "manual":
            return
        # Cycle the face actions on the timer (only in the facefocus class).
        if self.current_class == "facefocus":
            zc = self._config.get("zone_classes", {})
            fp = zc.get("facefocus", {}).get("params", {})
            actions = fp.get("actions", [])
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
        does not force wide — the class handlers keep camera ownership (approach →
        wide/track on face-lost, wide/idle on person-lost).
        """
        if self.camera is None:
            return
        if self.current_class == "facefocus":
            if self.machine.effective_behavior == "gentle":
                self.camera.set_intent(active="close", mode="analyze",
                                       lock_id=self._hunt_lock_id)

    def tick(self, curjpos: List[float]) -> None:
        """Per-tick class transition logic (the engagement ladder).

        Called by the engine after the command drain and before ``machine.step``.
        Drives the escalation/de-escalation of the current class:

        - **any → scan**: person lost (the locked person's ``CAM_TRACK`` is stale).
        - **track → farfocus**: the locked person is at far distance (wide cam engaged).
        - **farfocus → approach**: the far→close gate passes (visible in close +
          standing still + looking at the robot).
        - **approach → facefocus**: close enough (``engage_dist``).
        - **facefocus → retreat**: the face is stale (the person turns away / leaves).
        - **retreat → scan**: the break-off time expires.
        """
        self._sync_camera()
        # Cam test mode: the brain drives the scripted navigation/behavior.
        if self.machine.test_mode:
            self._test_tick(curjpos)
            self._update_hunt_state()
            return
        if self.machine.control_mode == "manual":
            self._clear_feral_if_stale()
            self._update_hunt_state()
            return
        # Assertive mode: the brain drives the robot to random safe destinations
        # (see :meth:`_assertive_tick`). Person tracking (the hunt ladder) runs
        # only when ``assertive.track`` is enabled.
        if self.machine.control_mode == "assertive":
            self._assertive_tick(curjpos)
            if self._hunt_active():
                self._hunt_tick(curjpos, navigate=False)
            self._clear_feral_if_stale()
            self._update_hunt_state()
            return
        # Auto mode: group navigation, then the hunt ladder, then autonomy.
        # Group navigation: while an active group is still navigating, drive the
        # path and suppress the regular hunt/autonomy (the robot is en route).
        if self.active_group is not None:
            if self._group_tick(curjpos):
                self._clear_feral_if_stale()
                self._update_hunt_state()
                return
            # Arrived: continue with the regular logic (autonomy now restricted
            # to the group's zones via _allowed_zones).
        self._hunt_tick(curjpos, navigate=True)

    # ------------------------------------------------------------------
    # Hunt ladder (person-lost detection + class escalation), shared by auto
    # and assertive modes.
    # ------------------------------------------------------------------
    def _hunt_tick(self, curjpos: List[float], navigate: bool = True) -> None:
        """Per-tick hunt ladder: person-lost detection + class escalation.

        Extracted from :meth:`tick` so assertive mode can reuse the person-tracking
        loop when ``assertive.track`` is enabled. ``navigate`` controls whether the
        autonomy next-link follower runs at the end (auto mode); assertive mode
        passes False (it has its own destination driver).
        """
        hunt = self._hunt_group()
        hunt_enabled = bool(hunt.get("enabled", False))
        if not hunt_enabled:
            if navigate:
                self._autonomy_tick(curjpos)
            return
        hp = hunt.get("params", {})
        lost_s = hp.get("lost_s", 2.0)
        # Person lost: any class → scan.
        if self._hunt_lock_id is not None and time.time() - self._hunt_last_track_ts > lost_s:
            self._hunt_lock_id = None
            self.machine.face_state = {}
            scan = hp.get("scan", {})
            # Move to the hunt scan zone only when the safety gate allows it; when
            # the current zone's safezone does not overlap the hunt zone's, scan in
            # place (the hunt state runs in the current zone, no move requested).
            if self._hunt_move_safe(scan.get("zone"), scan.get("action")):
                self.machine.trigger_action(scan.get("zone"), scan.get("action"))
            self._set_class("scan")
            self._record_lost()
            if navigate:
                self._autonomy_tick(curjpos)
            return
        self._clear_feral_if_stale()
        # Escalation / de-escalation based on the current class.
        if self.current_class == "track":
            # track → farfocus: the person is at far distance (wide cam engaged).
            self._set_class("farfocus")
        elif self.current_class == "farfocus":
            # farfocus → approach: the far→close gate (visible + still + looking).
            if self._far_close_gate():
                self._set_class("approach")
        elif self.current_class == "approach":
            # approach → facefocus: close enough (engage_dist).
            if self._is_close_enough():
                self._set_class("facefocus")
        elif self.current_class == "facefocus":
            # facefocus → retreat: the face is stale (the person turns away / leaves).
            zc = self._config.get("zone_classes", {})
            fp = zc.get("facefocus", {}).get("params", {})
            face_lost_s = fp.get("face_lost_s", 2.5)
            if time.time() - self._facefocus_last_face_ts > face_lost_s:
                self.machine.face_state = {}
                self._set_class("retreat")
        elif self.current_class == "retreat":
            # retreat → scan: the break-off time expires.
            zc = self._config.get("zone_classes", {})
            rp = zc.get("retreat", {}).get("params", {})
            break_off_s = rp.get("break_off_s", 3.0)
            if time.time() - self._retreat_start_ts > break_off_s:
                self._set_class("scan")
        self._update_hunt_state()
        # Autonomy navigation: follow the current action's next link when dwell
        # expires or an event fires (auto mode only).
        if navigate:
            self._autonomy_tick(curjpos)

    # ------------------------------------------------------------------
    # Autonomy navigation (follow next links across the enabled zone union).
    # ------------------------------------------------------------------
    def _autonomy_dwell_s(self, zone: str, action: str) -> float:
        """The dwell time for an action: per-action override, the zone's mode-group
        ``params.dwell_s``, or the global ``autonomy.dwell_s`` default."""
        z = self.machine.zones.get(zone)
        if z is not None:
            d = z.action_dwell_s(action)
            if d is not None:
                return d
            mg = z.mode_group
            if mg:
                g = self._config.get("zone_groups", {}).get(mg, {})
                if isinstance(g, dict) and isinstance(g.get("params"), dict):
                    pd = g["params"].get("dwell_s")
                    if isinstance(pd, (int, float)) and not isinstance(pd, bool):
                        return float(pd)
        return self._autonomy_cfg().get("dwell_s", 30.0)

    def _behavior_params(self, behavior: str) -> dict:
        """The per-behavior params (``stay``/``triggers``/``dwell_s``/``lock_duration_s``)
        off the machine's ``behavior_params``, else ``{}``."""
        params = getattr(self.machine, "behavior_params", None) or {}
        entry = params.get(behavior)
        return entry if isinstance(entry, dict) else {}

    def _valid_next_candidates(self, zone: str, action: str) -> list:
        """All valid ``(zone, action)`` candidates from the action's ``next`` list:
        the zone is in the enabled union, the action is named, exists, and is
        enabled. Skips candidates blocked by the safety gate (auto mode)."""
        z = self.machine.zones.get(zone)
        if z is None:
            return []
        cands = []
        allowed = self._allowed_zones()
        for n in z.action_next_list(action):
            tz = n.get("zone")
            ta = n.get("action")
            if not isinstance(tz, str) or tz not in allowed:
                continue
            if not isinstance(ta, str):
                continue
            tz_zone = self.machine.zones.get(tz)
            if tz_zone is None or not tz_zone.enabled:
                continue
            if ta not in tz_zone.actions() or not tz_zone.action_enabled(ta):
                continue
            if (tz, ta) in self._blocked_candidates:
                continue  # blocked by the safety gate on a previous tick
            cands.append((tz, ta))
        return cands

    def _random_valid_next(self, zone: str, action: str) -> Optional[tuple]:
        """A random valid ``(zone, action)`` candidate from the action's ``next`` list
        (replacing the old deterministic first-valid pick). Returns None when none."""
        cands = self._valid_next_candidates(zone, action)
        return random.choice(cands) if cands else None

    def _behavior_triggers_fired(self, zone: str, action: str, behavior: str, now: float) -> dict:
        """Which of the behavior's configured triggers fired this tick.

        Returns a dict of ``trigger -> bool`` for the behavior's configured
        ``triggers`` (default ``["dwell"]``). ``dwell`` fires when the action has run
        for >= its dwell timeout; ``new_lock`` / ``lost_lock`` are the lock-state
        edges (previous vs current lock id); ``lock_duration`` fires when the lock has
        been held for >= ``lock_duration_s``.
        """
        bp = self._behavior_params(behavior)
        triggers = bp.get("triggers")
        if not (isinstance(triggers, list) and triggers):
            triggers = ["dwell"]
        fired = {}
        for t in triggers:
            if t == "dwell":
                dwell_s = bp.get("dwell_s")
                if not (isinstance(dwell_s, (int, float)) and not isinstance(dwell_s, bool)):
                    dwell_s = self._autonomy_dwell_s(zone, action)
                fired["dwell"] = (now - self._autonomy_action_start_ts) >= dwell_s
            elif t == "new_lock":
                fired["new_lock"] = (self._prev_lock_id is None and self._hunt_lock_id is not None)
            elif t == "lost_lock":
                fired["lost_lock"] = (self._prev_lock_id is not None and self._hunt_lock_id is None)
            elif t == "lock_duration":
                ld = bp.get("lock_duration_s")
                if (self._hunt_lock_id is not None
                        and isinstance(ld, (int, float)) and not isinstance(ld, bool)):
                    fired["lock_duration"] = (now - self._lock_start_ts) >= ld
                else:
                    fired["lock_duration"] = False
        return fired

    def _rearm_stay(self) -> None:
        """Re-arm the timers so a stayed (looping) action re-evaluates on the next
        trigger instead of re-firing every tick."""
        self._autonomy_action_start_ts = time.time()
        if self._hunt_lock_id is not None:
            self._lock_start_ts = time.time()

    def _autonomy_advance(self, zone: str, action: str) -> bool:
        """Advance to a random valid next; no valid candidate -> stay (loop)."""
        target = self._random_valid_next(zone, action)
        if target is None:
            self._rearm_stay()
            return False
        tz, ta = target
        self._autonomy_go(zone, tz, ta)
        return True

    def _autonomy_decide(self, zone: str, action: str, behavior: str,
                          event: bool, fired: dict) -> bool:
        """The brain's decision on the current action's "end": stay (keep looping) or
        advance (pick a random valid next). Returns True if it advanced.

        - An external event (midi / camera / websocket) is a STRONG trigger: it always
          advances, bypassing the behavior's stay probability (operator control).
        - A behavior trigger (dwell / new_lock / lost_lock / lock_duration) that fired
          applies the stay probability: roll < stay -> stay (loop, re-arm timers);
          else advance to a random valid next.
        - Advancing with no valid candidate: stay (the action keeps looping).
        """
        if event:
            return self._autonomy_advance(zone, action)
        bp = self._behavior_params(behavior)
        stay = bp.get("stay", 0.0)
        if not (isinstance(stay, (int, float)) and not isinstance(stay, bool)):
            stay = 0.0
        if random.random() < stay:
            self._rearm_stay()
            return False
        return self._autonomy_advance(zone, action)

    def _update_lock_tracking(self) -> None:
        """Update the lock-state tracking (the new_lock/lost_lock/lock_duration inputs).

        ``_prev_lock_id`` becomes the current lock id; ``_lock_start_ts`` is reset to
        now when the lock is (re)acquired and zeroed when it is lost. Called at the
        end of every autonomy tick so the edge/duration triggers are evaluated against
        a consistent baseline on the next tick.
        """
        cur_lock = self._hunt_lock_id
        if cur_lock is not None:
            if cur_lock != self._prev_lock_id:
                self._lock_start_ts = time.time()
        else:
            self._lock_start_ts = 0.0
        self._prev_lock_id = cur_lock

    def _autonomy_go(self, zone: str, target_zone: str, target_action: str) -> None:
        """Follow a target: same-zone uses play_action, cross-zone uses trigger_action."""
        if target_zone == zone:
            self.machine.play_action(target_action)
        else:
            self.machine.trigger_action(target_zone, target_action)

    def _autonomy_tick(self, curjpos: List[float]) -> None:
        """Per-tick autonomy decision: on the current action's "end" (an event, or
        dwell expiry) the brain decides to stay (loop) or advance (pick a next).

        Called at the end of :meth:`tick`, after hunt bookkeeping. Detects a
        (zone, action) change to reset the dwell timer, then — when the action has
        ended (event fired or dwell expired) — asks the brain whether to loop or
        advance. Staying resets the dwell timer so the action keeps looping and the
        brain re-evaluates on the next dwell expiry.
        """
        # Guard: while a safety-gated pending move is unresolved, do not stack
        # another autonomy decision on top of it.
        if self.machine.pending:
            self._update_lock_tracking()
            return
        cur = (self.machine.current_zone, self.machine.current_action)
        if cur != self._autonomy_prev:
            # New (zone, action): reset the dwell timer.
            self._autonomy_prev = cur
            self._autonomy_action_start_ts = time.time()
            self._autonomy_triggered = False
            # Clear blocked candidates when the state changes (the old blocked
            # targets are no longer relevant).
            self._clear_blocked_if_changed()
        # The camera intent follows the action (applied on every action change,
        # regardless of whether autonomy is on).
        cur_cam = (self.machine.current_zone, self.machine.current_action)
        if cur_cam != self._cam_action:
            self._cam_action = cur_cam
            self._apply_action_cam(self.machine.current_zone, self.machine.current_action)
            self._apply_action_engine(self.machine.current_zone, self.machine.current_action)
        if not self._autonomy_cfg().get("enabled", False):
            self._update_lock_tracking()
            return
        if self.machine.transitioning:
            self._update_lock_tracking()
            return
        zone, action = cur
        if not zone:
            # No zone at all: not a stuck state (the robot is between zones).
            self._stuck_ticks = 0
            self._update_lock_tracking()
            return
        if not action:
            # Stuck: a zone is set but no action is active (no pending move and no
            # transition, guarded above). Re-arm the robot, or let the watchdog
            # backstop handle a persistent stuck state.
            self._handle_stuck(zone)
            self._update_lock_tracking()
            return
        # Normal path: the robot has a zone and an action; it is not stuck.
        self._stuck_ticks = 0
        # The current action must loop for autonomy to consider advancing (a
        # non-looping action has no "next" to follow in the same zone).
        z = self.machine.zones.get(zone)
        if z is None or not z.action_loops(action):
            self._update_lock_tracking()
            return
        now = time.time()
        behavior = z.action_behavior(action)
        fired = self._behavior_triggers_fired(zone, action, behavior, now)
        event = self._autonomy_triggered
        # Decide: an external event is a STRONG trigger (always advance); a fired
        # behavior trigger applies the stay probability (loop vs random valid next).
        if event or any(fired.values()):
            self._autonomy_decide(zone, action, behavior, event, fired)
            self._autonomy_triggered = False
        # If the (zone, action) did not change (stayed, self-loop advance, or advance
        # refused), reset the dwell timer so the brain re-evaluates on the next
        # trigger instead of re-firing every tick.
        if (self.machine.current_zone, self.machine.current_action) == cur:
            self._autonomy_action_start_ts = time.time()
        self._update_lock_tracking()

    # ------------------------------------------------------------------
    # Stuck-state recovery (auto mode).
    # ------------------------------------------------------------------
    def _handle_stuck(self, zone: str) -> None:
        """Recover a stuck robot (a zone is set but no action is active).

        Called from :meth:`_autonomy_tick` when the robot is in a ``(zone, None)``
        state: ``current_zone`` set, ``current_action`` None, no pending move, no
        transition. This happens when a non-looping action completes and its
        hand-off is either discarded by the safety gate (auto mode) or dead-ends on
        invalid/disabled ``next``/``exits`` data, or when a zone reload removes the
        current action.

        Tries to re-arm the robot by playing a valid action in the current zone. On
        success the stuck counter resets. On failure the counter increments; once it
        reaches the threshold the watchdog clears the blocked-candidate set (so a
        previously-discarded target can be retried) and logs a warning.
        """
        self._stuck_ticks += 1
        if self._rearm_stuck(zone):
            self._stuck_ticks = 0
            return
        if self._stuck_ticks >= self._stuck_threshold():
            self._blocked_candidates.clear()
            self._stuck_ticks = 0
            print(f"[brain] stuck in {zone!r} after {self._stuck_threshold()} ticks; "
                  f"cleared blocked candidates to retry")

    def _rearm_stuck(self, zone: str) -> bool:
        """Re-arm a stuck robot by playing a valid action in the current zone.

        Prefers an enabled, looping, non-blocked action (the robot keeps moving in
        the zone so the brain can then decide to advance). Falls back to the first
        enabled, non-blocked action. Returns the bool from :meth:`play_action`;
        no-op (False) when there is no valid action to play.
        """
        z = self.machine.zones.get(zone)
        if z is None or not z.enabled:
            return False
        # Prefer an enabled, looping, non-blocked action.
        for name in z.actions():
            if (z.action_enabled(name) and z.action_loops(name)
                    and (zone, name) not in self._blocked_candidates):
                return self.machine.play_action(name)
        # Fallback: the first enabled, non-blocked action (even non-looping).
        for name in z.actions():
            if z.action_enabled(name) and (zone, name) not in self._blocked_candidates:
                return self.machine.play_action(name)
        return False

    def _stuck_threshold(self) -> int:
        """The number of stuck ticks before the watchdog clears blocked candidates.

        Reads the optional ``autonomy.stuck_threshold`` (a number > 0); falls back
        to 10 ticks.
        """
        t = self._autonomy_cfg().get("stuck_threshold")
        if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
            return int(t)
        return 10

    # ------------------------------------------------------------------
    # Assertive mode (random safe destinations).
    # ------------------------------------------------------------------
    def _hunt_active(self) -> bool:
        """Whether the person-tracking (hunt) loop runs this tick.

        - manual: False (the operator drives the robot).
        - assertive: True only when ``assertive.track`` is enabled.
        - auto: True (the hunt ladder is part of auto mode).
        """
        if self.machine.control_mode == "manual":
            return False
        if self.machine.control_mode == "assertive":
            return bool(self._assertive_cfg().get("track", False))
        return True

    def _assertive_pool(self) -> List[tuple]:
        """The assertive destination pool: ``(zone, action)`` nodes the robot may go to.

        - An explicit ``pool`` (a list of ``"zone/action"`` strings): those entries,
          validated against the zone table (unknown/disabled entries are dropped).
        - Otherwise: every enabled ``(zone, action)`` node across all enabled zones.
        """
        a = self._assertive_cfg()
        pool = a.get("pool")
        cands: List[tuple] = []
        if isinstance(pool, list) and pool:
            for entry in pool:
                if not isinstance(entry, str) or "/" not in entry:
                    continue
                z, an = entry.split("/", 1)
                if not self.machine.zones.has(z):
                    continue  # unknown zone entry; drop it
                zone = self.machine.zones.get(z)
                if not zone.enabled:
                    continue
                if an not in zone.actions() or not zone.action_enabled(an):
                    continue
                cands.append((z, an))
        else:
            for zname in self.machine.zones.names():
                zone = self.machine.zones.get(zname)
                if zone is None or not zone.enabled:
                    continue
                for an in zone.actions():
                    if zone.action_enabled(an):
                        cands.append((zname, an))
        return cands

    def _assertive_pick_destination(self, cur_zone: str, cur_action: str) -> Optional[tuple]:
        """Pick the next assertive destination: a random ``(zone, action)`` from the pool.

        Skips the current node (a no-op) and candidates blocked by the safety gate.
        When ``wander`` is False, restricts the pool to the current zone's actions
        (no cross-zone travel). Returns None when there is nothing valid to go to.
        """
        a = self._assertive_cfg()
        wander = a.get("wander", True)
        pool = self._assertive_pool()
        if not wander:
            pool = [c for c in pool if c[0] == cur_zone]
        cur = (cur_zone, cur_action)
        options = [c for c in pool if c != cur and c not in self._blocked_candidates]
        if not options:
            return None
        return random.choice(options)

    def _assertive_roll_speed(self) -> None:
        """Roll a per-move speed in ``[speed_min, speed_max]`` and store it on the machine.

        The speed stays constant for the duration of a move (not re-rolled each tick);
        the machine's :meth:`_current_speed` honors it while control_mode is assertive.
        """
        a = self._assertive_cfg()
        lo = a.get("speed_min", 30.0)
        hi = a.get("speed_max", 100.0)
        self.machine.assertive_speed = random.uniform(lo, hi)

    def _assertive_find_path(self, dest: tuple) -> Optional[list]:
        """A safe path from the current position to ``dest`` (a ``(zone, action)``).

        Returns the path (a list of ``(zone, action)`` starting at the current node)
        or None when no safe path exists.
        """
        m = self.machine
        cur_zone = m.current_zone
        cur_action = m.current_action or ""
        zones_dict = m.zones.table()
        safety_check = None
        if m.safety is not None:
            safety_check = m.safety.check_conflict
        return find_safe_path(
            zones_dict, cur_zone, cur_action, dest[0], dest[1],
            safety_check=safety_check,
        )

    def _assertive_tick(self, curjpos: List[float]) -> bool:
        """Per-tick assertive driver: drive the robot to a random safe destination.

        - When there is no active navigation, the robot dwells at its current node;
          once the dwell expires, pick a destination, roll a per-move speed, and find
          a safe path to it (the start is stripped).
        - While navigating, drive the next waypoint when the robot is idle (no
          transition, no pending).
        - When all waypoints are triggered and the last move has settled, the dwell
          timer is reset (via the (zone, action) change) and the next reselect begins.

        Returns True while still navigating/dwelling (always, in assertive mode).
        """
        m = self.machine
        if m.pending:
            return True  # safety-gated move unresolved; wait
        cur = (m.current_zone, m.current_action)
        if cur != self.assertive_prev:
            # New (zone, action): reset the dwell timer (the robot is at a new node)
            # and drop the old blocked candidates (stale at a new node).
            self.assertive_prev = cur
            self.assertive_arrived_ts = time.time()
            self._blocked_candidates.clear()
        if self.assertive_nav:
            # Active navigation: drive the next waypoint when the robot is idle.
            if m.transitioning:
                return True  # still moving to the current waypoint
            if self.assertive_nav_index < len(self.assertive_nav):
                wz, wa = self.assertive_nav[self.assertive_nav_index]
                if wz == m.current_zone:
                    m.play_action(wa)
                else:
                    m.trigger_action(wz, wa)
                self.assertive_nav_index += 1
                print(f"[brain] assertive nav: to {wz}/{wa} "
                      f"({self.assertive_nav_index}/{len(self.assertive_nav)})")
                return True
            # All waypoints triggered; wait for the last move to settle.
            if m.transitioning:
                return True
            # Arrived: clear the navigation (the dwell timer was reset above).
            self.assertive_nav = []
            self.assertive_nav_index = 0
            return False
        # No active navigation: dwelling at the current node.
        if m.transitioning:
            return True  # still finishing the previous move
        now = time.time()
        dwell = self._assertive_cfg().get("dwell_s", 8.0)
        if (now - self.assertive_arrived_ts) < dwell:
            return True  # still dwelling at the current destination; hold
        # Dwell expired: pick the next destination and find a safe path to it.
        dest = self._assertive_pick_destination(m.current_zone, m.current_action or "")
        if dest is None:
            self.assertive_arrived_ts = now  # nowhere to go; re-evaluate later
            return True
        self._assertive_roll_speed()
        # No current action (e.g., entering assertive from manual/frozen): there is
        # no valid start node for a safe path, so trigger the destination directly
        # (the safety gate validates the move; a discard is handled by the reselect).
        if m.current_action is None:
            if m.current_zone == dest[0]:
                m.play_action(dest[1])
            else:
                m.trigger_action(dest[0], dest[1])
            print(f"[brain] assertive: (no current action) direct to {dest[0]}/{dest[1]}")
            return True
        # Same-zone destination: play the action directly (the robot is already in
        # the zone; no cross-zone path is needed). The safety gate still applies
        # (play_action stores the move as pending when wired).
        if dest[0] == m.current_zone:
            m.play_action(dest[1])
            print(f"[brain] assertive: same-zone to {dest[0]}/{dest[1]}")
            return True
        # Cross-zone destination: find a safe path and walk it.
        path = self._assertive_find_path(dest)
        if path is None:
            self.assertive_arrived_ts = now  # no safe path; re-evaluate later
            return True
        self.assertive_nav = path[1:] if len(path) > 1 else []
        self.assertive_nav_index = 0
        return True  # navigating (or already at the destination)

    # ------------------------------------------------------------------
    # External event handlers (autonomy triggers).
    # ------------------------------------------------------------------
    def _handle_midi_trigger(self, note: int) -> None:
        """MIDI note-on event: trigger an immediate next-link advance.

        Called by the MIDI adapter on note-on. Sets ``_autonomy_triggered`` so the
        next :meth:`_autonomy_tick` follows the current action's next link.
        No-op in manual mode (the operator drives the robot).
        """
        if self.machine.test_mode:
            return
        if self.machine.control_mode == "manual":
            return
        if self._autonomy_cfg().get("events", {}).get("midi", False):
            self._autonomy_triggered = True

    def handle_next_pose(self) -> None:
        """MIDI "next_pose" note: advance the current action's pose (midi cadence).

        Called by the MIDI adapter when the learned global "next_pose" key is
        received. Pulses the machine's cadence so the next tick advances the current
        action's anchor. Only meaningful in ``"midi"`` cadence mode (the machine
        ignores the pulse otherwise). Unlike the autonomy triggers, this is an
        explicit operator action and works in manual mode too.
        """
        if self.machine.test_mode:
            return
        self.machine.midi_pulse()

    def _handle_proceed(self) -> None:
        """WebSocket PROCEED event: trigger an immediate next-link advance.

        No-op in manual mode (the operator drives the robot).
        """
        if self.machine.control_mode == "manual":
            return
        if self._autonomy_cfg().get("events", {}).get("websocket", False):
            self._autonomy_triggered = True

    # ------------------------------------------------------------------
    # Safety gate handlers.
    # ------------------------------------------------------------------
    def _handle_safety_cancel(self) -> None:
        """Operator cancelled a blocked move: discard the pending move and mark the
        attempted target blocked so the brain does not immediately retry it."""
        # Capture the target before clearing, so we can mark it blocked.
        info = self.machine.pending_info()
        self.machine.cancel_pending()
        if info is not None:
            self.mark_candidate_blocked(info["zone"], info["action"])

    def _handle_safety_confirm(self) -> None:
        """Operator confirmed a blocked move: commit it despite the safety block."""
        info = self.machine.pending_info()
        self.machine.confirm_pending()
        if self.machine._pending is not None:
            # Still pending (confirm_pending only works in "confirm" phase).
            return
        if info is not None:
            # The move is being committed; clear any blocked mark for it.
            self._blocked_candidates.discard((info["zone"], info["action"]))

    def mark_candidate_blocked(self, zone: str, action: str) -> None:
        """Mark a (zone, action) candidate as blocked (discarded by the safety gate)."""
        self._blocked_candidates.add((zone, action))

    def _clear_blocked_if_changed(self) -> None:
        """Clear blocked candidates when the current (zone, action) changes.

        Called on every autonomy tick; the blocked set is only meaningful for the
        current action's next candidates. When the robot moves to a new (zone,
        action), the old blocked set is stale and is cleared.
        """
        cur = (self.machine.current_zone, self.machine.current_action)
        # The blocked set is keyed by (zone, action) of the *target*, not the
        # current state. We clear it when the current state changes because the
        # old blocked targets are no longer relevant.
        if self._autonomy_prev != cur:
            self._blocked_candidates.clear()

    # ------------------------------------------------------------------
    # Cam test controller (two-phase: NAVIGATE → BEHAVIOR).
    # ------------------------------------------------------------------
    def _handle_cam_test_start(self, p: dict, curjpos: List[float]) -> None:
        """Start a cam test: navigate to the target zone/action, then run the
        chosen behavior.

        Payload: ``{zone, action, behavior}`` — the target zone/action and the
        behavior to run there.

        1. Find a safe path from the current position to the target.
        2. If no safe path exists, reject (log error, do not enter test mode).
        3. Enter test mode: the brain drives navigation, then behavior.
        """
        target_zone = p.get("zone")
        target_action = p.get("action")
        behavior = p.get("behavior")
        if not target_zone or not target_action or not behavior:
            print("[brain] cam_test_start: missing zone/action/behavior; ignored")
            return
        # Validate the target exists and is enabled.
        if not self.machine.zones.has(target_zone):
            print(f"[brain] cam_test_start: unknown zone {target_zone!r}")
            return
        tz = self.machine.zones.get(target_zone)
        if not tz.enabled:
            print(f"[brain] cam_test_start: disabled zone {target_zone!r}")
            return
        if target_action not in tz.actions() or not tz.action_enabled(target_action):
            print(f"[brain] cam_test_start: unknown or disabled action {target_action!r} in {target_zone!r}")
            return
        # Find a safe path from the current position to the target.
        cur_zone = self.machine.current_zone
        cur_action = self.machine.current_action or ""
        zones_dict = self.machine.zones.table()
        safety_check = None
        if self.machine.safety is not None:
            safety_check = self.machine.safety.check_conflict
        path = find_safe_path(
            zones_dict,
            cur_zone, cur_action,
            target_zone, target_action,
            safety_check=safety_check,
        )
        if path is None:
            print(f"[brain] cam_test_start: no safe path from {cur_zone}/{cur_action} to {target_zone}/{target_action}")
            return
        # The path includes the start position; the brain navigates the rest.
        # Strip the start (the robot is already there).
        nav_path = path[1:] if len(path) > 1 else []
        self.machine.start_cam_test(nav_path, behavior)
        print(f"[brain] cam test started: navigating {len(nav_path)} waypoints to {target_zone}/{target_action}, behavior={behavior}")

    def _handle_cam_test_stop(self) -> None:
        """Stop the cam test: clear test state and return to auto mode."""
        self.machine.stop_cam_test()
        print("[brain] cam test stopped")

    def _test_tick(self, curjpos: List[float]) -> None:
        """Per-tick cam test navigation driver.

        Called from :meth:`tick` when ``test_mode`` is active. Drives the two-phase
        controller:

        - **NAVIGATE**: trigger the next waypoint when the previous one is done.
          When all waypoints are reached, switch to BEHAVIOR.
        - **BEHAVIOR**: the robot stays in the target zone/action with the forced
          behavior running. No further navigation.
        """
        m = self.machine
        if not m.test_mode:
            return
        if m.test_phase == "navigate":
            # Drive the next waypoint when the robot is idle (no transition, no pending).
            if not m.transitioning and not m.pending:
                if m.test_path_index < len(m.test_path):
                    wz, wa = m.test_path[m.test_path_index]
                    # Trigger the waypoint.
                    if wz == m.current_zone:
                        m.play_action(wa)
                    else:
                        m.trigger_action(wz, wa)
                    m.test_path_index += 1
                    print(f"[brain] cam test: navigating to {wz}/{wa} "
                          f"({m.test_path_index}/{len(m.test_path)})")
                else:
                    # All waypoints triggered; the robot should be at the target.
                    # Switch to behavior phase.
                    m.test_phase = "behavior"
                    m.enable_behavior(m.test_behavior)
                    print(f"[brain] cam test: arrived at target, running behavior {m.test_behavior!r}")
            # If transitioning or pending, wait (do nothing this tick).
        elif m.test_phase == "behavior":
            # The robot stays in the target zone/action with the forced behavior.
            # Ensure the behavior keeps running (the machine's step() handles this
            # via the forced_behavior + current_action).
            pass

    # ------------------------------------------------------------------
    # Runtime group navigation (one active group at a time).
    # ------------------------------------------------------------------
    def _group_targets(self, group_name: str) -> List[tuple]:
        """The group's enabled ``(zone, action)`` nodes (path targets)."""
        g = self._config.get("zone_groups", {}).get(group_name)
        if not isinstance(g, dict):
            return []
        targets: List[tuple] = []
        for z in g.get("zones", []):
            if not isinstance(z, str):
                continue
            zone = self.machine.zones.get(z)
            if zone is None or not zone.enabled:
                continue
            for a in zone.actions():
                if zone.action_enabled(a):
                    targets.append((z, a))
        return targets

    def _handle_enable_group(self, group_name: Optional[str], curjpos: List[float]) -> None:
        """Enable a zone group at runtime: navigate to it, then stay in it.

        1. Validate the group exists in ``zone_groups``.
        2. Switch to auto mode (enabling a group disables manual mode).
        3. Find a safe path from the current position to any enabled node in
           the group, log it, and walk the waypoints hop-by-hop.
        """
        if not isinstance(group_name, str) or not group_name:
            print("[brain] enable_group: missing group name; ignored")
            return
        zgs = self._config.get("zone_groups", {})
        if not isinstance(zgs, dict) or group_name not in zgs:
            print(f"[brain] enable_group: unknown group {group_name!r}")
            return
        # Enabling a group disables manual mode (the brain drives the navigation).
        self.machine.set_control_mode("auto", curjpos)
        # Collect the group's enabled nodes as path targets.
        targets = self._group_targets(group_name)
        if not targets:
            print(f"[brain] enable_group: group {group_name!r} has no enabled nodes")
            self.active_group = group_name
            self.group_nav = []
            self.group_nav_index = 0
            return
        # Find a safe path from the current position to any target node.
        cur_zone = self.machine.current_zone
        cur_action = self.machine.current_action or ""
        zones_dict = self.machine.zones.table()
        safety_check = None
        if self.machine.safety is not None:
            safety_check = self.machine.safety.check_conflict
        path = find_safe_path_to_any(
            zones_dict, cur_zone, cur_action, targets,
            safety_check=safety_check,
        )
        if path is None:
            print(f"[brain] enable_group: no safe path from {cur_zone}/{cur_action} "
                  f"to group {group_name!r}")
            # Revert: no active group (the robot keeps its current state).
            self.active_group = None
            self.group_nav = []
            self.group_nav_index = 0
            return
        # Log the path and store the waypoints (strip the start; we are there).
        path_str = " -> ".join(f"{z}/{a}" for z, a in path)
        print(f"[brain] enable_group {group_name!r}: {path_str}")
        self.active_group = group_name
        self.group_nav = path[1:] if len(path) > 1 else []
        self.group_nav_index = 0

    def clear_group(self) -> None:
        """Clear the active group: stop group navigation and resume normal autonomy."""
        self.active_group = None
        self.group_nav = []
        self.group_nav_index = 0
        print("[brain] group cleared; resuming normal autonomy")

    def _group_tick(self, curjpos: List[float]) -> bool:
        """Per-tick group navigation driver. Returns True while still navigating.

        Drives the next waypoint when the robot is idle (no transition, no
        pending). When all waypoints are triggered and the robot has finished
        the last move, the group is "arrived": ``group_nav`` is cleared and
        normal autonomy resumes (restricted to the group's zones).
        """
        m = self.machine
        if not self.group_nav:
            return False  # no waypoints; already arrived
        # Drive the next waypoint when the robot is idle.
        if m.transitioning or m.pending:
            return True  # still navigating; wait for the move to settle
        if self.group_nav_index < len(self.group_nav):
            wz, wa = self.group_nav[self.group_nav_index]
            if wz == m.current_zone:
                m.play_action(wa)
            else:
                m.trigger_action(wz, wa)
            self.group_nav_index += 1
            print(f"[brain] group nav: to {wz}/{wa} "
                  f"({self.group_nav_index}/{len(self.group_nav)})")
            return True  # still navigating
        # All waypoints triggered; wait for the robot to finish the last move.
        if m.transitioning:
            return True
        # Arrived.
        self.group_nav = []
        self.group_nav_index = 0
        return False

    def _allowed_zones(self) -> set:
        """The zones autonomy may navigate to.

        When a group is active, only that group's zones (the robot stays in the
        group with no group travel). Otherwise the union of all enabled groups'
        zones (the normal multi-group autonomy).
        """
        if self.active_group is not None:
            g = self._config.get("zone_groups", {}).get(self.active_group)
            if isinstance(g, dict):
                return set(z for z in g.get("zones", []) if isinstance(z, str))
        return self._enabled_zones
