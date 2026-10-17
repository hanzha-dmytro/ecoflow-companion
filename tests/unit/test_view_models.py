from datetime import UTC, date, datetime

import pytest

from ecoflow_companion.domain.models import (
    ConnectionState,
    FailureKind,
    LinkStatus,
    ModelSupport,
    StationInfo,
    StationSnapshot,
)
from ecoflow_companion.presentation.view_models import (
    FAILURE_HINTS,
    TOOLTIP_LIMIT,
    UNKNOWN,
    Tone,
    battery_tone,
    build_view,
)

AT = datetime(2026, 10, 17, 9, 44, 48, tzinfo=UTC)
TODAY = AT.astimezone().date()
STATION = StationInfo(
    "AA:BB:CC:DD:57:2E", "P321ZZZZ12345678", "Delta 3 Classic", ModelSupport.TESTED
)
CONNECTED = ConnectionState(LinkStatus.CONNECTED)


def view(snapshot, connection=CONNECTED, station=STATION):
    return build_view(snapshot, connection, station, today=TODAY)


def test_live_values():
    v = view(StationSnapshot(54, 310.4, 118.0, True, AT, stale=False))

    assert (v.battery_text, v.ac_in_text, v.ac_out_text) == ("54%", "310 W", "118 W")
    assert (v.grid_text, v.grid_tone) == ("Grid connected", Tone.OK)
    assert (v.link_text, v.link_tone) == ("Connected", Tone.OK)
    assert v.footer == ()
    assert v.badge is None
    assert v.title == "Delta 3 Classic"


def test_unknown_is_never_zero():
    v = view(StationSnapshot(stale=False))

    assert v.battery_text == UNKNOWN
    assert v.battery_level is None
    assert v.ac_in_text == UNKNOWN
    assert v.ac_out_text == UNKNOWN
    assert v.grid_text == "Grid unknown"


def test_zero_and_false_are_values():
    v = view(StationSnapshot(0, 0.0, 0.0, False, AT, stale=False))

    assert (v.battery_text, v.ac_in_text, v.grid_text) == ("0%", "0 W", "Grid disconnected")
    assert v.grid_tone is Tone.CRITICAL


def test_stale_values_are_muted_with_last_update_and_reason():
    state = ConnectionState(LinkStatus.RECONNECTING, AT, FailureKind.NOT_FOUND, "x")

    v = view(StationSnapshot(54, 310.0, 118.0, False, AT, stale=True), state)

    assert v.stale
    assert v.battery_tone is Tone.UNKNOWN
    assert v.grid_tone is Tone.UNKNOWN
    assert v.footer[0] == FAILURE_HINTS[FailureKind.NOT_FOUND]
    assert v.footer[1] == f"Last update {AT.astimezone():%H:%M:%S}"
    assert "(stale)" in v.tooltip
    assert "Grid unknown" in v.tooltip


def test_connected_without_data_says_waiting():
    v = view(StationSnapshot(stale=True))

    assert v.footer == ("Waiting for data…",)


def test_old_update_shows_date():
    v = build_view(StationSnapshot(54, last_update=AT, stale=True), CONNECTED, STATION,
                   today=date(2026, 10, 20))  # fmt: skip

    assert v.footer[-1].startswith("Last update 17 Oct")


@pytest.mark.parametrize(
    ("support", "badge"),
    [
        (ModelSupport.TESTED, None),
        (ModelSupport.UPSTREAM_ONLY, "Unverified model"),
        (ModelSupport.SIMULATED, "SIMULATED"),
    ],
)
def test_model_badge(support, badge):
    station = StationInfo("AA:BB:CC:DD:57:2E", "X", "Some model", support)

    assert view(StationSnapshot(stale=False), station=station).badge == badge


def test_no_station_yet():
    v = view(StationSnapshot(), ConnectionState(LinkStatus.CONNECTING), station=None)

    assert v.title == "EcoFlow station"
    assert v.link_text == "Connecting…"


@pytest.mark.parametrize(
    ("level", "tone"),
    [(None, Tone.UNKNOWN), (80, Tone.OK), (31, Tone.OK), (30, Tone.WARN), (11, Tone.WARN),
     (10, Tone.CRITICAL), (0, Tone.CRITICAL)],
)  # fmt: skip
def test_battery_tone_follows_thresholds(level, tone):
    assert battery_tone(level, (30, 20, 10)) is tone


def test_tooltip_fits_windows_limit():
    station = StationInfo("A", "S", "M" * 200, ModelSupport.TESTED)

    assert len(view(StationSnapshot(54, stale=False), station=station).tooltip) <= TOOLTIP_LIMIT


@pytest.mark.parametrize("kind", list(FailureKind))
def test_every_failure_has_a_hint(kind):
    assert FAILURE_HINTS[kind]
