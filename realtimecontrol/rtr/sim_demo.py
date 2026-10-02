"""Self-contained simulation demo.

Runs the whole core headless (sim robot, no MIDI) and drives it with a scripted
sequence of commands so you can watch the state machine, transitions, and wander
behaviour work end to end:

    python sim_demo.py

It runs for ~15s, printing a line each second, then exits. No hardware needed.
"""

import asyncio

from rtr.core.bus import StateBus
from rtr.core.commands import Cmd, Command
from rtr.core.engine import Engine
from rtr.robot import SimRobot
from rtr.state import StateMachine, Zones
from rtr.brain import Brain


async def demo() -> None:
    bus = StateBus()
    robot = SimRobot(tick_hz=30.0)
    robot.connect()

    zones = Zones()
    machine = StateMachine(zones, tick_hz=30.0)
    brain = Brain(machine)
    engine = Engine(bus, robot, brain, machine, tick_hz=30.0)

    def submit(cmd: Cmd, **payload) -> None:
        bus.submit(Command(cmd=cmd, payload=payload))

    async def script() -> None:
        # A little scripted show: wake up, stretch, wander, then an action.
        await asyncio.sleep(1.0)
        submit(Cmd.TRIGGER_ACTION, zone="wakeup", action="breathe")
        await asyncio.sleep(3.0)
        submit(Cmd.TRIGGER_ACTION, zone="stretch", action="look")
        await asyncio.sleep(3.0)
        submit(Cmd.TRIGGER_ACTION, zone="wander", action="breathe")
        await asyncio.sleep(3.0)
        submit(Cmd.PLAY_ACTION, action="look")
        await asyncio.sleep(2.0)

    engine_task = asyncio.create_task(engine.run())
    script_task = asyncio.create_task(script())

    # Print a status line each second.
    for _ in range(15):
        await asyncio.sleep(1.0)
        snap = bus.get_snapshot()
        if snap:
            print(f"  t={_:>2}  zone={snap.zone:<10} mode={snap.mode:<8} "
                  f"action={snap.action} moving={snap.moving} "
                  f"joints={[round(j, 1) for j in snap.joint_pose]}")

    engine.stop()
    engine_task.cancel()
    script_task.cancel()
    await asyncio.gather(engine_task, script_task, return_exceptions=True)
    robot.disconnect()
    print("\n[rtr] sim demo done")


if __name__ == "__main__":
    asyncio.run(demo())
