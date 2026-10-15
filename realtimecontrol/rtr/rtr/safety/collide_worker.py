"""CollisionChecker: persistent Node worker for KR60 self-collision checks.

Spawns ``node collide_worker.js`` as a child process and communicates over
stdin/stdout with one JSON object per line:

  in:  {"id": N, "pose": [a1, a2, a3, a4, a5, a6]}
  out: {"id": N, "collision": bool, "collisions": [...]}

The worker loads the KR60 collision STL world once at startup and answers
queries in O(1) amortised time (the SAT world is reused).

A reader thread keeps the stdout pipe drained so the main thread can issue
queries synchronously without deadlocking.
"""

import json
import os
import subprocess
import threading
from typing import Dict, List, Optional


class CollisionChecker:
    """Persistent Node collision worker wrapper.

    Parameters
    ----------
    worker_path : str or None
        Path to ``collide_worker.js``. Defaults to the sibling file in the
        project root (resolved relative to this package's parent directory).
    timeout : float
        Per-query timeout in seconds (default 5.0).
    """

    def __init__(self, worker_path: Optional[str] = None, timeout: float = 5.0):
        if worker_path is None:
            # rtr/safety/ -> rtr/ -> project root (where collide_worker.js lives)
            here = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(here))
            worker_path = os.path.join(project_root, "collide_worker.js")
        self._worker_path = worker_path
        self._timeout = timeout
        self._proc: Optional[subprocess.Popen] = None
        self._req_id = 0
        self._lock = threading.Lock()
        self._pending: Dict[int, threading.Event] = {}
        self._results: Dict[int, dict] = {}
        self._reader_thread: Optional[threading.Thread] = None
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle.
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Spawn the Node worker and start the reader thread (idempotent)."""
        if self._started:
            return
        self._proc = subprocess.Popen(
            ["node", self._worker_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name="collide-reader"
        )
        self._reader_thread.start()
        self._started = True
        print(f"[safety] collision worker started: {self._worker_path}")

    def stop(self) -> None:
        """Terminate the Node worker (idempotent)."""
        if not self._started:
            return
        if self._proc is not None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
            self._reader_thread = None
        self._started = False
        print("[safety] collision worker stopped")

    # ------------------------------------------------------------------
    # Query.
    # ------------------------------------------------------------------
    def check(self, pose: List[float]) -> dict:
        """Run a self-collision check on a 6-axis pose.

        Returns ``{"collision": bool, "collisions": [...], "error"?: str}``.
        When the worker is not running, returns a safe default (no collision)
        with a warning.
        """
        if not self._started:
            print("[safety] WARNING: collision worker not started; skipping check")
            return {"collision": False, "collisions": []}

        with self._lock:
            self._req_id += 1
            rid = self._req_id
            evt = threading.Event()
            self._pending[rid] = evt
            try:
                self._proc.stdin.write(
                    json.dumps({"id": rid, "pose": list(pose)}) + "\n"
                )
                self._proc.stdin.flush()
            except Exception as exc:
                self._pending.pop(rid, None)
                print(f"[safety] collision worker write failed: {exc}")
                return {"collision": True, "collisions": [], "error": str(exc)}

        if not evt.wait(timeout=self._timeout):
            self._pending.pop(rid, None)
            print("[safety] collision check timed out")
            return {"collision": True, "collisions": [], "error": "timeout"}

        self._pending.pop(rid, None)
        return self._results.pop(rid, {"collision": True, "collisions": [],
                                       "error": "no result"})

    # ------------------------------------------------------------------
    # Reader thread.
    # ------------------------------------------------------------------
    def _read_loop(self) -> None:
        """Continuously read stdout lines from the worker and resolve pending queries."""
        assert self._proc is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = msg.get("id")
            if rid is None:
                continue
            self._results[rid] = msg
            evt = self._pending.get(rid)
            if evt is not None:
                evt.set()
