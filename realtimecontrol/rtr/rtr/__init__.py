"""RTR - RealTimeRobot core.

A clean, modular control system for the KUKA KR60. See the README in the parent
folder for the architecture. The modules are:

- ``rtr.core``      engine loop, state bus, command/event API
- ``rtr.state``     state machine + zone data
- ``rtr.robot``     robot abstraction (Kuka, Sim) + joint-limit helpers
- ``rtr.brain``     decision layer
- ``rtr.camera``    camera control adapter
- ``rtr.display``   P5live display adapter
- ``rtr.sound``     MIDI sound adapter
- ``rtr.io``        input adapters (MIDI-in, WebSocket)
"""

__all__ = [
    "config",
    "core",
    "state",
    "robot",
    "brain",
    "camera",
    "display",
    "sound",
    "io",
    "remote",
]
