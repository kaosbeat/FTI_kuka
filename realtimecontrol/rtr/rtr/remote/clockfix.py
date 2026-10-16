"""Workaround for a broken CLOCK_MONOTONIC resolution on some kernels.

The TinaLinux 4.9.191 kernel (Trimui NextUI, the "tremui" R36S) returns a bogus
``clock_getres(CLOCK_MONOTONIC)`` of ~548 seconds. Python's asyncio reads this
via ``time.get_clock_info('monotonic').resolution`` and stores it as
``loop._clock_resolution`` at event-loop creation (``base_events.py``).

With a 548 s resolution, every asyncio timer is considered "due" the instant the
loop wakes, so ``asyncio.wait_for`` fires its timeout on the next loop
iteration and cancels the wrapped task. That is what made websockets' opening
handshake fail with ``timed out during opening handshake`` on the tremui, while
the same code worked on the R36S running DarkOS (a kernel that reports a sane
resolution).

:func:`install` clamps the reported monotonic resolution to nanoseconds before
asyncio reads it. It is a **no-op on kernels that report a sane resolution**
(< 1 s), so it is safe to import unconditionally. Import this module before
creating any asyncio event loop; the patch is applied at import time.
"""

import time as _time

# A clock resolution >= 1 s is never legitimate (real resolutions are
# nanosecond-scale); anything at or above this is the broken-kernel value.
_BOGUS_THRESHOLD_S = 1.0
# A nanosecond-scale resolution, like a sane kernel reports.
_SANE_RESOLUTION_S = 1e-9

_patched = False


class _FixedClockInfo:
    """Clock-info stand-in with a sane resolution; mirrors the C struct's attrs."""

    resolution = _SANE_RESOLUTION_S

    def __init__(self, info):
        self.period = getattr(info, "period", None)
        self.message = getattr(info, "message", "")


def install() -> None:
    """Clamp the monotonic clock resolution reported to asyncio. Idempotent."""
    global _patched
    if _patched:
        return
    _patched = True

    real_get_clock_info = _time.get_clock_info

    def get_clock_info(name):
        info = real_get_clock_info(name)
        if name == "monotonic" and info.resolution > _BOGUS_THRESHOLD_S:
            return _FixedClockInfo(info)
        return info

    _time.get_clock_info = get_clock_info


install()
