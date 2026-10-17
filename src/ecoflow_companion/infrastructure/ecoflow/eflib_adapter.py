"""``StationAdapter`` over the vendored upstream library ``eflib``."""

import asyncio
import logging
from collections.abc import Callable

from bleak.exc import BleakError
from google.protobuf.message import Message

from ecoflow_companion._vendor import eflib
from ecoflow_companion._vendor.eflib.connection import ConnectionState
from ecoflow_companion._vendor.eflib.devicebase import DeviceBase
from ecoflow_companion._vendor.eflib.exceptions import (
    AuthErrors,
    BaseAuthException,
    MaxConnectionAttemptsReached,
    MaxReconnectAttemptsReached,
    UnsupportedBluetoothProtocol,
)
from ecoflow_companion._vendor.eflib.logging_util import LogOptions
from ecoflow_companion._vendor.eflib.props import ProtobufProps
from ecoflow_companion.application.ports import (
    Clock,
    ConnectFailed,
    LinkLostListener,
    StationSelector,
    SystemClock,
    TelemetryListener,
)
from ecoflow_companion.domain.models import FailureKind, ModelSupport, StationInfo
from ecoflow_companion.infrastructure.ecoflow import discovery
from ecoflow_companion.infrastructure.ecoflow.telemetry_mapper import TelemetryMapper
from ecoflow_companion.redaction import Redactor

log = logging.getLogger(__name__)

# Auth replies that a retry may get past; the others need the user to act
_TRANSIENT_AUTH_ERRORS = (
    AuthErrors.DeviceInternalError,
    AuthErrors.AppSendDataError,
    AuthErrors.UnknownError,
)

type _Exc = BaseException | type[BaseException] | None


def _is(exc: _Exc, cls: type[BaseException] | tuple[type[BaseException], ...]) -> bool:
    return isinstance(exc, cls) or (isinstance(exc, type) and issubclass(exc, cls))


def _name(exc: _Exc) -> str:
    if exc is None:
        return "no exception"
    return exc.__name__ if isinstance(exc, type) else type(exc).__name__


def classify(state: ConnectionState | None, exc: _Exc) -> tuple[FailureKind, str]:
    """Failure kind and a non-sensitive description for a failed upstream connection."""
    if _is(exc, (MaxConnectionAttemptsReached, MaxReconnectAttemptsReached)):
        exc = getattr(exc, "last_error", None) or exc
    if _is(exc, UnsupportedBluetoothProtocol):
        # Seen when a second session from the same PC tries to connect (HARDWARE_LOG.md)
        return FailureKind.BUSY, (
            "station busy: it accepts one BLE session; close the EcoFlow app and other tools"
        )
    if _is(exc, _TRANSIENT_AUTH_ERRORS):
        return FailureKind.ERROR, f"authentication error from station ({_name(exc)})"
    if state is ConnectionState.ERROR_AUTH_FAILED or _is(exc, BaseAuthException):
        return FailureKind.AUTH_FAILED, (
            f"authentication rejected ({_name(exc)}): check the user ID and that the station "
            "is bound to that account"
        )
    if state is ConnectionState.ERROR_TIMEOUT or _is(exc, TimeoutError):
        return FailureKind.TIMEOUT, "BLE connection timed out"
    if state is ConnectionState.ERROR_NOT_FOUND:
        return FailureKind.NOT_FOUND, "station not reachable"
    return FailureKind.ERROR, f"connection failed in state {state} ({_name(exc)})"


class EflibAdapter:
    """One BLE session at a time, with upstream's own reconnect turned off.

    Reconnects belong to the monitor service (SPEC 5.3). Upstream logging is limited to
    connection debugging on request; packet and payload logging is never enabled because it
    could include the authentication packet.
    """

    def __init__(
        self,
        redactor: Redactor,
        *,
        clock: Clock | None = None,
        debug_logging: bool = False,
        ble_attempts: int = 3,
    ) -> None:
        self._redactor = redactor
        self._clock = clock or SystemClock()
        self._log_options = LogOptions.CONNECTION_DEBUG if debug_logging else LogOptions(0)
        self._ble_attempts = ble_attempts
        self._device: DeviceBase | None = None
        self._unlisten: list[Callable[[], None]] = []

    async def scan(self, scan_seconds: float) -> list[StationInfo]:
        try:
            found = await discovery.discover(scan_seconds, self._redactor)
        except (BleakError, OSError) as e:
            raise ConnectFailed(
                FailureKind.BLUETOOTH_UNAVAILABLE, self._redactor.apply(f"{_name(e)}: {e}")
            ) from e
        return [item.info for item in found]

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
        target = await discovery.find(selector, scan_timeout, self._redactor)
        info = target.info
        self._redactor.add_credentials(user_id, info.serial)
        if info.support is ModelSupport.UNSUPPORTED:
            raise ConnectFailed(
                FailureKind.UNSUPPORTED_MODEL,
                f"no upstream decoder for serial prefix {info.serial_prefix}",
            )
        device = eflib.NewDevice(target.ble_device, target.adv)
        mapper = TelemetryMapper(device) if isinstance(device, ProtobufProps) else None
        if device is None or mapper is None or not mapper.metrics:
            raise ConnectFailed(
                FailureKind.UNSUPPORTED_MODEL,
                f"upstream class for prefix {info.serial_prefix} has no supported telemetry",
            )
        device.with_logging_options(self._log_options).with_disabled_reconnect(True)

        established = False
        dropped = False
        # Upstream clears the state exception on DISCONNECTED; the listener still gets it
        drop_exc: _Exc = None

        def on_message(message: Message) -> None:
            if established:
                telemetry = mapper.map(message, self._clock.now(), self._clock.monotonic())
                if telemetry is not None:
                    on_telemetry(telemetry)

        def on_disconnect(exc: _Exc) -> None:
            nonlocal dropped, drop_exc
            if self._device is not device or dropped:
                return
            dropped, drop_exc = True, exc
            if established:
                on_link_lost(FailureKind.LINK_LOST, f"BLE link dropped ({_name(exc)})")

        self._device = device
        self._unlisten = [
            device.on_message_processed(on_message),
            device.on_disconnect(on_disconnect),
        ]
        try:
            async with asyncio.timeout(connect_timeout):
                await device.connect(user_id=user_id, max_attempts=self._ble_attempts)
                state, exc = await device.wait_until_authenticated_or_error(return_exc=True)
        except TimeoutError:
            await self.disconnect()
            raise ConnectFailed(
                FailureKind.TIMEOUT, f"not authenticated within {connect_timeout:g} s"
            ) from None
        except Exception as e:
            # Classified first: disconnecting discards upstream's connection state
            kind, detail = classify(device.connection_state, e)
            await self.disconnect()
            raise ConnectFailed(kind, self._redactor.apply(detail)) from e
        except BaseException:
            await self.disconnect()
            raise

        if state is not ConnectionState.AUTHENTICATED or dropped:
            await self.disconnect()
            kind, detail = classify(state, exc if exc is not None else drop_exc)
            if dropped and state is ConnectionState.AUTHENTICATED:
                kind, detail = FailureKind.LINK_LOST, "BLE link dropped right after authentication"
            raise ConnectFailed(kind, self._redactor.apply(detail))
        established = True
        return info

    async def disconnect(self) -> None:
        device, self._device = self._device, None
        unlisten, self._unlisten = self._unlisten, []
        for remove in unlisten:
            remove()
        if device is None or device.connection_state is None:
            return
        await device.disconnect()
