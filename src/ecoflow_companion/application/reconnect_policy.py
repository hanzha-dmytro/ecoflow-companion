"""Bounded exponential backoff between connection attempts."""

import random
from collections.abc import Callable


class ReconnectPolicy:
    """Delays of ``initial``, ``initial * factor``, ... capped at ``max_delay``.

    Each delay is spread by up to ``jitter`` (a fraction) so several retries do not line up
    with the station's advertising interval; the cap applies after jitter. The policy only
    computes delays: the caller waits, which keeps waiting cancellable.
    """

    def __init__(
        self,
        initial_delay: float = 2.0,
        max_delay: float = 60.0,
        factor: float = 2.0,
        jitter: float = 0.1,
        rng: Callable[[], float] = random.random,
    ) -> None:
        if initial_delay <= 0 or max_delay < initial_delay or factor < 1 or not 0 <= jitter < 1:
            raise ValueError("invalid reconnect policy parameters")
        self._initial = initial_delay
        self._max = max_delay
        self._factor = factor
        self._jitter = jitter
        self._rng = rng
        self._attempt = 0
        self._base = initial_delay

    @property
    def attempt(self) -> int:
        """Number of delays handed out since the last reset."""
        return self._attempt

    def next_delay(self) -> float:
        base = self._base
        # Grown step by step rather than as factor**attempt, which overflows after days
        self._base = min(base * self._factor, self._max)
        self._attempt += 1
        spread = 1 + self._jitter * (2 * self._rng() - 1)
        return min(base * spread, self._max)

    def reset(self) -> None:
        self._attempt = 0
        self._base = self._initial
