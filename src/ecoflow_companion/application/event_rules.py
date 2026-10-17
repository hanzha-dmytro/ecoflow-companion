"""Alert rules (SPEC section 8). Pure logic: time is passed in, nothing is scheduled here."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from ecoflow_companion.domain.events import (
    AlertEvent,
    BatteryLow,
    GridLost,
    GridRestored,
    LinkLost,
    LinkRestored,
)
from ecoflow_companion.domain.models import FailureKind, Telemetry


@dataclass(frozen=True, slots=True)
class BatteryRuleConfig:
    thresholds: tuple[int, ...] = (30, 20, 10)
    hysteresis: int = 3

    def __post_init__(self) -> None:
        if not all(1 <= t <= 99 for t in self.thresholds):
            raise ValueError("battery thresholds must be between 1 and 99")
        if not 1 <= self.hysteresis <= 50:
            raise ValueError("battery hysteresis must be between 1 and 50")


class BatteryRule:
    """Low-battery alerts on downward threshold crossings.

    - A threshold fires when a reported level is at or below it, then disarms.
    - It re-arms once a reported level is at or above threshold + hysteresis.
    - Several thresholds crossed by one reading produce one alert, for the lowest of them.
    - Startup: the first reading disarms every threshold the level is already at or below
      and produces one ``initial`` alert for the lowest of them.

    Only reported values reach this rule, so missing or stale levels are ignored. Armed state
    is kept across BLE outages: a level that dropped during an outage is still alerted.
    """

    def __init__(self, config: BatteryRuleConfig) -> None:
        self._thresholds = sorted(set(config.thresholds), reverse=True)
        self._hysteresis = config.hysteresis
        self._armed: dict[int, bool] | None = None

    def on_battery(self, percent: int, at: datetime) -> BatteryLow | None:
        if self._armed is None:
            crossed = [t for t in self._thresholds if percent <= t]
            self._armed = {t: t not in crossed for t in self._thresholds}
            return BatteryLow(percent, min(crossed), at, initial=True) if crossed else None

        for t in self._thresholds:
            if not self._armed[t] and percent >= t + self._hysteresis:
                self._armed[t] = True
        crossed = [t for t in self._thresholds if self._armed[t] and percent <= t]
        for t in crossed:
            self._armed[t] = False
        return BatteryLow(percent, min(crossed), at) if crossed else None


@dataclass(frozen=True, slots=True)
class GridRuleConfig:
    debounce_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.debounce_seconds < 0:
            raise ValueError("grid debounce must not be negative")


class GridRule:
    """Grid lost/restored alerts from the station's AC-plugged flag.

    A new flag value is confirmed once it has been the latest reported value for
    ``debounce_seconds`` and telemetry is still arriving: confirmation happens on a later
    update, never on a timer, so silence cannot confirm anything. A contrary report cancels
    the pending value. The first confirmed value is a baseline and is not alerted.

    A pending value is dropped when the BLE link is lost, and the baseline is forgotten when
    telemetry goes stale, so an outage alone never produces a grid alert. AC input power is
    never used as a substitute for the flag.
    """

    def __init__(self, config: GridRuleConfig) -> None:
        self._debounce = config.debounce_seconds
        self._confirmed: bool | None = None
        self._candidate: bool | None = None
        self._candidate_since = 0.0

    @property
    def confirmed(self) -> bool | None:
        return self._confirmed

    def on_telemetry(self, telemetry: Telemetry) -> GridLost | GridRestored | None:
        now = telemetry.received_monotonic
        plugged = telemetry.ac_plugged_in
        if plugged is not None:
            if plugged == self._confirmed:
                self._candidate = None
                return None
            if plugged != self._candidate:
                self._candidate, self._candidate_since = plugged, now
        if self._candidate is None or now - self._candidate_since < self._debounce:
            return None

        previous, self._confirmed, self._candidate = self._confirmed, self._candidate, None
        if previous is None:
            return None
        return (
            GridRestored(telemetry.received_at)
            if self._confirmed
            else GridLost(telemetry.received_at)
        )

    def on_link_lost(self) -> None:
        self._candidate = None

    def on_stale(self) -> None:
        self._confirmed = None
        self._candidate = None


@dataclass(frozen=True, slots=True)
class LinkRuleConfig:
    confirm_after_seconds: float = 15.0

    def __post_init__(self) -> None:
        if self.confirm_after_seconds < 0:
            raise ValueError("link-loss confirmation delay must not be negative")


class LinkRule:
    """BLE outage alerts: one ``LinkLost`` per outage, one ``LinkRestored`` after it.

    An outage starts when an established session drops. It is confirmed once it has lasted
    ``confirm_after_seconds`` without telemetry resuming; shorter blips produce no alert at
    all. ``LinkRestored`` is sent with the first telemetry after a confirmed outage. Retries
    inside one outage never produce further alerts. Failing to connect at startup is not an
    outage.
    """

    def __init__(self, config: LinkRuleConfig) -> None:
        self._confirm_after = config.confirm_after_seconds
        self._down_since: float | None = None
        self._down_at: datetime | None = None
        self._reason = FailureKind.LINK_LOST
        self._alerted = False

    def on_link_lost(self, reason: FailureKind, now: float, at: datetime) -> None:
        if self._down_since is None:
            self._down_since, self._down_at, self._reason = now, at, reason
            self._alerted = False

    def on_tick(self, now: float) -> LinkLost | None:
        if self._down_since is None or self._alerted:
            return None
        if now - self._down_since < self._confirm_after:
            return None
        self._alerted = True
        assert self._down_at is not None
        return LinkLost(self._down_at, self._reason)

    def on_telemetry(self, now: float, at: datetime) -> LinkRestored | None:
        if self._down_since is None:
            return None
        event = (
            LinkRestored(at, timedelta(seconds=now - self._down_since)) if self._alerted else None
        )
        self._down_since = self._down_at = None
        self._alerted = False
        return event


class EventRules:
    """All alert rules, fed from one place so their ordering is fixed."""

    def __init__(
        self,
        battery: BatteryRuleConfig | None = None,
        grid: GridRuleConfig | None = None,
        link: LinkRuleConfig | None = None,
    ) -> None:
        self.battery = BatteryRule(battery or BatteryRuleConfig())
        self.grid = GridRule(grid or GridRuleConfig())
        self.link = LinkRule(link or LinkRuleConfig())

    def on_telemetry(self, telemetry: Telemetry) -> list[AlertEvent]:
        events: list[AlertEvent | None] = [
            self.link.on_telemetry(telemetry.received_monotonic, telemetry.received_at)
        ]
        if telemetry.battery_percent is not None:
            events.append(self.battery.on_battery(telemetry.battery_percent, telemetry.received_at))
        events.append(self.grid.on_telemetry(telemetry))
        return [e for e in events if e is not None]

    def on_link_lost(self, reason: FailureKind, now: float, at: datetime) -> None:
        self.grid.on_link_lost()
        self.link.on_link_lost(reason, now, at)

    def on_tick(self, now: float, telemetry_fresh: bool) -> list[AlertEvent]:
        if not telemetry_fresh:
            self.grid.on_stale()
        event = self.link.on_tick(now)
        return [event] if event is not None else []
