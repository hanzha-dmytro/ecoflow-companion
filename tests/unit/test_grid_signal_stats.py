from ecoflow_companion._vendor.eflib.packet import Packet
from ecoflow_companion._vendor.eflib.pb import pd335_sys_pb2
from ecoflow_companion.diagnostics.ble_probe import GridSignalStats


def frame(**fields) -> Packet:
    payload = pd335_sys_pb2.DisplayPropertyUpload(**fields).SerializeToString()
    return Packet(src=0x02, dst=0x20, cmd_set=0xFE, cmd_id=0x15, payload=payload)


def test_counts_presence_per_field_across_partial_frames():
    stats = GridSignalStats()

    stats.on_packet(frame(plug_in_info_ac_charger_flag=True, pow_get_ac_in=300.0))
    stats.on_packet(frame(pow_get_ac_in=310.0))
    stats.on_packet(frame(cms_batt_soc=55.0))

    assert stats.frames == 3
    assert stats.present["plug_in_info_ac_charger_flag"] == 1
    assert stats.present["pow_get_ac_in"] == 2
    assert stats.present["flow_info_ac_in"] == 0
    assert stats.values["plug_in_info_ac_charger_flag"] == {"True": 1}


def test_records_transitions_and_ignores_power_jitter():
    stats = GridSignalStats()

    stats.on_packet(frame(flow_info_ac_in=14, pow_get_ac_in=300.0))
    stats.on_packet(frame(pow_get_ac_in=280.0))  # still > 0: not a change
    stats.on_packet(frame(flow_info_ac_in=12, pow_get_ac_in=0.0))

    changes = "\n".join(stats.changes)
    assert "flow_info_ac_in: 14 -> 12" in changes
    assert "pow_get_ac_in: True -> False" in changes
    assert len(stats.changes) == 2


def test_ignores_other_packets_and_invalid_payloads():
    stats = GridSignalStats()

    stats.on_packet(Packet(src=0x35, dst=0x21, cmd_set=0x01, cmd_id=0x52, payload=b""))
    stats.on_packet(Packet(src=0x02, dst=0x20, cmd_set=0xFE, cmd_id=0x15, payload=b"\xff\xff"))

    assert stats.frames == 0
    assert stats.decode_errors == 1
    assert "no DELTA 3 telemetry frames" in stats.report()
