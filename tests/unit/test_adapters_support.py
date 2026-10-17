"""Station selection, the simulated station and the monitor CLI's formatting."""

import asyncio

import pytest

from ecoflow_companion.application.ports import ConnectFailed, StationSelector
from ecoflow_companion.diagnostics.monitor_cli import format_event, format_snapshot
from ecoflow_companion.domain.events import BatteryLow, ConnectionChanged
from ecoflow_companion.domain.models import (
    ConnectionState,
    FailureKind,
    LinkStatus,
    ModelSupport,
    StationInfo,
    StationSnapshot,
)
from ecoflow_companion.infrastructure.ecoflow.discovery import _only_station
from ecoflow_companion.infrastructure.ecoflow.mock_adapter import (
    MOCK_STATION,
    MockScenario,
    MockStationAdapter,
)

STATION = StationInfo(
    "AA:BB:CC:DD:57:2E", "P321ZZZZ12345678", "Delta 3 Classic", ModelSupport.TESTED
)


def test_selector_matches_address_case_insensitively_or_serial():
    assert StationSelector(address="aa:bb:cc:dd:57:2e").matches(STATION)
    assert StationSelector(serial="P321ZZZZ12345678").matches(STATION)
    assert not StationSelector(address="AA:BB:CC:DD:00:00").matches(STATION)
    assert not StationSelector().matches(STATION)
    assert StationSelector().is_empty


def test_only_station_requires_exactly_one():
    with pytest.raises(ConnectFailed) as none:
        _only_station([])
    with pytest.raises(ConnectFailed) as several:
        _only_station([object(), object()])  # type: ignore[list-item]

    assert none.value.kind is FailureKind.NOT_FOUND
    assert several.value.kind is FailureKind.AMBIGUOUS


def test_mock_timeline(clock):
    adapter = MockStationAdapter(
        MockScenario(
            grid_pattern=((10, True), (5, False)), link_drop_every=100, link_drop_seconds=20
        ),
        clock=clock,
    )

    assert [adapter.grid_at(t) for t in (0, 9.9, 10, 14.9, 15, 25)] == [
        True,
        True,
        False,
        False,
        True,
        False,
    ]
    assert adapter.link_up_at(79.9)
    assert not adapter.link_up_at(80)
    assert adapter.link_up_at(100)


async def test_mock_plays_telemetry_and_drops_link(clock):
    scenario = MockScenario(update_interval=0.001, link_drop_every=100, link_drop_seconds=20)
    adapter = MockStationAdapter(scenario, clock=clock)
    telemetry, lost = [], []

    station = await adapter.connect(
        StationSelector(),
        "mock",
        scan_timeout=1,
        connect_timeout=1,
        on_telemetry=telemetry.append,
        on_link_lost=lambda kind, detail: lost.append(kind),
    )
    async with asyncio.timeout(2):
        while not telemetry:  # noqa: ASYNC110 - waiting for the simulated stream
            await asyncio.sleep(0.001)
        clock.advance(85)
        while not lost:  # noqa: ASYNC110
            await asyncio.sleep(0.001)
    await adapter.disconnect()

    assert station.support is ModelSupport.SIMULATED
    assert telemetry[0].ac_plugged_in is True
    assert lost == [FailureKind.LINK_LOST]
    with pytest.raises(ConnectFailed):
        await adapter.connect(
            StationSelector(),
            "mock",
            scan_timeout=1,
            connect_timeout=1,
            on_telemetry=telemetry.append,
            on_link_lost=lambda kind, detail: None,
        )


def test_mock_station_is_never_selected_for_a_real_one():
    assert not StationSelector(address=STATION.address).matches(MOCK_STATION)


def test_format_snapshot_shows_unknown_and_stale():
    text = format_snapshot(StationSnapshot(battery_percent=54, ac_input_watts=0.0, stale=True))

    assert "Battery 54%" in text
    assert "AC in 0 W" in text
    assert "AC out —" in text
    assert "Grid unknown" in text
    assert "STALE" in text


def test_format_event_lines(clock):
    state = ConnectionState(LinkStatus.RECONNECTING, None, FailureKind.BUSY, "station busy")

    assert format_event(ConnectionChanged(state)) == "BLE reconnecting (busy: station busy)"
    assert format_event(BatteryLow(19, 20, clock.now(), initial=True)) == (
        "ALERT battery low: 19% (threshold 20%) (already low at start)"
    )
