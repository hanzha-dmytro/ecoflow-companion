"""EflibAdapter with upstream's DELTA 3 Classic class and a stubbed BLE connection."""

import pytest

from ecoflow_companion._vendor import eflib
from ecoflow_companion._vendor.eflib.connection import ConnectionState
from ecoflow_companion._vendor.eflib.exceptions import (
    AuthErrors,
    MaxConnectionAttemptsReached,
    UnsupportedBluetoothProtocol,
)
from ecoflow_companion._vendor.eflib.packet import Packet
from ecoflow_companion._vendor.eflib.pb import pd335_sys_pb2
from ecoflow_companion.application.ports import ConnectFailed, StationSelector
from ecoflow_companion.domain.models import FailureKind
from ecoflow_companion.infrastructure.ecoflow import discovery, eflib_adapter
from ecoflow_companion.infrastructure.ecoflow.eflib_adapter import EflibAdapter, classify
from ecoflow_companion.redaction import Redactor

USER_ID = "1234567890123456789"
SERIAL = "P321ZZZZ12345678"


def frame(**fields) -> Packet:
    payload = pd335_sys_pb2.DisplayPropertyUpload(**fields).SerializeToString()
    return Packet(src=0x02, dst=0x20, cmd_set=0xFE, cmd_id=0x15, payload=payload)


class Session:
    """Stubs discovery and the upstream connection; records adapter callbacks."""

    def __init__(self, monkeypatch, station, auth_result=(ConnectionState.AUTHENTICATED, None)):
        ble, adv = station
        self.device = eflib.NewDevice(ble, adv)
        self.telemetry = []
        self.lost = []
        self.during_connect = None

        async def find(selector, scan_seconds, redactor):
            return discovery.Found(ble, adv, discovery.identify(SERIAL))

        async def connect(user_id=None, max_attempts=None):
            if self.during_connect is not None:
                await self.during_connect()

        async def wait_until_authenticated_or_error(return_exc=False):
            return auth_result

        monkeypatch.setattr(eflib_adapter.discovery, "find", find)
        monkeypatch.setattr(eflib_adapter.eflib, "NewDevice", lambda *_: self.device)
        monkeypatch.setattr(self.device, "connect", connect)
        monkeypatch.setattr(
            self.device, "wait_until_authenticated_or_error", wait_until_authenticated_or_error
        )

    async def open(self, adapter: EflibAdapter):
        return await adapter.connect(
            StationSelector(),
            USER_ID,
            scan_timeout=1,
            connect_timeout=1,
            on_telemetry=self.telemetry.append,
            on_link_lost=lambda kind, detail: self.lost.append((kind, detail)),
        )

    def drop(self, exc=None):
        # What upstream's Connection does on an unsolicited disconnect
        self.device._listeners.on_disconnect(exc)


async def test_session_forwards_telemetry_and_reports_one_link_loss(
    monkeypatch, clock, advertised_station
):
    session = Session(monkeypatch, advertised_station)
    adapter = EflibAdapter(Redactor(), clock=clock)

    info = await session.open(adapter)
    await session.device.data_parse(frame(cms_batt_soc=80.0, plug_in_info_ac_charger_flag=True))
    session.drop()
    session.drop(RuntimeError("again"))

    assert info.model == "Delta 3 Classic"
    assert [(t.battery_percent, t.ac_plugged_in) for t in session.telemetry] == [(80, True)]
    assert [kind for kind, _ in session.lost] == [FailureKind.LINK_LOST]


async def test_no_callbacks_after_disconnect(monkeypatch, clock, advertised_station):
    session = Session(monkeypatch, advertised_station)
    adapter = EflibAdapter(Redactor(), clock=clock)
    await session.open(adapter)

    await adapter.disconnect()
    await session.device.data_parse(frame(cms_batt_soc=80.0))
    session.drop()

    assert session.telemetry == []
    assert session.lost == []


async def test_auth_failure_is_classified_and_redacted(monkeypatch, clock, advertised_station):
    session = Session(
        monkeypatch,
        advertised_station,
        (ConnectionState.ERROR_AUTH_FAILED, AuthErrors.WrongKey(f"uid {USER_ID}")),
    )
    adapter = EflibAdapter(Redactor(), clock=clock)

    with pytest.raises(ConnectFailed) as failed:
        await session.open(adapter)

    assert failed.value.kind is FailureKind.AUTH_FAILED
    assert USER_ID not in str(failed.value)
    assert session.lost == []


async def test_drop_during_connect_fails_the_attempt_without_link_loss(
    monkeypatch, clock, advertised_station
):
    session = Session(monkeypatch, advertised_station, (ConnectionState.ERROR_BLEAK, None))
    adapter = EflibAdapter(Redactor(), clock=clock)

    async def drop_with_busy_error():
        session.drop(UnsupportedBluetoothProtocol("notify", []))

    session.during_connect = drop_with_busy_error

    with pytest.raises(ConnectFailed) as failed:
        await session.open(adapter)

    # The exception only reached the disconnect listener; it still decides the kind
    assert failed.value.kind is FailureKind.BUSY
    assert session.lost == []


@pytest.mark.parametrize(
    ("state", "exc", "kind"),
    [
        (ConnectionState.ERROR_BLEAK, UnsupportedBluetoothProtocol("notify", []), "busy"),
        (
            ConnectionState.ERROR_MAX_RECONNECT_ATTEMPTS_REACHED,
            MaxConnectionAttemptsReached(last_error=UnsupportedBluetoothProtocol("notify", [])),
            "busy",
        ),
        (ConnectionState.ERROR_AUTH_FAILED, AuthErrors.WrongKey(), "auth_failed"),
        (ConnectionState.ERROR_AUTH_FAILED, AuthErrors.NeedBindInstallFirst, "auth_failed"),
        (ConnectionState.ERROR_AUTH_FAILED, AuthErrors.DeviceInternalError(), "error"),
        (ConnectionState.ERROR_TIMEOUT, None, "timeout"),
        (ConnectionState.ERROR_NOT_FOUND, None, "not_found"),
        (ConnectionState.ERROR_UNKNOWN, ValueError("x"), "error"),
    ],
)
def test_classify(state, exc, kind):
    assert classify(state, exc)[0] == FailureKind(kind)


async def test_exception_from_upstream_connect_becomes_connect_failed(
    monkeypatch, clock, advertised_station
):
    session = Session(monkeypatch, advertised_station)
    adapter = EflibAdapter(Redactor(), clock=clock)

    async def raise_max_attempts():
        raise MaxConnectionAttemptsReached(last_error=UnsupportedBluetoothProtocol("notify", []))

    session.during_connect = raise_max_attempts

    with pytest.raises(ConnectFailed) as failed:
        await session.open(adapter)

    assert failed.value.kind is FailureKind.BUSY
