"""Mapping of upstream decoded fields to domain ``Telemetry``, one protobuf message at a time.

Upstream keeps the last value of every field on the device object, so reading the object
cannot tell a fresh value from a carried-over one. The mapper runs from upstream's
``on_message_processed`` hook and reports only the fields present in that message.
"""

import inspect
import math
from datetime import datetime

from google.protobuf.message import Message

from ecoflow_companion._vendor.eflib.props import ProtobufProps, proto_has_attr
from ecoflow_companion._vendor.eflib.props.protobuf_field import ProtobufField
from ecoflow_companion.domain.models import Telemetry

# Upstream field (eflib/devices/_delta3_base.py) -> domain metric. See docs/AUDIT.md.
FIELD_MAP = {
    "battery_level": "battery_percent",
    "ac_input_power": "ac_input_watts",
    "ac_output_power": "ac_output_watts",
    "plugged_in_ac": "ac_plugged_in",
}

# Protobuf fields upstream does not decode, read from the message itself -> domain metric
RAW_FIELD_MAP = {
    "cms_chg_dsg_state": "battery_charging",
}


def normalize_percent(value: object) -> int | None:
    """Upstream reports SoC as a float with 2 decimals; rounded half up to whole percent."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    if not 0 <= value <= 100:
        return None
    return math.floor(value + 0.5)


def normalize_watts(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return round(float(value), 1)


def normalize_flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def normalize_charge_state(value: object) -> bool | None:
    """``cms_chg_dsg_state``: 0 idle, 1 discharging, 2 charging (2 verified on hardware)."""
    if isinstance(value, bool) or value not in (0, 1, 2):
        return None
    return value == 2


_NORMALIZERS = {
    "battery_percent": normalize_percent,
    "ac_input_watts": normalize_watts,
    "ac_output_watts": normalize_watts,
    "ac_plugged_in": normalize_flag,
    "battery_charging": normalize_charge_state,
}


class TelemetryMapper:
    """Maps the fields of one upstream device instance to domain telemetry."""

    def __init__(self, device: ProtobufProps) -> None:
        self._device = device
        self._fields: dict[str, tuple[str, ProtobufField]] = {}
        for upstream_name, metric in FIELD_MAP.items():
            descriptor = inspect.getattr_static(type(device), upstream_name, None)
            if isinstance(descriptor, ProtobufField):
                self._fields[metric] = (upstream_name, descriptor)
        self._message_types = {d.pb_field.message_type for _, d in self._fields.values()}
        self._raw_fields = {
            name: metric
            for name, metric in RAW_FIELD_MAP.items()
            if any(name in t.DESCRIPTOR.fields_by_name for t in self._message_types)
        }

    @property
    def metrics(self) -> frozenset[str]:
        """Domain metrics this device's upstream class can provide."""
        return frozenset(self._fields) | frozenset(self._raw_fields.values())

    def map(
        self, message: Message, received_at: datetime, received_monotonic: float
    ) -> Telemetry | None:
        """Telemetry for one processed message; None if the message carries no mapped field."""
        if type(message) not in self._message_types:
            return None
        values = {}
        for metric, (upstream_name, descriptor) in self._fields.items():
            if descriptor.pb_field.message_type is not type(message):
                continue
            if not proto_has_attr(message, descriptor.pb_field):
                continue
            # Upstream has already applied its transform to the attribute on the device
            value = _NORMALIZERS[metric](getattr(self._device, upstream_name))
            if value is not None:
                values[metric] = value
        for name, metric in self._raw_fields.items():
            if name in message.DESCRIPTOR.fields_by_name and message.HasField(name):
                value = _NORMALIZERS[metric](getattr(message, name))
                if value is not None:
                    values[metric] = value
        return Telemetry(received_at, received_monotonic, **values)
