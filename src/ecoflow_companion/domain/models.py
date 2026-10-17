"""Domain models. ``None`` always means unknown/unavailable, never zero or ``False``."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Telemetry:
    """One telemetry update from the station.

    Stations send partial updates: a value is ``None`` when this update did not report it.
    ``received_monotonic`` is for timeout arithmetic, ``received_at`` for display and logs.
    """

    received_at: datetime
    received_monotonic: float
    battery_percent: int | None = None
    ac_input_watts: float | None = None
    ac_output_watts: float | None = None
    ac_plugged_in: bool | None = None

    def is_empty(self) -> bool:
        return (
            self.battery_percent is None
            and self.ac_input_watts is None
            and self.ac_output_watts is None
            and self.ac_plugged_in is None
        )


@dataclass(frozen=True, slots=True)
class StationSnapshot:
    """Latest known value of every metric, for display.

    ``stale`` is True when the values are not live: no update within the staleness window,
    or the BLE link is down. Stale values must not be presented as current.
    """

    battery_percent: int | None = None
    ac_input_watts: float | None = None
    ac_output_watts: float | None = None
    ac_plugged_in: bool | None = None
    last_update: datetime | None = None
    stale: bool = True


class LinkStatus(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class FailureKind(StrEnum):
    """Why a connection attempt failed or a link was lost."""

    NO_CREDENTIALS = "no_credentials"
    BLUETOOTH_UNAVAILABLE = "bluetooth_unavailable"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED_MODEL = "unsupported_model"
    BUSY = "busy"
    AUTH_FAILED = "auth_failed"
    TIMEOUT = "timeout"
    LINK_LOST = "link_lost"
    NO_TELEMETRY = "no_telemetry"
    ERROR = "error"

    @property
    def retryable(self) -> bool:
        """False when retrying cannot help until the user changes something."""
        return self not in _NOT_RETRYABLE


_NOT_RETRYABLE = frozenset(
    {
        FailureKind.NO_CREDENTIALS,
        FailureKind.AMBIGUOUS,
        FailureKind.UNSUPPORTED_MODEL,
        FailureKind.AUTH_FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class ConnectionState:
    status: LinkStatus = LinkStatus.DISCONNECTED
    last_successful_update: datetime | None = None
    error: FailureKind | None = None
    # Human-readable, already redacted; never contains the address, serial or user ID
    error_detail: str | None = None

    @property
    def connected(self) -> bool:
        return self.status is LinkStatus.CONNECTED


class ModelSupport(StrEnum):
    # Run on real hardware by this project; docs/HARDWARE_LOG.md lists what was checked
    TESTED = "tested"
    # Upstream has a decoder for the serial prefix; not run on hardware by this project
    UPSTREAM_ONLY = "upstream_only"
    # No upstream decoder: the station can be found but its telemetry cannot be read
    UNSUPPORTED = "unsupported"
    # Not a real station: synthetic data from the mock adapter
    SIMULATED = "simulated"


@dataclass(frozen=True, slots=True)
class StationInfo:
    """A discovered station. ``address`` and ``serial`` are sensitive: mask before logging."""

    address: str
    serial: str
    model: str | None
    support: ModelSupport
    rssi: int | None = None

    @property
    def serial_prefix(self) -> str:
        return self.serial[:4]
