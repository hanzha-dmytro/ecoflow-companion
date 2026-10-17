import asyncio
import contextlib

import pytest

from ecoflow_companion.application.event_rules import EventRules, LinkRuleConfig
from ecoflow_companion.application.monitor_service import MonitorConfig, MonitorService
from ecoflow_companion.application.ports import ConnectFailed, StationSelector
from ecoflow_companion.application.reconnect_policy import ReconnectPolicy
from ecoflow_companion.domain.events import (
    ConnectionChanged,
    LinkLost,
    LinkRestored,
    SnapshotUpdated,
    StationIdentified,
)
from ecoflow_companion.domain.models import FailureKind, LinkStatus, ModelSupport, StationInfo

STATION = StationInfo(
    "AA:BB:CC:DD:57:2E", "P321ZZZZ12345678", "Delta 3 Classic", ModelSupport.TESTED
)


class FakeAdapter:
    """Scripted adapter: each connect pops the next outcome (None = success)."""

    def __init__(self, outcomes: list[Exception | None] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.selectors: list[StationSelector] = []
        self.disconnects = 0
        self.on_telemetry = None
        self.on_link_lost = None

    async def scan(self, scan_seconds: float) -> list[StationInfo]:
        return [STATION]

    async def connect(
        self, selector, user_id, *, scan_timeout, connect_timeout, on_telemetry, on_link_lost
    ):
        self.selectors.append(selector)
        await asyncio.sleep(0)
        if self.outcomes and (outcome := self.outcomes.pop(0)) is not None:
            raise outcome
        self.on_telemetry, self.on_link_lost = on_telemetry, on_link_lost
        return STATION

    async def disconnect(self) -> None:
        self.disconnects += 1

    @property
    def connects(self) -> int:
        return len(self.selectors)


async def until(predicate, seconds: float = 2.0) -> None:
    """Let the service run until ``predicate`` holds; fail the test after ``seconds``."""
    async with asyncio.timeout(seconds):
        while not predicate():  # noqa: ASYNC110 - polling service state is the point here
            await asyncio.sleep(0.002)


class Harness:
    def __init__(self, clock, adapter, *, user_id="123", rules=None, policy=None, config=None):
        self.adapter = adapter
        self.events = []
        self.service = MonitorService(
            adapter,
            StationSelector(),
            lambda: user_id,
            self.events.append,
            # The tick loop is driven by hand in tests
            config=config or MonitorConfig(tick_seconds=3600, silent_link_timeout_seconds=90),
            rules=rules,
            policy=policy or ReconnectPolicy(initial_delay=0.01, max_delay=0.02, jitter=0),
            clock=clock,
        )
        self.task: asyncio.Task | None = None

    def start(self):
        self.task = asyncio.create_task(self.service.run())
        return self

    async def stop(self):
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task

    def statuses(self) -> list[LinkStatus]:
        return [e.state.status for e in self.events if isinstance(e, ConnectionChanged)]

    def of(self, kind):
        return [e for e in self.events if isinstance(e, kind)]


async def test_connects_identifies_station_and_pins_selector(clock, make_telemetry):
    h = Harness(clock, FakeAdapter()).start()
    await until(lambda: h.service.state.connected)

    h.adapter.on_telemetry(make_telemetry(battery_percent=54))
    await h.stop()

    assert h.of(StationIdentified) == [StationIdentified(STATION)]
    assert h.statuses() == [LinkStatus.CONNECTING, LinkStatus.CONNECTED, LinkStatus.DISCONNECTED]
    assert h.service.selector == StationSelector(STATION.address, STATION.serial)
    live = [e.snapshot for e in h.of(SnapshotUpdated) if not e.snapshot.stale]
    assert live[-1].battery_percent == 54
    assert h.adapter.disconnects >= 1


async def test_retryable_failure_is_retried_with_backoff(clock):
    adapter = FakeAdapter([ConnectFailed(FailureKind.NOT_FOUND, "x"), None])
    h = Harness(clock, adapter).start()

    await until(lambda: h.service.state.connected)
    await h.stop()

    assert adapter.connects == 2
    assert h.statuses()[:3] == [
        LinkStatus.CONNECTING,
        LinkStatus.RECONNECTING,
        LinkStatus.CONNECTED,
    ]
    reconnecting = [e.state for e in h.of(ConnectionChanged)][1]
    assert reconnecting.error is FailureKind.NOT_FOUND


async def test_non_retryable_failure_waits_for_user(clock):
    adapter = FakeAdapter([ConnectFailed(FailureKind.AUTH_FAILED, "rejected"), None])
    h = Harness(clock, adapter).start()

    await until(lambda: h.service.state.status is LinkStatus.DISCONNECTED and adapter.connects)
    await asyncio.sleep(0.1)
    assert adapter.connects == 1
    assert h.service.state.error is FailureKind.AUTH_FAILED

    h.service.request_reconnect()
    await until(lambda: h.service.state.connected)
    await h.stop()
    assert adapter.connects == 2


async def test_missing_user_id_does_not_touch_bluetooth(clock):
    adapter = FakeAdapter()
    h = Harness(clock, adapter, user_id=None).start()

    await until(lambda: h.service.state.error is FailureKind.NO_CREDENTIALS)
    await h.stop()

    assert adapter.connects == 0


async def test_link_loss_reconnects_and_alerts_once_per_outage(clock, make_telemetry):
    rules = EventRules(link=LinkRuleConfig(confirm_after_seconds=15))
    h = Harness(clock, FakeAdapter(), rules=rules).start()
    await until(lambda: h.service.state.connected)
    h.adapter.on_telemetry(make_telemetry(battery_percent=54))

    h.adapter.on_link_lost(FailureKind.LINK_LOST, "dropped")
    h.adapter.on_link_lost(FailureKind.LINK_LOST, "dropped again")  # ignored: same session
    assert h.service.state.status is LinkStatus.RECONNECTING
    await until(lambda: h.adapter.connects == 2 and h.service.state.connected)

    # Reconnected but no telemetry yet: the outage continues and gets confirmed once
    clock.advance(20)
    h.service.tick()
    h.service.tick()
    h.adapter.on_telemetry(make_telemetry(battery_percent=53))
    await h.stop()

    assert [type(e) for e in h.events if isinstance(e, LinkLost | LinkRestored)] == [
        LinkLost,
        LinkRestored,
    ]


async def test_callbacks_from_an_old_session_are_ignored(clock, make_telemetry):
    h = Harness(clock, FakeAdapter()).start()
    await until(lambda: h.service.state.connected)
    old_telemetry, old_link_lost = h.adapter.on_telemetry, h.adapter.on_link_lost

    h.service.request_reconnect()
    await until(lambda: h.adapter.connects == 2 and h.service.state.connected)
    old_link_lost(FailureKind.LINK_LOST, "late")
    old_telemetry(make_telemetry(battery_percent=1))
    await h.stop()

    assert h.service.snapshot().battery_percent is None
    assert LinkStatus.RECONNECTING not in h.statuses()


async def test_silent_session_is_dropped_and_reconnected(clock):
    h = Harness(clock, FakeAdapter()).start()
    await until(lambda: h.service.state.connected)

    clock.advance(89)
    h.service.tick()
    assert h.service.state.connected
    clock.advance(1)
    h.service.tick()

    assert h.service.state.error is FailureKind.NO_TELEMETRY
    await until(lambda: h.adapter.connects == 2 and h.service.state.connected)
    await h.stop()


async def test_snapshot_goes_stale_on_tick(clock, make_telemetry):
    h = Harness(clock, FakeAdapter()).start()
    await until(lambda: h.service.state.connected)
    h.adapter.on_telemetry(make_telemetry(battery_percent=54))
    assert not h.events[-1].snapshot.stale
    before = len(h.events)

    clock.advance(30)
    h.service.tick()
    assert len(h.events) == before  # still fresh: nothing to report
    clock.advance(1)
    h.service.tick()

    went_stale = h.events[before:]
    await h.stop()
    assert [type(e) for e in went_stale] == [SnapshotUpdated]
    assert went_stale[0].snapshot.stale
    assert went_stale[0].snapshot.battery_percent == 54


async def test_cancel_during_backoff_stops_promptly(clock):
    adapter = FakeAdapter([ConnectFailed(FailureKind.NOT_FOUND)])
    slow = ReconnectPolicy(initial_delay=30, max_delay=60, jitter=0)
    h = Harness(clock, adapter, policy=slow).start()
    await until(lambda: h.service.state.status is LinkStatus.RECONNECTING)

    async with asyncio.timeout(1):
        await h.stop()

    assert h.service.state.status is LinkStatus.DISCONNECTED
    assert adapter.disconnects >= 1


async def test_failing_sink_and_adapter_bug_do_not_stop_the_service(clock):
    adapter = FakeAdapter([RuntimeError("bug"), None])

    def sink(event):
        raise ValueError("sink bug")

    service = MonitorService(
        adapter,
        StationSelector(),
        lambda: "123",
        sink,
        config=MonitorConfig(tick_seconds=3600),
        policy=ReconnectPolicy(initial_delay=0.01, max_delay=0.02, jitter=0),
        clock=clock,
    )
    task = asyncio.create_task(service.run())

    await until(lambda: service.state.connected)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert adapter.connects == 2


@pytest.mark.parametrize("kind", [FailureKind.AMBIGUOUS, FailureKind.UNSUPPORTED_MODEL])
async def test_other_non_retryable_failures_stop_retrying(clock, kind):
    adapter = FakeAdapter([ConnectFailed(kind)])
    h = Harness(clock, adapter).start()

    await until(lambda: h.service.state.error is kind)
    await asyncio.sleep(0.05)
    await h.stop()

    assert adapter.connects == 1


async def test_backoff_keeps_growing_while_sessions_deliver_nothing(clock, make_telemetry):
    policy = ReconnectPolicy(initial_delay=0.001, max_delay=0.01, jitter=0)
    h = Harness(clock, FakeAdapter(), policy=policy).start()

    for expected_connects in (1, 2, 3):
        await until(
            lambda n=expected_connects: h.adapter.connects == n and h.service.state.connected
        )
        h.adapter.on_link_lost(FailureKind.LINK_LOST, "flapping")
    await until(lambda: h.adapter.connects == 4 and h.service.state.connected)
    assert policy.attempt == 3

    h.adapter.on_telemetry(make_telemetry(battery_percent=50))
    await h.stop()
    assert policy.attempt == 0
