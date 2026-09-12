"""StateBus: the core's thread-safe command intake and event fan-out.

The bus is the only object every adapter touches. Adapters that *send* commands call
:meth:`submit` (safe from any thread, so the rtmidi callback can call it directly).
The engine drains :meth:`drain_commands` once per tick. Adapters that *receive*
events call :meth:`subscribe` and are invoked by :meth:`publish` after each tick.

Keeping intake and fan-out in one small object is what replaces the old global
``kukastate`` dict + ``kukastate_queue`` that lived in ``tidalkuka.py``.
"""

import queue
import threading
from typing import Any, Callable, Dict, List

from .commands import Command, Event, Snapshot


class StateBus:
    """Thread-safe command queue + event pub/sub with a cached snapshot."""

    def __init__(self):
        self._commands: "queue.Queue[Command]" = queue.Queue()
        self._subscribers: List[Callable[[Event, Any], None]] = []
        self._lock = threading.Lock()
        self._snapshot: Snapshot | None = None

    # -- command intake ----------------------------------------------------
    def submit(self, command: Command) -> None:
        """Queue a command for the engine. Safe to call from any thread."""
        self._commands.put(command)

    def drain_commands(self) -> List[Command]:
        """Collect all queued commands (called once per tick by the engine)."""
        out: List[Command] = []
        while True:
            try:
                out.append(self._commands.get_nowait())
            except queue.Empty:
                return out

    # -- event fan-out -----------------------------------------------------
    def subscribe(self, handler: Callable[[Event, Any], None]) -> None:
        """Register a handler invoked as ``handler(event, data)``."""
        with self._lock:
            self._subscribers.append(handler)

    def unsubscribe(self, handler: Callable[[Event, Any], None]) -> None:
        with self._lock:
            try:
                self._subscribers.remove(handler)
            except ValueError:
                pass

    def publish(self, event: Event, data: Any) -> None:
        """Notify every subscriber. Errors in one handler never stop the others."""
        with self._lock:
            handlers = list(self._subscribers)
        for handler in handlers:
            try:
                handler(event, data)
            except Exception as exc:  # noqa: BLE001 - adapters must not kill the loop
                print(f"[bus] subscriber error on {event.value}: {exc}")

    # -- snapshot cache ----------------------------------------------------
    def set_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot

    def get_snapshot(self) -> Snapshot | None:
        return self._snapshot
