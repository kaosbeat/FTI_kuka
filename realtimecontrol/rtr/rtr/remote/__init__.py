"""Remote TUI adapter.

This submodule is the main entry point for the remote TUI client. It imports
everything in the package namespace, then exposes a ``from_args`` factory (see
``rtr/config.py``) and the top-level ``run()`` function for the CLI.

The remote module is designed to be independent of the core, only communicating
via WebSocket or HTTP APIs.
"""

__all__ = [
    "Config",
    "from_args",
    "run",
    "tui_run",
    "RemoteTUI",
    "Connection",
    "connection",
    "config",
    "events",
    "tui",
]

from . import config, connection, events, tui
from .connection import Connection
from .tui import RemoteTUI, run as tui_run
run = tui_run
