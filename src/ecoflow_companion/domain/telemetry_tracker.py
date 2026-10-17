"""Merging of partial telemetry updates into a display snapshot."""

from datetime import datetime

from ecoflow_companion.domain.models import StationSnapshot, Telemetry

_METRICS = (
    "battery_percent",
    "ac_input_watts",
    "ac_output_watts",
    "ac_plugged_in",
    "battery_charging",
)


class TelemetryTracker:
    """Keeps the latest reported value of each metric and when telemetry last arrived.

    A metric absent from an update keeps its previous value, as upstream does. Values are kept
    across disconnects so the UI can show them as stale; they are never reset to zero.
    """

    def __init__(self, stale_after_seconds: float) -> None:
        self._stale_after = stale_after_seconds
        self._values: dict[str, object] = {}
        self._last_monotonic: float | None = None
        self._last_at: datetime | None = None

    @property
    def last_update(self) -> datetime | None:
        return self._last_at

    @property
    def last_update_monotonic(self) -> float | None:
        return self._last_monotonic

    def update(self, telemetry: Telemetry) -> None:
        for name in _METRICS:
            if (value := getattr(telemetry, name)) is not None:
                self._values[name] = value
        self._last_monotonic = telemetry.received_monotonic
        self._last_at = telemetry.received_at

    def is_fresh(self, now_monotonic: float) -> bool:
        """True if any telemetry arrived within the staleness window."""
        return (
            self._last_monotonic is not None
            and now_monotonic - self._last_monotonic <= self._stale_after
        )

    def snapshot(self, now_monotonic: float, link_up: bool) -> StationSnapshot:
        return StationSnapshot(
            **self._values,  # type: ignore[arg-type]
            last_update=self._last_at,
            stale=not (link_up and self.is_fresh(now_monotonic)),
        )
