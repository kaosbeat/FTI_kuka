"""Beat clock: tracks a continuous beat phase from external MIDI clock or internal BPM.

The clock is the time base for Perform Mode. It maintains a single continuous
``beat`` counter (float) that advances every engine tick. When an external MIDI
clock (0xF8) is present, the BPM is derived from the inter-clock interval; when
the clock is absent (timeout), the clock falls back to the internal BPM.

Thread safety: the MIDI adapter thread calls :meth:`clock_tick` and the engine
thread calls :meth:`tick`. A ``threading.Lock`` guards all shared state.
"""

import threading
import time
from collections import deque
from typing import Dict, Optional


class BeatClock:
    """Tracks a continuous beat counter from external MIDI clock or internal BPM.

    The clock exposes a single ``beat`` float that advances in real time.
    The source is either ``"external"`` (derived from 0xF8 clock ticks) or
    ``"internal"`` (the configured internal BPM).

    Parameters
    ----------
    internal_bpm : float
        Fallback BPM when no external clock is present (default 130.0).
    clock_timeout : float
        Seconds of silence before an external clock is considered lost (default 1.0).
    ppqn : int
        MIDI pulses-per-quarter-note (default 24, the MIDI standard).
    window : int
        Rolling window size for inter-clock interval averaging (default 24).
    """

    def __init__(
        self,
        internal_bpm: float = 130.0,
        clock_timeout: float = 1.0,
        ppqn: int = 24,
        window: int = 24,
    ):
        self._internal_bpm = internal_bpm
        self._clock_timeout = clock_timeout
        self._ppqn = ppqn
        self._window = window
        self._lock = threading.Lock()

        # Continuous beat counter (float, monotonically increasing).
        self._beat: float = 0.0

        # Source: "external" or "internal".
        self._source: str = "internal"

        # External clock state.
        self._external_bpm: float = 0.0
        self._intervals: deque = deque(maxlen=self._window)
        self._last_clock_time: Optional[float] = None

        # Internal BPM (mutable via set_internal_bpm).
        self._bpm_internal: float = internal_bpm

        # Track the last tick time for dt computation.
        self._last_tick_time: Optional[float] = None

    # ------------------------------------------------------------------
    # MIDI clock callback (called from the MIDI adapter thread).
    # ------------------------------------------------------------------

    def clock_tick(self, now: Optional[float] = None) -> None:
        """Record one MIDI clock (0xF8) tick.

        Computes the inter-clock interval, appends it to the rolling window,
        and derives the external BPM. Does NOT advance the beat counter —
        that is the engine's job in :meth:`tick`.

        Parameters
        ----------
        now : float, optional
            Current monotonic time (seconds). Defaults to ``time.monotonic()``.
        """
        if now is None:
            now = time.monotonic()

        with self._lock:
            if self._last_clock_time is not None:
                interval = now - self._last_clock_time
                if interval > 0:
                    self._intervals.append(interval)
                    avg = sum(self._intervals) / len(self._intervals)
                    # MIDI clock: ppqn pulses per beat.
                    # beat_duration = ppqn * avg_interval
                    # bpm = 60 / beat_duration
                    self._external_bpm = 60.0 / (self._ppqn * avg)
            self._last_clock_time = now
            self._source = "external"

    # ------------------------------------------------------------------
    # Engine tick (called from the engine thread every tick).
    # ------------------------------------------------------------------

    def tick(self, now: Optional[float] = None) -> None:
        """Advance the continuous beat counter.

        Called once per engine tick (typically 20 Hz). Performs:
        1. Timeout check: if the external clock has been silent for longer
           than ``clock_timeout``, fall back to internal BPM.
        2. Select the active BPM (external if available, else internal).
        3. Advance ``beat`` by ``dt / beat_duration``.

        Parameters
        ----------
        now : float, optional
            Current monotonic time (seconds). Defaults to ``time.monotonic()``.
        """
        if now is None:
            now = time.monotonic()

        with self._lock:
            # Timeout check: external clock lost?
            if self._source == "external":
                if self._last_clock_time is not None:
                    elapsed = now - self._last_clock_time
                    if elapsed > self._clock_timeout:
                        # Clock lost — fall back to internal.
                        self._source = "internal"
                        self._external_bpm = 0.0
                        self._intervals.clear()
                        self._last_clock_time = None

            # Select active BPM.
            if self._source == "external" and self._external_bpm > 0:
                bpm = self._external_bpm
            else:
                bpm = self._bpm_internal

            # Compute dt from the last tick.
            if self._last_tick_time is None:
                # First tick: no dt to advance.
                self._last_tick_time = now
                return

            dt = now - self._last_tick_time
            if dt <= 0:
                self._last_tick_time = now
                return

            beat_duration = 60.0 / bpm
            self._beat += dt / beat_duration
            self._last_tick_time = now

    # ------------------------------------------------------------------
    # State query (thread-safe).
    # ------------------------------------------------------------------

    def state(self) -> Dict:
        """Return a JSON-serialisable snapshot of the clock state.

        Returns
        -------
        dict with keys:
            - ``beat``: continuous beat counter (float).
            - ``beat_index``: integer part of beat (current beat number).
            - ``beat_phase``: fractional part of beat (0.0–1.0).
            - ``bpm``: currently active BPM (external or internal).
            - ``beat_duration``: seconds per beat at the active BPM.
            - ``source``: ``"external"`` or ``"internal"``.
        """
        with self._lock:
            if self._source == "external" and self._external_bpm > 0:
                bpm = self._external_bpm
            else:
                bpm = self._bpm_internal
            beat_duration = 60.0 / bpm if bpm > 0 else 0.0
            return {
                "beat": self._beat,
                "beat_index": int(self._beat),
                "beat_phase": self._beat % 1.0,
                "bpm": bpm,
                "beat_duration": beat_duration,
                "source": self._source,
            }

    # ------------------------------------------------------------------
    # Configuration (thread-safe).
    # ------------------------------------------------------------------

    def set_internal_bpm(self, bpm: float) -> None:
        """Set the internal BPM (clamped to [1.0, 300.0]).

        Parameters
        ----------
        bpm : float
            Desired internal BPM.
        """
        with self._lock:
            self._bpm_internal = max(1.0, min(300.0, bpm))

    def reset(self, now: Optional[float] = None) -> None:
        """Reset the clock to its initial state.

        Clears the beat counter, external clock state, and intervals.

        Parameters
        ----------
        now : float, optional
            Current monotonic time (seconds). Defaults to ``time.monotonic()``.
        """
        if now is None:
            now = time.monotonic()

        with self._lock:
            self._beat = 0.0
            self._source = "internal"
            self._external_bpm = 0.0
            self._intervals.clear()
            self._last_clock_time = None
            self._last_tick_time = now
