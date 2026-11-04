"""Test: BeatClock (rtr/core/clock.py).

Covers:
- Internal BPM mode (beat advances at the configured rate).
- External MIDI clock mode (0xF8 ticks derive BPM from inter-clock intervals).
- Timeout fallback (external clock lost → internal BPM).
- state() fields.
- set_internal_bpm clamping.
- reset().

All tests use explicit injected ``now`` values (faked monotonic time) so no
real-time dependencies exist.

Run directly (no pytest needed)::

    python tests/test_clock.py

or under pytest::

    pytest tests/test_clock.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.core.clock import BeatClock


def test_internal_mode_advances():
    """Internal mode: at 130 BPM, after 1.0 s the beat should be ~2.1667."""
    clock = BeatClock(internal_bpm=130.0)
    # First tick establishes the baseline (no dt).
    clock.tick(now=0.0)
    # Advance by 1.0 second.
    clock.tick(now=1.0)
    s = clock.state()
    assert s["source"] == "internal"
    assert s["bpm"] == 130.0
    assert s["beat_duration"] == 60.0 / 130.0
    # beat = 1.0 / (60/130) = 130/60 = 2.1667
    assert abs(s["beat"] - 2.1667) < 0.01
    assert s["beat_index"] == 2
    assert abs(s["beat_phase"] - 0.1667) < 0.01


def test_internal_mode_multiple_ticks():
    """Internal mode: multiple ticks accumulate correctly."""
    clock = BeatClock(internal_bpm=120.0)
    clock.tick(now=0.0)
    # 20 Hz ticks for 0.5 s = 10 ticks of 0.05 s each.
    for i in range(1, 11):
        clock.tick(now=i * 0.05)
    s = clock.state()
    # Total time = 0.5 s, bpm = 120, beat_duration = 0.5 s
    # beat = 0.5 / 0.5 = 1.0
    assert abs(s["beat"] - 1.0) < 0.001
    assert s["beat_index"] == 1
    assert abs(s["beat_phase"] - 0.0) < 0.001


def test_external_mode_derives_bpm():
    """External mode: feeding clocks every 0.025 s → BPM ≈ 100.

    24 PPQN × 0.025 s = 0.6 s per beat → 60/0.6 = 100 BPM.
    """
    clock = BeatClock(internal_bpm=130.0, ppqn=24)
    # First tick establishes baseline.
    clock.tick(now=0.0)

    # Feed 24 MIDI clock ticks (one full beat) at 0.025 s intervals.
    t = 0.0
    for i in range(24):
        t += 0.025
        clock.clock_tick(now=t)

    s = clock.state()
    assert s["source"] == "external"
    # After 24 intervals of 0.025 s: avg = 0.025, bpm = 60/(24*0.025) = 100
    assert abs(s["bpm"] - 100.0) < 0.01
    assert abs(s["beat_duration"] - 0.6) < 0.001

    # Now tick the engine — beat should advance at external BPM.
    # _last_tick_time was set to 0.0 by the first tick; now = t = 0.6.
    clock.tick(now=t)  # dt = 0.6 s, beat_duration = 0.6 s → beat = 1.0
    s = clock.state()
    assert abs(s["beat"] - 1.0) < 0.01


def test_external_mode_shorter_interval():
    """External mode: faster clocks → higher BPM."""
    clock = BeatClock(internal_bpm=130.0, ppqn=24)
    clock.tick(now=0.0)

    # Feed 24 clocks at 0.01 s intervals → beat_duration = 24*0.01 = 0.24 s → BPM = 60/0.24 = 250
    t = 0.0
    for i in range(24):
        t += 0.01
        clock.clock_tick(now=t)

    s = clock.state()
    assert s["source"] == "external"
    assert abs(s["bpm"] - 250.0) < 1.0


def test_timeout_fallback():
    """Timeout: stop feeding clocks, tick past timeout → internal BPM."""
    clock = BeatClock(internal_bpm=130.0, clock_timeout=1.0, ppqn=24)
    clock.tick(now=0.0)

    # Feed a few clocks.
    t = 0.0
    for i in range(24):
        t += 0.025
        clock.clock_tick(now=t)

    assert clock.state()["source"] == "external"

    # Now stop feeding clocks and tick past the timeout.
    clock.tick(now=t + 1.5)
    s = clock.state()
    assert s["source"] == "internal"
    assert s["bpm"] == 130.0


def test_timeout_does_not_trigger_within_window():
    """Timeout: ticking within the timeout window keeps external source."""
    clock = BeatClock(internal_bpm=130.0, clock_timeout=1.0, ppqn=24)
    clock.tick(now=0.0)

    t = 0.0
    for i in range(24):
        t += 0.025
        clock.clock_tick(now=t)

    # Tick 0.5 s after last clock (within 1.0 s timeout).
    clock.tick(now=t + 0.5)
    s = clock.state()
    assert s["source"] == "external"
    assert abs(s["bpm"] - 100.0) < 0.01


def test_state_fields():
    """state() returns all expected fields with correct types."""
    clock = BeatClock(internal_bpm=130.0)
    clock.tick(now=0.0)
    clock.tick(now=0.5)
    s = clock.state()
    assert set(s.keys()) == {"beat", "beat_index", "beat_phase", "bpm", "beat_duration", "source"}
    assert isinstance(s["beat"], float)
    assert isinstance(s["beat_index"], int)
    assert isinstance(s["beat_phase"], float)
    assert isinstance(s["bpm"], float)
    assert isinstance(s["beat_duration"], float)
    assert isinstance(s["source"], str)


def test_set_internal_bpm():
    """set_internal_bpm changes the internal BPM."""
    clock = BeatClock(internal_bpm=130.0)
    clock.set_internal_bpm(200.0)
    clock.tick(now=0.0)
    clock.tick(now=1.0)
    s = clock.state()
    assert s["bpm"] == 200.0
    assert abs(s["beat"] - (1.0 / (60.0 / 200.0))) < 0.01
    assert abs(s["beat"] - 200.0 / 60.0) < 0.01


def test_set_internal_bpm_clamping():
    """set_internal_bpm clamps to [1.0, 300.0]."""
    clock = BeatClock(internal_bpm=130.0)
    clock.set_internal_bpm(0.5)
    clock.tick(now=0.0)
    clock.tick(now=1.0)
    assert clock.state()["bpm"] == 1.0

    clock2 = BeatClock(internal_bpm=130.0)
    clock2.set_internal_bpm(500.0)
    clock2.tick(now=0.0)
    clock2.tick(now=1.0)
    assert clock2.state()["bpm"] == 300.0


def test_reset():
    """reset() clears beat, source, and external state."""
    clock = BeatClock(internal_bpm=130.0, ppqn=24)
    clock.tick(now=0.0)

    t = 0.0
    for i in range(24):
        t += 0.025
        clock.clock_tick(now=t)

    clock.tick(now=t + 0.6)
    assert clock.state()["beat"] > 0

    # Reset at a new time.
    clock.reset(now=t + 0.6)
    s = clock.state()
    assert s["beat"] == 0.0
    assert s["beat_index"] == 0
    assert s["beat_phase"] == 0.0
    assert s["source"] == "internal"
    assert s["bpm"] == 130.0

    # After reset, internal mode should work.
    clock.tick(now=t + 1.6)
    s = clock.state()
    assert s["source"] == "internal"
    # 1.0 s at 130 BPM → 130/60 = 2.1667 beats
    assert abs(s["beat"] - 2.1667) < 0.01


def test_first_tick_no_advance():
    """The first tick establishes the baseline and does not advance beat."""
    clock = BeatClock(internal_bpm=130.0)
    clock.tick(now=0.0)
    s = clock.state()
    assert s["beat"] == 0.0
    assert s["beat_index"] == 0
    assert s["beat_phase"] == 0.0


def test_negative_dt_ignored():
    """A tick with now < last_tick_time does not advance (dt <= 0 guard)."""
    clock = BeatClock(internal_bpm=130.0)
    clock.tick(now=0.0)
    clock.tick(now=1.0)
    beat_after_forward = clock.state()["beat"]

    # Simulate a clock going backwards.
    clock.tick(now=0.5)
    s = clock.state()
    assert s["beat"] == beat_after_forward


def test_window_averaging():
    """The rolling window averages intervals, not just the last one."""
    clock = BeatClock(internal_bpm=130.0, ppqn=24, window=4)
    clock.tick(now=0.0)

    # Feed 5 clocks → 4 inter-clock intervals in the window.
    # The first clock_tick establishes _last_clock_time (no interval).
    t = 0.0
    intervals = [0.01, 0.02, 0.03, 0.04, 0.05]
    for dt in intervals:
        t += dt
        clock.clock_tick(now=t)

    s = clock.state()
    # 4 intervals: [0.02, 0.03, 0.04, 0.05], avg = 0.035
    # bpm = 60/(24*0.035) = 71.43
    assert abs(s["bpm"] - 71.43) < 0.1


def test_concurrent_safety_smoke():
    """Smoke test: many clock_tick + tick calls don't crash or deadlock.

    Not a full concurrency test (no threads), but exercises the lock paths.
    """
    clock = BeatClock(internal_bpm=130.0, ppqn=24)
    clock.tick(now=0.0)

    t = 0.0
    for i in range(100):
        t += 0.01
        clock.clock_tick(now=t)
        clock.tick(now=t)

    s = clock.state()
    assert s["source"] == "external"
    assert s["beat"] > 0


def main():
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {name}: {e}")
    if failures:
        print(f"\n{failures} TEST(S) FAILED")
        return 1
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
