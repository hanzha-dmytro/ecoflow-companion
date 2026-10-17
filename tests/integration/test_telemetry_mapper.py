"""Mapping through the real upstream DELTA 3 Classic device class.

Frames are synthesized with upstream's own protobuf definitions (no captured payloads are
stored in this repository) and fed through upstream's ``data_parse``, so upstream decoding,
field transforms and partial-update behaviour are exercised as on hardware.
"""

import pytest

from ecoflow_companion._vendor import eflib
from ecoflow_companion._vendor.eflib.packet import Packet
from ecoflow_companion._vendor.eflib.pb import pd335_bms_bp_pb2, pd335_sys_pb2
from ecoflow_companion.infrastructure.ecoflow.telemetry_mapper import (
    TelemetryMapper,
    normalize_charge_state,
    normalize_percent,
)


def frame(**fields) -> Packet:
    payload = pd335_sys_pb2.DisplayPropertyUpload(**fields).SerializeToString()
    return Packet(src=0x02, dst=0x20, cmd_set=0xFE, cmd_id=0x15, payload=payload)


@pytest.fixture
def station(clock, advertised_station):
    device = eflib.NewDevice(*advertised_station)
    mapper = TelemetryMapper(device)
    received = []

    def on_message(message):
        if (t := mapper.map(message, clock.now(), clock.monotonic())) is not None:
            received.append(t)

    device.on_message_processed(on_message)
    return device, mapper, received


def test_device_is_the_upstream_classic_with_all_metrics(station):
    device, mapper, _ = station

    assert type(device).__module__.endswith("delta3_classic")
    assert mapper.metrics == {
        "battery_percent",
        "ac_input_watts",
        "ac_output_watts",
        "ac_plugged_in",
        "battery_charging",
    }


async def test_full_frame_maps_all_metrics(station):
    device, _, received = station

    await device.data_parse(
        frame(
            cms_batt_soc=54.37,
            pow_get_ac_in=310.0,
            pow_get_ac_out=-118.44,
            plug_in_info_ac_charger_flag=True,
        )
    )

    t = received[-1]
    assert (t.battery_percent, t.ac_input_watts, t.ac_output_watts, t.ac_plugged_in) == (
        54,
        310.0,
        118.4,
        True,
    )


async def test_charge_state_is_read_from_the_message(station):
    device, _, received = station

    for state in (2, 1, 0, 7):
        await device.data_parse(frame(cms_chg_dsg_state=state))
    await device.data_parse(frame(cms_batt_soc=54.0))

    assert [t.battery_charging for t in received] == [True, False, False, None, None]


async def test_partial_frame_reports_only_its_own_fields(station):
    device, _, received = station
    await device.data_parse(frame(cms_batt_soc=54.0, plug_in_info_ac_charger_flag=True))

    await device.data_parse(frame(pow_get_ac_in=0.0))

    t = received[-1]
    # Upstream still holds battery and flag on the device; they are not fresh in this frame
    assert device.battery_level == 54.0
    assert t.battery_percent is None
    assert t.ac_plugged_in is None
    assert t.ac_input_watts == 0.0


async def test_unchanged_value_is_still_reported_as_fresh(station):
    device, _, received = station

    await device.data_parse(frame(plug_in_info_ac_charger_flag=True))
    await device.data_parse(frame(plug_in_info_ac_charger_flag=True))

    assert [t.ac_plugged_in for t in received] == [True, True]


async def test_false_flag_and_zero_power_are_values_not_unknown(station):
    device, _, received = station

    await device.data_parse(
        frame(plug_in_info_ac_charger_flag=False, pow_get_ac_in=0.0, pow_get_ac_out=0.0)
    )

    t = received[-1]
    assert t.ac_plugged_in is False
    assert t.ac_input_watts == 0.0
    assert t.ac_output_watts == 0.0


async def test_frame_without_mapped_fields_is_an_empty_update(station):
    device, _, received = station

    await device.data_parse(frame(bms_max_cell_temp=25))

    assert len(received) == 1
    assert received[0].is_empty()


def test_other_message_types_are_not_telemetry(station, clock):
    _, mapper, _ = station

    message = pd335_bms_bp_pb2.BMSHeartBeatReport()

    assert mapper.map(message, clock.now(), clock.monotonic()) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (54.37, 54),
        (54.5, 55),
        (99.5, 100),
        (0.0, 0),
        (100.0, 100),
        (100.4, None),
        (-1.0, None),
        (float("nan"), None),
        (None, None),
        (True, None),
    ],
)
def test_normalize_percent(raw, expected):
    assert normalize_percent(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(2, True), (1, False), (0, False), (3, None), (True, None), (None, None)],
)
def test_normalize_charge_state(raw, expected):
    assert normalize_charge_state(raw) == expected
