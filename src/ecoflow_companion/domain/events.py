"""Domain events published by the monitor service."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from ecoflow_companion.domain.models import (
    ConnectionState,
    FailureKind,
    StationInfo,
    StationSnapshot,
)


@dataclass(frozen=True, slots=True)
class SnapshotUpdated:
    """New telemetry arrived or the snapshot became stale."""

    snapshot: StationSnapshot


@dataclass(frozen=True, slots=True)
class ConnectionChanged:
    state: ConnectionState


@dataclass(frozen=True, slots=True)
class StationIdentified:
    """The station of the current session; sent before its first telemetry."""

    station: StationInfo


@dataclass(frozen=True, slots=True)
class BatteryLow:
    battery_percent: int
    threshold: int
    at: datetime
    # True for the single notification sent when monitoring starts already below a threshold
    initial: bool = False


@dataclass(frozen=True, slots=True)
class GridLost:
    at: datetime


@dataclass(frozen=True, slots=True)
class GridRestored:
    at: datetime


@dataclass(frozen=True, slots=True)
class LinkLost:
    """A BLE outage lasted long enough to be confirmed. Sent once per outage."""

    at: datetime
    reason: FailureKind


@dataclass(frozen=True, slots=True)
class LinkRestored:
    """Telemetry resumed after a confirmed outage."""

    at: datetime
    outage: timedelta


type AlertEvent = BatteryLow | GridLost | GridRestored | LinkLost | LinkRestored
type DomainEvent = SnapshotUpdated | ConnectionChanged | StationIdentified | AlertEvent
