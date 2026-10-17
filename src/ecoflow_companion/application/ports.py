"""Interfaces the application layer needs from infrastructure."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ecoflow_companion.domain.models import FailureKind, StationInfo, Telemetry

type TelemetryListener = Callable[[Telemetry], None]
type LinkLostListener = Callable[[FailureKind, str | None], None]


@dataclass(frozen=True, slots=True)
class StationSelector:
    """Which station to connect to. With no field set: the only station in range."""

    address: str | None = None
    serial: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.address is None and self.serial is None

    def matches(self, station: StationInfo) -> bool:
        if self.address is not None and station.address.upper() == self.address.upper():
            return True
        return self.serial is not None and station.serial == self.serial


class ConnectFailed(Exception):
    """A connection attempt failed. ``detail`` must already be redacted."""

    def __init__(self, kind: FailureKind, detail: str | None = None) -> None:
        super().__init__(f"{kind}: {detail}" if detail else str(kind))
        self.kind = kind
        self.detail = detail


class StationAdapter(Protocol):
    """Owner of the single BLE session to a station.

    At most one session exists at a time. ``on_link_lost`` is called at most once per
    session, only for a session that ``connect`` returned, and never after ``disconnect``.
    Listeners are called on the event loop thread.
    """

    async def scan(self, scan_seconds: float) -> list[StationInfo]: ...

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
        """Find, connect and authenticate. Raises ``ConnectFailed``."""
        ...

    async def disconnect(self) -> None:
        """End the current session, if any. Idempotent."""
        ...


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def now(self) -> datetime: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def now(self) -> datetime:
        return datetime.now().astimezone()
