"""From alert events to user notifications (SPEC 8.4).

The rules decide *when* something happened; this service decides whether the user wants to
hear about it, writes the text, drops repeats and reports what the host did with it.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from ecoflow_companion.application.ports import Clock, SystemClock
from ecoflow_companion.domain.events import (
    AlertEvent,
    BatteryLow,
    GridLost,
    GridRestored,
    LinkLost,
    LinkRestored,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Notification:
    # One visible notification per category: a newer one replaces the older
    category: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    # True only when the host accepted the notification; it may still be hidden by Focus
    # assist or similar, which Windows does not report
    accepted: bool
    detail: str


class Notifier(Protocol):
    def notify(self, notification: Notification) -> DeliveryResult: ...


@dataclass(frozen=True, slots=True)
class NotificationPreferences:
    enabled: bool = True
    grid: bool = True
    link_lost: bool = True
    link_restored: bool = True


@dataclass(frozen=True, slots=True)
class AlertContext:
    """What the UI knows when the alert arrives, to make the text useful."""

    station_name: str | None = None
    battery_percent: int | None = None


def _clock(at: datetime) -> str:
    return at.astimezone().strftime("%H:%M")


def _duration(value: timedelta) -> str:
    minutes, seconds = divmod(round(value.total_seconds()), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min {seconds} s" if minutes else f"{seconds} s"


def compose(event: AlertEvent, context: AlertContext) -> Notification:
    name = context.station_name or "The station"
    battery = f" Battery {context.battery_percent}%." if context.battery_percent is not None else ""
    match event:
        case BatteryLow(percent, threshold, _, initial):
            body = f"{name} is at or below the {threshold}% alert level."
            if initial:
                body += " It was already this low when monitoring started."
            return Notification("battery", f"Battery low: {percent}%", body)
        case GridLost(at):
            return Notification(
                "grid",
                "Grid power lost",
                f"{name} is running on battery since {_clock(at)}.{battery}",
            )
        case GridRestored(at):
            return Notification(
                "grid",
                "Grid power restored",
                f"{name} has grid power again ({_clock(at)}).{battery}",
            )
        case LinkLost(at, _):
            return Notification(
                "link",
                "Station disconnected",
                f"No Bluetooth connection to {name} since {_clock(at)}. "
                "Battery and grid alerts resume when it reconnects.",
            )
        case LinkRestored(_, outage):
            return Notification(
                "link",
                "Station reconnected",
                f"Connection to {name} is back after {_duration(outage)}.{battery}",
            )
    raise TypeError(f"not an alert event: {event!r}")


def _key(event: AlertEvent) -> tuple[str, object]:
    if isinstance(event, BatteryLow):
        return ("battery", event.threshold)
    return (type(event).__name__, None)


class NotificationService:
    """Applies the user's toggles, drops repeats and logs the host's answer.

    A repeat is the same alert (same kind; for battery, the same threshold) as the previous
    one in its category within ``repeat_window_seconds``. The rules do not repeat alerts on
    their own; repeats come from rules being rebuilt, e.g. after a settings change.
    """

    def __init__(
        self,
        notifier: Notifier,
        preferences: NotificationPreferences | None = None,
        *,
        repeat_window_seconds: float = 900.0,
        clock: Clock | None = None,
    ) -> None:
        self._notifier = notifier
        self._prefs = preferences or NotificationPreferences()
        self._repeat_window = repeat_window_seconds
        self._clock = clock or SystemClock()
        self._last: dict[str, tuple[tuple[str, object], float]] = {}

    def set_preferences(self, preferences: NotificationPreferences) -> None:
        self._prefs = preferences

    def wanted(self, event: AlertEvent) -> bool:
        p = self._prefs
        if not p.enabled:
            return False
        match event:
            case GridLost() | GridRestored():
                return p.grid
            case LinkLost():
                return p.link_lost
            case LinkRestored():
                return p.link_restored
        return True

    def handle(self, event: AlertEvent, context: AlertContext) -> DeliveryResult | None:
        """Notify about ``event``; None when it was not sent (turned off or a repeat)."""
        if not self.wanted(event):
            log.info("Notification off for %s", type(event).__name__)
            return None
        notification = compose(event, context)
        key, now = _key(event), self._clock.monotonic()
        previous = self._last.get(notification.category)
        if previous is not None and previous[0] == key and now - previous[1] < self._repeat_window:
            log.info("Repeated %s notification suppressed", notification.category)
            return None
        self._last[notification.category] = (key, now)
        return self._send(notification)

    def send_test(self) -> DeliveryResult:
        return self._send(Notification("test", "EcoFlow Companion", "Notifications are working."))

    def _send(self, notification: Notification) -> DeliveryResult:
        try:
            result = self._notifier.notify(notification)
        except Exception as e:
            log.exception("Notification failed")
            result = DeliveryResult(False, f"error: {type(e).__name__}")
        if result.accepted:
            log.info("Notification '%s': %s", notification.title, result.detail)
        else:
            log.warning("Notification '%s' not shown: %s", notification.title, result.detail)
        return result
