"""Simulated station for developing the UI and alert rules without hardware.

Every value is synthetic. The station is labelled ``ModelSupport.SIMULATED`` so it can never
be mistaken for a real one.
"""

import asyncio
import contextlib
from dataclasses import dataclass

from ecoflow_companion.application.ports import (
    Clock,
    ConnectFailed,
    LinkLostListener,
    StationSelector,
    SystemClock,
    TelemetryListener,
)
from ecoflow_companion.domain.models import FailureKind, ModelSupport, StationInfo, Telemetry

MOCK_STATION = StationInfo(
    address="00:00:00:00:00:00",
    serial="MOCK000000000000",
    model="Simulated station",
    support=ModelSupport.SIMULATED,
    rssi=-50,
)


@dataclass(frozen=True, slots=True)
class MockScenario:
    battery_start: float = 35.0
    # Percent per update: down while the grid is off, up while it is on
    battery_step: float = 0.2
    # Repeating (duration in seconds, grid plugged in) segments
    grid_pattern: tuple[tuple[float, bool], ...] = ((40.0, True), (25.0, False))
    # The link is down for the last ``link_drop_seconds`` of every ``link_drop_every``
    link_drop_every: float | None = 150.0
    link_drop_seconds: float = 25.0
    ac_output_watts: float = 120.0
    ac_charge_watts: float = 300.0
    update_interval: float = 1.0


class MockStationAdapter:
    """``StationAdapter`` that plays a ``MockScenario`` on a timeline starting at creation."""

    def __init__(self, scenario: MockScenario | None = None, clock: Clock | None = None) -> None:
        self._scenario = scenario or MockScenario()
        self._clock = clock or SystemClock()
        self._start = self._clock.monotonic()
        self._battery = self._scenario.battery_start
        self._task: asyncio.Task[None] | None = None

    def grid_at(self, elapsed: float) -> bool:
        pattern = self._scenario.grid_pattern
        t = elapsed % sum(duration for duration, _ in pattern)
        for duration, plugged in pattern:
            if t < duration:
                return plugged
            t -= duration
        return pattern[-1][1]

    def link_up_at(self, elapsed: float) -> bool:
        every = self._scenario.link_drop_every
        if every is None:
            return True
        return elapsed % every < every - self._scenario.link_drop_seconds

    def _elapsed(self) -> float:
        return self._clock.monotonic() - self._start

    async def scan(self, scan_seconds: float) -> list[StationInfo]:
        await asyncio.sleep(min(scan_seconds, 1.0))
        return [MOCK_STATION] if self.link_up_at(self._elapsed()) else []

    async def connect(
        self,
        selector: StationSelector,
        user_id: str,
        *,
        scan_timeout: float,
        connect_timeout: float,
        on_telemetry: TelemetryListener,
        on_link_lost: LinkLostListener,
    ) -> StationInfo:
        await self.disconnect()
        await asyncio.sleep(0.5)
        if not selector.is_empty and not selector.matches(MOCK_STATION):
            raise ConnectFailed(FailureKind.NOT_FOUND, "selected station is not simulated")
        if not self.link_up_at(self._elapsed()):
            raise ConnectFailed(FailureKind.NOT_FOUND, "simulated station out of range")
        self._task = asyncio.create_task(self._play(on_telemetry, on_link_lost))
        return MOCK_STATION

    async def disconnect(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _play(self, on_telemetry: TelemetryListener, on_link_lost: LinkLostListener) -> None:
        s = self._scenario
        while True:
            await asyncio.sleep(s.update_interval)
            elapsed = self._elapsed()
            if not self.link_up_at(elapsed):
                self._task = None
                on_link_lost(FailureKind.LINK_LOST, "simulated link drop")
                return
            plugged = self.grid_at(elapsed)
            step = s.battery_step if plugged else -s.battery_step
            self._battery = min(100.0, max(0.0, self._battery + step))
            charging = plugged and self._battery < 100
            ac_in = s.ac_output_watts + (s.ac_charge_watts if charging else 0.0) if plugged else 0.0
            on_telemetry(
                Telemetry(
                    received_at=self._clock.now(),
                    received_monotonic=self._clock.monotonic(),
                    battery_percent=round(self._battery),
                    ac_input_watts=ac_in,
                    ac_output_watts=s.ac_output_watts,
                    ac_plugged_in=plugged,
                )
            )
