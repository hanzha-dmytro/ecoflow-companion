"""A monotonic clock that stops while Windows sleeps.

``time.monotonic()`` on Windows (QueryPerformanceCounter since Python 3.13) keeps counting
through sleep and hibernation. Measured on the development PC: it runs level with
GetTickCount64 and was ~26 h ahead of awake time. With it, a BLE link that dropped when the
PC went to sleep at night would look 8 h dead on waking, and the outage alert plus "restored
after 8 h" would fire every morning although nothing was wrong with the station.

``QueryUnbiasedInterruptTime`` excludes sleep, so timeouts, debouncing and outage confirmation
count only time the PC was awake. Its 15.6 ms resolution is ample for second-scale rules.
"""

import ctypes
import sys
import time
from datetime import datetime


def _unbiased_seconds() -> float:
    value = ctypes.c_ulonglong()
    if not _query(ctypes.byref(value)):
        return time.monotonic()
    return value.value / 10_000_000  # 100-ns units


if sys.platform == "win32":
    _query = ctypes.WinDLL("kernel32").QueryUnbiasedInterruptTime
    _query.argtypes = (ctypes.POINTER(ctypes.c_ulonglong),)
    _query.restype = ctypes.c_int
    awake_monotonic = _unbiased_seconds
else:
    awake_monotonic = time.monotonic


class AwakeClock:
    """``Clock`` whose monotonic time excludes system sleep; wall time is local time."""

    def monotonic(self) -> float:
        return awake_monotonic()

    def now(self) -> datetime:
        return datetime.now().astimezone()
