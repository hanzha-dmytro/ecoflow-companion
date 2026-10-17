"""Connection lifecycle, telemetry freshness and alert evaluation (SPEC 5.3, 8, 10)."""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from ecoflow_companion.application.event_rules import EventRules
from ecoflow_companion.application.ports import (
    Clock,
    ConnectFailed,
    StationAdapter,
    StationSelector,
    SystemClock,
)
from ecoflow_companion.application.reconnect_policy import ReconnectPolicy
from ecoflow_companion.domain.events import (
    ConnectionChanged,
    DomainEvent,
    SnapshotUpdated,
    StationIdentified,
)
from ecoflow_companion.domain.models import (
    ConnectionState,
    FailureKind,
    LinkStatus,
    StationInfo,
    StationSnapshot,
    Telemetry,
)
from ecoflow_companion.domain.telemetry_tracker import TelemetryTracker

log = logging.getLogger(__name__)

type EventSink = Callable[[DomainEvent], None]

DISCONNECT_TIMEOUT = 15.0


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    stale_after_seconds: float = 30.0
    # A session that delivers nothing for this long is treated as dead and reconnected
    silent_link_timeout_seconds: float = 90.0
    scan_timeout_seconds: float = 15.0
    connect_timeout_seconds: float = 60.0
    tick_seconds: float = 1.0


class MonitorService:
    """Owns the BLE session: connects, reconnects with backoff and turns telemetry into events.

    Run ``run()`` as a task; cancel the task to stop, which also disconnects. Events go to
    ``sink`` synchronously on the event loop thread; a failing sink is logged and ignored.
    """

    def __init__(
        self,
        adapter: StationAdapter,
        selector: StationSelector,
        user_id_provider: Callable[[], str | None],
        sink: EventSink,
        *,
        config: MonitorConfig | None = None,
        rules: EventRules | None = None,
        policy: ReconnectPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._adapter = adapter
        self._selector = selector
        self._user_id_provider = user_id_provider
        self._sink = sink
        self._config = config or MonitorConfig()
        self._rules = rules or EventRules()
        self._policy = policy or ReconnectPolicy()
        self._clock = clock or SystemClock()
        self._tracker = TelemetryTracker(self._config.stale_after_seconds)

        self._state = ConnectionState()
        self._station: StationInfo | None = None
        self._wake = asyncio.Event()
        self._reconnect_requested = False
        self._session = 0
        self._lost: tuple[FailureKind, str | None] | None = None
        self._healthy = False
        self._session_started = 0.0
        self._last_stale: bool | None = None

    @property
    def state(self) -> ConnectionState:
        return replace(self._state, last_successful_update=self._tracker.last_update)

    @property
    def station(self) -> StationInfo | None:
        return self._station

    @property
    def selector(self) -> StationSelector:
        """The current target; pinned to the first connected station if it was empty."""
        return self._selector

    def snapshot(self) -> StationSnapshot:
        return self._tracker.snapshot(self._clock.monotonic(), self._state.connected)

    def request_reconnect(self) -> None:
        """Drop the current session (if any) and connect again now, resetting the backoff."""
        self._reconnect_requested = True
        self._wake.set()

    def set_rules(self, rules: EventRules) -> None:
        """Replace the alert rules (new settings); their state starts afresh."""
        self._rules = rules

    def set_selector(self, selector: StationSelector) -> None:
        """Target another station; reconnects only if the target changed."""
        if selector != self._selector:
            self._selector = selector
            self.request_reconnect()

    async def run(self) -> None:
        ticker = asyncio.create_task(self._tick_loop())
        try:
            await self._connection_loop()
        finally:
            ticker.cancel()
            self._session += 1  # late callbacks from the closing session are ignored
            await self._close_session()
            self._set_state(LinkStatus.DISCONNECTED)

    def tick(self) -> None:
        """Periodic housekeeping; called every ``tick_seconds`` by ``run()``."""
        now = self._clock.monotonic()
        fresh = self._tracker.is_fresh(now)
        for event in self._rules.on_tick(now, fresh):
            self._emit(event)

        if self._state.connected:
            last = self._tracker.last_update_monotonic
            silent_for = now - max(self._session_started, last if last is not None else 0.0)
            if silent_for >= self._config.silent_link_timeout_seconds:
                self._on_link_lost(
                    self._session,
                    FailureKind.NO_TELEMETRY,
                    f"no telemetry for {silent_for:.0f} s",
                )

        snapshot = self.snapshot()
        if snapshot.stale != self._last_stale:
            self._emit_snapshot(snapshot)

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(self._config.tick_seconds)
            try:
                self.tick()
            except Exception:
                log.exception("Monitor tick failed")

    async def _connection_loop(self) -> None:
        while True:
            self._reconnect_requested = False
            retrying = self._policy.attempt > 0
            failure = await self._attempt(retrying)

            if failure is None:
                # Connected: wait for the link to drop or a reconnect request
                while self._lost is None and not self._reconnect_requested:
                    await self._wait(None)
                await self._close_session()
                if self._reconnect_requested:
                    self._policy.reset()
                    continue
                assert self._lost is not None
                kind, detail = self._lost
                self._set_state(LinkStatus.RECONNECTING, kind, detail)
            elif not failure.kind.retryable:
                self._set_state(LinkStatus.DISCONNECTED, failure.kind, failure.detail)
                while not self._reconnect_requested:
                    await self._wait(None)
                self._policy.reset()
                continue
            else:
                self._set_state(LinkStatus.RECONNECTING, failure.kind, failure.detail)

            delay = self._policy.next_delay()
            log.info("Next connection attempt in %.1f s", delay)
            await self._wait(delay)
            if self._reconnect_requested:
                self._policy.reset()

    async def _attempt(self, retrying: bool) -> ConnectFailed | None:
        user_id = self._user_id_provider()
        if not user_id:
            return ConnectFailed(FailureKind.NO_CREDENTIALS, "EcoFlow user ID is not set")

        self._set_state(
            LinkStatus.RECONNECTING if retrying else LinkStatus.CONNECTING,
            self._state.error if retrying else None,
            self._state.error_detail if retrying else None,
        )
        self._session += 1
        session = self._session
        self._lost = None
        self._healthy = False
        try:
            station = await self._adapter.connect(
                self._selector,
                user_id,
                scan_timeout=self._config.scan_timeout_seconds,
                connect_timeout=self._config.connect_timeout_seconds,
                on_telemetry=lambda t: self._on_telemetry(session, t),
                on_link_lost=lambda kind, detail: self._on_link_lost(session, kind, detail),
            )
        except ConnectFailed as e:
            log.warning("Connection attempt failed: %s", e)
            return e
        except Exception as e:
            # A bug or an unexpected library error must not end monitoring
            log.exception("Connection attempt failed unexpectedly")
            return ConnectFailed(FailureKind.ERROR, type(e).__name__)

        self._station = station
        if self._selector.is_empty:
            # Never reconnect to a different station that happens to be in range later
            self._selector = StationSelector(address=station.address, serial=station.serial)
        self._session_started = self._clock.monotonic()
        self._emit(StationIdentified(station))
        if self._lost is None:
            self._set_state(LinkStatus.CONNECTED)
        return None

    def _on_telemetry(self, session: int, telemetry: Telemetry) -> None:
        if session != self._session or self._lost is not None:
            return
        if not self._healthy:
            # Backoff restarts only once a session delivers data, so a station that accepts
            # connections and drops them at once is still retried with growing delays
            self._healthy = True
            self._policy.reset()
        self._tracker.update(telemetry)
        snapshot = self.snapshot()
        self._emit_snapshot(snapshot)
        for event in self._rules.on_telemetry(telemetry):
            self._emit(event)

    def _on_link_lost(self, session: int, kind: FailureKind, detail: str | None) -> None:
        if session != self._session or self._lost is not None:
            return
        log.warning("BLE link lost: %s%s", kind, f" ({detail})" if detail else "")
        self._lost = (kind, detail)
        self._rules.on_link_lost(kind, self._clock.monotonic(), self._clock.now())
        self._set_state(LinkStatus.RECONNECTING, kind, detail)
        self._wake.set()

    async def _close_session(self) -> None:
        try:
            async with asyncio.timeout(DISCONNECT_TIMEOUT):
                await self._adapter.disconnect()
        except TimeoutError:
            log.warning("Disconnect did not finish within %.0f s", DISCONNECT_TIMEOUT)
        except Exception:
            log.exception("Disconnect failed")

    async def _wait(self, seconds: float | None) -> None:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                await self._wake.wait()
        self._wake.clear()

    def _set_state(
        self,
        status: LinkStatus,
        error: FailureKind | None = None,
        detail: str | None = None,
    ) -> None:
        new = ConnectionState(status, self._tracker.last_update, error, detail)
        if (new.status, new.error, new.error_detail) == (
            self._state.status,
            self._state.error,
            self._state.error_detail,
        ):
            return
        self._state = new
        self._emit(ConnectionChanged(new))
        self._emit_snapshot(self.snapshot())

    def _emit_snapshot(self, snapshot: StationSnapshot) -> None:
        self._last_stale = snapshot.stale
        self._emit(SnapshotUpdated(snapshot))

    def _emit(self, event: DomainEvent) -> None:
        try:
            self._sink(event)
        except Exception:
            log.exception("Event sink failed for %s", type(event).__name__)
