#!/usr/bin/env python3
"""Thin launcher for the canonical remote TUI.

This top-level ``remote/tui.py`` is a historical scaffold. The real TUI now
lives in the ``rtr`` package (``rtr/remote/tui.py``). This file only bootstraps
the package root onto ``sys.path`` and re-exports the canonical ``run()`` so the
original ``python remote/tui.py [flags]`` invocation keeps working.
"""
import os
import sys

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from rtr.remote.tui import run  # noqa: E402


if __name__ == "__main__":
    run()
