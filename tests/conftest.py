from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from ecoflow_companion.domain.models import Telemetry

EPOCH = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    """Monotonic and wall clock that only move when told to."""

    def __init__(self) -> None:
        self.t = 1000.0

    def monotonic(self) -> float:
        return self.t

    def now(self) -> datetime:
        return EPOCH + timedelta(seconds=self.t - 1000.0)

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_telemetry(clock: FakeClock) -> Callable[..., Telemetry]:
    """Telemetry stamped with the fake clock's current time."""

    def make(**values: object) -> Telemetry:
        return Telemetry(clock.now(), clock.monotonic(), **values)  # type: ignore[arg-type]

    return make
