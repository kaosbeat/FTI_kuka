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

from typing import List

from ..core.commands import Cmd, Command
from ..state.behavior import clamp
from ..state.machine import StateMachine
from ..state.zones import MODES  # re-exported for `from rtr.brain import MODES`


class Brain:
    """Maps commands onto the state machine. Holds no robot state of its own."""

    def __init__(self, machine: StateMachine):
        self.machine = machine

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
